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
