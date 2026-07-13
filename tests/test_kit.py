from app import kit, llm
from tests.conftest import make_product


KIT_OK = {"landing": {"headline": "Oficinas sem calote",
                      "subheadline": "Cobranca via Pix no fluxo da OS",
                      "benefits": ["Pix nativo", "Lembrete WhatsApp", "NF automatica"],
                      "cta": "Entrar na lista de espera"},
          "mom_test": [f"pergunta {i}" for i in range(5)],
          "whatsapp": "Oi pessoal, alguem aqui ja passou por..."}


def _produto(conn):
    pid = make_product(conn, "Alvo", score=8.5)
    return conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()


def test_build_kit_valido(conn, monkeypatch):
    capturado = {}

    def fake_ask(prompt, **kw):
        capturado.update(kw)
        return KIT_OK, 500, 80

    monkeypatch.setattr(llm, "ask_json", fake_ask)
    data, t_in, t_out = kit.build_kit(_produto(conn), "cobranca confusa (12x)", "concorrente_fraco")
    assert data == KIT_OK and (t_in, t_out) == (500, 80)
    assert capturado["reasoning_effort"] == "medium"


def test_build_kit_estrutura_invalida(conn, monkeypatch, capsys):
    quebrado = {"landing": {"headline": "H", "subheadline": "S",
                            "benefits": ["so", "dois"], "cta": "C"},
                "mom_test": ["p"] * 5, "whatsapp": "oi"}
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: (quebrado, 10, 5))
    assert kit.build_kit(_produto(conn), "", "") is None
    assert "[kit] estrutura invalida" in capsys.readouterr().out


def test_build_kit_llm_falhou(conn, monkeypatch):
    def explode(prompt, **kw):
        raise llm.LLMError("boom")

    monkeypatch.setattr(llm, "ask_json", explode)
    assert kit.build_kit(_produto(conn), "", "") is None
