import json

import pandas as pd
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


# --- fronteira de PII para fora do sistema (ADR-007) --------------------------


class _ExplicadorEspiao(explain.ExplicadorLLM):
    """Implementação que só registra o que chegaria ao prompt. Faz o papel do
    futuro `ExplicadorLLMTrueFoundry` sem nenhuma rede."""

    def __init__(self):
        self.contexto_recebido = None

    def _gerar_texto(self, contribuicoes, contexto):
        self.contexto_recebido = contexto
        return "explicação em texto"


def test_contexto_enviado_ao_llm_nunca_carrega_nome_do_paciente():
    """Desde 2026-09-28 (ADR-007) `pacientes.nome_completo` é gravado, para a
    "Fila do dia" ser operável por quem atende. Esta é a fronteira que impede
    o nome de sair para um provedor de LLM.

    Vale registrar por que a proteção é aqui e não no banco: o LLM é chamado de
    dentro do mesmo processo que já leu o nome para desenhar a tela, então
    cifrar a coluna não ajudaria -- a chave estaria na mesma memória. O que
    ajuda é o nome não entrar no dicionário que vira prompt."""
    espiao = _ExplicadorEspiao()

    espiao.explicar_em_texto(
        contribuicoes=[{"feature": "idade", "contribuicao": 0.3}],
        contexto={
            "nome_completo": "Ana Souza",
            "telefone": "5511987654321",
            "cpf": "52998224725",
            "id_agendamento": "550e8400-e29b-41d4-a716-446655440000",
            "especialidade": "cardiologia",
        },
    )

    assert espiao.contexto_recebido == {
        "id_agendamento": "550e8400-e29b-41d4-a716-446655440000",
        "especialidade": "cardiologia",
    }


def test_contexto_sem_pii_tolera_ausencia_e_dicionario_vazio():
    assert explain.contexto_sem_pii(None) == {}
    assert explain.contexto_sem_pii({}) == {}


def test_nenhuma_implementacao_de_explicador_pode_pular_a_fronteira():
    """`explicar_em_texto` é concreta justamente para a sanitização acontecer
    por construção. Uma subclasse que a sobrescrevesse voltaria a receber o
    contexto cru -- e o vazamento seria silencioso, porque nada na chamada
    mudaria de forma. Este teste é o que impede isso de passar em revisão.

    Percorre as subclasses recursivamente: uma sobrescrita escondida dois
    níveis abaixo vaza igual."""

    def _subclasses(classe):
        for sub in classe.__subclasses__():
            yield sub
            yield from _subclasses(sub)

    infratoras = [
        sub.__name__
        for sub in _subclasses(explain.ExplicadorLLM)
        if "explicar_em_texto" in sub.__dict__
    ]
    assert not infratoras, (
        f"{infratoras} sobrescreve(m) explicar_em_texto e pula(m) contexto_sem_pii "
        "(ADR-007) -- implemente _gerar_texto"
    )


def test_explicar_recusa_x_com_mais_de_uma_linha(explicacao_real):
    """Regressão: explicar() devolvia silenciosamente a explicação só da
    primeira linha. No job diário, que roda sobre a fila de D+2,
    isso gravaria a explicação do paciente errado em todas as predições
    seguintes -- sem erro visível e violando o SLO §4 na prática."""
    _, explainer, X = explicacao_real
    X_duas_linhas = pd.concat([X, X], ignore_index=True)

    with pytest.raises(ValueError, match="explicar_lote"):
        explain.explicar(explainer, X_duas_linhas)


def test_explicar_lote_devolve_uma_explicacao_por_linha(explicacao_real):
    _, explainer, X = explicacao_real
    X_tres_linhas = pd.concat([X, X, X], ignore_index=True)

    explicacoes = explain.explicar_lote(explainer, X_tres_linhas)

    assert len(explicacoes) == 3
    assert all(len(e) == len(X.columns) for e in explicacoes)


def test_explicar_lote_de_uma_linha_bate_com_explicar(explicacao_real):
    """explicar() é explicar_lote() de 1 linha -- garante que o caminho da
    API (uma predição) e o do job (fila) não divergem."""
    _, explainer, X = explicacao_real

    assert explain.explicar_lote(explainer, X)[0] == explain.explicar(explainer, X)
