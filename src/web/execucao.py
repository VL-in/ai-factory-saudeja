"""
SaúdeJá — chamadas bloqueantes fora do event loop da interface (ADR-013, regra 3).

Supabase, modelo e SHAP são síncronos. No NiceGUI, um handler roda no event
loop que atende todas as abas: uma consulta de 2 s feita ali congelaria a
tela de todo mundo e também o `/healthz`, que a sonda externa usa para medir
o uptime. Toda chamada a `ui.logic` feita por um handler passa por
`executar_em_thread`.

Por que `asyncio.to_thread`, e não o `run.io_bound` do NiceGUI: o
`run.io_bound` usa `loop.run_in_executor`, que **não copia** os
`contextvars` para a thread. O OpenTelemetry guarda o span corrente num
`contextvar`, então o span de `_acao_ui` criado na thread perderia o pai e o
`session.id`. O `to_thread` roda a função dentro de uma cópia do contexto.

O `session.id` dos spans (`telemetria.definir_sessao`) vem de um `ContextVar`
preenchido aqui, antes de despachar, com o id aleatório da aba
(`sessao.id_de_telemetria`). Na interface antiga ele era lido do
`st.session_state`; aqui, a thread não enxerga `app.storage.tab`, que depende
do contexto de página do NiceGUI.
"""
import asyncio
from collections.abc import Callable
from contextvars import ContextVar
from typing import ParamSpec, TypeVar

from web import sessao

P = ParamSpec("P")
R = TypeVar("R")

SESSAO_TELEMETRIA: ContextVar[str | None] = ContextVar("saudeja_sessao_telemetria", default=None)


def sessao_da_telemetria() -> str | None:
    """Provedor de `telemetria.definir_sessao`: lido a cada span, inclusive
    dentro da thread de `executar_em_thread`."""
    return SESSAO_TELEMETRIA.get()


def _id_da_aba() -> str | None:
    try:
        return sessao.id_de_telemetria()
    except RuntimeError:
        # Fora de uma aba conectada (um teste, uma tarefa de fundo): vale o
        # que já estiver no contexto.
        return SESSAO_TELEMETRIA.get()


async def executar_em_thread(funcao: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """`funcao(*args, **kwargs)` numa thread, com o contexto (span corrente e
    sessão da aba) levado junto. A exceção da função sobe para o handler."""
    marca = SESSAO_TELEMETRIA.set(_id_da_aba())
    try:
        return await asyncio.to_thread(funcao, *args, **kwargs)
    finally:
        SESSAO_TELEMETRIA.reset(marca)
