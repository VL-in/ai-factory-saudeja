import numpy as np
import pandas as pd
import pytest

import train


def test_carregar_dados_le_csv_corretamente(csv_consultas, df_consultas):
    df = train.carregar_dados(str(csv_consultas))
    assert len(df) == len(df_consultas)
    assert list(df.columns) == list(df_consultas.columns)


def test_carregar_dados_arquivo_inexistente_leva_a_erro_claro():
    with pytest.raises(FileNotFoundError):
        train.carregar_dados("caminho/que/nao/existe.csv")


def test_preprocessar_mapeia_sexo_para_binario(df_consultas):
    X, y, _ = train.preprocessar(df_consultas.copy())
    assert set(X["sexo"].unique()) <= {0, 1}
    assert X.loc[df_consultas["sexo"] == "F", "sexo"].eq(0).all()
    assert X.loc[df_consultas["sexo"] == "M", "sexo"].eq(1).all()


def test_preprocessar_retorna_features_esperadas(df_consultas):
    X, y, mapa_esp = train.preprocessar(df_consultas.copy())
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


def test_preprocessar_mapa_especialidade_cobre_todas_categorias(df_consultas):
    _, _, mapa_esp = train.preprocessar(df_consultas.copy())
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
    _, _, mapa_esp = train.preprocessar(df.copy())

    novo = df_consultas.iloc[[0]].copy()
    novo["especialidade"] = "especialidade-nunca-vista"
    novo["especialidade"] = novo["especialidade"].map(mapa_esp)

    assert novo["especialidade"].isna().all()


def test_treinar_retorna_modelo_e_loga_metricas_no_mlflow(df_consultas, tmp_path, monkeypatch):
    import mlflow

    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow-test.db'}")
    mlflow.set_experiment("teste-unitario-saudeja")

    X, y, _ = train.preprocessar(df_consultas.copy())

    with mlflow.start_run():
        modelo = train.treinar(X, y)

    assert hasattr(modelo, "predict")
    preds = modelo.predict(X)
    assert len(preds) == len(X)
    assert set(np.unique(preds)) <= {0, 1}


def test_main_end_to_end_gera_artefato_de_modelo(csv_consultas, tmp_path, monkeypatch):
    import joblib
    import mlflow

    model_path = tmp_path / "model.pkl"
    monkeypatch.setattr(train, "DATA_PATH", str(csv_consultas))
    monkeypatch.setattr(train, "MODEL_PATH", str(model_path))
    monkeypatch.setattr(
        train, "MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'mlflow-main.db'}"
    )
    monkeypatch.setattr(train, "MLFLOW_EXPERIMENT_NAME", "teste-main-e2e")

    train.main()

    assert model_path.exists()
    artefato = joblib.load(model_path)
    assert "model" in artefato and "mapa_especialidade" in artefato
    assert hasattr(artefato["model"], "predict")


def test_mlflow_tracking_uri_default_aponta_para_sqlite_relativo():
    assert train.MLFLOW_TRACKING_URI.startswith("sqlite:///")
