"""Fila de escavação: quem merece dossiê, em que ordem, e quando desistir.

Substitui o "máx. 2 por dia com score ≥ 7" da v1: quem não coube hoje fica na
fila, promovidas e marcadas pelo dono sobem, e quem passou 90 dias sem tração
nova expira (uma revisita pode trazê-la de volta).
"""
from app import db
from app.config import get


def priority(prior_total: float, promoted: bool, owner_signal: str | None, cfg: dict) -> float:
    value = float(prior_total or 0)
    if promoted:
        value += float(get(cfg, "fontes.fila.promoted_bonus", 1.5))
    if owner_signal in ("like", "watch", "interesting"):
        value += float(get(cfg, "fontes.fila.feedback_bonus", 0.5))
    return round(value, 2)


def refresh(conn, cfg: dict) -> dict:
    """Enfileira candidatas, recalcula prioridades e expira as antigas."""
    min_prior = float(get(cfg, "fontes.fila.min_prior", 6.0))
    expired = db.expire_queue(conn, db.days_ago_iso(int(get(cfg, "fontes.fila.expire_days", 90))))
    queued = 0
    eligible = set()
    for c in db.queue_candidates(conn, min_prior):
        db.set_queued(conn, c["id"], priority(c["prior_total"], bool(c["promoted_at"]),
                                              db.latest_signal(conn, c["id"]), cfg))
        eligible.add(c["id"])
        queued += 1
    for c in db.queued_companies(conn):
        if c["id"] not in eligible:  # score caiu ou virou descarte desde que entrou
            db.set_company_status(conn, c["id"], "watching", "saiu da fila: score abaixo do mínimo")
    conn.commit()
    size = len(db.queued_companies(conn))
    print(f"[fila] {queued} candidatas avaliadas, {expired} expiradas, {size} na fila")
    return {"queued": queued, "expired": expired, "size": size}


def next_batch(conn, cfg: dict) -> list:
    """As empresas que serão escavadas nesta execução."""
    return db.queued_companies(conn, int(get(cfg, "fontes.fila.max_digs_per_run", 2)))
