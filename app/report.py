"""Gera o relatorio diario em HTML e envia por e-mail (SMTP)."""
import html
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


def build_html(new_rows, cross_rows) -> str:
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

    return f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px;margin:0 auto;padding:24px;background:#fafafa">
  <h1 style="font-size:20px;margin-bottom:4px">⛏️ Garimpo diario — {today}</h1>
  <p style="color:#666;font-size:13px;margin-top:0">{len(new_rows)} novidades nas ultimas 24h · top {len(top)} abaixo</p>
  {body}
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
