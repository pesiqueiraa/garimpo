"""E-mail diário: destaques novos, dossiês prontos, promovidas e a saúde da execução.

Seção sem conteúdo não aparece. Melhor 6 cards bons que 30 medianos: o score
prévio decide o corte (fontes.entrega.top_n).
"""
from datetime import datetime, timedelta, timezone

from app import db
from app.config import get
from app.deliver import components as comp

BRT = timezone(timedelta(hours=-3))
WINDOW_DAYS = 26 / 24


def section(title: str, body: str) -> str:
    return comp.h2(title) + body if body else ""


def _with_context(conn, d: dict) -> dict:
    company_id = comp.as_dict(d.get("company")).get("id")
    if not company_id:
        return d
    score = db.latest_score(conn, company_id, "posterior") or db.latest_score(conn, company_id, "prior")
    return {**d, "score": score or {}}


def highlights(conn, cfg: dict, since: str, skip_ids: set) -> tuple[list, list]:
    """(cards das melhores novas, empresas novas). Só com score prévio e sem descarte."""
    fresh = db.companies_seen_since(conn, since)
    scores = db.latest_scores(conn, "prior")
    ranked = []
    for c in fresh:
        s = scores.get(c["id"])
        if not s or s.get("total") is None or s.get("kill_reason") or c["status"] == "discarded":
            continue
        if c["id"] in skip_ids:
            continue
        ranked.append((s["total"], c, s))
    ranked.sort(key=lambda r: (r[0], r[1]["id"]), reverse=True)
    top_n = int(get(cfg, "fontes.entrega.top_n", 6))
    cards = [comp.company_card(c, s, db.latest_extraction(conn, c["id"]), cfg)
             for _, c, s in ranked[:top_n]]
    return cards, fresh


def _queue_fallback(conn, cfg: dict) -> str:
    rows = db.queued_companies(conn, 3)
    line = comp.muted("Nada novo acima do corte hoje. O garimpo continua amanhã.")
    if not rows:
        return line
    scores = db.latest_scores(conn, "prior")
    cards = "".join(comp.company_card(c, scores.get(c["id"]), db.latest_extraction(conn, c["id"]), cfg)
                    for c in rows)
    return line + comp.muted("Enquanto isso, as próximas da fila de escavação:") + cards


def _dossiers(conn, cfg: dict, items: list[dict]) -> str:
    out = ""
    for d in items:
        try:
            if comp.as_dict(d.get("dossier")).get("issue_number"):
                out += comp.dossier_summary(d, cfg)
            else:
                out += comp.dossier_full(_with_context(conn, d))
        except Exception as e:  # um dossiê malformado não derruba o e-mail
            print(f"[entrega] dossiê {comp.as_dict(d.get('dossier')).get('id')} não renderizou: {e}")
    return out


def _promoted(conn, cfg: dict, since: str) -> str:
    rows = db.promoted_since(conn, since)
    if not rows:
        return ""
    scores = db.latest_scores(conn, "prior")
    return "".join(
        comp.company_card(c, scores.get(c["id"]), db.latest_extraction(conn, c["id"]), cfg,
                          note=f"↑ {c['status_reason']}" if c["status_reason"] else "↑ promovida")
        for c in rows)


def _month_start_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0,
                                                microsecond=0).isoformat(timespec="seconds")


def _usd(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def footer(conn, run_info: dict, fresh: list, now: datetime) -> str:
    """Alertas, custo e contagens da execução."""
    parts = []
    alerts = [a for a in run_info.get("alerts") or [] if a]
    if alerts:
        items = "".join(f"<div style=\"margin:2px 0;color:#8a4b00\">{comp.esc(a)}</div>" for a in alerts)
        parts.append(f"<div style=\"margin-bottom:8px\"><b>Saúde da execução</b>{items}</div>")
    run_cost = _usd((run_info.get("usage") or {}).get("usd"))
    month = db.cost_since(conn, _month_start_iso(now)) + run_cost
    parts.append(f"<div>Custo desta execução: US${run_cost:.4f} · no mês: US${month:.2f}</div>")
    auto = sum(1 for c in fresh if c["status"] == "discarded"
               and str(c["status_reason"] or "").startswith("descarte"))
    counts = [f"{len(fresh)} empresa(s) nova(s)"]
    if auto:
        counts.append(f"{auto} descartada(s) automaticamente")
    sources = run_info.get("source_counts") or {}
    if isinstance(sources, dict) and sources:
        counts.append(", ".join(f"{comp.esc(k)} {comp.esc(v)}" for k, v in sorted(sources.items())))
    parts.append(f"<div>{' · '.join(counts)}</div>")
    return "".join(parts)


def build(conn, cfg: dict, run_info: dict | None = None, now: datetime | None = None) -> tuple[str, str]:
    """Monta o e-mail diário. Devolve (assunto, html)."""
    run_info = run_info or {}
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=WINDOW_DAYS)).isoformat(timespec="seconds")

    dossiers = db.dossiers_since(conn, since)
    # quem já aparece como dossiê ou promovida não repete nos destaques
    shown = {comp.as_dict(d.get("company")).get("id") for d in dossiers}
    shown |= {c["id"] for c in db.promoted_since(conn, since)}
    cards, fresh = highlights(conn, cfg, since, shown)
    top = "".join(cards) if cards else _queue_fallback(conn, cfg)

    count_line = comp.muted(f"{len(fresh)} empresa(s) nova(s) nas últimas 24h"
                            + (f" · as {len(cards)} melhores abaixo" if cards else ""))
    body = (count_line
            + section("⭐ Destaques de hoje", top)
            + section("🔬 Dossiês prontos", _dossiers(conn, cfg, dossiers))
            + section("📈 Promovidas", _promoted(conn, cfg, since)))

    day = now.astimezone(BRT).strftime("%d/%m")
    k = len(dossiers)
    subject = f"⛏️ Garimpo — {day} · {len(fresh)} novidades · {k} dossiê{'s' if k != 1 else ''}"
    html = comp.page(f"⛏️ Garimpo diário — {day}", body, footer(conn, run_info, fresh, now))
    return subject, html
