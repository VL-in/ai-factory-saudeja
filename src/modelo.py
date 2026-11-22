"""
SaúdeJá — fábrica do classificador de no-show e acesso uniforme a ele.

Um lugar só para três perguntas que treino, validação cruzada e serving
precisam responder do mesmo jeito (código duplicado entre eles é skew de
código):

1. **Como ajustar** uma configuração (`model.tipo`, `balancing.strategy`,
   `model.calibracao` e hiperparâmetros do `params.yaml`) -- `ajustar`.
   `src/train.py`, `scripts/avaliar_cv.py` e `scripts/simular_dados.py` chamam
   esta função; nenhum deles monta o estimador por conta própria.
2. **O que o modelo espera** (colunas, ordem, categóricas, maior valor visto
   por feature numérica) -- `colunas_do_modelo`, `categoricas_do_modelo`,
   `maximos_vistos`. É o que `inference.verificar_assinatura` e a suíte de
   sanidade conferem contra o código.
3. **Em que espaço a explicação soma** -- `margem` devolve o log-odds da
   probabilidade que o modelo entrega, e `base_e_calibracao` diz como ele se
   relaciona com a margem do estimador de base. `src/explain.py` usa isso para
   a aditividade do SHAP valer também no modelo calibrado.

Tipos de modelo:
- `lightgbm`: `LGBMComMargem` (um `LGBMClassifier` que expõe a margem bruta
  como `decision_function`), com as categóricas declaradas.
- `logistica`: `Pipeline` do scikit-learn com one-hot nas categóricas e
  padronização nas numéricas, salvo inteiro no `model.pkl` -- o serving aplica
  a mesma transformação que o treino ajustou, sem recalcular nada.

Calibração (`model.calibracao: sigmoid`): `CalibratedClassifierCV` com
`ensemble=False`. A base é ajustada no fold de treino inteiro, e a sigmoide de
Platt é ajustada nas margens fora da amostra (CV de 5 folds). Por isso a
probabilidade final é `sigmoide(escala * margem_base + deslocamento)`: uma
transformação AFIM da margem da base, que preserva a aditividade do SHAP
(basta escalar as contribuições). Com `class_weight`, calibrar é o que traz a
probabilidade de volta para a prevalência real.

O artefato antigo (um `LGBMClassifier` puro, sem calibração) continua aceito
por todas as funções de acesso: é o campeão em produção.

Só depende do que a imagem de serving instala (scikit-learn, LightGBM, numpy,
pandas). O SMOTE-NC (`imbalanced-learn`, só do ambiente de treino) é
importado dentro da função que o usa.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

RANDOM_STATE = 42
FOLDS_CALIBRACAO = 5

TIPOS = ("lightgbm", "logistica")
BALANCEAMENTOS = ("smotenc", "class_weight", "none")
CALIBRACOES = ("none", "sigmoid")

# Defaults quando o params.yaml não traz o campo: a configuração do campeão em
# produção (LightGBM + SMOTE-NC, sem calibração). Um params.yaml antigo treina
# exatamente o que treinava antes.
TIPO_PADRAO = "lightgbm"
BALANCEAMENTO_PADRAO = "smotenc"
CALIBRACAO_PADRAO = "none"
C_LOGISTICA_PADRAO = 0.3
K_NEIGHBORS_PADRAO = 3

# Hiperparâmetros de regularização do LightGBM: só entram no estimador quando
# o params.yaml os declara (ausentes = default do LightGBM, como antes).
REGULARIZACAO_LIGHTGBM = (
    "min_child_samples",
    "reg_lambda",
    "subsample",
    "subsample_freq",
    "colsample_bytree",
)


# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------
def config_do_modelo(params: Mapping[str, Any]) -> dict[str, Any]:
    """A configuração de modelo descrita no `params.yaml`, com os defaults do
    campeão para o que faltar. Valida os nomes e as combinações."""
    modelo = dict(params.get("model", {}))
    balancing = dict(params.get("balancing", {}))
    config = {
        "tipo": modelo.get("tipo", TIPO_PADRAO),
        "balanceamento": balancing.get("strategy", BALANCEAMENTO_PADRAO),
        "calibracao": modelo.get("calibracao", CALIBRACAO_PADRAO),
        "k_neighbors": int(balancing.get("k_neighbors", K_NEIGHBORS_PADRAO)),
        "lightgbm": kwargs_lightgbm(modelo),
        "C": float(modelo.get("logistica", {}).get("C", C_LOGISTICA_PADRAO)),
    }
    validar_config(config)
    return config


def validar_config(config: Mapping[str, Any]) -> None:
    if config["tipo"] not in TIPOS:
        raise ValueError(f"model.tipo deve ser um de {TIPOS}, veio {config['tipo']!r}")
    if config["balanceamento"] not in BALANCEAMENTOS:
        raise ValueError(
            f"balancing.strategy deve ser um de {BALANCEAMENTOS}, veio {config['balanceamento']!r}"
        )
    if config["calibracao"] not in CALIBRACOES:
        raise ValueError(
            f"model.calibracao deve ser um de {CALIBRACOES}, veio {config['calibracao']!r}"
        )
    if config["calibracao"] != "none" and config["balanceamento"] == "smotenc":
        # A calibração ajusta a sigmoide nas margens fora da amostra do fold de
        # treino. Com SMOTE-NC antes dela, essas margens viriam do dado 50/50
        # sintético, e a probabilidade calibrada continuaria inflada.
        raise ValueError(
            "model.calibracao exige balancing.strategy class_weight ou none: com "
            "SMOTE-NC a calibração aprenderia a prevalência sintética de 50%"
        )


def kwargs_lightgbm(params_model: Mapping[str, Any]) -> dict[str, Any]:
    """Hiperparâmetros do LightGBM a partir do bloco `model` do params.yaml.
    Os quatro históricos são obrigatórios; os de regularização entram só se
    declarados."""
    kwargs: dict[str, Any] = {
        "n_estimators": params_model["n_estimators"],
        "learning_rate": params_model["learning_rate"],
        "max_depth": params_model["max_depth"],
        "num_leaves": params_model["num_leaves"],
    }
    for nome in REGULARIZACAO_LIGHTGBM:
        if params_model.get(nome) is not None:
            kwargs[nome] = params_model[nome]
    kwargs["random_state"] = RANDOM_STATE
    return kwargs


# --------------------------------------------------------------------------
# Estimadores
# --------------------------------------------------------------------------
class LGBMComMargem(lgb.LGBMClassifier):
    """`LGBMClassifier` que expõe a margem bruta (log-odds) como
    `decision_function`. É o que faz o `CalibratedClassifierCV` ajustar a
    sigmoide sobre a margem, e não sobre a probabilidade: a probabilidade
    calibrada fica afim na margem, e o SHAP da árvore continua somando."""

    def decision_function(self, X: Any) -> np.ndarray:
        return np.asarray(self.predict(X, raw_score=True))


def colunas_categoricas(X: pd.DataFrame, categoricas: list[str]) -> list[str]:
    return [c for c in categoricas if c in X.columns]


def logistica(
    X: pd.DataFrame, categoricas: list[str], class_weight: str | None, C: float
) -> Pipeline:
    """One-hot nas categóricas (binária vira uma coluna só, `drop="if_binary"`)
    e padronização nas numéricas. Label encoding, o que o LightGBM recebe, não
    serve a um modelo linear: trataria a especialidade 6 como "maior" que a 2.
    Categoria nunca vista no treino vira zeros (contribuição nula), como o
    LightGBM faz com categoria desconhecida."""
    cat = colunas_categoricas(X, categoricas)
    numericas = [c for c in X.columns if c not in cat]
    transformador = ColumnTransformer(
        [
            (
                "onehot",
                OneHotEncoder(handle_unknown="ignore", drop="if_binary", sparse_output=False),
                cat,
            ),
            ("padronizar", StandardScaler(), numericas),
        ],
        remainder="drop",
    )
    modelo = LogisticRegression(C=C, class_weight=class_weight, max_iter=2000)
    return Pipeline([("transformar", transformador), ("modelo", modelo)])


def balancear_smotenc(
    X: pd.DataFrame, y: pd.Series, cat_cols: list[str], k_neighbors: int
) -> tuple[pd.DataFrame, pd.Series]:
    """SMOTE-NC (ADR-003) só sobre as linhas recebidas -- quem chama garante
    que são as do fold de treino."""
    from imblearn.over_sampling import SMOTENC  # só no ambiente de treino

    cat_idx = [X.columns.get_loc(c) for c in cat_cols]
    smote = SMOTENC(
        categorical_features=cat_idx, k_neighbors=k_neighbors, random_state=RANDOM_STATE
    )
    return smote.fit_resample(X, y)


def _base(X: pd.DataFrame, config: Mapping[str, Any], categoricas: list[str]) -> Any:
    class_weight = "balanced" if config["balanceamento"] == "class_weight" else None
    if config["tipo"] == "lightgbm":
        # verbose só silencia o log; não muda o modelo
        return LGBMComMargem(**config["lightgbm"], class_weight=class_weight, verbose=-1)
    return logistica(X, categoricas, class_weight, config["C"])


def _fit_params(X: pd.DataFrame, config: Mapping[str, Any], categoricas: list[str], peso: Any):
    if config["tipo"] == "lightgbm":
        params: dict[str, Any] = {"categorical_feature": colunas_categoricas(X, categoricas)}
        if peso is not None:
            params["sample_weight"] = peso
        return params
    return {} if peso is None else {"modelo__sample_weight": peso}


def ajustar(
    X: pd.DataFrame,
    y: pd.Series,
    config: Mapping[str, Any],
    categoricas: list[str],
    sample_weight: Any = None,
) -> Any:
    """Ajusta a configuração pedida SÓ com as linhas recebidas (o fold de
    treino). SMOTE-NC, quando pedido, gera as sintéticas aqui dentro; a
    calibração faz a própria CV interna também só sobre estas linhas."""
    validar_config(config)
    X_fit, y_fit = X, y
    if config["balanceamento"] == "smotenc":
        if sample_weight is not None:
            raise ValueError("sample_weight não se combina com SMOTE-NC")
        X_fit, y_fit = balancear_smotenc(
            X, y, colunas_categoricas(X, categoricas), config["k_neighbors"]
        )

    base = _base(X_fit, config, categoricas)
    fit_params = _fit_params(X_fit, config, categoricas, sample_weight)
    if config["calibracao"] == "sigmoid":
        modelo: Any = CalibratedClassifierCV(
            base,
            method="sigmoid",
            cv=StratifiedKFold(FOLDS_CALIBRACAO, shuffle=True, random_state=RANDOM_STATE),
            ensemble=False,
        )
        if config["tipo"] == "lightgbm":
            # CalibratedClassifierCV repassa estes ao fit da base
            modelo.fit(X_fit, y_fit, **fit_params)
        else:
            # o roteamento de `modelo__sample_weight` não passa pelo
            # CalibratedClassifierCV; o peso vai como sample_weight dele, que o
            # repassa à base (Pipeline aceita) e usa na sigmoide
            modelo.fit(X_fit, y_fit, sample_weight=sample_weight)
    else:
        modelo = base
        modelo.fit(X_fit, y_fit, **fit_params)

    _anotar(modelo, X)
    return modelo


def _anotar(modelo: Any, X: pd.DataFrame) -> None:
    """Grava no objeto ajustado o que o acesso uniforme precisa e o estimador
    não guarda: o maior valor visto por feature numérica (domínio do contrato)
    e, na logística, a média do espaço transformado (o fundo do SHAP linear)."""
    numericas = X.select_dtypes(include="number")
    modelo.maximos_vistos_ = {c: float(numericas[c].max()) for c in numericas.columns}
    base, _, _ = base_e_calibracao(modelo)
    if tipo_do_estimador(base) == "logistica":
        Z = base.named_steps["transformar"].transform(X)
        base.media_transformada_ = np.asarray(Z, dtype=float).mean(axis=0)


# --------------------------------------------------------------------------
# Acesso uniforme
# --------------------------------------------------------------------------
def base_e_calibracao(modelo: Any) -> tuple[Any, float, float]:
    """(estimador de base, escala, deslocamento) com
    log-odds da probabilidade final = escala * margem_base + deslocamento.

    Sem calibração: (o próprio modelo, 1, 0). Com o `CalibratedClassifierCV`
    (`ensemble=False`), a sigmoide do scikit-learn é
    p = 1 / (1 + exp(a * f + b)), logo log-odds = -a * f - b."""
    if isinstance(modelo, CalibratedClassifierCV):
        if len(modelo.calibrated_classifiers_) != 1:
            raise ValueError("só o CalibratedClassifierCV com ensemble=False é suportado")
        calibrado = modelo.calibrated_classifiers_[0]
        sigmoide = calibrado.calibrators[0]
        return calibrado.estimator, -float(sigmoide.a_), -float(sigmoide.b_)
    return modelo, 1.0, 0.0


def tipo_do_estimador(base: Any) -> str:
    if isinstance(base, lgb.LGBMClassifier):
        return "lightgbm"
    if isinstance(base, Pipeline) and isinstance(base.steps[-1][1], LogisticRegression):
        return "logistica"
    raise TypeError(f"estimador não suportado: {type(base).__name__}")


def tipo_do_modelo(modelo: Any) -> str:
    return tipo_do_estimador(base_e_calibracao(modelo)[0])


def calibrado(modelo: Any) -> bool:
    return isinstance(modelo, CalibratedClassifierCV)


def margem_base(base: Any, X: Any) -> np.ndarray:
    if tipo_do_estimador(base) == "lightgbm":
        return np.asarray(base.predict(X, raw_score=True), dtype=float)
    return np.asarray(base.decision_function(X), dtype=float)


def margem(modelo: Any, X: Any) -> np.ndarray:
    """Log-odds da probabilidade que `predict_proba` devolve -- o espaço em
    que soma(SHAP) + valor esperado tem de fechar."""
    base, escala, deslocamento = base_e_calibracao(modelo)
    return escala * margem_base(base, X) + deslocamento


def colunas_do_modelo(modelo: Any) -> list[str]:
    base, _, _ = base_e_calibracao(modelo)
    if tipo_do_estimador(base) == "lightgbm":
        return list(base.booster_.feature_name())
    return [str(c) for c in base.feature_names_in_]


_LINHA_CATEGORICAS = re.compile(r"^\[categorical_feature: ?([\d,]*)\]$", re.MULTILINE)


def categoricas_do_modelo(modelo: Any) -> list[str]:
    """Features que o modelo trata como categóricas: no LightGBM, a linha
    `categorical_feature` do booster serializado (é ela que vale ao predizer);
    na logística, as colunas do one-hot."""
    base, _, _ = base_e_calibracao(modelo)
    if tipo_do_estimador(base) == "lightgbm":
        booster = base.booster_
        achado = _LINHA_CATEGORICAS.search(booster.model_to_string())
        indices = [int(i) for i in achado.group(1).split(",") if i] if achado else []
        nomes = booster.feature_name()
        return [nomes[i] for i in indices]
    transformador = base.named_steps["transformar"]
    for nome, _, colunas in transformador.transformers_:
        if nome == "onehot":
            return [str(c) for c in colunas]
    return []


def maximos_vistos(modelo: Any) -> dict[str, float]:
    """Maior valor de cada feature numérica no dado de treino. O artefato novo
    traz `maximos_vistos_`; o LightGBM antigo, o `feature_infos` do booster."""
    if hasattr(modelo, "maximos_vistos_"):
        return dict(modelo.maximos_vistos_)
    base, _, _ = base_e_calibracao(modelo)
    if tipo_do_estimador(base) != "lightgbm":
        return {}
    infos = base.booster_.dump_model().get("feature_infos", {})
    maximos = {}
    for nome, info in infos.items():
        if isinstance(info, dict) and "max_value" in info:
            maximos[nome] = float(info["max_value"])
    return maximos


def descrever(modelo: Any) -> dict[str, Any]:
    """Resumo para log e MLflow: tipo, calibração e hiperparâmetros da base."""
    base, escala, deslocamento = base_e_calibracao(modelo)
    tipo = tipo_do_estimador(base)
    resumo: dict[str, Any] = {"tipo": tipo, "calibrado": calibrado(modelo)}
    if calibrado(modelo):
        resumo["calibracao_escala"] = escala
        resumo["calibracao_deslocamento"] = deslocamento
    return resumo
