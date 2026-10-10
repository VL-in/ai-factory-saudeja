"""
Purga das cópias locais do DVC (LGPD.md §5): a máquina de quem desenvolve não
pode guardar o dataset de treino por mais tempo que o remote.

O remote apaga as versões do dataset por expiração (90 dias depois de
substituídas) e na eliminação ou revogação de um titular (ADR-009). Cada
`dvc pull` deixa uma cópia em `.dvc/cache` e no workspace, e nada apagava
essas cópias: o cache local acumulava todas as versões já baixadas. Duas
regras fecham essa porta:

1. **O cache local só guarda o que o workspace usa** (`dvc gc --workspace`).
   As versões antigas saem do cache local na hora, mais cedo que no remote,
   de onde podem ser baixadas de novo enquanto existirem lá. Nunca `--cloud`:
   este script não toca no remote.
2. **Arquivo com linha de paciente que o remote já apagou sai do workspace.**
   O re-treino faz `dvc push` antes de abrir o PR, então toda versão
   registrada no HEAD já esteve no remote. Se ela não está mais lá, foi
   apagada pela expiração, por uma eliminação ou por uma revogação, e a cópia
   local também precisa ir. Uma versão local que nunca foi publicada (export
   ou `dvc repro` feito na máquina) é apagada depois de 90 dias.

Só os artefatos com linha de paciente entram na regra 2: o dataset e os dois
recortes que o `preprocess` deriva dele. `model.pkl` e os JSON do pipeline não
guardam linha de titular (LGPD.md, Quadro 2). A semente sintética
(`consultas-historicas.csv`) também fica de fora.

A consulta ao remote precisa da credencial de leitura do DVC (a mesma do
`dvc pull`, lida do `.env`). Sem ela, a regra 1 roda e o script sai com erro,
porque a regra 2 não pôde ser verificada.

Uso:
    python scripts/purgar_dvc_local.py            # aplica
    python scripts/purgar_dvc_local.py --dry-run  # só mostra o que apagaria
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / ".dvc" / "cache" / "files" / "md5"

DATASET = "data/consultas-treino.csv"
# Recortes do dataset gravados pelo stage `preprocess` (dvc.yaml): têm as mesmas
# linhas, então seguem o destino dele.
DERIVADOS_DO_DATASET = ("data/interim/train_raw.pkl", "data/interim/test.pkl")
ARTEFATOS_COM_LINHAS = (DATASET, *DERIVADOS_DO_DATASET)

RETENCAO_LOCAL_NAO_PUBLICADA_DIAS = 90


@dataclass(frozen=True)
class Artefato:
    caminho: str
    md5_local: str | None  # None: o arquivo não existe no workspace
    md5_head: str | None  # hash registrado no commit HEAD (.dvc ou dvc.lock)
    no_remote: bool
    idade_dias: float


def motivo_para_apagar(artefato: Artefato) -> str | None:
    """Regra 2 do módulo, por artefato. Devolve o motivo, ou None para manter."""
    if artefato.md5_local is None:
        return None
    publicado = artefato.md5_local == artefato.md5_head
    if publicado and not artefato.no_remote:
        return "versão apagada do remote (expiração, eliminação ou revogação)"
    if not publicado and artefato.idade_dias > RETENCAO_LOCAL_NAO_PUBLICADA_DIAS:
        return f"versão local não publicada com mais de {RETENCAO_LOCAL_NAO_PUBLICADA_DIAS} dias"
    return None


def decidir(artefatos: list[Artefato]) -> dict[str, str]:
    """{caminho: motivo} do que sai do workspace. Os recortes derivados saem
    junto com o dataset, qualquer que seja o hash deles: têm as mesmas linhas."""
    apagar = {}
    for artefato in artefatos:
        motivo = motivo_para_apagar(artefato)
        if motivo:
            apagar[artefato.caminho] = motivo
    if DATASET in apagar:
        presentes = {a.caminho for a in artefatos if a.md5_local is not None}
        for derivado in DERIVADOS_DO_DATASET:
            if derivado in presentes:
                apagar.setdefault(derivado, f"derivado de {DATASET}, que foi apagado")
    return apagar


def ausentes_no_remote(status_cloud: dict) -> set[str]:
    """Saída de `dvc status --cloud --json`: `{caminho: "new" | "deleted" |
    "missing"}`. "new" é o que está no cache local e não no remote; "missing",
    o que não está em nenhum dos dois."""
    return {
        Path(caminho).as_posix()
        for caminho, situacao in status_cloud.items()
        if situacao in ("new", "missing")
    }


def _md5(caminho: Path) -> str | None:
    if not caminho.is_file():
        return None
    with open(caminho, "rb") as f:
        return hashlib.md5(f.read(), usedforsecurity=False).hexdigest()


def _git_show(caminho: str) -> str | None:
    saida = subprocess.run(  # noqa: S603 -- argumentos fixos, sem entrada externa
        ["git", "show", f"HEAD:{caminho}"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return saida.stdout if saida.returncode == 0 else None


def hashes_no_head() -> dict[str, str]:
    """Hash de cada artefato com linha de paciente, como registrado no HEAD."""
    hashes: dict[str, str] = {}
    dvc_do_dataset = _git_show(f"{DATASET}.dvc")
    if dvc_do_dataset:
        saida = yaml.safe_load(dvc_do_dataset)["outs"][0]
        hashes[DATASET] = saida["md5"]
    lock = _git_show("dvc.lock")
    if lock:
        for estagio in (yaml.safe_load(lock).get("stages") or {}).values():
            for saida in estagio.get("outs") or []:
                if saida["path"] in DERIVADOS_DO_DATASET:
                    hashes[saida["path"]] = saida["md5"]
    return hashes


def _dvc(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 -- argumentos fixos, sem entrada externa
        [sys.executable, "-m", "dvc", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def status_no_remote() -> dict:
    alvos = [f"{DATASET}.dvc", "preprocess"]
    resultado = _dvc("status", "--cloud", "--json", *alvos)
    if resultado.returncode != 0:
        raise RuntimeError(
            "não foi possível consultar o remote do DVC -- confira a credencial "
            f"de leitura no .env: {resultado.stderr.strip()[:300]}"
        )
    return json.loads(resultado.stdout or "{}")


def _caminho_no_cache(md5: str) -> Path:
    return CACHE_DIR / md5[:2] / md5[2:]


def _apagar(caminho: str, md5: str | None, dry_run: bool) -> None:
    alvos = [REPO_ROOT / caminho]
    if md5:
        alvos.append(_caminho_no_cache(md5))
    for alvo in alvos:
        if alvo.exists() and not dry_run:
            alvo.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--dry-run", action="store_true", help="só mostra o que apagaria")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass

    # Regra 1. `--dry` lista os hashes que sairiam, sem apagar.
    gc = _dvc("gc", "--workspace", "--force", *(["--dry"] if args.dry_run else []))
    if gc.returncode != 0:
        print(f"[erro] dvc gc --workspace falhou: {gc.stderr.strip()[:300]}")
        return 1
    removidos = sum(1 for linha in (gc.stdout + gc.stderr).splitlines() if "Removing" in linha)
    verbo = "sairiam" if args.dry_run else "saíram"
    print(f"[ok] {removidos} objeto(s) {verbo} do cache local; fica só o que o workspace usa")

    # Regra 2.
    try:
        ausentes = ausentes_no_remote(status_no_remote())
    except RuntimeError as exc:
        print(f"[erro] {exc}")
        return 1
    head = hashes_no_head()
    agora = time.time()
    artefatos = []
    for caminho in ARTEFATOS_COM_LINHAS:
        arquivo = REPO_ROOT / caminho
        artefatos.append(
            Artefato(
                caminho=caminho,
                md5_local=_md5(arquivo),
                md5_head=head.get(caminho),
                no_remote=caminho not in ausentes,
                idade_dias=(agora - arquivo.stat().st_mtime) / 86400 if arquivo.exists() else 0.0,
            )
        )

    apagar = decidir(artefatos)
    for artefato in artefatos:
        if artefato.caminho in apagar:
            _apagar(artefato.caminho, artefato.md5_local, args.dry_run)
            verbo = "apagaria" if args.dry_run else "apagado"
            print(f"[{verbo}] {artefato.caminho}: {apagar[artefato.caminho]}")
    if not apagar:
        print("[ok] nenhuma cópia local de dataset fora da retenção")
    return 0


if __name__ == "__main__":
    sys.exit(main())
