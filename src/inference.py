"""
SaúdeJá — módulo de inferência reusável (Passo 1 do plano de implementação).
API (Passo 3) e job diário (Passo 6) importam daqui em vez de duplicar a
lógica de "payload cru -> features -> predição".

Contrato crítico: preprocess.preprocessar() AJUSTA (fit) um mapa de
especialidade novo a cada chamada -- correto em treino, onde o dataset
inteiro está disponível, mas destrutivo em produção: uma predição chega
uma linha por vez, então o mapa "fit" nessa única linha sempre mapearia a
especialidade recebida para 0, incorporando um enviesamento sistemático
silencioso. Este módulo só APLICA o mapa já treinado e salvo em
data/model.pkl (ver src/train.py:95), nunca recalcula.
"""
import joblib
import pandas as pd
import yaml

from features import extrair_features_temporais
from preprocess import COLUNAS_CATEGORICAS  # noqa: F401 -- reexportado p/ API/job (Passo 3/6)

with open("params.yaml") as f:
    PARAMS = yaml.safe_load(f)

COLUNAS_BASE = [
    "idade",
    "sexo",
    "especialidade",
    "distancia_km",
    "dias_entre_agendamento_consulta",
    "historico_noshow",
]


class EspecialidadeDesconhecidaError(Exception):
    """Levantada quando o payload traz uma especialidade fora do mapa
    fixado no treino. Em preprocess.py isso vira NaN silencioso (aceitável
    lá, onde o mapa é fit no próprio dataset de treino); em produção o mapa
    é fixo, então um valor novo precisa virar erro claro (a API, Passo 3,
    traduz isso para HTTP 422) em vez de uma predição sem sentido sobre NaN.
    """


def carregar_modelo(path):
    """Carrega o artefato salvo por train.py: o modelo e o mapa de
    especialidade usados naquele treino (nunca recalculado aqui)."""
    artefato = joblib.load(path)
    return artefato["model"], artefato["mapa_especialidade"]


def aplicar_mapa_especialidade(df, mapa_especialidade):
    """Mesmo mapeamento que preprocess.preprocessar() faz (label encoding
    de especialidade), mas aplicando um mapa já fixado em vez de ajustar um
    novo -- e levantando erro claro para especialidade fora do mapa."""
    desconhecidas = set(df["especialidade"]) - set(mapa_especialidade)
    if desconhecidas:
        raise EspecialidadeDesconhecidaError(
            f"especialidade(s) desconhecida(s): {sorted(desconhecidas)} -- "
            f"esperado uma de {sorted(mapa_especialidade)}"
        )
    df = df.copy()
    df["especialidade"] = df["especialidade"].map(mapa_especialidade)
    return df


def construir_features(payload: dict, mapa_especialidade: dict, features_temporais: bool = None):
    """Monta 1 linha com exatamente as colunas/ordem que
    preprocess.preprocessar() produz, a partir de um payload cru (dict).
    features_temporais, se omitido, vem de params.yaml (mesma fonte que o
    pipeline de treino usa) -- parametrizável para o teste de paridade."""
    if features_temporais is None:
        features_temporais = PARAMS.get("features", {}).get("temporais", False)

    df = pd.DataFrame([payload])
    df["sexo"] = df["sexo"].map({"F": 0, "M": 1})
    df = aplicar_mapa_especialidade(df, mapa_especialidade)

    features = list(COLUNAS_BASE)
    if features_temporais:
        df = extrair_features_temporais(df)
        features += ["dia_de_semana", "horario"]

    return df[features]


def predizer(model, X):
    """Retorna a probabilidade da classe positiva (no-show) para cada
    linha de X -- o corte por threshold é decisão de quem chama (API/job),
    lida de params.yaml (decision.threshold), não deste módulo."""
    return model.predict_proba(X)[:, 1]
