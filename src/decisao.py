"""
SaúdeJá — regra de decisão do lembrete pago.

Dois modos (`decision.modo` em `params.yaml`):

- **`threshold`** (o de sempre): avisa quem tem probabilidade >= `decision.
  threshold`. A fração da fila que recebe lembrete varia com a calibração do
  modelo e com a fila do dia (16% a 29% no campeão LightGBM, só por diferença
  de amostra) -- e um modelo calibrado quase nunca passa de 0,6.
- **`top_fracao`**: avisa os K pacientes de maior probabilidade da fila, com
  K = ceil(`fracao_da_fila` x tamanho da fila). É a regra do orçamento do
  BRIEFING ("cortar 70% do custo de mensageria" = avisar 30%), e não depende
  da escala da probabilidade, só da ordem. Duas válvulas, ambas opcionais:
  `probabilidade_minima` (abaixo dela não avisa, mesmo sobrando orçamento) e
  `probabilidade_certa` (acima dela avisa, mesmo estourando a fração). As duas
  estão na escala da probabilidade, então só têm sentido com modelo calibrado.

A decisão é sobre a **fila inteira** (é preciso ver todas as probabilidades
para saber quem está entre os K), não linha a linha. Quem usa: o job D-2
(`src/jobs/inferencia_diaria.py`), o gate (taxa de disparo projetada no fold de
teste) e o `validate.py` (`recall_at_fracao`). A API `/predict` continua com o
threshold: uma predição avulsa não tem fila.

Módulo puro, sem I/O: os testes exercitam a regra sem banco nem modelo.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

MODO_THRESHOLD = "threshold"
MODO_TOP_FRACAO = "top_fracao"
MODOS = (MODO_THRESHOLD, MODO_TOP_FRACAO)


@dataclass(frozen=True)
class ConfigDecisao:
    """Uma regra de decisão completa. `threshold` existe nos dois modos: no
    `top_fracao` ele não decide o lembrete, mas continua sendo o corte da API
    `/predict` e o ponto em que `recall_1`/`f1_1` são medidos."""

    modo: str
    threshold: float
    fracao_da_fila: float | None = None
    probabilidade_minima: float | None = None
    probabilidade_certa: float | None = None

    def __post_init__(self) -> None:
        validar(self)

    def como_dict(self) -> dict[str, Any]:
        return asdict(self)


def _probabilidade(valor: Any, nome: str) -> float | None:
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, int | float):
        raise ValueError(f"decision.{nome} deve ser um número em [0, 1], veio {valor!r}")
    valor = float(valor)
    if not 0.0 <= valor <= 1.0:
        raise ValueError(f"decision.{nome} fora de [0, 1]: {valor}")
    return valor


def validar(config: ConfigDecisao) -> None:
    if config.modo not in MODOS:
        raise ValueError(f"decision.modo deve ser um de {MODOS}, veio {config.modo!r}")
    # O threshold pode passar de 1 (ninguém passa: "nunca avisa por risco");
    # só não pode ser negativo nem não numérico.
    if (
        isinstance(config.threshold, bool)
        or not isinstance(config.threshold, int | float)
        or config.threshold < 0
    ):
        raise ValueError(f"decision.threshold deve ser um número >= 0, veio {config.threshold!r}")
    minima = _probabilidade(config.probabilidade_minima, "probabilidade_minima")
    certa = _probabilidade(config.probabilidade_certa, "probabilidade_certa")
    if config.modo == MODO_TOP_FRACAO:
        fracao = config.fracao_da_fila
        if fracao is None or isinstance(fracao, bool) or not 0.0 < float(fracao) <= 1.0:
            raise ValueError(
                f"decision.fracao_da_fila deve estar em (0, 1] no modo top_fracao, veio {fracao!r}"
            )
    if minima is not None and certa is not None and minima > certa:
        raise ValueError(
            f"decision.probabilidade_minima ({minima}) maior que probabilidade_certa ({certa})"
        )


def config_de_params(params: Mapping[str, Any]) -> ConfigDecisao:
    """A regra descrita no bloco `decision` do `params.yaml`. Sem `modo`, é o
    threshold (o comportamento de antes deste módulo)."""
    bloco = dict(params.get("decision") or {})
    if "threshold" not in bloco:
        raise ValueError("params.yaml não define decision.threshold")
    return ConfigDecisao(
        modo=bloco.get("modo", MODO_THRESHOLD),
        threshold=float(bloco["threshold"]),
        fracao_da_fila=_opcional(bloco.get("fracao_da_fila")),
        probabilidade_minima=_opcional(bloco.get("probabilidade_minima")),
        probabilidade_certa=_opcional(bloco.get("probabilidade_certa")),
    )


def config_registrada(registro: Mapping[str, Any]) -> ConfigDecisao:
    """A regra com que um modelo foi medido e aprovado, lida do registro dele
    (`champion_metrics.json` ou `canario.json`). Registro sem o bloco `decisao`
    é anterior a este módulo: modo threshold, com o `decision_threshold`."""
    bloco = registro.get("decisao")
    if bloco:
        return ConfigDecisao(**bloco)
    if "decision_threshold" not in registro:
        raise ValueError("registro sem `decisao` nem `decision_threshold`")
    return ConfigDecisao(modo=MODO_THRESHOLD, threshold=float(registro["decision_threshold"]))


def _opcional(valor: Any) -> float | None:
    return None if valor is None else float(valor)


@dataclass(frozen=True)
class DecisaoDaFila:
    """O resultado de `decidir_fila`, na ordem das probabilidades recebidas.

    - `avisar[i]`: a linha i recebe lembrete pago;
    - `posicao[i]`: posição da linha i na fila ordenada por risco (1 = maior
      probabilidade; empate decidido pela ordem de chegada);
    - `corte`: a menor probabilidade avisada (ou, sem ninguém avisado, o piso
      que barrou todos). É o que vai para `predicoes.threshold_usado`: no modo
      threshold é o próprio threshold; no top_fracao é o corte que a fila do dia
      produziu. Em empate exato na fronteira do K, uma linha não avisada pode
      ter a mesma probabilidade do corte -- a `posicao` desempata a auditoria;
    - `k`: o orçamento do top_fracao (`None` no modo threshold)."""

    avisar: list[bool]
    posicao: list[int]
    corte: float
    k: int | None
    modo: str


def tamanho_do_orcamento(n: int, fracao: float) -> int:
    """K = ceil(fracao x n). Para cima, de propósito: com a fila de uma clínica
    pequena (10 consultas), 30% vira 3 e não 2 -- errar para o lado de avisar
    custa centavos; para o outro, R$ 180."""
    # round antes do ceil: 0.3 * 20 dá 6.000000000000001 em ponto flutuante
    return math.ceil(round(fracao * n, 9))


def posicoes_por_risco(probabilidades: Sequence[float]) -> list[int]:
    """Posição de cada linha na fila ordenada por probabilidade decrescente
    (1 = maior). Ordenação estável: empate fica na ordem de chegada, e o
    resultado é reproduzível."""
    ordem = np.argsort(-np.asarray(probabilidades, dtype=float), kind="stable")
    posicao = [0] * len(ordem)
    for rank, indice in enumerate(ordem, start=1):
        posicao[int(indice)] = rank
    return posicao


def decidir_fila(probabilidades: Sequence[float], config: ConfigDecisao) -> DecisaoDaFila:
    """Quem recebe lembrete pago, dada a fila inteira de probabilidades."""
    probs = [float(p) for p in probabilidades]
    posicao = posicoes_por_risco(probs)

    if config.modo == MODO_THRESHOLD:
        avisar = [p >= config.threshold for p in probs]
        return DecisaoDaFila(avisar, posicao, float(config.threshold), None, config.modo)

    # `validar` já garante a fração no top_fracao; o `or` só estreita o tipo.
    k = tamanho_do_orcamento(len(probs), float(config.fracao_da_fila or 0.0))
    minima = config.probabilidade_minima
    certa = config.probabilidade_certa
    avisar = []
    for p, pos in zip(probs, posicao, strict=True):
        no_orcamento = pos <= k and (minima is None or p >= minima)
        avisar.append(no_orcamento or (certa is not None and p >= certa))
    avisados = [p for p, a in zip(probs, avisar, strict=True) if a]
    # Ninguém avisado (com fila não vazia) só acontece com a probabilidade
    # mínima barrando todos: ela é o corte que valeu.
    sem_avisados = float(minima) if minima is not None else 1.0
    corte = min(avisados) if avisados else sem_avisados
    return DecisaoDaFila(avisar, posicao, corte, k, config.modo)


def taxa_de_disparo(probabilidades: Sequence[float], config: ConfigDecisao) -> float:
    """Fração da fila que receberia lembrete pago pela regra. No gate, é a
    taxa projetada no fold de teste (o fold inteiro faz o papel de uma fila)."""
    if len(probabilidades) == 0:
        return 0.0
    return float(np.mean(decidir_fila(probabilidades, config).avisar))


def recall_na_fracao(y_true: Any, y_proba: Any, fracao: float) -> tuple[float, float]:
    """Recall quando se avisa os ceil(fracao x n) pacientes de maior
    probabilidade. É a pergunta do negócio ("com o orçamento de 30% da fila,
    quantos faltosos pegamos?"), e não depende de threshold nem de calibração.
    Devolve (recall, fração efetivamente avisada); sem positivos, recall 0."""
    y_true = np.asarray(y_true)
    n = len(y_true)
    k = tamanho_do_orcamento(n, fracao)
    ordem = np.argsort(-np.asarray(y_proba, dtype=float), kind="stable")
    positivos = y_true.sum()
    capturados = y_true[ordem[:k]].sum()
    recall = float(capturados / positivos) if positivos else 0.0
    return recall, k / n
