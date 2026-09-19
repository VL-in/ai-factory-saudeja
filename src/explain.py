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
    """Retorna a explicação da primeira (e única) linha de X: lista de
    {"feature", "contribuicao"} ordenada por abs(contribuicao) desc.
    JSON-serializável (valores nativos float, não np.floatXX) -- vai
    trafegar na API e ser persistido em coluna jsonb (Passo 5)."""
    valores, _ = _valores_classe_positiva(explainer, X)
    contribuicoes = [
        {"feature": feature, "contribuicao": float(valor)}
        for feature, valor in zip(X.columns, valores[0])
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
