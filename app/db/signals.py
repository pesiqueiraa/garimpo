"""Observações (medições ao longo do tempo) e páginas de site."""
import sqlite3

from app.db.core import from_json, now_iso, to_json


def add_observations(conn: sqlite3.Connection, company_id: int, observations: list[dict],
                     observed_at: str | None = None) -> int:
    """Insere observações. Pula a que repete o último valor da mesma métrica e fonte.

    É um registro de mudanças: valor igual ao último não entra (o banco vive no git e
    ~300 empresas do YC são recoletadas todo dia). Tração nova = linha nova = score novo.
    """
    ts = observed_at or now_iso()
    added = 0
    for o in observations:
        metric = o.get("metric")
        if not metric:
            continue
        value_num = o.get("value_num")
        if value_num is not None:
            try:
                value_num = float(value_num)
            except (TypeError, ValueError):
                value_num = None
        value_text = o.get("value_text")
        value_text = None if value_text is None else str(value_text)[:500]
        if value_num is None and value_text is None:
            continue
        source = o.get("source") or "desconhecida"
        last = conn.execute(
            """SELECT value_num, value_text, observed_at FROM observations
               WHERE company_id=? AND metric=? AND source=? ORDER BY id DESC LIMIT 1""",
            (company_id, metric, source)).fetchone()
        if last is not None and last["value_num"] == value_num and last["value_text"] == value_text:
            continue
        conn.execute(
            """INSERT INTO observations (company_id, metric, value_num, value_text, source, observed_at)
               VALUES (?,?,?,?,?,?)""",
            (company_id, metric, value_num, value_text, source, ts))
        added += 1
    return added


def latest_observations(conn: sqlite3.Connection, company_id: int) -> dict:
    """{metric: {value_num, value_text, source, observed_at}} com a leitura mais recente de cada métrica."""
    rows = conn.execute(
        """SELECT o.* FROM observations o
           JOIN (SELECT metric, MAX(id) mid FROM observations WHERE company_id=? GROUP BY metric) t
             ON o.id = t.mid""",
        (company_id,)).fetchall()
    return {r["metric"]: {"value_num": r["value_num"], "value_text": r["value_text"],
                          "source": r["source"], "observed_at": r["observed_at"]} for r in rows}


def observation_history(conn: sqlite3.Connection, company_id: int, metric: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM observations WHERE company_id=? AND metric=? ORDER BY observed_at",
        (company_id, metric)).fetchall()


def save_site_page(conn: sqlite3.Connection, company_id: int, page: dict) -> None:
    conn.execute(
        """INSERT INTO site_pages (company_id, fetched_at, final_url, title, text,
               pricing_url, pricing_text, pricing_hash, features)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(company_id) DO UPDATE SET fetched_at=excluded.fetched_at,
               final_url=excluded.final_url, title=excluded.title, text=excluded.text,
               pricing_url=excluded.pricing_url, pricing_text=excluded.pricing_text,
               pricing_hash=excluded.pricing_hash, features=excluded.features""",
        (company_id, now_iso(), page.get("final_url"), (page.get("title") or "")[:300],
         (page.get("text") or "")[:4000], page.get("pricing_url"),
         (page.get("pricing_text") or "")[:2000], page.get("pricing_hash"),
         to_json(page.get("features") or {})))


def get_site_page(conn: sqlite3.Connection, company_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM site_pages WHERE company_id=?", (company_id,)).fetchone()
    if row is None:
        return None
    page = dict(row)
    page["features"] = from_json(row["features"], dict, {})
    return page


def companies_without_site_page(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Empresas com URL própria e sem leitura do site, mais novas primeiro."""
    return conn.execute(
        """SELECT c.* FROM companies c LEFT JOIN site_pages p ON p.company_id = c.id
           WHERE p.company_id IS NULL AND c.url IS NOT NULL AND c.url <> ''
             AND c.status NOT IN ('discarded', 'expired')
           ORDER BY c.first_seen DESC LIMIT ?""",
        (limit,)).fetchall()


def site_page_older_than(conn: sqlite3.Connection, company_id: int, iso: str) -> bool:
    row = conn.execute("SELECT fetched_at FROM site_pages WHERE company_id=?",
                       (company_id,)).fetchone()
    return row is None or row["fetched_at"] < iso
