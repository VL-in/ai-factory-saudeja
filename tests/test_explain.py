import json

import pytest

import explain
import inference


def _payload_da_linha(df, i):
    linha = df.iloc[i]
    return {
        "idade": linha["idade"],
        "sexo": linha["sexo"],
        "especialidade": linha["especialidade"],
        "distancia_km": linha["distancia_km"],
        "dias_entre_agendamento_consulta": linha["dias_entre_agendamento_consulta"],
        "historico_noshow": linha["historico_noshow"],
        "data_hora_agendada": linha["data_hora_agendada"],
    }


@pytest.fixture
def explicacao_real(df_consultas):
    """Carrega o data/model.pkl real (sem re-treinar) e monta X para a
    primeira linha do fixture cuja especialidade exista no mapa do modelo."""
    model, mapa_esp = inference.carregar_modelo("data/model.pkl")
    for i in range(len(df_consultas)):
        payload = _payload_da_linha(df_consultas, i)
        if payload["especialidade"] in mapa_esp:
            break
    else:
        pytest.skip("nenhuma especialidade do fixture existe no mapa do modelo real")

    X = inference.construir_features(payload, mapa_esp, features_temporais=True)
    explainer = explain.construir_explicador(model)
    return model, explainer, X


def test_explicar_aditividade_bate_com_margem_prevista(explicacao_real):
    """Prova matemática (não opinião): soma(shap_values) + expected_value
    tem que reproduzir a margem (log-odds) prevista pelo modelo."""
    model, explainer, X = explicacao_real

    contribuicoes = explain.explicar(explainer, X)

    soma = sum(c["contribuicao"] for c in contribuicoes)
    margem_prevista = model.predict(X, raw_score=True)[0]

    assert soma + explainer.expected_value == pytest.approx(margem_prevista, abs=1e-6)


def test_explicar_retorna_uma_contribuicao_por_feature(explicacao_real):
    _, explainer, X = explicacao_real

    contribuicoes = explain.explicar(explainer, X)

    assert len(contribuicoes) == X.shape[1]
    assert {c["feature"] for c in contribuicoes} == set(X.columns)


def test_explicar_ordena_por_abs_contribuicao_desc(explicacao_real):
    _, explainer, X = explicacao_real

    contribuicoes = explain.explicar(explainer, X)

    magnitudes = [abs(c["contribuicao"]) for c in contribuicoes]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_explicar_e_json_serializavel(explicacao_real):
    _, explainer, X = explicacao_real

    contribuicoes = explain.explicar(explainer, X)

    serializado = json.dumps(contribuicoes)
    assert json.loads(serializado) == contribuicoes


def test_explicador_llm_desativado_nao_chama_rede(monkeypatch):
    """O plug inativo nunca deve tentar rede -- qualquer tentativa de
    socket levantaria aqui antes de chegar numa chamada HTTP de verdade."""
    import socket

    def _bloqueado(*args, **kwargs):
        raise AssertionError("ExplicadorLLMDesativado tentou abrir socket de rede")

    monkeypatch.setattr(socket.socket, "connect", _bloqueado)

    resultado = explain.ExplicadorLLMDesativado().explicar_em_texto(
        contribuicoes=[{"feature": "idade", "contribuicao": 0.3}],
        contexto={"id_agendamento": 1},
    )

    assert resultado is None


def test_explicador_llm_e_interface_abstrata():
    with pytest.raises(TypeError):
        explain.ExplicadorLLM()
