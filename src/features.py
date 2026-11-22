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


# Periodo do dia (features.periodo_do_dia): 3 niveis no lugar das ~10 horas da
# grade. Com ~300 linhas de treino, varias horas tinham menos de 30 linhas -- o
# LightGBM sobreajustava categoria rara e a logistica ganhava um coeficiente
# instavel por hora. Fronteiras: a manha vai ate' o almoco (12h), o fim de
# tarde comeca as 16h (as duas ultimas horas do expediente).
PERIODO_MANHA = 0
PERIODO_INICIO_TARDE = 1
PERIODO_FIM_TARDE = 2
HORA_INICIO_TARDE = 12
HORA_FIM_TARDE = 16
SEXTA = 4
HORA_FIM_EXPEDIENTE = 17  # o slot das 17h-18h, o recorte "sexta 17h" herdado


def periodo_do_dia(hora: pd.Series) -> pd.Series:
    periodo = pd.Series(PERIODO_MANHA, index=hora.index)
    periodo[hora >= HORA_INICIO_TARDE] = PERIODO_INICIO_TARDE
    periodo[hora >= HORA_FIM_TARDE] = PERIODO_FIM_TARDE
    return periodo


def extrair_features_periodo(df):
    """`periodo` (0 manha, 1 inicio de tarde, 2 fim de tarde),
    `fim_expediente_sexta` (sexta, slot das 17h) e `fim_de_semana` (sabado;
    domingo a clinica nao abre). Parte de `dia_de_semana`/`horario` ja
    extraidos por `extrair_features_temporais` -- a mesma funcao no treino e no
    serving, entao a derivacao nao tem como divergir."""
    df = df.copy()
    df["periodo"] = periodo_do_dia(df["horario"])
    df["fim_expediente_sexta"] = (
        (df["dia_de_semana"] == SEXTA) & (df["horario"] >= HORA_FIM_EXPEDIENTE)
    ).astype(int)
    df["fim_de_semana"] = (df["dia_de_semana"] >= 5).astype(int)
    return df


def adicionar_features_derivadas(df, temporais: bool, periodo: bool):
    """Ponto unico de derivacao, chamado por `preprocess.preprocessar` e por
    `inference.construir_features_lote`."""
    if periodo and not temporais:
        raise ValueError("features.periodo_do_dia exige features.temporais")
    if temporais:
        df = extrair_features_temporais(df)
    if periodo:
        df = extrair_features_periodo(df)
    return df
