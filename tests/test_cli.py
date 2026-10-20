"""Ponta a ponta: o `rodar` orquestra todas as etapas, isola falhas e sempre gera o e-mail."""
import copy
from pathlib import Path

import pytest

from app import cli, db, llm
from app import main as compat
from tests.conftest import FACTS


@pytest.fixture
def prod_db(tmp_path, monkeypatch):
    path = tmp_path / "garimpo.db"
    monkeypatch.setattr("app.db.core.DB_PATH", path)
    monkeypatch.setattr("app.db.DB_PATH", path)
    return path


ITEMS = [
    {"name": "Agendaí", "tagline": "Agenda e cobrança para clínicas", "url": "https://agendai.com",
     "source": "hackernews", "kind": "launch", "external_id": "hn-1", "points": 150},
    {"name": "FPGA LLM", "tagline": "LLM a 21k tok/s num FPGA", "url": "https://fpga-llm.dev",
     "source": "hackernews", "kind": "launch", "external_id": "hn-2", "points": 300},
    {"name": "Agendaí", "tagline": "Agenda e cobrança para clínicas", "url": "https://www.agendai.com/",
     "source": "producthunt", "kind": "launch", "external_id": "ph-1", "points": 200},
]


def _edges(monkeypatch, items=ITEMS, llm_on=True):
    """Mocka só as bordas: coleta, rede dos sinais, LLM e SMTP."""
    from app import deliver, signals, sources
    monkeypatch.setattr(sources, "collect_all",
                        lambda cfg: (copy.deepcopy(items), {"hackernews": 2, "producthunt": 1}))
    monkeypatch.setattr(signals.site, "probe", lambda url, cfg: None)
    monkeypatch.setattr(signals, "run", lambda conn, cfg, company_ids=None: 0)
    sent = []
    monkeypatch.setattr(deliver.mail, "send", lambda html, subject: sent.append(subject) or True)
    if llm_on:
        monkeypatch.setenv("OPENAI_API_KEY", "k")

        def fake_ask(prompt, **kw):
            facts = copy.deepcopy(FACTS)
            if "FPGA" in prompt:
                facts["kill_flags"] = ["research_demo"]
                facts["business_type"] = "ai_model_or_infra"
            return facts, 100, 50
        monkeypatch.setattr(llm, "ask_json", fake_ask)
        monkeypatch.setattr(llm, "embed", lambda texts, dims=256: [[1.0] + [0.0] * (dims - 1)
                                                                   for _ in texts])
    return sent


def test_rodar_completo_sem_rede(prod_db, monkeypatch):
    from app import dig
    sent = _edges(monkeypatch)
    dug = []

    def fake_dig_run(conn, cfg):
        for c in db.queued_companies(conn, 2):
            did = db.save_dossier(conn, c["id"], {"attack_angle": "ângulo", "br_verdict": "sem_concorrente",
                                                  "score_posterior": 8.1})
            db.mark_dug(conn, c["id"])
            dug.append(did)
        return len(dug)
    monkeypatch.setattr(dig, "run", fake_dig_run)

    assert cli.main(["rodar"]) == 0
    conn = db.connect(prod_db)
    agendai = db.find_company_by_key(conn, "agendai.com")
    fpga = db.find_company_by_key(conn, "fpga-llm.dev")
    assert db.company_sources(conn, agendai["id"]) == ["hackernews", "producthunt"]
    assert db.get_company(conn, fpga["id"])["status"] == "discarded"
    assert db.latest_score(conn, agendai["id"])["total"] >= 6
    assert dug and db.get_company(conn, agendai["id"])["status"] == "dug"

    run = db.recent_runs(conn, 1)[0]
    assert run["source_counts"] == {"hackernews": 2, "producthunt": 1}
    assert run["errors"] == []
    assert run["tokens_in"] > 0
    assert sent and sent[0].startswith("⛏️")
    assert list(Path(prod_db).parent.glob("relatorio-*.html"))


def test_rodar_sem_nenhuma_chave_ainda_gera_email(prod_db, monkeypatch):
    sent = _edges(monkeypatch, llm_on=False)
    assert cli.main(["rodar", "--sem-email"]) == 0
    conn = db.connect(prod_db)
    run = db.recent_runs(conn, 1)[0]
    assert run["errors"] == []
    assert sent == []  # --sem-email
    html = next(Path(prod_db).parent.glob("relatorio-*.html")).read_text()
    assert "Agendaí" in html or "Garimpo" in html


def test_etapa_que_quebra_nao_derruba_o_resto(prod_db, monkeypatch):
    from app import revisit
    _edges(monkeypatch)

    def boom(conn, cfg):
        raise RuntimeError("explodiu")
    monkeypatch.setattr(revisit, "run", boom)
    assert cli.main(["rodar", "--sem-email"]) == 0
    conn = db.connect(prod_db)
    run = db.recent_runs(conn, 1)[0]
    assert any("revisitas" in e and "explodiu" in e for e in run["errors"])
    assert "email_diario" in run["stats"]


def test_rodar_semanal(prod_db, monkeypatch):
    sent = _edges(monkeypatch)
    assert cli.main(["rodar", "--semanal"]) == 0
    assert len(sent) == 2 and sent[1].startswith("📊")


def test_dia_semanal_no_fuso_de_brasilia(cfg):
    from datetime import datetime, timezone
    # domingo 02:00 UTC ainda é sábado em Brasília
    assert not cli.is_weekly_day(cfg, datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc))
    assert cli.is_weekly_day(cfg, datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc))


def test_feedback_pela_linha_de_comando(prod_db, monkeypatch):
    _edges(monkeypatch)
    cli.main(["coletar"])
    conn = db.connect(prod_db)
    cid = db.find_company_by_key(conn, "agendai.com")["id"]
    assert cli.main(["feedback", str(cid), "like", "--nota", "gostei"]) == 0
    assert db.latest_signal(db.connect(prod_db), cid) == "like"
    assert cli.main(["feedback", "999", "like"]) == 1


def test_atalho_da_v1():
    assert compat.translate([]) == ["rodar"]
    assert compat.translate(["--no-mail"]) == ["rodar", "--sem-email"]
    assert compat.translate(["--investigar", "acme"]) == ["investigar", "acme"]
