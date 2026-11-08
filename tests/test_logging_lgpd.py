"""
SaúdeJá — testes da blindagem LGPD do log.

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
   veredito no smoke test do deploy.
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


# (texto com PII, o trecho que não pode sobreviver). Também é o corpus da
# regra RE2 do Loki, mais abaixo: as duas cópias das regras ficam provadas
# contra os mesmos textos.
CASOS_DE_PII = [
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
    # IP, família própria: a contagem de dígitos da regra numérica deixava
    # passar todo IPv4 com menos de 10 dígitos.
    ("cliente 10.1.2.3 conectado", "10.1.2.3"),
    ("cliente 189.145.212.170 conectado", "189.145.212.170"),
    ('10.1.2.3:4444 - "WebSocket /_nicegui_ws/socket.io/" [accepted]', "10.1.2.3"),
    ("conexão de ('172.17.0.2', 51234) recusada", "172.17.0.2"),
    ("proxy em [2001:db8::1]:443", "2001:db8::1"),
    ("escutando em ::1 apenas", "::1"),
    ("origem 2001:0db8:0000:0000:0000:ff00:0042:8329", "2001:0db8:0000:0000:0000:ff00:0042:8329"),
    ("fim de frase com o IP 10.1.2.3.", "10.1.2.3"),
]


@pytest.mark.parametrize("texto,segredo", CASOS_DE_PII)
def test_redige_as_formas_de_pii_do_produto(texto, segredo):
    redigido = redigir(texto)
    assert segredo not in redigido, f"PII sobreviveu à redação: {redigido}"
    assert MARCADOR in redigido


TEXTOS_DE_DIAGNOSTICO = [
    f"paciente {HASH_PACIENTE} sem predição",
    f"agendamento {UUID_AGENDAMENTO} processado",
    "predição servida em duracao_ms=1423",
    "model_version=a1b2c3d4e5f6",
    "threshold=0.6 probabilidade=0.8123",
    "consulta em 2026-09-27 às 14:30",
    "namespace=saudeja",
    "nomeacao=automatica",
    "job D-2 concluído id_execucao=gh-38830675560-1",
    "span 4bf92f3577b34da6a3ce929d0e0e4736 status_http=500",
    # O que a família `ip` não pode levar junto: versão, número decimal,
    # horário e o run do Actions têm ponto ou dois-pontos, mas não a forma de IP.
    "release 2.6.0 publicada",
    "probabilidade 0.82 acima do threshold 0.6",
    "consulta às 14:30:00 de 2026-11-07T14:30:00+00:00",
    "pacote opentelemetry 1.44.0 e instrumentação 0.65b0",
    "versão com quatro números em sequência 1.2.3.4.5",
]


@pytest.mark.parametrize("texto", TEXTOS_DE_DIAGNOSTICO)
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


# --- a cópia RE2 das regras, para a regra P1 "PII em log" do Loki -------------
# O `re` do Python aceita a sintaxe usada em `REGRAS_DE_PII_RE2` (classes,
# `(?i:...)`, repetição contada), então o mesmo corpus serve às duas cópias.
# O que fica fora do teste é a diferença de `\w`: no RE2 ele é só ASCII.


@pytest.mark.parametrize("texto,segredo", CASOS_DE_PII)
def test_regra_do_loki_acha_a_pii_crua_e_nao_a_redigida(texto, segredo):
    import re

    padrao = re.compile(logging_config.regex_de_pii_re2())
    assert padrao.search(texto), f"a regra do Loki não achou PII em {texto!r}"
    assert not padrao.search(redigir(texto)), (
        f"a regra do Loki dispararia sobre a saída do filtro: {redigir(texto)!r}"
    )


@pytest.mark.parametrize("texto", TEXTOS_DE_DIAGNOSTICO)
def test_regra_do_loki_nao_dispara_no_que_o_filtro_deixa_passar(texto):
    """Falso positivo aqui é alerta crítico (P1) sem causa: o texto que o filtro
    deixa intacto chega ao Loki como está e não pode casar a regra."""
    import re

    assert not re.search(logging_config.regex_de_pii_re2(), texto)


def test_regra_do_loki_cabe_no_logql_e_cobre_as_tres_familias():
    """O LogQL recebe o padrão entre crases, e uma família esquecida na
    tradução deixaria a rede de segurança com um furo."""
    regex = logging_config.regex_de_pii_re2()
    assert "`" not in regex
    assert [n for n, _p in logging_config.REGRAS_DE_PII_RE2] == [
        n for n, _p, _s in logging_config.REGRAS_DE_PII
    ]


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
    caso de `ErroEnvioInfobip` antes da blindagem, que ecoava o corpo da resposta
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


def _logger_de_acesso_do_uvicorn(monkeypatch):
    """O logger `uvicorn.access` de verdade, com o `AccessFormatter` de verdade
    -- o formatador é a peça que quebrava, então imitá-lo não provaria nada."""
    from uvicorn.logging import AccessFormatter

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(
        AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    )
    handler.addFilter(FiltroRedacaoPII())
    # `handleError` imprime no stderr e engole a exceção: sem isto, um
    # formatador quebrado passaria no teste com o buffer simplesmente vazio.
    monkeypatch.setattr(logging, "raiseExceptions", True)
    monkeypatch.setattr(handler, "handleError", lambda _record: (_ for _ in ()).throw(
        AssertionError("o formatador de acesso do uvicorn quebrou")
    ))

    logger = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(logger, "handlers", [handler])
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "level", logging.INFO)
    return logger, buffer


# IPv4 com menos de 10 dígitos: a regra numérica sozinha não o pegaria.
IP_CURTO = "189.45.12.7"


def test_linha_de_acesso_do_uvicorn_sai_inteira_e_sem_ip(monkeypatch):
    """Zerar `record.args` derrubava o `AccessFormatter` (ele desempacota
    cinco campos): a linha de acesso sumia e o "--- Logging error ---" ia ao
    stderr com a mensagem -- IP do cliente incluído. A linha agora sai, com o
    status e o caminho, e o cliente vira marcador sempre."""
    logger, buffer = _logger_de_acesso_do_uvicorn(monkeypatch)

    logger.info(
        '%s - "%s %s HTTP/%s" %d', f"{IP_CURTO}:51234", "POST", "/predict", "1.1", 200
    )

    saida = buffer.getvalue()
    assert IP_CURTO not in saida
    assert saida.strip() == f'{MARCADOR} - "POST /predict HTTP/1.1" 200 OK'


def test_query_string_da_linha_de_acesso_e_redigida(monkeypatch):
    logger, buffer = _logger_de_acesso_do_uvicorn(monkeypatch)

    logger.info(
        '%s - "%s %s HTTP/%s" %d', "10.0.0.1:1", "GET", f"/health?contato={EMAIL}", "1.1", 404
    )

    saida = buffer.getvalue()
    assert EMAIL not in saida
    assert "404 Not Found" in saida


def test_falha_na_redacao_da_linha_de_acesso_nao_quebra_o_formatador(monkeypatch):
    """Fail-closed também aqui: a linha sai só com marcadores, e o formatador
    do uvicorn (que exige os cinco campos e um status inteiro) não quebra."""

    def _explodir(_texto):
        raise RuntimeError("regex quebrada")

    monkeypatch.setattr(logging_config, "redigir", _explodir)
    logger, buffer = _logger_de_acesso_do_uvicorn(monkeypatch)

    logger.info(
        '%s - "%s %s HTTP/%s" %d', f"{IP_CURTO}:51234", "GET", f"/x?e={EMAIL}", "1.1", 200
    )

    saida = buffer.getvalue()
    assert IP_CURTO not in saida
    assert EMAIL not in saida
    assert MARCADOR in saida


def test_outros_loggers_com_cinco_args_seguem_a_regra_geral():
    """O tratamento campo a campo vale só para `uvicorn.access`: num logger
    qualquer, cinco argumentos não dizem nada sobre o formato, e a regra geral
    (mensagem resolvida e redigida) é a que protege `nome=%s`."""
    logger, buffer = _logger_com_captura("teste.cinco_args", formato="texto")

    logger.info("nome=%s %s %s %s %s", "Ana Souza", "b", "c", "d", "e")

    assert "Ana Souza" not in buffer.getvalue()


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


# --- linhas de websocket do uvicorn (interface em NiceGUI, ADR-013) -------------
# O uvicorn loga cada websocket pelo logger `uvicorn.error`, com o cliente
# como `ip:porta` no primeiro argumento. O `connection open`/`connection
# closed` vem da biblioteca `websockets`, pelo mesmo logger, sem o endereço:
# entra aqui para provar que a linha passa intacta e sem IP.
LINHAS_DE_WEBSOCKET_DO_UVICORN = [
    ('%s - "WebSocket %s" [accepted]', ("10.1.2.3:4444", "/_nicegui_ws/socket.io/")),
    ('%s - "WebSocket %s" 403', ("189.45.12.7:51234", "/_nicegui_ws/socket.io/")),
    ('%s - "WebSocket %s" %d', ("[2001:db8::1]:443", "/_nicegui_ws/socket.io/", 1000)),
    ("connection open", ()),
    ("connection closed", ()),
]


@pytest.mark.parametrize("modelo,args", LINHAS_DE_WEBSOCKET_DO_UVICORN)
def test_linhas_de_websocket_do_uvicorn_saem_sem_ip(modelo, args):
    """Com o logger `uvicorn.error` de verdade e o handler blindado do jeito
    que `configurar_logging` o deixa (`propagate=False` do uvicorn
    incluído)."""
    logger = logging.getLogger("uvicorn.error")
    estado = (list(logger.handlers), logger.propagate, logger.level)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(FormatadorJSON())
    try:
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        aplicar_filtro_em_handlers_existentes()

        logger.info(modelo, *args)
    finally:
        logger.handlers, logger.propagate, _nivel = estado
        logger.setLevel(_nivel)

    linha = json.loads(buffer.getvalue().strip())
    for endereco in ("10.1.2.3", "189.45.12.7", "2001:db8::1"):
        assert endereco not in linha["mensagem"]
    assert auditoria_lgpd.auditar_texto(buffer.getvalue(), "<websocket>") == []
    if args:
        assert linha["mensagem"].startswith(f'{MARCADOR} - "WebSocket /_nicegui_ws/')
    else:
        assert linha["mensagem"] == modelo


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

    assert {a["regra"] for a in achados} == {"chave-de-pii", "email", "cpf-telefone"}
    # O relatório nunca reimprime o valor encontrado -- senão a auditoria é o
    # vazamento com outro nome.
    for achado in achados:
        assert TELEFONE not in achado["excerto"]
        assert EMAIL not in achado["excerto"]
        assert "Ana Souza" not in achado["excerto"]


def test_auditoria_nao_acha_nada_no_que_o_filtro_ja_produziu():
    """O teste que fecha o ciclo: a saída real do handler configurado é varrida
    pelo mesmo script que dá o veredito no smoke test do deploy."""
    logger, buffer = _logger_com_captura("teste.ciclo")
    logger.info(
        "cadastro de paciente cpf=%s telefone=%s email=%s", CPF, TELEFONE, EMAIL
    )
    logger.info("fila do dia", extra={"nome": "Ana Souza", "id_agendamento": UUID_AGENDAMENTO})

    assert auditoria_lgpd.auditar_texto(buffer.getvalue(), "<ciclo>") == []


def test_auditoria_acha_ip_cru_e_nao_acha_o_redigido():
    """O IP é a família nova: a auditoria do log do container (smoke do CI) tem
    de acusá-lo cru, e o mesmo texto depois do filtro tem de passar limpo."""
    linha = '10.1.2.3:4444 - "WebSocket /_nicegui_ws/socket.io/" [accepted]'

    achados = auditoria_lgpd.auditar_texto(linha, "<teste>")

    assert [a["regra"] for a in achados] == ["ip"]
    assert "10.1.2.3" not in achados[0]["excerto"]
    assert auditoria_lgpd.auditar_texto(redigir(linha), "<teste>") == []


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
