"""Escavação: de uma empresa da fila para um dossiê completo.

Fluxo por empresa: fatos (extração v2) → concorrentes sugeridos (LLM) →
confirmação e avaliações 1-3★ na Play Store e na App Store → buscas reais
(Google BR, Reclame Aqui, G2, América Latina) → mapa de dores (LLM) →
veredito do mercado BR (LLM, só com links reais) → TAM por CNAE → score
posterior (código) → ângulo de ataque (LLM) → kit de validação (posterior alto).

Três níveis de falha: busca/loja (registra e segue) → dossiê (record_dig_failure,
3 tentativas) → etapa (`run` nunca levanta, exceto o teto da execução, que
precisa parar o pipeline de LLM). Avaliações cruas nunca vão para o banco.
"""
from app import db, extract, llm, queue, resolve, score
from app.config import get
from app.dig import appstore, market, pains, playstore, tam, web, writer

SUGGEST_PROMPT = "dig_suggest_v2"
MAX_SUGGESTIONS = 5

# Sites que listam empresas mas não são a empresa: o domínio deles não identifica
# o concorrente (senão todo concorrente achado no Reclame Aqui viraria um só).
DIRECTORY_DOMAINS = {
    "reclameaqui.com.br", "g2.com", "capterra.com", "capterra.com.br", "getapp.com",
    "getapp.com.br", "softwareadvice.com", "trustpilot.com", "b2bstack.com.br",
    "crunchbase.com", "glassdoor.com", "glassdoor.com.br", "facebook.com", "instagram.com",
    "tiktok.com", "google.com", "wikipedia.org", "jusbrasil.com.br", "medium.com",
    "startupbase.com.br", "distrito.me", "exame.com", "globo.com", "uol.com.br",
    "estadao.com.br", "folha.uol.com.br", "infomoney.com.br", "tecmundo.com.br",
    "canaltech.com.br", "techtudo.com.br", "apple.com", "play.google.com",
}


# Status que o dono deu pelas Issues: uma investigação manual não os rebaixa para "dug".
OWNER_STATUSES = ("validating", "building")


class DossierTooExpensive(Exception):
    """O dossiê passou do teto de custo por dossiê."""


def _field(company, key: str):
    try:
        return company[key]
    except (KeyError, IndexError, TypeError):
        return None


def _spend(tokens: dict, tokens_in: int, tokens_out: int) -> None:
    """Soma os tokens do dossiê e aborta se o custo passou do teto por dossiê."""
    tokens["in"] += int(tokens_in or 0)
    tokens["out"] += int(tokens_out or 0)
    spent = llm.cost_usd(tokens["in"], tokens["out"])
    if spent > tokens["ceiling"]:
        raise DossierTooExpensive(f"US${spent:.4f} > teto US${tokens['ceiling']:.2f}")


def _competitor_domain(url: str | None, name: str) -> str | None:
    """Domínio do concorrente BR, ou None se o link é de um diretório/notícia."""
    try:
        _, domain = resolve.site_key(url, name)
    except Exception:
        return None
    if not domain or domain in DIRECTORY_DOMAINS:
        return None
    return domain


def suggest_competitors(company, facts: dict) -> tuple[list[dict], int, int]:
    """3-5 apps concorrentes para procurar nas lojas. Falha do LLM propaga (dossiê falha)."""
    prompt = llm.load_prompt(SUGGEST_PROMPT).format(
        name=_field(company, "name") or "", tagline=_field(company, "tagline") or "",
        summary=facts.get("summary_pt") or "n/d", niche=facts.get("niche") or "n/d",
        pain=facts.get("pain") or "n/d", segment=facts.get("br_customer_segment") or "n/d")
    data, t_in, t_out = llm.ask_json(prompt, max_tokens=600)
    raw = data.get("competitors") if isinstance(data, dict) else None
    suggestions = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict):
            continue
        term = str(s.get("search_term") or s.get("name") or "").strip()[:80]
        if term and term.lower() not in {x["search_term"].lower() for x in suggestions}:
            suggestions.append({"name": str(s.get("name") or term)[:120], "search_term": term})
    return suggestions[:MAX_SUGGESTIONS], t_in, t_out


def collect_stores(suggestions: list[dict], cfg: dict) -> tuple[list[dict], dict[str, list[str]]]:
    """Confirma cada sugestão na Play Store e na App Store e coleta as avaliações 1-3★."""
    per_star = int(get(cfg, "fontes.escavacao.reviews_per_star", playstore.REVIEWS_PER_STAR))
    pause = float(get(cfg, "fontes.escavacao.pause_s", playstore.PAUSE_S))
    competitors: list[dict] = []
    reviews_by_app: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for s in suggestions:
        term = s["search_term"]
        for found, collect in (
                (playstore.confirm_on_play_store(term, pause), playstore.collect_negative_reviews),
                (appstore.confirm_on_app_store(term, pause), appstore.collect_negative_reviews)):
            if not found or (found["store"], found["app_id"]) in seen:
                continue
            seen.add((found["store"], found["app_id"]))
            revs = collect(found["app_id"], found["country"], per_star, pause)
            found["reviews_collected"] = len(revs)
            competitors.append(found)
            if revs:
                reviews_by_app.setdefault(found["name"], []).extend(revs)
    return competitors, reviews_by_app


def _web_snippets(gathered: dict) -> list[dict]:
    """Reclame Aqui + G2 como reclamações curtas para o mapa de dores."""
    out = []
    for source in ("reclame_aqui", "g2"):
        for r in gathered.get(source) or []:
            out.append({"source": source, "title": r.get("title", ""),
                        "snippet": str(r.get("snippet") or "")[:300]})
    return out


def _catalyst_dicts(cfg: dict, ids: list[str]) -> list[dict]:
    wanted = set(ids or [])
    return [c for c in cfg.get("catalisadores", []) or [] if c.get("id") in wanted]


def _ensure_facts(conn, company) -> dict | None:
    facts = db.latest_extraction(conn, company["id"])
    if facts:
        return facts
    return extract.extract_one(conn, company)


def _verdict_text(verdict: str | None, rationale: str | None) -> str:
    return f"{verdict or 'desconhecido'}: {rationale or 'sem busca real'}"


def _build(conn, company, facts: dict, cfg: dict, tokens: dict) -> int:
    """Escava e grava o dossiê. Levanta em falha (quem chama registra a tentativa)."""
    company_id = company["id"]
    suggestions, t_in, t_out = suggest_competitors(company, facts)
    _spend(tokens, t_in, t_out)
    if not suggestions:
        fallback = (facts.get("search_keywords_pt") or [])[:1] + [_field(company, "name")]
        suggestions = [{"name": t, "search_term": t} for t in fallback if t]

    competitors, reviews_by_app = collect_stores(suggestions, cfg)
    gathered = web.gather(company, facts, cfg)

    clusters: list[dict] = []
    res = pains.cluster(facts.get("niche") or "", reviews_by_app, _web_snippets(gathered))
    if res:
        clusters, t_in, t_out = res
        _spend(tokens, t_in, t_out)

    found = None
    res = market.evaluate(conn, company, facts, gathered, competitors)
    if res:
        found, t_in, t_out = res
        _spend(tokens, t_in, t_out)
    verdict = found["verdict"] if found else "desconhecido"
    rationale = (found or {}).get("rationale") or (
        "sem busca real (SERPER_API_KEY ausente, buscas falharam ou veredito inválido)")

    size = tam.lookup(facts.get("br_cnae_guess"))
    if size:
        size["segment"] = facts.get("br_customer_segment") or None

    posterior = score.compute(facts, db.latest_observations(conn, company_id),
                              db.company_sources(conn, company_id),
                              db.max_points(conn, company_id), None, cfg, verdict=verdict)
    catalysts = _catalyst_dicts(cfg, posterior.get("catalysts") or [])

    res = writer.attack_angle(company, facts, market.players_block(competitors), clusters,
                              found, catalysts, size)
    if res is None:  # o ângulo é o coração do dossiê: sem ele, conta como falha e tenta de novo
        raise RuntimeError("ângulo de ataque não foi gerado")
    angle, t_in, t_out = res
    _spend(tokens, t_in, t_out)

    kit_data, kit_reason = None, None
    if posterior["total"] >= float(get(cfg, "fontes.kit.min_posterior", 8.0)) \
            and not posterior.get("kill_reason"):
        res = writer.kit(company, facts, clusters, _verdict_text(verdict, rationale), angle)
        if res:
            kit_data, t_in, t_out = res
            _spend(tokens, t_in, t_out)
            kit_reason = "score"

    prior = db.latest_score(conn, company_id, "prior")
    db.save_score(conn, company_id, "posterior", posterior, get(cfg, "pesos.version", "pesos"))
    dossier_id = db.save_dossier(conn, company_id, {
        "attack_angle": angle, "br_verdict": verdict, "br_rationale": rationale,
        "br_evidence": (found or {}).get("evidence") or [],
        "latam": (found or {}).get("latam") or [],
        "web_complaints": (found or {}).get("web_complaints") or [],
        "tam": size, "catalysts": posterior.get("catalysts") or [],
        "score_prior": prior["total"] if prior else None,
        "score_posterior": posterior["total"], "subscores": posterior["subscores"],
        "kit": kit_data, "kit_reason": kit_reason,
        "cost_usd": round(llm.cost_usd(tokens["in"], tokens["out"]), 6),
        "tokens_in": tokens["in"], "tokens_out": tokens["out"]})
    db.save_app_competitors(conn, dossier_id, company_id, competitors)
    db.save_pain_clusters(conn, dossier_id, company_id, clusters)
    for c in (found or {}).get("br_competitors") or []:
        br_id = db.upsert_br_competitor(conn, c["name"], _competitor_domain(c["url"], c["name"]),
                                        facts.get("niche") or None,
                                        [{"title": c.get("title") or c["name"], "url": c["url"]}])
        db.link_br_competitor(conn, br_id, company_id, dossier_id, c["strength"])
    if company["status"] not in OWNER_STATUSES:
        db.mark_dug(conn, company_id)
    conn.commit()

    extras = [f"veredito {verdict}", f"posterior {posterior['total']}"]
    if kit_data:
        extras.append("kit gerado")
    cost = llm.cost_usd(tokens["in"], tokens["out"])
    print(f"[escava] dossiê de '{company['name']}': {len(competitors)} apps, "
          f"{len(clusters)} dores, {', '.join(extras)}, US${cost:.4f}")
    return dossier_id


def dig_company(conn, company_id: int, cfg: dict) -> int | None:
    """Escava uma empresa e devolve o id do dossiê. None em falha (a tentativa é registrada).

    Só o teto da execução (llm.BudgetExceeded) sai daqui: ele precisa parar a etapa.
    """
    company = db.get_company(conn, company_id)
    if company is None:
        print(f"[escava] empresa id={company_id} não existe")
        return None
    tokens = {"in": 0, "out": 0,
              "ceiling": float(get(cfg, "fontes.escavacao.cost_ceiling_usd", 0.25))}
    conn.commit()  # o rollback de uma falha só pode desfazer o que este dossiê gravou
    try:
        facts = _ensure_facts(conn, company)
        if not facts:
            raise RuntimeError("sem extração de fatos")
        return _build(conn, company, facts, cfg, tokens)
    except llm.BudgetExceeded:
        raise
    except DossierTooExpensive as e:
        print(f"[escava] '{company['name']}' estourou o teto do dossiê: {e}")
    except Exception as e:
        print(f"[escava] falhou para '{company['name']}': {type(e).__name__}: {e}")
    try:
        # descarta gravações pela metade do dossiê (elas só começam depois de todas
        # as chamadas de LLM; extração e cache já foram commitados)
        conn.rollback()
        db.record_dig_failure(conn, company_id)
        conn.commit()
    except Exception as e:
        print(f"[escava] não consegui registrar a falha de '{company['name']}': {e}")
    return None


def run(conn, cfg: dict) -> int:
    """Escava o lote do dia da fila. Devolve quantos dossiês saíram.

    Para no teto da execução e repassa o BudgetExceeded (o pipeline registra).
    """
    if not llm.available():
        print("[escava] OPENAI_API_KEY ausente, pulando escavação")
        return 0
    batch = queue.next_batch(conn, cfg)
    if not batch:
        print("[escava] fila vazia")
        return 0
    done = 0
    try:
        for company in batch:
            if dig_company(conn, company["id"], cfg) is not None:
                done += 1
    except llm.BudgetExceeded as e:
        print(f"[escava] {e} — parando com {done} de {len(batch)} dossiê(s)")
        raise
    print(f"[escava] {done} de {len(batch)} dossiê(s) gerado(s)")
    return done


def weekly_kit(conn, cfg: dict) -> int | None:
    """Kit para o melhor dossiê da semana sem kit, mesmo abaixo do corte de score.

    A v1 exigia score ≥ 8 e nunca gerou kit; aqui sai um por semana, montado a
    partir do dossiê gravado (dores, veredito, ângulo). Reexecutar o domingo não
    gera um segundo. Nunca levanta.
    """
    if not get(cfg, "fontes.kit.weekly_best", True):
        return None
    if not llm.available():
        print("[kit] OPENAI_API_KEY ausente, sem kit da semana")
        return None
    since = db.days_ago_iso(7)
    if any(d["dossier"].get("kit_reason") == "weekly_best" for d in db.dossiers_since(conn, since)):
        print("[kit] o kit da semana já saiu, pulando")
        return None
    row = db.best_dossier_without_kit(conn, since,
                                      float(get(cfg, "fontes.kit.weekly_best_min", 5.0)))
    if row is None:
        print("[kit] nenhum dossiê da semana elegível para o kit")
        return None
    stored = db.get_dossier(conn, row["id"])
    company = stored["company"]
    facts = db.latest_extraction(conn, row["company_id"]) or {}
    dossier = stored["dossier"]
    try:
        res = writer.kit(company, facts, stored["pain_clusters"],
                         _verdict_text(dossier.get("br_verdict"), dossier.get("br_rationale")),
                         dossier.get("attack_angle"))
    except llm.BudgetExceeded as e:
        print(f"[kit] {e} — sem kit da semana")
        return None
    if res is None:
        return None
    data, t_in, t_out = res
    db.set_dossier_kit(conn, row["id"], data, "weekly_best", llm.cost_usd(t_in, t_out), t_in, t_out)
    conn.commit()
    print(f"[kit] kit da semana para '{company.get('name')}' (posterior {row['score_posterior']})")
    return row["id"]


def _probe_site(conn, company) -> None:
    """Lê o site da empresa nova (melhora muito a extração). Opcional: falhou, segue."""
    if not _field(company, "url") or db.get_site_page(conn, company["id"]):
        return
    try:
        from app.signals import site
        page = site.probe(company["url"])
        if page:
            db.save_site_page(conn, company["id"], page)
            conn.commit()
    except Exception as e:
        print(f"[escava] leitura do site de '{company['name']}' falhou: {e}")


def investigate(conn, term: str, cfg: dict) -> int | None:
    """Escavação manual (nome ou URL): cria a empresa se preciso e ignora a fila."""
    if not llm.available():
        print("[escava] OPENAI_API_KEY ausente, impossível investigar")
        return None
    term = (term or "").strip()
    if not term:
        return None
    try:
        url = term if term.lower().startswith("http") else None
        by_key = db.find_company_by_key(conn, resolve.site_key(url, term)[0]) if url else None
        rows = [by_key] if by_key is not None else db.find_companies(conn, term)
        if rows:
            company_id = rows[0]["id"]
        else:
            resolve.ingest(conn, [{"name": term, "url": url, "source": "manual",
                                   "kind": "manual", "external_id": term}])
            found = db.find_company_by_key(conn, resolve.site_key(url, term)[0])
            if found is None:
                rows = db.find_companies(conn, term)
                found = rows[0] if rows else None
            if found is None:
                print(f"[escava] não consegui criar a empresa '{term}'")
                return None
            company_id = found["id"]
        company = db.get_company(conn, company_id)
        print(f"[escava] investigando '{company['name']}' (id={company_id})")
        _probe_site(conn, company)
        if not db.latest_extraction(conn, company_id) and not extract.extract_one(conn, company):
            print(f"[escava] sem fatos extraídos para '{company['name']}'")
            return None
        score.run(conn, cfg, company_ids=[company_id])
        return dig_company(conn, company_id, cfg)
    except llm.BudgetExceeded as e:
        print(f"[escava] investigação parou no teto de custo: {e}")
    except Exception as e:
        print(f"[escava] investigação de '{term}' falhou: {type(e).__name__}: {e}")
    return None

