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


def _rodar_etapa(image_tag: str, data_dir: Path, script: str, env: dict[str, str]) -> str:
    """Uma etapa do dvc.yaml, do jeito que o stage a roda: mesma imagem, o
    `data/` montado em /app/data, os caminhos passados por variável de
    ambiente. MLflow em sqlite dentro do volume, em vez do mlflow-server."""
    comando = ["docker", "run", "--rm", "-v", f"{data_dir}:/app/data"]
    for chave, valor in env.items():
        comando += ["-e", f"{chave}={valor}"]
    resultado = subprocess.run(
        [*comando, image_tag, script],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert resultado.returncode == 0, (
        f"{script} falhou no container:\n{resultado.stdout}\n{resultado.stderr}"
    )
    return resultado.stdout


@pytest.mark.skipif(not _docker_disponivel(), reason="Docker não disponível/rodando")
def test_pipeline_completo_no_container_gera_selo_modelo_e_run(tmp_path):
    """As quatro etapas do dvc.yaml (validate_data -> preprocess -> train ->
    validate), em sequência, na imagem de treino. Até 2026-09-30 este teste
    rodava a imagem sem comando nenhum -- sobrevivia da época em que ela tinha
    um único script -- e falhava sem dizer por quê."""
    dataset_original = REPO_ROOT / "data" / "consultas-historicas.csv"
    assert dataset_original.exists(), (
        "dataset base não encontrado em data/ -- `dvc pull data/consultas-historicas.csv`"
    )

    data_dir = tmp_path / "data"
    (data_dir / "interim").mkdir(parents=True)
    shutil.copy(dataset_original, data_dir / "consultas-historicas.csv")
    shutil.copy(dataset_original, data_dir / "consultas-treino.csv")

    caminhos = {
        "DATA_PATH": "/app/data/consultas-treino.csv",
        "CONSULTAS_HISTORICAS_PATH": "/app/data/consultas-historicas.csv",
        "SELO_DADOS_PATH": "/app/data/interim/dados_validados.json",
        "RELATORIO_DADOS_PATH": "/app/data/interim/relatorio_dados.json",
        "TRAIN_RAW_PATH": "/app/data/interim/train_raw.pkl",
        "TEST_PATH": "/app/data/interim/test.pkl",
        "MAPA_ESPECIALIDADE_PATH": "/app/data/interim/mapa_especialidade.json",
        "MODEL_PATH": "/app/data/model.pkl",
        "RUN_ID_PATH": "/app/data/interim/mlflow_run_id.txt",
        "MLFLOW_TRACKING_URI": "sqlite:////app/data/mlflow.db",
    }

    image_tag = "saudeja-train-teste-integracao"
    subprocess.run(
        ["docker", "build", "-t", image_tag, "-f", "dockerfile", "."],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        saida = {
            script: _rodar_etapa(image_tag, data_dir, script, caminhos)
            for script in (
                "src/validate_data.py",
                "src/preprocess.py",
                "src/train.py",
                "src/validate.py",
            )
        }
    finally:
        subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True)

    assert "aprovado" in saida["src/validate_data.py"]
    assert (data_dir / "interim" / "dados_validados.json").exists()
    assert "modelo salvo em" in saida["src/train.py"]
    assert "roc_auc" in saida["src/validate.py"]
    assert (data_dir / "mlflow.db").exists(), "container não gerou data/mlflow.db"

    artefato = joblib.load(data_dir / "model.pkl")
    assert "model" in artefato and "mapa_especialidade" in artefato
    assert hasattr(artefato["model"], "predict")
