"""
SaúdeJá — gate de dados do re-treino mensal: stage
`validate_data` do `dvc.yaml`, que roda **antes** do `preprocess`.

O que ele protege: o re-treino mensal roda sozinho, às 3h, sobre
o que a clínica registrou no mês. Sem esta etapa, um dataset quebrado só
aparecia -- se aparecesse -- como uma métrica estranha no fim do treino, ou
nem isso: um modelo treinado sobre idade 150 ou sobre uma especialidade que o
cadastro não oferece passaria pelo gate se as métricas agregadas
sobrevivessem. Aqui o `dvc repro` falha antes de treinar, com o motivo escrito.

Três camadas, todas **bloqueantes**:

- **schema**: colunas, nulos, `id_consulta` único, `no_show` em {0, 1};
- **domínio**: a regra de negócio do contrato (`src/contrato_features.py`) em
  cada linha, com as especialidades do cadastro (`params.yaml`) -- uma
  especialidade nova no dado é bloqueio, porque a lista do cadastro é o que a
  clínica pode produzir;
- **distribuição**: taxa de positivos dentro da faixa de `params.yaml` e um
  mínimo de positivos no fold de teste, derivado da tolerância do gate
  (1/tolerância, a regra do SLO §3.1): com menos que isso, o gate compara
  métricas cujo menor passo é maior que a própria tolerância.

E dois registros **não bloqueantes**: linhas fora do domínio do treino (o
contrato manda marcar, nunca rejeitar) e PSI por feature entre a produção e a
semente, que vira alerta no resumo do re-treino.

**Completude de rótulo não mora aqui**, ao contrário do que o plano listava:
este stage lê o CSV, que por construção só contém consultas com desfecho --
a consulta passada ainda `agendado` só é visível no banco. Ela é contada no
export (`src/export_treino.py`), que é quem fala com o Supabase.

Duas saídas, de propósito separadas:

- `dados_validados.json` (o **selo**): só existe se o dataset passou, e só
  carrega o que depende do dado (md5, linhas, positivos). É a dependência do
  `preprocess` no `dvc.yaml` -- é ela que garante a ordem entre os stages. Por
  ser mínima, mudar uma checagem aqui não muda o selo, e o modelo não é
  re-treinado por uma alteração que não tocou no dado;
- `relatorio_dados.json`: tudo o que foi medido, lido pelo gate
  (`src/retrain_gate.py`) para o resumo do run. Escrito também quando o
  dataset é barrado -- é aí que ele mais importa.
"""
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import contrato_features
from config_projeto import caminho_de_env, carregar_params, para_horario_da_clinica

PARAMS = carregar_params()

DATA_PATH = caminho_de_env("DATA_PATH", "data/consultas-treino.csv")
SEMENTE_PATH = caminho_de_env("CONSULTAS_HISTORICAS_PATH", "data/consultas-historicas.csv")
SELO_PATH = caminho_de_env("SELO_DADOS_PATH", "data/interim/dados_validados.json")
RELATORIO_PATH = caminho_de_env("RELATORIO_DADOS_PATH", "data/interim/relatorio_dados.json")

COLUNAS_OBRIGATORIAS = (
    "id_consulta",
    "id_paciente",
    *contrato_features.CAMPOS_DO_PAYLOAD,
    "no_show",
)

FEATURES_CATEGORICAS_PSI = ("sexo", "especialidade", "dia_de_semana", "horario")
FEATURES_NUMERICAS_PSI = tuple(c.nome for c in contrato_features.CAMPOS_NUMERICOS)


@dataclass
class Relatorio:
    bloqueios: list[str] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    resumo: dict[str, Any] = field(default_factory=dict)

    @property
    def aprovado(self) -> bool:
        return not self.bloqueios

    def como_dict(self) -> dict[str, Any]:
        return {
            "aprovado": self.aprovado,
            "bloqueios": self.bloqueios,
            "alertas": self.alertas,
            "resumo": self.resumo,
        }


# --------------------------------------------------------------------------
# checagens
# --------------------------------------------------------------------------
def minimo_de_positivos_no_fold(params: dict[str, Any]) -> int:
    """1/tolerância das métricas de contagem, arredondado para cima (SLO
    §3.1). Com 0.05 dá 20; o fold vigente tem 21."""
    tolerancias = params["gate"]["tolerancia"]
    menor = min(float(tolerancias["recall_1"]), float(tolerancias["f1_1"]))
    return math.ceil(round(1 / menor, 9))


def positivos_no_fold_de_teste(y: pd.Series) -> int:
    """Positivos que o split do `preprocess` vai deixar no fold de teste. O
    split estratificado só depende de `y`, então rodá-lo aqui dá exatamente
    o fold que o treino vai usar -- sem estimar."""
    import preprocess

    _, _, _, y_teste = preprocess.dividir_treino_teste(y.to_frame(), y)
    return int(y_teste.sum())


def _checar_schema(df: pd.DataFrame, relatorio: Relatorio) -> bool:
    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
    if faltando:
        relatorio.bloqueios.append(f"schema: colunas ausentes {faltando}")
        return False
    nulos = {c: int(n) for c, n in df[list(COLUNAS_OBRIGATORIAS)].isna().sum().items() if n}
    if nulos:
        relatorio.bloqueios.append(f"schema: valores nulos por coluna {nulos}")
    duplicados = int(df["id_consulta"].astype(str).duplicated().sum())
    if duplicados:
        relatorio.bloqueios.append(f"schema: {duplicados} id_consulta duplicado(s)")
    rotulos = set(df["no_show"].dropna().unique()) - {0, 1}
    if rotulos:
        relatorio.bloqueios.append("schema: no_show fora de {0, 1}")
    return not nulos and not rotulos


def _checar_dominio(df: pd.DataFrame, relatorio: Relatorio, especialidades: list[str]) -> None:
    """Regra de negócio linha a linha (bloqueia) e, para quem passa, a contagem
    de campos fora do domínio do treino (só registra)."""
    colunas = list(contrato_features.CAMPOS_DO_PAYLOAD)
    violacoes: list[tuple[str, contrato_features.ViolacaoDoContrato]] = []
    fora: dict[str, int] = {}
    for identificador, linha in zip(
        df["id_consulta"].astype(str), df[colunas].to_dict("records"), strict=True
    ):
        try:
            validada = contrato_features.validar_payload(linha, especialidades)
        except contrato_features.ViolacaoDoContrato as exc:
            violacoes.append((identificador, exc))
            continue
        for campo in validada.campos_fora_do_dominio:
            fora[campo] = fora.get(campo, 0) + 1
    relatorio.resumo["fora_do_dominio_do_treino"] = fora

    if violacoes:
        por_regra: dict[str, int] = {}
        for _, violacao in violacoes:
            por_regra[str(violacao)] = por_regra.get(str(violacao), 0) + 1
        relatorio.bloqueios.append(
            f"domínio: {len(violacoes)} linha(s) fora da regra de negócio {por_regra} -- "
            f"primeiras: {[i for i, _ in violacoes[:5]]}"
        )


def _checar_distribuicao(df: pd.DataFrame, relatorio: Relatorio, params: dict[str, Any]) -> None:
    y = df["no_show"].astype(int)
    positivos = int(y.sum())
    taxa = positivos / len(y) if len(y) else 0.0
    relatorio.resumo["linhas"] = len(y)
    relatorio.resumo["positivos"] = positivos
    relatorio.resumo["taxa_positivos"] = round(taxa, 6)

    faixa = params["dados"]["taxa_positivos"]
    if not float(faixa["minimo"]) <= taxa <= float(faixa["maximo"]):
        relatorio.bloqueios.append(
            f"distribuição: taxa de positivos {taxa:.1%} fora da faixa "
            f"[{float(faixa['minimo']):.0%}, {float(faixa['maximo']):.0%}]"
        )

    minimo = minimo_de_positivos_no_fold(params)
    try:
        no_fold = positivos_no_fold_de_teste(y)
    except ValueError as exc:  # uma classe só, ou dataset pequeno demais para o split
        relatorio.bloqueios.append(f"distribuição: split estratificado impossível ({exc})")
        return
    relatorio.resumo["positivos_no_fold_de_teste"] = no_fold
    relatorio.resumo["minimo_de_positivos_no_fold"] = minimo
    if no_fold < minimo:
        relatorio.bloqueios.append(
            f"distribuição: {no_fold} positivos no fold de teste, abaixo do mínimo de {minimo} "
            "(1/tolerância do gate, SLO §3.1) -- o gate decidiria por ruído de amostragem"
        )


# --------------------------------------------------------------------------
# PSI produção x semente (alerta, não bloqueio)
# --------------------------------------------------------------------------
def psi(referencia: pd.Series, atual: pd.Series, categorica: bool) -> float:
    """Population Stability Index. Numérica: decis da referência. Categórica:
    uma faixa por categoria. Proporção zero é trocada por 1e-4 para o log
    existir -- convenção usual, que infla o PSI de categoria nova (e é esse o
    efeito que se quer ver)."""
    epsilon = 1e-4
    bordas = (
        np.array([])
        if categorica
        else np.unique(np.quantile(referencia.astype(float), np.linspace(0, 1, 11)))
    )
    if categorica or len(bordas) < 3:
        categorias = sorted(set(referencia) | set(atual), key=str)
        ref = referencia.value_counts(normalize=True).reindex(categorias, fill_value=0.0)
        cur = atual.value_counts(normalize=True).reindex(categorias, fill_value=0.0)
    else:
        bordas[0], bordas[-1] = -np.inf, np.inf
        ref = pd.cut(referencia.astype(float), bordas).value_counts(normalize=True, sort=False)
        cur = pd.cut(atual.astype(float), bordas).value_counts(normalize=True, sort=False)
    ref = ref.clip(lower=epsilon)
    cur = cur.clip(lower=epsilon)
    return float(((cur - ref) * np.log(cur / ref)).sum())


def _com_features_temporais(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    quando = [para_horario_da_clinica(str(v)) for v in df["data_hora_agendada"]]
    df["dia_de_semana"] = [q.weekday() for q in quando]
    df["horario"] = [q.hour for q in quando]
    return df


def _registrar_psi(
    df: pd.DataFrame, semente: pd.DataFrame | None, relatorio: Relatorio, limite: float
) -> None:
    if semente is None:
        relatorio.resumo["psi"] = None
        relatorio.alertas.append("PSI não calculado: semente indisponível")
        return
    ids_semente = set(semente["id_consulta"].astype(str))
    producao = df[~df["id_consulta"].astype(str).isin(ids_semente)]
    relatorio.resumo["linhas_de_producao"] = len(producao)
    if producao.empty:
        relatorio.resumo["psi"] = None
        return

    ref = _com_features_temporais(semente)
    cur = _com_features_temporais(producao)
    valores = {}
    for feature in (*FEATURES_NUMERICAS_PSI, *FEATURES_CATEGORICAS_PSI):
        valores[feature] = round(
            psi(ref[feature], cur[feature], feature in FEATURES_CATEGORICAS_PSI), 6
        )
    relatorio.resumo["psi"] = valores
    altos = {f: v for f, v in valores.items() if v > limite}
    if altos:
        relatorio.alertas.append(
            f"PSI acima de {limite} (produção x semente, {len(producao)} linha(s) de produção): "
            f"{altos}"
        )


# --------------------------------------------------------------------------
# orquestração
# --------------------------------------------------------------------------
def validar(
    df: pd.DataFrame, semente: pd.DataFrame | None = None, params: dict[str, Any] | None = None
) -> Relatorio:
    """Função pura sobre DataFrames -- é o que os testes exercitam."""
    params = params or PARAMS
    relatorio = Relatorio()
    if not _checar_schema(df, relatorio):
        return relatorio
    _checar_dominio(df, relatorio, list(params["cadastro"]["especialidades"]))
    _checar_distribuicao(df, relatorio, params)
    _registrar_psi(df, semente, relatorio, float(params["dados"]["psi_alerta"]))
    return relatorio


def _md5(caminho: str) -> str:
    with open(caminho, "rb") as f:
        return hashlib.md5(f.read(), usedforsecurity=False).hexdigest()


def formatar_resumo(relatorio: Relatorio) -> str:
    titulo = "aprovado" if relatorio.aprovado else "BLOQUEADO"
    linhas = [f"### Validação de dados do re-treino — {titulo}", ""]
    linhas += [f"- **bloqueio:** {b}" for b in relatorio.bloqueios]
    linhas += [f"- alerta: {a}" for a in relatorio.alertas]
    resumo = relatorio.resumo
    if "linhas" in resumo:
        linhas.append(
            f"- {resumo['linhas']} linhas, {resumo['positivos']} positivos "
            f"({resumo['taxa_positivos']:.1%}); "
            f"{resumo.get('positivos_no_fold_de_teste', '?')} no fold de teste "
            f"(mínimo {resumo.get('minimo_de_positivos_no_fold', '?')})"
        )
    if resumo.get("fora_do_dominio_do_treino"):
        linhas.append(
            f"- fora do domínio do treino (predito e marcado, não bloqueia): "
            f"{resumo['fora_do_dominio_do_treino']}"
        )
    return "\n".join(linhas)


def _escrever_json(caminho: str, conteudo: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
    tmp = f"{caminho}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(conteudo, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(tmp, caminho)


def main() -> int:
    df = pd.read_csv(DATA_PATH)
    semente = pd.read_csv(SEMENTE_PATH) if Path(SEMENTE_PATH).exists() else None
    relatorio = validar(df, semente)

    _escrever_json(RELATORIO_PATH, relatorio.como_dict())
    # Texto ASCII-seguro no console do Windows (mesmo cuidado do retrain_gate).
    texto = formatar_resumo(relatorio)
    codec = sys.stdout.encoding or "ascii"
    print(texto.encode(codec, errors="replace").decode(codec, errors="replace"))

    if not relatorio.aprovado:
        return 1
    _escrever_json(
        SELO_PATH,
        {
            "dataset_md5": _md5(DATA_PATH),
            "linhas": relatorio.resumo["linhas"],
            "positivos": relatorio.resumo["positivos"],
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
