"""
SaúdeJá — "só o campeão vai para produção" (Passos 10.4/10.5).

`data/champion_metrics.json` é a fonte de verdade do modelo em produção
(Passo 9.1): o sha do `model.pkl` que o gate promoveu e o threshold em que as
métricas dele foram medidas. Este módulo confere as duas coisas contra o que
está de fato no disco, e é chamado nos dois pontos por onde produção muda de
comportamento:

- **job D-2** (`src/jobs/inferencia_diaria.py`, pré-checagem): o job decide
  quem recebe SMS pago com `decision.threshold` de `params.yaml`. Se alguém
  mudar o threshold sem passar pelo gate, o `recall_1`/`f1_1` registrados do
  campeão deixam de descrever o que roda -- o job se recusa a rodar;
- **deploy** (`.github/workflows/deploy.yml`, antes do sync): um `dvc repro`
  manual commitado mudaria o `dvc.lock` e levaria ao Space um modelo que nunca
  passou pelo gate (achado 7 da revisão do Passo 10).

Só biblioteca padrão no topo, de propósito: o job de deploy instala o mínimo
(DVC) e roda este arquivo como script. O PyYAML, necessário só para ler o
threshold de `params.yaml`, é importado tarde.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

CHAMPION_PATH_PADRAO = "data/champion_metrics.json"
MODEL_PATH_PADRAO = "data/model.pkl"
PARAMS_PATH_PADRAO = "params.yaml"


class ModeloNaoCampeao(RuntimeError):
    """O modelo ou o threshold no disco não são os do campeão registrado."""


def calcular_model_version(path: str | Path) -> str:
    """Versão determinística e barata do modelo: os 12 primeiros hex do
    sha256 do próprio artefato. É a string que aparece em `/health`, em
    `predicoes.model_version` e em `champion_metrics.json` -- os três caminhos
    precisam produzir a mesma, por isso a definição mora num lugar só
    (`inference.calcular_model_version` a reexporta).

    sha256 em vez de md5: o uso aqui não é criptográfico, mas `hashlib.md5`
    levanta ValueError em host com OpenSSL em modo FIPS, o que derrubaria o
    startup da API por um detalhe sem relação com o modelo."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def _caminho(variavel: str, padrao: str) -> Path:
    return Path(os.environ.get(variavel) or REPO_ROOT / padrao)


def carregar_campeao(path: str | Path | None = None) -> dict[str, Any]:
    caminho = Path(path) if path else _caminho("CHAMPION_METRICS_PATH", CHAMPION_PATH_PADRAO)
    with open(caminho, encoding="utf-8") as f:
        campeao: dict[str, Any] = json.load(f)
    return campeao


def threshold_de_params(path: str | Path | None = None) -> float:
    import yaml  # tarde: ver docstring do módulo

    caminho = Path(path) if path else _caminho("PARAMS_PATH", PARAMS_PATH_PADRAO)
    with open(caminho, encoding="utf-8") as f:
        params = yaml.safe_load(f)
    return float(params["decision"]["threshold"])


def verificar_campeao(
    model_path: str | Path | None = None,
    champion_path: str | Path | None = None,
    threshold: float | None = None,
) -> str:
    """Levanta `ModeloNaoCampeao` se o `model.pkl` ou o threshold divergirem
    do campeão. Devolve a `model_version` conferida.

    `threshold=None` lê de `params.yaml` -- é o valor que o job usaria."""
    modelo = Path(model_path) if model_path else _caminho("MODEL_PATH", MODEL_PATH_PADRAO)
    campeao = carregar_campeao(champion_path)

    obtido = calcular_model_version(modelo)
    esperado = campeao.get("model_version")
    if obtido != esperado:
        raise ModeloNaoCampeao(
            f"model.pkl ({obtido}) não é o campeão registrado em champion_metrics.json "
            f"({esperado}) -- só o modelo promovido pelo gate de re-treino vai para produção"
        )

    atual = threshold_de_params() if threshold is None else float(threshold)
    registrado = float(campeao["decision_threshold"])
    if atual != registrado:
        raise ModeloNaoCampeao(
            f"decision.threshold de params.yaml ({atual}) diverge do threshold em que o "
            f"campeão foi medido ({registrado}) -- recall_1/f1_1 registrados deixariam de "
            "descrever o que roda em produção"
        )
    return obtido


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Confere se o modelo no disco é o campeão")
    parser.add_argument("--model", default=None)
    parser.add_argument("--champion", default=None)
    args = parser.parse_args(argv)
    try:
        versao = verificar_campeao(args.model, args.champion)
    except ModeloNaoCampeao as exc:
        # Formato de anotação do GitHub Actions: aparece no resumo do run.
        print(f"::error::{exc}")
        return 1
    print(f"[ok] model.pkl e threshold são os do campeão ({versao})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
