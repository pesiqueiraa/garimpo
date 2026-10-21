"""Gera data/cnae_counts.csv (cnae,uf,count) a partir dos dados abertos do CNPJ.

Uso (manual, fora do pipeline diário):

    python scripts/build_cnae_counts.py Estabelecimentos0.zip ... Estabelecimentos9.zip \
        [--out data/cnae_counts.csv]

De onde baixar: "Dados Abertos do CNPJ" da Receita Federal
(https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/<AAAA-MM>/,
arquivos Estabelecimentos0.zip a Estabelecimentos9.zip; também listados em
dados.gov.br, conjunto "Cadastro Nacional da Pessoa Jurídica - CNPJ"). São
alguns GB compactados — por isso o script lê cada zip em streaming, linha a
linha, sem descompactar no disco, e guarda só o contador.

Layout assumido (confirme no PDF de metadados que acompanha os arquivos — a
Receita já mudou endereço e nomes antes): CSV sem cabeçalho, latin-1,
separado por ';', valores entre aspas. Coluna 5 = situação cadastral
("02" = ativa), coluna 11 = CNAE fiscal principal (7 dígitos), coluna 19 = UF.

O resultado é um indicador de tamanho de mercado (quantos estabelecimentos
ativos têm aquele CNAE principal), não um TAM em reais. Só depende da stdlib.
"""
import argparse
import csv
import io
import sys
import zipfile
from collections import Counter
from pathlib import Path

COL_STATUS = 5
COL_CNAE = 11
COL_UF = 19
ACTIVE = "02"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "cnae_counts.csv"

# Algumas linhas da Receita passam do limite padrão do módulo csv (128 KB).
csv.field_size_limit(10 * 1024 * 1024)


def _is_active(value: str) -> bool:
    """Situação cadastral ativa ("02"; aceita "2" caso o zero à esquerda suma)."""
    value = (value or "").strip()
    return value == ACTIVE or value.lstrip("0") == ACTIVE.lstrip("0")


def count_rows(lines, counts: Counter) -> int:
    """Soma em `counts[(cnae, uf)]` as linhas ativas de um arquivo de Estabelecimentos."""
    read = 0
    for row in csv.reader(lines, delimiter=";", quotechar='"'):
        if len(row) <= COL_UF:
            continue
        read += 1
        if not _is_active(row[COL_STATUS]):
            continue
        cnae = "".join(ch for ch in row[COL_CNAE] if ch.isdigit())
        uf = row[COL_UF].strip().upper()
        if cnae and uf:
            counts[(cnae, uf)] += 1
    return read


def count_file(path: Path, counts: Counter) -> int:
    """Lê um .zip (todos os arquivos de dentro) ou um CSV solto."""
    path = Path(path)
    if zipfile.is_zipfile(path):
        read = 0
        with zipfile.ZipFile(path) as z:
            for member in z.infolist():
                if member.is_dir():
                    continue
                with z.open(member) as raw:
                    text = io.TextIOWrapper(raw, encoding="latin-1", newline="")
                    read += count_rows(text, counts)
        return read
    with open(path, encoding="latin-1", newline="") as f:
        return count_rows(f, counts)


def write_counts(counts: Counter, out: Path) -> None:
    """Grava cnae,uf,count ordenado (escreve num temporário e troca, para não deixar arquivo pela metade)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["cnae", "uf", "count"])
        for (cnae, uf), n in sorted(counts.items()):
            writer.writerow([cnae, uf, n])
    tmp.replace(out)


def build(paths: list[Path], out: Path) -> Counter:
    counts: Counter = Counter()
    for path in paths:
        try:
            read = count_file(path, counts)
        except (OSError, zipfile.BadZipFile, csv.Error, UnicodeDecodeError) as e:
            print(f"[cnae] {path} ilegível: {e}")
            continue
        print(f"[cnae] {Path(path).name}: {read:,} linhas lidas")
    write_counts(counts, out)
    total = sum(counts.values())
    print(f"[cnae] {total:,} estabelecimentos ativos em {len({c for c, _ in counts}):,} CNAEs → {out}")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conta estabelecimentos ativos por CNAE principal e UF.")
    parser.add_argument("files", nargs="+", type=Path, help="Estabelecimentos*.zip da Receita")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="CSV de saída")
    args = parser.parse_args(argv)
    missing = [str(p) for p in args.files if not p.exists()]
    if missing:
        print(f"[cnae] arquivo(s) não encontrado(s): {', '.join(missing)}")
        return 1
    build(args.files, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
