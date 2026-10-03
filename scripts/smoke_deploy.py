"""
Smoke da imagem de deploy e do Space publicado (Passo 11.1, itens A1/A2).

Dois modos, um para cada lado do sync:

- `local` (job `imagem` do `ci.yml`): espera a UI (`/_stcore/health`) e a API
  (`/health`) do container recém-buildado ficarem de pé e confere que a
  `model_version` servida é a do `model.pkl` empacotado no staging. Substitui
  o laço de `curl` que vivia no YAML: o mesmo critério de "saudável" passa a
  ter teste (tests/test_deploy.py).
- `space` (`deploy.yml`, depois do sync): antes, o workflow terminava no sync
  e ficava verde mesmo com o build do Space quebrado. Aqui ele espera o
  runtime do Space sair do build **no commit que acabou de subir** (o `sha`
  do runtime igual ao `sha` do repositório do Space) e bate em
  `/_stcore/health` na URL pública. A `model_version` não é conferida neste
  modo: o Space publica uma porta só (`app_port: 7860`, a UI) e a API em 8000
  não é pública -- depende da decisão da porta única do Passo 11.

Só biblioteca padrão, como `src/campeao.py`: o job de deploy instala o mínimo.

Uso:
    python scripts/smoke_deploy.py local --modelo build/space/data/model.pkl
    python scripts/smoke_deploy.py space --space-id "$HF_SPACE_ID" --timeout 1200
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from campeao import calcular_model_version  # noqa: E402

API_HF = "https://huggingface.co/api/spaces"

# Estágios do runtime do Space (huggingface_hub.SpaceStage). Os de erro só
# contam quando o runtime já está no sha novo -- antes disso, um BUILD_ERROR é
# o do deploy anterior.
ESTAGIO_PRONTO = "RUNNING"
ESTAGIOS_DE_ERRO = frozenset({"BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE"})

# (url, cabeçalhos) -> (status http, corpo). Injetável para os testes.
Buscar = Callable[[str, dict[str, str]], tuple[int, str]]


class FalhaSmoke(RuntimeError):
    """O alvo não ficou saudável no prazo, ou ficou com a versão errada."""


def buscar_http(url: str, cabecalhos: dict[str, str]) -> tuple[int, str]:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"esquema de URL não permitido: {url}")
    requisicao = urllib.request.Request(url, headers=cabecalhos)  # noqa: S310 -- esquema conferido acima
    try:
        with urllib.request.urlopen(requisicao, timeout=10) as resposta:  # noqa: S310
            return resposta.status, resposta.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError):
        # Conexão recusada/reset: o processo ainda está subindo.
        return 0, ""


def _esperar(
    condicao: Callable[[], str | None],
    timeout: float,
    intervalo: float,
    dormir: Callable[[float], None],
    relogio: Callable[[], float],
) -> None:
    """Repete `condicao` até ela devolver None (pronto). Uma string é o motivo
    de ainda não estar pronto, que vai para a mensagem de timeout."""
    limite = relogio() + timeout
    motivo = "nenhuma tentativa"
    while True:
        motivo_atual = condicao()
        if motivo_atual is None:
            return
        motivo = motivo_atual
        if relogio() >= limite:
            raise FalhaSmoke(f"não ficou saudável em {timeout:.0f}s: {motivo}")
        dormir(intervalo)


# --------------------------------------------------------------------------
# modo local: container recém-buildado
# --------------------------------------------------------------------------
def verificar_local(
    url_ui: str,
    url_api: str,
    model_version_esperada: str,
    timeout: float = 120,
    intervalo: float = 2,
    buscar: Buscar = buscar_http,
    dormir: Callable[[float], None] = time.sleep,
    relogio: Callable[[], float] = time.monotonic,
) -> None:
    def pronto() -> str | None:
        status_ui, _ = buscar(f"{url_ui}/_stcore/health", {})
        if status_ui != 200:
            return f"UI respondeu {status_ui or 'nada'}"
        status_api, _ = buscar(f"{url_api}/health", {})
        if status_api != 200:
            return f"API respondeu {status_api or 'nada'}"
        return None

    _esperar(pronto, timeout, intervalo, dormir, relogio)

    _, corpo = buscar(f"{url_api}/health", {})
    servida = json.loads(corpo).get("model_version")
    if servida != model_version_esperada:
        # Saudável, mas com outro modelo: a imagem não empacotou o que o
        # staging tinha (cache de camada velho, COPY errado).
        raise FalhaSmoke(
            f"a API serve o modelo {servida}, mas o staging empacotou {model_version_esperada}"
        )


# --------------------------------------------------------------------------
# modo space: depois do sync
# --------------------------------------------------------------------------
def dominio_padrao(space_id: str) -> str:
    """`dono/nome` -> `dono-nome.hf.space`, a regra do HF para o subdomínio
    (minúsculas, `_` e `.` viram `-`). Usado só se o runtime não listar o
    domínio."""
    return re.sub(r"[^a-z0-9-]", "-", space_id.lower().replace("/", "-")) + ".hf.space"


def _cabecalhos(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _json(buscar: Buscar, url: str, token: str | None) -> dict[str, Any]:
    status, corpo = buscar(url, _cabecalhos(token))
    if status != 200:
        raise FalhaSmoke(f"{url} respondeu {status or 'nada'}")
    dados: dict[str, Any] = json.loads(corpo)
    return dados


def verificar_space(
    space_id: str,
    token: str | None = None,
    timeout: float = 1200,
    intervalo: float = 15,
    buscar: Buscar = buscar_http,
    dormir: Callable[[float], None] = time.sleep,
    relogio: Callable[[], float] = time.monotonic,
) -> str:
    """Devolve a URL pública do Space saudável no commit mais recente."""
    # O sha do repositório do Space DEPOIS do sync: é o commit que o hub-sync
    # acabou de criar, e o runtime tem de chegar nele.
    sha_esperado = _json(buscar, f"{API_HF}/{space_id}", token).get("sha")
    if not sha_esperado:
        raise FalhaSmoke(f"a API do Hub não devolveu o sha do Space {space_id}")

    estado: dict[str, Any] = {}

    def runtime_no_commit_novo() -> str | None:
        runtime = _json(buscar, f"{API_HF}/{space_id}/runtime", token)
        estado["runtime"] = runtime
        estagio, sha = runtime.get("stage"), runtime.get("sha")
        if sha != sha_esperado:
            return f"runtime ainda em {str(sha)[:7]} ({estagio}), esperando {sha_esperado[:7]}"
        if estagio in ESTAGIOS_DE_ERRO:
            raise FalhaSmoke(
                f"o Space terminou em {estagio} no commit {sha_esperado[:7]} -- ver os logs "
                f"de build em https://huggingface.co/spaces/{space_id}?logs=build"
            )
        if estagio != ESTAGIO_PRONTO:
            return f"runtime em {estagio}"
        return None

    _esperar(runtime_no_commit_novo, timeout, intervalo, dormir, relogio)

    dominios = estado["runtime"].get("domains") or []
    dominio = dominios[0]["domain"] if dominios else dominio_padrao(space_id)
    url = f"https://{dominio}"

    def ui_saudavel() -> str | None:
        status, _ = buscar(f"{url}/_stcore/health", {})
        return None if status == 200 else f"UI pública respondeu {status or 'nada'}"

    # RUNNING diz que o container subiu; o health diz que o Streamlit atende.
    _esperar(ui_saudavel, min(timeout, 180), 5, dormir, relogio)
    return url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke da imagem de deploy / do Space")
    sub = parser.add_subparsers(dest="modo", required=True)
    p_local = sub.add_parser("local", help="container local recém-buildado (CI)")
    p_local.add_argument("--ui", default="http://localhost:7860")
    p_local.add_argument("--api", default="http://localhost:8000")
    p_local.add_argument("--modelo", type=Path, default=REPO_ROOT / "build/space/data/model.pkl")
    p_local.add_argument("--timeout", type=float, default=120)
    p_space = sub.add_parser("space", help="Space publicado, depois do sync (deploy)")
    p_space.add_argument("--space-id", required=True)
    p_space.add_argument("--timeout", type=float, default=1200)
    args = parser.parse_args(argv)

    try:
        if args.modo == "local":
            versao = calcular_model_version(args.modelo)
            verificar_local(args.ui, args.api, versao, timeout=args.timeout)
            print(f"[ok] UI e API saudáveis, servindo o modelo {versao}")
        else:
            url = verificar_space(args.space_id, os.environ.get("HF_TOKEN"), timeout=args.timeout)
            print(f"[ok] Space no commit novo e saudável em {url}")
            destino = os.environ.get("GITHUB_STEP_SUMMARY")
            if destino:
                with open(destino, "a", encoding="utf-8") as f:
                    f.write(f"Space publicado e saudável: {url}\n")
    except FalhaSmoke as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
