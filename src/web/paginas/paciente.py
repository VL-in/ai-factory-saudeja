"""
SaúdeJá — tela do paciente (`/`): ciência, cadastro e agendamento.

É a porta pública: quem chega pela URL do Space agenda sem login. Três etapas,
na mesma página:

1. **Ciência da Política de privacidade e dos Termos de uso** (ADR-010), antes
   de qualquer campo. A tela roda fora do Brasil, e a ciência é a garantia de
   que a informação do art. 9º, com o processamento no exterior em destaque
   (§ 3º), foi apresentada antes de o paciente digitar qualquer coisa. Não é
   consentimento: não há escolha sobre o uso dos dados. No NiceGUI, campo não
   montado é campo que não existe no navegador; o formulário só é montado depois
   do "Continuar". A caixa marcada vive em `app.storage.tab` e acompanha a ida e
   a volta aos documentos, em `/privacidade` e `/termos`.
2. **Formulário**, com exatamente os campos, tipos e formatos da interface
   antiga: campo que vira feature do modelo não muda por causa da troca de
   interface. A validação inteira mora em `logic.cadastrar_paciente_e_agendamento`,
   a única porta para o banco, que devolve todos os problemas de uma vez.
3. **Confirmação no lugar do formulário**: com o formulário ainda na tela, um
   segundo clique em "Agendar" gravaria o mesmo agendamento duas vezes.

A falha do banco aparece como mensagem genérica: o detalhe (variável de ambiente
ausente, erro do PostgREST) é da equipe técnica, que o recebe pelo log e por
`eventos_app`, não de quem está agendando.
"""
from nicegui import ui

import consentimento
import documentos_legais
from ui import logic
from web import dados, sessao, tema
from web.execucao import executar_em_thread
from web.paginas.documentos import ROTAS as ROTAS_DOS_DOCUMENTOS

ROTA = "/"

MENSAGEM_FALHA_NO_CADASTRO = (
    "Não foi possível cadastrar agora. Tente de novo em alguns minutos ou fale com a clínica."
)


def registrar() -> None:
    # Título neutro: quem agenda não precisa saber que vai ser classificado
    # como provável faltante.
    @ui.page(ROTA, title="SaúdeJá")
    async def pagina_paciente() -> None:
        tema.aplicar_tema()
        await ui.context.client.connected()
        tema.cabecalho()
        with ui.column().classes("w-full max-w-3xl mx-auto px-4 py-6 gap-4"):
            ui.label("Cadastro e agendamento").classes("text-2xl font-semibold")
            TelaDoPaciente(ui.column().classes("w-full gap-4")).desenhar()
            link_da_equipe()


def link_da_equipe() -> None:
    """Discreto: a equipe sabe onde fica, e o paciente não precisa dele."""
    ui.link("Área da equipe", "/equipe").classes("text-xs sj-suave self-end")


def mensagem_de_confirmacao(campos: dict, resultado: dict) -> str:
    """Neutra: nem todo agendamento recebe lembrete (só os priorizados), então
    a confirmação não promete contato. A etapa de treino não derruba o
    agendamento (ADR-010), mas o paciente precisa saber que a escolha dele não
    ficou registrada."""
    mensagem = (
        f"Agendamento confirmado para {campos['data_consulta']:%d/%m/%Y} às "
        f"{campos['hora_consulta'].strftime('%H:%M')}."
    )
    if resultado.get("falha_no_treino"):
        if campos["consentimentos"].get(consentimento.FINALIDADE_TREINO):
            mensagem += (
                "\n\nNão foi possível registrar a autorização de aprimoramento; você pode "
                "autorizar no próximo agendamento."
            )
        else:
            mensagem += (
                "\n\nSe você tinha autorizado o aprimoramento antes e quer retirar a "
                "autorização, peça à clínica."
            )
    return mensagem


class TelaDoPaciente:
    def __init__(self, area: ui.column) -> None:
        self.area = area

    def desenhar(self) -> None:
        estado = sessao.aba()
        self.area.clear()
        with self.area:
            confirmacao = estado.get(sessao.CHAVE_AGENDAMENTO_CONFIRMADO)
            if confirmacao is not None:
                self._confirmacao(confirmacao)
            elif not estado.get(sessao.CHAVE_CIENCIA_CONFIRMADA):
                self._etapa_ciencia()
            else:
                self._formulario()

    # --- 1. ciência -----------------------------------------------------------

    def _etapa_ciencia(self) -> None:
        estado = sessao.aba()
        ui.label(
            "Seus dados são usados para o seu atendimento nesta clínica e, só com a sua "
            "autorização, para aprimorar o sistema. O CPF não é armazenado."
        ).classes("text-sm sj-suave")
        with ui.column().classes("gap-0"):
            ui.label("Os dados digitados nesta tela são processados fora do Brasil.").classes(
                "font-semibold"
            ).mark("fora-do-brasil")
            ui.label("Os detalhes estão na Política de privacidade e nos Termos de uso.")
        with ui.row().classes("gap-4"):
            for documento in documentos_legais.DOCUMENTOS:
                with ui.link(target=ROTAS_DOS_DOCUMENTOS[documento]).classes(
                    "flex items-center gap-1 text-primary"
                ).mark(f"abrir-{documento}"):
                    ui.icon("description")
                    ui.label(documentos_legais.TITULOS[documento])

        def ao_marcar(evento) -> None:
            estado[sessao.CHAVE_CIENCIA] = bool(evento.value)
            continuar.set_enabled(bool(evento.value))

        caixa = ui.checkbox(
            documentos_legais.TEXTO_CIENCIA,
            value=bool(estado.get(sessao.CHAVE_CIENCIA, False)),
            on_change=ao_marcar,
        ).mark("ciencia")

        def confirmar() -> None:
            if not caixa.value:
                return
            estado[sessao.CHAVE_CIENCIA_CONFIRMADA] = documentos_legais.versao()
            self.desenhar()

        continuar = (
            ui.button("Continuar", on_click=confirmar)
            .props("unelevated no-caps")
            .mark("continuar")
        )
        continuar.set_enabled(bool(caixa.value))

    # --- 2. formulário --------------------------------------------------------

    def _formulario(self) -> None:
        especialidades = logic.listar_especialidades()
        hoje = logic.hoje_na_clinica()

        with ui.card().classes("w-full p-5 gap-3 rounded-xl"):
            # Reage na hora à data escolhida para oferecer só os horários que a
            # clínica atende naquele dia (seg-sex, sábado de manhã, nunca
            # domingo).
            data_consulta = tema.campo_de_data(
                "Data da consulta",
                logic.proxima_data_disponivel(),
                minimo=hoje,
                maximo=logic.data_maxima_de_consulta(hoje),
                ao_mudar=lambda: atualizar_horarios(),
            ).classes("w-full sm:w-64").mark("paciente-data")
            aviso_de_domingo = ui.column().classes("w-full")

            nome = (
                ui.input("Nome completo").props("outlined dense").classes("w-full")
                .mark("paciente-nome")
            )
            with ui.grid().classes("w-full gap-3 grid-cols-1 sm:grid-cols-2"):
                cpf = ui.input("CPF", placeholder="000.000.000-00").props("outlined dense").mark(
                    "paciente-cpf"
                )
                telefone = ui.input(
                    "Telefone (WhatsApp/SMS)", placeholder="(11) 98765-4321"
                ).props("outlined dense").mark("paciente-telefone")
                # Sem valor padrão: um 01/01/1990 pré-preenchido e esquecido
                # virava idade errada no modelo sem ninguém perceber; vazio, a
                # validação cobra o campo. Mesmo formato dos demais campos de data.
                nascimento = tema.campo_de_data(
                    "Data de nascimento", None, minimo=logic.DATA_NASCIMENTO_MINIMA, maximo=hoje
                ).mark("paciente-nascimento")
                sexo = ui.select(["F", "M"], value="F", label="Sexo").props("outlined dense")
                especialidade: ui.select | ui.input
                if especialidades:
                    especialidade = ui.select(
                        especialidades, value=especialidades[0], label="Especialidade"
                    ).props("outlined dense")
                else:
                    # A validação recusa o cadastro nesse caso (lista não
                    # configurada); o campo continua para o formulário não
                    # mudar de forma.
                    especialidade = ui.input("Especialidade").props("outlined dense")
                distancia = ui.number(
                    "Distância (km)", value=5.5, min=0.0, max=500.0, step=0.5
                ).props("outlined dense")
                # `dias_entre_agendamento_consulta` não é perguntado: é derivado
                # da data escolhida (logic.dias_ate_consulta). Perguntar
                # permitiria gravar um valor incoerente com a própria data, e é
                # feature do modelo.
                horario = ui.select({}, label="Horário da consulta").props(
                    "outlined dense"
                ).mark("paciente-horario")
            antecedencia = ui.label().classes("text-sm sj-suave")

            # Caixa própria, nunca pré-marcada (art. 8º, § 4º: autorização
            # genérica é nula), e a única do formulário: o agendamento e o
            # lembrete são parte do atendimento da clínica (art. 7º, V; LGPD.md,
            # seção 3). A tela não sabe quem é o paciente antes do envio, então a
            # caixa desmarcada é a decisão de agora: revoga o que estava vigente.
            # O efeito da caixa desmarcada e a regra do agendamento de criança
            # estão nos Termos de uso, seções 2 e 5.
            ui.label("Autorização opcional").classes("text-sm font-semibold")
            autorizou_treino = ui.checkbox(consentimento.TERMO_TREINO, value=False).mark(
                "paciente-treino"
            )
            avisos = ui.column().classes("w-full gap-2")
            agendar = ui.button("Agendar").props("unelevated no-caps").mark("agendar")

        def atualizar_horarios() -> None:
            dia = tema.ler_data(data_consulta.value)
            slots = logic.horarios_disponiveis(dia) if dia else []
            horario.set_options({h.strftime("%H:%M"): h.strftime("%H:%M") for h in slots})
            horario.value = slots[0].strftime("%H:%M") if slots else None
            aviso_de_domingo.clear()
            if dia and not slots:
                with aviso_de_domingo:
                    tema.aviso("A clínica não atende aos domingos: escolha outra data.", "aviso")
            antecedencia.text = (
                f"Antecedência do agendamento: {logic.dias_ate_consulta(dia)} dia(s)."
                if dia
                else ""
            )

        async def enviar() -> None:
            campos = {
                "cpf": cpf.value or "",
                "telefone": telefone.value or "",
                "data_nascimento": tema.ler_data(nascimento.value),
                "sexo": sexo.value,
                "especialidade": especialidade.value or "",
                "distancia_km": float(distancia.value or 0),
                "data_consulta": tema.ler_data(data_consulta.value),
                "hora_consulta": tema.ler_hora(horario.value),
                "nome_completo": nome.value or "",
                "consentimentos": {consentimento.FINALIDADE_TREINO: bool(autorizou_treino.value)},
                "versao_documentos": sessao.aba().get(sessao.CHAVE_CIENCIA_CONFIRMADA),
            }
            avisos.clear()
            agendar.disable()
            try:
                if campos["data_consulta"] is None:
                    raise logic.ErroValidacaoCadastro(
                        "Data da consulta inválida: escolha uma data no calendário."
                    )
                resultado = await executar_em_thread(
                    logic.cadastrar_paciente_e_agendamento, **campos
                )
            except logic.ErroValidacaoCadastro as exc:
                with avisos:
                    tema.aviso(
                        "Corrija os campos abaixo para concluir o agendamento:\n"
                        + "\n".join(f"- {problema}" for problema in exc.problemas),
                        "aviso",
                    )
                agendar.enable()
                return
            except logic.ErroPersistencia:
                with avisos:
                    tema.aviso(MENSAGEM_FALHA_NO_CADASTRO, "erro")
                agendar.enable()
                return
            finally:
                # O paciente agenda numa aba e a recepção lê a fila em outra; o
                # cache é do processo, então a limpeza alcança a fila dela.
                dados.invalidar_fila()
            sessao.aba()[sessao.CHAVE_AGENDAMENTO_CONFIRMADO] = mensagem_de_confirmacao(
                campos, resultado
            )
            self.desenhar()

        agendar.on_click(enviar)
        atualizar_horarios()

    # --- 3. confirmação -------------------------------------------------------

    def _confirmacao(self, mensagem: str) -> None:
        with ui.row().classes("w-full items-start gap-2 rounded-lg px-3 py-2 sj-ok no-wrap"):
            ui.icon("event_available").classes("text-lg shrink-0")
            ui.label(mensagem).classes("text-sm whitespace-pre-line").mark("confirmacao")
        ui.label("Se precisar remarcar ou cancelar, entre em contato com a clínica.").classes(
            "text-sm sj-suave"
        )

        def novo_agendamento() -> None:
            # A ciência é de quem agenda, e o próximo agendamento na mesma aba
            # pode ser de outra pessoa (o tablet da recepção, a mãe que agenda
            # para os dois filhos): a caixa volta desmarcada.
            estado = sessao.aba()
            for chave in (
                sessao.CHAVE_AGENDAMENTO_CONFIRMADO,
                sessao.CHAVE_CIENCIA,
                sessao.CHAVE_CIENCIA_CONFIRMADA,
            ):
                estado.pop(chave, None)
            self.desenhar()

        ui.button("Fazer outro agendamento", on_click=novo_agendamento).props(
            "outline no-caps"
        ).mark("novo-agendamento")
