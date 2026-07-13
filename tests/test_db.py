import json

from app import db
from tests.conftest import make_product


def test_products_ganha_colunas_de_escavacao(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    assert {"dig_status", "dig_attempts"} <= cols


def test_migracao_em_banco_preexistente(tmp_path):
    # simula banco da v1.0: cria a tabela sem as colunas novas
    import sqlite3
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute("""CREATE TABLE products (
        id INTEGER PRIMARY KEY AUTOINCREMENT, dedup_key TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL, tagline TEXT, url TEXT, source TEXT NOT NULL,
        source_url TEXT, points INTEGER DEFAULT 0, category TEXT,
        first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
        seen_count INTEGER DEFAULT 1, sources_seen TEXT, niche TEXT, pain TEXT,
        audience TEXT, br_verdict TEXT, opportunity_score REAL)""")
    raw.commit()
    raw.close()

    conn = db.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    assert {"dig_status", "dig_attempts"} <= cols
    conn.close()


def test_dig_queue_filtra_score_e_ordena(conn):
    make_product(conn, "Baixo", score=5.0)
    alto = make_product(conn, "Alto", score=9.0)
    medio = make_product(conn, "Medio", score=7.5)
    queue = db.dig_queue(conn, limit=2)
    assert [r["id"] for r in queue] == [alto, medio]


def test_dig_queue_exclui_done_e_failed(conn):
    done = make_product(conn, "Feito", score=9.0)
    db.mark_dig(conn, done, "done")
    failed = make_product(conn, "Falhou", score=8.5)
    db.mark_dig(conn, failed, "failed")
    assert db.dig_queue(conn, limit=5) == []


def test_terceira_falha_vira_failed(conn):
    pid = make_product(conn, "Instavel", score=8.0)
    db.record_dig_failure(conn, pid)
    db.record_dig_failure(conn, pid)
    assert len(db.dig_queue(conn, limit=5)) == 1  # 2 falhas: ainda na fila
    db.record_dig_failure(conn, pid)
    assert db.dig_queue(conn, limit=5) == []
    row = conn.execute("SELECT dig_status, dig_attempts FROM products WHERE id=?", (pid,)).fetchone()
    assert row["dig_status"] == "failed" and row["dig_attempts"] == 3


def test_salvar_e_ler_dossie_completo(conn):
    pid = make_product(conn, "Alvo", score=9.0)
    db.save_competitors(conn, pid, [
        {"app_id": "com.rival.app", "name": "Rival", "store": "br",
         "rating": 3.2, "ratings_count": 12000, "reviews_collected": 180},
    ])
    db.save_pain_clusters(conn, pid, [
        {"theme": "cobrança confusa", "frequency": 42, "severity": "alta",
         "examples": [{"app": "Rival", "excerpt": "me cobraram duas vezes"}]},
    ])
    db.save_dossier(conn, pid, "Atacar a dor de cobrança com Pix nativo.",
                    cost_usd=0.03, tokens_in=9000, tokens_out=800)

    result = db.dossiers_since(conn, "2000-01-01T00:00:00")
    assert len(result) == 1
    d = result[0]
    assert d["dossier"]["name"] == "Alvo"
    assert d["dossier"]["attack_angle"].startswith("Atacar")
    assert d["competitors"][0]["app_id"] == "com.rival.app"
    assert d["clusters"][0]["theme"] == "cobrança confusa"
    assert json.loads(d["clusters"][0]["examples"])[0]["app"] == "Rival"


def test_dossiers_since_respeita_cutoff(conn):
    pid = make_product(conn, "Antigo", score=9.0)
    db.save_dossier(conn, pid, "angulo", 0.01, 100, 10)
    assert db.dossiers_since(conn, "2999-01-01T00:00:00") == []


def test_dossiers_ganha_colunas_v12(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(dossiers)")}
    assert {"br_verdict_final", "br_evidence", "score_final", "kit"} <= cols


def test_migracao_dossiers_em_banco_v11(tmp_path):
    # simula banco da v1.1: tabela dossiers sem as colunas novas
    import sqlite3
    path = tmp_path / "v11.db"
    raw = sqlite3.connect(path)
    raw.execute("""CREATE TABLE dossiers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER UNIQUE NOT NULL,
        attack_angle TEXT, cost_usd REAL, tokens_in INTEGER,
        tokens_out INTEGER, created_at TEXT NOT NULL)""")
    raw.commit()
    raw.close()

    c = db.connect(path)
    cols = {r[1] for r in c.execute("PRAGMA table_info(dossiers)")}
    assert {"br_verdict_final", "br_evidence", "score_final", "kit"} <= cols
    c.close()


def test_save_dossier_retrocompativel(conn):
    pid = make_product(conn, "SemV12", score=9.0)
    db.save_dossier(conn, pid, "angulo", 0.01, 100, 10)
    row = conn.execute("SELECT * FROM dossiers WHERE product_id=?", (pid,)).fetchone()
    assert row["attack_angle"] == "angulo"
    assert row["br_verdict_final"] is None and row["kit"] is None
    assert row["score_final"] is None and row["br_evidence"] is None


def test_save_dossier_com_campos_v12(conn):
    pid = make_product(conn, "ComV12", score=9.0)
    db.save_dossier(
        conn, pid, "angulo", 0.02, 200, 20,
        br_verdict_final="concorrente_fraco",
        br_evidence=[{"title": "Rival BR", "url": "https://rival.com.br", "why": "player local"}],
        score_final=8.5,
        kit={"landing": {"headline": "H"}, "mom_test": ["p"] * 5, "whatsapp": "oi"},
    )
    row = conn.execute("SELECT * FROM dossiers WHERE product_id=?", (pid,)).fetchone()
    assert row["br_verdict_final"] == "concorrente_fraco"
    assert row["score_final"] == 8.5
    assert json.loads(row["br_evidence"])[0]["url"] == "https://rival.com.br"
    assert json.loads(row["kit"])["landing"]["headline"] == "H"
