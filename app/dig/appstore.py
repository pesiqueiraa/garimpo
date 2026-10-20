"""App Store: confirma concorrentes (iTunes Search API) e lê avaliações negativas (RSS público).

Ambos são endpoints públicos e documentados da Apple, sem chave. O RSS de
avaliações devolve as mais recentes (até ~50 por página) de todas as estrelas;
aqui ficam só as de 1 a 3 e com teto, para equiparar ao volume da Play Store.
Nada de avaliação crua é persistido.
"""
import time

import requests

SEARCH_URL = "https://itunes.apple.com/search"
REVIEWS_URL = "https://itunes.apple.com/{cc}/rss/customerreviews/page=1/id={app_id}/sortby=mostrecent/json"
TIMEOUT = 10
PAUSE_S = 1.5
USER_AGENT = "garimpo-pessoal/2.0 (projeto de estudo)"


def _get_json(url: str, params: dict | None = None) -> dict | None:
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[escava] appstore: {url[:80]} falhou: {e}")
        return None
    return data if isinstance(data, dict) else None


def confirm_on_app_store(search_term: str, pause_s: float = PAUSE_S) -> dict | None:
    """Busca o termo na App Store BR; sem resultado, tenta US. None se nada."""
    if not search_term:
        return None
    for country in ("br", "us"):
        data = _get_json(SEARCH_URL, {"term": search_term, "country": country,
                                      "entity": "software", "limit": 3})
        results = (data or {}).get("results") or []
        hit = next((h for h in results if isinstance(h, dict) and h.get("trackId")), None)
        if hit is None:
            if country == "br":
                time.sleep(pause_s)
            continue
        time.sleep(pause_s)
        return {
            "app_id": str(hit["trackId"]),
            "name": hit.get("trackName") or search_term,
            "store": "appstore",
            "country": country,
            "rating": _to_float(hit.get("averageUserRating")),
            "ratings_count": _to_int(hit.get("userRatingCount")),
            "reviews_collected": 0,
        }
    return None


def parse_reviews(data: dict | None, cap: int) -> list[str]:
    """Extrai os textos das avaliações 1-3★ do JSON do RSS.

    `feed.entry` pode ser lista, um único objeto (quando há só uma avaliação) ou
    ausente. Nas versões antigas do feed a primeira entrada era o próprio app,
    sem `im:rating` — ela cai fora naturalmente.
    """
    feed = (data or {}).get("feed") or {}
    entries = feed.get("entry") or []
    if isinstance(entries, dict):
        entries = [entries]
    texts: list[str] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        rating = _to_int(((e.get("im:rating") or {}).get("label")))
        content = (e.get("content") or {}).get("label")
        if rating is None or not 1 <= rating <= 3:
            continue
        if isinstance(content, str) and content.strip():
            texts.append(content.strip())
        if len(texts) >= cap:
            break
    return texts


def collect_negative_reviews(app_id: str, country: str, per_star: int = 70,
                             pause_s: float = PAUSE_S) -> list[str]:
    """Avaliações recentes de 1 a 3★, limitadas a ~3 × per_star."""
    data = _get_json(REVIEWS_URL.format(cc=country, app_id=app_id))
    time.sleep(pause_s)
    return parse_reviews(data, cap=3 * per_star)


def _to_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
