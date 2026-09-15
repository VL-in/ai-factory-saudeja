"""
SaúdeJá — script de treino do classificador de no-show.
Camila S.

Comecei a "produtizar" o notebook aqui, mas ainda não terminei.
A ideia era rodar isso no cron mensal, mas falta a parte de API.
"""
import os
import pandas as pd
import numpy as np
import joblib
import yaml
from dotenv import load_dotenv
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, roc_auc_score, average_precision_score
from imblearn.over_sampling import SMOTENC
import lightgbm as lgb
import mlflow
import mlflow.lightgbm

from features import extrair_features_temporais

load_dotenv()

with open("params.yaml") as f:
    PARAMS = yaml.safe_load(f)

DATA_PATH = os.environ.get("DATA_PATH", "./data/consultas-historicas.csv")
MODEL_PATH = os.environ.get("MODEL_PATH", "./data/model.pkl")
RANDOM_STATE = 42

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME = os.environ.get("MLFLOW_EXPERIMENT_NAME", "saudeja-no-show")


def carregar_dados(path):
    df = pd.read_csv(path)
    print(f"[ok] {len(df)} linhas carregadas de {path}")
    return df


def preprocessar(df):
    # sexo -> 0/1
    df["sexo"] = df["sexo"].map({"F": 0, "M": 1})

    # especialidade -> label encoding
    # (TODO: trocar por OneHot ou Target Encoding; label encoding aqui é meio porco
    # mas o LightGBM segura)
    especialidades = sorted(df["especialidade"].unique())
    mapa_esp = {e: i for i, e in enumerate(especialidades)}
    df["especialidade"] = df["especialidade"].map(mapa_esp)

    features = [
        "idade",
        "sexo",
        "especialidade",
        "distancia_km",
        "dias_entre_agendamento_consulta",
        "historico_noshow",
    ]

    if PARAMS.get("features", {}).get("temporais", False):
        df = extrair_features_temporais(df)
        features += ["dia_de_semana", "horario"]

    X = df[features]
    y = df["no_show"]
    return X, y, mapa_esp


TEST_SIZE = 0.20

# Colunas categoricas (label-encoded como inteiros, nao numericas de verdade) --
# usadas tanto pelo SMOTENC (evita interpolar valores fracionarios que nao
# existem, ex. dia_de_semana=2.7) quanto pelo LightGBM (categorical_feature).
# dia_de_semana/horario so entram se a feature estiver ativa (ver preprocessar).
COLUNAS_CATEGORICAS = ["sexo", "especialidade", "dia_de_semana", "horario"]


def treinar(X, y, balancing="none", k_neighbors=3):
    # Split 80/20 estratificado pela classe alvo -- mesma estratégia do
    # notebook original da Camila (train_test_split com stratify=y).
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y
    )

    cat_cols = [c for c in COLUNAS_CATEGORICAS if c in X_train.columns]

    model_kwargs = dict(
        n_estimators=PARAMS["model"]["n_estimators"],
        learning_rate=PARAMS["model"]["learning_rate"],
        max_depth=PARAMS["model"]["max_depth"],
        num_leaves=PARAMS["model"]["num_leaves"],
        random_state=RANDOM_STATE,
    )

    if balancing == "class_weight":
        model_kwargs["class_weight"] = "balanced"

    if balancing == "smotenc":
        cat_idx = [X_train.columns.get_loc(c) for c in cat_cols]
        smote = SMOTENC(categorical_features=cat_idx, k_neighbors=k_neighbors, random_state=RANDOM_STATE)
        X_train, y_train = smote.fit_resample(X_train, y_train)

    model = lgb.LGBMClassifier(**model_kwargs)
    model.fit(X_train, y_train, categorical_feature=cat_cols)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]
    report = classification_report(y_test, y_pred, digits=3, output_dict=True, zero_division=0)

    if "1" not in report:
        # stratify=y evita isso na maioria dos casos, mas não há garantia
        # matemática absoluta em amostras muito pequenas -- torna o problema
        # visível em vez de reportar 0.0 como se fosse desempenho real do modelo.
        aviso = (
            "classe positiva ausente no fold de teste -- "
            "precision_1/recall_1/f1_1 desta run não são confiáveis"
        )
        print(f"[aviso] {aviso}")
        mlflow.set_tag("aviso_split", aviso)
    metrics_1 = report.get("1", {"precision": 0.0, "recall": 0.0, "f1-score": 0.0})
    metrics_0 = report.get("0", {"precision": 0.0, "recall": 0.0, "f1-score": 0.0})

    mlflow.log_params(model_kwargs)
    mlflow.log_param("balancing", balancing)          # <- chave pra comparar depois
    mlflow.log_metric("accuracy", report["accuracy"])
    mlflow.log_metric("roc_auc", roc_auc_score(y_test, y_proba))
    mlflow.log_metric("pr_auc", average_precision_score(y_test, y_proba))  # mais informativa que ROC-AUC aqui
    mlflow.log_metric("f1_1", metrics_1["f1-score"])
    mlflow.log_metric("precision_1", metrics_1["precision"])
    mlflow.log_metric("recall_1", metrics_1["recall"])
    mlflow.log_metric("f1_0", metrics_0["f1-score"])
    mlflow.log_metric("precision_0", metrics_0["precision"])
    mlflow.log_metric("recall_0", metrics_0["recall"])
    mlflow.log_metric("f1_macro", report["macro avg"]["f1-score"])
    mlflow.lightgbm.log_model(model, artifact_path="model")

    return model


def main():
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)

    with mlflow.start_run():
        mlflow.log_param("data_path", DATA_PATH)

        df = carregar_dados(DATA_PATH)
        X, y, mapa_esp = preprocessar(df)
        model = treinar(
            X, y,
            balancing=PARAMS["balancing"]["strategy"],
            k_neighbors=PARAMS["balancing"]["k_neighbors"],
        )
        joblib.dump({"model": model, "mapa_especialidade": mapa_esp}, MODEL_PATH)
        mlflow.log_artifact(MODEL_PATH)
        print(f"\n[ok] modelo salvo em {MODEL_PATH}")


if __name__ == "__main__":
    main()

# TODO: precisaria adicionar API com FastAPI / Flask / Streamlit
# pra o time de Produto integrar com o sistema das clínicas.
# Abri o ticket SAUDEJA-241 mas não vou conseguir pegar antes das férias.
