"""E-mail semanal (domingo): temas em alta, quem ganhou tração, kit, fila, feedback e saúde."""
from datetime import datetime, timedelta, timezone

from app import db
from app.deliver import components as comp

BRT = timezone(timedelta(hours=-3))
SIGNAL_LABEL = {"like": "👍 curti", "dislike": "👎 não curti", "watch": "👀 acompanhar",
                "interesting": "interessante", "validating": "validando",
                "discarded": "descartado", "building": "construindo"}


def section(title: str, body: str) -> str:
    return comp.h2(title) + body if body else ""


def _box(inner: str) -> str:
    return ("<div style=\"border:1px solid #e3e3e3;border-radius:10px;padding:12px 14px;"
            f"margin-bottom:8px;background:#fff\">{inner}</div>")


def _trends(conn, now: datetime) -> str:
    """Temas da semana; some se a última rodada de tendências for velha (etapa falhou)."""
    fresh_since = (now.astimezone(BRT) - timedelta(days=8)).date().isoformat()
    out = ""
    for t in [t for t in db.latest_trends(conn) if str(t.get("week") or "") >= fresh_since][:6]:
        examples = [c["name"] for c in db.companies_by_ids(conn, list(t.get("company_ids") or [])[:3])]
        try:
            growth = f"{float(t['growth']):.1f}×"
        except (TypeError, ValueError, KeyError):
            growth = "—"
        meta = f"{t.get('size') or 0} empresas · {t.get('size_recent') or 0} na semana · ritmo {growth}"
        names = (f"<div style=\"font-size:12px;color:#555;margin-top:3px\">ex.: "
                 f"{comp.esc(', '.join(examples))}</div>") if examples else ""
        out += _box(f"<b style=\"font-size:14px\">{comp.esc(t.get('label') or 'tema sem nome')}</b>"
                    f"<div style=\"font-size:12px;color:{comp.MUTED}\">{comp.esc(meta)}</div>{names}")
    return out


def _movers(conn, cfg: dict) -> str:
    movers = db.traction_movers(conn, db.days_ago_iso(7))
    if not movers:
        return ""
    companies = {c["id"]: c for c in db.companies_by_ids(conn, [m["company_id"] for m in movers])}
    out = ""
    for m in movers:
        c = companies.get(m["company_id"])
        if c is None:
            continue
        note = (f"tração {comp.fmt_grade(m['traction_before'])} → {comp.fmt_grade(m['traction_now'])}"
                f" · score {comp.fmt_score(m.get('total_before')) or '?'} → "
                f"{comp.fmt_score(m.get('total_now')) or '?'}")
        out += comp.company_card(c, db.latest_score(conn, c["id"], "prior"),
                                 db.latest_extraction(conn, c["id"]), cfg, note=note)
    return out


def _weekly_kit(conn, cfg: dict) -> str:
    for d in db.dossiers_since(conn, db.days_ago_iso(7)):
        dos = d["dossier"]
        if dos.get("kit_reason") == "weekly_best":
            card = comp.kit_card(d, cfg)
            if card:
                return card
    return ""


def _queue(conn) -> str:
    rows = db.queued_companies(conn, 5)
    if not rows:
        return ""
    items = "".join(
        f"<tr><td style=\"padding:3px 10px 3px 0\">{comp.esc(c['name'])}</td>"
        f"<td style=\"padding:3px 0;color:{comp.MUTED};white-space:nowrap\">prioridade "
        f"{comp.esc(comp.fmt_score(c['queue_priority']) or '—')}</td></tr>" for c in rows)
    return f"<table style=\"font-size:13px;color:#333;border-collapse:collapse\">{items}</table>"


def _feedback(conn) -> str:
    summary = db.feedback_summary(conn, db.days_ago_iso(7))
    total = sum(summary.values())
    if not total:
        return comp.muted("Nenhum sinal seu esta semana — o score não aprende sem você.")
    parts = [f"{SIGNAL_LABEL.get(k, k)}: {v}" for k, v in sorted(summary.items(), key=lambda kv: -kv[1])]
    return (f"<div style=\"font-size:13px;color:#333\">{total} sinal(is) seu(s) esta semana — "
            f"{comp.esc(' · '.join(parts))}</div>")


def _health(conn) -> str:
    runs = db.recent_runs(conn, 7, "rodar")
    if not runs:
        return ""
    totals: dict[str, int] = {}
    for r in runs:
        for source, n in (r.get("source_counts") or {}).items():
            try:
                totals[source] = totals.get(source, 0) + int(n or 0)
            except (TypeError, ValueError):
                continue
    cost = db.cost_since(conn, db.days_ago_iso(7))  # inclui investigar/tendências avulsos
    errors = sum(len(r.get("errors") or []) for r in runs)
    lines = [f"{len(runs)} execução(ões) · custo da semana US${cost:.2f}"
             + (f" · {errors} erro(s) de etapa" if errors else "")]
    if totals:
        lines.append(" · ".join(f"{k} {v}" for k, v in sorted(totals.items(), key=lambda kv: -kv[1])))
        zeros = [k for k, v in totals.items() if v == 0]
        if zeros:
            lines.append("⚠️ sem itens na semana: " + ", ".join(sorted(zeros)))
    return "".join(f"<div style=\"font-size:13px;color:#333;margin:2px 0\">{comp.esc(x)}</div>"
                   for x in lines)


def build(conn, cfg: dict, now: datetime | None = None) -> tuple[str, str]:
    """Monta o e-mail semanal. Devolve (assunto, html)."""
    now = now or datetime.now(timezone.utc)
    body = (section("🔥 Temas em alta", _trends(conn, now))
            + section("🚀 Quem ganhou tração", _movers(conn, cfg))
            + section("🧰 Kit da semana", _weekly_kit(conn, cfg))
            + section("⏳ Fila de escavação", _queue(conn))
            + section("🗳️ Seu feedback", _feedback(conn))
            + section("🩺 Saúde", _health(conn)))
    if body.count("<h2") <= 1:  # só a seção de feedback
        body = comp.muted("Semana tranquila: pouco movimento para resumir.") + body
    day = now.astimezone(BRT).strftime("%d/%m")
    subject = f"📊 Garimpo semanal — {day}"
    return subject, comp.page(subject, body)
