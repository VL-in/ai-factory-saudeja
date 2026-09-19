"""
Extração de features derivadas a partir de dados brutos.
Usado tanto pelo pipeline de treino (train.py) quanto pela futura API
de inferência -- extrair do mesmo jeito nos dois lugares evita
training-serving skew.
"""
import pandas as pd

COLUNA_TIMESTAMP = "data_hora_agendada"


def extrair_features_temporais(df):
    # Copia defensiva: devolve um novo DataFrame em vez de anexar as colunas
    # no objeto do chamador -- sem isso preprocessar()/construir_features()
    # mutariam o df cru que receberam de fora.
    df = df.copy()
    timestamp = pd.to_datetime(df[COLUNA_TIMESTAMP])
    df["dia_de_semana"] = timestamp.dt.dayofweek  # 0=segunda ... 6=domingo
    df["horario"] = timestamp.dt.hour             # 0-23 (minutos descartados de proposito)
    return df
