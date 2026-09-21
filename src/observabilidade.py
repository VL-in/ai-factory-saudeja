"""
SaúdeJá — observabilidade de aplicação (Passo 8.5, [ADR-006]).

Camada interna da decisão do ADR-006: cada predição servida e cada execução
do job viram uma linha em `eventos_app` (Supabase, `sa-east-1`), que é a
fonte de onde saem os números do SLO §2 (latência) e §4 (cobertura de
explicação) no Passo 12 -- medidos ao longo da operação, não estimados na
véspera do pitch.

Três propriedades que o resto do módulo existe para garantir:

1. **Não bloqueia o caminho crítico.** O registro não pode entrar na latência
   que ele existe para medir (ADR-006, "Cons"). `registrar_evento` só enfileira
   (`queue.put_nowait`); um worker daemon grava fora da requisição. Fila cheia
   descarta o evento -- perder métrica é aceitável, atrasar a predição do
   funcionário não é.
2. **Observabilidade quebrada degrada, não interrompe.** Nenhuma falha de
   gravação (Supabase fora do ar, credencial ausente, `supabase-py` nem
   instalado) escapa deste módulo -- mesma filosofia do `ErroEnvioInfobip` do
   Passo 7, onde a falha de um envio não derruba a fila do dia.
3. **Nunca carrega PII.** `detalhe` é jsonb livre no schema, mas aqui só aceita
   chaves de `CHAVES_DETALHE_PERMITIDAS` -- contadores, rota, status HTTP e
   *nome de classe* de exceção. Mensagem de erro crua não entra: a da Infobip,
   por exemplo, ecoa o payload enviado (com o telefone do paciente), e o grep
   por nome de coluna de `tests/test_coerencia_repo.py` não teria como pegar
   isso dentro de um jsonb.

O import de `db.repositories` é **tardio, dentro do worker**, de propósito: a
imagem enxuta da API (`infra/api/dockerfile`, `requirements/api.txt`) não
instala `supabase-py` (ele mora em `ui.txt`, para não pesar no cold start do
SLO §2). Com import no topo, este módulo quebraria o boot daquela imagem; do
jeito que está, ela roda normalmente e apenas não registra eventos.
"""
import os
import queue
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

TIPO_PREDICAO = "predicao"
TIPO_JOB_D2 = "job_d2"
TIPO_ERRO = "erro"

ORIGEM_API = "api"
ORIGEM_PROCESSO = "processo"
ORIGEM_JOB = "job"

STATUS_OK = "ok"
STATUS_ERRO = "erro"

# Allowlist fechada do `detalhe` (ver docstring). Só contador, rótulo de rota,
# status HTTP e nome de classe de exceção -- nada de texto livre vindo de
# mensagem de erro de terceiro, que é por onde PII entraria sem ser notada.
CHAVES_DETALHE_PERMITIDAS = frozenset(
    {
        "rota",
        "status_http",
        "excecao",
        "agendamentos_encontrados",
        "predicoes_gravadas",
        "mensagens_disparadas",
        "erros",
        "eventos_purgados",
    }
)

TAMANHO_MAXIMO_FILA = 1000
TAMANHO_MAXIMO_VALOR = 120
SEGUNDOS_DE_PAUSA_APOS_FALHAS = 60.0
FALHAS_CONSECUTIVAS_ATE_PAUSAR = 5
RETENCAO_PADRAO_DIAS = 90

_fila: queue.Queue | None = None
_worker: threading.Thread | None = None
_trava = threading.Lock()
_escritor = None  # injetável nos testes (ver tests/test_observabilidade.py)

# Estado de degradação, tocado só pelo worker.
_falhas_consecutivas = 0
_pausado_ate = 0.0
_indisponivel_no_processo = False

_descartados = 0


def ativa() -> bool:
    """`OBSERVABILIDADE_ATIVA=false` desliga o registro por completo -- escape
    hatch para rodar sem nenhuma escrita (ex.: um script local que não deve
    sujar a tabela de produção), não o modo normal."""
    return (os.environ.get("OBSERVABILIDADE_ATIVA") or "true").strip().lower() != "false"


def retencao_dias() -> int:
    """Janela de retenção de `eventos_app` (ADR-006: a tabela divide o teto do
    free tier com os dados do produto). Aplicada pela purga diária do job."""
    try:
        return max(int(os.environ.get("OBSERVABILIDADE_RETENCAO_DIAS") or RETENCAO_PADRAO_DIAS), 1)
    except ValueError:
        return RETENCAO_PADRAO_DIAS


def sanitizar_detalhe(detalhe: dict | None) -> dict:
    """Mantém só as chaves da allowlist e corta valores longos. Chave
    desconhecida é **descartada em silêncio**, não um erro: um caller novo
    passando `telefone=...` por engano não pode derrubar a predição -- mas
    também não pode gravar o telefone."""
    if not detalhe:
        return {}
    limpo = {}
    for chave, valor in detalhe.items():
        if chave not in CHAVES_DETALHE_PERMITIDAS:
            continue
        if isinstance(valor, bool | int | float) or valor is None:
            limpo[chave] = valor
        else:
            limpo[chave] = str(valor)[:TAMANHO_MAXIMO_VALOR]
    return limpo


def registrar_evento(
    tipo: str,
    status: str = STATUS_OK,
    origem: str = ORIGEM_PROCESSO,
    duracao_ms: int | None = None,
    model_version: str | None = None,
    detalhe: dict | None = None,
) -> bool:
    """Enfileira um evento para gravação assíncrona. Devolve se foi enfileirado
    (False = desligado, fila cheia ou processo sem destino de escrita) -- quem
    chama pode ignorar o retorno; ele existe para os testes conseguirem afirmar
    o descarte sem inspecionar a fila."""
    if not ativa() or _indisponivel_no_processo:
        return False

    evento = {
        "tipo": tipo,
        "status": status,
        "origem": origem,
        "duracao_ms": int(duracao_ms) if duracao_ms is not None else None,
        "model_version": model_version,
        "detalhe": sanitizar_detalhe(detalhe),
    }

    try:
        _garantir_worker().put_nowait(evento)
    except queue.Full:
        # Fila cheia = destino lento/fora do ar. Descartar é a escolha certa:
        # bloquear aqui colocaria a observabilidade no caminho crítico, que é
        # exatamente o que o ADR-006 mandou evitar.
        global _descartados
        _descartados += 1
        return False
    except Exception:
        return False
    return True


@contextmanager
def medir(
    tipo: str,
    origem: str = ORIGEM_PROCESSO,
    model_version: str | None = None,
    detalhe: dict | None = None,
):
    """Cronometra o bloco e registra um evento ao sair -- `ok` se passou,
    `erro` (com o *nome da classe* da exceção, nunca a mensagem) se levantou.
    A exceção segue subindo: este módulo observa, não trata.

    Cede um dicionário mutável para o bloco completar o que só se sabe depois
    (`model_version`, contadores), sem obrigar quem chama a montar o evento
    duas vezes.
    """
    contexto = {"model_version": model_version, "detalhe": dict(detalhe or {})}
    inicio = time.perf_counter()
    try:
        yield contexto
    except Exception as exc:
        _registrar_do_contexto(tipo, origem, contexto, inicio, STATUS_ERRO, exc)
        raise
    _registrar_do_contexto(tipo, origem, contexto, inicio, STATUS_OK, None)


def _registrar_do_contexto(tipo, origem, contexto, inicio, status, exc):
    detalhe = dict(contexto.get("detalhe") or {})
    if exc is not None:
        detalhe["excecao"] = exc.__class__.__name__
    registrar_evento(
        tipo=tipo,
        status=status,
        origem=origem,
        duracao_ms=round((time.perf_counter() - inicio) * 1000),
        model_version=contexto.get("model_version"),
        detalhe=detalhe,
    )


def definir_escritor(funcao) -> None:
    """Troca o destino de gravação (default: `db.repositories`). Existe para os
    testes injetarem um escritor que conta chamadas ou falha de propósito, sem
    precisar de Supabase de pé para provar que a falha não derruba nada."""
    global _escritor, _indisponivel_no_processo, _falhas_consecutivas, _pausado_ate
    _escritor = funcao
    _indisponivel_no_processo = False
    _falhas_consecutivas = 0
    _pausado_ate = 0.0


def _garantir_worker() -> queue.Queue:
    global _fila, _worker
    with _trava:
        if _fila is None:
            _fila = queue.Queue(maxsize=TAMANHO_MAXIMO_FILA)
        if _worker is None or not _worker.is_alive():
            # daemon: o processo (job, uvicorn, streamlit) nunca deve ficar
            # preso esperando o worker de métrica terminar.
            _worker = threading.Thread(
                target=_consumir_fila, name="observabilidade", daemon=True
            )
            _worker.start()
    return _fila


def _consumir_fila() -> None:
    while True:
        evento = _fila.get()
        try:
            _gravar(evento)
        finally:
            _fila.task_done()


def _gravar(evento: dict) -> None:
    """Único ponto que fala com o destino. Engole toda exceção de propósito:
    este código roda no worker, e uma exceção aqui mataria a thread e levaria
    junto o registro de todos os eventos seguintes."""
    global _falhas_consecutivas, _pausado_ate, _indisponivel_no_processo

    if _pausado_ate and time.monotonic() < _pausado_ate:
        return

    try:
        _obter_escritor()(**evento)
    except ImportError:
        # supabase-py ausente (imagem enxuta da API): não adianta tentar de
        # novo neste processo -- nada vai mudar até o próximo deploy.
        _indisponivel_no_processo = True
    except Exception:
        _falhas_consecutivas += 1
        if _falhas_consecutivas >= FALHAS_CONSECUTIVAS_ATE_PAUSAR:
            # Destino fora do ar: parar de tentar por um tempo evita que cada
            # evento pague um timeout de HTTP e a fila encha com escritas
            # fadadas a falhar.
            _pausado_ate = time.monotonic() + SEGUNDOS_DE_PAUSA_APOS_FALHAS
            _falhas_consecutivas = 0
    else:
        _falhas_consecutivas = 0
        _pausado_ate = 0.0


def _obter_escritor():
    if _escritor is not None:
        return _escritor
    # Import tardio: ver docstring do módulo (a imagem da API não tem
    # supabase-py). ImportError aqui é tratado acima como indisponibilidade
    # permanente do processo, não como bug.
    import db.repositories as repositories

    return repositories.inserir_evento_app


def flush(timeout: float = 5.0) -> bool:
    """Espera a fila drenar. Obrigatório em processo curto (o job roda e sai):
    sem isso, os eventos enfileirados morrem com o processo antes de o worker
    daemon gravá-los. Serviço longo (API/Streamlit) não precisa chamar.

    Devolve se a fila esvaziou dentro do prazo -- não levanta: um flush que
    não completou é métrica perdida, não falha de negócio."""
    if _fila is None:
        return True
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if _fila.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return _fila.unfinished_tasks == 0


def percentil(valores, p: float) -> float | None:
    """Percentil por interpolação linear (mesma definição de `numpy.percentile`
    com method='linear'), implementado aqui para a aba não importar numpy só
    para isso e para ser testável com um conjunto de latências conhecidas.

    Feito em Python, sobre as linhas já lidas, porque o PostgREST não expõe
    `percentile_cont` -- e o volume (uma fila diária por clínica, ADR-006) não
    justifica criar uma função RPC no banco para isso."""
    ordenados = sorted(v for v in valores if v is not None)
    if not ordenados:
        return None
    if len(ordenados) == 1:
        return float(ordenados[0])
    posicao = (len(ordenados) - 1) * (p / 100)
    inferior = int(posicao)
    superior = min(inferior + 1, len(ordenados) - 1)
    peso = posicao - inferior
    return float(ordenados[inferior] * (1 - peso) + ordenados[superior] * peso)


def inicio_da_janela(horas: int, agora: datetime | None = None) -> datetime:
    """Começo da janela de observação, em UTC. Não no fuso da clínica de
    propósito: janela de métrica é "últimas N horas", uma duração, não uma data
    civil -- diferente da fila do dia, que é 19/09 em São Paulo."""
    return (agora or datetime.now(tz=timezone.utc)) - timedelta(hours=horas)
