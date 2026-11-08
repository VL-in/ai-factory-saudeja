"""
SaúdeJá — fixtures dos testes da interface em NiceGUI (src/web/, ADR-013).

O simulador do NiceGUI (`nicegui.testing`) abre as páginas sem navegador e sem
servidor: as requisições vão por ASGI direto ao app interno do NiceGUI, e o
websocket é simulado. Três adaptações, todas por causa da forma deste
repositório:

1. **As fixtures são importadas aqui, não ligadas por `pytest_plugins`.** O
   pytest só aceita `pytest_plugins` no `conftest.py` da raiz, e ligado lá o
   plugin valeria para a suíte inteira, inclusive a regra de falhar todo teste
   com log `ERROR`, que os testes de falha de banco de `logic` produzem de
   propósito.
2. **A fixture `usuario` registra as páginas por `web.main.configurar_paginas()`**,
   em vez de executar um `main.py`: a casca não tem efeito colateral no import
   (o app é montado por fábrica, `web.main:criar_app`), e o simulador começa
   cada teste com as rotas zeradas por `nicegui_reset_globals`.
3. **As abas simuladas são `UsuarioDaAba`** (tests/web/apoio_web.py), que
   mantém o mesmo `tab_id` entre páginas, como o navegador.
"""
import os
from collections.abc import AsyncGenerator

import httpx
import pytest
from apoio_web import UsuarioDaAba
from nicegui import core, ui
from nicegui.functions.download import download
from nicegui.functions.navigate import Navigate
from nicegui.functions.notify import notify
from nicegui.testing.general import prepare_simulation
from nicegui.testing.general_fixtures import (  # noqa: F401 -- fixture do NiceGUI
    nicegui_reset_globals,
)


@pytest.fixture(autouse=True)
def caches_da_interface_limpos():
    """Os caches de `web.dados` vivem no processo inteiro: uma fila falsa
    guardada num teste apareceria no seguinte."""
    from web import dados

    dados.limpar_caches()
    yield
    dados.limpar_caches()


@pytest.fixture
def ambiente_da_interface(monkeypatch):
    """Mesmo isolamento dos testes da tela antiga: sem Supabase de verdade e
    produção por padrão.

    Vazio, não `delenv`: `config_projeto` carrega o `.env` no import, e uma
    variável apagada é justamente a que o dotenv definiria de novo, mandando a
    suíte para o projeto Supabase de quem tem `.env` local. String vazia é
    falsy, e `db/client.py` a trata como configuração ausente."""
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    monkeypatch.setenv("PREDICT_BACKEND", "processo")
    monkeypatch.setenv("APP_ENV", "prod")


@pytest.fixture
async def usuario(
    nicegui_reset_globals,  # noqa: F811 -- fixture importada acima
    ambiente_da_interface,
    caplog: pytest.LogCaptureFixture,
) -> AsyncGenerator[UsuarioDaAba, None]:
    """Uma aba de navegador simulada sobre as páginas de `src/web/`.

    Falha o teste se a página logar `ERROR`, como o plugin do NiceGUI: uma
    exceção num handler vira log, não exceção no teste."""
    from web import main as web_main

    os.environ["NICEGUI_USER_SIMULATION"] = "true"
    try:
        prepare_simulation()
        web_main.configurar_paginas()
        async with core.app.router.lifespan_context(core.app), httpx.AsyncClient(
            transport=httpx.ASGITransport(core.app), base_url="http://test"
        ) as cliente:
            yield UsuarioDaAba(cliente)
        erros = [r for r in caplog.get_records("call") if r.levelname == "ERROR"]
        if erros:
            pytest.fail(
                "a página logou ERROR: " + "; ".join(r.getMessage() for r in erros),
                pytrace=False,
            )
    finally:
        os.environ.pop("NICEGUI_USER_SIMULATION", None)
        ui.navigate = Navigate()
        ui.notify = notify
        ui.download = download


@pytest.fixture
async def outra_aba(usuario) -> AsyncGenerator[UsuarioDaAba, None]:
    """Segunda aba no mesmo processo, para os testes do que é compartilhado
    entre abas (o cache da fila) e do que não é (a sessão)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(core.app), base_url="http://test"
    ) as cliente:
        yield UsuarioDaAba(cliente)
