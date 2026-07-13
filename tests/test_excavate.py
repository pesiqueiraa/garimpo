from app import db, excavate, llm
from tests.conftest import make_product


VEREDITO = {"verdict": "concorrente_fraco",
            "evidence": [{"title": "Rival BR", "url": "https://rival.com.br", "why": "app local"}],
            "score_final": 8.5, "rationale": "player pequeno e mal avaliado"}
KIT_OK = {"landing": {"headline": "H", "subheadline": "S",
                      "benefits": ["b1", "b2", "b3"], "cta": "C"},
          "mom_test": ["p1", "p2", "p3", "p4", "p5"],
          "whatsapp": "oi grupo"}


def _mock_v12(monkeypatch, verdict=VEREDITO, kit_data=KIT_OK):
    from app import kit as kit_mod
    from app import search as search_mod
    monkeypatch.setattr(search_mod, "verify_br_competition",
                        lambda product, players_block: (verdict, 200, 30) if verdict else None)
    monkeypatch.setattr(kit_mod, "build_kit",
                        lambda product, cb, vb: (kit_data, 300, 40) if kit_data else None)


def test_confirm_acha_na_loja_br(monkeypatch):
    def fake_search(term, lang, country, n_hits):
        assert (lang, country) == ("pt", "br")
        return [{"appId": "br.com.rival", "title": "Rival Brasil", "score": 3.4}]

    monkeypatch.setattr(excavate, "gp_search", fake_search)
    monkeypatch.setattr(excavate, "gp_app",
                        lambda app_id, lang, country: {"score": 3.4, "ratings": 5000})
    monkeypatch.setattr(excavate.time, "sleep", lambda s: None)
    c = excavate.confirm_on_play_store("rival")
    assert c == {"app_id": "br.com.rival", "name": "Rival Brasil",
                 "store": "br", "rating": 3.4, "ratings_count": 5000}


def test_confirm_cai_para_loja_us(monkeypatch):
    buscas = []

    def fake_search(term, lang, country, n_hits):
        buscas.append(country)
        if country == "br":
            return []
        return [{"appId": "com.global.app", "title": "GlobalApp", "score": 4.1}]

    monkeypatch.setattr(excavate, "gp_search", fake_search)
    monkeypatch.setattr(excavate, "gp_app",
                        lambda app_id, lang, country: {"score": 4.1, "ratings": 90000})
    monkeypatch.setattr(excavate.time, "sleep", lambda s: None)
    c = excavate.confirm_on_play_store("globalapp")
    assert buscas == ["br", "us"]
    assert c["store"] == "us"


def test_confirm_retorna_none_sem_resultado(monkeypatch):
    monkeypatch.setattr(excavate, "gp_search", lambda *a, **kw: [])
    monkeypatch.setattr(excavate.time, "sleep", lambda s: None)
    assert excavate.confirm_on_play_store("inexistente") is None


def test_collect_reviews_junta_1_2_3_estrelas(monkeypatch):
    estrelas_pedidas = []

    def fake_reviews(app_id, lang, country, count, filter_score_with, sort):
        estrelas_pedidas.append(filter_score_with)
        return ([{"content": f"review {filter_score_with}"}, {"content": None}], None)

    monkeypatch.setattr(excavate, "gp_reviews", fake_reviews)
    monkeypatch.setattr(excavate.time, "sleep", lambda s: None)
    revs = excavate.collect_negative_reviews("com.x", "br")
    assert estrelas_pedidas == [1, 2, 3]
    assert revs == ["review 1", "review 2", "review 3"]   # None filtrado


def test_sample_reviews_trunca_e_limita(monkeypatch):
    grande = {"AppA": ["x" * 999] * 200, "AppB": ["curta"] * 200}
    sampled = excavate.sample_reviews(grande, cap=10)
    assert len(sampled["AppA"]) == 5 and len(sampled["AppB"]) == 5
    assert len(sampled["AppA"][0]) == 300


SUGGESTION = {"competitors": [{"name": "Rival", "search_term": "rival oficina"}]}
CLUSTERS = {"clusters": [{"theme": "cobrança confusa", "frequency": 12,
                          "severity": "alta",
                          "examples": [{"app": "Rival", "excerpt": "cobraram 2x"}]}]}
ANGLE = {"attack_angle": "Pix nativo e cobrança transparente."}


def _mock_llm_ok(monkeypatch):
    respostas = iter([SUGGESTION, CLUSTERS, ANGLE])
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: (next(respostas), 1000, 100))


def _mock_store_ok(monkeypatch):
    monkeypatch.setattr(excavate, "confirm_on_play_store",
                        lambda term: {"app_id": "br.rival", "name": "Rival",
                                      "store": "br", "rating": 3.1,
                                      "ratings_count": 8000})
    monkeypatch.setattr(excavate, "collect_negative_reviews",
                        lambda app_id, store: ["péssimo, cobraram duas vezes"] * 30)


def test_excavate_all_gera_dossie_completo(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    pid = make_product(conn, "Alvo", score=9.0)
    _mock_llm_ok(monkeypatch)
    _mock_store_ok(monkeypatch)

    assert excavate.excavate_all(conn) == 1
    row = conn.execute("SELECT dig_status FROM products WHERE id=?", (pid,)).fetchone()
    assert row["dig_status"] == "done"
    d = db.dossiers_since(conn, "2000-01-01T00:00:00")[0]
    assert d["dossier"]["attack_angle"] == "Pix nativo e cobrança transparente."
    assert d["competitors"][0]["reviews_collected"] == 30
    assert d["clusters"][0]["theme"] == "cobrança confusa"
    assert d["dossier"]["cost_usd"] > 0


def test_excavate_sem_players_gera_dossie_vazio(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    pid = make_product(conn, "WebOnly", score=8.0)
    respostas = iter([SUGGESTION, ANGLE])   # sem chamada de cluster
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: (next(respostas), 500, 50))
    monkeypatch.setattr(excavate, "confirm_on_play_store", lambda term: None)

    assert excavate.excavate_all(conn) == 1
    d = db.dossiers_since(conn, "2000-01-01T00:00:00")[0]
    assert d["competitors"] == [] and d["clusters"] == []
    assert d["dossier"]["attack_angle"]


def test_excavate_falha_incrementa_tentativa(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    pid = make_product(conn, "Azarado", score=8.0)

    def explode(prompt, **kw):
        raise llm.LLMError("boom")

    monkeypatch.setattr(llm, "ask_json", explode)
    assert excavate.excavate_all(conn) == 0
    row = conn.execute("SELECT dig_status, dig_attempts FROM products WHERE id=?",
                       (pid,)).fetchone()
    assert row["dig_status"] is None and row["dig_attempts"] == 1


def test_excavate_estouro_de_orcamento_marca_failed(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    pid = make_product(conn, "Caro", score=8.0)
    # 1M tokens de saida = US$2.00 > teto de 0.25
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: (SUGGESTION, 0, 1_000_000))
    _mock_store_ok(monkeypatch)

    assert excavate.excavate_all(conn) == 0
    row = conn.execute("SELECT dig_status FROM products WHERE id=?", (pid,)).fetchone()
    assert row["dig_status"] == "failed"


def test_excavate_pula_sem_chave(conn, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    make_product(conn, "Qualquer", score=9.0)
    assert excavate.excavate_all(conn) == 0
    # produto continua elegivel (nao conta tentativa)
    assert len(db.dig_queue(conn)) == 1


def test_excavate_com_veredito_real_atualiza_tudo(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    pid = make_product(conn, "Alvo", score=9.0)
    _mock_llm_ok(monkeypatch)
    _mock_store_ok(monkeypatch)
    _mock_v12(monkeypatch)

    assert excavate.excavate_all(conn) == 1
    prod = conn.execute("SELECT br_verdict, opportunity_score FROM products WHERE id=?",
                        (pid,)).fetchone()
    assert prod["br_verdict"] == "concorrente_fraco"
    assert prod["opportunity_score"] == 8.5
    d = db.dossiers_since(conn, "2000-01-01T00:00:00")[0]["dossier"]
    assert d["br_verdict_final"] == "concorrente_fraco"
    assert d["score_final"] == 8.5
    import json as _json
    assert _json.loads(d["br_evidence"])[0]["url"] == "https://rival.com.br"
    assert _json.loads(d["kit"])["mom_test"] == ["p1", "p2", "p3", "p4", "p5"]


def test_excavate_score_baixo_nao_gera_kit(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    make_product(conn, "Fraco", score=9.0)
    _mock_llm_ok(monkeypatch)
    _mock_store_ok(monkeypatch)
    veredito_baixo = dict(VEREDITO, score_final=6.0, verdict="concorrente_forte")

    def kit_nao_deve_ser_chamado(product, cb, vb):
        raise AssertionError("kit nao deveria ser gerado com score < 8")

    from app import kit as kit_mod
    from app import search as search_mod
    monkeypatch.setattr(search_mod, "verify_br_competition",
                        lambda product, players_block: (veredito_baixo, 200, 30))
    monkeypatch.setattr(kit_mod, "build_kit", kit_nao_deve_ser_chamado)

    assert excavate.excavate_all(conn) == 1
    d = db.dossiers_since(conn, "2000-01-01T00:00:00")[0]["dossier"]
    assert d["kit"] is None and d["score_final"] == 6.0


def test_excavate_sem_serper_segue_como_v11(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    pid = make_product(conn, "SemBusca", score=9.0)
    _mock_llm_ok(monkeypatch)
    _mock_store_ok(monkeypatch)
    _mock_v12(monkeypatch, verdict=None, kit_data=None)

    assert excavate.excavate_all(conn) == 1
    prod = conn.execute("SELECT br_verdict FROM products WHERE id=?", (pid,)).fetchone()
    assert prod["br_verdict"] is None          # palpite intocado (make_product nao seta)
    d = db.dossiers_since(conn, "2000-01-01T00:00:00")[0]["dossier"]
    assert d["br_verdict_final"] is None and d["kit"] is None


def test_investigate_ignora_fila_e_score(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    pid = make_product(conn, "ForaDoRadar", score=3.0)   # score < 7
    db.mark_dig(conn, pid, "done")                        # e ja escavado
    _mock_llm_ok(monkeypatch)
    _mock_store_ok(monkeypatch)
    _mock_v12(monkeypatch)

    assert excavate.investigate(conn, pid) is True
    d = db.dossiers_since(conn, "2000-01-01T00:00:00")
    assert len(d) == 1 and d[0]["dossier"]["product_id"] == pid


def test_investigate_falha_retorna_false(conn, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    pid = make_product(conn, "Azarado2", score=3.0)

    def explode(prompt, **kw):
        raise llm.LLMError("boom")

    monkeypatch.setattr(llm, "ask_json", explode)
    assert excavate.investigate(conn, pid) is False
    assert "falhou" in capsys.readouterr().out


def test_investigate_produto_inexistente(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    assert excavate.investigate(conn, 99999) is False


def test_veredito_sem_score_final_preserva_score(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    pid = make_product(conn, "SemScore", score=9.0)
    _mock_llm_ok(monkeypatch)
    _mock_store_ok(monkeypatch)
    veredito_sem_score = {k: v for k, v in VEREDITO.items() if k != "score_final"}

    from app import kit as kit_mod
    from app import search as search_mod
    monkeypatch.setattr(search_mod, "verify_br_competition",
                        lambda product, players_block: (veredito_sem_score, 200, 30))
    monkeypatch.setattr(kit_mod, "build_kit",
                        lambda product, cb, vb: (KIT_OK, 300, 40))

    assert excavate.excavate_all(conn) == 1
    prod = conn.execute("SELECT br_verdict, opportunity_score FROM products WHERE id=?",
                        (pid,)).fetchone()
    assert prod["br_verdict"] == "concorrente_fraco"
    assert prod["opportunity_score"] == 9.0          # COALESCE preservou
    d = db.dossiers_since(conn, "2000-01-01T00:00:00")[0]["dossier"]
    assert d["score_final"] is None and d["kit"] is None   # sem score_final => sem kit


def test_confirm_pula_hit_sem_app_id(monkeypatch):
    monkeypatch.setattr(excavate.time, "sleep", lambda s: None)

    def fake_search(term, lang, country, n_hits):
        return [{"appId": None, "title": "Tile editorial"},
                {"appId": "com.real.app", "title": "App Real", "score": 4.0}]

    monkeypatch.setattr(excavate, "gp_search", fake_search)
    monkeypatch.setattr(excavate, "gp_app",
                        lambda app_id, lang, country: {"score": 4.0, "ratings": 100})
    c = excavate.confirm_on_play_store("notion")
    assert c["app_id"] == "com.real.app"


def test_confirm_todos_hits_sem_app_id_retorna_none(monkeypatch):
    monkeypatch.setattr(excavate.time, "sleep", lambda s: None)
    monkeypatch.setattr(excavate, "gp_search",
                        lambda term, lang, country, n_hits: [{"appId": None, "title": "T"}])
    assert excavate.confirm_on_play_store("qualquer") is None
