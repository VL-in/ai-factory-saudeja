"""
SaúdeJá — identificador do paciente no banco da clínica (src/id_paciente.py,
ADR-011).
"""
import hashlib
import hmac
import os

import pytest

import id_paciente
import pseudonimo

CPF = "111.444.777-35"
SHA256_DO_CPF = hashlib.sha256(b"11144477735").hexdigest()


def test_identificador_e_o_hmac_do_sha256_com_prefixo():
    """A fórmula é o contrato com as linhas já migradas: mudá-la faria todo
    paciente que volta ganhar um cadastro novo."""
    chave = os.environ[id_paciente.VARIAVEL_CHAVE].encode()
    esperado = hmac.new(chave, SHA256_DO_CPF.encode(), hashlib.sha256).hexdigest()

    assert id_paciente.de_cpf(CPF) == "hmac1:" + esperado


def test_cadastro_e_migracao_chegam_ao_mesmo_valor():
    """O cadastro calcula a partir do CPF; a migração, a partir do sha256 que
    o banco guardava. Os dois têm de coincidir, senão o paciente migrado não
    é reencontrado no recadastro."""
    assert id_paciente.de_cpf(CPF) == id_paciente.envelopar(SHA256_DO_CPF)


def test_cpf_com_ou_sem_pontuacao_vira_o_mesmo_identificador():
    assert id_paciente.de_cpf(CPF) == id_paciente.de_cpf("11144477735")


def test_identificador_nao_contem_o_cpf_nem_o_hash_sem_chave():
    identificador = id_paciente.de_cpf(CPF)

    assert "11144477735" not in identificador
    assert SHA256_DO_CPF not in identificador


def test_outra_chave_muda_o_identificador(monkeypatch):
    antes = id_paciente.de_cpf(CPF)
    monkeypatch.setenv(id_paciente.VARIAVEL_CHAVE, "z" * 32)

    assert id_paciente.de_cpf(CPF) != antes


def test_identificador_e_diferente_do_pseudonimo_do_treino(monkeypatch):
    monkeypatch.setenv(pseudonimo.VARIAVEL_CHAVE, "k" * 32)

    assert id_paciente.de_cpf(CPF).removeprefix(id_paciente.PREFIXO) != pseudonimo.id_titular(CPF)


@pytest.mark.parametrize("valor", [None, "", "trocar-esta-chave"])
def test_chave_ausente_ou_curta_e_erro_explicito(monkeypatch, valor):
    """Nunca um default silencioso, e nunca a volta ao sha256 sem chave."""
    if valor is None:
        monkeypatch.delenv(id_paciente.VARIAVEL_CHAVE)
    else:
        monkeypatch.setenv(id_paciente.VARIAVEL_CHAVE, valor)

    with pytest.raises(id_paciente.ChaveIdentificadorAusente):
        id_paciente.verificar_chave()
    with pytest.raises(id_paciente.ChaveIdentificadorAusente):
        id_paciente.de_cpf(CPF)


def test_chave_igual_a_do_pseudonimo_do_treino_e_recusada(monkeypatch):
    """Banco da clínica e base de treino têm controladores diferentes: quem
    tivesse uma chave não pode ter as duas."""
    mesma = "m" * 32
    monkeypatch.setenv(id_paciente.VARIAVEL_CHAVE, mesma)
    monkeypatch.setenv(pseudonimo.VARIAVEL_CHAVE, mesma)

    with pytest.raises(id_paciente.ChaveIdentificadorAusente, match="diferentes"):
        id_paciente.verificar_chave()


@pytest.mark.parametrize("cpf", ["", "123", None])
def test_cpf_sem_11_digitos_e_recusado(cpf):
    with pytest.raises(ValueError):
        id_paciente.de_cpf(cpf)


@pytest.mark.parametrize(
    "valor", ["EXT-1", SHA256_DO_CPF.upper(), SHA256_DO_CPF[:-1], "hmac1:" + SHA256_DO_CPF]
)
def test_envelopar_so_aceita_o_sha256_do_formato_antigo(valor):
    """Envolver de novo um valor já migrado (ou um texto qualquer) geraria um
    identificador que o cadastro nunca reencontra."""
    with pytest.raises(ValueError):
        id_paciente.envelopar(valor)


def test_formatos_antigo_e_atual_sao_reconhecidos():
    atual = id_paciente.envelopar(SHA256_DO_CPF)

    assert id_paciente.eh_legado(SHA256_DO_CPF) and not id_paciente.eh_atual(SHA256_DO_CPF)
    assert id_paciente.eh_atual(atual) and not id_paciente.eh_legado(atual)
    assert not id_paciente.eh_legado("EXT-1") and not id_paciente.eh_atual("EXT-1")
