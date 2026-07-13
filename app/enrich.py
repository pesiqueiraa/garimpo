"""Enriquecimento opcional via LLM (app/llm.py).

Classifica nicho, dor, publico e da um palpite inicial sobre concorrencia
no Brasil (baseado no conhecimento do modelo — para veredito confiavel,
o proximo passo e plugar uma API de busca como Serper/Tavily).

Requer OPENAI_API_KEY no ambiente. Sem a chave, o passo e pulado.
"""
import os

from app import llm

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


def enrich_rows(conn, limit: int = 25, only_id: int | None = None) -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("[enrich] OPENAI_API_KEY ausente, pulando enriquecimento")
        return 0

    if only_id is not None:
        rows = conn.execute(
            "SELECT * FROM products WHERE id=? AND niche IS NULL", (only_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM products WHERE niche IS NULL ORDER BY points DESC LIMIT ?",
            (limit,),
        ).fetchall()

    done = 0
    for row in rows:
        try:
            data, _, _ = llm.ask_json(PROMPT.format(
                name=row["name"], tagline=row["tagline"] or "",
                category=row["category"] or "n/d",
                source=row["source"], points=row["points"],
            ))
            score = data.get("opportunity_score")
            if not isinstance(score, (int, float)):
                score = None
            conn.execute(
                """UPDATE products SET niche=?, pain=?, audience=?,
                   br_verdict=?, opportunity_score=? WHERE id=?""",
                (data.get("niche"), data.get("pain"), data.get("audience"),
                 data.get("br_verdict"), score, row["id"]),
            )
            conn.commit()
            done += 1
        except Exception as e:
            print(f"[enrich] falhou para '{row['name']}': {e}")
    print(f"[enrich] {done} produtos enriquecidos")
    return done
