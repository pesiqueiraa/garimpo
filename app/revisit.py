"""Revisitas em D+30, D+90 e D+180: quem ganhou tração depois do lançamento?

É o "não há pressa no dia do lançamento" na prática. Empresas acompanhadas
(score prévio razoável, ou marcadas pelo dono) são relidas nas idades
configuradas: site e preços de novo, sinais de tração de novo, score de novo.
Se a tração subiu o bastante, a empresa é promovida e volta para a fila na frente.
"""
from datetime import datetime, timezone

from app import db, score
from app.config import get

KEEP_STATUS = ("discarded", "validating", "building")


def due_stage(first_seen_iso: str, current_stage: int, stages_days: list[int],
              now: datetime | None = None) -> int | None:
    """Maior estágio (1-based) já vencido e ainda não feito; None se nada vence."""
    try:
        first = datetime.fromisoformat(first_seen_iso)
    except (TypeError, ValueError):
        return None
    if first.tzinfo is None:
        first = first.replace(tzinfo=timezone.utc)
    age_days = ((now or datetime.now(timezone.utc)) - first).days
    due = None
    for i, days in enumerate(stages_days, start=1):
        if age_days >= days and i > current_stage:
            due = i
    return due


def _refresh_site(conn, company, cfg) -> None:
    """Relê o site (preço pode ter mudado) — falha graciosa."""
    from app.signals import site
    if not company["url"]:
        return
    try:
        page = site.probe(company["url"], cfg)
    except Exception as e:  # probe já é gracioso; isto é cinto e suspensório
        print(f"[revisita] site de '{company['name']}' falhou: {e}")
        return
    if page:
        db.save_site_page(conn, company["id"], page)
        db.add_observations(conn, company["id"], site.observations(page))


def run(conn, cfg: dict) -> dict:
    """Revisita as empresas vencidas (limite por execução) e promove quem subiu."""
    from app import signals
    stages = list(get(cfg, "fontes.revisitas.stages_days", [30, 90, 180]))
    limit = int(get(cfg, "fontes.revisitas.max_per_run", 40))
    min_prior = float(get(cfg, "fontes.revisitas.min_prior_to_watch", 5.0))
    delta_min = float(get(cfg, "fontes.revisitas.promote_delta", 1.0))

    due = []
    for company in db.revisit_candidates(conn, min_prior):
        stage = due_stage(company["first_seen"], company["revisit_stage"], stages)
        if stage is not None:
            due.append((company, stage))
    due = due[:limit]
    if not due:
        print("[revisita] nenhuma empresa vencida hoje")
        return {"revisited": 0, "promoted": 0}

    before = {c["id"]: db.latest_score(conn, c["id"], "prior") for c, _ in due}
    for company, _ in due:
        _refresh_site(conn, company, cfg)
    ids = [c["id"] for c, _ in due]
    try:
        signals.run(conn, cfg, company_ids=ids)
    except Exception as e:
        print(f"[revisita] sinais falharam: {e}")
    score.run(conn, cfg, company_ids=ids)

    promoted = 0
    for company, stage in due:
        old, new = before.get(company["id"]), db.latest_score(conn, company["id"], "prior")
        db.mark_revisited(conn, company["id"], stage)
        if not old or not new or new.get("kill_reason"):
            continue
        old_t = (old.get("subscores") or {}).get("traction") or 1
        new_t = (new.get("subscores") or {}).get("traction") or 1
        if new_t - old_t >= delta_min and company["status"] not in KEEP_STATUS:
            days = stages[stage - 1]
            db.set_company_status(conn, company["id"], "promoted",
                                  f"tração subiu de {old_t:.0f} para {new_t:.0f} na revisita D+{days}")
            promoted += 1
    conn.commit()
    print(f"[revisita] {len(due)} revisitadas, {promoted} promovidas")
    return {"revisited": len(due), "promoted": promoted}
