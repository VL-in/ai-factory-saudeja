"""
SaúdeJá — testes da lógica da interface (Passo 4). Não sobem o runtime do
Streamlit: src/ui/logic.py existe justamente para ser testável assim.

Filosofia do repositório (sem mock pesado): o backend em processo usa o
data/model.pkl real (já versionado via DVC, sem re-treinar) e o backend REST
usa um TestClient do próprio app FastAPI como transporte -- a API de verdade,
sem subir servidor nem forjar a resposta. Só os caminhos de FALHA de rede são
simulados, porque não dá para desligar um servidor que não existe.
"""
from datetime import date, datetime, time

import httpx
import pytest
from fastapi.testclient import TestClient

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


def test_listar_especialidades_vem_do_mapa_do_modelo(especialidade_valida):
    especialidades = logic.listar_especialidades(MODEL_PATH)

    assert especialidade_valida in especialidades


def test_listar_especialidades_sem_modelo_nao_derruba_a_ui(tmp_path):
    """Formulário degrada para texto livre em vez de estourar exceção."""
    assert logic.listar_especialidades(str(tmp_path / "inexistente.pkl")) == []


# --- backend em processo (default, ADR-005 b) ---------------------------------


def test_em_processo_retorna_probabilidade_e_explicacao(payload):
    resultado = logic.ClientePredicaoEmProcesso(MODEL_PATH).predizer(payload)

    assert 0.0 <= resultado.probabilidade <= 1.0
    assert resultado.classe_prevista == int(
        resultado.probabilidade >= resultado.threshold_usado
    )
    assert resultado.explicacao  # SLO §4 -- 100% das predições com explicação
    assert resultado.explicacao_texto is None  # plug do LLM inativo (Passo 13)
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


# --- fila do dia e cadastro (Passo 5), sem tocar no banco ----------------------


def _item(probabilidade=None, classe_prevista=None, explicacao=None, id_externo="EXT-1"):
    return logic.ItemFila(
        id_agendamento="a1",
        id_paciente_externo=id_externo,
        especialidade="cardiologia",
        data_hora_agendada=datetime(2026, 9, 19, 10, 0),
        probabilidade=probabilidade,
        classe_prevista=classe_prevista,
        explicacao=explicacao or [],
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


def test_dias_ate_consulta_deriva_da_data_escolhida():
    assert logic.dias_ate_consulta(date(2026, 9, 30), hoje=date(2026, 9, 19)) == 11
    assert logic.dias_ate_consulta(date(2026, 9, 19), hoje=date(2026, 9, 19)) == 0


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
