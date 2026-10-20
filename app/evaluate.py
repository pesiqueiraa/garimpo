"""Golden set: o dono rotula empresas e o Garimpo mede se o score concorda.

data/golden.csv tem as colunas site_key, name, label, note. `label` é a nota
do dono (0 a 10). Mudou prompt ou peso? Rode `python -m app avaliar` antes de
subir: se a correlação cair, a mudança piorou o score.
"""
import csv
from pathlib import Path

from app import db

GOLDEN_PATH = Path(__file__).resolve().parent.parent / "data" / "golden.csv"
FIELDS = ("site_key", "name", "label", "note")


def read_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def sample(conn, n: int, path: Path = GOLDEN_PATH) -> int:
    """Acrescenta n empresas ainda não rotuladas ao golden.csv (sem apagar nada)."""
    rows = read_golden(path)
    existing = {r["site_key"] for r in rows}
    new = db.sample_for_golden(conn, n, existing)
    rows += [{"site_key": c["site_key"], "name": c["name"], "label": "", "note": ""} for c in new]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)
    print(f"[avaliar] {len(new)} empresas acrescentadas em {path} — preencha a coluna label (0 a 10)")
    return len(new)


def _ranks(values: list[float]) -> list[float]:
    """Postos com empate pela média (para Spearman)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return None if vx == 0 or vy == 0 else round(cov / (vx * vy), 3)


def precision_at(scores: list[float], labels: list[float], k: int, good: float = 7.0) -> float | None:
    """Dos k primeiros pelo score, quantos o dono considera bons (label >= good)."""
    if not scores:
        return None
    top = sorted(zip(scores, labels), key=lambda p: p[0], reverse=True)[:k]
    return round(sum(1 for _, label in top if label >= good) / len(top), 2)


def evaluate(conn, path: Path = GOLDEN_PATH) -> dict:
    """Compara o score prévio atual (e o legado da v1) com os rótulos do dono."""
    pairs_prior, pairs_legacy, missing = [], [], 0
    for row in read_golden(path):
        try:
            label = float(str(row.get("label", "")).replace(",", "."))
        except ValueError:
            continue
        company = db.find_company_by_key(conn, row["site_key"])
        if company is None:
            missing += 1
            continue
        prior = db.latest_score(conn, company["id"], "prior")
        legacy = db.latest_score(conn, company["id"], "legacy")
        if prior and prior["total"] is not None:
            pairs_prior.append((prior["total"], label))
        if legacy and legacy["total"] is not None:
            pairs_legacy.append((legacy["total"], label))

    def _summary(pairs):
        return {"n": len(pairs),
                "spearman": spearman([p[0] for p in pairs], [p[1] for p in pairs]),
                "precision_at_10": precision_at([p[0] for p in pairs], [p[1] for p in pairs], 10)}

    result = {"v2": _summary(pairs_prior), "v1": _summary(pairs_legacy), "missing": missing}
    if not pairs_prior and not pairs_legacy:
        print("[avaliar] nenhum rótulo preenchido em data/golden.csv — rode `python -m app golden` "
              "e preencha a coluna label")
    else:
        for name in ("v2", "v1"):
            s = result[name]
            print(f"[avaliar] score {name}: n={s['n']} · Spearman={s['spearman']} · "
                  f"precisão@10={s['precision_at_10']}")
    return result
