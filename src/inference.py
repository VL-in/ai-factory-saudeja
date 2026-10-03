"""
SaúdeJá — módulo de inferência reusável.
API e job diário importam daqui em vez de duplicar a
lógica de "payload cru -> features -> predição".

Contrato crítico: preprocess.preprocessar() AJUSTA (fit) um mapa de
especialidade novo a cada chamada -- correto em treino, onde o dataset
inteiro está disponível, mas destrutivo em produção: uma predição chega
uma linha por vez, então o mapa "fit" nessa única linha sempre mapearia a
especialidade recebida para 0, incorporando um enviesamento sistemático
silencioso. Este módulo só APLICA o mapa já treinado e salvo em
data/model.pkl (ver src/train.py:95), nunca recalcula.
"""
import re

import joblib
import pandas as pd

# `calcular_model_version` mora em campeao.py (só biblioteca padrão, para o
# deploy rodar a guarda do campeão sem instalar o stack de ML) e é reexportada
# daqui: API, job e UI continuam chamando `inference.calcular_model_version`.
from campeao import calcular_model_version  # noqa: F401
from config_projeto import carregar_params
from features import extrair_features_temporais
from preprocess import COLUNAS_CATEGORICAS

PARAMS = carregar_params()

COLUNAS_BASE = [
    "idade",
    "sexo",
    "especialidade",
    "distancia_km",
    "dias_entre_agendamento_consulta",
    "historico_noshow",
]


class ModeloIncompativelError(Exception):
    """O artefato carregado não espera as features que este código monta
    (nomes, ordem ou categóricas) -- ver `verificar_assinatura`."""


class EspecialidadeDesconhecidaError(Exception):
    """Levantada quando o payload traz uma especialidade fora do mapa
    fixado no treino. Em preprocess.py isso vira NaN silencioso (aceitável
    lá, onde o mapa é fit no próprio dataset de treino); em produção o mapa
    é fixo, então um valor novo precisa virar erro claro (a API
    traduz isso para HTTP 422) em vez de uma predição sem sentido sobre NaN.
    """


def carregar_modelo(path, features_temporais: bool | None = None):
    """Carrega o artefato salvo por train.py: o modelo e o mapa de
    especialidade usados naquele treino (nunca recalculado aqui) -- e confere,
    antes de devolver, que o modelo espera exatamente as features que
    `construir_features` monta (contrato de features)."""
    artefato = joblib.load(path)
    model = artefato["model"]
    verificar_assinatura(model, features_temporais)
    return model, artefato["mapa_especialidade"]


def colunas_esperadas(features_temporais: bool | None = None) -> list[str]:
    """Colunas, na ordem, que `construir_features` e `preprocess.preprocessar`
    produzem para o valor vigente de `features.temporais`."""
    if features_temporais is None:
        features_temporais = PARAMS.get("features", {}).get("temporais", False)
    colunas = list(COLUNAS_BASE)
    if features_temporais:
        colunas += ["dia_de_semana", "horario"]
    return colunas


_LINHA_CATEGORICAS = re.compile(r"^\[categorical_feature: ?([\d,]*)\]$", re.MULTILINE)


def _categoricas_do_modelo(model) -> list[str]:
    """Features que o booster trata como categóricas, lidas do modelo
    serializado -- é essa linha que o LightGBM usa ao predizer."""
    booster = model.booster_
    achado = _LINHA_CATEGORICAS.search(booster.model_to_string())
    indices = [int(i) for i in achado.group(1).split(",") if i] if achado else []
    nomes = booster.feature_name()
    return [nomes[i] for i in indices]


def verificar_assinatura(model, features_temporais: bool | None = None) -> None:
    """Nomes, ordem e categóricas do modelo (`booster_.feature_name()`) contra
    o que este código monta. Um modelo treinado com `features.temporais=false`
    carregado com `true` -- ou com as colunas em outra ordem -- prediria sem
    erro nenhum sobre colunas trocadas; aqui isso vira falha na carga, antes
    de qualquer predição (e, no job, antes de qualquer SMS)."""
    esperadas = colunas_esperadas(features_temporais)
    obtidas = list(model.booster_.feature_name())
    if obtidas != esperadas:
        raise ModeloIncompativelError(
            f"o modelo espera as features {obtidas}, mas o código monta {esperadas} "
            "(nomes ou ordem divergem -- confira features.temporais em params.yaml)"
        )
    categoricas_esperadas = sorted(c for c in COLUNAS_CATEGORICAS if c in esperadas)
    categoricas_obtidas = sorted(_categoricas_do_modelo(model))
    if categoricas_obtidas != categoricas_esperadas:
        raise ModeloIncompativelError(
            f"o modelo trata como categóricas {categoricas_obtidas}, mas o código espera "
            f"{categoricas_esperadas}"
        )


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


def construir_features(
    payload: dict, mapa_especialidade: dict, features_temporais: bool | None = None
):
    """Monta 1 linha com exatamente as colunas/ordem que
    preprocess.preprocessar() produz, a partir de um payload cru (dict).
    features_temporais, se omitido, vem de params.yaml (mesma fonte que o
    pipeline de treino usa) -- parametrizável para o teste de paridade."""
    return construir_features_lote([payload], mapa_especialidade, features_temporais)


def construir_features_lote(
    payloads: list[dict], mapa_especialidade: dict, features_temporais: bool | None = None
):
    """`construir_features` para várias linhas de uma vez -- a suíte de
    sanidade do modelo (`src/sanidade_modelo.py`) prediz uma grade inteira de casos."""
    df = pd.DataFrame(payloads)
    df["sexo"] = df["sexo"].map({"F": 0, "M": 1})
    df = aplicar_mapa_especialidade(df, mapa_especialidade)

    features = colunas_esperadas(features_temporais)
    if "dia_de_semana" in features:
        df = extrair_features_temporais(df)

    return df[features]


def predizer(model, X):
    """Retorna a probabilidade da classe positiva (no-show) para cada
    linha de X -- o corte por threshold é decisão de quem chama (API/job),
    lida de params.yaml (decision.threshold), não deste módulo."""
    return model.predict_proba(X)[:, 1]
