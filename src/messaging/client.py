"""
SaúdeJá — mensageria (disparo de lembrete), interface + stub.

Antecipado minimamente para o Passo 6: o job D-2 precisa acionar um envio
(ou decidir não acionar) para cada agendamento processado, mas a integração
real com Infobip é o Passo 7. Mesmo padrão já usado em `src/explain.py`
(`ExplicadorLLM`/`ExplicadorLLMDesativado`): uma interface + um stub que
nunca faz rede, para o job (e os testes do job) não dependerem da integração
real existir. O Passo 7 troca `StubMessagingClient` por `InfobipClient` sem
mudar o job -- só o valor de `MESSAGING_PROVIDER`.

Nota de PII: `pacientes` não guarda telefone (minimização por design, ver
architecture.md §4.1) -- o contato de fato mora no sistema core da clínica.
`enviar_lembrete` recebe `id_paciente_externo` (a referência que já temos),
não um telefone; resolver isso para um contato de envio real é decisão do
Passo 7/integração com Infobip, não deste módulo.
"""
from abc import ABC, abstractmethod


class MessagingClient(ABC):
    """Contrato único para o job (Passo 6) disparar lembretes, com stub
    (dev/test, sem custo) e implementação real (Passo 7) atrás da mesma
    assinatura."""

    @abstractmethod
    def enviar_lembrete(self, id_paciente_externo: str, mensagem: str) -> dict:
        ...


class StubMessagingClient(MessagingClient):
    """Default até o Passo 7. Nunca faz chamada de rede -- só registra que
    "seria enviado", para o job e os testes funcionarem sem custo/credenciais
    de Infobip."""

    def enviar_lembrete(self, id_paciente_externo: str, mensagem: str) -> dict:
        return {"status": "simulado", "id_paciente_externo": id_paciente_externo}
