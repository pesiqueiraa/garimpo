"""Uma Issue do GitHub por dossiê: o dossiê completo vive lá, o e-mail leva o resumo.

A Issue também é o canal de feedback: trocar a label status:* vira sinal do
dono (lido por app/feedback.py). O marcador oculto no corpo liga a Issue ao
dossiê mesmo se o banco perder o issue_number.
"""
import re

from app import db
from app.deliver import components as comp
from app.deliver import github

SINCE_DAYS = 14
MAX_BODY = 60000  # o GitHub recusa corpo acima de 65.536 caracteres
KIT_HEADER = "## 🧰 Kit de validação"


def md(value) -> str:
    """Texto de fonte externa seguro para Markdown do GitHub: sem HTML nem quebra de layout."""
    text = str(value or "").replace("\r", " ").strip()
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    # espaço de largura zero: texto raspado não menciona ninguém (@fulano) nem referencia Issues (#12)
    text = text.replace("@", "@\u200b").replace("#", "#\u200b")
    return re.sub(r"([\\`*_\[\]|])", r"\\\1", text)


def md_link(url, text) -> str:
    label = md(text) or "link"
    href = comp.safe_url(url)
    if not href:
        return label
    return f"[{label}]({href.replace(')', '%29').replace('(', '%28')})"


def _score_section(dos: dict) -> list[str]:
    lines = []
    prior, post = comp.fmt_score(dos.get("score_prior")), comp.fmt_score(dos.get("score_posterior"))
    if prior or post:
        parts = ([f"prévio **{prior}**"] if prior else []) + ([f"posterior **{post}**"] if post else [])
        lines.append("- Score: " + " → ".join(parts) + " (de 0 a 10)")
    subs = comp.subscore_line(dos.get("subscores"))
    if subs:
        lines.append(f"- Subnotas (1 a 5): {subs}")
    if not lines:
        return []
    return ["## Score", *lines, "",
            "_Score é heurístico: subnotas calculadas em código a partir de fatos extraídos._", ""]


def render_issue_body(d: dict, score: dict | None = None) -> str:
    """Dossiê em Markdown para o corpo da Issue."""
    dos = comp.as_dict(d.get("dossier"))
    company = comp.as_dict(d.get("company"))
    if not isinstance(score, dict):
        score = d.get("score") if isinstance(d.get("score"), dict) else {}
    out: list[str] = []

    name = company.get("name") or "(sem nome)"
    out.append(f"# {md_link(company.get('url'), name)}")
    summary = (d.get("facts") or {}).get("summary_pt") if isinstance(d.get("facts"), dict) else None
    if summary or company.get("tagline"):
        out.append(f"> {md(summary or company.get('tagline'))}")
    out.append("")
    out += _score_section(dos)

    evidence = comp.score_evidence(score, 8)
    if evidence:
        out += ["## 📈 Evidências (tração e encaixe)", *[f"- {md(e)}" for e in evidence], ""]

    out.append("## 🇧🇷 Concorrência no Brasil")
    label = comp.verdict_label(dos.get("br_verdict"))
    out.append(f"**{md(label[0])}**" if label else "_Palpite do modelo — sem busca real._")
    if dos.get("br_rationale"):
        out.append(md(dos["br_rationale"]))
    items = comp.evidence_items(dos)
    if items:
        out.append("")
        out += [f"- {md_link(e['url'], e['title'])}" + (f" — {md(e['why'])}" if e["why"] else "")
                for e in items[:8]]
    br = [comp.as_dict(r) for r in d.get("br_competitors") or []]
    br = [r for r in br if r.get("name")]
    if br:
        out += ["", "**Concorrentes brasileiros mapeados:**"]
        for r in br[:10]:
            link = md_link(f"https://{r['domain']}", r["name"]) if r.get("domain") else md(r["name"])
            extra = " · ".join(md(x) for x in (r.get("niche"), r.get("strength")) if x)
            out.append(f"- {link}" + (f" ({extra})" if extra else ""))
    out.append("")

    apps = [comp.as_dict(r) for r in d.get("app_competitors") or []]
    apps = [a for a in apps if a.get("name")]
    if apps:
        out += ["## 📱 Concorrentes nas lojas", "| App | Loja | Nota | Avaliações |", "|---|---|---|---|"]
        for a in apps[:10]:
            store = comp.STORE_LABEL.get(a.get("store"), a.get("store") or "")
            flag = comp.COUNTRY_FLAG.get(str(a.get("country") or "").lower(), a.get("country") or "")
            rating = comp.fmt_grade(a.get("rating")) if a.get("rating") is not None else "—"
            out.append(f"| {md(a['name'])} | {md(flag)} {md(store)} | {rating} | "
                       f"{comp.fmt_int(a.get('ratings_count')) or '?'} |")
        out.append("")

    latam = comp.latam_items(dos)
    if latam:
        out += ["## 🌎 América Latina",
                *[f"- {comp.COUNTRY_FLAG.get(x['country'], md(x['country']))} {md_link(x['url'], x['name'])}"
                  for x in latam[:10]], ""]

    pains = [p for p in d.get("pain_clusters") or [] if isinstance(p, dict) and p.get("theme")]
    if pains:
        out.append("## 😤 Dores nas avaliações")
        for p in pains:
            meta = " · ".join(x for x in (f"{comp.fmt_int(p.get('frequency')) or 0}×",
                                          str(p.get("severity") or "")) if x)
            out.append(f"- **{md(p['theme'])}** ({md(meta)})")
            for e in comp.pain_examples(p, 2):
                out.append(f"  - _“{md(e['excerpt'])}”_" + (f" — {md(e['app'])}" if e["app"] else ""))
        out.append("")

    complaints = comp.complaint_items(dos)
    if complaints:
        out.append("## 💬 Reclamações na web")
        for x in complaints[:10]:
            line = f"- {md_link(x['url'], x['title'])}" + (f" ({md(x['source'])})" if x["source"] else "")
            out.append(line + (f" — {md(x['snippet'])}" if x["snippet"] else ""))
        out.append("")

    tam = comp.tam_info(dos)
    if tam:
        out.append("## 📊 Tamanho do mercado (estimativa)")
        if tam["count"]:
            line = f"- ~{tam['count']} empresas ativas"
            if tam["cnae"]:
                line += f" no CNAE {md(tam['cnae'])}"
            if tam["segment"]:
                line += f" ({md(tam['segment'])})"
            out.append(line)
        if tam["ufs"]:
            out.append("- Principais UFs: " + ", ".join(md(u) for u in tam["ufs"]))
        if tam["note"]:
            out.append(f"- {md(tam['note'])}")
        out += ["", "_Estimativa a partir de dados abertos do CNPJ — não é medição._", ""]

    catalysts = comp.catalyst_names(dos.get("catalysts"))
    if catalysts:
        out += ["## ⏰ Por que agora", *[f"- {md(c)}" for c in catalysts], ""]

    if dos.get("attack_angle"):
        angle = "\n".join(md(line) for line in str(dos["attack_angle"]).splitlines())
        out += ["## ⚔️ Ângulo de ataque", angle, ""]

    kit = render_kit_markdown(dos.get("kit"))
    if kit:
        out += [kit, ""]

    out += ["---",
            "**Como dar feedback:** troque a label `status:novo` por `status:interessante`, "
            "`status:validando`, `status:descartado` ou `status:construindo`. "
            "O Garimpo lê na próxima execução e ajusta o status e o score.",
            "",
            marker(dos, company)]
    text = "\n".join(out)
    if len(text) > MAX_BODY:  # corta o miolo, preserva o marcador
        text = text[:MAX_BODY - 200].rsplit("\n", 1)[0] + "\n\n_(dossiê cortado)_\n\n" + marker(dos, company)
    return text


def marker(dos: dict, company: dict) -> str:
    """Comentário oculto que liga a Issue ao dossiê e à empresa."""
    company_id = int(company.get("id") or dos.get("company_id") or 0)
    return f"<!-- garimpo:dossier={int(dos.get('id') or 0)} company={company_id} -->"


def render_kit_markdown(kit) -> str:
    """Kit de validação em Markdown; vazio se não houver kit."""
    k = comp.kit_info(kit)
    if not k:
        return ""
    out = [KIT_HEADER]
    if k["headline"]:
        out.append(f"**Landing:** {md(k['headline'])}")
    if k["subheadline"]:
        out.append(md(k["subheadline"]))
    if k["benefits"]:
        out += [""] + [f"- {md(b)}" for b in k["benefits"]]
    if k["cta"]:
        out += ["", f"**CTA:** {md(k['cta'])}"]
    if k["mom_test"]:
        out += ["", "**Perguntas (Mom Test):**"] + [f"{i}. {md(q)}" for i, q in enumerate(k["mom_test"], 1)]
    if k["whatsapp"]:
        out += ["", "**Abordagem WhatsApp:**", f"> {md(k['whatsapp'])}"]
    if k["where_to_find"]:
        out += ["", "**Onde achar os primeiros clientes:**"]
        out += [f"- {md_link(w['url'], w['name'])}" + (f" — {md(w['why'])}" if w["why"] else "")
                for w in k["where_to_find"]]
    return "\n".join(out)


def _title(d: dict) -> str:
    name = comp.as_dict(d.get("company")).get("name") or "(sem nome)"
    dos = comp.as_dict(d.get("dossier"))
    score = comp.fmt_score(comp.dossier_score(dos))
    title = f"🔬 {name} — {score}/10" if score else f"🔬 {name}"
    return title.replace("\n", " ")[:250]


def _with_context(conn, d: dict) -> dict:
    """Acrescenta fatos e o score mais recente (evidências de tração) ao dossiê."""
    company_id = comp.as_dict(d.get("company")).get("id") or comp.as_dict(d.get("dossier")).get("company_id")
    if not company_id:
        return d
    try:
        score = (db.latest_score(conn, company_id, "posterior")
                 or db.latest_score(conn, company_id, "prior"))
        return {**d, "facts": db.latest_extraction(conn, company_id) or {}, "score": score or {}}
    except Exception:
        return d


def sync(conn, cfg: dict) -> int:
    """Abre uma Issue para cada dossiê recente sem Issue. Devolve quantas criou."""
    if not github.available(cfg):
        print("[issues] GITHUB_TOKEN ou repositório ausente, pulando Issues")
        return 0
    github.ensure_labels(cfg)
    created = 0
    for d in db.dossiers_without_issue(conn, db.days_ago_iso(SINCE_DAYS)):
        dos = comp.as_dict(d.get("dossier"))
        try:
            issue = github.request("POST", "/repos/{repo}/issues", cfg, json={
                "title": _title(d), "body": render_issue_body(_with_context(conn, d)),
                "labels": ["garimpo-dossie", "status:novo"]})
            number = int((issue or {}).get("number"))
        except Exception as e:
            print(f"[issues] dossiê {dos.get('id')} falhou: {e}")
            continue
        db.set_dossier_issue(conn, dos["id"], number)
        conn.commit()
        created += 1
    print(f"[issues] {created} Issue(s) criada(s)")
    return created


def post_kit_comment(conn, cfg: dict, dossier_id) -> bool:
    """Publica o kit como comentário quando o dossiê já tem Issue."""
    if not dossier_id or not github.available(cfg):
        return False
    d = db.get_dossier(conn, int(dossier_id))
    if not d:
        return False
    dos = d["dossier"]
    body = render_kit_markdown(dos.get("kit"))
    if not dos.get("issue_number") or not body:
        return False
    number = int(dos["issue_number"])
    try:  # Issue criada hoje já nasceu com o kit no corpo: não repete
        issue = github.request("GET", f"/repos/{{repo}}/issues/{number}", cfg)
        if KIT_HEADER in str((issue or {}).get("body") or ""):
            return False
    except Exception as e:
        print(f"[issues] não consegui ler a issue #{number}: {e}")
    try:
        github.request("POST", f"/repos/{{repo}}/issues/{number}/comments", cfg,
                       json={"body": "🧰 Este dossiê ganhou o kit da semana.\n\n" + body})
    except Exception as e:
        print(f"[issues] comentário do kit falhou (dossiê {dossier_id}): {e}")
        return False
    return True
