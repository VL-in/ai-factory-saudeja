"""
SaúdeJá — testes da lógica da interface. Não sobem o runtime do
Streamlit: src/ui/logic.py existe justamente para ser testável assim.

Filosofia do repositório (sem mock pesado): o backend em processo usa o
data/model.pkl real (já versionado via DVC, sem re-treinar) e o backend REST
usa um TestClient do próprio app FastAPI como transporte -- a API de verdade,
sem subir servidor nem forjar a resposta. Só os caminhos de FALHA de rede são
simulados, porque não dá para desligar um servidor que não existe.
"""
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from supabase_auth.errors import AuthApiError, AuthRetryableError

import db.repositories as repositories
import inference
from api.main import app
from api.schemas import PacienteConsultaIn
from ui import logic

MODEL_PATH = "data/model.pkl"


@pytest.fixture(scope="module")
def especialidade_valida():
    _, mapa_especialidade = inference.carregar_modelo(MODEL_PATH)
    return sorted(mapa_especialidade)[0]


@pytest.fixture
def campos_formulario(especialidade_valida):
    """Exatamente o que os widgets de src/ui/app.py entregam."""
    return {
        "idade": 45,
        "sexo": "F",
        "especialidade": especialidade_valida,
        "distancia_km": 5.5,
        "dias_entre_agendamento_consulta": 14,
        "historico_noshow": 1,
        "data_consulta": date(2026, 1, 9),
        "hora_consulta": time(18, 0),
    }


@pytest.fixture
def payload(campos_formulario):
    return logic.montar_payload(**campos_formulario)


def test_montar_payload_combina_data_e_hora_em_timestamp(campos_formulario):
    resultado = logic.montar_payload(**campos_formulario)

    assert resultado["data_hora_agendada"] == datetime(2026, 1, 9, 18, 0)


def test_payload_da_ui_satisfaz_o_schema_da_api(payload):
    """Contrato UI -> API travado sem subir servidor: se o formulário deixar
    de produzir algum campo que PacienteConsultaIn exige (ou produzir um tipo
    errado), este teste quebra antes de virar 422 na tela do funcionário."""
    validado = PacienteConsultaIn(**payload)

    assert validado.data_hora_agendada == payload["data_hora_agendada"]


def test_listar_especialidades_vem_da_configuracao_do_cadastro():
    """O que a clínica atende é configuração (params.yaml), não
    efeito colateral do último treino."""
    assert logic.listar_especialidades() == sorted(
        inference.PARAMS["cadastro"]["especialidades"]
    )


def test_especialidades_do_cadastro_estao_no_mapa_do_modelo_em_producao():
    """Uma especialidade que o cadastro oferece e o modelo não conhece seria
    quarentena garantida no job D-2 para todo paciente dela."""
    _, mapa_especialidade = inference.carregar_modelo(MODEL_PATH)

    assert set(logic.listar_especialidades()) <= set(mapa_especialidade)


# --- backend em processo (default, ADR-005 b) ---------------------------------


def test_em_processo_retorna_probabilidade_e_explicacao(payload):
    resultado = logic.ClientePredicaoEmProcesso(MODEL_PATH).predizer(payload)

    assert 0.0 <= resultado.probabilidade <= 1.0
    assert resultado.classe_prevista == int(
        resultado.probabilidade >= resultado.threshold_usado
    )
    assert resultado.explicacao  # SLO §4 -- 100% das predições com explicação
    assert resultado.explicacao_texto is None  # plug do LLM inativo
    assert resultado.model_version


def test_em_processo_especialidade_desconhecida_vira_erro_de_validacao(payload):
    payload["especialidade"] = "especialidade-nunca-vista"

    with pytest.raises(logic.ErroValidacao, match="especialidade-nunca-vista"):
        logic.ClientePredicaoEmProcesso(MODEL_PATH).predizer(payload)


def test_em_processo_sem_modelo_vira_erro_de_indisponibilidade(payload, tmp_path):
    cliente = logic.ClientePredicaoEmProcesso(str(tmp_path / "inexistente.pkl"))

    with pytest.raises(logic.ErroIndisponivel, match="modelo não encontrado"):
        cliente.predizer(payload)


def test_em_processo_carrega_o_modelo_uma_vez_so(payload, monkeypatch):
    """O cliente é cacheado por st.cache_resource entre reruns; se ele
    recarregasse o artefato a cada predição, o cache não adiantaria nada."""
    cliente = logic.ClientePredicaoEmProcesso(MODEL_PATH)
    carregamentos = []
    original = inference.carregar_modelo

    def contando(path):
        carregamentos.append(path)
        return original(path)

    monkeypatch.setattr(inference, "carregar_modelo", contando)
    cliente.predizer(payload)
    cliente.predizer(payload)

    assert len(carregamentos) == 1


# --- backend REST (PREDICT_BACKEND=api) ---------------------------------------


def test_api_retorna_probabilidade_e_explicacao(payload):
    with TestClient(app) as http:
        resultado = logic.ClientePredicaoAPI(cliente_http=http).predizer(payload)

    assert 0.0 <= resultado.probabilidade <= 1.0
    assert resultado.explicacao
    assert resultado.model_version


def test_api_saude_reporta_backend_e_versao_do_modelo():
    with TestClient(app) as http:
        saude = logic.ClientePredicaoAPI(cliente_http=http).saude()

    assert saude["status"] == "ok"
    assert saude["backend"] == "api"
    assert saude["model_version"]


def test_api_especialidade_desconhecida_vira_erro_de_validacao(payload):
    payload["especialidade"] = "especialidade-nunca-vista"

    with TestClient(app) as http, pytest.raises(logic.ErroValidacao, match="nunca-vista"):
        logic.ClientePredicaoAPI(cliente_http=http).predizer(payload)


def test_api_422_do_pydantic_vira_mensagem_legivel(payload):
    """O 422 do Pydantic traz `detail` como lista de erros, não string -- a UI
    não pode exibir o repr cru da lista para o funcionário."""
    payload["sexo"] = "X"

    with TestClient(app) as http, pytest.raises(logic.ErroValidacao) as excecao:
        logic.ClientePredicaoAPI(cliente_http=http).predizer(payload)

    assert "sexo" in str(excecao.value)


def test_api_fora_do_ar_vira_erro_de_indisponibilidade(payload):
    class HttpQueRecusaConexao:
        def request(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    cliente = logic.ClientePredicaoAPI(cliente_http=HttpQueRecusaConexao())

    with pytest.raises(logic.ErroIndisponivel, match="não respondeu"):
        cliente.predizer(payload)


def test_api_erro_500_nao_vira_erro_de_validacao(payload):
    class HttpQueFalha:
        def request(self, *args, **kwargs):
            return httpx.Response(500, json={"detail": "boom"})

    cliente = logic.ClientePredicaoAPI(cliente_http=HttpQueFalha())

    with pytest.raises(logic.ErroIndisponivel, match="500"):
        cliente.predizer(payload)


# --- paridade entre os dois backends ------------------------------------------


def test_backends_produzem_a_mesma_predicao(payload):
    """A razão de existir do seam: REST e em processo são duas formas de
    invocar o MESMO src/inference.py. Se divergirem, a UI passa a mostrar um
    número diferente do que a API entrega a integrações externas."""
    em_processo = logic.ClientePredicaoEmProcesso(MODEL_PATH).predizer(payload)
    with TestClient(app) as http:
        via_api = logic.ClientePredicaoAPI(cliente_http=http).predizer(payload)

    assert via_api.probabilidade == pytest.approx(em_processo.probabilidade)
    assert via_api.classe_prevista == em_processo.classe_prevista
    assert via_api.threshold_usado == pytest.approx(em_processo.threshold_usado)
    assert via_api.model_version == em_processo.model_version
    assert via_api.explicacao == em_processo.explicacao


# --- fila do dia e cadastro, sem tocar no banco ---------------------------------


def _item(
    probabilidade=None,
    classe_prevista=None,
    explicacao=None,
    id_externo="EXT-1",
    nome_completo=None,
):
    return logic.ItemFila(
        id_agendamento="a1",
        id_paciente_externo=id_externo,
        especialidade="cardiologia",
        data_hora_agendada=datetime(2026, 9, 19, 10, 0),
        probabilidade=probabilidade,
        classe_prevista=classe_prevista,
        explicacao=explicacao or [],
        nome_completo=nome_completo,
    )


def test_resumo_da_fila_separa_alto_risco_de_sem_predicao():
    """"Sem predição" não pode ser contado como baixo risco: são agendamentos
    que o job D-2 ainda não processou, não pacientes que o modelo liberou."""
    resumo = logic.resumo_da_fila(
        [
            _item(probabilidade=0.9, classe_prevista=1),
            _item(probabilidade=0.1, classe_prevista=0),
            _item(),
        ]
    )

    assert resumo == {"total": 3, "alto_risco": 1, "sem_predicao": 1}


def test_item_sem_predicao_nao_finge_ter_probabilidade():
    assert _item().tem_predicao is False
    assert _item(probabilidade=0.4, classe_prevista=0).tem_predicao is True


# --- nome do paciente na fila do dia (ADR-007) --------------------------------


def test_rotulo_do_paciente_usa_o_nome_quando_existe():
    """A coluna "Paciente" mostrava o hash sha256 de 64 caracteres -- inútil
    para quem atende no balcão e precisa chamar a pessoa pelo nome."""
    assert _item(nome_completo="Ana Souza Costa").rotulo_paciente == "Ana Souza Costa"


def test_rotulo_do_paciente_sem_nome_nao_inventa_uma_pessoa():
    """Cadastro anterior à migration do nome: não há nome a recuperar (ele
    nunca foi gravado). Mostrar o início do hash, rotulado, é honesto; um
    placeholder com cara de nome enganaria quem opera a fila."""
    rotulo = _item(id_externo="9f86d081884c7d65" + "0" * 48).rotulo_paciente

    assert rotulo == "(cadastro sem nome) 9f86d081"
    assert "9f86d081884c7d65" not in rotulo, "o hash inteiro não precisa ir para a tela"


@pytest.mark.parametrize(
    "nome,esperado",
    [
        ("Ana Souza", True),
        ("Yi", True),  # nome curto é nome
        ("Ana D'Ávila Menezes-Filho", True),  # apóstrofo e hífen são comuns
        ("", False),
        ("   ", False),  # espaço em branco não é nome
        ("A", False),
        ("x" * 121, False),  # bate com o check da migration
    ],
)
def test_nome_valido_barra_so_preenchimento_claramente_errado(nome, esperado):
    """Não existe "validar nome": nomes brasileiros têm partícula, acento,
    apóstrofo e hífen, e um único termo é possível. O que se barra é campo
    vazio, espaço em branco e texto longo demais para a coluna."""
    assert logic.nome_valido(nome) is esperado


def test_cadastro_recusa_nome_invalido_antes_de_tocar_o_banco(monkeypatch):
    def _nao_deveria_ser_chamado(**kwargs):
        raise AssertionError("cadastro chegou ao banco com nome inválido")

    monkeypatch.setattr(logic.repositories, "inserir_paciente", _nao_deveria_ser_chamado)

    with pytest.raises(logic.ErroValidacaoCadastro, match="Nome inválido"):
        logic.cadastrar_paciente_e_agendamento(
            cpf="529.982.247-25",
            telefone="(11) 98765-4321",
            data_nascimento=date(1990, 1, 1),
            sexo="F",
            especialidade="cardiologia",
            distancia_km=5.5,
            data_consulta=date(2026, 9, 30),
            hora_consulta=time(10, 0),
            nome_completo=" ",
        )


def test_cadastro_repassa_o_nome_ao_repositorio(monkeypatch):
    capturado = {}

    def _inserir_paciente(**kwargs):
        capturado.update(kwargs)
        return {"id": "p1"}

    monkeypatch.setattr(logic.repositories, "inserir_paciente", _inserir_paciente)
    monkeypatch.setattr(logic.repositories, "contar_no_shows_anteriores", lambda _id: 0)
    monkeypatch.setattr(logic.repositories, "inserir_agendamento", lambda **kw: {"id": "a1"})
    # Data fixa da consulta exige "hoje" fixo: o
    # cadastro recusa consulta no passado.
    monkeypatch.setattr(logic, "hoje_na_clinica", lambda: date(2026, 9, 29))

    logic.cadastrar_paciente_e_agendamento(
        cpf="529.982.247-25",
        telefone="(11) 98765-4321",
        data_nascimento=date(1990, 1, 1),
        sexo="F",
        especialidade="cardiologia",
        distancia_km=5.5,
        data_consulta=date(2026, 9, 30),
        hora_consulta=time(10, 0),
        nome_completo="Ana Souza",
    )

    assert capturado["nome_completo"] == "Ana Souza"
    # O CPF continua não sendo persistido: o que vai é o hash dele.
    assert "cpf" not in capturado
    assert capturado["id_paciente_externo"] == logic._id_paciente_externo_de_cpf("52998224725")


class _ErroDeSchema(Exception):
    """Imita o `APIError` do supabase-py: o que identifica a causa é o
    atributo `code`, não o texto da mensagem."""

    def __init__(self, code, message):
        super().__init__({"message": message, "code": code})
        self.code = code


def test_banco_sem_a_migration_diz_o_que_fazer(monkeypatch):
    """Regressão de um incidente real (2026-09-28): a migration de
    `nome_completo` estava no Supabase local, onde os testes de integração
    rodam, e não no projeto remoto, para onde o `.env` aponta. A tela mostrou
    o dicionário cru do PostgREST -- que diz o que falta, mas não o que fazer.

    Não é erro de uso nem indisponibilidade: é ambiente fora de sincronia, e
    volta a cada migration nova, inclusive no deploy do HF Space."""

    def _coluna_inexistente(_dia):
        raise _ErroDeSchema("42703", "column pacientes_1.nome_completo does not exist")

    monkeypatch.setattr(logic.repositories, "buscar_fila_do_dia", _coluna_inexistente)

    with pytest.raises(logic.ErroPersistencia) as capturado:
        logic.buscar_fila_do_dia(date(2026, 9, 28))

    mensagem = str(capturado.value)
    assert "supabase db push" in mensagem
    # A mensagem original continua junto: sem ela não dá para saber QUAL
    # migration falta.
    assert "nome_completo" in mensagem


def test_falha_de_banco_comum_nao_vira_conselho_sobre_migration(monkeypatch):
    """O conselho só vale para o código certo -- sugerir `supabase db push`
    diante de um banco fora do ar mandaria a pessoa para o lugar errado."""

    def _indisponivel(_dia):
        raise _ErroDeSchema("08006", "connection failure")

    monkeypatch.setattr(logic.repositories, "buscar_fila_do_dia", _indisponivel)

    with pytest.raises(logic.ErroPersistencia) as capturado:
        logic.buscar_fila_do_dia(date(2026, 9, 28))

    assert "supabase db push" not in str(capturado.value)


def test_fila_do_dia_nao_pede_telefone_ao_banco():
    """`buscar_fila_do_dia` deixou o `select("*")` para trás (ADR-007): numa
    tabela que guarda PII por exceção, a lista de colunas é a fronteira, e o
    curinga fazia toda coluna nova de `pacientes` fluir para a UI sem ninguém
    decidir isso. O funcionário precisa chamar o paciente pelo nome, não discar
    para ele -- quem envia mensagem é o job D-2, por outro select."""
    colunas = repositories._COLUNAS_FILA_DO_DIA

    assert "nome_completo" in colunas
    assert "telefone" not in colunas
    assert "*" not in colunas


def test_dias_ate_consulta_deriva_da_data_escolhida():
    assert logic.dias_ate_consulta(date(2026, 9, 30), hoje=date(2026, 9, 19)) == 11
    assert logic.dias_ate_consulta(date(2026, 9, 19), hoje=date(2026, 9, 19)) == 0


# --- registro de desfecho, sem tocar no banco -----------------------------------


def test_atualizar_status_agendamento_delega_ao_repositorio(monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        logic.repositories,
        "atualizar_status_agendamento",
        lambda id_agendamento, status: chamadas.append((id_agendamento, status)),
    )

    logic.atualizar_status_agendamento("a1", logic.STATUS_NO_SHOW)

    assert chamadas == [("a1", "no_show")]


def test_atualizar_status_agendamento_traduz_falha_em_erro_persistencia(monkeypatch):
    def _levanta(id_agendamento, status):
        raise RuntimeError("conexão recusada")

    monkeypatch.setattr(logic.repositories, "atualizar_status_agendamento", _levanta)

    with pytest.raises(logic.ErroPersistencia):
        logic.atualizar_status_agendamento("a1", logic.STATUS_CONCLUIDO)


def test_dias_ate_consulta_nao_fica_negativo_para_data_passada():
    """Data no passado é erro de preenchimento, mas o modelo nunca viu
    antecedência negativa em treino -- 0 é o valor mais próximo do domínio
    real, e o schema (`>= 0`) rejeitaria o negativo de qualquer forma."""
    assert logic.dias_ate_consulta(date(2026, 9, 10), hoje=date(2026, 9, 19)) == 0


def test_status_banco_sem_configuracao_nao_levanta(monkeypatch):
    """A sidebar pinta um rótulo -- não pode exigir try/except de quem chama
    nem derrubar a UI quando o banco não está configurado."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    import db.client as client_module

    monkeypatch.setattr(client_module, "_cliente", None)

    status = logic.status_banco()

    assert status["conectado"] is False
    assert "não configurado" in status["detalhe"]


# --- seleção de backend --------------------------------------------------------


@pytest.mark.parametrize(
    "valor, classe_esperada",
    [
        (None, logic.ClientePredicaoEmProcesso),  # default = ADR-005 (b)
        ("processo", logic.ClientePredicaoEmProcesso),
        ("api", logic.ClientePredicaoAPI),
        ("API", logic.ClientePredicaoAPI),
    ],
)
def test_obter_cliente_respeita_predict_backend(valor, classe_esperada, monkeypatch):
    monkeypatch.delenv("PREDICT_BACKEND", raising=False)
    if valor is not None:
        monkeypatch.setenv("PREDICT_BACKEND", valor)

    assert isinstance(logic.obter_cliente(), classe_esperada)


def test_backend_desconhecido_falha_alto_em_vez_de_cair_num_default(monkeypatch):
    """Escolher silenciosamente um backend muda o modo de falha da aplicação
    inteira -- melhor quebrar no startup da UI do que em produção."""
    monkeypatch.setenv("PREDICT_BACKEND", "carteiro")

    with pytest.raises(ValueError, match="PREDICT_BACKEND inválido"):
        logic.obter_cliente()


# --- CPF: identificador automático do cadastro -----------------------------------


@pytest.mark.parametrize(
    "cpf",
    ["111.444.777-35", "11144477735", "  111.444.777-35  "],
)
def test_cpf_valido_aceita_numero_com_digito_verificador_correto(cpf):
    assert logic.cpf_valido(cpf) is True


@pytest.mark.parametrize(
    "cpf",
    [
        "111.444.777-36",  # dígito verificador errado
        "111.111.111-11",  # todos os dígitos iguais (formalmente "válido", mas descartado)
        "123.456.789-00",
        "123",
        "",
    ],
)
def test_cpf_valido_rejeita_numero_incoerente(cpf):
    assert logic.cpf_valido(cpf) is False


def test_id_paciente_externo_de_cpf_e_deterministico_e_nao_e_o_cpf_em_si():
    cpf = "111.444.777-35"

    hash1 = logic._id_paciente_externo_de_cpf(cpf)
    hash2 = logic._id_paciente_externo_de_cpf("11144477735")  # mesma pessoa, formatação diferente

    assert hash1 == hash2
    assert hash1 not in (cpf, "11144477735")  # nunca o CPF cru, formatado ou não
    assert len(hash1) == 64  # sha256 em hexadecimal


# --- telefone: contato de envio real do lembrete ----------------------------


@pytest.mark.parametrize(
    "telefone,esperado",
    [
        ("(11) 98765-4321", "5511987654321"),
        ("11987654321", "5511987654321"),  # sem código do país -- assume Brasil
        ("+55 11 98765-4321", "5511987654321"),  # já com código do país
        ("5511987654321", "5511987654321"),
    ],
)
def test_normalizar_telefone_garante_codigo_do_pais(telefone, esperado):
    assert logic.normalizar_telefone(telefone) == esperado


@pytest.mark.parametrize("telefone", ["(11) 98765-4321", "11987654321", "5511987654321"])
def test_telefone_valido_aceita_numero_brasileiro_com_ddd(telefone):
    assert logic.telefone_valido(telefone) is True


@pytest.mark.parametrize("telefone", ["123", "", "11-abc", "1"])
def test_telefone_valido_rejeita_numero_incoerente(telefone):
    assert logic.telefone_valido(telefone) is False


# --- horários do cadastro respeitam a grade da clínica (agenda_clinica) -------


def test_horarios_disponiveis_delega_para_agenda_clinica():
    domingo = date(2026, 1, 11)  # ver ANCORA em scripts/gerar_timestamp_sintetico.py
    assert logic.horarios_disponiveis(domingo) == []


def test_proxima_data_disponivel_nunca_cai_num_domingo():
    domingo = date(2026, 1, 11)
    assert logic.proxima_data_disponivel(domingo).weekday() != 6


# --- autenticação do funcionário (ADR-008) -------------------------------------
# O Supabase Auth de verdade é exercitado em tests/test_db.py (integracao,
# contra o Supabase CLI local); aqui só o transporte é trocado, para cobrir a
# tradução de cada resposta de erro sem depender de rede.


ID_FUNCIONARIO = "00000000-0000-0000-0000-000000000001"


class _AuthFalso:
    def __init__(self, erro=None, erro_no_sign_out=None):
        self.erro = erro
        self.erro_no_sign_out = erro_no_sign_out
        self.credenciais = []
        self.sign_outs = []

    def sign_in_with_password(self, credenciais):
        self.credenciais.append(credenciais)
        if self.erro:
            raise self.erro
        usuario = SimpleNamespace(id=ID_FUNCIONARIO, email=credenciais["email"])
        return SimpleNamespace(user=usuario)

    def sign_out(self, opcoes=None):
        self.sign_outs.append(opcoes)
        if self.erro_no_sign_out:
            raise self.erro_no_sign_out


def _fabrica(auth):
    return lambda: SimpleNamespace(auth=auth)


def _autenticar_com_erro(erro, senha="senha"):
    return logic.autenticar_funcionario(
        "a@clinica.test", senha, criar_client=_fabrica(_AuthFalso(erro))
    )


def test_autenticar_funcionario_devolve_sessao_com_email_normalizado():
    auth = _AuthFalso()
    agora = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

    sessao = logic.autenticar_funcionario(
        "  Recepcao@Clinica.TEST ", "senha-correta", criar_client=_fabrica(auth), agora=agora
    )

    assert auth.credenciais == [{"email": "recepcao@clinica.test", "password": "senha-correta"}]
    assert sessao.email == "recepcao@clinica.test"
    assert sessao.id_usuario == ID_FUNCIONARIO
    assert sessao.ultimo_uso == agora


def test_autenticar_funcionario_revoga_so_a_propria_sessao_do_supabase():
    """O token do Supabase não é usado pela UI -- revogar na hora, mas com
    escopo local: `global` derrubaria o mesmo funcionário em outro navegador."""
    auth = _AuthFalso()

    logic.autenticar_funcionario("a@clinica.test", "senha", criar_client=_fabrica(auth))

    assert auth.sign_outs == [{"scope": "local"}]


def test_falha_ao_revogar_token_nao_impede_o_login():
    auth = _AuthFalso(erro_no_sign_out=httpx.ConnectError("caiu"))

    sessao = logic.autenticar_funcionario("a@clinica.test", "senha", criar_client=_fabrica(auth))

    assert sessao.email == "a@clinica.test"


@pytest.mark.parametrize("email,senha", [("", "senha"), ("a@clinica.test", ""), ("   ", "x")])
def test_autenticar_funcionario_recusa_campo_vazio_sem_chamar_o_supabase(email, senha):
    auth = _AuthFalso()

    with pytest.raises(logic.ErroCredenciais, match="Informe e-mail e senha"):
        logic.autenticar_funcionario(email, senha, criar_client=_fabrica(auth))

    assert auth.credenciais == []


def test_senha_errada_e_email_inexistente_dao_a_mesma_mensagem():
    """Distinguir os dois casos diria a quem tenta adivinhar quais e-mails são
    da equipe -- o Supabase já responde igual, e a tradução não pode separar."""
    erro = AuthApiError("Invalid login credentials", 400, "invalid_credentials")

    with pytest.raises(logic.ErroCredenciais) as exc:
        _autenticar_com_erro(erro, senha="errada")

    assert str(exc.value) == logic.MENSAGEM_CREDENCIAIS_INVALIDAS


def test_conta_banida_explica_que_o_acesso_foi_desativado():
    erro = AuthApiError("User is banned", 400, "user_banned")

    with pytest.raises(logic.ErroCredenciais, match="desativado"):
        _autenticar_com_erro(erro)


@pytest.mark.parametrize(
    "erro",
    [
        AuthApiError("Rate limit", 429, "over_request_rate_limit"),
        AuthApiError("boom", 500, "unexpected_failure"),
        AuthRetryableError("gateway", 503),
        httpx.ConnectError("sem rede"),
        AuthApiError("Email logins are disabled", 422, "email_provider_disabled"),
    ],
    ids=["limite-de-tentativas", "erro-500", "gateway", "sem-rede", "provedor-email-desligado"],
)
def test_falha_do_servico_de_auth_nao_culpa_quem_digitou(erro):
    with pytest.raises(logic.ErroAutenticacaoIndisponivel):
        _autenticar_com_erro(erro)


def test_autenticar_sem_supabase_configurado_e_indisponibilidade(monkeypatch):
    # Vazio, não delenv -- mesmo motivo de tests/test_ui_smoke.py::_rodar.
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")

    with pytest.raises(logic.ErroAutenticacaoIndisponivel, match="SUPABASE_URL"):
        logic.autenticar_funcionario("a@clinica.test", "senha")


def test_login_usa_client_descartavel_nunca_o_singleton_do_backend(monkeypatch):
    """Regressão de desenho (ADR-008): o supabase-py troca o Authorization do
    client pelo JWT do usuário depois do sign-in. No singleton, o backend
    passaria a consultar como `authenticated` (RLS sem policies -> tabela
    vazia) para TODOS os navegadores conectados ao mesmo processo."""
    import db.client as client_module

    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:54321")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_teste")
    monkeypatch.setattr(client_module, "_cliente", None)

    singleton = client_module.obter_client()
    primeiro = client_module.criar_client_autenticacao()
    segundo = client_module.criar_client_autenticacao()

    assert primeiro is not singleton
    assert primeiro is not segundo
    assert primeiro.auth is not singleton.auth


def test_sessao_expira_so_depois_da_inatividade_maxima():
    inicio = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)
    sessao = logic.SessaoFuncionario("id", "a@clinica.test", ultimo_uso=inicio)

    assert logic.sessao_ativa(sessao, agora=inicio + logic.INATIVIDADE_MAXIMA)
    assert not logic.sessao_ativa(
        sessao, agora=inicio + logic.INATIVIDADE_MAXIMA + timedelta(seconds=1)
    )


def test_registrar_uso_zera_o_relogio_de_inatividade():
    inicio = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)
    sessao = logic.SessaoFuncionario("id", "a@clinica.test", ultimo_uso=inicio)
    depois = inicio + logic.INATIVIDADE_MAXIMA - timedelta(minutes=1)

    renovada = logic.registrar_uso(sessao, agora=depois)

    assert logic.sessao_ativa(renovada, agora=depois + logic.INATIVIDADE_MAXIMA)
    assert renovada.email == sessao.email


# --- prazo do agendamento e do desfecho (2026-09-29) ----------------------------


def _cadastrar(data_consulta):
    logic.cadastrar_paciente_e_agendamento(
        cpf="529.982.247-25",
        telefone="(11) 98765-4321",
        data_nascimento=date(1990, 1, 1),
        sexo="F",
        especialidade="cardiologia",
        distancia_km=5.5,
        data_consulta=data_consulta,
        hora_consulta=time(10, 0),
        nome_completo="Ana Souza",
    )


@pytest.mark.parametrize(
    "data_consulta",
    [date(2026, 9, 28), date(2026, 9, 29) + timedelta(days=181)],
    ids=["passado", "alem-de-180-dias"],
)
def test_cadastro_recusa_consulta_no_passado_ou_alem_de_180_dias(monkeypatch, data_consulta):
    monkeypatch.setattr(logic, "hoje_na_clinica", lambda: date(2026, 9, 29))

    def _nao_deveria_ser_chamado(**kwargs):
        raise AssertionError("cadastro chegou ao banco com data inválida")

    monkeypatch.setattr(logic.repositories, "inserir_paciente", _nao_deveria_ser_chamado)

    with pytest.raises(logic.ErroValidacaoCadastro, match="Data da consulta"):
        _cadastrar(data_consulta)


def test_data_maxima_de_consulta_e_180_dias_a_frente():
    assert logic.data_maxima_de_consulta(date(2026, 9, 29)) == date(2027, 3, 28)


@pytest.mark.parametrize(
    "consulta_utc, esperado",
    [
        ("2026-09-29T21:00:00+00:00", True),  # hoje 18h em SP
        ("2026-09-28T13:00:00+00:00", True),  # ontem
        # 30/09 00h30 UTC ainda é 29/09 21h30 em SP -- a data vale no fuso da
        # clínica, não em UTC.
        ("2026-09-30T00:30:00+00:00", True),
        ("2026-09-30T13:00:00+00:00", False),  # amanhã
    ],
)
def test_desfecho_so_para_consulta_de_hoje_ou_anterior(consulta_utc, esperado):
    data_hora = datetime.fromisoformat(consulta_utc)
    assert logic.pode_registrar_desfecho(data_hora, hoje=date(2026, 9, 29)) is esperado


def test_desfecho_de_consulta_futura_nao_chega_ao_banco(monkeypatch):
    def _nao_deveria_ser_chamado(id_agendamento, status):
        raise AssertionError("desfecho de consulta futura chegou ao banco")

    monkeypatch.setattr(
        logic.repositories, "atualizar_status_agendamento", _nao_deveria_ser_chamado
    )
    amanha = datetime.now(timezone.utc) + timedelta(days=2)

    with pytest.raises(logic.ErroDesfechoForaDePrazo):
        logic.atualizar_status_agendamento("a1", logic.STATUS_NO_SHOW, data_hora_agendada=amanha)


def test_recusa_do_repositorio_por_prazo_vira_erro_de_prazo_e_nao_de_persistencia(monkeypatch):
    def _recusa(id_agendamento, status):
        raise repositories.DesfechoForaDePrazo("fora do prazo")

    monkeypatch.setattr(logic.repositories, "atualizar_status_agendamento", _recusa)

    with pytest.raises(logic.ErroDesfechoForaDePrazo):
        logic.atualizar_status_agendamento("a1", logic.STATUS_NO_SHOW)
