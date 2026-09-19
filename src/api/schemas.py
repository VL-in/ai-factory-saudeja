"""
SaúdeJá — schemas Pydantic da API de predição (Passo 3 do plano de
implementação). PacienteConsultaIn espelha exatamente o payload cru que
inference.construir_features() espera. PredictOut já reserva
explicacao_texto: str | None (plug do LLM, Passo 2/13) para não exigir
migração de schema quando ele for ativado -- fica None até lá.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PacienteConsultaIn(BaseModel):
    idade: int = Field(ge=0)
    sexo: Literal["F", "M"]
    especialidade: str
    distancia_km: float = Field(ge=0)
    dias_entre_agendamento_consulta: int = Field(ge=0)
    historico_noshow: int = Field(ge=0)
    data_hora_agendada: datetime


class Contribuicao(BaseModel):
    feature: str
    contribuicao: float


class PredictOut(BaseModel):
    probabilidade: float
    classe_prevista: int
    threshold_usado: float
    explicacao: list[Contribuicao]
    explicacao_texto: str | None = None
    model_version: str
