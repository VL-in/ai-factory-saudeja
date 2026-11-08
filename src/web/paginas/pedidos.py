"""
SaúdeJá — aba "Pedidos do titular" da área da equipe (art. 18 e art. 20 da LGPD).

Os dados da clínica são da clínica: ela registra, confirma ou recusa, e o
workflow de purga executa a eliminação confirmada. Os dados de aprimoramento
são da SaúdeJá (ADR-010): a clínica só encaminha o pedido, pelo CPF, e a tela
não diz se havia dado, porque diria a quem digitasse um CPF alheio se aquela
pessoa autorizou o treino.

O pedido é localizado pelo código do agendamento que a "Fila do dia" mostra: a
equipe nunca procura o paciente por CPF ou nome (LGPD.md §8.1). O CPF digitado
para o encaminhamento não é guardado, e o campo é limpo depois de cada envio,
com sucesso ou não, para não ficar na tela.
"""
from typing import TYPE_CHECKING

from nicegui import ui

from ui import logic
from web import tema
from web.execucao import executar_em_thread

if TYPE_CHECKING:
    from web.paginas.equipe import AreaDaEquipe

TEXTO_TAMBEM_TREINO = (
    "O paciente também pede a eliminação dos dados de aprimoramento do sistema"
)
ROTULO_CPF = "CPF do paciente (só para encaminhar ao SaúdeJá; não é guardado)"


def acoes_do_pedido(pedido: dict) -> list[tuple[str, str]]:
    """(decisão, rótulo) que a tela oferece para o pedido. Eliminação
    confirmada não tem ação: o workflow de purga a executa."""
    if pedido["situacao"] == "pendente":
        return [("confirmar", "Confirmar"), ("recusar", "Recusar")]
    if pedido["tipo"] == "revisao_decisao":
        return [("concluir", "Concluir revisão")]
    return []


class PedidosDoTitular:
    def __init__(self, area: "AreaDaEquipe") -> None:
        self.area = area

    async def montar(self) -> None:
        ui.label("Pedidos do titular").classes("text-2xl font-semibold tracking-tight")
        ui.label(
            "Confirme a identidade de quem pede (CPF e data de nascimento) antes de "
            "registrar. O prazo de resposta é de 15 dias."
        ).classes("text-sm sj-suave")

        with ui.row().classes("w-full gap-5 items-start no-wrap max-lg:flex-wrap"):
            with ui.card().classes("w-[420px] max-lg:w-full p-5 gap-3 rounded-xl"):
                self._formulario()
            with ui.card().classes("flex-1 min-w-0 p-5 gap-3 rounded-xl"):
                ui.label("Pedidos em aberto").classes("text-base font-semibold")
                self._avisos_da_lista = ui.column().classes("w-full gap-2")
                self._lista = ui.column().classes("w-full gap-2").mark("lista-pedidos")
        await self._carregar_lista()

    def _formulario(self) -> None:
        ui.label("Novo pedido sobre os dados da clínica").classes("text-base font-semibold")
        self._codigo = (
            ui.input("Código do agendamento")
            .props("outlined dense")
            .classes("w-full")
            .tooltip('Em "Detalhes técnicos" da linha, na Fila do dia.')
            .mark("pedido-codigo")
        )
        self._tipo = (
            ui.select(
                dict(logic.ROTULOS_TIPO_PEDIDO),
                value=next(iter(logic.ROTULOS_TIPO_PEDIDO)),
                label="Tipo",
            )
            .props("outlined dense")
            .classes("w-full")
            .mark("pedido-tipo")
        )
        self._tambem_treino = ui.checkbox(TEXTO_TAMBEM_TREINO).mark("pedido-treino")
        self._cpf = (
            ui.input(ROTULO_CPF, placeholder="000.000.000-00")
            .props("outlined dense")
            .classes("w-full")
            .mark("pedido-cpf")
        )
        self._avisos_do_formulario = ui.column().classes("w-full gap-2")
        ui.button("Registrar pedido", on_click=self._registrar).props("unelevated no-caps").mark(
            "pedido-registrar"
        )

    async def _registrar(self) -> None:
        if await self.area.exigir_sessao() is None:
            return
        tambem_treino = bool(self._tambem_treino.value)
        cpf = self._cpf.value or ""
        self._cpf.value = ""
        self._avisos_do_formulario.clear()
        try:
            await executar_em_thread(
                logic.registrar_pedido_da_clinica, self._codigo.value or "", self._tipo.value
            )
            if tambem_treino:
                await executar_em_thread(logic.encaminhar_pedido_ao_treino, cpf)
        except (logic.ErroPedido, logic.ErroPersistencia) as exc:
            with self._avisos_do_formulario:
                tema.aviso(str(exc), "aviso")
        else:
            mensagem = "Pedido registrado."
            if tambem_treino:
                mensagem += " O pedido de aprimoramento foi encaminhado ao SaúdeJá."
            with self._avisos_do_formulario:
                tema.aviso(mensagem, "ok")
            self._codigo.value = ""
            self._tambem_treino.value = False
        await self._carregar_lista()

    async def _carregar_lista(self) -> None:
        self._lista.clear()
        try:
            pedidos = await executar_em_thread(logic.listar_pedidos_da_clinica)
        except logic.ErroPersistencia as exc:
            with self._lista:
                tema.aviso(str(exc), "erro")
            return
        with self._lista:
            if not pedidos:
                ui.label("Nenhum pedido em aberto.").classes("text-sm sj-suave")
                return
            for pedido in pedidos:
                self._linha_do_pedido(pedido)

    def _linha_do_pedido(self, pedido: dict) -> None:
        tipo = logic.ROTULOS_TIPO_PEDIDO.get(pedido["tipo"], pedido["tipo"])
        situacao = logic.ROTULOS_SITUACAO_PEDIDO.get(pedido["situacao"], pedido["situacao"])
        desde = logic.data_na_clinica(pedido["criado_em"]).strftime("%d/%m/%Y")
        with ui.row().classes("w-full items-center gap-3 py-1 border-b border-gray-200/40"):
            with ui.column().classes("gap-0 grow"):
                ui.label(tipo).classes("text-sm font-medium")
                ui.label(f"{situacao} · desde {desde}").classes("text-xs sj-suave")
            for decisao, rotulo in acoes_do_pedido(pedido):
                ui.button(
                    rotulo,
                    on_click=lambda d=decisao, i=pedido["id"]: self._decidir(i, d),
                ).props("outline no-caps dense").mark(f"pedido-{decisao}-{pedido['id']}")

    async def _decidir(self, id_pedido: str, decisao: str) -> None:
        if await self.area.exigir_sessao() is None:
            return
        self._avisos_da_lista.clear()
        try:
            await executar_em_thread(logic.decidir_pedido_da_clinica, id_pedido, decisao)
        except logic.ErroPersistencia as exc:
            with self._avisos_da_lista:
                tema.aviso(str(exc), "erro")
        await self._carregar_lista()
