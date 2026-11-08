"""
SaúdeJá — tela do paciente (`/`): ciência, cadastro e agendamento.

Esqueleto da casca: o conteúdo vem na fase da tela do paciente.
"""
from nicegui import ui

from web import tema

ROTA = "/"


def registrar() -> None:
    @ui.page(ROTA, title="SaúdeJá")
    async def pagina_paciente() -> None:
        tema.aplicar_tema()
        await ui.context.client.connected()
        tema.cabecalho()
        with ui.column().classes("w-full max-w-3xl mx-auto px-4 py-6 gap-4"):
            ui.label("Cadastro e agendamento").classes("text-2xl font-semibold")
            link_da_equipe()


def link_da_equipe() -> None:
    """Discreto: a equipe sabe onde fica, e o paciente não precisa dele."""
    ui.link("Área da equipe", "/equipe").classes("text-xs sj-suave self-end")
