"""
SaúdeJá — gate de promoção do re-treino mensal.

O que este módulo decide: se o modelo recém-treinado (*desafiante*) pode
substituir o que está em produção (*campeão*). A fonte de verdade do campeão
é `data/champion_metrics.json`, **versionado em git** — não uma run do
MLflow. O motivo é operacional, não estético: o `mlflow-server` deste repo
sobe por `docker-compose` só durante o workflow e morre com ele (volumes
efêmeros no runner do GitHub Actions), então uma run "campeã" marcada lá não
sobreviveria de um mês para o outro. Mesmo princípio do
[ADR-006](../docs/adr/adr-006-observabilidade.md): o registro não pode morar
dentro daquilo que ele mede.

**Regressão relativa, não alvo absoluto.** O gate compara `recall_1`/`f1_1`/
`roc_auc` contra o campeão, com as tolerâncias de `params.yaml` (`gate.
tolerancia`, justificadas no SLO §3.1). Os alvos absolutos do SLO §3
(`recall_1` >= 0.75, `f1_1` >= 0.65) **não** são critério de promoção: o
modelo vigente já os viola (0.429 / 0.419) e, se fossem, nada jamais seria
promovido. O gap absoluto é exceção documentada, com decisão prevista
para a validação final do pitch.

**Além da regressão relativa**: um **piso absoluto** de
`roc_auc` (`gate.piso`, contra o efeito catraca de promoções que regridem um
pouco cada) e a **suíte de sanidade** do desafiante (`src/sanidade_modelo.py`:
saída em [0, 1], não constante, SHAP aditivo, política do contrato nos casos
limítrofes). As duas bloqueiam. A **taxa de disparo projetada** no fold de
teste só avisa acima de `gate.taxa_disparo_alerta` (decisão da autora: sem
teto) -- e vai para o resumo e o PR de promoção, porque é o número que liga o
modelo ao custo de mensageria do BRIEFING.

**Cinco desfechos, cinco códigos de saída** (o workflow depende deles):

| Código | Situação | Efeito |
|---|---|---|
| 0 | aprovado | canário aberto em `data/canario/` (ou campeão reescrito, ver abaixo) |
| 1 | bloqueado (regressão, piso, sanidade, já revertido) | nada muda; o anterior segue |
| 2 | nenhum re-treino efetivo | dataset com hash idêntico, nada reexecutou |
| 3 | o pipeline falhou | em geral o `validate_data` barrou o dataset |
| 4 | canário em observação | o re-treino espera o canário ativo ser decidido |

No código 0, o campeão só é reescrito direto no bootstrap (sem campeão para
o canário enfrentar) ou com `canario.habilitado: false`. O workflow faz
`dvc push` + PR nos dois casos.

**Canário (ADR-009).** Aprovar no gate deixou de ser promover:
o fold de teste mede o modelo offline, e o canário mede o mesmo modelo na
fila real, numa fração dela, contra o campeão no mesmo período
(`src/canario.py`). Por isso, com campeão registrado, o código 0 grava o
canário e **não** toca em `champion_metrics.json` nem no `dvc.lock` de
`main`; quem promove é o `canario.yml`, quando os guardrails de produção
confirmam. O código 4 existe porque dois desafiantes ao mesmo tempo
dividiriam a fila em três braços e nenhum teria amostra para decidir.

O código 2 existe porque `RANDOM_STATE`/`TEST_SIZE` são fixos: sem desfecho
novo registrado pela clínica na "Fila do dia", reexecutar o treino reproduziria a
mesma métrica e geraria um `model.pkl` novo — trocando a `model_version` em
produção sem nenhuma mudança real. Por decisão da autora (2026-09-21) esse
caso **falha** o workflow em vez de sair em silêncio: um mês sem desfecho
registrado é um problema de operação da clínica que precisa aparecer, não
uma não-notícia.

**O que este módulo não faz**: `dvc push` e o commit/PR de promoção ficam no
`.github/workflows/retrain.yml`. Aqui não entra credencial de remote nem
token de git — o gate é uma decisão, e por isso é testável sem Azure e sem
GitHub (`tests/test_retrain_gate.py`).

Uso:
    python src/retrain_gate.py                  # ciclo completo (dvc repro + decisão)
    python src/retrain_gate.py --sem-repro      # só decide, contra o que já foi treinado
"""
import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import joblib
import yaml
from mlflow.tracking import MlflowClient

import canario
import inference
import sanidade_modelo
from config_projeto import REPO_ROOT, caminho_de_env, carregar_params, fuso_da_clinica
from inference import calcular_model_version

PARAMS = carregar_params()

CHAMPION_PATH = caminho_de_env("CHAMPION_METRICS_PATH", "data/champion_metrics.json")
RUN_ID_PATH = caminho_de_env("RUN_ID_PATH", "data/interim/mlflow_run_id.txt")
MODEL_PATH = caminho_de_env("MODEL_PATH", "data/model.pkl")
TEST_PATH = caminho_de_env("TEST_PATH", "data/interim/test.pkl")
DATASET_DVC_PATH = caminho_de_env("DATASET_DVC_PATH", "data/consultas-treino.csv.dvc")
RELATORIO_DADOS_PATH = caminho_de_env(
    "RELATORIO_DADOS_PATH", "data/interim/relatorio_dados.json"
)
DVC_LOCK_PATH = caminho_de_env("DVC_LOCK_PATH", "dvc.lock")

# O gate roda no HOST (ou no runner), não dentro dos containers do dvc.yaml --
# os stages apontam para http://mlflow-server:5000 (nome de serviço na rede
# Docker), que não resolve fora dela. O compose publica a 5000 no host, então
# localhost vale tanto na máquina da autora quanto no ubuntu-latest.
MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")

# As três que governam a promoção. Não são todas as que a run loga (accuracy,
# pr_auc, métricas da classe 0 etc. vão para o arquivo do campeão como
# registro), são as três que o SLO §3/§3.1 elegeu como critério.
METRICAS_DO_GATE = ("recall_1", "f1_1", "roc_auc")

SAIDA_PROMOVIDO = 0
SAIDA_BLOQUEADO = 1
SAIDA_SEM_RETREINO = 2
SAIDA_FALHA_PIPELINE = 3
SAIDA_CANARIO_EM_OBSERVACAO = 4

# Acima deste número de positivos no fold de teste, a tolerância de contagem
# de 0.05 deixa de ser justificada pela granularidade da amostra e passa a
# esconder regressões reais (SLO §3.1). O gate não decide sozinho o novo
# número -- avisa, e a revisão é humana.
POSITIVOS_PARA_REVISAR_TOLERANCIA = 50


class ErroGate(RuntimeError):
    """Falha operacional do gate (não é "modelo pior": é o gate sem
    condições de decidir -- run inexistente, métrica ausente, arquivo
    corrompido). Sobe como exceção em vez de virar um código de saída
    próprio, porque um gate que não consegue medir nunca deve promover."""


@dataclass
class Decisao:
    promover: bool
    codigo_saida: int
    motivo: str
    comparacoes: list[dict[str, Any]] = field(default_factory=list)
    bootstrap: bool = False
    avisos: list[str] = field(default_factory=list)
    taxa_disparo_projetada: float | None = None
    canario: bool = False


# --------------------------------------------------------------------------
# leitura
# --------------------------------------------------------------------------
def tolerancias(params: dict[str, Any] | None = None) -> dict[str, float]:
    """Tolerâncias de regressão por métrica, lidas de `params.yaml`.

    Não têm default no código de propósito: um default silencioso é a
    maneira mais fácil de o gate passar a decidir por um número que ninguém
    revisou. Chave ausente é erro."""
    config = (params or PARAMS).get("gate", {}).get("tolerancia", {})
    faltando = [m for m in METRICAS_DO_GATE if m not in config]
    if faltando:
        raise ErroGate(
            f"params.yaml não define gate.tolerancia para {faltando} -- "
            "ver SLO §3.1 para a regra que gera esses números"
        )
    return {m: float(config[m]) for m in METRICAS_DO_GATE}


def pisos(params: dict[str, Any] | None = None) -> dict[str, float]:
    """Pisos absolutos (`gate.piso`). Diferente da tolerância, a
    ausência aqui não é erro: o piso é uma proteção a mais sobre a comparação
    relativa, não o critério principal."""
    config = (params or PARAMS).get("gate", {}).get("piso", {}) or {}
    return {m: float(v) for m, v in config.items()}


def carregar_champion(path: str | None = None) -> dict[str, Any] | None:
    """Métricas do campeão em produção, ou `None` no primeiro ciclo
    (bootstrap). `None` e "arquivo corrompido" são coisas diferentes: o
    segundo levanta erro, porque promover por cima de um campeão ilegível
    seria promover sem comparar."""
    caminho = path or CHAMPION_PATH
    if not os.path.exists(caminho):
        return None
    try:
        with open(caminho, encoding="utf-8") as f:
            champion: dict[str, Any] = json.load(f)
    except json.JSONDecodeError as exc:
        raise ErroGate(f"{caminho} não é JSON válido: {exc}") from exc

    faltando = [m for m in METRICAS_DO_GATE if m not in champion.get("metricas", {})]
    if faltando:
        raise ErroGate(f"{caminho} não tem as métricas {faltando} em 'metricas'")
    return champion


def ler_metricas_da_run(run_id: str, tracking_uri: str | None = None) -> dict[str, float]:
    """Métricas do desafiante, lidas da run que o stage `validate` acabou de
    logar. Erro explícito quando a run não existe -- no runner o MLflow é
    efêmero, e um `run_id` herdado do cache do DVC que aponta para uma run
    que nunca existiu ali é exatamente o tipo de falha que, silenciosa,
    promoveria um modelo sem métrica."""
    client = MlflowClient(tracking_uri=tracking_uri or MLFLOW_TRACKING_URI)
    try:
        run = client.get_run(run_id)
    except Exception as exc:
        raise ErroGate(
            f"run {run_id} não encontrada em {tracking_uri or MLFLOW_TRACKING_URI} -- "
            "o mlflow-server está de pé e foi ele que recebeu este treino?"
        ) from exc

    metricas = dict(run.data.metrics)
    faltando = [m for m in METRICAS_DO_GATE if m not in metricas]
    if faltando:
        raise ErroGate(
            f"run {run_id} não logou {faltando} -- o stage 'validate' rodou até o fim?"
        )
    return metricas


def _ler_run_id(path: str | None = None) -> str | None:
    caminho = path or RUN_ID_PATH
    if not os.path.exists(caminho):
        return None
    with open(caminho) as f:
        return f.read().strip() or None


def positivos_no_fold_de_teste(path: str | None = None) -> int | None:
    """Quantos no-shows existem no fold de teste isolado. É o denominador da
    regra de tolerância do SLO §3.1 -- guardá-lo junto das métricas é o que
    permite auditar depois se 0.05 ainda fazia sentido naquele ciclo."""
    caminho = path or TEST_PATH
    if not os.path.exists(caminho):
        return None
    try:
        return int(joblib.load(caminho)["y"].sum())
    except Exception:
        return None


def _info_dataset() -> dict[str, Any]:
    """Identidade do dataset que gerou o desafiante: o md5 que o DVC
    registra. Sem isso, duas linhas de `champion_metrics.json` com métricas
    diferentes não dizem se o que mudou foi o dado ou o código."""
    info: dict[str, Any] = {"arquivo": os.path.basename(DATASET_DVC_PATH).removesuffix(".dvc")}
    if os.path.exists(DATASET_DVC_PATH):
        with open(DATASET_DVC_PATH, encoding="utf-8") as f:
            outs = (yaml.safe_load(f) or {}).get("outs") or [{}]
        info["md5_dvc"] = outs[0].get("md5")
    positivos = positivos_no_fold_de_teste()
    if positivos is not None:
        info["positivos_no_fold_de_teste"] = positivos
    return info


# --------------------------------------------------------------------------
# decisão (puro -- é o que os testes exercitam sem Docker/MLflow)
# --------------------------------------------------------------------------
def comparar(
    metricas_campeao: dict[str, Any], metricas_desafiante: dict[str, Any], tol: dict[str, float]
) -> list[dict[str, Any]]:
    """Uma linha por métrica do gate. `delta` negativo é piora; só conta
    como regressão o que piora **além** da tolerância daquela métrica."""
    linhas = []
    for metrica in METRICAS_DO_GATE:
        if metrica not in metricas_campeao or metrica not in metricas_desafiante:
            raise ErroGate(f"métrica {metrica} ausente em campeão ou desafiante")
        campeao = float(metricas_campeao[metrica])
        desafiante = float(metricas_desafiante[metrica])
        delta = desafiante - campeao
        linhas.append(
            {
                "metrica": metrica,
                "campeao": campeao,
                "desafiante": desafiante,
                "delta": delta,
                "tolerancia": tol[metrica],
                "regrediu": delta < -tol[metrica],
            }
        )
    return linhas


def abaixo_do_piso(metricas: dict[str, Any], piso: dict[str, float]) -> list[str]:
    return [
        f"{m} {float(metricas[m]):.4f} abaixo do piso absoluto {p}"
        for m, p in piso.items()
        if m in metricas and float(metricas[m]) < p
    ]


def decidir(
    champion: dict[str, Any] | None,
    metricas_desafiante: dict[str, Any],
    tol: dict[str, float] | None = None,
    positivos_no_fold: int | None = None,
    piso: dict[str, float] | None = None,
) -> Decisao:
    """Promove, bloqueia ou registra bootstrap. Função pura: não escreve
    arquivo nem toca em MLflow/DVC.

    O piso vale também no bootstrap: sem campeão para comparar, é a única
    coisa que impede um primeiro modelo perto do acaso de ir para produção."""
    tol = tol or tolerancias()
    piso = pisos() if piso is None else piso
    avisos = _avisos_de_tolerancia(tol, positivos_no_fold)
    furos_de_piso = abaixo_do_piso(metricas_desafiante, piso)

    if champion is None and furos_de_piso:
        return Decisao(
            promover=False,
            codigo_saida=SAIDA_BLOQUEADO,
            motivo=f"bootstrap bloqueado: {'; '.join(furos_de_piso)}",
            avisos=avisos,
        )

    if champion is None:
        return Decisao(
            promover=True,
            codigo_saida=SAIDA_PROMOVIDO,
            motivo=(
                "bootstrap: não havia campeão registrado, o desafiante é promovido sem "
                "comparação (primeiro ciclo do gate)"
            ),
            bootstrap=True,
            avisos=avisos,
        )

    comparacoes = comparar(champion["metricas"], metricas_desafiante, tol)
    regressoes = [c for c in comparacoes if c["regrediu"]]

    if regressoes or furos_de_piso:
        motivos = []
        if regressoes:
            detalhe = ", ".join(
                f"{c['metrica']} {c['campeao']:.4f} -> {c['desafiante']:.4f} "
                f"({c['delta']:+.4f}, tolerância {c['tolerancia']})"
                for c in regressoes
            )
            motivos.append(
                f"regressão além da tolerância em {len(regressoes)} métrica(s): {detalhe}"
            )
        motivos += furos_de_piso
        return Decisao(
            promover=False,
            codigo_saida=SAIDA_BLOQUEADO,
            motivo="; ".join(motivos),
            comparacoes=comparacoes,
            avisos=avisos,
        )

    return Decisao(
        promover=True,
        codigo_saida=SAIDA_PROMOVIDO,
        motivo="nenhuma métrica do gate regrediu além da tolerância",
        comparacoes=comparacoes,
        avisos=avisos,
    )


def _avisos_de_tolerancia(tol: dict[str, float], positivos_no_fold: int | None) -> list[str]:
    """O SLO §3.1 define a tolerância de contagem como ~1/(positivos no
    fold) e manda recalculá-la quando o dataset crescer. Um documento que
    manda revisar não revisa nada -- o gate mede o fold a cada ciclo e
    avisa, sem bloquear (mudar tolerância é decisão humana)."""
    if positivos_no_fold is None or positivos_no_fold < POSITIVOS_PARA_REVISAR_TOLERANCIA:
        return []
    frouxas = [m for m in ("recall_1", "f1_1") if tol[m] > 0.02]
    if not frouxas:
        return []
    return [
        f"o fold de teste tem {positivos_no_fold} positivos (>= "
        f"{POSITIVOS_PARA_REVISAR_TOLERANCIA}): a tolerância de {frouxas} ainda está em "
        f"{ {m: tol[m] for m in frouxas} }, mas 1/{positivos_no_fold} ~= "
        f"{1 / positivos_no_fold:.3f} já cabe em 0.02 -- revisar SLO §3.1 e params.yaml"
    ]


# --------------------------------------------------------------------------
# escrita
# --------------------------------------------------------------------------
def montar_champion(
    metricas: dict[str, Any],
    run_id: str,
    decisao: Decisao,
    model_path: str | None = None,
) -> dict[str, Any]:
    """O conteúdo de `champion_metrics.json`. `metricas` guarda tudo que a
    run logou (accuracy/pr_auc/classe 0 servem ao pitch), mas só
    `METRICAS_DO_GATE` é critério. `decision_threshold` entra porque
    `recall_1`/`f1_1` são medidos a um threshold específico: comparar
    métricas de thresholds diferentes seria comparar outra coisa."""
    caminho_modelo = model_path or MODEL_PATH
    return {
        "_comentario": (
            "Campeão em produção. Reescrito SOMENTE por "
            "src/retrain_gate.py (promoção direta) ou por src/canario.py (promoção "
            "depois do canário, ADR-009). Versionado em git porque o MLflow deste "
            "repo é efêmero e não sobrevive entre execuções do workflow mensal."
        ),
        "model_version": calcular_model_version(caminho_modelo),
        "mlflow_run_id": run_id,
        "promovido_em": datetime.now(tz=fuso_da_clinica()).isoformat(timespec="seconds"),
        "motivo": decisao.motivo,
        "decision_threshold": PARAMS["decision"]["threshold"],
        "taxa_disparo_projetada": decisao.taxa_disparo_projetada,
        "dataset": _info_dataset(),
        "metricas": {k: float(v) for k, v in sorted(metricas.items())},
        "excecao_slo_documentada": (
            "recall_1/f1_1 abaixo dos alvos absolutos do SLO §3 (0.75/0.65). O gate "
            "protege contra regressão relativa; o gap absoluto é limite do dataset "
            "(ADR-003) e será decidido na validação final para o pitch -- não é "
            "critério de promoção (SLO §3.1)."
        ),
    }


def escrever_champion(champion: dict[str, Any], path: str | None = None) -> str:
    caminho = path or CHAMPION_PATH
    # Escrita atômica, mesma razão de src/train.py: este arquivo é lido pelo
    # passo seguinte do workflow (commit/PR) e por quem inspeciona o repo.
    tmp = f"{caminho}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(champion, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, caminho)
    return caminho


def formatar_resumo(
    decisao: Decisao,
    metricas_desafiante: dict[str, Any] | None = None,
    dados: dict[str, Any] | None = None,
) -> str:
    """Markdown para o `$GITHUB_STEP_SUMMARY`. É aqui que a evidência de uma
    rejeição sobrevive ao workflow -- taguear a run no MLflow efêmero não
    serviria, porque ele é destruído junto com o job (ADR-006)."""
    # Sem emoji de propósito: o resumo é impresso no console de quem roda o
    # gate à mão, e o console do Windows em cp1252 levanta UnicodeEncodeError
    # em emoji/"Δ" -- o que transformava o código de saída do gate num
    # traceback (visto na verificação de 2026-09-21). O `_publicar_resumo`
    # ainda protege o caso geral; aqui a escolha é não criar o problema.
    if decisao.codigo_saida == SAIDA_SEM_RETREINO:
        titulo = "sem re-treino efetivo"
    elif decisao.codigo_saida == SAIDA_FALHA_PIPELINE:
        titulo = "pipeline falhou"
    elif decisao.codigo_saida == SAIDA_CANARIO_EM_OBSERVACAO:
        titulo = "canário em observação"
    elif decisao.promover and decisao.canario:
        titulo = "aprovado para canário"
    elif decisao.promover and decisao.bootstrap:
        titulo = "campeão criado (bootstrap)"
    elif decisao.promover:
        titulo = "promovido"
    else:
        titulo = "promoção bloqueada"

    linhas = [f"## Gate de re-treino — {titulo}", "", decisao.motivo, ""]

    if decisao.comparacoes:
        linhas += [
            "| Métrica | Campeão | Desafiante | Delta | Tolerância | Veredito |",
            "|---|---:|---:|---:|---:|:--|",
        ]
        for c in decisao.comparacoes:
            linhas.append(
                f"| `{c['metrica']}` | {c['campeao']:.4f} | {c['desafiante']:.4f} | "
                f"{c['delta']:+.4f} | {c['tolerancia']:.3f} | "
                f"{'**regrediu**' if c['regrediu'] else 'ok'} |"
            )
        linhas.append("")
    elif metricas_desafiante:
        linhas += ["| Métrica | Desafiante |", "|---|---:|"]
        for metrica in METRICAS_DO_GATE:
            linhas.append(f"| `{metrica}` | {metricas_desafiante[metrica]:.4f} |")
        linhas.append("")

    if decisao.taxa_disparo_projetada is not None:
        linhas += [
            f"Taxa de disparo projetada no fold de teste: "
            f"**{decisao.taxa_disparo_projetada:.1%}** dos agendamentos receberiam "
            "lembrete pago no threshold vigente.",
            "",
        ]

    for aviso in decisao.avisos:
        linhas += [f"> **Aviso:** {aviso}", ""]

    if dados:
        linhas.append("**Validação de dados** (`validate_data`):")
        linhas += [f"- **bloqueio:** {b}" for b in dados.get("bloqueios", [])]
        linhas += [f"- alerta: {a}" for a in dados.get("alertas", [])]
        fora = (dados.get("resumo") or {}).get("fora_do_dominio_do_treino")
        if fora:
            linhas.append(f"- fora do domínio do treino (não bloqueia): {fora}")
        if not dados.get("bloqueios") and not dados.get("alertas") and not fora:
            linhas.append("- sem bloqueios nem alertas")
        linhas.append("")

    if decisao.promover and decisao.canario:
        linhas += [
            "O desafiante **não** substituiu o campeão: ele entra como canário "
            "(`data/canario/`) e decide só uma fração da fila do job D-2 até os "
            "guardrails de produção confirmarem a promoção (`canario.yml`, ADR-009). "
            "`data/champion_metrics.json` e o `dvc.lock` de `main` seguem sendo os do "
            "campeão.",
            "",
        ]

    if not decisao.promover and decisao.codigo_saida == SAIDA_BLOQUEADO:
        linhas += [
            "O campeão **não** foi alterado: `data/champion_metrics.json` e o "
            "`model.pkl` publicado seguem sendo os de antes, e nada foi enviado ao "
            "remote. O modelo anterior continua em produção por construção, não por "
            "convenção.",
            "",
        ]
    return "\n".join(linhas)


def _publicar_resumo(texto: str) -> None:
    try:
        print(texto)
    except UnicodeEncodeError:
        # Resumo ilegível é ruim; gate que morre ao IMPRIMIR o resumo é pior --
        # o workflow lê o código de saída, e um traceback aqui apagaria a
        # decisão já tomada. O arquivo do summary é sempre gravado em utf-8.
        codec = sys.stdout.encoding or "ascii"
        print(texto.encode(codec, errors="replace").decode(codec, errors="replace"))
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(texto + "\n")


def _escrever_relatorio(
    caminho: str,
    decisao: Decisao,
    metricas: dict[str, Any] | None,
    dados: dict[str, Any] | None = None,
) -> None:
    """Artifact JSON do run -- o comparativo campeão x desafiante legível por
    máquina, para o caso bloqueado não deixar só um texto no log."""
    relatorio = {
        "promovido": decisao.promover,
        "codigo_saida": decisao.codigo_saida,
        "motivo": decisao.motivo,
        "bootstrap": decisao.bootstrap,
        "avisos": decisao.avisos,
        "comparacoes": decisao.comparacoes,
        "taxa_disparo_projetada": decisao.taxa_disparo_projetada,
        "canario": decisao.canario,
        "metricas_desafiante": {k: float(v) for k, v in sorted((metricas or {}).items())},
        "dados": dados,
    }
    os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(relatorio, f, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------
# orquestração
# --------------------------------------------------------------------------
def ler_relatorio_de_dados(path: str | None = None) -> dict[str, Any] | None:
    """Relatório do stage `validate_data`, se existir. Escrito
    inclusive quando o dataset é barrado -- que é quando ele mais importa."""
    caminho = path or RELATORIO_DADOS_PATH
    if not os.path.exists(caminho):
        return None
    try:
        with open(caminho, encoding="utf-8") as f:
            dados: dict[str, Any] = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return dados


def taxa_de_disparo_projetada(
    model_path: str | None = None, test_path: str | None = None
) -> float | None:
    """Fração do fold de teste que receberia lembrete pago no threshold
    vigente. `None` se o fold ou o modelo não puderem ser lidos -- é um
    número de acompanhamento, não pode derrubar a decisão do gate."""
    try:
        model, _ = inference.carregar_modelo(model_path or MODEL_PATH)
        X = joblib.load(test_path or TEST_PATH)["X"]
    except Exception:
        return None
    threshold = float(PARAMS["decision"]["threshold"])
    return float((inference.predizer(model, X) >= threshold).mean())


def verificar_sanidade(model_path: str | None = None) -> sanidade_modelo.ResultadoSanidade:
    """Ponto único de chamada da suíte (os testes do gate a substituem, porque
    o `model.pkl` deles são bytes quaisquer)."""
    return sanidade_modelo.verificar_modelo(model_path or MODEL_PATH)


def _aplicar_verificacoes_do_desafiante(decisao: Decisao) -> Decisao:
    """Sanidade (bloqueia) e taxa de disparo (avisa), somadas à decisão das
    métricas. Rodam também num ciclo já bloqueado: o resumo fica completo."""
    sanidade = verificar_sanidade()
    decisao.avisos += [f"sanidade: {a}" for a in sanidade.avisos]

    decisao.taxa_disparo_projetada = taxa_de_disparo_projetada()
    limite = float(PARAMS.get("gate", {}).get("taxa_disparo_alerta", 1.0))
    if decisao.taxa_disparo_projetada is not None and decisao.taxa_disparo_projetada > limite:
        decisao.avisos.append(
            f"taxa de disparo projetada {decisao.taxa_disparo_projetada:.1%} acima de "
            f"{limite:.0%} -- o modelo mandaria lembrete pago a mais pacientes do que o "
            "orçamento de mensageria prevê (BRIEFING: cortar 70%)"
        )

    if sanidade.aprovado:
        return decisao
    reprovacao = "suíte de sanidade reprovou o desafiante: " + "; ".join(sanidade.falhas)
    motivo = reprovacao if decisao.promover else f"{decisao.motivo}; {reprovacao}"
    decisao.promover = False
    decisao.codigo_saida = SAIDA_BLOQUEADO
    decisao.bootstrap = False
    decisao.motivo = motivo
    return decisao


def _bloquear_modelo_ja_revertido(decisao: Decisao) -> Decisao:
    """Um modelo revertido num canário não volta como desafiante. Com o
    pipeline determinístico, um mês sem desfecho novo depois do rollback
    reproduziria o mesmo `model.pkl` -- que já passou neste gate uma vez e
    passaria de novo."""
    if not os.path.exists(MODEL_PATH):
        return decisao
    versao = calcular_model_version(MODEL_PATH)
    if versao not in canario.modelos_revertidos():
        return decisao
    bloqueio = (
        f"o desafiante `{versao}` já foi revertido num canário "
        "(data/canario_historico.json) -- falhou nos guardrails de produção"
    )
    decisao.motivo = bloqueio if decisao.promover else f"{decisao.motivo}; {bloqueio}"
    decisao.promover = False
    decisao.bootstrap = False
    decisao.codigo_saida = SAIDA_BLOQUEADO
    return decisao


def _dvc_repro() -> None:
    """Reexecuta o pipeline. Sem `capture_output`: a saída do DVC/Docker vai
    direto para o log do workflow, que é onde se debuga um treino que falhou."""
    print("[gate] dvc repro (pipeline de treino completo)", flush=True)
    # S607: `dvc` vem do PATH de propósito -- é o do ambiente Python ativo
    # (`requirements/dvc.txt`), o mesmo que o workflow e a autora usam. Os
    # argumentos são literais; não há entrada externa no comando.
    subprocess.run(["dvc", "repro"], cwd=REPO_ROOT, check=True)  # noqa: S607


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate de promoção do re-treino mensal")
    parser.add_argument(
        "--sem-repro",
        action="store_true",
        help=(
            "não roda `dvc repro` -- decide contra o que já está treinado no workspace. "
            "Desliga também a detecção de 'nenhum re-treino efetivo', que depende de "
            "comparar o run_id antes e depois do repro."
        ),
    )
    parser.add_argument(
        "--relatorio-json",
        default=None,
        help="onde gravar o comparativo em JSON (artifact do run do Actions)",
    )
    args = parser.parse_args(argv)

    if canario.ativo():
        # Antes do `dvc repro`: um treino que não vai ser usado só gastaria
        # minutos de runner. Falha (como o código 2) porque um canário que
        # chega ao dia do re-treino sem decisão é pendência humana -- PR de
        # promoção ou reversão sem merge, ou canário sem tráfego.
        registro = canario.carregar() or {}
        decisao = Decisao(
            promover=False,
            codigo_saida=SAIDA_CANARIO_EM_OBSERVACAO,
            motivo=(
                f"há um canário em observação (`{registro.get('model_version')}`, desde "
                f"{registro.get('iniciado_em')}): o re-treino espera ele ser promovido ou "
                "revertido. Ver o último run do `canario.yml` e os PRs `canario/*` abertos."
            ),
        )
        _publicar_resumo(formatar_resumo(decisao))
        if args.relatorio_json:
            _escrever_relatorio(args.relatorio_json, decisao, None)
        return decisao.codigo_saida

    # O dvc.lock de `main`, ANTES do repro reescrevê-lo: é a base que a
    # promoção do canário confere para não apagar uma mudança feita depois.
    lock_base = (
        canario.sha256_de_arquivo(DVC_LOCK_PATH) if os.path.exists(DVC_LOCK_PATH) else None
    )

    if args.sem_repro:
        run_id = _ler_run_id()
    else:
        run_id_antes = _ler_run_id()
        try:
            _dvc_repro()
        except subprocess.CalledProcessError as exc:
            return _falha_do_pipeline(exc, args.relatorio_json)
        run_id = _ler_run_id()
        if run_id is not None and run_id == run_id_antes:
            # Nada reexecutou: dataset com o mesmo hash. Com RANDOM_STATE e
            # TEST_SIZE fixos, forçar o treino reproduziria a métrica anterior
            # e ainda geraria um model.pkl novo -- trocando a model_version em
            # produção sem mudança nenhuma. Falha (decisão de 2026-09-21).
            decisao = Decisao(
                promover=False,
                codigo_saida=SAIDA_SEM_RETREINO,
                motivo=(
                    "`dvc repro` não reexecutou o treino: o dataset está com o mesmo "
                    "hash do ciclo anterior (nenhum desfecho novo registrado pela "
                    "clínica na aba 'Fila do dia'). Não há desafiante para "
                    "comparar -- o campeão segue intacto."
                ),
            )
            _publicar_resumo(formatar_resumo(decisao))
            if args.relatorio_json:
                _escrever_relatorio(args.relatorio_json, decisao, None)
            return decisao.codigo_saida

    if not run_id:
        raise ErroGate(
            f"{RUN_ID_PATH} ausente ou vazio -- sem run do MLflow não há métrica do "
            "desafiante para comparar. Rode o pipeline (`dvc repro`) antes."
        )

    metricas = ler_metricas_da_run(run_id)
    champion = carregar_champion()
    decisao = decidir(
        champion, metricas, positivos_no_fold=positivos_no_fold_de_teste()
    )
    decisao = _aplicar_verificacoes_do_desafiante(decisao)
    decisao = _bloquear_modelo_ja_revertido(decisao)

    if (
        decisao.promover
        and champion is not None
        and canario.habilitado(PARAMS)
        and calcular_model_version(MODEL_PATH) == champion.get("model_version")
    ):
        # Mesmo artefato do campeão: não há o que observar num canário, e os
        # dois braços seriam indistinguíveis em `predicoes.model_version`.
        decisao.promover = False
        decisao.codigo_saida = SAIDA_SEM_RETREINO
        decisao.motivo = "o desafiante é byte a byte o campeão -- nada a promover"
    elif decisao.promover and champion is not None and canario.habilitado(PARAMS):
        novo = montar_champion(metricas, run_id, decisao)
        registro = canario.iniciar(
            MODEL_PATH,
            {
                "mlflow_run_id": run_id,
                "motivo": decisao.motivo,
                "decision_threshold": novo["decision_threshold"],
                "taxa_disparo_projetada": novo["taxa_disparo_projetada"],
                "dataset": novo["dataset"],
                "metricas": novo["metricas"],
                "comparacoes": decisao.comparacoes,
            },
            campeao_base=str(champion.get("model_version")),
            dvc_lock_path=DVC_LOCK_PATH,
            dataset_dvc_path=DATASET_DVC_PATH,
            dvc_lock_base_sha256=lock_base,
            params=PARAMS,
        )
        decisao.canario = True
        print(f"[gate] canário {registro['model_version']} criado em {canario.CANARIO_DIR}")
    elif decisao.promover:
        caminho = escrever_champion(montar_champion(metricas, run_id, decisao))
        print(f"[gate] campeão atualizado em {caminho}")

    dados = ler_relatorio_de_dados()
    _publicar_resumo(formatar_resumo(decisao, metricas, dados))
    if args.relatorio_json:
        _escrever_relatorio(args.relatorio_json, decisao, metricas, dados)
    return decisao.codigo_saida


def _falha_do_pipeline(exc: subprocess.CalledProcessError, relatorio_json: str | None) -> int:
    """`dvc repro` não terminou. O caso esperado é o `validate_data` barrando
    o dataset -- aí o relatório dele diz por quê, e vai para o resumo. Sem
    isso o workflow mostraria só um traceback com código 1, indistinguível de
    um bloqueio por regressão."""
    dados = ler_relatorio_de_dados()
    if dados is not None and not dados.get("aprovado", True):
        motivo = "a validação de dados barrou o dataset antes do treino: " + "; ".join(
            dados.get("bloqueios", [])
        )
    else:
        motivo = f"`dvc repro` falhou (código {exc.returncode}) -- ver o log do passo"
    decisao = Decisao(promover=False, codigo_saida=SAIDA_FALHA_PIPELINE, motivo=motivo)
    _publicar_resumo(formatar_resumo(decisao, dados=dados))
    if relatorio_json:
        _escrever_relatorio(relatorio_json, decisao, None, dados)
    return decisao.codigo_saida


if __name__ == "__main__":
    sys.exit(main())
