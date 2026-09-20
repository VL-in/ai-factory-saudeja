"""
SaúdeJá — testes do stub de mensageria (antecipado no Passo 6 para o job
D-2; a integração real com Infobip fica para o Passo 7). Prova que o stub
nunca faz chamada de rede, para o job (Passo 6) e seus testes poderem
depender dele sem custo/credenciais.
"""
import httpx
import pytest

from messaging.client import StubMessagingClient


def test_stub_nunca_chama_rede(monkeypatch):
    def _explode(*args, **kwargs):
        raise AssertionError("StubMessagingClient não deveria fazer nenhuma chamada de rede")

    monkeypatch.setattr(httpx.Client, "request", _explode)
    monkeypatch.setattr(httpx.Client, "send", _explode)

    resultado = StubMessagingClient().enviar_lembrete(
        id_paciente_externo="EXT-1", mensagem="teste"
    )

    assert resultado["status"] == "simulado"
    assert resultado["id_paciente_externo"] == "EXT-1"


def test_stub_implementa_o_mesmo_contrato_que_o_real_vai_usar():
    """Teste de contrato mínimo (Passo 7 substitui o stub por InfobipClient
    atrás da mesma assinatura, sem mudar o job)."""
    cliente = StubMessagingClient()
    assert callable(cliente.enviar_lembrete)
    resultado = cliente.enviar_lembrete(id_paciente_externo="EXT-2", mensagem="oi")
    assert isinstance(resultado, dict)


def test_provedor_desconhecido_levanta_erro_claro(monkeypatch):
    import jobs.inferencia_diaria as job

    monkeypatch.setenv("MESSAGING_PROVIDER", "infobip")

    with pytest.raises(job.ProvedorMensageriaDesconhecido, match="infobip"):
        job.obter_cliente_mensageria()
