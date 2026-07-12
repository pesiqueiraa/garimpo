"""Escavacao de concorrentes: Play Store + LLM viram dossie.

Fluxo por produto: sugestao de concorrentes (LLM) -> confirmacao na
Play Store (BR primeiro, US fallback) -> reviews 1-3 estrelas ->
clusterizacao de dores (LLM) -> angulo de ataque (LLM).
Reviews cruas nao sao persistidas; so o destilado vai para o banco.
"""
import time

from google_play_scraper import app as gp_app
from google_play_scraper import reviews as gp_reviews
from google_play_scraper import search as gp_search
from google_play_scraper import Sort

from app import db, llm

MAX_DOSSIERS_PER_RUN = 2
REVIEWS_PER_STAR = 70
REVIEW_CHAR_LIMIT = 300
CLUSTER_REVIEW_CAP = 150
PAUSE_S = 1.5
COST_CEILING_USD = 0.25

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
