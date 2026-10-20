"""Play Store: confirma concorrentes e coleta avaliações negativas (1-3★).

Loja BR primeiro (é o mercado que interessa); sem resultado, tenta a US.
Scraping leve: pausa entre requisições e limite de avaliações por estrela.
As avaliações cruas ficam só em memória — o banco recebe apenas o destilado.
As funções da biblioteca ficam no nível do módulo para os testes trocarem.
"""
import time

from google_play_scraper import Sort
from google_play_scraper import app as gp_app
from google_play_scraper import reviews as gp_reviews
from google_play_scraper import search as gp_search

REVIEWS_PER_STAR = 70
REVIEW_CHAR_LIMIT = 300
CLUSTER_REVIEW_CAP = 150
PAUSE_S = 1.5

STORE_LANG = {"br": "pt", "us": "en"}


def confirm_on_play_store(search_term: str, pause_s: float = PAUSE_S) -> dict | None:
    """Busca o termo na loja BR; sem resultado, tenta US. None se nada.

    Pula "tiles" editoriais sem appId (a busca da Play às vezes devolve um
    destaque antes do app de verdade).
    """
    if not search_term:
        return None
    for country in ("br", "us"):
        lang = STORE_LANG[country]
        try:
            hits = gp_search(search_term, lang=lang, country=country, n_hits=3)
        except Exception as e:
            print(f"[escava] play: busca '{search_term}' ({country}) falhou: {e}")
            hits = []
        hit = next((h for h in hits or [] if isinstance(h, dict) and h.get("appId")), None)
        if hit is None:
            if country == "br":
                time.sleep(pause_s)
            continue
        details = {}
        try:
            details = gp_app(hit["appId"], lang=lang, country=country) or {}
        except Exception:
            pass  # segue com os dados da busca
        time.sleep(pause_s)
        return {
            "app_id": hit["appId"],
            "name": hit.get("title") or search_term,
            "store": "play",
            "country": country,
            "rating": details.get("score", hit.get("score")),
            "ratings_count": details.get("ratings"),
            "reviews_collected": 0,
        }
    return None


def collect_negative_reviews(app_id: str, country: str, per_star: int = REVIEWS_PER_STAR,
                             pause_s: float = PAUSE_S) -> list[str]:
    """Até `per_star` avaliações por estrela (1, 2, 3), com pausa entre requisições."""
    lang = STORE_LANG.get(country, "en")
    texts: list[str] = []
    for star in (1, 2, 3):
        try:
            batch, _ = gp_reviews(app_id, lang=lang, country=country, count=per_star,
                                  filter_score_with=star, sort=Sort.NEWEST)
        except Exception as e:
            print(f"[escava] play: avaliações {star}★ de {app_id} falharam: {e}")
            batch = []
        texts += [r["content"] for r in batch or []
                  if isinstance(r, dict) and isinstance(r.get("content"), str) and r["content"].strip()]
        time.sleep(pause_s)
    return texts


def sample_reviews(reviews_by_app: dict[str, list[str]],
                   cap: int = CLUSTER_REVIEW_CAP) -> dict[str, list[str]]:
    """Trunca cada avaliação e limita o total, dividido igualmente entre os apps.

    Mantém o prompt de clusterização barato e evita que um app domine os temas.
    """
    if not reviews_by_app:
        return {}
    per_app = max(1, cap // len(reviews_by_app))
    return {name: [r[:REVIEW_CHAR_LIMIT] for r in revs[:per_app]]
            for name, revs in reviews_by_app.items()}
