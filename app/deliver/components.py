"""Peças de HTML do e-mail (estilo inline, legível no celular).

Tudo que vem do LLM, de scraping ou do banco passa por `esc` (html.escape) e
URLs só entram se forem http(s) — nomes, reviews e links são entrada hostil.
Cada peça degrada bem: campo ausente some, nunca vira card vazio ou quebrado.
Os helpers `*_items` normalizam o JSON dos dossiês e são reaproveitados pelo
renderizador de Markdown das Issues (deliver/issues.py).
"""
import html
import re

FONT = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MUTED = "#777"
LINK = "#0b57d0"

VERDICT_LABEL = {
    "sem_concorrente": ("🟢 Sem concorrente BR detectado", "#0a7a33"),
    "concorrente_fraco": ("🟡 Concorrente BR fraco", "#a07800"),
    "concorrente_forte": ("🔴 Concorrente BR forte", "#b3261e"),
    "desconhecido": ("⚪ Concorrência BR desconhecida", "#666"),
}
SUBSCORE_LABEL = (("traction", "Tração"), ("tropicalizability", "Trop."), ("timing", "Timing"),
                  ("fit", "Fit"), ("gap", "Gap"))
STATUS_LABEL = {"new": "nova", "watching": "acompanhando", "queued": "na fila",
                "dug": "escavada", "discarded": "descartada", "expired": "expirada",
                "promoted": "promovida", "validating": "validando", "building": "construindo"}
SEVERITY_COLOR = {"alta": "#b3261e", "media": "#a07800", "média": "#a07800", "baixa": "#666"}
COUNTRY_FLAG = {"br": "🇧🇷", "us": "🇺🇸", "mx": "🇲🇽", "co": "🇨🇴", "ar": "🇦🇷", "cl": "🇨🇱",
                "pe": "🇵🇪", "uy": "🇺🇾", "es": "🇪🇸"}
STORE_LABEL = {"play": "Play Store", "appstore": "App Store"}
FEEDBACK_ICONS = (("like", "👍", "curti"), ("dislike", "👎", "não curti"),
                  ("watch", "👀", "acompanhar"))


# ---------- utilitários ----------

def esc(value) -> str:
    """Texto seguro para HTML; None vira vazio."""
    return "" if value is None else html.escape(str(value), quote=True)


def safe_url(url) -> str | None:
    """Só http(s), sem espaços nem aspas. None se não servir como link."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not re.match(r"^https?://[^\s<>\"']+$", url, re.IGNORECASE):
        return None
    return url


def as_list(value) -> list:
    return value if isinstance(value, list) else []


def as_dict(obj) -> dict:
    """sqlite3.Row, dict ou None → dict."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    try:
        return dict(obj)
    except (TypeError, ValueError):
        return {}


def link(url, text, style: str = "") -> str:
    """<a> com texto escapado; sem URL válida, só o texto."""
    href = safe_url(url)
    label = esc(text)
    if not href:
        return label
    return f"<a href=\"{esc(href)}\" style=\"{style or 'color:' + LINK}\">{label}</a>"


def fmt_grade(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def fmt_score(value) -> str | None:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return None


def fmt_int(value) -> str | None:
    try:
        return f"{int(float(value)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return None


def h2(title: str) -> str:
    return (f"<h2 style=\"font-family:{FONT};font-size:17px;color:#111;"
            f"margin:26px 0 10px\">{title}</h2>")


def h3(title: str) -> str:
    return (f"<div style=\"font-size:11px;font-weight:700;color:#888;letter-spacing:.04em;"
            f"text-transform:uppercase;margin:14px 0 4px\">{title}</div>")


def muted(text_html: str) -> str:
    return f"<p style=\"font-size:13px;color:{MUTED};margin:6px 0\">{text_html}</p>"


def score_badge(total) -> str:
    value = fmt_score(total)
    if value is None:
        return ""
    return (f"<span style=\"float:right;background:#111;color:#fff;border-radius:12px;"
            f"padding:2px 10px;font-size:12px;font-weight:600;margin-left:8px\">{value}/10</span>")


def subscore_line(subscores) -> str:
    """'Tração 4 · Trop. 3 · Timing 3 · Fit 4 · Gap 3' (texto puro, já seguro)."""
    if not isinstance(subscores, dict):
        return ""
    parts = [f"{label} {fmt_grade(subscores[key])}" for key, label in SUBSCORE_LABEL
             if subscores.get(key) is not None]
    return " · ".join(parts)


def score_evidence(score, limit: int | None = None) -> list[str]:
    """Evidências do score sem os catalisadores (que já saem como chips/seção própria)."""
    s = score if isinstance(score, dict) else {}
    items = [str(e) for e in as_list(s.get("evidence")) if e and not str(e).startswith("catalisador:")]
    return items[:limit] if limit else items


def catalyst_names(items, cfg: dict | None = None) -> list[str]:
    """Ids (ou dicts) de catalisadores → nomes legíveis."""
    if not isinstance(items, list) or not items:
        return []
    if cfg is None:
        from app import config
        try:
            cfg = config.load()
        except Exception:
            cfg = {}
    known = {str(c.get("id")): c.get("nome") or c.get("id")
             for c in (cfg or {}).get("catalisadores") or [] if isinstance(c, dict)}
    names = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("nome") or item.get("name") or item.get("id")
        else:
            name = known.get(str(item), item)
        if name:
            names.append(str(name))
    return names


def chips(names: list[str]) -> str:
    return "".join(
        f"<span style=\"display:inline-block;background:#eef3fe;color:#1a3d8f;border-radius:10px;"
        f"padding:1px 8px;font-size:11px;margin:4px 4px 0 0\">⏰ {esc(n)}</span>" for n in names)


def first_paragraph(text, limit: int = 600) -> str:
    """Primeiro parágrafo do texto (corta em `limit` caracteres)."""
    if not text:
        return ""
    for part in re.split(r"\n\s*\n", str(text).strip()):
        part = part.strip()
        if part:
            return part if len(part) <= limit else part[:limit].rsplit(" ", 1)[0] + "…"
    return ""


# ---------- normalização dos campos JSON do dossiê ----------

def evidence_items(dossier: dict, limit: int | None = None) -> list[dict]:
    """br_evidence → [{title, url, why}] com url só se http(s)."""
    out = []
    for e in as_list(dossier.get("br_evidence")):
        if isinstance(e, dict):
            url = safe_url(e.get("url"))
            title = str(e.get("title") or e.get("url") or "").strip()
            if title or url:
                out.append({"title": title or url, "url": url, "why": str(e.get("why") or "").strip()})
        elif isinstance(e, str) and e.strip():
            out.append({"title": e.strip(), "url": safe_url(e), "why": ""})
    return out[:limit] if limit else out


def latam_items(dossier: dict) -> list[dict]:
    """latam → [{country, name, url}] (aceita lista ou {país: [...]})."""
    raw = dossier.get("latam")
    rows: list = []
    if isinstance(raw, dict):
        for country, items in raw.items():
            for item in as_list(items):
                if isinstance(item, dict):
                    rows.append({"country": country, **item})
    elif isinstance(raw, list):
        rows = [r for r in raw if isinstance(r, dict)]
    out = []
    for r in rows:
        name = str(r.get("name") or r.get("title") or "").strip()
        url = safe_url(r.get("url") or r.get("link"))
        if name or url:
            out.append({"country": str(r.get("country") or "").lower(), "name": name or url,
                        "url": url})
    return out


def complaint_items(dossier: dict) -> list[dict]:
    """web_complaints → [{source, title, url, snippet}]."""
    raw = dossier.get("web_complaints")
    out = []
    for r in as_list(raw):
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or "").strip()
        url = safe_url(r.get("url") or r.get("link"))
        if title or url:
            out.append({"source": str(r.get("source") or "").strip(), "title": title or url,
                        "url": url, "snippet": str(r.get("snippet") or "").strip()[:240]})
    return out


def tam_info(dossier: dict) -> dict | None:
    """tam → {count, cnae, segment, ufs: [str], note} ou None se não houver número."""
    raw = dossier.get("tam")
    if not isinstance(raw, dict):
        return None
    count = fmt_int(raw.get("count"))
    if count is None and not raw.get("note"):
        return None
    ufs = []
    for u in as_list(raw.get("top_ufs")):
        if isinstance(u, str):
            ufs.append(u)
        elif isinstance(u, (list, tuple)) and u:
            ufs.append(str(u[0]))
        elif isinstance(u, dict) and (u.get("uf") or u.get("name")):
            ufs.append(str(u.get("uf") or u.get("name")))
    return {"count": count, "cnae": str(raw.get("cnae") or "").strip(),
            "segment": str(raw.get("segment") or "").strip(), "ufs": ufs[:5],
            "note": str(raw.get("note") or "").strip()}


def pain_examples(cluster: dict, limit: int = 2) -> list[dict]:
    """Trechos de review de um tema de dor → [{excerpt, app}]."""
    out = []
    for e in as_list(cluster.get("examples")):
        if isinstance(e, dict) and (e.get("excerpt") or e.get("text")):
            out.append({"excerpt": str(e.get("excerpt") or e.get("text"))[:240],
                        "app": str(e.get("app") or "").strip()})
        elif isinstance(e, str) and e.strip():
            out.append({"excerpt": e.strip()[:240], "app": ""})
    return out[:limit]


def kit_info(kit) -> dict | None:
    """kit → {headline, subheadline, benefits, cta, mom_test, whatsapp, where_to_find} ou None."""
    if not isinstance(kit, dict):
        return None
    landing = kit.get("landing") if isinstance(kit.get("landing"), dict) else {}
    where = kit.get("where_to_find")
    if isinstance(where, str):
        where = [where]
    places = []
    for w in as_list(where):
        if isinstance(w, dict):
            name = str(w.get("name") or w.get("where") or w.get("place") or w.get("channel")
                       or w.get("title") or "").strip()
            why = str(w.get("why") or w.get("how") or w.get("note") or "").strip()
            url = safe_url(w.get("url"))
            if name or url:
                places.append({"name": name or url, "why": why, "url": url})
        elif isinstance(w, str) and w.strip():
            places.append({"name": w.strip(), "why": "", "url": None})
    info = {
        "headline": str(landing.get("headline") or "").strip(),
        "subheadline": str(landing.get("subheadline") or "").strip(),
        "benefits": [str(b) for b in as_list(landing.get("benefits")) if b][:5],
        "cta": str(landing.get("cta") or "").strip(),
        "mom_test": [str(q) for q in as_list(kit.get("mom_test")) if q][:8],
        "whatsapp": str(kit.get("whatsapp") or "").strip(),
        "where_to_find": places,
    }
    if not any((info["headline"], info["mom_test"], info["whatsapp"], places)):
        return None
    return info


def verdict_label(verdict) -> tuple[str, str] | None:
    if not verdict:
        return None
    return VERDICT_LABEL.get(str(verdict), (str(verdict), "#666"))


def dossier_score(dossier: dict):
    return dossier.get("score_posterior") if dossier.get("score_posterior") is not None \
        else dossier.get("score_prior")


# ---------- blocos ----------

def kit_block(kit) -> str:
    """Kit de validação em HTML; vazio se não houver kit utilizável."""
    k = kit_info(kit)
    if not k:
        return ""
    parts = []
    if k["headline"]:
        parts.append(f"<div style=\"font-size:13px\"><b>Landing:</b> {esc(k['headline'])}</div>")
    if k["subheadline"]:
        parts.append(f"<div style=\"font-size:12px;color:#444\">{esc(k['subheadline'])}</div>")
    if k["benefits"]:
        items = "".join(f"<li style=\"margin:2px 0\">{esc(b)}</li>" for b in k["benefits"])
        parts.append(f"<ul style=\"font-size:12px;color:#444;margin:4px 0 4px 18px;padding:0\">{items}</ul>")
    if k["cta"]:
        parts.append(f"<div style=\"font-size:12px\"><b>CTA:</b> {esc(k['cta'])}</div>")
    if k["mom_test"]:
        items = "".join(f"<li style=\"margin:2px 0\">{esc(q)}</li>" for q in k["mom_test"])
        parts.append("<div style=\"font-size:13px;margin-top:8px\"><b>Perguntas (Mom Test):</b></div>"
                     f"<ol style=\"font-size:12px;color:#444;margin:4px 0 4px 18px;padding:0\">{items}</ol>")
    if k["whatsapp"]:
        parts.append("<div style=\"font-size:13px;margin-top:8px\"><b>Abordagem WhatsApp:</b></div>"
                     "<div style=\"font-size:12px;color:#444;font-style:italic;border-left:3px solid #ddd;"
                     f"padding-left:8px\">{esc(k['whatsapp'])}</div>")
    if k["where_to_find"]:
        items = "".join(
            f"<li style=\"margin:2px 0\">{link(w['url'], w['name'])}"
            f"{' — ' + esc(w['why']) if w['why'] else ''}</li>" for w in k["where_to_find"])
        parts.append("<div style=\"font-size:13px;margin-top:8px\"><b>Onde achar os primeiros clientes:</b></div>"
                     f"<ul style=\"font-size:12px;color:#444;margin:4px 0 4px 18px;padding:0\">{items}</ul>")
    return ("<div style=\"background:#fffbea;border:1px solid #f0e2a8;border-radius:8px;"
            f"padding:10px 12px;margin-top:6px\">{''.join(parts)}</div>")


def _feedback_links(cfg: dict, company_id) -> str:
    from app import feedback  # import tardio: feedback importa deliver.github
    links = []
    for signal, icon, title in FEEDBACK_ICONS:
        url = feedback.feedback_link(cfg, company_id, signal)
        if url:
            links.append(f"<a href=\"{esc(url)}\" title=\"{title}\" style=\"text-decoration:none;"
                         f"font-size:18px;margin-right:14px\">{icon}</a>")
    if not links:
        return ""
    return f"<div style=\"margin-top:8px\">{''.join(links)}</div>"


def company_card(company, score, facts, cfg: dict, feedback: bool = True,
                 note: str | None = None) -> str:
    """Card de uma empresa: nome, resumo, nota, subnotas, evidências, catalisadores, status."""
    c = as_dict(company)
    s = score if isinstance(score, dict) else {}
    f = facts if isinstance(facts, dict) else {}
    name = c.get("name") or "(sem nome)"
    title = link(c.get("url"), name, "font-size:16px;font-weight:700;color:#111;text-decoration:none")
    if not safe_url(c.get("url")):
        title = f"<span style=\"font-size:16px;font-weight:700;color:#111\">{esc(name)}</span>"
    summary = f.get("summary_pt") or c.get("tagline") or ""

    lines = []
    if summary:
        lines.append(f"<div style=\"font-size:14px;color:#333;margin-top:3px\">{esc(summary)}</div>")
    meta = [str(x) for x in (f.get("niche"), f.get("br_customer_segment")) if x]
    status = c.get("status")
    if status and status != "new":
        meta.append(STATUS_LABEL.get(status, status))
    if meta:
        lines.append(f"<div style=\"font-size:12px;color:{MUTED};margin-top:4px\">"
                     f"{esc(' · '.join(meta))}</div>")
    subs = subscore_line(s.get("subscores"))
    if subs and s.get("total") is not None:
        lines.append(f"<div style=\"font-size:12px;color:#444;margin-top:6px\">{esc(subs)}</div>")
    evidence = score_evidence(s, 3)
    if evidence:
        items = "".join(f"<li style=\"margin:1px 0\">{esc(e)}</li>" for e in evidence)
        lines.append(f"<ul style=\"font-size:12px;color:#555;margin:4px 0 0 16px;padding:0\">{items}</ul>")
    names = catalyst_names(s.get("catalysts"), cfg)
    if names:
        lines.append(f"<div>{chips(names)}</div>")
    if note:
        lines.append(f"<div style=\"font-size:12px;color:#0a7a33;margin-top:6px\">{esc(note)}</div>")
    if feedback and c.get("id") is not None:
        lines.append(_feedback_links(cfg, c.get("id")))

    return ("<div style=\"border:1px solid #e3e3e3;border-radius:10px;padding:14px 16px;"
            f"margin-bottom:10px;background:#fff;font-family:{FONT}\">"
            f"{score_badge(s.get('total')) if s else ''}{title}{''.join(lines)}</div>")


def _verdict_html(dos: dict, max_links: int = 3) -> str:
    label = verdict_label(dos.get("br_verdict"))
    if not label:
        return muted("palpite do modelo — sem busca real")
    text, color = label
    out = f"<div style=\"font-size:13px;font-weight:600;color:{color}\">{esc(text)}</div>"
    if dos.get("br_rationale"):
        out += f"<div style=\"font-size:12px;color:#444;margin:2px 0\">{esc(dos['br_rationale'])}</div>"
    for e in evidence_items(dos, max_links):
        why = f" — {esc(e['why'])}" if e["why"] else ""
        out += (f"<div style=\"font-size:12px;margin:2px 0 2px 12px\">"
                f"{link(e['url'], e['title'])}{why}</div>")
    return out


def _pains_html(clusters: list, limit: int | None = None, examples: int = 2) -> str:
    out = ""
    valid = [cl for cl in as_list(clusters) if isinstance(cl, dict) and cl.get("theme")]
    for cl in valid[:limit] if limit else valid:
        sev = str(cl.get("severity") or "")
        color = SEVERITY_COLOR.get(sev, "#666")
        meta = " · ".join(x for x in (f"{fmt_int(cl.get('frequency')) or 0}×", sev) if x)
        quotes = "".join(
            f"<div style=\"font-size:12px;color:#666;font-style:italic;margin:2px 0 2px 12px\">"
            f"“{esc(e['excerpt'])}”{' — ' + esc(e['app']) if e['app'] else ''}</div>"
            for e in pain_examples(cl, examples))
        out += (f"<div style=\"margin:6px 0\"><b style=\"font-size:13px\">{esc(cl['theme'])}</b> "
                f"<span style=\"color:{color};font-size:12px\">({esc(meta)})</span>{quotes}</div>")
    return out


def _tam_line(dos: dict) -> str:
    t = tam_info(dos)
    if not t:
        return ""
    parts = []
    if t["count"]:
        base = f"~{t['count']} empresas ativas"
        if t["cnae"]:
            base += f" no CNAE {t['cnae']}"
        if t["segment"]:
            base += f" ({t['segment']})"
        parts.append(base)
    if t["ufs"]:
        parts.append("principais UFs: " + ", ".join(t["ufs"]))
    text = " · ".join(parts)
    note = f"<div style=\"font-size:11px;color:{MUTED}\">{esc(t['note'])}</div>" if t["note"] else ""
    return (f"<div style=\"font-size:13px;color:#333\">📊 {esc(text)} "
            f"<span style=\"font-size:11px;color:{MUTED}\">(estimativa)</span></div>{note}")


def _header(d: dict, size: int = 17) -> str:
    c = as_dict(d.get("company"))
    dos = as_dict(d.get("dossier"))
    name = c.get("name") or "(sem nome)"
    if safe_url(c.get("url")):
        title = link(c.get("url"), name,
                     f"font-size:{size}px;font-weight:700;color:#111;text-decoration:none")
    else:
        title = f"<span style=\"font-size:{size}px;font-weight:700;color:#111\">{esc(name)}</span>"
    tagline = (f"<div style=\"font-size:14px;color:#333;margin-top:3px\">{esc(c['tagline'])}</div>"
               if c.get("tagline") else "")
    return f"{score_badge(dossier_score(dos))}{title}{tagline}"


def _counts_line(dos: dict, competitors: int = 0) -> str:
    parts = []
    latam = latam_items(dos)
    if latam:
        parts.append(f"🌎 {len(latam)} parecido(s) na América Latina")
    complaints = complaint_items(dos)
    if complaints:
        parts.append(f"💬 {len(complaints)} reclamação(ões) na web")
    if competitors:
        parts.append(f"📱 {competitors} concorrente(s) nas lojas")
    if not parts:
        return ""
    return f"<div style=\"font-size:12px;color:#555;margin-top:8px\">{esc(' · '.join(parts))}</div>"


def dossier_summary(d: dict, cfg: dict) -> str:
    """Resumo do dossiê para o e-mail quando a versão completa está na Issue."""
    dos = as_dict(d.get("dossier"))
    blocks = [_header(d)]
    blocks.append(h3("🇧🇷 Veredito de concorrência") + _verdict_html(dos, 3))
    pains = _pains_html(d.get("pain_clusters") or [], limit=3, examples=1)
    if pains:
        blocks.append(h3("Dores mais citadas") + pains)
    angle = first_paragraph(dos.get("attack_angle"))
    if angle:
        blocks.append(h3("⚔️ Ângulo de ataque")
                      + f"<div style=\"font-size:13px;color:#222;line-height:1.5\">{esc(angle)}</div>")
    counts = _counts_line(dos, len(d.get("app_competitors") or []))
    if counts:
        blocks.append(counts)
    tam = _tam_line(dos)
    if tam:
        blocks.append(f"<div style=\"margin-top:6px\">{tam}</div>")
    kit = kit_block(dos.get("kit"))
    if kit:
        blocks.append(h3("🧰 Kit de validação") + kit)
    blocks.append(_issue_button(cfg, dos))
    return _dossier_box(blocks)


def kit_card(d: dict, cfg: dict) -> str:
    """Só o kit de um dossiê (e-mail semanal); vazio se não houver kit."""
    dos = as_dict(d.get("dossier"))
    kit = kit_block(dos.get("kit"))
    if not kit:
        return ""
    angle = first_paragraph(dos.get("attack_angle"), 300)
    blocks = [_header(d)]
    if angle:
        blocks.append(f"<div style=\"font-size:13px;color:#222;margin-top:6px\">{esc(angle)}</div>")
    blocks += [h3("🧰 Kit de validação") + kit, _issue_button(cfg, dos)]
    return _dossier_box(blocks)


def _issue_button(cfg: dict, dos: dict) -> str:
    from app.deliver import github
    url = github.issue_url(cfg, dos.get("issue_number"))
    if not url:
        return ""
    return (f"<div style=\"margin-top:12px\"><a href=\"{esc(url)}\" style=\"display:inline-block;"
            "background:#111;color:#fff;text-decoration:none;border-radius:8px;"
            "padding:8px 14px;font-size:13px;font-weight:600\">Abrir dossiê completo →</a></div>")


def _dossier_box(blocks: list[str]) -> str:
    return ("<div style=\"border:2px solid #111;border-radius:12px;padding:16px;margin-bottom:14px;"
            f"background:#fff;font-family:{FONT}\">{''.join(b for b in blocks if b)}</div>")


def _app_competitors_html(rows: list) -> str:
    lines = ""
    for r in rows or []:
        r = as_dict(r)
        if not r.get("name"):
            continue
        store = STORE_LABEL.get(r.get("store"), r.get("store") or "")
        flag = COUNTRY_FLAG.get(str(r.get("country") or "").lower(), r.get("country") or "")
        rating = fmt_grade(r.get("rating")) if r.get("rating") is not None else "—"
        count = fmt_int(r.get("ratings_count"))
        lines += (f"<tr><td style=\"padding:3px 10px 3px 0\">{esc(r['name'])}</td>"
                  f"<td style=\"padding:3px 8px;white-space:nowrap\">{esc(flag)} {esc(store)}</td>"
                  f"<td style=\"padding:3px 8px\">{esc(rating)}★</td>"
                  f"<td style=\"padding:3px 0 3px 8px;color:{MUTED}\">{esc(count or '?')} aval.</td></tr>")
    if not lines:
        return ""
    return f"<table style=\"font-size:12px;color:#333;border-collapse:collapse\">{lines}</table>"


def _br_competitors_html(rows: list) -> str:
    out = ""
    for r in rows or []:
        r = as_dict(r)
        if not r.get("name"):
            continue
        domain = r.get("domain")
        name = link(f"https://{domain}", r["name"]) if domain and safe_url(f"https://{domain}") \
            else esc(r["name"])
        extra = " · ".join(str(x) for x in (r.get("niche"), r.get("strength")) if x)
        tail = f" <span style=\"color:{MUTED};font-size:12px\">({esc(extra)})</span>" if extra else ""
        out += f"<div style=\"font-size:13px;margin:2px 0\">{name}{tail}</div>"
    return out


def _complaint_html(x: dict) -> str:
    source = f" <span style=\"color:{MUTED}\">({esc(x['source'])})</span>" if x["source"] else ""
    snippet = (f"<div style=\"color:#666;font-style:italic\">{esc(x['snippet'])}</div>"
               if x["snippet"] else "")
    return f"<div style=\"font-size:12px;margin:4px 0\">{link(x['url'], x['title'])}{source}{snippet}</div>"


def dossier_full(d: dict) -> str:
    """Dossiê completo em HTML (e-mail sem Issue e página do `investigar`)."""
    dos = as_dict(d.get("dossier"))
    score = d.get("score") if isinstance(d.get("score"), dict) else {}
    blocks = [_header(d)]

    prior, post = fmt_score(dos.get("score_prior")), fmt_score(dos.get("score_posterior"))
    score_parts = []
    if prior:
        score_parts.append(f"prévio {prior}")
    if post:
        score_parts.append(f"posterior {post}")
    subs = subscore_line(dos.get("subscores"))
    if score_parts or subs:
        blocks.append(h3("Score")
                      + f"<div style=\"font-size:13px;color:#333\">{esc(' → '.join(score_parts))}</div>"
                      + (f"<div style=\"font-size:12px;color:#555\">{esc(subs)}</div>" if subs else ""))
    evidence = score_evidence(score, 6)
    if evidence:
        items = "".join(f"<li>{esc(e)}</li>" for e in evidence)
        blocks.append(h3("Evidências do score")
                      + f"<ul style=\"font-size:12px;color:#444;margin:2px 0 0 16px;padding:0\">{items}</ul>")

    blocks.append(h3("🇧🇷 Veredito de concorrência") + _verdict_html(dos, 5))
    br = _br_competitors_html(d.get("br_competitors") or [])
    if br:
        blocks.append(h3("Concorrentes no Brasil") + br)
    apps = _app_competitors_html(d.get("app_competitors") or [])
    if apps:
        blocks.append(h3("Concorrentes nas lojas") + apps)
    latam = latam_items(dos)
    if latam:
        items = "".join(
            f"<div style=\"font-size:12px;margin:2px 0\">{esc(COUNTRY_FLAG.get(x['country'], x['country']))} "
            f"{link(x['url'], x['name'])}</div>" for x in latam[:8])
        blocks.append(h3("🌎 América Latina") + items)
    pains = _pains_html(d.get("pain_clusters") or [])
    if pains:
        blocks.append(h3("Mapa de dores") + pains)
    complaints = complaint_items(dos)
    if complaints:
        items = "".join(_complaint_html(x) for x in complaints[:6])
        blocks.append(h3("💬 Reclamações na web") + items)
    tam = _tam_line(dos)
    if tam:
        blocks.append(h3("Tamanho do mercado") + tam)
    names = catalyst_names(dos.get("catalysts"))
    if names:
        blocks.append(h3("Por que agora") + chips(names))
    angle = esc(dos.get("attack_angle") or "").replace("\n", "<br>")
    if angle:
        blocks.append(h3("⚔️ Ângulo de ataque")
                      + f"<div style=\"font-size:13px;color:#222;line-height:1.5\">{angle}</div>")
    kit = kit_block(dos.get("kit"))
    if kit:
        blocks.append(h3("🧰 Kit de validação") + kit)
    return _dossier_box(blocks)


def page(title: str, body_html: str, footer_html: str = "") -> str:
    """Casca do e-mail: HTML inline, largura máxima de 640px."""
    footer = (f"<div style=\"border-top:1px solid #e3e3e3;margin-top:28px;padding-top:12px;"
              f"font-size:12px;color:{MUTED};line-height:1.5\">{footer_html}</div>"
              if footer_html else "")
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title></head>
<body style="margin:0;padding:0;background:#f5f5f5">
<div style="font-family:{FONT};max-width:640px;margin:0 auto;padding:18px 14px;color:#111">
<h1 style="font-size:20px;margin:6px 0 4px">{esc(title)}</h1>
{body_html}
{footer}
<p style="color:#aaa;font-size:11px;margin-top:24px">Gerado automaticamente pelo seu garimpo pessoal.</p>
</div></body></html>"""


def dossier_page(d: dict) -> str:
    """Página avulsa com um único dossiê (comando `investigar`)."""
    name = as_dict(d.get("company")).get("name") or "empresa"
    return page(f"⛏️ Dossiê — {name}", dossier_full(d))
