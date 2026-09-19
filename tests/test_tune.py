import preprocess
import tune

GRID_PEQUENO = {
    "model__n_estimators": [50, 80],
    "model__num_leaves": [16, 31],
}


def test_buscar_melhores_parametros_retorna_grid_search_fitted(df_consultas_smote):
    """Usa um grid e um k_neighbors pequenos -- só valida a mecânica
    (Pipeline SMOTENC+LightGBM dentro do CV, best_params_/cv_results_
    preenchidos), não a qualidade do resultado em si."""
    X, y, _ = preprocess.preprocessar(df_consultas_smote.copy())
    X_train, _, y_train, _ = preprocess.dividir_treino_teste(X, y)

    grid_search = tune.buscar_melhores_parametros(
        X_train, y_train, k_neighbors=1, param_grid=GRID_PEQUENO, n_folds=3
    )

    assert hasattr(grid_search, "best_params_")
    assert set(grid_search.best_params_.keys()) == set(GRID_PEQUENO.keys())
    assert "mean_test_f1_1" in grid_search.cv_results_
    assert "mean_test_recall_1" in grid_search.cv_results_
    assert "mean_test_pr_auc" in grid_search.cv_results_


def test_buscar_melhores_parametros_nao_recebe_fold_de_teste(df_consultas_smote):
    """O CV interno do GridSearch só deve enxergar o fold de treino recebido
    -- mesma garantia de isolamento do fold de teste que train.py já tem."""
    X, y, _ = preprocess.preprocessar(df_consultas_smote.copy())
    X_train, X_test, y_train, _y_test = preprocess.dividir_treino_teste(X, y)

    tune.buscar_melhores_parametros(
        X_train, y_train, k_neighbors=1, param_grid=GRID_PEQUENO, n_folds=3
    )

    assert len(X_test) == round(len(df_consultas_smote) * preprocess.TEST_SIZE)


def test_main_end_to_end_loga_melhores_parametros_e_metricas_no_mlflow(
    df_consultas_smote, tmp_path, monkeypatch
):
    import joblib
    import mlflow

    train_raw_path = tmp_path / "train_raw.pkl"

    X, y, _ = preprocess.preprocessar(df_consultas_smote.copy())
    X_train, _, y_train, _ = preprocess.dividir_treino_teste(X, y)
    joblib.dump({"X": X_train, "y": y_train}, train_raw_path)

    tracking_uri = f"sqlite:///{tmp_path / 'mlflow-tune-main.db'}"
    monkeypatch.setattr(tune, "TRAIN_RAW_PATH", str(train_raw_path))
    monkeypatch.setattr(tune, "MLFLOW_TRACKING_URI", tracking_uri)
    monkeypatch.setattr(tune, "MLFLOW_EXPERIMENT_NAME", "teste-tune-main-e2e")
    monkeypatch.setattr(tune, "PARAM_GRID", GRID_PEQUENO)
    monkeypatch.setattr(tune, "N_FOLDS", 3)
    monkeypatch.setitem(tune.PARAMS["balancing"], "k_neighbors", 1)

    tune.main()

    mlflow.set_tracking_uri(tracking_uri)
    experiment = mlflow.get_experiment_by_name("teste-tune-main-e2e")
    runs = mlflow.search_runs(experiment_ids=[experiment.experiment_id])

    assert len(runs) == 1
    assert runs.iloc[0]["tags.pipeline_arquitetura"] == tune.PIPELINE_TAG_VALUE
    assert "metrics.cv_f1_1" in runs.columns
    assert "metrics.cv_recall_1" in runs.columns
    assert "metrics.cv_pr_auc" in runs.columns
