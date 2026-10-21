"""Fontes de descoberta. Cada uma expõe `collect(cfg) -> list[dict]` e nunca levanta.

A ordem do REGISTRY é a ordem de coleta. Uma fonte pode emitir itens com
`source` diferente do nome dela (hackernews → launch_hn, yc → yc_proven); a
contagem é por `item["source"]` para o alerta de saúde enxergar cada uma.
"""
from app.config import get
from app.sources import appstore, hackernews, producthunt, trustmrr, yc

REGISTRY = {
    "hackernews": hackernews.collect,
    "producthunt": producthunt.collect,
    "yc": yc.collect,
    "trustmrr": trustmrr.collect,
    "appstore": appstore.collect,
}

# fontes "filhas" que entram zeradas na contagem: (fonte, flag que liga, nome emitido).
# launch_hn fica de fora de propósito: são poucos posts por semana e dias zerados
# seguidos são normais — entraria como "parada" no alerta de saúde todo dia.
SUBSOURCES = (("yc", "proven", "yc_proven"),)


def collect_all(cfg: dict) -> tuple[list[dict], dict[str, int]]:
    """Roda as fontes ligadas. Devolve (itens, contagem por fonte); fonte ligada com 0 aparece."""
    items: list[dict] = []
    counts: dict[str, int] = {}
    for name, collect in REGISTRY.items():
        if not get(cfg, f"fontes.{name}.enabled", True):
            continue
        counts.setdefault(name, 0)
        for parent, flag, emitted in SUBSOURCES:
            if parent == name and get(cfg, f"fontes.{parent}.{flag}", True):
                counts.setdefault(emitted, 0)
        try:
            found = collect(cfg) or []
        except Exception as e:  # contrato diz que não levanta; garantia extra
            print(f"[{name}] falhou: {e}")
            found = []
        for item in found:
            if not isinstance(item, dict) or not item.get("name") or not item.get("source"):
                continue
            items.append(item)
            counts[item["source"]] = counts.get(item["source"], 0) + 1
    return items, counts
