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
from sklearn.model_selection import StratifiedGroupKFold, train_test_split

import contrato_features
import regras_dado_real
from config_projeto import caminho_de_env, carregar_params
from features import adicionar_features_derivadas

PARAMS = carregar_params()

DATA_PATH = caminho_de_env("DATA_PATH", "data/consultas-historicas.csv")
TRAIN_RAW_PATH = caminho_de_env("TRAIN_RAW_PATH", "data/interim/train_raw.pkl")
TEST_PATH = caminho_de_env("TEST_PATH", "data/interim/test.pkl")
MAPA_ESPECIALIDADE_PATH = caminho_de_env(
    "MAPA_ESPECIALIDADE_PATH", "data/interim/mapa_especialidade.json"
)

RANDOM_STATE = 42
TEST_SIZE = 0.20

# Features do modelo, na ordem em que o treino as monta. A inferência
# (`src/inference.py`) importa esta mesma lista: duas cópias poderiam divergir
# em silêncio, e a ordem das colunas é parte do contrato com o modelo.
# dia_de_semana/horario entram depois, só com `features.temporais`.
COLUNAS_BASE = [
    "idade",
    "sexo",
    "especialidade",
    "distancia_km",
    "dias_entre_agendamento_consulta",
    "historico_noshow",
]

# Colunas categoricas (label-encoded como inteiros, nao numericas de verdade) --
# usadas tanto pelo SMOTENC (evita interpolar valores fracionarios que nao
# existem, ex. dia_de_semana=2.7) quanto pelo LightGBM (categorical_feature).
# dia_de_semana/horario so entram se a feature estiver ativa (ver preprocessar).
COLUNAS_CATEGORICAS = ["sexo", "especialidade", "dia_de_semana", "horario", "periodo"]

# Com `features.periodo_do_dia`, o `horario` cru (10 niveis) sai e entram o
# periodo (3 niveis) e as duas flags. Desligado, a lista e' a de sempre.
COLUNAS_PERIODO = ["periodo", "fim_expediente_sexta", "fim_de_semana"]


def flags_de_features(params_features=None) -> tuple[bool, bool]:
    """(temporais, periodo_do_dia) do bloco `features` do params.yaml."""
    cfg = PARAMS.get("features", {}) if params_features is None else params_features
    return bool(cfg.get("temporais", False)), bool(cfg.get("periodo_do_dia", False))


def colunas_de_features(temporais: bool, periodo: bool) -> list[str]:
    """Colunas do modelo, na ordem -- fonte unica para o treino
    (`preprocessar`) e para o serving (`inference.colunas_esperadas`)."""
    if periodo and not temporais:
        raise ValueError("features.periodo_do_dia exige features.temporais")
    colunas = list(COLUNAS_BASE)
    if temporais:
        colunas.append("dia_de_semana")
        colunas += COLUNAS_PERIODO if periodo else ["horario"]
    return colunas


def carregar_dados(path):
    df = pd.read_csv(path)
    print(f"[ok] {len(df)} linhas carregadas de {path}")
    return df


def aplicar_contrato(df):
    """Contrato de features (`src/contrato_features.py`) no caminho de treino: coerção
    explícita de tipo e regra de negócio de cada linha.

    O stage `validate_data` do dvc.yaml já barra o dataset antes daqui, e com
    relatório; isto é a defesa em profundidade para quem chama `preprocessar`
    por outro caminho (testes, notebook, `tune.py`). A especialidade é checada
    contra as do próprio dataset -- confrontá-la com a lista do cadastro é
    trabalho do `validate_data`, que tem a configuração como dependência."""
    df = contrato_features.coagir_colunas_numericas(df)
    especialidades = {e for e in df["especialidade"] if isinstance(e, str)}
    violacoes = contrato_features.violacoes_do_dataframe(df, especialidades)
    if violacoes:
        raise contrato_features.ViolacaoDoContrato(
            "dataset", contrato_features.resumir_violacoes(violacoes)
        )
    return df


def preprocessar(df):
    # Copia defensiva: preprocessar() nao deve alterar o DataFrame do
    # chamador -- carregar_dados() devolve um df que pode ser reusado cru
    # (EDA, auditoria) depois desta chamada.
    df = aplicar_contrato(df).copy()

    # sexo -> 0/1
    df["sexo"] = df["sexo"].map({"F": 0, "M": 1})

    # especialidade -> label encoding
    # (TODO: trocar por OneHot ou Target Encoding; label encoding aqui é meio porco
    # mas o LightGBM segura)
    especialidades = sorted(df["especialidade"].unique())
    mapa_esp = {e: i for i, e in enumerate(especialidades)}
    df["especialidade"] = df["especialidade"].map(mapa_esp)

    temporais, periodo = flags_de_features()
    df = adicionar_features_derivadas(df, temporais, periodo)
    features = colunas_de_features(temporais, periodo)

    X = df[features]
    y = df["no_show"]
    return X, y, mapa_esp


def dividir_treino_teste(X, y, grupos=None):
    """Split 80/20 estratificado pela classe alvo, feito ANTES do
    balanceamento (SMOTE-NC roda em train.py, só sobre o fold de treino).

    Com `grupos` (o `id_paciente`), todas as consultas de um paciente caem do
    mesmo lado: o primeiro fold de um `StratifiedGroupKFold` de 5 (20% das
    linhas, estratificado o quanto os grupos permitem). Com id estável -- o
    pseudônimo da produção e o do dado externo --, o split aleatório poria o
    mesmo paciente no treino e no teste, e a métrica mediria memória."""
    if grupos is None:
        return train_test_split(X, y, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y)
    cv = StratifiedGroupKFold(
        n_splits=round(1 / TEST_SIZE), shuffle=True, random_state=RANDOM_STATE
    )
    idx_treino, idx_teste = next(cv.split(X, y, groups=grupos))
    return X.iloc[idx_treino], X.iloc[idx_teste], y.iloc[idx_treino], y.iloc[idx_teste]


def modo_de_split(df) -> str:
    """Modo de split da regra 5.2 (`regras_dado_real.modo_de_split`) para o
    dataset. Sem a coluna `origem` (export anterior a ela, só semente), o
    aleatório de sempre. O cronológico (mais de `dados.split` dias de
    produção) ainda não é aplicado: cai no split por paciente, que já evita
    o vazamento, e o preprocess avisa."""
    if regras_dado_real.COLUNA_ORIGEM not in df.columns:
        return regras_dado_real.SPLIT_ALEATORIO
    origem = regras_dado_real.origem_por_linha(df)
    modo = regras_dado_real.modo_de_split(df, origem, PARAMS)
    if modo == regras_dado_real.SPLIT_CRONOLOGICO:
        print(
            "[aviso] a regra pede split cronologico (mais dias de producao que "
            "dados.split.dias_producao_cronologico); ainda nao aplicado -- usando o split "
            "por paciente"
        )
        return regras_dado_real.SPLIT_POR_GRUPO
    return modo


def _origem_de(origem, X):
    return None if origem is None else origem.loc[X.index]


def main():
    for path in (TRAIN_RAW_PATH, TEST_PATH, MAPA_ESPECIALIDADE_PATH):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    df = carregar_dados(DATA_PATH)
    X, y, mapa_esp = preprocessar(df)
    modo = modo_de_split(df)
    grupos = (
        df.loc[X.index, "id_paciente"].astype(str).to_numpy()
        if modo == regras_dado_real.SPLIT_POR_GRUPO
        else None
    )
    X_train, X_test, y_train, y_test = dividir_treino_teste(X, y, grupos)

    # A origem (semente | producao | externa) viaja junto, fora de X: não é
    # feature, mas o treino marca o run com `dados_externos` quando há linha
    # externa (ADR-014) e as métricas podem ser separadas por origem.
    origem = df["origem"].astype(str) if "origem" in df.columns else None
    joblib.dump(
        {"X": X_train, "y": y_train, "origem": _origem_de(origem, X_train), "modo_split": modo},
        TRAIN_RAW_PATH,
    )
    joblib.dump(
        {"X": X_test, "y": y_test, "origem": _origem_de(origem, X_test), "modo_split": modo},
        TEST_PATH,
    )
    with open(MAPA_ESPECIALIDADE_PATH, "w") as f:
        json.dump(mapa_esp, f)

    print(
        f"[ok] pré-processamento concluído -- treino: {len(X_train)} linhas "
        f"(pré-balanceamento), teste: {len(X_test)} linhas (isolado do balanceamento), "
        f"split {modo}"
    )


if __name__ == "__main__":
    main()
