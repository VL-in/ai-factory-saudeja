"""
SaúdeJá — interface Streamlit.
Casca inicial, desenhada para CRESCER sem trocar de estrutura: as abas do
funcionário já existem todas, plugadas conforme cada capacidade fica pronta
("Fila do dia" -> banco; "Dev: disparo manual" -> job D-2). Assim cada
capacidade nova é plugada numa aba já testada visualmente, em vez de só ser
validada por pytest/curl.

Toda a lógica (backends de predição, montagem de payload, tradução de erro)
vive em src/ui/logic.py, que não importa streamlit e é testado sem o runtime
do Streamlit. Este arquivo é só apresentação.

Rodar localmente, da raiz do repositório:
    streamlit run src/ui/app.py
Variáveis: APP_ENV=dev|prod (aba de dev), PREDICT_BACKEND=processo|api,
API_BASE_URL (só no backend 'api').

A visão do funcionário exige login (e-mail e senha no Supabase Auth, docs/architecture.md §7);
a do paciente continua aberta, porque é o autoagendamento. Conta de
funcionário se cria com `python scripts/criar_funcionario.py`.
"""
import os
import sys
from datetime import date, time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from logging_config import configurar_logging  # noqa: E402
from ui import logic  # noqa: E402

# No topo do módulo, não dentro de um callback: o Streamlit reexecuta
# este script inteiro a cada interação, e já instalou os handlers dele antes de
# chegar aqui. `configurar_logging` é idempotente -- não duplica handler nem
# linha -- e a cada rerun reaplica o filtro de redação em handler que tenha
# aparecido no meio do caminho.
configurar_logging()

# 'dev' como default: em produção a imagem do Space define APP_ENV=prod
# explicitamente, e rodar local não exige configurar nada para ver
# a aba de acompanhamento do job.
APP_ENV = os.environ.get("APP_ENV", "dev")

PERFIL_PACIENTE = "Paciente"
PERFIL_FUNCIONARIO = "Funcionário da clínica"
PERFIS = [PERFIL_PACIENTE, PERFIL_FUNCIONARIO]

ABAS_FUNCIONARIO = ["Testar predição", "Explicabilidade", "Fila do dia", "Observabilidade"]
ABA_DEV = "Dev: disparo manual"

CHAVE_RESULTADO = "ultimo_resultado"
CHAVE_PAYLOAD = "ultimo_payload"
CHAVE_FUNCIONARIO = "funcionario"
CHAVE_AVISO_LOGIN = "aviso_login"

# Tudo que pertence a quem está logado sai junto com a sessão: sem isso, a
# última predição (com a explicação dela) ficaria na aba "Explicabilidade" para
# o próximo que entrasse no mesmo navegador.
CHAVES_DA_SESSAO_DO_FUNCIONARIO = (CHAVE_FUNCIONARIO, CHAVE_RESULTADO, CHAVE_PAYLOAD)


@st.cache_data(ttl=30, show_spinner=False)
def _status_banco():
    """cache_data com TTL curto: sem ele, cada widget mexido dispararia uma
    consulta de rede só para repintar um rótulo do diagnóstico; com TTL longo
    demais, o diagnóstico mentiria por minutos depois de o banco cair."""
    return logic.status_banco()


@st.cache_resource(show_spinner=False)
def _construir_cliente(backend: str):
    """cache_resource (não cache_data): o cliente carrega modelo +
    TreeExplainer uma vez e é reusado entre reruns do Streamlit -- recarregar
    a cada interação do formulário custaria muito mais que a própria predição.
    Precisa estar no nível do módulo: decorar uma função interna criaria um
    cache novo a cada chamada, que é o mesmo que não ter cache."""
    return logic.obter_cliente(backend)


def _cliente():
    return _construir_cliente(logic.backend_ativo())


def _aba_testar_predicao():
    st.subheader("Testar predição de no-show")
    st.caption(
        "Formulário manual sobre o mesmo módulo de inferência que o job diário "
        "usa. Sem banco configurado, esta aba é o harness de teste visual do "
        "núcleo de ML."
    )

    especialidades = logic.listar_especialidades()

    with st.form("form_predicao"):
        col1, col2, col3 = st.columns(3)
        idade = col1.number_input("Idade", min_value=0, max_value=120, value=45, step=1)
        sexo = col2.selectbox("Sexo", options=["F", "M"])
        if especialidades:
            especialidade = col3.selectbox("Especialidade", options=especialidades)
        else:
            # Sem o artefato do modelo por perto não há vocabulário a oferecer;
            # texto livre + erro 422 claro é melhor que bloquear o formulário.
            especialidade = col3.text_input("Especialidade", value="")

        col4, col5, col6 = st.columns(3)
        distancia_km = col4.number_input(
            "Distância (km)", min_value=0.0, max_value=500.0, value=5.5, step=0.5
        )
        dias = col5.number_input(
            "Dias entre agendamento e consulta", min_value=0, max_value=365, value=14, step=1
        )
        historico_noshow = col6.number_input(
            "No-shows anteriores", min_value=0, max_value=50, value=1, step=1
        )

        col7, col8 = st.columns(2)
        data_consulta = col7.date_input("Data da consulta", value=date(2026, 1, 9))
        hora_consulta = col8.time_input("Hora da consulta", value=time(18, 0))

        enviado = st.form_submit_button("Prever no-show", type="primary")

    if enviado:
        payload = logic.montar_payload(
            idade=idade,
            sexo=sexo,
            especialidade=especialidade,
            distancia_km=distancia_km,
            dias_entre_agendamento_consulta=dias,
            historico_noshow=historico_noshow,
            data_consulta=data_consulta,
            hora_consulta=hora_consulta,
        )
        try:
            resultado = _cliente().predizer(payload)
        except logic.ErroValidacao as exc:
            # Dado recusado: culpa do preenchimento, mensagem acionável.
            st.warning(f"Não foi possível prever com estes dados: {exc}")
            return
        except logic.ErroIndisponivel as exc:
            # Sistema fora do ar: nunca um traceback na cara do funcionário.
            st.error(f"Serviço de predição indisponível: {exc}")
            return
        st.session_state[CHAVE_RESULTADO] = resultado
        st.session_state[CHAVE_PAYLOAD] = payload

    resultado = st.session_state.get(CHAVE_RESULTADO)
    if resultado is None:
        st.info("Preencha o formulário e clique em **Prever no-show**.")
        return

    _mostrar_resultado(resultado)


def _mostrar_resultado(resultado):
    col1, col2, col3 = st.columns(3)
    col1.metric("Probabilidade de no-show", f"{resultado.probabilidade:.1%}")
    col2.metric("Threshold de decisão", f"{resultado.threshold_usado:.2f}")
    col3.metric("Classe prevista", "Alto risco" if resultado.classe_prevista else "Baixo risco")

    if resultado.classe_prevista:
        st.error(
            "Acima do threshold: o job diário dispararia lembrete pago "
            "para este agendamento."
        )
    else:
        st.success("Abaixo do threshold: nenhum lembrete pago seria disparado.")

    st.caption(
        f"modelo `{resultado.model_version}` · threshold de `params.yaml` "
        f"(decision.threshold) · predição via backend `{logic.backend_ativo()}`"
    )


def _aba_explicabilidade():
    st.subheader("Explicabilidade (SHAP)")
    st.caption(
        "SLO §4: 100% das predições acompanhadas de explicação. Os valores são "
        "contribuições em log-odds -- positivo empurra para no-show, negativo "
        "empurra contra."
    )

    resultado = st.session_state.get(CHAVE_RESULTADO)
    if resultado is None:
        st.info("Rode uma predição na aba **Testar predição** para ver a explicação.")
        return

    _mostrar_contribuicoes(resultado.explicacao, resultado.explicacao_texto)


def _mostrar_contribuicoes(explicacao, explicacao_texto=None):
    """Mesma renderização para a predição feita no formulário e para a que o
    job D-2 já gravou no banco (aba "Fila do dia") -- são o mesmo dado, a
    lista de contribuições de src/explain.py, mudando só a origem."""
    if not explicacao:
        st.warning(
            "Predição sem explicação registrada -- o SLO §4 exige 100% de "
            "cobertura, então isso indica um problema a investigar."
        )
        return

    contribuicoes = pd.DataFrame(explicacao)
    contribuicoes["efeito"] = [
        "aumenta o risco" if c > 0 else "reduz o risco" for c in contribuicoes["contribuicao"]
    ]
    st.dataframe(contribuicoes, hide_index=True, width="stretch")
    st.bar_chart(contribuicoes.set_index("feature")["contribuicao"])

    if explicacao_texto:
        st.info(explicacao_texto)
    else:
        st.caption(
            "Explicação em linguagem natural (LLM) ainda desativada -- plug "
            "reservado para a integração opcional com LLM."
        )


def _registrar_desfecho(item):
    """Ação da clínica registrar o que de fato aconteceu com o agendamento
    -- sem isto, `agendamentos.status` nunca sai de 'agendado' e
    o re-treino mensal nunca tem dado real para aprender, além do
    `historico_noshow` automático do cadastro nunca contar nada de verdade."""
    st.write(f"**Registrar desfecho de {item.rotulo_paciente}**")
    st.caption(f"Status atual: `{item.status}`")

    if not logic.pode_registrar_desfecho(item.data_hora_agendada):
        st.info("O desfecho só pode ser registrado no dia da consulta ou depois.")
        return

    col1, col2, col3 = st.columns(3)
    acoes = (
        (col1, "Consulta realizada", logic.STATUS_CONCLUIDO),
        (col2, "Paciente faltou (no-show)", logic.STATUS_NO_SHOW),
        (col3, "Cancelado", logic.STATUS_CANCELADO),
    )
    for coluna, rotulo, status in acoes:
        # disabled em vez de esconder o botão: mostra que a ação já foi
        # tomada em vez de o botão simplesmente sumir da tela.
        if coluna.button(
            rotulo,
            key=f"desfecho_{status}_{item.id_agendamento}",
            disabled=item.status == status,
        ):
            try:
                logic.atualizar_status_agendamento(
                    item.id_agendamento, status, data_hora_agendada=item.data_hora_agendada
                )
            except (logic.ErroPersistencia, logic.ErroDesfechoForaDePrazo) as exc:
                st.error(f"Não foi possível registrar o desfecho: {exc}")
            else:
                st.success(f"Desfecho registrado: {rotulo.lower()}.")
                st.rerun()


def _aba_fila_do_dia():
    st.subheader("Fila do dia")
    st.caption(
        "Agendamentos do dia, do maior para o menor risco de falta. Quem ainda "
        "não foi avaliado aparece no fim da lista."
    )
    # Aviso na tela, não só no ADR: quem opera precisa saber que a tela carrega
    # dado pessoal, porque a decisão de onde posicionar o monitor da recepção é
    # dela, não do código (ADR-007, risco aceito).
    st.caption(
        ":material/lock: Esta tela mostra **nome de paciente** e é de uso "
        "restrito da equipe. Evite deixá-la visível para a sala de espera."
    )

    dia = st.date_input("Data da fila", value=logic.hoje_na_clinica())

    try:
        fila = logic.buscar_fila_do_dia(dia)
    except logic.ErroPersistencia as exc:
        st.error(f"Não foi possível consultar a fila: {exc}")
        return

    if not fila:
        st.info("Nenhum agendamento para esta data.")
        return

    resumo = logic.resumo_da_fila(fila)
    col1, col2, col3 = st.columns(3)
    col1.metric("Agendamentos", resumo["total"])
    col2.metric(
        "Alto risco",
        resumo["alto_risco"],
        help="Pacientes com maior chance de faltar -- são os priorizados para o lembrete.",
    )
    col3.metric(
        "Sem predição",
        resumo["sem_predicao"],
        help=(
            "Ainda não avaliados -- a avaliação é feita dois dias antes da "
            "consulta. Não significa risco zero."
        ),
    )

    tabela = pd.DataFrame(
        [
            {
                "Paciente": item.rotulo_paciente,
                "Especialidade": item.especialidade,
                "Horário": item.data_hora_agendada,
                "Probabilidade": item.probabilidade,
                "Alto risco": bool(item.classe_prevista) if item.tem_predicao else None,
                "Fora do domínio": item.fora_do_dominio,
                "Status": item.status,
            }
            for item in fila
        ]
    )

    selecao = st.dataframe(
        tabela,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Horário": st.column_config.DatetimeColumn(format="HH:mm"),
            # ProgressColumn dá a leitura "quão alto" de relance, que é a
            # pergunta do funcionário ao bater o olho na fila -- um float
            # cru obriga a comparar números linha a linha.
            "Probabilidade": st.column_config.ProgressColumn(
                format="percent", min_value=0.0, max_value=1.0
            ),
            "Alto risco": st.column_config.CheckboxColumn(),
            "Fora do domínio": st.column_config.CheckboxColumn(
                help=(
                    "O agendamento tem algum dado que o modelo não viu no treino "
                    "(ex.: distância acima de 50 km, antecedência acima de 90 dias). "
                    "A probabilidade é extrapolação -- leia com cautela."
                )
            ),
        },
    )

    linhas_selecionadas = selecao.selection.rows if selecao and selecao.selection else []
    if not linhas_selecionadas:
        st.caption(
            "Selecione uma linha para registrar o desfecho e ver por que o "
            "paciente tem esse risco."
        )
        return

    item = fila[linhas_selecionadas[0]]
    st.divider()
    _registrar_desfecho(item)
    st.divider()
    st.write(f"**Por que {item.rotulo_paciente} tem esse risco**")
    # O hash continua visível, em letra miúda: é por ele que se rastreia o
    # paciente no banco e nos logs (onde o nome nunca aparece), então sem ele o
    # suporte perde o único identificador comum entre tela e diagnóstico.
    st.caption(f"identificador interno: `{item.id_paciente_externo}`")
    if not item.tem_predicao:
        st.info(
            "Este agendamento ainda não foi avaliado -- a explicação aparece "
            "depois da avaliação, feita dois dias antes da consulta."
        )
        return

    st.caption(
        f"probabilidade {item.probabilidade:.1%} · modelo `{item.model_version}` · "
        "explicação lida de `predicoes.explicacao_shap`, gravada junto da predição"
    )
    if item.fora_do_dominio:
        st.warning(
            "Este agendamento tem dados fora do que o modelo viu no treino -- a "
            "probabilidade acima é uma extrapolação e merece menos confiança que as demais."
        )
    _mostrar_contribuicoes(item.explicacao, item.explicacao_texto)


JANELAS_OBSERVABILIDADE = {"Últimas 24h": 24, "Últimos 7 dias": 24 * 7, "Últimos 30 dias": 24 * 30}
SLO_P95_MS = 2000  # SLO §2: p95 de uma predição já aquecida < 2s


@st.cache_data(ttl=30, show_spinner=False)
def _resumo_observabilidade(janela_horas: int):
    """Mesmo TTL curto do status do banco: sem cache, cada widget mexido dispararia
    três consultas de agregação; com TTL longo, o painel mentiria por minutos
    depois de o job rodar."""
    return logic.resumo_observabilidade(janela_horas)


def _aba_observabilidade():
    st.subheader("Observabilidade")
    st.caption(
        "Números do SLO medidos em produção, lidos da tabela `eventos_app` "
        "(ADR-006). Uptime (§1) e execução dos jobs agendados (§5) "
        "são medidos **de fora** — por sonda externa e dead-man's-switch —, "
        "porque um coletor que mora dentro do Space some junto com ele quando "
        "hiberna, inclusive a evidência de que caiu."
    )

    rotulo = st.selectbox("Janela", options=list(JANELAS_OBSERVABILIDADE))
    try:
        resumo = _resumo_observabilidade(JANELAS_OBSERVABILIDADE[rotulo])
    except logic.ErroPersistencia as exc:
        # Aviso, não erro: é painel de diagnóstico exibido passivamente (mesma
        # escolha do status do banco na aba de dev) -- sem ele, predição, fila e
        # cadastro seguem funcionando normalmente.
        st.warning(f"Observabilidade indisponível: {exc}")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric(
        "p95 de latência",
        f"{resumo['p95_ms']:.0f} ms" if resumo["p95_ms"] is not None else "—",
        help=f"SLO §2: < {SLO_P95_MS} ms numa predição já aquecida.",
    )
    col2.metric("Predições servidas", resumo["predicoes"])
    col3.metric(
        "Eventos com erro",
        resumo["erros"],
        help="Predição que falhou ou agendamento malformado no job D-2.",
    )

    if resumo["p95_ms"] is not None and resumo["p95_ms"] > SLO_P95_MS:
        st.error(f"p95 acima do alvo do SLO §2 ({SLO_P95_MS} ms).")

    cobertura = resumo["cobertura_explicacao"]
    col4, col5 = st.columns(2)
    col4.metric(
        "Cobertura de explicação (SLO §4)",
        f"{cobertura['percentual']:.0%}" if cobertura["percentual"] is not None else "—",
        help=(
            f"{cobertura['com_explicacao']} de {cobertura['predicoes']} predições gravadas "
            "com `explicacao_shap`. O alvo é 100%: a diretora médica não aceita caixa preta."
        ),
    )
    col5.metric(
        "Última execução do job D-2",
        resumo["ultimo_job_d2"].strftime("%d/%m %H:%M") if resumo["ultimo_job_d2"] else "—",
        help=(
            "Visão de dentro. A prova de que o job agendado NÃO deixou de rodar "
            "vem do dead-man's-switch externo (SLO §5), que alerta pelo silêncio."
        ),
    )

    if cobertura["percentual"] is not None and cobertura["percentual"] < 1:
        st.error(
            "Há predição gravada sem explicação SHAP -- violação do SLO §4, "
            "não só um gráfico faltando."
        )

    if resumo["por_origem"]:
        st.caption(
            "Predições por origem: "
            + " · ".join(f"`{origem}` {n}" for origem, n in resumo["por_origem"].items())
            + " — `processo` é a UI chamando o modelo direto (ADR-005 b), "
            "`api` é a rota `/predict`, `job` é o D-2."
        )

    if resumo["truncado"]:
        st.warning(
            "Janela truncada no limite de eventos lidos -- o p95 acima cobre só "
            "a parte mais recente do período, não ele inteiro."
        )


def _aba_dev():
    st.subheader("Dev: disparo manual do job de inferência")
    st.caption(
        "Roda `src/jobs/inferencia_diaria.py` para a data de hoje na "
        "clínica: busca a fila D-2, prediz, grava em `predicoes` e decide o "
        "disparo de lembrete (stub, a menos que MESSAGING_PROVIDER=infobip) por "
        "agendamento."
    )
    if st.button("Disparar job D-2 agora", type="primary"):
        try:
            resultado = logic.disparar_job_diario()
        except logic.ErroPersistencia as exc:
            st.error(f"Falha ao rodar o job: {exc}")
        else:
            col1, col2, col3 = st.columns(3)
            col1.metric("Agendamentos encontrados", resultado["agendamentos_encontrados"])
            col2.metric("Predições gravadas", resultado["predicoes_gravadas"])
            col3.metric("Mensagens disparadas", resultado["mensagens_disparadas"])
            if resultado["erros"]:
                st.warning(f"{len(resultado['erros'])} agendamento(s) com erro:")
                st.json(resultado["erros"])
            else:
                st.success("Job concluído sem erros.")
            st.caption("Veja o resultado na aba **Fila do dia** (recarrega a cada seleção).")

    st.divider()
    st.write("**Diagnóstico**")
    # Morava na sidebar, à vista de qualquer paciente: além de ruído para quem
    # agenda, expunha detalhe de infraestrutura na URL pública.
    st.caption(f"APP_ENV: `{APP_ENV}` · backend: `{logic.backend_ativo()}`")
    banco = _status_banco()
    if banco["conectado"]:
        st.success(f"Supabase: {banco['detalhe']}", icon=":material/check_circle:")
    else:
        # Aviso, não erro: a predição manual e a explicabilidade continuam
        # funcionando sem banco -- só a fila e o cadastro dependem dele.
        st.warning(f"Supabase: {banco['detalhe']}", icon=":material/warning:")

    # Sob botão de propósito: checar a saúde a cada rerun carregaria o modelo
    # (ou bateria na API) sem que ninguém tenha pedido.
    if st.button("Verificar backend"):
        try:
            st.json(_cliente().saude())
        except logic.ErroPredicao as exc:
            st.error(str(exc))


def _visao_funcionario():
    nomes = list(ABAS_FUNCIONARIO)
    if APP_ENV == "dev":
        nomes.append(ABA_DEV)

    abas = st.tabs(nomes)
    with abas[0]:
        _aba_testar_predicao()
    with abas[1]:
        _aba_explicabilidade()
    with abas[2]:
        _aba_fila_do_dia()
    with abas[3]:
        _aba_observabilidade()
    if APP_ENV == "dev":
        with abas[4]:
            _aba_dev()


def _visao_paciente():
    st.subheader("Cadastro e agendamento")
    st.caption(
        "**O CPF nunca é gravado** -- o identificador do paciente no banco é o "
        "hash dele, gerado automaticamente (LGPD, minimização de PII por "
        "design, docs/architecture.md §4.1), e o histórico de no-show é "
        "calculado pela clínica, não autodeclarado. Nome completo e telefone "
        "**são** gravados: o nome para a equipe conseguir chamar o paciente na "
        "fila do dia (ADR-007) e o telefone porque é para onde o lembrete real "
        "é enviado (Infobip). Nenhum dos dois sai para log, para o "
        "dataset de treino, para a API pública ou para o LLM -- ver "
        "docs/LGPD.md."
    )

    especialidades = logic.listar_especialidades()

    # Fora do st.form: precisa reagir de imediato à data escolhida para
    # oferecer só os horários que a clínica atende naquele dia (seg-sex,
    # sábado de manhã, nunca domingo) -- dentro de um form isso só
    # atualizaria no submit, um passo tarde demais.
    hoje = logic.hoje_na_clinica()
    data_consulta = st.date_input(
        "Data da consulta",
        value=logic.proxima_data_disponivel(),
        min_value=hoje,
        max_value=logic.data_maxima_de_consulta(hoje),
    )
    horarios = logic.horarios_disponiveis(data_consulta)
    if not horarios:
        st.warning("A clínica não atende aos domingos -- escolha outra data.")

    with st.form("form_paciente"):
        nome_completo = st.text_input("Nome completo")
        col_cpf, col_telefone = st.columns(2)
        cpf = col_cpf.text_input("CPF", placeholder="000.000.000-00")
        telefone = col_telefone.text_input(
            "Telefone (WhatsApp/SMS)", placeholder="(11) 98765-4321"
        )

        col1, col2 = st.columns(2)
        data_nascimento = col1.date_input(
            "Data de nascimento",
            value=date(1990, 1, 1),
            min_value=date(1900, 1, 1),
            max_value=logic.hoje_na_clinica(),
        )
        sexo = col2.selectbox("Sexo", options=["F", "M"])

        col3, col4 = st.columns(2)
        if especialidades:
            especialidade = col3.selectbox("Especialidade", options=especialidades)
        else:
            especialidade = col3.text_input("Especialidade", value="")
        distancia_km = col4.number_input(
            "Distância (km)", min_value=0.0, max_value=500.0, value=5.5, step=0.5
        )

        # `dias_entre_agendamento_consulta` não é perguntado: o agendamento
        # está sendo feito agora, então ele é derivado da data escolhida
        # (logic.dias_ate_consulta). Perguntar permitiria gravar um valor
        # incoerente com a própria data -- e é feature do modelo.
        if horarios:
            hora_consulta = st.selectbox(
                "Horário da consulta", options=horarios, format_func=lambda h: h.strftime("%H:%M")
            )
        else:
            hora_consulta = None
        st.caption(
            f"Antecedência do agendamento: **{logic.dias_ate_consulta(data_consulta)} dia(s)** "
            "-- calculada a partir da data escolhida, é uma das features do modelo."
        )

        enviado = st.form_submit_button("Agendar", type="primary")

    if not enviado:
        return

    if not nome_completo:
        st.warning("Informe o nome completo do paciente.")
        return
    if hora_consulta is None:
        st.warning("Escolha uma data em que a clínica atenda.")
        return

    try:
        logic.cadastrar_paciente_e_agendamento(
            cpf=cpf,
            telefone=telefone,
            data_nascimento=data_nascimento,
            sexo=sexo,
            especialidade=especialidade,
            distancia_km=distancia_km,
            data_consulta=data_consulta,
            hora_consulta=hora_consulta,
            nome_completo=nome_completo,
        )
    except logic.ErroValidacaoCadastro as exc:
        st.warning(str(exc))
        return
    except logic.ErroPersistencia as exc:
        st.error(f"Não foi possível cadastrar: {exc}")
        return

    st.success(
        f"Agendamento criado para {nome_completo} em "
        f"{data_consulta:%d/%m/%Y} às {hora_consulta.strftime('%H:%M')}."
    )
    st.caption(
        "A predição de no-show deste agendamento será calculada pelo job D-2 "
        "até dois dias antes da consulta."
    )


def _encerrar_sessao(aviso: str | None = None):
    for chave in CHAVES_DA_SESSAO_DO_FUNCIONARIO:
        st.session_state.pop(chave, None)
    if aviso:
        st.session_state[CHAVE_AVISO_LOGIN] = aviso


def _funcionario_logado():
    """Sessão do funcionário, já expirada por inatividade se for o caso. Cada
    rerun do Streamlit é uma interação de alguém com a tela, então é aqui que
    o relógio de inatividade zera."""
    sessao = st.session_state.get(CHAVE_FUNCIONARIO)
    if sessao is None:
        return None
    if not logic.sessao_ativa(sessao):
        minutos = int(logic.INATIVIDADE_MAXIMA.total_seconds() // 60)
        _encerrar_sessao(f"Sessão encerrada após {minutos} minutos sem uso. Entre novamente.")
        return None
    sessao = logic.registrar_uso(sessao)
    st.session_state[CHAVE_FUNCIONARIO] = sessao
    return sessao


def _tela_login():
    _, centro, _ = st.columns([1, 1.4, 1])
    with centro:
        st.subheader("Acesso da equipe")
        # Diz por que a porta existe: a fila do dia mostra nome de paciente
        # (ADR-007), e a justificativa para isso é saber quem está vendo.
        st.caption(
            "Área restrita aos funcionários da clínica -- a fila do dia mostra "
            "dados de pacientes. Não há cadastro aberto: a conta é criada pelo "
            "administrador do sistema."
        )

        aviso = st.session_state.pop(CHAVE_AVISO_LOGIN, None)
        if aviso:
            st.info(aviso)

        with st.form("form_login"):
            email = st.text_input("E-mail", autocomplete="username")
            senha = st.text_input("Senha", type="password", autocomplete="current-password")
            enviado = st.form_submit_button("Entrar", type="primary", width="stretch")

        if not enviado:
            return

        try:
            sessao = logic.autenticar_funcionario(email, senha)
        except logic.ErroCredenciais as exc:
            st.warning(str(exc))
            return
        except logic.ErroAutenticacaoIndisponivel as exc:
            st.error(f"Não foi possível entrar: {exc}")
            return

        st.session_state[CHAVE_FUNCIONARIO] = sessao
        st.rerun()


def _sidebar_sessao(sessao):
    st.sidebar.divider()
    st.sidebar.caption(f"Conectado como **{sessao.email}**")
    if st.sidebar.button("Sair", icon=":material/logout:"):
        _encerrar_sessao()
        st.rerun()


def main():
    # Título neutro: a tela do paciente é a porta de entrada pública, e quem
    # agenda não precisa saber que vai ser classificado como provável faltante.
    st.set_page_config(page_title="SaúdeJá", page_icon=":material/stethoscope:", layout="wide")
    st.title("SaúdeJá")

    st.sidebar.header("Perfil")
    # Paciente primeiro: é o público aberto, que chega pela URL pública; a
    # equipe é quem sabe trocar de perfil e passar pelo login.
    perfil = st.sidebar.radio("Quem está usando", options=PERFIS, index=0)
    sessao = _funcionario_logado()
    if sessao is not None:
        _sidebar_sessao(sessao)

    if perfil == PERFIL_PACIENTE:
        _visao_paciente()
    elif sessao is None:
        _tela_login()
    else:
        _visao_funcionario()


main()
