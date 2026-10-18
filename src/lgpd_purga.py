"""
SaúdeJá — workflow de purga LGPD (ADR-009, ADR-010): `.github/workflows/lgpd.yml`.

Três tratamentos que exigem exclusão, numa execução diária, com a única
credencial do projeto que apaga do remote do DVC (SAS com `d`, environment
`lgpd`, restrito a `main`):

1. **Pedidos sobre a base de treino** (revogação e eliminação; a SaúdeJá é a
   controladora): apaga do remote as versões do dataset e os recortes que
   contêm o titular, depois as linhas dele na base de treino
   (`treino_eliminar_titular`), depois marca o pedido como executado, o que
   anula o pseudônimo do pedido. **Nessa ordem**: o pedido carrega o
   pseudônimo até o fim, então uma execução que cai no meio é refeita inteira
   na seguinte. Antes de tocar o banco, a função confere que nenhum dos blobs
   ainda existe -- *"O titular já teve o registro no DVC apagado antes de
   seguir?"* -- e, se não, para naquele pedido.
2. **Pedidos de eliminação confirmados pela clínica**: só o `delete` em
   `pacientes` (o cascade leva agendamentos, predições e mensagens). O DVC não
   tem dado da clínica desde a base de treino separada. Pedido de revisão de
   decisão é atendido por pessoas, não por este workflow.
3. **Retenção**: versões do DVC que deixaram de ser vigentes em `main` há mais
   de `JANELA_TREINO_DIAS` (90) dias são apagadas, e a base de treino perde as
   consultas fora da janela e as provas eliminadas há mais de 365 dias.

**Lista explícita de hashes, nunca `dvc gc`**: os hashes saem do histórico git
dos arquivos que o DVC usa (`.dvc`, `dvc.lock` e as cópias `.salvo` do
canário). Nunca entram na lista: um hash referenciado pelo HEAD de `main` ou
pela ponta de qualquer branch remoto (salvo, na eliminação, a versão do
dataset e os recortes que contêm o titular), a semente sintética e o
`model.pkl` (parâmetros agregados, sem linha de titular). Blob do remote que
nenhum commit referencia (branch apagado sem merge) expira pela data do
próprio blob.

**Logs só com contadores**: nenhum pseudônimo, hash de CPF ou uuid de
paciente. O repositório é público e o log do Actions também.
"""
import argparse
import logging
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import db.repositories as repositories  # noqa: E402
import db.repositorio_treino as repositorio_treino  # noqa: E402
from config_projeto import REPO_ROOT, janela_treino_dias  # noqa: E402
from logging_config import configurar_logging  # noqa: E402
from saida_actions import anexar_ao_resumo  # noqa: E402

logger = logging.getLogger(__name__)

DATASET = "consultas-treino.csv"
SEMENTE = "consultas-historicas.csv"
MODELO = "model.pkl"
# Saídas dos stages que leem o dataset e guardam linha de consulta: os
# recortes de treino e teste e o relatório do gate de dados, que cita
# `id_consulta` de linha bloqueada. O selo e o mapa de especialidade não têm
# linha.
RECORTES_COM_LINHAS = ("train_raw.pkl", "test.pkl", "relatorio_dados.json")

PERGUNTA_DE_SEGURANCA = "O titular já teve o registro no DVC apagado antes de seguir?"

SAIDA_OK = 0
SAIDA_FALHA = 1


# --------------------------------------------------------------------------
# referências do DVC no histórico git (puro)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Referencia:
    """Um `md5` citado por um arquivo do DVC num commit."""

    arquivo: str  # arquivo versionado que cita o hash (`dvc.lock`, `x.dvc`, ...)
    saida: str  # caminho da saída (`data/consultas-treino.csv`, ...)
    md5: str
    entradas: tuple[str, ...] = ()  # md5 das entradas do stage (só no lock)


def referencias_do_arquivo(arquivo: str, texto: str) -> list[Referencia]:
    """As saídas citadas por um `.dvc`, um `dvc.lock` ou a cópia `.salvo` de
    um deles. YAML inválido não derruba a purga: o arquivo não cita nada."""
    try:
        conteudo = yaml.safe_load(texto) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(conteudo, dict):
        return []
    referencias = []
    if "stages" in conteudo:  # dvc.lock
        for stage in (conteudo.get("stages") or {}).values():
            entradas = tuple(d["md5"] for d in stage.get("deps") or [] if d.get("md5"))
            for saida in stage.get("outs") or []:
                if saida.get("md5"):
                    referencias.append(
                        Referencia(arquivo, saida["path"], saida["md5"], entradas)
                    )
    for saida in conteudo.get("outs") or []:  # .dvc
        if saida.get("md5"):
            referencias.append(Referencia(arquivo, saida["path"], saida["md5"]))
    return referencias


def _eh(referencia: Referencia, nome: str) -> bool:
    return referencia.saida.replace("\\", "/").split("/")[-1] == nome


@dataclass
class Historico:
    """Referências de cada commit de `main` que toca um arquivo do DVC, do
    mais antigo ao mais recente, mais as pontas dos outros branches."""

    commits: list[tuple[datetime, list[Referencia]]]
    pontas: list[list[Referencia]] = field(default_factory=list)

    def todas(self) -> Iterator[Referencia]:
        for _, referencias in self.commits:
            yield from referencias
        for referencias in self.pontas:
            yield from referencias

    def protegidos(self) -> set[str]:
        """Nunca apagados por expiração: o que o HEAD de `main` ou a ponta de
        qualquer branch referencia, a semente (em qualquer commit) e o modelo
        de qualquer versão vigente."""
        protegidos = {r.md5 for r in self.todas() if _eh(r, SEMENTE)}
        if self.commits:
            protegidos |= {r.md5 for r in self.commits[-1][1]}
        for referencias in self.pontas:
            protegidos |= {r.md5 for r in referencias}
        return protegidos

    def nunca_apagaveis(self) -> set[str]:
        """Nem na eliminação: a semente (sem titular) e o modelo vigente
        (parâmetros agregados, sem linha de titular; ADR-009)."""
        vigentes = list(self.commits[-1][1]) if self.commits else []
        return {r.md5 for r in self.todas() if _eh(r, SEMENTE)} | {
            r.md5 for r in vigentes if _eh(r, MODELO)
        }

    def fim_de_vigencia(self) -> dict[str, datetime | None]:
        """Para cada hash, quando deixou de ser referenciado em `main` (`None`
        = ainda vigente no HEAD). Um hash que some e volta conta a última
        saída depois da última volta."""
        fim: dict[str, datetime | None] = {}
        for instante, referencias in self.commits:
            presentes = {r.md5 for r in referencias}
            for md5 in presentes:
                fim[md5] = None
            for md5, atual in fim.items():
                if md5 not in presentes and atual is None:
                    fim[md5] = instante
        return fim


def hashes_expirados(historico: Historico, agora: datetime, retencao_dias: int) -> set[str]:
    """Versões que deixaram de ser vigentes em `main` há mais de
    `retencao_dias`, sem nenhum protegido."""
    limite = agora - timedelta(days=retencao_dias)
    expirados = {
        md5
        for md5, fim in historico.fim_de_vigencia().items()
        if fim is not None and fim < limite
    }
    return expirados - historico.protegidos()


def versoes_do_dataset(historico: Historico) -> set[str]:
    """Todo hash que já foi o dataset de treino, em `main` ou num branch,
    menos a semente (que pode ter o mesmo hash enquanto não há produção)."""
    semente = {r.md5 for r in historico.todas() if _eh(r, SEMENTE)}
    return {r.md5 for r in historico.todas() if _eh(r, DATASET)} - semente


def recortes_de(historico: Historico, versoes: set[str]) -> set[str]:
    """Recortes gerados a partir de uma das `versoes` do dataset: as saídas
    com linha de consulta dos stages cujo lock registra o dataset como
    entrada. Os recortes não têm o pseudônimo do titular (o `preprocess`
    guarda só as features), então entram pela ligação do lock, nunca por
    busca no conteúdo."""
    return {
        r.md5
        for r in historico.todas()
        if r.entradas
        and set(r.entradas) & versoes
        and any(_eh(r, nome) for nome in RECORTES_COM_LINHAS)
    }


class HashProtegido(Exception):
    """A lista a apagar contém um hash que nunca pode ser apagado."""


def conferir_lista(hashes: set[str], proibidos: set[str]) -> None:
    """A guarda principal (ADR-009): falha alto, em vez de apagar o modelo de
    produção ou a semente por um erro de cálculo."""
    if hashes & proibidos:
        raise HashProtegido(f"{len(hashes & proibidos)} hash(es) protegido(s) na lista a apagar")


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------
def _git(*argumentos: str, raiz: Path = REPO_ROOT) -> str:
    return subprocess.run(  # noqa: S603 -- argumentos fixos, sem shell
        ["git", *argumentos],  # noqa: S607
        cwd=raiz,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    ).stdout


def _arquivos_do_dvc_no_commit(sha: str, raiz: Path) -> list[str]:
    nomes = _git("ls-tree", "-r", "--name-only", sha, raiz=raiz).splitlines()
    return [
        n for n in nomes if n == "dvc.lock" or n.endswith(".dvc") or n.endswith(".salvo")
    ]


def _referencias_no_commit(sha: str, raiz: Path) -> list[Referencia]:
    referencias = []
    for arquivo in _arquivos_do_dvc_no_commit(sha, raiz):
        texto = _git("show", f"{sha}:{arquivo}", raiz=raiz)
        for r in referencias_do_arquivo(arquivo, texto):
            # O `path` de um `.dvc` é relativo à pasta dele.
            pasta = str(Path(arquivo).parent).replace("\\", "/")
            relativo = not arquivo.endswith("dvc.lock") and pasta != "."
            saida = f"{pasta}/{r.saida}" if relativo else r.saida
            referencias.append(Referencia(arquivo, saida, r.md5, r.entradas))
    return referencias


def ler_historico(ref: str = "HEAD", raiz: Path = REPO_ROOT) -> Historico:
    """Referências de cada commit de `ref` (primeiro pai) que mudou algum
    arquivo do DVC, e as pontas dos branches remotos."""
    linhas = _git(
        "log", "--reverse", "--first-parent", "--format=%H %ct", ref, "--",
        "dvc.lock", "*.dvc", "*.salvo",
        raiz=raiz,
    ).splitlines()
    commits = []
    for linha in linhas:
        sha, epoch = linha.split()
        instante = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
        commits.append((instante, _referencias_no_commit(sha, raiz)))
    pontas = [
        _referencias_no_commit(sha, raiz)
        for sha in _git("for-each-ref", "--format=%(objectname)", "refs/remotes", raiz=raiz)
        .split()
    ]
    return Historico(commits=commits, pontas=pontas)


# --------------------------------------------------------------------------
# remote do DVC
# --------------------------------------------------------------------------
class Remoto(Protocol):
    def existe(self, md5: str) -> bool: ...
    def ler(self, md5: str) -> bytes: ...
    def apagar(self, md5: str) -> bool: ...
    def todos(self) -> Iterable[str]: ...
    def modificado_em(self, md5: str) -> datetime | None: ...


class RemotoDVC:
    """O remote padrão do repositório, pelas classes do próprio DVC: o
    layout (`files/md5/xx/...`) e a credencial (`AZURE_STORAGE_CONNECTION_
    STRING`) são os mesmos do `dvc push`."""

    def __init__(self, raiz: Path = REPO_ROOT):
        from dvc.repo import Repo  # type: ignore[import-untyped]

        self._odb = Repo(str(raiz)).cloud.get_remote_odb(None, "lgpd_purga")

    def _caminho(self, md5: str) -> str:
        return str(self._odb.oid_to_path(md5))

    def existe(self, md5: str) -> bool:
        return bool(self._odb.fs.exists(self._caminho(md5)))

    def ler(self, md5: str) -> bytes:
        return bytes(self._odb.fs.cat_file(self._caminho(md5)))

    def apagar(self, md5: str) -> bool:
        caminho = self._caminho(md5)
        if not self._odb.fs.exists(caminho):
            return False
        self._odb.fs.rm(caminho)
        return True

    def todos(self) -> Iterable[str]:
        return list(self._odb.all())

    def modificado_em(self, md5: str) -> datetime | None:
        info: dict[str, Any] = self._odb.fs.info(self._caminho(md5))
        valor = info.get("last_modified") or info.get("mtime")
        if isinstance(valor, datetime):
            return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
        if isinstance(valor, int | float):
            return datetime.fromtimestamp(valor, tz=timezone.utc)
        return None


# Arquivo maior que isto não é versão do dataset (o export tem dezenas de KB):
# a busca do pseudônimo não baixa o modelo nem artefato grande.
TAMANHO_MAXIMO_DATASET = 50 * 1024 * 1024


def versoes_com_titular(
    remoto: Remoto, candidatos: Iterable[str], id_titular: str
) -> set[str]:
    """Versões do dataset, ainda no remote, que contêm o pseudônimo."""
    alvo = id_titular.encode()
    encontradas = set()
    for md5 in candidatos:
        if not remoto.existe(md5):
            continue
        conteudo = remoto.ler(md5)
        if len(conteudo) <= TAMANHO_MAXIMO_DATASET and alvo in conteudo:
            encontradas.add(md5)
    return encontradas


def orfaos_expirados(
    remoto: Remoto, historico: Historico, agora: datetime, retencao_dias: int
) -> set[str]:
    """Blobs que nenhum commit conhecido referencia (branch apagado sem
    merge) e que estão no remote há mais que a retenção."""
    conhecidos = {r.md5 for r in historico.todas()}
    limite = agora - timedelta(days=retencao_dias)
    expirados = set()
    for md5 in remoto.todos():
        if md5 in conhecidos:
            continue
        modificado = remoto.modificado_em(md5)
        if modificado is not None and modificado < limite:
            expirados.add(md5)
    return expirados


def apagar_lista(remoto: Remoto, hashes: set[str], proibidos: set[str]) -> int:
    conferir_lista(hashes, proibidos)
    return sum(1 for md5 in sorted(hashes) if remoto.apagar(md5))


# --------------------------------------------------------------------------
# execução
# --------------------------------------------------------------------------
@dataclass
class Resultado:
    pedidos_treino_executados: int = 0
    pedidos_treino_parados: int = 0
    linhas_treino_apagadas: int = 0
    pedidos_clinica_executados: int = 0
    blobs_apagados_por_pedido: int = 0
    blobs_expirados_apagados: int = 0
    purga_treino: dict[str, int] = field(default_factory=dict)
    falhas: list[str] = field(default_factory=list)


def executar_pedidos_do_treino(
    remoto: Remoto,
    historico: Historico,
    resultado: Resultado,
    repo_treino: Any = repositorio_treino,
) -> None:
    candidatos = versoes_do_dataset(historico)
    nunca = historico.nunca_apagaveis()
    for pedido in repo_treino.pedidos_pendentes():
        id_titular = pedido["id_titular"]
        try:
            versoes = versoes_com_titular(remoto, candidatos, id_titular)
            alvo = versoes | recortes_de(historico, versoes)
            resultado.blobs_apagados_por_pedido += apagar_lista(remoto, alvo, nunca)
            if any(remoto.existe(md5) for md5 in alvo):
                resultado.pedidos_treino_parados += 1
                resultado.falhas.append(f"pedido do treino parado: {PERGUNTA_DE_SEGURANCA} Não.")
                continue
            resultado.linhas_treino_apagadas += repo_treino.eliminar_titular(id_titular)
            if repo_treino.marcar_pedido_executado(pedido["id"]):
                resultado.pedidos_treino_executados += 1
        except Exception as exc:
            # Nenhum identificador no texto: só a classe. O pedido continua
            # pendente e a próxima execução o refaz inteiro.
            resultado.pedidos_treino_parados += 1
            resultado.falhas.append(f"pedido do treino falhou: {type(exc).__name__}")


def executar_pedidos_da_clinica(resultado: Resultado, repo: Any = repositories) -> None:
    for pedido in repo.pedidos_confirmados():
        if pedido["tipo"] != "eliminacao":
            continue  # revisão de decisão é atendida por pessoas
        try:
            if pedido["id_paciente"] is not None:
                repo.eliminar_paciente(pedido["id_paciente"])
            if repo.marcar_pedido_executado(pedido["id"]):
                resultado.pedidos_clinica_executados += 1
        except Exception as exc:
            resultado.falhas.append(f"pedido da clínica falhou: {type(exc).__name__}")


def executar_retencao(
    remoto: Remoto,
    historico: Historico,
    resultado: Resultado,
    agora: datetime,
    repo_treino: Any = repositorio_treino,
) -> None:
    janela = janela_treino_dias()
    try:
        expirados = hashes_expirados(historico, agora, janela) | orfaos_expirados(
            remoto, historico, agora, janela
        )
        resultado.blobs_expirados_apagados = apagar_lista(
            remoto, expirados, historico.protegidos()
        )
    except Exception as exc:
        resultado.falhas.append(f"expiração do DVC falhou: {type(exc).__name__}")
    try:
        resultado.purga_treino = repo_treino.purgar(janela)
    except Exception as exc:
        resultado.falhas.append(f"purga da base de treino falhou: {type(exc).__name__}")


def executar(
    remoto: Remoto,
    historico: Historico,
    agora: datetime | None = None,
    repo_treino: Any = repositorio_treino,
    repo_clinica: Any = repositories,
) -> Resultado:
    """Ordem: pedidos (prazo de 15 dias correndo), depois retenção."""
    resultado = Resultado()
    executar_pedidos_do_treino(remoto, historico, resultado, repo_treino)
    executar_pedidos_da_clinica(resultado, repo_clinica)
    executar_retencao(
        remoto, historico, resultado, agora or datetime.now(timezone.utc), repo_treino
    )
    return resultado


def _publicar(resultado: Resultado, escrever: Callable[[str], Any] = print) -> None:
    linhas = [
        "### Purga LGPD",
        "",
        "| Contador | Valor |",
        "|---|---:|",
        f"| pedidos do treino executados | {resultado.pedidos_treino_executados} |",
        f"| pedidos do treino parados | {resultado.pedidos_treino_parados} |",
        f"| linhas da base de treino apagadas | {resultado.linhas_treino_apagadas} |",
        f"| eliminações da clínica executadas | {resultado.pedidos_clinica_executados} |",
        f"| blobs do DVC apagados por pedido | {resultado.blobs_apagados_por_pedido} |",
        f"| blobs do DVC expirados apagados | {resultado.blobs_expirados_apagados} |",
        *(f"| purga do treino: {k} | {v} |" for k, v in sorted(resultado.purga_treino.items())),
        "",
        *(f"- **falha:** {f}" for f in resultado.falhas),
    ]
    texto = "\n".join(linhas)
    escrever(texto)
    anexar_ao_resumo(texto)
    for falha in resultado.falhas:
        escrever(f"::error::{falha}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Purga LGPD (ADR-009, ADR-010)")
    parser.add_argument(
        "--ref", default="HEAD", help="ref de main cujo histórico define o que é vigente"
    )
    args = parser.parse_args(argv)
    configurar_logging()
    historico = ler_historico(args.ref)
    resultado = executar(RemotoDVC(), historico)
    _publicar(resultado)
    return SAIDA_FALHA if resultado.falhas else SAIDA_OK


if __name__ == "__main__":
    sys.exit(main())
