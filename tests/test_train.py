import numpy as np

import preprocess
import train


def test_treinar_retorna_modelo_treinado_com_dados_balanceados(
    df_consultas_smote, tmp_path, monkeypatch
):
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


def test_main_end_to_end_gera_modelo_run_id_e_tag_de_pipeline(
    df_consultas_smote, tmp_path, monkeypatch
):
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


def test_treinar_logistica_calibrada_loga_tipo_e_flavor_sklearn(tmp_path):
    import mlflow

    import modelo
    from test_modelo import _config, dataset_sintetico

    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow-logistica.db'}")
    mlflow.set_experiment("teste-logistica")
    X, y, _ = preprocess.preprocessar(dataset_sintetico())
    config = _config("logistica", "class_weight", "sigmoid")

    with mlflow.start_run() as run:
        ajustado = train.treinar(X, y, config=config)

    assert modelo.tipo_do_modelo(ajustado) == "logistica" and modelo.calibrado(ajustado)
    dados = mlflow.get_run(run.info.run_id).data
    assert dados.params["model_tipo"] == "logistica"
    assert dados.params["calibracao"] == "sigmoid"
    assert dados.params["balancing"] == "class_weight"
    assert "calibracao_escala" in dados.metrics


def test_main_marca_o_run_com_dados_externos_quando_ha_linha_externa(
    df_consultas_smote, tmp_path, monkeypatch
):
    """ADR-014: run treinado com linha de origem externa leva a tag."""
    import json

    import joblib
    import mlflow
    import pandas as pd

    X, y, mapa_esp = preprocess.preprocessar(df_consultas_smote.copy())
    origem = pd.Series(["semente"] * 20 + ["externa"] * 20, index=X.index)
    caminhos = {nome: tmp_path / nome for nome in ("raw.pkl", "mapa.json", "model.pkl", "run.txt")}
    joblib.dump({"X": X, "y": y, "origem": origem}, caminhos["raw.pkl"])
    caminhos["mapa.json"].write_text(json.dumps(mapa_esp))
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow-externa.db'}"
    monkeypatch.setattr(train, "TRAIN_RAW_PATH", str(caminhos["raw.pkl"]))
    monkeypatch.setattr(train, "MAPA_ESPECIALIDADE_PATH", str(caminhos["mapa.json"]))
    monkeypatch.setattr(train, "MODEL_PATH", str(caminhos["model.pkl"]))
    monkeypatch.setattr(train, "RUN_ID_PATH", str(caminhos["run.txt"]))
    monkeypatch.setattr(train, "MLFLOW_TRACKING_URI", tracking_uri)
    monkeypatch.setattr(train, "MLFLOW_EXPERIMENT_NAME", "teste-externa")
    monkeypatch.setitem(train.PARAMS["balancing"], "k_neighbors", 1)

    train.main()

    mlflow.set_tracking_uri(tracking_uri)
    run = mlflow.get_run(caminhos["run.txt"].read_text().strip())
    assert run.data.tags.get(train.TAG_DADOS_EXTERNOS) == "true"
