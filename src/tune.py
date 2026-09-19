"""
SaúdeJá — GridSearch de hiperparâmetros do LightGBM para o classificador de
no-show.

Roda GridSearchCV sobre um Pipeline (imblearn) SMOTENC + LGBMClassifier, com
cross-validation estratificada aplicada SOMENTE sobre o fold de treino gerado
por preprocess.py -- o fold de teste isolado (test.pkl) nunca entra aqui,
mesma garantia já dada por train.py/validate.py. O SMOTENC entra como um
step do Pipeline, não antes do CV: assim o balanceamento sintético é
recalculado a cada fold, e nenhuma amostra sintética gerada a partir do fold
de validação daquela iteração "vaza" para o treino (ver ADR-003 sobre por
que o SMOTE-NC nunca pode tocar o fold de teste/validação).

Scoring multi-métrica (f1_1, recall_1, pr_auc), com refit em f1 da classe
positiva -- a métrica de interesse do projeto (ADR-003) e o critério já
usado para promover o SMOTE-NC (f1_1 + pr_auc, dado que um falso negativo de
no-show custa mais à clínica que um falso positivo -- BRIEFING.md/SLO.md).

Este script é exploratório, no mesmo espírito de
scripts/gerar_timestamp_sintetico.py -- NÃO é um stage do dvc.yaml.
Re-otimizar hiperparâmetros a cada re-treino mensal automatizado geraria
instabilidade de modelo sem ganho comprovado; a escolha de hiperparâmetros é
revisada por um humano e só é promovida a params.yaml manualmente, seguindo
o mesmo fluxo já usado no ADR-003: rodar aqui, decidir, editar
model.* em params.yaml, `dvc exp run` para validar oficialmente no fold de
teste isolado (validate.py), e só então comitar.
"""
import os

import joblib
import lightgbm as lgb
import mlflow
from imblearn.over_sampling import SMOTENC
from imblearn.pipeline import Pipeline
from sklearn.model_selection import GridSearchCV, StratifiedKFold

from config_projeto import caminho_de_env, carregar_params
from preprocess import COLUNAS_CATEGORICAS

PARAMS = carregar_params()

TRAIN_RAW_PATH = caminho_de_env("TRAIN_RAW_PATH", "data/interim/train_raw.pkl")
RANDOM_STATE = 42
N_FOLDS = 5

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME = os.environ.get("MLFLOW_EXPERIMENT_NAME", "saudeja-no-show")

PIPELINE_TAG_KEY = "pipeline_arquitetura"
PIPELINE_TAG_VALUE = "gridsearch-tuning"

# Grid pequeno de propósito -- o fold de treino tem ~300 linhas (dataset de
# ~380 linhas, split 80/20); uma grade grande demais arrisca escolher
# hiperparâmetros que só se ajustam ao ruído dos folds de CV, não a um
# padrão real (mesma preocupação de esparsidade do playbook de features).
PARAM_GRID = {
    "model__n_estimators": [80, 120, 200],
    "model__learning_rate": [0.01, 0.02, 0.05],
    "model__max_depth": [4, 6, -1],
    "model__num_leaves": [16, 32, 63],
}

# Scorers nativos do sklearn -- já lidam com pos_label=1/predict_proba
# corretamente, sem precisar de make_scorer manual.
SCORING = {"f1_1": "f1", "recall_1": "recall", "pr_auc": "average_precision"}
REFIT_METRIC = "f1_1"  # métrica de interesse do projeto -- ver ADR-003


def _construir_pipeline(cat_idx, k_neighbors):
    smote = SMOTENC(
        categorical_features=cat_idx, k_neighbors=k_neighbors, random_state=RANDOM_STATE
    )
    model = lgb.LGBMClassifier(random_state=RANDOM_STATE)
    return Pipeline([("smotenc", smote), ("model", model)])


def buscar_melhores_parametros(X_train, y_train, k_neighbors=None, param_grid=None, n_folds=None):
    """GridSearchCV sobre SMOTENC+LightGBM com CV estratificado no fold de
    treino. O SMOTENC dentro do Pipeline garante que o balanceamento é
    recalculado a cada fold, em vez de balancear uma vez só antes do CV
    (o que vazaria amostras sintéticas correlacionadas para o fold de
    validação de cada iteração)."""
    k_neighbors = k_neighbors if k_neighbors is not None else PARAMS["balancing"]["k_neighbors"]
    param_grid = param_grid if param_grid is not None else PARAM_GRID
    n_folds = n_folds if n_folds is not None else N_FOLDS

    cat_cols = [c for c in COLUNAS_CATEGORICAS if c in X_train.columns]
    cat_idx = [X_train.columns.get_loc(c) for c in cat_cols]

    pipeline = _construir_pipeline(cat_idx, k_neighbors)
    cv = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=RANDOM_STATE)

    grid_search = GridSearchCV(
        pipeline,
        param_grid=param_grid,
        scoring=SCORING,
        refit=REFIT_METRIC,
        cv=cv,
        n_jobs=1,
        error_score="raise",
    )
    grid_search.fit(X_train, y_train, model__categorical_feature=cat_cols)
    return grid_search


def main():
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)

    dados_treino = joblib.load(TRAIN_RAW_PATH)
    X_train, y_train = dados_treino["X"], dados_treino["y"]

    grid_search = buscar_melhores_parametros(X_train, y_train)

    melhor_idx = grid_search.best_index_
    cv_results = grid_search.cv_results_
    melhores_params = {k.replace("model__", ""): v for k, v in grid_search.best_params_.items()}
    cv_f1_1 = cv_results["mean_test_f1_1"][melhor_idx]
    cv_recall_1 = cv_results["mean_test_recall_1"][melhor_idx]
    cv_pr_auc = cv_results["mean_test_pr_auc"][melhor_idx]

    with mlflow.start_run(run_name="gridsearch-tuning"):
        mlflow.set_tag(PIPELINE_TAG_KEY, PIPELINE_TAG_VALUE)
        mlflow.log_param("cv_folds", N_FOLDS)
        mlflow.log_param("refit_metric", REFIT_METRIC)
        mlflow.log_params(melhores_params)
        mlflow.log_metric("cv_f1_1", cv_f1_1)
        mlflow.log_metric("cv_recall_1", cv_recall_1)
        mlflow.log_metric("cv_pr_auc", cv_pr_auc)
        mlflow.log_metric("n_candidatos", len(cv_results["params"]))

    print("[ok] GridSearch concluído -- melhores hiperparâmetros encontrados:")
    for k, v in melhores_params.items():
        print(f"  {k}: {v}")
    print(f"  cv_f1_1: {cv_f1_1:.4f}")
    print(f"  cv_recall_1: {cv_recall_1:.4f}")
    print(f"  cv_pr_auc: {cv_pr_auc:.4f}")
    print(
        "\n[info] resultado é exploratório (cross-validation no fold de "
        "treino) -- para promover, atualize model.* em params.yaml com os "
        "valores acima e rode `dvc exp run` para validar oficialmente no "
        "fold de teste isolado (validate.py) antes de comitar, seguindo o "
        "mesmo fluxo do ADR-003."
    )


if __name__ == "__main__":
    main()
