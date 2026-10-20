"""Envio do e-mail por SMTP (credenciais opcionais: sem SMTP, o HTML fica só em disco)."""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _env(name: str, default: str = "") -> str:
    # strip em tudo: secret colado com \n/espaço quebra DNS e headers
    return (os.environ.get(name) or default).strip()


def send(html_body: str, subject: str) -> bool:
    """Envia via SMTP. Variáveis: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_TO, MAIL_FROM.

    - Resend: smtp.resend.com:587, user "resend", pass = API key,
      MAIL_FROM=onboarding@resend.dev (sem domínio próprio, MAIL_TO precisa
      ser o e-mail da conta Resend)
    - Gmail: smtp.gmail.com:587 com senha de app (MAIL_FROM opcional)
    """
    host = _env("SMTP_HOST")
    user = _env("SMTP_USER")
    if not host or not user:
        print("[mail] SMTP não configurado, relatório salvo apenas em disco")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _env("MAIL_FROM", user)
    msg["To"] = _env("MAIL_TO", user)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        port = int(_env("SMTP_PORT", "587") or 587)
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, _env("SMTP_PASS"))
            s.send_message(msg)
        print(f"[mail] enviado para {msg['To']}")
        return True
    except Exception as e:
        print(f"[mail] falhou: {e}")
        return False
