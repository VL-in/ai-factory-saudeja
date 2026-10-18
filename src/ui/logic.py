"""
SaúdeJá — lógica da interface Streamlit,
separada de app.py para ser testável sem o runtime do Streamlit: nada aqui
importa `streamlit`.

Dois backends de predição atrás de uma interface única (ClientePredicao),
escolhidos por PREDICT_BACKEND -- reconcilia duas decisões que o repositório
tinha em conflito:

- ADR-005 (b): "Streamlit e o job chamam o modelo em processo; a API FastAPI
  é para integrações externas" -- daí ClientePredicaoEmProcesso ser o DEFAULT:
  em produção (Streamlit e API no mesmo container do HF Space) a UI
  não depende de a API estar de pé nem paga round-trip HTTP interno.
- architecture.md §3.1: a UI como harness de teste visual da API FastAPI --
  daí ClientePredicaoAPI (PREDICT_BACKEND=api), que exercita
  POST /predict de verdade, do formulário até a resposta.

Os dois caminhos convergem para o MESMO src/inference.py (a API também o usa),
então não há lógica de predição duplicada -- só a forma de invocá-la. O teste
de paridade em tests/test_ui_logic.py trava isso: mesmo payload, mesma
probabilidade nos dois backends.
"""
import contextlib
import logging
import os
import re
import sys
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import NoReturn

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import httpx  # noqa: E402
from supabase_auth.errors import AuthApiError  # noqa: E402

import agenda_clinica  # noqa: E402
import consentimento  # noqa: E402
import db.client as db_client  # noqa: E402
import db.repositories as repositories  # noqa: E402
import db.repositorio_treino as repositorio_treino  # noqa: E402
import id_paciente  # noqa: E402
import inference  # noqa: E402
import jobs.inferencia_diaria as job_inferencia_diaria  # noqa: E402
import observabilidade  # noqa: E402
import pseudonimo  # noqa: E402
from config_projeto import (  # noqa: E402
    caminho_de_env,
    fuso_da_clinica,
    hoje_na_clinica,
    ler_timestamptz,
)
from db.client import ConfiguracaoSupabaseAusente  # noqa: E402
from explain import ExplicadorLLMDesativado, construir_explicador, explicar  # noqa: E402
from features import calcular_idade  # noqa: E402

logger = logging.getLogger(__name__)

BACKEND_PADRAO = "processo"  # ADR-005 (b)
API_BASE_URL_PADRAO = "http://127.0.0.1:8000"
TIMEOUT_PADRAO = 10.0  # folga sobre o SLO §2 (p95 de /predict < 2s)


class ErroPredicao(Exception):
    """Base das falhas que a UI sabe exibir como mensagem, não como traceback."""


class ErroValidacao(ErroPredicao):
    """Payload recusado (especialidade fora do mapa do treino, campo inválido)
    -- corresponde ao HTTP 422 da API. Culpa do dado, não do sistema."""


class ErroIndisponivel(ErroPredicao):
    """Backend de predição fora do ar / inesperado (API não responde, modelo
    ausente). Culpa do sistema, não do dado."""


class ErroValidacaoCadastro(Exception):
    """Dado do formulário de cadastro recusado antes de chegar ao
    banco -- CPF com dígito verificador inválido, horário fora da grade de
    funcionamento da clínica (agenda_clinica). Mesma filosofia de
    ErroValidacao para a predição: culpa do preenchimento, não do sistema.

    Carrega TODOS os problemas do formulário (`problemas`), não só o
    primeiro: parar no primeiro obrigava o paciente a reenviar uma vez por
    campo errado para descobrir o seguinte."""

    def __init__(self, problemas: list[str] | str):
        self.problemas = [problemas] if isinstance(problemas, str) else list(problemas)
        super().__init__("\n".join(self.problemas))


@dataclass(frozen=True)
class ResultadoPredicao:
    """Mesma forma do PredictOut da API (src/api/schemas.py), para os dois
    backends devolverem uma estrutura só e app.py não saber qual usou."""

    probabilidade: float
    classe_prevista: int
    threshold_usado: float
    model_version: str
    explicacao: list = field(default_factory=list)
    explicacao_texto: str | None = None


def montar_payload(
    idade: int,
    sexo: str,
    especialidade: str,
    distancia_km: float,
    dias_entre_agendamento_consulta: int,
    historico_noshow: int,
    data_consulta: date,
    hora_consulta: time,
) -> dict:
    """Junta os campos do formulário no payload cru que construir_features()
    /PacienteConsultaIn esperam. O Streamlit coleta data e hora em widgets
    separados (st.date_input/st.time_input); o modelo precisa do timestamp
    único, de onde saem dia_de_semana e horario (src/features.py)."""
    return {
        "idade": int(idade),
        "sexo": sexo,
        "especialidade": especialidade,
        "distancia_km": float(distancia_km),
        "dias_entre_agendamento_consulta": int(dias_entre_agendamento_consulta),
        "historico_noshow": int(historico_noshow),
        "data_hora_agendada": datetime.combine(data_consulta, hora_consulta),
    }


def listar_especialidades() -> list[str]:
    """Especialidades que o cadastro e o formulário de teste oferecem
    (selectbox em vez de texto livre).

    Vêm de `params.yaml` (`cadastro.especialidades`), não mais do
    mapa do modelo: o que a clínica atende é configuração, e não efeito
    colateral do último treino. O teste de coerência trava que a lista está
    contida no mapa do modelo em produção -- uma especialidade que o modelo não
    conhece seria quarentena garantida no job D-2."""
    return sorted(inference.PARAMS.get("cadastro", {}).get("especialidades", []))


class ClientePredicaoEmProcesso:
    """Backend default (ADR-005 b): importa src/inference.py e src/explain.py
    direto, sem HTTP. Modelo e TreeExplainer carregados na primeira predição e
    reusados depois -- app.py ainda embrulha este objeto em st.cache_resource
    para que o carregamento não se repita a cada rerun do Streamlit."""

    nome = "processo"

    def __init__(self, model_path: str | None = None, explicador_llm=None):
        self._model_path = model_path or caminho_de_env("MODEL_PATH", "data/model.pkl")
        self._explicador_llm = explicador_llm or ExplicadorLLMDesativado()
        self._carregado = False

    def _carregar(self):
        if self._carregado:
            return
        try:
            self._model, self._mapa_especialidade = inference.carregar_modelo(self._model_path)
            self._explainer = construir_explicador(self._model)
            self._model_version = inference.calcular_model_version(self._model_path)
        except FileNotFoundError as exc:
            raise ErroIndisponivel(
                f"modelo não encontrado em {self._model_path} -- rode `dvc pull` ou `dvc repro`"
            ) from exc
        self._carregado = True

    def saude(self) -> dict:
        self._carregar()
        return {"status": "ok", "model_version": self._model_version, "backend": self.nome}

    def predizer(self, payload: dict) -> ResultadoPredicao:
        self._carregar()
        # Instrumentado (ADR-006) tanto quanto a rota /predict da API: o
        # ADR-005 (b) faz deste o caminho DEFAULT de produção -- medir só o
        # middleware do FastAPI calcularia o p95 do SLO §2 sobre uma rota que,
        # no Space, quase não recebe tráfego, enquanto a predição que o
        # funcionário de fato espera acontece aqui.
        with observabilidade.medir(
            observabilidade.TIPO_PREDICAO,
            origem=observabilidade.ORIGEM_PROCESSO,
            model_version=self._model_version,
        ):
            try:
                X = inference.construir_features(payload, self._mapa_especialidade)
            except inference.EspecialidadeDesconhecidaError as exc:
                raise ErroValidacao(str(exc)) from exc

            probabilidade = float(inference.predizer(self._model, X)[0])
            threshold = float(inference.PARAMS["decision"]["threshold"])
            contribuicoes = explicar(self._explainer, X)

            return ResultadoPredicao(
                probabilidade=probabilidade,
                classe_prevista=int(probabilidade >= threshold),
                threshold_usado=threshold,
                model_version=self._model_version,
                explicacao=contribuicoes,
                explicacao_texto=self._explicador_llm.explicar_em_texto(
                    contribuicoes, contexto={}
                ),
            )


class ClientePredicaoAPI:
    """Backend REST (PREDICT_BACKEND=api): fala com a API FastAPI por HTTP.
    `cliente_http` é injetável para os testes passarem um TestClient do próprio
    app FastAPI -- serviço real, sem subir servidor nem mockar a resposta
    (mesma filosofia de tests/test_api.py)."""

    nome = "api"

    def __init__(self, base_url=None, cliente_http=None, timeout: float | None = None):
        url = base_url or os.environ.get("API_BASE_URL") or API_BASE_URL_PADRAO
        self._base_url = url.rstrip("/")
        self._http = cliente_http or httpx.Client(
            base_url=self._base_url, timeout=timeout or TIMEOUT_PADRAO
        )

    def saude(self) -> dict:
        corpo = self._requisitar("GET", "/health")
        return {**corpo, "backend": self.nome}

    def predizer(self, payload: dict) -> ResultadoPredicao:
        corpo = self._requisitar("POST", "/predict", json=_serializar(payload))
        return ResultadoPredicao(
            probabilidade=corpo["probabilidade"],
            classe_prevista=corpo["classe_prevista"],
            threshold_usado=corpo["threshold_usado"],
            model_version=corpo["model_version"],
            explicacao=corpo.get("explicacao", []),
            explicacao_texto=corpo.get("explicacao_texto"),
        )

    def _requisitar(self, metodo: str, rota: str, **kwargs) -> dict:
        try:
            resposta = self._http.request(metodo, rota, **kwargs)
        except httpx.HTTPError as exc:
            raise ErroIndisponivel(
                f"API de predição não respondeu em {self._base_url} ({exc.__class__.__name__})"
            ) from exc

        if resposta.status_code == 422:
            raise ErroValidacao(_detalhe_422(resposta))
        if resposta.status_code >= 400:
            raise ErroIndisponivel(
                f"API de predição respondeu HTTP {resposta.status_code} em {rota}"
            )
        return resposta.json()


def _serializar(payload: dict) -> dict:
    """datetime -> ISO 8601 (o payload em processo trafega objetos Python; o
    REST precisa de JSON). Pydantic reconstrói o datetime do outro lado."""
    return {
        chave: valor.isoformat() if isinstance(valor, datetime) else valor
        for chave, valor in payload.items()
    }


def _detalhe_422(resposta) -> str:
    """`detail` vem como string no 422 que a API levanta para especialidade
    desconhecida, e como lista de erros no 422 que o próprio Pydantic gera."""
    try:
        detalhe = resposta.json().get("detail")
    except ValueError:
        return resposta.text
    if isinstance(detalhe, list):
        return "; ".join(_descrever_erro_pydantic(erro) for erro in detalhe)
    return str(detalhe)


def _descrever_erro_pydantic(erro: dict) -> str:
    """"loc" vem como ("body", "sexo"); o "body" não diz nada para quem está
    preenchendo o formulário."""
    campo = ".".join(str(parte) for parte in erro.get("loc", [])[1:])
    return f"{campo}: {erro.get('msg', '')}".strip(": ")


def backend_ativo() -> str:
    return (os.environ.get("PREDICT_BACKEND") or BACKEND_PADRAO).strip().lower()


class ErroPersistencia(Exception):
    """Falha ao ler/gravar no Supabase (indisponível, mal configurado ou
    rejeitou a operação) -- mesma filosofia de ErroPredicao: a UI mostra uma
    mensagem, não o traceback do cliente HTTP interno do supabase-py."""


@dataclass(frozen=True)
class ItemFila:
    """Uma linha da aba "Fila do dia" -- achatada a partir do agendamento +
    paciente + última predição (todos embutidos pela mesma consulta em
    src/db/repositories.py::buscar_fila_do_dia).

    Carrega a explicação junto da probabilidade de propósito: o SLO §4 exige
    que 100% das predições tenham explicação, então a fila precisa conseguir
    mostrar o "porquê" de cada risco sem uma segunda ida ao banco."""

    id_agendamento: str
    id_paciente_externo: str
    especialidade: str
    data_hora_agendada: datetime
    probabilidade: float | None
    classe_prevista: int | None
    explicacao: list = field(default_factory=list)
    explicacao_texto: str | None = None
    model_version: str | None = None
    status: str = "agendado"
    nome_completo: str | None = None
    # Contrato de features: a predição foi feita sobre um agendamento com algum campo
    # que o modelo não viu no treino -- a probabilidade é extrapolação. `None`
    # = predição anterior à checagem, ou ainda sem predição.
    fora_do_dominio: bool | None = None

    @property
    def tem_predicao(self) -> bool:
        return self.probabilidade is not None

    @property
    def rotulo_paciente(self) -> str:
        """O que a coluna "Paciente" da fila mostra (ADR-007). Nome quando
        existe; senão os primeiros caracteres do identificador, rotulados como
        cadastro antigo -- essas linhas foram criadas antes de a coluna existir
        e realmente não têm nome para recuperar. Inventar um placeholder que
        pareça nome seria pior que admitir a lacuna. O prefixo do formato
        (`hmac1:`, ADR-011) sai, porque é igual em todas as linhas."""
        if self.nome_completo:
            return self.nome_completo
        identificador = self.id_paciente_externo.removeprefix(id_paciente.PREFIXO)
        return f"(cadastro sem nome) {identificador[:8]}"


# Códigos do Postgres que significam a MESMA causa operacional: o banco em que
# a aplicação está falando não recebeu as migrations que o repositório já tem.
# 42703 = undefined_column, 42P01 = undefined_table.
# https://www.postgresql.org/docs/current/errcodes-appendix.html
_CODIGOS_DE_SCHEMA_DESATUALIZADO = frozenset({"42703", "42P01"})


def _relatar_falha_persistencia(exc: Exception, acao: str, rota: str) -> NoReturn:
    """Traduz a falha do banco na mensagem que a tela mostra -- e, antes,
    deixa rastro dela: antes da v2.2.0 a falha virava só a mensagem
    amigável, sem log nem evento, e o alerta diário não tinha como vê-la.

    `rota` é o rótulo fixo da operação. Só ele e o **nome da classe** da
    exceção vão para o log e para `eventos_app`: a mensagem de um erro do
    PostgREST pode ecoar o valor de uma coluna."""
    excecao = type(exc).__name__
    logger.warning("falha de persistência na UI", extra={"rota": rota, "excecao": excecao})
    observabilidade.registrar_evento(
        observabilidade.TIPO_ERRO,
        status=observabilidade.STATUS_ERRO,
        detalhe={"rota": rota, "excecao": excecao},
    )
    if isinstance(exc, ConfiguracaoSupabaseAusente):
        raise ErroPersistencia(str(exc)) from exc
    if getattr(exc, "code", None) in _CODIGOS_DE_SCHEMA_DESATUALIZADO:
        # Aconteceu de verdade em 2026-09-28, ao ligar `nome_completo`: a
        # migration estava aplicada no Supabase local (onde os testes de
        # integração rodam) e não no projeto remoto, que é para onde o `.env`
        # aponta. A tela mostrou o dicionário cru do PostgREST -- que diz o que
        # falta, mas não o que fazer. A distinção vale código porque não é um
        # erro de uso nem indisponibilidade: é ambiente fora de sincronia, e a
        # mesma situação volta a cada migration nova, inclusive no deploy do
        # HF Space.
        raise ErroPersistencia(
            f"{acao}: o banco está desatualizado em relação às migrations do "
            f"repositório ({exc}). Aplique com `supabase db push` (projeto remoto) "
            "ou `supabase db reset` (local) -- ver README, seção 'Banco de dados'."
        ) from exc
    raise ErroPersistencia(f"{acao}: {exc}") from exc


def _acao_ui(acao: str, *recusas: type[BaseException]):
    """Mede uma ação da interface de ponta a ponta (latência, volume e
    desfecho), em `eventos_app` e no Grafana. `recusas` são as
    exceções que significam "o sistema funcionou e recusou" (senha errada,
    formulário inválido): viram `recusado`, não `erro`, e não entram na taxa
    de erro que alerta a mantenedora."""
    return observabilidade.medir(
        observabilidade.TIPO_ACAO_UI, detalhe={"acao": acao}, recusas=recusas
    )


@_acao_ui("carregar_fila")
def buscar_fila_do_dia(dia: date | None = None) -> list[ItemFila]:
    """Fila do dia ordenada por risco (probabilidade desc, ver
    repositories.buscar_fila_do_dia)."""
    try:
        linhas = repositories.buscar_fila_do_dia(dia or hoje_na_clinica())
    except Exception as exc:
        _relatar_falha_persistencia(exc, "Falha ao consultar a fila do dia", rota="fila_do_dia")

    itens = []
    for linha in linhas:
        predicoes = linha.get("predicoes") or []
        ultima = max(predicoes, key=lambda p: p["criado_em"], default=None)
        itens.append(
            ItemFila(
                id_agendamento=linha["id"],
                id_paciente_externo=linha["pacientes"]["id_paciente_externo"],
                especialidade=linha["especialidade"],
                # O Postgres devolve timestamptz normalizado em UTC; sem o
                # astimezone, a fila mostraria 11:30 para uma consulta das
                # 08:30 da clínica.
                data_hora_agendada=ler_timestamptz(linha["data_hora_agendada"]).astimezone(
                    fuso_da_clinica()
                ),
                probabilidade=float(ultima["probabilidade"]) if ultima else None,
                classe_prevista=ultima["classe_prevista"] if ultima else None,
                explicacao=(ultima.get("explicacao_shap") or []) if ultima else [],
                explicacao_texto=ultima.get("explicacao_texto") if ultima else None,
                model_version=ultima.get("model_version") if ultima else None,
                status=linha["status"],
                nome_completo=linha["pacientes"].get("nome_completo"),
                fora_do_dominio=ultima.get("fora_do_dominio") if ultima else None,
            )
        )
    return itens


# Espelham db.repositories.STATUSES_DESFECHO -- rótulos que a aba "Fila do
# dia" oferece para a clínica registrar o desfecho real de um agendamento
# Sem isto o re-treino mensal nunca teria dado real
# para aprender: agendamentos.status nunca sairia de 'agendado'.
STATUS_CONCLUIDO = "concluido"
STATUS_NO_SHOW = "no_show"
STATUS_CANCELADO = "cancelado"

# O que a recepção lê no lugar do valor cru de `agendamentos.status`.
ROTULOS_STATUS = {
    "agendado": "Agendado",
    STATUS_CONCLUIDO: "Realizada",
    STATUS_NO_SHOW: "Faltou",
    STATUS_CANCELADO: "Cancelada",
}


def rotulo_status(status: str) -> str:
    """Status desconhecido aparece cru em vez de sumir: um valor novo no
    banco continua visível até ganhar rótulo aqui."""
    return ROTULOS_STATUS.get(status, status)


# Nome de cada feature do modelo (src/preprocess.py) na linguagem da
# recepção. Só o nome: o valor das categóricas na explicação é o código do
# label encoding (sexo=1, dia_de_semana=2), que não diz nada a quem lê.
ROTULOS_FEATURES = {
    "idade": "Idade",
    "sexo": "Sexo",
    "especialidade": "Especialidade",
    "distancia_km": "Distância até a clínica",
    "dias_entre_agendamento_consulta": "Antecedência do agendamento",
    "historico_noshow": "Faltas anteriores",
    "dia_de_semana": "Dia da semana da consulta",
    "horario": "Horário da consulta",
}


def principais_motivos(explicacao: list, quantidade: int = 3) -> list[dict]:
    """Os fatores que mais pesaram numa predição, prontos para a tela:
    `{"rotulo", "aumenta"}`. A explicação de src/explain.py já vem ordenada
    por |contribuição|; contribuição zero não é motivo de nada e fica de fora.
    O número em log-odds não vai para a recepção -- fica nos detalhes
    técnicos."""
    motivos = []
    for contribuicao in explicacao:
        if contribuicao["contribuicao"] == 0:
            continue
        motivos.append(
            {
                "rotulo": ROTULOS_FEATURES.get(contribuicao["feature"], contribuicao["feature"]),
                "aumenta": contribuicao["contribuicao"] > 0,
            }
        )
        if len(motivos) == quantidade:
            break
    return motivos


class ErroDesfechoForaDePrazo(Exception):
    """Desfecho pedido para consulta que ainda não aconteceu."""


def pode_registrar_desfecho(data_hora_agendada: datetime, hoje: date | None = None) -> bool:
    """Desfecho só para consulta de hoje ou anterior:
    no-show marcado numa consulta futura é rótulo que não aconteceu -- entra
    no re-treino e infla o `historico_noshow` dos próximos cadastros. A data é
    a da clínica, não a UTC do banco."""
    return data_hora_agendada.astimezone(fuso_da_clinica()).date() <= (
        hoje or hoje_na_clinica()
    )


@_acao_ui("registrar_desfecho", ErroDesfechoForaDePrazo)
def atualizar_status_agendamento(
    id_agendamento: str, status: str, data_hora_agendada: datetime | None = None
) -> None:
    """Registra o desfecho real de um agendamento, acionado pela aba "Fila
    do dia". A regra do prazo vale nos dois lados: aqui, quando a
    tela informa a data, e no próprio UPDATE do repositório, que filtra por
    data e não depende de quem chama."""
    if data_hora_agendada is not None and not pode_registrar_desfecho(data_hora_agendada):
        raise ErroDesfechoForaDePrazo(
            "O desfecho só pode ser registrado no dia da consulta ou depois."
        )
    try:
        repositories.atualizar_status_agendamento(id_agendamento, status)
    except repositories.DesfechoForaDePrazo as exc:
        raise ErroDesfechoForaDePrazo(
            "O desfecho só pode ser registrado no dia da consulta ou depois."
        ) from exc
    except Exception as exc:
        _relatar_falha_persistencia(
            exc, "Falha ao registrar desfecho do agendamento", rota="desfecho"
        )


def resumo_da_fila(itens: list[ItemFila]) -> dict:
    """Números que o funcionário olha antes da tabela: quantos pacientes,
    quantos o modelo marcou como alto risco (e portanto receberiam lembrete
    pago) e quantos ainda estão sem predição -- estes últimos são o sinal de
    que o job D-2 não rodou para aquela data, e não "risco zero"."""
    com_predicao = [item for item in itens if item.tem_predicao]
    return {
        "total": len(itens),
        "alto_risco": sum(1 for item in com_predicao if item.classe_prevista),
        "sem_predicao": len(itens) - len(com_predicao),
    }


def normalizar_cpf(cpf: str) -> str:
    """Mantém só os dígitos -- o formulário aceita '123.456.789-00' ou só os
    números, mesma tolerância que um sistema de cadastro real teria."""
    return re.sub(r"\D", "", cpf or "")


def cpf_valido(cpf: str) -> bool:
    """Validação do dígito verificador do CPF -- barra digitação errada antes
    de o número virar identificador do paciente. Não consulta a Receita
    Federal, só confere a coerência aritmética do número em si."""
    digitos = normalizar_cpf(cpf)
    if len(digitos) != 11 or digitos == digitos[0] * 11:
        return False

    def _digito_verificador(base: str) -> str:
        pesos = range(len(base) + 1, 1, -1)
        soma = sum(int(d) * peso for d, peso in zip(base, pesos, strict=True))
        resto = (soma * 10) % 11
        return "0" if resto == 10 else str(resto)

    return digitos[9] == _digito_verificador(digitos[:9]) and digitos[10] == _digito_verificador(
        digitos[:10]
    )


def _id_paciente_externo_de_cpf(cpf: str) -> str:
    """O CPF nunca é persistido (minimização de PII por design,
    architecture.md §4.1): o banco guarda o HMAC do hash dele, com a chave
    `PACIENTE_CHAVE_IDENTIFICADOR` (ADR-011, `src/id_paciente.py`). Sem a
    chave, levanta `ChaveIdentificadorAusente`: o cadastro falha em vez de
    voltar ao sha256 sem chave, que se reverte em CPF."""
    return id_paciente.de_cpf(normalizar_cpf(cpf))


def normalizar_telefone(telefone: str) -> str:
    """Mantém só os dígitos e garante o código do país: a Infobip
    espera o telefone em formato internacional sem "+" (ex. "5511987654321").
    O formulário só atende clínicas brasileiras, então um número com DDD
    (10-11 dígitos) sem "55" na frente recebe o prefixo automaticamente --
    mesma tolerância de digitação que `normalizar_cpf` já dá para o CPF."""
    digitos = re.sub(r"\D", "", telefone or "")
    if len(digitos) in (10, 11):
        digitos = "55" + digitos
    return digitos


def telefone_valido(telefone: str) -> bool:
    """Confere só a forma (código do país + DDD + número, 12-13 dígitos) --
    mesmo escopo de `cpf_valido`, sem consultar operadora/Infobip. Barra
    digitação claramente errada antes de o número virar contato de envio
    persistido no banco."""
    digitos = normalizar_telefone(telefone)
    return len(digitos) in (12, 13) and digitos.startswith("55")


def horarios_disponiveis(dia: date) -> list[time]:
    """Slots que a clínica atende em `dia` -- vazio aos domingos. O cadastro
    usa isto para não deixar escolher um horário que o dataset de treino
    nunca viu (scripts/gerar_timestamp_sintetico.py)."""
    return agenda_clinica.horarios_do_dia(dia)


def proxima_data_disponivel(a_partir_de: date | None = None) -> date:
    """Data default do formulário de cadastro -- nunca um domingo sem
    nenhum horário para oferecer."""
    return agenda_clinica.proximo_dia_valido(a_partir_de or hoje_na_clinica())


def data_maxima_de_consulta(hoje: date | None = None) -> date:
    """Último dia que o cadastro oferece (`agenda_clinica.PRAZO_MAXIMO_AGENDAMENTO_DIAS`)."""
    prazo = timedelta(days=agenda_clinica.PRAZO_MAXIMO_AGENDAMENTO_DIAS)
    return (hoje or hoje_na_clinica()) + prazo


def dias_ate_consulta(data_consulta: date, hoje: date | None = None) -> int:
    """`dias_entre_agendamento_consulta` do ponto de vista do cadastro: o
    agendamento está sendo feito AGORA, então o valor é derivado da data
    escolhida em vez de digitado. Pedi-lo ao paciente permitiria gravar um
    número incoerente com a própria data da consulta -- e é uma das features
    que mais pesam no modelo (ver checagem de sanidade do SHAP, CHANGELOG
    v0.15), então incoerência aqui vira predição errada lá."""
    return max((data_consulta - (hoje or hoje_na_clinica())).days, 0)


def status_banco() -> dict:
    """Indicador para a sidebar. Devolve estrutura em vez de levantar: é
    diagnóstico exibido de forma passiva, não uma operação que o usuário
    pediu -- quem chama quer pintar um rótulo, não tratar exceção."""
    try:
        repositories.verificar_conexao()
    except ConfiguracaoSupabaseAusente:
        return {"conectado": False, "detalhe": "não configurado (ver .env.example)"}
    except Exception as exc:
        return {"conectado": False, "detalhe": f"indisponível ({exc.__class__.__name__})"}
    return {"conectado": True, "detalhe": "conectado"}


NOME_MINIMO = 2
NOME_MAXIMO = 120


def nome_valido(nome_completo: str) -> bool:
    """Confere só a forma -- mesmo escopo de `cpf_valido`/`telefone_valido`:
    não existe "validar nome" de verdade (nomes brasileiros têm partícula,
    acento, apóstrofo, hífen e um único termo é possível). O que se barra é
    campo vazio, espaço em branco e texto longo demais para uma coluna de
    nome, que são os casos de preenchimento claramente errado. Limites batem
    com o `check` da migration `20260928000000_nome_paciente.sql`."""
    return NOME_MINIMO <= len((nome_completo or "").strip()) <= NOME_MAXIMO


# Limite inferior do formulário e da validação do cadastro (decisão da autora,
# 2026-10-05). Note que o contrato de features aceita idade só até 120 anos
# (`contrato_features`, faixa de negócio): quem nasceu antes disso passa aqui
# e é recusado no job D-2.
DATA_NASCIMENTO_MINIMA = date(1900, 1, 1)


def data_nascimento_valida(data_nascimento: date | None, hoje: date | None = None) -> bool:
    """Obrigatória e dentro da faixa do formulário. Sem limite mínimo de
    idade: a clínica atende pediatria, então um recém-nascido é paciente
    válido."""
    if data_nascimento is None:
        return False
    return DATA_NASCIMENTO_MINIMA <= data_nascimento <= (hoje or hoje_na_clinica())


def validar_cadastro(
    nome_completo: str | None,
    cpf: str,
    telefone: str,
    data_nascimento: date | None,
    especialidade: str,
    data_consulta: date,
    hora_consulta: time | None,
) -> list[str]:
    """Todos os problemas do formulário, na ordem em que os campos aparecem
    na tela -- lista vazia quando dá para gravar. Só confere a forma: não
    muda valor, formato nem opção que vira feature do modelo.

    `nome_completo=None` continua aceito (quem chama sem nome, ver
    `cadastrar_paciente_e_agendamento`); string vazia é campo em branco."""
    hoje = hoje_na_clinica()
    problemas = []

    if nome_completo is not None and not nome_valido(nome_completo):
        problemas.append(
            f"Nome inválido -- informe entre {NOME_MINIMO} e {NOME_MAXIMO} caracteres."
        )
    if not cpf_valido(cpf):
        problemas.append("CPF inválido -- confira os números digitados.")
    if not telefone_valido(telefone):
        problemas.append("Telefone inválido -- informe DDD + número (ex. (11) 98765-4321).")
    if data_nascimento is None:
        problemas.append("Informe a data de nascimento.")
    elif not data_nascimento_valida(data_nascimento, hoje):
        problemas.append(
            "Data de nascimento inválida -- informe uma data entre "
            f"{DATA_NASCIMENTO_MINIMA:%d/%m/%Y} e hoje."
        )

    # Antes, sem lista de especialidades, o formulário caía em texto livre e
    # gravava qualquer coisa -- o erro só aparecia no job D-2, dias depois,
    # como agendamento em quarentena.
    especialidades = listar_especialidades()
    if not especialidades:
        problemas.append(
            "Agendamento indisponível no momento: a lista de especialidades não "
            "está configurada. Tente mais tarde ou fale com a clínica."
        )
    elif especialidade not in especialidades:
        problemas.append("Especialidade inválida -- escolha uma das opções da lista.")

    if not agenda_clinica.data_de_consulta_valida(data_consulta, hoje):
        problemas.append(
            "Data da consulta inválida -- escolha entre hoje e "
            f"{agenda_clinica.PRAZO_MAXIMO_AGENDAMENTO_DIAS} dias à frente."
        )
    if hora_consulta is None:
        problemas.append("Escolha uma data em que a clínica atenda (não há horários aos domingos).")
    elif not agenda_clinica.horario_valido(data_consulta, hora_consulta):
        problemas.append(
            "Horário fora do funcionamento da clínica para esta data (seg-sex "
            "08h-11h30/13h-18h, sáb 08h-11h30, fechado aos domingos)."
        )

    return problemas


MENSAGEM_SEM_CIENCIA = (
    "Para agendar por esta tela, confirme que está ciente da Política de "
    "privacidade e dos Termos de uso."
)


@_acao_ui("cadastro", ErroValidacaoCadastro)
def cadastrar_paciente_e_agendamento(
    cpf: str,
    telefone: str,
    data_nascimento: date | None,
    sexo: str,
    especialidade: str,
    distancia_km: float,
    data_consulta: date,
    hora_consulta: time | None,
    nome_completo: str | None = None,
    *,
    consentimentos: dict[str, bool],
    versao_documentos: str | None,
) -> dict:
    """Cadastro do paciente (visão Paciente) --
    upsert do paciente seguido do agendamento.

    `nome_completo` passou a ser parâmetro em 2026-09-28 (ADR-007) e **é
    gravado**. Até então a tela usava o valor digitado só para a mensagem de
    confirmação e o descartava: a "Fila do dia" mostrava o hash sha256 de 64
    caracteres na coluna "Paciente", inútil para quem atende no balcão e
    precisa chamar a pessoa pelo nome. Continua opcional na assinatura para
    não quebrar quem chama sem ele, e `None` nunca apaga um nome já gravado
    (ver `repositories.inserir_paciente`).

    **CPF segue não sendo gravado**: vira só o identificador HMAC do paciente
    (`_id_paciente_externo_de_cpf`, ADR-011). `telefone` é a primeira exceção
    deliberada à minimização de PII -- é gravado (normalizado), porque sem
    contato o job D-2 não tem para onde mandar o lembrete real via Infobip.

    `historico_noshow` deixou de ser parâmetro também -- não é algo que o
    paciente tem como (ou deveria) autodeclarar; é contado a partir do
    histórico real de agendamentos dele nesta clínica
    (`repositories.contar_no_shows_anteriores`).

    `data_hora_agendada` vai com o fuso da clínica explícito: a coluna é
    `timestamptz`, então gravar um datetime naive deixaria o Postgres
    interpretá-lo no fuso do servidor (UTC no container) e a consulta
    apareceria 3h deslocada na fila do dia.

    `versao_documentos` é a versão da Política de privacidade e dos Termos de
    uso que o paciente declarou conhecer na caixa de ciência (ADR-010). Sem
    ela não há cadastro: a tela só libera o formulário depois da ciência, e a
    recusa aqui é a segunda barreira. Fica gravada no agendamento como prova
    de que a informação foi apresentada (art. 9º); não é consentimento.

    `consentimentos` é o estado da caixa do treino, por finalidade, e é
    obrigatório: quem chama tem de decidir, não herdar um default. **O
    agendamento não depende dele**: primeiro vem o serviço da clínica
    (paciente e agendamento), depois a etapa de treino
    (`registrar_no_treino`), que nunca derruba o agendamento. Se ela falhar, o
    resultado traz `falha_no_treino=True` e a tela avisa que a autorização não
    foi registrada -- sem prova não há treino, que é o lado seguro."""
    problemas = validar_cadastro(
        nome_completo=nome_completo,
        cpf=cpf,
        telefone=telefone,
        data_nascimento=data_nascimento,
        especialidade=especialidade,
        data_consulta=data_consulta,
        hora_consulta=hora_consulta,
    )
    if not versao_documentos:
        problemas.insert(0, MENSAGEM_SEM_CIENCIA)
    # As duas checagens de None nunca valem sem `problemas` (validar_cadastro
    # já as reporta): estão aqui só para o mypy estreitar o tipo abaixo.
    if problemas or data_nascimento is None or hora_consulta is None:
        raise ErroValidacaoCadastro(problemas)

    try:
        paciente = repositories.inserir_paciente(
            id_paciente_externo=_id_paciente_externo_de_cpf(cpf),
            data_nascimento=data_nascimento,
            sexo=sexo,
            telefone=normalizar_telefone(telefone),
            nome_completo=nome_completo,
        )
        historico_noshow = repositories.contar_no_shows_anteriores(paciente["id"])
        data_hora_agendada = datetime.combine(
            data_consulta, hora_consulta, tzinfo=fuso_da_clinica()
        )
        dias = dias_ate_consulta(data_consulta)
        agendamento = repositories.inserir_agendamento(
            id_paciente=paciente["id"],
            especialidade=especialidade,
            distancia_km=distancia_km,
            data_hora_agendada=data_hora_agendada,
            dias_entre_agendamento_consulta=dias,
            historico_noshow=historico_noshow,
            versao_documentos=versao_documentos,
        )
    except Exception as exc:
        _relatar_falha_persistencia(exc, "Falha ao cadastrar paciente/agendamento", rota="cadastro")

    # Daqui em diante o agendamento está confirmado.
    treino_ok = registrar_no_treino(
        cpf=cpf,
        marcada=bool(consentimentos.get(consentimento.FINALIDADE_TREINO)),
        id_agendamento=agendamento["id"],
        data_nascimento=data_nascimento,
        sexo=sexo,
        especialidade=especialidade,
        distancia_km=distancia_km,
        data_hora_agendada=data_hora_agendada,
        dias_entre_agendamento_consulta=dias,
        historico_noshow=historico_noshow,
    )
    return {**agendamento, "falha_no_treino": not treino_ok}


def registrar_no_treino(
    cpf: str,
    marcada: bool,
    id_agendamento: str,
    data_nascimento: date,
    sexo: str,
    especialidade: str,
    distancia_km: float,
    data_hora_agendada: datetime,
    dias_entre_agendamento_consulta: int,
    historico_noshow: int,
) -> bool:
    """Etapa de treino do agendamento (ADR-010), na base da SaúdeJá. Devolve
    se terminou sem erro; **nunca levanta**, porque o agendamento já está
    confirmado e não pode depender dela.

    A consulta do vigente acontece mesmo com a caixa desmarcada: é preciso
    saber se há consentimento a revogar. O que vai à base de treino nesse caso
    é só o pseudônimo, no corpo da RPC, sem dado da consulta.

    - grava os eventos só quando o estado muda (`decisoes_de_consentimento`);
    - com a caixa marcada, grava a linha da consulta. O banco a recusa se não
      houver consentimento vigente, então a ordem evento -> linha é obrigatória;
    - na revogação, abre o pedido que o workflow de purga executa (apaga as
      linhas e as versões do dataset que as contêm).

    A idade é a da data da consulta, como o export sempre calculou: a data de
    nascimento não vai para a base de treino. `historico_noshow` é o valor
    gravado no agendamento, o mesmo com que o job D-2 prediz."""
    try:
        id_titular = pseudonimo.id_titular(cpf)
        vigentes = consentimento.consentimentos_vigentes(
            repositorio_treino.eventos_do_titular(id_titular)
        )
        decisoes = consentimento.decisoes_de_consentimento(
            vigentes, {consentimento.FINALIDADE_TREINO: marcada}
        )
        repositorio_treino.registrar_consentimentos(
            id_titular,
            decisoes,
            versao_termo=consentimento.VERSAO_TERMO,
            canal=consentimento.CANAL_AUTOAGENDAMENTO,
            consentido_por=consentimento.consentido_por(data_nascimento, hoje_na_clinica()),
        )
        if marcada:
            repositorio_treino.inserir_consulta(
                id_consulta=pseudonimo.id_consulta(id_agendamento),
                id_titular=id_titular,
                idade=calcular_idade(data_nascimento, data_hora_agendada.date()),
                sexo=sexo,
                especialidade=especialidade,
                distancia_km=distancia_km,
                dias_entre_agendamento_consulta=dias_entre_agendamento_consulta,
                historico_noshow=historico_noshow,
                data_hora_agendada=data_hora_agendada,
            )
        if (consentimento.FINALIDADE_TREINO, consentimento.ACAO_REVOGADO) in decisoes:
            repositorio_treino.registrar_pedido(id_titular, "revogacao", "autoagendamento")
    except Exception as exc:
        # Só o nome da classe: a mensagem de um erro do PostgREST pode ecoar
        # o pseudônimo ou o valor de uma coluna.
        logger.warning("etapa de treino do agendamento falhou: %s", type(exc).__name__)
        observabilidade.registrar_evento(
            observabilidade.TIPO_ERRO,
            status=observabilidade.STATUS_ERRO,
            detalhe={"rota": "agendamento_treino", "excecao": type(exc).__name__},
        )
        return False
    return True


# --------------------------------------------------------------------------
# pedidos do titular (ADR-009, ADR-010)
# --------------------------------------------------------------------------
class ErroPedido(Exception):
    """Pedido recusado antes do banco: código ou CPF inválido."""


ROTULOS_TIPO_PEDIDO = {
    "eliminacao": "Eliminação do cadastro",
    "revisao_decisao": "Revisão da decisão de lembrete",
}
ROTULOS_SITUACAO_PEDIDO = {
    "pendente": "Aguardando a clínica",
    "confirmado_pela_clinica": "Confirmado",
}


@_acao_ui("registrar_pedido", ErroPedido)
def registrar_pedido_da_clinica(codigo_agendamento: str, tipo: str) -> None:
    """Pedido sobre os dados da clínica, pelo código do agendamento que a
    "Fila do dia" mostra: a equipe nunca procura o paciente por CPF ou nome
    (iriam na URL da consulta, LGPD.md §8.1). Nasce pendente; a clínica
    confirma em seguida, depois de conferir a identidade de quem pede."""
    codigo = (codigo_agendamento or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", codigo):
        raise ErroPedido("Código do agendamento inválido -- copie-o de \"Detalhes técnicos\".")
    try:
        id_paciente = repositories.paciente_do_agendamento(codigo)
        if id_paciente is None:
            raise ErroPedido("Nenhum agendamento com esse código.")
        repositories.registrar_pedido(id_paciente, tipo)
    except ErroPedido:
        raise
    except Exception as exc:
        _relatar_falha_persistencia(exc, "Falha ao registrar o pedido", rota="registrar_pedido")


@_acao_ui("encaminhar_pedido", ErroPedido)
def encaminhar_pedido_ao_treino(cpf: str) -> None:
    """Pedido de eliminação dos dados de aprimoramento, encaminhado pela
    clínica à SaúdeJá, que é a controladora da base de treino (ADR-010). O
    CPF vira o pseudônimo e não é guardado. A tela não diz se havia dado de
    aprimoramento: diria a quem digitasse um CPF alheio se aquela pessoa
    autorizou o treino."""
    if not cpf_valido(cpf):
        raise ErroPedido("CPF inválido -- confira os números digitados.")
    try:
        repositorio_treino.registrar_pedido(pseudonimo.id_titular(cpf), "eliminacao", "clinica")
    except Exception as exc:
        _relatar_falha_persistencia(
            exc, "Falha ao encaminhar o pedido ao SaúdeJá", rota="encaminhar_pedido"
        )


def data_na_clinica(timestamptz: str) -> date:
    """Data civil, no fuso da clínica, de um `timestamptz` do banco. O
    PostgREST devolve em UTC: cortar a string ("2026-10-02T01:00:00+00:00"[:10])
    mostraria o dia seguinte para o que aconteceu depois das 21h em São Paulo
    -- e é dessa data que a equipe conta o prazo de 15 dias de um pedido."""
    return ler_timestamptz(timestamptz).astimezone(fuso_da_clinica()).date()


@_acao_ui("listar_pedidos")
def listar_pedidos_da_clinica() -> list[dict]:
    try:
        return repositories.pedidos_abertos()
    except Exception as exc:
        _relatar_falha_persistencia(exc, "Falha ao listar os pedidos", rota="listar_pedidos")


@_acao_ui("decidir_pedido")
def decidir_pedido_da_clinica(id_pedido: str, decisao: str) -> bool:
    """`confirmar` (pendente -> confirmado; a eliminação confirmada é
    executada pelo workflow de purga), `recusar` (pendente -> recusado) ou
    `concluir` (revisão de decisão confirmada -> executado, quando a clínica
    respondeu ao paciente). Devolve se o pedido mudou."""
    acoes = {
        "confirmar": repositories.confirmar_pedido,
        "recusar": repositories.recusar_pedido,
        "concluir": repositories.marcar_pedido_executado,
    }
    if decisao not in acoes:
        raise ValueError(f"decisão desconhecida: {decisao!r}")
    try:
        return acoes[decisao](id_pedido)
    except Exception as exc:
        _relatar_falha_persistencia(exc, "Falha ao atualizar o pedido", rota="decidir_pedido")


def disparar_job_diario(dia: date | None = None) -> dict:
    """Aciona o job D-2 (`src/jobs/inferencia_diaria.py`) a partir da
    aba "Dev: disparo manual" -- a mesma função que o cron real chamaria,
    disparada manualmente para acompanhar agendamentos encontrados/predições
    gravadas/mensagens disparadas sem precisar de CLI/cron separados."""
    try:
        return job_inferencia_diaria.processar_dia(dia)
    except Exception as exc:
        _relatar_falha_persistencia(
            exc, "Falha ao rodar o job de inferência diária", rota="job_manual"
        )


def obter_cliente(backend: str | None = None):
    """Fábrica do cliente conforme PREDICT_BACKEND. Backend desconhecido é erro
    explícito, não fallback silencioso: rodar em REST achando que está em
    processo (ou o contrário) muda o modo de falha da aplicação inteira."""
    backend = (backend or backend_ativo()).strip().lower()
    if backend == "processo":
        return ClientePredicaoEmProcesso()
    if backend == "api":
        return ClientePredicaoAPI()
    raise ValueError(
        f"PREDICT_BACKEND inválido: {backend!r} -- use 'processo' (default, ADR-005) ou 'api'"
    )


LIMITE_EVENTOS_OBSERVABILIDADE = observabilidade.LIMITE_LEITURA_SLO


@_acao_ui("observabilidade")
def resumo_observabilidade(janela_horas: int = 24) -> dict:
    """Números do SLO §2/§4/§5 lidos de `eventos_app`/`predicoes` (ADR-006)
    -- é daqui que sai a aba "Observabilidade" e, na validação final para o
    pitch, os valores da coluna "medido" do SLA/SLO.

    `truncado` não é detalhe de implementação: o p95 é calculado em Python
    sobre as linhas lidas (o PostgREST não expõe `percentile_cont`), então uma
    janela que bateu no limite produziria um p95 do pedaço mais recente
    apresentado como se fosse o da janela inteira. Melhor dizer na tela que a
    janela foi cortada do que publicar um número errado no pitch."""
    desde = observabilidade.inicio_da_janela(janela_horas)
    try:
        eventos = repositories.buscar_eventos_app(
            desde, limite=LIMITE_EVENTOS_OBSERVABILIDADE, tipos=observabilidade.TIPOS_DO_SLO
        )
        total_predicoes, com_explicacao = repositories.contar_predicoes_e_explicacoes(desde)
        ultimo_job = repositories.ultimo_evento_app(
            observabilidade.TIPO_JOB_D2, status=observabilidade.STATUS_OK
        )
    except Exception as exc:
        _relatar_falha_persistencia(
            exc, "Falha ao consultar a observabilidade", rota="observabilidade"
        )

    predicoes = [e for e in eventos if e["tipo"] == observabilidade.TIPO_PREDICAO]
    latencias = [e["duracao_ms"] for e in predicoes if e["duracao_ms"] is not None]

    return {
        "janela_horas": janela_horas,
        "predicoes": len(predicoes),
        "p50_ms": observabilidade.percentil(latencias, 50),
        "p95_ms": observabilidade.percentil(latencias, 95),
        "erros": sum(1 for e in eventos if e["status"] == observabilidade.STATUS_ERRO),
        "por_origem": {
            origem: sum(1 for e in predicoes if e["origem"] == origem)
            for origem in sorted({e["origem"] for e in predicoes})
        },
        "cobertura_explicacao": {
            "predicoes": total_predicoes,
            "com_explicacao": com_explicacao,
            # None, não 0%, quando não houve predição na janela: "nenhuma
            # predição" e "nenhuma predição explicada" são coisas diferentes, e
            # o SLO §4 só é violado no segundo caso.
            "percentual": (com_explicacao / total_predicoes) if total_predicoes else None,
        },
        "ultimo_job_d2": (
            ler_timestamptz(ultimo_job["criado_em"]).astimezone(fuso_da_clinica())
            if ultimo_job
            else None
        ),
        "truncado": len(eventos) >= LIMITE_EVENTOS_OBSERVABILIDADE,
    }


# --- Autenticação do funcionário (docs/architecture.md §7) --------------------

# 30 min sem nenhuma interação encerra a sessão. A tela da recepção fica ligada
# o dia todo com nome de paciente (ADR-007); sem limite, quem sentasse no
# balcão depois do turno herdaria a sessão de quem saiu. Mais curto que isso
# obrigaria a equipe a relogar no meio do atendimento.
INATIVIDADE_MAXIMA = timedelta(minutes=30)

MENSAGEM_CREDENCIAIS_INVALIDAS = "E-mail ou senha incorretos."


class ErroAutenticacao(Exception):
    """Base das falhas de login que a UI sabe exibir como mensagem."""


class ErroCredenciais(ErroAutenticacao):
    """Login recusado por causa de quem está tentando entrar (senha errada,
    conta desativada) -- culpa do preenchimento, não do sistema."""


class ErroAutenticacaoIndisponivel(ErroAutenticacao):
    """Supabase Auth fora do ar, mal configurado ou limitando tentativas --
    culpa do sistema, não de quem digitou."""


@dataclass(frozen=True)
class SessaoFuncionario:
    """O que a UI guarda de quem está logado, em `st.session_state` (memória do
    servidor, nunca cookie). Sem o token do Supabase de propósito: ele só
    serviu para provar a senha -- os dados continuam sendo lidos com a chave
    secreta do backend (docs/architecture.md §7) --, então guardá-lo só criaria um segredo a
    mais para vazar."""

    id_usuario: str
    email: str
    ultimo_uso: datetime


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _traduzir_erro_de_auth(exc: AuthApiError) -> ErroAutenticacao:
    """Nenhuma mensagem distingue "e-mail não existe" de "senha errada": dizer
    qual dos dois falhou ensina a quem tenta adivinhar quais e-mails são da
    equipe."""
    if exc.status == 429 or exc.code == "over_request_rate_limit":
        # O limite do Supabase Auth é por IP, e todos os funcionários chegam
        # pelo mesmo IP (o do servidor do Streamlit) -- quem aparece aqui pode
        # nem ter errado a própria senha. Ver docs/LGPD/LGPD.md §9, risco 6.
        return ErroAutenticacaoIndisponivel(
            "muitas tentativas de login em pouco tempo. Aguarde alguns minutos e tente de novo."
        )
    if exc.code == "user_banned":
        return ErroCredenciais("Acesso desativado. Procure o administrador do sistema.")
    if exc.code == "email_not_confirmed":
        return ErroCredenciais(
            "Conta ainda não confirmada. Procure o administrador do sistema."
        )
    if exc.code == "email_provider_disabled":
        # Configuração do projeto, não da conta: vale para qualquer e-mail, então
        # dizê-lo não ajuda a adivinhar quem é da equipe -- e "senha incorreta"
        # mandaria todo mundo redefinir a senha à toa.
        return ErroAutenticacaoIndisponivel(
            "login por e-mail desligado no Supabase. Procure o administrador do sistema."
        )
    if exc.status is not None and exc.status >= 500:
        return ErroAutenticacaoIndisponivel(f"serviço de autenticação respondeu {exc.status}.")
    return ErroCredenciais(MENSAGEM_CREDENCIAIS_INVALIDAS)


@_acao_ui("login", ErroCredenciais)
def autenticar_funcionario(
    email: str, senha: str, criar_client=None, agora: datetime | None = None
) -> SessaoFuncionario:
    """E-mail e senha contra o Supabase Auth do mesmo projeto do banco.

    Funcionário é qualquer usuário existente no Supabase Auth: o cadastro
    aberto fica desligado no projeto e as contas são criadas pelo
    administrador (`scripts/criar_funcionario.py`). Não há RBAC -- toda a
    equipe vê as mesmas abas.

    `criar_client` existe para os testes trocarem o transporte; o default é um
    client descartável, nunca o singleton do backend (ver
    `db.client.criar_client_autenticacao`)."""
    email = (email or "").strip().lower()
    if not email or not senha:
        raise ErroCredenciais("Informe e-mail e senha.")

    try:
        cliente = (criar_client or db_client.criar_client_autenticacao)()
        resposta = cliente.auth.sign_in_with_password({"email": email, "password": senha})
    except ConfiguracaoSupabaseAusente as exc:
        raise ErroAutenticacaoIndisponivel(str(exc)) from exc
    except AuthApiError as exc:
        raise _traduzir_erro_de_auth(exc) from exc
    except Exception as exc:  # rede, timeout, AuthRetryableError
        raise ErroAutenticacaoIndisponivel(
            f"serviço de autenticação inacessível ({exc.__class__.__name__})."
        ) from exc

    usuario = resposta.user
    if usuario is None:
        # O supabase-py levanta quando a senha não confere; resposta sem
        # usuário não é "login recusado", é o serviço respondendo fora do
        # contrato -- tratado como indisponível, nunca como sucesso.
        raise ErroAutenticacaoIndisponivel("resposta do serviço de autenticação sem usuário.")
    # Revoga já o refresh token desta sessão do Supabase: a UI não o usa (ver
    # SessaoFuncionario), e um token válido que ninguém guarda é só superfície.
    # `local` e não o default `global`, que derrubaria as sessões do mesmo
    # funcionário em outros navegadores. Falhar aqui não impede o login: é
    # higiene, não requisito -- o token expira sozinho.
    with contextlib.suppress(Exception):
        cliente.auth.sign_out({"scope": "local"})

    return SessaoFuncionario(
        id_usuario=str(usuario.id),
        email=usuario.email or email,
        ultimo_uso=agora or _agora(),
    )


def sessao_ativa(sessao: SessaoFuncionario, agora: datetime | None = None) -> bool:
    return (agora or _agora()) - sessao.ultimo_uso <= INATIVIDADE_MAXIMA


def registrar_uso(sessao: SessaoFuncionario, agora: datetime | None = None) -> SessaoFuncionario:
    """Nova sessão com o relógio de inatividade zerado -- chamada a cada rerun
    do Streamlit, que só acontece quando alguém interage com a tela."""
    return replace(sessao, ultimo_uso=agora or _agora())
