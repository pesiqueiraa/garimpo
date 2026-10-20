"""App Store: o "gap" entre o top faturamento dos EUA e o do Brasil (RSS público da Apple).

App que fatura bem nos EUA e não aparece no top brasileiro da mesma categoria
é candidato natural a tropicalização. Formato assumido do RSS legado
(itunes.apple.com/.../json): {"feed": {"entry": [{"im:name": {"label"},
"id": {"label": url, "attributes": {"im:id"}}, "link": {"attributes": {"href"}},
"category": {"attributes": {"label"}}, "summary": {"label"}}]}}.
"""
import requests

from app.config import get
from app.resolve import normalize_name

RSS = "https://itunes.apple.com/{cc}/rss/topgrossingapplications/limit={limit}/genre={genre}/json"
TIMEOUT = 20
DEFAULT_UA = "garimpo-pessoal/2.0 (projeto de estudo)"
MAX_ITEMS = 20


def _label(node) -> str | None:
    return node.get("label") if isinstance(node, dict) else None


def _attrs(node) -> dict:
    if isinstance(node, list):
        node = next((n for n in node if isinstance(n, dict)), {})
    return (node or {}).get("attributes") or {} if isinstance(node, dict) else {}


def fetch(cc: str, cfg: dict) -> list[dict] | None:
    """Entradas do ranking de um país, na ordem do ranking. None se falhar."""
    url = RSS.format(cc=cc, limit=int(get(cfg, "fontes.appstore.limit", 100)),
                     genre=get(cfg, "fontes.appstore.genre", 6000))
    ua = get(cfg, "fontes.sinais.user_agent", DEFAULT_UA)
    try:
        r = requests.get(url, headers={"User-Agent": ua}, timeout=TIMEOUT)
        r.raise_for_status()
        entries = ((r.json() or {}).get("feed") or {}).get("entry") or []
    except Exception as e:
        print(f"[appstore] ranking {cc} falhou: {e}")
        return None
    if isinstance(entries, dict):  # feed com uma entrada só vem como objeto
        entries = [entries]
    return [e for e in entries if isinstance(e, dict)]


def _app_id(entry: dict) -> str | None:
    return _attrs(entry.get("id")).get("im:id")


def gap(us: list[dict], br: list[dict]) -> list[dict]:
    """Apps do top EUA ausentes do top BR (por id e por nome normalizado)."""
    br_ids = {_app_id(e) for e in br} - {None}
    br_names = {normalize_name(_label(e.get("im:name"))) for e in br} - {""}
    items = []
    for position, e in enumerate(us, start=1):
        app_id, name = _app_id(e), _label(e.get("im:name"))
        if not app_id or not name:
            continue
        if app_id in br_ids or normalize_name(name) in br_names:
            continue
        link = _attrs(e.get("link")).get("href") or _label(e.get("id"))
        tagline = _label(e.get("summary"))
        items.append({
            "name": name[:80], "tagline": tagline[:300] if tagline else None,
            "url": link, "source": "appstore", "kind": "ranking",
            "external_id": f"us:{app_id}", "source_url": link, "points": 0,
            "category": _attrs(e.get("category")).get("label"),
            "observations": [{"metric": "appstore_rank_us", "value_num": position,
                              "value_text": None}],
            "payload": {"artist": _label(e.get("im:artist")), "rank_us": position},
        })
        if len(items) >= MAX_ITEMS:
            break
    return items


def collect(cfg: dict) -> list[dict]:
    try:
        us = fetch("us", cfg)
        if not us:
            return []
        br = fetch("br", cfg)
        if br is None:
            # sem o ranking BR não dá para saber o gap; melhor nada que um falso positivo
            return []
        items = gap(us, br)
        print(f"[appstore] {len(items)} itens ({len(us)} no top EUA, {len(br)} no top BR)")
        return items
    except Exception as e:
        print(f"[appstore] erro inesperado: {e}")
        return []
