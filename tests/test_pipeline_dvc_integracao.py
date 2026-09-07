"""
Teste de integração de ponta a ponta do pipeline de treino via DVC + Docker.

Roda de verdade `docker build` + `docker run` conforme definido em dvc.yaml,
contra uma cópia do dataset real, e valida que os artefatos de saída são
gerados com o formato esperado. É lento (build de imagem) e exige Docker
instalado e rodando -- é pulado automaticamente quando não disponível.

Rodar manualmente com:
    pytest tests/test_pipeline_dvc_integracao.py -m integracao -v
"""
import shutil
import subprocess
from pathlib import Path

import joblib
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.integracao


def _docker_disponivel():
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10, check=True
        )
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _docker_disponivel(), reason="Docker não disponível/rodando")
def test_pipeline_train_docker_gera_model_pkl_e_mlflow_db(tmp_path):
    dataset_original = REPO_ROOT / "data" / "consultas-historicas.csv"
    assert dataset_original.exists(), "dataset base não encontrado em data/"

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(dataset_original, data_dir / "consultas-historicas.csv")

    image_tag = "saudeja-train-teste-integracao"
    subprocess.run(
        ["docker", "build", "-t", image_tag, "-f", "dockerfile", "."],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    try:
        resultado = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{data_dir}:/app/data",
                "-e",
                "MODEL_PATH=/app/data/model.pkl",
                "-e",
                "MLFLOW_TRACKING_URI=sqlite:////app/data/mlflow.db",
                image_tag,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    finally:
        subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True)

    assert "modelo salvo em" in resultado.stdout

    model_path = data_dir / "model.pkl"
    mlflow_db_path = data_dir / "mlflow.db"
    assert model_path.exists(), "container não gerou data/model.pkl"
    assert mlflow_db_path.exists(), "container não gerou data/mlflow.db"

    artefato = joblib.load(model_path)
    assert "model" in artefato and "mapa_especialidade" in artefato
    assert hasattr(artefato["model"], "predict")
