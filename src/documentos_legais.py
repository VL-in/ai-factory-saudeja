"""
SaúdeJá — Política de privacidade e Termos de uso na tela de agendamento
(ADR-010, decisão D2), sem Streamlit.

Antes do cadastro, o paciente marca uma caixa de **ciência** dos dois
documentos e só então o botão "Continuar" libera o formulário. A ciência não é
consentimento: é a forma de garantir que a informação do art. 9º, inclusive a
do processamento fora do Brasil em destaque (art. 9º, § 3º), foi apresentada
antes de qualquer dado ser digitado. A versão dos documentos fica gravada no
agendamento (`agendamentos.versao_documentos`) como prova.

**Fonte única**: os textos são os arquivos de `docs/LGPD/`, os mesmos que o
repositório versiona. `scripts/montar_staging_space.py` os copia para a imagem
do Space. A tela não mostra o material interno deles: o aviso de rascunho, o
bloco de condições de publicação e os links para documentos do repositório,
que não existem fora dele.

A versão vem do campo "**Versão:**" de cada documento.
`tests/test_documentos_legais.py` falha se o texto exibido mudar sem a versão
mudar junto -- a mesma regra de `consentimento.VERSAO_TERMO`.
"""
import re
from pathlib import Path

from config_projeto import caminho_de_env

POLITICA = "politica"
TERMOS = "termos"
DOCUMENTOS = (POLITICA, TERMOS)

TITULOS = {POLITICA: "Política de privacidade", TERMOS: "Termos de uso"}
_ARQUIVOS = {POLITICA: "POLITICA-DE-PRIVACIDADE.md", TERMOS: "TERMOS-DE-USO.md"}

# Em nome do paciente ou do responsável legal, como o termo do treino: no
# agendamento de criança, quem confirma é um dos pais (art. 14, § 1º).
TEXTO_CIENCIA = (
    "Eu, paciente ou responsável legal, li e estou ciente da Política de "
    "privacidade e dos Termos de uso."
)

_COMENTARIO_HTML = re.compile(r"<!--.*?-->", re.DOTALL)
# Parágrafo em citação que abre com o aviso de rascunho, até a linha em branco.
_AVISO_DE_RASCUNHO = re.compile(r"^> \*\*Rascunho[^\n]*(?:\n>[^\n]*)*\n?", re.MULTILINE)
# [texto](ARQUIVO.md) ou [texto](ARQUIVO.md#secao), relativo: vira só o texto.
_LINK_PARA_MD = re.compile(r"\[([^\]]+)\]\((?![a-z]+://)[^)\s]*\.md(?:#[^)\s]*)?\)")
_SEPARADOR_FINAL = re.compile(r"(?:\n\s*---\s*)+\Z")
_VERSAO = re.compile(r"\*\*Versão:\*\*\s*([0-9][0-9.]*[0-9]|[0-9])")


def _diretorio() -> Path:
    return Path(caminho_de_env("DOCUMENTOS_LEGAIS_DIR", "docs/LGPD"))


def _bruto(documento: str) -> str:
    if documento not in _ARQUIVOS:
        raise ValueError(f"documento desconhecido: {documento!r}")
    texto = (_diretorio() / _ARQUIVOS[documento]).read_text(encoding="utf-8")
    # Checkout de Windows com core.autocrlf: o texto (e o hash da versão) não
    # pode depender do fim de linha da máquina.
    return texto.replace("\r\n", "\n")


def texto_para_tela(documento: str) -> str:
    """Markdown do documento como o paciente o vê."""
    texto = _COMENTARIO_HTML.sub("", _bruto(documento))
    texto = _AVISO_DE_RASCUNHO.sub("", texto)
    texto = _LINK_PARA_MD.sub(r"\1", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return _SEPARADOR_FINAL.sub("", texto.strip()).strip()


def versao_do_documento(documento: str) -> str:
    encontrada = _VERSAO.search(_bruto(documento))
    if encontrada is None:
        raise ValueError(f"{_ARQUIVOS[documento]} sem o campo **Versão:**")
    return encontrada.group(1)


def versao() -> str:
    """O que vai para `agendamentos.versao_documentos`: as duas versões numa
    string só, legível sem consulta a outra tabela."""
    return "; ".join(f"{d} {versao_do_documento(d)}" for d in DOCUMENTOS)
