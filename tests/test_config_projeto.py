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
from datetime import timedelta
from pathlib import Path

import pytest

import config_projeto
import observabilidade

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("valor", "microssegundos"),
    [
        ("2026-10-04T19:46:05.01422+00:00", 14220),  # o do CI: 5 dígitos
        ("2026-10-04T19:46:05.1+00:00", 100000),
        ("2026-10-04T19:46:05.014220+00:00", 14220),
        ("2026-10-04T19:46:05+00:00", 0),
    ],
)
def test_ler_timestamptz_aceita_a_fracao_sem_zeros_finais(valor, microssegundos):
    """O PostgREST corta os zeros finais da fração, e o `fromisoformat` do
    Python 3.10 (o do CI e das imagens) recusa 5 dígitos. Completar a fração
    não pode mudar o valor: ".1" é um décimo de segundo."""
    lido = config_projeto.ler_timestamptz(valor)

    assert lido.replace(microsecond=0).isoformat() == "2026-10-04T19:46:05+00:00"
    assert lido.microsecond == microssegundos
    assert lido.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [(None, 90), ("", 90), ("30", 30), ("noventa", 90), ("30.5", 90), ("0", 1), ("-7", 1)],
)
@pytest.mark.parametrize(
    ("variavel", "janela"),
    [
        ("RETENCAO_DADOS_DERIVADOS_DIAS", config_projeto.retencao_dados_derivados_dias),
        ("JANELA_TREINO_DIAS", config_projeto.janela_treino_dias),
        ("OBSERVABILIDADE_RETENCAO_DIAS", observabilidade.retencao_dias),
    ],
)
def test_janelas_de_retencao_caem_no_padrao_e_nunca_em_zero(
    monkeypatch, variavel, janela, bruto, esperado
):
    """As três janelas da LGPD (dados derivados, base de treino e
    `eventos_app`): valor inválido cai no padrão de 90 dias em vez de derrubar
    a purga, e nenhum valor apaga tudo de uma vez -- o piso é 1 dia."""
    if bruto is None:
        monkeypatch.delenv(variavel, raising=False)
    else:
        monkeypatch.setenv(variavel, bruto)

    assert janela() == esperado


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
