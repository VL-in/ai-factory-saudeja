"""
SaúdeJá — etapa de treino do classificador de no-show.
Camila S. / segregação do pipeline em preprocess/train/validate: VL-in.

Recebe o fold de treino já pré-processado por preprocess.py (o fold de teste
fica isolado, gerado lá e só lido de volta em validate.py) e ajusta a
configuração do `params.yaml` -- `model.tipo` (lightgbm | logistica),
`balancing.strategy` (smotenc | class_weight | none) e `model.calibracao`
(none | sigmoid) -- pela fábrica de `src/modelo.py`, a mesma que
`scripts/avaliar_cv.py` usa. O balanceamento e a calibração rodam só sobre
este fold, para não vazar nada para a validação.
"""
import json
import os

import joblib
import mlflow

import modelo as modelo_ml
from config_projeto import caminho_de_env, carregar_params
from preprocess import COLUNAS_CATEGORICAS

PARAMS = carregar_params()

TRAIN_RAW_PATH = caminho_de_env("TRAIN_RAW_PATH", "data/interim/train_raw.pkl")
MAPA_ESPECIALIDADE_PATH = caminho_de_env(
    "MAPA_ESPECIALIDADE_PATH", "data/interim/mapa_especialidade.json"
)
MODEL_PATH = caminho_de_env("MODEL_PATH", "data/model.pkl")
RUN_ID_PATH = caminho_de_env("RUN_ID_PATH", "data/interim/mlflow_run_id.txt")
RANDOM_STATE = modelo_ml.RANDOM_STATE

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME = os.environ.get("MLFLOW_EXPERIMENT_NAME", "saudeja-no-show")

# Sinaliza no MLflow runs geradas a partir da segregação do pipeline em
# stages (preprocess/train/validate) -- ver ADR sobre modularização.
# Runs anteriores a essa mudança não têm essa tag.
PIPELINE_TAG_KEY = "pipeline_arquitetura"
PIPELINE_TAG_VALUE = "segregado-preprocess-train-validate"
# ADR-014: todo run treinado com linha de origem externa é rastreável.
TAG_DADOS_EXTERNOS = "dados_externos"
ORIGEM_EXTERNA = "externa"


def _construir_modelo_kwargs():
    """Hiperparâmetros do LightGBM do params.yaml (os quatro históricos e os
    de regularização declarados). Mantido para `tune.py` e os scripts."""
    return modelo_ml.kwargs_lightgbm(PARAMS["model"])


def _balancear(X_train, y_train, cat_cols, k_neighbors):
    return modelo_ml.balancear_smotenc(X_train, y_train, cat_cols, k_neighbors)


def _params_do_mlflow(config):
    """Parâmetros logados no run: os mesmos nomes de antes para o LightGBM
    (o histórico do MLflow continua comparável), mais tipo e calibração."""
    params = {
        "model_tipo": config["tipo"],
        "balancing": config["balanceamento"],
        "calibracao": config["calibracao"],
    }
    if config["tipo"] == "lightgbm":
        params.update(config["lightgbm"])
    else:
        params["C"] = config["C"]
        params["random_state"] = RANDOM_STATE
    if config["balanceamento"] == "smotenc":
        params["k_neighbors"] = config["k_neighbors"]
    return params


def _logar_modelo(model):
    """LightGBM puro vai pelo flavor do LightGBM (como sempre); logística e
    modelo calibrado, pelo do scikit-learn."""
    if modelo_ml.tipo_do_modelo(model) == "lightgbm" and not modelo_ml.calibrado(model):
        import mlflow.lightgbm

        mlflow.lightgbm.log_model(model, artifact_path="model")
    else:
        import mlflow.sklearn

        mlflow.sklearn.log_model(model, artifact_path="model")


def treinar(X_train, y_train, k_neighbors=None, config=None):
    """Ajusta a configuração do params.yaml (ou `config`, se passada) só no
    fold de treino recebido. Loga parâmetros e o próprio modelo na run MLflow
    ativa -- métricas de validação são responsabilidade de validate.py."""
    config = dict(config or modelo_ml.config_do_modelo(PARAMS))
    if k_neighbors is not None:
        config["k_neighbors"] = int(k_neighbors)

    model = modelo_ml.ajustar(X_train, y_train, config, COLUNAS_CATEGORICAS)

    mlflow.log_params(_params_do_mlflow(config))
    for nome, valor in modelo_ml.descrever(model).items():
        if nome.startswith("calibracao_"):
            mlflow.log_metric(nome, valor)
    _logar_modelo(model)

    return model


def tem_origem_externa(dados_treino) -> bool:
    origem = dados_treino.get("origem")
    return origem is not None and bool((origem.astype(str) == ORIGEM_EXTERNA).any())


def main():
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)

    dados_treino = joblib.load(TRAIN_RAW_PATH)
    X_train, y_train = dados_treino["X"], dados_treino["y"]

    with open(MAPA_ESPECIALIDADE_PATH) as f:
        mapa_esp = json.load(f)

    with mlflow.start_run() as run:
        mlflow.set_tag(PIPELINE_TAG_KEY, PIPELINE_TAG_VALUE)
        if tem_origem_externa(dados_treino):
            mlflow.set_tag(TAG_DADOS_EXTERNOS, "true")
        mlflow.log_param("features_temporais", PARAMS.get("features", {}).get("temporais", False))

        model = treinar(X_train, y_train)

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
# -> Atendido depois: API de predição em src/api/ (FastAPI) e interface em
#    src/web/ (NiceGUI, ADR-013).
