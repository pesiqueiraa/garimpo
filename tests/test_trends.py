from datetime import datetime, timedelta, timezone

from app import db, llm, trends
from tests.conftest import FACTS

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)  # quarta-feira


def _company(conn, name, days_ago, niche=None):
    seen = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    cid = db.insert_company(conn, f"{name.lower()}.com", f"{name.lower()}.com", name,
                            f"{name} tagline", f"https://{name.lower()}.com", seen_at=seen)
    if niche:
        db.save_extraction(conn, cid, "extract_v2", "gpt-5-mini",
                           {**FACTS, "niche": niche, "summary_pt": f"{name} para {niche}",
                            "pain": "", "br_customer_segment": ""})
    conn.commit()
    return cid


# ---------- cluster() ----------

def test_cluster_agrupa_vetores_parecidos():
    items = [
        (1, [1.0, 0.0, 0.0], "2026-10-01"),
        (2, [0.0, 1.0, 0.0], "2026-10-02"),
        (3, [0.95, 0.05, 0.0], "2026-10-03"),
        (4, [0.05, 0.98, 0.1], "2026-10-04"),
        (5, [0.0, 0.0, 1.0], "2026-10-05"),
        (6, [0.9, 0.1, 0.05], "2026-10-06"),
    ]
    groups = trends.cluster(items, 0.8)
    assert sorted(map(sorted, groups)) == [[1, 3, 6], [2, 4], [5]]


def test_cluster_respeita_ordem_de_first_seen_e_ignora_vetor_nulo():
    items = [(2, [0.0, 1.0], "2026-10-05"), (1, [1.0, 0.0], "2026-10-01"), (3, [0.0, 0.0], "2026-10-02")]
    assert trends.cluster(items, 0.9) == [[1], [2]]
    assert trends.cluster([], 0.5) == []


def test_cluster_entra_no_centroide_mais_parecido():
    items = [(1, [1.0, 0.0], "a"), (2, [0.0, 1.0], "b"), (3, [0.6, 0.8], "c")]
    groups = trends.cluster(items, 0.5)
    assert [3] not in groups and any(3 in g and 2 in g for g in groups)


def test_growth_e_semana():
    assert trends.growth(10, 7, 28, 7) == 7.0          # 3 antigas em 21 dias = 1 por semana
    assert trends.growth(10, 1, 28, 7) == round(1 / 3, 2)
    assert trends.growth(3, 3, 28, 7) == 6.0          # tudo novo: denominador mínimo 0,5
    assert trends.growth(4, 0, 28, 7) == 0.0
    assert trends.week_of(NOW) == "2026-10-18"
    assert trends.week_of(datetime(2026, 10, 18, 2, 0, tzinfo=timezone.utc)) == "2026-10-11"  # sábado 23h BRT


# ---------- run() com LLM mockado ----------

def test_run_com_embeddings_gera_tendencias_rotuladas(conn, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    clinics = [_company(conn, f"Clinica{i}", d, "agenda para clínicas") for i, d in
               enumerate([1, 2, 3, 20])]
    shops = [_company(conn, f"Loja{i}", d, "estoque para lojas") for i, d in enumerate([10, 15, 20])]
    lonely = _company(conn, "Solitaria", 2, "algo único")
    old = _company(conn, "Antiga", 60, "agenda para clínicas")

    embed_calls = []

    def fake_embed(texts, dims=256):
        embed_calls.append(len(texts))
        out = []
        for t in texts:
            if "clínicas" in t:
                out.append([1.0, 0.05, 0.0])
            elif "lojas" in t:
                out.append([0.0, 1.0, 0.05])
            else:
                out.append([0.0, 0.0, 1.0])
        return out

    prompts = []

    def fake_ask(prompt, max_tokens=500, reasoning_effort="minimal", schema=None, cache=True):
        prompts.append(prompt)
        return ({"label": "Agenda de clínicas." if "Clinica" in prompt else "Gestão de estoque"}, 10, 5)

    monkeypatch.setattr(llm, "embed", fake_embed)
    monkeypatch.setattr(llm, "ask_json", fake_ask)
    out = trends.run(conn, cfg, now=NOW)

    assert len(out) == 2
    top = out[0]
    assert sorted(top["company_ids"]) == sorted(clinics)
    assert top["size"] == 4 and top["size_recent"] == 3
    assert top["growth"] == trends.growth(4, 3, 28, 7)
    assert top["label"] == "Agenda de clínicas"
    assert out[1]["label"] == "Gestão de estoque" and out[1]["size_recent"] == 0
    assert sorted(out[1]["company_ids"]) == sorted(shops)
    assert all(lonely not in t["company_ids"] and old not in t["company_ids"] for t in out)
    assert len(prompts) == 2

    saved = db.latest_trends(conn)
    assert [t["week"] for t in saved] == ["2026-10-18", "2026-10-18"]
    assert saved[0]["label"] == "Agenda de clínicas"

    # embeddings já gravados com o mesmo texto não são refeitos
    embed_calls.clear()
    assert trends.embed_missing(conn, cfg, clinics) == 0
    assert embed_calls == []


def test_rotulo_cai_no_nicho_quando_llm_falha(conn, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    for i in range(3):
        _company(conn, f"Pix{i}", 1, "cobrança recorrente")
    monkeypatch.setattr(llm, "embed", lambda texts, dims=256: [[1.0, 0.0]] * len(texts))

    def boom(*a, **k):
        raise llm.LLMError("fora do ar")

    monkeypatch.setattr(llm, "ask_json", boom)
    out = trends.run(conn, cfg, now=NOW)
    assert len(out) == 1 and out[0]["label"] == "cobrança recorrente"


def test_orcamento_estourado_nao_levanta(conn, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    for i in range(3):
        _company(conn, f"Caro{i}", 1, "nicho caro")

    def over(*a, **k):
        raise llm.BudgetExceeded("teto")

    monkeypatch.setattr(llm, "embed", over)
    monkeypatch.setattr(llm, "ask_json", over)
    out = trends.run(conn, cfg, now=NOW)  # sem embeddings: agrupa pelo nicho
    assert len(out) == 1 and out[0]["label"] == "nicho caro"


def test_sem_chave_agrupa_por_nicho(conn, cfg, monkeypatch):
    def nao_chame(*a, **k):
        raise AssertionError("não deveria chamar o LLM sem chave")

    monkeypatch.setattr(llm, "embed", nao_chame)
    monkeypatch.setattr(llm, "ask_json", nao_chame)
    for i, niche in enumerate(["Agenda para Clínicas", "agenda para clinicas", "agenda para clínicas!"]):
        _company(conn, f"Ag{i}", i + 1, niche)
    _company(conn, "Outra1", 1, "estoque")
    _company(conn, "SemFatos", 1)

    assert trends.embed_missing(conn, cfg) == 0
    out = trends.run(conn, cfg, now=NOW)
    assert len(out) == 1
    assert out[0]["size"] == 3 and out[0]["size_recent"] == 3
    assert out[0]["label"]
    assert db.latest_trends(conn)[0]["size"] == 3


def test_sem_empresas_devolve_lista_vazia(conn, cfg):
    assert trends.run(conn, cfg, now=NOW) == []
