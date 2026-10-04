"""
SaúdeJá — smoke test da interface Streamlit, via
streamlit.testing.v1.AppTest: roda o script de verdade, sem browser.

Escopo deliberado: a casca (app carrega sem exceção, abas certas existem,
gating de APP_ENV e de login, placeholders dos passos futuros) e UM caminho
feliz ponta a ponta pelo formulário. A lógica de predição e a de autenticação
são cobertas por tests/test_ui_logic.py -- aqui o que se testa é a tela.
"""
from datetime import datetime, timedelta, timezone

import pytest
import streamlit as st
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
    """O cadastro usa a persistência de verdade -- sem SUPABASE_URL/
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
    """O painel de observabilidade é diagnóstico passivo (mesma escolha do status do
    banco na sidebar), então sem Supabase ele avisa -- não derruba a tela nem
    impede a predição manual, que não depende de banco nenhum."""
    at = _rodar(monkeypatch)

    assert not at.exception
    assert any("observabilidade" in aviso.value.lower() for aviso in at.warning)


def test_aba_dev_dispara_job_e_mostra_erro_amigavel_sem_supabase(monkeypatch):
    """O botão está ligado ao job de verdade (`disparar_job_diario`). Sem
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


# --- caminhos felizes das abas que dependem do banco ----------------------------
# Os testes acima cobrem essas abas só SEM Supabase (o erro amigável). O que a
# clínica vê em produção -- a fila com dados, o painel com números, o job que
# rodou -- não tinha nenhum smoke. O banco é trocado por funções falsas em
# `ui.logic`, o mesmo módulo que o app importa.


@pytest.fixture
def caches_do_streamlit_limpos():
    """`_resumo_observabilidade` e `_status_banco` usam `st.cache_data`, que
    vive no processo inteiro: um resumo falso cacheado aqui apareceria no
    teste "sem Supabase" seguinte."""
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def _item_fila(n, probabilidade, classe, **kwargs):
    return logic.ItemFila(
        id_agendamento=f"ag-{n}",
        id_paciente_externo=f"{n:064x}",
        especialidade="cardiologia",
        data_hora_agendada=datetime(2026, 10, 2, 9 + n, 0),
        probabilidade=probabilidade,
        classe_prevista=classe,
        explicacao=[{"feature": "historico_noshow", "valor": 3, "contribuicao": 0.8}],
        model_version="abc123",
        **kwargs,
    )


def test_fila_do_dia_com_agendamentos_mostra_os_contadores(monkeypatch, caches_do_streamlit_limpos):
    fila = [
        _item_fila(0, 0.81, 1, nome_completo="Paciente Teste Um"),
        _item_fila(1, 0.12, 0, fora_do_dominio=True),
        logic.ItemFila(
            id_agendamento="ag-2",
            id_paciente_externo="f" * 64,
            especialidade="dermatologia",
            data_hora_agendada=datetime(2026, 10, 2, 15, 0),
            probabilidade=None,
            classe_prevista=None,
        ),
    ]
    monkeypatch.setattr(logic, "buscar_fila_do_dia", lambda dia: fila)

    at = _rodar(monkeypatch)

    assert not at.exception
    contadores = {m.label: m.value for m in at.metric}
    assert contadores["Agendamentos"] == "3"
    assert contadores["Alto risco"] == "1"
    assert contadores["Sem predição"] == "1"
    assert not [e for e in at.error if "fila" in e.value.lower()]


def test_observabilidade_com_numeros_acusa_violacao_de_slo(monkeypatch, caches_do_streamlit_limpos):
    """p95 acima de 2 s e predição sem explicação são as duas violações que o
    painel tem de gritar -- testadas com o resumo no formato de
    `logic.resumo_observabilidade`."""
    resumo = {
        "janela_horas": 24,
        "predicoes": 40,
        "p50_ms": 900.0,
        "p95_ms": 2500.0,
        "erros": 2,
        "por_origem": {"job": 30, "processo": 10},
        "cobertura_explicacao": {"predicoes": 30, "com_explicacao": 29, "percentual": 29 / 30},
        "ultimo_job_d2": datetime(2026, 10, 2, 8, 17, tzinfo=timezone.utc),
        "truncado": True,
    }
    monkeypatch.setattr(logic, "resumo_observabilidade", lambda janela_horas: resumo)

    at = _rodar(monkeypatch)

    assert not at.exception
    contadores = {m.label: m.value for m in at.metric}
    assert contadores["p95 de latência"] == "2500 ms"
    assert contadores["Cobertura de explicação (SLO §4)"] == "97%"
    erros = " ".join(e.value for e in at.error)
    assert "SLO §2" in erros and "SLO §4" in erros
    assert any("truncada" in w.value for w in at.warning)


def test_aba_dev_com_job_bem_sucedido_mostra_os_contadores(monkeypatch, caches_do_streamlit_limpos):
    resultado = {
        "agendamentos_encontrados": 5,
        "predicoes_gravadas": 5,
        "mensagens_disparadas": 2,
        "erros": [],
    }
    monkeypatch.setattr(logic, "disparar_job_diario", lambda dia=None: resultado)
    at = _rodar(monkeypatch)

    _botao(at, "Disparar job D-2 agora").click().run()

    assert not at.exception
    contadores = {m.label: m.value for m in at.metric}
    assert contadores["Mensagens disparadas"] == "2"
    assert any("sem erros" in s.value for s in at.success)
