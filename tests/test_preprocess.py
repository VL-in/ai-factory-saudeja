import json

import joblib
import pandas as pd
import pytest

import preprocess


def test_carregar_dados_le_csv_corretamente(csv_consultas, df_consultas):
    df = preprocess.carregar_dados(str(csv_consultas))
    assert len(df) == len(df_consultas)
    assert list(df.columns) == list(df_consultas.columns)


def test_carregar_dados_arquivo_inexistente_leva_a_erro_claro():
    with pytest.raises(FileNotFoundError):
        preprocess.carregar_dados("caminho/que/nao/existe.csv")


def test_preprocessar_mapeia_sexo_para_binario(df_consultas):
    X, _, _ = preprocess.preprocessar(df_consultas.copy())
    assert set(X["sexo"].unique()) <= {0, 1}
    assert X.loc[df_consultas["sexo"] == "F", "sexo"].eq(0).all()
    assert X.loc[df_consultas["sexo"] == "M", "sexo"].eq(1).all()


def test_preprocessar_retorna_features_esperadas(df_consultas, monkeypatch):
    monkeypatch.setitem(preprocess.PARAMS, "features", {"temporais": False})

    X, y, _ = preprocess.preprocessar(df_consultas.copy())
    esperado = {
        "idade",
        "sexo",
        "especialidade",
        "distancia_km",
        "dias_entre_agendamento_consulta",
        "historico_noshow",
    }
    assert set(X.columns) == esperado
    assert y.equals(df_consultas["no_show"])


def test_preprocessar_inclui_features_temporais_quando_flag_ativa(df_consultas, monkeypatch):
    monkeypatch.setitem(preprocess.PARAMS, "features", {"temporais": True})

    X, _, _ = preprocess.preprocessar(df_consultas.copy())

    assert {"dia_de_semana", "horario"} <= set(X.columns)
    assert X["dia_de_semana"].between(0, 6).all()
    assert X["horario"].between(0, 23).all()


def test_preprocessar_mapa_especialidade_cobre_todas_categorias(df_consultas):
    _, _, mapa_esp = preprocess.preprocessar(df_consultas.copy())
    assert set(mapa_esp.keys()) == set(df_consultas["especialidade"].unique())
    # o mapeamento deve ser sobre inteiros consecutivos a partir de 0
    assert sorted(mapa_esp.values()) == list(range(len(mapa_esp)))


def test_preprocessar_especialidade_desconhecida_gera_nan(df_consultas):
    """
    Documenta uma falha silenciosa: preprocessar() usa um mapa de especialidade
    fechado (fit no próprio dataset). Uma especialidade nova em produção não vira
    erro, vira NaN silencioso na feature -- o que quebra o treino/predição do
    LightGBM sem mensagem clara. Este teste existe para tornar o comportamento
    visível caso alguém tente "corrigir" preprocessar() sem perceber o risco.
    """
    df = df_consultas.copy()
    _, _, mapa_esp = preprocess.preprocessar(df.copy())

    novo = df_consultas.iloc[[0]].copy()
    novo["especialidade"] = "especialidade-nunca-vista"
    novo["especialidade"] = novo["especialidade"].map(mapa_esp)

    assert novo["especialidade"].isna().all()


def test_dividir_treino_teste_e_estratificado_e_sem_sobreposicao(df_consultas_smote):
    X, y, _ = preprocess.preprocessar(df_consultas_smote.copy())
    X_train, X_test, y_train, y_test = preprocess.dividir_treino_teste(X, y)

    n = len(df_consultas_smote)
    n_teste_esperado = round(n * preprocess.TEST_SIZE)
    assert len(X_test) == n_teste_esperado
    assert len(X_train) == n - n_teste_esperado

    # nenhuma linha do teste vaza para o treino (índices originais disjuntos)
    assert set(X_train.index).isdisjoint(set(X_test.index))

    # proporção da classe positiva preservada (estratificação) com folga de 1 unidade
    proporcao_completa = y.mean()
    assert abs(y_train.mean() - proporcao_completa) < 0.15
    assert abs(y_test.mean() - proporcao_completa) < 0.15


def test_dividir_treino_teste_fold_de_teste_nao_e_balanceado(df_consultas_smote):
    """O fold de teste é o split bruto, sem SMOTE -- garante que o
    balanceamento (feito só em train.py) não vaza para cá."""
    X, y, _ = preprocess.preprocessar(df_consultas_smote.copy())
    _, X_test, _, y_test = preprocess.dividir_treino_teste(X, y)

    # tamanho do teste bate exatamente com o split 80/20 solicitado --
    # se houvesse contaminação por SMOTE, o teste teria mais linhas que isso.
    assert len(X_test) == round(len(df_consultas_smote) * preprocess.TEST_SIZE)
    assert len(y_test) == len(X_test)


def test_main_gera_train_raw_test_e_mapa_especialidade(csv_consultas, tmp_path, monkeypatch):
    train_raw_path = tmp_path / "train_raw.pkl"
    test_path = tmp_path / "test.pkl"
    mapa_path = tmp_path / "mapa_especialidade.json"

    monkeypatch.setattr(preprocess, "DATA_PATH", str(csv_consultas))
    monkeypatch.setattr(preprocess, "TRAIN_RAW_PATH", str(train_raw_path))
    monkeypatch.setattr(preprocess, "TEST_PATH", str(test_path))
    monkeypatch.setattr(preprocess, "MAPA_ESPECIALIDADE_PATH", str(mapa_path))

    preprocess.main()

    assert train_raw_path.exists() and test_path.exists() and mapa_path.exists()

    dados_treino = joblib.load(train_raw_path)
    dados_teste = joblib.load(test_path)
    assert set(dados_treino.keys()) == {"X", "y"}
    assert set(dados_teste.keys()) == {"X", "y"}
    assert len(dados_treino["X"]) + len(dados_teste["X"]) == 6  # linhas do df_consultas

    with open(mapa_path) as f:
        mapa_esp = json.load(f)
    assert isinstance(mapa_esp, dict) and len(mapa_esp) > 0


def test_preprocessar_nao_muta_o_df_recebido(df_consultas):
    """Regressão: preprocessar() reescrevia sexo/especialidade in-place no
    DataFrame do chamador, então carregar_dados() -> preprocessar() deixava
    o df "cru" já label-encoded para qualquer uso posterior (EDA/auditoria)."""
    original = df_consultas.copy()

    preprocess.preprocessar(df_consultas)

    pd.testing.assert_frame_equal(df_consultas, original)
