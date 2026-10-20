"""Entrega: e-mails diário e semanal, Issues do GitHub e SMTP.

Uso: `from app import deliver` e `deliver.daily.build(...)`, `deliver.issues.sync(...)`.
"""
from app.deliver import components, daily, github, issues, mail, weekly  # noqa: F401
