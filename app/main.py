"""Garimpo — pipeline diario: coleta -> dedup -> enriquece -> escava -> relatorio -> e-mail.

Uso:
    python main.py            # roda tudo
    python main.py --no-mail  # gera o HTML sem enviar (bom para testar)
    python main.py --investigar "nome ou url"  # escava um produto na hora
"""
import re
import sys
from datetime import date, datetime, timedelta, timezone

from app import collectors, db, enrich, excavate
from app import report


def run(send_mail: bool = True) -> None:
    conn = db.connect()

    # 1. Coleta
    items = collectors.collect_all()
    stats = {"new": 0, "cross_source": 0, "seen": 0}
    for item in items:
        stats[db.upsert(conn, item)] += 1
    print(f"[db] novos: {stats['new']} · cross-source: {stats['cross_source']} · repetidos: {stats['seen']}")

    # 2. Enriquecimento (opcional, precisa de OPENAI_API_KEY)
    enrich.enrich_rows(conn)

    # 3. Escavacao: dossies para produtos com score >= 7 (max 2/dia)
    excavate.excavate_all(conn)

    # 4. Relatorio
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=26)).isoformat(timespec="seconds")
    new_rows = db.new_since(conn, cutoff)
    cross_rows = db.cross_source_hits(conn)
    dossiers = db.dossiers_since(conn, cutoff)
    html_body = report.build_html(new_rows, cross_rows, dossiers)

    out = db.DB_PATH.parent / f"relatorio-{date.today().isoformat()}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html_body, encoding="utf-8")
    print(f"[report] salvo em {out}")

    # 5. E-mail
    if send_mail:
        report.send_email(html_body, f"⛏️ Garimpo diario — {date.today().strftime('%d/%m')}")

    conn.close()


def investigate(term: str, conn=None) -> int:
    """Modo manual: escava um produto na hora e salva HTML local. 0 = sucesso."""
    conn = conn or db.connect()
    row = conn.execute(
        "SELECT * FROM products WHERE url=? OR lower(name)=lower(?)", (term, term)
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM products WHERE name LIKE ? ORDER BY points DESC",
            (f"%{term}%",),
        ).fetchone()
    if row is None:
        db.upsert(conn, {"name": term,
                         "url": term if term.startswith("http") else None,
                         "source": "manual", "points": 0})
        row = conn.execute("SELECT * FROM products WHERE name=?", (term,)).fetchone()
        print(f"[investigar] produto novo criado: '{term}'")
    if row["niche"] is None:
        enrich.enrich_rows(conn, only_id=row["id"])
        row = conn.execute("SELECT * FROM products WHERE id=?", (row["id"],)).fetchone()

    if not excavate.investigate(conn, row["id"]):
        return 1

    d = next(x for x in db.dossiers_since(conn, "2000-01-01T00:00:00")
             if x["dossier"]["product_id"] == row["id"])
    slug = re.sub(r"[^a-z0-9]+", "-", row["name"].lower()).strip("-")[:40] or "produto"
    out = db.DB_PATH.parent / f"dossie-{slug}-{date.today().isoformat()}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report.build_dossier_page(d), encoding="utf-8")
    print(f"[investigar] dossie salvo em {out}")
    return 0


if __name__ == "__main__":
    if "--investigar" in sys.argv:
        idx = sys.argv.index("--investigar")
        if idx + 1 >= len(sys.argv):
            print('uso: python3 -m app.main --investigar "nome ou url"')
            sys.exit(2)
        sys.exit(investigate(sys.argv[idx + 1]))
    run(send_mail="--no-mail" not in sys.argv)
