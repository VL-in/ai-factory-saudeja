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
    assert vistas > 0
