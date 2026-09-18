"""
SaúdeJá — etapa de treino do classificador de no-show.
Camila S. / segregação do pipeline em preprocess/train/validate: VL-in.

Recebe o fold de treino já pré-processado por preprocess.py (o fold de teste
fica isolado, gerado lá e só lido de volta em validate.py) e aplica o
SMOTE-NC apenas sobre este fold antes do fit, para não vazar balanceamento
para a validação.
"""
import json
import os

import joblib
import yaml
from imblearn.over_sampling import SMOTENC
import lightgbm as lgb
import mlflow
import mlflow.lightgbm

from preprocess import COLUNAS_CATEGORICAS

with open("params.yaml") as f:
    PARAMS = yaml.safe_load(f)

TRAIN_RAW_PATH = os.environ.get("TRAIN_RAW_PATH", "./data/interim/train_raw.pkl")
MAPA_ESPECIALIDADE_PATH = os.environ.get(
    "MAPA_ESPECIALIDADE_PATH", "./data/interim/mapa_especialidade.json"
)
MODEL_PATH = os.environ.get("MODEL_PATH", "./data/model.pkl")
RUN_ID_PATH = os.environ.get("RUN_ID_PATH", "./data/interim/mlflow_run_id.txt")
RANDOM_STATE = 42

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME = os.environ.get("MLFLOW_EXPERIMENT_NAME", "saudeja-no-show")

# Sinaliza no MLflow runs geradas a partir da segregação do pipeline em
# stages (preprocess/train/validate) -- ver ADR sobre modularização.
# Runs anteriores a essa mudança não têm essa tag.
PIPELINE_TAG_KEY = "pipeline_arquitetura"
PIPELINE_TAG_VALUE = "segregado-preprocess-train-validate"


def _construir_modelo_kwargs():
    return dict(
        n_estimators=PARAMS["model"]["n_estimators"],
        learning_rate=PARAMS["model"]["learning_rate"],
        max_depth=PARAMS["model"]["max_depth"],
        num_leaves=PARAMS["model"]["num_leaves"],
        random_state=RANDOM_STATE,
    )


def _balancear(X_train, y_train, cat_cols, k_neighbors):
    cat_idx = [X_train.columns.get_loc(c) for c in cat_cols]
    smote = SMOTENC(categorical_features=cat_idx, k_neighbors=k_neighbors, random_state=RANDOM_STATE)
    return smote.fit_resample(X_train, y_train)


def treinar(X_train, y_train, k_neighbors=3):
    """Aplica SMOTE-NC (só no fold de treino recebido) e ajusta o LightGBM.
    Loga hiperparâmetros e o próprio modelo na run MLflow ativa -- métricas de
    validação são responsabilidade de validate.py, não daqui."""
    cat_cols = [c for c in COLUNAS_CATEGORICAS if c in X_train.columns]
    model_kwargs = _construir_modelo_kwargs()

    X_train_bal, y_train_bal = _balancear(X_train, y_train, cat_cols, k_neighbors)

    model = lgb.LGBMClassifier(**model_kwargs)
    model.fit(X_train_bal, y_train_bal, categorical_feature=cat_cols)

    mlflow.log_params(model_kwargs)
    mlflow.log_param("balancing", "smotenc")
    mlflow.log_param("k_neighbors", k_neighbors)
    mlflow.lightgbm.log_model(model, artifact_path="model")

    return model


def main():
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)

    dados_treino = joblib.load(TRAIN_RAW_PATH)
    X_train, y_train = dados_treino["X"], dados_treino["y"]

    with open(MAPA_ESPECIALIDADE_PATH) as f:
        mapa_esp = json.load(f)

    with mlflow.start_run() as run:
        mlflow.set_tag(PIPELINE_TAG_KEY, PIPELINE_TAG_VALUE)
        mlflow.log_param("features_temporais", PARAMS.get("features", {}).get("temporais", False))

        model = treinar(X_train, y_train, k_neighbors=PARAMS["balancing"]["k_neighbors"])

        joblib.dump({"model": model, "mapa_especialidade": mapa_esp}, MODEL_PATH)
        mlflow.log_artifact(MODEL_PATH)

        run_id = run.info.run_id

    # Escrita atômica (tmp + replace) -- o validate.py roda num container Docker
    # separado que lê este arquivo via bind mount logo em seguida; uma escrita
    # não-atômica pode deixar o arquivo momentaneamente vazio/truncado para
    # esse leitor (visto na prática no Docker Desktop/Windows).
    os.makedirs(os.path.dirname(RUN_ID_PATH) or ".", exist_ok=True)
    tmp_path = f"{RUN_ID_PATH}.tmp"
    with open(tmp_path, "w") as f:
        f.write(run_id)
    os.replace(tmp_path, RUN_ID_PATH)

    print(f"\n[ok] modelo salvo em {MODEL_PATH} (mlflow run_id={run_id})")


if __name__ == "__main__":
    main()

# TODO: precisaria adicionar API com FastAPI / Flask / Streamlit
# pra o time de Produto integrar com o sistema das clínicas.
# Abri o ticket SAUDEJA-241 mas não vou conseguir pegar antes das férias.
