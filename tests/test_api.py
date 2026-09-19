"""
SaúdeJá — testes da API FastAPI (Passo 3 do plano de implementação).
Usa o data/model.pkl real (já versionado via DVC, sem re-treinar) --
mesma filosofia de tests/test_inference.py e tests/test_explain.py: serviço
real e barato em vez de mock pesado.
"""
import pytest
from fastapi.testclient import TestClient

import inference
from api.main import app

MODEL_PATH = "data/model.pkl"


@pytest.fixture(scope="module")
def especialidade_valida():
    """Uma especialidade que existe de verdade no mapa fixado no modelo
    treinado -- evita que o teste dependa de um valor hardcoded que pode
    não bater com o dataset atual."""
    _, mapa_especialidade = inference.carregar_modelo(MODEL_PATH)
    return sorted(mapa_especialidade)[0]


@pytest.fixture
def payload_valido(especialidade_valida):
    return {
        "idade": 45,
        "sexo": "F",
        "especialidade": especialidade_valida,
        "distancia_km": 5.5,
        "dias_entre_agendamento_consulta": 14,
        "historico_noshow": 1,
        "data_hora_agendada": "2026-01-05T09:00:00",
    }


def test_health_retorna_200_com_model_version():
    with TestClient(app) as client:
        resposta = client.get("/health")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "ok"
    assert corpo["model_version"]


def test_predict_valido_retorna_probabilidade_e_explicacao(payload_valido):
    with TestClient(app) as client:
        resposta = client.post("/predict", json=payload_valido)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert 0.0 <= corpo["probabilidade"] <= 1.0
    assert corpo["classe_prevista"] in (0, 1)
    assert corpo["explicacao"]  # SLO §4 -- 100% das predições com explicação
    assert corpo["explicacao_texto"] is None  # plug do LLM ainda inativo (Passo 13)
    assert corpo["model_version"]


def test_predict_especialidade_desconhecida_retorna_422_nao_500(payload_valido):
    payload_valido["especialidade"] = "especialidade-nunca-vista"

    with TestClient(app) as client:
        resposta = client.post("/predict", json=payload_valido)

    assert resposta.status_code == 422
    assert "especialidade-nunca-vista" in resposta.json()["detail"]


@pytest.mark.parametrize("campo, valor_invalido", [("idade", -1), ("sexo", "X")])
def test_predict_payload_invalido_retorna_422(payload_valido, campo, valor_invalido):
    payload_valido[campo] = valor_invalido

    with TestClient(app) as client:
        resposta = client.post("/predict", json=payload_valido)

    assert resposta.status_code == 422


def test_predict_classe_prevista_respeita_threshold_de_params(payload_valido, monkeypatch):
    """classe_prevista = probabilidade >= threshold_usado, lido de
    params.yaml no startup -- threshold extremo em cada direção torna o
    teste determinístico independente da probabilidade real prevista."""
    monkeypatch.setitem(inference.PARAMS, "decision", {"threshold": 0.0})
    with TestClient(app) as client:
        resposta_threshold_baixo = client.post("/predict", json=payload_valido)

    monkeypatch.setitem(inference.PARAMS, "decision", {"threshold": 1.01})
    with TestClient(app) as client:
        resposta_threshold_alto = client.post("/predict", json=payload_valido)

    assert resposta_threshold_baixo.json()["classe_prevista"] == 1
    assert resposta_threshold_alto.json()["classe_prevista"] == 0
