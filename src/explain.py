"""
SaúdeJá — explicabilidade das predições via SHAP (Passo 2 do plano de
implementação). Cumpre SLO §4 (100% das predições com explicação).

Cálculo SÍNCRONO, dentro do módulo de inferência: volume baixo (fila diária
de uma clínica) e TreeExplainer sobre LightGBM é da ordem de milissegundos,
então cobrir 100% das predições é mais simples por construção do que com um
job assíncrono que pode falhar e deixar predições sem explicação.

O valor numérico do SHAP (contribuição por feature, em log-odds/margem) é a
fonte de verdade auditável e testável por aditividade
(soma(contribuicoes) + expected_value ≈ margem prevista, ver
tests/test_explain.py). Este módulo também define um plug INATIVO para
tradução dessas contribuições em texto via LLM (Passo 13) -- o
ExplicadorLLMDesativado nunca chama rede; ele só existe para
API/job (Passo 3/6) e o schema de `predicoes` (Passo 5) já reservarem o
formato (`explicacao_texto: str | None`) sem exigir migração depois.
"""
from abc import ABC, abstractmethod

import shap


def construir_explicador(model):
    """TreeExplainer sobre o LightGBM já treinado (model_output="raw", i.e.
    em espaço de margem/log-odds -- é o espaço em que a aditividade SHAP
    vale exatamente, ver docstring do módulo)."""
    return shap.TreeExplainer(model)


def _valores_classe_positiva(explainer, X):
    """Normaliza a saída de explainer.shap_values(X): versões/mecanismos
    diferentes do SHAP podem retornar um ndarray (n, features) já para a
    classe positiva (caso observado com LGBMClassifier binário), uma lista
    [neg, pos] de arrays, ou um ndarray 3D (n, features, classes)."""
    shap_values = explainer.shap_values(X)
    expected_value = explainer.expected_value

    if isinstance(shap_values, list):
        valores = shap_values[1]
        if hasattr(expected_value, "__len__"):
            expected_value = expected_value[1]
    elif shap_values.ndim == 3:
        valores = shap_values[:, :, 1]
        if hasattr(expected_value, "__len__"):
            expected_value = expected_value[1]
    else:
        valores = shap_values

    return valores, expected_value


def explicar(explainer, X):
    """Retorna a explicação da única linha de X: lista de
    {"feature", "contribuicao"} ordenada por abs(contribuicao) desc.
    JSON-serializável (valores nativos float, não np.floatXX) -- vai
    trafegar na API e ser persistido em coluna jsonb (Passo 5).

    Exige exatamente 1 linha: o job diário (Passo 6) processa uma fila e
    precisa de uma explicação POR agendamento (SLO §4). Aceitar X com n
    linhas e devolver só a explicação da primeira gravaria a explicação do
    paciente errado nas demais predições, sem erro visível -- use
    explicar_lote() para a fila."""
    if len(X) != 1:
        raise ValueError(
            f"explicar() espera exatamente 1 linha, recebeu {len(X)} -- "
            "use explicar_lote() para explicar uma fila inteira"
        )
    return explicar_lote(explainer, X)[0]


def explicar_lote(explainer, X):
    """Uma explicação por linha de X, na mesma ordem das linhas -- o SHAP é
    calculado de uma vez só para o lote inteiro (bem mais barato que n
    chamadas a explicar()) e depois fatiado por linha."""
    valores, _ = _valores_classe_positiva(explainer, X)
    return [_explicar_linha(X.columns, linha) for linha in valores]


def _explicar_linha(colunas, valores_da_linha):
    contribuicoes = [
        {"feature": feature, "contribuicao": float(valor)}
        for feature, valor in zip(colunas, valores_da_linha, strict=True)
    ]
    contribuicoes.sort(key=lambda c: abs(c["contribuicao"]), reverse=True)
    return contribuicoes


class ExplicadorLLM(ABC):
    """Interface para tradução das contribuições SHAP em texto. O SHAP
    continua sendo a fonte de verdade numérica; o LLM, quando ligado
    (Passo 13), só traduz contribuições já calculadas em uma frase -- nunca
    recalcula nem substitui a explicação."""

    @abstractmethod
    def explicar_em_texto(self, contribuicoes: list, contexto: dict) -> str | None:
        ...


class ExplicadorLLMDesativado(ExplicadorLLM):
    """Plug inativo (default até o Passo 13). Nunca faz chamada de rede."""

    def explicar_em_texto(self, contribuicoes: list, contexto: dict) -> str | None:
        return None
