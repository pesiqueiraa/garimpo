"""Product Hunt via API GraphQL oficial. Requer PH_TOKEN (opcional: sem ele a fonte é pulada).

Token gratuito em https://www.producthunt.com/v2/oauth/applications ("Developer Token").
Na v1 a fonte devolveu 1 item em 6 semanas sem nenhum aviso: o GraphQL responde
HTTP 200 com {"errors": [...]} (token inválido, campo inexistente, cota) e o
código só via um KeyError genérico. Aqui os erros da API são logados por extenso.
"""
import os
from datetime import datetime, timedelta, timezone

import requests

from app.config import get

API = "https://api.producthunt.com/v2/api/graphql"
TIMEOUT = 20
DEFAULT_UA = "garimpo-pessoal/2.0 (projeto de estudo)"

QUERY = """
query($after: DateTime!, $first: Int!) {
  posts(order: VOTES, postedAfter: $after, first: $first) {
    edges { node {
      id slug name tagline url website votesCount createdAt
      topics(first: 3) { edges { node { name } } }
    } }
  }
}
"""


def _topics(node: dict) -> list[str]:
    edges = ((node.get("topics") or {}).get("edges")) or []
    return [((e or {}).get("node") or {}).get("name") for e in edges
            if ((e or {}).get("node") or {}).get("name")]


def parse(data: dict) -> list[dict]:
    """Resposta do GraphQL → itens. `website` é o link de redirect do PH; a sondagem resolve depois."""
    edges = (((data or {}).get("data") or {}).get("posts") or {}).get("edges") or []
    items = []
    for e in edges:
        n = (e or {}).get("node") or {}
        if not n.get("name"):
            continue
        items.append({
            "name": str(n["name"])[:80], "tagline": n.get("tagline"),
            "url": n.get("website") or n.get("url"),
            "source": "producthunt", "kind": "launch",
            "external_id": str(n.get("id") or n.get("slug") or n["name"]),
            "source_url": n.get("url"),
            "points": int(n.get("votesCount") or 0),
            "category": ", ".join(_topics(n)) or None,
            "payload": {"slug": n.get("slug"), "created_at": n.get("createdAt")},
        })
    return items


def collect(cfg: dict) -> list[dict]:
    """Posts mais votados das últimas `hours` horas."""
    # strip: secret colado com \n/espaço quebraria o header Authorization
    token = (os.environ.get("PH_TOKEN") or "").strip()
    if not token:
        print("[producthunt] PH_TOKEN ausente, pulando (veja README)")
        return []
    hours = float(get(cfg, "fontes.producthunt.hours", 26))
    first = int(get(cfg, "fontes.producthunt.limit", 30))
    after = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    ua = get(cfg, "fontes.sinais.user_agent", DEFAULT_UA)
    try:
        r = requests.post(API, json={"query": QUERY, "variables": {"after": after, "first": first}},
                          headers={"User-Agent": ua, "Authorization": f"Bearer {token}",
                                   "Accept": "application/json"},
                          timeout=TIMEOUT)
    except Exception as e:
        print(f"[producthunt] falhou: {e}")
        return []
    if r.status_code != 200:
        print(f"[producthunt] HTTP {r.status_code}: {(r.text or '')[:300]}")
        return []
    try:
        data = r.json()
    except Exception as e:
        print(f"[producthunt] resposta não é JSON: {e}")
        return []
    if not isinstance(data, dict):
        print("[producthunt] resposta inesperada (não é objeto JSON)")
        return []
    if data.get("errors"):
        msgs = [str((err or {}).get("message", err)) if isinstance(err, dict) else str(err)
                for err in data["errors"]] if isinstance(data["errors"], list) else [str(data["errors"])]
        print(f"[producthunt] erros da API: {'; '.join(msgs)[:500]}")
        return []
    try:
        items = parse(data)
    except Exception as e:
        print(f"[producthunt] formato inesperado: {e}")
        return []
    if not items:
        print(f"[producthunt] 0 itens (resposta: {str(data)[:200]})")
    else:
        print(f"[producthunt] {len(items)} itens")
    return items
