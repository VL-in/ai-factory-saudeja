"""
SaúdeJá — análise exploratória do dataset externo final (amostra de treino
`consultas-externas.csv`, com o pool como referência estável) contra o
primeiro dataset de treino herdado (a semente, `consultas-historicas.csv`).

Exploratório, como `avaliar_cv.py`: fora do dvc.yaml. Imprime só agregados
(contagens, taxas, PSI) e grava gráficos de agregados em `--saida`. Nenhuma
linha de paciente sai do script.

Uso (na raiz do repo):
    python scripts/eda_dataset_externo.py --saida docs/analises/img
"""

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import contrato_dataset_externo as cde  # noqa: E402
from validate_data import psi  # noqa: E402

# Paleta validada (dataviz, slots 1 e 2): identidade da origem, sempre igual.
COR = {"semente": "#2a78d6", "externa": "#eb6834"}
TEXTO, TEXTO_2, GRADE, FUNDO = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb")
FAIXAS_ANTECEDENCIA = [(1, 2), (3, 7), (8, 14), (15, 30), (31, 60), (61, 90)]
FAIXAS_IDADE = [(0, 12), (13, 17), (18, 29), (30, 44), (45, 59), (60, 85)]


def _rotulo(faixa):
    return f"{faixa[0]}-{faixa[1]}"


def _faixa(valores, faixas):
    rotulos = pd.Series(pd.NA, index=valores.index, dtype=object)
    for f in faixas:
        rotulos[valores.between(*f)] = _rotulo(f)
    return pd.Categorical(rotulos, categories=[_rotulo(f) for f in faixas], ordered=True)


def com_derivadas(df):
    quando = pd.to_datetime(df["data_hora_agendada"])
    return df.assign(
        dia=quando.dt.dayofweek,
        hora=quando.dt.hour,
        faixa_antecedencia=_faixa(df["dias_entre_agendamento_consulta"], FAIXAS_ANTECEDENCIA),
        faixa_idade=_faixa(df["idade"], FAIXAS_IDADE),
        historico=df["historico_noshow"].clip(upper=3),
    )


def wilson(k, n, z=1.96):
    """IC 95% de Wilson para uma taxa: não sai de [0, 1] com n pequeno."""
    if n == 0:
        return np.nan, np.nan
    p = k / n
    centro = (p + z**2 / (2 * n)) / (1 + z**2 / n)
    meia = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / (1 + z**2 / n)
    return centro - meia, centro + meia


def taxa_por(df, coluna):
    g = df.groupby(coluna, observed=False)["no_show"].agg(["sum", "size"])
    linhas = []
    for nivel, (k, n) in g.iterrows():
        inf, sup = wilson(k, n)
        linhas.append(
            {"nivel": nivel, "n": int(n), "taxa": k / n if n else np.nan, "inf": inf, "sup": sup}
        )
    return pd.DataFrame(linhas)


def resumo_geral(df):
    por_paciente = df.groupby("id_paciente")
    return {
        "linhas": len(df),
        "pacientes": int(df["id_paciente"].nunique()),
        "consultas_por_paciente": round(len(df) / df["id_paciente"].nunique(), 2),
        "taxa_falta": round(float(df["no_show"].mean()), 4),
        "idade_mediana": float(df["idade"].median()),
        "antecedencia_mediana": float(df["dias_entre_agendamento_consulta"].median()),
        "historico_zero": round(float((df["historico_noshow"] == 0).mean()), 4),
        "historico_medio": round(float(df["historico_noshow"].mean()), 3),
        "sexo_f": round(float((df["sexo"] == "F").mean()), 4),
        # id de paciente que não identifica uma pessoa: idade ou sexo variando
        "pacientes_inconsistentes": int(
            ((por_paciente["sexo"].nunique() > 1) | (por_paciente["idade"].nunique() > 2)).sum()
        ),
        "datas": f"{df['data_hora_agendada'].min()[:10]} a {df['data_hora_agendada'].max()[:10]}",
    }


def psi_por_feature(semente, outro):
    numericas = ("idade", "distancia_km", "dias_entre_agendamento_consulta", "historico_noshow")
    categoricas = ("sexo", "especialidade", "dia", "hora")
    return {
        f: round(
            psi(
                semente[f].reset_index(drop=True), outro[f].reset_index(drop=True), f in categoricas
            ),
            3,
        )
        for f in (*numericas, *categoricas)
    }


def recorte_sexta(df):
    sexta = df["dia"] == 4
    fim = sexta & (df["hora"] >= 17)
    return {
        "sexta": round(float(df.loc[sexta, "no_show"].mean()), 4),
        "outros_dias": round(float(df.loc[~sexta, "no_show"].mean()), 4),
        "sexta_17h_18h": round(float(df.loc[fim, "no_show"].mean()), 4),
        "resto": round(float(df.loc[~fim, "no_show"].mean()), 4),
        "n_sexta_17h_18h": int(fim.sum()),
    }


def lembrete_por_antecedencia(df):
    """R6: SMS confundido com a antecedência -- só faz sentido estratificado."""
    linhas = []
    for faixa, g in df.groupby("faixa_antecedencia", observed=True):
        com, sem = g[g["lembrete_enviado"] == 1], g[g["lembrete_enviado"] == 0]
        linhas.append(
            {
                "faixa": faixa,
                "n": len(g),
                "taxa_sms": round(float(g["lembrete_enviado"].mean()), 3),
                "falta_com_sms": round(float(com["no_show"].mean()), 3) if len(com) else None,
                "falta_sem_sms": round(float(sem["no_show"].mean()), 3) if len(sem) else None,
            }
        )
    return linhas


# --------------------------------------------------------------------------
# gráficos
# --------------------------------------------------------------------------
def _estilo(ax, titulo):
    ax.set_facecolor(FUNDO)
    ax.set_title(titulo, loc="left", fontsize=11, color=TEXTO, pad=8)
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)
    ax.spines["bottom"].set_color(GRADE)
    ax.tick_params(colors=TEXTO_2, labelsize=9, length=0)
    ax.yaxis.grid(True, color=GRADE, linewidth=0.8)
    ax.set_axisbelow(True)


def grafico_taxas(conjuntos, caminho):
    """Taxa de falta por faixa (pontos com IC de Wilson), semente x externa,
    em pequenos múltiplos. Mesmo eixo y em todos para comparar."""
    paineis = (
        ("faixa_antecedencia", "Antecedência (dias)", None),
        ("faixa_idade", "Idade (anos)", None),
        ("dia", "Dia da semana", DIAS),
        ("historico", "Faltas anteriores (3 = 3 ou mais)", None),
    )
    fig, eixos = plt.subplots(2, 2, figsize=(10, 7), facecolor=FUNDO, sharey=True)
    deslocamento = {"semente": -0.12, "externa": 0.12}
    for ax, (coluna, titulo, nomes) in zip(eixos.flat, paineis, strict=True):
        _estilo(ax, titulo)
        niveis = None
        for origem, df in conjuntos.items():
            t = taxa_por(df, coluna)
            if niveis is None or len(t) > len(niveis):  # união: a externa não tem sábado
                niveis = t["nivel"].tolist()
            x = np.arange(len(t)) + deslocamento[origem]
            erro = np.vstack([t["taxa"] - t["inf"], t["sup"] - t["taxa"]])
            ax.errorbar(
                x,
                t["taxa"],
                yerr=erro,
                fmt="o",
                color=COR[origem],
                markersize=7,
                elinewidth=2,
                capsize=0,
                markeredgecolor=FUNDO,
                markeredgewidth=1.5,
                label=f"{origem} (n={len(df)})",
            )
        ax.set_xticks(np.arange(len(niveis)))
        ax.set_xticklabels(nomes[: len(niveis)] if nomes else [str(n) for n in niveis])
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    eixos[0, 0].set_ylabel("taxa de falta", color=TEXTO_2)
    eixos[1, 0].set_ylabel("taxa de falta", color=TEXTO_2)
    eixos[0, 0].set_ylim(0, 0.85)
    handles, labels = eixos[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", frameon=False, fontsize=9, ncol=2)
    fig.suptitle(
        "Taxa de falta por faixa: semente herdada x amostra externa (IC 95%)",
        x=0.01,
        ha="left",
        fontsize=12,
        color=TEXTO,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(caminho, dpi=130, facecolor=FUNDO)
    plt.close(fig)


def grafico_distribuicoes(conjuntos, caminho):
    """Proporção das linhas por faixa: onde as duas populações diferem."""
    paineis = (
        ("faixa_antecedencia", "Antecedência (dias)", None),
        ("faixa_idade", "Idade (anos)", None),
        ("dia", "Dia da semana", DIAS),
        ("historico", "Faltas anteriores (3 = 3 ou mais)", None),
    )
    fig, eixos = plt.subplots(2, 2, figsize=(10, 7), facecolor=FUNDO, sharey=True)
    largura = 0.38
    for ax, (coluna, titulo, nomes) in zip(eixos.flat, paineis, strict=True):
        _estilo(ax, titulo)
        niveis = None
        for i, (origem, df) in enumerate(conjuntos.items()):
            prop = df[coluna].value_counts(normalize=True, sort=False)
            prop = prop.reindex(range(len(nomes)), fill_value=0) if nomes else prop.sort_index()
            if niveis is None or len(prop) > len(niveis):
                niveis = list(prop.index)
            x = np.arange(len(prop)) + (i - 0.5) * (largura + 0.02)
            ax.bar(
                x,
                prop.to_numpy(),
                width=largura,
                color=COR[origem],
                label=origem,
                edgecolor=FUNDO,
                linewidth=2,
            )
        ax.set_xticks(np.arange(len(niveis)))
        ax.set_xticklabels(list(nomes) if nomes else [str(n) for n in niveis])
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    handles, labels = eixos[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", frameon=False, fontsize=9, ncol=2)
    fig.suptitle(
        "Distribuição das features reais: semente herdada x amostra externa",
        x=0.01,
        ha="left",
        fontsize=12,
        color=TEXTO,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(caminho, dpi=130, facecolor=FUNDO)
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--saida", type=Path, default=None, help="pasta dos gráficos (PNG)")
    parser.add_argument("--json", type=Path, default=None, help="grava os agregados em JSON")
    args = parser.parse_args(argv)

    semente = com_derivadas(pd.read_csv(REPO_ROOT / "data" / "consultas-historicas.csv"))
    amostra = com_derivadas(cde.carregar_adaptado(cde.CAMINHO_AMOSTRA))
    pool = com_derivadas(cde.carregar_adaptado(cde.CAMINHO_POOL))

    resultado = {
        "geral": {
            "semente": resumo_geral(semente),
            "amostra": resumo_geral(amostra),
            "pool": resumo_geral(pool),
        },
        "psi_contra_a_semente": {
            "amostra": psi_por_feature(semente, amostra),
            "pool": psi_por_feature(semente, pool),
        },
        "taxa_por_faixa": {
            coluna: {
                nome: taxa_por(df, coluna).round(4).astype({"nivel": str}).to_dict("records")
                for nome, df in (("semente", semente), ("amostra", amostra), ("pool", pool))
            }
            for coluna in ("faixa_antecedencia", "faixa_idade", "dia", "historico", "sexo")
        },
        "recorte_sexta": {
            "semente": recorte_sexta(semente),
            "pool": recorte_sexta(pool),
        },
        "lembrete_por_antecedencia_pool": lembrete_por_antecedencia(pool),
    }
    print(json.dumps(resultado, indent=2, ensure_ascii=False, default=str))
    if args.json:
        args.json.write_text(
            json.dumps(resultado, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
    if args.saida:
        args.saida.mkdir(parents=True, exist_ok=True)
        conjuntos = {"semente": semente, "externa": amostra}
        grafico_taxas(conjuntos, args.saida / "eda-taxa-de-falta-por-faixa.png")
        grafico_distribuicoes(conjuntos, args.saida / "eda-distribuicao-das-features.png")
    return resultado


if __name__ == "__main__":
    main()
