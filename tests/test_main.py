from app import db, enrich, excavate, main, report
from tests.conftest import make_product


def _stub_investigate_ok(conn_, pid):
    db.save_dossier(conn_, pid, "angulo do teste", 0.01, 100, 10, score_final=7.5)
    return True


def test_investigar_cria_produto_manual(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "garimpo.db")
    monkeypatch.setattr(enrich, "enrich_rows", lambda c, limit=25, only_id=None: 0)
    monkeypatch.setattr(excavate, "investigate", _stub_investigate_ok)

    rc = main.investigate("Ferramenta Xpto", conn=conn)
    assert rc == 0
    row = conn.execute("SELECT * FROM products WHERE name=?",
                       ("Ferramenta Xpto",)).fetchone()
    assert row["source"] == "manual"
    saida = list(tmp_path.glob("dossie-ferramenta-xpto-*.html"))
    assert len(saida) == 1
    assert "angulo do teste" in saida[0].read_text(encoding="utf-8")


def test_investigar_acha_existente_por_nome_parcial(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "garimpo.db")
    pid = make_product(conn, "Sqlsure", score=7.0)
    chamados = []

    def stub(conn_, product_id):
        chamados.append(product_id)
        return _stub_investigate_ok(conn_, product_id)

    monkeypatch.setattr(excavate, "investigate", stub)
    assert main.investigate("sqlsure", conn=conn) == 0
    assert chamados == [pid]
    # nao criou produto duplicado
    n = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
    assert n == 1


def test_investigar_falha_retorna_1(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "garimpo.db")
    make_product(conn, "Quebrado", score=7.0)
    monkeypatch.setattr(excavate, "investigate", lambda c, pid: False)
    assert main.investigate("Quebrado", conn=conn) == 1


def test_investigar_termo_que_colide_no_dedup(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "garimpo.db")
    # Cria produto com nome "FooApp" (sem URL) -> dedup_key = "name:fooapp"
    db.upsert(conn, {
        "name": "FooApp",
        "url": None,
        "source": "manual",
        "points": 0,
    })
    pid = conn.execute("SELECT id FROM products WHERE name=?", ("FooApp",)).fetchone()["id"]
    conn.execute(
        "UPDATE products SET niche='teste', opportunity_score=7.0 WHERE id=?",
        (pid,),
    )
    conn.commit()

    monkeypatch.setattr(enrich, "enrich_rows", lambda c, limit=25, only_id=None: 0)
    monkeypatch.setattr(excavate, "investigate", _stub_investigate_ok)

    # "Foo-App!" não bate por igualdade nem LIKE, mas normaliza p/ mesmo dedup_key "name:fooapp"
    rc = main.investigate("Foo-App!", conn=conn)
    assert rc == 0
    n = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
    assert n == 1   # não duplicou nem crashou
