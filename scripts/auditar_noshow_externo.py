"""
SaúdeJá — auditoria do bruto do dataset externo de desfecho real (Kaggle
`joniarroba/noshowappointments`, CC BY-NC-SA 4.0, ADR-014).

Imprime **só agregados** (contagens, taxas, faixas): nenhuma linha do bruto
sai na tela nem em arquivo. O dado é público e pseudonimizado, mas continua
sendo dado de paciente -- a mesma regra de `contrato_features` vale aqui.

O que mostra:
- schema e domínios do bruto (`contrato_dataset_externo.verificar_bruto`);
- linhas, pacientes e faixa de datas;
- quantas linhas cada filtro de população remove, na ordem do manifesto
  (`contrato_dataset_externo.remocoes_por_filtro`: os números batem com os do
  `adaptar_noshow_externo.py` por construção);
- taxa de falta e taxa de SMS por faixa de antecedência (o SMS só existe a
  partir de 3 dias: confundimento com a antecedência, risco R6 do ADR);
- pacientes com mais de uma consulta no mesmo dia (vazamento por paciente,
  risco R7);
- dia da semana na população.

Uso (na raiz do repo):
    python scripts/auditar_noshow_externo.py
    python scripts/auditar_noshow_externo.py --bruto caminho/do.csv
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import contrato_dataset_externo as cde  # noqa: E402

# Faixas de antecedência em dias (fechadas): o mesmo dia e o 1-2 (sem SMS)
# separados, porque é onde o mecanismo do Kaggle muda.
FAIXAS_ANTECEDENCIA = (
    ("negativa", -10_000, -1),
    ("0 (mesmo dia)", 0, 0),
    ("1-2", 1, 2),
    ("3-7", 3, 7),
    ("8-14", 8, 14),
    ("15-30", 15, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("> 90", 91, 10_000),
)
DIAS_DA_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sab", "dom")


def auditar(bruto: pd.DataFrame) -> dict:
    """Todos os agregados da auditoria, num dicionário (é o que os testes
    conferem). Nenhum valor de linha individual."""
    falta = (bruto["No-show"] == "Yes").astype(int)
    dias = cde.antecedencia_em_dias(bruto)
    consulta = pd.to_datetime(bruto["AppointmentDay"].astype(str).str[:10], format="%Y-%m-%d")
    populacao = cde.mascara_populacao(bruto)

    por_faixa = {}
    for nome, minimo, maximo in FAIXAS_ANTECEDENCIA:
        m = dias.between(minimo, maximo)
        n = int(m.sum())
        por_faixa[nome] = {
            "linhas": n,
            "taxa_falta": round(float(falta[m].mean()), 4) if n else None,
            "taxa_sms": round(float(bruto.loc[m, "SMS_received"].mean()), 4) if n else None,
        }

    mesmo_dia = bruto.assign(_dia=consulta).groupby(["PatientId", "_dia"]).size()
    pop = bruto[populacao]
    sms_pop = pop["SMS_received"] == 1
    falta_pop = falta[populacao]
    return {
        "violacoes_do_schema": cde.verificar_bruto(bruto),
        "linhas": len(bruto),
        "pacientes": int(bruto["PatientId"].nunique()),
        "consultas_de": str(consulta.min().date()),
        "consultas_ate": str(consulta.max().date()),
        "taxa_falta": round(float(falta.mean()), 4),
        "remocoes_por_filtro": cde.remocoes_por_filtro(bruto),
        "populacao": {
            "linhas": int(populacao.sum()),
            "pacientes": int(pop["PatientId"].nunique()),
            "taxa_falta": round(float(falta_pop.mean()), 4),
            "taxa_sms": round(float(sms_pop.mean()), 4),
            "taxa_falta_com_sms": round(float(falta_pop[sms_pop].mean()), 4),
            "taxa_falta_sem_sms": round(float(falta_pop[~sms_pop].mean()), 4),
            "dia_da_semana": {
                DIAS_DA_SEMANA[d]: int(n)
                for d, n in consulta[populacao].dt.dayofweek.value_counts().sort_index().items()
            },
        },
        "por_faixa_de_antecedencia": por_faixa,
        "pacientes_com_varias_consultas_no_mesmo_dia": int(
            (mesmo_dia > 1).groupby(level=0).any().sum()
        ),
        "consultas_em_dias_com_varias_do_mesmo_paciente": int(mesmo_dia[mesmo_dia > 1].sum()),
    }


def imprimir(resultado: dict, caminho: Path) -> None:
    print(f"[auditoria] {caminho.name}")
    if resultado["violacoes_do_schema"]:
        for v in resultado["violacoes_do_schema"]:
            print(f"  [VIOLACAO] {v}")
    else:
        print("  schema e dominios do bruto: ok")
    print(
        f"  {resultado['linhas']} linhas, {resultado['pacientes']} pacientes, consultas de "
        f"{resultado['consultas_de']} a {resultado['consultas_ate']}, "
        f"falta {resultado['taxa_falta']:.1%}"
    )
    print("\n  remocoes por filtro (em ordem, cada linha conta uma vez):")
    for nome, n in resultado["remocoes_por_filtro"].items():
        print(f"    {nome:<28} {n:>7}")
    p = resultado["populacao"]
    print(
        f"\n  populacao do SaudeJa: {p['linhas']} linhas, {p['pacientes']} pacientes, "
        f"falta {p['taxa_falta']:.1%}, SMS {p['taxa_sms']:.1%} "
        f"(falta com SMS {p['taxa_falta_com_sms']:.1%}, sem SMS {p['taxa_falta_sem_sms']:.1%})"
    )
    print(f"  dia da semana na populacao: {p['dia_da_semana']}")
    print(f"\n  {'antecedencia':<16} {'linhas':>7} {'falta':>7} {'SMS':>7}")
    for nome, f in resultado["por_faixa_de_antecedencia"].items():
        if f["linhas"]:
            print(f"  {nome:<16} {f['linhas']:>7} {f['taxa_falta']:>7.1%} {f['taxa_sms']:>7.1%}")
        else:
            print(f"  {nome:<16} {0:>7} {'-':>7} {'-':>7}")
    print(
        f"\n  pacientes com mais de uma consulta no mesmo dia: "
        f"{resultado['pacientes_com_varias_consultas_no_mesmo_dia']} "
        f"({resultado['consultas_em_dias_com_varias_do_mesmo_paciente']} consultas)"
    )


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bruto", type=Path, default=cde.CAMINHO_BRUTO)
    args = parser.parse_args(argv)
    bruto = pd.read_csv(args.bruto)
    resultado = auditar(bruto)
    print(f"  sha256 {cde.sha256_do_arquivo(args.bruto)}")
    imprimir(resultado, args.bruto)
    return resultado


if __name__ == "__main__":
    main()
