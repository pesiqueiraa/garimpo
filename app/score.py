"""Score em código: fatos + sinais viram subnotas de 1 a 5 e um total de 0 a 10.

Tudo aqui é função pura (sem rede, sem banco) para ser testável e explicável.
O LLM nunca dá a nota — ele só preenche os fatos que entram aqui. Os números
são heurísticos e moram em config/pesos.toml e config/perfil.toml.
"""
import math
from datetime import date, datetime

from app.config import get

SUBSCORES = ("traction", "tropicalizability", "timing", "fit", "gap")
DEFAULT_WEIGHTS = {"traction": 0.30, "tropicalizability": 0.25, "timing": 0.10,
                   "fit": 0.20, "gap": 0.15}


def _clamp(value: float, low: float = 1.0, high: float = 5.0) -> float:
    return max(low, min(high, value))


def _ladder(value: float | None, steps: list, higher_is_better: bool = True) -> int:
    """Primeiro degrau [limiar, nota] que o valor alcança; 1 se nenhum."""
    if value is None:
        return 1
    for threshold, grade in steps:
        if (value >= threshold) if higher_is_better else (value <= threshold):
            return int(grade)
    return 1


def _num(observations: dict, metric: str) -> float | None:
    obs = observations.get(metric)
    return None if obs is None else obs.get("value_num")


def _text(observations: dict, metric: str) -> str | None:
    obs = observations.get(metric)
    return None if obs is None else obs.get("value_text")


def _years_since(iso_date: str | None, today: date | None = None) -> float | None:
    if not iso_date:
        return None
    try:
        d = datetime.fromisoformat(iso_date[:10]).date()
    except ValueError:
        return None
    return ((today or date.today()) - d).days / 365.25


def traction(observations: dict, sources: list[str], max_points: int, cfg: dict,
             today: date | None = None) -> tuple[float, list[str]]:
    """Sinal mais forte entre MRR, equipe, vagas, Tranco, Form D, idade e pontos (+ bônus)."""
    t = get(cfg, "pesos.tracao", {}) or {}
    grades: list[tuple[int, str]] = []

    mrr = _num(observations, "mrr_usd")
    if mrr:
        grades.append((_ladder(mrr, t.get("mrr_usd", [[1, 2]])), f"MRR verificado US${mrr:,.0f}"))
    team = _num(observations, "team_size")
    if team:
        g = _ladder(team, t.get("team_size", [[3, 2]]))
        status = _text(observations, "yc_status")
        if status and status not in t.get("yc_proven_status", ["Active", "Public"]):
            g = min(g, 2)
        grades.append((g, f"equipe de {team:.0f} pessoas"
                          + (f" ({status})" if status else "")))
    jobs = _num(observations, "open_jobs")
    if jobs:
        grades.append((_ladder(jobs, t.get("open_jobs", [[1, 2]])), f"{jobs:.0f} vagas abertas"))
    rank = _num(observations, "tranco_rank")
    if rank:
        grades.append((_ladder(rank, t.get("tranco_rank_max", [[1_000_000, 3]]), False),
                       f"top {rank:,.0f} no ranking Tranco"))
    formd = _num(observations, "formd_filings")
    if formd:
        grades.append((_ladder(formd, t.get("formd_filings", [[1, 3]])),
                       f"{formd:.0f} captação(ões) registrada(s) na SEC"))
    age = _years_since(_text(observations, "domain_first_seen"), today)
    if age is not None and age > 0:
        g = _ladder(age, t.get("domain_age_years", [[3, 3]]))
        if g > 1:
            grades.append((g, f"site no ar há {age:.0f} anos"))
    if max_points:
        g = _ladder(max_points, t.get("points", [[20, 2]]))
        if g > 1:
            grades.append((g, f"{max_points} pontos/votos no lançamento"))
    rank_us = _num(observations, "appstore_rank_us")
    if rank_us:
        grades.append((4 if rank_us <= 50 else 3, f"#{rank_us:.0f} em faturamento na App Store EUA"))

    if not grades:
        return 1.0, []
    grades.sort(reverse=True)
    best = float(grades[0][0])
    strong = [g for g, _ in grades if g >= t.get("strong_signal_min", 4)]
    if len(strong) >= 2:
        best += t.get("strong_bonus", 1)
    if len(set(sources)) >= 2:
        best += t.get("multi_source_bonus", 1)
    evidence = [label for _, label in grades]
    if len(set(sources)) >= 2:
        evidence.append(f"apareceu em {len(set(sources))} fontes")
    return _clamp(best), evidence


def tropicalizability(extraction: dict) -> tuple[float, list[str]]:
    """Quantos trilhos locais o modelo exige: mais trilhos, mais vantagem para quem é daqui."""
    rails = extraction.get("local_rails") or {}
    active = [r for r, v in rails.items() if v is True]
    labels = {"payments": "pagamentos (Pix)", "invoicing_tax": "nota fiscal/impostos",
              "messaging": "mensageria (WhatsApp)", "banking_open_finance": "Open Finance",
              "government_integration": "integração com governo",
              "language_content": "idioma/conteúdo local", "local_regulation": "regulação local"}
    grade = _clamp(float(1 + len(active)))
    return grade, [f"exige {labels.get(r, r)}" for r in active]


def match_catalysts(extraction: dict, catalysts: list[dict]) -> list[dict]:
    """Catalisadores que cruzam com os trilhos E com as palavras do produto."""
    rails = {r for r, v in (extraction.get("local_rails") or {}).items() if v is True}
    text = " ".join(str(extraction.get(f) or "") for f in
                    ("summary_pt", "niche", "pain", "buyer", "pricing_model")).lower()
    text += " " + " ".join(extraction.get("search_keywords_pt") or []).lower()
    matched = []
    for c in catalysts:
        if not rails.intersection(c.get("rails", [])):
            continue
        if any(k.lower() in text for k in c.get("keywords", [])):
            matched.append(c)
    return matched


def timing(extraction: dict, catalysts: list[dict]) -> tuple[float, list[dict]]:
    matched = match_catalysts(extraction, catalysts)
    grade = {0: 3.0, 1: 4.0}.get(len(matched), 5.0)
    return grade, matched


def fit(extraction: dict, cfg: dict, affinity: float | None = None) -> tuple[float, list[str]]:
    """Preferências do perfil + afinidade com o feedback do dono (-1 a +1)."""
    perfil = cfg.get("perfil", {}) or {}
    base = float((perfil.get("audience") or {}).get(extraction.get("audience"), 3))
    adjust = 0.0
    for field in ("sales_motion", "technical_complexity", "pricing_model"):
        adjust += float((perfil.get(field) or {}).get(extraction.get(field), 0))
    notes = []
    if affinity is not None:
        weight = float(get(cfg, "perfil.afinidade.weight", 1.0))
        adjust += max(-1.0, min(1.0, affinity)) * weight
        if abs(affinity) >= 0.2:
            notes.append("parecido com o que você curtiu" if affinity > 0
                         else "parecido com o que você descartou")
    return _clamp(base + adjust), notes


def gap(extraction: dict, cfg: dict, verdict: str | None = None) -> float:
    if verdict:
        table = get(cfg, "pesos.gap.verdict", {}) or {}
        return float(table.get(verdict, 3))
    table = get(cfg, "pesos.gap.guess", {}) or {}
    return float(table.get(extraction.get("br_competitor_guess"), 3))


def kill_reason(extraction: dict, cfg: dict) -> str | None:
    flags = set(get(cfg, "pesos.descarte.kill_flags", []) or [])
    hit = [f for f in extraction.get("kill_flags") or [] if f in flags]
    if hit:
        return "descarte: " + ", ".join(hit)
    btypes = set(get(cfg, "pesos.descarte.kill_business_types", []) or [])
    if extraction.get("business_type") in btypes:
        return f"descarte: tipo {extraction['business_type']}"
    return None


def weighted_total(subscores: dict, weights: dict) -> float:
    total_w = sum(weights.get(k, 0) for k in SUBSCORES)
    if total_w <= 0:
        return 0.0
    value = sum(weights.get(k, 0) * (subscores[k] - 1) / 4 * 10 for k in SUBSCORES) / total_w
    return round(max(0.0, min(10.0, value)), 1)


def compute(extraction: dict, observations: dict, sources: list[str], max_points: int,
            affinity: float | None, cfg: dict, catalysts: list[dict] | None = None,
            verdict: str | None = None, today: date | None = None) -> dict:
    """Score prévio (verdict=None) ou posterior (com veredito da busca real)."""
    catalysts = cfg.get("catalisadores", []) if catalysts is None else catalysts
    t, t_ev = traction(observations, sources, max_points, cfg, today)
    tr, tr_ev = tropicalizability(extraction)
    ti, matched = timing(extraction, catalysts)
    f, f_ev = fit(extraction, cfg, affinity)
    g = gap(extraction, cfg, verdict)
    subscores = {"traction": t, "tropicalizability": tr, "timing": ti, "fit": f, "gap": g}
    kind = "posterior" if verdict else "prior"
    weights = get(cfg, f"pesos.{kind}", None) or DEFAULT_WEIGHTS
    reason = kill_reason(extraction, cfg)
    total = 0.0 if reason else weighted_total(subscores, weights)
    evidence = t_ev + tr_ev + [f"catalisador: {c.get('nome', c.get('id'))}" for c in matched] + f_ev
    return {"total": total, "subscores": subscores, "kill_reason": reason,
            "evidence": evidence, "catalysts": [c.get("id") for c in matched], "kind": kind}


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def centroid(vectors: list[list[float]]) -> list[float] | None:
    if not vectors:
        return None
    n = len(vectors)
    return [sum(v[i] for v in vectors) / n for i in range(len(vectors[0]))]


def affinity(vector: list[float] | None, liked: list[list[float]], disliked: list[list[float]],
             min_feedback: int = 3) -> float | None:
    """Quão mais perto do que o dono curtiu do que do que descartou (-1 a +1, ampliado)."""
    if vector is None or len(liked) + len(disliked) < min_feedback:
        return None
    pos = cosine(vector, centroid(liked)) if liked else 0.0
    neg = cosine(vector, centroid(disliked)) if disliked else 0.0
    return max(-1.0, min(1.0, (pos - neg) * 3))


def run(conn, cfg: dict, everything: bool = False, company_ids: list[int] | None = None) -> int:
    """Recalcula o score prévio das empresas com fatos novos (ou de todas)."""
    from app import db
    rows = (db.companies_by_ids(conn, company_ids) if company_ids is not None
            else db.companies_for_scoring(conn, everything))
    liked_ids, disliked_ids = db.liked_and_disliked(conn)
    vectors = db.get_embeddings(conn)
    liked = [vectors[i] for i in liked_ids if i in vectors]
    disliked = [vectors[i] for i in disliked_ids if i in vectors]
    min_fb = int(get(cfg, "perfil.afinidade.min_feedback", 3))
    version = get(cfg, "pesos.version", "pesos")
    done = 0
    for company in rows:
        facts = db.latest_extraction(conn, company["id"])
        if not facts:
            continue
        result = compute(facts, db.latest_observations(conn, company["id"]),
                         db.company_sources(conn, company["id"]),
                         db.max_points(conn, company["id"]),
                         affinity(vectors.get(company["id"]), liked, disliked, min_fb), cfg)
        db.save_score(conn, company["id"], "prior", result, version)
        owner_likes = db.latest_signal(conn, company["id"]) in db.POSITIVE
        if (result["kill_reason"] and not owner_likes
                and company["status"] in ("new", "watching", "queued")):
            db.set_company_status(conn, company["id"], "discarded", result["kill_reason"])
        elif (not result["kill_reason"] and company["status"] == "new"
              and result["total"] >= float(get(cfg, "fontes.revisitas.min_prior_to_watch", 5.0))):
            db.set_company_status(conn, company["id"], "watching", "score prévio alto")
        done += 1
    conn.commit()
    print(f"[score] {done} scores prévios calculados")
    return done
