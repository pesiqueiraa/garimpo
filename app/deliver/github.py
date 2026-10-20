"""Cliente mínimo da API do GitHub (Issues, comentários e labels).

Credenciais opcionais: sem GITHUB_TOKEN ou sem repositório, quem chama pula a
etapa. O repositório vem de fontes.entrega.github_repo ou de GITHUB_REPOSITORY
(preenchido pelo Actions). `request` levanta em erro HTTP — quem chama captura.
"""
import os
import re

import requests

from app.config import get

API = "https://api.github.com"
TIMEOUT = 20
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# nome -> (cor, descrição)
LABELS = {
    "garimpo-dossie": ("5319e7", "Dossiê gerado pelo Garimpo"),
    "garimpo-feedback": ("c5def5", "Sinal enviado pelo e-mail do Garimpo"),
    "status:novo": ("ededed", "Dossiê ainda não avaliado"),
    "status:interessante": ("0e8a16", "Vale acompanhar de perto"),
    "status:validando": ("fbca04", "Validando na rua"),
    "status:descartado": ("b60205", "Não vale a pena"),
    "status:construindo": ("1d76db", "Virou projeto"),
}


def repo(cfg: dict) -> str | None:
    """'dono/repo' configurado, ou None se ausente/inválido."""
    value = str(get(cfg, "fontes.entrega.github_repo", "") or "").strip()
    if not value:
        value = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    return value if REPO_RE.match(value) else None


def token() -> str | None:
    # strip: secret colado com \n quebraria o header Authorization
    return (os.environ.get("GITHUB_TOKEN") or "").strip() or None


def available(cfg: dict) -> bool:
    return bool(repo(cfg) and token())


def repo_url(cfg: dict) -> str | None:
    name = repo(cfg)
    return f"https://github.com/{name}" if name else None


def issue_url(cfg: dict, number) -> str | None:
    base = repo_url(cfg)
    try:
        return f"{base}/issues/{int(number)}" if base and number else None
    except (TypeError, ValueError):
        return None


def request(method: str, path: str, cfg: dict, json=None, params=None):
    """Chama a API e devolve o JSON (None em 204). `{repo}` no caminho é preenchido."""
    tok, name = token(), repo(cfg)
    if not tok or not name:
        raise RuntimeError("GitHub não configurado (GITHUB_TOKEN/repositório)")
    senders = {"GET": requests.get, "POST": requests.post, "PATCH": requests.patch}
    send = senders[method.upper()]
    url = path if path.startswith("http") else API + path.replace("{repo}", name)
    kwargs: dict = {"headers": {"Accept": "application/vnd.github+json",
                                "Authorization": f"Bearer {tok}",
                                "X-GitHub-Api-Version": "2022-11-28"},
                    "timeout": TIMEOUT}
    if json is not None:
        kwargs["json"] = json
    if params is not None:
        kwargs["params"] = params
    r = send(url, **kwargs)
    r.raise_for_status()
    if r.status_code == 204 or not r.content:
        return None
    return r.json()


def status_code(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None)


def ensure_labels(cfg: dict) -> int:
    """Cria as labels do Garimpo que faltam no repositório. Nunca levanta."""
    existing: set = set()
    try:
        data = request("GET", "/repos/{repo}/labels", cfg, params={"per_page": 100})
        existing = {lab.get("name") for lab in data or [] if isinstance(lab, dict)}
    except Exception as e:
        print(f"[github] não consegui listar labels: {e}")
    created = 0
    for name, (color, description) in LABELS.items():
        if name in existing:
            continue
        try:
            request("POST", "/repos/{repo}/labels", cfg,
                    json={"name": name, "color": color, "description": description})
            created += 1
        except Exception as e:
            if status_code(e) != 422:  # 422 = já existe
                print(f"[github] label '{name}' falhou: {e}")
    return created
