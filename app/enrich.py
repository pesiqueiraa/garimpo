"""Enriquecimento opcional via API da Anthropic.

Classifica nicho, dor, publico e da um palpite inicial sobre concorrencia
no Brasil (baseado no conhecimento do modelo — para veredito confiavel,
o proximo passo e plugar uma API de busca como Serper/Tavily).

Requer ANTHROPIC_API_KEY no ambiente. Sem a chave, o passo e pulado.
"""
import json
import os

import requests

MODEL = "claude-haiku-4-5-20251001"  # barato e suficiente para classificar

PROMPT = """Voce e um analista de oportunidades de SaaS para o mercado brasileiro.
Analise o produto abaixo e responda SOMENTE com JSON valido, sem markdown:

{{"niche": "nicho em 2-4 palavras em portugues",
 "pain": "dor que resolve, em uma frase curta em portugues",
 "audience": "B2B" ou "B2C" ou "ambos",
 "br_verdict": "sem_concorrente" | "concorrente_fraco" | "concorrente_forte" | "desconhecido",
 "opportunity_score": numero de 0 a 10}}

Criterios do score: tracao aparente + ausencia de player brasileiro conhecido
+ viabilidade de adaptar ao Brasil (Pix, WhatsApp, NF etc). Se nao tiver
certeza sobre concorrentes BR, use "desconhecido" e seja conservador.

Produto: {name}
Descricao: {tagline}
Categoria: {category}
Fonte: {source} ({points} pontos/votos)"""


def enrich_rows(conn, limit: int = 25) -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[enrich] ANTHROPIC_API_KEY ausente, pulando enriquecimento")
        return 0

    rows = conn.execute(
        "SELECT * FROM products WHERE niche IS NULL ORDER BY points DESC LIMIT ?",
        (limit,),
    ).fetchall()

    done = 0
    for row in rows:
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": MODEL,
                    "max_tokens": 300,
                    "messages": [{
                        "role": "user",
                        "content": PROMPT.format(
                            name=row["name"], tagline=row["tagline"] or "",
                            category=row["category"] or "n/d",
                            source=row["source"], points=row["points"],
                        ),
                    }],
                },
                timeout=60,
            )
            r.raise_for_status()
            text = r.json()["content"][0]["text"]
            data = json.loads(text.replace("```json", "").replace("```", "").strip())
            conn.execute(
                """UPDATE products SET niche=?, pain=?, audience=?,
                   br_verdict=?, opportunity_score=? WHERE id=?""",
                (data.get("niche"), data.get("pain"), data.get("audience"),
                 data.get("br_verdict"), data.get("opportunity_score"), row["id"]),
            )
            conn.commit()
            done += 1
        except Exception as e:
            print(f"[enrich] falhou para '{row['name']}': {e}")
    print(f"[enrich] {done} produtos enriquecidos")
    return done
