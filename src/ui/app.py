"""
SaúdeJá — interface Streamlit.
Casca inicial, desenhada para CRESCER sem trocar de estrutura: as abas do
funcionário já existem todas, plugadas conforme cada capacidade fica pronta
("Fila do dia" -> Passo 5/banco; "Dev: disparo manual" -> Passo 6/job). Assim
cada capacidade nova é plugada numa aba já testada visualmente, em vez de só
ser validada por pytest/curl até o fim do plano.

Toda a lógica (backends de predição, montagem de payload, tradução de erro)
vive em src/ui/logic.py, que não importa streamlit e é testado sem o runtime
do Streamlit. Este arquivo é só apresentação.

Rodar localmente, da raiz do repositório:
    streamlit run src/ui/app.py
Variáveis: APP_ENV=dev|prod (aba de dev), PREDICT_BACKEND=processo|api,
API_BASE_URL (só no backend 'api').
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

from ui import logic  # noqa: E402

# 'dev' como default: em produção o Passo 11 define APP_ENV=prod
# explicitamente no Space, e rodar local não exige configurar nada para ver
# a aba de acompanhamento do job.
APP_ENV = os.environ.get("APP_ENV", "dev")

ABAS_FUNCIONARIO = ["Testar predição", "Explicabilidade", "Fila do dia"]
ABA_DEV = "Dev: disparo manual"

CHAVE_RESULTADO = "ultimo_resultado"
CHAVE_PAYLOAD = "ultimo_payload"


@st.cache_data(ttl=30, show_spinner=False)
def _status_banco():
    """cache_data com TTL curto: sem ele, cada widget mexido dispararia uma
    consulta de rede só para repintar um rótulo da sidebar; com TTL longo
    demais, a sidebar mentiria por minutos depois de o banco cair."""
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
        "usará (Passo 6). Enquanto não há banco (Passo 5), esta aba é o harness "
        "de teste visual do núcleo de ML."
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
            "Acima do threshold: o job diário (Passo 6) dispararia lembrete pago "
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
            "reservado para o Passo 13."
        )


def _aba_fila_do_dia():
    st.subheader("Fila do dia")
    st.caption(
        "Agendamentos ordenados por risco (probabilidade de no-show). Quem "
        "ainda não tem predição aparece no fim -- o job D-2 (Passo 6) ainda "
        "não rodou para esse agendamento."
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
    col2.metric("Alto risco", resumo["alto_risco"], help="Receberiam lembrete pago no job D-2.")
    col3.metric(
        "Sem predição",
        resumo["sem_predicao"],
        help="O job D-2 ainda não rodou para estes -- não são 'risco zero'.",
    )

    tabela = pd.DataFrame(
        [
            {
                "Paciente": item.id_paciente_externo,
                "Especialidade": item.especialidade,
                "Horário": item.data_hora_agendada,
                "Probabilidade": item.probabilidade,
                "Alto risco": bool(item.classe_prevista) if item.tem_predicao else None,
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
        },
    )

    linhas_selecionadas = selecao.selection.rows if selecao and selecao.selection else []
    if not linhas_selecionadas:
        st.caption("Selecione uma linha para ver a explicação (SHAP) daquela predição.")
        return

    item = fila[linhas_selecionadas[0]]
    st.divider()
    st.write(f"**Por que o paciente `{item.id_paciente_externo}` tem esse risco**")
    if not item.tem_predicao:
        st.info(
            "Este agendamento ainda não foi predito pelo job D-2 (Passo 6) -- "
            "não há explicação gravada."
        )
        return

    st.caption(
        f"probabilidade {item.probabilidade:.1%} · modelo `{item.model_version}` · "
        "explicação lida de `predicoes.explicacao_shap`, gravada junto da predição"
    )
    _mostrar_contribuicoes(item.explicacao, item.explicacao_texto)


def _aba_dev():
    st.subheader("Dev: disparo manual do job de inferência")
    st.caption(
        "Roda `src/jobs/inferencia_diaria.py` (Passo 6) para a data de hoje na "
        "clínica: busca a fila D-2, prediz, grava em `predicoes` e decide o "
        "disparo de lembrete (stub até o Passo 7) por agendamento."
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
    st.write("**Diagnóstico do backend de predição**")
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
    if APP_ENV == "dev":
        with abas[3]:
            _aba_dev()


def _visao_paciente():
    st.subheader("Cadastro e agendamento")
    st.caption(
        "Nome completo e CPF ficam só nesta tela -- o banco nunca grava "
        "nenhum dos dois (LGPD, minimização de PII por design, "
        "docs/architecture.md §4.1). O identificador do paciente no banco é "
        "gerado automaticamente a partir do CPF, e o histórico de no-show é "
        "calculado pela clínica, não autodeclarado."
    )

    especialidades = logic.listar_especialidades()

    # Fora do st.form: precisa reagir de imediato à data escolhida para
    # oferecer só os horários que a clínica atende naquele dia (seg-sex,
    # sábado de manhã, nunca domingo) -- dentro de um form isso só
    # atualizaria no submit, um passo tarde demais.
    data_consulta = st.date_input("Data da consulta", value=logic.proxima_data_disponivel())
    horarios = logic.horarios_disponiveis(data_consulta)
    if not horarios:
        st.warning("A clínica não atende aos domingos -- escolha outra data.")

    with st.form("form_paciente"):
        nome_completo = st.text_input("Nome completo")
        cpf = st.text_input("CPF", placeholder="000.000.000-00")

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
            data_nascimento=data_nascimento,
            sexo=sexo,
            especialidade=especialidade,
            distancia_km=distancia_km,
            data_consulta=data_consulta,
            hora_consulta=hora_consulta,
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
        "(Passo 6), dois dias antes da consulta."
    )


def main():
    st.set_page_config(page_title="SaúdeJá — no-show", page_icon="🩺", layout="wide")
    st.title("SaúdeJá — predição de no-show")

    st.sidebar.header("Perfil")
    perfil = st.sidebar.radio(
        "Quem está usando", options=["Funcionário da clínica", "Paciente"], index=0
    )
    st.sidebar.caption(f"APP_ENV: `{APP_ENV}` · backend: `{logic.backend_ativo()}`")

    banco = _status_banco()
    if banco["conectado"]:
        st.sidebar.success(f"Supabase: {banco['detalhe']}", icon="✅")
    else:
        # Aviso, não erro: a predição manual e a explicabilidade continuam
        # funcionando sem banco -- só a fila e o cadastro dependem dele.
        st.sidebar.warning(f"Supabase: {banco['detalhe']}", icon="⚠️")

    if perfil == "Paciente":
        _visao_paciente()
    else:
        _visao_funcionario()


main()
