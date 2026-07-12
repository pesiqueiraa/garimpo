"""Armazenamento SQLite com deduplicação por URL/nome normalizado."""
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "garimpo.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    tagline TEXT,
    url TEXT,
    source TEXT NOT NULL,          -- hackernews | producthunt | yc
    source_url TEXT,
    points INTEGER DEFAULT 0,
    category TEXT,
    first_seen TEXT NOT NULL,      -- ISO datetime (UTC)
    last_seen TEXT NOT NULL,
    seen_count INTEGER DEFAULT 1,  -- aparecer em varias fontes = sinal forte
    sources_seen TEXT,             -- csv de fontes distintas
    -- campos de enriquecimento (preenchidos pelo LLM, opcionais)
    niche TEXT,
    pain TEXT,
    audience TEXT,                 -- B2B | B2C | ambos
    br_verdict TEXT,               -- sem_concorrente | concorrente_fraco | concorrente_forte | desconhecido
    opportunity_score REAL
);
CREATE TABLE IF NOT EXISTS competitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    app_id TEXT NOT NULL,
    name TEXT NOT NULL,
    store TEXT NOT NULL,             -- 'br' | 'us'
    rating REAL,
    ratings_count INTEGER,
    reviews_collected INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pain_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    theme TEXT NOT NULL,
    frequency INTEGER DEFAULT 0,
    severity TEXT,                   -- 'alta' | 'media' | 'baixa'
    examples TEXT,                   -- JSON: [{"app": ..., "excerpt": ...}]
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dossiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER UNIQUE NOT NULL REFERENCES products(id),
    attack_angle TEXT,
    cost_usd REAL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    created_at TEXT NOT NULL
);
"""


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def dedup_key(name: str, url: str | None) -> str:
    if url:
        # dominio raiz como chave preferencial
        m = re.search(r"https?://(?:www\.)?([^/]+)", url)
        if m:
            return "url:" + m.group(1).lower()
    return "name:" + normalize(name)


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def upsert(conn: sqlite3.Connection, item: dict) -> str:
    """Insere ou atualiza um produto. Retorna 'new', 'cross_source' ou 'seen'."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    key = dedup_key(item["name"], item.get("url"))
    row = conn.execute("SELECT * FROM products WHERE dedup_key = ?", (key,)).fetchone()

    if row is None:
        conn.execute(
            """INSERT INTO products
               (dedup_key, name, tagline, url, source, source_url, points,
                category, first_seen, last_seen, sources_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (key, item["name"], item.get("tagline"), item.get("url"),
             item["source"], item.get("source_url"), item.get("points", 0),
             item.get("category"), now, now, item["source"]),
        )
        conn.commit()
        return "new"

    sources = set((row["sources_seen"] or "").split(","))
    status = "cross_source" if item["source"] not in sources else "seen"
    sources.add(item["source"])
    conn.execute(
        """UPDATE products SET last_seen=?, seen_count=seen_count+1,
           points=MAX(points, ?), sources_seen=? WHERE dedup_key=?""",
        (now, item.get("points", 0), ",".join(sorted(sources)), key),
    )
    conn.commit()
    return status


def new_since(conn: sqlite3.Connection, iso_cutoff: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM products WHERE first_seen >= ?
           ORDER BY opportunity_score DESC NULLS LAST, points DESC""",
        (iso_cutoff,),
    ).fetchall()


def cross_source_hits(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM products WHERE sources_seen LIKE '%,%' ORDER BY points DESC LIMIT 10"
    ).fetchall()


def _migrate(conn: sqlite3.Connection) -> None:
    """Migracao leve: bancos da v1.0 ganham as colunas de escavacao."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    if "dig_status" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN dig_status TEXT")
    if "dig_attempts" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN dig_attempts INTEGER DEFAULT 0")
    conn.commit()


def dig_queue(conn: sqlite3.Connection, limit: int = 2) -> list[sqlite3.Row]:
    """Produtos elegiveis para escavacao, melhores primeiro."""
    return conn.execute(
        """SELECT * FROM products
           WHERE opportunity_score >= 7 AND dig_status IS NULL AND dig_attempts < 3
           ORDER BY opportunity_score DESC, points DESC LIMIT ?""",
        (limit,),
    ).fetchall()


def mark_dig(conn: sqlite3.Connection, product_id: int, status: str) -> None:
    conn.execute("UPDATE products SET dig_status=? WHERE id=?", (status, product_id))
    conn.commit()


def record_dig_failure(conn: sqlite3.Connection, product_id: int) -> None:
    conn.execute(
        """UPDATE products SET dig_attempts = dig_attempts + 1,
           dig_status = CASE WHEN dig_attempts + 1 >= 3 THEN 'failed' ELSE dig_status END
           WHERE id=?""",
        (product_id,),
    )
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_competitors(conn: sqlite3.Connection, product_id: int, comps: list[dict]) -> None:
    now = _now()
    conn.execute("DELETE FROM competitors WHERE product_id=?", (product_id,))
    conn.executemany(
        """INSERT INTO competitors
           (product_id, app_id, name, store, rating, ratings_count, reviews_collected, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        [(product_id, c["app_id"], c["name"], c["store"], c.get("rating"),
          c.get("ratings_count"), c.get("reviews_collected", 0), now) for c in comps],
    )
    conn.commit()


def save_pain_clusters(conn: sqlite3.Connection, product_id: int, clusters: list[dict]) -> None:
    now = _now()
    conn.execute("DELETE FROM pain_clusters WHERE product_id=?", (product_id,))
    conn.executemany(
        """INSERT INTO pain_clusters
           (product_id, theme, frequency, severity, examples, created_at)
           VALUES (?,?,?,?,?,?)""",
        [(product_id, c["theme"], c.get("frequency", 0), c.get("severity"),
          json.dumps(c.get("examples", []), ensure_ascii=False), now) for c in clusters],
    )
    conn.commit()


def save_dossier(conn: sqlite3.Connection, product_id: int, attack_angle: str,
                 cost_usd: float, tokens_in: int, tokens_out: int) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO dossiers
           (product_id, attack_angle, cost_usd, tokens_in, tokens_out, created_at)
           VALUES (?,?,?,?,?,?)""",
        (product_id, attack_angle, cost_usd, tokens_in, tokens_out, _now()),
    )
    conn.commit()


def dossiers_since(conn: sqlite3.Connection, iso_cutoff: str) -> list[dict]:
    """Dossies recentes com produto, players e clusters — prontos para o relatorio."""
    rows = conn.execute(
        """SELECT d.*, p.name, p.tagline, p.url, p.source_url, p.source, p.sources_seen,
                  p.niche, p.pain, p.opportunity_score, p.points
           FROM dossiers d JOIN products p ON p.id = d.product_id
           WHERE d.created_at >= ? ORDER BY d.created_at DESC""",
        (iso_cutoff,),
    ).fetchall()
    return [{
        "dossier": r,
        "competitors": conn.execute(
            "SELECT * FROM competitors WHERE product_id=? ORDER BY ratings_count DESC",
            (r["product_id"],)).fetchall(),
        "clusters": conn.execute(
            "SELECT * FROM pain_clusters WHERE product_id=? ORDER BY frequency DESC",
            (r["product_id"],)).fetchall(),
    } for r in rows]
