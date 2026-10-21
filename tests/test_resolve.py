import pytest

from app import db, resolve
from tests.conftest import make_company


@pytest.mark.parametrize("url,name,expected", [
    ("https://www.Acme.com/pricing?utm_source=hn#top", "Acme", ("acme.com", "acme.com")),
    ("https://app.acme.com.br/login", "Acme", ("acme.com.br", "acme.com.br")),
    ("acme.io", "Acme", ("acme.io", "acme.io")),
    ("https://github.com/Fulano/Projeto/tree/main", "Projeto", ("github.com/fulano/projeto", None)),
    ("https://github.com/Fulano", "Fulano", ("github.com/fulano", None)),
    ("https://foo.vercel.app/x", "Foo", ("foo.vercel.app", None)),
    ("https://bar.github.io/demo", "Bar", ("bar.github.io", None)),
    ("https://play.google.com/store/apps/details?id=com.Foo.App&hl=en", "Foo", ("play.google.com/com.foo.app", None)),
    ("https://apps.apple.com/us/app/foo/id123456", "Foo", ("apps.apple.com/id123456", None)),
    ("https://news.ycombinator.com/item?id=42", "Foo", ("news.ycombinator.com/42", None)),
    ("https://www.producthunt.com/r/abc123?utm_campaign=x", "Foo", ("producthunt.com/r/abc123", None)),
    (None, "Foo Bar!", ("name:foobar", None)),
    ("not a url", "Foo", ("name:foo", None)),
    ("https://shop.co.uk", "Shop", ("shop.co.uk", "shop.co.uk")),
])
def test_site_key(url, name, expected):
    assert resolve.site_key(url, name) == expected


def test_clean_url_tira_rastreio_e_fragmento():
    assert resolve.clean_url("HTTPS://Acme.com/a/?utm_medium=x&id=3#y") == "https://acme.com/a?id=3"
    assert resolve.clean_url("") is None
    assert resolve.clean_url(None) is None


def _item(name, url, source="hackernews", ext=None, points=10):
    return {"name": name, "tagline": f"{name} tagline", "url": url, "source": source,
            "kind": "launch", "external_id": ext or f"{source}-{name}", "points": points}


def test_ingest_cria_e_reconhece_cross_source(conn):
    stats = resolve.ingest(conn, [_item("Acme", "https://acme.com")])
    assert stats["new"] == 1
    stats = resolve.ingest(conn, [_item("Acme", "https://www.acme.com/pricing", "producthunt")])
    assert stats == {"new": 0, "seen": 0, "cross_source": 1, "skipped": 0}
    company = db.find_company_by_key(conn, "acme.com")
    assert db.company_sources(conn, company["id"]) == ["hackernews", "producthunt"]


def test_ingest_repetido_nao_duplica_evento(conn):
    resolve.ingest(conn, [_item("Acme", "https://acme.com", points=10)])
    resolve.ingest(conn, [_item("Acme", "https://acme.com", points=99)])
    company = db.find_company_by_key(conn, "acme.com")
    events = db.company_events(conn, company["id"])
    assert len(events) == 1 and events[0]["points"] == 99


def test_ingest_empresa_so_com_nome_ganha_dominio(conn):
    resolve.ingest(conn, [_item("Acme", None, "yc", "yc-acme")])
    assert db.find_company_by_key(conn, "name:acme") is not None
    resolve.ingest(conn, [_item("Acme", "https://acme.com")])
    assert db.find_company_by_key(conn, "name:acme") is None
    company = db.find_company_by_key(conn, "acme.com")
    assert company["domain"] == "acme.com"
    assert len(db.company_events(conn, company["id"])) == 2


def test_ingest_limite_de_novas(conn):
    items = [_item(f"P{i}", f"https://p{i}.com") for i in range(5)]
    stats = resolve.ingest(conn, items, max_new=3)
    assert stats["new"] == 3 and stats["skipped"] == 2


def test_ingest_grava_observacoes_do_item(conn):
    item = _item("Acme", "https://acme.com", "trustmrr")
    item["observations"] = [{"metric": "mrr_usd", "value_num": 12000, "value_text": None}]
    resolve.ingest(conn, [item])
    company = db.find_company_by_key(conn, "acme.com")
    obs = db.latest_observations(conn, company["id"])
    assert obs["mrr_usd"]["value_num"] == 12000
    assert obs["mrr_usd"]["source"] == "trustmrr"


def test_update_company_site_funde_duplicata(conn):
    real = make_company(conn, "Acme", url="https://acme.com", source="hackernews")
    ph = make_company(conn, "Acme PH", url="https://www.producthunt.com/r/xyz", source="producthunt")
    db.add_observations(conn, ph, [{"metric": "open_jobs", "value_num": 3, "source": "lever"}])
    survivor = db.update_company_site(conn, ph, "acme.com", "acme.com", "https://acme.com")
    assert survivor == real
    assert db.get_company(conn, ph) is None
    assert db.company_sources(conn, real) == ["hackernews", "producthunt"]
    assert db.latest_observations(conn, real)["open_jobs"]["value_num"] == 3
