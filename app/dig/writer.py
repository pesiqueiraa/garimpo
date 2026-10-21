"""Texto do dossiê: ângulo de ataque e kit de validação.

O LLM escreve, mas só a partir dos fatos já coletados (lojas, buscas, TAM,
catalisadores). Falha do LLM vira None com log, e quem chama decide: sem
ângulo o dossiê é refeito numa próxima tentativa; sem kit ele sai assim
mesmo. O teto da execução (BudgetExceeded) propaga.
"""
import re

from app import llm

ANGLE_PROMPT = "dig_angle_v2"
KIT_PROMPT = "dig_kit_v2"


def _field(company, key: str):
    try:
        return company[key]
    except (KeyError, IndexError, TypeError):
        return None


def clusters_block(clusters: list[dict]) -> str:
    return "; ".join(f"{c.get('theme')} ({c.get('frequency', 0)}x, {c.get('severity', '?')})"
                     for c in clusters if isinstance(c, dict)) or "sem dados de avaliações"


def market_block(market: dict | None) -> str:
    if not market:
        return "não verificada (sem busca real) — trate como desconhecida"
    comps = ", ".join(f"{c['name']} ({c['strength']})" for c in market.get("br_competitors") or [])
    text = f"{market['verdict']} — {market.get('rationale') or ''}"
    if comps:
        text += f" | concorrentes: {comps}"
    evidence = "; ".join(f"{e.get('title')} ({e.get('url')})" for e in market.get("evidence") or [])
    if evidence:
        text += f" | evidências: {evidence}"
    return text


def complaints_block(market: dict | None) -> str:
    items = (market or {}).get("web_complaints") or []
    return "; ".join(f"[{c['source']}] {c['title']}: {c.get('snippet', '')[:120]}"
                     for c in items) or "nenhuma encontrada"


def latam_block(market: dict | None) -> str:
    items = (market or {}).get("latam") or []
    return "; ".join(f"{i['name']} ({i['country'].upper()})" for i in items) \
        or "nenhum player encontrado (ou busca não feita)"


def catalysts_block(catalysts: list[dict]) -> str:
    return "; ".join(f"{c.get('nome') or c.get('id')}: {c.get('nota', '')}".strip()
                     for c in catalysts if isinstance(c, dict)) or "nenhum catalisador cruzou"


def tam_block(tam: dict | None) -> str:
    if not tam:
        return "sem dado (não estime números)"
    ufs = ", ".join(f"{uf} {n:,}".replace(",", ".") for uf, n in tam.get("top_ufs") or [])
    return (f"{tam['count']:,}".replace(",", ".") + f" {tam.get('note', '')}"
            + (f"; principais UFs: {ufs}" if ufs else ""))


def attack_angle(company, facts: dict, players: str, clusters: list[dict],
                 market: dict | None, catalysts: list[dict],
                 tam: dict | None) -> tuple[str, int, int] | None:
    """Ângulo de ataque (reasoning medium). None se o LLM falhar."""
    prompt = llm.load_prompt(ANGLE_PROMPT).format(
        name=_field(company, "name") or "", tagline=_field(company, "tagline") or "",
        niche=facts.get("niche") or "n/d", pain=facts.get("pain") or "n/d",
        segment=facts.get("br_customer_segment") or "n/d", players_block=players,
        clusters_block=clusters_block(clusters), market_block=market_block(market),
        complaints_block=complaints_block(market), latam_block=latam_block(market),
        catalysts_block=catalysts_block(catalysts), tam_block=tam_block(tam))
    try:
        data, t_in, t_out = llm.ask_json(prompt, max_tokens=1500, reasoning_effort="medium")
    except llm.BudgetExceeded:
        raise
    except Exception as e:
        print(f"[escava] ângulo de ataque falhou para '{_field(company, 'name')}': {e}")
        return None
    text = str((data or {}).get("attack_angle") or "").strip()
    if not text:
        print(f"[escava] ângulo de ataque vazio para '{_field(company, 'name')}'")
        return None
    return text, t_in, t_out


# where_to_find é só lugar ou tipo de lugar: e-mail, telefone ou @perfil saem.
PERSONAL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}|(?<!\w)@\w{3,}")


def clean_places(places) -> list[str]:
    """Lugares para achar entrevistados, sem nada que pareça dado pessoal."""
    if not isinstance(places, list):
        return []
    out = []
    for p in places:
        text = str(p or "").strip()[:200] if isinstance(p, (str, int, float)) else ""
        if text and not PERSONAL.search(text):
            out.append(text)
    return out


def valid_kit(data: dict) -> bool:
    """Estrutura mínima do kit: landing completa, 5 perguntas, mensagem e 3-5 lugares."""
    if not isinstance(data, dict):
        return False
    landing = data.get("landing") if isinstance(data.get("landing"), dict) else {}
    where = data.get("where_to_find")
    return (all(isinstance(landing.get(k), str) and landing.get(k).strip()
                for k in ("headline", "subheadline", "cta"))
            and isinstance(landing.get("benefits"), list) and len(landing["benefits"]) == 3
            and isinstance(data.get("mom_test"), list) and len(data["mom_test"]) == 5
            and isinstance(data.get("whatsapp"), str) and bool(data["whatsapp"].strip())
            and isinstance(where, list) and 3 <= len(where) <= 5
            and all(isinstance(w, str) and w.strip() for w in where))


def kit(company, facts: dict, clusters: list[dict], verdict_text: str,
        angle: str | None) -> tuple[dict, int, int] | None:
    """Kit de validação (landing, Mom Test, WhatsApp, onde achar entrevistados). None se falhar."""
    prompt = llm.load_prompt(KIT_PROMPT).format(
        name=_field(company, "name") or "", tagline=_field(company, "tagline") or "",
        niche=facts.get("niche") or "n/d", pain=facts.get("pain") or "n/d",
        segment=facts.get("br_customer_segment") or "n/d",
        clusters_block=clusters_block(clusters), verdict_block=verdict_text or "desconhecido",
        angle_block=(angle or "n/d")[:1500])
    try:
        data, t_in, t_out = llm.ask_json(prompt, max_tokens=2000, reasoning_effort="medium")
    except llm.BudgetExceeded:
        raise
    except Exception as e:
        print(f"[kit] falhou para '{_field(company, 'name')}': {e}")
        return None
    if isinstance(data, dict) and isinstance(data.get("where_to_find"), list):
        data = {**data, "where_to_find": clean_places(data["where_to_find"])}
    if not valid_kit(data):
        print(f"[kit] estrutura inválida para '{_field(company, 'name')}'")
        return None
    return data, t_in, t_out
