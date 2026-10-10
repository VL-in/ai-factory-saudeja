"""
SaúdeJá — regras do consentimento (ADR-009), sem banco.

O consentimento é registrado em `eventos_consentimento`, só de inserção
(migration `20261010000000_consentimento_e_pedidos.sql`): cada concessão ou
revogação é um evento, e o consentimento vigente é o último evento de cada
finalidade. Este módulo calcula o vigente a partir dos eventos já lidos, para
que a mesma regra sirva à consulta de um paciente (`repositories.
eventos_consentimento_do_paciente`) e ao lote do job D-2, que traz os eventos
embutidos no select da fila.

Ausência de evento é ausência de consentimento: o consentimento é manifestação
afirmativa do titular (LGPD, art. 5º, XII), e o ônus de prová-lo é do
controlador (art. 8º, § 2º).

O consentimento **não é feature do modelo**: entra como filtro do lembrete e
do export de treino, nunca como coluna do dataset.
"""
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

FINALIDADE_LEMBRETE = "lembrete"
FINALIDADE_TREINO = "treino"
FINALIDADE_TRANSFERENCIA = "transferencia_internacional"
FINALIDADES = (FINALIDADE_LEMBRETE, FINALIDADE_TREINO, FINALIDADE_TRANSFERENCIA)

ACAO_CONCEDIDO = "concedido"
ACAO_REVOGADO = "revogado"

CANAIS = ("autoagendamento", "clinica", "pedido_titular")
CONSENTIDO_POR = ("titular", "responsavel_legal")

# Versão do texto dos termos exibidos na tela de agendamento. Muda junto com o
# texto: um consentimento dado sob uma versão anterior não cobre uma finalidade
# reescrita (política de privacidade, seção 14).
VERSAO_TERMO = "2026-10-10"

# Prazo em que a prova sobrevive à eliminação do paciente (ADR-009). O mesmo
# valor está fixo no trigger da migration, que recusa apagar antes dele.
RETENCAO_PROVA_APOS_ELIMINACAO_DIAS = 365

Evento = Mapping[str, Any]


def consentimentos_vigentes(eventos: Iterable[Evento]) -> dict[str, Evento | None]:
    """Para cada finalidade de `FINALIDADES`, o evento de concessão vigente, ou
    `None` quando não há consentimento.

    Cada evento traz pelo menos `finalidade`, `acao` (`concedido`/`revogado`),
    `versao_termo` e `registrado_em` (texto ISO 8601 do `timestamptz`, como o
    PostgREST devolve). Vigente é o **último** evento da finalidade: se for
    `concedido`, é ele que volta (a tela de agendamento usa a `versao_termo`
    dele para decidir se pede o consentimento de novo); se for `revogado`, ou
    se não houver evento, volta `None`.
    """
    ultimos: dict[str, Evento] = {}
    for evento in eventos:
        finalidade = evento["finalidade"]
        if finalidade not in FINALIDADES:
            raise ValueError(f"finalidade desconhecida: {finalidade!r}")
        atual = ultimos.get(finalidade)
        if atual is None or _ordem(evento) > _ordem(atual):
            ultimos[finalidade] = evento
    vigentes: dict[str, Evento | None] = {}
    for finalidade in FINALIDADES:
        ultimo = ultimos.get(finalidade)
        concedido = ultimo is not None and ultimo["acao"] == ACAO_CONCEDIDO
        vigentes[finalidade] = ultimo if concedido else None
    return vigentes


def _ordem(evento: Evento) -> tuple[datetime, bool]:
    """Chave de "mais recente": o instante e, no empate, a revogação depois da
    concessão. Empatar é improvável (dois cliques no mesmo microssegundo), mas,
    se acontecer, ficar sem consentimento é o lado seguro: o ônus de provar o
    consentimento é do controlador (art. 8º, § 2º)."""
    return _instante(evento["registrado_em"]), evento["acao"] == ACAO_REVOGADO


def _instante(registrado_em: str) -> datetime:
    """Instante do `timestamptz`, comparável entre offsets diferentes.

    Mesma normalização de `repositories.ler_timestamptz` (o PostgREST corta os
    zeros finais da fração, e o `fromisoformat` do Python 3.10 só aceita 3 ou 6
    dígitos), repetida aqui de propósito: importar `db.repositories` puxaria o
    cliente do Supabase para um módulo que o resto do código usa sem banco."""
    return datetime.fromisoformat(
        re.sub(r"\.(\d{1,6})(?=[+-]|$)", lambda m: "." + m.group(1).ljust(6, "0"), registrado_em)
    )


def tem_consentimento(eventos: Iterable[Evento], finalidade: str) -> bool:
    """Atalho para os filtros do job D-2 e do export."""
    if finalidade not in FINALIDADES:
        raise ValueError(f"finalidade desconhecida: {finalidade!r}")
    return consentimentos_vigentes(eventos)[finalidade] is not None
