"""Vagas abertas via APIs públicas de ATS (Greenhouse, Lever, Ashby), sem chave.

Empresa contratando é empresa com dinheiro e tração. O slug do quadro de vagas
é chutado a partir do domínio e do nome; no Greenhouse o nome do quadro é
conferido com o da empresa para não pegar a vaga de um homônimo. Provedor que
responde 404 → próximo; o primeiro com ≥ 1 vaga ganha.
"""
import time

import requests

from app.config import get
from app.resolve import normalize_name

TIMEOUT = 15
DEFAULT_UA = "garimpo-pessoal/2.0 (projeto de estudo)"
GREENHOUSE_BOARD = "https://boards-api.greenhouse.io/v1/boards/{}"
GREENHOUSE_JOBS = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
LEVER = "https://api.lever.co/v0/postings/{}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{}"


def slug_candidates(company) -> list[str]:
    """Primeiro rótulo do domínio ('acme' de acme.io) e o nome normalizado, sem repetição."""
    out = []
    domain = company["domain"] or ""
    label = domain.split(".")[0] if domain else ""
    for value in (label, label.replace("-", ""), company["name_key"] or ""):
        value = (value or "").lower().strip()
        if len(value) >= 2 and value not in out:
            out.append(value)
    return out


def _get(url: str, cfg: dict):
    """JSON da resposta; None em 404 (quadro inexistente); levanta nas outras falhas."""
    ua = get(cfg, "fontes.sinais.user_agent", DEFAULT_UA)
    r = requests.get(url, headers={"User-Agent": ua}, timeout=TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _matches(board_name: str | None, company) -> bool:
    """Nome do quadro ≈ nome da empresa ou rótulo do domínio (contém um ao outro)."""
    board = normalize_name(board_name)
    if not board:
        return False
    targets = {company["name_key"] or "", normalize_name((company["domain"] or "").split(".")[0])}
    return any(t and (t == board or (len(t) >= 4 and (t in board or board in t))) for t in targets)


def greenhouse(slug: str, company, cfg: dict) -> int | None:
    board = _get(GREENHOUSE_BOARD.format(slug), cfg)
    if not isinstance(board, dict) or not _matches(board.get("name"), company):
        return None
    data = _get(GREENHOUSE_JOBS.format(slug), cfg)
    jobs = (data or {}).get("jobs") if isinstance(data, dict) else None
    return len(jobs) if isinstance(jobs, list) else None


def lever(slug: str, company, cfg: dict) -> int | None:
    data = _get(LEVER.format(slug), cfg)
    return len(data) if isinstance(data, list) else None


def ashby(slug: str, company, cfg: dict) -> int | None:
    data = _get(ASHBY.format(slug), cfg)
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return len(jobs) if isinstance(jobs, list) else None


PROVIDERS = (("greenhouse", greenhouse), ("lever", lever), ("ashby", ashby))


def lookup(company, cfg: dict) -> list[dict]:
    try:
        slugs = slug_candidates(company)
    except Exception as e:
        print(f"[vagas] empresa inválida: {e}")
        return []
    pause = float(get(cfg, "fontes.sinais.jobs_pause_s", 0.5))
    calls = 0
    for slug in slugs:
        for provider, fetch in PROVIDERS:
            if calls:
                time.sleep(pause)
            calls += 1
            try:
                count = fetch(slug, company, cfg)
            except Exception as e:
                print(f"[{provider}] {slug} falhou: {e}")
                continue
            if count:
                return [{"metric": "open_jobs", "value_num": count, "value_text": slug,
                         "source": provider}]
    return []
