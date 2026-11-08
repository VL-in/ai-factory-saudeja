"""
SaúdeJá — aba "Fila do dia" da área da equipe.

Agendamentos da data, do maior para o menor risco de falta, com o detalhe do
paciente selecionado: registro do desfecho, os três fatores que mais pesaram e
os identificadores técnicos. A fila vem de `dados.fila_do_dia` (cache de 60 s
compartilhado entre abas, ver web/dados.py) e toda ida ao banco passa por
`executar_em_thread`.

O que vai ao navegador é só o que a tela mostra: as linhas da tabela levam
nome, especialidade, horário, probabilidade, status e as duas marcas (alto
risco, previsão incerta). A explicação em log-odds não vai: o detalhe mostra os
fatores em português, como na interface antiga.
"""
from datetime import date
from typing import TYPE_CHECKING

from nicegui import ui

from ui import logic
from web import dados, tema
from web.execucao import executar_em_thread

if TYPE_CHECKING:
    from web.paginas.equipe import AreaDaEquipe

ACOES_DESFECHO = (
    ("Consulta realizada", logic.STATUS_CONCLUIDO),
    ("Paciente faltou", logic.STATUS_NO_SHOW),
    ("Cancelada", logic.STATUS_CANCELADO),
)

COLUNAS_FILA: list[dict] = [
    {"name": "paciente", "label": "Paciente", "field": "paciente", "align": "left"},
    {"name": "especialidade", "label": "Especialidade", "field": "especialidade", "align": "left"},
    {"name": "horario", "label": "Horário", "field": "horario", "align": "left", "sortable": True},
    {
        "name": "risco",
        "label": "Risco de falta",
        "field": "probabilidade",
        "align": "left",
        "sortable": True,
    },
    {"name": "status", "label": "Status", "field": "status", "align": "left"},
]

# Célula de risco: barra + percentual, selo de alto risco e alerta de previsão
# incerta ("fora do domínio" do contrato de features: dado que o modelo não viu
# no treino, probabilidade extrapolada). Template Vue/Quasar fixo; os valores
# entram por `{{ }}`, que escapa o texto.
SLOT_RISCO = r"""
<q-td :props="props">
  <div v-if="props.row.probabilidade !== null" class="row items-center no-wrap q-gutter-x-sm">
    <q-linear-progress rounded size="8px" style="width: 96px"
      :value="props.row.probabilidade"
      :color="props.row.alto_risco ? 'negative' : 'primary'" track-color="grey-4" />
    <span class="text-weight-medium" style="min-width: 3ch">
      {{ Math.round(props.row.probabilidade * 100) }}%</span>
    <q-badge v-if="props.row.alto_risco" color="negative" outline label="alto risco" />
    <q-icon v-if="props.row.incerto" name="help_outline" color="warning" size="18px">
      <q-tooltip>Previsão incerta: o agendamento tem dados diferentes dos que o sistema
        costuma ver (ex.: distância acima de 50 km, antecedência acima de 90 dias). Use a
        probabilidade com cautela.</q-tooltip>
    </q-icon>
  </div>
  <span v-else class="sj-suave">ainda não avaliado</span>
</q-td>
"""

SLOT_STATUS = r"""
<q-td :props="props">
  <q-chip dense square outline :label="props.value"
    :color="{Agendado:'grey-7', Realizada:'positive', Faltou:'negative',
      Cancelada:'orange-8'}[props.value]" />
</q-td>
"""

CANAL_REVISAO_SAUDEJA = "v.lin@gmail.com"

# Canal do art. 20 da LGPD para quem atende. A clínica é a controladora da
# decisão de lembrar (docs/LGPD/LGPD.md §1) e responde ao paciente; a SaúdeJá dá
# o apoio técnico. Procedimento completo em docs/LGPD/PROCEDIMENTO-REVISAO.md.
PASSOS_DA_REVISAO = (
    "Confirme a identidade de quem pede (CPF e data de nascimento).",
    "Escolha a data da consulta acima e abra a linha do paciente: a explicação mostra os "
    "fatores que aumentaram ou reduziram o risco.",
    "Se algum dado estiver errado (distância, histórico, horário), corrija o cadastro.",
    "Se o lembrete deveria ter sido enviado, avise o paciente por telefone. A decisão nunca "
    "afeta o atendimento.",
    "Explique ao paciente o resultado. Se a predição não aparecer (mais de 90 dias) ou se o "
    f"paciente contestar o critério, escreva para {CANAL_REVISAO_SAUDEJA} com o assunto "
    '"Revisão art. 20" e o código do agendamento (em "Detalhes técnicos" da linha). Não '
    "envie nome, CPF, telefone nem o identificador interno do paciente.",
)


def linhas_da_tabela(fila: list[logic.ItemFila]) -> list[dict]:
    return [
        {
            "id": item.id_agendamento,
            "paciente": item.rotulo_paciente,
            "especialidade": item.especialidade,
            "horario": item.data_hora_agendada.strftime("%H:%M"),
            "probabilidade": item.probabilidade,
            "alto_risco": bool(item.classe_prevista) if item.tem_predicao else False,
            "incerto": bool(item.fora_do_dominio),
            "status": logic.rotulo_status(item.status),
        }
        for item in fila
    ]


class FilaDoDia:
    def __init__(self, area: "AreaDaEquipe") -> None:
        self.area = area
        self.dia: date = logic.hoje_na_clinica()
        self.fila: list[logic.ItemFila] = []
        self.erro: str | None = None
        self.id_selecionado: str | None = None

    async def montar(self) -> None:
        with ui.row().classes("w-full items-end gap-3"):
            with ui.column().classes("gap-1 grow"):
                ui.label("Fila do dia").classes("text-2xl font-semibold tracking-tight")
                ui.label(
                    "Agendamentos do dia, do maior para o menor risco de falta. Quem ainda "
                    "não foi avaliado aparece no fim da lista."
                ).classes("text-sm sj-suave")
            self._botao_data = ui.button(self.dia.strftime("%d/%m/%Y"), icon="event").props(
                'outline no-caps aria-label="Data da fila"'
            )
            with self._botao_data, ui.menu() as self._menu_data:
                ui.date(value=self.dia.isoformat(), on_change=self._mudar_dia).props(
                    'mask="YYYY-MM-DD" today-btn'
                ).mark("data-da-fila")
            ui.button(icon="refresh", on_click=self._atualizar).props(
                'flat round aria-label="Atualizar"'
            ).tooltip("Atualizar").mark("atualizar-fila")

        # Aviso na tela, não só no ADR: quem opera precisa saber que a tela
        # carrega dado pessoal, porque a decisão de onde posicionar o monitor
        # da recepção é dela, não do código (ADR-007, risco aceito).
        with ui.row().classes("w-full items-center gap-2 text-xs sj-aviso rounded-lg px-3 py-2"):
            ui.icon("lock").classes("text-base")
            ui.label(
                "Esta tela mostra nome de paciente e é de uso restrito da equipe. Evite "
                "deixá-la visível para a sala de espera."
            )
        with ui.expansion("Paciente pediu revisão da decisão de lembrete?", icon="gavel").classes(
            "w-full text-sm"
        ):
            ui.label(
                "O paciente pode pedir explicação e revisão da decisão automática de enviar "
                "ou não o lembrete (LGPD, art. 20). Responda em até 15 dias."
            )
            for numero, passo in enumerate(PASSOS_DA_REVISAO, start=1):
                ui.label(f"{numero}. {passo}")

        self._conteudo = ui.column().classes("w-full gap-5")
        await self.recarregar()

    async def recarregar(self) -> None:
        """Lê a fila (do cache, se ainda válido) e redesenha. Não renova a
        sessão: o timer da tela também passa por aqui."""
        try:
            self.fila = await executar_em_thread(dados.fila_do_dia, self.dia)
            self.erro = None
        except logic.ErroPersistencia as exc:
            self.fila, self.erro = [], f"Não foi possível consultar a fila: {exc}"
        if self.id_selecionado not in {item.id_agendamento for item in self.fila}:
            self.id_selecionado = None
        self._desenhar()

    async def _atualizar(self) -> None:
        if await self.area.exigir_sessao() is None:
            return
        await self.recarregar()

    async def _mudar_dia(self, evento) -> None:
        if await self.area.exigir_sessao() is None:
            return
        self.dia = date.fromisoformat(evento.value)
        self.id_selecionado = None
        self._menu_data.close()
        self._botao_data.text = self.dia.strftime("%d/%m/%Y")
        await self.recarregar()

    async def _selecionar(self, evento) -> None:
        if await self.area.exigir_sessao() is None:
            return
        self.id_selecionado = evento.args[1]["id"]
        self._desenhar_detalhe()

    async def _registrar_desfecho(self, item: logic.ItemFila, status: str, rotulo: str) -> None:
        """Ação da clínica registrar o que de fato aconteceu com o agendamento:
        sem isto, `agendamentos.status` nunca sai de 'agendado' e o re-treino
        mensal nunca tem dado real para aprender."""
        if await self.area.exigir_sessao() is None:
            return
        try:
            await executar_em_thread(
                logic.atualizar_status_agendamento,
                item.id_agendamento,
                status,
                data_hora_agendada=item.data_hora_agendada,
            )
        except (logic.ErroPersistencia, logic.ErroDesfechoForaDePrazo) as exc:
            ui.notify(f"Não foi possível registrar o desfecho: {exc}", type="negative")
        else:
            ui.notify(
                f"Desfecho registrado: {rotulo.lower()}.", type="positive", icon="check_circle"
            )
        finally:
            # Também na falha: um erro depois do commit (timeout na resposta)
            # deixaria a fila mostrando o estado anterior até o prazo do cache.
            dados.invalidar_fila()
        await self.recarregar()

    # --- desenho --------------------------------------------------------------

    def _desenhar(self) -> None:
        self._conteudo.clear()
        with self._conteudo:
            if self.erro:
                tema.aviso(self.erro, "erro")
                return
            if not self.fila:
                with ui.card().classes("w-full items-center p-10 rounded-xl"):
                    ui.icon("event_available").classes("text-4xl sj-suave")
                    ui.label("Nenhum agendamento para esta data.").classes("sj-suave")
                return
            resumo = logic.resumo_da_fila(self.fila)
            with ui.row().classes("w-full gap-4"):
                tema.cartao_metrica(
                    "Agendamentos", resumo["total"], "groups", "teal",
                    "Total de consultas na data escolhida.",
                )
                tema.cartao_metrica(
                    "Alto risco", resumo["alto_risco"], "warning", "red",
                    "Pacientes com maior chance de faltar: são os priorizados para o lembrete.",
                )
                tema.cartao_metrica(
                    "Sem predição", resumo["sem_predicao"], "hourglass_empty", "slate",
                    "Ainda não avaliados: a avaliação é feita dois dias antes da consulta. "
                    "Não significa risco zero.",
                )
            with ui.row().classes("w-full gap-5 items-start no-wrap max-lg:flex-wrap"):
                with ui.card().classes("flex-1 min-w-0 p-0 rounded-xl overflow-hidden"):
                    tabela = (
                        ui.table(
                            columns=COLUNAS_FILA,
                            rows=linhas_da_tabela(self.fila),
                            row_key="id",
                            pagination=0,
                        )
                        .props("flat hide-bottom")
                        .classes("w-full cursor-pointer")
                        .mark("tabela-fila")
                    )
                    tabela.add_slot("body-cell-risco", SLOT_RISCO)
                    tabela.add_slot("body-cell-status", SLOT_STATUS)
                    tabela.on("rowClick", self._selecionar)
                self._coluna_detalhe = ui.column().classes("w-[380px] max-lg:w-full gap-0")
            self._desenhar_detalhe()

    def _desenhar_detalhe(self) -> None:
        item = next((i for i in self.fila if i.id_agendamento == self.id_selecionado), None)
        self._coluna_detalhe.clear()
        with self._coluna_detalhe, ui.card().classes("w-full p-5 gap-3 rounded-xl"):
            if item is None:
                ui.icon("touch_app").classes("text-3xl sj-suave")
                ui.label(
                    "Selecione um paciente para registrar o desfecho e ver por que ele tem "
                    "esse risco."
                ).classes("text-sm sj-suave")
                return
            self._detalhe(item)

    def _detalhe(self, item: logic.ItemFila) -> None:
        ui.label(item.rotulo_paciente).classes("text-lg font-semibold").mark("detalhe-paciente")
        ui.label(
            f"{item.especialidade} · {item.data_hora_agendada:%H:%M} · "
            f"{logic.rotulo_status(item.status)}"
        ).classes("text-sm sj-suave")

        ui.separator()
        ui.label("Registrar desfecho").classes("text-sm font-semibold")
        if not logic.pode_registrar_desfecho(item.data_hora_agendada):
            ui.label("O desfecho só pode ser registrado no dia da consulta ou depois.").classes(
                "text-sm sj-suave"
            )
        else:
            with ui.row().classes("gap-2"):
                for rotulo, status in ACOES_DESFECHO:
                    # Desabilitado em vez de escondido: mostra que a ação já
                    # foi tomada, em vez de o botão sumir da tela.
                    botao = ui.button(
                        rotulo,
                        on_click=lambda s=status, r=rotulo: self._registrar_desfecho(item, s, r),
                    ).props("unelevated no-caps dense").classes("px-3")
                    botao.set_enabled(item.status != status)

        ui.separator()
        ui.label("Por que esse risco").classes("text-sm font-semibold")
        if not item.tem_predicao:
            ui.label(
                "Este agendamento ainda não foi avaliado: a explicação aparece depois da "
                "avaliação, feita dois dias antes da consulta."
            ).classes("text-sm sj-suave")
        else:
            ui.label(f"Probabilidade de falta: {item.probabilidade:.0%}").classes("text-sm")
            if item.fora_do_dominio:
                tema.aviso(
                    "Previsão incerta: este agendamento tem dados diferentes dos que o sistema "
                    "costuma ver. Use a probabilidade com cautela.",
                    "aviso",
                )
            mostrar_motivos(item.explicacao, item.explicacao_texto)

        # O identificador continua acessível: é por ele que se rastreia o
        # paciente no banco e nos logs (onde o nome nunca aparece). O código do
        # agendamento é o que vai num pedido de revisão à SaúdeJá.
        with ui.expansion("Detalhes técnicos").classes("w-full text-sm").props("dense"):
            ui.label(f"código do agendamento: {item.id_agendamento}").classes(
                "font-mono text-xs break-all"
            )
            ui.label(f"identificador interno: {item.id_paciente_externo}").classes(
                "font-mono text-xs break-all"
            )
            if item.tem_predicao:
                ui.label(
                    f"probabilidade {item.probabilidade:.1%} · modelo {item.model_version} · "
                    "explicação lida de predicoes.explicacao_shap"
                ).classes("font-mono text-xs break-all")


def mostrar_motivos(explicacao: list, explicacao_texto: str | None = None) -> None:
    """Mesma renderização para a predição da fila (gravada pelo job D-2) e para
    a do teste manual: os principais fatores, em português e sem número."""
    if not explicacao:
        # SLO §4 exige explicação em 100% das predições: a falta dela é
        # problema a investigar, não um gráfico em branco.
        tema.aviso("Esta avaliação não tem explicação registrada. Avise o time técnico.", "aviso")
        return
    ui.label("Principais fatores desta avaliação:").classes("text-sm").mark("principais-fatores")
    for motivo in logic.principais_motivos(explicacao):
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon(
                "arrow_upward" if motivo["aumenta"] else "arrow_downward",
                color="negative" if motivo["aumenta"] else "positive",
            )
            ui.label(
                f"{motivo['rotulo']}: "
                + ("aumenta o risco de falta" if motivo["aumenta"] else "reduz o risco de falta")
            ).classes("text-sm").mark("motivo")
    if explicacao_texto:
        tema.aviso(explicacao_texto, "info")
