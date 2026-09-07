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
from dotenv import load_dotenv
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, roc_auc_score
import lightgbm as lgb
import mlflow
import mlflow.lightgbm

load_dotenv()

DATA_PATH = os.environ.get("DATA_PATH", "./data/consultas-historicas.csv")
MODEL_PATH = os.environ.get("MODEL_PATH", "./data/model.pkl")
RANDOM_STATE = 42

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "sqlite:///data/mlflow.db")
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
    X = df[features]
    y = df["no_show"]
    return X, y, mapa_esp


def treinar(X, y):
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=RANDOM_STATE, stratify=y
    )

    n_estimators = 200
    learning_rate = 0.05
    max_depth = 6
    num_leaves = 31

    model = lgb.LGBMClassifier(
        n_estimators=n_estimators,
        learning_rate=learning_rate,
        max_depth=max_depth,
        num_leaves=num_leaves,
        random_state=RANDOM_STATE,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    auc = roc_auc_score(y_test, y_proba)
    print("\n=== Classification report ===")
    print(classification_report(y_test, y_pred, digits=3))
    print(f"ROC-AUC: {auc:.3f}")

    mlflow.log_params({
        "n_estimators": n_estimators,
        "learning_rate": learning_rate,
        "max_depth": max_depth,
        "num_leaves": num_leaves,
        "random_state": RANDOM_STATE,
    })
    mlflow.log_param("test_size", 0.20)
    mlflow.log_metric("roc_auc", auc)
    report = classification_report(y_test, y_pred, digits=3, output_dict=True)
    mlflow.log_metric("accuracy", report["accuracy"])
    mlflow.log_metric("precision_1", report["1"]["precision"])
    mlflow.log_metric("recall_1", report["1"]["recall"])
    mlflow.log_metric("f1_1", report["1"]["f1-score"])
    mlflow.lightgbm.log_model(model, artifact_path="model")

    return model


def main():
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)

    with mlflow.start_run():
        mlflow.log_param("data_path", DATA_PATH)

        df = carregar_dados(DATA_PATH)
        X, y, mapa_esp = preprocessar(df)
        model = treinar(X, y)
        joblib.dump({"model": model, "mapa_especialidade": mapa_esp}, MODEL_PATH)
        mlflow.log_artifact(MODEL_PATH)
        print(f"\n[ok] modelo salvo em {MODEL_PATH}")


if __name__ == "__main__":
    main()

# TODO: precisaria adicionar API com FastAPI / Flask / Streamlit
# pra o time de Produto integrar com o sistema das clínicas.
# Abri o ticket SAUDEJA-241 mas não vou conseguir pegar antes das férias.
