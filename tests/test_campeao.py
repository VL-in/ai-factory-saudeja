"""
SaúdeJá — "só o campeão vai para produção".

`campeao.verificar_campeao` é chamada pela pré-checagem do job D-2 e pela
guarda do `deploy.yml`. O que se trava: o repositório como está passa; um
`model.pkl` ou um threshold diferentes do campeão registrado reprovam, com a
mensagem dizendo qual dos dois divergiu.
"""
import json

import pytest

import campeao
import inference

MODEL_PATH = "data/model.pkl"


def test_modelo_e_threshold_do_repositorio_sao_os_do_campeao():
    versao = campeao.verificar_campeao(MODEL_PATH)
    assert versao == campeao.carregar_campeao()["model_version"]


def test_model_version_e_a_mesma_definicao_em_campeao_e_em_inference():
    """API, job e UI chamam `inference.calcular_model_version`; o deploy chama
    `campeao.calcular_model_version`. Duas definições divergiriam em silêncio."""
    assert inference.calcular_model_version is campeao.calcular_model_version


def test_modelo_diferente_do_campeao_reprova(tmp_path):
    outro = tmp_path / "model.pkl"
    outro.write_bytes(b"um dvc repro manual commitado")

    with pytest.raises(campeao.ModeloNaoCampeao, match="não é o campeão"):
        campeao.verificar_campeao(outro)


def test_threshold_diferente_do_campeao_reprova():
    """recall_1/f1_1 do campeão foram medidos num threshold específico: com
    outro, o número registrado deixa de descrever o que roda."""
    registrado = campeao.carregar_campeao()["decision_threshold"]

    with pytest.raises(campeao.ModeloNaoCampeao, match="threshold"):
        campeao.verificar_campeao(MODEL_PATH, threshold=registrado + 0.05)


def test_cli_devolve_1_com_anotacao_de_erro(tmp_path, capsys):
    campeao_falso = tmp_path / "champion_metrics.json"
    campeao_falso.write_text(
        json.dumps({"model_version": "000000000000", "decision_threshold": 0.6}),
        encoding="utf-8",
    )

    codigo = campeao.main(["--model", MODEL_PATH, "--champion", str(campeao_falso)])

    assert codigo == 1
    assert capsys.readouterr().out.startswith("::error::")


def test_cli_devolve_0_para_o_repositorio():
    assert campeao.main(["--model", MODEL_PATH]) == 0
