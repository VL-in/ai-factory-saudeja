"""
SaúdeJá — regra de decisão do lembrete pago (`src/decisao.py`).

O que se trava aqui:
- **orçamento exato**: no `top_fracao`, uma fila de 20 com fração 0,3 manda
  exatamente ceil(0,3 x 20) = 6 lembretes, aos 6 de maior probabilidade;
- **válvulas**: `probabilidade_certa` força o envio além do orçamento, e
  `probabilidade_minima` bloqueia mesmo com orçamento sobrando;
- **modo threshold** idêntico ao de antes (probabilidade >= threshold);
- **registro legado** (sem bloco `decisao`) lido como threshold.
"""
import math

import numpy as np
import pytest

import decisao
from config_projeto import carregar_params


def _top(fracao=0.3, minima=None, certa=None, threshold=0.6):
    return decisao.ConfigDecisao(
        modo=decisao.MODO_TOP_FRACAO,
        threshold=threshold,
        fracao_da_fila=fracao,
        probabilidade_minima=minima,
        probabilidade_certa=certa,
    )


# Probabilidades distintas, fora de ordem, todas entre 0,2 e 0,39.
FILA_20 = [0.20 + 0.01 * ((7 * i) % 20) for i in range(20)]


def test_fila_de_20_com_fracao_30_avisa_exatamente_os_6_de_maior_risco():
    resultado = decisao.decidir_fila(FILA_20, _top())

    assert resultado.k == math.ceil(0.3 * 20) == 6
    assert sum(resultado.avisar) == 6
    avisados = sorted(p for p, a in zip(FILA_20, resultado.avisar, strict=True) if a)
    assert avisados == sorted(FILA_20)[-6:]
    assert resultado.corte == pytest.approx(min(avisados))


def test_probabilidade_certa_forca_o_envio_alem_do_orcamento():
    # 0,30 está na 10ª posição: as posições 7 a 10 entram pela válvula.
    resultado = decisao.decidir_fila(FILA_20, _top(certa=0.30))

    assert sum(resultado.avisar) == 10
    assert all(a for p, a in zip(FILA_20, resultado.avisar, strict=True) if p >= 0.30)


def test_probabilidade_minima_bloqueia_mesmo_com_orcamento_sobrando():
    # Só 3 dos 6 do orçamento passam de 0,365.
    resultado = decisao.decidir_fila(FILA_20, _top(minima=0.365))

    assert sum(resultado.avisar) == 3
    assert all(p >= 0.365 for p, a in zip(FILA_20, resultado.avisar, strict=True) if a)


def test_minima_barrando_todos_nao_avisa_ninguem_e_o_corte_e_a_minima():
    resultado = decisao.decidir_fila(FILA_20, _top(minima=0.9))

    assert not any(resultado.avisar)
    assert resultado.corte == 0.9


def test_certa_vence_minima_quando_as_duas_se_aplicam():
    regra = _top(fracao=0.25, minima=0.5, certa=0.9)
    resultado = decisao.decidir_fila([0.95, 0.1, 0.1, 0.1], regra)
    assert resultado.avisar == [True, False, False, False]


@pytest.mark.parametrize(("n", "k"), [(1, 1), (7, 3), (10, 3), (20, 6), (76, 23)])
def test_orcamento_arredonda_para_cima(n, k):
    assert decisao.tamanho_do_orcamento(n, 0.3) == k


def test_fila_vazia_nao_avisa_ninguem():
    resultado = decisao.decidir_fila([], _top())
    assert resultado.avisar == [] and resultado.k == 0


def test_empate_na_fronteira_respeita_o_orcamento_pela_ordem_de_chegada():
    resultado = decisao.decidir_fila([0.5, 0.5, 0.5, 0.1], _top(fracao=0.5))

    assert resultado.avisar == [True, True, False, False]
    assert resultado.posicao == [1, 2, 3, 4]


def test_posicoes_seguem_a_probabilidade_decrescente():
    assert decisao.posicoes_por_risco([0.1, 0.9, 0.5]) == [3, 1, 2]


def test_modo_threshold_e_o_corte_de_sempre():
    config = decisao.ConfigDecisao(modo=decisao.MODO_THRESHOLD, threshold=0.3)
    resultado = decisao.decidir_fila(FILA_20, config)

    assert resultado.avisar == [p >= 0.3 for p in FILA_20]
    assert resultado.corte == 0.3 and resultado.k is None


def test_taxa_de_disparo_no_top_fracao_e_a_fracao_configurada():
    probs = np.random.default_rng(0).random(76)
    assert decisao.taxa_de_disparo(probs, _top()) == pytest.approx(23 / 76)


# --------------------------------------------------------------------------
# configuração
# --------------------------------------------------------------------------
def test_params_do_repositorio_sao_uma_regra_valida_em_modo_top_fracao():
    """Desde a promoção única do ADR-016, o `params.yaml` descreve o desafiante
    em `top_fracao`, com as duas válvulas. O campeão decide pela regra
    registrada junto dele, não por este bloco (ver `config_registrada`)."""
    config = decisao.config_de_params(carregar_params())
    decisao.validar(config)
    assert config.modo == decisao.MODO_TOP_FRACAO
    assert config.threshold == 0.6
    assert config.fracao_da_fila == 0.3
    assert config.probabilidade_minima == 0.15
    assert config.probabilidade_certa == 0.50


def test_params_sem_modo_e_threshold():
    config = decisao.config_de_params({"decision": {"threshold": 0.6}})
    assert config.modo == decisao.MODO_THRESHOLD


@pytest.mark.parametrize(
    "bloco",
    [
        {"threshold": 0.6, "modo": "orcamento"},
        {"threshold": 0.6, "modo": "top_fracao"},
        {"threshold": 0.6, "modo": "top_fracao", "fracao_da_fila": 0},
        {"threshold": 0.6, "modo": "top_fracao", "fracao_da_fila": 1.5},
        {"threshold": -0.1},
        {"threshold": 0.6, "probabilidade_minima": 1.2},
        {"threshold": 0.6, "probabilidade_minima": 0.6, "probabilidade_certa": 0.5},
    ],
)
def test_regra_invalida_e_recusada(bloco):
    with pytest.raises(ValueError):
        decisao.config_de_params({"decision": bloco})


def test_registro_legado_e_lido_como_threshold():
    config = decisao.config_registrada({"decision_threshold": 0.6})
    assert config == decisao.ConfigDecisao(modo=decisao.MODO_THRESHOLD, threshold=0.6)


def test_registro_com_bloco_decisao_volta_igual():
    regra = _top(minima=0.15, certa=0.5)
    assert decisao.config_registrada({"decisao": regra.como_dict()}) == regra


# --------------------------------------------------------------------------
# recall na fração
# --------------------------------------------------------------------------
def test_recall_na_fracao_conta_os_positivos_entre_os_k_de_maior_risco():
    y = [1, 0, 1, 0, 0, 0, 0, 0, 0, 1]
    proba = [0.9, 0.8, 0.7, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.05]
    recall, fracao = decisao.recall_na_fracao(y, proba, 0.3)
    assert recall == pytest.approx(2 / 3)
    assert fracao == pytest.approx(0.3)


def test_recall_na_fracao_sem_positivos_e_zero():
    assert decisao.recall_na_fracao([0, 0, 0], [0.1, 0.2, 0.3], 0.3)[0] == 0.0
