"""
SaúdeJá — regra do consentimento vigente (src/consentimento.py, ADR-009).

Sem banco: os eventos chegam como o PostgREST os devolve, com `registrado_em`
em texto ISO 8601 e a fração de segundo sem os zeros finais.
"""
import hashlib
import itertools
from datetime import date

import pytest

from consentimento import (
    ACAO_CONCEDIDO,
    ACAO_REVOGADO,
    CONSENTIDO_POR_RESPONSAVEL,
    CONSENTIDO_POR_TITULAR,
    FINALIDADE_LEMBRETE,
    FINALIDADE_TRANSFERENCIA,
    FINALIDADE_TREINO,
    FINALIDADES,
    TEXTOS_DO_TERMO,
    VERSAO_TERMO,
    consentido_por,
    consentimentos_vigentes,
    decisoes_de_consentimento,
    tem_consentimento,
)


def _evento(finalidade, acao, registrado_em, versao="2026-10-10"):
    return {
        "finalidade": finalidade,
        "acao": acao,
        "versao_termo": versao,
        "registrado_em": registrado_em,
    }


def test_sem_evento_nao_ha_consentimento_em_nenhuma_finalidade():
    """Ausência de evento é ausência de consentimento (art. 8º, § 2º): o
    paciente cadastrado antes da coleta não recebe lembrete nem entra no treino."""
    vigentes = consentimentos_vigentes([])

    assert set(vigentes) == set(FINALIDADES)
    assert all(v is None for v in vigentes.values())


def test_concessao_vigente_devolve_o_proprio_evento():
    concessao = _evento(FINALIDADE_LEMBRETE, ACAO_CONCEDIDO, "2026-10-10T12:00:00+00:00")

    vigentes = consentimentos_vigentes([concessao])

    assert vigentes[FINALIDADE_LEMBRETE] == concessao
    assert vigentes[FINALIDADE_TREINO] is None


def test_revogacao_depois_da_concessao_anula():
    eventos = [
        _evento(FINALIDADE_TREINO, ACAO_CONCEDIDO, "2026-10-10T12:00:00+00:00"),
        _evento(FINALIDADE_TREINO, ACAO_REVOGADO, "2026-10-11T09:30:00+00:00"),
    ]

    assert consentimentos_vigentes(eventos)[FINALIDADE_TREINO] is None
    assert not tem_consentimento(eventos, FINALIDADE_TREINO)


def test_nova_concessao_depois_da_revogacao_volta_a_valer_com_a_versao_nova():
    eventos = [
        _evento(FINALIDADE_LEMBRETE, ACAO_CONCEDIDO, "2026-10-10T12:00:00+00:00", "v1"),
        _evento(FINALIDADE_LEMBRETE, ACAO_REVOGADO, "2026-10-11T12:00:00+00:00", "v1"),
        _evento(FINALIDADE_LEMBRETE, ACAO_CONCEDIDO, "2026-10-12T12:00:00+00:00", "v2"),
    ]

    vigente = consentimentos_vigentes(eventos)[FINALIDADE_LEMBRETE]

    assert vigente is not None
    assert vigente["versao_termo"] == "v2"


def test_finalidades_sao_independentes():
    """Revogar o treino não tira o lembrete, e vice-versa (LGPD.md §3)."""
    eventos = [
        _evento(FINALIDADE_LEMBRETE, ACAO_CONCEDIDO, "2026-10-10T12:00:00+00:00"),
        _evento(FINALIDADE_TREINO, ACAO_CONCEDIDO, "2026-10-10T12:00:00+00:00"),
        _evento(FINALIDADE_TRANSFERENCIA, ACAO_CONCEDIDO, "2026-10-10T12:00:00+00:00"),
        _evento(FINALIDADE_TREINO, ACAO_REVOGADO, "2026-10-11T12:00:00+00:00"),
    ]

    assert tem_consentimento(eventos, FINALIDADE_LEMBRETE)
    assert tem_consentimento(eventos, FINALIDADE_TRANSFERENCIA)
    assert not tem_consentimento(eventos, FINALIDADE_TREINO)


def test_o_vigente_nao_depende_da_ordem_em_que_os_eventos_chegam():
    """O select embutido do job D-2 não garante ordem entre os eventos de um
    paciente. Em qualquer ordem, o resultado tem de ser o mesmo."""
    eventos = [
        _evento(FINALIDADE_LEMBRETE, ACAO_CONCEDIDO, "2026-10-10T12:00:00+00:00"),
        _evento(FINALIDADE_LEMBRETE, ACAO_REVOGADO, "2026-10-11T12:00:00+00:00"),
        _evento(FINALIDADE_TREINO, ACAO_REVOGADO, "2026-10-10T12:00:00+00:00"),
        _evento(FINALIDADE_TREINO, ACAO_CONCEDIDO, "2026-10-12T08:00:00+00:00"),
    ]
    for ordem in itertools.permutations(eventos):
        vigentes = consentimentos_vigentes(ordem)
        assert vigentes[FINALIDADE_LEMBRETE] is None
        assert vigentes[FINALIDADE_TREINO] is not None


def test_offsets_diferentes_sao_comparados_pelo_instante():
    """09h30 em São Paulo (-03:00) é 12h30 UTC: depois das 12h00 UTC. Comparar
    o texto do `timestamptz` erraria aqui ("09:30" < "12:00"); o PostgREST
    devolve em UTC, mas os eventos montados em código podem vir com o fuso da
    clínica."""
    eventos = [
        _evento(FINALIDADE_TREINO, ACAO_REVOGADO, "2026-10-10T09:30:00-03:00"),
        _evento(FINALIDADE_TREINO, ACAO_CONCEDIDO, "2026-10-10T12:00:00+00:00"),
    ]

    assert consentimentos_vigentes(eventos)[FINALIDADE_TREINO] is None


def test_finalidade_desconhecida_e_erro_e_nao_falso():
    """Um erro de digitação na finalidade não pode virar "sem consentimento"
    em silêncio -- pararia os lembretes de todo mundo sem nenhum alarme."""
    with pytest.raises(ValueError):
        tem_consentimento([], "lembretes")


def test_no_empate_de_instante_a_revogacao_vence_em_qualquer_ordem():
    """Empate é improvável, mas sem consentimento é o lado seguro: o ônus de
    provar o consentimento é do controlador (art. 8º, § 2º)."""
    eventos = [
        _evento(FINALIDADE_LEMBRETE, ACAO_CONCEDIDO, "2026-10-10T12:00:00.12345+00:00"),
        _evento(FINALIDADE_LEMBRETE, ACAO_REVOGADO, "2026-10-10T09:00:00.12345-03:00"),
    ]

    for ordem in itertools.permutations(eventos):
        assert consentimentos_vigentes(ordem)[FINALIDADE_LEMBRETE] is None


def test_fracao_de_segundo_cortada_pelo_postgrest_e_aceita():
    """O PostgREST devolve "12:00:00.01422" (5 dígitos), que o `fromisoformat`
    do Python 3.10, o do CI, recusa sem normalização."""
    concessao = _evento(FINALIDADE_TREINO, ACAO_CONCEDIDO, "2026-10-04T19:46:05.01422+00:00")

    assert consentimentos_vigentes([concessao])[FINALIDADE_TREINO] == concessao


# --- coleta na tela de agendamento (Passo 8.6, peça 3) --------------------------

_TODAS_MARCADAS = dict.fromkeys(FINALIDADES, True)
_NENHUM_VIGENTE = dict.fromkeys(FINALIDADES)


def _vigentes(**por_finalidade):
    return {**_NENHUM_VIGENTE, **por_finalidade}


def test_paciente_novo_que_marca_tudo_concede_tudo_na_ordem_das_finalidades():
    assert decisoes_de_consentimento(_NENHUM_VIGENTE, _TODAS_MARCADAS) == [
        (f, ACAO_CONCEDIDO) for f in FINALIDADES
    ]


def test_caixa_desmarcada_sem_consentimento_vigente_nao_grava_nada():
    """Recusa de quem nunca consentiu não é evento: ausência de evento já é
    ausência de consentimento."""
    marcadas = {FINALIDADE_TRANSFERENCIA: True, FINALIDADE_LEMBRETE: False}

    assert decisoes_de_consentimento(_NENHUM_VIGENTE, marcadas) == [
        (FINALIDADE_TRANSFERENCIA, ACAO_CONCEDIDO)
    ]


def test_consentimento_vigente_na_versao_atual_e_caixa_marcada_nao_repete_o_evento():
    concessao = _evento(FINALIDADE_LEMBRETE, ACAO_CONCEDIDO, "2026-10-10T12:00:00+00:00")
    vigentes = _vigentes(**{FINALIDADE_LEMBRETE: concessao})

    assert decisoes_de_consentimento(vigentes, {FINALIDADE_LEMBRETE: True}) == []


def test_consentimento_de_versao_anterior_e_caixa_marcada_concede_de_novo():
    """Texto reescrito não é coberto pelo consentimento antigo (política de
    privacidade, seção 14)."""
    antiga = _evento(FINALIDADE_TREINO, ACAO_CONCEDIDO, "2026-10-01T12:00:00+00:00", "v0")
    vigentes = _vigentes(**{FINALIDADE_TREINO: antiga})

    assert decisoes_de_consentimento(vigentes, {FINALIDADE_TREINO: True}) == [
        (FINALIDADE_TREINO, ACAO_CONCEDIDO)
    ]


@pytest.mark.parametrize("versao", [VERSAO_TERMO, "v0"], ids=["versao-atual", "versao-anterior"])
def test_caixa_desmarcada_com_consentimento_vigente_revoga(versao):
    concessao = _evento(FINALIDADE_LEMBRETE, ACAO_CONCEDIDO, "2026-10-01T12:00:00+00:00", versao)
    vigentes = _vigentes(**{FINALIDADE_LEMBRETE: concessao})

    assert decisoes_de_consentimento(vigentes, {FINALIDADE_LEMBRETE: False}) == [
        (FINALIDADE_LEMBRETE, ACAO_REVOGADO)
    ]


def test_finalidade_ausente_das_caixas_conta_como_desmarcada():
    concessao = _evento(FINALIDADE_TREINO, ACAO_CONCEDIDO, "2026-10-10T12:00:00+00:00")
    vigentes = _vigentes(**{FINALIDADE_TREINO: concessao})

    assert decisoes_de_consentimento(vigentes, {}) == [(FINALIDADE_TREINO, ACAO_REVOGADO)]


def test_caixa_de_finalidade_desconhecida_e_erro():
    """Mesma regra de `tem_consentimento`: uma chave errada ("lembretes") não
    pode virar "caixa desmarcada" e revogar em silêncio."""
    with pytest.raises(ValueError):
        decisoes_de_consentimento(_NENHUM_VIGENTE, {"lembretes": True})


@pytest.mark.parametrize(
    ("data_nascimento", "esperado"),
    [
        (date(2014, 10, 11), CONSENTIDO_POR_RESPONSAVEL),  # faz 12 amanhã
        (date(2014, 10, 10), CONSENTIDO_POR_TITULAR),  # faz 12 hoje
        (date(2026, 10, 1), CONSENTIDO_POR_RESPONSAVEL),  # recém-nascido
        (date(1990, 1, 1), CONSENTIDO_POR_TITULAR),
    ],
)
def test_consentido_por_segue_a_idade_no_dia_do_consentimento(data_nascimento, esperado):
    """Criança (até 12 anos incompletos, ECA art. 2º) consente pelo responsável
    (LGPD art. 14, § 1º)."""
    assert consentido_por(data_nascimento, hoje=date(2026, 10, 10)) == esperado


# sha256 dos textos de cada versão do termo. Mudou um texto? Suba VERSAO_TERMO e
# acrescente a versão nova aqui -- nunca troque o hash de uma versão existente:
# eventos já gravados com ela apontariam para um texto que o paciente não leu.
_HASH_DOS_TEXTOS_POR_VERSAO = {
    "2026-10-10": "5269d3d99bac7c9fea8ea9e0b97c8f1f758e7479bb7b851d46ac46af12bcc5f5",
}


def test_texto_do_termo_nao_muda_sem_mudar_a_versao():
    """O evento guarda só `versao_termo`: a versão é o que diz qual texto o
    paciente leu, então texto novo exige versão nova."""
    atual = hashlib.sha256("\x00".join(TEXTOS_DO_TERMO).encode()).hexdigest()

    assert _HASH_DOS_TEXTOS_POR_VERSAO.get(VERSAO_TERMO) == atual
