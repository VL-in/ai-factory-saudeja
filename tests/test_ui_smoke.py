"""
SaúdeJá — smoke test da interface Streamlit (Passo 4), via
streamlit.testing.v1.AppTest: roda o script de verdade, sem browser.

Escopo deliberado: a casca (app carrega sem exceção, abas certas existem,
gating de APP_ENV, placeholders dos passos futuros) e UM caminho feliz ponta a
ponta pelo formulário. A lógica de predição em si é coberta por
tests/test_ui_logic.py -- aqui o que se testa é a tela.
"""
from streamlit.testing.v1 import AppTest

CAMINHO_APP = "src/ui/app.py"
TIMEOUT = 60  # primeiro run carrega modelo + TreeExplainer


def _rodar(monkeypatch, app_env="dev", backend="processo"):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("PREDICT_BACKEND", backend)
    return AppTest.from_file(CAMINHO_APP, default_timeout=TIMEOUT).run()


def test_app_carrega_sem_excecao(monkeypatch):
    at = _rodar(monkeypatch)

    assert not at.exception


def test_quatro_abas_do_funcionario_existem_em_dev(monkeypatch):
    at = _rodar(monkeypatch, app_env="dev")

    rotulos = [aba.label for aba in at.tabs]
    assert rotulos == [
        "Testar predição",
        "Explicabilidade",
        "Fila do dia",
        "Dev: disparo manual",
    ]


def test_aba_de_dev_some_fora_do_ambiente_de_dev(monkeypatch):
    at = _rodar(monkeypatch, app_env="prod")

    rotulos = [aba.label for aba in at.tabs]
    assert "Dev: disparo manual" not in rotulos
    assert len(rotulos) == 3


def test_visao_paciente_carrega_como_placeholder(monkeypatch):
    at = _rodar(monkeypatch)
    at.sidebar.radio[0].set_value("Paciente").run()

    assert not at.exception
    assert any("Passo 5" in info.value for info in at.info)


def test_abas_ainda_nao_ligadas_nomeiam_o_passo_que_as_liga(monkeypatch):
    """Placeholder que só diz "em breve" não ajuda ninguém a saber o que falta;
    a casca do Passo 4 promete apontar o passo do plano."""
    at = _rodar(monkeypatch)
    avisos = " ".join(info.value for info in at.info)

    assert "Passo 5" in avisos  # fila do dia -> banco
    assert "Passo 6" in avisos  # disparo manual -> job D-2


def test_formulario_produz_predicao_e_explicacao(monkeypatch):
    """Caminho feliz ponta a ponta pela tela: submeter o formulário e ter
    probabilidade + explicação na sessão. Só no backend em processo -- o par
    REST exigiria um uvicorn de pé e já é coberto por test_ui_logic.py com
    TestClient."""
    at = _rodar(monkeypatch, backend="processo")
    at.button[0].click().run()  # 'Prever no-show' (único botão da aba ativa)

    assert not at.exception
    resultado = at.session_state["ultimo_resultado"]
    assert 0.0 <= resultado.probabilidade <= 1.0
    assert resultado.explicacao  # SLO §4
    assert at.metric  # os cards de probabilidade/threshold/classe renderizaram
