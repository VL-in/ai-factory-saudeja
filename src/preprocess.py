"""
SaúdeJá — etapa de pré-processamento do pipeline de treino.
Lê o dataset bruto, aplica o feature engineering e faz o split treino/teste
(estratificado) ANTES de qualquer balanceamento -- o SMOTE-NC roda só na
etapa de treino (train.py), sobre o fold de treino aqui gerado, para o fold
de teste nunca ver dado sintético/balanceado.
"""
import json
import os

import joblib
import pandas as pd
import yaml
from sklearn.model_selection import train_test_split

from features import extrair_features_temporais

with open("params.yaml") as f:
    PARAMS = yaml.safe_load(f)

DATA_PATH = os.environ.get("DATA_PATH", "./data/consultas-historicas.csv")
TRAIN_RAW_PATH = os.environ.get("TRAIN_RAW_PATH", "./data/interim/train_raw.pkl")
TEST_PATH = os.environ.get("TEST_PATH", "./data/interim/test.pkl")
MAPA_ESPECIALIDADE_PATH = os.environ.get(
    "MAPA_ESPECIALIDADE_PATH", "./data/interim/mapa_especialidade.json"
)

RANDOM_STATE = 42
TEST_SIZE = 0.20

# Colunas categoricas (label-encoded como inteiros, nao numericas de verdade) --
# usadas tanto pelo SMOTENC (evita interpolar valores fracionarios que nao
# existem, ex. dia_de_semana=2.7) quanto pelo LightGBM (categorical_feature).
# dia_de_semana/horario so entram se a feature estiver ativa (ver preprocessar).
COLUNAS_CATEGORICAS = ["sexo", "especialidade", "dia_de_semana", "horario"]


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


def dividir_treino_teste(X, y):
    """Split 80/20 estratificado pela classe alvo, feito ANTES do
    balanceamento (SMOTE-NC roda em train.py, só sobre o fold de treino)."""
    return train_test_split(
        X, y, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y
    )


def main():
    for path in (TRAIN_RAW_PATH, TEST_PATH, MAPA_ESPECIALIDADE_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    df = carregar_dados(DATA_PATH)
    X, y, mapa_esp = preprocessar(df)
    X_train, X_test, y_train, y_test = dividir_treino_teste(X, y)

    joblib.dump({"X": X_train, "y": y_train}, TRAIN_RAW_PATH)
    joblib.dump({"X": X_test, "y": y_test}, TEST_PATH)
    with open(MAPA_ESPECIALIDADE_PATH, "w") as f:
        json.dump(mapa_esp, f)

    print(
        f"[ok] pré-processamento concluído -- treino: {len(X_train)} linhas "
        f"(pré-balanceamento), teste: {len(X_test)} linhas (isolado do balanceamento)"
    )


if __name__ == "__main__":
    main()
