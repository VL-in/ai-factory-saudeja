"""
SaúdeJá — pseudônimos da base de treino (src/pseudonimo.py, ADR-010).
"""
import hashlib

import pytest

import pseudonimo

CHAVE = "k" * pseudonimo.TAMANHO_MINIMO_DA_CHAVE
OUTRA_CHAVE = "z" * pseudonimo.TAMANHO_MINIMO_DA_CHAVE
CPF = "111.444.777-35"


@pytest.fixture
def chave(monkeypatch):
    monkeypatch.setenv(pseudonimo.VARIAVEL_CHAVE, CHAVE)


def test_mesma_chave_mesmo_pseudonimo(chave):
    assert pseudonimo.id_titular(CPF) == pseudonimo.id_titular(CPF)


def test_cpf_com_ou_sem_pontuacao_vira_o_mesmo_titular(chave):
    """O pedido de exclusão chega com o CPF digitado de qualquer jeito: o
    pseudônimo tem de ser o mesmo que o cadastro gerou."""
    assert pseudonimo.id_titular(CPF) == pseudonimo.id_titular("11144477735")


def test_outra_chave_muda_o_pseudonimo(monkeypatch, chave):
    antes = pseudonimo.id_titular(CPF)
    monkeypatch.setenv(pseudonimo.VARIAVEL_CHAVE, OUTRA_CHAVE)

    assert pseudonimo.id_titular(CPF) != antes


def test_pseudonimo_nao_e_o_hash_sem_chave_do_banco_da_clinica(chave):
    """O sha256 do CPF se reverte por força bruta; o pseudônimo do treino não
    pode coincidir com ele (LGPD.md, Quadro 2)."""
    sem_chave = hashlib.sha256(b"11144477735").hexdigest()

    assert pseudonimo.id_titular(CPF) != sem_chave
    assert "11144477735" not in pseudonimo.id_titular(CPF)


def test_dominios_de_titular_e_de_consulta_nao_colidem(chave):
    """O mesmo texto nos dois domínios gera pseudônimos diferentes: uma linha
    de consulta nunca se confunde com um titular."""
    assert pseudonimo.id_titular("11144477735") != pseudonimo.id_consulta("11144477735")


def test_id_consulta_e_deterministico_pelo_uuid(chave):
    uuid = "0b9c6f2e-1d3a-4f5b-9c8d-7e6f5a4b3c2d"

    assert pseudonimo.id_consulta(uuid) == pseudonimo.id_consulta(uuid)
    assert pseudonimo.id_consulta(uuid) != pseudonimo.id_consulta(uuid[:-1] + "e")


@pytest.mark.parametrize("valor", ["", "trocar-esta-chave"])
def test_chave_ausente_ou_curta_e_erro_explicito(monkeypatch, valor):
    """Nunca um default silencioso: um placeholder do .env não pode virar a
    chave de produção."""
    monkeypatch.setenv(pseudonimo.VARIAVEL_CHAVE, valor)

    assert not pseudonimo.chave_configurada()
    with pytest.raises(pseudonimo.ChavePseudonimoAusente):
        pseudonimo.id_titular(CPF)


@pytest.mark.parametrize("cpf", ["", "123", None])
def test_cpf_incompleto_nao_vira_pseudonimo(chave, cpf):
    with pytest.raises(ValueError):
        pseudonimo.id_titular(cpf)


def test_id_consulta_vazio_e_recusado(chave):
    with pytest.raises(ValueError):
        pseudonimo.id_consulta("")
