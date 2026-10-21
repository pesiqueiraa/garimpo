"""Tendências semanais: o que está aparecendo muito, e cada vez mais, lá fora.

Cada empresa vira um embedding (fatos extraídos, ou nome + tagline). Uma
clusterização gulosa junta as parecidas das últimas semanas; o crescimento
compara a última semana com o ritmo das anteriores. Só os grupos que mais
crescem ganham rótulo do LLM (uma chamada cada). Sem OPENAI_API_KEY, agrupa
pelo nicho da extração — pior, mas sai alguma coisa. Etapa opcional: nunca levanta.
"""
import hashlib
import math
import operator
import re
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from app import db, extract, llm
from app.config import get

BRT = timezone(timedelta(hours=-3))
BATCH = 100
LABEL_SCHEMA = {"name": "rotulo_tendencia",
                "schema": {"type": "object", "additionalProperties": False,
                           "required": ["label"], "properties": {"label": {"type": "string"}}}}


# ---------- embeddings ----------

def company_text(conn, company) -> str:
    """Texto que representa a empresa no embedding."""
    text = extract.describe(db.latest_extraction(conn, company["id"]))
    if text:
        return text
    return " — ".join(x for x in (company["name"], company["tagline"]) if x)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def embed_missing(conn, cfg: dict, company_ids: list[int] | None = None) -> int:
    """Gera embeddings que faltam (ou cujo texto mudou). Devolve quantos gravou."""
    if not llm.available():
        print("[tendencias] OPENAI_API_KEY ausente, sem embeddings")
        return 0
    dims = int(get(cfg, "fontes.tendencias.embedding_dims", 256))
    limit = int(get(cfg, "fontes.tendencias.max_embed_per_run", 1000))
    rows = db.companies_by_ids(conn, company_ids) if company_ids is not None \
        else db.all_companies(conn)
    known = db.embedding_hashes(conn)
    pending = []
    for company in rows:
        text = company_text(conn, company)
        if not text:
            continue
        digest = text_hash(text)
        if known.get(company["id"]) != digest:
            pending.append((company["id"], text, digest))
    pending = pending[:limit]
    done = 0
    for start in range(0, len(pending), BATCH):
        batch = pending[start:start + BATCH]
        try:
            vectors = llm.embed([text for _, text, _ in batch], dims)
        except llm.BudgetExceeded as e:
            print(f"[tendencias] {e} — parando com {done} embeddings")
            break
        except Exception as e:
            print(f"[tendencias] embeddings falharam: {e}")
            break
        for (company_id, _, digest), vector in zip(batch, vectors):
            db.save_embedding(conn, company_id, llm.EMBED_MODEL, vector, digest)
            done += 1
        conn.commit()
    if done:
        print(f"[tendencias] {done} embeddings gerados")
    return done


# ---------- agrupamento ----------

def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def cluster(items: list[tuple[int, list[float], str]], similarity: float) -> list[list[int]]:
    """Agrupamento guloso: na ordem de first_seen, cada item entra no grupo de centroide
    mais parecido (cosseno >= similarity) ou abre um grupo novo. Função pura."""
    sums: list[list[float]] = []      # soma dos vetores unitários (mesma direção do centroide)
    norms: list[float] = []
    members: list[list[int]] = []
    for company_id, vector, _ in sorted(items, key=lambda it: (it[2] or "", it[0])):
        length = _norm(vector)
        if not vector or length == 0:
            continue
        unit = [x / length for x in vector]
        best, best_sim = None, similarity
        for i, total in enumerate(sums):
            if norms[i] == 0:
                continue
            sim = sum(map(operator.mul, unit, total)) / norms[i]
            if sim >= best_sim:
                best, best_sim = i, sim
        if best is None:
            sums.append(unit)
            norms.append(1.0)
            members.append([company_id])
        else:
            sums[best] = list(map(operator.add, sums[best], unit))
            norms[best] = _norm(sums[best])
            members[best].append(company_id)
    return members


def normalize_niche(text) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def group_by_niche(niches: dict[int, str]) -> list[list[int]]:
    """Plano B sem embeddings: mesmo nicho normalizado = mesmo grupo."""
    groups: dict[str, list[int]] = {}
    for company_id, niche in niches.items():
        key = normalize_niche(niche)
        if key:
            groups.setdefault(key, []).append(company_id)
    return list(groups.values())


def growth(size: int, size_recent: int, window_days: int, recent_days: int) -> float:
    """Ritmo da última janela curta contra o ritmo das semanas anteriores."""
    older_days = max(1, window_days - recent_days)
    expected = (size - size_recent) * recent_days / older_days
    return round(size_recent / max(0.5, expected), 2)


def week_of(now: datetime | None = None) -> str:
    """Domingo mais recente (fuso de Brasília), em ISO."""
    today: date = (now or datetime.now(timezone.utc)).astimezone(BRT).date()
    return (today - timedelta(days=(today.weekday() + 1) % 7)).isoformat()


# ---------- rótulos ----------

def _fallback_label(member_ids: list[int], niches: dict[int, str], names: dict[int, str]) -> str:
    counter = Counter(n.strip() for n in (niches.get(i) for i in member_ids) if n and n.strip())
    if counter:
        return counter.most_common(1)[0][0][:80]
    first = next((names[i] for i in member_ids if names.get(i)), None)
    return f"parecidos com {first}"[:80] if first else "tema sem nome"


def _llm_label(group: dict, conn, names: dict[int, str]) -> str | None:
    lines = []
    for company_id in group["recent_ids"][:5] + [i for i in group["company_ids"]
                                                  if i not in group["recent_ids"]][:3]:
        facts = db.latest_extraction(conn, company_id) or {}
        company = db.get_company(conn, company_id)
        desc = facts.get("summary_pt") or (company["tagline"] if company else "") or ""
        niche = f" (nicho: {facts['niche']})" if facts.get("niche") else ""
        lines.append(f"- {names.get(company_id, '?')}: {str(desc)[:200]}{niche}")
    prompt = llm.load_prompt("trend_label_v2").format(
        size=group["size"], size_recent=group["size_recent"], examples="\n".join(lines))
    data, _, _ = llm.ask_json(prompt, max_tokens=300, schema=LABEL_SCHEMA)
    label = str((data or {}).get("label") or "").strip().strip(".").strip()
    return label[:80] if label else None


# ---------- etapa ----------

def run(conn, cfg: dict, now: datetime | None = None) -> list[dict]:
    """Agrupa as empresas da janela, mede crescimento, rotula e grava a semana."""
    try:
        return _run(conn, cfg, now)
    except llm.BudgetExceeded as e:
        print(f"[tendencias] {e} — tendências puladas")
    except Exception as e:
        print(f"[tendencias] falhou: {e}")
    return []


def _run(conn, cfg: dict, now: datetime | None) -> list[dict]:
    window = int(get(cfg, "fontes.tendencias.window_days", 28))
    recent = int(get(cfg, "fontes.tendencias.recent_days", 7))
    similarity = float(get(cfg, "fontes.tendencias.similarity", 0.62))
    min_cluster = int(get(cfg, "fontes.tendencias.min_cluster", 3))
    max_labels = int(get(cfg, "fontes.tendencias.max_labels", 6))
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=window)).isoformat(timespec="seconds")
    recent_since = (now - timedelta(days=recent)).isoformat(timespec="seconds")

    companies = db.companies_seen_since(conn, since)
    if not companies:
        print("[tendencias] nenhuma empresa na janela")
        return []
    ids = [c["id"] for c in companies]
    first_seen = {c["id"]: c["first_seen"] for c in companies}
    names = {c["id"]: c["name"] for c in companies}
    niches = {}
    for c in companies:
        facts = db.latest_extraction(conn, c["id"], include_legacy=True) or {}
        if facts.get("niche"):
            niches[c["id"]] = str(facts["niche"])

    groups: list[list[int]] = []
    use_llm = llm.available()
    if use_llm:
        embed_missing(conn, cfg, ids)
        vectors = db.get_embeddings(conn, ids)
        items = [(i, vectors[i], first_seen[i]) for i in ids if i in vectors]
        # embeddings falharam para a maioria: agrupar só o resto distorce, melhor o nicho
        groups = cluster(items, similarity) if len(items) * 2 >= len(ids) else []
    if not groups:
        groups = group_by_niche(niches)

    trends = []
    for members in groups:
        if len(members) < min_cluster:
            continue
        recent_ids = [i for i in members if (first_seen.get(i) or "") >= recent_since]
        trends.append({"company_ids": members, "recent_ids": recent_ids, "size": len(members),
                       "size_recent": len(recent_ids),
                       "growth": growth(len(members), len(recent_ids), window, recent)})
    trends.sort(key=lambda t: (t["growth"], t["size"]), reverse=True)

    for i, t in enumerate(trends):
        label = None
        if use_llm and i < max_labels:
            try:
                label = _llm_label(t, conn, names)
            except llm.BudgetExceeded as e:
                print(f"[tendencias] {e} — rótulos restantes pelo nicho")
                use_llm = False
            except Exception as e:
                print(f"[tendencias] rótulo falhou: {e}")
        t["label"] = label or _fallback_label(t["company_ids"], niches, names)

    week = week_of(now)
    out = [{"label": t["label"], "size": t["size"], "size_recent": t["size_recent"],
            "growth": t["growth"], "company_ids": t["company_ids"], "week": week} for t in trends]
    db.save_trends(conn, week, out)
    conn.commit()
    print(f"[tendencias] {len(out)} tema(s) na semana {week}")
    return out
