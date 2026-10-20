"""Wayback Machine (CDX): primeira captura do domínio com HTTP 200 → idade do site.

Site no ar há anos é sinal de que o negócio sobreviveu; domínio recém-nascido
num "lançamento" é o caso comum. Uma chamada por empresa, só com domínio próprio.
"""
import requests

from app.config import get

CDX = "https://web.archive.org/cdx/search/cdx"
TIMEOUT = 20
DEFAULT_UA = "garimpo-pessoal/2.0 (projeto de estudo)"


def parse(data) -> str | None:
    """[["timestamp"], ["20190314123456"]] → '2019-03-14'. Aceita com ou sem linha de cabeçalho."""
    if not isinstance(data, list):
        return None
    for row in data:
        value = row[0] if isinstance(row, list) and row else row
        value = str(value or "")
        if len(value) >= 8 and value[:8].isdigit():
            return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return None


def lookup(company, cfg: dict) -> list[dict]:
    domain = None
    try:
        domain = company["domain"]
        if not domain:
            return []
        ua = get(cfg, "fontes.sinais.user_agent", DEFAULT_UA)
        r = requests.get(CDX, params={"url": domain, "output": "json", "limit": 1,
                                      "fl": "timestamp", "filter": "statuscode:200"},
                         headers={"User-Agent": ua},
                         timeout=float(get(cfg, "fontes.sondagem.timeout_s", 10)) * 2)
        r.raise_for_status()
        first = parse(r.json() if (r.text or "").strip() else [])
    except Exception as e:
        print(f"[wayback] {domain or '?'} falhou: {e}")
        return []
    if not first:
        return []
    return [{"metric": "domain_first_seen", "value_num": None, "value_text": first,
             "source": "wayback"}]
