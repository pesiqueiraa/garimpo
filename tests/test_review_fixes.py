"""Regressões dos problemas achados na revisão independente da v2."""
import copy

import pytest

from app import cli, db, queue, resolve, revisit, score, signals
from tests.conftest import FACTS, make_company


# 1. revisita promove mesmo quando os sinais diários já atualizaram a tração

def test_revisita_compara_com_a_tracao_inicial(conn, cfg, monkeypatch):
    cid = make_company(conn, "Cresceu", facts=FACTS)
    conn.execute("UPDATE companies SET first_seen='2026-06-01T00:00:00+00:00', url=NULL WHERE id=?", (cid,))
    score.run(conn, cfg)  # primeiro score: tração baixa
    # os sinais diários acham o MRR ANTES da revisita e o score já sobe
    db.add_observations(conn, cid, [{"metric": "mrr_usd", "value_num": 80000, "source": "trustmrr"}])
    score.run(conn, cfg)
    db.set_company_status(conn, cid, "watching")
    monkeypatch.setattr(signals, "run", lambda *a, **kw: 0)
    assert revisit.run(conn, cfg)["promoted"] == 1
    company = db.get_company(conn, cid)
    assert company["status"] == "promoted" and company["promoted_at"]
    assert company["revisit_traction"] == 5.0  # base da próxima revisita


def test_revisita_seguinte_usa_a_tracao_da_anterior(conn, cfg, monkeypatch):
    cid = make_company(conn, "Parada", facts=FACTS)
    conn.execute("UPDATE companies SET first_seen='2026-01-01T00:00:00+00:00', url=NULL, "
                 "revisit_stage=1, revisit_traction=3 WHERE id=?", (cid,))
    db.add_observations(conn, cid, [{"metric": "team_size", "value_num": 10, "source": "yc"}])
    score.run(conn, cfg)  # tração 3, igual à da revisita anterior
    monkeypatch.setattr(signals, "run", lambda *a, **kw: 0)
    assert revisit.run(conn, cfg) == {"revisited": 1, "promoted": 0}


# 2. etapa que quebra no meio não deixa gravação pela metade

def test_etapa_com_erro_desfaz_o_que_ficou_pela_metade(conn):
    p = cli.Pipeline(conn, "teste")

    def half(conn_):
        db.set_company_status(conn_, make_company(conn_, "A"), "watching")
        conn_.commit()  # o que a etapa já commitou fica
        conn_.execute("UPDATE companies SET status='dug'")  # sem commit: deve ser desfeito
        raise RuntimeError("caiu")
    p.stage("meio", half, conn)
    assert {r["status"] for r in conn.execute("SELECT status FROM companies")} == {"watching"}
    assert p.errors and "caiu" in p.errors[0]


def test_ctrl_c_desfaz_e_repassa(conn):
    p = cli.Pipeline(conn, "teste")
    cid = make_company(conn, "A")

    def interrupted(conn_):
        conn_.execute("UPDATE companies SET status='dug' WHERE id=?", (cid,))
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        p.stage("x", interrupted, conn)
    assert db.get_company(conn, cid)["status"] == "new"


# 3. redirect para domínio sem relação não funde empresas

def test_redirect_sem_relacao_nao_funde(conn, cfg, monkeypatch):
    a = make_company(conn, "Alpha CRM", url="https://alphacrm.io")
    b = make_company(conn, "Beta Invoices", url="https://betainvoices.io")
    page = {"final_url": "https://login.microsoftonline.com/x", "text": "login", "features": {}}
    monkeypatch.setattr(signals.site, "probe", lambda url, cfg=None: copy.deepcopy(page))
    monkeypatch.setattr(signals.wayback, "lookup", lambda company, cfg: [])
    signals.probe_new(conn, cfg)
    assert db.get_company(conn, a) is not None and db.get_company(conn, b) is not None


def test_redirect_relacionado_funde_e_mantem_status_mais_forte(conn, cfg, monkeypatch):
    real = make_company(conn, "Acme", url="https://acme.com", status="watching")
    old = make_company(conn, "Acme", url="https://acme.ai", source="yc")
    db.set_company_status(conn, old, "validating")
    conn.execute("INSERT INTO site_pages (company_id, fetched_at) VALUES (?, '2026-01-01')", (real,))
    monkeypatch.setattr(signals.site, "probe", lambda url, cfg=None: {
        "final_url": "https://acme.com/", "text": "Acme", "features": {}})
    monkeypatch.setattr(signals.wayback, "lookup", lambda company, cfg: [])
    signals.probe_new(conn, cfg)
    assert db.get_company(conn, old) is None
    assert db.get_company(conn, real)["status"] == "validating"


# 4. depois do redirect, o mesmo item não recria a empresa antiga

def test_item_repetido_depois_do_redirect_nao_duplica(conn):
    item = {"name": "Acme", "tagline": "x", "url": "https://acme.ai", "source": "yc",
            "kind": "listing", "external_id": "acme", "points": 0}
    resolve.ingest(conn, [item])
    cid = db.find_company_by_key(conn, "acme.ai")["id"]
    db.update_company_site(conn, cid, "acme.com", "acme.com", "https://acme.com")
    stats = resolve.ingest(conn, [dict(item)])
    assert stats["new"] == 0
    assert db.find_company_by_key(conn, "acme.ai") is None
    assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 1


# 6. fila: bônus de promovida sobrevive ao segundo dia; expirada volta com prazo novo

def test_bonus_de_promovida_sobrevive_ao_status_queued(conn, cfg):
    p = make_company(conn, "Promovida", prior=6.5, status="promoted")
    n = make_company(conn, "Normal", prior=7.5)
    queue.refresh(conn, cfg)
    queue.refresh(conn, cfg)  # segundo dia: status já é 'queued'
    order = [c["id"] for c in db.queued_companies(conn)]
    assert order == [p, n]
    assert db.get_company(conn, p)["queue_priority"] == 8.0


def test_expirada_promovida_volta_com_prazo_novo(conn, cfg):
    cid = make_company(conn, "Volta", prior=7.0)
    queue.refresh(conn, cfg)
    conn.execute("UPDATE companies SET queued_at='2020-01-01T00:00:00+00:00' WHERE id=?", (cid,))
    queue.refresh(conn, cfg)
    assert db.get_company(conn, cid)["queued_at"] is None
    db.set_company_status(conn, cid, "promoted", "tração subiu")
    queue.refresh(conn, cfg)
    queue.refresh(conn, cfg)
    company = db.get_company(conn, cid)
    assert company["status"] == "queued" and company["queued_at"] > "2026"


def test_dug_zera_bonus(conn, cfg):
    cid = make_company(conn, "X", prior=7.0, status="promoted")
    db.mark_dug(conn, cid)
    assert db.get_company(conn, cid)["promoted_at"] is None


# 7. observação igual à anterior não é regravada em outro dia

def test_observacao_igual_em_outro_dia_nao_regrava(conn):
    cid = make_company(conn, "A")
    db.add_observations(conn, cid, [{"metric": "team_size", "value_num": 30, "source": "yc"}],
                        observed_at="2026-10-01T10:00:00+00:00")
    assert db.add_observations(conn, cid, [{"metric": "team_size", "value_num": 30, "source": "yc"}],
                               observed_at="2026-10-02T10:00:00+00:00") == 0
    assert db.add_observations(conn, cid, [{"metric": "team_size", "value_num": 31, "source": "yc"}],
                               observed_at="2026-10-03T10:00:00+00:00") == 1


# 8. trava do kit da semana olha a data do kit, não a do dossiê

def test_trava_do_kit_usa_data_do_kit(conn):
    cid = make_company(conn, "A")
    did = db.save_dossier(conn, cid, {"attack_angle": "x", "score_posterior": 6.0,
                                      "created_at": "2026-01-01T00:00:00+00:00"})
    db.set_dossier_kit(conn, did, {"landing": {}}, "weekly_best")
    assert db.weekly_kit_since(conn, db.days_ago_iso(6))["id"] == did
    conn.execute("UPDATE dossiers SET kit_at='2026-01-01T00:00:00+00:00' WHERE id=?", (did,))
    assert db.weekly_kit_since(conn, db.days_ago_iso(6)) is None
