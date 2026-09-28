"""
SaúdeJá — mensageria (disparo de lembrete), interface + stub + Infobip real
(Passo 7).

Mesmo padrão já usado em `src/explain.py` (`ExplicadorLLM`/
`ExplicadorLLMDesativado`): uma interface + um stub que nunca faz rede, para
o job (e os testes do job) não dependerem da integração real existir, e uma
implementação real atrás da mesma assinatura, trocada só por
`MESSAGING_PROVIDER`.

Nota de PII: `pacientes` não guarda nome/CPF/email (minimização por design,
ver architecture.md §4.1) -- mas guarda `telefone` desde a migration
`20260920020000_telefone_paciente.sql`: é a exceção deliberada, porque sem um
contato de envio o produto (lembrete pago para reduzir no-show) não tem para
onde mandar mensagem. `enviar_lembrete` recebe o telefone já normalizado
(`ui/logic.py::normalizar_telefone`), não o `id_paciente_externo`.
"""
import os
from abc import ABC, abstractmethod

import httpx


class MessagingClient(ABC):
    """Contrato único para o job (Passo 6) disparar lembretes, com stub
    (dev/test, sem custo) e implementação real (Passo 7) atrás da mesma
    assinatura. `canal` identifica quem enviou, para
    `repositories.registrar_mensagem` auditar sem o job precisar saber o
    provedor concreto."""

    canal: str

    @abstractmethod
    def enviar_lembrete(self, telefone: str, mensagem: str) -> dict:
        ...


class StubMessagingClient(MessagingClient):
    """Default até `MESSAGING_PROVIDER=infobip` ser ligado. Nunca faz chamada
    de rede -- só registra que "seria enviado", para o job e os testes
    funcionarem sem custo/credenciais de Infobip."""

    canal = "stub"

    def enviar_lembrete(self, telefone: str, mensagem: str) -> dict:
        return {"status": "simulado", "telefone": telefone}


class ConfiguracaoInfobipAusente(Exception):
    """`INFOBIP_BASE_URL`/`INFOBIP_CHAVE_API` ausentes com
    `MESSAGING_PROVIDER=infobip` -- erro explícito na hora de instanciar o
    cliente, não uma falha de rede confusa no primeiro envio."""


class ErroEnvioInfobip(Exception):
    """Infobip respondeu com erro (credenciais inválidas, número não
    verificado no sandbox de teste, payload rejeitado) ou não respondeu --
    o job (Passo 6) trata isso como falha de envio de UM agendamento, não
    deixa a exceção crua derrubar o processamento do resto da fila do dia.

    **A mensagem não carrega o corpo da resposta** (mudança do Passo 8): a
    Infobip ecoa o payload enviado no corpo de erro, com o `to` -- o telefone
    do paciente -- dentro. Essa string ia inteira para `resultado["erros"]`
    do job, era exibida crua por `st.json` na aba de dev e vazaria em qualquer
    `logger.warning(f"...{exc}")` futuro. O que fica na mensagem é o par
    (status HTTP, `messageId` da Infobip), que é o que identifica a causa --
    foi assim que se diagnosticou `EC_ACCOUNT_NOT_PROVISIONED_FOR_CHANNEL` no
    Passo 7.

    `corpo_bruto` guarda a resposta completa para inspeção em depurador, e
    **não deve ser logado nem exibido**: é o único lugar do repositório onde
    telefone de paciente pode aparecer em texto livre. O filtro de
    `src/logging_config.py` cobre o caso de alguém esquecer isso; este
    atributo existe para não perder a capacidade de diagnosticar uma falha
    nova, não para ser impresso.
    """

    def __init__(self, mensagem: str, corpo_bruto: str | None = None):
        super().__init__(mensagem)
        self.corpo_bruto = corpo_bruto


class InfobipClient(MessagingClient):
    """Implementação real (Passo 7), via SMS da Infobip.

    SMS em vez de WhatsApp Business: WhatsApp exige sender/template
    pré-aprovados pela Meta, inconciliável com o prazo/sandbox da disciplina;
    a interface (`MessagingClient`) já comporta trocar de canal depois sem
    mudar o job. Autenticação por API Key (`Authorization: App <chave>`),
    formato descrito em
    https://www.infobip.com/docs/essentials/api-essentials/api-authentication
    -- não é Bearer/OAuth, é o esquema "App" próprio da Infobip.
    """

    canal = "sms"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        remetente: str | None = None,
        cliente_http: httpx.Client | None = None,
    ):
        base_url = base_url or os.environ.get("INFOBIP_BASE_URL")
        api_key = api_key or os.environ.get("INFOBIP_CHAVE_API")
        if not base_url or not api_key:
            raise ConfiguracaoInfobipAusente(
                "INFOBIP_BASE_URL/INFOBIP_CHAVE_API ausentes -- ver .env.example"
            )
        # .env guarda só o host (ex. "y4ey59.api.infobip.com"), sem esquema --
        # aceita também um valor já com "https://" para não quebrar quem
        # copiar a URL completa da documentação.
        host = base_url.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
        self._remetente = remetente or os.environ.get("INFOBIP_REMETENTE") or "SaudeJa"
        self._http = cliente_http or httpx.Client(
            base_url=f"https://{host}",
            headers={
                "Authorization": f"App {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=10.0,
        )

    def enviar_lembrete(self, telefone: str, mensagem: str) -> dict:
        payload = {
            "messages": [
                {"destinations": [{"to": telefone}], "from": self._remetente, "text": mensagem}
            ]
        }
        try:
            resposta = self._http.post("/sms/2/text/advanced", json=payload)
        except httpx.HTTPError as exc:
            raise ErroEnvioInfobip(
                f"Infobip não respondeu ({exc.__class__.__name__})"
            ) from exc

        if resposta.status_code >= 400:
            raise ErroEnvioInfobip(
                f"Infobip respondeu HTTP {resposta.status_code} ({_codigo_de_erro(resposta)})",
                corpo_bruto=resposta.text,
            )
        return resposta.json()


def _codigo_de_erro(resposta: httpx.Response) -> str:
    """`messageId` da resposta de erro da Infobip (ex.
    `EC_ACCOUNT_NOT_PROVISIONED_FOR_CHANNEL`) -- o campo que de fato diz a
    causa, sem trazer o corpo inteiro com o telefone do destinatário junto
    (ver `ErroEnvioInfobip`).

    Formato documentado em
    https://www.infobip.com/docs/essentials/response-status-and-error-codes
    (`requestError.serviceException.messageId`). Resposta fora desse formato
    (proxy, HTML de gateway) devolve um rótulo genérico: não vale arriscar
    devolver um pedaço de texto arbitrário justamente na string que vai para
    o log."""
    try:
        excecao = resposta.json()["requestError"]["serviceException"]
    except (ValueError, KeyError, TypeError):
        return "sem código de erro no corpo"
    codigo = excecao.get("messageId")
    return str(codigo) if codigo else "sem código de erro no corpo"
