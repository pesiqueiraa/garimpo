"""Os TOML são editados à mão: este teste pega erro de digitação antes do Actions."""
from app import config, score


def test_toml_validos_e_completos(cfg):
    assert set(cfg) == {"fontes", "pesos", "perfil", "catalisadores"}
    for kind in ("prior", "posterior"):
        weights = cfg["pesos"][kind]
        assert set(weights) == set(score.SUBSCORES)
        assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert cfg["fontes"]["orcamento"]["max_usd_per_run"] > 0
    assert cfg["fontes"]["fila"]["max_digs_per_run"] >= 1


def test_catalisadores_bem_formados(cfg):
    from app.extract import RAILS
    ids = [c["id"] for c in cfg["catalisadores"]]
    assert len(ids) == len(set(ids)) and ids
    for c in cfg["catalisadores"]:
        assert c["keywords"] and set(c["rails"]) <= set(RAILS), c["id"]


def test_flags_de_descarte_existem_na_extracao(cfg):
    from app.extract import ENUMS, KILL_FLAGS
    assert set(cfg["pesos"]["descarte"]["kill_flags"]) <= set(KILL_FLAGS)
    assert set(cfg["pesos"]["descarte"]["kill_business_types"]) <= set(ENUMS["business_type"])


def test_config_ausente_nao_quebra(tmp_path):
    empty = config.load(str(tmp_path))
    assert empty["fontes"] == {} and empty["catalisadores"] == []
    assert config.get(empty, "fontes.fila.min_prior", 6.0) == 6.0
