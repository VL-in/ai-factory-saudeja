"""Purga das cópias locais do DVC (scripts/purgar_dvc_local.py).

A máquina de quem desenvolve não pode guardar o dataset de treino por mais
tempo que o remote (LGPD.md §5). Estes testes travam a decisão do que sai do
workspace: o que o remote já apagou, o que nunca foi publicado e passou da
retenção, e os recortes derivados do dataset, que seguem o destino dele.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import purgar_dvc_local as p


def _artefato(caminho=p.DATASET, md5_local="aaa", md5_head="aaa", no_remote=True, idade=1.0):
    return p.Artefato(caminho, md5_local, md5_head, no_remote, idade)


def test_versao_publicada_que_continua_no_remote_fica():
    assert p.decidir([_artefato()]) == {}


def test_versao_publicada_que_o_remote_apagou_sai():
    apagar = p.decidir([_artefato(no_remote=False)])
    assert "remote" in apagar[p.DATASET]


def test_versao_local_nao_publicada_fica_ate_a_retencao():
    recente = _artefato(md5_head="bbb", no_remote=False, idade=10)
    limite = p.RETENCAO_LOCAL_NAO_PUBLICADA_DIAS
    antiga = _artefato(md5_head="bbb", no_remote=False, idade=limite + 1)
    assert p.decidir([recente]) == {}
    assert "não publicada" in p.decidir([antiga])[p.DATASET]


def test_arquivo_ausente_nao_gera_acao():
    assert p.decidir([_artefato(md5_local=None, no_remote=False)]) == {}


def test_derivados_saem_junto_com_o_dataset_mesmo_com_hash_diferente():
    """Os recortes do `preprocess` têm as mesmas linhas do dataset. Um
    `dvc repro` local gera um hash diferente do HEAD, mas o conteúdo é o do
    titular que o remote apagou."""
    artefatos = [
        _artefato(no_remote=False),
        _artefato(caminho="data/interim/train_raw.pkl", md5_local="xxx", md5_head="yyy"),
        _artefato(caminho="data/interim/test.pkl", md5_local=None, md5_head="zzz"),
    ]
    apagar = p.decidir(artefatos)
    assert set(apagar) == {p.DATASET, "data/interim/train_raw.pkl"}
    assert "derivado" in apagar["data/interim/train_raw.pkl"]


def test_status_do_remote_reconhece_new_e_missing_com_separador_do_windows():
    status = {
        "data\\consultas-treino.csv": "new",
        "data/interim/test.pkl": "missing",
        "data/interim/train_raw.pkl": "deleted",
    }
    assert p.ausentes_no_remote(status) == {p.DATASET, "data/interim/test.pkl"}


def test_semente_sintetica_e_modelo_nunca_entram_na_purga():
    """A semente não tem titular (LGPD.md, Quadro 2) e o `model.pkl` não guarda
    linha de paciente: o script não pode apagá-los do workspace."""
    assert "data/consultas-historicas.csv" not in p.ARTEFATOS_COM_LINHAS
    assert "data/model.pkl" not in p.ARTEFATOS_COM_LINHAS


def test_script_nunca_apaga_do_remote():
    """A única credencial que apaga do remote fica no environment `lgpd`
    (ADR-009). Uma purga local com `--cloud` apagaria o histórico do remote com
    a credencial de quem desenvolve."""
    texto = (Path(p.__file__)).read_text(encoding="utf-8")
    codigo = "\n".join(linha.split("#", 1)[0] for linha in texto.splitlines())
    assert '"--cloud"' not in codigo.replace('"status", "--cloud"', "")
    assert '"-c"' not in codigo
