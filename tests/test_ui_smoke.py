"""
SaúdeJá — smoke test da interface Streamlit (Passo 4), via
streamlit.testing.v1.AppTest: roda o script de verdade, sem browser.

Escopo deliberado: a casca (app carrega sem exceção, abas certas existem,
gating de APP_ENV e de login, placeholders dos passos futuros) e UM caminho
feliz ponta a ponta pelo formulário. A lógica de predição e a de autenticação
são cobertas por tests/test_ui_logic.py -- aqui o que se testa é a tela.
"""
from datetime import datetime, timedelta, timezone

from streamlit.testing.v1 import AppTest

from ui import logic

CAMINHO_APP = "src/ui/app.py"
TIMEOUT = 60  # primeiro run carrega modelo + TreeExplainer


def _sessao(ultimo_uso=None):
    return logic.SessaoFuncionario(
        id_usuario="00000000-0000-0000-0000-000000000001",
        email="recepcao@clinica.test",
        ultimo_uso=ultimo_uso or datetime.now(timezone.utc),
    )


def _botao(at, rotulo):
    return next(b for b in at.button if b.label == rotulo)


def _rodar(monkeypatch, app_env="dev", backend="processo", sessao="logado"):
    """`sessao="logado"` (default) entra já autenticado: o login em si é
    testado nos testes próprios dele, e os demais testes são sobre as abas que
    ficam atrás dele. Injetar a sessão em `session_state` é o mesmo estado que
    um login bem-sucedido deixa (app.py::_tela_login). Passe `None` para
    começar deslogado ou uma `SessaoFuncionario` específica."""
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("PREDICT_BACKEND", backend)
    # Determinístico independente do .env do dev: os testes de UI não devem
    # gravar no Supabase real nem depender de rede -- src/db/ é exercitado de
    # verdade só em tests/test_db.py (marcado integracao, contra o Supabase
    # CLI local).
    #
    # Vazio, não `delenv`: `config_projeto` chama `load_dotenv()` no import, e
    # uma variável APAGADA é justamente o caso em que o dotenv a define de
    # novo -- a suíte acabava falando com o projeto Supabase real de quem tem
    # `.env` local. Definida como string vazia, a chave existe (o dotenv não
    # sobrescreve) e é falsy, que é o que `db/client.py` trata como
    # ConfiguracaoSupabaseAusente.
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    at = AppTest.from_file(CAMINHO_APP, default_timeout=TIMEOUT)
    if sessao == "logado":
        sessao = _sessao()
    if sessao is not None:
        at.session_state["funcionario"] = sessao
    return at.run()


def test_app_carrega_sem_excecao(monkeypatch):
    at = _rodar(monkeypatch)

    assert not at.exception


def test_abas_do_funcionario_existem_em_dev(monkeypatch):
    at = _rodar(monkeypatch, app_env="dev")

    rotulos = [aba.label for aba in at.tabs]
    assert rotulos == [
        "Testar predição",
        "Explicabilidade",
        "Fila do dia",
        "Observabilidade",
        "Dev: disparo manual",
    ]


def test_aba_de_dev_some_fora_do_ambiente_de_dev(monkeypatch):
    at = _rodar(monkeypatch, app_env="prod")

    rotulos = [aba.label for aba in at.tabs]
    assert "Dev: disparo manual" not in rotulos
    assert len(rotulos) == 4


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
    _botao(at, "Agendar").click().run()

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


def test_aba_observabilidade_sem_supabase_avisa_sem_derrubar_a_tela(monkeypatch):
    """Passo 8.5: o painel é diagnóstico passivo (mesma escolha do status do
    banco na sidebar), então sem Supabase ele avisa -- não derruba a tela nem
    impede a predição manual, que não depende de banco nenhum."""
    at = _rodar(monkeypatch)

    assert not at.exception
    assert any("observabilidade" in aviso.value.lower() for aviso in at.warning)


def test_aba_dev_dispara_job_e_mostra_erro_amigavel_sem_supabase(monkeypatch):
    """Passo 6 liga o botão ao job de verdade (`disparar_job_diario`). Sem
    SUPABASE_URL/SUPABASE_SECRET_KEY no ambiente de teste da UI (mesma
    convenção das demais abas), clicar não derruba a tela -- mostra
    ErroPersistencia traduzido, não um traceback."""
    at = _rodar(monkeypatch)

    _botao(at, "Disparar job D-2 agora").click().run()

    assert not at.exception
    assert any("job" in erro.value.lower() for erro in at.error)


def test_formulario_produz_predicao_e_explicacao(monkeypatch):
    """Caminho feliz ponta a ponta pela tela: submeter o formulário e ter
    probabilidade + explicação na sessão. Só no backend em processo -- o par
    REST exigiria um uvicorn de pé e já é coberto por test_ui_logic.py com
    TestClient."""
    at = _rodar(monkeypatch, backend="processo")
    _botao(at, "Prever no-show").click().run()

    assert not at.exception
    resultado = at.session_state["ultimo_resultado"]
    assert 0.0 <= resultado.probabilidade <= 1.0
    assert resultado.explicacao  # SLO §4
    assert at.metric  # os cards de probabilidade/threshold/classe renderizaram


# --- login do funcionário (ADR-008) -------------------------------------------


def test_visao_do_funcionario_sem_login_mostra_so_a_tela_de_login(monkeypatch):
    """A "Fila do dia" carrega nome de paciente (ADR-007): sem login, nenhuma
    aba do funcionário pode ser renderizada -- nem escondida por CSS, que é
    como o `st.tabs` esconde as abas inativas."""
    at = _rodar(monkeypatch, sessao=None)

    assert not at.exception
    assert not at.tabs
    assert [campo.label for campo in at.text_input] == ["E-mail", "Senha"]
    assert "ultimo_resultado" not in at.session_state


def test_login_sem_supabase_configurado_mostra_erro_amigavel(monkeypatch):
    at = _rodar(monkeypatch, sessao=None)
    at.text_input[0].set_value("recepcao@clinica.test")
    at.text_input[1].set_value("senha-qualquer")
    _botao(at, "Entrar").click().run()

    assert not at.exception
    assert not at.tabs
    assert any("não foi possível entrar" in erro.value.lower() for erro in at.error)


def test_login_com_campos_vazios_avisa_sem_chamar_o_supabase(monkeypatch):
    at = _rodar(monkeypatch, sessao=None)
    _botao(at, "Entrar").click().run()

    assert not at.exception
    assert any("informe e-mail e senha" in aviso.value.lower() for aviso in at.warning)
    assert not at.error  # não chegou a tentar o Supabase (que falharia com erro)


def test_login_bem_sucedido_libera_as_abas(monkeypatch):
    def autenticar_falso(email, senha):
        return _sessao()

    monkeypatch.setattr(logic, "autenticar_funcionario", autenticar_falso)
    at = _rodar(monkeypatch, sessao=None)
    at.text_input[0].set_value("recepcao@clinica.test")
    at.text_input[1].set_value("senha-correta")
    _botao(at, "Entrar").click().run()

    assert not at.exception
    assert [aba.label for aba in at.tabs][:4] == [
        "Testar predição",
        "Explicabilidade",
        "Fila do dia",
        "Observabilidade",
    ]
    assert any("recepcao@clinica.test" in legenda.value for legenda in at.sidebar.caption)


def test_visao_paciente_nao_exige_login(monkeypatch):
    """O autoagendamento do paciente continua aberto: a porta é só na frente
    dos dados da clínica."""
    at = _rodar(monkeypatch, sessao=None)
    at.sidebar.radio[0].set_value("Paciente").run()

    assert not at.exception
    assert "Nome completo" in [campo.label for campo in at.text_input]


def test_sair_encerra_a_sessao_e_descarta_a_ultima_predicao(monkeypatch):
    """A última predição fica em `session_state` para a aba "Explicabilidade"
    -- quem entra depois no mesmo navegador não pode herdá-la."""
    at = _rodar(monkeypatch)
    _botao(at, "Prever no-show").click().run()
    assert "ultimo_resultado" in at.session_state

    _botao(at, "Sair").click().run()

    assert not at.exception
    assert not at.tabs
    assert "funcionario" not in at.session_state
    assert "ultimo_resultado" not in at.session_state


def test_sessao_inativa_volta_para_o_login_com_aviso(monkeypatch):
    parada = datetime.now(timezone.utc) - logic.INATIVIDADE_MAXIMA - timedelta(minutes=1)
    at = _rodar(monkeypatch, sessao=_sessao(ultimo_uso=parada))

    assert not at.exception
    assert not at.tabs
    assert any("sem uso" in info.value for info in at.info)
