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
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, roc_auc_score
import lightgbm as lgb

DATA_PATH = os.environ.get("DATA_PATH", "./data/consultas-historicas.csv")
MODEL_PATH = os.environ.get("MODEL_PATH", "./model.pkl")
RANDOM_STATE = 42


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

    model = lgb.LGBMClassifier(
        n_estimators=200,
        learning_rate=0.05,
        max_depth=6,
        num_leaves=31,
        random_state=RANDOM_STATE,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    print("\n=== Classification report ===")
    print(classification_report(y_test, y_pred, digits=3))
    print(f"ROC-AUC: {roc_auc_score(y_test, y_proba):.3f}")
    return model


def main():
    df = carregar_dados(DATA_PATH)
    X, y, mapa_esp = preprocessar(df)
    model = treinar(X, y)
    joblib.dump({"model": model, "mapa_especialidade": mapa_esp}, MODEL_PATH)
    print(f"\n[ok] modelo salvo em {MODEL_PATH}")


if __name__ == "__main__":
    main()

# TODO: precisaria adicionar API com FastAPI / Flask / Streamlit
# pra o time de Produto integrar com o sistema das clínicas.
# Abri o ticket SAUDEJA-241 mas não vou conseguir pegar antes das férias.
