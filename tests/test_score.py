import copy
from datetime import date

from app import db, score
from tests.conftest import FACTS, make_company


def obs(**values):
    out = {}
    for metric, value in values.items():
        if isinstance(value, str):
            out[metric] = {"value_num": None, "value_text": value}
        else:
            out[metric] = {"value_num": value, "value_text": None}
    return out


def test_tracao_sem_sinal_e_1(cfg):
    assert score.traction({}, ["hackernews"], 0, cfg) == (1.0, [])


def test_tracao_pega_o_sinal_mais_forte(cfg):
    grade, evidence = score.traction(obs(mrr_usd=60000, open_jobs=2), ["trustmrr"], 0, cfg)
    assert grade == 5.0
    assert any("MRR" in e for e in evidence)


def test_tracao_bonus_multiplas_fontes_e_sinais_fortes(cfg):
    one, _ = score.traction(obs(team_size=25), ["yc"], 0, cfg)
    both, ev = score.traction(obs(team_size=25), ["yc", "hackernews"], 0, cfg)
    assert both == one + 1
    assert any("fontes" in e for e in ev)
    strong, _ = score.traction(obs(team_size=25, open_jobs=10), ["yc"], 0, cfg)
    assert strong == 5.0


def test_tracao_yc_inativa_nao_conta_como_provada(cfg):
    grade, _ = score.traction(obs(team_size=80, yc_status="Inactive"), ["yc"], 0, cfg)
    assert grade == 2.0


def test_tracao_tranco_menor_e_melhor(cfg):
    assert score.traction(obs(tranco_rank=50000), ["x"], 0, cfg)[0] == 5.0
    assert score.traction(obs(tranco_rank=900000), ["x"], 0, cfg)[0] == 3.0
    assert score.traction(obs(tranco_rank=5000000), ["x"], 0, cfg)[0] == 1.0


def test_tracao_idade_do_dominio(cfg):
    grade, ev = score.traction(obs(domain_first_seen="2019-01-01"), ["x"], 0, cfg,
                               today=date(2026, 10, 20))
    assert grade == 3.0 and "anos" in ev[0]


def test_tropicalizabilidade_conta_trilhos():
    facts = copy.deepcopy(FACTS)
    assert score.tropicalizability(facts)[0] == 4.0
    facts["local_rails"] = {k: False for k in facts["local_rails"]}
    assert score.tropicalizability(facts) == (1.0, [])


def test_catalisador_exige_trilho_e_palavra(cfg):
    facts = copy.deepcopy(FACTS)
    ids = [c["id"] for c in score.match_catalysts(facts, cfg["catalisadores"])]
    assert "pix_automatico" in ids or "cobranca_hibrida" in ids
    facts["local_rails"]["payments"] = False
    facts["local_rails"]["invoicing_tax"] = False
    assert score.match_catalysts(facts, cfg["catalisadores"]) == []


def test_timing_neutro_sem_catalisador(cfg):
    facts = copy.deepcopy(FACTS)
    facts["local_rails"] = {k: False for k in facts["local_rails"]}
    assert score.timing(facts, cfg["catalisadores"])[0] == 3.0


def test_fit_segue_o_perfil_e_a_afinidade(cfg):
    facts = copy.deepcopy(FACTS)
    base, _ = score.fit(facts, cfg)
    assert base == 5.0  # B2B 4 + self_serve 1 + assinatura 0.5 (limitado a 5)
    facts.update(audience="B2C", sales_motion="sales_led", technical_complexity="alta")
    low, _ = score.fit(facts, cfg)
    assert low == 1.0
    up, notes = score.fit(facts, cfg, affinity=1.0)
    assert up == 1.5 and "curtiu" in notes[0]


def test_gap_previo_usa_palpite_e_posterior_veredito(cfg):
    assert score.gap(FACTS, cfg) == 3.0
    assert score.gap(FACTS, cfg, "sem_concorrente") == 5.0
    assert score.gap(FACTS, cfg, "concorrente_forte") == 1.0


def test_descarte_zera_o_total(cfg):
    facts = copy.deepcopy(FACTS)
    facts["kill_flags"] = ["ai_model_release"]
    r = score.compute(facts, obs(mrr_usd=99999), ["x", "y"], 500, None, cfg)
    assert r["total"] == 0.0 and "ai_model_release" in r["kill_reason"]
    facts = copy.deepcopy(FACTS)
    facts["business_type"] = "hardware"
    assert score.compute(facts, {}, ["x"], 0, None, cfg)["kill_reason"] == "descarte: tipo hardware"


def test_total_entre_0_e_10_e_diferencia(cfg):
    weak = copy.deepcopy(FACTS)
    weak.update(audience="B2C", sales_motion="sales_led", technical_complexity="alta",
                pricing_model="anuncio", br_competitor_guess="concorrente_forte",
                local_rails={k: False for k in FACTS["local_rails"]})
    strong = score.compute(FACTS, obs(mrr_usd=60000), ["a", "b"], 300, None, cfg)
    poor = score.compute(weak, {}, ["a"], 0, None, cfg)
    assert 0 <= poor["total"] < strong["total"] <= 10
    assert strong["total"] - poor["total"] >= 5  # a v1 colapsava tudo em 6-7


def test_posterior_pesa_mais_o_gap(cfg):
    prior = score.compute(FACTS, {}, ["a"], 0, None, cfg)
    post_good = score.compute(FACTS, {}, ["a"], 0, None, cfg, verdict="sem_concorrente")
    post_bad = score.compute(FACTS, {}, ["a"], 0, None, cfg, verdict="concorrente_forte")
    assert post_good["kind"] == "posterior" and prior["kind"] == "prior"
    assert post_good["total"] > prior["total"] > post_bad["total"]


def test_affinity():
    liked = [[1.0, 0.0], [0.9, 0.1]]
    disliked = [[0.0, 1.0]]
    assert score.affinity([1.0, 0.0], liked, disliked) > 0.5
    assert score.affinity([0.0, 1.0], liked, disliked) < -0.5
    assert score.affinity([1.0, 0.0], liked[:1], []) is None  # pouco feedback ainda


def test_run_grava_score_e_muda_status(conn, cfg):
    good = make_company(conn, "Boa", facts=FACTS, points=300)
    db.add_observations(conn, good, [{"metric": "mrr_usd", "value_num": 60000, "source": "trustmrr"}])
    bad_facts = copy.deepcopy(FACTS)
    bad_facts["kill_flags"] = ["research_demo"]
    bad = make_company(conn, "Demo", facts=bad_facts)
    assert score.run(conn, cfg) == 2
    assert db.latest_score(conn, good)["total"] >= 7
    assert db.get_company(conn, good)["status"] == "watching"
    assert db.get_company(conn, bad)["status"] == "discarded"
    assert score.run(conn, cfg) == 0  # nada mudou


def test_run_nao_descarta_o_que_o_dono_curtiu(conn, cfg):
    facts = copy.deepcopy(FACTS)
    facts["kill_flags"] = ["research_demo"]
    cid = make_company(conn, "Curtida", facts=facts)
    db.add_feedback(conn, cid, "like", "cli")
    score.run(conn, cfg)
    assert db.get_company(conn, cid)["status"] != "discarded"
