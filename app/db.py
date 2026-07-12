"""Armazenamento SQLite com deduplicação por URL/nome normalizado."""
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
