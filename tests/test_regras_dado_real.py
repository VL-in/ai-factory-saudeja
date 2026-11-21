"""src/regras_dado_real.py -- regras da Etapa 5, com a tabela de origens do
ADR-014: cada regra conta só as origens que lhe cabem."""

import numpy as np
import pandas as pd
import pytest

import contrato_dataset_externo as cde
import export_treino
import regras_dado_real as rdr

PARAMS = {
    "dados": {
        "peso_semente": {"limiar_reduzir": 200, "limiar_retirar": 600, "peso_reduzido": 0.3},
        "split": {"dias_producao_cronologico": 60},
    }
}


def test_nomes_das_origens_sao_os_do_export_e_do_contrato():
    assert rdr.ORIGEM_SEMENTE == export_treino.ORIGEM_SEMENTE
    assert rdr.ORIGEM_PRODUCAO == export_treino.ORIGEM_PRODUCAO
    assert rdr.ORIGEM_EXTERNA == export_treino.ORIGEM_EXTERNA == cde.ORIGEM_EXTERNA
    assert rdr.COLUNA_ORIGEM == cde.COLUNA_ORIGEM


# --------------------------------------------------------------------------
# origem
# --------------------------------------------------------------------------
def test_coluna_origem_manda():
    df = pd.DataFrame({"id_consulta": [1, 2, 3], "origem": ["semente", "externa", "producao"]})
    assert rdr.origem_por_linha(df, ids_semente={"2"}).tolist() == [
        "semente",
        "externa",
        "producao",
    ]


def test_sem_coluna_origem_usa_a_regra_antiga():
    df = pd.DataFrame({"id_consulta": [1, 2, "uuid-x"]})
    assert rdr.origem_por_linha(df, ids_semente={"1", "2"}).tolist() == [
        "semente",
        "semente",
        "producao",
    ]


def test_contagem_soma_externa_e_producao_como_desfecho_real():
    origem = pd.Series(["semente"] * 3 + ["externa"] * 4 + ["producao"] * 2)
    assert rdr.contar_por_origem(origem) == {
        "semente": 3,
        "externa": 4,
        "producao": 2,
        "desfecho_real": 6,
    }


# --------------------------------------------------------------------------
# 5.1 peso da semente
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("n_real", "peso"),
    [(0, 1.0), (199, 1.0), (200, 0.3), (599, 0.3), (600, 0.0), (1500, 0.0)],
)
def test_peso_da_semente_nos_limiares(n_real, peso):
    assert rdr.peso_da_semente(n_real, PARAMS) == peso


# --------------------------------------------------------------------------
# 5.2 modo de split
# --------------------------------------------------------------------------
def _df(origens, datas):
    return pd.DataFrame({"origem": origens, "data_hora_agendada": datas})


def test_so_semente_e_split_aleatorio():
    df = _df(["semente"] * 2, ["2026-01-05 09:00:00", "2026-01-09 09:00:00"])
    assert rdr.modo_de_split(df, df["origem"], PARAMS) == rdr.SPLIT_ALEATORIO


def test_externa_sem_producao_e_split_por_grupo_mesmo_com_meses_de_datas():
    # a externa tem id de paciente estável, mas NÃO conta como dias de operação
    df = _df(["semente", "externa", "externa"], ["2026-01-05", "2016-01-01", "2016-06-08"])
    assert rdr.dias_de_producao(df, df["origem"]) == 0
    assert rdr.modo_de_split(df, df["origem"], PARAMS) == rdr.SPLIT_POR_GRUPO


@pytest.mark.parametrize(
    ("fim", "modo"), [("2026-04-01", "por_grupo"), ("2026-04-02", "cronologico")]
)
def test_split_cronologico_so_com_mais_de_60_dias_de_producao(fim, modo):
    # 2026-01-31 -> 2026-04-01 = 60 dias (não passa); 61 passa
    df = _df(["producao", "producao"], ["2026-01-31 10:00:00", f"{fim} 10:00:00"])
    assert rdr.modo_de_split(df, df["origem"], PARAMS) == modo


# --------------------------------------------------------------------------
# 5.3 positivos de produção no fold
# --------------------------------------------------------------------------
def test_positivos_de_producao_no_fold_ignoram_externa_e_semente():
    origem = pd.Series(
        ["semente", "externa", "producao", "producao", "producao"], index=[10, 11, 12, 13, 14]
    )
    y_teste = pd.Series([1, 1, 1, 0, 1], index=[10, 11, 12, 13, 14])
    assert rdr.positivos_de_producao_no_fold(y_teste, origem) == 2
    # só uma parte das linhas está no fold de teste
    assert rdr.positivos_de_producao_no_fold(y_teste.loc[[11, 12]], origem) == 1


# --------------------------------------------------------------------------
# 5.5 features por paciente, point-in-time
# --------------------------------------------------------------------------
def test_features_por_paciente_so_usam_o_que_se_sabia_no_agendamento():
    df = pd.DataFrame(
        {
            "id_paciente": ["A", "A", "A", "A", "B"],
            # consultas em 02/05 (falta), 10/05, 20/05 e 30/05
            "data_hora_agendada": [
                "2016-05-02 08:00:00",
                "2016-05-10 08:00:00",
                "2016-05-20 08:00:00",
                "2016-05-30 08:00:00",
                "2016-05-30 08:00:00",
            ],
            # agendadas em 01/05, 02/05 (dia da falta), 05/05 e 25/05
            "dias_entre_agendamento_consulta": [1, 8, 15, 5, 3],
            "no_show": [1, 0, 1, 0, 0],
        }
    )
    f = rdr.features_por_paciente(df)
    # 1a: nada antes. 2a: agendada no dia da falta -> não conta (desfecho podia
    # não ser conhecido). 3a: agendada 05/05 -> sabe da falta de 02/05.
    # 4a: agendada 25/05 -> sabe de 02/05 (falta), 10/05 e 20/05 (falta).
    assert f["consultas_anteriores"].tolist() == [0, 0, 1, 3, 0]
    assert f["taxa_faltas_anteriores"].tolist() == pytest.approx([0, 0, 1.0, 2 / 3, 0])
    assert f["dias_desde_ultima_consulta"].iloc[2] == 3  # 05/05 - 02/05
    assert f["dias_desde_ultima_consulta"].iloc[3] == 5  # 25/05 - 20/05
    assert np.isnan(f["dias_desde_ultima_consulta"].iloc[0])


def test_features_por_paciente_nao_dependem_da_ordem_das_linhas():
    rng = np.random.default_rng(4)
    n = 300
    df = pd.DataFrame(
        {
            "id_paciente": rng.choice([f"P{i}" for i in range(40)], n),
            "data_hora_agendada": [f"2016-05-{d:02d} 09:00:00" for d in rng.integers(2, 29, n)],
            "dias_entre_agendamento_consulta": rng.integers(1, 20, n),
            "no_show": rng.integers(0, 2, n),
        }
    )
    embaralhado = df.sample(frac=1, random_state=2)
    pd.testing.assert_frame_equal(
        rdr.features_por_paciente(embaralhado).loc[df.index], rdr.features_por_paciente(df)
    )
