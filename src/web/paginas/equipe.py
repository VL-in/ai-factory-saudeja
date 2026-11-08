"""
SaúdeJá — área da equipe (`/equipe`): login e abas.

A visão da equipe exige login (e-mail e senha no Supabase Auth,
docs/architecture.md §7); a do paciente continua aberta, porque é o
autoagendamento. Conta de funcionário se cria com
`python scripts/criar_funcionario.py`.

Sem sessão, a página desenha só o formulário de login: nenhuma aba é montada,
nem escondida. É a mesma regra da interface antiga (a "Fila do dia" carrega
nome de paciente, ADR-007), e aqui ela é mais forte, porque o que não é montado
no servidor também não vai ao navegador.

A sessão é conferida a cada ação (`exigir_sessao`), que renova o relógio de
inatividade, e por um timer de 60 s, que não renova: devolve ao login a tela
parada há mais de 30 min, mesmo sem clique (o Streamlit só percebia no clique
seguinte), e recarrega a fila do cache, para a tela aberta não segurar o nome
de um paciente eliminado por mais de 60 s.

A rotina da recepção vem primeiro; o que é do time técnico (painel do SLO,
teste manual do modelo e a explicação dele, disparo manual do job) fica numa
aba própria, presente também em produção, porque é lá que o painel do SLO de
produção é lido (ADR-006).

Visual na mesma linha do início (`inicio.py`) e do agendamento (`paciente.py`):
sem sessão, o login fica sobre uma foto da recepção, servida pela aplicação
(`estatico/equipe-fundo.webp`), sob o mesmo véu na cor do tema; com sessão, uma
faixa de topo mais baixa com a mesma foto, a data da clínica e as abas num
trilho de pílulas. O conteúdo de cada aba não muda. CSS constante
(regra D7): nenhum dado entra nele.
"""
import mimetypes
import os
from datetime import date

from nicegui import app, ui

from ui import logic
from web import rotas, sessao, tema
from web.execucao import executar_em_thread
from web.paginas.fila import FilaDoDia
from web.paginas.pedidos import PedidosDoTitular
from web.paginas.time_tecnico import TimeTecnico

ROTA = rotas.EQUIPE
URL_IMAGEM_DE_FUNDO = "/estatico/equipe-fundo.webp"
CAMINHO_IMAGEM_DE_FUNDO = tema.CAMINHO_FAVICON.parent / "equipe-fundo.webp"
ABA_FILA = "Fila do dia"
ABA_PEDIDOS = "Pedidos do titular"
ABA_TIME_TECNICO = "Time técnico"
ABAS_FUNCIONARIO = (ABA_FILA, ABA_PEDIDOS, ABA_TIME_TECNICO)

_DIAS_DA_SEMANA = (
    "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira",
    "sábado", "domingo",
)
_MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
    "setembro", "outubro", "novembro", "dezembro",
)

CSS_EQUIPE = (
    "<style>"
    # Foto com véu: a tela de login inteira e, com sessão, a faixa do topo.
    ".sj-eq-foto{position:relative;isolation:isolate;overflow:hidden}"
    # URL literal, sem f-string: a guarda de HTML cru só aceita literal;
    # test_equipe confere que é URL_IMAGEM_DE_FUNDO. A recepção da foto fica
    # à direita, longe do texto.
    '.sj-eq-foto::before{content:"";position:absolute;inset:0;z-index:-2;'
    'background:url("/estatico/equipe-fundo.webp") 75% center/cover no-repeat}'
    '.sj-eq-foto::after{content:"";position:absolute;inset:0;z-index:-1;'
    "background:linear-gradient(90deg,var(--sj-fundo) 0%,"
    "color-mix(in srgb,var(--sj-fundo) 92%,transparent) 40%,"
    "color-mix(in srgb,var(--sj-fundo) 60%,transparent) 72%,"
    "color-mix(in srgb,var(--sj-fundo) 40%,transparent) 100%)}"
    "@media (max-width:767px){.sj-eq-foto::after{"
    "background:color-mix(in srgb,var(--sj-fundo) 88%,transparent)}}"
    ".sj-eq-login{display:flex;align-items:center;min-height:calc(100svh - 64px)}"
    ".sj-eq-login-grade{display:grid;gap:2.5rem;align-items:center;width:100%}"
    "@media (min-width:900px){.sj-eq-login-grade{grid-template-columns:minmax(0,1fr) 400px}}"
    ".sj-eq-topo{border-bottom:1px solid var(--sj-borda)}"
    ".sj-eq-selo{display:inline-flex;align-items:center;gap:.4rem;padding:.3rem .75rem;"
    "border-radius:999px;font-size:.8rem;font-weight:600;color:var(--q-primary);"
    "background:color-mix(in srgb,var(--q-primary) 12%,var(--sj-superficie))}"
    ".sj-eq-titulo{font-size:clamp(2rem,5vw,3.2rem);font-weight:700;line-height:1.08;"
    "letter-spacing:-.025em;margin:0;max-width:14ch}"
    ".sj-eq-titulo--topo{font-size:clamp(1.6rem,4vw,2.2rem);max-width:none}"
    ".sj-eq-item-icone{display:grid;place-items:center;width:2.25rem;height:2.25rem;"
    "flex-shrink:0;border-radius:.65rem;color:var(--q-primary);"
    "background:color-mix(in srgb,var(--q-primary) 12%,var(--sj-superficie))}"
    ".sj-eq-cartao{box-shadow:0 18px 48px rgba(15,23,42,.14)!important}"
    # Abas principais num trilho de pílulas (as subabas do Time técnico não
    # levam a classe e continuam como eram).
    ".sj-eq-abas{background:var(--sj-superficie);border:1px solid var(--sj-borda);"
    "border-radius:.9rem;padding:.3rem}"
    ".sj-eq-abas .q-tab{border-radius:.65rem;min-height:42px;padding:0 1rem;"
    "color:var(--sj-suave)}"
    ".sj-eq-abas .q-tab--active{color:var(--q-primary);"
    "background:color-mix(in srgb,var(--q-primary) 12%,transparent)}"
    ".sj-eq-abas .q-tab__indicator{display:none}"
    ".sj-eq-abas .q-tab__label{font-weight:600}"
    "</style>"
)

# Timer da tela: confere a inatividade sem renovar e recarrega a fila do cache.
# Variável de módulo para os testes encurtarem o intervalo.
INTERVALO_DA_TELA_S = 60.0


def app_env() -> str:
    """'prod' como padrão: a aba de dev dispara o job D-2 sem a pré-checagem
    do campeão (e com SMS de verdade, se MESSAGING_PROVIDER=infobip), então um
    ambiente que esqueceu de declarar APP_ENV não pode ganhá-la. Lido a cada
    página, não no import."""
    return os.environ.get("APP_ENV") or "prod"


def registrar() -> None:
    # Como no início: `.webp` no `mimetypes` (a imagem `python:3.10-slim` não
    # o conhece) e arquivo único, não a pasta `estatico/`.
    mimetypes.add_type("image/webp", ".webp")
    app.add_static_file(local_file=CAMINHO_IMAGEM_DE_FUNDO, url_path=URL_IMAGEM_DE_FUNDO)

    @ui.page(ROTA, title="Área da equipe · SaúdeJá")
    async def pagina_equipe() -> None:
        tema.aplicar_tema()
        ui.add_head_html(CSS_EQUIPE)
        # Sem o respiro padrão do NiceGUI: a foto vai de borda a borda.
        ui.query(".nicegui-content").classes("p-0")
        await ui.context.client.connected()
        await AreaDaEquipe().montar()


def data_por_extenso(dia: date) -> str:
    """"domingo, 8 de novembro de 2026", sem depender do locale da máquina
    (a imagem `python:3.10-slim` não tem pt_BR)."""
    return f"{_DIAS_DA_SEMANA[dia.weekday()]}, {dia.day} de {_MESES[dia.month - 1]} de {dia.year}"


class AreaDaEquipe:
    def __init__(self) -> None:
        self.fila: FilaDoDia | None = None

    async def montar(self) -> None:
        self._acoes = tema.cabecalho("Área da equipe")
        self._conteudo = ui.column().classes("w-full gap-0")
        # O timer do NiceGUI dispara já na criação (`immediate=True`), e a
        # página leria a fila duas vezes ao montar: o primeiro disparo é
        # ignorado em `_tique`. Não `immediate=False`: com ele, a página fechada
        # antes do primeiro intervalo faz o NiceGUI logar ERROR ("client
        # deleted") ao acordar o timer.
        self._primeiro_tique = True
        ui.timer(INTERVALO_DA_TELA_S, self._tique)
        await self.mostrar()

    async def mostrar(self) -> None:
        """Login ou abas, conforme a sessão da aba. Chamada na abertura, depois
        do login, na saída e quando uma ação encontra a sessão vencida.

        A sessão é lida antes do `clear`: quem chama costuma ser um botão de
        dentro do conteúdo, e depois de apagado o NiceGUI não resolve mais o
        `context.client` pela pilha de slots do handler."""
        funcionario = sessao.funcionario_logado()
        self.fila = None
        self._conteudo.clear()
        self._acoes.clear()
        if funcionario is None:
            with self._conteudo:
                self._tela_login()
            return
        with self._acoes:
            ui.label(funcionario.email).classes("text-sm sj-suave max-sm:hidden")
            ui.button("Sair", icon="logout", on_click=self._sair).props("flat dense no-caps")
        with self._conteudo:
            self._faixa_do_topo()
            with ui.column().classes("w-full max-w-7xl mx-auto px-4 sm:px-6 py-6 gap-5"):
                await self._abas()

    async def exigir_sessao(self) -> logic.SessaoFuncionario | None:
        """Guarda das ações: renova a sessão ou, vencida, volta ao login com o
        aviso. Quem chama interrompe a ação quando recebe `None`."""
        funcionario = sessao.funcionario_logado()
        if funcionario is None:
            await self.mostrar()
        return funcionario

    async def _sair(self) -> None:
        sessao.encerrar()
        await self.mostrar()

    async def _tique(self) -> None:
        if self._primeiro_tique:
            self._primeiro_tique = False
            return
        if sessao.sessao_expirou():
            sessao.encerrar(sessao.mensagem_de_inatividade())
            await self.mostrar()
        elif self.fila is not None:
            await self.fila.recarregar()

    # --- login ----------------------------------------------------------------

    def _tela_login(self) -> None:
        with (
            ui.element("section").classes("sj-eq-foto sj-eq-login w-full").mark("login"),
            ui.element("div").classes(
                "sj-eq-login-grade max-w-6xl mx-auto px-4 sm:px-8 py-10"
            ),
        ):
            with ui.column().classes("gap-5"):
                with ui.link(target=rotas.INICIO).classes(
                    "flex items-center gap-1 text-sm sj-suave no-underline"
                ).mark("voltar-inicio"):
                    ui.icon("arrow_back").classes("text-base")
                    ui.label("Início")
                with ui.element("div").classes("sj-eq-selo"):
                    ui.icon("badge").classes("text-base")
                    ui.label("Área da equipe")
                ui.label("O dia da clínica num só lugar").classes("sj-eq-titulo")
                ui.label(
                    "Agenda do dia com a prioridade de lembrete, registro de presença e "
                    "pedidos dos titulares, para a recepção e o time técnico."
                ).classes("text-lg sj-suave max-w-md max-sm:hidden")
                with ui.column().classes("gap-3 mt-1 max-sm:hidden"):
                    for icone, texto in (
                        ("today", "Agendamentos do dia, com quem precisa de lembrete"),
                        ("how_to_reg", "Registro de compareceu, faltou ou cancelou"),
                        ("assignment_ind", "Pedidos dos titulares sobre os próprios dados"),
                    ):
                        with ui.row().classes("items-center gap-3 no-wrap"):
                            with ui.element("span").classes("sj-eq-item-icone"):
                                ui.icon(icone).classes("text-xl")
                            ui.label(texto).classes("text-sm")
            self._cartao_de_login()

    def _cartao_de_login(self) -> None:
        with ui.card().classes("sj-eq-cartao w-full p-6 sm:p-8 gap-4 rounded-2xl"):
            with ui.row().classes("items-center gap-3 no-wrap"):
                with ui.element("span").classes("sj-eq-item-icone"):
                    ui.icon("lock").classes("text-xl")
                ui.label("Acesso da equipe").classes("text-xl font-semibold")
            # Diz por que a porta existe: a fila do dia mostra nome de paciente
            # (ADR-007), e a justificativa para isso é saber quem está vendo.
            ui.label(
                "Área restrita aos funcionários da clínica: a fila do dia mostra dados de "
                "pacientes. Não há cadastro aberto: a conta é criada pelo administrador do "
                "sistema."
            ).classes("text-sm sj-suave")
            aviso_de_login = sessao.tirar_aviso_de_login()
            if aviso_de_login:
                tema.aviso(aviso_de_login, "info")
            email = (
                ui.input("E-mail")
                .props('outlined type="email" autocomplete="username"')
                .classes("w-full")
                .mark("login-email")
            )
            with email.add_slot("prepend"):
                ui.icon("mail_outline").classes("sj-suave")
            senha = (
                ui.input("Senha", password=True, password_toggle_button=True)
                .props('outlined autocomplete="current-password"')
                .classes("w-full")
                .mark("login-senha")
            )
            with senha.add_slot("prepend"):
                ui.icon("lock_outline").classes("sj-suave")
            mensagens = ui.column().classes("w-full gap-2")

            async def entrar() -> None:
                mensagens.clear()
                botao.props("loading")
                try:
                    funcionario = await executar_em_thread(
                        logic.autenticar_funcionario, email.value, senha.value
                    )
                except logic.ErroCredenciais as exc:
                    with mensagens:
                        tema.aviso(str(exc), "aviso")
                    return
                except logic.ErroAutenticacaoIndisponivel as exc:
                    with mensagens:
                        tema.aviso(f"Não foi possível entrar: {exc}", "erro")
                    return
                finally:
                    botao.props(remove="loading")
                sessao.entrar(funcionario)
                await self.mostrar()

            botao = (
                ui.button("Entrar", on_click=entrar)
                .props("unelevated no-caps icon=login size=md")
                .classes("w-full")
                .mark("login-entrar")
            )
            email.on("keydown.enter", lambda: senha.run_method("focus"))
            senha.on("keydown.enter", entrar)
            ui.label(
                "Esqueceu a senha ou precisa de acesso? Fale com o administrador do sistema."
            ).classes("text-xs sj-suave text-center w-full")

    # --- faixa do topo --------------------------------------------------------

    def _faixa_do_topo(self) -> None:
        with (
            ui.element("section").classes("sj-eq-foto sj-eq-topo w-full").mark("topo-equipe"),
            ui.column().classes("w-full max-w-7xl mx-auto px-4 sm:px-6 pt-7 pb-6 gap-2"),
        ):
            with ui.element("div").classes("sj-eq-selo"):
                ui.icon("badge").classes("text-base")
                ui.label("Área da equipe")
            ui.label("Painel da clínica").classes("sj-eq-titulo sj-eq-titulo--topo")
            with ui.row().classes("items-center gap-2 sj-suave"):
                ui.icon("event").classes("text-base")
                ui.label(data_por_extenso(logic.hoje_na_clinica())).classes("text-sm").mark(
                    "data-de-hoje"
                )

    # --- abas -----------------------------------------------------------------

    async def _abas(self) -> None:
        with ui.tabs().props("align=left no-caps inline-label").classes(
            "sj-eq-abas w-full"
        ) as abas:
            ui.tab(ABA_FILA, icon="today").mark("aba-principal")
            ui.tab(ABA_PEDIDOS, icon="assignment_ind").mark("aba-principal")
            ui.tab(ABA_TIME_TECNICO, icon="insights").mark("aba-principal")
        with ui.tab_panels(abas, value=ABA_FILA).classes("w-full bg-transparent"):
            with ui.tab_panel(ABA_FILA).classes("px-0"):
                self.fila = FilaDoDia(self)
                await self.fila.montar()
            with ui.tab_panel(ABA_PEDIDOS).classes("px-0"):
                await PedidosDoTitular(self).montar()
            with ui.tab_panel(ABA_TIME_TECNICO).classes("px-0"):
                await TimeTecnico(self, com_aba_de_dev=app_env() == "dev").montar()
