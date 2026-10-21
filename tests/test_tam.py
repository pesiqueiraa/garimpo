"""TAM por CNAE (app/dig/tam.py) e o script que gera o CSV a partir dos dados abertos do CNPJ."""
import importlib.util
import zipfile
from pathlib import Path

import pytest

from app.dig import tam

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_cnae_counts.py"


@pytest.fixture(autouse=True)
def _limpa_cache():
    tam.clear_cache()
    yield
    tam.clear_cache()


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("build_cnae_counts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _csv(tmp_path, body: str) -> Path:
    path = tmp_path / "cnae_counts.csv"
    path.write_text("cnae,uf,count\n" + body, encoding="utf-8")
    return path


def test_lookup_soma_e_ordena_ufs(tmp_path):
    path = _csv(tmp_path, "8630504,SP,500\n8630504,RJ,200\n8630504,MG,300\n"
                          "8630504,BA,100\n4520001,SP,999\n")
    out = tam.lookup("8630504", path)
    assert out == {"cnae": "8630504", "count": 1100,
                   "top_ufs": [["SP", 500], ["MG", 300], ["RJ", 200]],
                   "note": "empresas ativas com CNAE principal 8630504 (dados abertos da Receita)"}


def test_lookup_aceita_cnae_formatado(tmp_path):
    path = _csv(tmp_path, "8630504,SP,5\n")
    assert tam.lookup("8630-5/04", path)["count"] == 5


def test_lookup_none_quando_falta_algo(tmp_path):
    path = _csv(tmp_path, "8630504,SP,5\n")
    assert tam.lookup("1111111", path) is None
    assert tam.lookup("", path) is None
    assert tam.lookup(None, path) is None
    assert tam.lookup("8630504", tmp_path / "nao_existe.csv") is None


def test_lookup_ignora_linhas_quebradas(tmp_path):
    path = _csv(tmp_path, "8630504,SP,abc\n8630504,SP,7\n,RJ,3\n8630504,MG,0\n")
    out = tam.lookup("8630504", path)
    assert out["count"] == 7 and out["top_ufs"] == [["SP", 7]]


def test_lookup_usa_cache(tmp_path):
    path = _csv(tmp_path, "8630504,SP,5\n")
    assert tam.lookup("8630504", path)["count"] == 5
    path.write_text("cnae,uf,count\n8630504,SP,99\n", encoding="utf-8")
    assert tam.lookup("8630504", path)["count"] == 5       # leu do cache
    tam.clear_cache()
    assert tam.lookup("8630504", path)["count"] == 99


def _row(status: str, cnae: str, uf: str) -> str:
    """Linha no layout dos Estabelecimentos (30 colunas, aspas, ';', sem cabeçalho)."""
    cols = [""] * 30
    cols[0], cols[1], cols[2] = "12345678", "0001", "90"
    cols[4] = "CLÍNICA SORRISO"            # nome fantasia com acento (latin-1)
    cols[5], cols[11], cols[19] = status, cnae, uf
    cols[12] = "8630503;8630502"           # CNAE secundário com ';' dentro das aspas
    return ";".join(f'"{c}"' for c in cols)


def test_build_cnae_counts_conta_ativas_por_cnae_e_uf(tmp_path, script):
    rows = [_row("02", "8630504", "SP"), _row("02", "8630504", "SP"),
            _row("02", "8630504", "RJ"), _row("08", "8630504", "SP"),   # baixada: fora
            _row("02", "4520001", "MG"), "linha;curta"]
    zip1 = tmp_path / "Estabelecimentos0.zip"
    with zipfile.ZipFile(zip1, "w") as z:
        z.writestr("K3241.K03200Y0.D41011.ESTABELE", ("\n".join(rows) + "\n").encode("latin-1"))
    zip2 = tmp_path / "Estabelecimentos1.zip"
    with zipfile.ZipFile(zip2, "w") as z:
        z.writestr("K3241.K03200Y1.D41011.ESTABELE", (_row("02", "8630504", "SP") + "\n").encode("latin-1"))
    out = tmp_path / "data" / "cnae_counts.csv"

    assert script.main([str(zip1), str(zip2), "--out", str(out)]) == 0

    assert out.read_text(encoding="utf-8").splitlines() == [
        "cnae,uf,count", "4520001,MG,1", "8630504,RJ,1", "8630504,SP,3"]
    result = tam.lookup("8630504", out)
    assert result["count"] == 4 and result["top_ufs"] == [["SP", 3], ["RJ", 1]]


def test_build_cnae_counts_arquivo_ausente(tmp_path, script, capsys):
    assert script.main([str(tmp_path / "nao_tem.zip"), "--out", str(tmp_path / "x.csv")]) == 1
    assert "não encontrado" in capsys.readouterr().out
    assert not (tmp_path / "x.csv").exists()
