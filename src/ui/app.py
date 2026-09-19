"""
SaúdeJá — interface Streamlit (Passo 4 do plano de implementação).

Casca inicial, desenhada para CRESCER sem trocar de estrutura: as abas do
funcionário já existem todas, as que dependem de capacidades ainda não
construídas mostram um placeholder nomeando o passo que as liga ("Fila do
dia" -> Passo 5/banco; "Dev: disparo manual" -> Passo 6/job). Assim cada
capacidade nova é plugada numa aba já testada visualmente, em vez de só ser
validada por pytest/curl até o fim do plano.

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

    contribuicoes = pd.DataFrame(resultado.explicacao)
    contribuicoes["efeito"] = [
        "aumenta o risco" if c > 0 else "reduz o risco" for c in contribuicoes["contribuicao"]
    ]
    st.dataframe(contribuicoes, hide_index=True, width="stretch")
    st.bar_chart(contribuicoes.set_index("feature")["contribuicao"])

    if resultado.explicacao_texto:
        st.info(resultado.explicacao_texto)
    else:
        st.caption(
            "Explicação em linguagem natural (LLM) ainda desativada -- plug "
            "reservado para o Passo 13."
        )


def _aba_fila_do_dia():
    st.subheader("Fila do dia")
    st.info(
        "Conecte o banco — **Passo 5**. Esta aba passará a listar os "
        "agendamentos do dia ordenados por risco (`buscar_fila_do_dia`), com a "
        "probabilidade e a explicação já gravadas pelo job D-2."
    )
    st.date_input("Data da fila", value=date.today(), disabled=True)


def _aba_dev():
    st.subheader("Dev: disparo manual do job de inferência")
    st.info(
        "Disponível a partir do **Passo 6**: este botão chamará "
        "`src/jobs/inferencia_diaria.py::main()` e mostrará quantos "
        "agendamentos foram encontrados, predições gravadas e mensagens "
        "disparadas."
    )
    st.button("Disparar job D-2 agora", disabled=True)

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
    st.info(
        "Disponível a partir do **Passo 5**: sem banco não há onde persistir o "
        "cadastro. O formulário abaixo é apenas a casca da tela."
    )
    with st.form("form_paciente"):
        st.text_input("Identificador do paciente na clínica", disabled=True)
        st.number_input("Idade", min_value=0, max_value=120, value=30, disabled=True)
        st.selectbox("Sexo", options=["F", "M"], disabled=True)
        st.form_submit_button("Agendar", disabled=True)


def main():
    st.set_page_config(page_title="SaúdeJá — no-show", page_icon="🩺", layout="wide")
    st.title("SaúdeJá — predição de no-show")

    st.sidebar.header("Perfil")
    perfil = st.sidebar.radio(
        "Quem está usando", options=["Funcionário da clínica", "Paciente"], index=0
    )
    st.sidebar.caption(f"APP_ENV: `{APP_ENV}` · backend: `{logic.backend_ativo()}`")

    if perfil == "Paciente":
        _visao_paciente()
    else:
        _visao_funcionario()


main()
