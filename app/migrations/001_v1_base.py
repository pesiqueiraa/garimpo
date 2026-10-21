"""Migração 001: garante o schema da v1 (base de partida da v2).

Bancos da v1 em produção já têm estas tabelas; bancos novos ganham as tabelas
vazias para que a 002 (renomear para legacy_*) tenha um caminho único.
Copia o _migrate() da v1: colunas adicionadas depois na v1.1/v1.2.
"""

V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    tagline TEXT,
    url TEXT,
    source TEXT NOT NULL,
    source_url TEXT,
    points INTEGER DEFAULT 0,
    category TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    seen_count INTEGER DEFAULT 1,
    sources_seen TEXT,
    niche TEXT,
    pain TEXT,
    audience TEXT,
    br_verdict TEXT,
    opportunity_score REAL
);
CREATE TABLE IF NOT EXISTS competitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    app_id TEXT NOT NULL,
    name TEXT NOT NULL,
    store TEXT NOT NULL,
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
    severity TEXT,
    examples TEXT,
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


def run(conn) -> None:
    for statement in V1_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    for col, kind in (("dig_status", "TEXT"), ("dig_attempts", "INTEGER DEFAULT 0")):
        if col not in cols:
            conn.execute(f"ALTER TABLE products ADD COLUMN {col} {kind}")
    dcols = {r[1] for r in conn.execute("PRAGMA table_info(dossiers)")}
    for col, kind in (("br_verdict_final", "TEXT"), ("br_evidence", "TEXT"),
                      ("score_final", "REAL"), ("kit", "TEXT")):
        if col not in dcols:
            conn.execute(f"ALTER TABLE dossiers ADD COLUMN {col} {kind}")
