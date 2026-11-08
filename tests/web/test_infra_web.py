"""
SaúdeJá — infraestrutura da interface em NiceGUI (src/web/main.py, ADR-013).

Saúde para a sonda externa, nada de terceiros no navegador, contexto de
telemetria levado às threads, memória por aba com prazo e nada gravado em
disco. Os testes que precisam do FastAPI da casca (saúde, HTML servido) montam
`web.main.criar_app()` com o `TestClient`; o simulador do NiceGUI fala direto
com o app interno dele e não enxerga essas rotas.
"""
import asyncio
import re
import time

import pytest
from fastapi.testclient import TestClient
from nicegui import app as app_nicegui
from nicegui.app.app import prune_tab_storage
from nicegui.storage import Storage

import telemetria
from ui import logic
from web import execucao, sessao
from web import main as web_main

ROTAS_DE_PAGINA = ["/", "/equipe", "/privacidade", "/termos"]


@pytest.fixture
def cliente_da_casca(nicegui_reset_globals, ambiente_da_interface):
    with TestClient(web_main.criar_app()) as cliente:
        yield cliente


@pytest.mark.parametrize("rota", [web_main.ROTA_SAUDE, web_main.ROTA_SAUDE_LEGADA])
def test_saude_responde_ok_em_texto_puro(cliente_da_casca, rota):
    """A sonda do Synthetic Monitoring exige 2xx e corpo `ok` (ADR-006). O
    alias `/_stcore/health` mantém o check atual verde até ele trocar de rota."""
    resposta = cliente_da_casca.get(rota)

    assert resposta.status_code == 200
    assert resposta.text == "ok"
    assert resposta.headers["content-type"].startswith("text/plain")


def test_app_da_interface_nao_publica_documentacao_de_api(cliente_da_casca):
    for rota in ("/docs", "/redoc", "/openapi.json"):
        assert cliente_da_casca.get(rota).headers.get("X-Nicegui-Content") != "json"
        assert "swagger" not in cliente_da_casca.get(rota).text.lower()


_ORIGEM_EXTERNA = re.compile(
    r"""(?:src|href)\s*=\s*["']?(?:https?:)?//|@import\s+(?:url\()?["']?(?:https?:)?//"""
    r"""|url\(\s*["']?(?:https?:)?//""",
    re.IGNORECASE,
)


@pytest.mark.parametrize("rota", ROTAS_DE_PAGINA)
def test_pagina_nao_carrega_nada_de_outra_origem(cliente_da_casca, rota):
    """Fonte, ícones e scripts vêm da própria aplicação: uma fonte do Google
    Fonts entregaria o IP de quem abre a tela a mais um operador (ADR-013,
    regra 4)."""
    resposta = cliente_da_casca.get(rota)

    assert resposta.status_code == 200
    assert resposta.headers.get("X-Nicegui-Content") == "page"
    # O tema sai no HTML inicial (antes do websocket): é ele que traria a
    # fonte de fora, como na prova de conceito.
    assert "--sj-fundo" in resposta.text
    assert _ORIGEM_EXTERNA.findall(resposta.text) == []
    assert "fonts.googleapis" not in resposta.text


def test_a_checagem_de_origem_externa_enxerga_o_que_procura():
    """Controle negativo: o `<link>` da prova de conceito tem de ser achado."""
    poc = (
        '<link href="https://fonts.googleapis.com/css2?family=Inter" rel="stylesheet">'
        "<style>@import url('https://exemplo.test/x.css')</style>"
        '<script src="//cdn.exemplo.test/a.js"></script>'
    )
    assert len(_ORIGEM_EXTERNA.findall(poc)) == 3


def test_favicon_e_local(cliente_da_casca):
    resposta = cliente_da_casca.get("/favicon.ico")

    assert resposta.status_code == 200
    assert "svg" in resposta.headers["content-type"]


async def test_executar_em_thread_leva_a_sessao_e_o_span_corrente(telemetria_em_memoria):
    """O `run.io_bound` do NiceGUI perderia os dois: o span de `_acao_ui`
    nasceria sem pai e sem `session.id`."""
    telemetria.definir_sessao(execucao.sessao_da_telemetria)
    marca = execucao.SESSAO_TELEMETRIA.set("aba-de-teste")
    try:
        with telemetria.span("clique"):
            await execucao.executar_em_thread(_abrir_span_filho)
    finally:
        execucao.SESSAO_TELEMETRIA.reset(marca)
        telemetria.definir_sessao(None)

    filho, pai = telemetria_em_memoria.spans()
    assert filho.name == "na_thread"
    assert filho.parent is not None and filho.parent.span_id == pai.context.span_id
    assert filho.attributes["session.id"] == "aba-de-teste"


def _abrir_span_filho():
    with telemetria.span("na_thread"):
        pass


async def test_executar_em_thread_nao_segura_o_event_loop():
    """Enquanto a thread dorme, o loop continua atendendo outras tarefas (a
    sonda do `/healthz`, as outras abas)."""
    atendidas = []

    async def outra_tarefa():
        atendidas.append(time.monotonic())

    inicio = time.monotonic()
    await asyncio.gather(execucao.executar_em_thread(time.sleep, 0.3), outra_tarefa())

    assert atendidas and atendidas[0] - inicio < 0.2


async def test_aba_parada_some_da_memoria_no_prazo_da_inatividade(usuario):
    """O padrão do NiceGUI guarda a aba fechada por 30 dias, com e-mail e id
    do funcionário na memória do processo."""
    assert app_nicegui.storage.max_tab_storage_age == logic.INATIVIDADE_MAXIMA.total_seconds()
    await usuario.open("/")
    with usuario:
        sessao.aba()["marca"] = "x"
    aba = app_nicegui.storage._tabs[usuario.tab_id]
    aba.last_modified = time.time() - logic.INATIVIDADE_MAXIMA.total_seconds() - 1

    await prune_tab_storage()

    assert usuario.tab_id not in app_nicegui.storage._tabs
    # A página continua aberta: o acesso seguinte recria a aba vazia, em vez
    # de derrubar o handler com o AssertionError do NiceGUI.
    with usuario:
        assert "marca" not in sessao.aba()


async def test_percorrer_as_paginas_nao_grava_nada_em_disco(
    usuario, tmp_path, monkeypatch, nicegui_reset_globals
):
    """`/app` é só de leitura para o uid 1000 no Space, e nada da interface
    pode depender de gravar em disco."""
    monkeypatch.setattr(Storage, "path", tmp_path / "nicegui")
    for rota in ROTAS_DE_PAGINA:
        await usuario.open(rota)

    assert not (tmp_path / "nicegui").exists()
    assert not Storage.path.exists()
