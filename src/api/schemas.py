"""
SaúdeJá — schemas Pydantic da API de predição. PacienteConsultaIn espelha
exatamente o payload cru que inference.construir_features() espera. PredictOut já reserva
explicacao_texto: str | None (plug do LLM, ver src/explain.py) para não exigir
migração de schema quando ele for ativado -- fica None até lá.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PacienteConsultaIn(BaseModel):
    """Forma e tipo do payload. A regra de negócio (idade até 120, grade da
    clínica, prazo de agendamento, distância até 500 km) não é repetida aqui:
    a rota aplica o mesmo `contrato_features` do job D-2, e copiar os limites
    em `Field` criaria uma segunda fonte que divergiria na primeira mudança."""

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
    # Contrato de features: predito, mas sobre campo que o modelo não viu no
    # treino (a probabilidade é extrapolação). Mesmo critério de
    # `predicoes.fora_do_dominio` no job D-2.
    fora_do_dominio: bool = False
    campos_fora_do_dominio: list[str] = Field(default_factory=list)
