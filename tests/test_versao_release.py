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
