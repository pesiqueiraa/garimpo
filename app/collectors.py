"""Coletores por fonte. Regra: API oficial primeiro, falha graciosa sempre.

Cada coletor retorna uma lista de dicts no formato:
{name, tagline, url, source, source_url, points, category}
"""
import os
import time

import requests

UA = {"User-Agent": "garimpo-pessoal/0.1 (projeto de estudo)"}
TIMEOUT = 20


def collect_hackernews(min_points: int = 10, hours: int = 26) -> list[dict]:
    """Show HN via API publica da Algolia (sem autenticacao)."""
    cutoff = int(time.time()) - hours * 3600
    url = "https://hn.algolia.com/api/v1/search_by_date"
    params = {
        "tags": "show_hn",
        "numericFilters": f"created_at_i>{cutoff},points>={min_points}",
        "hitsPerPage": 50,
    }
    try:
        r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        hits = r.json().get("hits", [])
    except Exception as e:
        print(f"[hackernews] falhou: {e}")
        return []

    items = []
    for h in hits:
        title = (h.get("title") or "").removeprefix("Show HN:").strip()
        if not title:
            continue
        items.append({
            "name": title.split("–")[0].split("-")[0].strip()[:80] or title[:80],
            "tagline": title,
            "url": h.get("url"),
            "source": "hackernews",
            "source_url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            "points": h.get("points", 0),
            "category": None,
        })
    print(f"[hackernews] {len(items)} itens")
    return items


PH_QUERY = """
query($after: DateTime!) {
  posts(order: VOTES, postedAfter: $after, first: 30) {
    edges { node {
      name tagline url votesCount
      topics(first: 3) { edges { node { name } } }
    } }
  }
}
"""


def collect_producthunt(hours: int = 26) -> list[dict]:
    """Product Hunt via API GraphQL oficial. Requer PH_TOKEN no ambiente.

    Token gratuito em https://www.producthunt.com/v2/oauth/applications
    (use o 'Developer Token' da sua aplicacao).
    """
    # strip: secret colado com \n/espaco quebraria o header Authorization
    token = (os.environ.get("PH_TOKEN") or "").strip()
    if not token:
        print("[producthunt] PH_TOKEN ausente, pulando (veja README)")
        return []

    from datetime import datetime, timedelta, timezone
    after = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        r = requests.post(
            "https://api.producthunt.com/v2/api/graphql",
            json={"query": PH_QUERY, "variables": {"after": after}},
            headers={**UA, "Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        edges = r.json()["data"]["posts"]["edges"]
    except Exception as e:
        print(f"[producthunt] falhou: {e}")
        return []

    items = []
    for e in edges:
        n = e["node"]
        topics = [t["node"]["name"] for t in n.get("topics", {}).get("edges", [])]
        items.append({
            "name": n["name"],
            "tagline": n.get("tagline"),
            "url": n.get("url"),
            "source": "producthunt",
            "source_url": n.get("url"),
            "points": n.get("votesCount", 0),
            "category": ", ".join(topics) or None,
        })
    print(f"[producthunt] {len(items)} itens")
    return items


SEASON_ORDER = {"winter": 0, "spring": 1, "summer": 2, "fall": 3}


def _batch_sort_key(slug: str) -> tuple[int, int]:
    # slugs no formato "winter-2026"; desconhecidos vao para o fim
    season, _, year = slug.partition("-")
    return (int(year) if year.isdigit() else 0, SEASON_ORDER.get(season, -1))


def collect_yc(batches: int = 3) -> list[dict]:
    """Empresas recentes do YC via espelho publico yc-oss (GitHub Pages)."""
    try:
        r = requests.get(
            "https://yc-oss.github.io/api/meta.json", headers=UA, timeout=TIMEOUT
        )
        r.raise_for_status()
        # so considera batches que ja tem empresas publicadas
        slugs = [s for s, b in r.json()["batches"].items() if b.get("count")]
        recent = sorted(slugs, key=_batch_sort_key, reverse=True)[:batches]
    except Exception as e:
        print(f"[yc] lista de batches falhou: {e}")
        return []

    items = []
    for slug in recent:
        try:
            r = requests.get(
                f"https://yc-oss.github.io/api/batches/{slug}.json",
                headers=UA, timeout=TIMEOUT,
            )
            r.raise_for_status()
            for c in r.json():
                items.append({
                    "name": c.get("name", ""),
                    "tagline": c.get("one_liner"),
                    "url": c.get("website"),
                    "source": "yc",
                    "source_url": c.get("url"),
                    "points": 0,
                    "category": ", ".join(c.get("industries", [])[:3]) or None,
                })
        except Exception as e:
            print(f"[yc] batch {slug} falhou: {e}")
    print(f"[yc] {len(items)} itens")
    return items


def collect_all() -> list[dict]:
    items = []
    items += collect_hackernews()
    items += collect_producthunt()
    items += collect_yc()
    return [i for i in items if i.get("name")]
