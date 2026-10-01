"""
SaúdeJá — suíte de sanidade e casos limítrofes do modelo (Passo 10.4).

O gate do Passo 9.1 compara três métricas agregadas no fold de teste. Isso
deixa passar modelos que ninguém quer em produção: um que marca a fila inteira
(com 21 positivos em 76 linhas, marcar todos dá `recall_1` 1,0 e `f1_1` 0,433,
acima do campeão), um que devolve NaN para uma especialidade, um cujo SHAP não
fecha com a probabilidade que a fila mostra. Esta suíte olha para o
**comportamento** do artefato, não para a média dele.

Roda em dois lugares, sobre o mesmo código:

- pelo `src/retrain_gate.py`, sobre o **desafiante**, antes de reescrever o
  campeão -- falha aqui **bloqueia** a promoção;
- pelo CI (`tests/test_sanidade_modelo.py`), sobre o `model.pkl` de `main`.

Não depende do fold de teste (`data/interim/test.pkl`, que o CI não baixa): as
entradas são uma grade determinística montada a partir do próprio contrato de
features e do mapa de especialidades do modelo.

| Checagem | Falha ou aviso |
|---|---|
| assinatura (features, ordem, categóricas) | falha |
| especialidades do cadastro contidas no mapa do modelo | falha |
| saída em [0, 1], sem NaN | falha |
| determinística (mesma entrada, mesma saída) | falha |
| não constante | falha |
| aditividade do SHAP (soma + valor esperado = margem) | falha |
| política do contrato nos casos limítrofes | falha |
| mais `historico_noshow` não reduz o risco médio | aviso |
| domínio do contrato igual ao que o modelo viu | aviso |
"""
import itertools
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

import contrato_features
import inference
from config_projeto import carregar_params, fuso_da_clinica
from explain import _valores_classe_positiva, construir_explicador

TOLERANCIA_ADITIVIDADE = 1e-6
LINHAS_PARA_SHAP = 40

# Horários da grade (seg 08:00, qua 14:30, sex 18:00, sáb 11:30) -- cobre as
# duas pontas do expediente e o sábado, que é só de manhã.
HORARIOS_DA_GRADE = (
    datetime(2026, 10, 5, 8, 0),
    datetime(2026, 10, 7, 14, 30),
    datetime(2026, 10, 9, 18, 0),
    datetime(2026, 10, 10, 11, 30),
)


@dataclass
class ResultadoSanidade:
    falhas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def aprovado(self) -> bool:
        return not self.falhas


def _payload_base(especialidade: str) -> dict[str, Any]:
    return {
        "idade": 45,
        "sexo": "F",
        "especialidade": especialidade,
        "distancia_km": 5.5,
        "dias_entre_agendamento_consulta": 14,
        "historico_noshow": 1,
        "data_hora_agendada": HORARIOS_DA_GRADE[2].replace(tzinfo=fuso_da_clinica()),
    }


def grade_de_casos(especialidades: list[str]) -> list[dict[str, Any]]:
    """Produto cartesiano pequeno sobre o domínio do treino -- determinístico,
    para a suíte dar o mesmo veredito em qualquer máquina."""
    fuso = fuso_da_clinica()
    return [
        {
            "idade": idade,
            "sexo": sexo,
            "especialidade": especialidade,
            "distancia_km": distancia,
            "dias_entre_agendamento_consulta": dias,
            "historico_noshow": historico,
            "data_hora_agendada": quando.replace(tzinfo=fuso),
        }
        for idade, sexo, especialidade, distancia, dias, historico, quando in itertools.product(
            (5, 35, 70),
            ("F", "M"),
            especialidades,
            (1.0, 15.0, 45.0),
            (1, 30, 90),
            (0, 3),
            HORARIOS_DA_GRADE,
        )
    ]


# Casos limítrofes e o que o contrato manda fazer com cada um: "recusa" (vai
# para a quarentena, sem predição) ou "marca" (prediz, com fora_do_dominio).
CASOS_LIMITROFES: tuple[tuple[str, str, object, str], ...] = (
    ("idade negativa", "idade", -5, "recusa"),
    ("idade 150", "idade", 150, "recusa"),
    ("idade fracionária", "idade", 45.5, "recusa"),
    ("idade NaN", "idade", math.nan, "recusa"),
    ("antecedência acima de 180 dias", "dias_entre_agendamento_consulta", 200, "recusa"),
    ("distância NaN", "distancia_km", math.nan, "recusa"),
    ("sexo fora de F/M", "sexo", "X", "recusa"),
    ("distância de 450 km", "distancia_km", 450.0, "marca"),
    ("antecedência de 120 dias", "dias_entre_agendamento_consulta", 120, "marca"),
    ("idade 100", "idade", 100, "marca"),
)


def _checar_saida(probabilidades: np.ndarray, resultado: ResultadoSanidade) -> None:
    if np.isnan(probabilidades).any():
        resultado.falhas.append("o modelo devolveu NaN para entradas válidas")
    elif probabilidades.min() < 0 or probabilidades.max() > 1:
        resultado.falhas.append("probabilidade fora de [0, 1]")
    elif float(np.std(probabilidades)) < 1e-9:
        resultado.falhas.append(
            f"o modelo é constante: devolve {probabilidades[0]:.4f} para toda a grade de casos"
        )


def _checar_aditividade(model: Any, X: Any, resultado: ResultadoSanidade) -> None:
    """soma(SHAP) + valor esperado tem de dar a margem (log-odds) que o modelo
    prevê -- senão a explicação na fila não é a da probabilidade mostrada."""
    amostra = X.iloc[:LINHAS_PARA_SHAP]
    valores, esperado = _valores_classe_positiva(construir_explicador(model), amostra)
    margem = model.predict(amostra, raw_score=True)
    erro = float(np.max(np.abs(np.asarray(valores).sum(axis=1) + float(esperado) - margem)))
    if erro > TOLERANCIA_ADITIVIDADE:
        resultado.falhas.append(
            f"SHAP não é aditivo: diferença máxima de {erro:.2e} entre soma das "
            "contribuições + valor esperado e a margem prevista"
        )


def _checar_casos_limitrofes(
    model: Any, mapa: dict[str, int], resultado: ResultadoSanidade
) -> None:
    especialidade = sorted(mapa)[0]
    for descricao, campo, valor, esperado in CASOS_LIMITROFES:
        payload = {**_payload_base(especialidade), campo: valor}
        try:
            linha = contrato_features.validar_payload(payload, mapa)
        except contrato_features.ViolacaoDoContrato:
            if esperado != "recusa":
                resultado.falhas.append(f"contrato recusou '{descricao}', que devia só marcar")
            continue
        if esperado == "recusa":
            resultado.falhas.append(f"contrato aceitou '{descricao}', que devia recusar")
            continue
        if not linha.fora_do_dominio:
            resultado.falhas.append(f"'{descricao}' passou sem a marca fora_do_dominio")
        X = inference.construir_features(linha.valores, mapa)
        probabilidade = float(inference.predizer(model, X)[0])
        if not 0.0 <= probabilidade <= 1.0:
            resultado.falhas.append(f"'{descricao}' produziu probabilidade inválida")


def _avisar_direcao_do_historico(
    model: Any, mapa: dict[str, int], casos: list[dict[str, Any]], resultado: ResultadoSanidade
) -> None:
    """Expectativa de negócio, não lei: quem faltou mais não deveria ter risco
    médio menor. Só aviso -- com um dataset sintético de 380 linhas, uma
    inversão pode ser ruído, e bloquear por ela seria impor a intuição sobre o
    dado."""
    base = [c for c in casos if c["historico_noshow"] == 0]
    medias = []
    for historico in (0, 5):
        X = inference.construir_features_lote(
            [{**c, "historico_noshow": historico} for c in base], mapa
        )
        medias.append(float(inference.predizer(model, X).mean()))
    if medias[1] < medias[0]:
        resultado.avisos.append(
            f"risco médio cai com o histórico de faltas ({medias[0]:.3f} com 0 faltas, "
            f"{medias[1]:.3f} com 5) -- contraria a expectativa de negócio"
        )


def _avisar_dominio(model: Any, resultado: ResultadoSanidade) -> None:
    """O domínio do contrato (o que marca `fora_do_dominio`) tem de ser o que
    o modelo de fato viu. Um re-treino com dado novo (distância 60 km) muda o
    que o modelo viu sem mudar o contrato -- a marca passaria a mentir."""
    infos = model.booster_.dump_model().get("feature_infos", {})
    for campo in contrato_features.CAMPOS_NUMERICOS:
        info = infos.get(campo.nome)
        if not info or campo.dominio.maximo is None:
            continue
        if float(info["max_value"]) != float(campo.dominio.maximo):
            resultado.avisos.append(
                f"{campo.nome}: o modelo viu até {info['max_value']:g}, o contrato marca "
                f"fora do domínio acima de {campo.dominio.maximo:g} -- atualizar "
                "src/contrato_features.py"
            )


def verificar_modelo(model_path: str, params: dict[str, Any] | None = None) -> ResultadoSanidade:
    params = params or carregar_params()
    resultado = ResultadoSanidade()
    try:
        model, mapa = inference.carregar_modelo(model_path)
    except inference.ModeloIncompativelError as exc:
        resultado.falhas.append(f"assinatura do modelo: {exc}")
        return resultado

    faltando = sorted(set(params["cadastro"]["especialidades"]) - set(mapa))
    if faltando:
        resultado.falhas.append(
            f"o modelo não conhece especialidades que o cadastro oferece: {faltando} -- "
            "todo paciente delas iria para a quarentena"
        )

    casos = grade_de_casos(sorted(mapa))
    X = inference.construir_features_lote(casos, mapa)
    probabilidades = inference.predizer(model, X)
    _checar_saida(probabilidades, resultado)
    if not np.array_equal(probabilidades, inference.predizer(model, X), equal_nan=True):
        resultado.falhas.append("o modelo não é determinístico: mesma entrada, saídas diferentes")
    _checar_aditividade(model, X, resultado)
    _checar_casos_limitrofes(model, mapa, resultado)
    _avisar_direcao_do_historico(model, mapa, casos, resultado)
    _avisar_dominio(model, resultado)
    return resultado
