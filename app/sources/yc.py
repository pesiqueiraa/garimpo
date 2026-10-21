"""Y Combinator via espelho público yc-oss (GitHub Pages), sem autenticação.

Três usos:
- lotes recentes como descoberta (o que o YC acabou de financiar);
- "provadas": empresas antigas, ativas e com equipe grande entram como prova de
  tração — é o melhor indicador de modelo que já funcionou lá fora;
- lookup por domínio para os sinais (empresa vinda do HN/PH que é do YC).

Formato assumido do yc-oss: meta.json com {"batches": {"winter-2025": {"count": N}}}
e listas de empresas com name, slug, website, one_liner, team_size, status,
batch ("Winter 2021"), industries, industry, isHiring, url.
"""
import re
from datetime import date
from functools import lru_cache

import requests

from app.config import get
from app.resolve import site_key

BASE = "https://yc-oss.github.io/api"
TIMEOUT = 20
TIMEOUT_ALL = 60
DEFAULT_UA = "garimpo-pessoal/2.0 (projeto de estudo)"
SEASON_ORDER = {"winter": 0, "spring": 1, "summer": 2, "fall": 3}


def _get_json(url: str, cfg: dict | None, timeout: int = TIMEOUT):
    ua = get(cfg or {}, "fontes.sinais.user_agent", DEFAULT_UA)
    r = requests.get(url, headers={"User-Agent": ua}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _batch_sort_key(slug: str) -> tuple[int, int]:
    # slugs no formato "winter-2026"; desconhecidos vão para o fim
    season, _, year = slug.partition("-")
    return (int(year) if year.isdigit() else 0, SEASON_ORDER.get(season, -1))


def batch_year(batch: str | None) -> int | None:
    """'Winter 2021' → 2021; 'W21'/'S05'/'IK12' → 2021/2005/2012; resto → None."""
    if not batch or not isinstance(batch, str):
        return None
    m = re.search(r"(19|20)\d{2}", batch)
    if m:
        return int(m.group(0))
    m = re.fullmatch(r"\s*[A-Za-z]{1,3}\s?(\d{2})\s*", batch)
    return 2000 + int(m.group(1)) if m else None


def _industries(c: dict) -> list[str]:
    values = c.get("industries") if isinstance(c.get("industries"), list) else []
    if c.get("industry"):
        values = values + [c["industry"]]
    return [str(v) for v in values if v]


def _team(c: dict) -> int:
    try:
        return int(c.get("team_size") or 0)
    except (TypeError, ValueError):
        return 0


def observations(c: dict) -> list[dict]:
    """Equipe, status, lote e se está contratando — o que o YC publica de tração."""
    out = []
    if _team(c):
        out.append({"metric": "team_size", "value_num": _team(c), "value_text": None})
    if c.get("status"):
        out.append({"metric": "yc_status", "value_num": None, "value_text": str(c["status"])})
    if c.get("batch"):
        out.append({"metric": "yc_batch", "value_num": None, "value_text": str(c["batch"])})
    if c.get("isHiring") is not None:
        out.append({"metric": "is_hiring", "value_num": 1 if c.get("isHiring") else 0,
                    "value_text": None})
    return out


def _item(c: dict, source: str) -> dict | None:
    if not c.get("name"):
        return None
    slug = c.get("slug") or c.get("id") or c["name"]
    return {"name": str(c["name"])[:80], "tagline": c.get("one_liner"),
            "url": c.get("website") or None, "source": source, "kind": "listing",
            "external_id": str(slug),
            "source_url": c.get("url") or f"https://www.ycombinator.com/companies/{slug}",
            "points": 0, "category": ", ".join(_industries(c)[:3]) or None,
            "observations": observations(c),
            "payload": {"batch": c.get("batch"), "stage": c.get("stage")}}


def recent(cfg: dict) -> list[dict]:
    """Empresas dos `recent_batches` lotes mais novos que já têm empresas publicadas."""
    n = int(get(cfg, "fontes.yc.recent_batches", 2))
    if n <= 0:
        return []
    try:
        meta = _get_json(f"{BASE}/meta.json", cfg)
        batches = meta.get("batches") or {}
        slugs = [s for s, b in batches.items() if isinstance(b, dict) and b.get("count")]
        newest = sorted(slugs, key=_batch_sort_key, reverse=True)[:n]
    except Exception as e:
        print(f"[yc] lista de lotes falhou: {e}")
        return []
    items = []
    for slug in newest:
        try:
            companies = _get_json(f"{BASE}/batches/{slug}.json", cfg)
        except Exception as e:
            print(f"[yc] lote {slug} falhou: {e}")
            continue
        for c in companies if isinstance(companies, list) else []:
            item = _item(c, "yc") if isinstance(c, dict) else None
            if item:
                items.append(item)
    print(f"[yc] {len(items)} itens (lotes {', '.join(newest) or '-'})")
    return items


@lru_cache(maxsize=1)
def fetch_all() -> tuple:
    """Todas as empresas do YC, baixadas uma vez por processo (falha também fica em cache)."""
    try:
        data = _get_json(f"{BASE}/companies/all.json", None, timeout=TIMEOUT_ALL)
    except Exception as e:
        print(f"[yc] all.json falhou: {e}")
        return ()
    if not isinstance(data, list):
        print("[yc] all.json em formato inesperado")
        return ()
    return tuple(c for c in data if isinstance(c, dict))


def select_proven(companies, cfg: dict, today: date | None = None) -> list[dict]:
    """Filtra as provadas e roda a janela do dia (determinística: mesmo dia, mesma seleção)."""
    today = today or date.today()
    statuses = set(get(cfg, "fontes.yc.proven_status", ["Active", "Public"]))
    min_team = int(get(cfg, "fontes.yc.proven_min_team", 25))
    max_year = today.year - int(get(cfg, "fontes.yc.proven_min_age_years", 2))
    wanted = set(get(cfg, "fontes.yc.proven_industries", []) or [])
    per_run = int(get(cfg, "fontes.yc.proven_per_run", 15))
    pool = []
    for c in companies:
        year = batch_year(c.get("batch"))
        if (c.get("name") and c.get("status") in statuses and _team(c) >= min_team
                and year is not None and year <= max_year
                and (not wanted or wanted & set(_industries(c)))):
            pool.append(c)
    if not pool or per_run <= 0:
        return []
    pool.sort(key=lambda c: (-_team(c), str(c.get("name")).lower()))
    start = (today.toordinal() * per_run) % len(pool)
    count = min(per_run, len(pool))
    return [pool[(start + i) % len(pool)] for i in range(count)]


def proven(cfg: dict) -> list[dict]:
    chosen = select_proven(fetch_all(), cfg)
    items = [i for i in (_item(c, "yc_proven") for c in chosen) if i]
    print(f"[yc_proven] {len(items)} itens")
    return items


def collect(cfg: dict) -> list[dict]:
    """Lotes recentes + (se ligado) a janela diária de empresas provadas."""
    items: list[dict] = []
    try:
        items += recent(cfg)
    except Exception as e:
        print(f"[yc] erro inesperado nos lotes: {e}")
    if get(cfg, "fontes.yc.proven", True):
        try:
            items += proven(cfg)
        except Exception as e:
            print(f"[yc_proven] erro inesperado: {e}")
    return items


@lru_cache(maxsize=1)
def _by_domain() -> dict:
    index = {}
    for c in fetch_all():
        try:
            _, domain = site_key(c.get("website"), c.get("name"))
        except Exception:
            continue
        if domain and domain not in index:
            index[domain] = c
    return index


def lookup(company, cfg: dict) -> list[dict]:
    """Sinal: a empresa (pelo domínio registrável) está no YC? Devolve equipe/status/lote."""
    try:
        domain = company["domain"]
        if not domain:
            return []
        c = _by_domain().get(domain.lower())
        if c is None:
            return []
        return [{**o, "source": "yc"} for o in observations(c)]
    except Exception as e:
        print(f"[yc] lookup falhou: {e}")
        return []
