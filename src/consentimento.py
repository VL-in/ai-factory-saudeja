"""
SaúdeJá — regras do consentimento do treino (ADR-009, ADR-010), sem banco.

Só uma finalidade depende de consentimento: o uso dos desfechos reais para
aprimorar o modelo (art. 11, I), de que a SaúdeJá é controladora. O
agendamento, a predição e o lembrete fazem parte do serviço da clínica (art.
7º, V; art. 11, II, f) e não dependem de consentimento nenhum: recusar o treino
não impede o agendamento.

O consentimento é registrado em `treino.eventos_consentimento`, só de inserção
(migration `20261011000000_schema_treino.sql`), chaveado pelo pseudônimo do
titular (`src/pseudonimo.py`): cada concessão ou revogação é um evento, e o
consentimento vigente é o último evento. Este módulo calcula o vigente a partir
dos eventos já lidos (`db.repositorio_treino.eventos_do_titular`).

Ausência de evento é ausência de consentimento: o consentimento é manifestação
afirmativa do titular (LGPD, art. 5º, XII), e o ônus de prová-lo é do
controlador (art. 8º, § 2º).

O consentimento **não é feature do modelo**: decide se a consulta entra na base
de treino, nunca vira coluna do dataset.

A caixa de **ciência** da Política de privacidade e dos Termos de uso, antes do
formulário, não é consentimento: é informação (art. 9º). Ela fica em
`documentos_legais`.
"""
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from config_projeto import ler_timestamptz
from features import calcular_idade

FINALIDADE_TREINO = "treino"
FINALIDADES = (FINALIDADE_TREINO,)

ACAO_CONCEDIDO = "concedido"
ACAO_REVOGADO = "revogado"

CANAL_AUTOAGENDAMENTO = "autoagendamento"
CANAIS = (CANAL_AUTOAGENDAMENTO, "clinica", "pedido_titular")
CONSENTIDO_POR_TITULAR = "titular"
CONSENTIDO_POR_RESPONSAVEL = "responsavel_legal"
CONSENTIDO_POR = (CONSENTIDO_POR_TITULAR, CONSENTIDO_POR_RESPONSAVEL)

# Criança, para o ECA (art. 2º), é quem tem até 12 anos incompletos, e o
# art. 14, § 1º, da LGPD exige o consentimento de um dos pais ou do responsável
# só para ela. Adolescente consente como titular. A regra precisa de validação
# jurídica (LGPD.md, subseção 9.2, item 17).
IDADE_MINIMA_PARA_CONSENTIR = 12

# Versão do texto dos termos exibidos na tela de agendamento (abaixo). Muda
# junto com o texto: um consentimento dado sob uma versão anterior não cobre
# uma finalidade reescrita (política de privacidade, seção 14), e a tela pede
# o consentimento de novo. `tests/test_consentimento.py` falha se um texto
# mudar sem a versão mudar junto.
VERSAO_TERMO = "2026-10-10"

# --- Texto do termo, versão VERSAO_TERMO ---------------------------------------
# É a prova do que o paciente leu: o evento guarda só a versão, e é esta versão
# que diz qual texto estava na tela. Linguagem simples, sem jargão técnico
# (revisão de UX, P1). Curto, a pedido da autora (2026-10-10): o detalhe --
# base separada, provedor, país, efeito da caixa desmarcada, agendamento de
# criança -- está nos Termos de uso, seções 2 e 5, e na Política.
#
# Uma caixa só para o treino, com o processamento fora do Brasil em destaque no
# próprio termo (decisão D3 do ADR-010): o re-treino roda no GitHub, fora do
# país, e só recebe dados de quem marcou esta caixa. Precisa de validação
# jurídica quanto ao "distinguindo claramente" do art. 33, VIII.
TERMO_TREINO = (
    "Autorizo o SaúdeJá a usar os dados das consultas do paciente, incluindo se "
    "ele compareceu ou faltou, para melhorar o sistema de lembretes. Nome, "
    "telefone e CPF não são usados. **Esse uso é feito fora do Brasil.** Posso "
    "cancelar a autorização quando quiser, e recusar não impede o agendamento."
)
TEXTOS_DO_TERMO = (TERMO_TREINO,)

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
    consentimento é do controlador (art. 8º, § 2º).

    O instante vem com o offset que o PostgREST devolveu, então dois eventos
    com offsets diferentes continuam comparáveis."""
    return ler_timestamptz(evento["registrado_em"]), evento["acao"] == ACAO_REVOGADO


def tem_consentimento(eventos: Iterable[Evento], finalidade: str) -> bool:
    """Atalho para a etapa de treino do agendamento e para os testes."""
    if finalidade not in FINALIDADES:
        raise ValueError(f"finalidade desconhecida: {finalidade!r}")
    return consentimentos_vigentes(eventos)[finalidade] is not None


def consentido_por(data_nascimento: date, hoje: date) -> str:
    """Quem dá o consentimento, pela idade do paciente no dia em que ele é
    dado (não no dia da consulta): criança (menos de 12 anos) consente pelos
    pais ou pelo responsável legal (art. 14, § 1º)."""
    if calcular_idade(data_nascimento, hoje) < IDADE_MINIMA_PARA_CONSENTIR:
        return CONSENTIDO_POR_RESPONSAVEL
    return CONSENTIDO_POR_TITULAR


def decisoes_de_consentimento(
    vigentes: Mapping[str, Evento | None],
    marcadas: Mapping[str, bool],
    versao_termo: str = VERSAO_TERMO,
) -> list[tuple[str, str]]:
    """Eventos a gravar num agendamento, como pares (finalidade, ação), na
    ordem de `FINALIDADES`. Só grava quando o estado muda: a tabela é a prova,
    e um evento repetido a cada agendamento não prova nada a mais.

    `vigentes` é a saída de `consentimentos_vigentes` para o paciente (antes
    deste agendamento). `marcadas` é o estado de cada caixa na tela; finalidade
    ausente conta como caixa desmarcada.
    """
    desconhecidas = set(marcadas) - set(FINALIDADES)
    if desconhecidas:
        # Uma chave errada ("treinos") viraria caixa desmarcada e revogaria
        # em silêncio o consentimento vigente.
        raise ValueError(f"finalidade desconhecida: {sorted(desconhecidas)!r}")

    decisoes: list[tuple[str, str]] = []
    for finalidade in FINALIDADES:
        vigente = vigentes.get(finalidade)
        if marcadas.get(finalidade, False):
            if vigente is None or vigente["versao_termo"] != versao_termo:
                decisoes.append((finalidade, ACAO_CONCEDIDO))
        elif vigente is not None:
            decisoes.append((finalidade, ACAO_REVOGADO))
    return decisoes
