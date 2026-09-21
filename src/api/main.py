"""
SaúdeJá — API FastAPI de predição de no-show (Passo 3 do plano de
implementação). Expõe /predict e /health sobre o módulo de inferência
(Passo 1) + explicabilidade (Passo 2), sem duplicar lógica.

Bootstrap de sys.path abaixo: os módulos de src/ (inference.py, explain.py)
são importados "soltos" (sem prefixo de pacote), o mesmo padrão que
tests/conftest.py já usa e que train.py/validate.py obtêm de graça quando
rodados como `python src/<script>.py` (o interpretador coloca o diretório do
script em sys.path[0]). Este arquivo vive em src/api/, um nível mais fundo,
então precisa fazer esse ajuste explicitamente para funcionar independente
de como for iniciado (uvicorn a partir da raiz do repo, TestClient nos
testes, ou o CMD do container em infra/api/dockerfile).
"""
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402

import inference  # noqa: E402
import observabilidade  # noqa: E402
from config_projeto import caminho_de_env  # noqa: E402
from explain import ExplicadorLLMDesativado, construir_explicador, explicar  # noqa: E402

from .schemas import PacienteConsultaIn, PredictOut  # noqa: E402

MODEL_PATH = caminho_de_env("MODEL_PATH", "data/model.pkl")

# Só as rotas cujo tempo de resposta o SLO §2 compromete. `/health` fica de
# fora de propósito: a sonda externa do ADR-006 bate nela de minutos em minutos
# para medir uptime, e registrar cada batida encheria `eventos_app` com milhares
# de linhas por mês que não dizem nada sobre a latência de uma predição -- ruído
# ocupando o teto do free tier. O uptime é medido por quem sonda, de fora, que é
# o princípio do ADR-006 ("o coletor não pode morar dentro daquilo que mede").
ROTAS_OBSERVADAS = frozenset({"/predict"})

explicador_llm = ExplicadorLLMDesativado()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Carregado uma vez no startup, não por request -- crítico para o SLO
    # de latência p95<2s (recarregar o LightGBM/SHAP explainer a cada
    # chamada custaria muito mais que a própria predição).
    model, mapa_especialidade = inference.carregar_modelo(MODEL_PATH)
    app.state.model = model
    app.state.mapa_especialidade = mapa_especialidade
    app.state.explainer = construir_explicador(model)
    app.state.threshold = inference.PARAMS["decision"]["threshold"]
    app.state.model_version = inference.calcular_model_version(MODEL_PATH)
    yield


app = FastAPI(title="SaudeJá — API de predição de no-show", lifespan=lifespan)


@app.middleware("http")
async def registrar_latencia(request: Request, call_next):
    """Instrumentação do SLO §2 (Passo 8.5, ADR-006): mede o tempo de resposta
    de `/predict` e registra em `eventos_app`.

    Mede aqui, no middleware, e não dentro da rota, porque o p95 que o SLA
    promete é o da resposta inteira (validação do Pydantic e serialização
    incluídas), não só o do trecho que faz a predição. O registro sai por
    `observabilidade.registrar_evento`, que só enfileira -- a gravação acontece
    fora da requisição, para não entrar na latência que ela existe para medir.

    Um 4xx (payload recusado, ex. especialidade fora do mapa) conta como `erro`
    de requisição mas **não** como indisponibilidade: o SLO §1 fala de 5xx, e a
    coluna `status_http` do detalhe preserva a distinção para quem consultar.
    """
    if request.url.path not in ROTAS_OBSERVADAS:
        return await call_next(request)

    inicio = time.perf_counter()
    try:
        resposta = await call_next(request)
    except Exception as exc:
        observabilidade.registrar_evento(
            tipo=observabilidade.TIPO_PREDICAO,
            status=observabilidade.STATUS_ERRO,
            origem=observabilidade.ORIGEM_API,
            duracao_ms=round((time.perf_counter() - inicio) * 1000),
            model_version=getattr(app.state, "model_version", None),
            detalhe={"rota": request.url.path, "excecao": exc.__class__.__name__},
        )
        raise

    observabilidade.registrar_evento(
        tipo=observabilidade.TIPO_PREDICAO,
        status=(
            observabilidade.STATUS_OK
            if resposta.status_code < 400
            else observabilidade.STATUS_ERRO
        ),
        origem=observabilidade.ORIGEM_API,
        duracao_ms=round((time.perf_counter() - inicio) * 1000),
        model_version=getattr(app.state, "model_version", None),
        detalhe={"rota": request.url.path, "status_http": resposta.status_code},
    )
    return resposta


@app.get("/health")
def health():
    return {"status": "ok", "model_version": app.state.model_version}


@app.post("/predict", response_model=PredictOut)
def predict(payload: PacienteConsultaIn):
    try:
        X = inference.construir_features(payload.model_dump(), app.state.mapa_especialidade)
    except inference.EspecialidadeDesconhecidaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    probabilidade = float(inference.predizer(app.state.model, X)[0])
    threshold = app.state.threshold
    contribuicoes = explicar(app.state.explainer, X)

    return PredictOut(
        probabilidade=probabilidade,
        classe_prevista=int(probabilidade >= threshold),
        threshold_usado=threshold,
        explicacao=contribuicoes,
        explicacao_texto=explicador_llm.explicar_em_texto(contribuicoes, contexto={}),
        model_version=app.state.model_version,
    )
