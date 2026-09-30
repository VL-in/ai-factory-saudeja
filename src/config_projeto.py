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


def para_horario_da_clinica(valor) -> datetime:
    """Data/hora de consulta no fuso da clínica, venha de onde vier.

    O Postgres devolve `timestamptz` normalizado em UTC ("...T21:00:00+00:00"
    para uma consulta das 18h em São Paulo). Lido sem conversão, o `horario`
    que o modelo vê fica 3h adiantado (21, que o treino nunca viu), o export
    grava a hora UTC no dataset de treino e o SMS informa o horário errado ao
    paciente (revisão do Passo 10, 2026-09-29). Valor **sem** fuso é tratado
    como já local: é o formato do dataset histórico e dos testes."""
    if isinstance(valor, str):
        valor = datetime.fromisoformat(valor)
    elif hasattr(valor, "to_pydatetime"):  # pd.Timestamp
        valor = valor.to_pydatetime()
    if valor.tzinfo is None:
        return valor.replace(tzinfo=fuso_da_clinica())
    return valor.astimezone(fuso_da_clinica())


RETENCAO_DADOS_DERIVADOS_PADRAO_DIAS = 365


def retencao_dados_derivados_dias() -> int:
    """Janela de retenção de `predicoes` e `mensagens_disparadas`
    (`RETENCAO_DADOS_DERIVADOS_DIAS`, default 365) -- princípio da necessidade
    da LGPD (Art. 6º, III), detalhado em `docs/LGPD.md` §5.

    Só dado **derivado**: a probabilidade que o modelo calculou e o registro de
    que uma mensagem foi (ou não) disparada. `pacientes`/`agendamentos` ficam
    de fora de propósito -- o registro do atendimento é da clínica, que é a
    controladora; apagá-lo por conta própria seria a operadora decidindo sobre
    dado que não é dela.

    365 dias, e não os 90 de `eventos_app`: a auditoria de disparo de lembrete
    é compromisso do SLA §6, e um ciclo contratual anual é o horizonte em que
    ela pode ser cobrada. Valor inválido cai no default em vez de levantar --
    mesma escolha de `observabilidade.retencao_dias()`.
    """
    try:
        bruto = os.environ.get("RETENCAO_DADOS_DERIVADOS_DIAS")
        return max(int(bruto or RETENCAO_DADOS_DERIVADOS_PADRAO_DIAS), 1)
    except ValueError:
        return RETENCAO_DADOS_DERIVADOS_PADRAO_DIAS


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
