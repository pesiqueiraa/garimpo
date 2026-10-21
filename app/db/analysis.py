"""Extrações do LLM, scores, embeddings, tendências e cache do LLM."""
import sqlite3
from array import array

from app.db.core import from_json, now_iso, to_json

LEGACY_PROMPT = "legacy_v1"


# ---------- extrações ----------

def save_extraction(conn: sqlite3.Connection, company_id: int, prompt_version: str,
                    model: str, data: dict) -> int:
    cur = conn.execute(
        """INSERT INTO extractions (company_id, prompt_version, model, data, created_at)
           VALUES (?,?,?,?,?)""",
        (company_id, prompt_version, model, to_json(data), now_iso()))
    return cur.lastrowid


def latest_extraction(conn: sqlite3.Connection, company_id: int,
                      include_legacy: bool = False) -> dict | None:
    """Última extração (dict com os fatos + _prompt_version/_created_at)."""
    sql = "SELECT * FROM extractions WHERE company_id=?"
    if not include_legacy:
        sql += f" AND prompt_version <> '{LEGACY_PROMPT}'"
    row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", (company_id,)).fetchone()
    if row is None:
        return None
    data = from_json(row["data"], dict, {})
    data["_prompt_version"] = row["prompt_version"]
    data["_created_at"] = row["created_at"]
    return data


def companies_needing_extraction(conn: sqlite3.Connection, prompt_version: str, limit: int,
                                 legacy_only: bool = False) -> list[sqlite3.Row]:
    """Empresas sem extração nesta versão de prompt (as mais novas primeiro).

    legacy_only=True pega só as que vieram da v1 (reprocessamento único).
    """
    sql = """SELECT c.* FROM companies c
             WHERE NOT EXISTS (SELECT 1 FROM extractions e
                               WHERE e.company_id = c.id AND e.prompt_version = ?)
               AND c.status NOT IN ('discarded', 'expired')"""
    if legacy_only:
        sql += " AND c.legacy_product_id IS NOT NULL"
    sql += " ORDER BY c.first_seen DESC LIMIT ?"
    return conn.execute(sql, (prompt_version, limit)).fetchall()


# ---------- scores ----------

def save_score(conn: sqlite3.Connection, company_id: int, kind: str, result: dict,
               weights_version: str) -> int:
    cur = conn.execute(
        """INSERT INTO scores (company_id, kind, total, subscores, kill_reason, evidence,
               catalysts, weights_version, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (company_id, kind, result.get("total"), to_json(result.get("subscores")),
         result.get("kill_reason"), to_json(result.get("evidence")),
         to_json(result.get("catalysts")), weights_version, now_iso()))
    return cur.lastrowid


def _score_dict(row) -> dict:
    return {"id": row["id"], "company_id": row["company_id"], "kind": row["kind"],
            "total": row["total"], "subscores": from_json(row["subscores"], dict, {}),
            "kill_reason": row["kill_reason"], "evidence": from_json(row["evidence"], list, []),
            "catalysts": from_json(row["catalysts"], list, []),
            "weights_version": row["weights_version"], "created_at": row["created_at"]}


def latest_score(conn: sqlite3.Connection, company_id: int, kind: str = "prior") -> dict | None:
    row = conn.execute("SELECT * FROM scores WHERE company_id=? AND kind=? ORDER BY id DESC LIMIT 1",
                       (company_id, kind)).fetchone()
    return _score_dict(row) if row else None


def previous_score(conn: sqlite3.Connection, company_id: int, kind: str = "prior") -> dict | None:
    """O penúltimo score (para medir quem subiu)."""
    row = conn.execute(
        "SELECT * FROM scores WHERE company_id=? AND kind=? ORDER BY id DESC LIMIT 1 OFFSET 1",
        (company_id, kind)).fetchone()
    return _score_dict(row) if row else None


def latest_scores(conn: sqlite3.Connection, kind: str = "prior") -> dict[int, dict]:
    """{company_id: score} do score mais recente de cada empresa."""
    rows = conn.execute(
        """SELECT s.* FROM scores s
           JOIN (SELECT company_id, MAX(id) mid FROM scores WHERE kind=? GROUP BY company_id) t
             ON s.id = t.mid""",
        (kind,)).fetchall()
    return {r["company_id"]: _score_dict(r) for r in rows}


def companies_for_scoring(conn: sqlite3.Connection, everything: bool = False) -> list[sqlite3.Row]:
    """Empresas com extração v2 cujo score prévio está ausente ou desatualizado."""
    if everything:
        return conn.execute(
            f"""SELECT DISTINCT c.* FROM companies c JOIN extractions e ON e.company_id = c.id
                WHERE e.prompt_version <> '{LEGACY_PROMPT}'""").fetchall()
    return conn.execute(
        f"""SELECT c.* FROM companies c
            WHERE EXISTS (SELECT 1 FROM extractions e WHERE e.company_id = c.id
                          AND e.prompt_version <> '{LEGACY_PROMPT}')
              AND (
                NOT EXISTS (SELECT 1 FROM scores s WHERE s.company_id = c.id AND s.kind = 'prior')
                OR (SELECT MAX(created_at) FROM extractions e WHERE e.company_id = c.id)
                   > (SELECT MAX(created_at) FROM scores s WHERE s.company_id = c.id AND s.kind = 'prior')
                OR (SELECT MAX(observed_at) FROM observations o WHERE o.company_id = c.id)
                   > (SELECT MAX(created_at) FROM scores s WHERE s.company_id = c.id AND s.kind = 'prior')
                OR (SELECT MAX(created_at) FROM feedback f WHERE f.company_id = c.id)
                   > (SELECT MAX(created_at) FROM scores s WHERE s.company_id = c.id AND s.kind = 'prior')
              )""").fetchall()


# ---------- embeddings ----------

def save_embedding(conn: sqlite3.Connection, company_id: int, model: str,
                   vector: list[float], text_hash: str) -> None:
    blob = array("f", vector).tobytes()
    conn.execute(
        """INSERT INTO embeddings (company_id, model, dims, vector, text_hash, created_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(company_id) DO UPDATE SET model=excluded.model, dims=excluded.dims,
             vector=excluded.vector, text_hash=excluded.text_hash, created_at=excluded.created_at""",
        (company_id, model, len(vector), blob, text_hash, now_iso()))


def get_embeddings(conn: sqlite3.Connection, company_ids: list[int] | None = None) -> dict[int, list[float]]:
    if company_ids is None:
        rows = conn.execute("SELECT company_id, vector FROM embeddings").fetchall()
    elif not company_ids:
        return {}
    else:
        marks = ",".join("?" * len(company_ids))
        rows = conn.execute(f"SELECT company_id, vector FROM embeddings WHERE company_id IN ({marks})",
                            company_ids).fetchall()
    out = {}
    for r in rows:
        vec = array("f")
        vec.frombytes(r["vector"])
        out[r["company_id"]] = vec.tolist()
    return out


def embedding_hashes(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT company_id, text_hash FROM embeddings").fetchall()
    return {r["company_id"]: r["text_hash"] for r in rows}


# ---------- tendências ----------

def save_trends(conn: sqlite3.Connection, week: str, clusters: list[dict]) -> None:
    conn.execute("DELETE FROM trends WHERE week=?", (week,))
    for c in clusters:
        conn.execute(
            """INSERT INTO trends (week, label, size, size_recent, growth, company_ids, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (week, c.get("label"), c["size"], c["size_recent"], c.get("growth"),
             to_json(c["company_ids"]), now_iso()))


def latest_trends(conn: sqlite3.Connection) -> list[dict]:
    row = conn.execute("SELECT MAX(week) w FROM trends").fetchone()
    if not row or not row["w"]:
        return []
    rows = conn.execute("SELECT * FROM trends WHERE week=? ORDER BY growth DESC, size DESC",
                        (row["w"],)).fetchall()
    return [{**dict(r), "company_ids": from_json(r["company_ids"], list, [])} for r in rows]


# ---------- cache do LLM ----------

def cache_get(conn: sqlite3.Connection, key: str) -> tuple[dict, int, int] | None:
    row = conn.execute("SELECT * FROM llm_cache WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    data = from_json(row["response"])
    if data is None:
        return None
    return data, row["tokens_in"], row["tokens_out"]


def cache_put(conn: sqlite3.Connection, key: str, response, tokens_in: int, tokens_out: int) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO llm_cache (key, response, tokens_in, tokens_out, created_at)
           VALUES (?,?,?,?,?)""",
        (key, to_json(response), tokens_in, tokens_out, now_iso()))
    conn.commit()


def prune_cache(conn: sqlite3.Connection, older_than_iso: str) -> int:
    return conn.execute("DELETE FROM llm_cache WHERE created_at < ?", (older_than_iso,)).rowcount
