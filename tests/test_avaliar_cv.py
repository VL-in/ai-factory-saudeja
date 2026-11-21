"""scripts/avaliar_cv.py -- validação cruzada repetida (exploratória).

A garantia central é a mesma do tune.py e do ADR-003: o balanceamento roda
dentro de cada fold, e o fold de validação nunca recebe linha sintética."""

import importlib.util
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import preprocess

_CAMINHO = Path(__file__).resolve().parents[1] / "scripts" / "avaliar_cv.py"
_spec = importlib.util.spec_from_file_location("avaliar_cv", _CAMINHO)
avaliar_cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(avaliar_cv)


@pytest.fixture
def X_y(df_consultas_smote):
    X, y, _ = preprocess.preprocessar(df_consultas_smote.copy())
    return X, y


@pytest.fixture(autouse=True)
def smotenc_com_poucos_vizinhos(monkeypatch):
    # 40 linhas, 3 folds: ~9 positivos no treino de cada fold -- k=3 do
    # params.yaml funciona, mas k=1 deixa o teste independente do valor vigente.
    monkeypatch.setitem(avaliar_cv.PARAMS["balancing"], "k_neighbors", 1)


def test_fold_de_validacao_nunca_recebe_linha_sintetica(X_y, monkeypatch):
    """Espiona o que entra no fit e no predict_proba de cada fold. O fit recebe
    mais linhas que o treino do fold (as sintéticas do SMOTE-NC); o predict
    recebe exatamente as linhas originais do fold de validação, nenhuma a mais,
    e nenhuma que tenha sido usada no treino daquele fold."""
    X, y = X_y
    chamadas = []

    class Espiao(avaliar_cv.lgb.LGBMClassifier):
        def fit(self, X_fit, y_fit, **kwargs):
            chamadas.append({"fit": X_fit.copy()})
            return super().fit(X_fit, y_fit, **kwargs)

        def predict_proba(self, X_pred, **kwargs):
            chamadas[-1]["predict"] = X_pred.copy()
            return super().predict_proba(X_pred, **kwargs)

    monkeypatch.setattr(avaliar_cv.lgb, "LGBMClassifier", Espiao)
    n_splits, n_repeats = 3, 2

    avaliar_cv.avaliar(
        X, y, modelo="lightgbm", balanceamento="smotenc", n_splits=n_splits, n_repeats=n_repeats
    )

    assert len(chamadas) == n_splits * n_repeats
    vistos = pd.Series(0, index=X.index)
    for chamada in chamadas:
        X_pred = chamada["predict"]
        n_treino_do_fold = len(X) - len(X_pred)
        # SMOTE-NC gerou linhas sintéticas -- só no treino
        assert len(chamada["fit"]) > n_treino_do_fold
        # validação = linhas originais, com os mesmos valores e índices
        pd.testing.assert_frame_equal(X_pred, X.loc[X_pred.index])
        assert X_pred.index.is_unique
        vistos[X_pred.index] += 1
    # cada linha original cai na validação exatamente uma vez por repetição
    assert (vistos == n_repeats).all()


@pytest.mark.parametrize(
    ("modelo", "balanceamento"),
    [
        ("lightgbm", "smotenc"),
        ("lightgbm", "class_weight"),
        ("lightgbm", "none"),
        ("logistica", "class_weight"),
        ("logistica", "smotenc"),
    ],
)
def test_avaliar_devolve_todas_as_metricas_por_fold_e_resumo(X_y, modelo, balanceamento):
    X, y = X_y
    resultado = avaliar_cv.avaliar(
        X, y, modelo=modelo, balanceamento=balanceamento, n_splits=3, n_repeats=2
    )

    assert set(resultado["folds"]) == set(avaliar_cv.METRICAS)
    assert all(len(v) == 6 for v in resultado["folds"].values())
    for r in resultado["resumo"].values():
        assert r["ic95_inf"] <= r["media"] <= r["ic95_sup"]
    assert resultado["referencia"]["pr_auc_acaso"] == pytest.approx(y.mean())


def test_avaliar_recusa_configuracao_desconhecida(X_y):
    X, y = X_y
    with pytest.raises(ValueError):
        avaliar_cv.avaliar(X, y, modelo="rede_neural")
    with pytest.raises(ValueError):
        avaliar_cv.avaliar(X, y, balanceamento="adasyn")


def test_recall_na_fracao_avisa_os_k_de_maior_probabilidade():
    y = np.array([1, 0, 1, 0, 0, 0, 0, 0, 0, 1])
    proba = np.array([0.9, 0.8, 0.7, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.2])
    # 30% de 10 = 3 avisados: 0.9 (1), 0.8 (0), 0.7 (1) -> 2 de 3 faltosos
    recall, fracao = avaliar_cv.recall_na_fracao(y, proba, 0.30)
    assert recall == pytest.approx(2 / 3)
    assert fracao == pytest.approx(0.3)


def test_recall_na_fracao_arredonda_para_cima():
    # 30% de 7 = 2,1 -> 3 avisados (fila pequena não fica sem lembrete)
    y = np.array([0, 0, 1, 0, 0, 0, 0])
    proba = np.array([0.9, 0.8, 0.7, 0.1, 0.1, 0.1, 0.1])
    recall, fracao = avaliar_cv.recall_na_fracao(y, proba, 0.30)
    assert recall == 1.0
    assert fracao == pytest.approx(3 / 7)


def test_resumir_corrige_o_intervalo_pela_dependencia_entre_folds():
    """O intervalo corrigido (Nadeau-Bengio) é mais largo que o ingênuo
    desvio/raiz(n): com k=5, a variância ganha o termo 1/(k-1) = 0,25."""
    valores = [0.50, 0.55, 0.60, 0.65, 0.70] * 5
    r = avaliar_cv.resumir(valores, n_splits=5)
    desvio = np.std(valores, ddof=1)
    largura_ingenua = 2 * 2.064 * desvio / np.sqrt(25)  # t(24; 0,975) ~= 2,064

    assert r["media"] == pytest.approx(0.60)
    assert r["desvio"] == pytest.approx(desvio)
    assert (r["ic95_sup"] - r["ic95_inf"]) > 2 * largura_ingenua


def test_resumir_sem_variacao_tem_intervalo_de_largura_zero():
    r = avaliar_cv.resumir([0.6] * 25, n_splits=5)
    assert r["ic95_inf"] == pytest.approx(0.6)
    assert r["ic95_sup"] == pytest.approx(0.6)


@pytest.fixture
def logging_da_raiz_preservado():
    """O MLflow com backend SQLite roda as migrations do Alembic, que chamam
    `logging.config.fileConfig` e removem os handlers da raiz -- inclusive o
    `_HandlerSaudeJa` que `configurar_logging` instalou num teste anterior e
    que, por ser idempotente, não reinstala. Sem restaurar, um teste de logging
    que rode depois deste (ordem alfabética) acha a raiz vazia."""
    raiz = logging.getLogger()
    handlers, nivel = list(raiz.handlers), raiz.level
    desabilitados = {
        nome: logger.disabled
        for nome, logger in logging.root.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    yield
    raiz.handlers[:] = handlers
    raiz.setLevel(nivel)
    for nome, estava in desabilitados.items():
        logging.getLogger(nome).disabled = estava


def test_main_loga_run_com_tag_cv_repetida(
    df_consultas_smote, tmp_path, monkeypatch, logging_da_raiz_preservado
):
    import mlflow

    csv = tmp_path / "consultas.csv"
    df_consultas_smote.to_csv(csv, index=False)
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow-cv.db'}"
    monkeypatch.setattr(avaliar_cv, "MLFLOW_TRACKING_URI", tracking_uri)
    monkeypatch.setattr(avaliar_cv, "MLFLOW_EXPERIMENT_NAME", "teste-avaliar-cv")

    avaliar_cv.main(
        [
            "--dados",
            str(csv),
            "--n-splits",
            "3",
            "--n-repeats",
            "1",
            "--balanceamento",
            "class_weight",
        ]
    )

    mlflow.set_tracking_uri(tracking_uri)
    experimento = mlflow.get_experiment_by_name("teste-avaliar-cv")
    runs = mlflow.search_runs(experiment_ids=[experimento.experiment_id])
    assert len(runs) == 1
    run = runs.iloc[0]
    assert run["tags.pipeline_arquitetura"] == "cv-repetida"
    assert run["params.balancing"] == "class_weight"
    for estatistica in ("media", "desvio", "ic95_inf", "ic95_sup"):
        assert f"metrics.cv_roc_auc_{estatistica}" in runs.columns
    assert "metrics.cv_recall_at_fracao_media" in runs.columns


def test_main_sem_mlflow_nao_abre_run(df_consultas_smote, tmp_path, monkeypatch):
    csv = tmp_path / "consultas.csv"
    df_consultas_smote.to_csv(csv, index=False)

    def _proibido(*_a, **_k):
        raise AssertionError("não deveria logar")

    monkeypatch.setattr(avaliar_cv, "logar_mlflow", _proibido)
    resultado = avaliar_cv.main(
        ["--dados", str(csv), "--n-splits", "3", "--n-repeats", "1", "--sem-mlflow"]
    )
    assert "roc_auc" in resultado["resumo"]


# --------------------------------------------------------------------------
# Extensões do teste de sinal do dataset externo (ADR-014)
# --------------------------------------------------------------------------
@pytest.fixture
def X_y_grupos():
    """120 linhas de 30 pacientes (4 consultas cada), sinal forte em
    `historico_noshow`."""
    rng = np.random.default_rng(0)
    n = 120
    grupos = np.repeat([f"P{i}" for i in range(30)], 4)
    y = pd.Series(rng.integers(0, 2, n))
    X = pd.DataFrame(
        {
            "idade": rng.integers(18, 80, n),
            "sexo": rng.integers(0, 2, n),
            "historico_noshow": y * 3 + rng.integers(0, 2, n),
            "dia_de_semana": rng.integers(0, 6, n),
        }
    )
    return X, y, grupos


def test_cv_por_grupo_nunca_poe_o_mesmo_paciente_nos_dois_lados(X_y_grupos):
    X, y, grupos = X_y_grupos
    folds = list(avaliar_cv.divisoes(X, y, grupos, n_splits=5, n_repeats=3, random_state=1))
    assert len(folds) == 15
    vistos = np.zeros(len(X), dtype=int)
    for idx_treino, idx_validacao in folds:
        assert not set(grupos[idx_treino]) & set(grupos[idx_validacao])
        vistos[idx_validacao] += 1
    assert (vistos == 3).all()  # cada linha valida uma vez por repetição


def test_cv_por_grupo_muda_os_folds_entre_repeticoes(X_y_grupos):
    X, y, grupos = X_y_grupos
    folds = list(avaliar_cv.divisoes(X, y, grupos, n_splits=5, n_repeats=2, random_state=1))
    primeira = {tuple(sorted(v)) for _, v in folds[:5]}
    segunda = {tuple(sorted(v)) for _, v in folds[5:]}
    assert primeira != segunda


def test_avaliar_com_grupos_e_subconjunto_de_features(X_y_grupos):
    X, y, grupos = X_y_grupos
    X_sub = avaliar_cv.selecionar_features(X, ["idade", "historico_noshow"])
    assert list(X_sub.columns) == ["idade", "historico_noshow"]
    resultado = avaliar_cv.avaliar(
        X_sub, y, "logistica", "class_weight", n_splits=3, n_repeats=2, grupos=grupos
    )
    assert resultado["resumo"]["roc_auc"]["media"] > 0.8


def test_selecionar_features_recusa_feature_que_o_pipeline_nao_tem(X_y_grupos):
    with pytest.raises(ValueError, match="fora do pipeline"):
        avaliar_cv.selecionar_features(X_y_grupos[0], ["distancia_km"])


def test_permutacao_acusa_sinal_real_com_o_menor_p_possivel(X_y_grupos):
    X, y, grupos = X_y_grupos
    perm = avaliar_cv.teste_de_permutacao(X, y, 19, grupos=grupos, n_splits=3)
    assert perm["p_valor"] == pytest.approx(1 / 20)
    assert perm["roc_auc_real"] > perm["nula_p99"]


def test_permutacao_sem_sinal_nao_acusa(X_y_grupos):
    X, _, grupos = X_y_grupos
    y_ruido = pd.Series(np.random.default_rng(5).integers(0, 2, len(X)))
    perm = avaliar_cv.teste_de_permutacao(
        X[["idade", "sexo"]], y_ruido, 19, grupos=grupos, n_splits=3
    )
    assert perm["p_valor"] > 0.05


def test_auc_univariada_com_ic_que_contem_o_ponto(X_y_grupos):
    X, y, _ = X_y_grupos
    uni = avaliar_cv.auc_univariada(X, y, n_bootstrap=200)
    assert uni["historico_noshow"]["auc"] > 0.95
    for r in uni.values():
        assert r["ic95_inf"] <= r["auc"] <= r["ic95_sup"]
    # categórica: taxa por categoria, nunca abaixo de 0,5
    assert uni["dia_de_semana"]["auc"] >= 0.5


def test_preparar_dados_exclui_pacientes_e_amostra_estratificado(tmp_path):
    df = pd.DataFrame(
        {
            "id_paciente": [f"K{i % 50:016x}" for i in range(400)],
            "no_show": [1 if i % 4 == 0 else 0 for i in range(400)],
        }
    )
    reservado = tmp_path / "reservado.csv"
    df[df["id_paciente"].isin({"K0000000000000000", "K0000000000000001"})].to_csv(reservado)
    recorte = avaliar_cv.preparar_dados(df, reservado, amostra=100, semente_amostra=1)
    assert len(recorte) == 100
    assert recorte["no_show"].sum() == 25
    assert not {"K0000000000000000", "K0000000000000001"} & set(recorte["id_paciente"])


def test_main_com_dado_externo_marca_a_run_com_dados_externos(
    df_consultas_smote, tmp_path, monkeypatch, logging_da_raiz_preservado
):
    import mlflow

    csv = tmp_path / "externas.csv"
    df_consultas_smote.assign(origem="externa").to_csv(csv, index=False)
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow-ext.db'}"
    monkeypatch.setattr(avaliar_cv, "MLFLOW_TRACKING_URI", tracking_uri)
    monkeypatch.setattr(avaliar_cv, "MLFLOW_EXPERIMENT_NAME", "teste-externo")

    avaliar_cv.main(
        [
            "--dados",
            str(csv),
            "--n-splits",
            "3",
            "--n-repeats",
            "1",
            "--modelo",
            "logistica",
            "--balanceamento",
            "class_weight",
            "--grupo",
            "id_paciente",
            "--features",
            "idade",
            "sexo",
            "historico_noshow",
        ]
    )

    mlflow.set_tracking_uri(tracking_uri)
    experimento = mlflow.get_experiment_by_name("teste-externo")
    run = mlflow.search_runs(experiment_ids=[experimento.experiment_id]).iloc[0]
    assert run["tags.dados_externos"] == "true"
    assert run["params.cv_grupo"] == "id_paciente"
    assert run["params.features"] == "idade,sexo,historico_noshow"


def test_tem_dado_externo_so_com_a_origem_externa():
    assert not avaliar_cv.tem_dado_externo(pd.DataFrame({"x": [1]}))
    assert not avaliar_cv.tem_dado_externo(pd.DataFrame({"origem": ["semente"]}))
    assert avaliar_cv.tem_dado_externo(pd.DataFrame({"origem": ["semente", "externa"]}))
