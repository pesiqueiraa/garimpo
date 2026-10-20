"""Acompanhamento ao longo do tempo: revisitas, promovidas e quem subiu."""
import sqlite3

from app.db.core import from_json, now_iso

WATCHED = ("watching", "queued", "dug", "promoted", "validating", "building", "new", "expired")


def revisit_candidates(conn: sqlite3.Connection, min_prior: float) -> list[sqlite3.Row]:
    """Empresas acompanhadas: status ativo e score prévio >= mínimo, ou marcadas pelo dono."""
    marks = ",".join("?" * len(WATCHED))
    return conn.execute(
        f"""SELECT c.*, s.total AS prior_total FROM companies c
            LEFT JOIN scores s ON s.id = (SELECT MAX(id) FROM scores
                                          WHERE company_id = c.id AND kind = 'prior')
            WHERE c.status IN ({marks})
              AND (COALESCE(s.total, 0) >= ? OR c.status IN ('watching', 'validating', 'building')
                   OR EXISTS (SELECT 1 FROM feedback f WHERE f.company_id = c.id
                              AND f.signal IN ('like', 'watch', 'interesting')))
              AND COALESCE(s.kill_reason, '') = ''
            ORDER BY c.first_seen""",
        (*WATCHED, min_prior)).fetchall()


def mark_revisited(conn: sqlite3.Connection, company_id: int, stage: int) -> None:
    conn.execute("UPDATE companies SET revisit_stage=?, last_revisit_at=? WHERE id=?",
                 (stage, now_iso(), company_id))


def promoted_since(conn: sqlite3.Connection, iso: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM companies WHERE status='promoted' AND status_changed_at >= ?
           ORDER BY status_changed_at DESC""", (iso,)).fetchall()


def traction_movers(conn: sqlite3.Connection, since_iso: str, limit: int = 8) -> list[dict]:
    """Empresas cuja subnota de tração subiu: compara o score mais recente com o
    último score anterior à janela."""
    rows = conn.execute(
        """SELECT s.company_id, s.subscores, s.total, s.created_at FROM scores s
           WHERE s.kind='prior' AND s.id IN (
               SELECT MAX(id) FROM scores WHERE kind='prior' GROUP BY company_id)
             AND s.created_at >= ?""", (since_iso,)).fetchall()
    movers = []
    for r in rows:
        before = conn.execute(
            """SELECT subscores, total FROM scores WHERE company_id=? AND kind='prior'
               AND created_at < ? ORDER BY id DESC LIMIT 1""",
            (r["company_id"], since_iso)).fetchone()
        if before is None:
            continue
        now_t = from_json(r["subscores"], dict, {}).get("traction")
        was_t = from_json(before["subscores"], dict, {}).get("traction")
        if now_t is None or was_t is None or now_t <= was_t:
            continue
        movers.append({"company_id": r["company_id"], "traction_before": was_t,
                       "traction_now": now_t, "total_before": before["total"],
                       "total_now": r["total"]})
    movers.sort(key=lambda m: (m["traction_now"] - m["traction_before"], m["total_now"] or 0),
                reverse=True)
    return movers[:limit]


def sample_for_golden(conn: sqlite3.Connection, n: int, exclude_keys: set[str]) -> list[sqlite3.Row]:
    """Amostra diversa para o golden set: alterna faixas do score legado/prévio."""
    rows = conn.execute(
        """SELECT c.*, COALESCE(
               (SELECT total FROM scores WHERE company_id=c.id AND kind='prior' ORDER BY id DESC LIMIT 1),
               (SELECT total FROM scores WHERE company_id=c.id AND kind='legacy' ORDER BY id DESC LIMIT 1)
           ) AS any_score
           FROM companies c ORDER BY any_score DESC, c.id""").fetchall()
    rows = [r for r in rows if r["site_key"] not in exclude_keys]
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]
