"""
SaúdeJá — o que a interface mostra quando algo falha.

Duas regras:

1. **A recepção não vê detalhe técnico.** "Fila do dia", "Pedidos do titular" e
   o login usam o `logic` de verdade, com o banco trocado por falhas que
   imitam as reais: configuração ausente, banco sem migration, erro do
   PostgREST. Nenhum nome de variável de ambiente, código ou comando pode
   chegar à tela. Só a aba "Time técnico" mostra o diagnóstico.
2. **Página de erro em português e sem a exceção** (src/web/paginas/erro.py):
   endereço inexistente, falha ao montar a página e falha num clique.
"""
import pytest
from apoio_web import entrar_na_equipe, esperar_ate
from nicegui import ElementFilter, ui
from postgrest.exceptions import APIError

from db import repositories
from ui import logic
from web.paginas import equipe, erro

# O que denuncia detalhe técnico na tela da recepção.
TERMOS_TECNICOS = (
    "SUPABASE",
    "supabase",
    ".env",
    "PostgREST",
    "PGRST",
    "42703",
    "{",
    "'code'",
    "JWT",
    "migration",
    "Error",
)

ERRO_DO_POSTGREST = APIError(
    {"code": "PGRST301", "message": "JWT expired", "details": None, "hint": None}
)
SCHEMA_DESATUALIZADO = APIError(
    {"code": "42703", "message": "column pacientes_1.nome_completo does not exist"}
)


def _textos_da_aba(usuario, aba: str) -> list[str]:
    """Textos visíveis de uma aba da área da equipe. As três abas são montadas
    no login, e a "Time técnico" pode, por desenho, mostrar o diagnóstico."""
    with usuario:
        (painel,) = [p for p in ElementFilter(kind=ui.tab_panel) if p.props.get("name") == aba]
        return [
            str(getattr(elemento, "text", "") or elemento.props.get("label", "") or "")
            for elemento in ElementFilter(kind=ui.label).within(instance=painel)
        ]


def _sem_termo_tecnico(textos) -> None:
    for texto in textos:
        for termo in TERMOS_TECNICOS:
            assert termo not in texto, f"{termo!r} na tela da recepção: {texto!r}"


def _falhar_com(erro_do_banco):
    def _falha(*_args, **_kwargs):
        raise erro_do_banco

    return _falha


# --- 1. a recepção não vê detalhe técnico --------------------------------------------


async def test_fila_sem_banco_configurado_nao_mostra_a_variavel_de_ambiente(usuario):
    """Sem SUPABASE_URL (fixture `ambiente_da_interface`), como num Space mal
    configurado. Antes, a recepção lia "SUPABASE_URL não definida -- configure
    no .env (ver .env.example)"."""
    await entrar_na_equipe(usuario)
    await usuario.should_see("Não foi possível consultar a fila do dia", retries=20)

    textos = _textos_da_aba(usuario, equipe.ABA_FILA)
    _sem_termo_tecnico(textos)
    assert any(logic.MENSAGEM_AVISE_A_EQUIPE in texto for texto in textos)


@pytest.mark.parametrize(
    "erro_do_banco", [SCHEMA_DESATUALIZADO, ERRO_DO_POSTGREST], ids=["sem-migration", "postgrest"]
)
async def test_fila_com_erro_do_banco_nao_mostra_o_erro_cru(usuario, monkeypatch, erro_do_banco):
    monkeypatch.setattr(repositories, "buscar_fila_do_dia", _falhar_com(erro_do_banco))
    await entrar_na_equipe(usuario)
    await usuario.should_see("Não foi possível consultar a fila do dia", retries=20)

    _sem_termo_tecnico(_textos_da_aba(usuario, equipe.ABA_FILA))


async def test_falha_no_desfecho_avisa_sem_o_erro_cru(usuario, monkeypatch):
    from datetime import datetime

    from config_projeto import fuso_da_clinica

    item = logic.ItemFila(
        id_agendamento="0b9c6f2e-1d3a-4f5b-9c8d-7e6f5a4b3c2d",
        id_paciente_externo="f" * 64,
        especialidade="cardiologia",
        data_hora_agendada=datetime(2026, 1, 5, 9, 0, tzinfo=fuso_da_clinica()),
        probabilidade=0.8,
        classe_prevista=1,
        nome_completo="Paciente de Teste",
    )
    monkeypatch.setattr(logic, "buscar_fila_do_dia", lambda dia: [item])
    monkeypatch.setattr(
        repositories, "atualizar_status_agendamento", _falhar_com(ERRO_DO_POSTGREST)
    )
    await entrar_na_equipe(usuario)
    await usuario.should_see(marker="tabela-fila", retries=20)
    usuario.find(marker="tabela-fila").trigger("rowClick", [{}, {"id": item.id_agendamento}, 0])
    await usuario.should_see(marker="detalhe-paciente", retries=20)

    usuario.find("Cancelada").click()

    await esperar_ate(lambda: usuario.notify.messages)
    (mensagem,) = usuario.notify.messages
    assert mensagem.startswith("Não foi possível registrar o desfecho")
    _sem_termo_tecnico([mensagem])


async def test_pedidos_com_erro_do_banco_nao_mostram_o_erro_cru(usuario, monkeypatch):
    monkeypatch.setattr(logic, "buscar_fila_do_dia", lambda dia: [])
    monkeypatch.setattr(repositories, "pedidos_abertos", _falhar_com(ERRO_DO_POSTGREST))
    monkeypatch.setattr(repositories, "paciente_do_agendamento", _falhar_com(ERRO_DO_POSTGREST))
    await entrar_na_equipe(usuario)
    await usuario.should_see("Não foi possível listar os pedidos", retries=20)

    usuario.find(marker="pedido-codigo").type("0b9c6f2e-1d3a-4f5b-9c8d-7e6f5a4b3c2d")
    usuario.find(marker="pedido-registrar").click()

    await usuario.should_see("Não foi possível registrar o pedido", retries=20)
    _sem_termo_tecnico(_textos_da_aba(usuario, equipe.ABA_PEDIDOS))


async def test_login_sem_banco_configurado_nao_mostra_a_variavel_de_ambiente(usuario):
    await usuario.open("/equipe")
    usuario.find(marker="login-email").type("recepcao@clinica.test")
    usuario.find(marker="login-senha").type("senha")
    usuario.find(marker="login-entrar").click()

    await usuario.should_see(marker="aviso-erro", retries=20)
    with usuario:
        textos = [e.text for e in ElementFilter(marker="aviso-erro")]
    _sem_termo_tecnico(textos)
    assert any(logic.MENSAGEM_AVISE_A_EQUIPE in texto for texto in textos)


async def test_time_tecnico_continua_mostrando_o_diagnostico(usuario):
    """O detalhe não sumiu: ele mudou de lugar. É na aba técnica que quem
    mantém o sistema descobre o que falta configurar."""
    await entrar_na_equipe(usuario)

    await esperar_ate(
        lambda: any(
            "SUPABASE_URL" in texto for texto in _textos_da_aba(usuario, equipe.ABA_TIME_TECNICO)
        ),
        timeout=10,
    )


# --- 2. página de erro em português e sem a exceção ----------------------------------


async def test_endereco_inexistente_mostra_pagina_propria_em_portugues(usuario):
    resposta = await usuario.http_client.get("/nao-existe?cpf=11144477735")

    assert resposta.status_code == 404
    assert erro.TITULO_NAO_ENCONTRADA in resposta.text
    assert "This page doesn" not in resposta.text
    assert "HTTPException" not in resposta.text
    assert "11144477735" not in resposta.text


async def test_falha_ao_montar_a_pagina_nao_mostra_a_excecao(usuario, caplog):
    @ui.page("/quebrada-na-montagem")
    def pagina_quebrada() -> None:
        raise FileNotFoundError("/app/docs/LGPD/POLITICA-DE-PRIVACIDADE.md")

    resposta = await usuario.http_client.get("/quebrada-na-montagem")

    assert resposta.status_code == 500
    assert erro.TITULO_ERRO_INESPERADO in resposta.text
    assert "Server error" not in resposta.text
    assert "FileNotFoundError" not in resposta.text
    assert "/app/docs" not in resposta.text
    # A exceção continua no log, para a equipe técnica. O ERROR é o esperado
    # aqui; a fixture `usuario` falharia o teste por ele.
    assert any(r.levelname == "ERROR" for r in caplog.records)
    caplog.clear()


async def test_falha_num_clique_avisa_que_a_acao_nao_foi_concluida(usuario, caplog):
    """Antes, um clique que levantava exceção não dava resposta nenhuma: o
    NiceGUI só logava. No agendamento, o botão ficava desabilitado e mudo."""

    @ui.page("/clique-quebrado")
    def pagina() -> None:
        def _quebrar() -> None:
            raise RuntimeError("detalhe interno")

        ui.button("Quebrar", on_click=_quebrar)

    await usuario.open("/clique-quebrado")
    usuario.find("Quebrar").click()

    await esperar_ate(lambda: usuario.notify.messages)
    assert usuario.notify.messages == [erro.MENSAGEM_ACAO_NAO_CONCLUIDA]
    assert any(r.levelname == "ERROR" for r in caplog.records)
    caplog.clear()
