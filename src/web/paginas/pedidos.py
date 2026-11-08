"""
SaúdeJá — aba "Pedidos do titular" da área da equipe.

Esqueleto: o conteúdo vem na fase dos pedidos do titular.
"""
from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from web.paginas.equipe import AreaDaEquipe


class PedidosDoTitular:
    def __init__(self, area: "AreaDaEquipe") -> None:
        self.area = area

    async def montar(self) -> None:
        ui.label("Pedidos do titular").classes("text-2xl font-semibold tracking-tight")
