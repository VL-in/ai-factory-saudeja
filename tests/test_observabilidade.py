"""
SaúdeJá — testes da observabilidade de aplicação (Passo 8.5, ADR-006).

Três propriedades são as que realmente importam aqui, e cada uma tem teste
próprio:

1. o p95 publicado no pitch (SLO §2) bate com um conjunto de latências
   conhecidas -- um percentil errado é pior que nenhum, porque parece medição;
2. falha ao gravar evento **não derruba** a predição nem o job (observabilidade
   quebrada degrada, não interrompe -- mesma filosofia do `ErroEnvioInfobip` do
   Passo 7);
3. nenhum dado de paciente entra em `eventos_app`. O grep de `nome de coluna`
   de `test_coerencia_repo.py` não alcança um jsonb livre, então a guarda tem
   que ser aqui, sobre a allowlist de `detalhe` e sobre os call sites de `src/`.

Os testes de round-trip contra o Supabase local seguem a convenção de
`test_db.py`/`test_job_inferencia.py` (`supabase start`, marcados `integracao`).
"""
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import inference
import observabilidade

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = "data/model.pkl"


@pytest.fixture
def eventos_capturados():
    """Substitui o destino de gravação por um espião em memória -- prova o que
    é registrado sem precisar de Supabase de pé (mesma ideia do cliente de
    mensageria espião de test_job_inferencia.py)."""
    capturados = []

    def _escritor(**evento):
        capturados.append(evento)

    # Drena o que outro teste possa ter deixado na fila antes de trocar o
    # destino -- senão um evento alheio cairia no espião deste teste.
    observabilidade.flush()
    observabilidade.definir_escritor(_escritor)
    yield capturados
    observabilidade.definir_escritor(None)


@pytest.fixture
def escritor_que_falha():
    """Destino permanentemente quebrado: o caminho principal (predição, job)
    tem que continuar funcionando por cima dele."""
    chamadas = []

    def _escritor(**evento):
        chamadas.append(evento)
        raise RuntimeError("Supabase fora do ar")

    observabilidade.definir_escritor(_escritor)
    yield chamadas
    observabilidade.definir_escritor(None)


# --- percentil: função pura, é o número que vai para o pitch -------------------


@pytest.mark.parametrize(
    "valores, p, esperado",
    [
        ([10] * 100, 95, 10.0),
        (list(range(1, 101)), 95, 95.05),  # interpolação linear, mesma definição do numpy
        ([5], 95, 5.0),
        ([], 95, None),
    ],
)
def test_percentil_bate_com_conjunto_de_latencias_conhecido(valores, p, esperado):
    resultado = observabilidade.percentil(valores, p)

    if esperado is None:
        assert resultado is None
    else:
        assert resultado == pytest.approx(esperado)


def test_percentil_ignora_duracao_ausente():
    """Evento sem `duracao_ms` (ex.: agendamento malformado, que falha antes de
    haver o que cronometrar) não pode contar como latência zero e puxar o p95
    para baixo."""
    assert observabilidade.percentil([100, None, 200], 50) == pytest.approx(150.0)


def test_inicio_da_janela_conta_duracao_nao_data_civil():
    agora = datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc)

    assert observabilidade.inicio_da_janela(24, agora=agora) == agora - timedelta(hours=24)


# --- PII: a allowlist do `detalhe` é a única guarda possível num jsonb ---------


def test_detalhe_descarta_chave_fora_da_allowlist():
    limpo = observabilidade.sanitizar_detalhe(
        {"rota": "/predict", "telefone": "5511987654321", "id_paciente_externo": "abc"}
    )

    assert limpo == {"rota": "/predict"}


def test_allowlist_do_detalhe_nao_tem_chave_de_identificacao():
    """Guarda contra alguém acrescentar 'telefone'/'motivo' à allowlist sem
    perceber que isso abre a porta para PII em `eventos_app`."""
    proibidas = ("nome", "cpf", "email", "telefone", "paciente", "motivo", "mensagem")

    for chave in observabilidade.CHAVES_DETALHE_PERMITIDAS:
        assert not any(p in chave for p in proibidas), f"chave suspeita na allowlist: {chave}"


def test_call_sites_de_src_nao_passam_dado_de_paciente_no_detalhe():
    """Checagem estática sobre `src/`, no mesmo espírito de
    `test_coerencia_repo.py`: nenhuma chamada a `registrar_evento`/`medir`
    referencia campo de paciente no `detalhe`. Complementa (não substitui) a
    allowlist em runtime -- esta falha no CI, antes de rodar."""
    proibidos = re.compile(r"(telefone|id_paciente|cpf|data_nascimento)")

    for arquivo in (REPO_ROOT / "src").rglob("*.py"):
        texto = arquivo.read_text(encoding="utf-8")
        for bloco in re.findall(r"detalhe=\{[^}]*\}", texto, flags=re.DOTALL):
            assert not proibidos.search(bloco), f"{arquivo.name}: detalhe com dado de paciente"


def test_evento_registrado_carrega_so_o_nome_da_classe_da_excecao(eventos_capturados):
    """`str(exc)` de uma falha da Infobip ecoa o payload enviado, telefone
    incluído -- por isso `medir` grava a classe, não a mensagem."""

    class ErroComTelefoneNaMensagem(Exception):
        pass

    with pytest.raises(ErroComTelefoneNaMensagem), observabilidade.medir(
        observabilidade.TIPO_PREDICAO
    ):
        raise ErroComTelefoneNaMensagem("falhou para 5511987654321")

    observabilidade.flush()
    assert eventos_capturados[0]["detalhe"] == {"excecao": "ErroComTelefoneNaMensagem"}
    assert "5511987654321" not in json.dumps(eventos_capturados[0])


# --- degradação: quebrar a métrica não pode quebrar o produto -----------------


def test_falha_ao_gravar_evento_nao_derruba_quem_registrou(escritor_que_falha):
    assert observabilidade.registrar_evento(observabilidade.TIPO_PREDICAO) is True

    observabilidade.flush()
    assert escritor_que_falha, "o escritor deveria ter sido chamado (e falhado) em background"


def test_predicao_em_processo_continua_funcionando_com_observabilidade_quebrada(
    escritor_que_falha,
):
    """O caminho DEFAULT de produção (ADR-005 b) sob um destino de métrica
    quebrado: a predição tem que sair igual."""
    from ui import logic

    _, mapa_especialidade = inference.carregar_modelo(MODEL_PATH)
    payload = logic.montar_payload(
        idade=45,
        sexo="F",
        especialidade=sorted(mapa_especialidade)[0],
        distancia_km=5.5,
        dias_entre_agendamento_consulta=14,
        historico_noshow=1,
        data_consulta=datetime(2026, 1, 9).date(),
        hora_consulta=datetime(2026, 1, 9, 18, 0).time(),
    )

    resultado = logic.ClientePredicaoEmProcesso().predizer(payload)

    assert 0.0 <= resultado.probabilidade <= 1.0
    assert resultado.explicacao  # SLO §4 intacto


def test_observabilidade_desligada_nao_registra_nada(eventos_capturados, monkeypatch):
    monkeypatch.setenv("OBSERVABILIDADE_ATIVA", "false")

    assert observabilidade.registrar_evento(observabilidade.TIPO_PREDICAO) is False
    assert eventos_capturados == []


def test_registro_nao_bloqueia_quando_a_fila_enche(monkeypatch, eventos_capturados):
    """Fila cheia (destino lento) descarta em vez de segurar o caminho crítico
    -- a observabilidade não pode entrar na latência que ela mede."""
    import queue

    cheia = queue.Queue(maxsize=1)
    cheia.put({})
    monkeypatch.setattr(observabilidade, "_garantir_worker", lambda: cheia)

    assert observabilidade.registrar_evento(observabilidade.TIPO_PREDICAO) is False


# --- instrumentação da API: mede /predict, ignora a sonda de /health ----------


def test_predict_registra_evento_de_latencia(eventos_capturados):
    from api.main import app

    _, mapa_especialidade = inference.carregar_modelo(MODEL_PATH)
    payload = {
        "idade": 45,
        "sexo": "F",
        "especialidade": sorted(mapa_especialidade)[0],
        "distancia_km": 5.5,
        "dias_entre_agendamento_consulta": 14,
        "historico_noshow": 1,
        "data_hora_agendada": "2026-01-05T09:00:00",
    }

    with TestClient(app) as client:
        client.post("/predict", json=payload)
    observabilidade.flush()

    evento = next(e for e in eventos_capturados if e["origem"] == observabilidade.ORIGEM_API)
    assert evento["tipo"] == observabilidade.TIPO_PREDICAO
    assert evento["status"] == observabilidade.STATUS_OK
    assert evento["duracao_ms"] >= 0
    assert evento["model_version"]
    assert evento["detalhe"] == {"rota": "/predict", "status_http": 200}


def test_health_nao_gera_evento(eventos_capturados):
    """A sonda externa de uptime bate em /health de minutos em minutos (ADR-006):
    registrar cada batida encheria a tabela de ruído e ocuparia o free tier sem
    dizer nada sobre latência de predição."""
    from api.main import app

    with TestClient(app) as client:
        client.get("/health")
    observabilidade.flush()

    assert eventos_capturados == []


def test_payload_invalido_registra_erro_com_status_http(eventos_capturados):
    """4xx conta como requisição com erro, mas o `status_http` preservado
    permite distinguir "dado recusado" de indisponibilidade (SLO §1 fala de
    5xx)."""
    from api.main import app

    with TestClient(app) as client:
        client.post("/predict", json={"idade": -1})
    observabilidade.flush()

    evento = eventos_capturados[0]
    assert evento["status"] == observabilidade.STATUS_ERRO
    assert evento["detalhe"]["status_http"] == 422


# --- round-trip contra o Supabase local (integracao) ---------------------------


def _status_supabase_local() -> dict:
    saida = subprocess.run(
        ["supabase", "status", "-o", "json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(saida.stdout)


@pytest.fixture
def db(monkeypatch):
    status = _status_supabase_local()
    monkeypatch.setenv("SUPABASE_URL", status["API_URL"])
    monkeypatch.setenv("SUPABASE_SECRET_KEY", status["SECRET_KEY"])

    import db.client as client_module

    client_module._cliente = None
    client = client_module.obter_client()

    yield client

    client.table("eventos_app").delete().neq(
        "id", "00000000-0000-0000-0000-000000000000"
    ).execute()
    client_module._cliente = None


@pytest.mark.integracao
def test_registrar_evento_faz_round_trip_no_supabase(db):
    import db.repositories as repositories

    observabilidade.definir_escritor(repositories.inserir_evento_app)
    try:
        observabilidade.registrar_evento(
            tipo=observabilidade.TIPO_PREDICAO,
            origem=observabilidade.ORIGEM_JOB,
            duracao_ms=123,
            model_version="teste",
            detalhe={"rota": "/predict", "telefone": "5511987654321"},
        )
        assert observabilidade.flush(timeout=10)
    finally:
        observabilidade.definir_escritor(None)

    linhas = db.table("eventos_app").select("*").execute().data
    assert len(linhas) == 1
    assert linhas[0]["duracao_ms"] == 123
    assert linhas[0]["origem"] == "job"
    # O telefone é barrado antes de entrar na fila, não confiando no schema.
    assert linhas[0]["detalhe"] == {"rota": "/predict"}


@pytest.mark.integracao
def test_purga_remove_so_eventos_fora_da_retencao(db):
    import db.repositories as repositories

    agora = datetime.now(tz=timezone.utc)
    db.table("eventos_app").insert(
        [
            {
                "criado_em": (agora - timedelta(days=200)).isoformat(),
                "tipo": "predicao",
                "origem": "job",
                "status": "ok",
            },
            {
                "criado_em": agora.isoformat(),
                "tipo": "predicao",
                "origem": "job",
                "status": "ok",
            },
        ]
    ).execute()

    removidas = repositories.purgar_eventos_app(agora - timedelta(days=90))

    assert removidas == 1
    assert len(db.table("eventos_app").select("id").execute().data) == 1


@pytest.mark.integracao
def test_resumo_de_observabilidade_agrega_o_que_a_aba_mostra(db):
    import db.repositories as repositories
    from ui import logic

    agora = datetime.now(tz=timezone.utc)
    db.table("eventos_app").insert(
        [
            {
                "criado_em": agora.isoformat(),
                "tipo": "predicao",
                "origem": "processo",
                "status": "ok",
                "duracao_ms": ms,
            }
            for ms in (10, 20, 30, 40, 1000)
        ]
        + [
            {
                "criado_em": agora.isoformat(),
                "tipo": "job_d2",
                "origem": "job",
                "status": "ok",
                "duracao_ms": 900,
            }
        ]
    ).execute()

    resumo = logic.resumo_observabilidade(janela_horas=24)

    assert resumo["predicoes"] == 5
    assert resumo["p95_ms"] == pytest.approx(
        observabilidade.percentil([10, 20, 30, 40, 1000], 95)
    )
    assert resumo["por_origem"] == {"processo": 5}
    assert resumo["ultimo_job_d2"] is not None
    assert resumo["truncado"] is False
    assert repositories.buscar_eventos_app(agora - timedelta(hours=1))
