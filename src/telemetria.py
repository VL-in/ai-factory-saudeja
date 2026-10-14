"""
SaúdeJá — telemetria OpenTelemetry com envio OTLP direto ao Grafana Cloud
(Passo 8.9 do plano; revê os descartes do [ADR-006]).

Este módulo **configura** os três provedores (logs, métricas e traces), liga
o `logging` do processo ao exportador de logs e oferece as primitivas
`contar`, `registrar` e `span`, com catálogo fechado de métricas
(`METRICAS`) e de atributos (`ATRIBUTOS_PERMITIDOS`). Os pontos de medição
continuam em `src/observabilidade.py`: `registrar_evento` e `medir` alimentam
`eventos_app` e este destino a partir da mesma chamada. `eventos_app` segue
sendo a fonte de verdade de longo prazo.

Quatro propriedades que o resto do módulo existe para garantir:

1. **No-op por padrão.** Sem `OTEL_EXPORTER_OTLP_ENDPOINT`, com
   `OBSERVABILIDADE_ATIVA=false`, com `OTEL_SDK_DISABLED=true` ou sem os
   pacotes `opentelemetry-*` instalados, nada é criado e nada sai do processo.
   É o estado do CI, da suíte de testes (fixture autouse em
   `tests/conftest.py`) e de quem roda local sem configurar o Grafana.
2. **Telemetria quebrada degrada, não interrompe.** Mesma filosofia de
   `observabilidade.py`: nenhuma exceção deste módulo chega à UI, à API ou ao
   job. Os exportadores trabalham em thread própria; Grafana fora do ar vira
   aviso no stdout, não latência na predição.
3. **Idempotente.** O Streamlit reexecuta `src/ui/app.py` inteiro a cada
   interação; sem a guarda, cada clique criaria um exportador novo, com thread
   e conexão próprias. A chamada repetida só pendura o handler em logger
   isolado que tenha aparecido depois da primeira.
4. **O filtro de PII vale para o que sai por OTLP.** O handler do OTel nasce
   já com `FiltroRedacaoPII` (`src/logging_config.py`). Além disso, os pontos
   de entrada chamam `configurar_telemetria` **antes** de `configurar_logging`,
   e o passeio de `aplicar_filtro_em_handlers_existentes` também o alcança --
   duas barreiras, porque o Loki é a primeira cópia de log que **não** é
   efêmera, e o que entrar lá fica 14 dias.
5. **Span sai sanitizado, venha de onde vier.** Desde a P3, FastAPI e `httpx`
   são instrumentados automaticamente, e esses spans trazem URL com query
   string, IP de quem chamou e a mensagem da exceção. O exportador de spans é
   envolvido por `_ExportadorSanitizado`, que reescreve todo span antes de ele
   sair do processo.

Tracing (P3): `span` aceita `identificadores` (`IDENTIFICADORES_DE_SPAN`), que
nunca chegam a métrica; `id_execucao()` liga `eventos_app` ao trace (UI e API)
ou ao run do Actions (job); `definir_sessao` dá o `session.id` da UI.

Import tardio dos pacotes OTel, dentro de `configurar_telemetria`, pelo mesmo
motivo de `observabilidade.py`: o módulo precisa ser importável numa imagem
que não os tenha, e só deixar de exportar.
"""
import contextlib
import logging
import os
import threading
from collections.abc import Callable, Iterator
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from logging_config import FiltroRedacaoPII

SERVICO_UI = "saudeja-ui"
SERVICO_API = "saudeja-api"
SERVICO_JOB_D2 = "saudeja-job-d2"
SERVICOS = frozenset({SERVICO_UI, SERVICO_API, SERVICO_JOB_D2})

VERSAO_DESCONHECIDA = "desconhecida"
# `id_execucao` do job: `gh-<run_id>-<tentativa>` (ver `id_do_run`).
PREFIXO_RUN = "gh-"

# Buckets explícitos para os histogramas de latência (`saudeja_*_duracao_ms`):
# a predição aquecida leva ~10 ms e a primeira de um processo ~1,4 s, e os
# buckets padrão do OTel não separam bem essa faixa.
BUCKETS_DE_LATENCIA_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 30000)
# Probabilidade de no-show em faixas de 0,1: o formato da distribuição, ao lado
# da taxa de disparo, é o sinal de drift do modelo.
BUCKETS_DE_PROBABILIDADE = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)

CONTADOR = "contador"
HISTOGRAMA = "histograma"

# Catálogo fechado das métricas do projeto (Passo 8.9, "Métricas: nomes,
# atributos e definições"). Nome fora daqui é ignorado em runtime e recusado
# pela guarda estática de tests/test_telemetria.py.
#
# Sem unidade declarada de propósito: o Grafana Cloud traduz OTLP para nomes
# Prometheus acrescentando a unidade ao nome, e `saudeja_acao_duracao_ms` com
# unidade "ms" viraria `saudeja_acao_duracao_ms_milliseconds`. A unidade já
# está no nome.
METRICAS = {
    "saudeja_acao_total": (
        CONTADOR,
        "Ações por acao, origem, status e classe da exceção (volume e taxa de erro)",
    ),
    "saudeja_acao_duracao_ms": (HISTOGRAMA, "Latência por acao e origem, em ms"),
    "saudeja_erro_total": (CONTADOR, "Eventos `erro` por rota, origem e classe da exceção"),
    "saudeja_predicao_duracao_ms": (
        HISTOGRAMA,
        "Features + predição + SHAP, por origem, model_version e braço, em ms",
    ),
    "saudeja_predicao_probabilidade": (
        HISTOGRAMA,
        "Probabilidade de no-show da fila D-2, por model_version e braço",
    ),
    "saudeja_job_d2_agendamentos_total": (
        CONTADOR,
        "Agendamentos do job D-2 por resultado e braço",
    ),
    "saudeja_sms_total": (CONTADOR, "Lembretes por status_envio"),
}

# Atributos permitidos em métrica e em span: cardinalidade FECHADA. O plano
# gratuito do Grafana Cloud corta em 10 mil séries, e cada valor distinto de
# atributo é uma série nova. Identificador de agendamento, paciente, sessão ou
# trace nunca entra em métrica -- vai só em span (P3) e em log.
ATRIBUTOS_PERMITIDOS = frozenset(
    {
        "acao",
        "rota",
        "origem",
        "status",
        "excecao",
        "model_version",
        "braco",
        "resultado",
        "status_envio",
    }
)
TAMANHO_MAXIMO_ATRIBUTO = 120

# Identificadores que só um SPAN pode levar (P3): ligam o trace ao agendamento,
# à execução do job e à sessão do Streamlit. Ficam FORA de
# `ATRIBUTOS_PERMITIDOS` de propósito -- aquela allowlist vale também para
# métrica, e um uuid por agendamento lá viraria uma série por agendamento.
# Nenhum é dado pessoal: `id_agendamento` é uuid interno, `id_execucao` é o
# `GITHUB_RUN_ID` ou o trace_id, `session.id` é aleatório e não se liga ao
# e-mail de quem fez login.
IDENTIFICADORES_DE_SPAN = frozenset({"id_agendamento", "id_execucao", "session.id"})

# Rotas que a instrumentação do FastAPI não rastreia, pelo mesmo motivo que as
# deixa fora de `eventos_app` (`api/main.py::ROTAS_OBSERVADAS`): a sonda bate
# nelas de minutos em minutos, e cada batida seria um trace sem diagnóstico.
ROTAS_FORA_DO_TRACING = "health"

# --- sanitização dos spans de instrumentação automática (P3) ------------------
# Os spans que o projeto abre já nascem limpos (`span`, allowlist acima). Os da
# instrumentação automática (FastAPI, httpx) não: trazem a URL com a query
# string -- a do PostgREST carrega o VALOR dos filtros (`?telefone=eq....`) --,
# o IP e o user agent de quem chamou, e o FastAPI grava a MENSAGEM da exceção
# (`record_exception` e a descrição do status). O exportador sanitizado
# reescreve todo span antes de sair do processo, qualquer que seja a origem.
_ATRIBUTOS_DE_URL = frozenset({"url.full", "http.url"})
_ATRIBUTOS_DE_ALVO = frozenset({"http.target"})
_ATRIBUTOS_REMOVIDOS = frozenset(
    {
        "url.query",
        "url.fragment",
        "client.address",
        "client.port",
        "network.peer.address",
        "network.peer.port",
        "net.peer.ip",
        "net.peer.port",
        "net.sock.peer.addr",
        "net.sock.peer.port",
        "http.client_ip",
        "user_agent.original",
        "http.user_agent",
        "exception.message",
        "exception.stacktrace",
        "exception.escaped",
    }
)
_PREFIXOS_REMOVIDOS = ("http.request.header.", "http.response.header.")

# Logs do próprio SDK (exportador sem rede, endpoint recusando) não voltam para
# o exportador: com Grafana fora, cada falha geraria uma linha que falharia de
# novo no próximo lote. Eles continuam indo para o stdout.
_PREFIXO_LOGGERS_DO_OTEL = "opentelemetry"

_trava = threading.Lock()
_decidido = False
_ativa_no_processo = False
_handler: logging.Handler | None = None
_provedores: list[Any] = []
_instrumentos: dict[str, Any] = {}
# Execução do job no Actions (`GITHUB_RUN_ID`), fixada na configuração do
# serviço do job. Vale com a telemetria desligada: liga `eventos_app` ao run
# mesmo sem Grafana.
_id_execucao_do_processo: str | None = None
# Quem sabe a sessão da UI (`src/ui/app.py` registra um que lê o
# `st.session_state`). Uma função, e não um valor: as ações da Fila do dia
# rodam em callbacks (`on_click`), antes do corpo do script.
_provedor_de_sessao: Callable[[], str | None] | None = None

logger = logging.getLogger(__name__)


def ativa() -> bool:
    """Há para onde exportar, e exportar é permitido? Não diz se os pacotes
    estão instalados -- isso só se descobre no import, em `configurar_telemetria`."""
    if not (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip():
        return False
    if (os.environ.get("OTEL_SDK_DISABLED") or "").strip().lower() == "true":
        return False
    # Import tardio: `observabilidade` importa este módulo no topo.
    import observabilidade

    return observabilidade.ativa()


def configurada() -> bool:
    """A telemetria está exportando neste processo?"""
    return _ativa_no_processo


def configurar_telemetria(servico: str) -> bool:
    """Liga os três provedores OTel deste processo. Devolve se ficou ativa.

    Chamada uma vez por ponto de entrada (`lifespan` da API, topo de
    `src/ui/app.py`, `main()` do job), **antes** de `configurar_logging`.
    """
    global _decidido, _ativa_no_processo, _handler, _id_execucao_do_processo
    if servico not in SERVICOS:
        raise ValueError(f"serviço desconhecido para a telemetria: {servico!r}")
    with _trava:
        if servico == SERVICO_JOB_D2:
            _id_execucao_do_processo = id_do_run()
        if _decidido:
            if _handler is not None:
                _pendurar_handler(_handler)
            return _ativa_no_processo
        _decidido = True
        if not ativa():
            return False
        try:
            _handler = _montar(servico)
        except ImportError:
            logger.warning(
                "telemetria desligada: pacotes opentelemetry ausentes nesta imagem",
                extra={"servico": servico},
            )
            return False
        except Exception as exc:
            logger.warning(
                "telemetria desligada: falha ao configurar",
                extra={"servico": servico, "excecao": type(exc).__name__},
            )
            return False
        _pendurar_handler(_handler)
        _ativa_no_processo = True
        _instrumentar_httpx()
        return True


def forcar_envio(timeout: float = 5.0) -> bool:
    """Esvazia os lotes pendentes. Para processo curto (o job D-2), o
    equivalente a `observabilidade.flush()`: sem isto, o último lote de logs e
    spans morreria com o processo. Nunca levanta."""
    if not _ativa_no_processo:
        return True
    tudo_enviado = True
    for provedor in list(_provedores):
        try:
            if provedor.force_flush(int(timeout * 1000)) is False:
                tudo_enviado = False
        except Exception:
            tudo_enviado = False
    return tudo_enviado


def contar(nome: str, atributos: dict[str, Any] | None = None, valor: int = 1) -> None:
    """Soma `valor` ao contador `nome`. No-op com a telemetria desligada;
    nunca levanta."""
    _registrar(nome, CONTADOR, valor, atributos)


def registrar(nome: str, valor: float, atributos: dict[str, Any] | None = None) -> None:
    """Registra `valor` no histograma `nome`. No-op com a telemetria desligada;
    nunca levanta."""
    _registrar(nome, HISTOGRAMA, valor, atributos)


def atributos_permitidos(atributos: dict[str, Any] | None) -> dict[str, str]:
    """Só as chaves de `ATRIBUTOS_PERMITIDOS`, sem `None`, como texto curto.
    Chave desconhecida é descartada em silêncio, como em
    `observabilidade.sanitizar_detalhe`: um `id_agendamento` passado por engano
    não pode derrubar a ação, nem virar uma série por agendamento."""
    if not atributos:
        return {}
    return {
        chave: str(valor)[:TAMANHO_MAXIMO_ATRIBUTO]
        for chave, valor in atributos.items()
        if chave in ATRIBUTOS_PERMITIDOS and valor is not None
    }


def identificadores_de_span(identificadores: dict[str, Any] | None) -> dict[str, str]:
    """Só as chaves de `IDENTIFICADORES_DE_SPAN`, sem `None`, como texto curto."""
    if not identificadores:
        return {}
    return {
        chave: str(valor)[:TAMANHO_MAXIMO_ATRIBUTO]
        for chave, valor in identificadores.items()
        if chave in IDENTIFICADORES_DE_SPAN and valor is not None
    }


def definir_sessao(provedor: Callable[[], str | None] | None) -> None:
    """Registra quem informa o `session.id` da UI. Chamado pelo
    `src/ui/app.py`; a cada span, o provedor é consultado, e uma falha dele só
    deixa o span sem sessão."""
    global _provedor_de_sessao
    _provedor_de_sessao = provedor


def _sessao_atual() -> str | None:
    if _provedor_de_sessao is None:
        return None
    try:
        return _provedor_de_sessao()
    except Exception:
        return None


def id_do_run() -> str | None:
    """`gh-<GITHUB_RUN_ID>-<GITHUB_RUN_ATTEMPT>`, ou `None` fora do Actions.

    O prefixo não é enfeite. O `GITHUB_RUN_ID` tem 10 a 11 dígitos, e solto num
    log o `FiltroRedacaoPII` o redigiria como CPF ou telefone (e a regra de
    PII do Loki dispararia). Com o hífen antes dos dígitos, a sequência não
    casa -- a mesma fronteira que já protege o último grupo de um uuid --, e o
    filtro continua sem exceção nenhuma. A tentativa separa o re-run do run
    original, que têm o mesmo `GITHUB_RUN_ID`."""
    run = (os.environ.get("GITHUB_RUN_ID") or "").strip()
    if not run:
        return None
    tentativa = (os.environ.get("GITHUB_RUN_ATTEMPT") or "").strip() or "1"
    return f"{PREFIXO_RUN}{run}-{tentativa}"


def id_execucao() -> str | None:
    """O que liga `eventos_app`, Loki e Tempo (P3): `id_do_run()` no job; na
    UI e na API, o trace_id do span corrente (32 hex). `None` sem os dois --
    telemetria desligada fora do Actions, ou evento registrado fora de span."""
    if _id_execucao_do_processo:
        return _id_execucao_do_processo
    if not _ativa_no_processo:
        return None
    try:
        from opentelemetry import trace

        contexto = trace.get_current_span().get_span_context()
        if not contexto.is_valid:
            return None
        return trace.format_trace_id(contexto.trace_id)
    except Exception:
        return None


@contextlib.contextmanager
def span(
    nome: str,
    atributos: dict[str, Any] | None = None,
    recusas: tuple[type[BaseException], ...] = (),
    identificadores: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Span em volta do bloco. Exceção marca o span como erro com **só o nome
    da classe**: o `record_exception` padrão do OTel gravaria a mensagem, que é
    o vetor mais provável de PII (a resposta de um provedor ecoando o payload).
    Exceção de `recusas` (senha errada, formulário inválido) não é erro do
    sistema: fica só o atributo `excecao`. A exceção segue subindo.

    `atributos` passa pela allowlist de métrica; `identificadores`, pela de
    `IDENTIFICADORES_DE_SPAN`. O `session.id` da UI entra sozinho."""
    aberto = None
    if _ativa_no_processo:
        aberto = _abrir_span(
            nome,
            {
                **atributos_permitidos(atributos),
                **identificadores_de_span(
                    {"session.id": _sessao_atual(), **(identificadores or {})}
                ),
            },
        )
    if aberto is None:
        yield
        return
    with aberto as corrente:
        try:
            yield
        except BaseException as exc:
            _marcar_excecao(corrente, exc, recusada=isinstance(exc, recusas))
            raise


def _abrir_span(nome: str, atributos: dict[str, str]) -> Any:
    """`atributos` já filtrados por quem chama (`span`)."""
    if not _ativa_no_processo or not _provedores:
        return None
    try:
        return _provedores[0].get_tracer("saudeja").start_as_current_span(
            nome,
            attributes=atributos,
            record_exception=False,
            set_status_on_exception=False,
        )
    except Exception:
        return None


def _marcar_excecao(corrente: Any, exc: BaseException, recusada: bool) -> None:
    with contextlib.suppress(Exception):
        from opentelemetry.trace import Status, StatusCode

        classe = type(exc).__name__
        corrente.set_attribute("excecao", classe)
        if not recusada:
            corrente.set_attribute("error.type", classe)
            corrente.set_status(Status(StatusCode.ERROR, classe))


def _registrar(nome: str, tipo: str, valor: float, atributos: dict[str, Any] | None) -> None:
    if not _ativa_no_processo or METRICAS.get(nome, (None,))[0] != tipo:
        return
    try:
        instrumento = _instrumento(nome, tipo)
        if instrumento is None:
            return
        if tipo == CONTADOR:
            instrumento.add(valor, atributos_permitidos(atributos))
        else:
            instrumento.record(valor, atributos_permitidos(atributos))
    except Exception:
        return


def _instrumento(nome: str, tipo: str) -> Any:
    """Criado na primeira medição e guardado, para não recriar a cada chamada."""
    instrumento = _instrumentos.get(nome)
    if instrumento is not None or len(_provedores) < 2:
        return instrumento
    medidor = _provedores[1].get_meter("saudeja")
    descricao = METRICAS[nome][1]
    if tipo == CONTADOR:
        instrumento = medidor.create_counter(nome, description=descricao)
    else:
        instrumento = medidor.create_histogram(nome, description=descricao)
    _instrumentos[nome] = instrumento
    return instrumento


def recurso(servico: str) -> dict[str, str]:
    """Atributos do recurso OTel. `deployment.environment` é o que separa dev
    de prod num stack só (decisão 8 do Passo 8.9): alerta só dispara para `prod`."""
    return {
        "service.name": servico,
        "service.version": _versao(),
        "deployment.environment": (os.environ.get("APP_ENV") or "dev").strip() or "dev",
    }


def _versao() -> str:
    """Release SemVer quando o ambiente a declara (`APP_VERSION`); no Actions,
    o sha curto do commit, que já identifica o código que rodou."""
    versao = (os.environ.get("APP_VERSION") or "").strip()
    if versao:
        return versao
    sha = (os.environ.get("GITHUB_SHA") or "").strip()
    return sha[:7] if sha else VERSAO_DESCONHECIDA


def _criar_exportadores() -> tuple[Any, Any, Any]:
    """(spans, métricas, logs) por OTLP/HTTP; o do meio pode ser um
    `MetricReader` pronto. Endpoint e cabeçalho de
    autenticação vêm de `OTEL_EXPORTER_OTLP_ENDPOINT`/`_HEADERS`, lidos pelo
    próprio SDK. Separado para os testes trocarem por exportadores em memória."""
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(), OTLPMetricExporter(), OTLPLogExporter()


def url_sem_consulta(url: str) -> str:
    """Esquema + host + caminho. Sem query string (a do PostgREST carrega o
    valor dos filtros), sem fragmento e sem usuário/senha embutidos."""
    try:
        partes = urlsplit(str(url))
        host = partes.hostname or ""
        if partes.port:
            host = f"{host}:{partes.port}"
        return urlunsplit((partes.scheme, host, partes.path, "", ""))
    except Exception:
        return ""


def sanitizar_atributos(atributos: Any) -> dict[str, Any]:
    """Atributos de span de qualquer origem, prontos para sair do processo."""
    limpos: dict[str, Any] = {}
    for chave, valor in dict(atributos or {}).items():
        if chave in _ATRIBUTOS_REMOVIDOS or chave.startswith(_PREFIXOS_REMOVIDOS):
            continue
        if chave in _ATRIBUTOS_DE_URL:
            valor = url_sem_consulta(valor)
        elif chave in _ATRIBUTOS_DE_ALVO:
            valor = str(valor).split("?", 1)[0].split("#", 1)[0]
        limpos[chave] = valor
    return limpos


def sanitizar_span(span: Any) -> Any:
    """Cópia do `ReadableSpan` com atributos e eventos sanitizados e a
    descrição do status reduzida ao tipo do erro. O FastAPI grava
    `"{classe}: {mensagem}"` na descrição e a mensagem num evento
    `exception`; aqui fica só a classe, como nos spans do projeto."""
    from opentelemetry.sdk.trace import Event, ReadableSpan
    from opentelemetry.trace import Status, StatusCode

    atributos = sanitizar_atributos(span.attributes)
    eventos = [
        Event(evento.name, sanitizar_atributos(evento.attributes), evento.timestamp)
        for evento in span.events
    ]
    status = span.status
    if status.status_code is StatusCode.ERROR:
        # A classe da exceção antes do `error.type`: no span de servidor, este
        # é o status HTTP ("500"), que diz menos.
        tipos = [e.attributes.get("exception.type") for e in eventos if e.attributes]
        tipo = (
            atributos.get("excecao")
            or next((t for t in tipos if t), None)
            or atributos.get("error.type")
            or "erro"
        )
        status = Status(StatusCode.ERROR, str(tipo)[:TAMANHO_MAXIMO_ATRIBUTO])
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=atributos,
        events=eventos,
        links=span.links,
        kind=span.kind,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class _ExportadorSanitizado:
    """Envolve o exportador de spans: todo span passa por `sanitizar_span`
    antes de sair. É a barreira para os spans que o projeto não abre (FastAPI,
    httpx), o equivalente do `FiltroRedacaoPII` no handler de log. Um span que
    não pôde ser sanitizado é descartado, não exportado cru."""

    def __init__(self, interno: Any) -> None:
        self._interno = interno

    def export(self, spans: Any) -> Any:
        limpos = []
        for span in spans:
            with contextlib.suppress(Exception):
                limpos.append(sanitizar_span(span))
        return self._interno.export(limpos)

    def shutdown(self) -> None:
        self._interno.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._interno.force_flush(timeout_millis)


def _preparar_instrumentacao_http() -> None:
    """Convenções HTTP estáveis (`url.full`, `http.server.request.duration`,
    o nome que o plano usa) em vez das antigas, cuja métrica de servidor leva
    `http.target` -- caminho com query string -- como atributo. Precisa estar
    no ambiente antes da primeira instrumentação do processo."""
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")


def _instrumentar_httpx() -> None:
    """Spans filhos para toda chamada `httpx` do processo: o Supabase
    (`supabase-py` usa `httpx`), a Infobip no job e a API quando a UI usa
    `PREDICT_BACKEND=api` -- caso em que o `traceparent` vai junto e o trace
    atravessa UI -> API. Nunca levanta."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        _preparar_instrumentacao_http()
        instrumentador = HTTPXClientInstrumentor()
        if not instrumentador.is_instrumented_by_opentelemetry:
            instrumentador.instrument(
                tracer_provider=_provedores[0], meter_provider=_provedores[1]
            )
    except Exception as exc:
        logger.warning(
            "instrumentação do httpx desligada", extra={"excecao": type(exc).__name__}
        )


def instrumentar_fastapi(app: Any) -> bool:
    """Span por request e `http.server.request.duration` na API. Chamado no
    `lifespan`, depois de `configurar_telemetria`; no-op com a telemetria
    desligada. Devolve se instrumentou.

    O Starlette monta a pilha de middlewares no primeiro evento ASGI -- que é
    o próprio `lifespan` --, então a instrumentação, que troca o montador, só
    vale se a pilha for descartada: o request seguinte a remonta com o
    middleware do OTel por fora."""
    if not _ativa_no_processo or len(_provedores) < 2:
        return False
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        _preparar_instrumentacao_http()
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=_provedores[0],
            meter_provider=_provedores[1],
            excluded_urls=ROTAS_FORA_DO_TRACING,
            exclude_spans=["receive", "send"],
        )
        app.middleware_stack = None
        return True
    except Exception as exc:
        logger.warning(
            "instrumentação do FastAPI desligada", extra={"excecao": type(exc).__name__}
        )
        return False


def _montar(servico: str) -> logging.Handler:
    from opentelemetry import metrics, trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

    exportador_spans, exportador_metricas, exportador_logs = _criar_exportadores()
    resource = Resource.create(recurso(servico))

    provedor_traces = TracerProvider(resource=resource)
    # `cast`: o envelope não herda de `SpanExporter` porque o módulo não
    # importa o OTel no topo (import tardio); a interface é a mesma.
    provedor_traces.add_span_processor(
        BatchSpanProcessor(cast("SpanExporter", _ExportadorSanitizado(exportador_spans)))
    )

    # Um leitor pronto (os testes passam um `InMemoryMetricReader`) é usado
    # como veio; um exportador ganha o leitor periódico (60 s, o default).
    leitor = (
        exportador_metricas
        if isinstance(exportador_metricas, MetricReader)
        else PeriodicExportingMetricReader(exportador_metricas)
    )
    provedor_metricas = MeterProvider(
        resource=resource,
        metric_readers=[leitor],
        views=[
            View(
                instrument_name="saudeja_*_duracao_ms",
                aggregation=ExplicitBucketHistogramAggregation(
                    boundaries=[float(b) for b in BUCKETS_DE_LATENCIA_MS]
                ),
            ),
            View(
                instrument_name="saudeja_predicao_probabilidade",
                aggregation=ExplicitBucketHistogramAggregation(
                    boundaries=list(BUCKETS_DE_PROBABILIDADE)
                ),
            ),
        ],
    )

    provedor_logs = LoggerProvider(resource=resource)
    provedor_logs.add_log_record_processor(BatchLogRecordProcessor(exportador_logs))

    # Globais para a instrumentação automática (P3) e para `trace.get_tracer`
    # achá-los sem receber o provedor. O SDK só aceita definir uma vez por
    # processo; os testes usam as referências guardadas em `_provedores`.
    trace.set_tracer_provider(provedor_traces)
    metrics.set_meter_provider(provedor_metricas)
    set_logger_provider(provedor_logs)
    _provedores[:] = [provedor_traces, provedor_metricas, provedor_logs]

    handler = LoggingHandler(level=logging.NOTSET, logger_provider=provedor_logs)
    handler.addFilter(_fora_os_logs_do_otel)
    # Já nasce blindado (propriedade 4 da docstring), sem depender da ordem de
    # chamada em relação a `configurar_logging`.
    handler.addFilter(FiltroRedacaoPII())
    return handler


def _fora_os_logs_do_otel(record: logging.LogRecord) -> bool:
    return not record.name.startswith(_PREFIXO_LOGGERS_DO_OTEL)


def _pendurar_handler(handler: logging.Handler) -> None:
    """Na raiz e em todo logger que não propaga até ela.

    `uvicorn` e `uvicorn.access` usam `propagate=False` com handlers próprios,
    e o Streamlit faz o mesmo com o logger `streamlit`: só a raiz deixaria de
    fora justamente o log de acesso da API e o traceback de exceção da UI. Um
    registro para no primeiro logger com `propagate=False` da subida, então
    pendurar em cada um deles não duplica linha.
    """
    for alvo in _loggers_alvo():
        if handler not in alvo.handlers:
            alvo.addHandler(handler)


def _loggers_alvo() -> list[logging.Logger]:
    raiz = logging.getLogger()
    alvos = [raiz]
    for nome, candidato in list(raiz.manager.loggerDict.items()):
        if not isinstance(candidato, logging.Logger) or candidato.propagate:
            continue
        if nome.startswith(_PREFIXO_LOGGERS_DO_OTEL):
            continue
        alvos.append(candidato)
    return alvos


def _reiniciar() -> None:
    """Só para os testes: desliga os provedores e tira o handler de todos os
    loggers, devolvendo o módulo ao estado de antes da primeira chamada."""
    global _decidido, _ativa_no_processo, _handler, _id_execucao_do_processo
    global _provedor_de_sessao
    with _trava:
        with contextlib.suppress(Exception):
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            instrumentador = HTTPXClientInstrumentor()
            if instrumentador.is_instrumented_by_opentelemetry:
                instrumentador.uninstrument()
        _id_execucao_do_processo = None
        _provedor_de_sessao = None
        if _handler is not None:
            for alvo in [logging.getLogger(), *logging.getLogger().manager.loggerDict.values()]:
                if isinstance(alvo, logging.Logger) and _handler in alvo.handlers:
                    alvo.removeHandler(_handler)
        for provedor in _provedores:
            with contextlib.suppress(Exception):
                provedor.shutdown()
        _provedores.clear()
        _instrumentos.clear()
        _handler = None
        _decidido = False
        _ativa_no_processo = False
