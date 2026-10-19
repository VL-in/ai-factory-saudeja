"""
SaúdeJá — Política de privacidade e Termos de uso exibidos na tela de
agendamento (src/documentos_legais.py, ADR-010).
"""
import hashlib
import shutil
from pathlib import Path

import pytest

import documentos_legais as dl

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("documento", dl.DOCUMENTOS)
def test_a_tela_nao_mostra_material_interno_do_repositorio(documento):
    """O aviso de rascunho, as condições de publicação e os links para
    documentos do repositório não existem para quem agenda."""
    texto = dl.texto_para_tela(documento)

    assert "Rascunho" not in texto
    assert "<!--" not in texto
    assert "CONDIÇÕES PARA PUBLICAR" not in texto
    assert ".md)" not in texto
    assert not texto.endswith("---")


def test_termos_trazem_o_processamento_no_exterior_em_destaque_e_a_alternativa():
    """Art. 9º, § 3º: o uso da tela implica processamento fora do Brasil, e
    quem não quiser agenda pela clínica. A mensagem saiu da tela inicial, mas
    não some."""
    texto = dl.texto_para_tela(dl.TERMOS)

    assert "**Ao usar esta tela, os dados que você digita são processados fora do Brasil**" in texto
    assert "agende com a clínica, por telefone ou no balcão" in texto


def test_versao_junta_as_duas_versoes():
    assert dl.versao() == (
        f"politica {dl.versao_do_documento(dl.POLITICA)}; "
        f"termos {dl.versao_do_documento(dl.TERMOS)}"
    )


def test_fim_de_linha_do_checkout_nao_muda_o_texto(tmp_path, monkeypatch):
    """Checkout de Windows com core.autocrlf grava CRLF; o CI, LF. O texto e
    o hash da versão têm de ser os mesmos nos dois."""
    lf = {d: dl.texto_para_tela(d) for d in dl.DOCUMENTOS}
    for arquivo in dl._ARQUIVOS.values():
        original = (REPO_ROOT / "docs" / "LGPD" / arquivo).read_bytes().replace(b"\r\n", b"\n")
        (tmp_path / arquivo).write_bytes(original.replace(b"\n", b"\r\n"))
    monkeypatch.setenv("DOCUMENTOS_LEGAIS_DIR", str(tmp_path))

    assert {d: dl.texto_para_tela(d) for d in dl.DOCUMENTOS} == lf


def test_documento_sem_versao_e_erro(tmp_path, monkeypatch):
    for arquivo in dl._ARQUIVOS.values():
        shutil.copy(REPO_ROOT / "docs" / "LGPD" / arquivo, tmp_path / arquivo)
    termos = tmp_path / dl._ARQUIVOS[dl.TERMOS]
    termos.write_text(
        termos.read_text(encoding="utf-8").replace("**Versão:**", "Versão"), encoding="utf-8"
    )
    monkeypatch.setenv("DOCUMENTOS_LEGAIS_DIR", str(tmp_path))

    with pytest.raises(ValueError):
        dl.versao()


def test_documento_desconhecido_e_erro():
    with pytest.raises(ValueError):
        dl.texto_para_tela("contrato")


# sha256 do que a tela mostra (caixa de ciência + os dois documentos) por
# versão. Mudou um texto? Suba o campo "Versão" do documento e acrescente a
# versão nova aqui -- nunca troque o hash de uma versão existente: agendamentos
# já gravados com ela apontariam para um texto que o paciente não viu.
#
# Exceção única: o hash de "politica 0.2; termos 0.1" foi trocado em 2026-10-11
# (contato do encarregado na seção 9 da Política) porque a versão ainda não
# tinha chegado a nenhum Space -- nenhum agendamento a gravou.
_HASH_POR_VERSAO = {
    "politica 0.2; termos 0.1": "538d2c1131b81907c5aa1921d4cf1ae132384041267806bfbb7562f7d44a3f65",
    "politica 0.3; termos 0.2": "68c5ba28bed333c15326359a3a02918a233d4cbda566a3e80a767219e98dc2d5",
    # Primeiras versões publicadas, vigentes a partir de 2026-10-18.
    "politica 1.0; termos 1.0": "c23a75d2aa283247feb0eba6b120965669854cb3c93096e815b9b9eff4a2e55d",
}


def test_texto_exibido_nao_muda_sem_mudar_a_versao():
    textos = [dl.TEXTO_CIENCIA, *(dl.texto_para_tela(d) for d in dl.DOCUMENTOS)]
    atual = hashlib.sha256("\x00".join(textos).encode()).hexdigest()

    assert _HASH_POR_VERSAO.get(dl.versao()) == atual
