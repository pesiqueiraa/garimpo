"""Kit de validacao: landing copy, perguntas Mom Test e abordagem WhatsApp.

Gerado apenas para dossies fortes (score final >= 8). Etapa opcional:
falha do kit nunca bloqueia o dossie.
"""
from app import llm

KIT_PROMPT = """Voce e um growth hacker brasileiro preparando a validacao de rua
de uma ideia de SaaS tropicalizada. Com base no dossie abaixo, gere o kit.
Responda SOMENTE JSON:
{{"landing": {{"headline": "titulo curto e forte",
   "subheadline": "1 frase de apoio",
   "benefits": ["beneficio 1", "beneficio 2", "beneficio 3"],
   "cta": "chamada para lista de espera"}},
 "mom_test": ["pergunta 1", "pergunta 2", "pergunta 3", "pergunta 4", "pergunta 5"],
 "whatsapp": "mensagem de abertura para grupo de WhatsApp do nicho"}}
Regras: tudo em portugues do Brasil, especifico do nicho (nada generico);
mom_test = exatamente 5 perguntas sobre comportamento PASSADO e real
(estilo Mom Test — nunca "voce usaria?" ou "voce pagaria?");
whatsapp = tom natural de colega do nicho, sem parecer spam, sem link.

Produto: {name} — {tagline}
Nicho: {niche} | Dor: {pain}
Mapa de dores dos concorrentes: {clusters_block}
Veredito de concorrencia BR: {verdict_block}"""


def _valid(data: dict) -> bool:
    landing = data.get("landing") or {}
    return (all(landing.get(k) for k in ("headline", "subheadline", "cta"))
            and isinstance(landing.get("benefits"), list) and len(landing["benefits"]) == 3
            and isinstance(data.get("mom_test"), list) and len(data["mom_test"]) == 5
            and bool(data.get("whatsapp")))


def build_kit(product, clusters_block: str, verdict_block: str) -> tuple[dict, int, int] | None:
    """Kit de validacao via LLM. None = kit falhou (dossie segue sem ele)."""
    try:
        data, t_in, t_out = llm.ask_json(KIT_PROMPT.format(
            name=product["name"], tagline=product["tagline"] or "",
            niche=product["niche"] or "n/d", pain=product["pain"] or "n/d",
            clusters_block=clusters_block, verdict_block=verdict_block),
            max_tokens=2000, reasoning_effort="medium")
    except Exception as e:
        print(f"[kit] falhou para '{product['name']}': {e}")
        return None
    if not _valid(data):
        print(f"[kit] estrutura invalida para '{product['name']}'")
        return None
    return data, t_in, t_out
