"""Sinais do dono: like, dislike, watch e os status das Issues de dossiê."""
import sqlite3

from app.db.core import now_iso

SIGNALS = ("like", "dislike", "watch", "interesting", "validating", "discarded", "building")
POSITIVE = ("like", "watch", "interesting", "validating", "building")
NEGATIVE = ("dislike", "discarded")


def add_feedback(conn: sqlite3.Connection, company_id: int, signal: str, origin: str,
                 note: str | None = None, external_ref: str | None = None) -> bool:
    """Registra um sinal. False se o mesmo sinal da mesma origem já existia (idempotente)."""
    if signal not in SIGNALS:
        raise ValueError(f"sinal inválido: {signal}")
    cur = conn.execute(
        """INSERT OR IGNORE INTO feedback (company_id, signal, origin, note, external_ref, created_at)
           VALUES (?,?,?,?,?,?)""",
        (company_id, signal, origin, note, external_ref or "", now_iso()))
    return cur.rowcount > 0


def feedback_for(conn: sqlite3.Connection, company_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM feedback WHERE company_id=? ORDER BY id",
                        (company_id,)).fetchall()


def latest_signal(conn: sqlite3.Connection, company_id: int) -> str | None:
    row = conn.execute("SELECT signal FROM feedback WHERE company_id=? ORDER BY id DESC LIMIT 1",
                       (company_id,)).fetchone()
    return row["signal"] if row else None


def feedback_summary(conn: sqlite3.Connection, since_iso: str) -> dict[str, int]:
    rows = conn.execute("SELECT signal, COUNT(*) n FROM feedback WHERE created_at >= ? GROUP BY signal",
                        (since_iso,)).fetchall()
    return {r["signal"]: r["n"] for r in rows}


def liked_and_disliked(conn: sqlite3.Connection) -> tuple[list[int], list[int]]:
    """Empresas cujo último sinal é positivo / negativo."""
    rows = conn.execute(
        """SELECT f.company_id, f.signal FROM feedback f
           JOIN (SELECT company_id, MAX(id) mid FROM feedback GROUP BY company_id) t
             ON f.id = t.mid""").fetchall()
    liked = [r["company_id"] for r in rows if r["signal"] in POSITIVE]
    disliked = [r["company_id"] for r in rows if r["signal"] in NEGATIVE]
    return liked, disliked


def count_feedback(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) n FROM feedback").fetchone()["n"]
