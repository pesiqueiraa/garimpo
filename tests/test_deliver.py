import re
from html.parser import HTMLParser

import requests

from app import db, deliver, trends
from app.deliver import components, github, issues
from tests.conftest import FACTS, FakeResponse, make_company

EVIL = "<script>alert(1)</script>"


class _Checker(HTMLParser):
    """Confere que as tags abrem e fecham direito (HTML do e-mail não pode quebrar)."""

    VOID = {"meta", "br", "img", "hr", "input", "link"}

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> inesperado (pilha {self.stack[-3:]})")
            return
        self.stack.pop()


def assert_valid_html(html: str) -> None:
    assert html.startswith("<!DOCTYPE html>")
    assert "<script" not in html.lower()
    checker = _Checker()
    checker.feed(html)
    assert checker.errors == []
    assert checker.stack == []


def sections(html: str) -> list[str]:
    return re.findall(r"<h2[^>]*>(.*?)</h2>", html)


def assert_no_empty_sections(html: str) -> None:
    """Toda <h2> é seguida de conteúdo antes da próxima <h2> ou do rodapé."""
    parts = re.split(r"<h2[^>]*>.*?</h2>", html)
    for part in parts[1:]:
        visible = re.sub(r"<[^>]+>|\s", "", part.split("Gerado automaticamente")[0])
        assert visible, "seção vazia no e-mail"


def _github_env(monkeypatch, token=True):
    monkeypatch.setenv("GITHUB_REPOSITORY", "dono/garimpo")
    if token:
        monkeypatch.setenv("GITHUB_TOKEN", "tok")


SCORE = {"total": 7.4, "subscores": {"traction": 4, "tropicalizability": 3, "timing": 3, "fit": 4,
                                     "gap": 3},
         "evidence": ["MRR verificado US$12,000", "equipe de 9 pessoas", "apareceu em 2 fontes",
                      "não deve aparecer"], "catalysts": ["pix_automatico"], "kill_reason": None}


def _dossier(conn, company_id, issue_number=None, kit=None, kit_reason=None, verdict="concorrente_fraco"):
    data = {
        "attack_angle": "Primeiro parágrafo do ângulo.\n\nSegundo parágrafo que não vai no resumo.",
        "br_verdict": verdict, "br_rationale": "Há dois players pequenos.",
        "br_evidence": [{"title": "Concorrente BR", "url": "https://concorrente.com.br", "why": "faz agenda"},
                        {"title": "Link ruim", "url": "javascript:alert(1)", "why": ""}],
        "latam": [{"country": "mx", "name": "AgendaMX", "url": "https://agenda.mx"}],
        "web_complaints": [{"source": "reclame_aqui", "title": "Sistema trava", "url": "https://ra.com/x",
                            "snippet": "não funciona"}],
        "tam": {"cnae": "8630504", "segment": "clínicas odontológicas", "count": 41250,
                "top_ufs": [["SP", 12000], ["MG", 5000]], "note": "dados abertos do CNPJ"},
        "catalysts": ["pix_automatico"], "score_prior": 6.8, "score_posterior": 7.9,
        "subscores": {"traction": 4, "tropicalizability": 4, "timing": 4, "fit": 4, "gap": 4},
        "kit": kit, "kit_reason": kit_reason, "issue_number": issue_number, "cost_usd": 0.05,
    }
    dossier_id = db.save_dossier(conn, company_id, data)
    db.save_pain_clusters(conn, dossier_id, company_id, [
        {"theme": "Cobrança confusa", "frequency": 12, "severity": "alta",
         "examples": [{"excerpt": f"cobrou duas vezes {EVIL}", "app": "AppX"}]},
        {"theme": "Suporte lento", "frequency": 5, "severity": "media", "examples": []}])
    db.save_app_competitors(conn, dossier_id, company_id, [
        {"store": "play", "country": "br", "app_id": "com.x", "name": "AppX", "rating": 3.9,
         "ratings_count": 1200}])
    br_id = db.upsert_br_competitor(conn, "ConcorrenteBR", "concorrente.com.br", "agenda", [])
    db.link_br_competitor(conn, br_id, company_id, dossier_id, "fraco")
    conn.commit()
    return dossier_id


KIT = {"landing": {"headline": "Agenda que cobra sozinha", "subheadline": "Pix automático",
                   "benefits": ["menos faltas", "cobrança automática", "WhatsApp"], "cta": "Entrar na lista"},
       "mom_test": ["Como você cobra hoje?", "Quando foi a última falta?"],
       "whatsapp": "Oi, pessoal da odonto!",
       "where_to_find": [{"name": "Grupos de dentistas no Facebook", "why": "muitos donos de clínica"},
                         "Congresso CIOSP"]}


# ---------- componentes ----------

def test_card_escapa_nome_e_resumo_maliciosos(cfg):
    company = {"id": 1, "name": f"Evil{EVIL}", "tagline": "x", "url": "javascript:alert(1)",
               "status": "new"}
    facts = {**FACTS, "summary_pt": f"resumo <img src=x onerror=alert(1)>"}
    html = components.company_card(company, {**SCORE, "evidence": [EVIL, "catalisador: Pix Automático"]},
                                   facts, cfg)
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    assert "javascript:" not in html
    assert "7.4/10" in html
    assert "Tração 4 · Trop. 3 · Timing 3 · Fit 4 · Gap 3" in html
    assert html.count("Pix Automático") == 1      # chip, não repete como evidência
    assert "não deve aparecer" not in html


def test_card_sem_score_nem_fatos_nao_quebra(cfg):
    html = components.company_card({"id": 2, "name": "Nua", "tagline": None, "url": None}, None, None, cfg)
    assert "Nua" in html and "/10" not in html and "Tração" not in html


def test_links_de_feedback_so_com_repo(cfg, monkeypatch):
    company = {"id": 42, "name": "Foo", "url": "https://foo.com"}
    assert "issues/new" not in components.company_card(company, SCORE, FACTS, cfg)
    monkeypatch.setenv("GITHUB_REPOSITORY", "dono/garimpo")
    html = components.company_card(company, SCORE, FACTS, cfg)
    assert html.count("https://github.com/dono/garimpo/issues/new?") == 3
    assert "feedback+like+42" in html and "feedback+dislike+42" in html and "feedback+watch+42" in html
    assert "issues/new" not in components.company_card(company, SCORE, FACTS, cfg, feedback=False)


def test_dossier_full_e_resumo(conn, cfg, monkeypatch):
    cid = make_company(conn, "Clinicando", facts=FACTS, prior=7.0)
    d = db.get_dossier(conn, _dossier(conn, cid, kit=KIT, kit_reason="score"))
    full = components.dossier_full(d)
    for text in ("Concorrente BR fraco", "ConcorrenteBR", "AppX", "AgendaMX", "Sistema trava",
                 "41.250", "estimativa", "Pix Automático", "Segundo parágrafo",
                 "Grupos de dentistas no Facebook", "Congresso CIOSP", "Mom Test"):
        assert text in full, text
    assert "<script>" not in full and "javascript:" not in full

    summary = components.dossier_summary(d, cfg)
    assert "Primeiro parágrafo" in summary and "Segundo parágrafo" not in summary
    assert "estimativa" in summary and "Agenda que cobra sozinha" in summary
    assert "Abrir dossiê completo" not in summary  # sem issue_number

    _github_env(monkeypatch)
    d["dossier"]["issue_number"] = 17
    assert "https://github.com/dono/garimpo/issues/17" in components.dossier_summary(d, cfg)

    page = components.dossier_page(d)
    assert_valid_html(page)
    assert "Clinicando" in page


def test_dossie_minimo_nao_mostra_blocos_vazios(conn):
    cid = make_company(conn, "Crua")
    d = db.get_dossier(conn, db.save_dossier(conn, cid, {}))
    html = components.dossier_full(d)
    assert "palpite do modelo" in html
    for absent in ("Mapa de dores", "América Latina", "Kit de validação", "Reclamações",
                   "Tamanho do mercado", "Concorrentes nas lojas"):
        assert absent not in html


# ---------- e-mail diário ----------

def test_diario_banco_vazio(conn, cfg):
    subject, html = deliver.daily.build(conn, cfg, {})
    assert_valid_html(html)
    assert_no_empty_sections(html)
    assert "0 novidades · 0 dossiês" in subject
    assert "Nada novo acima do corte" in html
    assert "Dossiês prontos" not in html and "Promovidas" not in html


def test_diario_populado(conn, cfg, monkeypatch):
    _github_env(monkeypatch, token=False)
    ids = [make_company(conn, f"Emp{i}", facts=FACTS, prior=float(i)) for i in range(1, 9)]
    killed = make_company(conn, "Morta", facts=FACTS)
    db.save_score(conn, killed, "prior", {**SCORE, "total": 0.0, "kill_reason": "descarte: crypto"}, "t")
    dug = make_company(conn, "Escavada", facts=FACTS, prior=9.5)
    _dossier(conn, dug, issue_number=33)
    other = make_company(conn, "SemIssue", facts=FACTS, prior=3.0)
    _dossier(conn, other)
    promoted = make_company(conn, "Subiu", facts=FACTS, prior=6.0)
    db.set_company_status(conn, promoted, "promoted", "tração subiu de 2 para 4")
    conn.commit()

    run_info = {"stats": {}, "source_counts": {"hackernews": 12, "producthunt": 0},
                "alerts": ["⚠️ producthunt não trouxe nada hoje (costuma trazer ~5)", f"❌ {EVIL}"],
                "usage": {"usd": 0.0123}}
    subject, html = deliver.daily.build(conn, cfg, run_info)
    assert_valid_html(html)
    assert_no_empty_sections(html)
    assert sections(html) == ["⭐ Destaques de hoje", "🔬 Dossiês prontos", "📈 Promovidas"]
    assert "2 dossiês" in subject and re.search(r"Garimpo — \d\d/\d\d · 12 novidades", subject)

    top = html.split("Dossiês prontos")[0]
    assert top.count("/10</span>") == 6           # top_n
    assert "Emp8" in top and "Emp3" in top and "Emp2" not in top
    assert "Morta" not in html
    assert "issues/new" in top                     # links de feedback (repo configurado)
    assert "https://github.com/dono/garimpo/issues/33" in html
    assert "Segundo parágrafo" in html             # o sem Issue sai completo
    assert "tração subiu de 2 para 4" in html
    assert "costuma trazer ~5" in html and "&lt;script&gt;" in html
    assert "US$0.0123" in html
    assert ids


def test_diario_sem_destaque_mostra_fila(conn, cfg):
    cid = make_company(conn, "NaFila", facts=FACTS, prior=7.0)
    db.set_queued(conn, cid, 7.5)
    conn.execute("UPDATE companies SET first_seen='2026-01-01T00:00:00+00:00' WHERE id=?", (cid,))
    conn.commit()
    _, html = deliver.daily.build(conn, cfg, {})
    assert "Nada novo acima do corte" in html and "NaFila" in html


# ---------- e-mail semanal ----------

def test_semanal_ignora_tendencias_velhas(conn, cfg):
    cid = make_company(conn, "Velha")
    db.save_trends(conn, "2026-01-04", [{"label": "tema antigo", "size": 3, "size_recent": 1,
                                         "growth": 1.0, "company_ids": [cid]}])
    _, html = deliver.weekly.build(conn, cfg)
    assert "tema antigo" not in html and "Temas em alta" not in html


def test_semanal_banco_vazio(conn, cfg):
    subject, html = deliver.weekly.build(conn, cfg)
    assert_valid_html(html)
    assert_no_empty_sections(html)
    assert subject.startswith("📊 Garimpo semanal — ")
    assert "o score não aprende sem você" in html
    assert sections(html) == ["🗳️ Seu feedback"]


def test_semanal_populado(conn, cfg):
    a = make_company(conn, "Alfa", facts=FACTS, prior=5.0)
    b = make_company(conn, "Beta", facts=FACTS, prior=6.0)
    c = make_company(conn, "Gama", facts=FACTS, prior=4.0)
    db.save_trends(conn, trends.week_of(), [{"label": f"agenda {EVIL}", "size": 3, "size_recent": 2,
                                         "growth": 2.5, "company_ids": [a, b, c]}])
    # Alfa ganhou tração: score antigo (antes da janela) com tração menor
    db.save_score(conn, a, "prior", {**SCORE, "subscores": {**SCORE["subscores"], "traction": 2}}, "t")
    conn.execute("UPDATE scores SET created_at='2026-01-01T00:00:00+00:00' WHERE company_id=?", (a,))
    db.save_score(conn, a, "prior", SCORE, "t")
    _dossier(conn, b, kit=KIT, kit_reason="weekly_best")
    db.set_queued(conn, c, 6.3)
    db.add_feedback(conn, a, "like", "email", external_ref="1")
    run_id = db.start_run(conn, "rodar")
    db.finish_run(conn, run_id, {}, {"hackernews": 30, "producthunt": 0}, {"usd": 0.02}, ["x: falhou"])
    conn.commit()

    subject, html = deliver.weekly.build(conn, cfg)
    assert_valid_html(html)
    assert_no_empty_sections(html)
    assert sections(html) == ["🔥 Temas em alta", "🚀 Quem ganhou tração", "🧰 Kit da semana",
                              "⏳ Fila de escavação", "🗳️ Seu feedback", "🩺 Saúde"]
    assert "&lt;script&gt;" in html and "Alfa, Beta, Gama" in html and "2.5×" in html
    assert "tração 2 → 4" in html
    assert "Agenda que cobra sozinha" in html
    assert "prioridade 6.3" in html
    assert "👍 curti: 1" in html
    assert "hackernews 30" in html and "sem itens na semana: producthunt" in html


# ---------- Issues ----------

def test_render_issue_body_tem_marcador_e_secoes(conn):
    cid = make_company(conn, "Clinicando", facts=FACTS, prior=7.0)
    dossier_id = _dossier(conn, cid, kit=KIT, kit_reason="score")
    d = db.get_dossier(conn, dossier_id)
    body = issues.render_issue_body({**d, "score": SCORE, "facts": FACTS})
    assert f"<!-- garimpo:dossier={dossier_id} company={cid} -->" in body
    for text in ("prévio **6.8**", "posterior **7.9**", "Tração 4", "MRR verificado",
                 "Concorrente BR fraco", "[Concorrente BR](https://concorrente.com.br)", "ConcorrenteBR",
                 "AgendaMX", "Cobrança confusa", "Sistema trava", "(estimativa)", "Pix Automático",
                 "Segundo parágrafo", "Mom Test", "Grupos de dentistas", "status:interessante"):
        assert text in body, text
    assert "<script>" not in body and "javascript:" not in body


def test_sync_sem_token_pula(conn, cfg, monkeypatch):
    _github_env(monkeypatch, token=False)
    cid = make_company(conn, "Foo")
    _dossier(conn, cid)
    assert issues.sync(conn, cfg) == 0


def test_sync_cria_issues_e_grava_numero(conn, cfg, monkeypatch):
    _github_env(monkeypatch)
    first = make_company(conn, "Primeira", facts=FACTS, prior=7.0)
    second = make_company(conn, "Segunda", facts=FACTS, prior=6.0)
    d1, d2 = _dossier(conn, first), _dossier(conn, second)
    legacy = db.save_dossier(conn, second, {"legacy_dossier_id": 5})
    conn.commit()

    posted = []

    def fake_get(url, headers=None, params=None, timeout=None, **kw):
        return FakeResponse([{"name": "garimpo-dossie"}])

    def fake_post(url, headers=None, json=None, timeout=None, **kw):
        posted.append((url, json))
        if url.endswith("/labels"):
            return FakeResponse({}, status_code=422) if json["name"] == "status:novo" \
                else FakeResponse({"name": json["name"]}, status_code=201)
        assert headers["X-GitHub-Api-Version"] == "2022-11-28"
        if "Segunda" in json["title"]:
            return FakeResponse({"message": "boom"}, status_code=500)
        return FakeResponse({"number": 100 + len(posted)}, status_code=201)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    assert issues.sync(conn, cfg) == 1

    issue_posts = [(u, j) for u, j in posted if u.endswith("/repos/dono/garimpo/issues")]
    assert len(issue_posts) == 2                       # legado não vira Issue
    title, payload = issue_posts[0][1]["title"], issue_posts[0][1]
    assert title == "🔬 Primeira — 7.9/10"
    assert payload["labels"] == ["garimpo-dossie", "status:novo"]
    assert f"garimpo:dossier={d1}" in payload["body"]
    assert db.get_dossier(conn, d1)["dossier"]["issue_number"] is not None
    assert db.get_dossier(conn, d2)["dossier"]["issue_number"] is None   # falhou, tenta amanhã
    assert db.get_dossier(conn, legacy)["dossier"]["issue_number"] is None
    created_labels = {j["name"] for u, j in posted if u.endswith("/labels")}
    assert "garimpo-dossie" not in created_labels and "status:construindo" in created_labels


def test_post_kit_comment(conn, cfg, monkeypatch):
    _github_env(monkeypatch)
    cid = make_company(conn, "ComKit")
    with_issue = _dossier(conn, cid, issue_number=9, kit=KIT, kit_reason="weekly_best")
    without_issue = _dossier(conn, cid, kit=KIT, kit_reason="weekly_best")
    posted = []
    bodies = {"/issues/9": "dossiê sem kit"}
    monkeypatch.setattr(requests, "get",
                        lambda url, **kw: FakeResponse({"body": bodies["/issues/9"]}))
    monkeypatch.setattr(requests, "post",
                        lambda url, **kw: posted.append((url, kw["json"])) or FakeResponse({"id": 1}, 201))
    assert issues.post_kit_comment(conn, cfg, with_issue) is True
    assert posted[0][0].endswith("/repos/dono/garimpo/issues/9/comments")
    assert "Agenda que cobra sozinha" in posted[0][1]["body"]
    assert issues.post_kit_comment(conn, cfg, without_issue) is False
    assert issues.post_kit_comment(conn, cfg, None) is False
    # Issue que já nasceu com o kit no corpo não ganha comentário repetido
    bodies["/issues/9"] = "...\n" + issues.KIT_HEADER + "\n..."
    assert issues.post_kit_comment(conn, cfg, with_issue) is False
    assert len(posted) == 1


def test_github_helpers(cfg, monkeypatch):
    assert github.available(cfg) is False and github.repo_url(cfg) is None
    monkeypatch.setenv("GITHUB_REPOSITORY", "dono/garimpo")
    assert github.repo_url(cfg) == "https://github.com/dono/garimpo"
    assert github.available(cfg) is False
    monkeypatch.setenv("GITHUB_TOKEN", "  tok \n")
    assert github.available(cfg) is True
    monkeypatch.setenv("GITHUB_REPOSITORY", "lixo com espaço")
    assert github.repo(cfg) is None


def test_mail_sem_smtp_nao_envia():
    assert deliver.mail.send("<p>oi</p>", "assunto") is False
