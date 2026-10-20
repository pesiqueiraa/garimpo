"""Mercado brasileiro: UMA chamada de LLM transforma as buscas reais em veredito.

Entram os resultados do Google BR, Reclame Aqui, G2 e América Latina, os apps
confirmados nas lojas e o mapa de concorrentes brasileiros já conhecidos
(reaproveitado entre escavações). O LLM classifica e aponta evidências; o
código descarta todo link que não veio de um resultado real — o LLM nunca
inventa URL no dossiê.
"""
from app import db, llm
from app.dig import web

PROMPT = "dig_market_v2"
VERDICTS = ("sem_concorrente", "concorrente_fraco", "concorrente_forte")
SNIPPET_LIMIT = 220

_STR = {"type": "string"}
SCHEMA = {
    "name": "mercado_br",
    "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["verdict", "rationale", "evidence", "br_competitors", "latam",
                     "web_complaints"],
        "properties": {
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "rationale": _STR,
            "evidence": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["title", "url", "why"],
                "properties": {"title": _STR, "url": _STR, "why": _STR}}},
            "br_competitors": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "url", "strength"],
                "properties": {"name": _STR, "url": _STR,
                               "strength": {"type": "string", "enum": ["forte", "fraco"]}}}},
            "latam": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["country", "name", "url"],
                "properties": {"country": _STR, "name": _STR, "url": _STR}}},
            "web_complaints": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["source", "title", "url", "snippet"],
                "properties": {"source": {"type": "string", "enum": ["reclame_aqui", "g2"]},
                               "title": _STR, "url": _STR, "snippet": _STR}}},
        },
    },
}


def _field(company, key: str):
    try:
        return company[key]
    except (KeyError, IndexError, TypeError):
        return None


def _norm_url(url) -> str:
    return str(url or "").strip().rstrip("/")


def _results_block(results: list[dict]) -> str:
    if not results:
        return "(sem resultados)"
    return "\n".join(f"- {r.get('title', '')} — {r.get('url', '')} — "
                     f"{str(r.get('snippet') or '')[:SNIPPET_LIMIT]}" for r in results)


def players_block(competitors: list[dict]) -> str:
    """Resumo dos apps confirmados nas lojas (reusado no ângulo de ataque)."""
    store_names = {"play": "Play", "appstore": "App Store"}
    return "; ".join(
        f"{c.get('name')} ({store_names.get(c.get('store'), c.get('store'))} "
        f"{str(c.get('country') or '').upper()}, nota {c.get('rating') or '?'}, "
        f"{c.get('ratings_count') or '?'} avaliações)"
        for c in competitors) or "nenhum app encontrado nas lojas"


def niche_terms(facts: dict) -> list[str]:
    """Termos para achar concorrentes BR já conhecidos em nichos parecidos."""
    terms = [facts.get("niche"), facts.get("br_customer_segment")]
    terms += list(facts.get("search_keywords_pt") or [])
    return [str(t) for t in terms if t]


def _known_block(rows) -> tuple[str, dict[str, dict]]:
    """Texto do mapa de mercado conhecido + URLs de evidência já validadas antes."""
    lines, urls = [], {}
    for r in rows:
        evidence = db.from_json(r["evidence"], list, [])
        first = next((e for e in evidence if isinstance(e, dict) and e.get("url")), None)
        if first:
            urls[_norm_url(first["url"])] = {"title": first.get("title") or r["name"],
                                             "url": first["url"], "snippet": ""}
        lines.append(f"- {r['name']}" + (f" ({r['domain']})" if r["domain"] else "")
                     + f" — nicho: {r['niche'] or 'n/d'}, visto {r['seen_count']}x"
                     + (f" — {first['url']}" if first else ""))
    return "\n".join(lines) or "(nenhum)", urls


def build_prompt(company, facts: dict, gathered: dict, competitors: list[dict],
                 known_text: str) -> str:
    br_parts = [f'Busca "{q.get("query")}":\n{_results_block(q.get("results") or [])}'
                for q in gathered.get("br") or []]
    latam_parts = [f"País {cc}:\n{_results_block(results)}"
                   for cc, results in (gathered.get("latam") or {}).items()]
    return llm.load_prompt(PROMPT).format(
        name=_field(company, "name") or "", tagline=_field(company, "tagline") or "",
        niche=facts.get("niche") or "n/d", pain=facts.get("pain") or "n/d",
        segment=facts.get("br_customer_segment") or "n/d",
        players_block=players_block(competitors), known_block=known_text,
        br_block="\n\n".join(br_parts) or "(sem resultados)",
        reclame_block=_results_block(gathered.get("reclame_aqui") or []),
        g2_block=_results_block(gathered.get("g2") or []),
        latam_block="\n\n".join(latam_parts) or "(sem resultados)")


def _index(gathered: dict) -> dict:
    """URL normalizada -> resultado, separado por origem (para validar o que o LLM citou)."""
    br = {}
    for q in gathered.get("br") or []:
        for r in q.get("results") or []:
            br.setdefault(_norm_url(r.get("url")), r)
    complaints = {}
    for source in ("reclame_aqui", "g2"):
        for r in gathered.get(source) or []:
            complaints.setdefault(_norm_url(r.get("url")), {**r, "source": source})
    latam = {}
    for cc, results in (gathered.get("latam") or {}).items():
        for r in results or []:
            latam.setdefault(_norm_url(r.get("url")), {**r, "country": cc})
    for d in (br, complaints, latam):
        d.pop("", None)
    return {"br": br, "complaints": complaints, "latam": latam}


def validate(data: dict, gathered: dict, known_urls: dict | None = None) -> dict | None:
    """Mantém só o que aponta para resultados reais. None se o veredito for inválido."""
    if not isinstance(data, dict) or data.get("verdict") not in VERDICTS:
        got = data.get("verdict") if isinstance(data, dict) else type(data).__name__
        print(f"[escava] veredito inválido do LLM: {got!r}")
        return None
    idx = _index(gathered)
    any_url = {**(known_urls or {}), **idx["latam"], **idx["complaints"], **idx["br"]}

    def items(key: str) -> list[dict]:
        value = data.get(key)
        return [i for i in value if isinstance(i, dict)] if isinstance(value, list) else []

    evidence = []
    for e in items("evidence"):
        hit = any_url.get(_norm_url(e.get("url")))
        if hit:
            evidence.append({"title": str(hit.get("title") or e.get("title") or "")[:200],
                             "url": hit["url"], "why": str(e.get("why") or "")[:300]})

    competitors, seen = [], set()
    for c in items("br_competitors"):
        name = str(c.get("name") or "").strip()[:120]
        hit = any_url.get(_norm_url(c.get("url")))
        if not name or not hit or name.lower() in seen:
            continue
        seen.add(name.lower())
        strength = "forte" if str(c.get("strength") or "").lower().startswith("fort") else "fraco"
        competitors.append({"name": name, "url": hit["url"], "strength": strength,
                            "title": str(hit.get("title") or "")[:200]})

    latam = []
    for item in items("latam"):
        name = str(item.get("name") or "").strip()[:120]
        hit = idx["latam"].get(_norm_url(item.get("url")))
        if name and hit:
            latam.append({"country": hit["country"], "name": name, "url": hit["url"]})

    complaints = []
    for item in items("web_complaints"):
        hit = idx["complaints"].get(_norm_url(item.get("url")))
        if hit:
            complaints.append({"source": hit["source"], "title": str(hit.get("title") or "")[:200],
                               "url": hit["url"],
                               "snippet": str(hit.get("snippet") or "")[:SNIPPET_LIMIT]})

    if data["verdict"] != "sem_concorrente" and not evidence and not competitors:
        print(f"[escava] veredito {data['verdict']} sem nenhum link real que o sustente, descartado")
        return None
    dropped = sum(len(items(k)) for k in ("evidence", "br_competitors", "latam", "web_complaints")) \
        - len(evidence) - len(competitors) - len(latam) - len(complaints)
    if dropped:
        print(f"[escava] {dropped} item(ns) do veredito descartado(s): link fora dos resultados")
    return {"verdict": data["verdict"], "rationale": str(data.get("rationale") or "")[:600],
            "evidence": evidence[:4], "br_competitors": competitors[:8],
            "latam": latam[:6], "web_complaints": complaints[:6]}


def evaluate(conn, company, facts: dict, gathered: dict,
             competitors: list[dict]) -> tuple[dict, int, int] | None:
    """Veredito do mercado BR com busca real. None sem resultados, se o LLM falhar
    ou se o veredito for inválido (orçamento propaga)."""
    if not web.has_results(gathered):
        if gathered:  # sem chave o web.gather já avisou
            print("[escava] nenhuma busca trouxe resultado, veredito fica desconhecido")
        return None
    known_text, known_urls = _known_block(db.known_br_competitors(conn, niche_terms(facts)))
    prompt = build_prompt(company, facts, gathered, competitors, known_text)
    try:
        data, t_in, t_out = llm.ask_json(prompt, max_tokens=2500, schema=SCHEMA)
    except llm.BudgetExceeded:
        raise
    except Exception as e:
        print(f"[escava] veredito do mercado falhou: {e}")
        return None
    result = validate(data, gathered, known_urls)
    if result is None:
        return None
    return result, t_in, t_out
