"""Configuração do Garimpo: lê config/*.toml com tomllib (stdlib, sem dependência nova).

Os arquivos são editáveis à mão; o código sempre usa get() com valor padrão,
então um arquivo ausente ou uma chave faltando nunca derruba o pipeline.
"""
import tomllib
from functools import lru_cache
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
FILES = ("fontes", "pesos", "perfil", "catalisadores")


@lru_cache(maxsize=4)
def load(config_dir: str | None = None) -> dict:
    """Carrega todos os .toml de config/ num dict {fontes, pesos, perfil, catalisadores}."""
    base = Path(config_dir) if config_dir else CONFIG_DIR
    cfg: dict = {}
    for name in FILES:
        path = base / f"{name}.toml"
        try:
            cfg[name] = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(f"[config] {path.name} ausente, usando padrões")
            cfg[name] = {}
        except tomllib.TOMLDecodeError as e:
            print(f"[config] {path.name} inválido ({e}), usando padrões")
            cfg[name] = {}
    cfg["catalisadores"] = cfg["catalisadores"].get("catalisador", [])
    return cfg


def get(cfg: dict, path: str, default=None):
    """Busca 'fontes.fila.min_prior' no dict de config, com padrão se faltar."""
    node = cfg
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
