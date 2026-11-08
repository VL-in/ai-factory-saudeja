"""
SaúdeJá — aba "Time técnico" da área da equipe.

Esqueleto: o conteúdo vem na fase do time técnico.
"""
from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from web.paginas.equipe import AreaDaEquipe


class TimeTecnico:
    def __init__(self, area: "AreaDaEquipe", com_aba_de_dev: bool) -> None:
        self.area = area
        self.com_aba_de_dev = com_aba_de_dev

    async def montar(self) -> None:
        ui.label("Área do time técnico").classes("text-sm sj-suave")
