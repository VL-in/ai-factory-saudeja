"""
SaúdeJá — testes do job de inferência diária D-2 (Passo 6 do plano de
implementação). Mesma filosofia de tests/test_db.py: Supabase CLI local
(`supabase start`), sem mock pesado, marcado `integracao` (pytest.ini).

Usa o data/model.pkl real (já versionado via DVC, sem re-treinar -- mesma
filosofia de tests/test_inference.py/test_api.py) e um cliente de mensageria
"espião" (implementa a mesma interface do stub, mas grava as chamadas) para
verificar que o disparo só acontece para agendamentos de alto risco, sem
depender de mock de rede.
"""
import json
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

import inference
from config_projeto import fuso_da_clinica, hoje_na_clinica

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = "data/model.pkl"
HORA_DA_CONSULTA = time(10, 0)


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

    zero = "00000000-0000-0000-0000-000000000000"
    client.table("agendamentos").delete().neq("id", zero).execute()
    client.table("pacientes").delete().neq("id", zero).execute()
    client_module._cliente = None


class _ClienteMensageriaEspiao:
    """Mesma assinatura de messaging.client.MessagingClient, mas grava as
    chamadas em vez de só simular -- prova que o job só aciona o envio para
    quem cruzou o threshold, sem inspecionar rede/mock pesado."""

    def __init__(self):
        self.chamadas = []

    def enviar_lembrete(self, id_paciente_externo: str, mensagem: str) -> dict:
        self.chamadas.append(id_paciente_externo)
        return {"status": "simulado", "id_paciente_externo": id_paciente_externo}


def _payload(idade, sexo, especialidade, distancia_km, dias, historico_noshow, data_hora):
    return {
        "idade": idade,
        "sexo": sexo,
        "especialidade": especialidade,
        "distancia_km": distancia_km,
        "dias_entre_agendamento_consulta": dias,
        "historico_noshow": historico_noshow,
        "data_hora_agendada": data_hora,
    }


def _probabilidade_real(payload: dict) -> float:
    model, mapa_esp = inference.carregar_modelo(MODEL_PATH)
    X = inference.construir_features(payload, mapa_esp)
    return float(inference.predizer(model, X)[0])


def _criar_agendamento_d2(repositories, id_externo: str, payload: dict):
    paciente = repositories.inserir_paciente(
        id_paciente_externo=id_externo, idade=payload["idade"], sexo=payload["sexo"]
    )
    agendamento = repositories.inserir_agendamento(
        id_paciente=paciente["id"],
        especialidade=payload["especialidade"],
        distancia_km=payload["distancia_km"],
        data_hora_agendada=payload["data_hora_agendada"],
        dias_entre_agendamento_consulta=payload["dias_entre_agendamento_consulta"],
        historico_noshow=payload["historico_noshow"],
    )
    return agendamento


@pytest.mark.integracao
def test_job_processa_fila_d2_grava_predicoes_e_dispara_so_para_alto_risco(db, monkeypatch):
    import db.repositories as repositories
    import jobs.inferencia_diaria as job

    _, mapa_esp = inference.carregar_modelo(MODEL_PATH)
    especialidade = sorted(mapa_esp)[0]
    data_hora_d2 = datetime.combine(
        hoje_na_clinica() + timedelta(days=2), HORA_DA_CONSULTA, tzinfo=fuso_da_clinica()
    )

    payload_a = _payload(45, "F", especialidade, 5.5, 14, 1, data_hora_d2)
    payload_b = _payload(30, "M", especialidade, 3.0, 30, 0, data_hora_d2)
    prob_a = _probabilidade_real(payload_a)
    prob_b = _probabilidade_real(payload_b)
    if prob_a == prob_b:
        pytest.skip("modelo real deu a mesma probabilidade para os dois payloads sintéticos")

    payload_alto, payload_baixo = (
        (payload_a, payload_b) if prob_a > prob_b else (payload_b, payload_a)
    )
    threshold_no_meio = (prob_a + prob_b) / 2
    monkeypatch.setitem(inference.PARAMS, "decision", {"threshold": threshold_no_meio})

    agendamento_alto = _criar_agendamento_d2(repositories, "EXT-JOB-ALTO", payload_alto)
    agendamento_baixo = _criar_agendamento_d2(repositories, "EXT-JOB-BAIXO", payload_baixo)

    espiao = _ClienteMensageriaEspiao()
    resultado = job.processar_dia(hoje_na_clinica(), cliente_mensageria=espiao)

    assert resultado["agendamentos_encontrados"] == 2
    assert resultado["predicoes_gravadas"] == 2
    assert resultado["mensagens_disparadas"] == 1
    assert resultado["erros"] == []
    assert espiao.chamadas == ["EXT-JOB-ALTO"]

    predicoes = db.table("predicoes").select("*").execute().data
    por_agendamento = {p["id_agendamento"]: p for p in predicoes}
    assert por_agendamento[agendamento_alto["id"]]["classe_prevista"] == 1
    assert por_agendamento[agendamento_baixo["id"]]["classe_prevista"] == 0
    assert por_agendamento[agendamento_alto["id"]]["explicacao_shap"]
    assert por_agendamento[agendamento_baixo["id"]]["explicacao_shap"]

    mensagens = db.table("mensagens_disparadas").select("*").execute().data
    status_por_agendamento = {m["id_agendamento"]: m["status_envio"] for m in mensagens}
    assert status_por_agendamento[agendamento_alto["id"]] == "enviado"
    assert status_por_agendamento[agendamento_baixo["id"]] == "nao_enviado"


@pytest.mark.integracao
def test_job_nao_reprocessa_agendamento_ja_com_predicao(db):
    import db.repositories as repositories
    import jobs.inferencia_diaria as job

    _, mapa_esp = inference.carregar_modelo(MODEL_PATH)
    especialidade = sorted(mapa_esp)[0]
    data_hora_d2 = datetime.combine(
        hoje_na_clinica() + timedelta(days=2), HORA_DA_CONSULTA, tzinfo=fuso_da_clinica()
    )
    payload = _payload(50, "F", especialidade, 8.0, 20, 2, data_hora_d2)
    _criar_agendamento_d2(repositories, "EXT-JOB-REPETIDO", payload)

    espiao = _ClienteMensageriaEspiao()
    primeira = job.processar_dia(hoje_na_clinica(), cliente_mensageria=espiao)
    segunda = job.processar_dia(hoje_na_clinica(), cliente_mensageria=espiao)

    assert primeira["predicoes_gravadas"] == 1
    assert segunda["agendamentos_encontrados"] == 0
    assert segunda["predicoes_gravadas"] == 0


@pytest.mark.integracao
def test_job_sem_agendamentos_d2_nao_toca_mensageria(db):
    import jobs.inferencia_diaria as job

    espiao = _ClienteMensageriaEspiao()
    resultado = job.processar_dia(hoje_na_clinica(), cliente_mensageria=espiao)

    assert resultado == {
        "agendamentos_encontrados": 0,
        "predicoes_gravadas": 0,
        "mensagens_disparadas": 0,
        "erros": [],
    }
    assert espiao.chamadas == []
