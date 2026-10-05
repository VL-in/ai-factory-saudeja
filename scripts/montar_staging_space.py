"""
Monta o diretório de staging que sobe para o Hugging Face Space.

Antes, a lista fechada vivia em `cp` soltos dentro do `deploy.yml`, e o CI
buildava a imagem a partir do checkout inteiro (filtrado pelo
`.dockerignore`): os dois contextos batiam por convenção, não por teste (achado
A1 do roteiro do primeiro deploy). Agora os dois workflows chamam este script:

- `ci.yml` (job `imagem`): builda e testa a imagem **a partir do staging** --
  o contexto exato que o Space vai buildar, não uma aproximação dele;
- `deploy.yml`: monta o mesmo staging, no mesmo SHA que o CI acabou de testar
  (o CI é o primeiro job do deploy, via `workflow_call`), e o sincroniza.

A lista é **fechada** de propósito, mesmo princípio da allowlist de
`eventos_app` (`src/observabilidade.py`): arquivo novo no repositório não vai para produção
sem alguém acrescentá-lo em `ARQUIVOS`/`DIRETORIOS`. O `hub-sync` faz upload
por HTTP e não respeita os `.gitignore` aninhados -- subir o checkout levaria
`.dvc/config.local` (URL do remote), o cache do DVC e o dataset de treino para
um Space público fora do Brasil.

`tests/test_deploy.py` trava a coerência: toda origem de `COPY` do
`infra/deploy/dockerfile` existe no staging, e nada proibido entra nele.

Só biblioteca padrão: o job de deploy instala o mínimo (DVC + PyYAML).

Uso:
    python scripts/montar_staging_space.py              # -> build/space
    python scripts/montar_staging_space.py --destino /tmp/space
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DESTINO_PADRAO = REPO_ROOT / "build" / "space"
DOCKERFILE = "infra/deploy/dockerfile"

# (origem no repositório, destino no staging). O Dockerfile vira `Dockerfile`
# na raiz do staging: é o nome que o SDK Docker do Space procura.
ARQUIVOS = (
    (DOCKERFILE, "Dockerfile"),
    ("requirements/base.txt", "requirements/base.txt"),
    ("requirements/api.txt", "requirements/api.txt"),
    ("requirements/ui.txt", "requirements/ui.txt"),
    ("params.yaml", "params.yaml"),
    ("infra/deploy/entrypoint.sh", "infra/deploy/entrypoint.sh"),
    (".streamlit/config.toml", ".streamlit/config.toml"),
    ("data/model.pkl", "data/model.pkl"),
)
DIRETORIOS = (("src", "src"),)

# Nada de cache de bytecode: o Dockerfile compila src/ no build, e um .pyc de
# outra versão do Python (o 3.13 de alguém) só ocuparia espaço no Space.
IGNORAR_EM_DIRETORIOS = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")

# Defesa contra a lista acima ser alargada sem querer: dado de treino, config
# do DVC, banco local ou ambiente nunca podem estar no que sobe.
PROIBIDO = re.compile(r"(\.(csv|env|dvc|db|parquet)$)|(^|/)\.dvc/|config\.local|(^|/)\.env")

# README próprio do Space: o SDK Docker lê o front-matter dele. Não vai no
# README do GitHub, que renderizaria o bloco YAML como tabela.
README_DO_SPACE = """---
title: SaudeJa
sdk: docker
app_port: 7860
pinned: false
---

Classificador de no-show do SaudeJa -- espelho de deploy gerado pelo
`.github/workflows/deploy.yml` (scripts/montar_staging_space.py). Nao edite
aqui: o proximo deploy sobrescreve este Space inteiro.
"""


class ErroStaging(RuntimeError):
    """Origem ausente ou arquivo proibido no staging."""


def arquivos_proibidos(destino: Path) -> list[str]:
    return sorted(
        p.relative_to(destino).as_posix()
        for p in destino.rglob("*")
        if p.is_file() and PROIBIDO.search(p.relative_to(destino).as_posix())
    )


def montar(destino: Path = DESTINO_PADRAO, raiz: Path = REPO_ROOT) -> list[str]:
    """Recria `destino` do zero e devolve a lista ordenada do que entrou.

    Falha (ErroStaging) se alguma origem não existir -- `data/model.pkl` sem
    `dvc pull` é o caso comum, e melhor parar aqui do que subir um Space sem
    modelo -- ou se algo proibido tiver entrado."""
    ausentes = [o for o, _ in (*ARQUIVOS, *DIRETORIOS) if not (raiz / o).exists()]
    if ausentes:
        raise ErroStaging(
            f"origem ausente no repositório: {', '.join(ausentes)}"
            + (" -- rode `dvc pull data/model.pkl`" if "data/model.pkl" in ausentes else "")
        )

    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)
    for origem, alvo in ARQUIVOS:
        (destino / alvo).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(raiz / origem, destino / alvo)
    for origem, alvo in DIRETORIOS:
        shutil.copytree(raiz / origem, destino / alvo, ignore=IGNORAR_EM_DIRETORIOS)
    (destino / "README.md").write_text(README_DO_SPACE, encoding="utf-8", newline="\n")

    proibidos = arquivos_proibidos(destino)
    if proibidos:
        raise ErroStaging(f"o staging contém arquivo que não pode ir para o Space: {proibidos}")

    return sorted(p.relative_to(destino).as_posix() for p in destino.rglob("*") if p.is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monta o staging do HF Space (lista fechada)")
    parser.add_argument("--destino", type=Path, default=DESTINO_PADRAO)
    args = parser.parse_args(argv)
    try:
        conteudo = montar(args.destino)
    except ErroStaging as exc:
        # Formato de anotação do GitHub Actions: aparece no resumo do run.
        print(f"::error::{exc}")
        return 1
    print("\n".join(conteudo))
    print(f"[ok] staging com {len(conteudo)} arquivo(s) em {args.destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
