import copy

import pytest
import requests

from app import db, extract, llm
from tests.conftest import FACTS, FakeResponse, make_company


def _chat(content, tin=100, tout=20):
    return FakeResponse({"choices": [{"message": {"content": content}}],
                         "usage": {"prompt_tokens": tin, "completion_tokens": tout}})


def test_sem_chave_levanta(monkeypatch):
    with pytest.raises(llm.LLMError):
        llm.ask_json("oi")


def test_retry_quando_json_invalido(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    answers = iter([_chat("isso não é json"), _chat('{"ok": 1}')])
    monkeypatch.setattr(requests, "post", lambda *a, **kw: next(answers))
    data, tin, tout = llm.ask_json("oi")
    assert data == {"ok": 1} and tin == 200 and tout == 40


def test_schema_vai_no_pedido(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(json)
        return _chat('{"a": "b"}')
    monkeypatch.setattr(requests, "post", fake_post)
    llm.ask_json("oi", schema={"name": "x", "schema": {"type": "object"}})
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True


def test_schema_recusado_cai_para_json_object(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    formats = []

    def fake_post(url, headers, json, timeout):
        formats.append(json["response_format"]["type"])
        if json["response_format"]["type"] == "json_schema":
            return FakeResponse({"error": "bad schema"}, status_code=400)
        return _chat('{"ok": true}')
    monkeypatch.setattr(requests, "post", fake_post)
    data, _, _ = llm.ask_json("oi", schema={"name": "x", "schema": {"type": "object"}})
    assert data == {"ok": True} and formats == ["json_schema", "json_object"]


def test_cache_nao_cobra_duas_vezes(monkeypatch, conn):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **kw: calls.append(1) or _chat('{"v": 1}'))
    llm.configure(conn)
    assert llm.ask_json("mesmo prompt")[1] == 100
    assert llm.ask_json("mesmo prompt") == ({"v": 1}, 0, 0)
    assert len(calls) == 1 and llm.usage()["cached"] == 1


def test_orcamento_da_execucao(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _chat('{"v": 1}', 1_000_000, 0))
    llm.configure(budget_usd=0.20)
    llm.ask_json("primeira")  # gasta US$0,25
    with pytest.raises(llm.BudgetExceeded):
        llm.ask_json("segunda")


def test_embed_em_lote(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")

    def fake_post(url, headers, json, timeout):
        assert json["dimensions"] == 8
        return FakeResponse({"data": [{"index": i, "embedding": [float(i)] * 8}
                                      for i in range(len(json["input"]))][::-1],
                             "usage": {"total_tokens": 10}})
    monkeypatch.setattr(requests, "post", fake_post)
    vectors = llm.embed(["a", "b", "c"], dims=8)
    assert [v[0] for v in vectors] == [0.0, 1.0, 2.0]


def test_normalize_corrige_campos_ruins():
    raw = {"audience": "B2Z", "business_type": "foguete", "local_rails": {"payments": "sim"},
           "kill_flags": ["crypto", "inventado"], "br_cnae_guess": "8630-5/04",
           "search_keywords_pt": ["  agenda  ", "", 3]}
    facts = extract.normalize(raw)
    assert facts["audience"] == "ambos" and facts["business_type"] == "other"
    assert facts["local_rails"]["payments"] is False  # só True de verdade conta
    assert facts["kill_flags"] == ["crypto"]
    assert facts["br_cnae_guess"] == "8630504"
    assert facts["search_keywords_pt"] == ["agenda", "3"]
    assert facts["sales_motion"] == "desconhecido"


def test_extract_usa_texto_do_site(monkeypatch, conn):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    cid = make_company(conn, "Acme")
    db.save_site_page(conn, cid, {"final_url": "https://acme.com", "text": "Agenda para dentistas",
                                  "pricing_text": "US$ 29 por mês"})
    prompts = []

    def fake_ask(prompt, **kw):
        prompts.append(prompt)
        assert kw["schema"]["name"] == "extracao_produto"
        return copy.deepcopy(FACTS), 10, 5
    monkeypatch.setattr(llm, "ask_json", fake_ask)
    assert extract.run(conn, {"fontes": {}}) == 1
    assert "Agenda para dentistas" in prompts[0] and "US$ 29" in prompts[0]
    assert db.latest_extraction(conn, cid)["niche"] == FACTS["niche"]
    assert extract.run(conn, {"fontes": {}}) == 0  # já extraída nesta versão


def test_extract_para_quando_estoura_orcamento(monkeypatch, conn):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    for name in ("A", "B", "C"):
        make_company(conn, name)
    calls = []

    def fake_ask(prompt, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise llm.BudgetExceeded("teto")
        return copy.deepcopy(FACTS), 1, 1
    monkeypatch.setattr(llm, "ask_json", fake_ask)
    assert extract.run(conn, {"fontes": {}}) == 1
    assert len(calls) == 2


def test_extract_sem_chave_pula(conn):
    make_company(conn, "A")
    assert extract.run(conn, {"fontes": {}}) == 0


def test_extract_em_paralelo_grava_na_thread_principal(monkeypatch, conn):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    ids = [make_company(conn, f"P{i}") for i in range(7)]

    def fake_ask(prompt, **kw):
        assert kw["cache"] is False  # cache usa SQLite, que não cruza threads
        return copy.deepcopy(FACTS), 10, 5
    monkeypatch.setattr(llm, "ask_json", fake_ask)
    assert extract.run(conn, {"fontes": {}}, workers=3) == 7
    assert all(db.latest_extraction(conn, i) for i in ids)


def test_extract_respeita_prazo(monkeypatch, conn):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    make_company(conn, "A")
    monkeypatch.setattr(llm, "ask_json", lambda *a, **kw: (copy.deepcopy(FACTS), 1, 1))
    assert extract.run(conn, {"fontes": {}}, deadline=0.0001) == 0
