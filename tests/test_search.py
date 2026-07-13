import requests

from app import llm, search
from tests.conftest import make_product


class FakeSerperResponse:
    def __init__(self, organic):
        self._organic = organic

    def raise_for_status(self):
        pass

    def json(self):
        return {"organic": self._organic}


def test_search_br_monta_payload_e_header(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "chave-serper\n")  # com \n de proposito
    capturado = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        capturado.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeSerperResponse([])

    monkeypatch.setattr(requests, "post", fake_post)
    search.search_br("app oficina Brasil")
    assert capturado["url"] == "https://google.serper.dev/search"
    assert capturado["headers"]["X-API-KEY"] == "chave-serper"   # strip aplicado
    assert capturado["json"] == {"q": "app oficina Brasil", "gl": "br", "hl": "pt"}
    assert capturado["timeout"] == 10


def test_search_br_parseia_organic(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "k")
    organic = [{"title": f"T{i}", "link": f"https://x{i}.com", "snippet": f"S{i}"}
               for i in range(12)]
    monkeypatch.setattr(requests, "post", lambda *a, **kw: FakeSerperResponse(organic))
    results = search.search_br("qualquer")
    assert len(results) == 8      # top 8
    assert results[0] == {"title": "T0", "url": "https://x0.com", "snippet": "S0"}


VEREDITO = {"verdict": "concorrente_fraco",
            "evidence": [{"title": "Rival BR", "url": "https://rival.com.br", "why": "app local"}],
            "score_final": 8.5, "rationale": "um player pequeno"}


def _produto(conn):
    pid = make_product(conn, "Alvo", score=7.5)
    return conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()


def test_verify_sem_chave_retorna_none(conn, monkeypatch, capsys):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    assert search.verify_br_competition(_produto(conn), "nenhum") is None
    assert "SERPER_API_KEY ausente" in capsys.readouterr().out


def test_verify_retorna_veredito_e_tokens(conn, monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "k")
    monkeypatch.setattr(search, "search_br",
                        lambda q, n=8: [{"title": "T", "url": "https://t.com", "snippet": "s"}])
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: (VEREDITO, 300, 40))
    data, t_in, t_out = search.verify_br_competition(_produto(conn), "nenhum player")
    assert data["verdict"] == "concorrente_fraco"
    assert data["evidence"][0]["url"] == "https://rival.com.br"
    assert (t_in, t_out) == (300, 40)


def test_verify_serper_fora_do_ar_retorna_none(conn, monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "k")

    def explode(q, n=8):
        raise requests.ConnectionError("sem rede")

    monkeypatch.setattr(search, "search_br", explode)
    assert search.verify_br_competition(_produto(conn), "nenhum") is None


def test_verify_llm_falhou_retorna_none(conn, monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "k")
    monkeypatch.setattr(search, "search_br", lambda q, n=8: [{"title": "T", "url": "u", "snippet": ""}])

    def explode(prompt, **kw):
        raise llm.LLMError("boom")

    monkeypatch.setattr(llm, "ask_json", explode)
    assert search.verify_br_competition(_produto(conn), "nenhum") is None


def test_verify_veredito_invalido_retorna_none(conn, monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "k")
    monkeypatch.setattr(search, "search_br", lambda q, n=8: [{"title": "T", "url": "u", "snippet": ""}])
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: ({"verdict": "sei_la"}, 10, 5))
    assert search.verify_br_competition(_produto(conn), "nenhum") is None


def test_verify_score_final_nao_numerico_vira_none(conn, monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "k")
    monkeypatch.setattr(search, "search_br",
                        lambda q, n=8: [{"title": "T", "url": "https://t.com", "snippet": "s"}])
    veredito = dict(VEREDITO, score_final="muito alto")
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: (veredito, 10, 5))
    data, _, _ = search.verify_br_competition(_produto(conn), "nenhum")
    assert data["score_final"] is None
    assert data["verdict"] == "concorrente_fraco"
