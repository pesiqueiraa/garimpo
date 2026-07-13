from app import enrich, llm
from tests.conftest import make_product


FAKE = {"niche": "gestão de oficinas", "pain": "agendamento manual",
        "audience": "B2B", "br_verdict": "desconhecido", "opportunity_score": 8}


def test_enrich_usa_llm_e_grava(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    pid = make_product(conn, "Novo", score=8.0)
    conn.execute("UPDATE products SET niche=NULL WHERE id=?", (pid,))
    conn.commit()
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: (FAKE, 100, 20))

    assert enrich.enrich_rows(conn) == 1
    row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    assert row["niche"] == "gestão de oficinas"
    assert row["opportunity_score"] == 8


def test_enrich_pula_sem_chave(conn, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert enrich.enrich_rows(conn) == 0


def test_enrich_sobrevive_a_falha_do_llm(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    pid = make_product(conn, "Azarado", score=8.0)
    conn.execute("UPDATE products SET niche=NULL WHERE id=?", (pid,))
    conn.commit()

    def explode(prompt, **kw):
        raise llm.LLMError("boom")

    monkeypatch.setattr(llm, "ask_json", explode)
    assert enrich.enrich_rows(conn) == 0   # nao levanta excecao


def test_enrich_only_id_enriquece_so_um(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    alvo = make_product(conn, "Alvo", score=8.0)
    outro = make_product(conn, "Outro", score=8.0)
    conn.execute("UPDATE products SET niche=NULL")
    conn.commit()
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: (FAKE, 100, 20))

    assert enrich.enrich_rows(conn, only_id=alvo) == 1
    assert conn.execute("SELECT niche FROM products WHERE id=?", (alvo,)).fetchone()["niche"]
    assert conn.execute("SELECT niche FROM products WHERE id=?", (outro,)).fetchone()["niche"] is None
