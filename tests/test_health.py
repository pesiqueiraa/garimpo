from app import health


def runs(*counts):
    """Execuções anteriores, mais recentes primeiro."""
    return [{"source_counts": c} for c in counts]


def test_sem_alerta_quando_fonte_trouxe_itens():
    assert health.alerts(runs({"hn": 10}), {"hn": 5}, []) == []


def test_zero_hoje_com_historico_positivo_avisa_com_mediana():
    previous = runs({"hn": 10}, {"hn": 12}, {"hn": 8}, {"hn": 0})
    out = health.alerts(previous, {"hn": 0}, [])
    assert out == ["⚠️ hn não trouxe nada hoje (costuma trazer ~9)"]


def test_mediana_usa_no_maximo_sete_execucoes():
    previous = runs(*([{"ph": 1}] * 7 + [{"ph": 100}] * 5))
    assert health.alerts(previous, {"ph": 0}, []) == ["⚠️ ph não trouxe nada hoje (costuma trazer ~1)"]


def test_tres_execucoes_zeradas_vira_parada_e_substitui_aviso():
    previous = runs({"ph": 0}, {"ph": 0}, {"ph": 30}, {"ph": 30}, {"ph": 30})
    out = health.alerts(previous, {"ph": 0}, [])
    assert out == ["🛑 ph parada há 3 execuções — verifique token/API"]


def test_zero_sem_historico_nao_avisa():
    assert health.alerts([], {"nova": 0}, []) == []
    assert health.alerts(runs({"outra": 3}), {"nova": 0}, []) == []


def test_zero_com_mediana_zero_e_uma_execucao_so_nao_avisa():
    assert health.alerts(runs({"x": 0}), {"x": 0}, []) == []


def test_erros_de_etapa_ate_cinco():
    errors = [f"etapa{i}: falhou" for i in range(8)]
    out = health.alerts([], {}, errors)
    assert out == [f"❌ etapa{i}: falhou" for i in range(5)]


def test_entrada_malformada_nao_quebra():
    previous = [{"source_counts": None}, {}, {"source_counts": {"hn": "abc"}}, {"source_counts": {"hn": 4}}]
    assert health.alerts(previous, {"hn": None}, [None, ""]) == [
        "⚠️ hn não trouxe nada hoje (costuma trazer ~4)"]


def test_todas_as_fontes_zeradas_alerta_mesmo_sem_historico():
    from app import health
    out = health.alerts([], {"hackernews": 0, "yc": 0}, [])
    assert out and "nenhuma fonte" in out[0]
    assert not any("nenhuma fonte" in a for a in health.alerts([], {"hackernews": 3, "yc": 0}, []))
