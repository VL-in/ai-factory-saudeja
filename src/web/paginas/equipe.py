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
"""
import os

from nicegui import ui

from ui import logic
from web import sessao, tema
from web.execucao import executar_em_thread
from web.paginas.fila import FilaDoDia
from web.paginas.pedidos import PedidosDoTitular
from web.paginas.time_tecnico import TimeTecnico

ROTA = "/equipe"
ABA_FILA = "Fila do dia"
ABA_PEDIDOS = "Pedidos do titular"
ABA_TIME_TECNICO = "Time técnico"
ABAS_FUNCIONARIO = (ABA_FILA, ABA_PEDIDOS, ABA_TIME_TECNICO)

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
    @ui.page(ROTA, title="Área da equipe · SaúdeJá")
    async def pagina_equipe() -> None:
        tema.aplicar_tema()
        await ui.context.client.connected()
        await AreaDaEquipe().montar()


class AreaDaEquipe:
    def __init__(self) -> None:
        self.fila: FilaDoDia | None = None

    async def montar(self) -> None:
        self._acoes = tema.cabecalho("Área da equipe")
        self._conteudo = ui.column().classes("w-full max-w-7xl mx-auto px-4 sm:px-6 py-6 gap-5")
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
        with ui.card().classes("w-full max-w-sm mx-auto p-8 gap-4 rounded-xl"):
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
                .props('outlined autocomplete="username"')
                .classes("w-full")
                .mark("login-email")
            )
            senha = (
                ui.input("Senha", password=True, password_toggle_button=True)
                .props('outlined autocomplete="current-password"')
                .classes("w-full")
                .mark("login-senha")
            )
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
                .props("unelevated no-caps")
                .classes("w-full")
                .mark("login-entrar")
            )
            senha.on("keydown.enter", entrar)

    # --- abas -----------------------------------------------------------------

    async def _abas(self) -> None:
        with ui.tabs().props("align=left no-caps inline-label").classes("w-full") as abas:
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
