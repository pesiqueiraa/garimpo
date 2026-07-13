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
