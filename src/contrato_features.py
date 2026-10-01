"""
SaúdeJá — contrato de features (Passo 10.3): o que um dado cru precisa
satisfazer para virar feature do modelo, e o que acontece quando não satisfaz.

Antes deste módulo as proteções estavam espalhadas e incompletas: Pydantic só
na API, checks `>= 0` no banco, widgets da UI. `construir_features` predizia
sem erro sobre idade -5 ou 150, sexo 'X' (virava NaN), distância 450 km e data
nula -- e o job diário é justamente o caminho que não passa pela API nem pela
UI. Aqui mora a regra única, aplicada no job, no export do re-treino, no
`preprocess` e no stage `validate_data` do `dvc.yaml`.

**Duas faixas por campo, porque há dois tipos de "fora":**

- **Regra de negócio** -- fora dela, a linha é **recusada**
  (`ViolacaoDoContrato`): quarentena no job, fora do export, bloqueio no
  `validate_data`. O dado é impossível (idade 150, sexo 'X', consulta às 3h de
  domingo), e predizer sobre ele seria inventar.
- **Domínio do treino** -- fora dela, a linha é **predita e marcada**
  (`fora_do_dominio`). O dado é plausível, só não foi visto no treino
  (distância 60 km, 120 dias de antecedência). Decisão da autora: marcar,
  nunca rejeitar.

As faixas do domínio são o que o modelo vigente de fato viu (`feature_infos`
do booster: idade 0-85, distância até 50, antecedência 1-90, histórico até 10,
seg-sáb 8h-18h). A suíte de sanidade (`src/sanidade_modelo.py`) avisa se um
modelo re-treinado passar a ver outra coisa.

**Coerção explícita, não implícita.** `"45"` e `45.0` viram `45`; `45.5` é
recusado para um campo inteiro, em vez de truncado em silêncio. NaN/None são
recusados -- nunca viram 0.

**A mensagem de erro não carrega o valor.** `ViolacaoDoContrato` diz o campo e
a regra, nunca o dado: ela vai para o resultado do job, para o log e para a
aba de dev, e um valor cru de `data_nascimento` ou `telefone` ali seria PII
fora do lugar (Passo 8).
"""
import math
import numbers
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import agenda_clinica
from config_projeto import para_horario_da_clinica

CAMPO_SEXO = "sexo"
CAMPO_ESPECIALIDADE = "especialidade"
CAMPO_DATA_HORA = "data_hora_agendada"
SEXOS_VALIDOS = ("F", "M")

# Domínio temporal do treino: seg (0) a sáb (5), 8h a 18h. A grade da clínica
# (agenda_clinica) é mais estreita que isso -- sem almoço, sábado só de manhã
# --, então quem passa na regra de negócio sempre cai aqui. A checagem existe
# para o dia em que a grade mudar sem o modelo ser re-treinado.
DIAS_DA_SEMANA_DO_DOMINIO = range(0, 6)
HORAS_DO_DOMINIO = range(8, 19)


class ViolacaoDoContrato(ValueError):
    """Dado cru fora da regra de negócio. Carrega o campo e a regra, nunca o
    valor (ver docstring do módulo)."""

    def __init__(self, campo: str, regra: str):
        self.campo = campo
        self.regra = regra
        super().__init__(f"{campo}: {regra}")


@dataclass(frozen=True)
class Faixa:
    minimo: float | None = None
    maximo: float | None = None

    def contem(self, valor: float) -> bool:
        if self.minimo is not None and valor < self.minimo:
            return False
        return self.maximo is None or valor <= self.maximo

    def descrever(self) -> str:
        if self.maximo is None:
            return f">= {self.minimo:g}"
        if self.minimo is None:
            return f"<= {self.maximo:g}"
        return f"entre {self.minimo:g} e {self.maximo:g}"


@dataclass(frozen=True)
class CampoNumerico:
    nome: str
    inteiro: bool
    negocio: Faixa
    dominio: Faixa


CAMPOS_NUMERICOS = (
    CampoNumerico("idade", inteiro=True, negocio=Faixa(0, 120), dominio=Faixa(0, 85)),
    CampoNumerico("distancia_km", inteiro=False, negocio=Faixa(0, None), dominio=Faixa(0, 50)),
    CampoNumerico(
        "dias_entre_agendamento_consulta",
        inteiro=True,
        negocio=Faixa(0, agenda_clinica.PRAZO_MAXIMO_AGENDAMENTO_DIAS),
        dominio=Faixa(1, 90),
    ),
    CampoNumerico("historico_noshow", inteiro=True, negocio=Faixa(0, None), dominio=Faixa(0, 10)),
)

CAMPOS_DO_PAYLOAD = (
    *(c.nome for c in CAMPOS_NUMERICOS),
    CAMPO_SEXO,
    CAMPO_ESPECIALIDADE,
    CAMPO_DATA_HORA,
)


@dataclass(frozen=True)
class LinhaValidada:
    """Payload já coagido (tipos corretos, data no fuso da clínica) e os campos
    que ficaram fora do domínio do treino."""

    valores: dict[str, Any]
    campos_fora_do_dominio: tuple[str, ...]

    @property
    def fora_do_dominio(self) -> bool:
        return bool(self.campos_fora_do_dominio)


def coagir_numero(campo: str, valor: object, inteiro: bool) -> int | float:
    """Número a partir do que o banco, o CSV ou um formulário entregam.

    Aceita número (inclusive os escalares do numpy/pandas) e string numérica.
    Recusa `None`, NaN, infinito, booleano e -- para campo inteiro -- valor
    fracionário: truncar 45.5 para 45 esconderia um dado que chegou errado."""
    if valor is None or isinstance(valor, bool):
        raise ViolacaoDoContrato(campo, "ausente ou de tipo inválido")
    if isinstance(valor, str):
        try:
            numero = float(valor.strip())
        except ValueError:
            raise ViolacaoDoContrato(campo, "não é numérico") from None
    elif isinstance(valor, numbers.Real):
        numero = float(valor)
    else:
        raise ViolacaoDoContrato(campo, "ausente ou de tipo inválido")

    if math.isnan(numero) or math.isinf(numero):
        raise ViolacaoDoContrato(campo, "ausente (NaN)")
    if not inteiro:
        return numero
    if not numero.is_integer():
        raise ViolacaoDoContrato(campo, "deve ser inteiro")
    return int(numero)


def coagir_data_hora(valor: object) -> datetime:
    """Data/hora da consulta no fuso da clínica (`para_horario_da_clinica`:
    UTC do banco é convertido, valor sem fuso é tratado como já local)."""
    if not isinstance(valor, str | datetime):
        raise ViolacaoDoContrato(CAMPO_DATA_HORA, "ausente ou de tipo inválido")
    try:
        return para_horario_da_clinica(valor)
    except ValueError:
        raise ViolacaoDoContrato(CAMPO_DATA_HORA, "não é uma data/hora válida") from None


def validar_payload(
    payload: Mapping[str, Any], especialidades_validas: Collection[str]
) -> LinhaValidada:
    """Aplica o contrato a um payload cru (formato de `construir_features`).

    Levanta `ViolacaoDoContrato` no primeiro campo fora da regra de negócio.
    `especialidades_validas` é o mapa do modelo no job (o que ele sabe
    codificar) e a lista do cadastro no `validate_data` (o que a clínica pode
    produzir)."""
    valores: dict[str, Any] = {}
    fora: list[str] = []

    for campo in CAMPOS_NUMERICOS:
        numero = coagir_numero(campo.nome, payload.get(campo.nome), campo.inteiro)
        if not campo.negocio.contem(numero):
            raise ViolacaoDoContrato(campo.nome, f"deve estar {campo.negocio.descrever()}")
        if not campo.dominio.contem(numero):
            fora.append(campo.nome)
        valores[campo.nome] = numero

    sexo = payload.get(CAMPO_SEXO)
    sexo = sexo.strip().upper() if isinstance(sexo, str) else sexo
    if sexo not in SEXOS_VALIDOS:
        raise ViolacaoDoContrato(CAMPO_SEXO, f"deve ser um de {SEXOS_VALIDOS}")
    valores[CAMPO_SEXO] = sexo

    especialidade = payload.get(CAMPO_ESPECIALIDADE)
    especialidade = especialidade.strip() if isinstance(especialidade, str) else especialidade
    if especialidade not in especialidades_validas:
        raise ViolacaoDoContrato(CAMPO_ESPECIALIDADE, "fora da lista de especialidades válidas")
    valores[CAMPO_ESPECIALIDADE] = especialidade

    quando = coagir_data_hora(payload.get(CAMPO_DATA_HORA))
    if not agenda_clinica.horario_valido(quando.date(), quando.time()):
        raise ViolacaoDoContrato(CAMPO_DATA_HORA, "fora da grade de atendimento da clínica")
    if (
        quando.weekday() not in DIAS_DA_SEMANA_DO_DOMINIO
        or quando.hour not in HORAS_DO_DOMINIO
    ):
        fora.append(CAMPO_DATA_HORA)
    valores[CAMPO_DATA_HORA] = quando

    return LinhaValidada(valores=valores, campos_fora_do_dominio=tuple(fora))


def violacao_ou_none(
    payload: Mapping[str, Any], especialidades_validas: Collection[str]
) -> ViolacaoDoContrato | None:
    """Versão sem exceção, para quem valida um dataset inteiro e precisa
    contar violações em vez de parar na primeira linha."""
    try:
        validar_payload(payload, especialidades_validas)
    except ViolacaoDoContrato as exc:
        return exc
    return None


# --- dataset inteiro (export, preprocess, validate_data) -------------------------


def violacoes_do_dataframe(
    df: Any, especialidades_validas: Collection[str], coluna_id: str = "id_consulta"
) -> list[tuple[Any, ViolacaoDoContrato]]:
    """Uma entrada por linha que viola a regra de negócio: (identificador da
    linha, violação). Não para na primeira -- o `validate_data` precisa contar
    e resumir, e o export precisa saber exatamente quais linhas deixar de fora.

    O identificador é `id_consulta` (inteiro na semente, uuid do agendamento na
    produção): não é PII e é o que permite achar a linha no banco."""
    colunas = [c for c in CAMPOS_DO_PAYLOAD if c in df.columns]
    ids = df[coluna_id] if coluna_id in df.columns else df.index
    violacoes = []
    for identificador, linha in zip(ids, df[colunas].to_dict("records"), strict=True):
        violacao = violacao_ou_none(linha, especialidades_validas)
        if violacao is not None:
            violacoes.append((identificador, violacao))
    return violacoes


def coagir_colunas_numericas(df: Any) -> Any:
    """Coerção explícita de tipo por coluna, para o `preprocess`: cada campo
    numérico do contrato termina em `int64` (inteiros) ou `float64`.

    Coluna que **já** está no dtype certo não é tocada, e se nenhuma precisar
    de conversão o próprio DataFrame recebido é devolvido. Não é só economia:
    o `train_raw.pkl`/`test.pkl` que o DVC versiona são pickles, e reescrever
    uma coluna idêntica pode mudar o layout interno do DataFrame -- e com ele o
    md5 do artefato, o que dispararia um re-treino sem mudança nenhuma no dado."""
    import pandas as pd

    convertido = None
    for campo in CAMPOS_NUMERICOS:
        if campo.nome not in df.columns:
            continue
        alvo = "int64" if campo.inteiro else "float64"
        if str(df[campo.nome].dtype) == alvo:
            continue
        valores = [coagir_numero(campo.nome, v, campo.inteiro) for v in df[campo.nome]]
        if convertido is None:
            convertido = df.copy()
        convertido[campo.nome] = pd.Series(valores, index=df.index, dtype=alvo)
    return df if convertido is None else convertido


def resumir_violacoes(violacoes: list[tuple[Any, ViolacaoDoContrato]], limite: int = 5) -> str:
    """Texto curto para log/erro: quantas linhas, e as primeiras com o campo e
    a regra (sem o valor -- ver `ViolacaoDoContrato`)."""
    amostra = "; ".join(f"id {ident}: {violacao}" for ident, violacao in violacoes[:limite])
    resto = f" (+{len(violacoes) - limite})" if len(violacoes) > limite else ""
    return f"{len(violacoes)} linha(s) fora da regra de negócio -- {amostra}{resto}"
