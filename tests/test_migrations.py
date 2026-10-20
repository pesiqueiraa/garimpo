"""A migração precisa levar o banco real da v1 para a v2 sem perder nada."""
import json
import sqlite3

from app import db

V1_PRODUCTS = [
    # id, dedup_key, name, url, source, sources_seen, points, niche, score, dig_status
    (1, "url:acme.com", "Acme", "https://acme.com", "hackernews", "hackernews,producthunt", 120,
     "agenda", 7.0, "done"),
    (2, "url:app.acme.com", "Acme App", "https://app.acme.com", "yc", "yc", 0, "agenda", 6.0, None),
    (3, "name:semurl", "Sem URL", None, "yc", "yc", 0, None, None, None),
    (4, "url:github.com", "Proj", "https://github.com/a/proj", "hackernews", "hackernews", 30,
     "devtools", 3.0, None),
]


def _v1_db(path):
    from importlib import import_module
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    import_module("app.migrations.001_v1_base").run(conn)
    for (pid, key, name, url, source, seen, points, niche, score, dig) in V1_PRODUCTS:
        conn.execute(
            """INSERT INTO products (id, dedup_key, name, tagline, url, source, source_url, points,
               category, first_seen, last_seen, sources_seen, niche, pain, audience, br_verdict,
               opportunity_score, dig_status, dig_attempts)
               VALUES (?,?,?,?,?,?,?,?,NULL,'2026-07-20T10:00:00+00:00','2026-07-21T10:00:00+00:00',
                       ?,?,'dor','B2B','desconhecido',?,?,0)""",
            (pid, key, name, f"{name} tagline", url, source, f"https://hn/{pid}", points, seen,
             niche, score, dig))
    conn.execute(
        """INSERT INTO dossiers (id, product_id, attack_angle, cost_usd, tokens_in, tokens_out,
           created_at, br_verdict_final, br_evidence, score_final, kit)
           VALUES (10, 1, 'ângulo', 0.01, 100, 50, '2026-07-22T10:00:00+00:00', 'concorrente_fraco',
                   '[{"title":"x","url":"https://x.com.br"}]', 6.0, NULL)""")
    conn.execute("""INSERT INTO competitors (product_id, app_id, name, store, rating, ratings_count,
                    reviews_collected, created_at) VALUES (1, 'br.rival', 'Rival', 'br', 3.1, 900, 40,
                    '2026-07-22T10:00:00+00:00')""")
    conn.execute("""INSERT INTO pain_clusters (product_id, theme, frequency, severity, examples, created_at)
                    VALUES (1, 'cobrança confusa', 12, 'alta', '[]', '2026-07-22T10:00:00+00:00')""")
    conn.commit()
    conn.close()


def test_migra_banco_v1_para_v2(tmp_path):
    path = tmp_path / "v1.db"
    _v1_db(path)
    conn = db.connect(path)
    assert db.schema_version(conn) == 3

    # Acme e Acme App compartilham o domínio registrável → mesma empresa
    assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 3
    acme = db.find_company_by_key(conn, "acme.com")
    assert acme["status"] == "dug" and acme["legacy_product_id"] == 1
    assert db.company_sources(conn, acme["id"]) == ["hackernews", "producthunt", "yc"]
    assert db.find_company_by_key(conn, "name:semurl") is not None
    assert db.find_company_by_key(conn, "github.com/a/proj") is not None

    legacy = db.latest_extraction(conn, acme["id"], include_legacy=True)
    assert legacy["_prompt_version"] == "legacy_v1" and legacy["niche"] == "agenda"
    assert db.latest_extraction(conn, acme["id"]) is None  # sem extração v2 ainda
    assert db.latest_score(conn, acme["id"], "legacy")["total"] in (7.0, 6.0)

    d = db.last_dossier_for_company(conn, acme["id"])
    assert d["dossier"]["br_verdict"] == "concorrente_fraco"
    assert d["dossier"]["score_posterior"] == 6.0
    assert d["dossier"]["legacy_dossier_id"] == 10
    assert d["app_competitors"][0]["store"] == "play" and d["app_competitors"][0]["country"] == "br"
    assert d["pain_clusters"][0]["theme"] == "cobrança confusa"

    # tabelas da v1 continuam lá para consulta
    assert conn.execute("SELECT COUNT(*) FROM legacy_products").fetchone()[0] == 4
    conn.close()


def test_migracao_e_idempotente(tmp_path):
    path = tmp_path / "v1.db"
    _v1_db(path)
    db.connect(path).close()
    conn = db.connect(path)
    assert db.migrate(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 3


def test_banco_novo_nasce_na_v2(conn):
    assert db.schema_version(conn) == 3
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"companies", "events", "observations", "dossiers", "br_competitors", "runs",
            "legacy_products"} <= tables


def test_dossie_legado_nao_vira_issue(tmp_path):
    path = tmp_path / "v1.db"
    _v1_db(path)
    conn = db.connect(path)
    assert db.dossiers_without_issue(conn, "2000-01-01") == []


def test_observacao_repetida_no_mesmo_dia_nao_duplica(conn):
    from tests.conftest import make_company
    cid = make_company(conn, "Acme")
    obs = [{"metric": "team_size", "value_num": 12, "source": "yc"}]
    assert db.add_observations(conn, cid, obs) == 1
    assert db.add_observations(conn, cid, obs) == 0
    assert db.add_observations(conn, cid, [{"metric": "team_size", "value_num": 15, "source": "yc"}]) == 1
    assert len(db.observation_history(conn, cid, "team_size")) == 2


def test_json_do_dossie_volta_decodificado(conn):
    from tests.conftest import make_company
    cid = make_company(conn, "Acme")
    did = db.save_dossier(conn, cid, {"attack_angle": "a", "br_evidence": [{"url": "u"}],
                                      "kit": {"landing": {}}, "tam": {"count": 3}})
    d = db.get_dossier(conn, did)["dossier"]
    assert d["br_evidence"] == [{"url": "u"}] and d["tam"] == {"count": 3}
    assert json.loads(json.dumps(d["kit"])) == {"landing": {}}
