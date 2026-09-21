from datetime import date

import pandas as pd

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
