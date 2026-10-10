"""
SaúdeJá — regra do consentimento vigente (src/consentimento.py, ADR-009).

Sem banco: os eventos chegam como o PostgREST os devolve, com `registrado_em`
em texto ISO 8601 e a fração de segundo sem os zeros finais.
"""
import itertools

import pytest

from consentimento import (
    ACAO_CONCEDIDO,
    ACAO_REVOGADO,
    FINALIDADE_LEMBRETE,
    FINALIDADE_TRANSFERENCIA,
    FINALIDADE_TREINO,
    FINALIDADES,
    consentimentos_vigentes,
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
