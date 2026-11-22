from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from features import calcular_idade, extrair_features_temporais


def test_extrai_dia_de_semana_e_horario_do_timestamp(df_consultas):
    df = extrair_features_temporais(df_consultas.copy())
    # 2026-01-05 e segunda-feira (ancora usada na geracao sintetica)
    assert list(df["dia_de_semana"]) == [0, 1, 4, 3, 4, 5]
    assert list(df["horario"]) == [9, 14, 18, 10, 17, 8]


def test_horario_descarta_minutos_de_proposito():
    # 17:30 e 17:00 caem na mesma categoria de hora -- reduz esparsidade
    # do cruzamento dia x horario (ver playbook, item 4 do checklist).
    df = pd.DataFrame({"data_hora_agendada": ["2026-01-09 17:30:00", "2026-01-09 17:00:00"]})
    df = extrair_features_temporais(df)
    assert list(df["horario"]) == [17, 17]


@pytest.mark.skipif(
    not Path("data/consultas-historicas.csv").exists(),
    reason=(
        "dataset versionado por DVC, fora do git -- o CI baixa só o model.pkl "
        "(regra: nunca `dvc pull` sem alvo). No re-treino, a mesma regra é checada "
        "pelo stage validate_data."
    ),
)
def test_dia_de_semana_e_horario_respeitam_grade_de_negocio_no_dataset_real():
    """
    A clinica funciona seg-sex 08h-18h (exceto almoco 12h-13h) e sabado
    08h-11h30, sem domingo. Este teste falha se o dataset real for
    regenerado fora dessa grade.
    """
    df = pd.read_csv("data/consultas-historicas.csv")
    df = extrair_features_temporais(df)

    assert df["dia_de_semana"].between(0, 5).all(), "nao deveria haver domingo (6) agendado"
    assert df["horario"].isin([8, 9, 10, 11, 13, 14, 15, 16, 17, 18]).all(), (
        "horario fora da jornada da clinica ou dentro do almoco (12h)"
    )

    sabado = df["dia_de_semana"] == 5
    assert df.loc[sabado, "horario"].between(8, 11).all(), (
        "sabado so funciona ate 11:30 -- nao deveria haver horario >= 12"
    )


def test_calcular_idade_antes_do_aniversario_no_ano_da_referencia():
    # Nasceu em 20/04; em 19/04 do ano de referência ainda não fez aniversário.
    assert calcular_idade(date(1998, 4, 20), date(2026, 4, 19)) == 27


def test_calcular_idade_no_dia_ou_apos_o_aniversario():
    assert calcular_idade(date(1998, 4, 20), date(2026, 4, 20)) == 28
    assert calcular_idade(date(1998, 4, 20), date(2026, 12, 31)) == 28


def test_extrair_features_temporais_nao_muta_o_df_recebido(df_consultas):
    """Regressão: a função anexava as colunas no DataFrame do chamador e o
    devolvia. Quem passasse um df sem .copy() (preprocessar(), e antes
    construir_features()) via o objeto original ganhar colunas derivadas."""
    colunas_antes = list(df_consultas.columns)

    extrair_features_temporais(df_consultas)

    assert list(df_consultas.columns) == colunas_antes


def test_periodo_do_dia_e_flags_nas_fronteiras():
    import features

    df = pd.DataFrame(
        {
            "data_hora_agendada": [
                "2026-01-05 11:00:00",  # segunda, manhã
                "2026-01-05 13:00:00",  # segunda, início de tarde
                "2026-01-05 15:00:00",  # segunda, início de tarde
                "2026-01-05 16:00:00",  # segunda, fim de tarde
                "2026-01-09 16:00:00",  # sexta 16h: fim de tarde, não é fim de expediente
                "2026-01-09 17:00:00",  # sexta 17h: fim de expediente
                "2026-01-10 09:00:00",  # sábado
            ]
        }
    )
    out = features.adicionar_features_derivadas(df, temporais=True, periodo=True)

    assert out["periodo"].tolist() == [0, 1, 1, 2, 2, 2, 0]
    assert out["fim_expediente_sexta"].tolist() == [0, 0, 0, 0, 0, 1, 0]
    assert out["fim_de_semana"].tolist() == [0, 0, 0, 0, 0, 0, 1]


def test_periodo_sem_temporais_e_recusado():
    import features
    import preprocess

    with pytest.raises(ValueError, match="temporais"):
        features.adicionar_features_derivadas(pd.DataFrame(), temporais=False, periodo=True)
    with pytest.raises(ValueError, match="temporais"):
        preprocess.colunas_de_features(temporais=False, periodo=True)


def test_colunas_sem_periodo_continuam_as_do_campeao():
    import preprocess

    assert preprocess.colunas_de_features(True, False)[-2:] == ["dia_de_semana", "horario"]
    assert preprocess.colunas_de_features(True, True)[-4:] == [
        "dia_de_semana",
        "periodo",
        "fim_expediente_sexta",
        "fim_de_semana",
    ]
