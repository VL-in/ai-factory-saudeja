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
    # Determinístico independente do .env do dev: os testes de UI não devem
    # gravar no Supabase real nem depender de rede -- src/db/ é exercitado de
    # verdade só em tests/test_db.py (marcado integracao, contra o Supabase
    # CLI local).
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
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


def test_visao_paciente_carrega_o_formulario_de_cadastro(monkeypatch):
    """Passo 5 liga a persistência de verdade -- sem SUPABASE_URL/
    SUPABASE_SECRET_KEY no ambiente de teste, submeter com um CPF válido e um
    horário dentro da grade da clínica falha com uma mensagem amigável
    (ErroPersistencia), não com traceback."""
    at = _rodar(monkeypatch)
    at.sidebar.radio[0].set_value("Paciente").run()

    assert not at.exception
    assert at.text_input  # campos de nome completo/CPF, não mais placeholder

    at.text_input[0].set_value("Paciente de Teste").run()  # nome completo
    at.text_input[1].set_value("111.444.777-35").run()  # CPF válido
    at.text_input[2].set_value("(11) 98765-4321").run()  # telefone válido
    at.button[0].click().run()  # 'Agendar'

    assert not at.exception
    assert any("cadastrar" in erro.value.lower() for erro in at.error)


def test_aba_fila_do_dia_sem_supabase_configurado_mostra_erro_amigavel(monkeypatch):
    """`st.tabs` renderiza o conteúdo de todas as abas no mesmo script run
    (a troca de aba no navegador é só CSS) -- a aba "Fila do dia" já roda em
    _rodar(). Sem SUPABASE_URL/SUPABASE_SECRET_KEY (ambiente de teste da UI,
    ver tests/test_db.py para os testes de integração de verdade), ela não
    derruba a tela -- mostra ErroPersistencia traduzido."""
    at = _rodar(monkeypatch)

    assert not at.exception
    assert any("fila" in erro.value.lower() for erro in at.error)


def test_aba_dev_dispara_job_e_mostra_erro_amigavel_sem_supabase(monkeypatch):
    """Passo 6 liga o botão ao job de verdade (`disparar_job_diario`). Sem
    SUPABASE_URL/SUPABASE_SECRET_KEY no ambiente de teste da UI (mesma
    convenção das demais abas), clicar não derruba a tela -- mostra
    ErroPersistencia traduzido, não um traceback."""
    at = _rodar(monkeypatch)

    botao = next(b for b in at.button if b.label == "Disparar job D-2 agora")
    botao.click().run()

    assert not at.exception
    assert any("job" in erro.value.lower() for erro in at.error)


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
