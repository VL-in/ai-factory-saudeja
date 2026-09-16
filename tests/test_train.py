import numpy as np
import pytest

import preprocess
import train


def test_treinar_retorna_modelo_treinado_com_dados_balanceados(df_consultas_smote, tmp_path, monkeypatch):
    import mlflow

    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow-test.db'}")
    mlflow.set_experiment("teste-unitario-saudeja")

    X, y, _ = preprocess.preprocessar(df_consultas_smote.copy())
    X_train, _, y_train, _ = preprocess.dividir_treino_teste(X, y)

    with mlflow.start_run():
        modelo = train.treinar(X_train, y_train)

    assert hasattr(modelo, "predict")
    preds = modelo.predict(X_train)
    assert len(preds) == len(X_train)
    assert set(np.unique(preds)) <= {0, 1}


def test_treinar_nao_recebe_nem_gera_fold_de_teste(df_consultas_smote, tmp_path, monkeypatch):
    """treinar() só enxerga o que for passado -- não faz split interno.
    Isso é o que garante que o fold de teste (isolado em preprocess.py)
    nunca é visto durante o balanceamento/fit."""
    import mlflow

    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow-test2.db'}")
    mlflow.set_experiment("teste-unitario-saudeja-2")

    X, y, _ = preprocess.preprocessar(df_consultas_smote.copy())
    X_train, X_test, y_train, y_test = preprocess.dividir_treino_teste(X, y)

    with mlflow.start_run():
        train.treinar(X_train, y_train)

    # o fold de teste não foi tocado por treinar() -- continua com o tamanho
    # original do split, sem nenhuma amostra sintética do SMOTE-NC
    assert len(X_test) == round(len(df_consultas_smote) * preprocess.TEST_SIZE)


def test_main_end_to_end_gera_modelo_run_id_e_tag_de_pipeline(df_consultas_smote, tmp_path, monkeypatch):
    import json

    import joblib
    import mlflow

    train_raw_path = tmp_path / "train_raw.pkl"
    mapa_path = tmp_path / "mapa_especialidade.json"
    model_path = tmp_path / "model.pkl"
    run_id_path = tmp_path / "mlflow_run_id.txt"

    X, y, mapa_esp = preprocess.preprocessar(df_consultas_smote.copy())
    X_train, _, y_train, _ = preprocess.dividir_treino_teste(X, y)
    joblib.dump({"X": X_train, "y": y_train}, train_raw_path)
    with open(mapa_path, "w") as f:
        json.dump(mapa_esp, f)

    tracking_uri = f"sqlite:///{tmp_path / 'mlflow-main.db'}"
    monkeypatch.setattr(train, "TRAIN_RAW_PATH", str(train_raw_path))
    monkeypatch.setattr(train, "MAPA_ESPECIALIDADE_PATH", str(mapa_path))
    monkeypatch.setattr(train, "MODEL_PATH", str(model_path))
    monkeypatch.setattr(train, "RUN_ID_PATH", str(run_id_path))
    monkeypatch.setattr(train, "MLFLOW_TRACKING_URI", tracking_uri)
    monkeypatch.setattr(train, "MLFLOW_EXPERIMENT_NAME", "teste-main-e2e")

    train.main()

    assert model_path.exists()
    artefato = joblib.load(model_path)
    assert "model" in artefato and "mapa_especialidade" in artefato
    assert hasattr(artefato["model"], "predict")

    assert run_id_path.exists()
    run_id = run_id_path.read_text().strip()
    assert run_id

    mlflow.set_tracking_uri(tracking_uri)
    run = mlflow.get_run(run_id)
    assert run.data.tags.get(train.PIPELINE_TAG_KEY) == train.PIPELINE_TAG_VALUE


def test_mlflow_tracking_uri_default_aponta_para_localhost():
    assert train.MLFLOW_TRACKING_URI == "http://localhost:5000"
