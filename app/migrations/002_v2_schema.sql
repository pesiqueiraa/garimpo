-- Migração 002: a v1 vira legacy_* (só leitura) e nasce o schema da v2.

ALTER TABLE products RENAME TO legacy_products;
ALTER TABLE competitors RENAME TO legacy_competitors;
ALTER TABLE pain_clusters RENAME TO legacy_pain_clusters;
ALTER TABLE dossiers RENAME TO legacy_dossiers;

-- Empresa é a unidade: um site_key por empresa.
CREATE TABLE companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_key TEXT UNIQUE NOT NULL,      -- acme.com | github.com/fulano/projeto | name:acme
    domain TEXT,                        -- domínio registrável (vazio em plataformas compartilhadas)
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    tagline TEXT,
    url TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    status_reason TEXT,
    status_changed_at TEXT,
    queue_priority REAL,
    queued_at TEXT,
    dig_attempts INTEGER NOT NULL DEFAULT 0,
    revisit_stage INTEGER NOT NULL DEFAULT 0,
    last_revisit_at TEXT,
    legacy_product_id INTEGER
);
CREATE INDEX idx_companies_name_key ON companies(name_key);
CREATE INDEX idx_companies_domain ON companies(domain);
CREATE INDEX idx_companies_status ON companies(status);
CREATE INDEX idx_companies_first_seen ON companies(first_seen);

-- Lançamentos, listagens e rankings: eventos de uma empresa.
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    source TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- launch | listing | ranking | manual
    external_id TEXT NOT NULL,
    url TEXT,
    title TEXT,
    points INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    payload TEXT,                       -- JSON bruto, apagado após N dias
    seen_at TEXT NOT NULL,
    UNIQUE(source, external_id)
);
CREATE INDEX idx_events_company ON events(company_id);

-- Medições ao longo do tempo. Só recebe INSERT.
CREATE TABLE observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    metric TEXT NOT NULL,
    value_num REAL,
    value_text TEXT,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE INDEX idx_observations_company ON observations(company_id, metric, observed_at);

-- Última leitura do site e da página de preços.
CREATE TABLE site_pages (
    company_id INTEGER PRIMARY KEY REFERENCES companies(id),
    fetched_at TEXT NOT NULL,
    final_url TEXT,
    title TEXT,
    text TEXT,
    pricing_url TEXT,
    pricing_text TEXT,
    pricing_hash TEXT,
    features TEXT                       -- JSON
);

-- Fatos extraídos pelo LLM.
CREATE TABLE extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_extractions_company ON extractions(company_id, id);

-- Scores calculados em código (prior/posterior) ou herdados da v1 (legacy).
CREATE TABLE scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    kind TEXT NOT NULL,
    total REAL,
    subscores TEXT,
    kill_reason TEXT,
    evidence TEXT,
    catalysts TEXT,
    weights_version TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_scores_company ON scores(company_id, kind, id);

CREATE TABLE dossiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    attack_angle TEXT,
    br_verdict TEXT,
    br_rationale TEXT,
    br_evidence TEXT,                   -- JSON [{title, url, why}]
    latam TEXT,                         -- JSON [{country, name, url}]
    web_complaints TEXT,                -- JSON [{source, title, url, snippet}]
    tam TEXT,                           -- JSON {cnae, segment, count, top_ufs, note}
    catalysts TEXT,                     -- JSON [id]
    score_prior REAL,
    score_posterior REAL,
    subscores TEXT,
    kit TEXT,
    kit_reason TEXT,                    -- score | weekly_best
    issue_number INTEGER,
    cost_usd REAL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    created_at TEXT NOT NULL,
    legacy_dossier_id INTEGER
);
CREATE INDEX idx_dossiers_company ON dossiers(company_id);
CREATE INDEX idx_dossiers_created ON dossiers(created_at);

CREATE TABLE app_competitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    company_id INTEGER NOT NULL REFERENCES companies(id),
    store TEXT NOT NULL,                -- play | appstore
    country TEXT NOT NULL,              -- br | us
    app_id TEXT NOT NULL,
    name TEXT NOT NULL,
    rating REAL,
    ratings_count INTEGER,
    reviews_collected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_app_competitors_dossier ON app_competitors(dossier_id);

CREATE TABLE pain_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    company_id INTEGER NOT NULL REFERENCES companies(id),
    theme TEXT NOT NULL,
    frequency INTEGER NOT NULL DEFAULT 0,
    severity TEXT,
    examples TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_pain_clusters_dossier ON pain_clusters(dossier_id);

-- Mapa permanente do mercado brasileiro.
CREATE TABLE br_competitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    domain TEXT UNIQUE,
    niche TEXT,
    evidence TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_br_competitors_name ON br_competitors(name_key);

CREATE TABLE br_competitor_links (
    br_competitor_id INTEGER NOT NULL REFERENCES br_competitors(id),
    company_id INTEGER NOT NULL REFERENCES companies(id),
    dossier_id INTEGER REFERENCES dossiers(id),
    strength TEXT,                      -- forte | fraco
    created_at TEXT NOT NULL,
    PRIMARY KEY (br_competitor_id, company_id)
);

CREATE TABLE feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    signal TEXT NOT NULL,               -- like | dislike | watch | interesting | validating | discarded | building
    origin TEXT NOT NULL,               -- email | issue_label | cli
    note TEXT,
    external_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(origin, external_ref, signal, company_id)
);
CREATE INDEX idx_feedback_company ON feedback(company_id);

CREATE TABLE embeddings (
    company_id INTEGER PRIMARY KEY REFERENCES companies(id),
    model TEXT NOT NULL,
    dims INTEGER NOT NULL,
    vector BLOB NOT NULL,               -- float32
    text_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week TEXT NOT NULL,                 -- data do domingo (YYYY-MM-DD)
    label TEXT,
    size INTEGER NOT NULL,
    size_recent INTEGER NOT NULL,
    growth REAL,
    company_ids TEXT NOT NULL,          -- JSON
    created_at TEXT NOT NULL
);
CREATE INDEX idx_trends_week ON trends(week);

CREATE TABLE llm_cache (
    key TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    stats TEXT,                         -- JSON por etapa
    source_counts TEXT,                 -- JSON {fonte: itens}
    cost_usd REAL NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    errors TEXT                         -- JSON [mensagem]
);
