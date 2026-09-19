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
from mlflow.tracking import MlflowClient
from sklearn.metrics import average_precision_score, classification_report, roc_auc_score

from config_projeto import caminho_de_env, carregar_params

PARAMS = carregar_params()

TEST_PATH = caminho_de_env("TEST_PATH", "data/interim/test.pkl")
MODEL_PATH = caminho_de_env("MODEL_PATH", "data/model.pkl")
RUN_ID_PATH = caminho_de_env("RUN_ID_PATH", "data/interim/mlflow_run_id.txt")

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")


def validar(model, X_test, y_test, threshold=0.5):
    """Calcula métricas de negócio/classificação sobre o fold de teste.
    Loga um aviso (tag MLflow) se a classe positiva estiver ausente no fold --
    torna visível uma falha de amostragem em vez de reportar 0.0 como
    desempenho real do modelo."""
    y_proba = model.predict_proba(X_test)[:, 1]
    y_pred = (y_proba >= threshold).astype(int)
    report = classification_report(y_test, y_pred, digits=3, output_dict=True, zero_division=0)
    classe_positiva_ausente = "1" not in report

    if classe_positiva_ausente:
        aviso = (
            "classe positiva ausente no fold de teste -- "
            "precision_1/recall_1/f1_1/roc_auc/pr_auc desta run não são confiáveis"
        )
        print(f"[aviso] {aviso}")
        mlflow.set_tag("aviso_split", aviso)

    metrics_1 = report.get("1", {"precision": 0.0, "recall": 0.0, "f1-score": 0.0})
    metrics_0 = report.get("0", {"precision": 0.0, "recall": 0.0, "f1-score": 0.0})

    # roc_auc_score levanta ValueError (em vez de só avisar) quando y_test
    # tem uma única classe -- mesmo caso já sinalizado acima via aviso_split,
    # então cai no mesmo fallback 0.0 das demais métricas da classe 1 em vez
    # de derrubar o stage inteiro.
    if classe_positiva_ausente:
        roc_auc = 0.0
        pr_auc = 0.0
    else:
        roc_auc = roc_auc_score(y_test, y_proba)
        pr_auc = average_precision_score(y_test, y_proba)  # mais informativa que ROC-AUC aqui

    return {
        "accuracy": report["accuracy"],
        "roc_auc": roc_auc,
        "pr_auc": pr_auc,
        "f1_1": metrics_1["f1-score"],
        "precision_1": metrics_1["precision"],
        "recall_1": metrics_1["recall"],
        "f1_0": metrics_0["f1-score"],
        "precision_0": metrics_0["precision"],
        "recall_0": metrics_0["recall"],
        "f1_macro": report["macro avg"]["f1-score"],
    }


def _logar_threshold(run_id, threshold):
    """Loga decision_threshold como param apenas na primeira vez que este run
    o recebe -- params do MLflow são imutáveis, e o run_id é reaproveitado
    entre reruns de validate.py quando só o threshold muda no params.yaml
    (train.py não é reexecutado, então o run_id em RUN_ID_PATH não muda).
    Se o valor já registrado divergir do atual, a divergência vira uma tag
    em vez de derrubar o stage."""
    client = MlflowClient()
    threshold_existente = client.get_run(run_id).data.params.get("decision_threshold")

    if threshold_existente is None:
        mlflow.log_param("decision_threshold", threshold)
    elif float(threshold_existente) != threshold:
        aviso = (
            f"decision_threshold registrado ({threshold_existente}) "
            f"diverge do atual ({threshold})"
        )
        print(f"[aviso] {aviso}")
        mlflow.set_tag("decision_threshold_divergente", str(threshold))


def main():
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

    dados_teste = joblib.load(TEST_PATH)
    X_test, y_test = dados_teste["X"], dados_teste["y"]

    artefato = joblib.load(MODEL_PATH)
    model = artefato["model"]

    with open(RUN_ID_PATH) as f:
        run_id = f.read().strip()

    if not run_id:
        # mlflow.start_run(run_id="") não dá erro -- string vazia é "falsy",
        # então o MLflow silenciosamente cria uma run nova e desgarrada (sem
        # os hiperparâmetros do treino) em vez de resumir a run certa. Isso
        # produz runs órfãs no MLflow indistinguíveis do treino que as gerou.
        # Falha alto aqui em vez de deixar isso acontecer sem avisar.
        raise RuntimeError(
            f"{RUN_ID_PATH} veio vazio -- não é possível resumir a run do "
            "MLflow. Rode a stage 'train' de novo antes de 'validate'."
        )

    threshold = PARAMS["decision"]["threshold"]

    with mlflow.start_run(run_id=run_id):
        metrics = validar(model, X_test, y_test, threshold=threshold)
        _logar_threshold(run_id, threshold)
        mlflow.log_metrics(metrics)

    print(f"[ok] métricas de validação logadas no MLflow (run_id={run_id})")
    for nome, valor in metrics.items():
        print(f"  {nome}: {valor:.4f}")


if __name__ == "__main__":
    main()
