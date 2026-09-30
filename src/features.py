"""
Extração de features derivadas a partir de dados brutos.
Usado tanto pelo pipeline de treino (train.py) quanto pela futura API
de inferência -- extrair do mesmo jeito nos dois lugares evita
training-serving skew.
"""
from datetime import date

import pandas as pd

from config_projeto import fuso_da_clinica

COLUNA_TIMESTAMP = "data_hora_agendada"


def calcular_idade(data_nascimento: date, referencia: date) -> int:
    """Idade em anos completos na data `referencia` (a consulta, não "hoje")
    -- é o mesmo conceito que `idade` já representa no dataset histórico
    (data/consultas-historicas.csv), só que agora derivado da data de
    nascimento gravada no cadastro (src/ui/) em vez de digitado direto.
    `preprocess.py`/`train.py` não mudam: continuam lendo `idade` pronta do
    CSV -- esta função entra só na camada de montagem do payload de
    produção (job D-2, src/jobs/inferencia_diaria.py)."""
    idade = referencia.year - data_nascimento.year
    if (referencia.month, referencia.day) < (data_nascimento.month, data_nascimento.day):
        idade -= 1
    return idade


def extrair_features_temporais(df):
    # Copia defensiva: devolve um novo DataFrame em vez de anexar as colunas
    # no objeto do chamador -- sem isso preprocessar()/construir_features()
    # mutariam o df cru que receberam de fora.
    df = df.copy()
    timestamp = pd.to_datetime(df[COLUNA_TIMESTAMP])
    # Timestamp com fuso (vindo do banco, em UTC) vira hora da clínica antes de
    # extrair dia/hora -- o treino aprendeu a grade local (8h-18h). Sem fuso,
    # já é local (dataset histórico). Ver config_projeto.para_horario_da_clinica.
    if timestamp.dt.tz is not None:
        timestamp = timestamp.dt.tz_convert(fuso_da_clinica())
    df["dia_de_semana"] = timestamp.dt.dayofweek  # 0=segunda ... 6=domingo
    df["horario"] = timestamp.dt.hour             # 0-23 (minutos descartados de proposito)
    return df
