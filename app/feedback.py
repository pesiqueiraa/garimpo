"""Sinais do dono: links 👍 👎 👀 do e-mail e labels status:* das Issues de dossiê.

O e-mail não tem servidor por trás: cada link abre uma Issue pré-preenchida
("feedback like 42"). A leitura diária registra o sinal, comenta e fecha a
Issue. Nas Issues de dossiê, trocar a label status:* é o sinal. Tudo é
idempotente (UNIQUE em feedback) e sem GITHUB_TOKEN a leitura é pulada.
"""
import re
from urllib.parse import quote_plus, urlencode

from app import db
from app.deliver import github

TITLE_RE = re.compile(r"feedback\s+(like|dislike|watch)\s+(\d+)", re.IGNORECASE)
MARKER_RE = re.compile(r"<!--\s*garimpo:dossier=(\d+)\s+company=(\d+)\s*-->")
LABEL_SIGNAL = {"status:interessante": "interesting", "status:validando": "validating",
                "status:descartado": "discarded", "status:construindo": "building"}
# quem pode mandar sinal: num repositório público qualquer um abre Issue
TRUSTED_AUTHORS = ("OWNER", "MEMBER", "COLLABORATOR")
SIGNAL_TEXT = {"like": "👍 curti", "dislike": "👎 não curti", "watch": "👀 acompanhar"}
# status que um sinal leve (like/watch/interesting) não rebaixa; promoted mantém o bônus da fila
KEEP_ON_POSITIVE = ("dug", "validating", "building", "queued", "promoted")
MAX_PAGES = 3


def feedback_link(cfg: dict, company_id, signal: str) -> str | None:
    """URL de Issue nova pré-preenchida com o sinal; None sem repositório."""
    name = github.repo(cfg)
    if not name or signal not in SIGNAL_TEXT:
        return None
    body = (f"Sinal {SIGNAL_TEXT[signal]} para a empresa {company_id}. "
            "Só clique em Submit — o Garimpo registra e fecha esta Issue na próxima execução.")
    query = urlencode({"title": f"feedback {signal} {company_id}", "labels": "garimpo-feedback",
                       "body": body}, quote_via=quote_plus)
    return f"https://github.com/{name}/issues/new?{query}"


def apply(conn, company_id: int, signal: str) -> None:
    """Efeito do sinal no status da empresa."""
    company = db.get_company(conn, company_id)
    if company is None:
        return
    status = company["status"]
    if signal in ("like", "watch", "interesting"):
        if status not in KEEP_ON_POSITIVE and status != "watching":
            db.set_company_status(conn, company_id, "watching", "marcada por você")
    elif signal in ("dislike", "discarded"):
        if status not in ("validating", "building") and not (
                status == "discarded" and company["status_reason"] == "descartado por você"):
            db.set_company_status(conn, company_id, "discarded", "descartado por você")
    elif signal in ("validating", "building"):
        if status != signal:
            db.set_company_status(conn, company_id, signal, "marcada por você")


def record(conn, company_id: int, signal: str, origin: str, note: str | None = None,
           external_ref: str | None = None) -> bool:
    """Grava o sinal e aplica o efeito só quando ele é novo. True se novo."""
    is_new = db.add_feedback(conn, company_id, signal, origin, note=note, external_ref=external_ref)
    if is_new:
        apply(conn, company_id, signal)
    return is_new


def parse_title(title) -> tuple[str, int] | None:
    """'feedback like 42' → ('like', 42)."""
    m = TITLE_RE.search(str(title or ""))
    return (m.group(1).lower(), int(m.group(2))) if m else None


def _list_issues(cfg: dict, label: str, state: str) -> list[dict]:
    """Issues (sem PRs) com a label, as atualizadas mais recentemente primeiro (até 300)."""
    issues: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        data = github.request("GET", "/repos/{repo}/issues", cfg,
                              params={"labels": label, "state": state, "per_page": 100,
                                      "page": page, "sort": "updated", "direction": "desc"})
        batch = [i for i in data or [] if isinstance(i, dict) and "pull_request" not in i]
        issues += batch
        if not isinstance(data, list) or len(data) < 100:
            break
    return issues


def _reply_and_close(cfg: dict, number: int, text: str) -> None:
    try:
        github.request("POST", f"/repos/{{repo}}/issues/{number}/comments", cfg, json={"body": text})
    except Exception as e:
        print(f"[feedback] comentário na issue #{number} falhou: {e}")
    try:
        github.request("PATCH", f"/repos/{{repo}}/issues/{number}", cfg, json={"state": "closed"})
    except Exception as e:
        print(f"[feedback] não consegui fechar a issue #{number}: {e}")


def _read_email_feedback(conn, cfg: dict, counts: dict) -> None:
    for issue in _list_issues(cfg, "garimpo-feedback", "open"):
        number = issue.get("number")
        parsed = parse_title(issue.get("title"))
        if not number or not parsed:
            continue
        signal, company_id = parsed
        author = issue.get("author_association")
        if author is not None and author not in TRUSTED_AUTHORS:
            counts["ignored"] += 1
            print(f"[feedback] issue #{number} aberta por quem não é do repositório, ignorada")
            continue
        if db.get_company(conn, company_id) is None:
            counts["unknown"] += 1
            _reply_and_close(cfg, number, f"Empresa {company_id} não existe no Garimpo — "
                                          "nada registrado.")
            continue
        if record(conn, company_id, signal, "email", external_ref=str(number)):
            counts["email"] += 1
        conn.commit()
        _reply_and_close(cfg, number, "Registrado ✅")
        counts["closed"] += 1


def _issue_company(issue: dict, by_issue: dict[int, int]) -> int | None:
    number = issue.get("number")
    if number in by_issue:
        return by_issue[number]
    m = MARKER_RE.search(str(issue.get("body") or ""))
    return int(m.group(2)) if m else None


def _read_dossier_labels(conn, cfg: dict, counts: dict) -> None:
    by_issue = {r["issue_number"]: r["company_id"] for r in db.dossiers_with_issue(conn)}
    for issue in _list_issues(cfg, "garimpo-dossie", "all"):
        number = issue.get("number")
        company_id = _issue_company(issue, by_issue)
        if not number or company_id is None or db.get_company(conn, company_id) is None:
            continue
        for lab in issue.get("labels") or []:
            name = lab.get("name") if isinstance(lab, dict) else lab
            signal = LABEL_SIGNAL.get(str(name or "").lower())
            if signal and record(conn, company_id, signal, "issue_label",
                                 external_ref=f"{number}:{name}"):
                counts["labels"] += 1
    conn.commit()


def read(conn, cfg: dict) -> dict:
    """Lê os sinais das Issues. Nunca levanta; devolve as contagens."""
    counts = {"email": 0, "labels": 0, "closed": 0, "unknown": 0, "ignored": 0}
    if not github.available(cfg):
        print("[feedback] GITHUB_TOKEN ou repositório ausente, pulando leitura das Issues")
        return counts
    try:
        _read_email_feedback(conn, cfg, counts)
    except Exception as e:
        print(f"[feedback] leitura dos links do e-mail falhou: {e}")
    try:
        _read_dossier_labels(conn, cfg, counts)
    except Exception as e:
        print(f"[feedback] leitura das labels de dossiê falhou: {e}")
    try:
        conn.commit()
    except Exception:
        pass
    print(f"[feedback] {counts['email']} sinal(is) do e-mail, {counts['labels']} de labels")
    return counts
