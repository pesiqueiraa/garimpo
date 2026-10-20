"""Alertas de saúde das fontes (lógica pura, sem banco nem rede).

Nasceu do Product Hunt que trouxe 1 item em 6 semanas sem ninguém perceber:
fonte que costuma trazer itens e hoje veio zerada vira aviso no e-mail.
"""
from statistics import median

HISTORY = 7
MAX_ERRORS = 5


def _count(run: dict, source: str) -> int | None:
    counts = run.get("source_counts") if isinstance(run, dict) else None
    if not isinstance(counts, dict) or source not in counts:
        return None
    try:
        return int(counts[source] or 0)
    except (TypeError, ValueError):
        return None


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def alerts(recent_runs: list[dict], current_counts: dict[str, int], errors: list[str]) -> list[str]:
    """Avisos para o rodapé do e-mail. `recent_runs` = execuções anteriores, mais recentes primeiro."""
    out: list[str] = []
    previous = list(recent_runs or [])[:HISTORY]
    for source, value in (current_counts or {}).items():
        try:
            now = int(value or 0)
        except (TypeError, ValueError):
            now = 0
        if now > 0:
            continue
        last_two = [_count(r, source) for r in previous[:2]]
        history = [c for c in (_count(r, source) for r in previous) if c is not None]
        if len(last_two) == 2 and last_two == [0, 0]:
            out.append(f"🛑 {source} parada há 3 execuções — verifique token/API")
        elif history and median(history) > 0:
            out.append(f"⚠️ {source} não trouxe nada hoje (costuma trazer ~{_fmt(median(history))})")
    for error in [e for e in errors or [] if e][:MAX_ERRORS]:
        out.append(f"❌ {error}")
    return out
