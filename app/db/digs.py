"""Fila de escavação, dossiês, concorrentes de loja, dores e concorrentes brasileiros."""
import sqlite3

from app.db.core import from_json, now_iso, to_json


# ---------- fila ----------

def queue_candidates(conn: sqlite3.Connection, min_prior: float,
                     recent_dossier_iso: str | None = None) -> list[sqlite3.Row]:
    """Empresas elegíveis: score prévio >= mínimo, sem descarte e sem dossiê nos últimos 180 dias."""
    from app.db.core import days_ago_iso
    recent = recent_dossier_iso or days_ago_iso(180)
    return conn.execute(
        """SELECT c.*, s.total AS prior_total, s.subscores AS prior_subscores
           FROM companies c
           JOIN scores s ON s.id = (SELECT MAX(id) FROM scores
                                    WHERE company_id = c.id AND kind = 'prior')
           WHERE s.total >= ? AND s.kill_reason IS NULL
             AND c.status IN ('new', 'watching', 'promoted', 'queued')
             AND c.dig_attempts < 3
             AND NOT EXISTS (SELECT 1 FROM dossiers d WHERE d.company_id = c.id
                             AND d.created_at >= ?)""",
        (min_prior, recent)).fetchall()


def set_queued(conn: sqlite3.Connection, company_id: int, priority: float) -> None:
    conn.execute(
        """UPDATE companies SET status='queued', queue_priority=?,
           queued_at=COALESCE(queued_at, ?), status_changed_at=COALESCE(status_changed_at, ?)
           WHERE id=?""",
        (priority, now_iso(), now_iso(), company_id))


def queued_companies(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM companies WHERE status='queued' ORDER BY queue_priority DESC, queued_at"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def expire_queue(conn: sqlite3.Connection, older_than_iso: str) -> int:
    cur = conn.execute(
        """UPDATE companies SET status='expired', status_reason='90 dias na fila sem tração nova',
           status_changed_at=?, queue_priority=NULL, queued_at=NULL, promoted_at=NULL
           WHERE status='queued' AND queued_at < ?""",
        (now_iso(), older_than_iso))
    return cur.rowcount


def record_dig_failure(conn: sqlite3.Connection, company_id: int) -> None:
    conn.execute(
        """UPDATE companies SET dig_attempts = dig_attempts + 1,
           status = CASE WHEN dig_attempts + 1 >= 3 THEN 'expired' ELSE status END,
           status_reason = CASE WHEN dig_attempts + 1 >= 3 THEN '3 falhas na escavação'
                                ELSE status_reason END
           WHERE id=?""",
        (company_id,))


def mark_dug(conn: sqlite3.Connection, company_id: int) -> None:
    conn.execute(
        """UPDATE companies SET status='dug', queue_priority=NULL, queued_at=NULL,
           promoted_at=NULL, status_changed_at=?, status_reason=NULL WHERE id=?""",
        (now_iso(), company_id))


def dig_cost_since(conn: sqlite3.Connection, iso: str) -> float:
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) s FROM dossiers WHERE created_at >= ?",
                       (iso,)).fetchone()
    return float(row["s"])


# ---------- dossiês ----------

DOSSIER_JSON_FIELDS = ("br_evidence", "latam", "web_complaints", "tam", "catalysts",
                       "subscores", "kit")


def save_dossier(conn: sqlite3.Connection, company_id: int, data: dict) -> int:
    cur = conn.execute(
        """INSERT INTO dossiers (company_id, attack_angle, br_verdict, br_rationale, br_evidence,
               latam, web_complaints, tam, catalysts, score_prior, score_posterior, subscores,
               kit, kit_reason, issue_number, cost_usd, tokens_in, tokens_out, created_at,
               legacy_dossier_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (company_id, data.get("attack_angle"), data.get("br_verdict"), data.get("br_rationale"),
         to_json(data.get("br_evidence")), to_json(data.get("latam")),
         to_json(data.get("web_complaints")), to_json(data.get("tam")),
         to_json(data.get("catalysts")), data.get("score_prior"), data.get("score_posterior"),
         to_json(data.get("subscores")), to_json(data.get("kit")), data.get("kit_reason"),
         data.get("issue_number"), data.get("cost_usd"), data.get("tokens_in"),
         data.get("tokens_out"), data.get("created_at") or now_iso(),
         data.get("legacy_dossier_id")))
    return cur.lastrowid


def save_app_competitors(conn: sqlite3.Connection, dossier_id: int, company_id: int,
                         competitors: list[dict]) -> None:
    ts = now_iso()
    conn.executemany(
        """INSERT INTO app_competitors (dossier_id, company_id, store, country, app_id, name,
               rating, ratings_count, reviews_collected, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [(dossier_id, company_id, c.get("store", "play"), c.get("country", "br"), str(c["app_id"]),
          c["name"], c.get("rating"), c.get("ratings_count"), int(c.get("reviews_collected") or 0),
          ts) for c in competitors])


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def save_pain_clusters(conn: sqlite3.Connection, dossier_id: int, company_id: int,
                       clusters: list[dict]) -> None:
    ts = now_iso()
    conn.executemany(
        """INSERT INTO pain_clusters (dossier_id, company_id, theme, frequency, severity,
               examples, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        [(dossier_id, company_id, str(c.get("theme") or "?")[:200], _to_int(c.get("frequency")),
          c.get("severity"), to_json(c.get("examples") or []), ts)
         for c in clusters if isinstance(c, dict)])


def _dossier_dict(conn: sqlite3.Connection, row) -> dict:
    d = dict(row)
    for field in DOSSIER_JSON_FIELDS:
        d[field] = from_json(row[field])
    company = conn.execute("SELECT * FROM companies WHERE id=?", (row["company_id"],)).fetchone()
    return {
        "dossier": d,
        "company": dict(company) if company else {},
        "app_competitors": [dict(r) for r in conn.execute(
            "SELECT * FROM app_competitors WHERE dossier_id=? ORDER BY ratings_count DESC",
            (row["id"],))],
        "pain_clusters": [{**dict(r), "examples": from_json(r["examples"], list, [])}
                          for r in conn.execute(
            "SELECT * FROM pain_clusters WHERE dossier_id=? ORDER BY frequency DESC", (row["id"],))],
        "br_competitors": [dict(r) for r in conn.execute(
            """SELECT b.*, l.strength FROM br_competitor_links l
               JOIN br_competitors b ON b.id = l.br_competitor_id
               WHERE l.dossier_id=? ORDER BY l.strength DESC""", (row["id"],))],
    }


def get_dossier(conn: sqlite3.Connection, dossier_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM dossiers WHERE id=?", (dossier_id,)).fetchone()
    return _dossier_dict(conn, row) if row else None


def dossiers_since(conn: sqlite3.Connection, iso: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM dossiers WHERE created_at >= ? ORDER BY created_at DESC",
                        (iso,)).fetchall()
    return [_dossier_dict(conn, r) for r in rows]


def last_dossier_for_company(conn: sqlite3.Connection, company_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM dossiers WHERE company_id=? ORDER BY id DESC LIMIT 1",
                       (company_id,)).fetchone()
    return _dossier_dict(conn, row) if row else None


def dossiers_without_issue(conn: sqlite3.Connection, since_iso: str) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM dossiers WHERE issue_number IS NULL AND created_at >= ?
           AND legacy_dossier_id IS NULL ORDER BY id""", (since_iso,)).fetchall()
    return [_dossier_dict(conn, r) for r in rows]


def dossiers_with_issue(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, company_id, issue_number FROM dossiers "
                        "WHERE issue_number IS NOT NULL").fetchall()


def set_dossier_issue(conn: sqlite3.Connection, dossier_id: int, issue_number: int) -> None:
    conn.execute("UPDATE dossiers SET issue_number=? WHERE id=?", (issue_number, dossier_id))


def set_dossier_kit(conn: sqlite3.Connection, dossier_id: int, kit: dict, reason: str,
                    extra_cost: float = 0.0, extra_in: int = 0, extra_out: int = 0) -> None:
    conn.execute(
        """UPDATE dossiers SET kit=?, kit_reason=?, kit_at=?, cost_usd=COALESCE(cost_usd,0)+?,
           tokens_in=COALESCE(tokens_in,0)+?, tokens_out=COALESCE(tokens_out,0)+? WHERE id=?""",
        (to_json(kit), reason, now_iso(), extra_cost, extra_in, extra_out, dossier_id))


def weekly_kit_since(conn: sqlite3.Connection, iso: str) -> sqlite3.Row | None:
    """Kit da semana gerado desde `iso` (trava contra um segundo kit na mesma semana)."""
    return conn.execute(
        "SELECT * FROM dossiers WHERE kit_reason='weekly_best' AND kit_at >= ? ORDER BY kit_at DESC",
        (iso,)).fetchone()


def best_dossier_without_kit(conn: sqlite3.Connection, since_iso: str,
                             min_score: float) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT d.* FROM dossiers d JOIN companies c ON c.id = d.company_id
           WHERE d.created_at >= ? AND d.kit IS NULL AND d.score_posterior >= ?
             AND c.status NOT IN ('discarded')
           ORDER BY d.score_posterior DESC, d.id DESC LIMIT 1""",
        (since_iso, min_score)).fetchone()


def kits_since(conn: sqlite3.Connection, iso: str) -> int:
    row = conn.execute("SELECT COUNT(*) n FROM dossiers WHERE kit IS NOT NULL AND created_at >= ?",
                       (iso,)).fetchone()
    return row["n"]


# ---------- concorrentes brasileiros ----------

def upsert_br_competitor(conn: sqlite3.Connection, name: str, domain: str | None,
                         niche: str | None, evidence: list | None) -> int:
    from app.resolve import normalize_name
    name_key = normalize_name(name)
    row = None
    if domain:
        row = conn.execute("SELECT * FROM br_competitors WHERE domain=?", (domain,)).fetchone()
    if row is None and name_key:
        row = conn.execute("SELECT * FROM br_competitors WHERE name_key=? AND "
                           "(domain IS NULL OR ? IS NULL)", (name_key, domain)).fetchone()
    ts = now_iso()
    if row is None:
        cur = conn.execute(
            """INSERT INTO br_competitors (name, name_key, domain, niche, evidence,
                   first_seen, last_seen) VALUES (?,?,?,?,?,?,?)""",
            (name[:200], name_key, domain, niche, to_json(evidence or []), ts, ts))
        return cur.lastrowid
    merged = from_json(row["evidence"], list, []) + list(evidence or [])
    seen, unique = set(), []
    for e in merged:
        key = (e or {}).get("url") if isinstance(e, dict) else str(e)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    conn.execute(
        """UPDATE br_competitors SET last_seen=?, seen_count=seen_count+1,
           domain=COALESCE(domain, ?), niche=COALESCE(niche, ?), evidence=? WHERE id=?""",
        (ts, domain, niche, to_json(unique[-10:]), row["id"]))
    return row["id"]


def link_br_competitor(conn: sqlite3.Connection, br_competitor_id: int, company_id: int,
                       dossier_id: int | None, strength: str | None) -> None:
    conn.execute(
        """INSERT INTO br_competitor_links (br_competitor_id, company_id, dossier_id, strength, created_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(br_competitor_id, company_id) DO UPDATE SET
             dossier_id=excluded.dossier_id, strength=excluded.strength""",
        (br_competitor_id, company_id, dossier_id, strength, now_iso()))


def known_br_competitors(conn: sqlite3.Connection, niche_terms: list[str], limit: int = 10) -> list[sqlite3.Row]:
    """Concorrentes BR já vistos em nichos parecidos (o mapa do mercado reaproveitado)."""
    terms = [t for t in niche_terms if t and len(t) >= 4][:5]
    if not terms:
        return []
    clauses = " OR ".join("lower(niche) LIKE ?" for _ in terms)
    return conn.execute(
        f"SELECT * FROM br_competitors WHERE {clauses} ORDER BY seen_count DESC LIMIT ?",
        [f"%{t.lower()}%" for t in terms] + [limit]).fetchall()


def count_br_competitors(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) n FROM br_competitors").fetchone()["n"]
