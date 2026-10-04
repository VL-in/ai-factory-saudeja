"""
SaúdeJá — alerta ativo sobre a camada interna de observabilidade ([ADR-006]).

O ADR-006 listou como contra que "não há alerta ativo sobre latência ou taxa
de erro": a degradação só aparecia se alguém abrisse a aba "Observabilidade".
Este módulo fecha esse buraco sem infraestrutura nova. O
`.github/workflows/alerta_observabilidade.yml` roda uma vez por dia, depois do
job D-2 e do canário, lê a janela das últimas 24h de `eventos_app` e
`predicoes` e **falha o workflow** quando um limite é violado. Falha de
workflow é o canal de alerta que o projeto já usa (e-mail nativo do GitHub e
`/fail` do Healthchecks), então nenhum canal novo entra.

Três etapas, separadas para serem testáveis sem banco:

1. `medir` lê o Supabase e devolve `Medicoes`, só com números.
2. `avaliar` decide quais números violam o SLO e devolve uma linha por alerta.
3. `publicar` escreve as anotações e o `$GITHUB_STEP_SUMMARY`.

LGPD: as linhas lidas de `eventos_app` já passaram pela allowlist de
`src/observabilidade.py`, e daqui só saem contadores, percentis e nomes de
classe de exceção. O log do Actions deste repositório é público.

Uso:
    python src/alerta_observabilidade.py [--janela-horas 24]
"""
import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import observabilidade  # noqa: E402

# Mesmo teto da aba "Observabilidade" (src/ui/logic.py): acima dele, o p95
# seria o do pedaço mais recente da janela, e `truncado` avisa isso.
LIMITE_EVENTOS = 5000

# Limites do alerta. Só entram compromissos do docs/SLO.md: um e-mail de falha
# por algo que o SLO não promete ensina a ignorar o e-mail.
#
# SLO §4: 100% das predições expostas à clínica com explicação.
COBERTURA_MINIMA_EXPLICACAO = 1.0
# SLO §1: quarentena "0 no dia típico". Cada agendamento em quarentena e cada
# 5xx da API viram um evento com status `erro`, e cada quarentena é um
# paciente que recebeu SMS sem predição (lembrete por exceção), ou seja, custo.
# Com o volume de uma clínica, um evento já é sinal, não ruído.
ERROS_TOLERADOS = 0
# SLO §2: o job processa a fila em segundos e precisa terminar antes das 09h.
# `duracao_ms` não inclui o atraso do cron do GitHub, que é o que ocupa a folga
# de ~40 min. O limite fica na metade do `timeout-minutes: 20` do job_d2.yml,
# para avisar antes de o GitHub matar o job.
DURACAO_MAXIMA_JOB_D2_MS = 10 * 60 * 1000
# Fica de fora, de propósito, o p95 de `api`/`processo`. Desde a revisão de 2026-09-30
# ele é indicador de desenvolvimento (SLO §2), visível no resumo, sem alerta.


@dataclass(frozen=True)
class Medicoes:
    """O que a janela mostrou. `None` quer dizer "não houve o que medir", que
    é diferente de zero: sem predição na janela, a cobertura de explicação não
    é 0%, ela não existe."""

    janela_horas: int
    predicoes_por_origem: dict[str, int] = field(default_factory=dict)
    p95_ms_por_origem: dict[str, float | None] = field(default_factory=dict)
    erros: int = 0
    excecoes: dict[str, int] = field(default_factory=dict)
    execucoes_job_d2: int = 0
    duracao_max_job_d2_ms: int | None = None
    predicoes_gravadas: int = 0
    predicoes_com_explicacao: int = 0
    truncado: bool = False

    @property
    def cobertura_explicacao(self) -> float | None:
        if not self.predicoes_gravadas:
            return None
        return self.predicoes_com_explicacao / self.predicoes_gravadas


def medir(janela_horas: int = 24) -> Medicoes:
    # Import tardio: os testes de `avaliar`/`publicar` não precisam do
    # supabase-py, e o módulo continua importável sem ele.
    import db.repositories as repositories

    desde = observabilidade.inicio_da_janela(janela_horas)
    eventos = repositories.buscar_eventos_app(desde, limite=LIMITE_EVENTOS)
    gravadas, com_explicacao = repositories.contar_predicoes_e_explicacoes(desde)
    return medicoes_dos_eventos(eventos, janela_horas, gravadas, com_explicacao)


def medicoes_dos_eventos(
    eventos: list[dict],
    janela_horas: int,
    predicoes_gravadas: int,
    predicoes_com_explicacao: int,
) -> Medicoes:
    predicoes = [e for e in eventos if e["tipo"] == observabilidade.TIPO_PREDICAO]
    origens = sorted({e["origem"] for e in predicoes})
    jobs = [e for e in eventos if e["tipo"] == observabilidade.TIPO_JOB_D2]
    duracoes_job = [e["duracao_ms"] for e in jobs if e["duracao_ms"] is not None]
    com_erro = [e for e in eventos if e["status"] == observabilidade.STATUS_ERRO]
    return Medicoes(
        janela_horas=janela_horas,
        predicoes_por_origem={o: sum(1 for e in predicoes if e["origem"] == o) for o in origens},
        p95_ms_por_origem={
            o: observabilidade.percentil(
                (e["duracao_ms"] for e in predicoes if e["origem"] == o), 95
            )
            for o in origens
        },
        erros=len(com_erro),
        excecoes=dict(
            Counter((e.get("detalhe") or {}).get("excecao", "sem_classe") for e in com_erro)
        ),
        execucoes_job_d2=len(jobs),
        duracao_max_job_d2_ms=max(duracoes_job) if duracoes_job else None,
        predicoes_gravadas=predicoes_gravadas,
        predicoes_com_explicacao=predicoes_com_explicacao,
        truncado=len(eventos) >= LIMITE_EVENTOS,
    )


def avaliar(m: Medicoes) -> list[str]:
    """Uma linha por limite violado; lista vazia quando a janela está
    saudável. Cada linha vira um `::error::` no run e um item do resumo, então
    precisa dizer o número medido e o limite que ele ultrapassou."""
    alertas = []
    cobertura = m.cobertura_explicacao
    # `None` (nenhuma predição na janela) não viola o §4: não houve o que explicar.
    if cobertura is not None and cobertura < COBERTURA_MINIMA_EXPLICACAO:
        alertas.append(
            f"cobertura de explicação em {cobertura:.1%} "
            f"({m.predicoes_com_explicacao}/{m.predicoes_gravadas}); SLO §4 exige 100%"
        )
    if m.erros > ERROS_TOLERADOS:
        classes = ", ".join(f"{nome}={n}" for nome, n in sorted(m.excecoes.items()))
        alertas.append(
            f"{m.erros} evento(s) com erro em {m.janela_horas}h ({classes}); "
            f"tolerado: {ERROS_TOLERADOS}"
        )
    # Sem execução registrada: ou o job não rodou (o Healthchecks do D-2
    # também avisa), ou rodou sem conseguir gravar em `eventos_app`, e então
    # a aba "Observabilidade" está cega. Os dois casos pedem atenção.
    if m.execucoes_job_d2 == 0:
        alertas.append(f"nenhuma execução do job D-2 registrada em {m.janela_horas}h")
    if m.duracao_max_job_d2_ms is not None and m.duracao_max_job_d2_ms > DURACAO_MAXIMA_JOB_D2_MS:
        alertas.append(
            f"job D-2 levou {m.duracao_max_job_d2_ms / 60_000:.1f} min; "
            f"limite: {DURACAO_MAXIMA_JOB_D2_MS / 60_000:.0f} min (timeout do workflow: 20)"
        )
    return alertas


def publicar(m: Medicoes, alertas: list[str]) -> None:
    """Anotações do Actions e `$GITHUB_STEP_SUMMARY`. No terminal, as linhas
    `::error::` servem de leitura."""
    for alerta in alertas:
        print(f"::error::{alerta}")
    if m.truncado:
        print(
            f"::warning::janela truncada em {LIMITE_EVENTOS} eventos -- p95 do trecho mais recente"
        )
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destino:
        return

    def fmt(valor: float | None, sufixo: str = "") -> str:
        return "n/d" if valor is None else f"{valor:.0f}{sufixo}"

    cobertura = m.cobertura_explicacao
    linhas = [
        f"### Observabilidade das últimas {m.janela_horas}h — "
        + ("**com alerta**" if alertas else "saudável"),
        "",
        "| Medida | Valor |",
        "|---|---:|",
        *(
            f"| predições `{o}` (p95) | {n} ({fmt(m.p95_ms_por_origem.get(o), ' ms')}) |"
            for o, n in m.predicoes_por_origem.items()
        ),
        f"| eventos com erro | {m.erros} |",
        f"| execuções do job D-2 | {m.execucoes_job_d2} |",
        f"| maior duração do job D-2 | {fmt(m.duracao_max_job_d2_ms, ' ms')} |",
        f"| cobertura de explicação (SLO §4) | "
        f"{'n/d' if cobertura is None else f'{cobertura:.1%}'} "
        f"({m.predicoes_com_explicacao}/{m.predicoes_gravadas}) |",
        "",
        *(f"- exceção `{nome}`: {n}" for nome, n in sorted(m.excecoes.items())),
        *(f"- **alerta:** {a}" for a in alertas),
    ]
    with open(destino, "a", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--janela-horas", type=int, default=24)
    args = parser.parse_args(argv)
    try:
        medicoes = medir(args.janela_horas)
    except Exception as exc:
        # Sem medir não há como afirmar que está saudável: falhar é o
        # conservador. Só o nome da classe, como em `eventos_app`.
        print(f"::error::não foi possível ler a observabilidade ({type(exc).__name__})")
        return 1
    alertas = avaliar(medicoes)
    publicar(medicoes, alertas)
    return 1 if alertas else 0


if __name__ == "__main__":
    sys.exit(main())
