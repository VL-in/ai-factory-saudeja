"""Purga das cópias locais do DVC (scripts/purgar_dvc_local.py).

A máquina de quem desenvolve não pode guardar o dataset de treino por mais
tempo que o remote (LGPD.md §5). Estes testes travam a decisão do que sai do
workspace: o que o remote já apagou, o que nunca foi publicado e passou da
retenção, e os recortes derivados do dataset, que seguem o destino dele.
"""
import subprocess
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


def test_hashes_do_head_vem_do_dvc_do_dataset_e_so_dos_recortes_do_lock(monkeypatch):
    """O modelo e o mapa de especialidade estão no mesmo `dvc.lock`, mas não
    têm linha de titular: só os recortes do dataset entram na comparação."""
    arquivos = {
        f"{p.DATASET}.dvc": "outs:\n- md5: aaa\n  path: consultas-treino.csv\n",
        "dvc.lock": (
            "stages:\n"
            "  preprocess:\n"
            "    outs:\n"
            "    - path: data/interim/train_raw.pkl\n      md5: bbb\n"
            "    - path: data/interim/mapa_especialidade.json\n      md5: ccc\n"
            "  train:\n"
            "    outs:\n"
            "    - path: data/model.pkl\n      md5: ddd\n"
        ),
    }
    monkeypatch.setattr(p, "_git_show", arquivos.get)

    assert p.hashes_no_head() == {p.DATASET: "aaa", "data/interim/train_raw.pkl": "bbb"}


def _workspace(tmp_path, monkeypatch, status_no_remote):
    """Um workspace com o dataset e a cópia dele no cache local, publicado (o
    hash do HEAD é o do arquivo). O `dvc` e o git ficam de fora: o teste é
    sobre o que o `main` apaga com o que eles responderiam."""
    dataset = tmp_path / p.DATASET
    dataset.parent.mkdir(parents=True)
    dataset.write_text("id_consulta,id_paciente\n1,pseudonimo\n", encoding="utf-8")
    md5 = p._md5(dataset)
    cache = tmp_path / ".dvc" / "cache" / "files" / "md5"
    copia = cache / md5[:2] / md5[2:]
    copia.parent.mkdir(parents=True)
    copia.write_text("cópia do cache", encoding="utf-8")

    monkeypatch.setattr(p, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(p, "CACHE_DIR", cache)
    monkeypatch.setattr(p, "hashes_no_head", lambda: {p.DATASET: md5})
    monkeypatch.setattr(
        p, "_dvc", lambda *args: subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    )
    monkeypatch.setattr(p, "status_no_remote", status_no_remote)
    return dataset, copia


def test_main_apaga_do_workspace_e_do_cache_a_versao_que_o_remote_apagou(tmp_path, monkeypatch):
    dataset, copia = _workspace(tmp_path, monkeypatch, lambda: {p.DATASET: "missing"})

    assert p.main([]) == 0

    assert not dataset.exists()
    assert not copia.exists()


def test_main_dry_run_so_mostra_o_que_apagaria(tmp_path, monkeypatch, capsys):
    dataset, copia = _workspace(tmp_path, monkeypatch, lambda: {p.DATASET: "missing"})

    assert p.main(["--dry-run"]) == 0

    assert dataset.exists() and copia.exists()
    assert f"[apagaria] {p.DATASET}" in capsys.readouterr().out


def test_main_sem_acesso_ao_remote_sai_com_erro_e_nao_apaga(tmp_path, monkeypatch):
    """Sem saber o que o remote ainda tem, a regra 2 não tem como decidir: o
    script falha em vez de apagar (ou manter) às cegas."""

    def _sem_credencial():
        raise RuntimeError("não foi possível consultar o remote do DVC")

    dataset, copia = _workspace(tmp_path, monkeypatch, _sem_credencial)

    assert p.main([]) == 1

    assert dataset.exists() and copia.exists()


def test_script_nunca_apaga_do_remote():
    """A única credencial que apaga do remote fica no environment `lgpd`
    (ADR-009). Uma purga local com `--cloud` apagaria o histórico do remote com
    a credencial de quem desenvolve."""
    texto = (Path(p.__file__)).read_text(encoding="utf-8")
    codigo = "\n".join(linha.split("#", 1)[0] for linha in texto.splitlines())
    assert '"--cloud"' not in codigo.replace('"status", "--cloud"', "")
    assert '"-c"' not in codigo
