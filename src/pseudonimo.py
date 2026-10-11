"""
SaúdeJá — pseudônimos da base de treino (ADR-010), sem banco.

A base de treino (schema `treino`, controlada pela SaúdeJá) não guarda nome,
telefone, CPF nem o `id_paciente_externo` do banco da clínica. Cada titular e
cada consulta aparecem lá por um pseudônimo calculado com HMAC-SHA256 e uma
chave secreta, `TREINO_CHAVE_PSEUDONIMO`:

- `id_titular = HMAC(chave, "titular:" + CPF só com dígitos)`;
- `id_consulta = HMAC(chave, "agendamento:" + uuid do agendamento)`.

**Por que não o sha256 do CPF** (`ui.logic._id_paciente_externo_de_cpf`): o
espaço de CPFs válidos é pequeno, e o hash sem chave se reverte por força
bruta (LGPD.md, Quadro 2). Com a chave guardada à parte, quem tem a base de
treino não chega ao CPF: é a pseudonimização do art. 13, § 4º.

**Rastreio para a exclusão**: o titular informa o CPF à clínica ou ao
encarregado, e o pseudônimo é recalculado. Funciona mesmo depois de a clínica
eliminar o cadastro dela, porque a base de treino não depende de `pacientes`.

**A chave não roda**: trocá-la quebra o vínculo com as linhas e as provas já
gravadas. Vazamento é incidente (LGPD.md, seção 10). Cada ambiente tem a sua,
e ela nunca vai ao re-treino nem ao workflow de purga, que recebem os
pseudônimos prontos.

O prefixo de domínio separa os dois usos: um uuid de agendamento e um CPF
nunca geram o mesmo pseudônimo, mesmo que um dia tenham o mesmo texto.
"""
import hashlib
import hmac
import os
import re

import config_projeto  # noqa: F401 -- side effect: carrega .env em os.environ

VARIAVEL_CHAVE = "TREINO_CHAVE_PSEUDONIMO"
# 32 caracteres: uma chave de 128 bits em hexadecimal já passa disso. Recusar
# chave curta evita que um placeholder do .env ("trocar", "teste") vire a
# chave de produção sem ninguém perceber.
TAMANHO_MINIMO_DA_CHAVE = 32

_DOMINIO_TITULAR = "titular"
_DOMINIO_AGENDAMENTO = "agendamento"


class ChavePseudonimoAusente(Exception):
    """`TREINO_CHAVE_PSEUDONIMO` não configurada, ou curta demais."""


def chave_configurada() -> bool:
    """Se a etapa de treino tem como rodar neste ambiente -- para o
    diagnóstico da aba de dev e o resumo do job D-2, sem levantar."""
    try:
        _chave()
    except ChavePseudonimoAusente:
        return False
    return True


def id_titular(cpf: str) -> str:
    """Pseudônimo do titular. Aceita o CPF com ou sem pontuação."""
    digitos = re.sub(r"\D", "", cpf or "")
    if len(digitos) != 11:
        # A validação do cadastro já barra CPF inválido; aqui a recusa evita
        # que um texto vazio vire um pseudônimo válido, igual para todo mundo.
        raise ValueError("CPF precisa ter 11 dígitos para virar pseudônimo")
    return _hmac(_DOMINIO_TITULAR, digitos)


def id_consulta(id_agendamento: str) -> str:
    """Pseudônimo da consulta, a partir do uuid do agendamento no banco da
    clínica. É a chave da sincronização diária do desfecho (job D-2)."""
    if not id_agendamento:
        raise ValueError("id do agendamento vazio")
    return _hmac(_DOMINIO_AGENDAMENTO, str(id_agendamento))


def _hmac(dominio: str, valor: str) -> str:
    mensagem = f"{dominio}:{valor}".encode()
    return hmac.new(_chave(), mensagem, hashlib.sha256).hexdigest()


def _chave() -> bytes:
    chave = os.environ.get(VARIAVEL_CHAVE, "")
    if len(chave) < TAMANHO_MINIMO_DA_CHAVE:
        raise ChavePseudonimoAusente(
            f"{VARIAVEL_CHAVE} ausente ou com menos de {TAMANHO_MINIMO_DA_CHAVE} "
            "caracteres -- configure no .env (ver .env.example)"
        )
    return chave.encode()
