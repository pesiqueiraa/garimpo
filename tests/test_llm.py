import json

import pytest
import requests

from app import llm


class FakeResponse:
    def __init__(self, text, tokens_in=100, tokens_out=20):
        self._payload = {
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
        }

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_ask_json_parseia_e_conta_tokens(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    monkeypatch.setattr(requests, "post",
                        lambda *a, **kw: FakeResponse('{"ok": true}', 100, 20))
    data, t_in, t_out = llm.ask_json("qualquer prompt")
    assert data == {"ok": True}
    assert (t_in, t_out) == (100, 20)


def test_ask_json_retry_em_json_invalido(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    respostas = iter([FakeResponse("não sou json", 100, 20),
                      FakeResponse('{"ok": 1}', 150, 30)])
    chamadas = []

    def fake_post(*a, **kw):
        chamadas.append(kw["json"])
        return next(respostas)

    monkeypatch.setattr(requests, "post", fake_post)
    data, t_in, t_out = llm.ask_json("prompt")
    assert data == {"ok": 1}
    assert (t_in, t_out) == (250, 50)          # tokens dos 2 attempts somados
    assert len(chamadas) == 2
    # o retry inclui a resposta ruim e a instrucao corretiva no historico
    assert len(chamadas[1]["messages"]) == 3


def test_ask_json_estoura_apos_segundo_json_invalido(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    monkeypatch.setattr(requests, "post", lambda *a, **kw: FakeResponse("lixo"))
    with pytest.raises(llm.LLMError):
        llm.ask_json("prompt")


def test_ask_json_sem_chave(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(llm.LLMError):
        llm.ask_json("prompt")


def test_ask_json_remove_cerca_de_markdown(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    monkeypatch.setattr(requests, "post",
                        lambda *a, **kw: FakeResponse('```json\n{"a": 1}\n```'))
    data, _, _ = llm.ask_json("prompt")
    assert data == {"a": 1}


def test_cost_usd():
    assert llm.cost_usd(1_000_000, 0) == pytest.approx(0.25)
    assert llm.cost_usd(0, 1_000_000) == pytest.approx(2.00)


def test_ask_json_ignora_whitespace_na_chave(monkeypatch):
    # secrets colados com \n no final quebravam o header Authorization
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste\n")
    headers_capturados = {}

    def fake_post(*a, **kw):
        headers_capturados.update(kw["headers"])
        return FakeResponse('{"ok": true}')

    monkeypatch.setattr(requests, "post", fake_post)
    llm.ask_json("prompt")
    assert headers_capturados["Authorization"] == "Bearer sk-teste"
