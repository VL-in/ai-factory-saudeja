"""
SaúdeJá — identificador do paciente no banco da clínica (ADR-011), sem banco.

`pacientes.id_paciente_externo` identifica o paciente sem guardar o CPF. Até
o ADR-011 ele era o sha256 do CPF, e esse hash se reverte por força bruta:
há só 10⁹ CPFs válidos (os dois últimos dígitos são verificadores), e uma GPU
comum testa todos em menos de um segundo. Quem lesse o identificador -- num
vazamento da tabela, num print da "Fila do dia", num log -- teria o CPF.

O identificador passa a ser um HMAC-SHA256 do hash antigo, com uma chave
secreta que fica fora do banco (`PACIENTE_CHAVE_IDENTIFICADOR`):

    id_paciente_externo = "hmac1:" + HMAC(chave, sha256(CPF só com dígitos))

**Por que envolver o hash antigo, e não o CPF**: as linhas já gravadas têm só
o sha256, e o CPF não está no banco. Como o HMAC é calculado sobre o que já
está gravado, a migração (`scripts/migrar_id_paciente.py`) troca linha a
linha sem precisar do CPF de ninguém, e o cadastro novo chega ao mesmo valor
pelas duas etapas. É a técnica de migrar senhas de md5 para bcrypt sem pedir a
senha de novo.

**O prefixo `hmac1:`** separa o formato novo do antigo (os dois são 64
caracteres hexadecimais). É o que torna a migração idempotente -- rodar duas
vezes nunca envolve um valor já envolvido -- e reserva espaço para uma troca
de chave futura (`hmac2:` sobre o `hmac1:`), se um dia for preciso.

**Chave própria, separada da `TREINO_CHAVE_PSEUDONIMO`**: o banco da clínica é
da clínica, e a base de treino é da SaúdeJá (ADR-010). Com a mesma chave, quem
tivesse uma teria as duas.

**A chave é da clínica**, controladora do banco: ela gera, guarda a cópia
oficial e a entrega à SaúdeJá só para o Space, que calcula o identificador no
cadastro (e, durante a migração, para o `.env` do encarregado). O job D-2 só lê
o identificador pronto e não a recebe.

**A chave não roda e não pode se perder**: o identificador é a chave do upsert
do cadastro. Com outra chave, o paciente que volta não encontra a linha dele,
ganha um cadastro novo e o histórico de faltas (feature do modelo) recomeça
do zero.
"""
import hashlib
import hmac
import os
import re

import config_projeto  # noqa: F401 -- side effect: carrega .env em os.environ
import pseudonimo

VARIAVEL_CHAVE = "PACIENTE_CHAVE_IDENTIFICADOR"
PREFIXO = "hmac1:"

_HASH_LEGADO = re.compile(r"[0-9a-f]{64}")


class ChaveIdentificadorAusente(Exception):
    """`PACIENTE_CHAVE_IDENTIFICADOR` não configurada, curta demais ou igual à
    chave do pseudônimo da base de treino."""


def verificar_chave() -> None:
    """Levanta `ChaveIdentificadorAusente`, com o motivo, se a chave deste
    ambiente não serve."""
    _chave()


def de_cpf(cpf: str) -> str:
    """Identificador do paciente. Aceita o CPF com ou sem pontuação."""
    digitos = re.sub(r"\D", "", cpf or "")
    if len(digitos) != 11:
        # A validação do cadastro já barra CPF inválido; aqui a recusa evita
        # que um texto vazio vire um identificador válido, igual para todos.
        raise ValueError("CPF precisa ter 11 dígitos para virar identificador")
    return envelopar(hashlib.sha256(digitos.encode()).hexdigest())


def envelopar(hash_legado: str) -> str:
    """O identificador novo a partir do sha256 que o banco guardava. É o
    passo que a migração aplica às linhas existentes."""
    if not eh_legado(hash_legado):
        raise ValueError("o identificador antigo é um sha256 em hexadecimal minúsculo")
    return PREFIXO + hmac.new(_chave(), hash_legado.encode(), hashlib.sha256).hexdigest()


def eh_legado(valor: str) -> bool:
    """Se o valor ainda é o sha256 sem chave, do formato anterior ao ADR-011."""
    return bool(_HASH_LEGADO.fullmatch(valor or ""))


def eh_atual(valor: str) -> bool:
    return (valor or "").startswith(PREFIXO)


def _chave() -> bytes:
    chave = os.environ.get(VARIAVEL_CHAVE, "")
    if len(chave) < pseudonimo.TAMANHO_MINIMO_DA_CHAVE:
        raise ChaveIdentificadorAusente(
            f"{VARIAVEL_CHAVE} ausente ou com menos de {pseudonimo.TAMANHO_MINIMO_DA_CHAVE} "
            "caracteres -- configure no .env (ver .env.example)"
        )
    if hmac.compare_digest(chave, os.environ.get(pseudonimo.VARIAVEL_CHAVE, "")):
        raise ChaveIdentificadorAusente(
            f"{VARIAVEL_CHAVE} igual a {pseudonimo.VARIAVEL_CHAVE}: as duas bases têm "
            "controladores diferentes e precisam de chaves diferentes (ADR-011)"
        )
    return chave.encode()
