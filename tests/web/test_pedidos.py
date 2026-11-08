"""
SaúdeJá — aba "Pedidos do titular" na interface em NiceGUI (linhas P1 e P2 da
matriz da migração).
"""
from apoio_web import entrar_na_equipe, esperar_ate, textos
from nicegui import ElementFilter

from ui import logic
from web.paginas import pedidos

CODIGO = "0b9c6f2e-1d3a-4f5b-9c8d-7e6f5a4b3c2d"
CPF_VALIDO = "111.444.777-35"


def _sem_fila(monkeypatch):
    monkeypatch.setattr(logic, "buscar_fila_do_dia", lambda dia: [])


async def test_registra_e_encaminha_o_treino_sem_revelar_se_havia_dado(usuario, monkeypatch):
    """P1. A tela não diz se havia dado de aprimoramento: diria a quem
    digitasse um CPF alheio se aquela pessoa autorizou o treino."""
    _sem_fila(monkeypatch)
    registrados, encaminhados = [], []
    monkeypatch.setattr(
        logic, "registrar_pedido_da_clinica", lambda cod, tipo: registrados.append((cod, tipo))
    )
    monkeypatch.setattr(logic, "encaminhar_pedido_ao_treino", encaminhados.append)
    monkeypatch.setattr(logic, "listar_pedidos_da_clinica", lambda: [])
    await entrar_na_equipe(usuario)

    usuario.find(marker="pedido-codigo").type(CODIGO)
    usuario.find(marker="pedido-treino").click()
    usuario.find(marker="pedido-cpf").type(CPF_VALIDO)
    usuario.find(marker="pedido-registrar").click()

    await usuario.should_see(marker="aviso-ok", retries=20)
    assert registrados == [(CODIGO, "eliminacao")]
    assert encaminhados == [CPF_VALIDO]
    (sucesso,) = textos(usuario, marker="aviso-ok")
    assert "encaminhado ao SaúdeJá" in sucesso
    assert "não havia" not in sucesso and "não encontrado" not in sucesso
    with usuario:
        (cpf,) = ElementFilter(marker="pedido-cpf")
        assert cpf.value == ""  # o CPF não fica na tela


async def test_pedido_recusado_avisa_e_limpa_o_cpf(usuario, monkeypatch):
    _sem_fila(monkeypatch)
    monkeypatch.setattr(logic, "listar_pedidos_da_clinica", lambda: [])
    await entrar_na_equipe(usuario)

    usuario.find(marker="pedido-codigo").type("codigo-invalido")
    usuario.find(marker="pedido-cpf").type(CPF_VALIDO)
    usuario.find(marker="pedido-registrar").click()

    await usuario.should_see("Código do agendamento inválido", retries=20)
    with usuario:
        (cpf,) = ElementFilter(marker="pedido-cpf")
        (codigo,) = ElementFilter(marker="pedido-codigo")
        assert cpf.value == ""
        assert codigo.value == "codigo-invalido"  # para corrigir sem redigitar


async def test_lista_e_decide(usuario, monkeypatch):
    """P2."""
    _sem_fila(monkeypatch)
    decisoes = []
    monkeypatch.setattr(
        logic,
        "listar_pedidos_da_clinica",
        lambda: [
            {"id": "p1", "tipo": "eliminacao", "situacao": "pendente",
             "criado_em": "2026-10-01T10:00:00+00:00", "confirmado_em": None},
            {"id": "p2", "tipo": "revisao_decisao", "situacao": "confirmado_pela_clinica",
             "criado_em": "2026-10-02T10:00:00+00:00",
             "confirmado_em": "2026-10-03T10:00:00+00:00"},
        ],
    )
    monkeypatch.setattr(
        logic, "decidir_pedido_da_clinica", lambda id_, decisao: decisoes.append((id_, decisao))
    )
    await entrar_na_equipe(usuario)

    await usuario.should_see(marker="pedido-confirmar-p1", retries=20)
    await usuario.should_see(marker="pedido-recusar-p1")
    await usuario.should_see(marker="pedido-concluir-p2")
    usuario.find(marker="pedido-confirmar-p1").click()

    await esperar_ate(lambda: decisoes)
    assert decisoes == [("p1", "confirmar")]


async def test_lista_sem_supabase_avisa_sem_derrubar_a_aba(usuario, monkeypatch):
    _sem_fila(monkeypatch)
    await entrar_na_equipe(usuario)

    await usuario.should_see(marker="pedido-registrar", retries=20)
    with usuario:
        erros = list(ElementFilter(marker="aviso-erro").within(marker="lista-pedidos"))
    assert len(erros) == 1


def test_eliminacao_confirmada_nao_tem_acao_na_tela():
    """O workflow de purga a executa; a clínica não tem mais o que decidir."""
    confirmada = {"tipo": "eliminacao", "situacao": "confirmado_pela_clinica"}
    assert pedidos.acoes_do_pedido(confirmada) == []
