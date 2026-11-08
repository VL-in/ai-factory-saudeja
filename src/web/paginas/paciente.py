"""
SaúdeJá — tela do paciente (`/paciente`): ciência, cadastro e agendamento.

Aberta, sem login: quem chega pela URL do Space escolhe "Sou paciente" na
tela inicial (`/`, `inicio.py`) e agenda aqui. Três etapas,
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

Visual na mesma linha da tela inicial (`inicio.py`): faixa de topo com a mesma
foto, servida pela aplicação, sob o mesmo véu na cor do tema; indicador das três
etapas; formulário em seções (consulta, dados pessoais, autorização) com um
resumo ao lado que acompanha a data, o horário e a especialidade escolhidos.
O CSS é constante (regra D7): nenhum dado entra nele, e os textos vão por
`ui.label`, que escapa o conteúdo.
"""
from nicegui import ui

import consentimento
import documentos_legais
from ui import logic
from web import dados, rotas, sessao, tema
from web.execucao import executar_em_thread
from web.paginas.documentos import ROTAS as ROTAS_DOS_DOCUMENTOS

ROTA = rotas.PACIENTE

TITULO = "Cadastro e agendamento"
SUBTITULO = (
    "Em três passos: veja como seus dados são usados, preencha seus dados e "
    "escolha o melhor horário. Sem login."
)
ETAPAS = ("Seus dados e privacidade", "Cadastro e horário", "Confirmação")

MENSAGEM_FALHA_NO_CADASTRO = (
    "Não foi possível cadastrar agora. Tente de novo em alguns minutos ou fale com a clínica."
)

CSS_PACIENTE = (
    "<style>"
    # Faixa do topo: a foto do início, mais baixa e mais esmaecida.
    ".sj-ag-topo{position:relative;isolation:isolate;overflow:hidden;"
    "border-bottom:1px solid var(--sj-borda)}"
    # URL literal, sem f-string: a guarda de HTML cru só aceita literal;
    # test_paciente confere que é inicio.URL_IMAGEM_DE_FUNDO.
    '.sj-ag-topo::before{content:"";position:absolute;inset:0;z-index:-2;'
    'background:url("/estatico/inicio-fundo.webp") 70% 35%/cover no-repeat}'
    '.sj-ag-topo::after{content:"";position:absolute;inset:0;z-index:-1;'
    "background:linear-gradient(90deg,var(--sj-fundo) 0%,"
    "color-mix(in srgb,var(--sj-fundo) 94%,transparent) 45%,"
    "color-mix(in srgb,var(--sj-fundo) 65%,transparent) 75%,"
    "color-mix(in srgb,var(--sj-fundo) 45%,transparent) 100%)}"
    "@media (max-width:767px){.sj-ag-topo::after{"
    "background:color-mix(in srgb,var(--sj-fundo) 90%,transparent)}}"
    ".sj-ag-selo{display:inline-flex;align-items:center;gap:.4rem;padding:.3rem .75rem;"
    "border-radius:999px;font-size:.8rem;font-weight:600;color:var(--q-primary);"
    "background:color-mix(in srgb,var(--q-primary) 12%,var(--sj-superficie))}"
    ".sj-ag-titulo{font-size:clamp(1.8rem,4.5vw,2.6rem);font-weight:700;line-height:1.1;"
    "letter-spacing:-.02em;margin:0}"
    # Indicador das etapas.
    ".sj-ag-etapas{display:flex;gap:.5rem;list-style:none;margin:0;padding:0;width:100%}"
    ".sj-ag-etapa{flex:1 1 0;display:flex;align-items:center;gap:.6rem;min-width:0;"
    "padding:.65rem .8rem;border-radius:.8rem;border:1px solid var(--sj-borda);"
    "background:var(--sj-superficie);color:var(--sj-suave);font-size:.85rem}"
    ".sj-ag-etapa-num{display:grid;place-items:center;flex-shrink:0;width:1.75rem;"
    "height:1.75rem;border-radius:999px;font-weight:700;font-size:.8rem;"
    "background:color-mix(in srgb,currentColor 14%,transparent)}"
    ".sj-ag-etapa--atual{color:var(--sj-texto);border-color:var(--q-primary);"
    "box-shadow:0 0 0 1px var(--q-primary)}"
    ".sj-ag-etapa--atual .sj-ag-etapa-num{background:var(--q-primary);color:#fff}"
    ".sj-ag-etapa--feita{color:var(--q-primary)}"
    "@media (max-width:639px){.sj-ag-etapa:not(.sj-ag-etapa--atual) .sj-ag-etapa-nome"
    "{display:none}.sj-ag-etapa{flex:0 0 auto}.sj-ag-etapa--atual{flex:1 1 auto}}"
    # Grade do formulário: resumo ao lado no desktop, abaixo no celular.
    ".sj-ag-grade{display:grid;gap:1.25rem;width:100%;align-items:start}"
    "@media (min-width:1024px){.sj-ag-grade{grid-template-columns:minmax(0,1fr) 300px}"
    ".sj-ag-lado{position:sticky;top:88px}}"
    ".sj-ag-secao-icone{display:grid;place-items:center;width:2.25rem;height:2.25rem;"
    "flex-shrink:0;border-radius:.65rem;color:var(--q-primary);"
    "background:color-mix(in srgb,var(--q-primary) 12%,transparent)}"
    ".sj-ag-destaque{border-left:4px solid var(--q-primary);border-radius:.6rem;"
    "background:color-mix(in srgb,var(--q-primary) 8%,var(--sj-superficie))}"
    ".sj-ag-opcional{border:1px dashed var(--sj-borda);border-radius:.8rem}"
    # Links dos documentos, no mesmo desenho dos acessos do início.
    ".sj-ag-doc{display:flex;align-items:center;gap:.75rem;flex:1 1 220px;min-height:44px;"
    "padding:.75rem 1rem;border-radius:.8rem;text-decoration:none;color:var(--sj-texto);"
    "border:1px solid var(--sj-borda);background:var(--sj-superficie);"
    "transition:border-color .15s,box-shadow .15s}"
    ".sj-ag-doc:hover{border-color:var(--q-primary);box-shadow:0 6px 18px rgba(15,23,42,.10)}"
    ".sj-ag-doc:focus-visible{outline:3px solid var(--q-primary);outline-offset:3px}"
    ".sj-ag-resumo-linha{display:flex;align-items:center;gap:.6rem;width:100%;padding:.55rem 0;"
    "border-bottom:1px solid var(--sj-borda)}"
    ".sj-ag-resumo-linha:last-child{border-bottom:0}"
    ".sj-ag-ok-icone{display:grid;place-items:center;width:4rem;height:4rem;"
    "border-radius:999px;background:var(--sj-ok-fundo);color:var(--sj-ok-texto)}"
    "@media (prefers-reduced-motion:reduce){.sj-ag-doc{transition:none}}"
    "</style>"
)


def registrar() -> None:
    # Título neutro: quem agenda não precisa saber que vai ser classificado
    # como provável faltante.
    @ui.page(ROTA, title="SaúdeJá")
    async def pagina_paciente() -> None:
        tema.aplicar_tema()
        ui.add_head_html(CSS_PACIENTE)
        # Sem o respiro padrão do NiceGUI: a faixa do topo vai de borda a borda.
        ui.query(".nicegui-content").classes("p-0")
        await ui.context.client.connected()
        tema.cabecalho("Agendamento")
        with (
            ui.element("section").classes("sj-ag-topo w-full").mark("topo-agendamento"),
            ui.column().classes("w-full max-w-5xl mx-auto px-4 sm:px-8 pt-8 pb-7 gap-3"),
        ):
            with ui.link(target=rotas.INICIO).classes(
                "flex items-center gap-1 text-sm sj-suave no-underline"
            ).mark("voltar-inicio"):
                ui.icon("arrow_back").classes("text-base")
                ui.label("Início")
            with ui.element("div").classes("sj-ag-selo"):
                ui.icon("event_available").classes("text-base")
                ui.label("Agendamento online")
            ui.label(TITULO).classes("sj-ag-titulo")
            ui.label(SUBTITULO).classes("text-base sj-suave max-w-xl")
        with ui.column().classes("w-full max-w-5xl mx-auto px-4 sm:px-8 py-6 gap-5"):
            TelaDoPaciente(ui.column().classes("w-full gap-5")).desenhar()
            link_da_equipe()


def link_da_equipe() -> None:
    """Discreto: a equipe sabe onde fica, e o paciente não precisa dele."""
    ui.link("Área da equipe", rotas.EQUIPE).classes("text-xs sj-suave self-end")


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


# --- peças visuais -------------------------------------------------------------


def _indicador_de_etapas(atual: int) -> None:
    """Onde o paciente está e quanto falta. Só leitura: as etapas não são
    clicáveis, porque voltar à ciência depois dela dada não tem sentido."""
    with ui.element("ol").classes("sj-ag-etapas").props(
        'aria-label="Etapas do agendamento"'
    ).mark("etapas"):
        for indice, nome in enumerate(ETAPAS, start=1):
            if indice < atual:
                estado = " sj-ag-etapa--feita"
            elif indice == atual:
                estado = " sj-ag-etapa--atual"
            else:
                estado = ""
            item = ui.element("li").classes(f"sj-ag-etapa{estado}")
            if indice == atual:
                item.props('aria-current="step"')
            with item:
                with ui.element("span").classes("sj-ag-etapa-num"):
                    if indice < atual:
                        ui.icon("check").classes("text-base")
                    else:
                        ui.label(str(indice))
                ui.label(nome).classes("sj-ag-etapa-nome truncate font-medium")


def _titulo_de_secao(icone: str, titulo: str, descricao: str | None = None) -> None:
    with ui.row().classes("items-center gap-3 no-wrap"):
        with ui.element("span").classes("sj-ag-secao-icone"):
            ui.icon(icone).classes("text-xl")
        with ui.column().classes("gap-0"):
            ui.label(titulo).classes("text-base font-semibold leading-tight")
            if descricao:
                ui.label(descricao).classes("text-xs sj-suave")


def _linha_do_resumo(icone: str, rotulo: str) -> ui.label:
    with ui.element("div").classes("sj-ag-resumo-linha"):
        ui.icon(icone).classes("text-lg sj-suave")
        with ui.column().classes("gap-0 min-w-0"):
            ui.label(rotulo).classes("text-xs sj-suave")
            valor = ui.label("—").classes("text-sm font-semibold")
    return valor


class TelaDoPaciente:
    def __init__(self, area: ui.column) -> None:
        self.area = area

    def desenhar(self) -> None:
        estado = sessao.aba()
        self.area.clear()
        with self.area:
            confirmacao = estado.get(sessao.CHAVE_AGENDAMENTO_CONFIRMADO)
            if confirmacao is not None:
                _indicador_de_etapas(3)
                self._confirmacao(confirmacao)
            elif not estado.get(sessao.CHAVE_CIENCIA_CONFIRMADA):
                _indicador_de_etapas(1)
                self._etapa_ciencia()
            else:
                _indicador_de_etapas(2)
                self._formulario()

    # --- 1. ciência -----------------------------------------------------------

    def _etapa_ciencia(self) -> None:
        estado = sessao.aba()
        with ui.card().classes("w-full p-5 sm:p-7 gap-5 rounded-2xl"):
            _titulo_de_secao(
                "verified_user",
                "Antes de começar",
                "Leia como seus dados são usados. Nenhum campo é pedido antes disso.",
            )
            ui.label(
                "Seus dados são usados para o seu atendimento nesta clínica e, só com a sua "
                "autorização, para aprimorar o sistema. O CPF não é armazenado."
            ).classes("text-sm sj-suave")
            with ui.row().classes("sj-ag-destaque w-full items-start gap-3 px-4 py-3 no-wrap"):
                ui.icon("public").classes("text-xl text-primary shrink-0")
                with ui.column().classes("gap-0"):
                    ui.label(
                        "Os dados digitados nesta tela são processados fora do Brasil."
                    ).classes("font-semibold").mark("fora-do-brasil")
                    ui.label(
                        "Os detalhes estão na Política de privacidade e nos Termos de uso."
                    ).classes("text-sm")
            with ui.row().classes("w-full gap-3"):
                for documento in documentos_legais.DOCUMENTOS:
                    with ui.link(target=ROTAS_DOS_DOCUMENTOS[documento]).classes(
                        "sj-ag-doc"
                    ).mark(f"abrir-{documento}"):
                        with ui.element("span").classes("sj-ag-secao-icone"):
                            ui.icon("description").classes("text-xl")
                        with ui.column().classes("gap-0 grow"):
                            ui.label(documentos_legais.TITULOS[documento]).classes(
                                "text-sm font-semibold"
                            )
                            ui.label("Ler o documento").classes("text-xs sj-suave")
                        ui.icon("arrow_forward").classes("text-lg sj-suave")

            ui.separator()

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

            with ui.row().classes("w-full items-center justify-end gap-3"):
                continuar = (
                    ui.button("Continuar", on_click=confirmar)
                    .props("unelevated no-caps icon-right=arrow_forward size=md")
                    .classes("px-5 w-full sm:w-auto")
                    .mark("continuar")
                )
            continuar.set_enabled(bool(caixa.value))

    # --- 2. formulário --------------------------------------------------------

    def _formulario(self) -> None:
        especialidades = logic.listar_especialidades()
        hoje = logic.hoje_na_clinica()

        with ui.element("div").classes("sj-ag-grade"):
            with ui.card().classes("w-full p-5 sm:p-7 gap-5 rounded-2xl"):
                # --- a consulta ---
                _titulo_de_secao(
                    "calendar_month",
                    "Sua consulta",
                    "Segunda a sexta e sábado de manhã. Só aparecem os horários do dia.",
                )
                with ui.grid().classes("w-full gap-3 grid-cols-1 sm:grid-cols-2"):
                    # Reage na hora à data escolhida para oferecer só os
                    # horários que a clínica atende naquele dia (seg-sex,
                    # sábado de manhã, nunca domingo).
                    data_consulta = tema.campo_de_data(
                        "Data da consulta",
                        logic.proxima_data_disponivel(),
                        minimo=hoje,
                        maximo=logic.data_maxima_de_consulta(hoje),
                        ao_mudar=lambda: atualizar_horarios(),
                    ).classes("w-full").mark("paciente-data")
                    horario = (
                        ui.select({}, label="Horário da consulta")
                        .props("outlined dense")
                        .classes("w-full")
                        .mark("paciente-horario")
                    )
                    especialidade: ui.select | ui.input
                    if especialidades:
                        especialidade = (
                            ui.select(
                                especialidades, value=especialidades[0], label="Especialidade"
                            )
                            .props("outlined dense")
                            .classes("w-full")
                        )
                    else:
                        # A validação recusa o cadastro nesse caso (lista não
                        # configurada); o campo continua para o formulário não
                        # mudar de forma.
                        especialidade = (
                            ui.input("Especialidade").props("outlined dense").classes("w-full")
                        )
                    # Os limites do campo são só ajuda de preenchimento: o
                    # NiceGUI os aplica quando o campo perde o foco, e quem
                    # recusa de fato é logic.validar_cadastro, com o máximo do
                    # contrato.
                    distancia = (
                        ui.number(
                            "Distância até a clínica (km)",
                            value=5.5,
                            min=0.0,
                            max=logic.DISTANCIA_MAXIMA_KM,
                            step=0.5,
                        )
                        .props('outlined dense hint="Aproximada, de onde você sai até a clínica"')
                        .classes("w-full")
                        .mark("paciente-distancia")
                    )
                    # `dias_entre_agendamento_consulta` não é perguntado: é
                    # derivado da data escolhida (logic.dias_ate_consulta).
                    # Perguntar permitiria gravar um valor incoerente com a
                    # própria data, e é feature do modelo.
                aviso_de_domingo = ui.column().classes("w-full")

                ui.separator()

                # --- quem vai ser atendido ---
                _titulo_de_secao(
                    "person",
                    "Seus dados",
                    "De quem vai ser atendido. O CPF serve só para identificar você.",
                )
                nome = (
                    ui.input("Nome completo")
                    .props('outlined dense autocomplete="name"')
                    .classes("w-full")
                    .mark("paciente-nome")
                )
                with ui.grid().classes("w-full gap-3 grid-cols-1 sm:grid-cols-2"):
                    cpf = (
                        ui.input("CPF", placeholder="000.000.000-00")
                        .props(
                            'outlined dense inputmode="numeric" autocomplete="off" '
                            'hint="Não é armazenado"'
                        )
                        .mark("paciente-cpf")
                    )
                    telefone = (
                        ui.input("Telefone (WhatsApp/SMS)", placeholder="(11) 98765-4321")
                        .props('outlined dense type="tel" inputmode="tel" autocomplete="tel"')
                        .mark("paciente-telefone")
                    )
                    # Sem valor padrão: um 01/01/1990 pré-preenchido e esquecido
                    # virava idade errada no modelo sem ninguém perceber; vazio,
                    # a validação cobra o campo. Mesmo formato dos demais campos
                    # de data.
                    nascimento = tema.campo_de_data(
                        "Data de nascimento",
                        None,
                        minimo=logic.data_nascimento_minima(hoje),
                        maximo=hoje,
                    ).mark("paciente-nascimento")
                    sexo = ui.select(
                        {"F": "Feminino", "M": "Masculino"}, value="F", label="Sexo"
                    ).props("outlined dense")

                ui.separator()

                # Caixa própria, nunca pré-marcada (art. 8º, § 4º: autorização
                # genérica é nula), e a única do formulário: o agendamento e o
                # lembrete são parte do atendimento da clínica (art. 7º, V;
                # LGPD.md, seção 3). A tela não sabe quem é o paciente antes do
                # envio, então a caixa desmarcada é a decisão de agora: revoga o
                # que estava vigente. O efeito da caixa desmarcada e a regra do
                # agendamento de criança estão nos Termos de uso, seções 2 e 5.
                with ui.column().classes("sj-ag-opcional w-full gap-2 p-4"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("tune").classes("text-lg sj-suave")
                        ui.label("Autorização opcional").classes("text-sm font-semibold")
                        ui.label("Não afeta o seu agendamento").classes("text-xs sj-suave")
                    autorizou_treino = ui.checkbox(value=False).mark("paciente-treino")
                    with autorizou_treino:
                        tema.texto_com_destaque(consentimento.TERMO_TREINO)

                avisos = ui.column().classes("w-full gap-2")
                with ui.row().classes("w-full items-center justify-between gap-3"):
                    ui.label("Confira os dados antes de confirmar.").classes(
                        "text-xs sj-suave max-sm:hidden"
                    )
                    agendar = (
                        ui.button("Confirmar agendamento")
                        .props("unelevated no-caps icon=event_available size=md")
                        .classes("px-5 w-full sm:w-auto")
                        .mark("agendar")
                    )

            # --- resumo ao lado ---
            with ui.column().classes("sj-ag-lado w-full gap-4"):
                with ui.card().classes("w-full p-5 gap-1 rounded-2xl").mark("resumo"):
                    ui.label("Resumo da consulta").classes("text-base font-semibold mb-1")
                    resumo_data = _linha_do_resumo("event", "Data")
                    resumo_hora = _linha_do_resumo("schedule", "Horário")
                    resumo_especialidade = _linha_do_resumo("medical_services", "Especialidade")
                    resumo_antecedencia = _linha_do_resumo("hourglass_empty", "Antecedência")
                with ui.card().classes("w-full p-5 gap-2 rounded-2xl"):
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        ui.icon("lock").classes("text-lg text-primary")
                        ui.label("Privacidade").classes("text-sm font-semibold")
                    ui.label(
                        "O CPF não é armazenado. Você pode reler a Política e os Termos a "
                        "qualquer momento."
                    ).classes("text-xs sj-suave")
                    with ui.row().classes("gap-x-3 gap-y-1"):
                        for documento in documentos_legais.DOCUMENTOS:
                            ui.link(
                                documentos_legais.TITULOS[documento],
                                ROTAS_DOS_DOCUMENTOS[documento],
                            ).classes("text-xs text-primary")

        def atualizar_resumo() -> None:
            dia = tema.ler_data(data_consulta.value)
            resumo_data.text = dia.strftime("%d/%m/%Y") if dia else "—"
            resumo_hora.text = horario.value or "—"
            resumo_especialidade.text = str(especialidade.value or "—")
            if dia:
                dias = logic.dias_ate_consulta(dia)
                resumo_antecedencia.text = (
                    "Hoje" if dias == 0 else f"{dias} dia" + ("s" if dias != 1 else "")
                )
            else:
                resumo_antecedencia.text = "—"

        def atualizar_horarios() -> None:
            dia = tema.ler_data(data_consulta.value)
            slots = logic.horarios_disponiveis(dia) if dia else []
            horario.set_options({h.strftime("%H:%M"): h.strftime("%H:%M") for h in slots})
            horario.value = slots[0].strftime("%H:%M") if slots else None
            aviso_de_domingo.clear()
            if dia and not slots:
                with aviso_de_domingo:
                    tema.aviso("A clínica não atende aos domingos: escolha outra data.", "aviso")
            atualizar_resumo()

        horario.on_value_change(lambda: atualizar_resumo())
        especialidade.on_value_change(lambda: atualizar_resumo())

        def ocupado(sim: bool) -> None:
            """Botão travado e girando enquanto o cadastro vai ao banco: o
            paciente vê que o clique foi recebido e não clica de novo."""
            if sim:
                agendar.disable()
                agendar.props("loading")
            else:
                agendar.props(remove="loading")
                agendar.enable()

        async def enviar() -> None:
            campos = {
                "cpf": cpf.value or "",
                "telefone": telefone.value or "",
                "data_nascimento": tema.ler_data(nascimento.value),
                "sexo": sexo.value,
                "especialidade": especialidade.value or "",
                # Vazio segue vazio: a validação cobra o campo, em vez de gravar 0 km.
                "distancia_km": distancia.value,
                "data_consulta": tema.ler_data(data_consulta.value),
                "hora_consulta": tema.ler_hora(horario.value),
                "nome_completo": nome.value or "",
                "consentimentos": {consentimento.FINALIDADE_TREINO: bool(autorizou_treino.value)},
                "versao_documentos": sessao.aba().get(sessao.CHAVE_CIENCIA_CONFIRMADA),
            }
            avisos.clear()
            ocupado(True)
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
                ocupado(False)
                return
            except logic.ErroPersistencia:
                with avisos:
                    tema.aviso(MENSAGEM_FALHA_NO_CADASTRO, "erro")
                ocupado(False)
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
        with ui.card().classes(
            "w-full max-w-2xl mx-auto p-6 sm:p-8 gap-4 rounded-2xl items-center text-center"
        ):
            with ui.element("span").classes("sj-ag-ok-icone"):
                ui.icon("check_circle").classes("text-4xl")
            ui.label("Tudo certo!").classes("text-2xl font-bold")
            ui.label(mensagem).classes("text-base whitespace-pre-line max-w-md").mark(
                "confirmacao"
            )
            with ui.row().classes("sj-info w-full items-start gap-2 rounded-lg px-4 py-3 no-wrap"):
                ui.icon("support_agent").classes("text-lg shrink-0")
                ui.label(
                    "Se precisar remarcar ou cancelar, entre em contato com a clínica."
                ).classes("text-sm text-left")

            def novo_agendamento() -> None:
                # A ciência é de quem agenda, e o próximo agendamento na mesma
                # aba pode ser de outra pessoa (o tablet da recepção, a mãe que
                # agenda para os dois filhos): a caixa volta desmarcada.
                estado = sessao.aba()
                for chave in (
                    sessao.CHAVE_AGENDAMENTO_CONFIRMADO,
                    sessao.CHAVE_CIENCIA,
                    sessao.CHAVE_CIENCIA_CONFIRMADA,
                ):
                    estado.pop(chave, None)
                self.desenhar()

            with ui.row().classes("w-full justify-center gap-3 mt-2"):
                ui.button("Fazer outro agendamento", on_click=novo_agendamento).props(
                    "unelevated no-caps icon=add"
                ).mark("novo-agendamento")
                with ui.link(target=rotas.INICIO).classes(
                    "flex items-center gap-1 px-4 py-2 rounded text-primary no-underline"
                ).mark("ir-ao-inicio"):
                    ui.icon("home").classes("text-lg")
                    ui.label("Voltar ao início")
