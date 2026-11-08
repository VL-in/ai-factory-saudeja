"""
SaúdeJá — aba "Time técnico" da área da equipe.

O painel do SLO em produção, o teste manual do modelo e a explicação dele e,
só com `APP_ENV=dev`, o disparo manual do job D-2. Não faz parte da rotina da
recepção, mas fica também em produção: é aqui que o painel do SLO de produção
é lido (ADR-006).

Toda chamada que vai ao banco ou ao modelo passa por `executar_em_thread`; o
resumo de observabilidade e o status do banco ficam 30 s em cache
(web/dados.py), como na interface antiga.
"""
import json
import os
from datetime import date, time
from typing import TYPE_CHECKING

from nicegui import ui

from ui import logic
from web import dados, sessao, tema
from web.execucao import executar_em_thread
from web.paginas.fila import mostrar_motivos

if TYPE_CHECKING:
    from web.paginas.equipe import AreaDaEquipe

SUBABA_OBSERVABILIDADE = "Observabilidade"
SUBABA_TESTAR = "Testar predição"
SUBABA_EXPLICABILIDADE = "Explicabilidade"
SUBABA_DEV = "Dev: disparo manual"
SUBABAS = (SUBABA_OBSERVABILIDADE, SUBABA_TESTAR, SUBABA_EXPLICABILIDADE)

JANELAS_OBSERVABILIDADE = {"Últimas 24h": 24, "Últimos 7 dias": 24 * 7, "Últimos 30 dias": 24 * 30}
SLO_P95_MS = 2000  # SLO §2: p95 de uma predição já aquecida < 2s

COLUNAS_SHAP: list[dict] = [
    {"name": "feature", "label": "feature", "field": "feature", "align": "left"},
    {"name": "valor", "label": "valor", "field": "valor", "align": "right"},
    {"name": "contribuicao", "label": "contribuição (log-odds)", "field": "contribuicao",
     "align": "right"},
]


def _predizer(payload: dict) -> logic.ResultadoPredicao:
    return dados.cliente_de_predicao().predizer(payload)


def _saude_do_backend() -> dict:
    return dados.cliente_de_predicao().saude()


class TimeTecnico:
    def __init__(self, area: "AreaDaEquipe", com_aba_de_dev: bool) -> None:
        self.area = area
        self.com_aba_de_dev = com_aba_de_dev

    async def montar(self) -> None:
        ui.label(
            "Área do time técnico: o painel do SLO em produção, o teste manual do modelo e a "
            "explicação dele. Não faz parte da rotina da recepção."
        ).classes("text-sm sj-suave")
        nomes = [*SUBABAS, *([SUBABA_DEV] if self.com_aba_de_dev else [])]
        with ui.tabs().props("align=left no-caps dense").classes("w-full") as subabas:
            for nome in nomes:
                ui.tab(nome).mark("subaba")
        with ui.tab_panels(subabas, value=SUBABA_OBSERVABILIDADE).classes(
            "w-full bg-transparent"
        ):
            with ui.tab_panel(SUBABA_OBSERVABILIDADE).classes("px-0 gap-4"):
                await self._observabilidade()
            with ui.tab_panel(SUBABA_TESTAR).classes("px-0 gap-4"):
                self._testar_predicao()
            with ui.tab_panel(SUBABA_EXPLICABILIDADE).classes("px-0 gap-4"):
                self._explicabilidade = ui.column().classes("w-full gap-3")
                self._desenhar_explicabilidade()
            if self.com_aba_de_dev:
                with ui.tab_panel(SUBABA_DEV).classes("px-0 gap-4"):
                    await self._dev()

    # --- Observabilidade ------------------------------------------------------

    async def _observabilidade(self) -> None:
        ui.label("Observabilidade").classes("text-xl font-semibold")
        ui.label(
            "Números do SLO medidos em produção, lidos da tabela eventos_app (ADR-006). Uptime "
            "(§1) e execução dos jobs agendados (§5) são medidos de fora, por sonda externa e "
            "dead-man's-switch, porque um coletor que mora dentro do Space some junto com ele "
            "quando hiberna, inclusive a evidência de que caiu."
        ).classes("text-sm sj-suave")
        self._janela = (
            ui.select(
                list(JANELAS_OBSERVABILIDADE),
                value=next(iter(JANELAS_OBSERVABILIDADE)),
                label="Janela",
                on_change=self._mudar_janela,
            )
            .props("outlined dense")
            .classes("w-56")
            .mark("janela-observabilidade")
        )
        self._painel = ui.column().classes("w-full gap-4")
        await self._carregar_observabilidade()

    async def _mudar_janela(self) -> None:
        if await self.area.exigir_sessao() is None:
            return
        await self._carregar_observabilidade()

    async def _carregar_observabilidade(self) -> None:
        janela_horas = JANELAS_OBSERVABILIDADE[self._janela.value]
        try:
            resumo = await executar_em_thread(dados.resumo_observabilidade, janela_horas)
        except logic.ErroPersistencia as exc:
            self._painel.clear()
            with self._painel:
                # Aviso, não erro: é diagnóstico exibido passivamente; sem ele,
                # predição, fila e cadastro seguem funcionando.
                tema.aviso(f"Observabilidade indisponível: {exc}", "aviso")
            return
        self._painel.clear()
        with self._painel:
            self._desenhar_resumo(resumo)

    def _desenhar_resumo(self, resumo: dict) -> None:
        p95 = resumo["p95_ms"]
        cobertura = resumo["cobertura_explicacao"]
        with ui.row().classes("w-full gap-4"):
            tema.cartao_metrica(
                "p95 de latência", f"{p95:.0f} ms" if p95 is not None else "—", "speed", "teal",
                f"SLO §2: < {SLO_P95_MS} ms numa predição já aquecida.",
            )
            tema.cartao_metrica("Predições servidas", resumo["predicoes"], "query_stats", "teal")
            tema.cartao_metrica(
                "Eventos com erro", resumo["erros"], "report", "red",
                "Predição que falhou ou agendamento malformado no job D-2.",
            )
        if p95 is not None and p95 > SLO_P95_MS:
            tema.aviso(f"p95 acima do alvo do SLO §2 ({SLO_P95_MS} ms).", "erro")
        with ui.row().classes("w-full gap-4"):
            tema.cartao_metrica(
                "Cobertura de explicação (SLO §4)",
                f"{cobertura['percentual']:.0%}" if cobertura["percentual"] is not None else "—",
                "psychology", "teal",
                f"{cobertura['com_explicacao']} de {cobertura['predicoes']} predições gravadas "
                "com explicacao_shap. O alvo é 100%: a diretora médica não aceita caixa preta.",
            )
            tema.cartao_metrica(
                "Última execução do job D-2",
                resumo["ultimo_job_d2"].strftime("%d/%m %H:%M") if resumo["ultimo_job_d2"] else "—",
                "schedule", "slate",
                "Visão de dentro. A prova de que o job agendado não deixou de rodar vem do "
                "dead-man's-switch externo (SLO §5), que alerta pelo silêncio.",
            )
        if cobertura["percentual"] is not None and cobertura["percentual"] < 1:
            tema.aviso(
                "Há predição gravada sem explicação SHAP: violação do SLO §4, não só um gráfico "
                "faltando.",
                "erro",
            )
        if resumo["por_origem"]:
            ui.label(
                "Predições por origem: "
                + " · ".join(f"{origem} {n}" for origem, n in resumo["por_origem"].items())
                + " — processo é a UI chamando o modelo direto (ADR-005 b), api é a rota "
                "/predict, job é o D-2."
            ).classes("text-sm sj-suave")
        if resumo["truncado"]:
            tema.aviso(
                "Janela truncada no limite de eventos lidos: o p95 acima cobre só a parte mais "
                "recente do período, não ele inteiro.",
                "aviso",
            )

    # --- Testar predição ------------------------------------------------------

    def _testar_predicao(self) -> None:
        ui.label("Testar predição de no-show").classes("text-xl font-semibold")
        ui.label(
            "Formulário manual sobre o mesmo módulo de inferência que o job diário usa. Sem "
            "banco configurado, esta aba é o harness de teste visual do núcleo de ML."
        ).classes("text-sm sj-suave")
        especialidades = logic.listar_especialidades()
        with ui.card().classes("w-full p-5 gap-3 rounded-xl"):
            with ui.grid().classes("w-full gap-3 grid-cols-1 sm:grid-cols-3"):
                idade = ui.number("Idade", value=45, min=0, max=120, step=1, format="%d").props(
                    "outlined dense"
                )
                sexo = ui.select(["F", "M"], value="F", label="Sexo").props("outlined dense")
                especialidade: ui.select | ui.input
                if especialidades:
                    especialidade = ui.select(
                        especialidades, value=especialidades[0], label="Especialidade"
                    ).props("outlined dense")
                else:
                    # Sem o artefato do modelo por perto não há vocabulário a
                    # oferecer; texto livre + erro 422 claro é melhor que
                    # bloquear o formulário.
                    especialidade = ui.input("Especialidade").props("outlined dense")
                distancia = ui.number(
                    "Distância (km)", value=5.5, min=0.0, max=500.0, step=0.5
                ).props("outlined dense")
                dias = ui.number(
                    "Dias entre agendamento e consulta", value=14, min=0, max=365, step=1,
                    format="%d",
                ).props("outlined dense")
                historico = ui.number(
                    "No-shows anteriores", value=1, min=0, max=50, step=1, format="%d"
                ).props("outlined dense")
                data_consulta = tema.campo_de_data("Data da consulta", date(2026, 1, 9))
                hora_consulta = tema.campo_de_hora("Hora da consulta", time(18, 0))
            self._avisos_da_predicao = ui.column().classes("w-full gap-2")

            async def prever() -> None:
                if await self.area.exigir_sessao() is None:
                    return
                self._avisos_da_predicao.clear()
                dia, hora = tema.ler_data(data_consulta.value), tema.ler_hora(hora_consulta.value)
                if dia is None or hora is None:
                    with self._avisos_da_predicao:
                        tema.aviso(
                            "Informe a data (AAAA/MM/DD) e a hora (HH:MM) da consulta.", "aviso"
                        )
                    return
                payload = logic.montar_payload(
                    idade=int(idade.value or 0),
                    sexo=sexo.value,
                    especialidade=especialidade.value or "",
                    distancia_km=float(distancia.value or 0),
                    dias_entre_agendamento_consulta=int(dias.value or 0),
                    historico_noshow=int(historico.value or 0),
                    data_consulta=dia,
                    hora_consulta=hora,
                )
                try:
                    resultado = await executar_em_thread(_predizer, payload)
                except logic.ErroValidacao as exc:
                    # Dado recusado: culpa do preenchimento, mensagem acionável.
                    with self._avisos_da_predicao:
                        tema.aviso(f"Não foi possível prever com estes dados: {exc}", "aviso")
                    return
                except logic.ErroIndisponivel as exc:
                    with self._avisos_da_predicao:
                        tema.aviso(f"Serviço de predição indisponível: {exc}", "erro")
                    return
                estado = sessao.aba()
                estado[sessao.CHAVE_RESULTADO] = resultado
                estado[sessao.CHAVE_PAYLOAD] = payload
                self._desenhar_resultado()
                self._desenhar_explicabilidade()

            ui.button("Prever no-show", on_click=prever).props("unelevated no-caps").mark(
                "prever"
            )
        self._resultado = ui.column().classes("w-full gap-3")
        self._desenhar_resultado()

    def _desenhar_resultado(self) -> None:
        self._resultado.clear()
        resultado = sessao.aba().get(sessao.CHAVE_RESULTADO)
        with self._resultado:
            if resultado is None:
                tema.aviso("Preencha o formulário e clique em Prever no-show.", "info")
                return
            with ui.row().classes("w-full gap-4"):
                tema.cartao_metrica(
                    "Probabilidade de no-show", f"{resultado.probabilidade:.1%}", "percent", "teal"
                )
                tema.cartao_metrica(
                    "Threshold de decisão", f"{resultado.threshold_usado:.2f}", "tune", "slate"
                )
                tema.cartao_metrica(
                    "Classe prevista",
                    "Alto risco" if resultado.classe_prevista else "Baixo risco",
                    "flag", "red" if resultado.classe_prevista else "teal",
                )
            if resultado.classe_prevista:
                tema.aviso(
                    "Acima do threshold: o job diário dispararia lembrete pago para este "
                    "agendamento.",
                    "erro",
                )
            else:
                tema.aviso("Abaixo do threshold: nenhum lembrete pago seria disparado.", "ok")
            ui.label(
                f"modelo {resultado.model_version} · threshold de params.yaml "
                f"(decision.threshold) · predição via backend {logic.backend_ativo()}"
            ).classes("text-xs sj-suave font-mono")

    # --- Explicabilidade ------------------------------------------------------

    def _desenhar_explicabilidade(self) -> None:
        self._explicabilidade.clear()
        resultado = sessao.aba().get(sessao.CHAVE_RESULTADO)
        with self._explicabilidade:
            ui.label("Explicabilidade").classes("text-xl font-semibold")
            ui.label(
                "Os fatores que mais pesaram na última avaliação feita em Testar predição. Para "
                "um paciente da fila, selecione a linha dele na Fila do dia."
            ).classes("text-sm sj-suave")
            if resultado is None:
                tema.aviso(
                    "Nenhuma avaliação feita ainda nesta sessão. Rode uma em Testar predição.",
                    "info",
                )
                return
            mostrar_motivos(resultado.explicacao, resultado.explicacao_texto)
            # A tabela crua em log-odds é de quem mantém o modelo, sobre a
            # predição do teste manual (não é dado de paciente).
            with ui.expansion("Detalhes técnicos").classes("w-full text-sm").props("dense"):
                ui.label(f"modelo {resultado.model_version}").classes("font-mono text-xs")
                if resultado.explicacao:
                    ui.table(
                        columns=COLUNAS_SHAP,
                        rows=[
                            {
                                "feature": c["feature"],
                                "valor": c.get("valor"),
                                "contribuicao": round(float(c["contribuicao"]), 4),
                            }
                            for c in resultado.explicacao
                        ],
                        row_key="feature",
                        pagination=0,
                    ).props("flat dense hide-bottom").classes("w-full")

    # --- Dev: disparo manual --------------------------------------------------

    async def _dev(self) -> None:
        ui.label("Dev: disparo manual do job de inferência").classes("text-xl font-semibold")
        ui.label(
            "Roda src/jobs/inferencia_diaria.py para a data de hoje na clínica: busca a fila "
            "D-2, prediz, grava em predicoes e decide o disparo de lembrete (stub, a menos que "
            "MESSAGING_PROVIDER=infobip) por agendamento."
        ).classes("text-sm sj-suave")
        self._resultado_do_job = ui.column().classes("w-full gap-3")
        ui.button("Disparar job D-2 agora", on_click=self._disparar_job).props(
            "unelevated no-caps"
        ).mark("disparar-job")

        ui.separator()
        ui.label("Diagnóstico").classes("text-base font-semibold")
        # Morava na sidebar da interface antiga, à vista de qualquer paciente.
        ui.label(
            f"APP_ENV: {os.environ.get('APP_ENV') or 'prod'} · backend: {logic.backend_ativo()}"
        ).classes("text-xs font-mono sj-suave").mark("diagnostico")
        banco = await executar_em_thread(dados.status_banco)
        if banco["conectado"]:
            tema.aviso(f"Supabase: {banco['detalhe']}", "ok")
        else:
            # Aviso, não erro: a predição manual e a explicabilidade continuam
            # funcionando sem banco; só a fila e o cadastro dependem dele.
            tema.aviso(f"Supabase: {banco['detalhe']}", "aviso")
        saude = ui.column().classes("w-full gap-2")

        async def verificar_backend() -> None:
            # Sob botão de propósito: checar a saúde a cada abertura carregaria
            # o modelo (ou bateria na API) sem que ninguém tenha pedido.
            if await self.area.exigir_sessao() is None:
                return
            saude.clear()
            try:
                resposta = await executar_em_thread(_saude_do_backend)
            except logic.ErroPredicao as exc:
                with saude:
                    tema.aviso(str(exc), "erro")
                return
            with saude:
                ui.label(json.dumps(resposta, ensure_ascii=False, indent=2)).classes(
                    "font-mono text-xs whitespace-pre"
                )

        ui.button("Verificar backend", on_click=verificar_backend).props("outline no-caps")

    async def _disparar_job(self) -> None:
        if await self.area.exigir_sessao() is None:
            return
        self._resultado_do_job.clear()
        try:
            resultado = await executar_em_thread(logic.disparar_job_diario)
        except logic.ErroPersistencia as exc:
            with self._resultado_do_job:
                tema.aviso(f"Falha ao rodar o job: {exc}", "erro")
            return
        finally:
            # Também depois da falha: o job pode ter gravado parte das predições.
            dados.invalidar_fila()
        with self._resultado_do_job:
            with ui.row().classes("w-full gap-4"):
                tema.cartao_metrica(
                    "Agendamentos encontrados", resultado["agendamentos_encontrados"], "event",
                    "teal",
                )
                tema.cartao_metrica(
                    "Predições gravadas", resultado["predicoes_gravadas"], "save", "teal"
                )
                tema.cartao_metrica(
                    "Mensagens disparadas", resultado["mensagens_disparadas"], "sms", "teal"
                )
            if resultado["erros"]:
                tema.aviso(f"{len(resultado['erros'])} agendamento(s) com erro:", "aviso")
                ui.label(json.dumps(resultado["erros"], ensure_ascii=False, indent=2)).classes(
                    "font-mono text-xs whitespace-pre"
                )
            else:
                tema.aviso("Job concluído sem erros.", "ok")
            ui.label("Veja o resultado na aba Fila do dia (botão Atualizar).").classes(
                "text-sm sj-suave"
            )
