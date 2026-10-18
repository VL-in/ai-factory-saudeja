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
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
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


def para_horario_da_clinica(valor: str | datetime) -> datetime:
    """Data/hora de consulta no fuso da clínica, venha de onde vier.

    O Postgres devolve `timestamptz` normalizado em UTC ("...T21:00:00+00:00"
    para uma consulta das 18h em São Paulo). Lido sem conversão, o `horario`
    que o modelo vê fica 3h adiantado (21, que o treino nunca viu), o export
    grava a hora UTC no dataset de treino e o SMS informa o horário errado ao
    paciente (bug corrigido em 2026-09-29). Valor **sem** fuso é tratado
    como já local: é o formato do dataset histórico e dos testes."""
    if isinstance(valor, str):
        convertido = datetime.fromisoformat(valor)
    elif hasattr(valor, "to_pydatetime"):  # pd.Timestamp (subclasse de datetime)
        convertido = valor.to_pydatetime()
    else:
        convertido = valor
    if convertido.tzinfo is None:
        return convertido.replace(tzinfo=fuso_da_clinica())
    return convertido.astimezone(fuso_da_clinica())


_FRACAO_DE_SEGUNDO = re.compile(r"\.(\d{1,6})(?=[+-]|$)")


def ler_timestamptz(valor: str) -> datetime:
    """`timestamptz` no formato que o PostgREST devolve.

    O PostgREST corta os zeros finais da fração de segundo
    ("...T12:00:00.12345+00:00"), e o `fromisoformat` do Python 3.10 só aceita
    3 ou 6 dígitos. Com `now()`, ~1 em cada 10 valores termina em zero, então a
    leitura direta falha só às vezes. A fração é completada antes da leitura.

    Fica aqui, e não em `db.repositories`, porque `consentimento` lê o mesmo
    formato sem banco e não deve importar o cliente do Supabase."""
    return datetime.fromisoformat(
        _FRACAO_DE_SEGUNDO.sub(lambda m: "." + m.group(1).ljust(6, "0"), valor)
    )


RETENCAO_DADOS_DERIVADOS_PADRAO_DIAS = 90


def retencao_dados_derivados_dias() -> int:
    """Janela de retenção de `predicoes` e `mensagens_disparadas`
    (`RETENCAO_DADOS_DERIVADOS_DIAS`, default 90) -- princípio da necessidade
    da LGPD (Art. 6º, III), detalhado em `docs/LGPD/LGPD.md` §5.

    Só dado **derivado**: a probabilidade que o modelo calculou e o registro de
    que uma mensagem foi (ou não) disparada. `pacientes`/`agendamentos` ficam
    de fora de propósito -- o registro do atendimento é da clínica, que é a
    controladora; apagá-lo por conta própria seria a operadora decidindo sobre
    dado que não é dela.

    90 dias: a janela de rastreabilidade e auditoria do modelo. Cobre o
    canário (até 21 dias, mais a espera pelo desfecho; ADR-008) e a maior
    janela do SLO de explicação (30 dias), com folga para investigar um modelo
    depois de promovido. O re-treino não depende destas tabelas: lê o desfecho
    e o lembrete da base de treino (`treino.consultas_treino`), que o job D-2
    sincroniza a partir de `agendamentos`.
    Valor inválido cai no default em vez de levantar --
    mesma escolha de `observabilidade.retencao_dias()`.
    """
    try:
        bruto = os.environ.get("RETENCAO_DADOS_DERIVADOS_DIAS")
        return max(int(bruto or RETENCAO_DADOS_DERIVADOS_PADRAO_DIAS), 1)
    except ValueError:
        return RETENCAO_DADOS_DERIVADOS_PADRAO_DIAS


JANELA_TREINO_PADRAO_DIAS = 90


def janela_treino_dias() -> int:
    """Janela da base de treino (`JANELA_TREINO_DIAS`, default 90; ADR-009 e
    ADR-010): só consultas com até 90 dias entram no export, o job D-2 só
    sincroniza o desfecho delas, e a purga tira da base de treino o que passou
    disso. Um número só para os três, para que nenhum deles trabalhe com uma
    janela que os outros não cobrem. Valor inválido cai no default."""
    try:
        bruto = os.environ.get("JANELA_TREINO_DIAS")
        return max(int(bruto or JANELA_TREINO_PADRAO_DIAS), 1)
    except ValueError:
        return JANELA_TREINO_PADRAO_DIAS


def caminho_de_env(variavel: str, default_relativo: str) -> str:
    """Valor de `variavel` se definida; senão o default, resolvido a partir
    da raiz do repositório em vez do CWD do processo."""
    return os.environ.get(variavel) or str(REPO_ROOT / default_relativo)


def carregar_params(path: str | Path | None = None) -> dict[str, Any]:
    """Lê params.yaml. Os módulos guardam o resultado em um `PARAMS` de
    nível de módulo -- os testes dependem desse nome para sobrescrever
    chaves via monkeypatch.setitem."""
    with open(path or PARAMS_PATH, encoding="utf-8") as f:
        params: dict[str, Any] = yaml.safe_load(f)
    return params
