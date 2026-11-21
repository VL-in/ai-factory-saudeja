"""
SaúdeJá — estudo de simulação com o dataset externo de desfecho real
(ADR-014, passo 6). Exploratório, como `avaliar_cv.py` e `tune.py`: NÃO é
stage do dvc.yaml, não grava artefato e não muda o modelo em produção.

Três estudos, todos avaliados no **teste reservado** (`teste-reservado.csv`,
10 mil consultas de pacientes que nunca entram no treino) e treinados com
linhas do **pool de simulação** (o pool sem os pacientes do reservado):

- **curva** (5a) -- curva de aprendizado com dado real: treina com n linhas,
  `--repeticoes` sorteios por n, e mede roc_auc e lift@30% no reservado.
  Responde quantas linhas são necessárias para o modelo passar do piso de
  0,60 e quanto se ganha de ~900 linhas (90 dias de uma clínica) para ~3.600
  (12 meses) -- o número que falta para discutir a janela de 90 dias.
- **sinal** (5b) -- recuperação de sinal conhecido: o rótulo é sorteado de
  uma logística com coeficientes conhecidos sobre linhas reais do pool,
  calibrada para AUC verdadeira de 0,60, 0,65 e 0,70. A AUC verdadeira é
  calculável (a probabilidade real é conhecida). O pipeline (contrato,
  encoding, modelo) deve recuperar perto dela; se perder muito, há bug.
- **mistura** (5c) -- semente + n linhas reais, com peso da semente em
  {1, 0,3, 0}. É o teste empírico dos limiares da regra de peso da semente
  (200 e 600 linhas reais, peso 0,3).

Modelo: o do teste de sinal pré-registrado (regressão logística C=0,3 com
`class_weight="balanced"`), e o LightGBM com `class_weight` como
sensibilidade. A curva usa só as features reais; a mistura usa todas as do
pipeline (a semente carrega o padrão fabricado de "sexta 17h", e medir se ele
atrapalha no dado real é a pergunta). Nenhum dado gerado na 5b entra em treino
oficial. A saída é só agregado.

Uso (na raiz do repo):
    python scripts/simular_dados.py --estudo curva
    python scripts/simular_dados.py --estudo todos --repeticoes 10 --json saida.json
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy import optimize
from sklearn.metrics import roc_auc_score

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import contrato_dataset_externo as cde  # noqa: E402
import preprocess  # noqa: E402
import train  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "avaliar_cv", REPO_ROOT / "scripts" / "avaliar_cv.py"
)
# o avaliar_cv não é pacote (scripts/ não tem __init__): carregado pelo caminho
if _spec is None or _spec.loader is None:
    raise ImportError("scripts/avaliar_cv.py não encontrado")
avaliar_cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(avaliar_cv)

CAMINHO_SEMENTE = REPO_ROOT / "data" / "consultas-historicas.csv"
RANDOM_STATE = 42
REPETICOES = 10
FRACAO_DA_FILA = 0.30
PISO_ROC_AUC = 0.60

FEATURES_REAIS = (
    "idade",
    "sexo",
    "dias_entre_agendamento_consulta",
    "historico_noshow",
    "dia_de_semana",
)
MODELOS = ("logistica", "lightgbm")
TAMANHOS_CURVA = (100, 200, 400, 900, 1500, 3600, 6000)
TAMANHOS_SINAL = (200, 900, 1500, 3600)
AUCS_VERDADEIRAS = (0.60, 0.65, 0.70)
TAMANHOS_MISTURA = (100, 200, 400, 600, 900, 1500)
PESOS_SEMENTE = (1.0, 0.3, 0.0)

# Coeficientes do modelo verdadeiro da 5b, ANTES da escala que calibra a AUC
# (em desvios-padrão para as numéricas). Os sinais e tamanhos relativos são
# arbitrários: o que importa é a AUC final ser conhecida. Inclui categóricas
# (sexo, dia da semana, especialidade) para exercitar o encoding.
COEF_NUMERICOS = {"idade": -0.6, "dias_entre_agendamento_consulta": 0.8, "historico_noshow": 0.5}
COEF_SEXO_M = 0.3
COEF_DIA = {0: 0.0, 1: -0.2, 2: -0.1, 3: 0.2, 4: 0.5, 5: 0.3}
COEF_ESPECIALIDADE = {"cardiologia": -0.4, "pediatria": 0.4, "dermatologia": 0.3}


# --------------------------------------------------------------------------
# modelo e métricas
# --------------------------------------------------------------------------
def ajustar(X, y, modelo, peso=None):
    """Mesmo ajuste do `avaliar_cv` (logística com one-hot e padronização;
    LightGBM com os hiperparâmetros do params.yaml), com `class_weight`
    balanceado e peso por linha opcional (o peso da semente, na 5c)."""
    if modelo == "logistica":
        estimador = avaliar_cv._logistica(X, "balanced")
        estimador.fit(X, y, modelo__sample_weight=peso)
    elif modelo == "lightgbm":
        cat_cols = avaliar_cv._colunas_categoricas(X)
        estimador = lgb.LGBMClassifier(
            **train._construir_modelo_kwargs(), class_weight="balanced", verbose=-1
        )
        estimador.fit(X, y, sample_weight=peso, categorical_feature=cat_cols)
    else:
        raise ValueError(f"modelo desconhecido: {modelo}")
    return estimador


def medir(y, proba):
    recall, fracao = avaliar_cv.recall_na_fracao(np.asarray(y), proba, FRACAO_DA_FILA)
    return {"roc_auc": float(roc_auc_score(y, proba)), "lift": float(recall / fracao)}


def resumir(valores):
    v = np.asarray(valores, dtype=float)
    return {
        "media": float(v.mean()),
        "p025": float(np.quantile(v, 0.025)),
        "p975": float(np.quantile(v, 0.975)),
    }


def _X(df, features=None):
    X, y, mapa = preprocess.preprocessar(df)
    return (X[list(features)] if features else X), y, mapa


# --------------------------------------------------------------------------
# 5a -- curva de aprendizado
# --------------------------------------------------------------------------
def estudo_curva(
    simulacao,
    reservado,
    tamanhos=TAMANHOS_CURVA,
    repeticoes=REPETICOES,
    modelos=MODELOS,
    random_state=RANDOM_STATE,
):
    X_teste, y_teste, _ = _X(reservado, FEATURES_REAIS)
    resultado = {}
    for modelo in modelos:
        por_n = {}
        for n in tamanhos:
            if n > len(simulacao):
                continue
            medidas = []
            for r in range(repeticoes):
                treino = cde.amostra_estratificada(simulacao, n, random_state + r)
                X, y, _ = _X(treino, FEATURES_REAIS)
                proba = ajustar(X, y, modelo).predict_proba(X_teste)[:, 1]
                medidas.append(medir(y_teste, proba))
            por_n[n] = {m: resumir([d[m] for d in medidas]) for m in ("roc_auc", "lift")} | {
                "acima_do_piso": float(np.mean([d["roc_auc"] > PISO_ROC_AUC for d in medidas]))
            }
        X, y, _ = _X(simulacao, FEATURES_REAIS)
        teto = medir(y_teste, ajustar(X, y, modelo).predict_proba(X_teste)[:, 1])
        resultado[modelo] = {"por_n": por_n, "teto_pool_inteiro": teto | {"n": len(simulacao)}}
    return resultado


# --------------------------------------------------------------------------
# 5b -- recuperação de sinal conhecido
# --------------------------------------------------------------------------
def logito_base(df, referencia=None):
    """Combinação linear (sem escala nem intercepto) do modelo verdadeiro,
    calculada direto das colunas do export -- independente do encoding do
    pipeline, que é o que se quer testar. As numéricas são padronizadas com a
    média e o desvio de `referencia` (o mesmo para treino e teste)."""
    referencia = df if referencia is None else referencia
    z = np.zeros(len(df))
    for coluna, coef in COEF_NUMERICOS.items():
        ref = referencia[coluna].astype(float)
        v = df[coluna].astype(float)
        z += coef * ((v - ref.mean()) / (ref.std() or 1.0)).to_numpy()
    z += COEF_SEXO_M * (df["sexo"] == "M").to_numpy()
    dia = pd.to_datetime(df["data_hora_agendada"]).dt.dayofweek
    z += dia.map(COEF_DIA).fillna(0.0).to_numpy()
    z += df["especialidade"].map(COEF_ESPECIALIDADE).fillna(0.0).to_numpy()
    return z


def _probabilidade(z, escala, intercepto):
    return 1 / (1 + np.exp(-(escala * z + intercepto)))


def calibrar(z, auc_alvo, prevalencia=0.28):
    """(escala, intercepto) tais que a AUC verdadeira (a da probabilidade
    real contra o rótulo sorteado dela, em esperança) é `auc_alvo` e a
    prevalência média é `prevalencia`. A AUC esperada é calculada sem sortear:
    AUC = P(score do positivo > do negativo) com pesos p e 1-p."""

    def auc_esperada(escala):
        intercepto = _intercepto(z, escala, prevalencia)
        p = _probabilidade(z, escala, intercepto)
        # por valor distinto de z: empate entre positivo e negativo vale 1/2
        valores, inverso = np.unique(z, return_inverse=True)
        positivos = np.bincount(inverso, weights=p, minlength=len(valores))
        negativos = np.bincount(inverso, weights=1 - p, minlength=len(valores))
        abaixo = np.cumsum(negativos) - negativos
        favoraveis = (positivos * (abaixo + 0.5 * negativos)).sum()
        return float(favoraveis / (positivos.sum() * negativos.sum()))

    # escala em unidades do desvio de z: o intervalo de busca vale para qualquer z
    desvio = float(np.std(z)) or 1.0
    escala = optimize.brentq(lambda s: auc_esperada(s) - auc_alvo, 1e-4 / desvio, 10.0 / desvio)
    return escala, _intercepto(z, escala, prevalencia)


def _intercepto(z, escala, prevalencia):
    return optimize.brentq(
        lambda b: _probabilidade(z, escala, b).mean() - prevalencia, -200.0, 200.0
    )


def estudo_sinal(
    simulacao,
    reservado,
    aucs=AUCS_VERDADEIRAS,
    tamanhos=TAMANHOS_SINAL,
    repeticoes=REPETICOES,
    modelos=MODELOS,
    random_state=RANDOM_STATE,
):
    """Para cada AUC verdadeira: sorteia o rótulo do pool e do reservado com
    a mesma probabilidade conhecida, treina com n linhas do pool e mede no
    reservado. `recuperacao` = AUC medida / AUC do oráculo no mesmo teste."""
    rng = np.random.default_rng(random_state)
    z_sim = logito_base(simulacao, simulacao)
    z_res = logito_base(reservado, simulacao)
    resultado = {}
    for auc_alvo in aucs:
        escala, intercepto = calibrar(np.concatenate([z_sim, z_res]), auc_alvo)
        p_sim = _probabilidade(z_sim, escala, intercepto)
        p_res = _probabilidade(z_res, escala, intercepto)
        teste = reservado.assign(no_show=rng.binomial(1, p_res))
        X_teste, y_teste, mapa_teste = _X(teste)
        oraculo = float(roc_auc_score(y_teste, p_res))
        sintetico = simulacao.assign(no_show=rng.binomial(1, p_sim))
        por_modelo = {}
        for modelo in modelos:
            por_n = {}
            for n in tamanhos:
                medidas = []
                for r in range(repeticoes):
                    treino = cde.amostra_estratificada(sintetico, n, random_state + r)
                    X, y, mapa = _X(treino)
                    if mapa != mapa_teste:  # encoding divergente = bug do pipeline
                        raise RuntimeError("mapa de especialidade diferente entre treino e teste")
                    proba = ajustar(X, y, modelo).predict_proba(X_teste)[:, 1]
                    medidas.append(medir(y_teste, proba)["roc_auc"])
                aucs_n = np.asarray(medidas)
                por_n[n] = resumir(aucs_n) | {
                    "recuperacao": float(aucs_n.mean() / oraculo),
                    "acima_do_piso": float((aucs_n > PISO_ROC_AUC).mean()),
                }
            por_modelo[modelo] = por_n
        resultado[f"{auc_alvo:.2f}"] = {"auc_oraculo_no_teste": oraculo, "modelos": por_modelo}
    return resultado


# --------------------------------------------------------------------------
# 5c -- mistura semente + real
# --------------------------------------------------------------------------
def estudo_mistura(
    simulacao,
    reservado,
    semente,
    tamanhos=TAMANHOS_MISTURA,
    pesos=PESOS_SEMENTE,
    repeticoes=REPETICOES,
    modelos=MODELOS,
    random_state=RANDOM_STATE,
):
    """Treina com a semente (peso w por linha) + n linhas reais (peso 1) e
    mede no reservado real, com todas as features do pipeline. Peso 0 é o
    mesmo que treinar só com as n linhas reais."""
    X_teste, y_teste, mapa_teste = _X(reservado)
    colunas = list(X_teste.columns)
    X_sem, y_sem, mapa_sem = _X(semente)
    if mapa_sem != mapa_teste:
        raise RuntimeError("mapa de especialidade diferente entre semente e reservado")
    resultado = {}
    for modelo in modelos:
        so_semente = medir(
            y_teste, ajustar(X_sem[colunas], y_sem, modelo).predict_proba(X_teste)[:, 1]
        )
        por_n = {}
        for n in tamanhos:
            por_peso = {}
            for w in pesos:
                medidas = []
                for r in range(repeticoes):
                    treino = cde.amostra_estratificada(simulacao, n, random_state + r)
                    X_real, y_real, _ = _X(treino)
                    if w > 0:
                        X = pd.concat([X_sem[colunas], X_real[colunas]], ignore_index=True)
                        y = pd.concat([y_sem, y_real], ignore_index=True)
                        peso = np.concatenate([np.full(len(y_sem), w), np.ones(len(y_real))])
                    else:
                        X, y, peso = X_real[colunas].reset_index(drop=True), y_real, None
                    proba = ajustar(X, y, modelo, peso).predict_proba(X_teste)[:, 1]
                    medidas.append(medir(y_teste, proba))
                por_peso[f"{w:g}"] = {
                    m: resumir([d[m] for d in medidas]) for m in ("roc_auc", "lift")
                }
            por_n[n] = por_peso
        resultado[modelo] = {"so_semente": so_semente, "por_n": por_n}
    return resultado


# --------------------------------------------------------------------------
# saída
# --------------------------------------------------------------------------
def imprimir_curva(r):
    for modelo, dados in r.items():
        print(
            f"\n[5a curva] {modelo} -- roc_auc e lift@30% no teste reservado (media [p2.5, p97.5])"
        )
        for n, d in dados["por_n"].items():
            a, lf = d["roc_auc"], d["lift"]
            print(
                f"  n={n:>6}  auc {a['media']:.3f} [{a['p025']:.3f}, {a['p975']:.3f}]  "
                f"lift {lf['media']:.2f} [{lf['p025']:.2f}, {lf['p975']:.2f}]  "
                f"auc>0,60 em {d['acima_do_piso']:.0%}"
            )
        t = dados["teto_pool_inteiro"]
        print(f"  teto (pool inteiro, n={t['n']}): auc {t['roc_auc']:.3f}, lift {t['lift']:.2f}")


def imprimir_sinal(r):
    for alvo, dados in r.items():
        print(
            f"\n[5b sinal] AUC verdadeira {alvo} -- oraculo no teste "
            f"{dados['auc_oraculo_no_teste']:.3f}"
        )
        for modelo, por_n in dados["modelos"].items():
            for n, d in por_n.items():
                print(
                    f"  {modelo:<9} n={n:>5}  auc {d['media']:.3f} "
                    f"[{d['p025']:.3f}, {d['p975']:.3f}]"
                    f"  recuperacao {d['recuperacao']:.1%}  auc>0,60 em {d['acima_do_piso']:.0%}"
                )


def imprimir_mistura(r):
    for modelo, dados in r.items():
        s = dados["so_semente"]
        print(
            f"\n[5c mistura] {modelo} -- so a semente (380): auc {s['roc_auc']:.3f}, "
            f"lift {s['lift']:.2f}; colunas: peso da semente"
        )
        for n, por_peso in dados["por_n"].items():
            partes = [
                f"w={w}: auc {d['roc_auc']['media']:.3f} lift {d['lift']['media']:.2f}"
                for w, d in por_peso.items()
            ]
            print(f"  n real={n:>5}  " + "  |  ".join(partes))


def carregar(caminho_semente=CAMINHO_SEMENTE):
    pool = cde.carregar_adaptado(cde.CAMINHO_POOL)
    reservado = cde.carregar_adaptado(cde.CAMINHO_RESERVADO)
    simulacao = cde.pool_de_simulacao(pool, reservado)
    semente = pd.read_csv(caminho_semente)
    return simulacao, reservado, semente


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--estudo", choices=("curva", "sinal", "mistura", "todos"), default="todos")
    parser.add_argument("--repeticoes", type=int, default=REPETICOES)
    parser.add_argument("--modelos", nargs="+", choices=MODELOS, default=list(MODELOS))
    parser.add_argument("--json", type=Path, default=None, help="grava os agregados em JSON")
    args = parser.parse_args(argv)

    simulacao, reservado, semente = carregar()
    print(
        f"[dados] pool de simulacao {len(simulacao)} linhas; teste reservado {len(reservado)} "
        f"linhas ({reservado['no_show'].mean():.1%} de falta); semente {len(semente)}"
    )
    saida = {}
    if args.estudo in ("curva", "todos"):
        saida["curva"] = estudo_curva(
            simulacao, reservado, repeticoes=args.repeticoes, modelos=args.modelos
        )
        imprimir_curva(saida["curva"])
    if args.estudo in ("sinal", "todos"):
        saida["sinal"] = estudo_sinal(
            simulacao, reservado, repeticoes=args.repeticoes, modelos=args.modelos
        )
        imprimir_sinal(saida["sinal"])
    if args.estudo in ("mistura", "todos"):
        saida["mistura"] = estudo_mistura(
            simulacao, reservado, semente, repeticoes=args.repeticoes, modelos=args.modelos
        )
        imprimir_mistura(saida["mistura"])
    if args.json:
        args.json.write_text(json.dumps(saida, indent=2, default=str), encoding="utf-8")
    return saida


if __name__ == "__main__":
    main()
