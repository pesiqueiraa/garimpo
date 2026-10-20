"""Fixtures compartilhadas. Banco SQLite real em arquivo temporário, sem rede.

Testes mockam só as bordas (requests, llm.ask_json, llm.embed, google_play_scraper),
nunca a lógica.
"""
import copy

import pytest
import requests

from app import config, db, llm


class NetworkBlocked(BaseException):
    """Acesso à rede não mockado num teste."""


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture
def cfg():
    """Config real do repositório (cópia, para o teste poder alterar)."""
    return copy.deepcopy(config.load())


@pytest.fixture(autouse=True)
def _isolamento(monkeypatch):
    """Nenhum teste usa chave real nem rede; quem precisa seta/mocka explicitamente."""
    for var in ("OPENAI_API_KEY", "SERPER_API_KEY", "PH_TOKEN", "GITHUB_TOKEN",
                "GITHUB_REPOSITORY", "SMTP_HOST", "SMTP_USER"):
        monkeypatch.delenv(var, raising=False)
    llm.configure(conn=None, budget_usd=None)

    def _sem_rede(*args, **kwargs):
        # BaseException de propósito: o código de produção engole Exception
        # (falha graciosa) e o teste passaria sem perceber o acesso à rede.
        raise NetworkBlocked(f"teste tentou acessar a rede sem mock: {args[:1]}")

    monkeypatch.setattr(requests, "get", _sem_rede)
    monkeypatch.setattr(requests, "post", _sem_rede)
    monkeypatch.setattr(requests, "head", _sem_rede)


FACTS = {
    "summary_pt": "Agenda online com cobrança para clínicas",
    "niche": "agenda para clínicas", "pain": "faltas e cobrança manual",
    "buyer": "dono da clínica", "user": "recepção", "audience": "B2B",
    "business_type": "saas", "pricing_model": "assinatura", "sales_motion": "self_serve",
    "technical_complexity": "media",
    "local_rails": {"payments": True, "invoicing_tax": True, "messaging": True,
                    "banking_open_finance": False, "government_integration": False,
                    "language_content": False, "local_regulation": False},
    "kill_flags": [], "br_cnae_guess": "8630504", "br_customer_segment": "clínicas odontológicas",
    "search_keywords_pt": ["agenda clínica", "sistema para dentista"],
    "search_keywords_es": ["agenda clínica dental"],
    "br_competitor_guess": "concorrente_fraco",
}


def make_company(conn, name="FooApp", url=None, source="hackernews", points=50,
                 facts: dict | None = None, prior: float | None = None,
                 status: str | None = None) -> int:
    """Cria empresa + evento (e opcionalmente extração v2 e score prévio). Devolve o id."""
    from app import resolve
    url = url or f"https://{name.lower()}.com"
    resolve.ingest(conn, [{"name": name, "tagline": f"{name} faz algo útil", "url": url,
                           "source": source, "kind": "launch", "external_id": f"{source}-{name}",
                           "points": points}])
    key, _ = resolve.site_key(url, name)
    company_id = db.find_company_by_key(conn, key)["id"]
    if facts is not None:
        db.save_extraction(conn, company_id, "extract_v2", "gpt-5-mini", facts)
    if prior is not None:
        db.save_score(conn, company_id, "prior",
                      {"total": prior, "subscores": {"traction": 3, "tropicalizability": 3,
                                                     "timing": 3, "fit": 3, "gap": 3},
                       "kill_reason": None, "evidence": [], "catalysts": []}, "teste")
    if status:
        db.set_company_status(conn, company_id, status)
    conn.commit()
    return company_id


class FakeResponse:
    """Resposta HTTP falsa para mocks de requests.get/post."""

    def __init__(self, json_data=None, status_code=200, text=None, content=None, url=None,
                 headers=None):
        self._json = json_data
        self.status_code = status_code
        self.text = text if text is not None else ("" if json_data is None else str(json_data))
        self.content = content if content is not None else self.text.encode()
        self.url = url or "https://example.com"
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("sem JSON")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err
