"""Hacker News via API pública da Algolia: Show HN (lançamentos) e Launch HN (empresas do YC).

Sem autenticação. O nome do produto sai do título: a v1 cortava no primeiro "-"
e quebrava nomes com hífen ("Foo-Bar"), por isso aqui só valem separadores com
espaço em volta (" – ", " — ", " - ", ": ").
"""
import html
import re
import time
from urllib.parse import urlsplit

import requests

from app.config import get

ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM = "https://news.ycombinator.com/item?id={}"
TIMEOUT = 20
DEFAULT_UA = "garimpo-pessoal/2.0 (projeto de estudo)"
SEPARATORS = (" – ", " — ", " - ", ": ")
BATCH_RE = re.compile(r"\(\s*YC\s+([A-Z]{1,2}\d{2})\s*\)", re.I)
# links do texto que não são o site do produto (demo em vídeo, agenda, redes)
NOT_SITE_HOSTS = ("ycombinator.com", "youtube.com", "youtu.be", "loom.com", "vimeo.com",
                  "x.com", "twitter.com", "linkedin.com", "calendly.com", "cal.com",
                  "imgur.com", "wikipedia.org", "arxiv.org", "docs.google.com")


def split_title(title: str) -> tuple[str, str]:
    """'Nome – descrição' → ('Nome', 'descrição'), cortando no separador que aparece primeiro."""
    title = (title or "").strip()
    low = title.lower()
    cut = None
    for sep in SEPARATORS:
        i = low.find(sep)
        if i > 0 and (cut is None or i < cut[0]):
            cut = (i, sep)
    if cut is None:
        return title, ""
    return title[:cut[0]].strip(), title[cut[0] + len(cut[1]):].strip()


def _clean_name(name: str) -> str:
    name = BATCH_RE.sub("", name)
    return re.sub(r"\s+", " ", name).strip(" \"'“”-–—:|,")[:80]


def parse_show(title: str) -> tuple[str, str]:
    """Título do Show HN → (nome, tagline). Sem separador, o título inteiro vira nome e tagline."""
    body = re.sub(r"^\s*show hn\s*:\s*", "", title or "", flags=re.I).strip()
    name, desc = split_title(body)
    return _clean_name(name) or body[:80], desc or body


def parse_launch(title: str) -> tuple[str, str, str | None]:
    """'Launch HN: Acme (YC W25) – faz X' → ('Acme', 'faz X', 'W25')."""
    body = re.sub(r"^\s*launch hn\s*:\s*", "", title or "", flags=re.I).strip()
    m = BATCH_RE.search(body)
    batch = m.group(1).upper() if m else None
    name, desc = split_title(re.sub(r"\s+", " ", BATCH_RE.sub(" ", body)))
    if m and not desc and m.start() > 0:
        # "Acme (YC W25) faz X": sem separador, o lote marca o fim do nome
        name, desc = body[:m.start()], body[m.end():]
    return _clean_name(name) or body[:80], desc.strip() or body, batch


def _search(params: dict, cfg: dict, label: str) -> list[dict] | None:
    ua = get(cfg, "fontes.sinais.user_agent", DEFAULT_UA)
    try:
        r = requests.get(ALGOLIA, params=params, headers={"User-Agent": ua}, timeout=TIMEOUT)
        r.raise_for_status()
        hits = r.json().get("hits")
    except Exception as e:
        print(f"[{label}] falhou: {e}")
        return None
    return [h for h in hits if isinstance(h, dict)] if isinstance(hits, list) else []


def link_in_text(story_text: str | None) -> str | None:
    """Primeiro link externo do texto do post — Launch HN quase sempre é post de texto sem URL."""
    text = html.unescape(story_text or "")
    for m in re.finditer(r"https?://[^\s\"'<>)\]]+", text):
        url = m.group(0).rstrip(".,;:!?")
        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
        if host and not any(host == s or host.endswith("." + s) for s in NOT_SITE_HOSTS):
            return url
    return None


def _item(h: dict, name: str, tagline: str, source: str) -> dict:
    return {"name": name, "tagline": tagline,
            "url": h.get("url") or link_in_text(h.get("story_text")),
            "source": source, "kind": "launch", "external_id": str(h.get("objectID")),
            "source_url": HN_ITEM.format(h.get("objectID")),
            "points": int(h.get("points") or 0), "category": None,
            "payload": {"comments": h.get("num_comments"), "created_at": h.get("created_at")}}


def show_hn(cfg: dict, cutoff: int) -> list[dict]:
    min_points = int(get(cfg, "fontes.hackernews.min_points", 10))
    hits = _search({"tags": "show_hn", "hitsPerPage": 50,
                    "numericFilters": f"created_at_i>{cutoff},points>={min_points}"},
                   cfg, "hackernews")
    items = []
    for h in hits or []:
        if not h.get("objectID") or not (h.get("title") or "").strip():
            continue
        name, tagline = parse_show(h["title"])
        items.append(_item(h, name, tagline, "hackernews"))
    print(f"[hackernews] {len(items)} itens (Show HN)")
    return items


def launch_hn(cfg: dict, cutoff: int) -> list[dict]:
    """Launch HN não é tag da Algolia: busca no título e filtra o prefixo."""
    hits = _search({"query": "Launch HN", "tags": "story", "hitsPerPage": 50,
                    "restrictSearchableAttributes": "title",
                    "numericFilters": f"created_at_i>{cutoff}"}, cfg, "launch_hn")
    items = []
    for h in hits or []:
        title = (h.get("title") or "").strip()
        if not h.get("objectID") or not title.lower().startswith("launch hn:"):
            continue
        name, tagline, batch = parse_launch(title)
        item = _item(h, name, tagline, "launch_hn")
        if batch:
            item["observations"] = [{"metric": "yc_batch", "value_num": None, "value_text": batch}]
        items.append(item)
    print(f"[launch_hn] {len(items)} itens")
    return items


def collect(cfg: dict) -> list[dict]:
    """Show HN das últimas `hours` horas com pontos mínimos; Launch HN se ligado."""
    try:
        cutoff = int(time.time() - float(get(cfg, "fontes.hackernews.hours", 26)) * 3600)
        items = show_hn(cfg, cutoff)
        if get(cfg, "fontes.hackernews.launch_hn", True):
            items += launch_hn(cfg, cutoff)
        return items
    except Exception as e:
        print(f"[hackernews] erro inesperado: {e}")
        return []
