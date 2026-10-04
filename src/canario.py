"""
SaúdeJá — canário do modelo com rollback automático (ADR-009).

O gate do re-treino (`src/retrain_gate.py`) mede o desafiante **offline**, no
fold de teste isolado. Este módulo cobre o que o fold não mede: como o modelo
se comporta **na fila real**, contra os pacientes de hoje, decidindo quem
recebe lembrete pago. Em vez de substituir o campeão de uma vez, o desafiante
aprovado pelo gate vira **canário**: decide uma fração da fila do job D-2
(`canario.fracao`), o campeão decide o resto, e os dois são comparados no
mesmo período.

**Onde o canário roda -- e onde não roda.** Só no job D-2, no runner do
GitHub Actions. A inferência que importa é a batch (a UI só lê predições gravadas), e
dividir a fila dentro do job dispensa roteador, proxy ou segundo Space: o
`data/model.pkl` continua sendo o campeão em toda parte (deploy, Space, CI,
guarda do `src/campeao.py`), e o canário vive em `data/canario/`, que o
staging do deploy não copia.

**Unidade de divisão: o paciente** (`pacientes.id_paciente_externo`), por
hash com a `model_version` do canário como semente. Estável durante o canário
-- o mesmo paciente não alterna de modelo entre uma consulta e outra -- e
renovada a cada canário, para não serem sempre os mesmos pacientes a receber
o modelo em observação. Quando o produto tiver a entidade clínica, a unidade
pode passar a ser ela (ADR-009); `braco()` recebe a unidade como texto.

**Guardrails, sem fold de teste** -- medidos no banco, por braço
(`predicoes.model_version`), desde o início do canário:

- `taxa_disparo`: fração da fila mandada para lembrete pago. É o custo do
  BRIEFING ("cortar 70%");
- `falta_nao_avisada`: entre os pacientes que o modelo classificou como baixo
  risco (não receberam lembrete) e cuja consulta já tem desfecho, a fração
  que faltou. É o erro que custa R$ 180, e o único desfecho que o SMS não
  contamina: quem recebeu lembrete teve o desfecho alterado pela intervenção;
- `quarentena` (por execução, no próprio job): fração da fila que o modelo
  não conseguiu predizer. Modelo novo com mapa de especialidade diferente,
  por exemplo, mandaria a especialidade inteira para o lembrete sem predição.

Cada guardrail compara canário e campeão com **margem de não-inferioridade**
(`canario.margem`) e teste de diferença de proporções (Agresti-Caffo, z
unilateral): *violado* se o canário é pior que o campeão além da margem com
significância; *não inferior* se é estatisticamente pior por menos que a
margem. Promover exige não-inferioridade em todos, amostra e dias mínimos;
qualquer violação reverte. Sem evidência até `dias_maximos`, reverte também
-- o conservador é ficar com o campeão.

**Rollback em três camadas**, da mais rápida para a mais formal:

1. **automático, no job**: a pré-checagem do canário avalia os guardrails
   antes de rotear a fila; violação grava `canarios_revertidos` no banco e a
   fila do dia vai 100% para o campeão, já nessa execução;
2. **manual, instantâneo**: a variável `CANARIO_DESLIGADO` do repositório faz
   o job ignorar o canário, sem PR nem deploy;
3. **formal, em git**: o `canario.yml` abre o PR que remove `data/canario/` e
   registra a decisão em `data/canario_historico.json` -- que o gate lê para
   nunca mais aprovar como canário um modelo já revertido.

Uso (o `canario.yml` chama estes comandos):
    python src/canario.py status
    python src/canario.py verificar            # CI: canário ativo é consistente
    python src/canario.py avaliar              # decide aguardar/promover/reverter
    python src/canario.py promover             # reescreve o campeão a partir do canário
    python src/canario.py reverter --motivo "..."
"""
import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import campeao
from config_projeto import caminho_de_env, carregar_params, fuso_da_clinica

# Lidos em tempo de chamada (não capturados em default de argumento): os
# testes apontam tudo para `tmp_path` trocando estes atributos do módulo.
CANARIO_PATH = caminho_de_env("CANARIO_PATH", "data/canario.json")
CANARIO_DIR = caminho_de_env("CANARIO_DIR", "data/canario")
HISTORICO_PATH = caminho_de_env("CANARIO_HISTORICO_PATH", "data/canario_historico.json")
CHAMPION_PATH = caminho_de_env("CHAMPION_METRICS_PATH", "data/champion_metrics.json")
DVC_LOCK_PATH = caminho_de_env("DVC_LOCK_PATH", "dvc.lock")
DATASET_DVC_PATH = caminho_de_env("DATASET_DVC_PATH", "data/consultas-treino.csv.dvc")

# Nomes dentro de CANARIO_DIR. As cópias do lock e do .dvc do dataset NÃO
# terminam em `.dvc`/`dvc.lock`: o DVC coleta todo `*.dvc` do repositório, e
# uma cópia com essa extensão declararia um out fantasma em data/canario/.
MODELO = "model.pkl"
LOCK_SALVO = "dvc.lock.salvo"
DATASET_DVC_SALVO = "consultas-treino.csv.dvc.salvo"

ACAO_AGUARDAR = "aguardar"
ACAO_PROMOVER = "promover"
ACAO_REVERTER = "reverter"

SITUACAO_AMOSTRA_INSUFICIENTE = "amostra_insuficiente"
SITUACAO_VIOLADO = "violado"
SITUACAO_NAO_INFERIOR = "nao_inferior"
SITUACAO_INCONCLUSIVO = "inconclusivo"

# O que o job e o `avaliar` comparam no banco: (numerador, denominador) dentro
# de `Estatisticas`. A quarentena não está aqui -- agendamento em quarentena
# não gera linha em `predicoes`, então ela só é medida por execução.
GUARDRAILS_DO_BANCO = {
    "taxa_disparo": ("disparos", "predicoes"),
    "falta_nao_avisada": ("faltas_baixo_risco", "desfechos_baixo_risco"),
}

CHAVES_DE_CONFIG = (
    "fracao",
    "dias_minimos",
    "dias_maximos",
    "predicoes_minimas",
    "desfechos_minimos",
    "amostra_minima_por_braco",
    "z_critico",
    "margem",
)
MARGENS = ("taxa_disparo", "falta_nao_avisada", "quarentena")


class ErroCanario(RuntimeError):
    """O canário está num estado em que não dá para decidir ou agir com
    segurança (arquivo ausente, campeão trocado no meio, lock divergente).
    Ninguém deve promover nem rotear tráfego por cima disso."""


# --------------------------------------------------------------------------
# configuração e arquivos
# --------------------------------------------------------------------------
def habilitado(params: dict[str, Any] | None = None) -> bool:
    """Se o gate deve abrir canário ao aprovar um desafiante. Sem o bloco
    `canario` em `params.yaml`, o comportamento é o anterior ao canário:
    promoção direta."""
    bloco = (params if params is not None else carregar_params()).get("canario") or {}
    return bool(bloco.get("habilitado", False))


def ler_config(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parâmetros do canário, sem default no código -- mesmo critério das
    tolerâncias do gate: um default silencioso é a maneira mais fácil de a
    decisão passar a depender de um número que ninguém revisou."""
    bloco = (params if params is not None else carregar_params()).get("canario") or {}
    faltando = [c for c in CHAVES_DE_CONFIG if c not in bloco]
    faltando += [f"margem.{m}" for m in MARGENS if m not in (bloco.get("margem") or {})]
    if faltando:
        raise ErroCanario(f"params.yaml não define canario.{faltando}")
    fracao = float(bloco["fracao"])
    if not 0.0 < fracao < 1.0:
        raise ErroCanario(f"canario.fracao={fracao} fora de (0, 1)")
    return {
        "fracao": fracao,
        "dias_minimos": int(bloco["dias_minimos"]),
        "dias_maximos": int(bloco["dias_maximos"]),
        "predicoes_minimas": int(bloco["predicoes_minimas"]),
        "desfechos_minimos": int(bloco["desfechos_minimos"]),
        "amostra_minima_por_braco": int(bloco["amostra_minima_por_braco"]),
        "z_critico": float(bloco["z_critico"]),
        "margem": {m: float(bloco["margem"][m]) for m in MARGENS},
    }


def _dir() -> Path:
    return Path(CANARIO_DIR)


def caminho_do_modelo() -> Path:
    return _dir() / MODELO


def ativo() -> bool:
    return Path(CANARIO_PATH).exists()


def carregar() -> dict[str, Any] | None:
    caminho = Path(CANARIO_PATH)
    if not caminho.exists():
        return None
    with open(caminho, encoding="utf-8") as f:
        registro: dict[str, Any] = json.load(f)
    return registro


def sha256_de_arquivo(path: str | Path) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _escrever_json(caminho: str | Path, conteudo: dict[str, Any]) -> None:
    # Atômica, como a do campeão em src/retrain_gate.py: o arquivo é lido pelo
    # passo seguinte do workflow (commit/PR) e pelo job do dia seguinte.
    tmp = f"{caminho}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(conteudo, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, caminho)


def carregar_historico() -> dict[str, Any]:
    caminho = Path(HISTORICO_PATH)
    if not caminho.exists():
        return {
            "_comentario": (
                "Histórico dos canários (ADR-009): cada canário encerrado, "
                "promovido ou revertido. Reescrito só por src/canario.py. O gate de "
                "re-treino lê os revertidos para nunca reabrir canário com um modelo que "
                "já falhou em produção."
            ),
            "canarios": [],
        }
    with open(caminho, encoding="utf-8") as f:
        historico: dict[str, Any] = json.load(f)
    return historico


def modelos_revertidos() -> set[str]:
    """`model_version` de todo canário já revertido. Sem esta lista, um mês
    sem desfecho novo depois de um rollback reproduziria o mesmo modelo
    (pipeline determinístico), o gate o aprovaria de novo -- já tinha aprovado
    uma vez -- e o canário revertido voltaria à fila."""
    return {
        c["model_version"]
        for c in carregar_historico().get("canarios", [])
        if c.get("decisao") == "revertido"
    }


def _registrar_no_historico(entrada: dict[str, Any]) -> None:
    historico = carregar_historico()
    historico.setdefault("canarios", []).append(entrada)
    _escrever_json(HISTORICO_PATH, historico)


def _remover_arquivos_do_canario() -> None:
    Path(CANARIO_PATH).unlink(missing_ok=True)
    shutil.rmtree(_dir(), ignore_errors=True)


def _agora() -> datetime:
    return datetime.now(tz=fuso_da_clinica())


# --------------------------------------------------------------------------
# ciclo de vida: iniciar (gate), promover e reverter (canario.yml)
# --------------------------------------------------------------------------
def iniciar(
    model_path: str | Path,
    registro: dict[str, Any],
    *,
    campeao_base: str,
    dvc_lock_path: str | Path,
    dataset_dvc_path: str | Path,
    dvc_lock_base_sha256: str | None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Chamado pelo gate quando aprova um desafiante e já existe campeão.

    Grava em `data/canario/` a cópia do modelo (o workflow faz `dvc add` dela)
    e as cópias do `dvc.lock` e do `.dvc` do dataset **deste** treino. O PR do
    canário não leva o `dvc.lock` para a raiz: em `main`, o lock continua
    descrevendo o campeão -- é ele que o `dvc pull data/model.pkl` do deploy,
    do CI e do job lê. As cópias voltam para a raiz só na promoção.

    `dvc_lock_base_sha256` é o hash do `dvc.lock` de `main` antes do treino. A
    promoção se recusa se ele mudou nesse meio-tempo: restaurar o lock salvo
    por cima apagaria a mudança de outra pessoa."""
    cfg = ler_config(params)
    versao = campeao.calcular_model_version(model_path)
    if versao == campeao_base:
        # Os dois braços teriam a mesma `model_version` em `predicoes`, e a
        # comparação mediria o campeão contra ele mesmo.
        raise ErroCanario(f"o desafiante {versao} é byte a byte o campeão")
    destino = _dir()
    destino.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(model_path, destino / MODELO)
    shutil.copyfile(dvc_lock_path, destino / LOCK_SALVO)
    if Path(dataset_dvc_path).exists():
        shutil.copyfile(dataset_dvc_path, destino / DATASET_DVC_SALVO)

    canario = {
        "_comentario": (
            "Canário em observação (ADR-009). Existe só enquanto o canário "
            "está ativo: o job D-2 manda canario.fracao da fila para este modelo e o "
            "resto para o campeão. Criado por src/retrain_gate.py; removido por "
            "src/canario.py ao promover ou reverter."
        ),
        **registro,
        "model_version": versao,
        "iniciado_em": _agora().isoformat(timespec="seconds"),
        "campeao_base": campeao_base,
        "dvc_lock_base_sha256": dvc_lock_base_sha256,
        "fracao": cfg["fracao"],
    }
    _escrever_json(CANARIO_PATH, canario)
    return canario


def _exigir_canario() -> dict[str, Any]:
    registro = carregar()
    if registro is None:
        raise ErroCanario(f"nenhum canário ativo ({CANARIO_PATH} não existe)")
    return registro


def promover(evidencia: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reescreve o campeão com o canário e devolve o novo campeão.

    O que muda em git: `champion_metrics.json`, o `dvc.lock` e o `.dvc` do
    dataset (restaurados das cópias salvas no início), `canario_historico.json`
    e a remoção de `data/canario/`. O workflow confere em seguida, com
    `dvc pull data/model.pkl` + `src/campeao.py`, que o lock restaurado entrega
    exatamente o modelo que o canário rodou."""
    registro = _exigir_canario()
    campeao_atual = campeao.carregar_campeao(CHAMPION_PATH)
    if registro.get("campeao_base") != campeao_atual.get("model_version"):
        raise ErroCanario(
            f"o canário foi aprovado contra o campeão {registro.get('campeao_base')}, mas o "
            f"campeão agora é {campeao_atual.get('model_version')} -- as métricas do canário "
            "não descrevem a comparação com o modelo em produção"
        )
    base = registro.get("dvc_lock_base_sha256")
    if base and sha256_de_arquivo(DVC_LOCK_PATH) != base:
        raise ErroCanario(
            "o dvc.lock de main mudou depois que o canário começou -- restaurar o lock do "
            "treino do canário apagaria essa mudança. Reverter o canário e re-treinar."
        )

    lock_salvo = _dir() / LOCK_SALVO
    if not lock_salvo.exists():
        raise ErroCanario(f"{lock_salvo} ausente: sem ele o model.pkl promovido não é alcançável")

    agora = _agora().isoformat(timespec="seconds")
    novo = {
        "_comentario": (
            "Campeão em produção. Reescrito SOMENTE por src/retrain_gate.py "
            "(promoção direta) ou por src/canario.py (promoção depois do canário, "
            "ADR-009). Versionado em git porque o MLflow deste repo é efêmero e não "
            "sobrevive entre execuções do workflow mensal."
        ),
        "model_version": registro["model_version"],
        "mlflow_run_id": registro.get("mlflow_run_id"),
        "promovido_em": agora,
        "motivo": (
            f"promovido depois do canário ({registro.get('iniciado_em')} a {agora}, "
            f"{registro.get('fracao', 0):.0%} da fila): guardrails de produção não "
            f"inferiores ao campeão {registro.get('campeao_base')}. Gate offline: "
            f"{registro.get('motivo', '')}"
        ),
        "decision_threshold": registro["decision_threshold"],
        "taxa_disparo_projetada": registro.get("taxa_disparo_projetada"),
        "dataset": registro.get("dataset", {}),
        "metricas": registro["metricas"],
        "canario": {
            "iniciado_em": registro.get("iniciado_em"),
            "encerrado_em": agora,
            "fracao": registro.get("fracao"),
            "campeao_anterior": registro.get("campeao_base"),
            "evidencia": evidencia,
        },
        "excecao_slo_documentada": (
            "recall_1/f1_1 abaixo dos alvos absolutos do SLO §3 (0.75/0.65). O gate "
            "protege contra regressão relativa; o gap absoluto é limite do dataset "
            "(ADR-003) e será decidido na validação final para o pitch -- não é "
            "critério de promoção (SLO §3.1)."
        ),
    }
    _escrever_json(CHAMPION_PATH, novo)
    shutil.copyfile(lock_salvo, DVC_LOCK_PATH)
    dataset_salvo = _dir() / DATASET_DVC_SALVO
    if dataset_salvo.exists():
        shutil.copyfile(dataset_salvo, DATASET_DVC_PATH)
    _registrar_no_historico(
        {
            "model_version": registro["model_version"],
            "decisao": "promovido",
            "iniciado_em": registro.get("iniciado_em"),
            "encerrado_em": agora,
            "campeao_base": registro.get("campeao_base"),
            "motivo": "guardrails de produção não inferiores ao campeão",
            "evidencia": evidencia,
        }
    )
    _remover_arquivos_do_canario()
    return novo


def reverter(motivo: str, evidencia: dict[str, Any] | None = None) -> dict[str, Any]:
    """Encerra o canário sem tocar no campeão. Devolve a entrada gravada no
    histórico. O campeão nunca deixou de estar em produção: o rollback é tirar
    a fração do canário, não restaurar nada."""
    registro = _exigir_canario()
    entrada = {
        "model_version": registro["model_version"],
        "decisao": "revertido",
        "iniciado_em": registro.get("iniciado_em"),
        "encerrado_em": _agora().isoformat(timespec="seconds"),
        "campeao_base": registro.get("campeao_base"),
        "motivo": motivo,
        "evidencia": evidencia,
    }
    _registrar_no_historico(entrada)
    _remover_arquivos_do_canario()
    return entrada


def verificar(params: dict[str, Any] | None = None) -> str | None:
    """Coerência de um canário ativo -- o que o CI confere no PR que o abre e
    em todo PR enquanto ele estiver ativo. `None` se não há canário.

    O modelo baixado é o registrado; o campeão é o mesmo contra o qual o
    canário foi aprovado; as cópias do lock e do dataset existem (sem elas a
    promoção não teria o que restaurar); o threshold é um número válido."""
    registro = carregar()
    if registro is None:
        return None
    ler_config(params)
    modelo = caminho_do_modelo()
    if not modelo.exists():
        raise ErroCanario(f"{modelo} ausente -- rode `dvc pull {modelo.as_posix()}`")
    obtido = campeao.calcular_model_version(modelo)
    if obtido != registro.get("model_version"):
        raise ErroCanario(
            f"o modelo do canário no disco ({obtido}) não é o registrado em "
            f"canario.json ({registro.get('model_version')})"
        )
    atual = campeao.carregar_campeao(CHAMPION_PATH).get("model_version")
    if registro.get("campeao_base") != atual:
        raise ErroCanario(
            f"o canário foi aprovado contra o campeão {registro.get('campeao_base')}, "
            f"mas o campeão agora é {atual}"
        )
    if not (_dir() / LOCK_SALVO).exists():
        raise ErroCanario(f"{_dir() / LOCK_SALVO} ausente")
    threshold = float(registro["decision_threshold"])
    if not 0.0 <= threshold <= 1.0:
        raise ErroCanario(f"decision_threshold do canário fora de [0, 1]: {threshold}")
    return str(obtido)


# --------------------------------------------------------------------------
# divisão da fila
# --------------------------------------------------------------------------
def braco(unidade: str, fracao: float, semente: str) -> bool:
    """`True` se a unidade (hoje, o paciente) cai no braço do canário.

    sha256 de `semente:unidade` lido como fração uniforme em [0, 1): estável
    para o mesmo par, independente entre canários diferentes (a semente é a
    `model_version`), e sem estado -- o job não precisa lembrar quem já foi
    sorteado. O identificador entra só no hash, nunca em log."""
    digest = hashlib.sha256(f"{semente}:{unidade}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < fracao


# --------------------------------------------------------------------------
# decisão (pura -- é o que os testes exercitam sem banco)
# --------------------------------------------------------------------------
@dataclass
class Estatisticas:
    """Contagens de um braço desde o início do canário, lidas de `predicoes`
    (com o desfecho de `agendamentos`)."""

    model_version: str
    predicoes: int = 0
    disparos: int = 0
    fora_do_dominio: int = 0
    desfechos_baixo_risco: int = 0
    faltas_baixo_risco: int = 0
    primeira_predicao: datetime | None = None


@dataclass
class Avaliacao:
    acao: str
    motivo: str
    comparacoes: list[dict[str, Any]] = field(default_factory=list)
    dias: int = 0
    avisos: list[str] = field(default_factory=list)

    def como_dict(self) -> dict[str, Any]:
        return {
            "acao": self.acao,
            "motivo": self.motivo,
            "dias": self.dias,
            "comparacoes": self.comparacoes,
            "avisos": self.avisos,
        }


def z_nao_inferioridade(x_c: int, n_c: int, x_b: int, n_b: int, margem: float) -> float:
    """z de `p_canario - p_campeao - margem`, com o ajuste de Agresti-Caffo
    (um sucesso e um fracasso a mais em cada braço). O ajuste evita o erro
    padrão zero quando um braço tem 0% ou 100% -- comum nos primeiros dias --,
    que transformaria uma amostra pequena em certeza.

    z > z_crítico: o canário é pior que o campeão **além** da margem.
    z < -z_crítico: o canário é pior **por menos** que a margem (não inferior)."""
    p_c = (x_c + 1) / (n_c + 2)
    p_b = (x_b + 1) / (n_b + 2)
    erro_padrao = math.sqrt(p_c * (1 - p_c) / (n_c + 2) + p_b * (1 - p_b) / (n_b + 2))
    return (p_c - p_b - margem) / erro_padrao


def comparar_proporcao(
    metrica: str,
    x_c: int,
    n_c: int,
    x_b: int,
    n_b: int,
    margem: float,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    linha: dict[str, Any] = {
        "metrica": metrica,
        "canario": x_c / n_c if n_c else None,
        "campeao": x_b / n_b if n_b else None,
        "n_canario": n_c,
        "n_campeao": n_b,
        "margem": margem,
        "z": None,
    }
    minimo = cfg["amostra_minima_por_braco"]
    if n_c < minimo or n_b < minimo:
        linha["situacao"] = SITUACAO_AMOSTRA_INSUFICIENTE
        return linha
    z = z_nao_inferioridade(x_c, n_c, x_b, n_b, margem)
    linha["z"] = round(z, 3)
    if z > cfg["z_critico"]:
        linha["situacao"] = SITUACAO_VIOLADO
    elif z < -cfg["z_critico"]:
        linha["situacao"] = SITUACAO_NAO_INFERIOR
    else:
        linha["situacao"] = SITUACAO_INCONCLUSIVO
    return linha


def _descrever(c: dict[str, Any]) -> str:
    return (
        f"{c['metrica']} {c['canario']:.1%} no canário x {c['campeao']:.1%} no campeão "
        f"(margem {c['margem']:.0%}, z={c['z']})"
    )


def decidir(
    est_canario: Estatisticas,
    est_campeao: Estatisticas,
    agora: datetime,
    cfg: dict[str, Any],
) -> Avaliacao:
    """aguardar, promover ou reverter. Função pura.

    Os dias contam da **primeira predição** do canário, não da aprovação no
    gate: o PR do canário pode levar dias para ser revisado, e esse tempo não
    é observação."""
    comparacoes = [
        comparar_proporcao(
            nome,
            getattr(est_canario, num),
            getattr(est_canario, den),
            getattr(est_campeao, num),
            getattr(est_campeao, den),
            cfg["margem"][nome],
            cfg,
        )
        for nome, (num, den) in GUARDRAILS_DO_BANCO.items()
    ]
    avisos = []
    if est_canario.predicoes and est_canario.fora_do_dominio / est_canario.predicoes > 0.10:
        avisos.append(
            f"{est_canario.fora_do_dominio} de {est_canario.predicoes} predições do canário "
            "fora do domínio do treino"
        )

    if est_canario.primeira_predicao is None:
        return Avaliacao(
            ACAO_AGUARDAR,
            "o canário ainda não decidiu nenhum agendamento",
            comparacoes,
            avisos=avisos,
        )
    dias = (agora - est_canario.primeira_predicao).days

    violados = [c for c in comparacoes if c["situacao"] == SITUACAO_VIOLADO]
    if violados:
        return Avaliacao(
            ACAO_REVERTER,
            "guardrail violado: " + "; ".join(_descrever(c) for c in violados),
            comparacoes,
            dias,
            avisos,
        )

    todos_nao_inferiores = all(c["situacao"] == SITUACAO_NAO_INFERIOR for c in comparacoes)
    amostra_ok = (
        est_canario.predicoes >= cfg["predicoes_minimas"]
        and est_canario.desfechos_baixo_risco >= cfg["desfechos_minimos"]
    )
    if dias >= cfg["dias_minimos"] and amostra_ok and todos_nao_inferiores:
        return Avaliacao(
            ACAO_PROMOVER,
            f"{dias} dia(s), {est_canario.predicoes} predições e "
            f"{est_canario.desfechos_baixo_risco} desfechos de baixo risco no canário, "
            "todos os guardrails não inferiores ao campeão",
            comparacoes,
            dias,
            avisos,
        )
    if dias >= cfg["dias_maximos"]:
        return Avaliacao(
            ACAO_REVERTER,
            f"{dias} dia(s) de canário sem evidência de não-inferioridade (amostra "
            f"{est_canario.predicoes} predições / {est_canario.desfechos_baixo_risco} "
            "desfechos) -- o conservador é ficar com o campeão",
            comparacoes,
            dias,
            avisos,
        )
    return Avaliacao(
        ACAO_AGUARDAR,
        f"{dias} dia(s) de observação; faltam dias, amostra ou significância para decidir",
        comparacoes,
        dias,
        avisos,
    )


def estatisticas_do_banco(model_version: str, desde: datetime) -> Estatisticas:
    import db.repositories as repositories

    contagens = repositories.estatisticas_de_modelo(model_version, desde)
    return Estatisticas(model_version=model_version, **contagens)


def avaliar(agora: datetime | None = None, params: dict[str, Any] | None = None) -> Avaliacao:
    """Lê os dois braços no banco e decide. Usado pelo job (antes de rotear a
    fila) e pelo `canario.yml` (para abrir o PR)."""
    registro = _exigir_canario()
    cfg = ler_config(params)
    est_c = estatisticas_do_banco(
        registro["model_version"], datetime.fromisoformat(registro["iniciado_em"])
    )
    # O campeão é medido a partir da primeira predição do canário, não da
    # aprovação no gate: antes do merge do PR do canário ele decidia 100% da
    # fila, e esse período não é comparação -- é só o campeão sozinho.
    desde = est_c.primeira_predicao or datetime.fromisoformat(registro["iniciado_em"])
    est_b = estatisticas_do_banco(registro["campeao_base"], desde)
    return decidir(est_c, est_b, agora or _agora(), cfg)


# --------------------------------------------------------------------------
# integração com o job D-2
# --------------------------------------------------------------------------
@dataclass
class ContextoCanario:
    """O que o job precisa para mandar parte da fila ao canário."""

    model: Any
    mapa_especialidade: dict[str, Any]
    explainer: Any
    model_version: str
    threshold: float
    fracao: float

    def decide(self, unidade: str | None) -> bool:
        return unidade is not None and braco(unidade, self.fracao, self.model_version)


@dataclass
class Preparacao:
    contexto: ContextoCanario | None = None
    falhas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    avaliacao: Avaliacao | None = None
    model_version: str | None = None


def _desligado_por_variavel() -> bool:
    return (os.environ.get("CANARIO_DESLIGADO") or "").strip().lower() in ("1", "true", "sim")


def preparar_para_job(
    campeao_version: str,
    params: dict[str, Any] | None = None,
    agora: datetime | None = None,
) -> Preparacao:
    """Decide se esta execução do job usa o canário. **Nunca levanta**: na
    dúvida, a fila vai 100% para o campeão e o problema vira falha do run
    (Healthchecks `/fail`) -- um canário quebrado não pode custar o lembrete
    de ninguém.

    Ordem: canário existe -> não foi desligado à mão -> modelo e campeão
    conferem -> não foi revertido no banco -> guardrails acumulados não estão
    violados. A violação grava `canarios_revertidos` (rollback automático) e
    já tira o canário desta execução."""
    prep = Preparacao()
    try:
        registro = carregar()
    except Exception as exc:
        prep.falhas.append(f"canario.json ilegível ({exc.__class__.__name__}) -- fila 100% campeão")
        return prep
    if registro is None:
        return prep
    versao = str(registro.get("model_version"))
    prep.model_version = versao

    if _desligado_por_variavel():
        prep.avisos.append(
            f"canário {versao} desligado pela variável CANARIO_DESLIGADO -- fila 100% campeão"
        )
        return prep

    try:
        cfg = ler_config(params)
        if registro.get("campeao_base") != campeao_version:
            raise ErroCanario(
                f"o canário foi aprovado contra o campeão {registro.get('campeao_base')}, mas "
                f"o campeão em produção é {campeao_version}"
            )
        verificar(params)

        import db.repositories as repositories

        revertido = repositories.canario_revertido(versao)
        if revertido is not None:
            prep.avisos.append(
                f"canário {versao} revertido em {revertido.get('revertido_em')} "
                f"({revertido.get('motivo')}) -- fila 100% campeão até o PR de reversão "
                "ser mesclado"
            )
            return prep

        prep.avaliacao = avaliar(agora, params)
        if prep.avaliacao.acao == ACAO_REVERTER:
            repositories.registrar_canario_revertido(versao, prep.avaliacao.motivo)
            prep.falhas.append(
                f"rollback automático do canário {versao}: {prep.avaliacao.motivo} -- fila "
                "100% campeão a partir desta execução"
            )
            return prep
        if prep.avaliacao.acao == ACAO_PROMOVER:
            prep.avisos.append(
                f"canário {versao} pronto para promoção: {prep.avaliacao.motivo} (o "
                "canario.yml abre o PR)"
            )
        prep.avisos += prep.avaliacao.avisos

        import inference
        from explain import construir_explicador

        model, mapa = inference.carregar_modelo(str(caminho_do_modelo()))
        prep.contexto = ContextoCanario(
            model=model,
            mapa_especialidade=mapa,
            explainer=construir_explicador(model),
            model_version=versao,
            threshold=float(registro["decision_threshold"]),
            fracao=cfg["fracao"],
        )
    except ErroCanario as exc:
        prep.falhas.append(f"canário {versao} ignorado: {exc} -- fila 100% campeão")
    except Exception as exc:
        # Só a classe: a mensagem de uma exceção do supabase-py ou do joblib
        # pode carregar URL ou caminho de infraestrutura, e o log do Actions é
        # público.
        prep.falhas.append(
            f"canário {versao} ignorado ({exc.__class__.__name__}) -- fila 100% campeão"
        )
    return prep


def avaliar_execucao(
    contexto: ContextoCanario,
    por_braco: dict[str, dict[str, int]],
    params: dict[str, Any] | None = None,
) -> list[str]:
    """Guardrail de quarentena, medido na própria execução: quarentena não
    gera linha em `predicoes`, então não entra na avaliação acumulada.

    Rollback se o canário põe em quarentena uma fração da fila maior que o
    campeão além da margem -- ou se põe a fila inteira dele, com pelo menos 5
    agendamentos, enquanto o campeão predisse algum (defeito sistêmico do
    modelo novo, não do dado: o dado é o mesmo nos dois braços)."""
    cfg = ler_config(params)
    c = por_braco.get("canario", {})
    b = por_braco.get("campeao", {})
    n_c, q_c = c.get("agendamentos", 0), c.get("quarentena", 0)
    n_b, q_b = b.get("agendamentos", 0), b.get("quarentena", 0)

    motivo = None
    comparacao = comparar_proporcao(
        "quarentena", q_c, n_c, q_b, n_b, cfg["margem"]["quarentena"], cfg
    )
    if comparacao["situacao"] == SITUACAO_VIOLADO:
        motivo = "guardrail violado: " + _descrever(comparacao)
    elif n_c >= 5 and q_c == n_c and q_b < n_b:
        motivo = (
            f"todos os {n_c} agendamento(s) do canário foram para a quarentena, e o campeão "
            "predisse a mesma fila"
        )
    if motivo is None:
        return []
    try:
        import db.repositories as repositories

        repositories.registrar_canario_revertido(contexto.model_version, motivo)
    except Exception as exc:
        return [
            f"rollback do canário {contexto.model_version} não pôde ser gravado "
            f"({exc.__class__.__name__}): {motivo}"
        ]
    return [f"rollback automático do canário {contexto.model_version}: {motivo}"]


# --------------------------------------------------------------------------
# resumo e CLI
# --------------------------------------------------------------------------
def formatar_avaliacao(avaliacao: Avaliacao, registro: dict[str, Any]) -> str:
    """Markdown para o `$GITHUB_STEP_SUMMARY` e para o corpo do PR. Sem emoji,
    pelo mesmo motivo do resumo do gate (console cp1252 no Windows)."""
    linhas = [
        f"## Canário `{registro.get('model_version')}` — {avaliacao.acao}",
        "",
        f"Campeão `{registro.get('campeao_base')}`, {registro.get('fracao', 0):.0%} da fila no "
        f"canário desde {registro.get('iniciado_em')}; {avaliacao.dias} dia(s) de tráfego.",
        "",
        avaliacao.motivo,
        "",
        "| Guardrail | Canário | Campeão | n canário | n campeão | Margem | z | Situação |",
        "|---|---:|---:|---:|---:|---:|---:|:--|",
    ]
    for c in avaliacao.comparacoes:
        canario = "n/d" if c["canario"] is None else f"{c['canario']:.1%}"
        campeao_ = "n/d" if c["campeao"] is None else f"{c['campeao']:.1%}"
        linhas.append(
            f"| `{c['metrica']}` | {canario} | {campeao_} | {c['n_canario']} | "
            f"{c['n_campeao']} | {c['margem']:.0%} | {c['z'] if c['z'] is not None else '-'} | "
            f"{c['situacao']} |"
        )
    linhas.append("")
    linhas += [f"> **Aviso:** {a}" for a in avaliacao.avisos]
    return "\n".join(linhas) + "\n"


def _publicar(texto: str) -> None:
    try:
        print(texto)
    except UnicodeEncodeError:
        codec = sys.stdout.encoding or "ascii"
        print(texto.encode(codec, errors="replace").decode(codec, errors="replace"))
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(texto + "\n")


def _saida_do_workflow(chave: str, valor: str) -> None:
    destino = os.environ.get("GITHUB_OUTPUT")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(f"{chave}={valor}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canário do modelo (ADR-009)")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("status", help="mostra o canário ativo, se houver")
    p_verificar = sub.add_parser(
        "verificar", help="confere modelo, campeão base e cópias salvas (CI)"
    )
    p_verificar.add_argument(
        "--sanidade",
        action="store_true",
        help="roda também a suíte de sanidade (src/sanidade_modelo.py) no modelo do canário",
    )
    p_avaliar = sub.add_parser("avaliar", help="lê os guardrails no banco e decide")
    p_avaliar.add_argument("--relatorio-json", default=None)
    p_avaliar.add_argument("--resumo-md", default=None, help="o mesmo resumo, para o corpo do PR")
    p_promover = sub.add_parser("promover", help="reescreve o campeão a partir do canário")
    p_promover.add_argument("--evidencia-json", default=None)
    p_reverter = sub.add_parser("reverter", help="encerra o canário sem tocar no campeão")
    p_reverter.add_argument("--motivo", required=True)
    p_reverter.add_argument("--evidencia-json", default=None)
    p_reverter.add_argument(
        "--registrar-no-banco",
        action="store_true",
        help="grava canarios_revertidos para o job parar de usar o canário antes do merge",
    )
    args = parser.parse_args(argv)

    try:
        if args.comando == "status":
            registro = carregar()
            if registro is None:
                print("nenhum canário ativo")
            else:
                visivel = {k: v for k, v in registro.items() if k != "_comentario"}
                print(json.dumps(visivel, indent=2, ensure_ascii=False))
            return 0

        if args.comando == "verificar":
            versao = verificar()
            if versao is not None and args.sanidade:
                # Mesma suíte que o gate roda no desafiante e o CI roda no
                # campeão: o PR do canário não pode levar à fila um artefato
                # que ela reprovaria.
                import sanidade_modelo

                sanidade = sanidade_modelo.verificar_modelo(str(caminho_do_modelo()))
                for aviso in sanidade.avisos:
                    print(f"::warning::sanidade do canário: {aviso}")
                if not sanidade.aprovado:
                    raise ErroCanario(
                        "suíte de sanidade reprovou o canário: " + "; ".join(sanidade.falhas)
                    )
            print(
                "[ok] nenhum canário ativo"
                if versao is None
                else f"[ok] canário {versao} coerente"
            )
            return 0

        if args.comando == "avaliar":
            registro = carregar()
            if registro is None:
                _publicar("Nenhum canário ativo.")
                _saida_do_workflow("acao", "nenhum")
                return 0
            avaliacao = avaliar()
            resumo = formatar_avaliacao(avaliacao, registro)
            _publicar(resumo)
            if args.resumo_md:
                Path(args.resumo_md).write_text(resumo, encoding="utf-8")
            _saida_do_workflow("acao", avaliacao.acao)
            _saida_do_workflow("model_version", str(registro.get("model_version")))
            if args.relatorio_json:
                with open(args.relatorio_json, "w", encoding="utf-8") as f:
                    json.dump(avaliacao.como_dict(), f, indent=2, ensure_ascii=False)
            if avaliacao.acao == ACAO_REVERTER:
                import db.repositories as repositories

                repositories.registrar_canario_revertido(
                    str(registro["model_version"]), avaliacao.motivo
                )
            return 0

        evidencia = None
        if getattr(args, "evidencia_json", None) and Path(args.evidencia_json).exists():
            with open(args.evidencia_json, encoding="utf-8") as f:
                evidencia = json.load(f)

        if args.comando == "promover":
            novo = promover(evidencia)
            print(f"[ok] canário {novo['model_version']} promovido a campeão")
            return 0

        if args.comando == "reverter":
            registro = _exigir_canario()
            if args.registrar_no_banco:
                import db.repositories as repositories

                repositories.registrar_canario_revertido(
                    str(registro["model_version"]), args.motivo
                )
            entrada = reverter(args.motivo, evidencia)
            print(f"[ok] canário {entrada['model_version']} revertido; o campeão segue em produção")
            return 0
    except ErroCanario as exc:
        print(f"::error::{exc}")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
