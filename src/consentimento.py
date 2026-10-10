"""
SaúdeJá — regras do consentimento (ADR-009), sem banco.

O consentimento é registrado em `eventos_consentimento`, só de inserção
(migration `20261010000000_consentimento_e_pedidos.sql`): cada concessão ou
revogação é um evento, e o consentimento vigente é o último evento de cada
finalidade. Este módulo calcula o vigente a partir dos eventos já lidos, para
que a mesma regra sirva à consulta de um paciente (`repositories.
eventos_consentimento_do_paciente`) e ao lote do export de treino.

Duas finalidades dependem de consentimento: o treino com desfechos reais
(art. 11, I) e a transferência internacional (art. 33, VIII). O lembrete por
SMS **não**: só leva telefone, data e hora, e se apoia na execução do contrato
do paciente com a clínica (art. 7º, V; LGPD.md, seção 3), sem caixa na tela e
sem filtro no job D-2.

Ausência de evento é ausência de consentimento: o consentimento é manifestação
afirmativa do titular (LGPD, art. 5º, XII), e o ônus de prová-lo é do
controlador (art. 8º, § 2º).

O consentimento **não é feature do modelo**: entra como filtro do export de
treino, nunca como coluna do dataset.
"""
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from features import calcular_idade

FINALIDADE_TREINO = "treino"
FINALIDADE_TRANSFERENCIA = "transferencia_internacional"
FINALIDADES = (FINALIDADE_TREINO, FINALIDADE_TRANSFERENCIA)

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
# jurídica (Passo 8.6, pendências).
IDADE_MINIMA_PARA_CONSENTIR = 12

# Versão do texto dos termos exibidos na tela de agendamento (abaixo). Muda
# junto com o texto: um consentimento dado sob uma versão anterior não cobre
# uma finalidade reescrita (política de privacidade, seção 14), e a tela pede
# o consentimento de novo. `tests/test_consentimento.py` falha se um texto
# mudar sem a versão mudar junto.
VERSAO_TERMO = "2026-10-10"

# --- Textos dos termos, versão VERSAO_TERMO -------------------------------------
# São a prova do que o paciente leu: o evento guarda só a versão, e é esta
# versão que diz qual texto estava na tela. Linguagem simples, sem jargão
# técnico (revisão de UX, P1), e em nome do paciente ou do responsável legal.

# Informação prévia da transferência (LGPD.md, subseção 4.2, alínea b): países,
# provedores, dados e finalidade de cada um, e a ausência das cláusulas-padrão.
INFORMACAO_TRANSFERENCIA = (
    "**Os dados deste cadastro são processados fora do Brasil.** Eles ficam "
    "guardados no Brasil, mas dois fornecedores os processam em servidores "
    "fora do país, em geral nos Estados Unidos:\n\n"
    "- **Hugging Face**: hospeda esta tela e a tela da equipe da clínica. Recebe "
    "tudo o que for digitado aqui (nome, CPF antes de virar código, telefone, "
    "data de nascimento, sexo e dados da consulta) para fazer o agendamento.\n"
    "- **GitHub**: todo dia, calcula quais consultas a clínica deve confirmar "
    "com antecedência e envia o lembrete. Recebe data de nascimento, sexo, "
    "dados da consulta, histórico de comparecimento e telefone. Se o "
    "aprimoramento do sistema for autorizado, recebe também o resultado das "
    "consultas e o código gerado a partir do CPF. Nunca recebe nome nem CPF.\n\n"
    "Os dois não guardam os dados depois do processamento. Eles não adotaram "
    "as cláusulas-padrão aprovadas pela Autoridade Nacional de Proteção de "
    "Dados (ANPD), por isso o processamento fora do Brasil só acontece com "
    "autorização (LGPD, art. 33, VIII).\n\n"
    "Sem essa autorização, não é possível agendar por esta tela: a clínica faz "
    "o agendamento por telefone ou no balcão, e o atendimento não é afetado. "
    "Quem autorizar e mudar de ideia depois pede à clínica, e o cadastro neste "
    "sistema é eliminado."
)
TERMO_TRANSFERENCIA = (
    "Eu, paciente ou responsável legal, autorizo o processamento dos dados deste "
    "cadastro fora do Brasil, pelo Hugging Face e pelo GitHub, como descrito acima."
)
TERMO_TREINO = (
    "Eu, paciente ou responsável legal, autorizo o uso do resultado das consultas "
    "do paciente (se compareceu ou faltou) e dos dados da consulta, sem nome, "
    "telefone nem CPF, para aprimorar o sistema que ajuda clínicas a confirmar "
    "consultas. Esse aprimoramento é feito pelo SaúdeJá e beneficia também "
    "outras clínicas que usam o sistema."
)
TEXTOS_DO_TERMO = (INFORMACAO_TRANSFERENCIA, TERMO_TRANSFERENCIA, TERMO_TREINO)

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
