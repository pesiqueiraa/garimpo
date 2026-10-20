"""Registro de execuções: contagem por fonte, custo e erros (base dos alertas de saúde)."""
import sqlite3

from app.db.core import from_json, now_iso, to_json


def start_run(conn: sqlite3.Connection, command: str) -> int:
    cur = conn.execute("INSERT INTO runs (command, started_at) VALUES (?, ?)", (command, now_iso()))
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, stats: dict, source_counts: dict,
               usage: dict, errors: list[str]) -> None:
    conn.execute(
        """UPDATE runs SET finished_at=?, stats=?, source_counts=?, cost_usd=?,
           tokens_in=?, tokens_out=?, errors=? WHERE id=?""",
        (now_iso(), to_json(stats), to_json(source_counts), float(usage.get("usd") or 0),
         int(usage.get("in") or 0), int(usage.get("out") or 0), to_json(errors), run_id))
    conn.commit()


def recent_runs(conn: sqlite3.Connection, n: int = 14, command: str | None = None) -> list[dict]:
    """Execuções terminadas, mais recentes primeiro, com JSON já decodificado."""
    sql = "SELECT * FROM runs WHERE finished_at IS NOT NULL"
    params: list = []
    if command:
        sql += " AND command=?"
        params.append(command)
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", params + [n]).fetchall()
    return [{**dict(r), "stats": from_json(r["stats"], dict, {}),
             "source_counts": from_json(r["source_counts"], dict, {}),
             "errors": from_json(r["errors"], list, [])} for r in rows]


def cost_since(conn: sqlite3.Connection, iso: str) -> float:
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) s FROM runs WHERE started_at >= ?",
                       (iso,)).fetchone()
    return float(row["s"])
