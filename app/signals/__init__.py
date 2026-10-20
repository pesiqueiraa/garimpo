"""Sinais por empresa: sondagem do site (todas as novas) e tração (só candidatas).

Duas etapas do pipeline diário:
- `probe_new`: lê o site de quem ainda não tem leitura, resolve redirects (o
  link do Product Hunt vira o domínio real e funde duplicatas) e pega a idade
  do domínio no Wayback;
- `run`: para quem passou do score prévio mínimo, busca YC, vagas, Tranco e
  Form D — chamadas mais caras, por isso só para candidatas e no máximo 1x/semana.
"""
import time
from datetime import date

from app import db
from app.config import get
from app.resolve import clean_url, site_key
from app.signals import formd, jobs, site, tranco, wayback
from app.sources import yc

# ordem de consulta; tranco é em lote (um download serve todas as empresas)
TRACTION = (("yc", yc.lookup), ("jobs", jobs.lookup), ("tranco", tranco.lookup),
            ("formd", formd.lookup))
BATCHED = {"tranco"}
# métricas/fontes que só a etapa `run` grava: marcam a última consulta de tração
TRACTION_METRICS = ("open_jobs", "tranco_rank", "formd_filings")
TRACTION_SOURCES = {"greenhouse", "lever", "ashby", "tranco", "sec_formd"}
RECHECK_DAYS = 7
# redirect para estes domínios é login/parking, não o site da empresa
NOT_COMPANY_DOMAINS = {
    "google.com", "facebook.com", "linkedin.com", "microsoft.com", "live.com", "apple.com",
    "godaddy.com", "sedo.com", "dan.com", "afternic.com", "hugedomains.com", "cloudflare.com",
    "auth0.com", "okta.com", "wix.com", "squarespace.com", "shopify.com", "bit.ly", "linktr.ee",
}


def _new_site(company, final_url: str | None) -> tuple[str, str | None] | None:
    """(site_key, domínio) do destino do redirect, se for um site próprio diferente do atual."""
    if not final_url:
        return None
    key, domain = site_key(final_url, company["name"])
    if not domain or key == company["site_key"] or domain in NOT_COMPANY_DOMAINS:
        return None
    return key, domain


def probe_new(conn, cfg: dict) -> int:
    """Sonda os sites ainda não lidos. Devolve quantos sites abriram."""
    limit = int(get(cfg, "fontes.sondagem.max_sites_per_run", 60))
    pause = float(get(cfg, "fontes.sondagem.wayback_pause_s", 1.0))
    try:
        pending = db.companies_without_site_page(conn, limit)
    except Exception as e:
        print(f"[sondagem] não consegui listar empresas: {e}")
        return 0
    read = merged = aged = 0
    for row in pending:
        try:
            company = db.get_company(conn, row["id"])
            if company is None or db.get_site_page(conn, company["id"]) is not None:
                continue  # fundida ou já lida nesta mesma rodada
            page = site.probe(company["url"], cfg)
            # página vazia também é gravada: site fora do ar não é tentado todo dia
            db.save_site_page(conn, company["id"], page or {})
            company_id = company["id"]
            if page:
                read += 1 if page.get("text") else 0
                db.add_observations(conn, company_id, site.observations(page))
                target = _new_site(company, page.get("final_url"))
                if target:
                    new_id = db.update_company_site(conn, company_id, target[0], target[1],
                                                    clean_url(page["final_url"]))
                    if new_id != company_id:
                        merged += 1
                        print(f"[sondagem] '{company['name']}' fundida em {target[0]}")
                    company_id = new_id
            # idade do domínio só para site que respondeu: site morto não precisa de idade
            company = db.get_company(conn, company_id) if page else None
            if company is not None and company["domain"] and \
                    "domain_first_seen" not in db.latest_observations(conn, company_id):
                if aged:
                    time.sleep(pause)
                found = wayback.lookup(company, cfg)
                aged += 1
                db.add_observations(conn, company_id, found)
            conn.commit()
        except Exception as e:
            print(f"[sondagem] '{row['name']}' falhou: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
    print(f"[sondagem] {read} de {len(pending)} sites lidos, {merged} fundidas, "
          f"{aged} consultas ao Wayback")
    return read


def _last_traction_check(conn, company_id: int) -> str | None:
    latest = db.latest_observations(conn, company_id)
    seen = [o["observed_at"] for m, o in latest.items()
            if m in TRACTION_METRICS and o.get("source") in TRACTION_SOURCES]
    return max(seen) if seen else None


def candidates(conn, cfg: dict, today: date | None = None) -> list:
    """Quem merece consulta de tração hoje.

    Score prévio ≥ mínimo, sem descarte, sem consulta nos últimos 7 dias.
    Recém-chegadas (2 dias) vão primeiro, pelo score; o resto entra num rodízio
    diário — sem marcador de "consultei e não achei nada", as empresas sem sinal
    nenhum voltariam todo dia e tomariam a vez das outras.
    """
    min_prior = float(get(cfg, "fontes.sinais.min_prior_for_signals", 4.5))
    cap = int(get(cfg, "fontes.sinais.max_companies_per_run", 40))
    scores = db.latest_scores(conn, "prior")
    eligible = [cid for cid, s in scores.items()
                if s.get("total") is not None and s["total"] >= min_prior and not s.get("kill_reason")]
    week_ago, recent = db.days_ago_iso(RECHECK_DAYS), db.days_ago_iso(2)
    fresh, rest = [], []
    for c in db.companies_by_ids(conn, eligible):
        if c["status"] in ("discarded", "expired"):
            continue
        last = _last_traction_check(conn, c["id"])
        if last and last >= week_ago:
            continue
        (fresh if last is None and (c["first_seen"] or "") >= recent else rest).append(c)
    order = lambda c: (-scores[c["id"]]["total"], c["id"])  # noqa: E731
    fresh.sort(key=order)
    rest.sort(key=order)
    chosen = fresh[:cap]
    room = cap - len(chosen)
    if room > 0 and rest:
        start = ((today or date.today()).toordinal() * room) % len(rest)
        chosen += [rest[(start + i) % len(rest)] for i in range(min(room, len(rest)))]
    return chosen


def run(conn, cfg: dict, company_ids: list[int] | None = None) -> int:
    """Consulta os sinais de tração e grava as observações. Devolve quantas empresas tiveram sinal."""
    try:
        companies = candidates(conn, cfg) if company_ids is None else \
            db.companies_by_ids(conn, list(company_ids))
    except Exception as e:
        print(f"[sinais] não consegui escolher candidatas: {e}")
        return 0
    if not companies:
        print("[sinais] nenhuma empresa para consultar")
        return 0
    found: dict[int, list[dict]] = {c["id"]: [] for c in companies}
    per_source: dict[str, int] = {}
    for c in companies:
        for name, lookup in TRACTION:
            if name in BATCHED:
                continue
            try:
                obs = lookup(c, cfg) or []
            except Exception as e:  # lookups não levantam; garantia extra
                print(f"[sinais] {name} falhou para '{c['name']}': {e}")
                obs = []
            found[c["id"]] += obs
            if obs:
                per_source[name] = per_source.get(name, 0) + 1
    by_domain = {}
    for c in companies:
        if c["domain"]:
            by_domain.setdefault(c["domain"].lower(), []).append(c["id"])
    ranks = tranco.ranks_for(set(by_domain), cfg)
    for domain, rank in ranks.items():
        for cid in by_domain.get(domain, []):
            found[cid].append(tranco.observation(rank))
            per_source["tranco"] = per_source.get("tranco", 0) + 1
    with_signal = added = 0
    for cid, obs in found.items():
        if not obs:
            continue
        with_signal += 1
        try:
            added += db.add_observations(conn, cid, obs)
        except Exception as e:
            print(f"[sinais] gravação falhou para a empresa {cid}: {e}")
    conn.commit()
    detail = ", ".join(f"{k}: {v}" for k, v in sorted(per_source.items())) or "nenhum sinal"
    print(f"[sinais] {len(companies)} empresas consultadas, {with_signal} com sinal, "
          f"{added} observações novas ({detail})")
    return with_signal
