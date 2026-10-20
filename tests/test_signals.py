"""Sinais por empresa: sondagem do site, Wayback, vagas, Tranco, Form D e orquestração. Sem rede."""
import io
import zipfile

import pytest
import requests

from app import db, signals
from app.signals import formd, jobs, site, tranco, wayback
from app.sources import yc
from tests.conftest import FakeResponse, make_company


class Unexpected(BaseException):
    """URL não prevista no teste (BaseException: o código de produção não engole)."""


def router(routes: dict):
    calls = []

    def fake(url, params=None, headers=None, timeout=None, **kwargs):
        calls.append({"url": url, "params": params, "headers": headers})
        for key, resp in routes.items():
            if key in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp(url, params) if callable(resp) else resp
        raise Unexpected(url)

    fake.calls = calls
    return fake


@pytest.fixture(autouse=True)
def _limpo(monkeypatch):
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    monkeypatch.setattr(signals.time, "sleep", lambda s: None)
    monkeypatch.setattr(tranco, "_cache", {})
    yc.fetch_all.cache_clear()
    yc._by_domain.cache_clear()
    yield
    yc.fetch_all.cache_clear()
    yc._by_domain.cache_clear()


def html(url, text, status=200):
    return FakeResponse(text=text, url=url, status_code=status,
                        headers={"Content-Type": "text/html; charset=utf-8"})


# ---------- site ----------

HOME = """<!doctype html><html lang="en-US"><head><title> Acme — Invoices that chase
themselves </title><style>.x{color:red}</style><script>var price="$1";</script></head>
<body><!-- $3 comentário --><nav><a href="/">Home</a><a href="https://stripe.com/pricing">Stripe</a>
<a href='/pricing?ref=nav#plans'>Pricing</a><a href="https://app.acme.com/plans/compare-all">All</a></nav>
<h1>Get paid faster</h1><p>Works with Stripe, QuickBooks and Google Calendar. Raised $5M.</p>
<a href="/signup">Get started</a> <a href="/demo">Book a demo</a></body></html>"""

PRICING = """<html><body><h2>Plans</h2><div>Free plan — $0 forever, 1 user</div>
<div>Pro $29/mo</div><div>Business $99.50/mo</div><div>Enterprise: contact sales</div>
<p>Brazil: R$ 15/mês</p></body></html>"""


def test_probe_le_home_e_precos(monkeypatch, cfg):
    fake = router({"acme.com/pricing": html("https://acme.com/pricing?ref=nav", PRICING),
                   "acme.com": html("https://acme.com/", HOME)})
    monkeypatch.setattr(requests, "get", fake)
    page = site.probe("https://acme.com", cfg)
    assert page["final_url"] == "https://acme.com/"
    assert page["title"] == "Acme — Invoices that chase themselves"
    assert "Get paid faster" in page["text"] and "color:red" not in page["text"]
    assert "comentário" not in page["text"] and "var price" not in page["text"]
    assert page["pricing_url"] == "https://acme.com/pricing?ref=nav"
    assert "Pro $29/mo" in page["pricing_text"]
    assert len(page["pricing_hash"]) == 16
    f = page["features"]
    assert f["price_min_usd"] == 29 and f["has_free_plan"] is True
    assert f["sales_motion"] == "misto"
    assert f["integrations"] == ["stripe", "quickbooks", "google calendar"]
    assert f["lang"] == "en-US"
    assert fake.calls[0]["headers"]["User-Agent"] == cfg["fontes"]["sinais"]["user_agent"]
    assert [c["url"] for c in fake.calls] == ["https://acme.com", "https://acme.com/pricing?ref=nav"]


def test_probe_sales_led_sem_link_de_precos(monkeypatch, cfg):
    home = ("<html><body><h1>Enterprise risk platform</h1><a href='/about'>About</a>"
            "<a href='/contact'>Request a demo</a> Talk to sales. Starting at $1,200 per seat.</body></html>")
    fake = router({"risk.io/pricing": html("https://risk.io/pricing", "nope", status=404),
                   "risk.io": html("https://risk.io/", home)})
    monkeypatch.setattr(requests, "get", fake)
    page = site.probe("https://risk.io", cfg)
    assert fake.calls[-1]["url"] == "https://risk.io/pricing"  # tentou /pricing uma vez
    assert page["pricing_url"] is None and page["pricing_text"] == "" and page["pricing_hash"] is None
    f = page["features"]
    assert f["sales_motion"] == "sales_led" and f["has_free_plan"] is False
    assert f["price_min_usd"] == 1200  # sem página de preços, usa o texto da home
    assert f["lang"] is None


def test_probe_self_serve(monkeypatch, cfg):
    home = "<html lang='pt-BR'><body>Start your free trial. <a href='/planos'>Planos</a></body></html>"
    pricing = "<html><body>Plano grátis para sempre. Pro US$ 12/mês</body></html>"
    monkeypatch.setattr(requests, "get", router({"/planos": html("https://loja.com.br/planos", pricing),
                                                 "loja.com.br": html("https://loja.com.br/", home)}))
    page = site.probe("https://loja.com.br", cfg)
    f = page["features"]
    assert page["pricing_url"].endswith("/planos")
    assert f["sales_motion"] == "self_serve" and f["has_free_plan"] is True
    assert f["price_min_usd"] == 12 and f["lang"] == "pt-BR"


def test_probe_home_ja_e_a_pagina_de_precos(monkeypatch, cfg):
    fake = router({"acme.com": html("https://acme.com/pricing", PRICING)})
    monkeypatch.setattr(requests, "get", fake)
    page = site.probe("https://acme.com/pricing", cfg)
    assert len(fake.calls) == 1 and page["pricing_url"] == "https://acme.com/pricing"


def test_probe_limita_tamanho(monkeypatch, cfg):
    big = "<html><body>" + ("palavra " * 400_000) + "</body></html>"
    monkeypatch.setattr(requests, "get", router({"/pricing": html("https://big.com/pricing", "", 404),
                                                 "big.com": html("https://big.com/", big)}))
    page = site.probe("https://big.com", cfg)
    assert len(page["text"]) <= 4000


@pytest.mark.parametrize("resp", [requests.ConnectionError("dns"), html("https://dead.com/", "erro", 500)])
def test_probe_falha_graciosa(monkeypatch, cfg, resp):
    monkeypatch.setattr(requests, "get", router({"dead.com": resp}))
    assert site.probe("https://dead.com", cfg) is None


def test_probe_redirect_para_site_que_barra_robos(monkeypatch, cfg):
    """PH redireciona para o site real, que responde 403: o destino ainda serve para resolver a empresa."""
    monkeypatch.setattr(requests, "get", router({"producthunt.com": html("https://real.io/", "", 403)}))
    page = site.probe("https://www.producthunt.com/r/XYZ", cfg)
    assert page["final_url"] == "https://real.io/" and page["text"] == ""
    assert site.observations(page) == []


def test_pricing_link_so_do_proprio_site():
    raw = ('<a href="https://stripe.com/pricing">s</a><a href="https://app.acme.com/plans">p</a>'
           '<a href="mailto:pricing@acme.com">m</a>')
    assert site.find_pricing_link(raw, "https://acme.com/") == "https://app.acme.com/plans"
    assert site.find_pricing_link('<a href="https://stripe.com/pricing">s</a>', "https://acme.com/") is None


def test_pricing_hash_ignora_espacos_e_caixa():
    assert site.pricing_hash("Pro  $29\n/mo") == site.pricing_hash("pro $29 /MO")
    assert site.pricing_hash("Pro $29/mo") != site.pricing_hash("Pro $39/mo")
    assert site.pricing_hash("") is None


def test_preco_minimo_ignora_captacao_e_real():
    assert site.min_price("Raised $5M and $2 million; R$ 10; A$ 5; $0; $9.99/mo; $1,500/yr") == 9.99
    assert site.min_price("nada aqui") is None


def test_observacoes_do_site():
    page = {"text": "x", "pricing_text": "y", "pricing_hash": "abcd1234abcd1234",
            "features": {"price_min_usd": 29.0, "has_free_plan": True, "sales_motion": "misto"}}
    obs = {o["metric"]: o for o in site.observations(page)}
    assert obs["price_min_usd"]["value_num"] == 29 and obs["has_free_plan"]["value_num"] == 1
    assert obs["sales_motion"]["value_text"] == "misto"
    assert obs["pricing_hash"]["value_text"] == "abcd1234abcd1234"
    assert {o["source"] for o in obs.values()} == {"site"}
    unknown = {**page, "features": {"has_free_plan": False, "sales_motion": "desconhecido"}}
    assert [o["metric"] for o in site.observations(unknown)] == ["has_free_plan", "pricing_hash"]
    assert site.observations(None) == [] and site.observations({"text": ""}) == []


# ---------- wayback ----------

def test_wayback(monkeypatch, cfg, conn):
    cid = make_company(conn, "Acme", url="https://acme.com")
    fake = router({"web.archive.org": FakeResponse([["timestamp"], ["20190314123456"]])})
    monkeypatch.setattr(requests, "get", fake)
    obs = wayback.lookup(db.get_company(conn, cid), cfg)
    assert obs == [{"metric": "domain_first_seen", "value_num": None, "value_text": "2019-03-14",
                    "source": "wayback"}]
    assert fake.calls[0]["params"]["url"] == "acme.com"
    assert fake.calls[0]["params"]["filter"] == "statuscode:200"


def test_wayback_sem_dominio_ou_falha(monkeypatch, cfg, conn):
    gh = make_company(conn, "Proj", url="https://github.com/fulano/proj")
    acme = make_company(conn, "Acme", url="https://acme.com")
    assert wayback.lookup(db.get_company(conn, gh), cfg) == []  # sem rede: sem domínio próprio
    monkeypatch.setattr(requests, "get", router({"archive": FakeResponse([])}))
    assert wayback.lookup(db.get_company(conn, acme), cfg) == []
    monkeypatch.setattr(requests, "get", router({"archive": FakeResponse(None, status_code=503)}))
    assert wayback.lookup(db.get_company(conn, acme), cfg) == []
    monkeypatch.setattr(requests, "get", router({"archive": requests.Timeout("lento")}))
    assert wayback.lookup(db.get_company(conn, acme), cfg) == []


# ---------- vagas ----------

def test_vagas_slugs(conn):
    cid = make_company(conn, "Acme Pay", url="https://get-acme.io")
    assert jobs.slug_candidates(db.get_company(conn, cid)) == ["get-acme", "getacme", "acmepay"]


def test_vagas_cai_para_o_proximo_provedor(monkeypatch, cfg, conn):
    cid = make_company(conn, "Acme", url="https://acme.com")
    fake = router({
        "greenhouse.io/v1/boards/acme": FakeResponse({"status": 404}, status_code=404),
        "api.lever.co/v0/postings/acme": FakeResponse([]),
        "api.ashbyhq.com/posting-api/job-board/acme": FakeResponse(
            {"apiVersion": "1", "jobs": [{"title": "Engineer"}, {"title": "AE"}, {"title": "SDR"}]}),
    })
    monkeypatch.setattr(requests, "get", fake)
    obs = jobs.lookup(db.get_company(conn, cid), cfg)
    assert obs == [{"metric": "open_jobs", "value_num": 3, "value_text": "acme", "source": "ashby"}]
    assert [c["url"].split("/")[2] for c in fake.calls] == [
        "boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com"]


def test_vagas_greenhouse_confere_o_nome_do_quadro(monkeypatch, cfg, conn):
    cid = make_company(conn, "Acme", url="https://acme.com")
    gh_jobs = FakeResponse({"jobs": [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]})
    lever = FakeResponse([{"id": "a"}, {"id": "b"}])
    company = db.get_company(conn, cid)

    monkeypatch.setattr(requests, "get", router({
        "boards/acme/jobs": gh_jobs, "boards/acme": FakeResponse({"name": "Zenith Labs"}),
        "lever.co": lever}))
    assert jobs.lookup(company, cfg)[0]["source"] == "lever"  # homônimo no Greenhouse ignorado

    monkeypatch.setattr(requests, "get", router({
        "boards/acme/jobs": gh_jobs, "boards/acme": FakeResponse({"name": "ACME, Inc."})}))
    assert jobs.lookup(company, cfg) == [{"metric": "open_jobs", "value_num": 4, "value_text": "acme",
                                          "source": "greenhouse"}]


def test_vagas_nada_encontrado_ou_rede_fora(monkeypatch, cfg, conn):
    company = db.get_company(conn, make_company(conn, "Acme", url="https://acme.com"))
    monkeypatch.setattr(requests, "get", router({"": FakeResponse({}, status_code=404)}))
    assert jobs.lookup(company, cfg) == []
    monkeypatch.setattr(requests, "get", router({"": FakeResponse({}, status_code=500)}))
    assert jobs.lookup(company, cfg) == []
    monkeypatch.setattr(requests, "get", router({"": requests.ConnectionError("x")}))
    assert jobs.lookup(company, cfg) == []


# ---------- Tranco ----------

def _zip(rows: list[tuple[int, str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("top-1m.csv", "".join(f"{r},{d}\r\n" for r, d in rows))
    return buf.getvalue()


TRANCO_ZIP = _zip([(1, "google.com"), (2, "facebook.com"), (1234, "acme.com"),
                   (98765, "loja.com.br"), (999999, "tiny.io")])


def test_tranco_ranks_for(monkeypatch, cfg):
    fake = router({"tranco-list.eu": FakeResponse(content=TRANCO_ZIP)})
    monkeypatch.setattr(requests, "get", fake)
    found = tranco.ranks_for({"acme.com", "loja.com.br", "naoexiste.com", ""}, cfg)
    assert found == {"acme.com": 1234, "loja.com.br": 98765}
    assert tranco.ranks_for({"tiny.io"}, cfg) == {"tiny.io": 999999}
    assert len(fake.calls) == 1  # baixado uma vez por processo
    assert tranco.ranks_for(set(), cfg) == {}


def test_tranco_lookup(monkeypatch, cfg, conn):
    monkeypatch.setattr(requests, "get", router({"tranco": FakeResponse(content=TRANCO_ZIP)}))
    acme = db.get_company(conn, make_company(conn, "Acme", url="https://www.acme.com"))
    other = db.get_company(conn, make_company(conn, "Outra", url="https://outra.com"))
    assert tranco.lookup(acme, cfg) == [{"metric": "tranco_rank", "value_num": 1234, "value_text": None,
                                         "source": "tranco"}]
    assert tranco.lookup(other, cfg) == []


@pytest.mark.parametrize("resp", [requests.Timeout("lento"), FakeResponse(status_code=500, text="x"),
                                  FakeResponse(content=b"isto nao e zip")])
def test_tranco_falha_graciosa(monkeypatch, cfg, resp):
    fake = router({"tranco": resp})
    monkeypatch.setattr(requests, "get", fake)
    assert tranco.ranks_for({"acme.com"}, cfg) == {}
    assert tranco.ranks_for({"acme.com"}, cfg) == {}
    assert len(fake.calls) == 1  # não insiste no mesmo processo


# ---------- Form D ----------

FORMD = {"took": 12, "hits": {"total": {"value": 3}, "hits": [
    {"_id": "0001-1", "_source": {"display_names": ["Acme Invoices, Inc.  (CIK 0001234567)"],
                                  "file_date": "2023-05-01", "form": "D"}},
    {"_id": "0001-2", "_source": {"display_names": ["ACME INVOICES LLC  (CIK 0001234568)"],
                                  "file_date": "2024-02-10", "form": "D/A"}},
    {"_id": "0002-1", "_source": {"display_names": ["Acme Capital Fund I, LP  (CIK 0009999999)"],
                                  "file_date": "2025-01-01", "form": "D"}},
]}}


def test_formd_conta_so_o_nome_certo(monkeypatch, cfg, conn):
    company = db.get_company(conn, make_company(conn, "Acme Invoices", url="https://acmeinvoices.com"))
    fake = router({"efts.sec.gov/LATEST/search-index": FakeResponse(FORMD)})
    monkeypatch.setattr(requests, "get", fake)
    obs = formd.lookup(company, cfg)
    assert {o["metric"]: o["value_num"] or o["value_text"] for o in obs} == {
        "formd_filings": 2, "formd_last": "2024-02-10"}
    assert {o["source"] for o in obs} == {"sec_formd"}
    assert fake.calls[0]["params"] == {"q": '"Acme Invoices"', "forms": "D"}
    assert fake.calls[0]["headers"]["User-Agent"] == cfg["fontes"]["sinais"]["sec_user_agent"]


def test_formd_fundo_homonimo_nao_conta():
    data = {"hits": {"hits": [
        {"_source": {"display_names": ["Acme Capital Fund I, LP  (CIK 1)"], "file_date": "2025-01-01"}},
        {"_source": {"display_names": ["Acme SPV 3 LLC  (CIK 2)"], "file_date": "2025-02-01"}},
        {"_source": {"display_names": ["Pacmeco Holdings  (CIK 3)"], "file_date": "2025-03-01"}},
        {"_source": {"display_names": "ACME, INC.  (CIK 4)", "file_date": "2021-07-08T00:00:00"}},
    ]}}
    assert formd.parse(data, "acme") == (1, "2021-07-08")
    assert formd.parse({}, "acme") == (0, None) and formd.parse({"hits": None}, "acme") == (0, None)


def test_formd_pula_nome_curto_ou_generico(cfg, conn):
    # sem mock de rede: se chamasse, o teste estouraria com NetworkBlocked
    for name in ("Abc", "Pay", "Cloud", "Health"):
        company = db.get_company(conn, make_company(conn, name, url=f"https://{name.lower()}x.com"))
        assert formd.lookup(company, cfg) == []


def test_formd_sem_resultado_ou_falha(monkeypatch, cfg, conn):
    company = db.get_company(conn, make_company(conn, "Bolt Ledger", url="https://boltledger.com"))
    monkeypatch.setattr(requests, "get", router({"efts": FakeResponse({"hits": {"hits": []}})}))
    assert formd.lookup(company, cfg) == []
    monkeypatch.setattr(requests, "get", router({"efts": FakeResponse({}, status_code=403)}))
    assert formd.lookup(company, cfg) == []


# ---------- orquestração: run ----------

YC_ALL = [{"name": "Acme", "slug": "acme", "website": "https://acme.com", "team_size": 42,
           "status": "Active", "batch": "Winter 2021", "isHiring": True, "industries": ["B2B"]}]
ACME_FORMD = {"hits": {"hits": [{"_source": {"display_names": ["ACME INC  (CIK 1)"],
                                             "file_date": "2022-01-05"}}]}}


def _ats(url, params):
    if "boards/acme/jobs" in url:
        return FakeResponse({"jobs": [{"id": i} for i in range(5)]})
    if "boards/acme" in url:
        return FakeResponse({"name": "Acme"})
    return FakeResponse({}, status_code=404)


def _sec(url, params):
    return FakeResponse(ACME_FORMD if params["q"] == '"Acme"' else {"hits": {"hits": []}})


def _traction_routes():
    return router({"yc-oss.github.io": FakeResponse(YC_ALL), "greenhouse.io": _ats, "lever.co": _ats,
                   "ashbyhq.com": _ats, "tranco-list.eu": FakeResponse(content=TRANCO_ZIP),
                   "efts.sec.gov": _sec})


def test_run_grava_observacoes(monkeypatch, cfg, conn):
    acme = make_company(conn, "Acme", url="https://acme.com", prior=6.0)
    bolt = make_company(conn, "Bolt Ledger", url="https://boltledger.com", prior=5.0)
    low = make_company(conn, "Baixa", url="https://baixa.com", prior=3.0)
    killed = make_company(conn, "Morta", url="https://morta.com", prior=6.0)
    db.save_score(conn, killed, "prior", {"total": 0, "subscores": {}, "kill_reason": "hardware",
                                          "evidence": [], "catalysts": []}, "teste")
    conn.commit()
    fake = _traction_routes()
    monkeypatch.setattr(requests, "get", fake)

    assert signals.run(conn, cfg) == 1
    latest = db.latest_observations(conn, acme)
    assert latest["team_size"]["value_num"] == 42 and latest["team_size"]["source"] == "yc"
    assert latest["open_jobs"]["value_num"] == 5 and latest["open_jobs"]["source"] == "greenhouse"
    assert latest["tranco_rank"]["value_num"] == 1234
    assert latest["formd_filings"]["value_num"] == 1 and latest["formd_last"]["value_text"] == "2022-01-05"
    assert db.latest_observations(conn, bolt) == {}
    consulted = " ".join(c["url"] + str(c["params"]) for c in fake.calls)
    assert "baixa" not in consulted and "morta" not in consulted.lower()
    assert sum("tranco-list.eu" in c["url"] for c in fake.calls) == 1  # um download para o lote

    # no dia seguinte (mesma semana) a Acme não é consultada de novo; a Bolt, sem sinal, sim
    fake.calls.clear()
    assert signals.run(conn, cfg) == 0
    assert not any("/acme" in c["url"] for c in fake.calls)
    assert any("boltledger" in c["url"] for c in fake.calls)


def test_run_com_ids_explicitos_e_teto(monkeypatch, cfg, conn):
    ids = [make_company(conn, n, url=f"https://{n.lower()}.com", prior=7.0) for n in ("Acme", "Zeta", "Kappa")]
    monkeypatch.setattr(requests, "get", _traction_routes())
    cfg["fontes"]["sinais"]["max_companies_per_run"] = 2
    assert len(signals.candidates(conn, cfg)) == 2
    assert signals.run(conn, cfg, company_ids=[ids[0]]) == 1
    assert signals.run(conn, cfg, company_ids=[]) == 0


def test_run_rede_fora_nao_quebra(monkeypatch, cfg, conn):
    make_company(conn, "Acme", url="https://acme.com", prior=6.0)
    monkeypatch.setattr(requests, "get", router({"": requests.ConnectionError("sem rede")}))
    assert signals.run(conn, cfg) == 0


# ---------- orquestração: probe_new ----------

ACME_HOME = ("<html lang='en'><head><title>Acme</title></head><body>Invoices. <a href='/pricing'>Pricing</a>"
             " Sign up free.</body></html>")


def test_probe_new_redirect_funde_com_empresa_existente(monkeypatch, cfg, conn):
    existing = make_company(conn, "Acme", url="https://acme.com", source="hackernews")
    db.save_site_page(conn, existing, {"final_url": "https://acme.com/", "text": "antiga"})
    ph = make_company(conn, "Acme Invoices", url="https://www.producthunt.com/r/ABC123?utm_campaign=x",
                      source="producthunt")
    conn.commit()
    fake = router({"acme.com/pricing": html("https://acme.com/pricing", PRICING),
                   "producthunt.com/r/ABC123": html("https://acme.com/?ref=producthunt", ACME_HOME),
                   "web.archive.org": FakeResponse([["timestamp"], ["20180102030405"]])})
    monkeypatch.setattr(requests, "get", fake)

    assert signals.probe_new(conn, cfg) == 1
    assert db.get_company(conn, ph) is None
    assert set(db.company_sources(conn, existing)) == {"hackernews", "producthunt"}
    latest = db.latest_observations(conn, existing)
    assert latest["price_min_usd"]["value_num"] == 29 and latest["price_min_usd"]["source"] == "site"
    assert latest["sales_motion"]["value_text"] == "misto"  # sign up + contact sales
    assert latest["domain_first_seen"]["value_text"] == "2018-01-02"
    assert db.companies_without_site_page(conn, 10) == []


def test_probe_new_redirect_troca_o_dominio(monkeypatch, cfg, conn):
    cid = make_company(conn, "Acme", url="https://acme-old.com")
    monkeypatch.setattr(requests, "get", router({
        "acme.com/pricing": html("https://acme.com/pricing", "", 404),
        "acme-old.com": html("https://www.acme.com/", ACME_HOME),
        "web.archive.org": FakeResponse([])}))
    signals.probe_new(conn, cfg)
    company = db.get_company(conn, cid)
    assert company["site_key"] == "acme.com" and company["domain"] == "acme.com"
    assert company["url"] == "https://www.acme.com"
    assert db.get_site_page(conn, cid)["title"] == "Acme"


def test_probe_new_site_fora_do_ar_nao_e_retentado(monkeypatch, cfg, conn):
    cid = make_company(conn, "Dead", url="https://dead.com")
    fake = router({"": requests.ConnectionError("dns")})
    monkeypatch.setattr(requests, "get", fake)
    assert signals.probe_new(conn, cfg) == 0
    page = db.get_site_page(conn, cid)
    assert page is not None and page["text"] == "" and page["final_url"] is None
    fake.calls.clear()
    assert signals.probe_new(conn, cfg) == 0 and fake.calls == []


def test_probe_new_nao_troca_para_pagina_de_login(monkeypatch, cfg, conn):
    cid = make_company(conn, "Acme", url="https://acme.com")
    monkeypatch.setattr(requests, "get", router({
        "acme.com": html("https://accounts.google.com/signin", "<html><body>Sign in</body></html>"),
        "accounts.google.com": html("https://accounts.google.com/pricing", "", 404),
        "web.archive.org": FakeResponse([])}))
    signals.probe_new(conn, cfg)
    assert db.get_company(conn, cid)["site_key"] == "acme.com"
