"""
Versão da release (SemVer) a partir do CHANGELOG.

`main` é produção: todo merge que muda o que vai para o Space é um deploy, e
todo deploy de produção vira uma tag `vX.Y.Z` e uma Release no GitHub. A
versão mora num lugar só, o primeiro cabeçalho `## [vX.Y.Z]` de
`docs/logs/CHANGELOG.md`. Quem abre o PR `dev` -> `main` renomeia a seção
`[Não publicado]` para a versão nova, e o deploy cria a tag com essa seção
como notas da Release.

As versões antigas do CHANGELOG (`[v1.14]`, sem PATCH) são anteriores ao
SemVer e são ignoradas.

Subcomandos:
    atual         imprime a versão do topo do CHANGELOG (X.Y.Z)
    conferir      falha se a versão do topo não for publicável: já tem tag em
                  outro commit, é menor ou igual à última tag, ou ainda há
                  item em `[Não publicado]`. Com --sha, uma tag que já aponta
                  para esse commit é um reenvio, não um erro. Escreve
                  `versao` e `situacao` (nova | reenvio) no GITHUB_OUTPUT.
    notas         imprime o corpo da seção da versão do topo
    subir-patch   insere a seção X.Y.(Z+1) com um item -- usado pelos PRs
                  automáticos que trocam o modelo em produção (re-treino e
                  promoção do canário), que deployam sem passar por `dev`

Só biblioteca padrão: roda nos jobs que instalam só o DVC e no job da
Release, que não instala nada.

Uso:
    python scripts/versao_release.py conferir --sha "$GITHUB_SHA"
    python scripts/versao_release.py subir-patch --item "Modelo promovido: ..."
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = REPO_ROOT / "docs" / "logs" / "CHANGELOG.md"

Versao = tuple[int, int, int]

_CABECALHO_SEMVER = re.compile(r"^## \[v(\d+)\.(\d+)\.(\d+)\]", re.MULTILINE)
_CABECALHO = re.compile(r"^## ", re.MULTILINE)
_CABECALHO_VERSAO = re.compile(r"^## \[v", re.MULTILINE)  # inclui as anteriores ao SemVer
_NAO_PUBLICADO = re.compile(r"^## \[Não publicado\].*$", re.MULTILINE)
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class VersaoInvalida(Exception):
    """A versão do CHANGELOG não pode ser publicada como está."""


def formatar(versao: Versao) -> str:
    return ".".join(str(n) for n in versao)


def versao_atual(texto: str) -> Versao | None:
    achado = _CABECALHO_SEMVER.search(texto)
    if achado is None:
        return None
    return (int(achado[1]), int(achado[2]), int(achado[3]))


def _secao(texto: str, inicio: re.Match[str]) -> str:
    """Corpo da seção que começa em `inicio`, até o próximo `## `."""
    proximo = _CABECALHO.search(texto, inicio.end())
    fim = proximo.start() if proximo else len(texto)
    return texto[inicio.end() : fim].strip("\n")


def nao_publicado_com_conteudo(texto: str) -> bool:
    achado = _NAO_PUBLICADO.search(texto)
    return achado is not None and _secao(texto, achado).strip() != ""


def notas(texto: str) -> str:
    achado = _CABECALHO_SEMVER.search(texto)
    if achado is None:
        raise VersaoInvalida("nenhuma seção `## [vX.Y.Z]` no CHANGELOG")
    # A linha do cabeçalho traz autor e data; as notas começam na seguinte.
    resto = _secao(texto, achado)
    return resto.split("\n", 1)[1].strip() + "\n" if "\n" in resto else ""


def tags_semver(tags: dict[str, str]) -> dict[Versao, str]:
    """{nome da tag: sha} -> {versão: sha}, só as tags `vX.Y.Z`."""
    versoes = {}
    for nome, sha in tags.items():
        achado = _TAG.match(nome)
        if achado:
            versoes[(int(achado[1]), int(achado[2]), int(achado[3]))] = sha
    return versoes


def decidir(texto: str, tags: dict[Versao, str], sha: str | None) -> tuple[Versao, str]:
    """(versão, "nova" | "reenvio"), ou VersaoInvalida com o motivo."""
    versao = versao_atual(texto)
    if versao is None:
        raise VersaoInvalida(
            "o CHANGELOG não tem seção `## [vX.Y.Z]`: renomeie `[Não publicado]` "
            "para a versão da release"
        )
    if nao_publicado_com_conteudo(texto):
        raise VersaoInvalida(
            "há itens em `[Não publicado]`: eles iriam para produção sem estar "
            f"nas notas da v{formatar(versao)} -- mova-os para a seção da versão"
        )
    if versao in tags:
        if sha is not None and tags[versao] == sha:
            return versao, "reenvio"
        raise VersaoInvalida(
            f"v{formatar(versao)} já foi publicada em outro commit: suba a versão no CHANGELOG"
        )
    ultima = max(tags, default=None)
    if ultima is not None and versao < ultima:
        raise VersaoInvalida(
            f"v{formatar(versao)} é menor que a última publicada, v{formatar(ultima)}"
        )
    return versao, "nova"


def inserir_patch(texto: str, item: str, autor: str, hoje: date, tags: dict[Versao, str]) -> str:
    atual = max([v for v in (versao_atual(texto), *tags) if v is not None], default=None)
    if atual is None:
        raise VersaoInvalida("nenhuma versão SemVer publicada para subir o PATCH")
    nova = (atual[0], atual[1], atual[2] + 1)
    secao = (
        f"## [v{formatar(nova)}] ({autor}) - {hoje.isoformat()}\n\n### Modificado\n- {item}\n\n"
    )
    # Logo acima da última versão: `[Não publicado]`, se existir, continua no topo.
    alvo = _CABECALHO_SEMVER.search(texto) or _CABECALHO_VERSAO.search(texto)
    posicao = alvo.start() if alvo else len(texto)
    return texto[:posicao] + secao + texto[posicao:]


# nome, objeto da tag e, se anotada, o commit para onde ela aponta
_FORMATO_TAG = "%(refname:short) %(objectname) %(*objectname)"


def _tags_do_git() -> dict[str, str]:
    saida = subprocess.run(  # noqa: S603 -- argumentos fixos, sem entrada externa
        ["git", "for-each-ref", f"--format={_FORMATO_TAG}", "refs/tags"],  # noqa: S607
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    tags = {}
    for linha in saida.splitlines():
        nome, objeto, *alvo = linha.split()
        # Tag anotada: o commit é o `*objectname`; tag leve: o próprio objeto.
        tags[nome] = alvo[0] if alvo else objeto
    return tags


def _escrever_saida(**valores: str) -> None:
    caminho = os.environ.get("GITHUB_OUTPUT")
    if caminho:
        with open(caminho, "a", encoding="utf-8") as saida:
            for chave, valor in valores.items():
                saida.write(f"{chave}={valor}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("atual")
    conferir = sub.add_parser("conferir")
    conferir.add_argument("--sha", help="commit sendo deployado (reenvio da mesma tag)")
    sub.add_parser("notas")
    patch = sub.add_parser("subir-patch")
    patch.add_argument("--item", required=True)
    patch.add_argument("--autor", default="github-actions")
    args = parser.parse_args(argv)

    texto = CHANGELOG.read_text(encoding="utf-8")
    try:
        if args.comando == "atual":
            versao = versao_atual(texto)
            if versao is None:
                raise VersaoInvalida("nenhuma seção `## [vX.Y.Z]` no CHANGELOG")
            print(formatar(versao))
        elif args.comando == "conferir":
            versao, situacao = decidir(texto, tags_semver(_tags_do_git()), args.sha)
            print(f"v{formatar(versao)}: {situacao}")
            _escrever_saida(versao=formatar(versao), situacao=situacao)
        elif args.comando == "notas":
            print(notas(texto), end="")
        else:
            novo = inserir_patch(
                texto, args.item, args.autor, date.today(), tags_semver(_tags_do_git())
            )
            CHANGELOG.write_text(novo, encoding="utf-8", newline="\n")
            print(f"v{formatar(versao_atual(novo) or (0, 0, 0))}")
    except VersaoInvalida as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
