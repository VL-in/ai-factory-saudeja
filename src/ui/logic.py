"""
SaúdeJá — lógica da interface Streamlit (Passo 4 do plano de implementação),
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

import inference  # noqa: E402
from config_projeto import caminho_de_env  # noqa: E402
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
