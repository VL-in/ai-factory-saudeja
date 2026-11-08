"""
SaúdeJá — o que os testes da interface pressupõem do simulador do NiceGUI.

Spike da migração para o NiceGUI (ADR-013), mantido como teste: se uma versão
nova do NiceGUI mudar alguma destas premissas, os testes de tela passariam a
afirmar coisas que não acontecem no navegador, e é aqui que isso aparece
primeiro.
"""
import os

import httpx
import pytest
from apoio_web import UsuarioDaAba
from nicegui import app, core, ui
from nicegui.storage import Storage
from nicegui.testing.general import prepare_simulation
from nicegui.testing.user import User


def _registrar_paginas_do_spike():
    """Páginas registradas por função, depois do reset de globais do
    simulador, que é como `web.main.configurar_paginas` as registra."""

    @ui.page("/spike/grava")
    async def grava():
        await ui.context.client.connected()
        app.storage.tab["marca"] = "da aba"
        ui.label("gravado")

    @ui.page("/spike/le")
    async def le():
        await ui.context.client.connected()
        ui.label(f"lido: {app.storage.tab.get('marca', 'nada')}")


@pytest.fixture
async def cliente_http(nicegui_reset_globals):
    os.environ["NICEGUI_USER_SIMULATION"] = "true"
    try:
        prepare_simulation()
        _registrar_paginas_do_spike()
        async with core.app.router.lifespan_context(core.app), httpx.AsyncClient(
            transport=httpx.ASGITransport(core.app), base_url="http://test"
        ) as cliente:
            yield cliente
    finally:
        os.environ.pop("NICEGUI_USER_SIMULATION", None)


async def test_pagina_registrada_por_funcao_aparece_no_simulador(cliente_http):
    usuario = UsuarioDaAba(cliente_http)
    await usuario.open("/spike/grava")
    await usuario.should_see("gravado")


async def test_storage_da_aba_funciona_sem_storage_secret(cliente_http):
    """A sessão da equipe vive em `app.storage.tab`, e a casca não passa
    `storage_secret` (que ligaria o cookie de sessão e o armazenamento em
    disco de `app.storage.user`)."""
    assert Storage.secret is None
    usuario = UsuarioDaAba(cliente_http)
    await usuario.open("/spike/grava")
    await usuario.open("/spike/le")
    await usuario.should_see("lido: da aba")


async def test_user_do_nicegui_mantem_a_aba_entre_paginas(cliente_http):
    """Até o NiceGUI 3.2.0, o `User` original sorteava um `tab_id` a cada
    `open()` e parecia perder o `app.storage.tab` na navegação, o que no
    navegador não acontece; `UsuarioDaAba` corrigia isso. Se o `User` voltar
    a trocar de aba, `UsuarioDaAba` precisa fixar o `tab_id` de novo."""
    usuario = User(cliente_http)
    await usuario.open("/spike/grava")
    await usuario.open("/spike/le")
    await usuario.should_see("lido: da aba")
