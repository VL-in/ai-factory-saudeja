import sys
from pathlib import Path

import pandas as pd
import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import observabilidade
import telemetria

_HORAS_DA_GRADE = (8, 9, 10, 11, 13, 14, 15, 16, 17)


def _descartar_evento(**evento):
    """Destino nulo de `eventos_app` durante os testes."""


@pytest.fixture(autouse=True)
def observabilidade_sem_destino_real():
    """Nenhum teste grava em `eventos_app` de verdade (ADR-006).

    Sem isto, qualquer teste que faça uma predição enfileira um evento e o
    worker de `src/observabilidade.py` cai no destino default -- que resolve
    `SUPABASE_URL`/`SUPABASE_SECRET_KEY`, e o `.env` local de quem desenvolve
    aponta para o projeto REAL: a suíte rápida sujaria a tabela de produção e
    ainda deixaria um client conectado em cache para os testes seguintes.
    Quem precisa observar o que foi registrado troca o destino por um espião
    próprio (tests/test_observabilidade.py); o teardown aqui devolve o nulo.
    """
    observabilidade.definir_escritor(_descartar_evento)
    yield
    observabilidade.flush(timeout=2)
    observabilidade.definir_escritor(_descartar_evento)


@pytest.fixture
def eventos_capturados(monkeypatch):
    """Substitui o destino de gravação por um espião em memória -- prova o que
    é registrado sem precisar de Supabase de pé (mesma ideia do cliente de
    mensageria espião de test_job_inferencia.py).

    Liga a observabilidade explicitamente: o ci.yml exporta
    `OBSERVABILIDADE_ATIVA=false` no workflow inteiro (CI não é tráfego de
    produto), e sem isto o espião nunca recebe nada no runner -- os testes
    passavam na máquina local e quebravam no primeiro CI real."""
    monkeypatch.setenv("OBSERVABILIDADE_ATIVA", "true")
    capturados = []

    def _escritor(**evento):
        capturados.append(evento)

    # Drena o que outro teste possa ter deixado na fila antes de trocar o
    # destino -- senão um evento alheio cairia no espião deste teste.
    observabilidade.flush()
    observabilidade.definir_escritor(_escritor)
    yield capturados
    observabilidade.definir_escritor(None)


@pytest.fixture(autouse=True)
def telemetria_sem_exportar(monkeypatch):
    """Nenhum teste exporta telemetria para o Grafana Cloud.

    Mesmo motivo da fixture de cima: `config_projeto` carrega o `.env` local
    no import, e o `.env` de quem desenvolve pode ter o endpoint OTLP real.
    Sem endpoint, `configurar_telemetria` é no-op; quem precisa observar o que
    sairia (tests/test_telemetria.py) define o endpoint e troca os exportadores
    por exportadores em memória. O teardown devolve o módulo ao estado inicial.
    """
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    telemetria._reiniciar()
    yield
    telemetria._reiniciar()


@pytest.fixture(autouse=True)
def canario_sem_desligamento_do_env(monkeypatch):
    """`CANARIO_DESLIGADO` do `.env` local não vaza para a suíte. Com ela
    definida, os testes do canário que esperam o braço ligado falhavam na
    máquina de quem desenvolve e passavam no CI, que não tem `.env`. Quem testa
    o desligamento define a variável no próprio teste."""
    monkeypatch.delenv("CANARIO_DESLIGADO", raising=False)


CHAVE_DO_IDENTIFICADOR_DE_TESTE = "i" * 32


@pytest.fixture(autouse=True)
def chave_do_identificador_de_teste(monkeypatch):
    """O cadastro não roda sem `PACIENTE_CHAVE_IDENTIFICADOR` (ADR-011). Uma
    chave fixa de teste, no lugar da que o `.env` local possa ter: a suíte
    nunca calcula identificador com a chave real. Quem testa a chave ausente
    a remove no próprio teste."""
    monkeypatch.setenv("PACIENTE_CHAVE_IDENTIFICADOR", CHAVE_DO_IDENTIFICADOR_DE_TESTE)


class TelemetriaEmMemoria:
    """O que teria saído por OTLP, lido em memória."""

    def __init__(self, spans, leitor, logs):
        self.spans_exportados = spans
        self.leitor = leitor
        self.logs = logs

    def spans(self) -> list:
        telemetria.forcar_envio(timeout=5)
        return list(self.spans_exportados.get_finished_spans())

    def pontos(self, nome: str) -> list[tuple[dict, float]]:
        """(atributos, valor) de cada série da métrica `nome`. Valor é a soma
        do contador ou a contagem de medições do histograma."""
        dados = self.leitor.get_metrics_data()
        pontos = []
        for recurso in dados.resource_metrics if dados else []:
            for escopo in recurso.scope_metrics:
                for metrica in escopo.metrics:
                    if metrica.name != nome:
                        continue
                    for ponto in metrica.data.data_points:
                        valor = getattr(ponto, "value", None)
                        pontos.append(
                            (dict(ponto.attributes), valor if valor is not None else ponto.count)
                        )
        return pontos


@pytest.fixture
def telemetria_em_memoria(monkeypatch):
    """Telemetria LIGADA, com exportadores em memória no lugar dos OTLP: nada
    sai do processo, e o teste lê spans, métricas e logs que teriam saído."""
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setenv("OBSERVABILIDADE_ATIVA", "true")
    em_memoria = TelemetriaEmMemoria(
        InMemorySpanExporter(), InMemoryMetricReader(), InMemoryLogRecordExporter()
    )
    monkeypatch.setattr(
        telemetria,
        "_criar_exportadores",
        lambda: (em_memoria.spans_exportados, em_memoria.leitor, em_memoria.logs),
    )
    assert telemetria.configurar_telemetria(telemetria.SERVICO_UI)
    return em_memoria


@pytest.fixture
def df_consultas():
    """Amostra sintética com o mesmo schema de data/consultas-historicas.csv."""
    return pd.DataFrame(
        {
            "id_consulta": [1, 2, 3, 4, 5, 6],
            "id_paciente": ["P1", "P2", "P3", "P4", "P5", "P6"],
            "idade": [52, 22, 34, 61, 45, 29],
            "sexo": ["F", "F", "M", "M", "F", "M"],
            "especialidade": [
                "cardiologia",
                "clinica geral",
                "cardiologia",
                "dermatologia",
                "clinica geral",
                "dermatologia",
            ],
            "distancia_km": [7.4, 11.3, 3.2, 15.0, 5.5, 9.8],
            "dias_entre_agendamento_consulta": [14, 65, 7, 30, 21, 3],
            "historico_noshow": [2, 1, 0, 3, 0, 1],
            "no_show": [0, 0, 1, 0, 1, 0],
            "data_hora_agendada": [
                "2026-01-05 09:00:00",  # segunda
                "2026-01-06 14:30:00",  # terca
                "2026-01-09 18:00:00",  # sexta, fim de expediente (no_show=1)
                "2026-01-08 10:00:00",  # quinta
                "2026-01-09 17:30:00",  # sexta, fim de expediente (no_show=1)
                "2026-01-10 08:30:00",  # sabado
            ],
        }
    )


@pytest.fixture
def df_consultas_smote():
    """Amostra maior que df_consultas -- SMOTE-NC (k_neighbors=3) precisa de
    amostras suficientes na classe minoritária do fold de treino."""
    n = 40
    especialidades = ["cardiologia", "clinica geral", "dermatologia"]
    return pd.DataFrame(
        {
            "id_consulta": range(1, n + 1),
            "id_paciente": [f"P{i}" for i in range(1, n + 1)],
            "idade": [20 + (i % 50) for i in range(n)],
            "sexo": ["F" if i % 2 == 0 else "M" for i in range(n)],
            "especialidade": [especialidades[i % 3] for i in range(n)],
            "distancia_km": [round(2 + (i % 15) * 1.3, 1) for i in range(n)],
            "dias_entre_agendamento_consulta": [1 + (i % 60) for i in range(n)],
            "historico_noshow": [i % 4 for i in range(n)],
            "no_show": [1 if i % 3 == 0 else 0 for i in range(n)],
            # Seg a sex (05-09/01/2026) e só horários da grade da clínica: o
            # contrato de features recusa domingo e o almoço, e o
            # `preprocess` o aplica como defesa em profundidade.
            "data_hora_agendada": [
                f"2026-01-{5 + (i % 5):02d} {_HORAS_DA_GRADE[i % len(_HORAS_DA_GRADE)]:02d}:00:00"
                for i in range(n)
            ],
        }
    )


@pytest.fixture
def csv_consultas(tmp_path, df_consultas):
    caminho = tmp_path / "consultas-historicas.csv"
    df_consultas.to_csv(caminho, index=False)
    return caminho
