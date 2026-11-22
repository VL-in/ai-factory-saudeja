"""
SaúdeJá — explicabilidade das predições via SHAP. Cumpre SLO §4 (100% das predições com explicação).

Cálculo SÍNCRONO, dentro do módulo de inferência: volume baixo (fila diária
de uma clínica) e TreeExplainer sobre LightGBM é da ordem de milissegundos,
então cobrir 100% das predições é mais simples por construção do que com um
job assíncrono que pode falhar e deixar predições sem explicação.

O explicador depende do tipo de modelo (`src/modelo.py`):
- LightGBM sem calibração (o campeão em produção): `shap.TreeExplainer`, como
  sempre;
- regressão logística: `ExplicadorLinear`, o SHAP exato de um modelo linear
  com fundo no dado de treino (o mesmo valor do `shap.LinearExplainer`
  intervencional), somando as colunas do one-hot de volta na feature de origem;
- modelo calibrado (sigmoide de Platt): o explicador da base, com as
  contribuições escaladas pela calibração (`ExplicadorCalibrado`). A
  calibração é afim na margem da base, então a soma continua fechando com o
  log-odds da probabilidade mostrada.

O valor numérico do SHAP (contribuição por feature, em log-odds/margem) é a
fonte de verdade auditável e testável por aditividade
(soma(contribuicoes) + expected_value ≈ margem prevista, ver
tests/test_explain.py). Este módulo também define um plug INATIVO para
tradução dessas contribuições em texto via LLM (integração opcional, ainda não ligada) -- o
ExplicadorLLMDesativado nunca chama rede; ele só existe para
API/job e o schema de `predicoes` já reservarem o
formato (`explicacao_texto: str | None`) sem exigir migração depois.

Este plug é também a **fronteira de PII para fora do sistema** (ADR-007): é o
único ponto por onde dado de paciente sairia para um provedor de LLM. Desde
que `pacientes.nome_completo` passou a ser gravado, `ExplicadorLLM` sanitiza o
contexto do prompt por construção -- ver `contexto_sem_pii`.
"""
from abc import ABC, abstractmethod

import numpy as np
import shap

import modelo as modelo_ml
from logging_config import chave_de_pii


def construir_explicador(model):
    """Explicador no espaço de log-odds da probabilidade que o modelo entrega
    -- é o espaço em que a aditividade SHAP vale exatamente (ver docstring do
    módulo). Todos expõem `shap_values(X)` e `expected_value`, a interface do
    `shap.TreeExplainer`. Para o LightGBM sem calibração, é o próprio
    TreeExplainer (model_output="raw")."""
    base, escala, deslocamento = modelo_ml.base_e_calibracao(model)
    if modelo_ml.tipo_do_estimador(base) == "lightgbm":
        explicador = shap.TreeExplainer(base)
    else:
        explicador = ExplicadorLinear(base)
    if modelo_ml.calibrado(model):
        return ExplicadorCalibrado(explicador, escala, deslocamento)
    return explicador


class ExplicadorLinear:
    """SHAP exato da regressão logística (`Pipeline` de `src/modelo.py`).

    Num modelo linear com features tratadas como independentes, a
    contribuição de cada coluna transformada é coef * (valor - média no
    treino), e o valor esperado é intercepto + coef . média -- a soma das
    contribuições mais o esperado dá a margem (`decision_function`) por
    construção. As colunas do one-hot de uma categórica somam numa
    contribuição só, a da feature de origem: a explicação fala em
    "especialidade", não em "especialidade_3".
    """

    def __init__(self, pipeline):
        self._transformar = pipeline.named_steps["transformar"]
        regressao = pipeline.named_steps["modelo"]
        self._coef = np.asarray(regressao.coef_[0], dtype=float)
        self._media = np.asarray(pipeline.media_transformada_, dtype=float)
        self.expected_value = float(regressao.intercept_[0] + self._coef @ self._media)
        self._colunas = [str(c) for c in pipeline.feature_names_in_]
        origem = _feature_de_cada_saida(self._transformar)
        if len(origem) != len(self._coef):
            raise ValueError(
                f"o transformador gera {len(origem)} colunas, a regressão tem {len(self._coef)}"
            )
        # matriz (colunas transformadas x features de origem) que soma o one-hot
        self._agregar = np.zeros((len(origem), len(self._colunas)))
        for i, nome in enumerate(origem):
            self._agregar[i, self._colunas.index(nome)] = 1.0

    def shap_values(self, X):
        Z = np.asarray(self._transformar.transform(X[self._colunas]), dtype=float)
        return (self._coef * (Z - self._media)) @ self._agregar


def _feature_de_cada_saida(transformador) -> list[str]:
    """Feature de origem de cada coluna que o `ColumnTransformer` produz, na
    ordem de saída. One-hot: uma coluna por categoria vista, menos a
    descartada (`drop="if_binary"`); padronização: uma por feature."""
    origem: list[str] = []
    for nome, etapa, colunas in transformador.transformers_:
        if nome == "remainder" or etapa == "drop" or len(colunas) == 0:
            continue
        if nome == "onehot":
            descartadas = getattr(etapa, "drop_idx_", None)
            for i, coluna in enumerate(colunas):
                n = len(etapa.categories_[i])
                if descartadas is not None and descartadas[i] is not None:
                    n -= 1
                origem += [str(coluna)] * n
        else:
            origem += [str(c) for c in colunas]
    return origem


class ExplicadorCalibrado:
    """Explicador de um modelo calibrado: log-odds final = escala * margem da
    base + deslocamento (ver `modelo.base_e_calibracao`). Escalar cada
    contribuição e levar o deslocamento para o valor esperado mantém a soma
    exata no espaço da probabilidade calibrada."""

    def __init__(self, explicador_base, escala: float, deslocamento: float):
        self._base = explicador_base
        self._escala = escala
        esperado_base = float(_esperado_classe_positiva(explicador_base))
        self.expected_value = escala * esperado_base + deslocamento

    def shap_values(self, X):
        valores, _ = _valores_classe_positiva(self._base, X)
        return self._escala * np.asarray(valores, dtype=float)


def _esperado_classe_positiva(explainer) -> float:
    """Valor esperado da classe positiva: escalar, ou o último de um vetor
    [neg, pos] (ou de um vetor de um elemento só)."""
    return float(np.ravel(explainer.expected_value)[-1])


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
    trafegar na API e ser persistido em coluna jsonb (`predicoes.explicacao_shap`).

    Exige exatamente 1 linha: o job diário processa uma fila e
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
    continua sendo a fonte de verdade numérica; o LLM, quando ligado,
    só traduz contribuições já calculadas em uma frase -- nunca
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
        (TrueFoundry, quando ligada) monta o prompt e chama o provedor."""


class ExplicadorLLMDesativado(ExplicadorLLM):
    """Plug inativo (default enquanto o LLM não for ligado). Nunca faz chamada de rede."""

    def _gerar_texto(self, contribuicoes: list, contexto: dict) -> str | None:
        return None
