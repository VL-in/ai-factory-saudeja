"""src/modelo.py -- fábrica do classificador e acesso uniforme a ele.

O que estes testes travam:
- o params.yaml sem os campos novos treina exatamente o campeão de antes
  (LightGBM + SMOTE-NC, sem calibração);
- toda configuração válida devolve um modelo cuja `margem` é o log-odds da
  probabilidade entregue (é o espaço em que o SHAP tem de somar);
- colunas, categóricas e domínio visto são lidos do mesmo jeito para os dois
  tipos e com ou sem calibração;
- a calibração traz a probabilidade de volta para a prevalência real.
"""

import numpy as np
import pandas as pd
import pytest
from scipy.special import logit

import modelo
import preprocess

CATEGORICAS = preprocess.COLUNAS_CATEGORICAS
ESPECIALIDADES = [
    "cardiologia",
    "clinica geral",
    "dermatologia",
    "ginecologia",
    "oftalmologia",
    "ortopedia",
    "pediatria",
]
HORAS = (8, 9, 10, 11, 13, 14, 15, 16, 17)

CONFIGS_VALIDAS = [
    ("lightgbm", "smotenc", "none"),
    ("lightgbm", "class_weight", "none"),
    ("lightgbm", "class_weight", "sigmoid"),
    ("lightgbm", "none", "sigmoid"),
    ("logistica", "smotenc", "none"),
    ("logistica", "class_weight", "none"),
    ("logistica", "class_weight", "sigmoid"),
    ("logistica", "none", "none"),
]


def dataset_sintetico(n=360, semente=7):
    """Consultas válidas no contrato (todas as especialidades do cadastro,
    horários da grade, seg a sex) com sinal conhecido em antecedência e
    histórico, prevalência perto de 25%."""
    rng = np.random.default_rng(semente)
    dias = rng.integers(1, 91, n)
    historico = rng.integers(0, 6, n)
    z = -1.6 + 0.02 * (dias - 30) + 0.35 * historico
    no_show = (rng.random(n) < 1 / (1 + np.exp(-z))).astype(int)
    return pd.DataFrame(
        {
            "id_consulta": range(1, n + 1),
            "id_paciente": [f"P{i}" for i in range(n)],
            "idade": rng.integers(1, 86, n),
            "sexo": rng.choice(["F", "M"], n),
            "especialidade": [ESPECIALIDADES[i % len(ESPECIALIDADES)] for i in range(n)],
            "distancia_km": np.round(rng.uniform(0.5, 40, n), 1),
            "dias_entre_agendamento_consulta": dias,
            "historico_noshow": historico,
            "no_show": no_show,
            "data_hora_agendada": [
                f"2026-01-{5 + (i % 5):02d} {HORAS[i % len(HORAS)]:02d}:00:00" for i in range(n)
            ],
        }
    )


@pytest.fixture(scope="module")
def X_y():
    X, y, _ = preprocess.preprocessar(dataset_sintetico())
    return X, y


def _config(tipo, balanceamento, calibracao, **extra):
    params = {
        "model": {
            "tipo": tipo,
            "calibracao": calibracao,
            "n_estimators": 60,
            "learning_rate": 0.05,
            "max_depth": 3,
            "num_leaves": 8,
            **extra,
        },
        "balancing": {"strategy": balanceamento, "k_neighbors": 3},
    }
    return modelo.config_do_modelo(params)


# --- configuração ---------------------------------------------------------
def test_params_sem_os_campos_novos_e_a_configuracao_do_campeao():
    config = modelo.config_do_modelo(
        {
            "model": {"n_estimators": 120, "learning_rate": 0.02, "max_depth": 6, "num_leaves": 32},
            "balancing": {"k_neighbors": 3},
        }
    )
    assert (config["tipo"], config["balanceamento"], config["calibracao"]) == (
        "lightgbm",
        "smotenc",
        "none",
    )
    assert config["lightgbm"] == {
        "n_estimators": 120,
        "learning_rate": 0.02,
        "max_depth": 6,
        "num_leaves": 32,
        "random_state": modelo.RANDOM_STATE,
    }


def test_regularizacao_nula_fica_de_fora_e_declarada_entra():
    nula = modelo.kwargs_lightgbm(
        {"n_estimators": 1, "learning_rate": 0.1, "max_depth": 2, "num_leaves": 4,
         "reg_lambda": None, "min_child_samples": None}
    )
    assert "reg_lambda" not in nula and "min_child_samples" not in nula
    declarada = modelo.kwargs_lightgbm(
        {"n_estimators": 1, "learning_rate": 0.1, "max_depth": 2, "num_leaves": 4,
         "reg_lambda": 5, "min_child_samples": 20, "subsample": 0.8, "subsample_freq": 1}
    )
    assert declarada["reg_lambda"] == 5 and declarada["min_child_samples"] == 20
    assert declarada["subsample"] == 0.8 and declarada["subsample_freq"] == 1


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("tipo", "rede_neural"), ("strategy", "adasyn"), ("calibracao", "isotonica")],
)
def test_nome_invalido_falha_alto(campo, valor):
    params = {
        "model": {"n_estimators": 1, "learning_rate": 0.1, "max_depth": 2, "num_leaves": 4},
        "balancing": {"k_neighbors": 3},
    }
    bloco = "balancing" if campo == "strategy" else "model"
    params[bloco][campo] = valor
    with pytest.raises(ValueError):
        modelo.config_do_modelo(params)


def test_calibracao_com_smotenc_e_recusada():
    """A sigmoide aprenderia a prevalência sintética de 50% do SMOTE-NC."""
    with pytest.raises(ValueError, match="calibracao"):
        _config("lightgbm", "smotenc", "sigmoid")


# --- ajuste ---------------------------------------------------------------
def test_configuracao_padrao_reproduz_o_treino_antigo(X_y):
    """Guarda de regressão: com os defaults, a fábrica ajusta o mesmo
    LightGBM que o `train.py` ajustava antes dela (SMOTE-NC + LGBMClassifier
    com as categóricas), predição por predição."""
    import lightgbm as lgb
    from imblearn.over_sampling import SMOTENC

    X, y = X_y
    config = _config("lightgbm", "smotenc", "none")
    novo = modelo.ajustar(X, y, config, CATEGORICAS)

    cat = [c for c in CATEGORICAS if c in X.columns]
    X_bal, y_bal = SMOTENC(
        categorical_features=[X.columns.get_loc(c) for c in cat], k_neighbors=3, random_state=42
    ).fit_resample(X, y)
    antigo = lgb.LGBMClassifier(**config["lightgbm"]).fit(X_bal, y_bal, categorical_feature=cat)

    np.testing.assert_allclose(novo.predict_proba(X), antigo.predict_proba(X))


@pytest.mark.parametrize(("tipo", "balanceamento", "calibracao"), CONFIGS_VALIDAS)
def test_toda_configuracao_entrega_probabilidade_e_margem_coerentes(
    X_y, tipo, balanceamento, calibracao
):
    X, y = X_y
    ajustado = modelo.ajustar(X, y, _config(tipo, balanceamento, calibracao), CATEGORICAS)

    proba = ajustado.predict_proba(X)[:, 1]
    assert np.all((proba > 0) & (proba < 1))
    # margem = log-odds da probabilidade entregue, em qualquer configuração
    np.testing.assert_allclose(modelo.margem(ajustado, X), logit(proba), atol=1e-6)
    assert modelo.tipo_do_modelo(ajustado) == tipo
    assert modelo.calibrado(ajustado) == (calibracao == "sigmoid")


@pytest.mark.parametrize(("tipo", "balanceamento", "calibracao"), CONFIGS_VALIDAS)
def test_colunas_categoricas_e_dominio_lidos_do_mesmo_jeito(X_y, tipo, balanceamento, calibracao):
    X, y = X_y
    ajustado = modelo.ajustar(X, y, _config(tipo, balanceamento, calibracao), CATEGORICAS)

    assert modelo.colunas_do_modelo(ajustado) == list(X.columns)
    assert sorted(modelo.categoricas_do_modelo(ajustado)) == sorted(
        c for c in CATEGORICAS if c in X.columns
    )
    maximos = modelo.maximos_vistos(ajustado)
    assert maximos["dias_entre_agendamento_consulta"] == float(
        X["dias_entre_agendamento_consulta"].max()
    )
    assert maximos["distancia_km"] == float(X["distancia_km"].max())


def test_calibracao_traz_a_probabilidade_para_a_prevalencia(X_y):
    """Com class_weight, a probabilidade média sem calibrar fica perto de 0,5;
    calibrada, perto da prevalência real."""
    X, y = X_y
    sem = modelo.ajustar(X, y, _config("logistica", "class_weight", "none"), CATEGORICAS)
    com = modelo.ajustar(X, y, _config("logistica", "class_weight", "sigmoid"), CATEGORICAS)
    prevalencia = float(y.mean())

    media_sem = float(sem.predict_proba(X)[:, 1].mean())
    media_com = float(com.predict_proba(X)[:, 1].mean())
    assert abs(media_com - prevalencia) < 0.03
    assert abs(media_sem - prevalencia) > abs(media_com - prevalencia)


def test_calibracao_nao_muda_a_ordenacao_da_base(X_y):
    """A sigmoide é monótona na margem da base: o ranking (e o roc_auc) é o da
    base ajustada no fold inteiro."""
    X, y = X_y
    com = modelo.ajustar(X, y, _config("lightgbm", "class_weight", "sigmoid"), CATEGORICAS)
    base, escala, _ = modelo.base_e_calibracao(com)
    assert escala > 0
    ordem_final = np.argsort(com.predict_proba(X)[:, 1], kind="stable")
    ordem_base = np.argsort(modelo.margem_base(base, X), kind="stable")
    np.testing.assert_array_equal(ordem_final, ordem_base)


def test_sample_weight_chega_ao_estimador(X_y):
    """Peso por linha (o peso da semente, quando a regra 5.1 for aplicada)."""
    X, y = X_y
    peso = np.where(np.arange(len(y)) % 2 == 0, 1.0, 0.0)
    config = _config("logistica", "none", "none")
    com_peso = modelo.ajustar(X, y, config, CATEGORICAS, sample_weight=peso)
    so_pares = modelo.ajustar(X.iloc[::2], y.iloc[::2], config, CATEGORICAS)
    # mesmo modelo que treinar só nas linhas de peso 1 (a padronização
    # difere, então compara a ordenação, não o coeficiente)
    a = com_peso.predict_proba(X)[:, 1]
    b = so_pares.predict_proba(X)[:, 1]
    assert np.corrcoef(a, b)[0, 1] > 0.99


def test_smotenc_com_peso_e_recusado(X_y):
    X, y = X_y
    with pytest.raises(ValueError, match="sample_weight"):
        modelo.ajustar(
            X, y, _config("lightgbm", "smotenc", "none"), CATEGORICAS, sample_weight=np.ones(len(y))
        )
