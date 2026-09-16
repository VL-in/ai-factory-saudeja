import json

import joblib
import numpy as np
import pandas as pd
import pytest

import preprocess
import train
import validate


@pytest.fixture
def modelo_treinado(df_consultas_smote, tmp_path):
    import mlflow

    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow-fixture.db'}")
    mlflow.set_experiment("teste-fixture-validate")

    X, y, _ = preprocess.preprocessar(df_consultas_smote.copy())
    X_train, X_test, y_train, y_test = preprocess.dividir_treino_teste(X, y)

    with mlflow.start_run():
        modelo = train.treinar(X_train, y_train)

    return modelo, X_test, y_test


def test_validar_retorna_todas_as_metricas_esperadas(modelo_treinado):
    modelo, X_test, y_test = modelo_treinado
    metrics = validate.validar(modelo, X_test, y_test)

    esperadas = {
        "accuracy", "roc_auc", "pr_auc",
        "f1_1", "precision_1", "recall_1",
        "f1_0", "precision_0", "recall_0",
        "f1_macro",
    }
    assert set(metrics.keys()) == esperadas
    assert all(isinstance(v, float) for v in metrics.values())


class _ModeloSempreClasseZero:
    """Stub que nunca prevê a classe positiva -- usado para forçar o caso em
    que a classe 1 fica ausente tanto de y_true quanto de y_pred."""

    def predict(self, X):
        return np.zeros(len(X), dtype=int)

    def predict_proba(self, X):
        proba = np.zeros((len(X), 2))
        proba[:, 0] = 1.0
        return proba


def test_validar_sinaliza_aviso_quando_classe_positiva_ausente(tmp_path):
    import mlflow

    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow-aviso.db'}")
    mlflow.set_experiment("teste-aviso-split")

    X_test = pd.DataFrame({"idade": [30, 40, 50]})
    y_test_sem_positivos = pd.Series(np.zeros(len(X_test), dtype=int))

    with mlflow.start_run() as run:
        metrics = validate.validar(_ModeloSempreClasseZero(), X_test, y_test_sem_positivos)
        tags = mlflow.get_run(run.info.run_id).data.tags

    assert metrics["precision_1"] == 0.0 and metrics["recall_1"] == 0.0
    assert "aviso_split" in tags


def test_main_end_to_end_loga_metricas_na_mesma_run_do_treino(df_consultas_smote, tmp_path, monkeypatch):
    import mlflow

    tracking_uri = f"sqlite:///{tmp_path / 'mlflow-e2e.db'}"

    train_raw_path = tmp_path / "train_raw.pkl"
    test_path = tmp_path / "test.pkl"
    mapa_path = tmp_path / "mapa_especialidade.json"
    model_path = tmp_path / "model.pkl"
    run_id_path = tmp_path / "mlflow_run_id.txt"

    X, y, mapa_esp = preprocess.preprocessar(df_consultas_smote.copy())
    X_train, X_test, y_train, y_test = preprocess.dividir_treino_teste(X, y)
    joblib.dump({"X": X_train, "y": y_train}, train_raw_path)
    joblib.dump({"X": X_test, "y": y_test}, test_path)
    with open(mapa_path, "w") as f:
        json.dump(mapa_esp, f)

    monkeypatch.setattr(train, "TRAIN_RAW_PATH", str(train_raw_path))
    monkeypatch.setattr(train, "MAPA_ESPECIALIDADE_PATH", str(mapa_path))
    monkeypatch.setattr(train, "MODEL_PATH", str(model_path))
    monkeypatch.setattr(train, "RUN_ID_PATH", str(run_id_path))
    monkeypatch.setattr(train, "MLFLOW_TRACKING_URI", tracking_uri)
    monkeypatch.setattr(train, "MLFLOW_EXPERIMENT_NAME", "teste-e2e-validate")
    train.main()

    monkeypatch.setattr(validate, "TEST_PATH", str(test_path))
    monkeypatch.setattr(validate, "MODEL_PATH", str(model_path))
    monkeypatch.setattr(validate, "RUN_ID_PATH", str(run_id_path))
    monkeypatch.setattr(validate, "MLFLOW_TRACKING_URI", tracking_uri)
    validate.main()

    mlflow.set_tracking_uri(tracking_uri)
    run_id = run_id_path.read_text().strip()
    run = mlflow.get_run(run_id)
    # métricas de validação e a tag de treino coexistem na MESMA run
    assert run.data.tags.get(train.PIPELINE_TAG_KEY) == train.PIPELINE_TAG_VALUE
    assert "roc_auc" in run.data.metrics
    assert "f1_1" in run.data.metrics
