"""Escavação v2 (app/dig): lojas, buscas, dores, mercado, texto e o dossiê de ponta a ponta.

Só as bordas são mockadas (google_play_scraper, requests, web.search, llm.ask_json).
"""
import pytest
import requests

from app import db, dig, llm
from app.dig import appstore, market, pains, playstore, tam, web, writer
from tests.conftest import FACTS, FakeResponse, make_company


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(playstore.time, "sleep", lambda s: None)


# ---------- Play Store ----------

def test_confirm_acha_na_loja_br(monkeypatch, no_sleep):
    def fake_search(term, lang, country, n_hits):
        assert (lang, country) == ("pt", "br")
        return [{"appId": "br.com.rival", "title": "Rival Brasil", "score": 3.4}]

    monkeypatch.setattr(playstore, "gp_search", fake_search)
    monkeypatch.setattr(playstore, "gp_app",
                        lambda app_id, lang, country: {"score": 3.4, "ratings": 5000})
    c = playstore.confirm_on_play_store("rival")
    assert c == {"app_id": "br.com.rival", "name": "Rival Brasil", "store": "play",
                 "country": "br", "rating": 3.4, "ratings_count": 5000,
                 "reviews_collected": 0}


def test_confirm_cai_para_loja_us(monkeypatch, no_sleep):
    buscas = []

    def fake_search(term, lang, country, n_hits):
        buscas.append(country)
        return [] if country == "br" else [{"appId": "com.global.app", "title": "GlobalApp"}]

    monkeypatch.setattr(playstore, "gp_search", fake_search)
    monkeypatch.setattr(playstore, "gp_app",
                        lambda app_id, lang, country: {"score": 4.1, "ratings": 90000})
    c = playstore.confirm_on_play_store("globalapp")
    assert buscas == ["br", "us"]
    assert c["country"] == "us" and c["store"] == "play"


def test_confirm_retorna_none_sem_resultado(monkeypatch, no_sleep):
    monkeypatch.setattr(playstore, "gp_search", lambda *a, **kw: [])
    assert playstore.confirm_on_play_store("inexistente") is None


def test_confirm_busca_quebrada_tenta_us(monkeypatch, no_sleep, capsys):
    def fake_search(term, lang, country, n_hits):
        if country == "br":
            raise RuntimeError("timeout")
        return [{"appId": "com.x", "title": "X"}]

    def app_quebrado(*a, **kw):
        raise ValueError("detalhes indisponíveis")

    monkeypatch.setattr(playstore, "gp_search", fake_search)
    monkeypatch.setattr(playstore, "gp_app", app_quebrado)
    c = playstore.confirm_on_play_store("x")
    assert c["app_id"] == "com.x" and c["ratings_count"] is None   # detalhes falharam, segue
    assert "[escava] play: busca 'x' (br) falhou" in capsys.readouterr().out


def test_confirm_pula_hit_sem_app_id(monkeypatch, no_sleep):
    monkeypatch.setattr(playstore, "gp_search", lambda term, lang, country, n_hits: [
        {"appId": None, "title": "Tile editorial"},
        {"appId": "com.real.app", "title": "App Real", "score": 4.0}])
    monkeypatch.setattr(playstore, "gp_app",
                        lambda app_id, lang, country: {"score": 4.0, "ratings": 100})
    assert playstore.confirm_on_play_store("notion")["app_id"] == "com.real.app"


def test_confirm_todos_hits_sem_app_id_retorna_none(monkeypatch, no_sleep):
    monkeypatch.setattr(playstore, "gp_search",
                        lambda term, lang, country, n_hits: [{"appId": None, "title": "T"}])
    assert playstore.confirm_on_play_store("qualquer") is None


def test_collect_reviews_junta_1_2_3_estrelas(monkeypatch, no_sleep):
    pedidos = []

    def fake_reviews(app_id, lang, country, count, filter_score_with, sort):
        pedidos.append((filter_score_with, count))
        return ([{"content": f"review {filter_score_with}"}, {"content": None}], None)

    monkeypatch.setattr(playstore, "gp_reviews", fake_reviews)
    revs = playstore.collect_negative_reviews("com.x", "br", per_star=40)
    assert pedidos == [(1, 40), (2, 40), (3, 40)]
    assert revs == ["review 1", "review 2", "review 3"]


def test_sample_reviews_trunca_e_limita():
    sampled = playstore.sample_reviews({"AppA": ["x" * 999] * 200, "AppB": ["curta"] * 200}, cap=10)
    assert len(sampled["AppA"]) == 5 and len(sampled["AppB"]) == 5
    assert len(sampled["AppA"][0]) == 300


# ---------- App Store ----------

RSS = {"feed": {"entry": [
    {"im:name": {"label": "Rival iOS"}},                          # entrada do próprio app (feed antigo)
    {"im:rating": {"label": "1"}, "content": {"label": "trava sempre"}},
    {"im:rating": {"label": "5"}, "content": {"label": "amei"}},
    {"im:rating": {"label": "3"}, "content": {"label": "caro demais"}},
    {"im:rating": {"label": "x"}, "content": {"label": "nota quebrada"}},
    {"im:rating": {"label": "2"}, "content": {"label": "   "}},
]}}


def test_parse_reviews_fica_com_1_a_3_estrelas():
    assert appstore.parse_reviews(RSS, cap=10) == ["trava sempre", "caro demais"]
    assert appstore.parse_reviews(RSS, cap=1) == ["trava sempre"]


def test_parse_reviews_tolera_formatos_estranhos():
    single = {"feed": {"entry": {"im:rating": {"label": "2"}, "content": {"label": "só uma"}}}}
    assert appstore.parse_reviews(single, cap=5) == ["só uma"]
    assert appstore.parse_reviews({"feed": {}}, cap=5) == []
    assert appstore.parse_reviews(None, cap=5) == []
    assert appstore.parse_reviews({"feed": {"entry": ["lixo", 3]}}, cap=5) == []


def test_confirm_app_store_br_vazio_cai_para_us(monkeypatch, no_sleep):
    chamadas = []

    def fake_get(url, params=None, timeout=None, headers=None):
        chamadas.append(params["country"])
        assert params["entity"] == "software" and params["limit"] == 3
        if params["country"] == "br":
            return FakeResponse({"resultCount": 0, "results": []})
        return FakeResponse({"results": [{"trackId": 123, "trackName": "Rival iOS",
                                          "averageUserRating": 2.94, "userRatingCount": 340}]})

    monkeypatch.setattr(requests, "get", fake_get)
    c = appstore.confirm_on_app_store("rival")
    assert chamadas == ["br", "us"]
    assert c == {"app_id": "123", "name": "Rival iOS", "store": "appstore", "country": "us",
                 "rating": 2.94, "ratings_count": 340, "reviews_collected": 0}


def test_app_store_fora_do_ar_retorna_none(monkeypatch, no_sleep, capsys):
    def explode(*a, **kw):
        raise requests.ConnectionError("sem rede")

    monkeypatch.setattr(requests, "get", explode)
    assert appstore.confirm_on_app_store("rival") is None
    assert appstore.collect_negative_reviews("123", "br") == []
    assert "[escava] appstore" in capsys.readouterr().out


def test_collect_app_store_usa_rss_do_pais(monkeypatch, no_sleep):
    urls = []

    def fake_get(url, params=None, timeout=None, headers=None):
        urls.append(url)
        return FakeResponse(RSS)

    monkeypatch.setattr(requests, "get", fake_get)
    assert appstore.collect_negative_reviews("123", "br", per_star=1) == ["trava sempre", "caro demais"]
    assert urls == ["https://itunes.apple.com/br/rss/customerreviews/page=1/id=123/sortby=mostrecent/json"]


# ---------- Busca (Serper) ----------

def test_search_monta_payload_e_header(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "chave-serper\n")
    capturado = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        capturado.update({"url": url, "headers": headers, "json": json})
        return FakeResponse({"organic": [{"title": f"T{i}", "link": f"https://x{i}.com",
                                          "snippet": "s"} for i in range(12)] + [{"title": "sem link"}]})

    monkeypatch.setattr(requests, "post", fake_post)
    results = web.search("app oficina Brasil", gl="mx", hl="es", n=8)
    assert capturado["url"] == "https://google.serper.dev/search"
    assert capturado["headers"]["X-API-KEY"] == "chave-serper"
    assert capturado["json"] == {"q": "app oficina Brasil", "gl": "mx", "hl": "es"}
    assert len(results) == 8 and results[0] == {"title": "T0", "url": "https://x0.com", "snippet": "s"}


def test_gather_sem_chave_retorna_vazio(cfg, capsys):
    assert web.gather({"name": "FooApp", "tagline": "x"}, FACTS, cfg) == {}
    assert "SERPER_API_KEY ausente" in capsys.readouterr().out


def test_gather_monta_as_buscas_e_pula_falhas(monkeypatch, cfg, capsys):
    monkeypatch.setenv("SERPER_API_KEY", "k")
    feitas = []

    def fake_search(query, gl="br", hl="pt", n=8):
        feitas.append((query, gl, hl))
        if gl == "co":
            raise requests.Timeout("lento")
        return [{"title": query, "url": f"https://{gl}.example/{len(feitas)}", "snippet": ""}]

    monkeypatch.setattr(web, "search", fake_search)
    out = web.gather({"name": "FooApp", "tagline": "x"}, FACTS, cfg)
    assert feitas[:2] == [("agenda clínica Brasil", "br", "pt"),
                          ("sistema para dentista Brasil", "br", "pt")]
    assert ("site:reclameaqui.com.br agenda clínica", "br", "pt") in feitas
    assert ('site:g2.com "FooApp" reviews', "us", "en") in feitas
    assert ("agenda clínica dental", "mx", "es") in feitas
    assert len(out["br"]) == 2 and out["reclame_aqui"] and out["g2"]
    assert set(out["latam"]) == {"mx", "ar"}                 # "co" falhou e foi pulado
    assert "serper falhou" in capsys.readouterr().out
    assert web.has_results(out)


def test_br_queries_sem_palavras_chave_usa_nicho_e_dor():
    facts = {"niche": "gestão de oficinas", "pain": "orçamento demorado"}
    assert web.br_queries({"name": "X", "tagline": "t"}, facts) == [
        "app gestão de oficinas Brasil", "orçamento demorado Brasil"]


# ---------- Dores ----------

def test_normalize_dores_coage_campos():
    data = {"clusters": [
        {"theme": "suporte lento", "frequency": "4", "severity": "Média",
         "examples": [{"app": "A", "excerpt": "x" * 500}, "lixo", {"app": "B", "excerpt": ""}]},
        {"theme": "cobrança dupla", "frequency": "12x", "severity": "crítica", "examples": None},
        {"theme": "", "frequency": 99},
        "não é dict",
    ]}
    out = pains.normalize(data)
    assert [c["theme"] for c in out] == ["cobrança dupla", "suporte lento"]   # por frequência
    assert out[0] == {"theme": "cobrança dupla", "frequency": 12, "severity": "media",
                      "examples": []}
    assert out[1]["severity"] == "media" and out[1]["frequency"] == 4
    assert len(out[1]["examples"]) == 1 and len(out[1]["examples"][0]["excerpt"]) == 200
    assert pains.normalize({"clusters": "x"}) == [] and pains.normalize(None) == []


def test_cluster_sem_dados_nao_chama_llm(monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **kw: pytest.fail("não deveria chamar"))
    assert pains.cluster("nicho", {}, []) is None


def test_cluster_inclui_reclamacoes_da_web(monkeypatch):
    prompts = []

    def fake_ask(prompt, **kw):
        prompts.append(prompt)
        return {"clusters": [{"theme": "t", "frequency": 1, "severity": "alta", "examples": []}]}, 10, 5

    monkeypatch.setattr(llm, "ask_json", fake_ask)
    clusters, t_in, t_out = pains.cluster("agenda", {"Rival": ["trava"]},
                                          [{"source": "reclame_aqui", "title": "Cobrança indevida",
                                            "snippet": "cobraram duas vezes"}])
    assert clusters[0]["severity"] == "alta" and (t_in, t_out) == (10, 5)
    assert "- trava" in prompts[0] and "cobraram duas vezes" in prompts[0]


# ---------- Mercado ----------

GATHERED = {
    "br": [{"query": "agenda clínica Brasil",
            "results": [{"title": "Rival Agenda", "url": "https://rivalagenda.com.br/",
                         "snippet": "agenda para clínicas"},
                        {"title": "Lista de apps", "url": "https://blog.exemplo.com.br/apps",
                         "snippet": "top 10"}]}],
    "reclame_aqui": [{"title": "Rival Agenda - Reclame Aqui",
                      "url": "https://www.reclameaqui.com.br/empresa/rival-agenda/",
                      "snippet": "cobrança indevida"}],
    "g2": [{"title": "FooApp Reviews", "url": "https://www.g2.com/products/fooapp/reviews",
            "snippet": "support is slow"}],
    "latam": {"mx": [{"title": "AgendaMX", "url": "https://agendamx.mx", "snippet": ""}], "ar": []},
}

MARKET = {
    "verdict": "concorrente_fraco", "rationale": "um player pequeno e mal avaliado",
    "evidence": [{"title": "Rival", "url": "https://rivalagenda.com.br", "why": "player local"},
                 {"title": "Inventado", "url": "https://inventado.com.br", "why": "?"}],
    "br_competitors": [{"name": "Rival Agenda", "url": "https://rivalagenda.com.br/", "strength": "fraco"},
                       {"name": "Fantasma", "url": "https://fantasma.com.br", "strength": "forte"}],
    "latam": [{"country": "mx", "name": "AgendaMX", "url": "https://agendamx.mx"},
              {"country": "co", "name": "Inventada", "url": "https://inventada.co"}],
    "web_complaints": [{"source": "reclame_aqui", "title": "x",
                        "url": "https://www.reclameaqui.com.br/empresa/rival-agenda/", "snippet": "y"},
                       {"source": "g2", "title": "x", "url": "https://www.g2.com/inventado", "snippet": "y"}],
}


def test_validate_descarta_links_fora_dos_resultados():
    out = market.validate(MARKET, GATHERED)
    assert out["verdict"] == "concorrente_fraco"
    assert [e["url"] for e in out["evidence"]] == ["https://rivalagenda.com.br/"]
    assert [c["name"] for c in out["br_competitors"]] == ["Rival Agenda"]
    assert out["br_competitors"][0]["strength"] == "fraco"
    assert out["latam"] == [{"country": "mx", "name": "AgendaMX", "url": "https://agendamx.mx"}]
    assert len(out["web_complaints"]) == 1
    assert out["web_complaints"][0]["source"] == "reclame_aqui"
    assert out["web_complaints"][0]["snippet"] == "cobrança indevida"   # trecho real, não o do LLM


def test_validate_veredito_invalido_retorna_none():
    assert market.validate(dict(MARKET, verdict="sei_la"), GATHERED) is None
    assert market.validate("lixo", GATHERED) is None


def test_validate_concorrente_sem_nenhum_link_real_retorna_none():
    data = dict(MARKET, verdict="concorrente_forte",
                evidence=[{"title": "x", "url": "https://inventado.com", "why": "?"}],
                br_competitors=[])
    assert market.validate(data, GATHERED) is None


def test_evaluate_sem_resultados_nao_chama_llm(conn, monkeypatch):
    monkeypatch.setattr(llm, "ask_json", lambda *a, **kw: pytest.fail("não deveria chamar"))
    company = {"id": 1, "name": "FooApp", "tagline": "x"}
    assert market.evaluate(conn, company, FACTS, {}, []) is None
    assert market.evaluate(conn, company, FACTS, {"br": [{"query": "q", "results": []}],
                                                  "latam": {"mx": []}}, []) is None


def test_evaluate_usa_schema_e_mapa_de_concorrentes_conhecidos(conn, monkeypatch):
    db.upsert_br_competitor(conn, "Velho Rival", "velhorival.com.br", "agenda para clínicas",
                            [{"title": "Velho Rival", "url": "https://velhorival.com.br"}])
    capturado = {}

    def fake_ask(prompt, max_tokens=500, reasoning_effort="minimal", schema=None, cache=True):
        capturado.update(prompt=prompt, schema=schema)
        return dict(MARKET, br_competitors=[{"name": "Velho Rival", "url": "https://velhorival.com.br",
                                             "strength": "forte"}]), 100, 20

    monkeypatch.setattr(llm, "ask_json", fake_ask)
    data, t_in, t_out = market.evaluate(conn, {"id": 1, "name": "FooApp", "tagline": "x"},
                                        FACTS, GATHERED, [])
    assert capturado["schema"] is market.SCHEMA
    assert "Velho Rival" in capturado["prompt"] and "https://agendamx.mx" in capturado["prompt"]
    assert data["br_competitors"][0]["name"] == "Velho Rival"   # link veio do mapa já validado
    assert (t_in, t_out) == (100, 20)


def test_evaluate_llm_falhou_retorna_none(conn, monkeypatch):
    def explode(prompt, **kw):
        raise llm.LLMError("boom")

    monkeypatch.setattr(llm, "ask_json", explode)
    assert market.evaluate(conn, {"id": 1, "name": "F", "tagline": ""}, FACTS, GATHERED, []) is None


# ---------- Ângulo e kit ----------

KIT_OK = {"landing": {"headline": "Clínica sem falta", "subheadline": "Lembrete e Pix no WhatsApp",
                      "benefits": ["Pix nativo", "Lembrete WhatsApp", "NFS-e automática"],
                      "cta": "Entrar na lista de espera"},
          "mom_test": [f"pergunta {i}" for i in range(5)],
          "whatsapp": "Oi pessoal, alguém aqui já passou por...",
          "where_to_find": ["CRO regional", "grupos de dentistas no Facebook",
                            "Google Maps: 'clínica odontológica'"]}


def test_valid_kit_exige_where_to_find():
    assert writer.valid_kit(KIT_OK)
    assert not writer.valid_kit({k: v for k, v in KIT_OK.items() if k != "where_to_find"})
    assert not writer.valid_kit(dict(KIT_OK, where_to_find=["um", "dois"]))
    assert not writer.valid_kit(dict(KIT_OK, where_to_find=["a", "b", "c", "d", "e", "f"]))
    assert not writer.valid_kit(dict(KIT_OK, where_to_find=["a", "", "c"]))
    assert not writer.valid_kit(dict(KIT_OK, landing=dict(KIT_OK["landing"], benefits=["só", "dois"])))
    assert not writer.valid_kit(dict(KIT_OK, mom_test=["p"] * 4))


def test_kit_valido_usa_reasoning_medium(monkeypatch):
    capturado = {}

    def fake_ask(prompt, **kw):
        capturado.update(kw, prompt=prompt)
        return KIT_OK, 500, 80

    monkeypatch.setattr(llm, "ask_json", fake_ask)
    data, t_in, t_out = writer.kit({"name": "FooApp", "tagline": "x"}, FACTS,
                                   [{"theme": "cobrança confusa", "frequency": 12, "severity": "alta"}],
                                   "concorrente_fraco: pequeno", "Ataque pelo Pix")
    assert data == KIT_OK and (t_in, t_out) == (500, 80)
    assert capturado["reasoning_effort"] == "medium"
    assert "cobrança confusa (12x, alta)" in capturado["prompt"] and "Ataque pelo Pix" in capturado["prompt"]


def test_kit_remove_dado_pessoal_de_where_to_find(monkeypatch):
    sujo = dict(KIT_OK, where_to_find=KIT_OK["where_to_find"] + ["Dra. Ana: ana@clinica.com.br",
                                                                 "(11) 98765-4321"])
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: (sujo, 10, 5))
    data, _, _ = writer.kit({"name": "FooApp"}, FACTS, [], "", None)
    assert data["where_to_find"] == KIT_OK["where_to_find"]


def test_kit_estrutura_invalida_retorna_none(monkeypatch, capsys):
    monkeypatch.setattr(llm, "ask_json",
                        lambda prompt, **kw: (dict(KIT_OK, where_to_find=[]), 10, 5))
    assert writer.kit({"name": "FooApp"}, FACTS, [], "", None) is None
    assert "[kit] estrutura inválida" in capsys.readouterr().out


def test_kit_e_angulo_repassam_teto_da_execucao(monkeypatch):
    def estoura(prompt, **kw):
        raise llm.BudgetExceeded("teto")

    monkeypatch.setattr(llm, "ask_json", estoura)
    with pytest.raises(llm.BudgetExceeded):
        writer.kit({"name": "F"}, FACTS, [], "", None)
    with pytest.raises(llm.BudgetExceeded):
        writer.attack_angle({"name": "F"}, FACTS, "", [], None, [], None)


def test_angulo_recebe_catalisadores_tam_latam_e_reclamacoes(monkeypatch):
    capturado = {}

    def fake_ask(prompt, **kw):
        capturado.update(kw, prompt=prompt)
        return {"attack_angle": "Ataque pelo Pix"}, 300, 60

    monkeypatch.setattr(llm, "ask_json", fake_ask)
    found = market.validate(MARKET, GATHERED)
    size = {"cnae": "8630504", "count": 12345, "top_ufs": [["SP", 5000]], "note": "empresas ativas"}
    text, _, _ = writer.attack_angle({"name": "FooApp", "tagline": "x"}, FACTS, "Rival (Play BR)",
                                     [], found, [{"id": "pix_automatico", "nome": "Pix Automático",
                                                  "nota": "recorrência"}], size)
    assert text == "Ataque pelo Pix" and capturado["reasoning_effort"] == "medium"
    p = capturado["prompt"]
    assert "Pix Automático" in p and "12.345" in p and "AgendaMX (MX)" in p
    assert "Rival Agenda - Reclame Aqui" in p


def test_angulo_falhou_retorna_none(monkeypatch, capsys):
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: ({"attack_angle": ""}, 1, 1))
    assert writer.attack_angle({"name": "F"}, FACTS, "", [], None, [], None) is None
    assert "ângulo de ataque vazio" in capsys.readouterr().out


# ---------- Dossiê de ponta a ponta ----------

SUGGESTION = {"competitors": [{"name": "Rival Agenda", "search_term": "rival agenda"},
                              {"name": "Rival de novo", "search_term": "agenda rival app"}]}
CLUSTERS = {"clusters": [{"theme": "cobrança confusa", "frequency": 12, "severity": "alta",
                          "examples": [{"app": "Rival Agenda", "excerpt": "cobraram 2x"}]},
                         {"theme": "app trava", "frequency": 5, "severity": "media", "examples": []}]}
ANGLE = {"attack_angle": "Pix nativo e lembrete no WhatsApp."}
RAW_REVIEW = "REVIEW-CRUA-NUNCA-PERSISTIDA " + "blá " * 80

ROUTES = (("sugerir aplicativos concorrentes", SUGGESTION), ("agrupar reclamações", CLUSTERS),
          ("avaliar a concorrência BRASILEIRA", MARKET), ("escrever o ângulo de ataque", ANGLE),
          ("montar o kit de validação", KIT_OK), ("extrai FATOS", FACTS))


def _route_llm(monkeypatch, tokens=(1000, 100), calls=None):
    def fake_ask(prompt, max_tokens=500, reasoning_effort="minimal", schema=None, cache=True):
        for marker, response in ROUTES:
            if marker in prompt:
                if calls is not None:
                    calls.append((marker, prompt))
                return response, *tokens
        raise AssertionError(f"prompt inesperado: {prompt[:80]}")

    monkeypatch.setattr(llm, "ask_json", fake_ask)


def _mock_edges(monkeypatch, cfg, review_counts=None):
    """Play Store, App Store (requests.get), Serper (web.search) e pausas."""
    monkeypatch.setenv("SERPER_API_KEY", "k")
    monkeypatch.setattr(playstore.time, "sleep", lambda s: None)
    monkeypatch.setattr(playstore, "gp_search", lambda term, lang, country, n_hits: [
        {"appId": "br.com.rival", "title": "Rival Agenda", "score": 3.2}])
    monkeypatch.setattr(playstore, "gp_app",
                        lambda app_id, lang, country: {"score": 3.2, "ratings": 1200})

    def fake_reviews(app_id, lang, country, count, filter_score_with, sort):
        if review_counts is not None:
            review_counts.append(count)
        return ([{"content": RAW_REVIEW}, {"content": f"play {filter_score_with}★"}], None)

    monkeypatch.setattr(playstore, "gp_reviews", fake_reviews)

    def fake_get(url, params=None, timeout=None, headers=None):
        if url == appstore.SEARCH_URL:
            return FakeResponse({"results": [{"trackId": 987, "trackName": "Rival iOS",
                                              "averageUserRating": 2.5, "userRatingCount": 340}]})
        if "customerreviews" in url:
            return FakeResponse(RSS)
        raise AssertionError(f"GET inesperado: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    def fake_search(query, gl="br", hl="pt", n=8):
        if query.startswith("site:reclameaqui"):
            return GATHERED["reclame_aqui"]
        if query.startswith("site:g2.com"):
            return GATHERED["g2"]
        if hl == "es":
            return GATHERED["latam"].get(gl, [])
        return GATHERED["br"][0]["results"]

    monkeypatch.setattr(web, "search", fake_search)


@pytest.fixture
def cnae_csv(tmp_path, monkeypatch):
    path = tmp_path / "cnae_counts.csv"
    path.write_text("cnae,uf,count\n8630504,SP,5000\n8630504,MG,1500\n8630504,RJ,2000\n"
                    "8630504,AC,10\n", encoding="utf-8")
    monkeypatch.setattr(tam, "DEFAULT_PATH", path)
    tam.clear_cache()
    yield path
    tam.clear_cache()


def test_dig_company_gera_dossie_completo(conn, cfg, monkeypatch, cnae_csv):
    cfg["fontes"]["kit"]["min_posterior"] = 5.0      # garante o kit por score neste cenário
    cfg["fontes"]["escavacao"]["reviews_per_star"] = 7
    company_id = make_company(conn, "FooApp", facts=FACTS, prior=7.2, status="queued")
    calls, counts = [], []
    _route_llm(monkeypatch, calls=calls)
    _mock_edges(monkeypatch, cfg, counts)

    dossier_id = dig.dig_company(conn, company_id, cfg)

    assert dossier_id is not None
    d = db.get_dossier(conn, dossier_id)
    dossier = d["dossier"]
    assert dossier["attack_angle"] == ANGLE["attack_angle"]
    assert dossier["br_verdict"] == "concorrente_fraco"
    assert [e["url"] for e in dossier["br_evidence"]] == ["https://rivalagenda.com.br/"]
    assert dossier["latam"] == [{"country": "mx", "name": "AgendaMX", "url": "https://agendamx.mx"}]
    assert dossier["web_complaints"][0]["source"] == "reclame_aqui"
    assert dossier["tam"]["count"] == 8510 and dossier["tam"]["top_ufs"][0] == ["SP", 5000]
    assert dossier["tam"]["segment"] == FACTS["br_customer_segment"]
    assert dossier["score_prior"] == 7.2
    posterior = db.latest_score(conn, company_id, "posterior")
    assert posterior is not None and dossier["score_posterior"] == posterior["total"]
    assert posterior["subscores"]["gap"] == 4.0        # gap do veredito real (concorrente_fraco)
    assert dossier["subscores"] == posterior["subscores"]
    assert dossier["catalysts"] == posterior["catalysts"] and dossier["catalysts"]
    assert dossier["kit"]["where_to_find"] == KIT_OK["where_to_find"]
    assert dossier["kit_reason"] == "score"
    assert dossier["tokens_in"] == 5000 and dossier["tokens_out"] == 500   # 5 chamadas
    assert dossier["cost_usd"] == pytest.approx(llm.cost_usd(5000, 500))

    stores = sorted((c["store"], c["country"], c["app_id"]) for c in d["app_competitors"])
    assert stores == [("appstore", "br", "987"), ("play", "br", "br.com.rival")]   # deduplicado
    play = next(c for c in d["app_competitors"] if c["store"] == "play")
    assert play["reviews_collected"] == 6 and counts == [7, 7, 7]
    assert [c["theme"] for c in d["pain_clusters"]] == ["cobrança confusa", "app trava"]

    assert [(b["name"], b["domain"], b["strength"]) for b in d["br_competitors"]] == [
        ("Rival Agenda", "rivalagenda.com.br", "fraco")]
    assert d["br_competitors"][0]["niche"] == FACTS["niche"]
    assert db.get_company(conn, company_id)["status"] == "dug"

    # o prompt de dores recebeu avaliações das duas lojas e a reclamação da web
    pains_prompt = next(p for m, p in calls if m == "agrupar reclamações")
    assert "play 1★" in pains_prompt and "trava sempre" in pains_prompt
    assert "cobrança indevida" in pains_prompt
    # avaliação crua nunca vai para o banco
    assert not any("REVIEW-CRUA" in line for line in conn.iterdump())


def test_dig_company_sem_serper_sai_com_veredito_desconhecido(conn, cfg, monkeypatch, cnae_csv):
    company_id = make_company(conn, "FooApp", facts=FACTS, prior=7.0, status="queued")
    _route_llm(monkeypatch)
    _mock_edges(monkeypatch, cfg)
    monkeypatch.delenv("SERPER_API_KEY")

    dossier_id = dig.dig_company(conn, company_id, cfg)
    d = db.get_dossier(conn, dossier_id)
    assert d["dossier"]["br_verdict"] == "desconhecido"
    assert d["dossier"]["br_evidence"] == [] and d["br_competitors"] == []
    assert db.latest_score(conn, company_id, "posterior")["subscores"]["gap"] == 3.0
    assert d["dossier"]["kit"] is None          # posterior abaixo de 8


def test_dig_company_estouro_do_teto_do_dossie(conn, cfg, monkeypatch, capsys):
    company_id = make_company(conn, "Caro", facts=FACTS, prior=7.0, status="queued")
    _route_llm(monkeypatch, tokens=(0, 1_000_000))       # US$2 > teto de US$0,25
    _mock_edges(monkeypatch, cfg)

    assert dig.dig_company(conn, company_id, cfg) is None
    assert db.dossiers_since(conn, "2000-01-01") == []
    company = db.get_company(conn, company_id)
    assert company["dig_attempts"] == 1 and company["status"] == "queued"
    assert "estourou o teto do dossiê" in capsys.readouterr().out


def test_dig_company_falha_registra_tentativa(conn, cfg, monkeypatch):
    company_id = make_company(conn, "Azarado", facts=FACTS, prior=7.0, status="queued")

    def explode(prompt, **kw):
        raise llm.LLMError("boom")

    monkeypatch.setattr(llm, "ask_json", explode)
    assert dig.dig_company(conn, company_id, cfg) is None
    assert db.get_company(conn, company_id)["dig_attempts"] == 1


def test_run_pula_sem_chave(conn, cfg, monkeypatch):
    make_company(conn, "Qualquer", facts=FACTS, prior=8.0, status="queued")
    monkeypatch.setattr(llm, "ask_json", lambda *a, **kw: pytest.fail("não deveria chamar"))
    assert dig.run(conn, cfg) == 0


def test_run_escava_a_fila(conn, cfg, monkeypatch, cnae_csv):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    company_id = make_company(conn, "FooApp", facts=FACTS, prior=7.0)
    db.set_queued(conn, company_id, 7.0)
    conn.commit()
    _route_llm(monkeypatch)
    _mock_edges(monkeypatch, cfg)
    assert dig.run(conn, cfg) == 1
    assert db.get_company(conn, company_id)["status"] == "dug"


def test_run_repassa_teto_da_execucao(conn, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    ids = [make_company(conn, n, facts=FACTS, prior=7.0) for n in ("Um", "Dois")]
    for i in ids:
        db.set_queued(conn, i, 7.0)
    conn.commit()
    chamadas = []

    def estoura(prompt, **kw):
        chamadas.append(1)
        raise llm.BudgetExceeded("teto da execução atingido")

    monkeypatch.setattr(llm, "ask_json", estoura)
    with pytest.raises(llm.BudgetExceeded):
        dig.run(conn, cfg)
    assert len(chamadas) == 1                                  # parou na primeira
    assert all(db.get_company(conn, i)["dig_attempts"] == 0 for i in ids)   # não é culpa da empresa


# ---------- Kit da semana ----------

def _stored_dossier(conn, name, posterior, kit=None):
    company_id = make_company(conn, name, facts=FACTS, prior=6.0)
    dossier_id = db.save_dossier(conn, company_id, {
        "attack_angle": f"ângulo de {name}", "br_verdict": "sem_concorrente",
        "br_rationale": "nada no Google BR", "score_posterior": posterior, "kit": kit,
        "cost_usd": 0.01, "tokens_in": 100, "tokens_out": 10})
    db.save_pain_clusters(conn, dossier_id, company_id,
                          [{"theme": f"dor de {name}", "frequency": 3, "severity": "alta"}])
    conn.commit()
    return dossier_id


def test_weekly_kit_gera_para_o_melhor_da_semana(conn, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    _stored_dossier(conn, "Medio", 5.5)
    best = _stored_dossier(conn, "Melhor", 6.5)
    _stored_dossier(conn, "Fraco", 3.0)
    prompts = []

    def fake_ask(prompt, **kw):
        prompts.append(prompt)
        return KIT_OK, 400, 50

    monkeypatch.setattr(llm, "ask_json", fake_ask)
    assert dig.weekly_kit(conn, cfg) == best
    d = db.get_dossier(conn, best)["dossier"]
    assert d["kit"] == KIT_OK and d["kit_reason"] == "weekly_best"
    assert d["tokens_in"] == 500 and d["cost_usd"] == pytest.approx(0.01 + llm.cost_usd(400, 50))
    assert "dor de Melhor" in prompts[0] and "ângulo de Melhor" in prompts[0]
    assert "sem_concorrente: nada no Google BR" in prompts[0]


def test_weekly_kit_nao_repete_na_mesma_semana(conn, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    _stored_dossier(conn, "Primeiro", 7.0)
    _stored_dossier(conn, "Segundo", 6.0)
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: (KIT_OK, 10, 5))
    assert dig.weekly_kit(conn, cfg) is not None
    assert dig.weekly_kit(conn, cfg) is None                    # reexecução do domingo


def test_weekly_kit_desligado_ou_sem_candidato(conn, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    monkeypatch.setattr(llm, "ask_json", lambda *a, **kw: pytest.fail("não deveria chamar"))
    _stored_dossier(conn, "Fraco", 3.0)
    assert dig.weekly_kit(conn, cfg) is None                    # abaixo de weekly_best_min
    cfg["fontes"]["kit"]["weekly_best"] = False
    _stored_dossier(conn, "Bom", 7.0)
    assert dig.weekly_kit(conn, cfg) is None


def test_weekly_kit_invalido_nao_grava(conn, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    dossier_id = _stored_dossier(conn, "Bom", 7.0)
    monkeypatch.setattr(llm, "ask_json", lambda prompt, **kw: ({"landing": {}}, 10, 5))
    assert dig.weekly_kit(conn, cfg) is None
    assert db.get_dossier(conn, dossier_id)["dossier"]["kit"] is None


# ---------- Investigação manual ----------

def test_investigate_cria_empresa_nova(conn, cfg, monkeypatch, cnae_csv):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    calls = []
    _route_llm(monkeypatch, calls=calls)
    _mock_edges(monkeypatch, cfg)

    dossier_id = dig.investigate(conn, "AgendaZap", cfg)

    assert dossier_id is not None
    d = db.get_dossier(conn, dossier_id)
    assert d["company"]["name"] == "AgendaZap" and d["company"]["status"] == "dug"
    assert d["company"]["site_key"] == "name:agendazap"
    assert db.company_sources(conn, d["company"]["id"]) == ["manual"]
    assert db.latest_extraction(conn, d["company"]["id"])["niche"] == FACTS["niche"]
    assert db.latest_score(conn, d["company"]["id"], "prior") is not None
    assert d["dossier"]["score_prior"] is not None
    assert calls[0][0] == "extrai FATOS"


def test_investigate_empresa_existente_ignora_fila(conn, cfg, monkeypatch, cnae_csv):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    company_id = make_company(conn, "ForaDoRadar", facts=FACTS, prior=3.0, status="dug")
    db.save_site_page(conn, company_id, {"text": "site já lido"})   # não sonda o site de novo
    _route_llm(monkeypatch)
    _mock_edges(monkeypatch, cfg)
    dossier_id = dig.investigate(conn, "foradoradar", cfg)
    assert db.get_dossier(conn, dossier_id)["company"]["id"] == company_id


def test_investigate_sem_chave(conn, cfg):
    assert dig.investigate(conn, "Qualquer", cfg) is None
