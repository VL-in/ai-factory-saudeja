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
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]

# Carrega .env (se existir) para os.environ -- não sobrescreve variáveis já
# definidas no ambiente (default do python-dotenv), então docker-compose
# `environment:`/secrets do HF Space continuam tendo precedência sobre o
# .env local. Módulos que leem SUPABASE_URL/SUPABASE_SECRET_KEY (src/db/)
# dependem disso: sem load_dotenv, .env.example documentaria variáveis que
# nunca chegam ao processo fora de Docker.
load_dotenv(REPO_ROOT / ".env")

PARAMS_PATH = Path(os.environ.get("PARAMS_PATH") or REPO_ROOT / "params.yaml")


def fuso_da_clinica() -> ZoneInfo:
    """Fuso em que a clínica raciocina sobre datas. Lido a cada chamada (não
    fixado no import) para os testes conseguirem simular outra região."""
    return ZoneInfo(os.environ.get("TIMEZONE_CLINICA") or "America/Sao_Paulo")


def hoje_na_clinica() -> date:
    """Data civil de hoje no fuso da clínica -- não `date.today()`, que segue
    o fuso do processo. O container do HF Space roda em UTC: depois das 21h
    em São Paulo, `date.today()` lá já é o dia seguinte, e a "fila do dia"
    do funcionário mostraria o dia errado."""
    return datetime.now(tz=fuso_da_clinica()).date()


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
