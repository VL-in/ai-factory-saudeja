"""
SaúdeJá — lógica da interface Streamlit,
separada de app.py para ser testável sem o runtime do Streamlit: nada aqui
importa `streamlit`.

Dois backends de predição atrás de uma interface única (ClientePredicao),
escolhidos por PREDICT_BACKEND -- reconcilia duas decisões que o repositório
tinha em conflito:

- ADR-005 (b): "Streamlit e o job chamam o modelo em processo; a API FastAPI
  é para integrações externas" -- daí ClientePredicaoEmProcesso ser o DEFAULT:
  em produção (Passo 11, Streamlit e API no mesmo container do HF Space) a UI
  não depende de a API estar de pé nem paga round-trip HTTP interno.
- Passo 4 / architecture.md §3.1: a UI como harness de teste visual da API do
  Passo 3 -- daí ClientePredicaoAPI (PREDICT_BACKEND=api), que exercita
  POST /predict de verdade, do formulário até a resposta.

Os dois caminhos convergem para o MESMO src/inference.py (a API também o usa),
então não há lógica de predição duplicada -- só a forma de invocá-la. O teste
de paridade em tests/test_ui_logic.py trava isso: mesmo payload, mesma
probabilidade nos dois backends.
"""
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import httpx  # noqa: E402

import db.repositories as repositories  # noqa: E402
import inference  # noqa: E402
from config_projeto import caminho_de_env, fuso_da_clinica, hoje_na_clinica  # noqa: E402
from db.client import ConfiguracaoSupabaseAusente  # noqa: E402
from explain import ExplicadorLLMDesativado, construir_explicador, explicar  # noqa: E402

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


def listar_especialidades(model_path: str | None = None) -> list:
    """Especialidades do mapa fixado no treino, para o formulário oferecer um
    selectbox em vez de texto livre (uma especialidade fora do mapa só poderia
    virar erro 422, ver inference.EspecialidadeDesconhecidaError).

    Lê o artefato local nos dois backends de propósito: a API não expõe um
    endpoint de vocabulário, e a UI roda na mesma imagem que o modelo (Passo
    11). Se o artefato não estiver disponível, devolve [] e app.py cai para
    entrada de texto livre -- a UI não deve morrer por causa do formulário."""
    try:
        _, mapa_especialidade = inference.carregar_modelo(
            model_path or caminho_de_env("MODEL_PATH", "data/model.pkl")
        )
    except Exception:  # artefato ausente/corrompido não pode derrubar a UI
        return []
    return sorted(mapa_especialidade)


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
            explicacao_texto=self._explicador_llm.explicar_em_texto(contribuicoes, contexto={}),
        )


class ClientePredicaoAPI:
    """Backend REST (PREDICT_BACKEND=api): fala com a API do Passo 3 por HTTP.
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

    @property
    def tem_predicao(self) -> bool:
        return self.probabilidade is not None


def _relatar_falha_persistencia(exc: Exception, acao: str):
    if isinstance(exc, ConfiguracaoSupabaseAusente):
        raise ErroPersistencia(str(exc)) from exc
    raise ErroPersistencia(f"{acao}: {exc}") from exc


def buscar_fila_do_dia(dia: date | None = None) -> list[ItemFila]:
    """Fila do dia ordenada por risco (probabilidade desc, ver
    repositories.buscar_fila_do_dia) -- Passo 5, liga a aba antes placeholder
    de app.py."""
    try:
        linhas = repositories.buscar_fila_do_dia(dia or hoje_na_clinica())
    except Exception as exc:
        _relatar_falha_persistencia(exc, "Falha ao consultar a fila do dia")

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
                data_hora_agendada=datetime.fromisoformat(
                    linha["data_hora_agendada"]
                ).astimezone(fuso_da_clinica()),
                probabilidade=float(ultima["probabilidade"]) if ultima else None,
                classe_prevista=ultima["classe_prevista"] if ultima else None,
                explicacao=(ultima.get("explicacao_shap") or []) if ultima else [],
                explicacao_texto=ultima.get("explicacao_texto") if ultima else None,
                model_version=ultima.get("model_version") if ultima else None,
            )
        )
    return itens


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


def cadastrar_paciente_e_agendamento(
    id_paciente_externo: str,
    idade: int,
    sexo: str,
    especialidade: str,
    distancia_km: float,
    historico_noshow: int,
    data_consulta: date,
    hora_consulta: time,
) -> dict:
    """Cadastro do paciente (visão Paciente, Passo 5) -- upsert do paciente
    por id_paciente_externo seguido do agendamento.

    `data_hora_agendada` vai com o fuso da clínica explícito: a coluna é
    `timestamptz`, então gravar um datetime naive deixaria o Postgres
    interpretá-lo no fuso do servidor (UTC no container) e a consulta
    apareceria 3h deslocada na fila do dia."""
    try:
        paciente = repositories.inserir_paciente(
            id_paciente_externo=id_paciente_externo, idade=idade, sexo=sexo
        )
        return repositories.inserir_agendamento(
            id_paciente=paciente["id"],
            especialidade=especialidade,
            distancia_km=distancia_km,
            data_hora_agendada=datetime.combine(
                data_consulta, hora_consulta, tzinfo=fuso_da_clinica()
            ),
            dias_entre_agendamento_consulta=dias_ate_consulta(data_consulta),
            historico_noshow=historico_noshow,
        )
    except Exception as exc:
        _relatar_falha_persistencia(exc, "Falha ao cadastrar paciente/agendamento")


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
