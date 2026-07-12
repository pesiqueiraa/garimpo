"""Gera o relatorio diario em HTML e envia por e-mail (SMTP)."""
import html
import json
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SOURCE_LABEL = {"hackernews": "Show HN", "producthunt": "Product Hunt", "yc": "Y Combinator"}
VERDICT_LABEL = {
    "sem_concorrente": ("🟢 Sem concorrente BR detectado", "#0a7a33"),
    "concorrente_fraco": ("🟡 Concorrente BR fraco", "#a07800"),
    "concorrente_forte": ("🔴 Concorrente BR forte", "#b3261e"),
    "desconhecido": ("⚪ Concorrencia BR desconhecida", "#666"),
}
STORE_FLAG = {"br": "🇧🇷", "us": "🌍"}
SEVERITY_COLOR = {"alta": "#b3261e", "media": "#a07800", "baixa": "#666"}


def _dossier_html(d) -> str:
    dos = d["dossier"]
    name = html.escape(dos["name"])
    tagline = html.escape(dos["tagline"] or "")
    url = dos["url"] or dos["source_url"] or "#"
    score = dos["opportunity_score"]

    meta = SOURCE_LABEL.get(dos["source"], dos["source"])
    if dos["points"]:
        meta += f" · {dos['points']} pts"
    if dos["sources_seen"] and "," in dos["sources_seen"]:
        meta += " · 🔁 múltiplas fontes"

    if d["competitors"]:
        linhas = "".join(
            f"<tr><td style='padding:4px 10px 4px 0'>{html.escape(c['name'])}</td>"
            f"<td style='padding:4px 10px'>{STORE_FLAG.get(c['store'], c['store'])}</td>"
            f"<td style='padding:4px 10px'>{c['rating'] if c['rating'] is not None else '—'}</td>"
            f"<td style='padding:4px 10px'>{c['ratings_count'] or '?'} avaliações</td></tr>"
            for c in d["competitors"])
        players = (f"<table style='font-size:13px;color:#333;border-collapse:collapse'>{linhas}</table>")
    else:
        players = ("<p style='font-size:13px;color:#888'>Nenhum player na Play Store "
                   "— sem dados de reviews.</p>")

    dores = ""
    for cl in d["clusters"]:
        cor = SEVERITY_COLOR.get(cl["severity"], "#666")
        exemplos = "".join(
            (f"<div style='font-size:12px;color:#666;font-style:italic;margin:2px 0 2px 12px'>"
             f"“{html.escape(e['excerpt'])}” — {html.escape(e['app'])}</div>")
            for e in json.loads(cl["examples"] or "[]")[:2])
        dores += (f"<div style='margin:8px 0'><b style='font-size:13px'>{html.escape(cl['theme'])}</b> "
                  f"<span style='color:{cor};font-size:12px'>({cl['frequency']}× · {cl['severity']})</span>"
                  f"{exemplos}</div>")

    angle = html.escape(dos["attack_angle"] or "").replace("\n", "<br>")

    def _h3(t):
        return f"<div style='font-size:12px;font-weight:700;color:#888;text-transform:uppercase;margin:14px 0 4px'>{t}</div>"

    return f"""
    <div style="border:2px solid #111;border-radius:12px;padding:18px;margin-bottom:16px;background:#fff">
      <span style='float:right;background:#111;color:#fff;border-radius:12px;padding:2px 10px;font-size:12px'>{score:.0f}/10</span>
      <a href="{html.escape(url)}" style="font-size:17px;font-weight:700;color:#111;text-decoration:none">{name}</a>
      <div style="font-size:14px;color:#333;margin-top:3px">{tagline}</div>
      {_h3("Tração lá fora")}<div style="font-size:13px;color:#444">{meta}</div>
      {_h3("Players encontrados")}{players}
      {_h3("Mapa de dores")}{dores or "<p style='font-size:13px;color:#888'>—</p>"}
      {_h3("⚔️ Ângulo de ataque")}<div style="font-size:13px;color:#222;line-height:1.5">{angle}</div>
    </div>"""


def _card(row) -> str:
    name = html.escape(row["name"])
    tagline = html.escape(row["tagline"] or "")
    url = row["url"] or row["source_url"] or "#"
    src = SOURCE_LABEL.get(row["source"], row["source"])
    score = row["opportunity_score"]
    verdict, vcolor = VERDICT_LABEL.get(row["br_verdict"] or "", ("", "#666"))

    meta = f"{src}"
    if row["points"]:
        meta += f" · {row['points']} pts"
    if row["category"]:
        meta += f" · {html.escape(row['category'])}"

    extra = ""
    if row["niche"] or row["pain"]:
        extra = (
            f"<div style='margin-top:6px;font-size:13px;color:#444'>"
            f"<b>{html.escape(row['niche'] or '')}</b>"
            f"{' — ' + html.escape(row['pain']) if row['pain'] else ''}</div>"
        )
    badge = ""
    if verdict:
        badge = f"<div style='margin-top:6px;font-size:12px;color:{vcolor};font-weight:600'>{verdict}</div>"
    score_html = ""
    if score is not None:
        score_html = (
            f"<span style='float:right;background:#111;color:#fff;border-radius:12px;"
            f"padding:2px 10px;font-size:12px'>{score:.0f}/10</span>"
        )

    return f"""
    <div style="border:1px solid #e5e5e5;border-radius:10px;padding:14px 16px;margin-bottom:10px">
      {score_html}
      <a href="{html.escape(url)}" style="font-size:16px;font-weight:700;color:#111;text-decoration:none">{name}</a>
      <div style="font-size:14px;color:#333;margin-top:3px">{tagline}</div>
      <div style="font-size:12px;color:#888;margin-top:6px">{meta}</div>
      {extra}{badge}
    </div>"""


def build_html(new_rows, cross_rows, dossiers=()) -> str:
    today = date.today().strftime("%d/%m/%Y")
    top = sorted(
        new_rows,
        key=lambda r: ((r["opportunity_score"] or 0), r["points"]),
        reverse=True,
    )[:12]

    cross_html = ""
    if cross_rows:
        cross_html = (
            "<h2 style='font-size:16px;margin:24px 0 10px'>🔁 Aparecendo em multiplas fontes (sinal forte)</h2>"
            + "".join(_card(r) for r in cross_rows[:5])
        )

    body = "".join(_card(r) for r in top) or "<p>Nenhuma novidade hoje. O garimpo continua amanha.</p>"

    dossier_html = ""
    if dossiers:
        dossier_html = (
            "<h2 style='font-size:16px;margin:24px 0 10px'>🔬 Dossiês prontos</h2>"
            + "".join(_dossier_html(d) for d in dossiers)
        )

    return f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px;margin:0 auto;padding:24px;background:#fafafa">
  <h1 style="font-size:20px;margin-bottom:4px">⛏️ Garimpo diario — {today}</h1>
  <p style="color:#666;font-size:13px;margin-top:0">{len(new_rows)} novidades nas ultimas 24h · top {len(top)} abaixo</p>
  {body}
  {dossier_html}
  {cross_html}
  <p style="color:#aaa;font-size:11px;margin-top:24px">Gerado automaticamente pelo seu garimpo pessoal.</p>
</body></html>"""


def send_email(html_body: str, subject: str) -> bool:
    """Envia via SMTP. Config por variaveis de ambiente:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_TO, MAIL_FROM
    - Resend: smtp.resend.com:587, user "resend", pass = API key,
      MAIL_FROM=onboarding@resend.dev (sem dominio proprio, MAIL_TO
      precisa ser o e-mail da conta Resend)
    - Gmail: smtp.gmail.com:587 com senha de app (MAIL_FROM opcional)"""
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    if not host or not user:
        print("[mail] SMTP nao configurado, relatorio salvo apenas em disco")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ.get("MAIL_FROM", user)
    msg["To"] = os.environ.get("MAIL_TO", user)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
            s.starttls()
            s.login(user, os.environ["SMTP_PASS"])
            s.send_message(msg)
        print(f"[mail] enviado para {msg['To']}")
        return True
    except Exception as e:
        print(f"[mail] falhou: {e}")
        return False
