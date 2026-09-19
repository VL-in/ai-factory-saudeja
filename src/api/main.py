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
import hashlib
import sys
from contextlib import asynccontextmanager
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi import FastAPI, HTTPException  # noqa: E402

import inference  # noqa: E402
from config_projeto import caminho_de_env  # noqa: E402
from explain import ExplicadorLLMDesativado, construir_explicador, explicar  # noqa: E402

from .schemas import PacienteConsultaIn, PredictOut  # noqa: E402

MODEL_PATH = caminho_de_env("MODEL_PATH", "data/model.pkl")

explicador_llm = ExplicadorLLMDesativado()


def _calcular_model_version(path: str) -> str:
    """Versão determinística e barata do modelo carregado: hash do próprio
    artefato. A fonte de verdade formal do "campeão" em produção fica para
    data/champion_metrics.json (Passo 9) -- aqui só precisamos de algo
    estável para detectar troca de modelo em /health e nas respostas.

    sha256 em vez de md5: o uso aqui não é criptográfico, mas hashlib.md5
    levanta ValueError em host com OpenSSL em modo FIPS, o que derrubaria
    o startup da API por um detalhe sem relação com o modelo."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


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
    app.state.model_version = _calcular_model_version(MODEL_PATH)
    yield


app = FastAPI(title="SaudeJá — API de predição de no-show", lifespan=lifespan)


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
