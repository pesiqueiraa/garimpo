"""Migração 003: copia os dados da v1 (legacy_*) para o modelo da v2.

- cada produto vira empresa (site_key novo; projetos do GitHub deixam de colidir)
- cada fonte em sources_seen vira um evento
- a classificação da v1 vira extração 'legacy_v1' e score 'legacy'
- dossiês, concorrentes da Play Store e dores são copiados e religados à empresa

As tabelas legacy_* continuam intactas para consulta.
"""
import json


def _status(product) -> str:
    return "dug" if product["dig_status"] == "done" else "new"


def run(conn) -> None:
    from app.resolve import normalize_name, site_key

    product_to_company: dict[int, int] = {}
    for p in conn.execute("SELECT * FROM legacy_products ORDER BY id").fetchall():
        key, domain = site_key(p["url"], p["name"])
        existing = conn.execute("SELECT id FROM companies WHERE site_key=?", (key,)).fetchone()
        if existing:
            company_id = existing["id"]
            conn.execute(
                """UPDATE companies SET first_seen=MIN(first_seen, ?), last_seen=MAX(last_seen, ?),
                   status=CASE WHEN ?='dug' THEN 'dug' ELSE status END WHERE id=?""",
                (p["first_seen"], p["last_seen"], _status(p), company_id))
        else:
            company_id = conn.execute(
                """INSERT INTO companies (site_key, domain, name, name_key, tagline, url,
                       first_seen, last_seen, status, status_changed_at, dig_attempts,
                       legacy_product_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, domain, p["name"], normalize_name(p["name"]), p["tagline"], p["url"],
                 p["first_seen"], p["last_seen"], _status(p), p["last_seen"],
                 p["dig_attempts"] or 0, p["id"])).lastrowid
        product_to_company[p["id"]] = company_id

        sources = [s for s in (p["sources_seen"] or p["source"] or "").split(",") if s]
        for source in sources:
            primary = source == p["source"]
            kind = {"yc": "listing", "manual": "manual"}.get(source, "launch")
            conn.execute(
                """INSERT OR IGNORE INTO events (company_id, source, kind, external_id, url, title,
                       points, category, payload, seen_at)
                   VALUES (?,?,?,?,?,?,?,?,NULL,?)""",
                (company_id, source, kind, f"legacy:{p['id']}:{source}",
                 p["source_url"] if primary else None, p["tagline"] or p["name"],
                 (p["points"] or 0) if primary else 0, p["category"], p["first_seen"]))

        if p["niche"] is not None or p["opportunity_score"] is not None:
            data = {"niche": p["niche"], "pain": p["pain"], "audience": p["audience"],
                    "br_competitor_guess": p["br_verdict"],
                    "opportunity_score": p["opportunity_score"]}
            conn.execute(
                """INSERT INTO extractions (company_id, prompt_version, model, data, created_at)
                   VALUES (?, 'legacy_v1', 'gpt-5-mini', ?, ?)""",
                (company_id, json.dumps(data, ensure_ascii=False), p["first_seen"]))
        if p["opportunity_score"] is not None:
            conn.execute(
                """INSERT INTO scores (company_id, kind, total, weights_version, created_at)
                   VALUES (?, 'legacy', ?, 'legacy_v1', ?)""",
                (company_id, p["opportunity_score"], p["first_seen"]))

    product_to_dossier: dict[int, int] = {}
    for d in conn.execute("SELECT * FROM legacy_dossiers ORDER BY id").fetchall():
        company_id = product_to_company.get(d["product_id"])
        if company_id is None:
            continue
        product_to_dossier[d["product_id"]] = conn.execute(
            """INSERT INTO dossiers (company_id, attack_angle, br_verdict, br_evidence,
                   score_posterior, kit, kit_reason, cost_usd, tokens_in, tokens_out,
                   created_at, legacy_dossier_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (company_id, d["attack_angle"], d["br_verdict_final"], d["br_evidence"],
             d["score_final"], d["kit"], "score" if d["kit"] else None, d["cost_usd"],
             d["tokens_in"], d["tokens_out"], d["created_at"], d["id"])).lastrowid

    for c in conn.execute("SELECT * FROM legacy_competitors ORDER BY id").fetchall():
        dossier_id = product_to_dossier.get(c["product_id"])
        if dossier_id is None:
            continue
        conn.execute(
            """INSERT INTO app_competitors (dossier_id, company_id, store, country, app_id, name,
                   rating, ratings_count, reviews_collected, created_at)
               VALUES (?,?, 'play', ?,?,?,?,?,?,?)""",
            (dossier_id, product_to_company[c["product_id"]], c["store"], c["app_id"], c["name"],
             c["rating"], c["ratings_count"], c["reviews_collected"] or 0, c["created_at"]))

    for pc in conn.execute("SELECT * FROM legacy_pain_clusters ORDER BY id").fetchall():
        dossier_id = product_to_dossier.get(pc["product_id"])
        if dossier_id is None:
            continue
        conn.execute(
            """INSERT INTO pain_clusters (dossier_id, company_id, theme, frequency, severity,
                   examples, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (dossier_id, product_to_company[pc["product_id"]], pc["theme"], pc["frequency"] or 0,
             pc["severity"], pc["examples"], pc["created_at"]))

    print(f"[db] backfill: {len(product_to_company)} produtos → "
          f"{conn.execute('SELECT COUNT(*) FROM companies').fetchone()[0]} empresas, "
          f"{len(product_to_dossier)} dossiês")
