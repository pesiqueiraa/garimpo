"""Fontes de descoberta: parse com payloads realistas e falha graciosa. Sem rede."""
import json
from datetime import date

import pytest
import requests

from app import sources
from app.sources import appstore, hackernews, producthunt, trustmrr, yc
from tests.conftest import FakeResponse


class Unexpected(BaseException):
    """URL não prevista no teste (BaseException: o código de produção não engole)."""


def router(routes: dict):
    """requests.get/post falso: casa a primeira chave contida na URL (resposta, exceção ou função)."""
    calls = []

    def fake(url, params=None, headers=None, timeout=None, **kwargs):
        calls.append({"url": url, "params": params, "headers": headers, **kwargs})
        for key, resp in routes.items():
            if key in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp(url, params) if callable(resp) else resp
        raise Unexpected(url)

    fake.calls = calls
    return fake


@pytest.fixture(autouse=True)
def _sem_pausa_e_sem_cache(monkeypatch):
    monkeypatch.setattr(trustmrr.time, "sleep", lambda s: None)
    yc.fetch_all.cache_clear()
    yc._by_domain.cache_clear()
    yield
    yc.fetch_all.cache_clear()
    yc._by_domain.cache_clear()


# ---------- Hacker News ----------

@pytest.mark.parametrize("title,name,tagline", [
    ("Show HN: Foo-Bar – Invoicing for plumbers", "Foo-Bar", "Invoicing for plumbers"),
    ("Show HN: Acme - simple CRM for dentists", "Acme", "simple CRM for dentists"),
    ("Show HN: Zed: a fast editor", "Zed", "a fast editor"),
    ("Show HN: X-Ray — see inside your API calls", "X-Ray", "see inside your API calls"),
    ("Show HN: Self-hosted Notion alternative", "Self-hosted Notion alternative",
     "Self-hosted Notion alternative"),
    ("Show HN: Pay-as-you-go GPU – cheap - fast", "Pay-as-you-go GPU", "cheap - fast"),
])
def test_hn_nome_respeita_hifen(title, name, tagline):
    assert hackernews.parse_show(title) == (name, tagline)


@pytest.mark.parametrize("title,name,tagline,batch", [
    ("Launch HN: Acme (YC W25) – AI bookkeeping for dentists", "Acme",
     "AI bookkeeping for dentists", "W25"),
    ("Launch HN: Beta-Co (YC S24): Open-source payroll", "Beta-Co", "Open-source payroll", "S24"),
    ("Launch HN: Gamma (YC X25) builds payroll for Brazil", "Gamma", "builds payroll for Brazil", "X25"),
    ("Launch HN: Delta – no batch here", "Delta", "no batch here", None),
])
def test_launch_hn_extrai_lote(title, name, tagline, batch):
    assert hackernews.parse_launch(title) == (name, tagline, batch)


SHOW_HITS = {"hits": [
    {"objectID": "41000001", "title": "Show HN: Foo-Bar – Invoicing for plumbers",
     "url": "https://foo-bar.io", "points": 120, "num_comments": 40,
     "created_at": "2026-10-19T12:00:00Z"},
    {"objectID": "41000002", "title": "Show HN: Ask about my side project", "url": None,
     "points": 15},
    {"objectID": "41000003", "title": "", "points": 30},
]}
LAUNCH_HITS = {"hits": [
    {"objectID": "41000010", "title": "Launch HN: Acme (YC W25) – AI bookkeeping for dentists",
     "url": None, "points": 80,
     "story_text": ("Hi HN! Demo: <a href=\"https:&#x2F;&#x2F;www.loom.com&#x2F;share&#x2F;x\">loom</a>. "
                    "Try it at <a href=\"https:&#x2F;&#x2F;acme.ai&#x2F;\">https:&#x2F;&#x2F;acme.ai&#x2F;</a>.")},
    {"objectID": "41000011", "title": "Ask HN: What happened to Launch HN posts?", "points": 5},
]}


def _algolia(url, params):
    return FakeResponse(LAUNCH_HITS if params.get("query") == "Launch HN" else SHOW_HITS)


def test_hn_coleta_show_e_launch(monkeypatch, cfg):
    fake = router({"hn.algolia.com": _algolia})
    monkeypatch.setattr(requests, "get", fake)
    items = hackernews.collect(cfg)
    show = [i for i in items if i["source"] == "hackernews"]
    launch = [i for i in items if i["source"] == "launch_hn"]
    assert [i["name"] for i in show] == ["Foo-Bar", "Ask about my side project"]
    assert show[0]["external_id"] == "41000001"
    assert show[0]["source_url"] == "https://news.ycombinator.com/item?id=41000001"
    assert show[0]["kind"] == "launch" and show[0]["points"] == 120
    assert show[1]["url"] is None
    assert len(launch) == 1 and launch[0]["name"] == "Acme"
    assert launch[0]["url"] == "https://acme.ai/"  # post de texto: link do corpo, pulando o vídeo
    assert launch[0]["observations"] == [{"metric": "yc_batch", "value_num": None, "value_text": "W25"}]
    params = [c["params"] for c in fake.calls]
    assert params[0]["tags"] == "show_hn" and "points>=10" in params[0]["numericFilters"]
    assert params[1]["restrictSearchableAttributes"] == "title" and params[1]["tags"] == "story"


def test_hn_launch_desligado(monkeypatch, cfg):
    cfg["fontes"]["hackernews"]["launch_hn"] = False
    fake = router({"hn.algolia.com": _algolia})
    monkeypatch.setattr(requests, "get", fake)
    items = hackernews.collect(cfg)
    assert len(fake.calls) == 1 and {i["source"] for i in items} == {"hackernews"}


@pytest.mark.parametrize("resp", [requests.ConnectionError("timeout"), FakeResponse({}, status_code=500)])
def test_hn_falha_graciosa(monkeypatch, cfg, resp):
    monkeypatch.setattr(requests, "get", router({"hn.algolia.com": resp}))
    assert hackernews.collect(cfg) == []


# ---------- Product Hunt ----------

PH_OK = {"data": {"posts": {"edges": [
    {"node": {"id": "987654", "slug": "acme-invoices", "name": "Acme Invoices",
              "tagline": "Invoices that chase themselves",
              "url": "https://www.producthunt.com/posts/acme-invoices?utm_campaign=api",
              "website": "https://www.producthunt.com/r/ABCDEF123?utm_campaign=api",
              "votesCount": 312, "createdAt": "2026-10-19T07:01:00Z",
              "topics": {"edges": [{"node": {"name": "Fintech"}}, {"node": {"name": "SaaS"}}]}}},
    {"node": {"id": None, "slug": "beta", "name": "Beta", "tagline": None, "url": None,
              "website": None, "votesCount": None, "topics": None}},
]}}}


def test_ph_sem_token_nao_chama_rede(cfg, capsys):
    assert producthunt.collect(cfg) == []
    assert "PH_TOKEN ausente" in capsys.readouterr().out


def test_ph_coleta(monkeypatch, cfg):
    monkeypatch.setenv("PH_TOKEN", "  token123\n")
    fake = router({"api.producthunt.com": FakeResponse(PH_OK)})
    monkeypatch.setattr(requests, "post", fake)
    items = producthunt.collect(cfg)
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer token123"
    sent = fake.calls[0]["json"]
    assert "website" in sent["query"] and "slug" in sent["query"] and sent["variables"]["first"] == 30
    a, b = items
    assert a["external_id"] == "987654" and a["url"].startswith("https://www.producthunt.com/r/")
    assert a["source_url"].startswith("https://www.producthunt.com/posts/acme-invoices")
    assert a["points"] == 312 and a["category"] == "Fintech, SaaS" and a["kind"] == "launch"
    assert b["external_id"] == "beta" and b["points"] == 0 and b["category"] is None


def test_ph_erros_da_api_sao_logados(monkeypatch, cfg, capsys):
    monkeypatch.setenv("PH_TOKEN", "x")
    body = {"errors": [{"message": "Field 'website' doesn't exist on type 'Post'"}], "data": None}
    monkeypatch.setattr(requests, "post", router({"producthunt": FakeResponse(body)}))
    assert producthunt.collect(cfg) == []
    out = capsys.readouterr().out
    assert "[producthunt] erros da API:" in out and "website" in out


@pytest.mark.parametrize("resp,log", [
    (FakeResponse({"error": "invalid_token"}, status_code=401), "HTTP 401"),
    (requests.ConnectionError("reset"), "falhou"),
    (FakeResponse(None, text="<html>oops</html>"), "não é JSON"),
])
def test_ph_falha_graciosa(monkeypatch, cfg, capsys, resp, log):
    monkeypatch.setenv("PH_TOKEN", "x")
    monkeypatch.setattr(requests, "post", router({"producthunt": resp}))
    assert producthunt.collect(cfg) == []
    assert log in capsys.readouterr().out


# ---------- YC ----------

def _yc(name, team=30, status="Active", batch="Winter 2021", industries=("B2B",), website=None,
        hiring=True):
    slug = name.lower().replace(" ", "-")
    return {"id": hash(name) % 10000, "name": name, "slug": slug, "one_liner": f"{name} does X",
            "website": website or f"https://{slug}.com", "team_size": team, "status": status,
            "batch": batch, "industries": list(industries), "industry": industries[0] if industries else None,
            "isHiring": hiring, "url": f"https://www.ycombinator.com/companies/{slug}"}


META = {"batches": {"winter-2026": {"count": 2}, "summer-2025": {"count": 1},
                    "fall-2026": {"count": 0}, "spring-2025": {"count": 3}}}


def test_yc_lotes_recentes(monkeypatch, cfg):
    fake = router({
        "meta.json": FakeResponse(META),
        "batches/winter-2026.json": FakeResponse([_yc("Novo Um", team=4, batch="Winter 2026"),
                                                  _yc("Novo Dois", team=2, batch="Winter 2026", hiring=False)]),
        "batches/summer-2025.json": FakeResponse([_yc("Verao", team=6, batch="Summer 2025")]),
    })
    monkeypatch.setattr(requests, "get", fake)
    cfg["fontes"]["yc"]["proven"] = False
    items = yc.collect(cfg)
    assert [i["name"] for i in items] == ["Novo Um", "Novo Dois", "Verao"]
    first = items[0]
    assert first["kind"] == "listing" and first["source"] == "yc" and first["external_id"] == "novo-um"
    obs = {o["metric"]: o for o in first["observations"]}
    assert obs["team_size"]["value_num"] == 4 and obs["yc_status"]["value_text"] == "Active"
    assert obs["yc_batch"]["value_text"] == "Winter 2026" and obs["is_hiring"]["value_num"] == 1
    assert {o["metric"]: o for o in items[1]["observations"]}["is_hiring"]["value_num"] == 0


@pytest.mark.parametrize("batch,year", [("Winter 2021", 2021), ("Summer 2019", 2019), ("W21", 2021),
                                        ("S05", 2005), ("IK12", 2012), ("Unspecified", None),
                                        (None, None), ("", None)])
def test_yc_ano_do_lote(batch, year):
    assert yc.batch_year(batch) == year


def _proven_pool():
    return [
        _yc("Grande", team=500), _yc("Media", team=60, batch="S19"), _yc("Pequena", team=10),
        _yc("Morta", team=80, status="Inactive"), _yc("Comprada", team=90, status="Acquired"),
        _yc("Publica", team=3000, status="Public", industries=("Fintech",)),
        _yc("Nova Demais", team=40, batch="Winter 2026"), _yc("Sem Lote", team=40, batch="Unspecified"),
        _yc("Fora Do Setor", team=200, industries=("Government",)),
        _yc("Alfa", team=60, batch="Summer 2018"), _yc("Beta", team=45, batch="W20", industries=("Consumer",)),
    ]


def test_yc_provadas_filtra(cfg):
    cfg["fontes"]["yc"]["proven_per_run"] = 50
    chosen = yc.select_proven(_proven_pool(), cfg, today=date(2026, 10, 20))
    names = sorted(c["name"] for c in chosen)
    assert names == ["Alfa", "Beta", "Grande", "Media", "Publica"]


def test_yc_provadas_rodizio_deterministico(cfg):
    cfg["fontes"]["yc"]["proven_per_run"] = 2
    pool = _proven_pool()
    day = date(2026, 10, 20)
    first = [c["name"] for c in yc.select_proven(pool, cfg, today=day)]
    again = [c["name"] for c in yc.select_proven(list(reversed(pool)), cfg, today=day)]
    assert first == again and len(first) == 2
    # ordem estável: equipe desc, depois nome → Publica, Grande, Alfa, Media, Beta
    ordered = ["Publica", "Grande", "Alfa", "Media", "Beta"]
    start = (day.toordinal() * 2) % 5
    assert first == [ordered[(start + i) % 5] for i in range(2)]
    seen = set()
    for d in range(5):
        seen |= {c["name"] for c in yc.select_proven(pool, cfg, today=date(2026, 10, 20 + d))}
    assert seen == set(ordered)  # em poucos dias o rodízio cobre todo mundo


def test_yc_provadas_na_coleta(monkeypatch, cfg):
    cfg["fontes"]["yc"]["recent_batches"] = 0
    cfg["fontes"]["yc"]["proven_per_run"] = 3
    fake = router({"companies/all.json": FakeResponse(_proven_pool())})
    monkeypatch.setattr(requests, "get", fake)
    items = yc.collect(cfg)
    assert len(items) == 3 and {i["source"] for i in items} == {"yc_proven"}
    assert all(i["kind"] == "listing" for i in items)
    yc.collect(cfg)
    assert len(fake.calls) == 1  # all.json baixado uma vez por processo


def test_yc_lookup_por_dominio(monkeypatch, cfg, conn):
    from app import db
    from tests.conftest import make_company
    cid = make_company(conn, "Acme", url="https://app.acme.com/login")
    other = make_company(conn, "Outra", url="https://outra.io")
    pool = [_yc("Acme", team=42, website="https://www.acme.com/"), _yc("Zeta")]
    monkeypatch.setattr(requests, "get", router({"companies/all.json": FakeResponse(pool)}))
    obs = yc.lookup(db.get_company(conn, cid), cfg)
    assert {o["metric"]: o.get("value_num") or o.get("value_text") for o in obs} == {
        "team_size": 42, "yc_status": "Active", "yc_batch": "Winter 2021", "is_hiring": 1}
    assert {o["source"] for o in obs} == {"yc"}
    assert yc.lookup(db.get_company(conn, other), cfg) == []


def test_yc_falha_graciosa(monkeypatch, cfg):
    monkeypatch.setattr(requests, "get", router({"yc-oss": FakeResponse({}, status_code=500)}))
    assert yc.collect(cfg) == []
    monkeypatch.setattr(requests, "get", router({"yc-oss": requests.Timeout("lento")}))
    yc.fetch_all.cache_clear()
    assert yc.collect(cfg) == []


# ---------- TrustMRR ----------

ESCAPED_PAGE = r'''<!DOCTYPE html><html><head>
<meta property="og:title" content="Acme Invoices&#x27;s verified revenue | TrustMRR">
<meta name="description" content="Invoices that chase themselves">
<link rel="icon" href="https://trustmrr.com/favicon.ico"></head><body>
<script>self.__next_f.push([1,"5:[\"$\",\"div\",null,{\"startup\":{\"name\":\"Acme Invoices\",\"website\":\"https://acmeinvoices.com\",\"category\":\"Fintech\",\"mrr\":12345.5,\"last30DaysRevenue\":13020,\"growthMoM\":12.5,\"onSale\":true,\"askingPrice\":250000}}]"])</script>
</body></html>'''

PLAIN_PAGE = '''<html><head><title>Beta Analytics - TrustMRR</title>
<meta name="description" content="Analytics for small shops"></head><body>
<nav><a href="https://trustmrr.com/">Home</a><a href="https://x.com/trustmrr">X</a>
<a href="/marketplace">Startups for sale</a></nav>
<a href="https://cdn.beta.com/logo.png">logo</a>
<a href="https://beta-analytics.com/?ref=trustmrr" rel="nofollow">Visit website</a>
<div><span>$12.3k</span> <span>MRR</span></div><div>Last 30 days: $14,100</div>
<div>+8.2% MoM</div></body></html>'''

JSON_PAGE = '''<html><head><title>Gamma | TrustMRR</title></head><body><script id="__NEXT_DATA__"
type="application/json">{"props":{"startup":{"name":"Gamma","url":"https://gamma.app.br",
"mrrCents": 450000, "revenueLast30Days": 4600}}}</script></body></html>'''


def test_trustmrr_payload_escapado():
    data = trustmrr.parse_startup(ESCAPED_PAGE, "acme-invoices")
    assert data["name"] == "Acme Invoices"
    assert data["website"] == "https://acmeinvoices.com"
    assert data["mrr"] == 12345.5 and data["revenue_30d"] == 13020 and data["growth"] == 12.5
    assert data["for_sale"] is True and data["asking_price"] == 250000
    assert data["category"] == "Fintech"


def test_trustmrr_texto_visivel():
    data = trustmrr.parse_startup(PLAIN_PAGE, "beta-analytics")
    assert data["name"] == "Beta Analytics" and data["tagline"] == "Analytics for small shops"
    assert data["website"] == "https://beta-analytics.com/?ref=trustmrr"
    assert data["mrr"] == 12300 and data["revenue_30d"] == 14100 and data["growth"] == 8.2
    assert data["for_sale"] is False  # "for sale" no menu não conta


def test_trustmrr_json_simples_em_centavos():
    data = trustmrr.parse_startup(JSON_PAGE, "gamma")
    assert data["mrr"] == 4500 and data["revenue_30d"] == 4600
    assert data["website"] == "https://gamma.app.br" and data["name"] == "Gamma"


def test_trustmrr_sem_numeros():
    data = trustmrr.parse_startup("<html><title>Delta</title></html>", "delta")
    assert data["mrr"] is None and data["for_sale"] is False and data["website"] is None


def test_trustmrr_slugs_da_listagem():
    page = ('<a href="/startup/acme-invoices">A</a><a href="https://trustmrr.com/startup/beta-analytics">B</a>'
            r'{\"href\":\"/startup/acme-invoices\"} <a href="/startup/gamma?tab=revenue">G</a>')
    assert trustmrr.listing_slugs(page) == ["acme-invoices", "beta-analytics", "gamma"]


def test_trustmrr_coleta_filtra_mrr_minimo(monkeypatch, cfg):
    small = PLAIN_PAGE.replace("$12.3k", "$300")
    listing = '<a href="/startup/acme-invoices">A</a> <a href="/startup/small">S</a> <a href="/startup/broken">X</a>'
    pauses = []
    monkeypatch.setattr(trustmrr.time, "sleep", lambda s: pauses.append(s))
    fake = router({"/startup/acme-invoices": FakeResponse(text=ESCAPED_PAGE),
                   "/startup/small": FakeResponse(text=small),
                   "/startup/broken": FakeResponse(text="", status_code=500),
                   "trustmrr.com/": FakeResponse(text=listing)})
    monkeypatch.setattr(requests, "get", fake)
    items = trustmrr.collect(cfg)
    assert len(items) == 1
    item = items[0]
    assert item["source"] == "trustmrr" and item["kind"] == "listing"
    assert item["external_id"] == "acme-invoices" and item["url"] == "https://acmeinvoices.com"
    assert item["source_url"] == "https://trustmrr.com/startup/acme-invoices"
    obs = {o["metric"]: o for o in item["observations"]}
    assert obs["mrr_usd"]["value_num"] == 12345.5 and obs["revenue_30d_usd"]["value_num"] == 13020
    assert obs["growth_mom_pct"]["value_num"] == 12.5 and obs["for_sale"]["value_num"] == 1
    assert pauses and all(p == 1.5 for p in pauses) and len(pauses) == 3


def test_trustmrr_respeita_per_run(monkeypatch, cfg):
    cfg["fontes"]["trustmrr"]["per_run"] = 1
    listing = '<a href="/startup/acme-invoices">A</a> <a href="/startup/other">B</a>'
    fake = router({"/startup/acme-invoices": FakeResponse(text=ESCAPED_PAGE),
                   "trustmrr.com/": FakeResponse(text=listing)})
    monkeypatch.setattr(requests, "get", fake)
    assert len(trustmrr.collect(cfg)) == 1 and len(fake.calls) == 2


@pytest.mark.parametrize("resp", [FakeResponse(text="erro", status_code=500), requests.ConnectionError("x")])
def test_trustmrr_falha_graciosa(monkeypatch, cfg, resp):
    monkeypatch.setattr(requests, "get", router({"trustmrr.com": resp}))
    assert trustmrr.collect(cfg) == []


# ---------- App Store ----------

def _entry(app_id, name, summary="An app", category="Business"):
    return {"im:name": {"label": name}, "summary": {"label": summary},
            "im:artist": {"label": f"{name} Inc."},
            "category": {"attributes": {"im:id": "6000", "term": category, "label": category}},
            "id": {"label": f"https://apps.apple.com/us/app/x/id{app_id}?uo=2",
                   "attributes": {"im:id": str(app_id), "im:bundleId": f"com.x.{app_id}"}},
            "link": {"attributes": {"rel": "alternate", "type": "text/html",
                                    "href": f"https://apps.apple.com/us/app/x/id{app_id}?uo=2"}}}


US_FEED = {"feed": {"entry": [_entry(1, "Zoom Workplace"), _entry(2, "Invoice Maker Pro", "x" * 500),
                              _entry(3, "Indeed Flex"), _entry(4, "Square Point of Sale")]}}
BR_FEED = {"feed": {"entry": [_entry(1, "Zoom Workplace"), _entry(99, "Indeed  flex"),
                              _entry(50, "Nubank PJ")]}}


def _apple(url, params):
    return FakeResponse(BR_FEED if "/br/" in url else US_FEED)


def test_appstore_gap(monkeypatch, cfg):
    fake = router({"itunes.apple.com": _apple})
    monkeypatch.setattr(requests, "get", fake)
    items = appstore.collect(cfg)
    assert [i["name"] for i in items] == ["Invoice Maker Pro", "Square Point of Sale"]
    first = items[0]
    assert first["external_id"] == "us:2" and first["kind"] == "ranking" and first["source"] == "appstore"
    assert first["url"] == "https://apps.apple.com/us/app/x/id2?uo=2" and first["category"] == "Business"
    assert len(first["tagline"]) == 300
    assert first["observations"] == [{"metric": "appstore_rank_us", "value_num": 2, "value_text": None}]
    assert items[1]["observations"][0]["value_num"] == 4
    assert "/us/rss/topgrossingapplications/limit=100/genre=6000/json" in fake.calls[0]["url"]


def test_appstore_limite_de_20(monkeypatch, cfg):
    us = {"feed": {"entry": [_entry(i, f"App {i}") for i in range(1, 60)]}}
    monkeypatch.setattr(requests, "get", router({"/us/": FakeResponse(us), "/br/": FakeResponse(BR_FEED)}))
    assert len(appstore.collect(cfg)) == 20


def test_appstore_sem_ranking_br_nao_inventa_gap(monkeypatch, cfg):
    monkeypatch.setattr(requests, "get", router({"/us/": FakeResponse(US_FEED),
                                                 "/br/": FakeResponse({}, status_code=500)}))
    assert appstore.collect(cfg) == []
    monkeypatch.setattr(requests, "get", router({"itunes": requests.ConnectionError("x")}))
    assert appstore.collect(cfg) == []


# ---------- registro ----------

def test_collect_all_conta_por_fonte(monkeypatch, cfg):
    def hn(c):
        return [{"name": "A", "source": "hackernews"}, {"name": "B", "source": "launch_hn"}]

    def broken(c):
        raise RuntimeError("bug")

    monkeypatch.setitem(sources.REGISTRY, "hackernews", hn)
    monkeypatch.setitem(sources.REGISTRY, "producthunt", lambda c: [])
    monkeypatch.setitem(sources.REGISTRY, "yc", lambda c: [{"name": "C", "source": "yc_proven"},
                                                           {"name": "", "source": "yc"}])
    monkeypatch.setitem(sources.REGISTRY, "trustmrr", broken)
    monkeypatch.setitem(sources.REGISTRY, "appstore", lambda c: (_ for _ in ()).throw(AssertionError))
    cfg["fontes"]["appstore"]["enabled"] = False
    items, counts = sources.collect_all(cfg)
    assert [i["name"] for i in items] == ["A", "B", "C"]
    assert counts == {"hackernews": 1, "launch_hn": 1, "producthunt": 0, "yc": 0, "yc_proven": 1,
                      "trustmrr": 0}


def test_registro_na_ordem():
    assert list(sources.REGISTRY) == ["hackernews", "producthunt", "yc", "trustmrr", "appstore"]


def test_payload_de_item_e_serializavel(monkeypatch, cfg):
    monkeypatch.setattr(requests, "get", router({"hn.algolia.com": _algolia}))
    for item in hackernews.collect(cfg):
        json.dumps(item)
