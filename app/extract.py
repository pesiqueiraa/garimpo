"""Extração de fatos com LLM: o que o produto é, para quem, e o que exige no Brasil.

O LLM não dá nota aqui. Ele preenche campos categóricos (validados contra um
JSON Schema) e o score.py transforma esses fatos em subnotas. Entra o texto do
site e da página de preços, não só a tagline — é o que mais melhora a extração.
"""
import json

from app import db, llm

RAILS = ("payments", "invoicing_tax", "messaging", "banking_open_finance",
         "government_integration", "language_content", "local_regulation")
KILL_FLAGS = ("dev_tool_niche", "ai_model_release", "hardware", "two_sided_marketplace",
              "banking_license", "health_data", "crypto", "research_demo", "enterprise_only",
              "not_a_product")
ENUMS = {
    "audience": ("B2B", "B2C", "ambos"),
    "business_type": ("saas", "marketplace", "dev_tool", "ai_model_or_infra", "hardware",
                      "consumer_app", "service", "content_media", "open_source_project",
                      "fintech_regulated", "other"),
    "pricing_model": ("assinatura", "uso", "freemium", "licenca", "transacao", "anuncio",
                      "desconhecido"),
    "sales_motion": ("self_serve", "sales_led", "misto", "desconhecido"),
    "technical_complexity": ("baixa", "media", "alta"),
    "br_competitor_guess": ("sem_concorrente", "concorrente_fraco", "concorrente_forte",
                            "desconhecido"),
}
TEXT_FIELDS = ("summary_pt", "niche", "pain", "buyer", "user", "br_cnae_guess",
               "br_customer_segment")


def _schema() -> dict:
    props = {f: {"type": "string"} for f in TEXT_FIELDS}
    props.update({f: {"type": "string", "enum": list(v)} for f, v in ENUMS.items()})
    props["local_rails"] = {
        "type": "object", "additionalProperties": False, "required": list(RAILS),
        "properties": {r: {"type": "boolean"} for r in RAILS}}
    props["kill_flags"] = {"type": "array", "items": {"type": "string", "enum": list(KILL_FLAGS)}}
    props["search_keywords_pt"] = {"type": "array", "items": {"type": "string"}}
    props["search_keywords_es"] = {"type": "array", "items": {"type": "string"}}
    return {"name": "extracao_produto",
            "schema": {"type": "object", "additionalProperties": False,
                       "required": list(props), "properties": props}}


EXTRACT_SCHEMA = _schema()


def normalize(data: dict) -> dict:
    """Coerção defensiva: o que vier fora do esperado vira valor neutro (nunca levanta)."""
    out: dict = {}
    for f in TEXT_FIELDS:
        value = data.get(f)
        out[f] = str(value).strip()[:300] if value not in (None, "") else ""
    for f, allowed in ENUMS.items():
        value = data.get(f)
        fallback = "desconhecido" if "desconhecido" in allowed else (
            "other" if f == "business_type" else ("ambos" if f == "audience" else "media"))
        out[f] = value if value in allowed else fallback
    rails = data.get("local_rails") if isinstance(data.get("local_rails"), dict) else {}
    out["local_rails"] = {r: rails.get(r) is True for r in RAILS}
    flags = data.get("kill_flags") if isinstance(data.get("kill_flags"), list) else []
    out["kill_flags"] = sorted({f for f in flags if f in KILL_FLAGS})
    for f in ("search_keywords_pt", "search_keywords_es"):
        values = data.get(f) if isinstance(data.get(f), list) else []
        out[f] = [str(v).strip()[:80] for v in values if str(v).strip()][:5]
    cnae = "".join(ch for ch in out["br_cnae_guess"] if ch.isdigit())
    out["br_cnae_guess"] = cnae if len(cnae) == 7 else ""
    return out


def build_prompt(company, page: dict | None, sources: list[str], points: int,
                 categories: list[str], prompt_version: str) -> str:
    return llm.load_prompt(prompt_version).format(
        name=company["name"], tagline=company["tagline"] or "",
        category=", ".join(categories) or "n/d", sources=", ".join(sources) or "n/d",
        points=points,
        site_text=((page or {}).get("text") or "(site não lido)")[:2500],
        pricing_text=((page or {}).get("pricing_text") or "(sem página de preços)")[:800])


def extract_one(conn, company, prompt_version: str = "extract_v2") -> dict | None:
    """Extrai e grava os fatos de uma empresa. None se o LLM falhar (orçamento propaga)."""
    prompt = build_prompt(company, db.get_site_page(conn, company["id"]),
                          db.company_sources(conn, company["id"]),
                          db.max_points(conn, company["id"]),
                          db.company_categories(conn, company["id"]), prompt_version)
    try:
        data, _, _ = llm.ask_json(prompt, max_tokens=1200, schema=EXTRACT_SCHEMA)
    except llm.BudgetExceeded:
        raise
    except Exception as e:
        print(f"[extrair] falhou para '{company['name']}': {e}")
        return None
    facts = normalize(data)
    db.save_extraction(conn, company["id"], prompt_version, llm.MODEL, facts)
    conn.commit()
    return facts


def _ask_facts(prompt: str) -> dict | None:
    """Chamada do LLM isolada para rodar em thread (sem cache: o cache usa a conexão SQLite,
    que não pode cruzar threads). BudgetExceeded propaga."""
    try:
        data, _, _ = llm.ask_json(prompt, max_tokens=1200, schema=EXTRACT_SCHEMA, cache=False)
        return normalize(data)
    except llm.BudgetExceeded:
        raise
    except Exception as e:
        print(f"[extrair] falhou: {e}")
        return None


def run(conn, cfg: dict, limit: int | None = None, legacy_only: bool = False,
        workers: int = 1, deadline: float | None = None) -> int:
    """Extrai fatos das empresas que ainda não têm a versão atual do prompt.

    workers > 1 paraleliza só as chamadas ao LLM (o banco fica na thread principal);
    deadline (time.monotonic) para com folga — a próxima execução continua de onde parou.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor

    from app.config import get
    if not llm.available():
        print("[extrair] OPENAI_API_KEY ausente, pulando extração")
        return 0
    version = get(cfg, "fontes.extracao.prompt_version", "extract_v2")
    limit = limit or get(cfg, "fontes.extracao.max_per_run", 60)
    rows = db.companies_needing_extraction(conn, version, limit, legacy_only=legacy_only)
    done = 0
    if workers <= 1:
        for company in rows:
            if deadline and time.monotonic() > deadline:
                print("[extrair] tempo esgotado — continua na próxima execução")
                break
            try:
                if extract_one(conn, company, version) is not None:
                    done += 1
            except llm.BudgetExceeded as e:
                print(f"[extrair] {e} — parando com {done} extraídas")
                break
    else:
        batch_size = workers * 4
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for start in range(0, len(rows), batch_size):
                if deadline and time.monotonic() > deadline:
                    print("[extrair] tempo esgotado — continua na próxima execução")
                    break
                batch = rows[start:start + batch_size]
                prompts = [build_prompt(c, db.get_site_page(conn, c["id"]),
                                        db.company_sources(conn, c["id"]),
                                        db.max_points(conn, c["id"]),
                                        db.company_categories(conn, c["id"]), version)
                           for c in batch]
                try:
                    results = list(pool.map(_ask_facts, prompts))
                except llm.BudgetExceeded as e:
                    print(f"[extrair] {e} — parando com {done} extraídas")
                    break
                for company, facts in zip(batch, results):
                    if facts is not None:
                        db.save_extraction(conn, company["id"], version, llm.MODEL, facts)
                        done += 1
                conn.commit()
    print(f"[extrair] {done} empresas com fatos extraídos ({version})")
    return done


def describe(facts: dict | None) -> str:
    """Texto curto usado em embeddings e tendências."""
    if not facts:
        return ""
    return json.dumps({k: facts.get(k) for k in ("summary_pt", "niche", "pain",
                                                 "br_customer_segment")}, ensure_ascii=False)
