"""
SaúdeJá — testes da telemetria OpenTelemetry (`src/telemetria.py`, Passo 8.9).

Três coisas são provadas aqui:

1. **No-op por padrão e degradação.** Sem endpoint, com o registro desligado,
   sem os pacotes ou com o exportador quebrado, UI, API e job seguem normais.
2. **O log que sai por OTLP passa pelo filtro de PII**, nas duas ordens de
   chamada, inclusive o de logger de terceiro com `propagate=False`
   (`uvicorn.access`). O Loki é a primeira cópia de log que não é efêmera.
3. **Os pontos de entrada chamam a telemetria antes do logging**, e o job
   esvazia o lote no fim.
4. **Tracing (P3)**: os spans da instrumentação automática (httpx, FastAPI)
   saem sem query string, sem IP e sem a mensagem da exceção; identificadores
   vão ao span e nunca à métrica; `id_execucao` liga `eventos_app` ao trace ou
   ao run do Actions.
"""
import ast
import io
import logging
import sys
from pathlib import Path

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, LogRecordExportResult
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import logging_config
import telemetria
from logging_config import MARCADOR, configurar_logging

REPO_ROOT = Path(__file__).resolve().parents[1]
TELEFONE = "5511987654321"
ENDPOINT_FALSO = "http://127.0.0.1:4318"


@pytest.fixture(autouse=True)
def estado_de_logging_isolado():
    """Mesma restauração de `test_logging_lgpd.py`: a telemetria pendura
    handler na raiz e em loggers de terceiros, estado global do processo."""
    raiz = logging.getLogger()
    handlers = list(raiz.handlers)
    nivel = raiz.level
    configurado = logging_config._configurado
    isolados = {
        nome: (list(lg.handlers), lg.propagate)
        for nome, lg in raiz.manager.loggerDict.items()
        if isinstance(lg, logging.Logger)
    }
    yield
    telemetria._reiniciar()
    raiz.handlers = handlers
    raiz.setLevel(nivel)
    logging_config._configurado = configurado
    for nome, (hs, propaga) in isolados.items():
        lg = logging.getLogger(nome)
        lg.handlers = hs
        lg.propagate = propaga


class _ExportadorDeLogQuebrado(InMemoryLogRecordExporter):
    def export(self, batch):
        raise ConnectionError("Grafana fora do ar")


@pytest.fixture
def exportador_de_logs(monkeypatch):
    """Endpoint definido e exportadores em memória no lugar dos OTLP: nada
    sai do processo, e o teste lê o que teria saído."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", ENDPOINT_FALSO)
    monkeypatch.delenv("OBSERVABILIDADE_ATIVA", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    logs = InMemoryLogRecordExporter()
    chamadas = []

    def criar():
        chamadas.append(1)
        return InMemorySpanExporter(), ConsoleMetricExporter(out=io.StringIO()), logs

    monkeypatch.setattr(telemetria, "_criar_exportadores", criar)
    logs.chamadas = chamadas
    return logs


def _exportados(logs: InMemoryLogRecordExporter) -> list:
    assert telemetria.forcar_envio(timeout=5)
    return [r.log_record for r in logs.get_finished_logs()]


def _handlers_otel(logger: logging.Logger) -> list[logging.Handler]:
    from opentelemetry.instrumentation.logging.handler import LoggingHandler

    return [h for h in logger.handlers if isinstance(h, LoggingHandler)]


# --- no-op e degradação ---------------------------------------------------------


def test_sem_endpoint_nao_configura_nada():
    assert telemetria.configurar_telemetria(telemetria.SERVICO_UI) is False
    assert not telemetria.configurada()
    assert _handlers_otel(logging.getLogger()) == []
    assert telemetria.forcar_envio() is True


@pytest.mark.parametrize(
    "variavel,valor", [("OBSERVABILIDADE_ATIVA", "false"), ("OTEL_SDK_DISABLED", "true")]
)
def test_desligada_por_variavel_mesmo_com_endpoint(
    exportador_de_logs, monkeypatch, variavel, valor
):
    monkeypatch.setenv(variavel, valor)
    assert telemetria.configurar_telemetria(telemetria.SERVICO_API) is False
    assert exportador_de_logs.chamadas == []


def test_sem_os_pacotes_degrada_para_no_op(exportador_de_logs, monkeypatch):
    """Imagem sem `opentelemetry-*`: o import tardio falha e o processo segue."""
    monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.logging.handler", None)
    assert telemetria.configurar_telemetria(telemetria.SERVICO_JOB_D2) is False
    logging.getLogger("saudeja.teste").warning("segue logando")


def test_servico_desconhecido_e_erro_de_programacao():
    with pytest.raises(ValueError):
        telemetria.configurar_telemetria("saudeja-outro")


def test_exportador_que_falha_nao_derruba_o_log(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", ENDPOINT_FALSO)
    monkeypatch.setattr(
        telemetria,
        "_criar_exportadores",
        lambda: (
            InMemorySpanExporter(),
            ConsoleMetricExporter(out=io.StringIO()),
            _ExportadorDeLogQuebrado(),
        ),
    )
    assert telemetria.configurar_telemetria(telemetria.SERVICO_JOB_D2)
    logging.getLogger("saudeja.teste").error("falha no lote")
    telemetria.forcar_envio(timeout=2)


def test_e_idempotente_um_exportador_por_processo(exportador_de_logs):
    """O Streamlit reexecuta o script a cada clique."""
    for _ in range(3):
        assert telemetria.configurar_telemetria(telemetria.SERVICO_UI)
    assert len(exportador_de_logs.chamadas) == 1
    assert len(_handlers_otel(logging.getLogger())) == 1


# --- recurso --------------------------------------------------------------------


def test_recurso_separa_ambiente_e_versao(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("APP_VERSION", "v2.1.0")
    assert telemetria.recurso(telemetria.SERVICO_UI) == {
        "service.name": "saudeja-ui",
        "service.version": "v2.1.0",
        "deployment.environment": "prod",
    }


def test_versao_cai_para_o_sha_no_actions_e_ambiente_para_dev(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("GITHUB_SHA", "d057f26aa11bb22cc33")
    assert telemetria.recurso(telemetria.SERVICO_JOB_D2)["service.version"] == "d057f26"
    assert telemetria.recurso(telemetria.SERVICO_JOB_D2)["deployment.environment"] == "dev"
    monkeypatch.delenv("GITHUB_SHA")
    assert telemetria.recurso(telemetria.SERVICO_API)["service.version"] == "desconhecida"


# --- PII ------------------------------------------------------------------------


@pytest.mark.parametrize("ordem", ["telemetria_primeiro", "logging_primeiro", "sem_logging"])
def test_log_exportado_sai_redigido_em_qualquer_ordem(exportador_de_logs, ordem):
    """A ordem certa é telemetria antes de logging. `sem_logging` é o caso
    que só o filtro embutido no handler OTLP segura: nas outras ordens o
    handler do projeto, primeiro na raiz, já redige o registro no lugar."""
    if ordem == "telemetria_primeiro":
        telemetria.configurar_telemetria(telemetria.SERVICO_JOB_D2)
        configurar_logging()
    elif ordem == "logging_primeiro":
        configurar_logging()
        telemetria.configurar_telemetria(telemetria.SERVICO_JOB_D2)
    else:
        # Logger de terceiro com handler próprio sem filtro (o que um
        # `configurar_logging` esquecido deixaria): o registro chega cru ao
        # handler OTLP. A raiz não serve aqui, porque os handlers do pytest
        # persistem entre testes e já carregam o filtro de testes anteriores.
        isolado = logging.getLogger("saudeja.teste")
        isolado.handlers = [logging.StreamHandler(io.StringIO())]
        isolado.propagate = False
        telemetria.configurar_telemetria(telemetria.SERVICO_JOB_D2)

    logging.getLogger("saudeja.teste").warning(
        "envio para %s falhou", TELEFONE, extra={"nome": "Ana Souza", "rota": "/predict"}
    )
    registros = _exportados(exportador_de_logs)

    assert len(registros) == 1
    registro = registros[0]
    assert TELEFONE not in registro.body
    assert MARCADOR in registro.body
    assert registro.attributes["nome"] == MARCADOR
    assert registro.attributes["rota"] == "/predict"


def test_handler_otlp_nasce_com_um_filtro_de_pii_so(exportador_de_logs):
    telemetria.configurar_telemetria(telemetria.SERVICO_API)
    configurar_logging()
    (handler,) = _handlers_otel(logging.getLogger())
    filtros = [f for f in handler.filters if isinstance(f, logging_config.FiltroRedacaoPII)]
    assert len(filtros) == 1


def test_logger_de_terceiro_sem_propagacao_tambem_exporta_e_redige(exportador_de_logs):
    """`uvicorn.access` usa `propagate=False`: só a raiz deixaria o log de
    acesso de fora. `uvicorn.error` propaga até `uvicorn`, que não propaga --
    a linha sai uma vez só."""
    for nome in ("uvicorn", "uvicorn.access"):
        lg = logging.getLogger(nome)
        lg.handlers = [logging.StreamHandler(io.StringIO())]
        lg.propagate = False
        lg.setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").propagate = True

    telemetria.configurar_telemetria(telemetria.SERVICO_API)
    configurar_logging()
    logging.getLogger("uvicorn.access").info("%s - POST /predict 200", TELEFONE)
    logging.getLogger("uvicorn.error").info("Application startup complete.")

    corpos = [r.body for r in _exportados(exportador_de_logs)]
    assert len(corpos) == 2
    assert all(TELEFONE not in c for c in corpos)


def test_logger_isolado_que_aparece_depois_e_coberto_na_chamada_seguinte(exportador_de_logs):
    """O Streamlit configura o logger dele em momento que não controlamos."""
    telemetria.configurar_telemetria(telemetria.SERVICO_UI)
    tardio = logging.getLogger("streamlit_tardio_teste")
    tardio.handlers = [logging.StreamHandler(io.StringIO())]
    tardio.propagate = False

    telemetria.configurar_telemetria(telemetria.SERVICO_UI)
    assert len(_handlers_otel(tardio)) == 1


def test_logs_do_proprio_sdk_nao_voltam_para_o_exportador(exportador_de_logs):
    """Com o Grafana fora, cada falha de exportação geraria uma linha que
    falharia de novo no lote seguinte."""
    telemetria.configurar_telemetria(telemetria.SERVICO_JOB_D2)
    logging.getLogger("opentelemetry.exporter.otlp.teste").warning("falha ao exportar")
    assert _exportados(exportador_de_logs) == []


def test_exportador_em_memoria_e_o_que_o_teste_le():
    """Sanidade do próprio dublê: o resultado de export do SDK não mudou de
    nome, ou os testes acima passariam sem exportar nada."""
    assert InMemoryLogRecordExporter().export([]) == LogRecordExportResult.SUCCESS


# --- pontos de entrada ----------------------------------------------------------


def _ordem_das_chamadas(caminho: Path) -> list[str]:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    chamadas = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call):
            alvo = no.func
            nome = alvo.attr if isinstance(alvo, ast.Attribute) else getattr(alvo, "id", "")
            if nome in {"configurar_telemetria", "configurar_logging", "forcar_envio"}:
                chamadas.append((no.lineno, nome))
    return [nome for _, nome in sorted(chamadas)]


@pytest.mark.parametrize(
    "arquivo", ["src/api/main.py", "src/ui/app.py", "src/jobs/inferencia_diaria.py"]
)
def test_ponto_de_entrada_configura_telemetria_antes_do_logging(arquivo):
    ordem = _ordem_das_chamadas(REPO_ROOT / arquivo)
    assert ordem.count("configurar_telemetria") == 1
    assert ordem.count("configurar_logging") == 1
    assert ordem.index("configurar_telemetria") < ordem.index("configurar_logging")


def test_job_esvazia_o_lote_no_fim():
    ordem = _ordem_das_chamadas(REPO_ROOT / "src/jobs/inferencia_diaria.py")
    assert ordem[-1] == "forcar_envio"


# --- métricas e spans (P2) ------------------------------------------------------


def test_primitivas_sao_no_op_com_a_telemetria_desligada():
    telemetria.contar("saudeja_acao_total", {"acao": "login"})
    telemetria.registrar("saudeja_acao_duracao_ms", 12, {"acao": "login"})
    with telemetria.span("login", {"acao": "login"}):
        pass


def test_contador_e_histograma_chegam_com_os_atributos_permitidos(telemetria_em_memoria):
    telemetria.contar(
        "saudeja_acao_total",
        {"acao": "carregar_fila", "status": "ok", "id_agendamento": "550e8400", "excecao": None},
    )
    telemetria.registrar("saudeja_acao_duracao_ms", 42, {"acao": "carregar_fila"})

    assert telemetria_em_memoria.pontos("saudeja_acao_total") == [
        ({"acao": "carregar_fila", "status": "ok"}, 1)
    ]
    assert telemetria_em_memoria.pontos("saudeja_acao_duracao_ms") == [
        ({"acao": "carregar_fila"}, 1)
    ]


def test_metrica_fora_do_catalogo_ou_com_tipo_trocado_e_ignorada(telemetria_em_memoria):
    telemetria.contar("saudeja_inventada_total", {"acao": "x"})
    telemetria.registrar("saudeja_acao_total", 3, {"acao": "x"})
    assert telemetria_em_memoria.pontos("saudeja_inventada_total") == []
    assert telemetria_em_memoria.pontos("saudeja_acao_total") == []


def test_histograma_de_probabilidade_usa_faixas_de_0_1(telemetria_em_memoria):
    telemetria.registrar("saudeja_predicao_probabilidade", 0.35, {"braco": "campeao"})
    dados = telemetria_em_memoria.leitor.get_metrics_data()
    (metrica,) = [
        m
        for r in dados.resource_metrics
        for e in r.scope_metrics
        for m in e.metrics
        if m.name == "saudeja_predicao_probabilidade"
    ]
    (ponto,) = metrica.data.data_points
    assert list(ponto.explicit_bounds) == list(telemetria.BUCKETS_DE_PROBABILIDADE)


def test_span_com_erro_leva_so_a_classe_da_excecao(telemetria_em_memoria):
    with pytest.raises(RuntimeError), telemetria.span("carregar_fila", {"acao": "carregar_fila"}):
        raise RuntimeError(f"PostgREST ecoou {TELEFONE}")

    (span,) = telemetria_em_memoria.spans()
    assert span.status.status_code.name == "ERROR"
    assert span.status.description == "RuntimeError"
    assert span.attributes["excecao"] == "RuntimeError"
    assert span.events == ()  # sem record_exception: a mensagem não sai
    assert TELEFONE not in str(dict(span.attributes))


def test_span_de_recusa_nao_e_erro(telemetria_em_memoria):
    with pytest.raises(KeyError), telemetria.span("login", recusas=(KeyError,)):
        raise KeyError("senha")

    (span,) = telemetria_em_memoria.spans()
    assert span.status.status_code.name != "ERROR"
    assert span.attributes["excecao"] == "KeyError"


def test_atributos_permitidos_nao_tem_identificador():
    """Cardinalidade fechada: identificador de agendamento, paciente, sessão
    ou trace como atributo de métrica abriria uma série por valor (limite de
    10 mil séries no plano gratuito) e carregaria dado pessoal."""
    proibidos = ("id", "sess", "trace", "span", "paciente", "telefone", "email", "nome", "cpf")
    for chave in telemetria.ATRIBUTOS_PERMITIDOS:
        assert not any(p in chave.split("_") or chave.startswith(p) for p in proibidos), chave


def _chamadas_de_telemetria():
    for arquivo in (REPO_ROOT / "src").rglob("*.py"):
        if arquivo.name == "telemetria.py":
            continue
        for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8"))):
            if (
                isinstance(no, ast.Call)
                and isinstance(no.func, ast.Attribute)
                and isinstance(no.func.value, ast.Name)
                and no.func.value.id == "telemetria"
                and no.func.attr in {"contar", "registrar", "span"}
            ):
                yield arquivo.name, no


def test_chamadas_de_src_usam_metricas_e_atributos_do_catalogo():
    """Guarda estática: em runtime, nome ou chave fora do catálogo é
    descartado em silêncio (não pode derrubar a ação), então o erro de
    digitação só aparece aqui."""
    vistas = 0
    for arquivo, chamada in _chamadas_de_telemetria():
        vistas += 1
        if chamada.func.attr != "span":
            nome = chamada.args[0]
            assert isinstance(nome, ast.Constant) and nome.value in telemetria.METRICAS, (
                f"{arquivo}:{chamada.lineno}: métrica fora do catálogo"
            )
        for argumento in chamada.args[1:]:
            if isinstance(argumento, ast.Dict):
                chaves = {k.value for k in argumento.keys if isinstance(k, ast.Constant)}
                assert chaves <= telemetria.ATRIBUTOS_PERMITIDOS, (
                    f"{arquivo}:{chamada.lineno}: atributo fora da allowlist: "
                    f"{chaves - telemetria.ATRIBUTOS_PERMITIDOS}"
                )
        for palavra in chamada.keywords:
            if palavra.arg == "identificadores" and isinstance(palavra.value, ast.Dict):
                chaves = {k.value for k in palavra.value.keys if isinstance(k, ast.Constant)}
                assert chaves <= telemetria.IDENTIFICADORES_DE_SPAN, (
                    f"{arquivo}:{chamada.lineno}: identificador fora da allowlist: "
                    f"{chaves - telemetria.IDENTIFICADORES_DE_SPAN}"
                )
    assert vistas > 0


# --- tracing (P3) ---------------------------------------------------------------


def _todo_o_span(span) -> str:
    """Tudo o que sai num span, como texto, para procurar vazamento."""
    return " ".join(
        [
            span.name,
            str(dict(span.attributes)),
            str(span.status.description),
            *(f"{e.name} {dict(e.attributes or {})}" for e in span.events),
        ]
    )


@pytest.fixture
def servidor_http():
    """Servidor HTTP local de verdade: a instrumentação do httpx envolve o
    transporte real (`HTTPTransport`), que um `MockTransport` não usa."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Responde(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, *args):
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Responde)
    fio = threading.Thread(target=servidor.serve_forever, daemon=True)
    fio.start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()


def test_span_do_httpx_sai_sem_a_query_string_do_postgrest(telemetria_em_memoria, servidor_http):
    """A query do PostgREST carrega o VALOR do filtro: `?telefone=eq....`."""
    import httpx

    url = f"{servidor_http}/rest/v1/pacientes?telefone=eq.{TELEFONE}&select=id"
    with telemetria.span("carregar_fila", {"acao": "carregar_fila"}), httpx.Client() as cliente:
        cliente.get(url, headers={"apikey": "segredo-do-supabase"})

    spans = telemetria_em_memoria.spans()
    (filho,) = [s for s in spans if s.name == "GET"]
    (pai,) = [s for s in spans if s.name == "carregar_fila"]
    assert filho.parent.span_id == pai.context.span_id
    assert filho.attributes["url.full"] == f"{servidor_http}/rest/v1/pacientes"
    assert TELEFONE not in _todo_o_span(filho)
    assert "segredo-do-supabase" not in _todo_o_span(filho)


def test_span_do_fastapi_sai_sem_mensagem_da_excecao_nem_ip(telemetria_em_memoria):
    """O FastAPI grava a mensagem da exceção (evento e descrição do status) e o
    IP de quem chamou: tudo isso some antes de sair do processo."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    app = FastAPI()

    @app.get("/quebra")
    def quebra():
        raise RuntimeError(f"PostgREST ecoou {TELEFONE}")

    @app.get("/health")
    def health():
        return {"ok": True}

    assert telemetria.instrumentar_fastapi(app)
    try:
        with TestClient(app, raise_server_exceptions=False) as cliente:
            cliente.get(f"/quebra?telefone={TELEFONE}")
            cliente.get("/health")
    finally:
        FastAPIInstrumentor.uninstrument_app(app)

    (servidor,) = telemetria_em_memoria.spans()  # /health fica fora do tracing
    assert servidor.name == "GET /quebra"
    assert servidor.status.status_code.name == "ERROR"
    assert servidor.status.description == "RuntimeError"
    assert TELEFONE not in _todo_o_span(servidor)
    assert "client.address" not in servidor.attributes
    assert "user_agent.original" not in servidor.attributes
    assert all(set(e.attributes or {}) <= {"exception.type"} for e in servidor.events)


def test_fastapi_nao_e_instrumentado_com_a_telemetria_desligada():
    from fastapi import FastAPI

    assert telemetria.instrumentar_fastapi(FastAPI()) is False


def test_sanitizacao_de_url_tira_consulta_fragmento_e_credencial():
    assert (
        telemetria.url_sem_consulta("https://u:s@abc.supabase.co:8443/rest/v1/x?a=eq.1#f")
        == "https://abc.supabase.co:8443/rest/v1/x"
    )
    assert telemetria.sanitizar_atributos(
        {"http.target": "/predict?cpf=12345678900", "url.query": "cpf=1", "http.route": "/predict"}
    ) == {"http.target": "/predict", "http.route": "/predict"}


def test_identificadores_vao_ao_span_e_nunca_a_metrica(telemetria_em_memoria):
    with telemetria.span(
        "predizer",
        {"braco": "campeao", "id_agendamento": "nao-pela-allowlist-de-metrica"},
        identificadores={"id_agendamento": "550e8400-e29b", "telefone": TELEFONE},
    ):
        telemetria.contar(
            "saudeja_job_d2_agendamentos_total",
            {"resultado": "predito", "id_agendamento": "550e8400-e29b"},
        )

    (span,) = telemetria_em_memoria.spans()
    assert span.attributes["id_agendamento"] == "550e8400-e29b"
    assert TELEFONE not in _todo_o_span(span)
    assert telemetria_em_memoria.pontos("saudeja_job_d2_agendamentos_total") == [
        ({"resultado": "predito"}, 1)
    ]


def test_identificadores_de_span_nao_tem_dado_pessoal():
    proibidos = ("paciente", "telefone", "email", "nome", "cpf", "funcionario")
    for chave in telemetria.IDENTIFICADORES_DE_SPAN:
        assert not any(p in chave for p in proibidos), chave
    assert not telemetria.IDENTIFICADORES_DE_SPAN & telemetria.ATRIBUTOS_PERMITIDOS


def test_session_id_vem_do_provedor_e_provedor_quebrado_nao_derruba(telemetria_em_memoria):
    telemetria.definir_sessao(lambda: "a1b2c3")
    with telemetria.span("login", {"acao": "login"}):
        pass

    def _quebrado():
        raise RuntimeError("sem ScriptRunContext")

    telemetria.definir_sessao(_quebrado)
    with telemetria.span("login", {"acao": "login"}):
        pass

    primeiro, segundo = telemetria_em_memoria.spans()
    assert primeiro.attributes["session.id"] == "a1b2c3"
    assert "session.id" not in segundo.attributes


def test_id_execucao_e_o_trace_dentro_do_span_e_nada_fora(telemetria_em_memoria):
    assert telemetria.id_execucao() is None
    with telemetria.span("carregar_fila"):
        dentro = telemetria.id_execucao()
    (span,) = telemetria_em_memoria.spans()
    assert dentro == f"{span.context.trace_id:032x}"


def test_id_execucao_do_job_e_o_run_do_actions_mesmo_sem_telemetria(monkeypatch):
    """Sem Grafana, a linha de `eventos_app` do job ainda aponta para o run."""
    monkeypatch.setenv("GITHUB_RUN_ID", "18432112345")
    assert telemetria.configurar_telemetria(telemetria.SERVICO_JOB_D2) is False
    assert telemetria.id_execucao() == "18432112345"


def test_job_vira_um_trace_com_as_etapas(monkeypatch, telemetria_em_memoria):
    """Span raiz da execução com pré-checagem, canário, a fila (`job_d2` com
    `buscar_fila`, `predizer`, `gravar`, `enviar_lembrete`), purga e
    sincronização -- e o run do Actions no span raiz."""
    from jobs import inferencia_diaria as job

    class _Preparacao:
        def __init__(self):
            self.contexto = None
            self.falhas = []
            self.avisos = []

    agendamento = {
        "id": "a01",
        "especialidade": "cardiologia",
        "distancia_km": 5.5,
        "data_hora_agendada": "2026-10-02T13:00:00+00:00",
        "dias_entre_agendamento_consulta": 14,
        "historico_noshow": 1,
        "pacientes": {"data_nascimento": "1980-01-01", "sexo": "F", "telefone": TELEFONE},
    }

    class _Mensageria:
        canal = "espiao"

        def enviar_lembrete(self, telefone, mensagem):
            pass

    monkeypatch.setenv("GITHUB_RUN_ID", "18432112345")
    monkeypatch.setattr(job, "configurar_logging", lambda: None)
    monkeypatch.setattr(job, "pre_checagem", lambda: "v1")
    monkeypatch.setattr(job.canario, "preparar_para_job", lambda *a, **k: _Preparacao())
    monkeypatch.setattr(job, "obter_cliente_mensageria", _Mensageria)
    monkeypatch.setattr(job, "purgar_eventos_antigos", lambda: 0)
    monkeypatch.setattr(job, "purgar_dados_derivados_antigos", lambda: {})
    monkeypatch.setattr(job, "sincronizar_treino", lambda: 0)
    monkeypatch.setattr(
        job.repositories, "buscar_agendamentos_d2_pendentes", lambda data: [agendamento]
    )
    for nome in ("gravar_predicao", "registrar_mensagem", "marcar_lembrete_enviado"):
        monkeypatch.setattr(job.repositories, nome, lambda *a, **k: None)
    monkeypatch.setitem(job.inference.PARAMS, "decision", {"threshold": 0.0})

    assert job.main([]) == job.SAIDA_OK

    spans = {s.name: s for s in telemetria_em_memoria.spans()}
    raiz = spans["execucao_job_d2"]
    assert raiz.parent is None
    assert raiz.attributes["id_execucao"] == "18432112345"
    for etapa in ("pre_checagem", "canario", "job_d2", "purgar", "sincronizar_treino"):
        assert spans[etapa].parent.span_id == raiz.context.span_id, etapa
    for etapa in ("buscar_fila", "predizer", "gravar", "enviar_lembrete"):
        assert spans[etapa].parent.span_id == spans["job_d2"].context.span_id, etapa
    assert spans["predizer"].attributes["id_agendamento"] == "a01"
    assert all(TELEFONE not in _todo_o_span(s) for s in spans.values())


def test_probabilidade_so_e_medida_pelo_job():
    """A predição de teste da UI deformaria a distribuição que serve de sinal
    de drift: o histograma de probabilidade é só da fila real do job."""
    arquivos = {
        arquivo
        for arquivo, chamada in _chamadas_de_telemetria()
        if chamada.args
        and isinstance(chamada.args[0], ast.Constant)
        and chamada.args[0].value == "saudeja_predicao_probabilidade"
    }
    assert arquivos == {"inferencia_diaria.py"}
