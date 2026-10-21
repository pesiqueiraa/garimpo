from urllib.parse import parse_qs, urlsplit

import requests

from app import db, feedback
from tests.conftest import FakeResponse, make_company


def _github_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok\n")
    monkeypatch.setenv("GITHUB_REPOSITORY", "dono/garimpo")


# ---------- links e títulos ----------

def test_feedback_link_sem_repo_e_none(cfg):
    assert feedback.feedback_link(cfg, 7, "like") is None


def test_feedback_link_com_repo(cfg, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "dono/garimpo")
    url = feedback.feedback_link(cfg, 7, "dislike")
    parts = urlsplit(url)
    assert url.startswith("https://github.com/dono/garimpo/issues/new?")
    query = parse_qs(parts.query)
    assert query["title"] == ["feedback dislike 7"]
    assert query["labels"] == ["garimpo-feedback"]
    assert "7" in query["body"][0]
    assert feedback.feedback_link(cfg, 7, "hack") is None


def test_feedback_link_usa_repo_da_config(cfg, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "outro/repo")
    cfg["fontes"].setdefault("entrega", {})["github_repo"] = "meu/garimpo"
    assert feedback.feedback_link(cfg, 1, "watch").startswith("https://github.com/meu/garimpo/")


def test_parse_title():
    assert feedback.parse_title("feedback like 42") == ("like", 42)
    assert feedback.parse_title("Feedback  WATCH 7 ") == ("watch", 7)
    assert feedback.parse_title("feedback+dislike+3") is None
    assert feedback.parse_title("feedback love 3") is None
    assert feedback.parse_title(None) is None


# ---------- efeito no status ----------

def test_record_idempotente(conn):
    cid = make_company(conn, "Agendei")
    assert feedback.record(conn, cid, "like", "email", external_ref="10") is True
    assert feedback.record(conn, cid, "like", "email", external_ref="10") is False
    assert len(db.feedback_for(conn, cid)) == 1


def test_like_vira_watching_mas_nao_rebaixa(conn):
    nova = make_company(conn, "Nova")
    feedback.record(conn, nova, "like", "cli")
    assert db.get_company(conn, nova)["status"] == "watching"
    for status in ("dug", "queued", "validating", "building"):
        cid = make_company(conn, f"Emp{status}", status=status)
        feedback.record(conn, cid, "watch", "cli")
        assert db.get_company(conn, cid)["status"] == status


def test_dislike_descarta_exceto_validando(conn):
    cid = make_company(conn, "Ruim", status="queued")
    feedback.record(conn, cid, "dislike", "cli")
    row = db.get_company(conn, cid)
    assert row["status"] == "discarded"
    assert row["status_reason"] == "descartado por você"
    keep = make_company(conn, "Validando", status="validating")
    feedback.record(conn, keep, "discarded", "cli")
    assert db.get_company(conn, keep)["status"] == "validating"


def test_validating_e_building(conn):
    cid = make_company(conn, "Projeto")
    feedback.record(conn, cid, "validating", "cli")
    assert db.get_company(conn, cid)["status"] == "validating"
    feedback.record(conn, cid, "building", "cli")
    assert db.get_company(conn, cid)["status"] == "building"


# ---------- leitura das Issues ----------

def test_read_sem_token_pula(conn, cfg):
    assert feedback.read(conn, cfg) == {"email": 0, "labels": 0, "closed": 0, "unknown": 0,
                                        "ignored": 0}


def _mock_github(monkeypatch, feedback_issues, dossier_issues):
    calls = {"get": [], "post": [], "patch": []}

    def fake_get(url, headers=None, params=None, timeout=None, **kw):
        calls["get"].append((url, params))
        assert headers["Authorization"] == "Bearer tok"
        if params and params.get("labels") == "garimpo-feedback":
            return FakeResponse(feedback_issues if params.get("page") == 1 else [])
        if params and params.get("labels") == "garimpo-dossie":
            return FakeResponse(dossier_issues if params.get("page") == 1 else [])
        return FakeResponse([])

    def fake_post(url, headers=None, json=None, timeout=None, **kw):
        calls["post"].append((url, json))
        return FakeResponse({"id": 1}, status_code=201)

    def fake_patch(url, headers=None, json=None, timeout=None, **kw):
        calls["patch"].append((url, json))
        return FakeResponse({"state": "closed"})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "patch", fake_patch)
    return calls


def test_read_registra_e_fecha_issues_de_feedback(conn, cfg, monkeypatch):
    _github_env(monkeypatch)
    cid = make_company(conn, "Clinicando")
    issues = [{"number": 5, "title": f"feedback like {cid}", "labels": []},
              {"number": 6, "title": "feedback dislike 99999", "labels": []},
              {"number": 7, "title": "pergunta qualquer", "labels": []},
              {"number": 8, "title": f"feedback watch {cid}", "pull_request": {}}]
    calls = _mock_github(monkeypatch, issues, [])
    counts = feedback.read(conn, cfg)

    assert counts["email"] == 1 and counts["unknown"] == 1 and counts["closed"] == 1
    assert db.latest_signal(conn, cid) == "like"
    assert db.get_company(conn, cid)["status"] == "watching"
    closed = sorted(url.rsplit("/", 1)[1] for url, body in calls["patch"])
    assert closed == ["5", "6"]
    comments = {url.split("/issues/")[1]: body["body"] for url, body in calls["post"]}
    assert comments["5/comments"] == "Registrado ✅"
    assert "não existe" in comments["6/comments"]

    # segunda leitura (issue ainda aberta por algum motivo) não duplica o sinal
    feedback.read(conn, cfg)
    assert len(db.feedback_for(conn, cid)) == 1


def test_read_mapeia_labels_de_status_das_issues_de_dossie(conn, cfg, monkeypatch):
    _github_env(monkeypatch)
    by_db = make_company(conn, "PeloBanco")
    by_marker = make_company(conn, "PeloMarcador", status="dug")
    dossier_id = db.save_dossier(conn, by_db, {"issue_number": 11})
    conn.commit()
    dossier_issues = [
        {"number": 11, "title": "🔬 PeloBanco", "body": "",
         "labels": [{"name": "garimpo-dossie"}, {"name": "status:validando"}]},
        {"number": 12, "title": "🔬 PeloMarcador",
         "body": f"texto\n<!-- garimpo:dossier=999 company={by_marker} -->",
         "labels": [{"name": "garimpo-dossie"}, {"name": "status:descartado"}, {"name": "status:novo"}]},
        {"number": 13, "title": "🔬 Sem dono", "body": "", "labels": [{"name": "status:construindo"}]},
    ]
    _mock_github(monkeypatch, [], dossier_issues)
    counts = feedback.read(conn, cfg)

    assert counts["labels"] == 2
    assert db.get_company(conn, by_db)["status"] == "validating"
    assert db.get_company(conn, by_marker)["status"] == "discarded"
    refs = {r["external_ref"] for r in db.feedback_for(conn, by_db)}
    assert refs == {"11:status:validando"}
    assert dossier_id

    # idempotente: ler de novo não grava outro sinal
    assert feedback.read(conn, cfg)["labels"] == 0
    assert len(db.feedback_for(conn, by_marker)) == 1


def test_read_falha_de_rede_nao_levanta(conn, cfg, monkeypatch):
    _github_env(monkeypatch)

    def boom(*a, **k):
        raise requests.ConnectionError("fora do ar")

    monkeypatch.setattr(requests, "get", boom)
    assert feedback.read(conn, cfg)["email"] == 0


def test_read_ignora_feedback_de_estranhos(conn, cfg, monkeypatch):
    _github_env(monkeypatch)
    cid = make_company(conn, "Alvo")
    issues = [{"number": 20, "title": f"feedback dislike {cid}", "author_association": "NONE"},
              {"number": 21, "title": f"feedback like {cid}", "author_association": "OWNER"}]
    calls = _mock_github(monkeypatch, issues, [])
    counts = feedback.read(conn, cfg)
    assert counts["ignored"] == 1 and counts["email"] == 1
    assert db.latest_signal(conn, cid) == "like"
    assert [url.rsplit("/", 1)[1] for url, _ in calls["patch"]] == ["21"]
    params = calls["get"][0][1]
    assert params["sort"] == "updated" and params["per_page"] == 100
