"""Ranking Tranco (top 1 milhão de domínios, agregado de várias listas, resistente a manipulação).

Estar no top 1M já indica tráfego real. O zip (~10 MB) é baixado uma vez por
processo e fica em memória; a busca percorre o CSV em streaming e devolve só os
domínios pedidos — nunca monta um dict de 1 milhão de entradas.
"""
import csv
import io
import zipfile

import requests

from app.config import get

URL = "https://tranco-list.eu/top-1m.csv.zip"
TIMEOUT = 60
DEFAULT_UA = "garimpo-pessoal/2.0 (projeto de estudo)"

_cache: dict = {}


def _download(cfg: dict | None = None) -> bytes | None:
    """Bytes do zip, baixado uma vez por processo (falha também fica em cache: não insiste)."""
    if "zip" in _cache:
        return _cache["zip"]
    ua = get(cfg or {}, "fontes.sinais.user_agent", DEFAULT_UA)
    try:
        r = requests.get(URL, headers={"User-Agent": ua}, timeout=TIMEOUT)
        r.raise_for_status()
        _cache["zip"] = r.content
    except Exception as e:
        print(f"[tranco] download falhou: {e}")
        _cache["zip"] = None
    return _cache["zip"]


def ranks_in_zip(content: bytes, domains: set[str]) -> dict[str, int]:
    """Percorre o CSV (rank,domínio) dentro do zip e para quando achou todos."""
    wanted = {d.lower().removeprefix("www.") for d in domains if d}
    found: dict[str, int] = {}
    if not wanted or not content:
        return found
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
        if name is None:
            print("[tranco] zip sem CSV")
            return found
        with zf.open(name) as fh:
            for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace")):
                if len(row) < 2:
                    continue
                domain = row[1].strip().lower()
                if domain in wanted and domain not in found:
                    try:
                        found[domain] = int(row[0])
                    except ValueError:
                        continue
                    if len(found) == len(wanted):
                        break
    return found


def ranks_for(domains: set[str], cfg: dict | None = None) -> dict[str, int]:
    """{domínio: posição} só dos domínios pedidos que estão no top 1M. Nunca levanta."""
    try:
        domains = {d for d in (domains or set()) if d}
        if not domains:
            return {}
        content = _download(cfg)
        return ranks_in_zip(content, domains) if content else {}
    except Exception as e:
        print(f"[tranco] leitura falhou: {e}")
        return {}


def observation(rank: int) -> dict:
    return {"metric": "tranco_rank", "value_num": rank, "value_text": None, "source": "tranco"}


def lookup(company, cfg: dict) -> list[dict]:
    try:
        domain = (company["domain"] or "").lower()
    except Exception:
        return []
    if not domain:
        return []
    rank = ranks_for({domain}, cfg).get(domain)
    return [observation(rank)] if rank else []
