"""
SaúdeJá — avaliação de uma configuração do classificador de no-show com
validação cruzada repetida (RepeatedStratifiedKFold, 5 folds x 5 repetições).

Por que existe: toda decisão de modelo do projeto até aqui (SMOTE-NC no
ADR-003, features temporais, GridSearch descartado, o campeão do gate) foi
medida num único split 80/20, com 76 linhas e 21 positivos no fold de teste.
Um paciente move recall_1 em ~0,048, e só trocar a semente do split move o
roc_auc do mesmo pipeline de 0,51 a 0,68. Com 25 medições em vez de uma, a
comparação entre configurações passa a ter média, desvio e intervalo.

Exploratório, como src/tune.py: NÃO é stage do dvc.yaml e não grava
artefato. O número oficial de uma configuração promovida continua sendo o do
fold de teste isolado (`dvc exp run` -> validate.py); este script decide QUAL
configuração vale levar até lá.

Garantias:
- O pré-processamento é o do pipeline (`preprocess.preprocessar`: contrato de
  features, encoding, features temporais conforme params.yaml).
- O balanceamento roda DENTRO de cada fold, só sobre as linhas de treino
  daquele fold (mesma regra do ADR-003 e do tune.py): o fold de validação
  nunca recebe linha sintética. `tests/test_avaliar_cv.py` trava isso.
- Na configuração vigente (lightgbm + smotenc), o ajuste por fold é o mesmo
  de `train.treinar`: mesmos hiperparâmetros de params.yaml, mesmo SMOTE-NC
  (`train._balancear`), mesmas colunas categóricas.

Uso (na raiz do repo):
    python scripts/avaliar_cv.py                                   # vigente
    python scripts/avaliar_cv.py --balanceamento class_weight
    python scripts/avaliar_cv.py --modelo logistica --balanceamento class_weight
    python scripts/avaliar_cv.py --sem-mlflow                      # só imprime
"""

import argparse
import math
import os
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
from scipy import stats
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import RepeatedStratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import preprocess  # noqa: E402
import train  # noqa: E402
from config_projeto import carregar_params  # noqa: E402

PARAMS = carregar_params()

DADOS_PADRAO = REPO_ROOT / "data" / "consultas-treino.csv"
RANDOM_STATE = 42
N_SPLITS = 5
N_REPEATS = 5
# "Cortar 70% do custo de mensageria" do BRIEFING: avisar os 30% de maior risco.
FRACAO_DA_FILA = 0.30

MODELOS = ("lightgbm", "logistica")
BALANCEAMENTOS = ("smotenc", "class_weight", "none")

# Regularização da logística: o valor medido no diagnóstico do modelo. Fica aqui,
# e não em params.yaml, enquanto a logística for só desafiante exploratória; vira
# parâmetro quando model.tipo existir no pipeline oficial.
C_LOGISTICA = 0.3

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME = os.environ.get("MLFLOW_EXPERIMENT_NAME", "saudeja-no-show")
PIPELINE_TAG_KEY = "pipeline_arquitetura"
PIPELINE_TAG_VALUE = "cv-repetida"

METRICAS = (
    "roc_auc",
    "pr_auc",
    "brier",
    "f1_1",
    "recall_1",
    "precision_1",
    "taxa_disparo",
    "recall_at_fracao",
    "lift_at_fracao",
)


# --------------------------------------------------------------------------
# Modelo por fold
# --------------------------------------------------------------------------
def _colunas_categoricas(X):
    return [c for c in preprocess.COLUNAS_CATEGORICAS if c in X.columns]


def _logistica(X, class_weight):
    """One-hot nas categóricas de vários níveis e padronização nas numéricas.
    `sexo` já é 0/1 e passa direto. Label encoding (o que o LightGBM recebe)
    não serve a um modelo linear: trataria a especialidade 6 como "maior" que a 2."""
    categoricas = [c for c in _colunas_categoricas(X) if c != "sexo"]
    numericas = [c for c in X.columns if c not in preprocess.COLUNAS_CATEGORICAS]
    transformador = ColumnTransformer(
        [
            ("onehot", OneHotEncoder(handle_unknown="ignore"), categoricas),
            ("padronizar", StandardScaler(), numericas),
        ],
        remainder="passthrough",
    )
    modelo = LogisticRegression(C=C_LOGISTICA, class_weight=class_weight, max_iter=2000)
    return Pipeline([("transformar", transformador), ("modelo", modelo)])


def ajustar_no_fold(X_treino, y_treino, modelo, balanceamento):
    """Ajusta a configuração pedida SÓ com as linhas de treino do fold.
    O SMOTE-NC, quando pedido, gera as linhas sintéticas aqui dentro, a partir
    de X_treino -- o fold de validação não passa por esta função."""
    cat_cols = _colunas_categoricas(X_treino)
    class_weight = "balanced" if balanceamento == "class_weight" else None

    if balanceamento == "smotenc":
        X_treino, y_treino = train._balancear(
            X_treino, y_treino, cat_cols, PARAMS["balancing"]["k_neighbors"]
        )

    if modelo == "lightgbm":
        kwargs = train._construir_modelo_kwargs()
        # verbose só silencia o log de 25 ajustes; não muda o modelo.
        estimador = lgb.LGBMClassifier(**kwargs, class_weight=class_weight, verbose=-1)
        estimador.fit(X_treino, y_treino, categorical_feature=cat_cols)
    elif modelo == "logistica":
        estimador = _logistica(X_treino, class_weight)
        estimador.fit(X_treino, y_treino)
    else:
        raise ValueError(f"modelo desconhecido: {modelo}")
    return estimador


# --------------------------------------------------------------------------
# Métricas
# --------------------------------------------------------------------------
def recall_na_fracao(y_true, y_proba, fracao):
    """Recall quando se avisa os ceil(fracao * n) pacientes de maior
    probabilidade. É a pergunta do negócio ("com o orçamento de 30% da fila,
    quantos faltosos pegamos?"), e não depende de threshold nem de calibração.
    Devolve (recall, fração efetivamente avisada)."""
    y_true = np.asarray(y_true)
    n = len(y_true)
    k = math.ceil(fracao * n)
    # sort estável: em empate, a ordem original decide (resultado reproduzível)
    ordem = np.argsort(-np.asarray(y_proba), kind="stable")
    capturados = y_true[ordem[:k]].sum()
    return capturados / y_true.sum(), k / n


def metricas_do_fold(y_true, y_proba, threshold, fracao):
    y_pred = (y_proba >= threshold).astype(int)
    recall_k, fracao_avisada = recall_na_fracao(y_true, y_proba, fracao)
    return {
        "roc_auc": roc_auc_score(y_true, y_proba),
        "pr_auc": average_precision_score(y_true, y_proba),
        "brier": brier_score_loss(y_true, y_proba),
        "f1_1": f1_score(y_true, y_pred, zero_division=0),
        "recall_1": recall_score(y_true, y_pred, zero_division=0),
        "precision_1": precision_score(y_true, y_pred, zero_division=0),
        "taxa_disparo": y_pred.mean(),
        "recall_at_fracao": recall_k,
        # lift 1,0 = escolher ao acaso a mesma fração da fila
        "lift_at_fracao": recall_k / fracao_avisada,
    }


def resumir(valores, n_splits):
    """Média, desvio e intervalo de 95% de uma métrica medida nos folds.

    O intervalo usa a correção de Nadeau & Bengio (2003): os 25 folds NÃO são
    independentes (cada linha treina em 4 de 5 folds de cada repetição), e o
    erro-padrão ingênuo (desvio / raiz de 25) daria um intervalo estreito
    demais. A variância é inflada pelo termo n_teste/n_treino = 1/(k-1)."""
    valores = np.asarray(valores, dtype=float)
    n = len(valores)
    media = valores.mean()
    desvio = valores.std(ddof=1) if n > 1 else 0.0
    erro_padrao = math.sqrt((1 / n + 1 / (n_splits - 1)) * desvio**2)
    t = stats.t.ppf(0.975, df=n - 1) if n > 1 else 0.0
    return {
        "media": media,
        "desvio": desvio,
        "ic95_inf": media - t * erro_padrao,
        "ic95_sup": media + t * erro_padrao,
    }


# --------------------------------------------------------------------------
# Validação cruzada
# --------------------------------------------------------------------------
def avaliar(
    X,
    y,
    modelo="lightgbm",
    balanceamento="smotenc",
    threshold=None,
    fracao=FRACAO_DA_FILA,
    n_splits=N_SPLITS,
    n_repeats=N_REPEATS,
    random_state=RANDOM_STATE,
):
    """Roda a CV repetida e devolve {"folds": {métrica: [valores]},
    "resumo": {métrica: {media, desvio, ic95_inf, ic95_sup}}, "referencia": {...}}."""
    if modelo not in MODELOS:
        raise ValueError(f"modelo deve ser um de {MODELOS}")
    if balanceamento not in BALANCEAMENTOS:
        raise ValueError(f"balanceamento deve ser um de {BALANCEAMENTOS}")
    threshold = PARAMS["decision"]["threshold"] if threshold is None else threshold

    cv = RepeatedStratifiedKFold(n_splits=n_splits, n_repeats=n_repeats, random_state=random_state)
    folds = {m: [] for m in METRICAS}
    for idx_treino, idx_validacao in cv.split(X, y):
        estimador = ajustar_no_fold(X.iloc[idx_treino], y.iloc[idx_treino], modelo, balanceamento)
        X_val, y_val = X.iloc[idx_validacao], y.iloc[idx_validacao]
        y_proba = estimador.predict_proba(X_val)[:, 1]
        for nome, valor in metricas_do_fold(y_val, y_proba, threshold, fracao).items():
            folds[nome].append(valor)

    prevalencia = float(np.mean(y))
    return {
        "folds": folds,
        "resumo": {m: resumir(v, n_splits) for m, v in folds.items()},
        "referencia": {
            # o que um modelo sem informação alguma obtém
            "pr_auc_acaso": prevalencia,
            "brier_taxa_base": prevalencia * (1 - prevalencia),
            "roc_auc_acaso": 0.5,
            "lift_acaso": 1.0,
        },
    }


# --------------------------------------------------------------------------
# Saída
# --------------------------------------------------------------------------
def imprimir(resultado, args, n_linhas):
    print(
        f"\n[cv] modelo={args.modelo} balanceamento={args.balanceamento} "
        f"threshold={args.threshold} fracao={args.fracao} "
        f"| {args.n_splits} folds x {args.n_repeats} repeticoes, {n_linhas} linhas"
    )
    print(f"{'metrica':<18} {'media':>7} {'desvio':>7}   {'IC 95% (corrigido)':>20}")
    for nome, r in resultado["resumo"].items():
        print(
            f"{nome:<18} {r['media']:7.3f} {r['desvio']:7.3f}   "
            f"[{r['ic95_inf']:6.3f}, {r['ic95_sup']:6.3f}]"
        )
    ref = resultado["referencia"]
    print(
        f"\n[referencia] acaso: roc_auc 0.500, pr_auc {ref['pr_auc_acaso']:.3f} "
        f"(prevalencia), lift 1.00; brier do modelo que responde a taxa base "
        f"{ref['brier_taxa_base']:.3f} (menor e' melhor)"
    )


def logar_mlflow(resultado, args, n_linhas):
    import mlflow

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
    with mlflow.start_run(run_name=f"cv-{args.modelo}-{args.balanceamento}"):
        mlflow.set_tag(PIPELINE_TAG_KEY, PIPELINE_TAG_VALUE)
        mlflow.log_params(
            {
                "modelo": args.modelo,
                "balancing": args.balanceamento,
                "cv_n_splits": args.n_splits,
                "cv_n_repeats": args.n_repeats,
                "decision_threshold": args.threshold,
                "fracao_da_fila": args.fracao,
                "features_temporais": PARAMS.get("features", {}).get("temporais", False),
                "n_linhas": n_linhas,
                "dados": Path(args.dados).name,
            }
        )
        if args.modelo == "lightgbm":
            mlflow.log_params(train._construir_modelo_kwargs())
        else:
            mlflow.log_param("C", C_LOGISTICA)
        if args.balanceamento == "smotenc":
            mlflow.log_param("k_neighbors", PARAMS["balancing"]["k_neighbors"])
        for nome, r in resultado["resumo"].items():
            for estatistica, valor in r.items():
                mlflow.log_metric(f"cv_{nome}_{estatistica}", valor)


def _parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--modelo", choices=MODELOS, default="lightgbm")
    p.add_argument("--balanceamento", choices=BALANCEAMENTOS, default="smotenc")
    p.add_argument("--dados", default=str(DADOS_PADRAO))
    p.add_argument("--threshold", type=float, default=PARAMS["decision"]["threshold"])
    p.add_argument("--fracao", type=float, default=FRACAO_DA_FILA)
    p.add_argument("--n-splits", type=int, default=N_SPLITS)
    p.add_argument("--n-repeats", type=int, default=N_REPEATS)
    p.add_argument("--sem-mlflow", action="store_true", help="só imprime, não loga")
    return p


def main(argv=None):
    args = _parser().parse_args(argv)
    df = preprocess.carregar_dados(args.dados)
    X, y, _ = preprocess.preprocessar(df)
    resultado = avaliar(
        X,
        y,
        modelo=args.modelo,
        balanceamento=args.balanceamento,
        threshold=args.threshold,
        fracao=args.fracao,
        n_splits=args.n_splits,
        n_repeats=args.n_repeats,
    )
    imprimir(resultado, args, len(X))
    if not args.sem_mlflow:
        logar_mlflow(resultado, args, len(X))
        print(f"[ok] run logada no MLflow ({PIPELINE_TAG_KEY}={PIPELINE_TAG_VALUE})")
    return resultado


if __name__ == "__main__":
    main()
