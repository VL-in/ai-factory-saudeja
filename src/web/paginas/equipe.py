"""
SaúdeJá — área da equipe (`/equipe`): login e abas.

Esqueleto da casca: o login e as abas vêm nas fases seguintes.
"""
from nicegui import ui

from web import tema

ROTA = "/equipe"


def registrar() -> None:
    @ui.page(ROTA, title="Área da equipe · SaúdeJá")
    async def pagina_equipe() -> None:
        tema.aplicar_tema()
        await ui.context.client.connected()
        tema.cabecalho("Área da equipe")
        with ui.column().classes("w-full max-w-sm mx-auto px-4 py-6 gap-4"):
            ui.label("Acesso da equipe").classes("text-xl font-semibold")
