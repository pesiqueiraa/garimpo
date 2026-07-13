import json

from app import db, report
from tests.conftest import make_product


def _um_dossie(conn):
    pid = make_product(conn, "Alvo", score=9.0)
    db.save_competitors(conn, pid, [
        {"app_id": "br.rival", "name": "Rival", "store": "br",
         "rating": 3.2, "ratings_count": 12000, "reviews_collected": 180}])
    db.save_pain_clusters(conn, pid, [
        {"theme": "cobrança confusa", "frequency": 42, "severity": "alta",
         "examples": [{"app": "Rival", "excerpt": "me cobraram duas vezes"}]}])
    db.save_dossier(conn, pid, "Atacar cobrança com Pix nativo.", 0.03, 9000, 800)
    return db.dossiers_since(conn, "2000-01-01T00:00:00")


def _dossie_v12(conn, kit=None, evidence=None, verdict="concorrente_fraco", score_final=8.5):
    pid = make_product(conn, "AlvoV12", score=9.0)
    db.save_dossier(conn, pid, "Atacar com Pix.", 0.05, 9000, 800,
                    br_verdict_final=verdict, br_evidence=evidence,
                    score_final=score_final, kit=kit)
    return db.dossiers_since(conn, "2000-01-01T00:00:00")


def test_build_html_sem_dossies_continua_igual(conn):
    html_out = report.build_html([], [])
    assert "Dossiês prontos" not in html_out


def test_build_html_renderiza_dossie(conn):
    html_out = report.build_html([], [], _um_dossie(conn))
    assert "Dossiês prontos" in html_out
    assert "Alvo" in html_out
    assert "Rival" in html_out and "🇧🇷" in html_out
    assert "cobrança confusa" in html_out
    assert "me cobraram duas vezes" in html_out
    assert "Atacar cobrança com Pix nativo." in html_out


def test_dossie_sem_players_mostra_aviso(conn):
    pid = make_product(conn, "WebOnly", score=8.0)
    db.save_dossier(conn, pid, "Angulo sem reviews.", 0.01, 500, 50)
    html_out = report.build_html([], [], db.dossiers_since(conn, "2000-01-01T00:00:00"))
    assert "sem dados de reviews" in html_out.lower()


def test_html_injection_em_clusters_e_sanitized(conn):
    """Regression: LLM-derived cluster fields com HTML/malicious frequency sao escapados."""
    pid = make_product(conn, "Alvo", score=9.0)
    db.save_competitors(conn, pid, [
        {"app_id": "br.rival", "name": "Rival", "store": "br",
         "rating": 3.2, "ratings_count": 12000, "reviews_collected": 180}])
    # theme, severity com tags HTML; frequency como string maliciosa
    db.save_pain_clusters(conn, pid, [
        {"theme": "<script>alert(1)</script>", "frequency": "<b>99</b>", "severity": "<img src=x>",
         "examples": [{"app": "Rival", "excerpt": "normal text"}]}])
    db.save_dossier(conn, pid, "Angulo normal.", 0.03, 9000, 800)

    html_out = report.build_html([], [], db.dossiers_since(conn, "2000-01-01T00:00:00"))

    # Raw tags nao aparecem (nao ha XSS)
    assert "<script>" not in html_out
    assert "<img" not in html_out

    # Escapados aparecem (theme e severity sao escapados)
    assert "&lt;script&gt;" in html_out
    assert "&lt;img" in html_out

    # Frequency foi coagido para 0 na insercao, aparece como "0×"
    assert "(0×" in html_out


def test_send_email_ignora_whitespace_nos_secrets(monkeypatch):
    # secrets colados com \n/espaco no final quebravam DNS e headers do e-mail
    monkeypatch.setenv("SMTP_HOST", "smtp.exemplo.com\n")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "resend ")
    monkeypatch.setenv("SMTP_PASS", "chave\n")
    monkeypatch.setenv("MAIL_TO", "eu@exemplo.com\n")
    monkeypatch.setenv("MAIL_FROM", "onboarding@exemplo.com\n")
    chamadas = {}

    class FakeSMTP:
        def __init__(self, host, port):
            chamadas["host"], chamadas["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, pw):
            chamadas["user"], chamadas["pass"] = user, pw

        def send_message(self, msg):
            chamadas["to"], chamadas["from"] = msg["To"], msg["From"]

    monkeypatch.setattr(report.smtplib, "SMTP", FakeSMTP)
    assert report.send_email("<p>oi</p>", "assunto") is True
    assert chamadas == {"host": "smtp.exemplo.com", "port": 587,
                        "user": "resend", "pass": "chave",
                        "to": "eu@exemplo.com", "from": "onboarding@exemplo.com"}


def test_dossie_com_veredito_real_renderiza_links(conn):
    evid = [{"title": "Rival <b>BR</b>", "url": "https://rival.com.br", "why": "player & local"}]
    html_out = report.build_html([], [], _dossie_v12(conn, evidence=evid))
    assert "Veredito de concorrência" in html_out
    assert "Concorrente BR fraco" in html_out
    assert 'href="https://rival.com.br"' in html_out
    assert "Rival &lt;b&gt;BR&lt;/b&gt;" in html_out      # title escapado
    assert "player &amp; local" in html_out               # why escapado
    assert "palpite do modelo" not in html_out


def test_dossie_sem_veredito_mostra_palpite(conn):
    pid = make_product(conn, "SoV11", score=9.0)
    db.save_dossier(conn, pid, "Angulo.", 0.03, 900, 80)
    html_out = report.build_html([], [], db.dossiers_since(conn, "2000-01-01T00:00:00"))
    assert "palpite do modelo — sem busca real" in html_out


def test_evidencia_sem_http_nao_vira_link(conn):
    evid = [{"title": "Suspeito", "url": "javascript:alert(1)", "why": "x"}]
    html_out = report.build_html([], [], _dossie_v12(conn, evidence=evid))
    assert "javascript:alert(1)" not in html_out
    assert "Suspeito" in html_out                          # aparece como texto


def test_dossie_renderiza_kit(conn):
    kit = {"landing": {"headline": "Oficinas sem calote", "subheadline": "Sub",
                       "benefits": ["b1", "b2", "b3"], "cta": "Lista de espera"},
           "mom_test": [f"pergunta {i}" for i in range(5)],
           "whatsapp": "Oi grupo, alguem ja passou por isso?"}
    html_out = report.build_html([], [], _dossie_v12(conn, kit=kit))
    assert "Kit de validação" in html_out
    assert "Oficinas sem calote" in html_out
    assert html_out.count("pergunta ") == 5
    assert "Oi grupo" in html_out


def test_score_final_substitui_badge(conn):
    html_out = report.build_html([], [], _dossie_v12(conn, score_final=8.0))
    assert "8/10" in html_out


def test_build_dossier_page(conn):
    d = _dossie_v12(conn)[0]
    page = report.build_dossier_page(d)
    assert page.startswith("<!doctype html>")
    assert "AlvoV12" in page and "Veredito de concorrência" in page


def test_json_persistido_corrompido_nao_quebra_render(conn):
    """Regressão: JSON malformado em br_evidence ou kit não quebra o render."""
    pid = make_product(conn, "Corrompido", score=9.0)
    db.save_dossier(conn, pid, "Angulo.", 0.03, 900, 80)
    # Injetar JSON inválido diretamente no banco (DB layer escapa com json.dumps)
    conn.execute("UPDATE dossiers SET br_evidence=?, kit=?, br_verdict_final=? WHERE product_id=?",
                 ("{nao é json", "[]", "concorrente_fraco", pid))
    conn.commit()
    html_out = report.build_html([], [], db.dossiers_since(conn, "2000-01-01T00:00:00"))
    assert "Corrompido" in html_out            # dossiê renderizou
    assert "Kit de validação" not in html_out  # kit não-dict ignorado


def test_br_evidence_elemento_nao_dict_skipped(conn):
    """Regressão: elemento não-dict em br_evidence é pulado (não causa AttributeError)."""
    # Simular JSON válido mas com elemento não-dict
    evid = [{"title": "Válido", "url": "https://example.com", "why": "ok"}, "string_invalida", 123]
    # Não podemos usar db.save_dossier com dados já stringificados
    # Então injetar diretamente:
    pid = make_product(conn, "MixedTypes", score=9.0)
    db.save_dossier(conn, pid, "Angulo.", 0.03, 900, 80, br_verdict_final="concorrente_fraco")
    conn.execute(
        "UPDATE dossiers SET br_evidence=? WHERE product_id=?",
        (json.dumps(evid, ensure_ascii=False), pid)
    )
    conn.commit()
    html_out = report.build_html([], [], db.dossiers_since(conn, "2000-01-01T00:00:00"))
    assert "Válido" in html_out              # elemento válido renderiza
    assert "string_invalida" not in html_out  # string é skipada
    assert "MixedTypes" in html_out          # dossiê renderizou mesmo com lixo


def test_kit_campo_array_em_vez_dict_ignorado(conn):
    """Regressão: campo kit com array em vez de dict é ignorado (não causa erro)."""
    pid = make_product(conn, "KitBroken", score=9.0)
    db.save_dossier(conn, pid, "Angulo.", 0.03, 900, 80)
    # Injetar kit como array em vez de dict
    conn.execute("UPDATE dossiers SET kit=? WHERE product_id=?",
                 (json.dumps(["item1", "item2"], ensure_ascii=False), pid))
    conn.commit()
    html_out = report.build_html([], [], db.dossiers_since(conn, "2000-01-01T00:00:00"))
    assert "KitBroken" in html_out            # dossiê renderizou
    assert "Kit de validação" not in html_out  # kit array foi ignorado
