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

Este plug é também a **fronteira de PII para fora do sistema** (ADR-007): é o
único ponto por onde dado de paciente sairia para um provedor de LLM. Desde
que `pacientes.nome_completo` passou a ser gravado, `ExplicadorLLM` sanitiza o
contexto do prompt por construção -- ver `contexto_sem_pii`.
"""
from abc import ABC, abstractmethod

import shap

from logging_config import chave_de_pii


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


def contexto_sem_pii(contexto: dict | None) -> dict:
    """Remove do contexto do prompt qualquer campo que nomeie PII.

    Esta é a fronteira que impede o nome do paciente (gravado a partir de
    2026-09-28, ADR-007) de sair para o LLM/TrueFoundry. **O que protege aqui é
    o código, não a forma de armazenamento**: o LLM é chamado de dentro do
    mesmo processo que já leu o nome para desenhar a "Fila do dia", então
    cifrar a coluna no banco não ajudaria -- a chave estaria na mesma memória.
    O que ajuda é o nome nunca entrar no dicionário que vira prompt.

    Reusa `logging_config.chave_de_pii` em vez de ter lista própria: duas
    noções de "campo de PII" divergiriam, e a que divergisse em silêncio seria
    justamente esta, que guarda a fronteira externa.

    Descarta em silêncio, não levanta: um caller novo passando `nome_completo`
    por engano não pode derrubar a tela do funcionário -- mas também não pode
    mandar o nome para um provedor de LLM fora do Brasil.
    """
    if not contexto:
        return {}
    return {
        chave: valor for chave, valor in contexto.items() if not chave_de_pii(chave)
    }


class ExplicadorLLM(ABC):
    """Interface para tradução das contribuições SHAP em texto. O SHAP
    continua sendo a fonte de verdade numérica; o LLM, quando ligado
    (Passo 13), só traduz contribuições já calculadas em uma frase -- nunca
    recalcula nem substitui a explicação.

    `explicar_em_texto` é **concreta de propósito** e o que as implementações
    sobrescrevem é `_gerar_texto`: assim a sanitização do contexto (ADR-007)
    acontece por construção, e não porque cada implementação futura lembrou de
    chamá-la. `tests/test_explain.py` trava isso -- nenhuma subclasse pode
    sobrescrever `explicar_em_texto` e pular a fronteira.
    """

    def explicar_em_texto(self, contribuicoes: list, contexto: dict) -> str | None:
        return self._gerar_texto(contribuicoes, contexto_sem_pii(contexto))

    @abstractmethod
    def _gerar_texto(self, contribuicoes: list, contexto: dict) -> str | None:
        """Recebe o contexto **já sem PII**. É aqui que a implementação real
        (Passo 13) monta o prompt e chama o provedor."""


class ExplicadorLLMDesativado(ExplicadorLLM):
    """Plug inativo (default até o Passo 13). Nunca faz chamada de rede."""

    def _gerar_texto(self, contribuicoes: list, contexto: dict) -> str | None:
        return None
