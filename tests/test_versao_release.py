"""Versão da release a partir do CHANGELOG (scripts/versao_release.py).

O deploy de produção cria a tag `vX.Y.Z` a partir do topo do CHANGELOG. Estes
testes travam as recusas que impedem uma tag errada: versão já publicada em
outro commit, versão que anda para trás e item esquecido em `[Não publicado]`.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import versao_release as vr

INTRO = "# Changelog\n\nTexto de introdução.\n\n"
ANTIGAS = "## [v1.14] (Vanessa + Claude) - 2026-10-01\n\n### Adicionado\n- canário\n"


def _changelog(*secoes: str) -> str:
    return INTRO + "\n".join(secoes) + "\n" + ANTIGAS


def test_versoes_anteriores_ao_semver_sao_ignoradas():
    assert vr.versao_atual(INTRO + ANTIGAS) is None
    with pytest.raises(vr.VersaoInvalida, match="Não publicado"):
        vr.decidir(INTRO + ANTIGAS, {}, None)


def test_primeira_release_sem_tag_nenhuma():
    texto = _changelog("## [v2.0.0] (Vanessa) - 2026-10-03\n\n### Adicionado\n- deploy\n")
    assert vr.decidir(texto, {}, "abc") == ((2, 0, 0), "nova")


def test_versao_ja_publicada_em_outro_commit_e_recusada():
    texto = _changelog("## [v2.0.0] (Vanessa) - 2026-10-03\n\n- x\n")
    with pytest.raises(vr.VersaoInvalida, match="já foi publicada"):
        vr.decidir(texto, {(2, 0, 0): "outro"}, "abc")


def test_reenvio_do_mesmo_commit_nao_e_erro():
    """`workflow_dispatch` do deploy em `main` refaz o deploy da versão
    que já tem tag -- sem isso, não daria para reenviar."""
    texto = _changelog("## [v2.0.0] (Vanessa) - 2026-10-03\n\n- x\n")
    assert vr.decidir(texto, {(2, 0, 0): "abc"}, "abc") == ((2, 0, 0), "reenvio")


def test_versao_que_anda_para_tras_e_recusada():
    texto = _changelog("## [v2.0.5] (Vanessa) - 2026-10-03\n\n- x\n")
    with pytest.raises(vr.VersaoInvalida, match="menor"):
        vr.decidir(texto, {(2, 1, 0): "a"}, "abc")


def test_item_esquecido_em_nao_publicado_e_recusado():
    texto = _changelog(
        "## [Não publicado] (Vanessa) - 2026-10-04\n\n- esquecido\n",
        "## [v2.0.0] (Vanessa) - 2026-10-03\n\n- x\n",
    )
    with pytest.raises(vr.VersaoInvalida, match="Não publicado"):
        vr.decidir(texto, {}, None)


def test_nao_publicado_vazio_e_aceito():
    texto = _changelog("## [Não publicado]\n", "## [v2.1.0] (Vanessa) - 2026-10-03\n\n- x\n")
    assert vr.decidir(texto, {(2, 0, 0): "a"}, None) == ((2, 1, 0), "nova")


def test_notas_sao_o_corpo_da_secao_sem_o_cabecalho():
    texto = _changelog("## [v2.0.0] (Vanessa) - 2026-10-03\n\n### Adicionado\n- deploy\n")
    assert vr.notas(texto) == "### Adicionado\n- deploy\n"


def test_notas_de_cabecalho_sem_autor_nem_data_guardam_a_primeira_linha():
    """`## [v2.4.0]` sozinho na linha: o corte da linha do cabeçalho caía na
    primeira linha das notas, e a Release saía sem o "### Adicionado"."""
    texto = _changelog("## [v2.4.0]\n\n### Adicionado\n- alertas\n")
    assert vr.notas(texto) == "### Adicionado\n- alertas\n"


def test_secao_so_com_cabecalho_nao_tem_notas():
    assert vr.notas(_changelog("## [v2.4.0] (Vanessa) - 2026-10-18\n")) == ""


def test_subir_patch_entra_acima_da_ultima_versao():
    texto = _changelog("## [v2.0.0] (Vanessa) - 2026-10-03\n\n- x\n")
    novo = vr.inserir_patch(
        texto, "Modelo promovido: `abc`.", "github-actions", date(2026, 11, 1), {}
    )
    assert vr.versao_atual(novo) == (2, 0, 1)
    assert novo.index("[v2.0.1]") < novo.index("[v2.0.0]")
    assert vr.notas(novo) == "### Modificado\n- Modelo promovido: `abc`.\n"
    assert vr.decidir(novo, {(2, 0, 0): "a"}, None) == ((2, 0, 1), "nova")


def test_subir_patch_parte_da_maior_entre_changelog_e_tags():
    texto = _changelog("## [v2.0.0] (Vanessa) - 2026-10-03\n\n- x\n")
    novo = vr.inserir_patch(texto, "y", "bot", date(2026, 11, 1), {(2, 0, 3): "a"})
    assert vr.versao_atual(novo) == (2, 0, 4)


def test_tags_fora_do_semver_sao_ignoradas():
    assert vr.tags_semver({"v2.0.0": "a", "v1.14": "b", "teste": "c"}) == {(2, 0, 0): "a"}


def test_changelog_real_e_legivel():
    """O CHANGELOG versionado tem de continuar no formato que o deploy lê."""
    texto = vr.CHANGELOG.read_text(encoding="utf-8")
    versao = vr.versao_atual(texto)
    if versao is not None:
        assert vr.notas(texto).strip()


# --- linha de comando: o que o deploy.yml e os PRs automáticos executam --------


def _changelog_em_disco(tmp_path, monkeypatch, *secoes):
    caminho = tmp_path / "CHANGELOG.md"
    caminho.write_text(_changelog(*secoes), encoding="utf-8")
    monkeypatch.setattr(vr, "CHANGELOG", caminho)
    return caminho


def test_conferir_escreve_versao_e_situacao_na_saida_do_passo(tmp_path, monkeypatch):
    """O `deploy.yml` lê `steps.versao.outputs.situacao` para decidir se cria
    a Release: é a única saída do script que muda o que vai ao ar."""
    _changelog_em_disco(tmp_path, monkeypatch, "## [v2.1.0] (Vanessa) - 2026-10-05\n\n- x\n")
    monkeypatch.setattr(vr, "_tags_do_git", lambda: {"v2.0.0": "aaa"})
    saida = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(saida))

    assert vr.main(["conferir", "--sha", "bbb"]) == 0
    assert saida.read_text(encoding="utf-8") == "versao=2.1.0\nsituacao=nova\n"


def test_conferir_recusado_sai_com_1_sem_escrever_a_saida(tmp_path, monkeypatch, capsys):
    _changelog_em_disco(tmp_path, monkeypatch, "## [v2.1.0] (Vanessa) - 2026-10-05\n\n- x\n")
    monkeypatch.setattr(vr, "_tags_do_git", lambda: {"v2.1.0": "outro-commit"})
    saida = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(saida))

    assert vr.main(["conferir", "--sha", "bbb"]) == 1
    assert "::error::" in capsys.readouterr().err
    assert not saida.exists()


def test_subir_patch_reescreve_o_changelog_em_disco(tmp_path, monkeypatch, capsys):
    caminho = _changelog_em_disco(
        tmp_path, monkeypatch, "## [v2.1.0] (Vanessa) - 2026-10-05\n\n- x\n"
    )
    monkeypatch.setattr(vr, "_tags_do_git", lambda: {"v2.1.0": "aaa"})

    assert vr.main(["subir-patch", "--item", "Modelo promovido."]) == 0

    novo = caminho.read_text(encoding="utf-8")
    assert vr.versao_atual(novo) == (2, 1, 1)
    assert "(github-actions)" in novo
    assert capsys.readouterr().out.strip() == "v2.1.1"


def test_tags_do_git_resolvem_a_tag_anotada_para_o_commit(monkeypatch):
    """`%(*objectname)` só existe na tag anotada: é o commit para onde ela
    aponta, e não o objeto da tag. Na tag leve, o próprio objeto é o commit."""
    import subprocess

    saida = "v2.0.0 objeto-da-tag commit-anotado\nv2.1.0 commit-leve \n"
    monkeypatch.setattr(
        vr.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=saida),
    )

    assert vr._tags_do_git() == {"v2.0.0": "commit-anotado", "v2.1.0": "commit-leve"}
