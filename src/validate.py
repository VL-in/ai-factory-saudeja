"""
SaúdeJá — etapa de validação do classificador de no-show.

Roda o modelo treinado (train.py) sobre o fold de teste isolado por
preprocess.py -- este fold nunca passou pelo SMOTE-NC, então as métricas
aqui refletem a distribuição real de classes, não a balanceada usada no fit.
As métricas são logadas na MESMA run do MLflow aberta por train.py (resumida
via run_id), para o histórico de uma run continuar completo mesmo com o
pipeline segregado em stages/containers separados. O MLflow é a única fonte
de histórico de métricas -- não persistimos um metrics.json em paralelo.
"""
import os

import joblib
import mlflow
from sklearn.metrics import average_precision_score, classification_report, roc_auc_score

TEST_PATH = os.environ.get("TEST_PATH", "./data/interim/test.pkl")
MODEL_PATH = os.environ.get("MODEL_PATH", "./data/model.pkl")
RUN_ID_PATH = os.environ.get("RUN_ID_PATH", "./data/interim/mlflow_run_id.txt")

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")


def validar(model, X_test, y_test):
    """Calcula métricas de negócio/classificação sobre o fold de teste.
    Loga um aviso (tag MLflow) se a classe positiva estiver ausente no fold --
    torna visível uma falha de amostragem em vez de reportar 0.0 como
    desempenho real do modelo."""
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]
    report = classification_report(y_test, y_pred, digits=3, output_dict=True, zero_division=0)

    if "1" not in report:
        aviso = (
            "classe positiva ausente no fold de teste -- "
            "precision_1/recall_1/f1_1 desta run não são confiáveis"
        )
        print(f"[aviso] {aviso}")
        mlflow.set_tag("aviso_split", aviso)

    metrics_1 = report.get("1", {"precision": 0.0, "recall": 0.0, "f1-score": 0.0})
    metrics_0 = report.get("0", {"precision": 0.0, "recall": 0.0, "f1-score": 0.0})

    return {
        "accuracy": report["accuracy"],
        "roc_auc": roc_auc_score(y_test, y_proba),
        "pr_auc": average_precision_score(y_test, y_proba),  # mais informativa que ROC-AUC aqui
        "f1_1": metrics_1["f1-score"],
        "precision_1": metrics_1["precision"],
        "recall_1": metrics_1["recall"],
        "f1_0": metrics_0["f1-score"],
        "precision_0": metrics_0["precision"],
        "recall_0": metrics_0["recall"],
        "f1_macro": report["macro avg"]["f1-score"],
    }


def main():
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

    dados_teste = joblib.load(TEST_PATH)
    X_test, y_test = dados_teste["X"], dados_teste["y"]

    artefato = joblib.load(MODEL_PATH)
    model = artefato["model"]

    with open(RUN_ID_PATH) as f:
        run_id = f.read().strip()

    with mlflow.start_run(run_id=run_id):
        metrics = validar(model, X_test, y_test)
        mlflow.log_metrics(metrics)

    print(f"[ok] métricas de validação logadas no MLflow (run_id={run_id})")
    for nome, valor in metrics.items():
        print(f"  {nome}: {valor:.4f}")


if __name__ == "__main__":
    main()
