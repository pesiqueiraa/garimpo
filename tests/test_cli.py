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


KIT = {"landing": {"headline": "H", "subheadline": "S", "benefits": ["a", "b", "c"], "cta": "C"},
       "mom_test": ["p1", "p2", "p3", "p4", "p5"], "whatsapp": "oi, pessoal",
       "where_to_find": ["Associação Brasileira de Odontologia", "grupos de dentistas no Facebook",
                         "categoria 'clínica odontológica' no Google Maps"]}


def fake_llm(prompt, **kw):
    """LLM falso que responde conforme o prompt (extração, escavação, kit, tendência)."""
    if "extrai FATOS" in prompt:
        facts = copy.deepcopy(FACTS)
        if "FPGA" in prompt:
            facts["kill_flags"] = ["research_demo"]
            facts["business_type"] = "ai_model_or_infra"
        return facts, 100, 50
    if "kit de validação" in prompt:
        return copy.deepcopy(KIT), 80, 40
    if "ângulo de ataque" in prompt:
        return {"attack_angle": "Atacar a cobrança manual com Pix Automático e lembretes no WhatsApp."}, 90, 60
    if "concorrentes" in prompt:
        return {"competitors": [{"name": "Rival", "search_term": "agenda clinica"}]}, 30, 10
    if "TEMA comum" in prompt:
        return {"label": "agendas para clínicas"}, 20, 5
    return {}, 1, 1


def _edges(monkeypatch, items=ITEMS, llm_on=True):
    """Mocka só as bordas: coleta, rede dos sinais e das lojas, LLM e SMTP."""
    from app import deliver, signals, sources
    from app.dig import appstore, playstore
    monkeypatch.setattr(sources, "collect_all",
                        lambda cfg: (copy.deepcopy(items), {"hackernews": 2, "producthunt": 1}))
    monkeypatch.setattr(signals.site, "probe", lambda url, cfg=None: None)
    monkeypatch.setattr(signals, "run", lambda conn, cfg, company_ids=None: 0)
    monkeypatch.setattr(playstore, "confirm_on_play_store", lambda term, pause_s=0: None)
    monkeypatch.setattr(appstore, "confirm_on_app_store", lambda term, pause_s=0: None)
    sent = []
    monkeypatch.setattr(deliver.mail, "send", lambda html, subject: sent.append(subject) or True)
    if llm_on:
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        monkeypatch.setattr(llm, "ask_json", fake_llm)
        monkeypatch.setattr(llm, "embed", lambda texts, dims=256: [[1.0] + [0.0] * (dims - 1)
                                                                   for _ in texts])
    return sent


def test_rodar_completo_sem_rede(prod_db, monkeypatch):
    sent = _edges(monkeypatch)
    assert cli.main(["rodar"]) == 0
    conn = db.connect(prod_db)
    agendai = db.find_company_by_key(conn, "agendai.com")
    fpga = db.find_company_by_key(conn, "fpga-llm.dev")
    assert db.company_sources(conn, agendai["id"]) == ["hackernews", "producthunt"]
    assert db.get_company(conn, fpga["id"])["status"] == "discarded"
    assert db.latest_score(conn, agendai["id"])["total"] >= 6

    # escavação de verdade (lojas e busca mockadas): dossiê com score posterior
    d = db.last_dossier_for_company(conn, agendai["id"])
    assert d is not None and d["dossier"]["score_posterior"] is not None
    assert "Pix Automático" in d["dossier"]["attack_angle"]
    assert db.get_company(conn, agendai["id"])["status"] == "dug"
    assert db.last_dossier_for_company(conn, fpga["id"]) is None

    run = db.recent_runs(conn, 1)[0]
    assert run["source_counts"] == {"hackernews": 2, "producthunt": 1}
    assert run["errors"] == []
    assert d["dossier"]["tokens_in"] > 0  # custo do dossiê vem dos tokens devolvidos pelo LLM
    assert sent and sent[0].startswith("⛏️")
    html = next(Path(prod_db).parent.glob("relatorio-*.html")).read_text()
    assert "Agendaí" in html and "FPGA" not in html


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


def _um_dia_depois(path):
    """Empurra todos os carimbos de tempo do banco 1 dia para trás (simula o dia seguinte)."""
    from datetime import datetime, timedelta
    columns = {"runs": ("started_at", "finished_at"), "companies": ("first_seen", "last_seen",
               "status_changed_at", "queued_at"), "events": ("seen_at",),
               "dossiers": ("created_at",), "scores": ("created_at",),
               "extractions": ("created_at",), "observations": ("observed_at",)}
    conn = db.connect(path)
    for table, cols in columns.items():
        for col in cols:
            for rowid, value in conn.execute(f"SELECT rowid, {col} FROM {table}").fetchall():
                if value:
                    shifted = (datetime.fromisoformat(value) - timedelta(days=1)).isoformat(timespec="seconds")
                    conn.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?", (shifted, rowid))
    conn.commit()
    conn.close()


def test_dois_dias_seguidos_sem_repetir_nem_duplicar(prod_db, monkeypatch):
    sent = _edges(monkeypatch)
    assert cli.main(["rodar"]) == 0
    day1 = next(Path(prod_db).parent.glob("relatorio-*.html")).read_text()
    assert "Agendaí" in day1
    _um_dia_depois(prod_db)

    novo = {"name": "Notinha", "tagline": "Notas fiscais para freelancers",
            "url": "https://notinha.io", "source": "hackernews", "kind": "launch",
            "external_id": "hn-3", "points": 120}
    _edges(monkeypatch, items=ITEMS + [novo])
    assert cli.main(["rodar"]) == 0
    day2 = next(Path(prod_db).parent.glob("relatorio-*.html")).read_text()
    assert "Notinha" in day2
    assert "Agendaí" not in day2  # o dossiê de ontem não volta no e-mail de hoje

    conn = db.connect(prod_db)
    assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM dossiers WHERE company_id=?",
                        (db.find_company_by_key(conn, "agendai.com")["id"],)).fetchone()[0] == 1
    assert all(r["errors"] == [] for r in db.recent_runs(conn, 2))
