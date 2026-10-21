from datetime import datetime, timezone

from app import db, evaluate, queue, revisit
from tests.conftest import FACTS, make_company


def test_fila_ordena_por_prioridade_e_respeita_limite(conn, cfg):
    a = make_company(conn, "A", prior=7.0)
    b = make_company(conn, "B", prior=8.5)
    c = make_company(conn, "C", prior=6.5, status="promoted")
    make_company(conn, "Baixa", prior=4.0)
    queue.refresh(conn, cfg)
    order = [r["id"] for r in db.queued_companies(conn)]
    assert order == [b, c, a]  # promovida ganha +1,5
    assert [r["id"] for r in queue.next_batch(conn, cfg)] == [b, c]


def test_fila_ignora_descartadas_e_escavadas_recentemente(conn, cfg):
    killed = make_company(conn, "Morta", prior=9.0)
    db.save_score(conn, killed, "prior", {"total": 0.0, "subscores": {}, "kill_reason": "descarte: x"},
                  "t")
    dug = make_company(conn, "Feita", prior=9.0)
    db.save_dossier(conn, dug, {"attack_angle": "x"})
    queue.refresh(conn, cfg)
    assert db.queued_companies(conn) == []


def test_fila_bonus_de_feedback(conn, cfg):
    a = make_company(conn, "A", prior=7.0)
    b = make_company(conn, "B", prior=7.2)
    db.add_feedback(conn, a, "like", "cli")
    queue.refresh(conn, cfg)
    assert db.queued_companies(conn)[0]["id"] == a


def test_fila_expira_antigas(conn, cfg):
    a = make_company(conn, "A", prior=7.0)
    queue.refresh(conn, cfg)
    conn.execute("UPDATE companies SET queued_at='2020-01-01T00:00:00+00:00' WHERE id=?", (a,))
    stats = queue.refresh(conn, cfg)
    assert stats["expired"] == 1
    assert db.get_company(conn, a)["status"] == "expired"


def test_due_stage():
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    assert revisit.due_stage("2026-10-01T00:00:00+00:00", 0, [30, 90, 180], now) is None
    assert revisit.due_stage("2026-09-01T00:00:00+00:00", 0, [30, 90, 180], now) == 1
    assert revisit.due_stage("2026-07-01T00:00:00+00:00", 0, [30, 90, 180], now) == 2  # pula o 30
    assert revisit.due_stage("2026-07-01T00:00:00+00:00", 2, [30, 90, 180], now) is None
    assert revisit.due_stage("lixo", 0, [30], now) is None


def test_revisita_promove_quem_ganhou_tracao(conn, cfg, monkeypatch):
    from app import signals
    cid = make_company(conn, "Cresceu", facts=FACTS)
    conn.execute("UPDATE companies SET first_seen='2026-06-01T00:00:00+00:00', url=NULL WHERE id=?",
                 (cid,))
    from app import score
    score.run(conn, cfg)
    db.set_company_status(conn, cid, "watching")

    def fake_signals(conn_, cfg_, company_ids=None):
        db.add_observations(conn_, cid, [{"metric": "mrr_usd", "value_num": 80000, "source": "trustmrr"}])
        return 1
    monkeypatch.setattr(signals, "run", fake_signals)
    result = revisit.run(conn, cfg)
    assert result == {"revisited": 1, "promoted": 1}
    company = db.get_company(conn, cid)
    assert company["status"] == "promoted" and "tração subiu" in company["status_reason"]
    assert company["revisit_stage"] >= 2
    assert revisit.run(conn, cfg)["revisited"] == 0  # estágio já feito


def test_spearman_e_precisao():
    assert evaluate.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert evaluate.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert evaluate.spearman([1, 2], [1, 2]) is None
    assert evaluate.precision_at([9, 8, 1], [8, 2, 9], 2) == 0.5


def test_golden_amostra_e_avaliacao(conn, tmp_path):
    for i, total in enumerate([9.0, 7.0, 5.0, 3.0]):
        make_company(conn, f"E{i}", prior=total)
    path = tmp_path / "golden.csv"
    assert evaluate.sample(conn, 10, path) == 4
    assert evaluate.sample(conn, 10, path) == 0  # não repete
    rows = evaluate.read_golden(path)
    labels = {"e0.com": "8", "e1.com": "7", "e2.com": "4", "e3.com": "2"}
    for r in rows:
        r["label"] = labels.get(r["site_key"], "")
    import csv
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=evaluate.FIELDS)
        w.writeheader()
        w.writerows(rows)
    result = evaluate.evaluate(conn, path)
    assert result["v2"]["n"] == 4 and result["v2"]["spearman"] == 1.0


def test_fila_tira_quem_caiu_abaixo_do_minimo(conn, cfg):
    a = make_company(conn, "A", prior=7.0)
    queue.refresh(conn, cfg)
    db.save_score(conn, a, "prior", {"total": 3.0, "subscores": {}, "kill_reason": None}, "t")
    queue.refresh(conn, cfg)
    assert db.get_company(conn, a)["status"] == "watching"
    assert db.queued_companies(conn) == []
