"""Busca real no Google (Serper.dev) para o dossiê: Brasil, Reclame Aqui, G2 e América Latina.

Etapa opcional: sem SERPER_API_KEY devolve {} e o dossiê segue com veredito
"desconhecido". Cada busca que falha é registrada e pulada — uma busca ruim
nunca derruba a escavação.
"""
import os

import requests

from app.config import get

SERPER_URL = "https://google.serper.dev/search"
TIMEOUT = 10
RESULTS_PER_QUERY = 8


def _api_key() -> str:
    # strip: secret colado com \n/espaço quebraria o header
    return (os.environ.get("SERPER_API_KEY") or "").strip()


def available() -> bool:
    return bool(_api_key())


def search(query: str, gl: str = "br", hl: str = "pt", n: int = RESULTS_PER_QUERY) -> list[dict]:
    """Top resultados orgânicos do Google no país `gl`. Erros propagam para o chamador."""
    r = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": _api_key(), "Content-Type": "application/json"},
        json={"q": query, "gl": gl, "hl": hl},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    organic = (r.json() or {}).get("organic") or []
    return [{"title": str(o.get("title") or ""), "url": str(o.get("link") or ""),
             "snippet": str(o.get("snippet") or "")}
            for o in organic[:n] if isinstance(o, dict) and o.get("link")]


def _safe_search(query: str, gl: str, hl: str, n: int) -> list[dict] | None:
    try:
        return search(query, gl=gl, hl=hl, n=n)
    except Exception as e:
        print(f"[escava] serper falhou para '{query}' ({gl}): {e}")
        return None


def _field(company, key: str):
    try:
        return company[key]
    except (KeyError, IndexError, TypeError):
        return None


def br_queries(company, facts: dict) -> list[str]:
    """Duas buscas no Google BR: palavras-chave do cliente, ou nicho/dor como fallback."""
    keywords = [k for k in facts.get("search_keywords_pt") or [] if k]
    name = _field(company, "name") or ""
    q1 = keywords[0] if keywords else f"app {facts.get('niche') or name}"
    q2 = (keywords[1] if len(keywords) > 1
          else facts.get("pain") or _field(company, "tagline") or name)
    return [f"{q1} Brasil", f"{q2} Brasil"]


def gather(company, facts: dict, cfg: dict) -> dict:
    """Roda as buscas do dossiê. {} sem chave.

    Retorna {"br": [{"query", "results"}], "reclame_aqui": [...], "g2": [...],
    "latam": {país: [...]}} — só com as buscas que deram certo.
    """
    if not available():
        print("[escava] SERPER_API_KEY ausente, sem busca real (veredito fica desconhecido)")
        return {}
    n = int(get(cfg, "fontes.escavacao.serper_results", RESULTS_PER_QUERY))
    name = _field(company, "name") or ""
    out: dict = {"br": [], "reclame_aqui": [], "g2": [], "latam": {}}

    for q in br_queries(company, facts):
        results = _safe_search(q, "br", "pt", n)
        if results is not None:
            out["br"].append({"query": q, "results": results})

    keywords = [k for k in facts.get("search_keywords_pt") or [] if k]
    kw = keywords[0] if keywords else (facts.get("niche") or name)
    if kw:
        results = _safe_search(f"site:reclameaqui.com.br {kw}", "br", "pt", n)
        if results is not None:
            out["reclame_aqui"] = results

    if name:
        results = _safe_search(f'site:g2.com "{name}" reviews', "us", "en", n)
        if results is not None:
            out["g2"] = results

    keywords_es = [k for k in facts.get("search_keywords_es") or [] if k]
    if keywords_es:
        for country in get(cfg, "fontes.escavacao.latam_countries", ["mx", "co", "ar"]) or []:
            results = _safe_search(keywords_es[0], str(country), "es", n)
            if results is not None:
                out["latam"][str(country)] = results
    return out


def has_results(gathered: dict) -> bool:
    """Há ao menos um resultado de busca para o LLM avaliar?"""
    if not gathered:
        return False
    if any(q.get("results") for q in gathered.get("br") or []):
        return True
    if gathered.get("reclame_aqui") or gathered.get("g2"):
        return True
    return any(gathered.get("latam", {}).values())
