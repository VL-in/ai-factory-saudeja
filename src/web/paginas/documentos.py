"""
SaúdeJá — Política de privacidade (`/privacidade`) e Termos de uso (`/termos`).

O texto longo fica fora da tela de agendamento, que mostra só os links (ADR-010,
decisão D2). Sai dos arquivos versionados em `docs/LGPD/`, por
`documentos_legais.texto_para_tela`, a mesma fonte da versão gravada na ciência:
é a única exceção à regra de não renderizar markdown na interface (D7 da
migração), porque o conteúdo é do repositório, não do banco nem do usuário.

A volta ao agendamento é um link na mesma aba: a ciência marcada vive em
`app.storage.tab`, que acompanha a navegação, então a caixa continua marcada.
"""
from nicegui import ui

import documentos_legais
from web import rotas, tema

ROTAS = {documentos_legais.POLITICA: "/privacidade", documentos_legais.TERMOS: "/termos"}


def registrar() -> None:
    for documento, rota in ROTAS.items():
        _registrar_documento(documento, rota)


def _voltar() -> None:
    link = ui.link(target=rotas.PACIENTE).classes("flex items-center gap-1 text-primary")
    with link.mark("voltar"):
        ui.icon("arrow_back")
        ui.label("Voltar ao agendamento")


def _registrar_documento(documento: str, rota: str) -> None:
    titulo = documentos_legais.TITULOS[documento]

    @ui.page(rota, title=f"{titulo} · SaúdeJá")
    async def pagina_documento() -> None:
        tema.aplicar_tema()
        await ui.context.client.connected()
        tema.cabecalho()
        with ui.column().classes("w-full max-w-3xl mx-auto px-4 py-6 gap-4"):
            _voltar()
            ui.markdown(documentos_legais.texto_para_tela(documento)).classes(
                "sj-documento w-full"
            ).mark("documento")
            _voltar()
