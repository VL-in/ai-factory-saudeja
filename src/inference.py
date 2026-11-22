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
import joblib
import pandas as pd

import modelo as modelo_ml

# `calcular_model_version` mora em campeao.py (só biblioteca padrão, para o
# deploy rodar a guarda do campeão sem instalar o stack de ML) e é reexportada
# daqui: API, job e UI continuam chamando `inference.calcular_model_version`.
from campeao import calcular_model_version  # noqa: F401
from config_projeto import carregar_params
from features import adicionar_features_derivadas

# As mesmas listas do treino, importadas e não copiadas: a ordem das colunas é
# parte do contrato com o modelo (ver `verificar_assinatura`).
from preprocess import COLUNAS_CATEGORICAS, colunas_de_features, flags_de_features

PARAMS = carregar_params()


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


def carregar_modelo(
    path, features_temporais: bool | None = None, periodo_do_dia: bool | None = None
):
    """Carrega o artefato salvo por train.py: o modelo e o mapa de
    especialidade usados naquele treino (nunca recalculado aqui) -- e confere,
    antes de devolver, que o modelo espera exatamente as features que
    `construir_features` monta (contrato de features)."""
    artefato = joblib.load(path)
    model = artefato["model"]
    verificar_assinatura(model, features_temporais, periodo_do_dia)
    return model, artefato["mapa_especialidade"]


def _flags(features_temporais: bool | None, periodo_do_dia: bool | None) -> tuple[bool, bool]:
    """(temporais, periodo) pedidos ou, se omitidos, os do params.yaml -- a
    mesma fonte do treino. Sem features temporais, não há período do dia."""
    temporais_params, periodo_params = flags_de_features(PARAMS.get("features", {}))
    temporais = temporais_params if features_temporais is None else bool(features_temporais)
    periodo = (periodo_params and temporais) if periodo_do_dia is None else bool(periodo_do_dia)
    return temporais, periodo


def colunas_esperadas(
    features_temporais: bool | None = None, periodo_do_dia: bool | None = None
) -> list[str]:
    """Colunas, na ordem, que `construir_features` e `preprocess.preprocessar`
    produzem para o bloco `features` vigente (`preprocess.colunas_de_features`,
    a mesma função do treino)."""
    return colunas_de_features(*_flags(features_temporais, periodo_do_dia))


def _categoricas_do_modelo(model) -> list[str]:
    """Features que o modelo trata como categóricas (ver
    `modelo.categoricas_do_modelo`: no LightGBM, a linha do booster que vale
    ao predizer; na logística, as colunas do one-hot)."""
    return modelo_ml.categoricas_do_modelo(model)


def verificar_assinatura(
    model, features_temporais: bool | None = None, periodo_do_dia: bool | None = None
) -> None:
    """Nomes, ordem e categóricas do modelo contra o que este código monta,
    para qualquer tipo de modelo (`src/modelo.py`). Um modelo treinado com
    `features.temporais=false` carregado com `true` -- ou com as colunas em
    outra ordem -- prediria sem erro nenhum sobre colunas trocadas; aqui isso
    vira falha na carga, antes de qualquer predição (e, no job, antes de
    qualquer SMS)."""
    esperadas = colunas_esperadas(features_temporais, periodo_do_dia)
    obtidas = modelo_ml.colunas_do_modelo(model)
    if obtidas != esperadas:
        raise ModeloIncompativelError(
            f"o modelo espera as features {obtidas}, mas o código monta {esperadas} "
            "(nomes ou ordem divergem -- confira o bloco features em params.yaml)"
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
    payload: dict,
    mapa_especialidade: dict,
    features_temporais: bool | None = None,
    periodo_do_dia: bool | None = None,
):
    """Monta 1 linha com exatamente as colunas/ordem que
    preprocess.preprocessar() produz, a partir de um payload cru (dict).
    As flags de features, se omitidas, vêm de params.yaml (mesma fonte que o
    pipeline de treino usa) -- parametrizáveis para o teste de paridade."""
    return construir_features_lote(
        [payload], mapa_especialidade, features_temporais, periodo_do_dia
    )


def construir_features_lote(
    payloads: list[dict],
    mapa_especialidade: dict,
    features_temporais: bool | None = None,
    periodo_do_dia: bool | None = None,
):
    """`construir_features` para várias linhas de uma vez -- a suíte de
    sanidade do modelo (`src/sanidade_modelo.py`) prediz uma grade inteira de casos."""
    df = pd.DataFrame(payloads)
    df["sexo"] = df["sexo"].map({"F": 0, "M": 1})
    df = aplicar_mapa_especialidade(df, mapa_especialidade)

    temporais, periodo = _flags(features_temporais, periodo_do_dia)
    df = adicionar_features_derivadas(df, temporais, periodo)
    return df[colunas_de_features(temporais, periodo)]


def predizer(model, X):
    """Retorna a probabilidade da classe positiva (no-show) para cada
    linha de X -- o corte por threshold é decisão de quem chama (API/job),
    lida de params.yaml (decision.threshold), não deste módulo."""
    return model.predict_proba(X)[:, 1]
