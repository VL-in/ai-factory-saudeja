"""
SaúdeJá — suíte de sanidade do modelo, rodada pelo CI contra o
`data/model.pkl` de `main` e pelo gate de re-treino contra o desafiante.

O primeiro teste é o que o CI exige do modelo em produção. Os demais são
controles negativos: cada checagem é forçada a falhar de propósito, porque uma
suíte que nunca reprova daria verde sem verificar nada.
"""
import numpy as np

import inference
import sanidade_modelo

MODEL_PATH = "data/model.pkl"


def test_modelo_em_producao_passa_na_suite_de_sanidade():
    resultado = sanidade_modelo.verificar_modelo(MODEL_PATH)

    assert resultado.aprovado, resultado.falhas
    # avisos não reprovam, mas o campeão vigente não deveria ter nenhum
    assert resultado.avisos == []


def test_grade_de_casos_e_deterministica_e_toda_valida():
    """A grade é a entrada da suíte: precisa ser a mesma em qualquer máquina e
    estar inteira dentro da regra de negócio (senão a suíte testaria a
    quarentena, não o modelo)."""
    import contrato_features

    _, mapa = inference.carregar_modelo(MODEL_PATH)
    casos = sanidade_modelo.grade_de_casos(sorted(mapa))

    assert casos == sanidade_modelo.grade_de_casos(sorted(mapa))
    for caso in casos[:: max(len(casos) // 50, 1)]:
        contrato_features.validar_payload(caso, mapa)


def test_modelo_constante_reprova(monkeypatch):
    monkeypatch.setattr(inference, "predizer", lambda model, X: np.full(len(X), 0.5))

    resultado = sanidade_modelo.verificar_modelo(MODEL_PATH)

    assert any("constante" in f for f in resultado.falhas)


def test_probabilidade_nan_reprova(monkeypatch):
    monkeypatch.setattr(inference, "predizer", lambda model, X: np.full(len(X), np.nan))

    resultado = sanidade_modelo.verificar_modelo(MODEL_PATH)

    assert any("NaN" in f for f in resultado.falhas)


def test_modelo_incompativel_com_as_features_reprova(monkeypatch):
    monkeypatch.setitem(inference.PARAMS, "features", {"temporais": False})

    resultado = sanidade_modelo.verificar_modelo(MODEL_PATH)

    assert any("assinatura" in f for f in resultado.falhas)


def test_especialidade_do_cadastro_que_o_modelo_nao_conhece_reprova():
    params = {"cadastro": {"especialidades": ["cardiologia", "neurologia"]}}

    resultado = sanidade_modelo.verificar_modelo(MODEL_PATH, params=params)

    assert any("neurologia" in f for f in resultado.falhas)


def test_caso_limitrofe_com_politica_errada_reprova(monkeypatch):
    """Controle negativo da checagem da política do contrato: declarar que
    idade 150 deveria só ser marcada faz a suíte reprovar."""
    monkeypatch.setattr(
        sanidade_modelo, "CASOS_LIMITROFES", (("idade 150", "idade", 150, "marca"),)
    )

    resultado = sanidade_modelo.verificar_modelo(MODEL_PATH)

    assert any("idade 150" in f for f in resultado.falhas)


def test_direcao_do_historico_contraria_so_avisa(monkeypatch):
    """Quem faltou mais com risco menor contraria a expectativa de negócio --
    mas com 380 linhas sintéticas pode ser ruído: aviso, nunca bloqueio."""
    original = inference.predizer

    def invertido(model, X):
        return original(model, X) * np.where(X["historico_noshow"] > 0, 0.1, 1.0)

    monkeypatch.setattr(inference, "predizer", invertido)

    resultado = sanidade_modelo.verificar_modelo(MODEL_PATH)

    assert any("histórico de faltas" in a for a in resultado.avisos)
    assert not any("histórico" in f for f in resultado.falhas)
