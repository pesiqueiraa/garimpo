"""TAM aproximado: quantas empresas ativas no Brasil têm o CNAE do cliente típico.

Lê data/cnae_counts.csv (cnae,uf,count), gerado à mão por
scripts/build_cnae_counts.py a partir dos dados abertos do CNPJ. É um
indicador de tamanho, não um TAM em reais — o dossiê rotula como tal. Sem o
arquivo, o dossiê sai sem TAM.
"""
import csv
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "cnae_counts.csv"

_cache: dict[str, dict[str, dict[str, int]]] = {}


def _load(path: Path) -> dict[str, dict[str, int]] | None:
    """{cnae: {uf: count}} do CSV, em cache por caminho."""
    key = str(path)
    if key in _cache:
        return _cache[key]
    try:
        with open(path, newline="", encoding="utf-8") as f:
            table: dict[str, dict[str, int]] = {}
            for row in csv.DictReader(f):
                cnae = "".join(ch for ch in str(row.get("cnae") or "") if ch.isdigit())
                try:
                    count = int(row.get("count") or 0)
                except ValueError:
                    continue
                if cnae and count > 0:
                    uf = str(row.get("uf") or "").strip().upper() or "??"
                    table.setdefault(cnae, {})
                    table[cnae][uf] = table[cnae].get(uf, 0) + count
    except FileNotFoundError:
        return None
    except (OSError, csv.Error) as e:
        print(f"[escava] tam: {path.name} ilegível: {e}")
        return None
    _cache[key] = table
    return table


def clear_cache() -> None:
    _cache.clear()


def lookup(cnae: str | None, path: Path | None = None) -> dict | None:
    """{"cnae", "count", "top_ufs": [[uf, n] x3], "note"} ou None (sem arquivo, sem CNAE)."""
    digits = "".join(ch for ch in str(cnae or "") if ch.isdigit())
    if not digits:
        return None
    table = _load(Path(path) if path else DEFAULT_PATH)
    if not table or digits not in table:
        return None
    by_uf = table[digits]
    top = sorted(by_uf.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return {"cnae": digits, "count": sum(by_uf.values()),
            "top_ufs": [[uf, n] for uf, n in top],
            "note": f"empresas ativas com CNAE principal {digits} (dados abertos da Receita)"}
