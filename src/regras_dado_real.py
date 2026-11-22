"""
SaúdeJá — regras do pipeline para quando o desfecho real chega (ADR-014,
tabela "quais origens contam em cada regra").

Funções puras, com os números em `params.yaml` (`dados.peso_semente` e
`dados.split`). Hoje o `validate_data` só as **reporta** no relatório do
re-treino (o que a regra recomendaria para este dataset); aplicá-las no
treino é outra decisão, com ADR próprio. O que importa aqui é que cada regra
diga explicitamente quais origens conta:

| Regra                                   | semente | externa | produção |
|-----------------------------------------|---------|---------|----------|
| Peso da semente: desfecho real          | não     | conta   | conta    |
| Split cronológico: dias de operação     | não     | não     | conta    |
| Positivos de produção no fold de teste  | não     | não     | conta    |

A origem vem da coluna `origem` (semente, externa, producao). Dataset sem a
coluna (exportado antes dela existir) usa a regra antiga: tudo que não está na
semente é produção, e não há externa.
"""

from collections.abc import Collection, Mapping
from typing import Any

import pandas as pd

ORIGEM_SEMENTE = "semente"
ORIGEM_EXTERNA = "externa"
ORIGEM_PRODUCAO = "producao"
ORIGENS = (ORIGEM_SEMENTE, ORIGEM_EXTERNA, ORIGEM_PRODUCAO)
COLUNA_ORIGEM = "origem"

SPLIT_ALEATORIO = "aleatorio"
SPLIT_POR_GRUPO = "por_grupo"
SPLIT_CRONOLOGICO = "cronologico"


def origem_por_linha(df: pd.DataFrame, ids_semente: Collection[str] | None = None) -> pd.Series:
    """A origem de cada linha. Com a coluna `origem`, ela manda. Sem ela, o
    `id_consulta` que está na semente é semente, e o resto é produção."""
    if COLUNA_ORIGEM in df.columns:
        return df[COLUNA_ORIGEM].astype(str)
    ids = set(ids_semente or ())
    na_semente = df["id_consulta"].astype(str).isin(ids)
    return pd.Series(
        [ORIGEM_SEMENTE if s else ORIGEM_PRODUCAO for s in na_semente],
        index=df.index,
        dtype=object,
    )


def contar_por_origem(origem: pd.Series) -> dict[str, int]:
    contagem = {o: int((origem == o).sum()) for o in ORIGENS}
    contagem["desfecho_real"] = contagem[ORIGEM_EXTERNA] + contagem[ORIGEM_PRODUCAO]
    return contagem


def peso_da_semente(n_desfecho_real: int, params: Mapping[str, Any]) -> float:
    """Regra 5.1: quanto pesa cada linha da semente sintética no treino.
    Externa CONTA como desfecho real -- o objetivo da regra é tirar peso do
    sintético, e o dado externo é real. Abaixo de `limiar_reduzir`, peso 1;
    a partir dele, `peso_reduzido`; a partir de `limiar_retirar`, 0 (a
    semente sai do treino e fica só como conjunto de sanidade)."""
    regra = params["dados"]["peso_semente"]
    if n_desfecho_real >= int(regra["limiar_retirar"]):
        return 0.0
    if n_desfecho_real >= int(regra["limiar_reduzir"]):
        return float(regra["peso_reduzido"])
    return 1.0


def dias_de_producao(df: pd.DataFrame, origem: pd.Series) -> int:
    """Dias entre a primeira e a última consulta de PRODUÇÃO. Externa não
    conta: as datas do Kaggle são de 2016 e não dizem nada sobre quanto
    tempo a clínica opera."""
    producao = df.loc[origem == ORIGEM_PRODUCAO, "data_hora_agendada"]
    if producao.empty:
        return 0
    quando = pd.to_datetime(producao.astype(str), format="ISO8601", errors="coerce").dropna()
    if quando.empty:
        return 0
    return int((quando.max().normalize() - quando.min().normalize()).days)


def modo_de_split(df: pd.DataFrame, origem: pd.Series, params: Mapping[str, Any]) -> str:
    """Regra 5.2: como separar o fold de teste.
    - cronológico: com mais de `dados.split.dias_producao_cronologico` dias
      de produção, o teste passa a ser o último mês (o que o re-treino
      mensal enfrenta de verdade);
    - por grupo: com linha de id de paciente estável (produção ou externa),
      todas as consultas de um paciente ficam do mesmo lado;
    - aleatório: só semente, cujo `id_paciente` não identifica uma pessoa."""
    minimo = int(params["dados"]["split"]["dias_producao_cronologico"])
    if dias_de_producao(df, origem) > minimo:
        return SPLIT_CRONOLOGICO
    if origem.isin((ORIGEM_PRODUCAO, ORIGEM_EXTERNA)).any():
        return SPLIT_POR_GRUPO
    return SPLIT_ALEATORIO


def positivos_de_producao_no_fold(y_teste: pd.Series, origem: pd.Series) -> int:
    """Regra 5.3: faltas de PRODUÇÃO no fold de teste. Externa não conta: a
    pergunta é quando a métrica do gate passa a ser sobre a clínica, e não
    sobre a semente ou sobre o Kaggle. `y_teste` mantém o índice do dataset
    (é o que o split do `preprocess` devolve)."""
    do_teste = origem.loc[y_teste.index]
    return int(((y_teste == 1) & (do_teste == ORIGEM_PRODUCAO)).sum())


def features_por_paciente(df: pd.DataFrame) -> pd.DataFrame:
    """Regra 5.5, point-in-time: para cada consulta, o que se sabia do
    paciente NO AGENDAMENTO (data da consulta - antecedência). Só entram
    consultas anteriores do mesmo `id_paciente` com data estritamente antes
    da data do agendamento, cujo desfecho já era conhecido.

    Devolve, com o índice de `df`:
    - `consultas_anteriores`: quantas;
    - `taxa_faltas_anteriores`: faltas / consultas anteriores (0 sem nenhuma);
    - `dias_desde_ultima_consulta`: do agendamento até a última anterior
      (NaN sem nenhuma).

    Ainda não é feature do modelo: precisa de id de paciente estável
    (produção ou externa) e entra no contrato de features com ADR próprio."""
    consulta = pd.to_datetime(df["data_hora_agendada"].astype(str), format="ISO8601").dt.normalize()
    agendamento = consulta - pd.to_timedelta(df["dias_entre_agendamento_consulta"], unit="D")
    base = pd.DataFrame(
        {
            "paciente": df["id_paciente"].astype(str).to_numpy(),
            "consulta": consulta.to_numpy(),
            "falta": df["no_show"].astype(int).to_numpy(),
        }
    )
    por_dia = (
        base.groupby(["paciente", "consulta"], as_index=False)
        .agg(n=("falta", "size"), faltas=("falta", "sum"))
        .sort_values(["paciente", "consulta"])
    )
    por_dia["n_acumulado"] = por_dia.groupby("paciente")["n"].cumsum()
    por_dia["faltas_acumuladas"] = por_dia.groupby("paciente")["faltas"].cumsum()
    por_dia["ultima"] = por_dia["consulta"]

    consultas = pd.DataFrame(
        {
            "paciente": base["paciente"],
            "dia": agendamento.to_numpy(),
            "_linha": range(len(df)),
        }
    )
    juntos = pd.merge_asof(
        consultas.sort_values("dia"),
        por_dia.rename(columns={"consulta": "dia"})[
            ["paciente", "dia", "n_acumulado", "faltas_acumuladas", "ultima"]
        ].sort_values("dia"),
        on="dia",
        by="paciente",
        direction="backward",
        allow_exact_matches=False,
    ).sort_values("_linha")

    n = juntos["n_acumulado"].fillna(0).astype(int).to_numpy()
    faltas = juntos["faltas_acumuladas"].fillna(0).to_numpy()
    dias = (juntos["dia"] - juntos["ultima"]).dt.days.to_numpy()
    taxa = [f / c if c else 0.0 for f, c in zip(faltas, n, strict=True)]
    return pd.DataFrame(
        {
            "consultas_anteriores": n,
            "taxa_faltas_anteriores": taxa,
            "dias_desde_ultima_consulta": dias,
        },
        index=df.index,
    )
