"""
SaúdeJá — logging estruturado com redação de PII (Passo 8, "Blindagem LGPD").

O requisito é absoluto no BRIEFING.md ("Sem PII em logs. Nunca.") e mensurável
no SLO §6 (0 ocorrências). O que este módulo resolve é a parte *preventiva*
dele: o [ADR-006](../docs/adr/adr-006-observabilidade.md) já estabeleceu que o
log de runtime do HF Space é efêmero (restart/rebuild apaga, sem busca e sem
retenção), então não existe varredura a posteriori que sirva de evidência --
a garantia tem de estar no caminho por onde a linha de log passa.

Três decisões que explicam a forma do módulo:

1. **O filtro mora no HANDLER, não no logger, e é aplicado a TODO handler do
   processo** (`aplicar_filtro_em_handlers_existentes`). O log que de fato vai
   existir em produção não é nosso: é o do `uvicorn`, do `httpx`, do
   `supabase-py` e do `streamlit`. Um logger próprio do projeto com filtro
   embutido protegeria só as linhas que nós escrevemos -- exatamente as que já
   estão sob nosso controle. Pior: `uvicorn` põe `propagate=False` nos seus
   loggers e pendura handlers próprios, então nem o handler da raiz os
   alcançaria.
2. **Duas famílias de regra, porque PII tem duas naturezas.** CPF, telefone,
   e-mail e IP têm *forma* reconhecível e são pegos por padrão. Nome não tem
   forma alguma -- "Ana Souza" é indistinguível de qualquer outro par de
   palavras --, então o que se reconhece é a *chave* que o anuncia
   (`nome=`, `"nome":`, `extra={"nome": ...}`). Daí `CHAVES_PROIBIDAS`.
3. **Falha de redação apaga a linha, não a deixa passar.** Se algo aqui
   levantar exceção, o registro é substituído por um marcador visível em vez de
   ser emitido cru: perder uma linha de log é aceitável, vazar dado de paciente
   não é. Mesma direção de escolha da fila de `src/observabilidade.py`, que
   descarta evento em vez de atrasar a predição.

Chamado uma vez por entrypoint: `lifespan` da API (`src/api/main.py`), `main()`
do job (`src/jobs/inferencia_diaria.py`) e o topo de `src/ui/app.py`. Os
scripts do pipeline de treino (`preprocess`/`train`/`validate`) seguem usando
`print`: rodam sobre dataset pseudonimizado, fora do caminho de qualquer dado
de paciente vivo, e sua saída é lida por humano no terminal/MLflow.

O que **não** é redigido, de propósito: `id_paciente_externo` (hash sha256 de
CPF -- é a forma minimizada que o banco guarda, e sem ela não há como
correlacionar nada em diagnóstico), `id_agendamento`/`id_paciente` (uuid
interno) e `model_version` (hash do artefato).
"""
import json
import logging
import os
import re
import sys
import threading
from datetime import datetime, timezone

# `<...>` e não `[...]`: o valor de uma chave é delimitado até `,`/`;`/`)`/`}`/`]`
# (ver `_PADRAO_CHAVE_VALOR`), então um marcador com `]` dentro seria cortado no
# meio e a redação deixaria de ser idempotente -- `scripts/auditoria_lgpd.py`
# passaria a acusar como achado a própria linha que o filtro já limpou.
MARCADOR = "<REDIGIDO>"
NIVEL_PADRAO = "INFO"
FORMATO_PADRAO = "json"

# Chaves cujo VALOR é PII por definição -- nome é o motivo desta lista existir
# (não tem forma detectável por regex, só o rótulo que o precede).
#
# `sobrenome` entra explicitamente: não é alcançável como variação de `nome`,
# porque a regra exige fronteira de palavra e `sobrenome` não tem uma antes do
# `nome`. Mais longas primeiro, para a alternância não parar na curta.
CHAVES_PROIBIDAS = (
    "sobrenome", "nome", "name", "cpf", "email", "e_mail", "telefone", "celular", "phone",
)

_ALTERNATIVAS_DE_CHAVE = "|".join(CHAVES_PROIBIDAS)

# `nome=Ana Souza, idade=34` -> o valor vai até a vírgula, não até o espaço:
# cortar no espaço deixaria "Souza" na linha.
#
# Os dois grupos opcionais em volta da chave cobrem as duas formas de composição
# que aparecem na prática -- `nome_completo` (sufixo) e `paciente_nome`
# (prefixo). Sem o prefixo, `extra={"paciente_nome": ...}` passaria batido; sem
# a fronteira de palavra na frente, `namespace` seria lido como `name`.
_PADRAO_CHAVE_VALOR = re.compile(
    rf"""(?P<chave>["']?\b(?:\w+_)?(?:{_ALTERNATIVAS_DE_CHAVE})(?:_\w+)?["']?\s*[:=]\s*)"""
    r"""(?P<valor>"[^"]*"|'[^']*'|[^,;)}\]\n]+)""",
    re.IGNORECASE,
)

_PADRAO_EMAIL = re.compile(r"(?<![\w.])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# Sequência numérica candidata a CPF (11 dígitos), telefone brasileiro (10 a 13
# com código do país) ou IP. A contagem de dígitos é conferida em
# `_redigir_sequencia_numerica`, não na própria regex: o que separa um telefone
# de um `duracao_ms` é quantos dígitos tem, e expressar isso em regex sobre um
# texto com separadores variados (`(11) 98765-4321`, `+55 11 98765-4321`,
# `123.456.789-00`) fica ilegível.
#
# As fronteiras excluem `-` e `_` de propósito: sem isso, o último grupo de um
# uuid (`...-446655440000`, 12 dígitos) viraria "telefone" e o diagnóstico
# perderia justamente o identificador que não é PII.
_PADRAO_SEQUENCIA_NUMERICA = re.compile(r"(?<![\w\-])\+?\d[\d\s().\-]{8,20}\d(?![\w\-])")

_DIGITOS_MINIMOS = 10  # telefone fixo com DDD
_DIGITOS_MAXIMOS = 13  # celular com código do país (55 + DDD + 9 dígitos)

# Atributos que o próprio `logging` põe em todo LogRecord -- o que sobrar são
# os campos passados por `extra=`, que é o que o formatador serializa e o
# filtro precisa varrer.
_ATRIBUTOS_PADRAO_DO_RECORD = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "taskName", "thread", "threadName",
    }
)

_MENSAGEM_DE_FALHA = f"{MARCADOR} falha ao redigir este registro de log -- linha descartada"

_trava = threading.Lock()
_configurado = False


def _redigir_chave_valor(casamento: re.Match) -> str:
    return f"{casamento.group('chave')}{MARCADOR}"


def _redigir_por_completo(casamento: re.Match) -> str:
    return MARCADOR


def _redigir_sequencia_numerica(casamento: re.Match) -> str:
    """Redige só o que tem contagem de dígitos de CPF/telefone/IP.

    Erra para o lado de redigir: um epoch em milissegundos (13 dígitos) ou um
    IP de cliente no log de acesso do uvicorn caem aqui. No caso do IP isso não
    é dano colateral -- endereço IP é dado pessoal sob a LGPD, e o `ts` de cada
    linha já vem do formatador, não da mensagem.
    """
    trecho = casamento.group(0)
    digitos = sum(1 for c in trecho if c.isdigit())
    if _DIGITOS_MINIMOS <= digitos <= _DIGITOS_MAXIMOS:
        return MARCADOR
    return trecho


# Fonte única das regras: o filtro as aplica (`redigir`) e
# `scripts/auditoria_lgpd.py` as consome para procurar o que escapou. Duas
# listas de regex sobre o mesmo requisito divergiriam, e a que divergisse em
# silêncio seria justamente a que dá o veredito na auditoria.
#
# Cada regra é (nome, padrão, substituição). A substituição é o que decide se
# há PII: para a sequência numérica ela devolve o próprio trecho quando a
# contagem de dígitos não é de CPF/telefone/IP, e isso é o que faz um
# `duracao_ms` não virar achado.
REGRAS_DE_PII = (
    ("chave-de-pii", _PADRAO_CHAVE_VALOR, _redigir_chave_valor),
    ("email", _PADRAO_EMAIL, _redigir_por_completo),
    ("cpf-telefone-ip", _PADRAO_SEQUENCIA_NUMERICA, _redigir_sequencia_numerica),
)


def redigir(texto: str) -> str:
    """Aplica as três famílias de regra a um texto já renderizado.

    A ordem de `REGRAS_DE_PII` importa: a regra de chave primeiro, porque ela
    apaga o valor inteiro (inclusive um nome, que nenhuma outra pegaria);
    depois e-mail; por fim a sequência numérica, a mais propensa a casar demais.
    """
    if not texto:
        return texto
    for _nome, padrao, substituir in REGRAS_DE_PII:
        texto = padrao.sub(substituir, texto)
    return texto


def _redigir_valor_de_campo(chave: str, valor):
    """Campo vindo de `extra=`. A chave proibida perde o valor inteiro sem
    olhar o conteúdo: `extra={"nome": "Ana"}` não tem como ser pego pela regra
    de texto, porque no JSON de saída a chave e o valor são serializados
    separadamente e nunca formam a string `nome=Ana`."""
    if _chave_proibida(chave):
        return MARCADOR
    if isinstance(valor, str):
        return redigir(valor)
    if isinstance(valor, dict):
        return {k: _redigir_valor_de_campo(k, v) for k, v in valor.items()}
    if isinstance(valor, list | tuple):
        return [_redigir_valor_de_campo(chave, v) for v in valor]
    return valor


def chave_de_pii(chave: str) -> bool:
    """`chave` nomeia um campo de PII? Mesmas três formas que a regra de texto
    reconhece: exata (`nome`), com sufixo (`nome_completo`) e com prefixo
    (`paciente_nome`).

    Público porque não serve só ao log: `src/explain.py` usa esta mesma função
    para barrar PII no contexto enviado ao LLM (Passo 13). Duas noções de "o
    que é campo de PII" no repositório divergiriam, e a que divergisse em
    silêncio seria a que guarda a fronteira externa."""
    alvo = str(chave).lower()
    return any(
        alvo == proibida
        or alvo.startswith(f"{proibida}_")
        or alvo.endswith(f"_{proibida}")
        for proibida in CHAVES_PROIBIDAS
    )


def _chave_proibida(chave: str) -> bool:
    return chave_de_pii(chave)


class FiltroRedacaoPII(logging.Filter):
    """Redige o registro no lugar (mensagem, traceback e campos de `extra=`).

    Filtro e não formatador de propósito: um filtro de handler roda para
    *qualquer* formatador que esteja instalado, inclusive o do uvicorn e o do
    Streamlit, que não são nossos. Um formatador só protegeria o handler que
    o usa.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            _redigir_record(record)
        except Exception:
            # Fail-closed: substitui a linha por um marcador em vez de deixar
            # passar conteúdo não verificado. A falha fica visível no próprio
            # log, e não silenciosa.
            record.msg = _MENSAGEM_DE_FALHA
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def _redigir_record(record: logging.LogRecord) -> None:
    # `getMessage()` resolve msg % args; guardar o resultado e zerar args é o
    # jeito padrão de reescrever a mensagem sem deixar o argumento cru para o
    # formatador reinterpolar depois.
    record.msg = redigir(record.getMessage())
    record.args = ()

    if record.exc_info:
        excecao = record.exc_info[0]
        # Só a CLASSE sobrevive estruturada, mesma escolha da allowlist de
        # `src/observabilidade.py`: a mensagem da exceção é o vetor mais
        # provável de PII (a resposta de um provedor ecoando o payload).
        record.excecao = excecao.__name__ if excecao else None
        record.exc_text = redigir(logging.Formatter().formatException(record.exc_info))
        # Zerado para que nenhum outro formatador do processo consiga
        # reconstruir o traceback cru a partir dele.
        record.exc_info = None
    if record.stack_info:
        record.stack_info = redigir(record.stack_info)

    for chave in list(record.__dict__):
        if chave in _ATRIBUTOS_PADRAO_DO_RECORD or chave == "excecao":
            continue
        record.__dict__[chave] = _redigir_valor_de_campo(chave, record.__dict__[chave])


class FormatadorJSON(logging.Formatter):
    """Uma linha JSON por registro. Formato estruturado importa mesmo com log
    efêmero: `docker compose logs`/a aba de logs do Space são grep-áveis, e
    `scripts/auditoria_lgpd.py` varre o mesmo texto sem precisar de parser
    próprio para cada camada."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "nivel": record.levelname,
            "logger": record.name,
            "mensagem": record.getMessage(),
            "origem": f"{record.module}:{record.lineno}",
        }
        for chave, valor in record.__dict__.items():
            if chave in _ATRIBUTOS_PADRAO_DO_RECORD or chave in payload:
                continue
            payload[chave] = valor
        if record.exc_text:
            payload["traceback"] = record.exc_text
        # default=str: um `extra` com date/Decimal não pode derrubar o log.
        return json.dumps(payload, ensure_ascii=False, default=str)


class _HandlerSaudeJa(logging.StreamHandler):
    """Subclasse só para o handler do projeto ser reconhecível na raiz -- é
    como `configurar_logging` sabe o que substituir ao ser chamado de novo, sem
    derrubar handlers de terceiros que estejam legitimamente instalados."""


def configurar_logging(nivel: str | None = None, formato: str | None = None, forcar=False):
    """Instala o handler estruturado na raiz e blinda todo handler do processo.

    Idempotente: chamada de novo (a UI importa módulos que a API também usa)
    não duplica handler nem duplica linha. O que ela **repete** de propósito é
    `aplicar_filtro_em_handlers_existentes`, porque handler novo pode ter
    aparecido nesse meio-tempo -- é o caso do uvicorn com `--reload` e do
    Streamlit, que configuram logging em momentos que não controlamos.
    """
    global _configurado
    with _trava:
        raiz = logging.getLogger()
        if not _configurado or forcar:
            for handler in list(raiz.handlers):
                if isinstance(handler, _HandlerSaudeJa):
                    raiz.removeHandler(handler)
            handler = _HandlerSaudeJa(sys.stdout)
            handler.setFormatter(_formatador(formato))
            raiz.addHandler(handler)
            raiz.setLevel(_nivel(nivel))
            _silenciar_ruido_de_terceiros()
            _configurado = True
        aplicar_filtro_em_handlers_existentes()
        return raiz


def _formatador(formato: str | None) -> logging.Formatter:
    escolhido = (formato or os.environ.get("LOG_FORMATO") or FORMATO_PADRAO).strip().lower()
    if escolhido == "texto":
        # Escape hatch para leitura local -- a redação continua valendo, porque
        # ela mora no filtro do handler, não neste formatador.
        return logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
    return FormatadorJSON()


def _nivel(nivel: str | None) -> int:
    nome = (nivel or os.environ.get("LOG_LEVEL") or NIVEL_PADRAO).strip().upper()
    return getattr(logging, nome, logging.INFO)


def _silenciar_ruido_de_terceiros() -> None:
    """`httpx` loga uma linha INFO com a URL completa de cada requisição -- e o
    `supabase-py` usa `httpx` por baixo, então uma fila do dia renderizada
    produziria uma linha por consulta ao PostgREST, com filtros e tudo.

    Reduzir para WARNING é menos superfície de PII (query string de PostgREST
    carrega valor de filtro) e menos ruído num log que é efêmero e não tem
    busca -- mesmo argumento que manteve `/health` fora da instrumentação do
    Passo 8.5. Erro de HTTP continua aparecendo.
    """
    for nome in ("httpx", "httpcore", "hpack", "urllib3"):
        logging.getLogger(nome).setLevel(logging.WARNING)


def aplicar_filtro_em_handlers_existentes() -> int:
    """Pendura `FiltroRedacaoPII` em todo handler já instalado no processo.

    É a peça que faz a garantia valer para o log de terceiros. `uvicorn` define
    `propagate=False` em `uvicorn.access`/`uvicorn.error` e instala handlers
    próprios; o Streamlit faz algo equivalente. Sem este passeio, esses
    registros -- os únicos que existem em volume no Space -- sairiam sem passar
    por nenhuma redação.

    Devolve quantos handlers foram blindados nesta chamada (0 em chamada
    repetida), para o teste conseguir afirmar a idempotência.
    """
    blindados = 0
    for logger in _todos_os_loggers():
        for handler in list(getattr(logger, "handlers", [])):
            if any(isinstance(f, FiltroRedacaoPII) for f in handler.filters):
                continue
            handler.addFilter(FiltroRedacaoPII())
            blindados += 1
    return blindados


def _todos_os_loggers():
    raiz = logging.getLogger()
    yield raiz
    # `loggerDict` também guarda PlaceHolder (nó intermediário de hierarquia),
    # que não tem handlers -- daí o getattr defensivo em quem consome.
    yield from list(raiz.manager.loggerDict.values())
