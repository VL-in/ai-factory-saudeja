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
- O ajuste por fold é o de `train.treinar`, pela mesma fábrica
  (`src/modelo.py`): mesmos hiperparâmetros de params.yaml, mesmo SMOTE-NC,
  mesma logística, mesma calibração, mesmas colunas categóricas. Da linha de
  comando vêm só o tipo, o balanceamento e a calibração.
- `--calibracao sigmoid`: sigmoide de Platt com CV interna, dentro do fold de
  treino (mede o Brier antes e depois da calibração).

Extensões para o teste de sinal do dataset externo (ADR-014):
- `--grupo id_paciente`: troca o `RepeatedStratifiedKFold` por
  `StratifiedGroupKFold` repetido (uma semente de embaralhamento por
  repetição). Todas as consultas de um paciente caem no mesmo fold -- com id
  de paciente estável, a CV comum vaza o paciente entre treino e validação.
- `--features`: subconjunto das features do pipeline (o teste de sinal usa só
  as reais e deixa as imputadas de fora).
- `--permutacoes N`: refaz a CV (5 folds x 1 repetição, folds fixos) com o
  `no_show` embaralhado N vezes; p-valor = (1 + nº de permutações com
  roc_auc >= o real) / (N + 1).
- `--univariada`: AUC de cada feature sozinha, com IC 95% por bootstrap.
- `--excluir-pacientes-de CSV`: tira do dado todo paciente que aparece no CSV
  (o teste reservado nunca entra no ajuste, risco R12 do ADR-014).
- `--amostra N --semente-amostra S`: sorteia N linhas, estratificadas pelo
  desfecho, antes da CV (as amostras extras do teste de robustez).
- Run com dado de origem `externa` leva a tag MLflow `dados_externos=true`.

Curva de custo por fração da fila (`--curva`, regra `top_fracao` do ADR-016):
- usa as probabilidades fora da amostra (out-of-fold) da própria CV: em cada
  repetição, toda linha recebe a probabilidade do fold em que foi validação;
- para cada fração f (`--fracoes`), avisa os ceil(f x n) de maior
  probabilidade de cada repetição e mede recall, precisão e lift (média das
  repetições), com a mesma conta de `decisao.recall_na_fracao`;
- converte em reais por 1.000 consultas com premissas declaradas: custo do
  lembrete = `--segmentos` x `custo.sms_unitario_usd` x `custo.cambio_usd_brl`
  do params.yaml; faltas evitadas = 1.000 x prevalência x recall x `--eficacia`
  (eficácia do lembrete, ASSUMIDA); receita = faltas evitadas x
  `--custo-falta`. Inclui a linha do sorteio (avisar `--fracao` ao acaso).
  É a tabela do SLA §4.1.

Uso (na raiz do repo):
    python scripts/avaliar_cv.py                                   # vigente
    python scripts/avaliar_cv.py --balanceamento class_weight
    python scripts/avaliar_cv.py --modelo logistica --balanceamento class_weight
    python scripts/avaliar_cv.py --modelo logistica --balanceamento class_weight \
        --calibracao sigmoid
    python scripts/avaliar_cv.py --sem-mlflow                      # só imprime
    python scripts/avaliar_cv.py --modelo logistica --balanceamento class_weight \
        --calibracao sigmoid --curva --sem-mlflow                  # SLA §4.1
    python scripts/avaliar_cv.py --dados data/externo/consultas-externas.csv \
        --grupo id_paciente --modelo logistica --balanceamento class_weight \
        --features idade sexo dias_entre_agendamento_consulta historico_noshow \
        dia_de_semana --permutacoes 200 --univariada --sem-mlflow
"""

import argparse
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import RepeatedStratifiedKFold, StratifiedGroupKFold

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import contrato_dataset_externo as cde  # noqa: E402
import modelo as modelo_ml  # noqa: E402
import preprocess  # noqa: E402
from config_projeto import carregar_params  # noqa: E402
from decisao import recall_na_fracao  # noqa: E402  (reexportada: a mesma do validate.py)

PARAMS = carregar_params()
CONFIG_PARAMS = modelo_ml.config_do_modelo(PARAMS)

DADOS_PADRAO = REPO_ROOT / "data" / "consultas-treino.csv"
RANDOM_STATE = 42
N_SPLITS = 5
N_REPEATS = 5
# "Cortar 70% do custo de mensageria" do BRIEFING: avisar os 30% de maior risco
# (`decision.fracao_da_fila`, a mesma fração do validate.py e do gate).
FRACAO_DA_FILA = float(PARAMS["decision"].get("fracao_da_fila", 0.30))

MODELOS = modelo_ml.TIPOS
BALANCEAMENTOS = modelo_ml.BALANCEAMENTOS
CALIBRACOES = modelo_ml.CALIBRACOES

# Regularização da logística: `model.logistica.C` do params.yaml (0,3, o valor
# medido no diagnóstico do modelo, quando o params não o traz).
C_LOGISTICA = CONFIG_PARAMS["C"]

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME = os.environ.get("MLFLOW_EXPERIMENT_NAME", "saudeja-no-show")
PIPELINE_TAG_KEY = "pipeline_arquitetura"
PIPELINE_TAG_VALUE = "cv-repetida"
TAG_DADOS_EXTERNOS = "dados_externos"
N_BOOTSTRAP = 1000

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


# Curva de custo (--curva): frações avaliadas e premissas padrão do SLA §4.1.
FRACOES_DA_CURVA = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 1.0)
EFICACIA_LEMBRETE = 0.30  # ASSUMIDA: 30% de quem ia faltar e foi avisado comparece
CUSTO_FALTA_BRL = 180.0  # BRIEFING
SEGMENTOS_DO_LEMBRETE = 2  # o texto acentuado tem 72 caracteres (2 segmentos de 70)
CONSULTAS_DA_CURVA = 1000


# --------------------------------------------------------------------------
# Modelo por fold
# --------------------------------------------------------------------------
def _colunas_categoricas(X):
    return modelo_ml.colunas_categoricas(X, preprocess.COLUNAS_CATEGORICAS)


def _logistica(X, class_weight):
    """A logística da fábrica (`src/modelo.py`), com o `C` do params.yaml."""
    return modelo_ml.logistica(X, preprocess.COLUNAS_CATEGORICAS, class_weight, C_LOGISTICA)


def config_da_rodada(modelo, balanceamento, calibracao="none"):
    """A configuração do params.yaml (hiperparâmetros, `C`, `k_neighbors`)
    com tipo, balanceamento e calibração trocados pelos da linha de comando."""
    config = modelo_ml.config_do_modelo(PARAMS)
    config.update(tipo=modelo, balanceamento=balanceamento, calibracao=calibracao, C=C_LOGISTICA)
    modelo_ml.validar_config(config)
    return config


def ajustar_no_fold(X_treino, y_treino, modelo, balanceamento, calibracao="none"):
    """Ajusta a configuração pedida SÓ com as linhas de treino do fold, pela
    mesma fábrica do `train.py` (`modelo.ajustar`). O SMOTE-NC e a CV interna
    da calibração, quando pedidos, rodam aqui dentro, a partir de X_treino --
    o fold de validação não passa por esta função."""
    if modelo not in MODELOS:
        raise ValueError(f"modelo desconhecido: {modelo}")
    config = config_da_rodada(modelo, balanceamento, calibracao)
    return modelo_ml.ajustar(X_treino, y_treino, config, preprocess.COLUNAS_CATEGORICAS)


# --------------------------------------------------------------------------
# Métricas
# --------------------------------------------------------------------------
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
def divisoes(X, y, grupos, n_splits, n_repeats, random_state):
    """(índices de treino, de validação) de cada fold. Sem `grupos`, o
    `RepeatedStratifiedKFold` de sempre. Com `grupos`, um
    `StratifiedGroupKFold` por repetição, embaralhado com `random_state + r`:
    o scikit-learn não tem a versão repetida, e trocar a semente é o que a
    `RepeatedStratifiedKFold` faz por dentro."""
    if grupos is None:
        cv = RepeatedStratifiedKFold(
            n_splits=n_splits, n_repeats=n_repeats, random_state=random_state
        )
        yield from cv.split(X, y)
        return
    for r in range(n_repeats):
        cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=random_state + r)
        yield from cv.split(X, y, groups=grupos)


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
    grupos=None,
    calibracao="none",
    guardar_oof=False,
):
    """Roda a CV repetida e devolve {"folds": {métrica: [valores]},
    "resumo": {métrica: {media, desvio, ic95_inf, ic95_sup}}, "referencia": {...}}.

    Com `guardar_oof`, devolve também "oof": matriz (repetições x linhas) com a
    probabilidade que cada linha recebeu quando estava no fold de validação --
    os mesmos ajustes das métricas, sem treinar de novo (base da `--curva`)."""
    if modelo not in MODELOS:
        raise ValueError(f"modelo deve ser um de {MODELOS}")
    if balanceamento not in BALANCEAMENTOS:
        raise ValueError(f"balanceamento deve ser um de {BALANCEAMENTOS}")
    threshold = PARAMS["decision"]["threshold"] if threshold is None else threshold

    folds = {m: [] for m in METRICAS}
    oof = np.full((n_repeats, len(y)), np.nan)
    divisao = divisoes(X, y, grupos, n_splits, n_repeats, random_state)
    for i, (idx_treino, idx_validacao) in enumerate(divisao):
        estimador = ajustar_no_fold(
            X.iloc[idx_treino], y.iloc[idx_treino], modelo, balanceamento, calibracao
        )
        X_val, y_val = X.iloc[idx_validacao], y.iloc[idx_validacao]
        y_proba = estimador.predict_proba(X_val)[:, 1]
        # as duas divisões entregam os n_splits folds de cada repetição em sequência
        oof[i // n_splits, idx_validacao] = y_proba
        for nome, valor in metricas_do_fold(y_val, y_proba, threshold, fracao).items():
            folds[nome].append(valor)

    prevalencia = float(np.mean(y))
    extras = {"oof": oof} if guardar_oof else {}
    return {
        **extras,
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


def premissas_de_custo(
    eficacia=EFICACIA_LEMBRETE, custo_falta=CUSTO_FALTA_BRL, segmentos=SEGMENTOS_DO_LEMBRETE
):
    """Premissas da curva, com o preço do SMS e o câmbio lidos do params.yaml
    (`custo`, a mesma fonte do job D-2 e do painel de custo)."""
    custo = PARAMS.get("custo") or {}
    sms_brl = segmentos * float(custo["sms_unitario_usd"]) * float(custo["cambio_usd_brl"])
    return {
        "eficacia": float(eficacia),
        "custo_falta_brl": float(custo_falta),
        "segmentos": int(segmentos),
        "custo_lembrete_brl": sms_brl,
    }


def curva_por_fracao(y, oof, fracoes, premissas, fracao_sorteio=FRACAO_DA_FILA):
    """Uma linha por fração avisada, com médias entre as repetições do `oof`
    e valores por `CONSULTAS_DA_CURVA` consultas. A última linha é o sorteio:
    avisar `fracao_sorteio` da fila ao acaso pega, em média, essa mesma fração
    dos faltosos (recall = fração, precisão = prevalência, lift 1)."""
    y = np.asarray(y)
    n = CONSULTAS_DA_CURVA
    prevalencia = float(y.mean())
    sms = premissas["custo_lembrete_brl"]

    def _linha(nome, fracao, recall, precisao):
        evitadas = n * prevalencia * recall * premissas["eficacia"]
        return {
            "linha": nome,
            "fracao": float(fracao),
            "recall": float(recall),
            "precisao": float(precisao),
            "lift": float(recall / fracao),
            "custo_sms_brl": float(fracao * n * sms),
            "faltas_evitadas": float(evitadas),
            "receita_preservada_brl": float(evitadas * premissas["custo_falta_brl"]),
        }

    linhas = []
    for fracao in fracoes:
        recalls, precisoes = [], []
        for probs in oof:
            recall, fracao_avisada = recall_na_fracao(y, probs, fracao)
            k = fracao_avisada * len(y)
            recalls.append(recall)
            precisoes.append(recall * y.sum() / k)
        linhas.append(_linha("modelo", fracao, np.mean(recalls), np.mean(precisoes)))
    linhas.append(_linha("sorteio", fracao_sorteio, fracao_sorteio, prevalencia))
    return linhas


def _roc_auc_medio(X, y, modelo, balanceamento, grupos, n_splits, random_state):
    """roc_auc médio em n_splits folds x 1 repetição, folds fixos por
    `random_state` -- a medida comparada no teste de permutação."""
    valores = []
    for idx_treino, idx_validacao in divisoes(X, y, grupos, n_splits, 1, random_state):
        estimador = ajustar_no_fold(X.iloc[idx_treino], y.iloc[idx_treino], modelo, balanceamento)
        y_proba = estimador.predict_proba(X.iloc[idx_validacao])[:, 1]
        valores.append(roc_auc_score(y.iloc[idx_validacao], y_proba))
    return float(np.mean(valores))


def teste_de_permutacao(
    X,
    y,
    n_permutacoes,
    modelo="logistica",
    balanceamento="class_weight",
    grupos=None,
    n_splits=N_SPLITS,
    random_state=RANDOM_STATE,
):
    """Distribuição nula do roc_auc: a mesma CV (mesmos folds) com o
    `no_show` embaralhado. Se a AUC real fica acima de quase todas as
    embaralhadas, o sinal não é sorte. Os folds são os mesmos em todas as
    rodadas; só o rótulo muda."""
    rng = np.random.default_rng(random_state)
    real = _roc_auc_medio(X, y, modelo, balanceamento, grupos, n_splits, random_state)
    nula = []
    for _ in range(n_permutacoes):
        y_perm = pd.Series(rng.permutation(y.to_numpy()), index=y.index)
        nula.append(
            _roc_auc_medio(X, y_perm, modelo, balanceamento, grupos, n_splits, random_state)
        )
    nula = np.asarray(nula)
    return {
        "roc_auc_real": real,
        "nula_media": float(nula.mean()),
        "nula_p99": float(np.quantile(nula, 0.99)),
        "n_permutacoes": n_permutacoes,
        "p_valor": float((1 + (nula >= real).sum()) / (n_permutacoes + 1)),
    }


def _codificar_para_auc(valores, y, categorica):
    """Numérica: o próprio valor. Categórica: a taxa de falta da categoria
    na mesma amostra (a melhor ordenação possível das categorias; o label
    encoding daria uma ordem arbitrária)."""
    if not categorica:
        return valores
    return valores.map(y.groupby(valores).mean())


def auc_univariada(X, y, n_bootstrap=N_BOOTSTRAP, random_state=RANDOM_STATE):
    """AUC de cada feature sozinha contra o desfecho, com IC 95% por
    bootstrap percentil (linhas reamostradas com reposição; nas categóricas,
    a taxa por categoria é recalculada em cada reamostra). 0,5 é sorteio;
    abaixo de 0,5 a feature ordena ao contrário (só nas numéricas -- a
    categórica, por construção, fica >= 0,5)."""
    rng = np.random.default_rng(random_state)
    y = y.reset_index(drop=True)
    resultado = {}
    for coluna in X.columns:
        valores = X[coluna].reset_index(drop=True)
        categorica = coluna in preprocess.COLUNAS_CATEGORICAS
        auc = roc_auc_score(y, _codificar_para_auc(valores, y, categorica))
        reamostras = []
        for _ in range(n_bootstrap):
            idx = rng.integers(0, len(y), len(y))
            y_b = y.iloc[idx].reset_index(drop=True)
            v_b = valores.iloc[idx].reset_index(drop=True)
            if y_b.nunique() < 2:
                continue
            reamostras.append(roc_auc_score(y_b, _codificar_para_auc(v_b, y_b, categorica)))
        resultado[coluna] = {
            "auc": float(auc),
            "ic95_inf": float(np.quantile(reamostras, 0.025)),
            "ic95_sup": float(np.quantile(reamostras, 0.975)),
        }
    return resultado


# --------------------------------------------------------------------------
# Dados
# --------------------------------------------------------------------------
def preparar_dados(df, excluir_pacientes_de=None, amostra=None, semente_amostra=RANDOM_STATE):
    """Recorte do dado antes da CV: sem os pacientes de outro arquivo
    (o teste reservado) e, se pedido, uma amostra estratificada."""
    if excluir_pacientes_de is not None:
        fora = pd.read_csv(
            excluir_pacientes_de, dtype={"id_paciente": str}, usecols=["id_paciente"]
        )
        df = df[~df["id_paciente"].astype(str).isin(set(fora["id_paciente"]))]
    if amostra is not None:
        df = cde.amostra_estratificada(df, amostra, semente_amostra)
    return df.reset_index(drop=True)


def tem_dado_externo(df):
    return bool(
        cde.COLUNA_ORIGEM in df.columns and (df[cde.COLUNA_ORIGEM] == cde.ORIGEM_EXTERNA).any()
    )


def selecionar_features(X, features):
    if not features:
        return X
    desconhecidas = sorted(set(features) - set(X.columns))
    if desconhecidas:
        raise ValueError(
            f"features fora do pipeline: {desconhecidas} (disponíveis: {list(X.columns)})"
        )
    return X[list(features)]


# --------------------------------------------------------------------------
# Saída
# --------------------------------------------------------------------------
def imprimir(resultado, args, n_linhas):
    print(
        f"\n[cv] modelo={args.modelo} balanceamento={args.balanceamento} "
        f"threshold={args.threshold} fracao={args.fracao} "
        f"| {args.n_splits} folds x {args.n_repeats} repeticoes, {n_linhas} linhas"
        f"{' | grupo=' + args.grupo if args.grupo else ''}"
    )
    if args.features:
        print(f"[cv] features: {args.features}")
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


def imprimir_curva(curva, premissas):
    print(
        f"\n[curva] por {CONSULTAS_DA_CURVA} consultas | lembrete R$ "
        f"{premissas['custo_lembrete_brl']:.3f} ({premissas['segmentos']} segmentos), "
        f"falta R$ {premissas['custo_falta_brl']:.0f}, eficacia do lembrete "
        f"{premissas['eficacia']:.0%} (ASSUMIDA)"
    )
    print(
        f"{'linha':<8} {'fracao':>6} {'recall':>6} {'precisao':>8} {'lift':>5} "
        f"{'custo R$':>9} {'evitadas':>9} {'receita R$':>11}"
    )
    for c in curva:
        print(
            f"{c['linha']:<8} {c['fracao']:6.0%} {c['recall']:6.2f} {c['precisao']:8.2f} "
            f"{c['lift']:5.2f} {c['custo_sms_brl']:9.0f} {c['faltas_evitadas']:9.1f} "
            f"{c['receita_preservada_brl']:11.0f}"
        )


def imprimir_permutacao(perm):
    print(
        f"\n[permutacao] roc_auc real (5x1) {perm['roc_auc_real']:.3f} | nula: media "
        f"{perm['nula_media']:.3f}, p99 {perm['nula_p99']:.3f} | "
        f"p = {perm['p_valor']:.4f} ({perm['n_permutacoes']} permutacoes)"
    )


def imprimir_univariada(univariada):
    print(f"\n{'feature (AUC univariada)':<34} {'auc':>6}   {'IC 95% (bootstrap)':>18}")
    for nome, r in univariada.items():
        print(f"{nome:<34} {r['auc']:6.3f}   [{r['ic95_inf']:6.3f}, {r['ic95_sup']:6.3f}]")


def logar_mlflow(
    resultado, args, n_linhas, dados_externos=False, extras=None, params_extras=None
):
    import mlflow

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
    with mlflow.start_run(run_name=f"cv-{args.modelo}-{args.balanceamento}-{args.calibracao}"):
        mlflow.set_tag(PIPELINE_TAG_KEY, PIPELINE_TAG_VALUE)
        if dados_externos:
            # ADR-014: todo run que usa a origem externa é rastreável
            mlflow.set_tag(TAG_DADOS_EXTERNOS, "true")
        if args.grupo:
            mlflow.log_param("cv_grupo", args.grupo)
        if args.features:
            mlflow.log_param("features", ",".join(args.features))
        for nome, valor in (extras or {}).items():
            mlflow.log_metric(nome, valor)
        if params_extras:
            mlflow.log_params(params_extras)
        mlflow.log_params(
            {
                "modelo": args.modelo,
                "balancing": args.balanceamento,
                "calibracao": args.calibracao,
                "cv_n_splits": args.n_splits,
                "cv_n_repeats": args.n_repeats,
                "decision_threshold": args.threshold,
                "fracao_da_fila": args.fracao,
                "features_temporais": PARAMS.get("features", {}).get("temporais", False),
                "features_periodo_do_dia": PARAMS.get("features", {}).get(
                    "periodo_do_dia", False
                ),
                "n_linhas": n_linhas,
                "dados": Path(args.dados).name,
            }
        )
        if args.modelo == "lightgbm":
            mlflow.log_params(CONFIG_PARAMS["lightgbm"])
        else:
            mlflow.log_param("C", C_LOGISTICA)
        if args.balanceamento == "smotenc":
            mlflow.log_param("k_neighbors", CONFIG_PARAMS["k_neighbors"])
        for nome, r in resultado["resumo"].items():
            for estatistica, valor in r.items():
                mlflow.log_metric(f"cv_{nome}_{estatistica}", valor)


def _parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--modelo", choices=MODELOS, default="lightgbm")
    p.add_argument("--balanceamento", choices=BALANCEAMENTOS, default="smotenc")
    p.add_argument(
        "--calibracao",
        choices=CALIBRACOES,
        default="none",
        help="sigmoide de Platt dentro do fold (exige class_weight ou none)",
    )
    p.add_argument("--dados", default=str(DADOS_PADRAO))
    p.add_argument("--threshold", type=float, default=PARAMS["decision"]["threshold"])
    p.add_argument("--fracao", type=float, default=FRACAO_DA_FILA)
    p.add_argument("--n-splits", type=int, default=N_SPLITS)
    p.add_argument("--n-repeats", type=int, default=N_REPEATS)
    p.add_argument("--sem-mlflow", action="store_true", help="só imprime, não loga")
    p.add_argument("--grupo", default=None, help="coluna de grupo (ex.: id_paciente)")
    p.add_argument("--features", nargs="+", default=None, help="subconjunto de features")
    p.add_argument("--permutacoes", type=int, default=0, help="teste de permutação com N rodadas")
    p.add_argument("--univariada", action="store_true", help="AUC por feature com IC bootstrap")
    p.add_argument("--excluir-pacientes-de", default=None, help="CSV cujos pacientes saem do dado")
    p.add_argument("--amostra", type=int, default=None, help="sorteia N linhas estratificadas")
    p.add_argument("--semente-amostra", type=int, default=RANDOM_STATE)
    p.add_argument(
        "--curva",
        action="store_true",
        help="curva de custo por fração da fila (top_fracao), a tabela do SLA §4.1",
    )
    p.add_argument("--fracoes", type=float, nargs="+", default=list(FRACOES_DA_CURVA))
    p.add_argument(
        "--eficacia", type=float, default=EFICACIA_LEMBRETE, help="eficácia do lembrete"
    )
    p.add_argument("--custo-falta", type=float, default=CUSTO_FALTA_BRL)
    p.add_argument("--segmentos", type=int, default=SEGMENTOS_DO_LEMBRETE)
    return p


def main(argv=None):
    args = _parser().parse_args(argv)
    df = preprocess.carregar_dados(args.dados)
    df = preparar_dados(df, args.excluir_pacientes_de, args.amostra, args.semente_amostra)
    X, y, _ = preprocess.preprocessar(df)
    X = selecionar_features(X, args.features)
    grupos = df[args.grupo].astype(str).to_numpy() if args.grupo else None
    resultado = avaliar(
        X,
        y,
        modelo=args.modelo,
        balanceamento=args.balanceamento,
        threshold=args.threshold,
        fracao=args.fracao,
        n_splits=args.n_splits,
        n_repeats=args.n_repeats,
        grupos=grupos,
        calibracao=args.calibracao,
        guardar_oof=args.curva,
    )
    imprimir(resultado, args, len(X))
    extras = {}
    params_extras = {}
    if args.curva:
        premissas = premissas_de_custo(args.eficacia, args.custo_falta, args.segmentos)
        resultado["curva"] = curva_por_fracao(
            y, resultado.pop("oof"), args.fracoes, premissas, args.fracao
        )
        resultado["premissas_curva"] = premissas
        imprimir_curva(resultado["curva"], premissas)
        for c in resultado["curva"]:
            if c["linha"] == "modelo":
                pct = round(c["fracao"] * 100)
                extras[f"curva_recall_{pct}"] = c["recall"]
                extras[f"curva_lift_{pct}"] = c["lift"]
                extras[f"curva_receita_{pct}"] = c["receita_preservada_brl"]
        params_extras = {f"curva_{k}": v for k, v in premissas.items()}
    if args.permutacoes:
        resultado["permutacao"] = teste_de_permutacao(
            X, y, args.permutacoes, args.modelo, args.balanceamento, grupos, args.n_splits
        )
        imprimir_permutacao(resultado["permutacao"])
        extras["permutacao_p_valor"] = resultado["permutacao"]["p_valor"]
    if args.univariada:
        resultado["univariada"] = auc_univariada(X, y)
        imprimir_univariada(resultado["univariada"])
    if not args.sem_mlflow:
        logar_mlflow(resultado, args, len(X), tem_dado_externo(df), extras, params_extras)
        print(f"[ok] run logada no MLflow ({PIPELINE_TAG_KEY}={PIPELINE_TAG_VALUE})")
    return resultado


if __name__ == "__main__":
    main()
