"""
SaúdeJá — interface em NiceGUI (ADR-013), no lugar do Streamlit.

Um app FastAPI só da interface, num processo próprio na porta 7860. A API de
predição continua no processo dela, na porta 8000, sem mudança de exposição: a
interface não é montada no app da API porque isso publicaria `/predict` e
`/docs`, sem autenticação, na URL do Space (ADR-013, "Por que processo
próprio").

Rodar localmente, da raiz do repositório:

    python -m uvicorn web.main:criar_app --factory --app-dir src --port 7860

Fábrica (`--factory`) e não um `app` criado no import: montar o NiceGUI mexe em
estado global dele (rotas, middlewares, configuração), e um `app` de módulo
seria montado em todo teste que importasse este arquivo.

Variáveis: APP_ENV=dev|prod (aba de dev; sem ela vale prod), PREDICT_BACKEND=
processo|api, API_BASE_URL (só no backend 'api').

Rotas: `/` paciente, `/privacidade` e `/termos`, `/equipe` (login e abas),
`/healthz` (saúde). Toda a regra de negócio continua em `src/ui/logic.py`; os
módulos de `src/web/` só apresentam.
"""
import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import PlainTextResponse  # noqa: E402
from nicegui import ui  # noqa: E402

import telemetria  # noqa: E402
from logging_config import configurar_logging  # noqa: E402
from web import dados, execucao, sessao, tema  # noqa: E402
from web.paginas import documentos, equipe, erro, paciente  # noqa: E402

ROTA_SAUDE = "/healthz"
# Alias temporário: a sonda externa e o rollback para o Streamlit batem em
# `/_stcore/health` (SLO.md §1.1, emenda de 2026-10-17 do ADR-006). Responde
# igual a `/healthz` até os checks do Grafana passarem para a rota nova e a
# janela de rollback fechar; aí sai, com o teste dele.
ROTA_SAUDE_LEGADA = "/_stcore/health"


def saude() -> PlainTextResponse:
    """200 com corpo `ok`, que é o que a sonda do Synthetic Monitoring exige
    (ADR-006). Não toca banco nem modelo: mede se a interface atende, e um
    Supabase fora do ar não pode derrubar o uptime da tela, que continua
    mostrando o erro amigável."""
    return PlainTextResponse("ok")


def configurar_paginas() -> None:
    """Registra as páginas e a política de memória por aba. Separada de
    `criar_app` para o simulador de testes (tests/web/) registrar as mesmas
    páginas sem montar o FastAPI."""
    sessao.configurar_storage()
    erro.registrar()
    paciente.registrar()
    documentos.registrar()
    equipe.registrar()


async def _podar_caches_periodicamente() -> None:
    while True:
        await asyncio.sleep(dados.TTL_FILA_DO_DIA_S)
        dados.podar_caches()


@contextlib.asynccontextmanager
async def _ciclo_de_vida(_app: FastAPI) -> AsyncIterator[None]:
    """Telemetria antes do logging, como no `lifespan` da API: o handler de
    log do OpenTelemetry precisa existir para receber o filtro de redação. O
    NiceGUI já instalou o dele quando este bloco roda (`run_with` envolve este
    `lifespan`), e `configurar_logging` blinda todo handler do processo,
    inclusive os do uvicorn, que logam cada websocket com o IP do cliente."""
    telemetria.configurar_telemetria(telemetria.SERVICO_UI)
    configurar_logging()
    telemetria.definir_sessao(execucao.sessao_da_telemetria)
    poda = asyncio.create_task(_podar_caches_periodicamente())
    try:
        yield
    finally:
        poda.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poda


def criar_app() -> FastAPI:
    # Sem /docs nem /openapi.json: este app não é API, e a página de
    # documentação publicaria a lista de rotas internas na URL do Space.
    app = FastAPI(lifespan=_ciclo_de_vida, docs_url=None, redoc_url=None, openapi_url=None)
    for rota in (ROTA_SAUDE, ROTA_SAUDE_LEGADA):
        app.add_api_route(rota, saude, methods=["GET", "HEAD"], include_in_schema=False)
    configurar_paginas()
    # Sem `storage_secret`: ele ligaria o cookie de sessão e o armazenamento
    # em disco (`app.storage.user`), proibidos aqui (web/sessao.py). Sem a
    # mensagem de boas-vindas, que imprime os IPs da máquina no log.
    ui.run_with(
        app,
        title="SaúdeJá",
        language="pt-BR",
        favicon=tema.CAMINHO_FAVICON,
        dark=None,
        show_welcome_message=False,
    )
    return app
