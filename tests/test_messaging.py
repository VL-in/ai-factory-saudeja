"""
SaúdeJá — testes de src/messaging/client.py (Passo 7).

Filosofia igual à do resto do repositório (tests/conftest.py): sem mock
pesado do zero -- `httpx.MockTransport` é a própria biblioteca simulando a
camada de rede, não uma reimplementação da lógica de request/response do
cliente. Prova duas coisas: o stub nunca faz rede de verdade (mesmo que
alguém troque `httpx` de baixo dele) e o `InfobipClient` monta a requisição
certa e trata erro sem derrubar o chamador.
"""
import httpx
import pytest

from messaging.client import (
    ConfiguracaoInfobipAusente,
    ErroEnvioInfobip,
    InfobipClient,
    MessagingClient,
    StubMessagingClient,
)


def test_stub_nunca_faz_chamada_de_rede(monkeypatch):
    """Mesmo que algo tente abrir uma conexão de baixo do stub, o teste
    falha -- prova que `StubMessagingClient` é seguro para rodar em dev/CI
    sem credenciais nem custo."""

    def _rede_proibida(*args, **kwargs):
        raise AssertionError("StubMessagingClient não deveria tocar a rede")

    monkeypatch.setattr(httpx.Client, "post", _rede_proibida)
    monkeypatch.setattr(httpx.Client, "send", _rede_proibida)

    resultado = StubMessagingClient().enviar_lembrete(
        telefone="5511987654321", mensagem="teste"
    )

    assert resultado["status"] == "simulado"


def test_stub_e_infobip_expoem_o_mesmo_contrato():
    """Contrato de assinatura: o job (`inferencia_diaria.py`) troca de
    provedor só via `MESSAGING_PROVIDER`, sem saber qual classe concreta está
    por trás -- os dois precisam aceitar exatamente `(telefone, mensagem)`."""
    assert issubclass(StubMessagingClient, MessagingClient)
    assert issubclass(InfobipClient, MessagingClient)
    assert StubMessagingClient.canal == "stub"
    assert InfobipClient.canal == "sms"


def test_infobip_client_sem_credenciais_falha_explicito(monkeypatch):
    monkeypatch.delenv("INFOBIP_BASE_URL", raising=False)
    monkeypatch.delenv("INFOBIP_CHAVE_API", raising=False)

    with pytest.raises(ConfiguracaoInfobipAusente):
        InfobipClient()


def _cliente_infobip_com_transporte(handler) -> InfobipClient:
    """`cliente_http` injetado já precisa vir com os headers que
    `InfobipClient.__init__` normalmente monta (Authorization/Content-Type)
    -- passar um cliente pronto substitui o transporte por completo, não só
    a URL de destino."""
    http = httpx.Client(
        base_url="https://y4ey59.api.infobip.com",
        headers={"Authorization": "App chave-de-teste", "Content-Type": "application/json"},
        transport=httpx.MockTransport(handler),
    )
    return InfobipClient(
        base_url="y4ey59.api.infobip.com", api_key="chave-de-teste", cliente_http=http
    )


def test_infobip_client_envia_sms_com_autenticacao_app_e_destino_certo():
    capturado = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["url"] = str(request.url)
        capturado["auth"] = request.headers["Authorization"]
        capturado["body"] = request.read()
        return httpx.Response(200, json={"messages": [{"status": {"groupName": "PENDING"}}]})

    cliente = _cliente_infobip_com_transporte(handler)
    resultado = cliente.enviar_lembrete(telefone="5511987654321", mensagem="Confirma?")

    assert capturado["url"].endswith("/sms/2/text/advanced")
    assert capturado["auth"] == "App chave-de-teste"
    assert b'"5511987654321"' in capturado["body"]
    assert resultado["messages"][0]["status"]["groupName"] == "PENDING"


def test_infobip_client_levanta_erro_proprio_em_falha_http():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    cliente = _cliente_infobip_com_transporte(handler)

    with pytest.raises(ErroEnvioInfobip, match="401"):
        cliente.enviar_lembrete(telefone="5511987654321", mensagem="Confirma?")
