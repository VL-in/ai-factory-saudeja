"""
SaúdeJá — testes da blindagem LGPD do log (Passo 8).

O requisito é absoluto ("Sem PII em logs. Nunca.", BRIEFING.md; 0 ocorrências,
SLO §6) e, segundo o [ADR-006](../docs/adr/adr-006-observabilidade.md), não há
como verificá-lo a posteriori em produção: o log do HF Space é efêmero. Estes
testes são, junto com as guardas estáticas de `test_coerencia_repo.py`, a
evidência que substitui a auditoria de log de produção.

Três coisas são provadas aqui, e a segunda é a que mais importa:

1. A redação funciona para as quatro formas de PII que o sistema manipula
   (CPF, telefone, e-mail e nome) e **não** estraga os identificadores
   pseudonimizados de que o diagnóstico depende.
2. Ela vale para o log de **terceiros** -- `uvicorn`, `httpx`, `streamlit` --,
   que é o único que existe em volume em produção. Um filtro que só cobrisse as
   linhas escritas por nós protegeria exatamente as que já estão sob controle.
3. O ciclo fecha: a saída de um handler configurado, varrida por
   `scripts/auditoria_lgpd.py`, dá zero achado -- o mesmo script que dá o
   veredito no smoke test do Passo 11.
"""
import io
import json
import logging
import sys
from pathlib import Path

import pytest

import logging_config
from logging_config import (
    MARCADOR,
    FiltroRedacaoPII,
    FormatadorJSON,
    aplicar_filtro_em_handlers_existentes,
    configurar_logging,
    redigir,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import auditoria_lgpd

CPF = "529.982.247-25"
TELEFONE = "5511987654321"
TELEFONE_FORMATADO = "(11) 98765-4321"
EMAIL = "paciente@exemplo.com.br"
HASH_PACIENTE = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
UUID_AGENDAMENTO = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture(autouse=True)
def estado_de_logging_isolado():
    """`configurar_logging` mexe no logger RAIZ -- estado global do processo.
    Sem restaurar, um teste daqui mudaria o nível de log e os handlers de toda
    a suíte seguinte (e o `caplog` do pytest depende da raiz)."""
    raiz = logging.getLogger()
    handlers = list(raiz.handlers)
    nivel = raiz.level
    configurado = logging_config._configurado
    yield
    raiz.handlers = handlers
    raiz.setLevel(nivel)
    logging_config._configurado = configurado


def _logger_com_captura(nome: str, formato="json"):
    """Logger isolado com handler em memória, montado com o MESMO formatador e
    filtro que `configurar_logging` instala. `propagate=False` evita que a
    linha suba para a raiz e seja capturada duas vezes."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(
        FormatadorJSON() if formato == "json" else logging.Formatter("%(message)s")
    )
    handler.addFilter(FiltroRedacaoPII())

    logger = logging.getLogger(nome)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, buffer


# --- redação: as quatro formas de PII que o sistema manipula -------------------


@pytest.mark.parametrize(
    "texto,segredo",
    [
        (f"cadastro recusado para cpf={CPF}", CPF),
        ("cpf sem pontuação: 52998224725", "52998224725"),
        (f"enviando SMS para {TELEFONE}", TELEFONE),
        (f"telefone digitado: {TELEFONE_FORMATADO}", "98765-4321"),
        (f"contato: {EMAIL}", EMAIL),
        ('{"nome": "Ana Souza", "idade": 34}', "Ana Souza"),
        ("nome=Ana Souza, idade=34", "Ana Souza"),
        ("nome_completo=Ana Souza", "Ana Souza"),
        ("paciente_nome=Ana Souza", "Ana Souza"),
        ("sobrenome=Souza", "Souza"),
        ("Infobip recusou: {'to': '5511987654321'}", TELEFONE),
    ],
)
def test_redige_as_formas_de_pii_do_produto(texto, segredo):
    redigido = redigir(texto)
    assert segredo not in redigido, f"PII sobreviveu à redação: {redigido}"
    assert MARCADOR in redigido


@pytest.mark.parametrize(
    "texto",
    [
        f"paciente {HASH_PACIENTE} sem predição",
        f"agendamento {UUID_AGENDAMENTO} processado",
        "predição servida em duracao_ms=1423",
        "model_version=a1b2c3d4e5f6",
        "threshold=0.6 probabilidade=0.8123",
        "consulta em 2026-09-27 às 14:30",
    ],
)
def test_nao_redige_o_que_o_diagnostico_precisa(texto):
    """Redigir demais é falha, não excesso de zelo: sem `id_agendamento` no log
    não se responde "por que este paciente não recebeu lembrete", e o hash de
    CPF É a forma minimizada que o banco guarda (architecture.md §4.1)."""
    assert redigir(texto) == texto


def test_nome_de_variavel_parecido_nao_vira_falso_positivo():
    """`name` é chave proibida, `namespace` não -- a regra exige fronteira de
    palavra antes, senão qualquer identificador que comece igual perderia o
    valor e o log viraria inútil."""
    assert redigir("namespace=saudeja") == "namespace=saudeja"
    assert redigir("nomeacao=automatica") == "nomeacao=automatica"


# --- o filtro no caminho real do registro -------------------------------------


def test_saida_e_uma_linha_json_com_os_campos_esperados():
    logger, buffer = _logger_com_captura("teste.json")
    logger.info("predição servida", extra={"model_version": "abc123", "duracao_ms": 12})

    linha = json.loads(buffer.getvalue().strip())
    assert linha["nivel"] == "INFO"
    assert linha["mensagem"] == "predição servida"
    assert linha["logger"] == "teste.json"
    assert linha["model_version"] == "abc123"
    assert linha["duracao_ms"] == 12
    assert "ts" in linha and "origem" in linha


def test_extra_com_chave_proibida_perde_o_valor():
    """Caso que a regra de texto não pega sozinha: no JSON de saída a chave e o
    valor são serializados separadamente, então a string `telefone=...` nunca
    se forma. A proteção aqui é pela chave, como na allowlist do
    `src/observabilidade.py`."""
    logger, buffer = _logger_com_captura("teste.extra")
    logger.warning(
        "falha ao enviar",
        extra={"telefone": TELEFONE, "nome": "Ana Souza", "id_agendamento": UUID_AGENDAMENTO},
    )

    linha = json.loads(buffer.getvalue().strip())
    assert linha["telefone"] == MARCADOR
    assert linha["nome"] == MARCADOR
    assert linha["id_agendamento"] == UUID_AGENDAMENTO


def test_extra_aninhado_tambem_e_redigido():
    logger, buffer = _logger_com_captura("teste.aninhado")
    logger.info("payload", extra={"paciente": {"telefone": TELEFONE, "sexo": "F"}})

    linha = json.loads(buffer.getvalue().strip())
    assert linha["paciente"]["telefone"] == MARCADOR
    assert linha["paciente"]["sexo"] == "F"


def test_traceback_de_excecao_e_redigido_e_a_classe_sobrevive():
    """O vetor mais provável de PII em log não é uma f-string nossa -- é a
    mensagem de uma exceção de terceiro subindo num traceback. Era exatamente o
    caso de `ErroEnvioInfobip` antes do Passo 8, que ecoava o corpo da resposta
    da Infobip com o telefone do destinatário dentro."""
    logger, buffer = _logger_com_captura("teste.excecao")
    try:
        raise ValueError(f"provedor recusou o envio para {TELEFONE}")
    except ValueError:
        logger.exception("erro no disparo")

    saida = buffer.getvalue()
    assert TELEFONE not in saida
    assert MARCADOR in saida
    linha = json.loads(saida.strip())
    assert linha["excecao"] == "ValueError"
    assert "traceback" in linha


def test_falha_na_redacao_descarta_a_linha(monkeypatch):
    """Fail-closed: se a redação quebrar, a linha não sai crua. Perder log é
    aceitável; vazar dado de paciente não é."""

    def _explodir(_texto):
        raise RuntimeError("regex quebrada")

    monkeypatch.setattr(logging_config, "redigir", _explodir)
    logger, buffer = _logger_com_captura("teste.falha")
    logger.info("cpf=%s", CPF)

    saida = buffer.getvalue()
    assert CPF not in saida
    assert "falha ao redigir" in saida


# --- a parte que importa: log de terceiro, com handler próprio ----------------


def test_filtro_alcanca_handler_de_terceiro_instalado_antes(monkeypatch):
    """Simula o que o uvicorn faz: instala handler próprio e põe
    `propagate=False`, de modo que o handler da raiz nunca vê esses registros.
    É o log de acesso -- com IP de cliente -- e o de erro, os dois únicos que
    existem em volume no Space.
    """
    buffer = io.StringIO()
    handler_de_terceiro = logging.StreamHandler(buffer)
    imitando_uvicorn = logging.getLogger("uvicorn.access.falso")
    imitando_uvicorn.handlers = [handler_de_terceiro]
    imitando_uvicorn.propagate = False
    imitando_uvicorn.setLevel(logging.INFO)

    # Sem a blindagem, a linha sai crua.
    imitando_uvicorn.info("cliente %s POST /predict", TELEFONE)
    assert TELEFONE in buffer.getvalue()

    configurar_logging()
    buffer.truncate(0)
    buffer.seek(0)
    imitando_uvicorn.info("cliente %s POST /predict", TELEFONE)

    assert TELEFONE not in buffer.getvalue()
    assert MARCADOR in buffer.getvalue()


def test_configurar_logging_e_idempotente():
    """A UI reexecuta o script a cada interação e a API importa os mesmos
    módulos -- chamada repetida não pode duplicar handler (linha dobrada no log)
    nem empilhar filtros."""
    configurar_logging()
    raiz = logging.getLogger()
    nossos = [h for h in raiz.handlers if isinstance(h, logging_config._HandlerSaudeJa)]
    assert len(nossos) == 1

    configurar_logging()
    nossos = [h for h in raiz.handlers if isinstance(h, logging_config._HandlerSaudeJa)]
    assert len(nossos) == 1
    assert len(nossos[0].filters) == 1
    # Duas chamadas seguidas: a primeira pode blindar um handler que o próprio
    # pytest instalou entre testes, a segunda tem de ser no-op -- é isso que
    # prova que o passeio não empilha filtro no mesmo handler.
    aplicar_filtro_em_handlers_existentes()
    assert aplicar_filtro_em_handlers_existentes() == 0


def test_httpx_fica_em_warning():
    """`httpx` loga a URL completa de cada requisição em INFO, e o
    `supabase-py` roda sobre ele: uma fila do dia renderizada geraria uma linha
    por consulta ao PostgREST, com valor de filtro na query string."""
    configurar_logging()
    assert logging.getLogger("httpx").level == logging.WARNING


# --- ciclo fechado com scripts/auditoria_lgpd.py ------------------------------


def test_auditoria_acha_pii_em_log_nao_blindado():
    linhas = "\n".join(
        [
            "2026-09-27 INFO cadastro nome=Ana Souza",
            f"2026-09-27 INFO envio para {TELEFONE}",
            f"2026-09-27 INFO contato {EMAIL}",
        ]
    )
    achados = auditoria_lgpd.auditar_texto(linhas, "<teste>")

    assert {a["regra"] for a in achados} == {"chave-de-pii", "email", "cpf-telefone-ip"}
    # O relatório nunca reimprime o valor encontrado -- senão a auditoria é o
    # vazamento com outro nome.
    for achado in achados:
        assert TELEFONE not in achado["excerto"]
        assert EMAIL not in achado["excerto"]
        assert "Ana Souza" not in achado["excerto"]


def test_auditoria_nao_acha_nada_no_que_o_filtro_ja_produziu():
    """O teste que fecha o ciclo: a saída real do handler configurado é varrida
    pelo mesmo script que dá o veredito no smoke test do Passo 11."""
    logger, buffer = _logger_com_captura("teste.ciclo")
    logger.info(
        "cadastro de paciente cpf=%s telefone=%s email=%s", CPF, TELEFONE, EMAIL
    )
    logger.info("fila do dia", extra={"nome": "Ana Souza", "id_agendamento": UUID_AGENDAMENTO})

    assert auditoria_lgpd.auditar_texto(buffer.getvalue(), "<ciclo>") == []


def test_auditoria_ignora_duracao_e_identificador_interno():
    """Falso positivo torna auditoria inútil: se cada `duracao_ms` e cada uuid
    virasse achado, ninguém leria o relatório."""
    linhas = json.dumps(
        {
            "mensagem": "job D-2 concluído",
            "duracao_ms": 1423,
            "id_agendamento": UUID_AGENDAMENTO,
            "model_version": "a1b2c3d4",
            "id_paciente_externo": HASH_PACIENTE,
        }
    )
    assert auditoria_lgpd.auditar_texto(linhas, "<teste>") == []


def test_auditoria_falha_por_codigo_de_saida(tmp_path, capsys):
    """Código de saída != 0 é o que permite pendurar a auditoria num passo de
    CI ou num smoke test sem ninguém precisar ler a saída."""
    arquivo = tmp_path / "app.log"
    arquivo.write_text(f"INFO envio para {TELEFONE}\n", encoding="utf-8")
    assert auditoria_lgpd.main([str(arquivo)]) == 1

    arquivo.write_text("INFO envio concluído\n", encoding="utf-8")
    assert auditoria_lgpd.main([str(arquivo)]) == 0
