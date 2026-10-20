"""Mapa de dores: avaliações negativas das lojas + reclamações públicas viram 3-7 temas.

O LLM só agrupa e conta; os trechos citados são cortados a 200 caracteres e
saem sem autor (as avaliações cruas nunca vão para o banco). A saída é coagida
com cuidado porque vem de texto raspado — entrada potencialmente hostil.
"""
import re

from app import llm
from app.dig.playstore import sample_reviews

PROMPT = "dig_pains_v2"
SEVERITIES = ("alta", "media", "baixa")
EXCERPT_LIMIT = 200
WEB_SNIPPETS_CAP = 12


def _to_int(value) -> int:
    """'12', 12.0, '12x' e '~12' viram 12; o resto vira 0."""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        match = re.search(r"\d+", str(value or ""))
        return int(match.group()) if match else 0


def _reviews_block(sampled: dict[str, list[str]]) -> str:
    parts = []
    for app_name, revs in sampled.items():
        if revs:
            parts.append(f"## {app_name}\n" + "\n".join(f"- {r}" for r in revs))
    return "\n\n".join(parts) or "(sem avaliações)"


def _web_block(web_snippets: list[dict]) -> str:
    lines = [f"- [{s.get('source', 'web')}] {s.get('title', '')}: {s.get('snippet', '')}"
             for s in web_snippets[:WEB_SNIPPETS_CAP] if isinstance(s, dict)]
    return "\n".join(lines) or "(nenhuma)"


def normalize(data: dict) -> list[dict]:
    """Coerção defensiva: frequency int, severity no conjunto, trechos curtos. Nunca levanta."""
    clusters = data.get("clusters") if isinstance(data, dict) else None
    if not isinstance(clusters, list):
        return []
    out = []
    for c in clusters:
        if not isinstance(c, dict):
            continue
        theme = str(c.get("theme") or "").strip()[:200]
        if not theme:
            continue
        severity = str(c.get("severity") or "").strip().lower().replace("é", "e")
        examples = []
        for e in c.get("examples") or []:
            if not isinstance(e, dict):
                continue
            excerpt = str(e.get("excerpt") or "").strip()[:EXCERPT_LIMIT]
            if excerpt:
                examples.append({"app": str(e.get("app") or "?")[:100], "excerpt": excerpt})
        out.append({"theme": theme, "frequency": _to_int(c.get("frequency")),
                    "severity": severity if severity in SEVERITIES else "media",
                    "examples": examples[:3]})
    out.sort(key=lambda c: c["frequency"], reverse=True)
    return out[:7]


def cluster(niche: str, reviews_by_app: dict[str, list[str]],
            web_snippets: list[dict] | None = None) -> tuple[list[dict], int, int] | None:
    """Agrupa as dores. None se não há nada para agrupar ou o LLM falhou (orçamento propaga)."""
    web_snippets = web_snippets or []
    if not any(reviews_by_app.values()) and not web_snippets:
        return None
    prompt = llm.load_prompt(PROMPT).format(
        niche=niche or "n/d", reviews_block=_reviews_block(sample_reviews(reviews_by_app)),
        web_block=_web_block(web_snippets))
    try:
        data, t_in, t_out = llm.ask_json(prompt, max_tokens=2500)
    except llm.BudgetExceeded:
        raise
    except Exception as e:
        print(f"[escava] clusterização de dores falhou: {e}")
        return None
    return normalize(data), t_in, t_out
