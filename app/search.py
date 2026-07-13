"""Busca real no Google BR (Serper.dev) para o veredito de concorrencia.

Etapa opcional da escavacao: sem chave ou com erro, o dossie segue com o
veredito-palpite do enriquecimento. Nunca derruba o pipeline.
"""
import os

import requests

from app import llm

SERPER_URL = "https://google.serper.dev/search"
TIMEOUT = 10
RESULTS_PER_QUERY = 8

VERDICT_PROMPT = """Voce e um analista de mercado brasileiro. Avalie a concorrencia
BRASILEIRA para o produto abaixo usando SOMENTE os resultados de busca reais
(Google BR) e os players de Play Store listados. Responda SOMENTE JSON:
{{"verdict": "sem_concorrente" | "concorrente_fraco" | "concorrente_forte",
 "evidence": [{{"title": "titulo do resultado", "url": "url do resultado",
                "why": "por que e evidencia, em 1 frase"}}],
 "score_final": <0-10, score recalibrado: tracao la fora + dores encontradas
                 + espaco real no Brasil>,
 "rationale": "1-2 frases justificando o veredito"}}
Regras: 2-3 itens em evidence; use SOMENTE urls presentes nos resultados
abaixo (nunca invente); se os resultados nao mostram player BR direto,
verdict="sem_concorrente" e evidence aponta os resultados que provam a busca.

Produto: {name} — {tagline}
Nicho: {niche}
Score atual (palpite, sem busca): {score}
Players na Play Store: {players_block}

Resultados da busca 1 ("{q1}"):
{results1}

Resultados da busca 2 ("{q2}"):
{results2}"""


def search_br(query: str, n: int = RESULTS_PER_QUERY) -> list[dict]:
    """Top resultados organicos do Google BR. Erros propagam para o chamador."""
    api_key = (os.environ.get("SERPER_API_KEY") or "").strip()
    r = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "gl": "br", "hl": "pt"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return [{"title": o.get("title", ""), "url": o.get("link", ""),
             "snippet": o.get("snippet", "")}
            for o in r.json().get("organic", [])[:n]]


def _results_block(results: list[dict]) -> str:
    if not results:
        return "(sem resultados)"
    return "\n".join(f"- {r['title']} — {r['url']} — {r['snippet']}" for r in results)


def verify_br_competition(product, players_block: str) -> tuple[dict, int, int] | None:
    """Veredito de concorrencia BR com busca real. None = etapa pulada."""
    if not (os.environ.get("SERPER_API_KEY") or "").strip():
        print("[busca] SERPER_API_KEY ausente, veredito continua palpite")
        return None

    q1 = f"app {product['niche'] or product['name']} Brasil"
    q2 = f"{product['pain'] or product['tagline'] or product['name']} Brasil"
    results1, results2 = [], []
    for q, dest in ((q1, results1), (q2, results2)):
        try:
            dest.extend(search_br(q))
        except Exception as e:
            print(f"[busca] serper falhou para '{q}': {e}")
    if not results1 and not results2:
        return None

    try:
        data, t_in, t_out = llm.ask_json(VERDICT_PROMPT.format(
            name=product["name"], tagline=product["tagline"] or "",
            niche=product["niche"] or "n/d",
            score=product["opportunity_score"] if product["opportunity_score"] is not None else "n/d",
            players_block=players_block,
            q1=q1, results1=_results_block(results1),
            q2=q2, results2=_results_block(results2)),
            max_tokens=1000)
    except Exception as e:
        print(f"[busca] veredito falhou: {e}")
        return None

    if data.get("verdict") not in ("sem_concorrente", "concorrente_fraco", "concorrente_forte"):
        print(f"[busca] veredito invalido do LLM: {data.get('verdict')!r}")
        return None

    sf = data.get("score_final")
    if sf is not None and not isinstance(sf, (int, float)):
        print(f"[busca] score_final nao numerico do LLM: {sf!r} — ignorando")
        data["score_final"] = None
    return data, t_in, t_out
