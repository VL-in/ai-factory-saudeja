"""
SaúdeJá — estado por aba da interface em NiceGUI (ADR-013, regra 2).

Tudo vive em `app.storage.tab`: memória do processo, uma entrada por aba do
navegador, sem cookie e sem disco. É o papel que o `st.session_state` tinha
(docs/architecture.md §7). Ficam proibidos, e `tests/test_coerencia_repo.py`
os barra em `src/web/`:

- `app.storage.user`: grava em disco (`.nicegui/`) e exige `storage_secret`;
- `app.storage.browser`: manda o estado ao navegador, num cookie;
- `app.storage.general`: compartilhado entre todos e gravado em disco.

O que fica na aba:

- a sessão do funcionário (`logic.SessaoFuncionario`, sem token do Supabase),
  conferida por `logic.sessao_ativa` a cada ação;
- a última predição da aba "Testar predição", que sai junto com a sessão;
- a ciência do paciente na Política e nos Termos, que acompanha a ida e a
  volta aos documentos, porque o `tab_id` é o mesmo na navegação da aba;
- o id aleatório de telemetria (`session.id` dos spans), sem ligação com o
  e-mail de quem entrou.

A aba fechada some da memória `INATIVIDADE_MAXIMA` depois da última mudança
(`configurar_storage`), em vez dos 30 dias padrão do NiceGUI: o padrão
guardaria e-mail e id do funcionário no processo por um mês.

Duplicar a aba copia o estado, sessão inclusive (`copy_tab` do NiceGUI): é o
mesmo navegador e a mesma pessoa, e fica aceito (docs/architecture.md §7).
"""
import uuid
from datetime import datetime

from nicegui import app, context
from nicegui.observables import ObservableDict

from ui import logic

CHAVE_FUNCIONARIO = "funcionario"
CHAVE_AVISO_LOGIN = "aviso_login"
CHAVE_RESULTADO = "ultimo_resultado"
CHAVE_PAYLOAD = "ultimo_payload"
CHAVE_TELEMETRIA = "_telemetria_sessao"
CHAVE_TEMA = "tema"
# Ciência da Política de privacidade e dos Termos de uso (ADR-010): a caixa
# marcada e, no clique em "Continuar", a versão dos documentos, que é a que vai
# para o agendamento.
CHAVE_CIENCIA = "ciencia_documentos"
CHAVE_CIENCIA_CONFIRMADA = "ciencia_confirmada"
CHAVE_AGENDAMENTO_CONFIRMADO = "agendamento_confirmado"

# Tudo que pertence a quem está logado sai junto com a sessão: sem isso, a
# última predição (com a explicação dela) ficaria em "Explicabilidade" para o
# próximo que entrasse na mesma aba.
CHAVES_DA_SESSAO_DO_FUNCIONARIO = (CHAVE_FUNCIONARIO, CHAVE_RESULTADO, CHAVE_PAYLOAD)


def configurar_storage() -> None:
    """Aba parada some da memória no mesmo prazo da inatividade da sessão."""
    app.storage.max_tab_storage_age = logic.INATIVIDADE_MAXIMA.total_seconds()


def aba() -> ObservableDict:
    """O `app.storage.tab` da aba atual.

    A poda do NiceGUI apaga a entrada de uma aba parada há mais de
    `max_tab_storage_age`, mesmo com a página ainda aberta. O acesso seguinte
    levantaria `AssertionError` dentro do NiceGUI e derrubaria o handler: o
    paciente que deixou o formulário aberto meia hora perderia o clique em
    "Agendar" para um traceback. A entrada é recriada vazia, que é o estado
    certo depois da poda (sem sessão, sem ciência)."""
    cliente = context.client
    if cliente.has_socket_connection and cliente.tab_id is not None:
        # Atributo interno do NiceGUI (versão fixada em ui.txt); o teste da
        # poda em tests/web/test_infra_web.py quebra se ele mudar.
        abas = app.storage._tabs
        if cliente.tab_id not in abas:
            abas[cliente.tab_id] = ObservableDict()
    return app.storage.tab


def id_de_telemetria() -> str:
    return aba().setdefault(CHAVE_TELEMETRIA, uuid.uuid4().hex)


def mensagem_de_inatividade() -> str:
    minutos = int(logic.INATIVIDADE_MAXIMA.total_seconds() // 60)
    return f"Sessão encerrada após {minutos} minutos sem uso. Entre novamente."


def encerrar(aviso: str | None = None) -> None:
    estado = aba()
    for chave in CHAVES_DA_SESSAO_DO_FUNCIONARIO:
        estado.pop(chave, None)
    if aviso:
        estado[CHAVE_AVISO_LOGIN] = aviso


def entrar(sessao_funcionario: logic.SessaoFuncionario) -> None:
    estado = aba()
    estado.pop(CHAVE_AVISO_LOGIN, None)
    estado[CHAVE_FUNCIONARIO] = sessao_funcionario


def funcionario_logado(agora: datetime | None = None) -> logic.SessaoFuncionario | None:
    """Sessão do funcionário com o relógio de inatividade zerado, ou `None`.

    Chamada a cada ação de quem está na tela: é a ação que prova uso. Sessão
    parada há mais de `INATIVIDADE_MAXIMA` é encerrada aqui, com o aviso que a
    tela de login mostra."""
    estado = aba()
    atual = estado.get(CHAVE_FUNCIONARIO)
    if atual is None:
        return None
    if not logic.sessao_ativa(atual, agora):
        encerrar(mensagem_de_inatividade())
        return None
    renovada = logic.registrar_uso(atual, agora)
    estado[CHAVE_FUNCIONARIO] = renovada
    return renovada


def sessao_expirou(agora: datetime | None = None) -> bool:
    """Para o timer da tela: confere sem renovar. Um timer não é uso, e
    renovar a sessão nele a manteria viva para sempre com a tela aberta."""
    atual = aba().get(CHAVE_FUNCIONARIO)
    return atual is not None and not logic.sessao_ativa(atual, agora)


def tirar_aviso_de_login() -> str | None:
    return aba().pop(CHAVE_AVISO_LOGIN, None)
