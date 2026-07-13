"""Escavacao de concorrentes: Play Store + LLM viram dossie.

Fluxo por produto: sugestao de concorrentes (LLM) -> confirmacao na
Play Store (BR primeiro, US fallback) -> reviews 1-3 estrelas ->
clusterizacao de dores (LLM) -> veredito BR real (Serper) -> angulo de ataque (LLM) -> kit de validacao (LLM, score >= 8).
Reviews cruas nao sao persistidas; so o destilado vai para o banco.
"""
import os
import time

from google_play_scraper import app as gp_app
from google_play_scraper import reviews as gp_reviews
from google_play_scraper import search as gp_search
from google_play_scraper import Sort

from app import db, kit, llm, search

MAX_DOSSIERS_PER_RUN = 2
REVIEWS_PER_STAR = 70
REVIEW_CHAR_LIMIT = 300
CLUSTER_REVIEW_CAP = 150
PAUSE_S = 1.5
COST_CEILING_USD = 0.25
KIT_SCORE_MIN = 8

STORE_LANG = {"br": "pt", "us": "en"}


class BudgetExceeded(Exception):
    """Dossie passou do teto de custo em tokens."""


def confirm_on_play_store(search_term: str) -> dict | None:
    """Busca o termo na loja BR; sem resultado, tenta US. None se nada."""
    for store in ("br", "us"):
        lang = STORE_LANG[store]
        try:
            hits = gp_search(search_term, lang=lang, country=store, n_hits=3)
        except Exception as e:
            print(f"[escava] busca '{search_term}' ({store}) falhou: {e}")
            if store == "br":
                time.sleep(PAUSE_S)
            continue
        if not hits:
            if store == "br":
                time.sleep(PAUSE_S)
            continue
        hit = hits[0]
        details = {}
        try:
            details = gp_app(hit["appId"], lang=lang, country=store)
        except Exception:
            pass  # segue com os dados da busca
        time.sleep(PAUSE_S)
        return {
            "app_id": hit["appId"],
            "name": hit.get("title", search_term),
            "store": store,
            "rating": details.get("score", hit.get("score")),
            "ratings_count": details.get("ratings"),
        }
    return None


def collect_negative_reviews(app_id: str, store: str) -> list[str]:
    """Ate ~70 reviews por estrela (1, 2, 3), com pausa entre requisicoes."""
    lang = STORE_LANG[store]
    texts = []
    for star in (1, 2, 3):
        try:
            batch, _ = gp_reviews(app_id, lang=lang, country=store,
                                  count=REVIEWS_PER_STAR,
                                  filter_score_with=star, sort=Sort.NEWEST)
        except Exception as e:
            print(f"[escava] reviews {star}★ de {app_id} falharam: {e}")
            batch = []
        texts += [r["content"] for r in batch if r.get("content")]
        time.sleep(PAUSE_S)
    return texts


def sample_reviews(reviews_by_app: dict[str, list[str]],
                   cap: int = CLUSTER_REVIEW_CAP) -> dict[str, list[str]]:
    """Trunca cada review e limita o total, dividido igualmente entre apps."""
    if not reviews_by_app:
        return {}
    per_app = max(1, cap // len(reviews_by_app))
    return {name: [r[:REVIEW_CHAR_LIMIT] for r in revs[:per_app]]
            for name, revs in reviews_by_app.items()}


SUGGEST_PROMPT = """Voce e um analista de mercado de SaaS. Para o produto abaixo,
liste de 3 a 5 aplicativos concorrentes ou similares que provavelmente existem
na Google Play Store — inclua players globais E brasileiros, se conhecer.
Responda SOMENTE JSON:
{{"competitors": [{{"name": "nome do app", "search_term": "termo curto de busca"}}]}}

Produto: {name}
Descricao: {tagline}
Nicho: {niche}
Dor que resolve: {pain}"""

CLUSTER_PROMPT = """Voce e um analista de produto. Abaixo estao reviews negativas
(1-3 estrelas) de apps concorrentes no nicho "{niche}". Agrupe as reclamacoes
em 3 a 7 temas de dor, em portugues. Responda SOMENTE JSON:
{{"clusters": [{{"theme": "nome curto do tema",
  "frequency": <n de reviews que citam o tema>,
  "severity": "alta" | "media" | "baixa",
  "examples": [{{"app": "nome do app", "excerpt": "trecho literal curto"}}]}}]}}
Ordene por frequency decrescente. 2-3 examples por tema.

Reviews por app:
{reviews_block}"""

ANGLE_PROMPT = """Voce e um estrategista de SaaS focado em tropicalizar produtos
validados la fora para o Brasil (Pix, WhatsApp, nota fiscal, Simples Nacional).

Produto original: {name} — {tagline} (nicho: {niche})
Players encontrados na Play Store: {players_block}
Mapa de dores extraido de reviews 1-3 estrelas: {clusters_block}
Concorrencia no Google BR (busca real): {search_block}

Escreva o "angulo de ataque" para um empreendedor brasileiro: quais dores
negligenciadas atacar e como adaptar ao Brasil. Um paragrafo denso (max 120
palavras) seguido de 3 bullets de diferenciais concretos, separados por \\n.
Responda SOMENTE JSON: {{"attack_angle": "texto"}}"""


def _reviews_block(sampled: dict[str, list[str]]) -> str:
    parts = []
    for app_name, revs in sampled.items():
        joined = "\n".join(f"- {r}" for r in revs)
        parts.append(f"## {app_name}\n{joined}")
    return "\n\n".join(parts)


def _check_budget(tokens: dict) -> None:
    spent = llm.cost_usd(tokens["in"], tokens["out"])
    if spent > COST_CEILING_USD:
        raise BudgetExceeded(f"US${spent:.2f} > teto US${COST_CEILING_USD}")


def _excavate_one(conn, product) -> None:
    tokens = {"in": 0, "out": 0}

    data, t_in, t_out = llm.ask_json(SUGGEST_PROMPT.format(
        name=product["name"], tagline=product["tagline"] or "",
        niche=product["niche"] or "n/d", pain=product["pain"] or "n/d"))
    tokens["in"] += t_in; tokens["out"] += t_out
    _check_budget(tokens)
    suggestions = data.get("competitors", [])[:5]

    competitors, reviews_by_app, seen = [], {}, set()
    for s in suggestions:
        comp = confirm_on_play_store(s.get("search_term") or s.get("name", ""))
        if not comp or comp["app_id"] in seen:
            continue
        seen.add(comp["app_id"])
        revs = collect_negative_reviews(comp["app_id"], comp["store"])
        comp["reviews_collected"] = len(revs)
        competitors.append(comp)
        if revs:
            reviews_by_app[comp["name"]] = revs

    clusters = []
    if reviews_by_app:
        data, t_in, t_out = llm.ask_json(
            CLUSTER_PROMPT.format(niche=product["niche"] or "n/d",
                                  reviews_block=_reviews_block(sample_reviews(reviews_by_app))),
            max_tokens=2000)
        tokens["in"] += t_in; tokens["out"] += t_out
        _check_budget(tokens)
        clusters = data.get("clusters", [])

    players_block = ("; ".join(
        f"{c['name']} ({'BR' if c['store'] == 'br' else 'global'}, "
        f"nota {c['rating']}, {c['ratings_count'] or '?'} avaliacoes)"
        for c in competitors) or "nenhum app encontrado na Play Store")
    clusters_block = ("; ".join(
        f"{c['theme']} ({c['frequency']}x, {c['severity']})" for c in clusters)
        or "sem dados de reviews")

    verdict = None
    vres = search.verify_br_competition(product, players_block)
    if vres:
        verdict, t_in, t_out = vres
        tokens["in"] += t_in; tokens["out"] += t_out
        _check_budget(tokens)
        conn.execute(
            """UPDATE products SET br_verdict=?,
               opportunity_score=COALESCE(?, opportunity_score) WHERE id=?""",
            (verdict["verdict"], verdict.get("score_final"), product["id"]))
        conn.commit()

    search_block = ("; ".join(
        f"{e.get('title', '?')} ({e.get('url', '')})"
        for e in (verdict or {}).get("evidence", []))
        or "sem busca real — considere apenas os dados acima")
    if verdict and verdict.get("rationale"):
        search_block += f" | veredito: {verdict['verdict']} — {verdict['rationale']}"

    data, t_in, t_out = llm.ask_json(
        ANGLE_PROMPT.format(name=product["name"], tagline=product["tagline"] or "",
                            niche=product["niche"] or "n/d",
                            players_block=players_block, clusters_block=clusters_block,
                            search_block=search_block),
        max_tokens=1500, reasoning_effort="medium")
    tokens["in"] += t_in; tokens["out"] += t_out
    _check_budget(tokens)
    attack_angle = data.get("attack_angle", "")

    kit_data = None
    score_final = (verdict or {}).get("score_final")
    if score_final is not None and score_final >= KIT_SCORE_MIN:
        kres = kit.build_kit(product, clusters_block,
                             f"{verdict['verdict']}: {verdict.get('rationale', '')}")
        if kres:
            kit_data, t_in, t_out = kres
            tokens["in"] += t_in; tokens["out"] += t_out
            _check_budget(tokens)

    cost = llm.cost_usd(tokens["in"], tokens["out"])
    db.save_competitors(conn, product["id"], competitors)
    db.save_pain_clusters(conn, product["id"], clusters)
    db.save_dossier(conn, product["id"], attack_angle,
                    cost, tokens["in"], tokens["out"],
                    br_verdict_final=verdict["verdict"] if verdict else None,
                    br_evidence=verdict.get("evidence") if verdict else None,
                    score_final=score_final,
                    kit=kit_data)
    db.mark_dig(conn, product["id"], "done")
    extras = []
    if verdict:
        extras.append(f"veredito {verdict['verdict']}")
    if kit_data:
        extras.append("kit gerado")
    print(f"[escava] dossie de '{product['name']}': {len(competitors)} players, "
          f"{len(clusters)} dores{', ' + ', '.join(extras) if extras else ''}, US${cost:.4f}")


def excavate_all(conn) -> int:
    """Escava a fila do dia. Nunca levanta excecao — falha graciosa."""
    if not os.environ.get("OPENAI_API_KEY"):
        print("[escava] OPENAI_API_KEY ausente, pulando escavacao")
        return 0
    queue = db.dig_queue(conn, MAX_DOSSIERS_PER_RUN)
    if not queue:
        print("[escava] fila vazia (nenhum produto com score >= 7 pendente)")
        return 0

    done = 0
    for product in queue:
        try:
            _excavate_one(conn, product)
            done += 1
        except BudgetExceeded as e:
            print(f"[escava] '{product['name']}' estourou orcamento: {e}")
            db.mark_dig(conn, product["id"], "failed")
        except Exception as e:
            print(f"[escava] falhou para '{product['name']}': {e}")
            db.record_dig_failure(conn, product["id"])
    return done


def investigate(conn, product_id: int) -> bool:
    """Escava um produto especifico na hora — ignora fila, score e limite diario."""
    if not os.environ.get("OPENAI_API_KEY"):
        print("[escava] OPENAI_API_KEY ausente, impossivel investigar")
        return False
    product = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if product is None:
        print(f"[escava] produto id={product_id} nao existe")
        return False
    try:
        _excavate_one(conn, product)
        return True
    except Exception as e:
        print(f"[escava] investigacao de '{product['name']}' falhou: {e}")
        return False
