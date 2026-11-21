"""
SaúdeJá — adaptação do dataset externo de desfecho real (Kaggle
`joniarroba/noshowappointments`, CC BY-NC-SA 4.0, ADR-014) para o formato do
export de treino, como a origem `externa`.

Lê `data/externo/raw/medical-appoint-no-shows2016.csv` e grava, em
`data/externo/` (fora do git, versionado por DVC):

- `pool-adaptado.csv`: a população do SaúdeJá (antecedência de 1 a 90 dias,
  idade de 0 a 85) no formato do export, mais `origem` e `colunas_imputadas`;
- `manifesto.json`: sha256 do bruto, remoções por filtro (em ordem: bruto -
  soma dos filtros = pool), contagens e `random_state`.

Regras (seções 2, 3.1 e 3.2 do ADR-014), cada uma travada por teste em
`tests/test_adaptar_noshow_externo.py`:

- **`historico_noshow` point-in-time**: faltas do paciente em consultas cuja
  data é ANTERIOR à data do agendamento atual (o desfecho já era conhecido
  quando a consulta foi marcada). O Kaggle não tem a hora da consulta, então a
  comparação é por data e estrita: uma falta do mesmo dia do agendamento não
  entra, porque pode ter acontecido depois dele. Calculado sobre as 110 mil
  linhas, ANTES do filtro de população -- uma consulta do mesmo dia que o
  paciente faltou continua sendo histórico real dele.
- **Pseudônimo**: `id_paciente` = "K" + 16 hex do sha256 de sal + PatientId.
  O sal vem de `EXTERNO_SAL_PSEUDONIMO` (.env, fora do git) e não é gravado
  em lugar nenhum.
- **Imputação independente do desfecho**: especialidade e distância
  reamostradas da semente; horário sorteado dos slots da semente do mesmo
  tipo de dia (útil ou sábado, que só tem manhã). Nada disso olha `no_show`.
- **Minimização**: colunas de saúde, `Scholarship` e o bairro não saem do
  bruto. `lembrete_enviado` (o SMS) é exportado, mas não é feature.
- **Contrato de features**: toda linha passa pela mesma quarentena do export
  (`contrato_features.violacoes_do_dataframe`); as recusadas saem e são
  contadas no manifesto.

Antes de gravar, o resultado passa por `contrato_dataset_externo`
(schema/negócio, imputação e fidelidade ao bruto): com violação, nada é
gravado. A saída impressa tem só agregados.

Uso (na raiz do repo):
    python scripts/adaptar_noshow_externo.py
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import contrato_dataset_externo as cde  # noqa: E402
import contrato_features  # noqa: E402
from config_projeto import carregar_params  # noqa: E402  (também carrega o .env)

VERSAO_SCRIPT = "1.0"
VARIAVEL_SAL = "EXTERNO_SAL_PSEUDONIMO"
CAMINHO_SEMENTE = REPO_ROOT / "data" / "consultas-historicas.csv"
DIA_SABADO = 5


# --------------------------------------------------------------------------
# regras
# --------------------------------------------------------------------------
def _data(coluna: pd.Series) -> pd.Series:
    """A data como escrita no bruto, sem converter o `Z` para fuso (mesma
    regra de `contrato_dataset_externo.antecedencia_em_dias`)."""
    return pd.to_datetime(coluna.astype(str).str[:10], format="%Y-%m-%d")


def historico_point_in_time(bruto: pd.DataFrame) -> pd.Series:
    """Para cada linha do bruto: quantas faltas o mesmo paciente teve em
    consultas com data ESTRITAMENTE anterior à data do agendamento desta
    linha. Índice alinhado ao do bruto."""
    falta = (bruto["No-show"] == "Yes").astype(int)
    paciente = bruto["PatientId"].astype(str)
    consultas = (
        pd.DataFrame({"paciente": paciente, "dia": _data(bruto["AppointmentDay"]), "falta": falta})
        .groupby(["paciente", "dia"], as_index=False)["falta"]
        .sum()
    )
    # faltas acumuladas até o fim de cada dia de consulta, por paciente
    consultas["acumulado"] = consultas.groupby("paciente")["falta"].cumsum()
    agendamentos = pd.DataFrame(
        {"paciente": paciente, "dia": _data(bruto["ScheduledDay"]), "_linha": bruto.index}
    )
    juntos = pd.merge_asof(
        agendamentos.sort_values("dia"),
        consultas[["paciente", "dia", "acumulado"]].sort_values("dia"),
        on="dia",
        by="paciente",
        direction="backward",
        allow_exact_matches=False,  # consulta no mesmo dia do agendamento não conta
    )
    return (
        juntos.set_index("_linha")["acumulado"].reindex(bruto.index).fillna(0).astype(int)
    ).rename("historico_noshow")


def pseudonimo(patient_id: str, sal: str) -> str:
    return "K" + hashlib.sha256(f"{sal}{patient_id}".encode()).hexdigest()[:16]


def imputar(
    dias_da_consulta: pd.Series, semente: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """Especialidade, distância e horário sorteados da semente, sem olhar o
    desfecho. O horário vem dos slots da semente do mesmo tipo de dia: a
    marginal fica a da semente, e o sábado só recebe slots da manhã."""
    n = len(dias_da_consulta)
    quando_semente = pd.to_datetime(semente["data_hora_agendada"])
    slots_semente = quando_semente.dt.strftime("%H:%M").to_numpy()
    sabado_semente = (quando_semente.dt.dayofweek == DIA_SABADO).to_numpy()

    dias = pd.to_datetime(dias_da_consulta).reset_index(drop=True)
    sabado = (dias.dt.dayofweek == DIA_SABADO).to_numpy()
    slots = np.empty(n, dtype=object)
    for eh_sabado in (False, True):
        alvo = sabado == eh_sabado
        if alvo.any():
            candidatos = slots_semente[sabado_semente == eh_sabado]
            slots[alvo] = rng.choice(candidatos, size=int(alvo.sum()))

    return pd.DataFrame(
        {
            "especialidade": rng.choice(semente["especialidade"].to_numpy(), size=n),
            "distancia_km": rng.choice(semente["distancia_km"].astype(float).to_numpy(), size=n),
            "data_hora_agendada": [f"{d.date()} {s}:00" for d, s in zip(dias, slots, strict=True)],
        }
    )


def adaptar(
    bruto: pd.DataFrame,
    semente: pd.DataFrame,
    especialidades: list[str],
    sal: str,
    random_state: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """(pool adaptado, remoções por filtro em ordem). Função pura: mesmo
    bruto, semente, sal e random_state dão o mesmo pool."""
    rng = np.random.default_rng(random_state)
    historico = historico_point_in_time(bruto)
    mascara = cde.mascara_populacao(bruto)
    pop = bruto[mascara]
    imputado = imputar(_data(pop["AppointmentDay"]), semente, rng)

    pool = pd.DataFrame(
        {
            "id_consulta": [f"{cde.PREFIXO_CONSULTA}{int(i)}" for i in pop["AppointmentID"]],
            "id_paciente": [pseudonimo(str(p), sal) for p in pop["PatientId"]],
            "idade": pop["Age"].astype(int).to_numpy(),
            "sexo": pop["Gender"].to_numpy(),
            "especialidade": imputado["especialidade"].to_numpy(),
            "distancia_km": imputado["distancia_km"].to_numpy(),
            "dias_entre_agendamento_consulta": cde.antecedencia_em_dias(pop).astype(int).to_numpy(),
            "historico_noshow": historico[mascara].to_numpy(),
            "no_show": (pop["No-show"] == "Yes").astype(int).to_numpy(),
            "data_hora_agendada": imputado["data_hora_agendada"].to_numpy(),
            "lembrete_enviado": pop["SMS_received"].astype(int).to_numpy(),
            cde.COLUNA_ORIGEM: cde.ORIGEM_EXTERNA,
            cde.COLUNA_IMPUTADAS: cde.VALOR_COLUNAS_IMPUTADAS,
        }
    )[list(cde.COLUNAS_ADAPTADO)]

    filtros = cde.remocoes_por_filtro(bruto)
    quarentena = contrato_features.violacoes_do_dataframe(pool, especialidades)
    filtros["quarentena_contrato"] = len(quarentena)
    if quarentena:
        fora = {identificador for identificador, _ in quarentena}
        pool = pool[~pool["id_consulta"].isin(fora)]
    return pool.reset_index(drop=True), filtros


def montar_manifesto(
    pool: pd.DataFrame,
    filtros: dict[str, int],
    sha256_bruto: str,
    random_state: int,
    reservado: pd.DataFrame | None = None,
    amostra: pd.DataFrame | None = None,
) -> dict[str, Any]:
    def _resumo(df: pd.DataFrame | None) -> dict[str, Any]:
        if df is None:
            return {}
        return {
            "linhas": len(df),
            "positivos": int(df["no_show"].sum()),
            "pacientes": int(df["id_paciente"].nunique()),
            "taxa_falta": round(float(df["no_show"].mean()), 4),
        }

    return {
        "versao_script": VERSAO_SCRIPT,
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "licenca": cde.LICENCA,
        "fonte": cde.URL_FONTE,
        "random_state": random_state,
        "bruto": {
            "arquivo": cde.CAMINHO_BRUTO.name,
            "sha256": sha256_bruto,
            "linhas": cde.LINHAS_BRUTO,
        },
        "filtros": filtros,
        "pool": {
            **_resumo(pool),
            "historico_noshow_zero": round(float((pool["historico_noshow"] == 0).mean()), 4),
            "taxa_lembrete": round(float(pool["lembrete_enviado"].mean()), 4),
        },
        "reservado": _resumo(reservado),
        "amostra": _resumo(amostra),
    }


def conferir(
    pool: pd.DataFrame,
    bruto: pd.DataFrame,
    semente: pd.DataFrame,
    especialidades: list[str],
    filtros: dict[str, int],
) -> list[str]:
    """As regras do contrato do dataset externo sobre o pool recém-gerado."""
    return [
        *cde.verificar_adaptado(pool, semente, especialidades),
        *cde.verificar_imputadas(pool, semente),
        *cde.verificar_fidelidade_ao_bruto(pool, bruto, filtros["quarentena_contrato"]),
    ]


# --------------------------------------------------------------------------
# entrada
# --------------------------------------------------------------------------
def ler_sal() -> str:
    sal = os.environ.get(VARIAVEL_SAL, "")
    if len(sal) < 32:
        raise SystemExit(
            f"[erro] {VARIAVEL_SAL} ausente ou curto (mínimo 32 caracteres) -- ver .env.example"
        )
    return sal


def gravar_json(caminho: Path, conteudo: dict[str, Any]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as arquivo:
        json.dump(conteudo, arquivo, indent=2, ensure_ascii=False)
        arquivo.write("\n")


def ler_bruto(caminho: Path) -> pd.DataFrame:
    # PatientId como texto: alguns ids do Kaggle não são inteiros, e o float
    # mudaria a representação usada no pseudônimo.
    return pd.read_csv(caminho, dtype={"PatientId": str})


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bruto", type=Path, default=cde.CAMINHO_BRUTO)
    parser.add_argument("--semente", type=Path, default=CAMINHO_SEMENTE)
    parser.add_argument("--saida", type=Path, default=cde.DIR_EXTERNO)
    args = parser.parse_args(argv)

    params = carregar_params()
    externa = params["dados"]["externa"]
    especialidades = list(params["cadastro"]["especialidades"])
    random_state = int(externa["random_state"])

    bruto = ler_bruto(args.bruto)
    violacoes = cde.verificar_bruto(bruto)
    if violacoes:
        raise SystemExit(f"[erro] bruto fora do contrato: {violacoes}")
    semente = pd.read_csv(args.semente)

    pool, filtros = adaptar(bruto, semente, especialidades, ler_sal(), random_state)
    violacoes = conferir(pool, bruto, semente, especialidades, filtros)
    if violacoes:
        for v in violacoes:
            print(f"  [VIOLACAO] {v}")
        raise SystemExit("[erro] pool fora do contrato -- nada foi gravado")

    manifesto = montar_manifesto(pool, filtros, cde.sha256_do_arquivo(args.bruto), random_state)
    args.saida.mkdir(parents=True, exist_ok=True)
    pool.to_csv(args.saida / cde.CAMINHO_POOL.name, index=False)
    gravar_json(args.saida / cde.CAMINHO_MANIFESTO.name, manifesto)

    p = manifesto["pool"]
    print(
        f"[ok] pool: {p['linhas']} linhas, {p['pacientes']} pacientes, "
        f"falta {p['taxa_falta']:.1%}, "
        f"historico 0 em {p['historico_noshow_zero']:.1%}, lembrete {p['taxa_lembrete']:.1%}"
    )
    print(f"[ok] remocoes por filtro: {filtros}")
    return manifesto


if __name__ == "__main__":
    main()
