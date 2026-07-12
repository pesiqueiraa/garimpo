"""Garimpo — pipeline diario: coleta -> dedup -> enriquece -> relatorio -> e-mail.

Uso:
    python main.py            # roda tudo
    python main.py --no-mail  # gera o HTML sem enviar (bom para testar)
"""
import sys
from datetime import date, datetime, timedelta, timezone

from app import collectors, db, enrich
from app import report


def run(send_mail: bool = True) -> None:
    conn = db.connect()

    # 1. Coleta
    items = collectors.collect_all()
    stats = {"new": 0, "cross_source": 0, "seen": 0}
    for item in items:
        stats[db.upsert(conn, item)] += 1
    print(f"[db] novos: {stats['new']} · cross-source: {stats['cross_source']} · repetidos: {stats['seen']}")

    # 2. Enriquecimento (opcional, precisa de ANTHROPIC_API_KEY)
    enrich.enrich_rows(conn)

    # 3. Relatorio
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=26)).isoformat(timespec="seconds")
    new_rows = db.new_since(conn, cutoff)
    cross_rows = db.cross_source_hits(conn)
    html_body = report.build_html(new_rows, cross_rows)

    out = db.DB_PATH.parent / f"relatorio-{date.today().isoformat()}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html_body, encoding="utf-8")
    print(f"[report] salvo em {out}")

    # 4. E-mail
    if send_mail:
        report.send_email(html_body, f"⛏️ Garimpo diario — {date.today().strftime('%d/%m')}")

    conn.close()


if __name__ == "__main__":
    run(send_mail="--no-mail" not in sys.argv)
