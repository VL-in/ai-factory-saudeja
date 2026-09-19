import pandas as pd
import pytest

import inference
import preprocess


def _payload_da_linha(df, i):
    """Monta o payload cru (dict) que a API/job (Passos 3/6) receberiam,
    a partir de uma linha do fixture já no schema de consultas-historicas.csv."""
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


def test_carregar_modelo_le_artefato_real_sem_retreinar():
    model, mapa_especialidade = inference.carregar_modelo("data/model.pkl")
    assert hasattr(model, "predict_proba")
    assert isinstance(mapa_especialidade, dict) and len(mapa_especialidade) > 0


def test_aplicar_mapa_especialidade_mapeia_valores_conhecidos(df_consultas):
    _, _, mapa_esp = preprocess.preprocessar(df_consultas.copy())
    df = df_consultas[["especialidade"]].copy()

    resultado = inference.aplicar_mapa_especialidade(df, mapa_esp)

    esperado = df_consultas["especialidade"].map(mapa_esp)
    assert list(resultado["especialidade"]) == list(esperado)


def test_aplicar_mapa_especialidade_desconhecida_levanta_erro_claro(df_consultas):
    _, _, mapa_esp = preprocess.preprocessar(df_consultas.copy())
    df = pd.DataFrame({"especialidade": ["neurologia"]})

    with pytest.raises(inference.EspecialidadeDesconhecidaError, match="neurologia"):
        inference.aplicar_mapa_especialidade(df, mapa_esp)


@pytest.mark.parametrize("features_temporais", [False, True])
def test_construir_features_bate_com_preprocessar_para_mesma_linha(
    df_consultas, features_temporais, monkeypatch
):
    """Teste de paridade treino-serving: construir_features() deve produzir
    exatamente as mesmas colunas/valores que preprocess.preprocessar() para
    a mesma linha, usando o mapa fit sobre o dataset inteiro. Detecta
    training-serving skew automaticamente caso os dois caminhos divirjam.
    A flag de features temporais é fixada explicitamente (independente do
    valor atual em params.yaml) para o teste não depender de configuração
    externa."""
    monkeypatch.setitem(preprocess.PARAMS, "features", {"temporais": features_temporais})
    X_completo, _, mapa_esp = preprocess.preprocessar(df_consultas.copy())

    payload = _payload_da_linha(df_consultas, 0)
    X_inferencia = inference.construir_features(
        payload, mapa_esp, features_temporais=features_temporais
    )

    esperado = X_completo.iloc[[0]].reset_index(drop=True)
    obtido = X_inferencia.reset_index(drop=True)

    assert list(obtido.columns) == list(esperado.columns)
    pd.testing.assert_frame_equal(obtido, esperado, check_dtype=False)


def test_construir_features_usa_features_temporais_de_params_por_padrao(df_consultas, monkeypatch):
    monkeypatch.setitem(inference.PARAMS, "features", {"temporais": True})
    _, _, mapa_esp = preprocess.preprocessar(df_consultas.copy())

    payload = _payload_da_linha(df_consultas, 0)
    X = inference.construir_features(payload, mapa_esp)

    assert {"dia_de_semana", "horario"} <= set(X.columns)


def test_construir_features_especialidade_desconhecida_propaga_erro(df_consultas):
    _, _, mapa_esp = preprocess.preprocessar(df_consultas.copy())
    payload = _payload_da_linha(df_consultas, 0)
    payload["especialidade"] = "especialidade-nunca-vista"

    with pytest.raises(inference.EspecialidadeDesconhecidaError):
        inference.construir_features(payload, mapa_esp, features_temporais=False)


def test_predizer_retorna_probabilidade_entre_0_e_1_com_modelo_real(df_consultas):
    model, mapa_esp = inference.carregar_modelo("data/model.pkl")
    payload = _payload_da_linha(df_consultas, 0)

    X = inference.construir_features(payload, mapa_esp, features_temporais=True)
    probabilidades = inference.predizer(model, X)

    assert len(probabilidades) == 1
    assert 0.0 <= probabilidades[0] <= 1.0


def test_pipeline_ponta_a_ponta_sem_retreinar(df_consultas):
    """Carrega o data/model.pkl real (já versionado via DVC) e roda o
    caminho completo payload -> features -> predição, sem chamar train.py."""
    model, mapa_esp = inference.carregar_modelo("data/model.pkl")

    for i in range(len(df_consultas)):
        payload = _payload_da_linha(df_consultas, i)
        if payload["especialidade"] not in mapa_esp:
            # o fixture pode ter especialidade fora do mapa do modelo real
            continue
        X = inference.construir_features(payload, mapa_esp, features_temporais=True)
        probabilidade = inference.predizer(model, X)[0]
        assert 0.0 <= probabilidade <= 1.0
