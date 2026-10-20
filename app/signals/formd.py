"""Form D da SEC (EDGAR full-text search): captações privadas registradas nos EUA.

Sinal FRACO: a busca é pelo nome, e nomes curtos ou comuns colidem com outras
empresas (um "Acme" qualquer em Delaware). Por isso só contamos registros cujo
nome declarado começa com o nome normalizado da empresa (fundos e SPVs com o
mesmo nome ficam de fora) e pulamos nomes com menos de 4 letras ou genéricos. A SEC exige um contato no User-Agent
(fontes.sinais.sec_user_agent) e limita a 10 requisições por segundo.
"""
import requests

from app.config import get
from app.resolve import normalize_name

URL = "https://efts.sec.gov/LATEST/search-index"
TIMEOUT = 20
DEFAULT_SEC_UA = "garimpo-pessoal/2.0 garimpo@example.com"
GENERIC = {
    "app", "apps", "labs", "data", "cloud", "health", "capital", "group", "global", "digital",
    "solutions", "systems", "software", "tech", "technologies", "ventures", "partners", "holdings",
    "media", "studio", "studios", "network", "services", "consulting", "energy", "finance",
    "financial", "fund", "insurance", "home", "pay", "money", "bank", "care", "learn", "school",
    "market", "shop", "store", "travel", "food", "mobile", "online", "smart", "open", "next",
    "first", "prime", "pro", "plus", "one", "hub", "flow", "base", "stack", "works", "point",
    "link", "spark", "bright", "north", "south", "blue", "green", "red", "apex", "summit",
    "atlas", "nova", "zen", "pulse", "core", "edge", "wave", "venture", "trust", "estate",
}


def usable_name(name_key: str | None) -> bool:
    return bool(name_key) and len(name_key) >= 4 and name_key not in GENERIC


def _same_company(display_name: str, name_key: str) -> bool:
    """'Acme Invoices, Inc. (CIK 0001234567)' casa com 'acmeinvoices'; fundos com o nome não."""
    norm = normalize_name(str(display_name).split("(CIK")[0])
    if not norm.startswith(name_key):
        return False
    rest = norm[len(name_key):]
    # "Acme Capital Fund I, LP" é o fundo de alguém, não a startup captando
    return not ("fund" in rest or "spv" in rest or rest.endswith("lp"))


def parse(data: dict, name_key: str) -> tuple[int, str | None]:
    """Conta os Form D cujo nome declarado começa com o nome da empresa; devolve (quantos, último)."""
    hits = ((data or {}).get("hits") or {}).get("hits") or []
    count, last = 0, None
    for h in hits if isinstance(hits, list) else []:
        src = (h or {}).get("_source") or {}
        names = src.get("display_names") or []
        if isinstance(names, str):
            names = [names]
        if not any(_same_company(n, name_key) for n in names):
            continue
        count += 1
        filed = str(src.get("file_date") or "")[:10]
        if filed and (last is None or filed > last):
            last = filed
    return count, last


def lookup(company, cfg: dict) -> list[dict]:
    try:
        name, name_key = company["name"], company["name_key"]
    except Exception:
        return []
    if not usable_name(name_key):
        return []
    ua = get(cfg, "fontes.sinais.sec_user_agent", DEFAULT_SEC_UA)
    try:
        r = requests.get(URL, params={"q": f'"{name}"', "forms": "D"},
                         headers={"User-Agent": ua, "Accept": "application/json"}, timeout=TIMEOUT)
        r.raise_for_status()
        count, last = parse(r.json(), name_key)
    except Exception as e:
        print(f"[sec_formd] '{name}' falhou: {e}")
        return []
    if not count:
        return []
    out = [{"metric": "formd_filings", "value_num": count, "value_text": None, "source": "sec_formd"}]
    if last:
        out.append({"metric": "formd_last", "value_num": None, "value_text": last, "source": "sec_formd"})
    return out
