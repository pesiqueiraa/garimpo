"""Empresas e eventos (lançamentos, listagens, rankings)."""
import sqlite3

from app.db.core import from_json, now_iso, to_json

STATUSES = ("new", "watching", "queued", "dug", "discarded", "expired",
            "promoted", "validating", "building")


def _name_key(name: str) -> str:
    from app.resolve import normalize_name
    return normalize_name(name)


def insert_company(conn: sqlite3.Connection, site_key: str, domain: str | None,
                   name: str, tagline: str | None, url: str | None,
                   seen_at: str | None = None) -> int:
    ts = seen_at or now_iso()
    cur = conn.execute(
        """INSERT INTO companies (site_key, domain, name, name_key, tagline, url,
           first_seen, last_seen, status, status_changed_at)
           VALUES (?,?,?,?,?,?,?,?, 'new', ?)""",
        (site_key, domain, name[:200], _name_key(name), tagline, url, ts, ts, ts))
    return cur.lastrowid


def get_company(conn: sqlite3.Connection, company_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()


def find_company_by_key(conn: sqlite3.Connection, site_key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM companies WHERE site_key=?", (site_key,)).fetchone()


def find_company_by_name_key(conn: sqlite3.Connection, name_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM companies WHERE name_key=? ORDER BY id LIMIT 1", (name_key,)).fetchone()


def find_companies(conn: sqlite3.Connection, term: str, limit: int = 5) -> list[sqlite3.Row]:
    """Busca para --investigar: URL, site_key, nome exato ou parecido."""
    like = f"%{term.lower()}%"
    return conn.execute(
        """SELECT * FROM companies
           WHERE url = ? OR site_key = ? OR lower(name) = lower(?)
              OR lower(name) LIKE ? OR site_key LIKE ?
           ORDER BY (lower(name) = lower(?)) DESC, id DESC LIMIT ?""",
        (term, term.lower(), term, like, like, term, limit)).fetchall()


def touch_company(conn: sqlite3.Connection, company_id: int, tagline: str | None = None) -> None:
    conn.execute(
        "UPDATE companies SET last_seen=?, tagline=COALESCE(tagline, ?) WHERE id=?",
        (now_iso(), tagline, company_id))


def update_company_site(conn: sqlite3.Connection, company_id: int, site_key: str,
                        domain: str | None, url: str | None) -> int:
    """Troca a chave (ex.: redirect resolvido). Se a chave já existe, funde. Retorna o id final."""
    other = conn.execute("SELECT id FROM companies WHERE site_key=? AND id<>?",
                         (site_key, company_id)).fetchone()
    if other is not None:
        return merge_companies(conn, keep_id=other["id"], drop_id=company_id)
    conn.execute("UPDATE companies SET site_key=?, domain=?, url=COALESCE(?, url) WHERE id=?",
                 (site_key, domain, url, company_id))
    return company_id


def merge_companies(conn: sqlite3.Connection, keep_id: int, drop_id: int) -> int:
    """Move tudo de drop_id para keep_id e apaga drop_id."""
    if keep_id == drop_id:
        return keep_id
    for table in ("events", "observations", "extractions", "scores", "dossiers",
                  "app_competitors", "pain_clusters"):
        conn.execute(f"UPDATE {table} SET company_id=? WHERE company_id=?", (keep_id, drop_id))
    conn.execute("UPDATE OR IGNORE feedback SET company_id=? WHERE company_id=?", (keep_id, drop_id))
    conn.execute("DELETE FROM feedback WHERE company_id=?", (drop_id,))
    conn.execute("UPDATE OR IGNORE br_competitor_links SET company_id=? WHERE company_id=?",
                 (keep_id, drop_id))
    conn.execute("DELETE FROM br_competitor_links WHERE company_id=?", (drop_id,))
    for table in ("site_pages", "embeddings"):
        conn.execute(f"DELETE FROM {table} WHERE company_id=? AND EXISTS "
                     f"(SELECT 1 FROM {table} WHERE company_id=?)", (drop_id, keep_id))
        conn.execute(f"UPDATE {table} SET company_id=? WHERE company_id=?", (keep_id, drop_id))
    drop = get_company(conn, drop_id)
    conn.execute(
        """UPDATE companies SET first_seen=MIN(first_seen, ?), last_seen=MAX(last_seen, ?),
           tagline=COALESCE(tagline, ?), legacy_product_id=COALESCE(legacy_product_id, ?)
           WHERE id=?""",
        (drop["first_seen"], drop["last_seen"], drop["tagline"], drop["legacy_product_id"], keep_id))
    conn.execute("DELETE FROM companies WHERE id=?", (drop_id,))
    return keep_id


def set_company_status(conn: sqlite3.Connection, company_id: int, status: str,
                       reason: str | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"status inválido: {status}")
    conn.execute(
        "UPDATE companies SET status=?, status_reason=?, status_changed_at=? WHERE id=?",
        (status, reason, now_iso(), company_id))


def companies_seen_since(conn: sqlite3.Connection, iso: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM companies WHERE first_seen >= ? ORDER BY first_seen DESC", (iso,)).fetchall()


def companies_by_ids(conn: sqlite3.Connection, ids: list[int]) -> list[sqlite3.Row]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return conn.execute(f"SELECT * FROM companies WHERE id IN ({marks})", ids).fetchall()


def companies_by_status(conn: sqlite3.Connection, statuses: tuple[str, ...]) -> list[sqlite3.Row]:
    marks = ",".join("?" * len(statuses))
    return conn.execute(f"SELECT * FROM companies WHERE status IN ({marks})", statuses).fetchall()


def all_companies(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM companies ORDER BY id").fetchall()


def count_companies(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT status, COUNT(*) n FROM companies GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def add_event(conn: sqlite3.Connection, company_id: int, item: dict) -> bool:
    """Registra um evento; False se (source, external_id) já existia."""
    external_id = str(item.get("external_id") or item.get("source_url") or item.get("url")
                      or item["name"])
    cur = conn.execute(
        """INSERT OR IGNORE INTO events
           (company_id, source, kind, external_id, url, title, points, category, payload, seen_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (company_id, item["source"], item.get("kind") or "launch", external_id,
         item.get("source_url") or item.get("url"), item.get("tagline") or item["name"],
         int(item.get("points") or 0), item.get("category"),
         to_json(item.get("payload")), item.get("seen_at") or now_iso()))
    if cur.rowcount == 0:
        conn.execute("UPDATE events SET points=MAX(points, ?) WHERE source=? AND external_id=?",
                     (int(item.get("points") or 0), item["source"], external_id))
    return cur.rowcount > 0


def company_sources(conn: sqlite3.Connection, company_id: int) -> list[str]:
    rows = conn.execute("SELECT DISTINCT source FROM events WHERE company_id=?",
                        (company_id,)).fetchall()
    return sorted(r["source"] for r in rows)


def company_events(conn: sqlite3.Connection, company_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM events WHERE company_id=? ORDER BY seen_at",
                        (company_id,)).fetchall()


def max_points(conn: sqlite3.Connection, company_id: int) -> int:
    row = conn.execute("SELECT MAX(points) m FROM events WHERE company_id=?",
                       (company_id,)).fetchone()
    return int(row["m"] or 0)


def company_categories(conn: sqlite3.Connection, company_id: int) -> list[str]:
    rows = conn.execute("SELECT DISTINCT category FROM events WHERE company_id=? "
                        "AND category IS NOT NULL", (company_id,)).fetchall()
    return [r["category"] for r in rows]


def prune_payloads(conn: sqlite3.Connection, older_than_iso: str) -> int:
    cur = conn.execute("UPDATE events SET payload=NULL WHERE payload IS NOT NULL AND seen_at < ?",
                       (older_than_iso,))
    return cur.rowcount


def event_payload(conn: sqlite3.Connection, event_id: int):
    row = conn.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()
    return from_json(row["payload"]) if row else None
