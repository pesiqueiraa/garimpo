from app import excavate


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
