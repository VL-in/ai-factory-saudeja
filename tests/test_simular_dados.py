"""scripts/simular_dados.py -- estudo de simulação, com dado FICTÍCIO no
formato do export (nenhuma linha do Kaggle).

O que se trava: a calibração da 5b acerta a AUC verdadeira pedida (inclusive
com empates de score), o pipeline recupera um sinal conhecido forte, o peso
da semente entra no ajuste e os estudos devolvem a estrutura esperada."""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

import agenda_clinica
from config_projeto import carregar_params

_CAMINHO = Path(__file__).resolve().parents[1] / "scripts" / "simular_dados.py"
_spec = importlib.util.spec_from_file_location("simular_dados", _CAMINHO)
simular = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(simular)

ESPECIALIDADES = carregar_params()["cadastro"]["especialidades"]


def _consultas(rng, n, prefixo="K-", inicio="2016-05-02"):
    dias = [d for d in pd.date_range(inicio, periods=12) if d.dayofweek != 6]
    datas = []
    for i in range(n):
        dia = dias[i % len(dias)]
        slots = agenda_clinica.horarios_do_dia(dia.date())
        datas.append(f"{dia.date()} {slots[rng.integers(len(slots))].strftime('%H:%M')}:00")
    return pd.DataFrame(
        {
            "id_consulta": [f"{prefixo}{i}" for i in range(n)],
            "id_paciente": [f"K{i // 2:016x}" for i in range(n)],
            "idade": rng.integers(0, 86, n),
            "sexo": rng.choice(["F", "M"], n),
            "especialidade": [ESPECIALIDADES[i % len(ESPECIALIDADES)] for i in range(n)],
            "distancia_km": np.round(rng.uniform(0.5, 30, n), 1),
            "dias_entre_agendamento_consulta": rng.integers(1, 91, n),
            "historico_noshow": rng.integers(0, 4, n),
            "no_show": rng.binomial(1, 0.28, n),
            "data_hora_agendada": datas,
        }
    )


@pytest.fixture(scope="module")
def dados():
    rng = np.random.default_rng(11)
    return {
        "simulacao": _consultas(rng, 3000),
        "reservado": _consultas(rng, 1500, prefixo="R-"),
        "semente": _consultas(rng, 380, prefixo="", inicio="2026-01-05"),
    }


@pytest.mark.parametrize("alvo", [0.60, 0.70])
def test_calibrar_acerta_a_auc_verdadeira_e_a_prevalencia(alvo):
    rng = np.random.default_rng(3)
    z = rng.integers(-3, 4, 20_000).astype(float)  # muitos empates de propósito
    escala, intercepto = simular.calibrar(z, alvo, prevalencia=0.28)
    p = simular._probabilidade(z, escala, intercepto)
    assert p.mean() == pytest.approx(0.28, abs=1e-3)
    y = rng.binomial(1, p)
    assert roc_auc_score(y, p) == pytest.approx(alvo, abs=0.015)


def test_pipeline_recupera_sinal_conhecido_forte(dados):
    r = simular.estudo_sinal(
        dados["simulacao"],
        dados["reservado"],
        aucs=(0.75,),
        tamanhos=(2000,),
        repeticoes=2,
        modelos=("logistica",),
    )["0.75"]
    medida = r["modelos"]["logistica"][2000]
    assert r["auc_oraculo_no_teste"] == pytest.approx(0.75, abs=0.04)
    assert medida["recuperacao"] > 0.9


def test_logito_base_usa_a_mesma_padronizacao_no_treino_e_no_teste(dados):
    sim = dados["simulacao"]
    deslocado = sim.assign(idade=sim["idade"] + 10)
    # com a referência fixa, idade +10 muda o logito; padronizando pelo
    # próprio df, o deslocamento sumiria
    assert not np.allclose(simular.logito_base(deslocado, sim), simular.logito_base(sim, sim))
    assert np.allclose(simular.logito_base(deslocado), simular.logito_base(sim))


@pytest.mark.parametrize("modelo", ["logistica", "lightgbm"])
def test_peso_por_linha_entra_no_ajuste(dados, modelo):
    X, y, _ = simular._X(dados["simulacao"].head(600), simular.FEATURES_REAIS)
    sem_peso = simular.ajustar(X, y, modelo).predict_proba(X)[:, 1]
    peso = np.where(np.arange(len(y)) < 300, 0.05, 1.0)
    com_peso = simular.ajustar(X, y, modelo, peso).predict_proba(X)[:, 1]
    assert not np.allclose(sem_peso, com_peso)


def test_curva_devolve_faixa_por_n_e_pula_n_maior_que_o_pool(dados):
    r = simular.estudo_curva(
        dados["simulacao"],
        dados["reservado"],
        tamanhos=(200, 400, 10_000),
        repeticoes=3,
        modelos=("logistica",),
    )["logistica"]
    assert set(r["por_n"]) == {200, 400}
    for d in r["por_n"].values():
        assert d["roc_auc"]["p025"] <= d["roc_auc"]["media"] <= d["roc_auc"]["p975"]
        assert 0.0 <= d["acima_do_piso"] <= 1.0
    assert r["teto_pool_inteiro"]["n"] == len(dados["simulacao"])


def test_mistura_cobre_todos_os_pesos_e_a_semente_sozinha(dados):
    r = simular.estudo_mistura(
        dados["simulacao"],
        dados["reservado"],
        dados["semente"],
        tamanhos=(200,),
        pesos=(1.0, 0.3, 0.0),
        repeticoes=2,
        modelos=("logistica",),
    )["logistica"]
    assert set(r["por_n"][200]) == {"1", "0.3", "0"}
    assert 0.0 <= r["so_semente"]["roc_auc"] <= 1.0
