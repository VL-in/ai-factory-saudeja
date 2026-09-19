"""
SaúdeJá — resolução de caminhos e carregamento de configuração do projeto.

Motivo de existir: todos os módulos de src/ liam `params.yaml` com um
caminho relativo ao CWD (`open("params.yaml")`) e usavam defaults como
"./data/model.pkl". Isso funciona quando o processo é iniciado da raiz do
repositório (containers do dvc.yaml, pytest) e quebra silenciosamente em
qualquer outro CWD -- o caso mais provável sendo a API iniciada por um
supervisor/uvicorn a partir de outro diretório, onde o erro apareceria só
no import, longe da causa.

REPO_ROOT é derivado do próprio arquivo (src/config_projeto.py -> raiz), o
que vale tanto no repositório local quanto na imagem Docker (src/ em
/app/src, params.yaml em /app/params.yaml). Variáveis de ambiente continuam
tendo precedência -- é assim que o dvc.yaml aponta os caminhos para dentro
do bind mount do container.
"""
import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

PARAMS_PATH = Path(os.environ.get("PARAMS_PATH") or REPO_ROOT / "params.yaml")


def caminho_de_env(variavel: str, default_relativo: str) -> str:
    """Valor de `variavel` se definida; senão o default, resolvido a partir
    da raiz do repositório em vez do CWD do processo."""
    return os.environ.get(variavel) or str(REPO_ROOT / default_relativo)


def carregar_params(path=None) -> dict:
    """Lê params.yaml. Os módulos guardam o resultado em um `PARAMS` de
    nível de módulo -- os testes dependem desse nome para sobrescrever
    chaves via monkeypatch.setitem."""
    with open(path or PARAMS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)
