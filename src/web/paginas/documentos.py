"""
SaúdeJá — Política de privacidade (`/privacidade`) e Termos de uso (`/termos`).

Esqueleto da casca: o conteúdo vem na fase da tela do paciente.
"""
from nicegui import ui

import documentos_legais
from web import tema

ROTAS = {documentos_legais.POLITICA: "/privacidade", documentos_legais.TERMOS: "/termos"}


def registrar() -> None:
    for documento, rota in ROTAS.items():
        _registrar_documento(documento, rota)


def _registrar_documento(documento: str, rota: str) -> None:
    titulo = documentos_legais.TITULOS[documento]

    @ui.page(rota, title=f"{titulo} · SaúdeJá")
    async def pagina_documento() -> None:
        tema.aplicar_tema()
        await ui.context.client.connected()
        tema.cabecalho()
        with ui.column().classes("w-full max-w-3xl mx-auto px-4 py-6 gap-4"):
            ui.label(titulo).classes("text-2xl font-semibold")
