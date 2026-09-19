"""
Regressão: os módulos de src/ liam params.yaml e resolviam os caminhos
default a partir do CWD do processo, não da raiz do repositório. Isso
funcionava nos containers do dvc.yaml e no pytest (ambos partem da raiz) e
quebrava em qualquer outro CWD -- notadamente a API iniciada por um
supervisor, onde o FileNotFoundError aparecia no import, longe da causa.
"""
import os
import subprocess
import sys
from pathlib import Path

import config_projeto

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repo_root_aponta_para_a_raiz_do_repositorio():
    assert (config_projeto.REPO_ROOT / "params.yaml").exists()
    assert config_projeto.REPO_ROOT == REPO_ROOT


def test_caminho_de_env_usa_variavel_de_ambiente_quando_definida(monkeypatch):
    monkeypatch.setenv("CAMINHO_DE_TESTE", "/app/data/model.pkl")
    assert config_projeto.caminho_de_env("CAMINHO_DE_TESTE", "data/model.pkl") == (
        "/app/data/model.pkl"
    )


def test_caminho_de_env_resolve_default_a_partir_da_raiz_nao_do_cwd(monkeypatch):
    monkeypatch.delenv("CAMINHO_DE_TESTE", raising=False)
    caminho = Path(config_projeto.caminho_de_env("CAMINHO_DE_TESTE", "data/model.pkl"))
    assert caminho.is_absolute()
    assert caminho == REPO_ROOT / "data" / "model.pkl"


def test_modulos_de_src_importam_a_partir_de_qualquer_cwd(tmp_path):
    """Importa inference/preprocess/train/validate/api.main de um CWD
    arbitrário, em um subprocesso limpo -- é o cenário que quebrava antes."""
    codigo = (
        f"import sys; sys.path.insert(0, {str(REPO_ROOT / 'src')!r})\n"
        "import inference, preprocess, train, validate\n"
        "import api.main\n"
        "assert inference.PARAMS['decision']['threshold'] is not None\n"
        "print('ok')\n"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert resultado.returncode == 0, resultado.stderr
    assert "ok" in resultado.stdout
