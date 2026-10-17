"""
SaúdeJá — alertas, políticas de notificação e painéis do Grafana como código
(Passo 8.9, P7).

O que está no git (`infra/grafana/`):

- `alertas/*.yaml`: regras no formato de provisionamento do Grafana (o mesmo do
  "Export" da tela de alertas), com marcadores `${NOME}`;
- `notificacoes.yaml`: as rotas por criticidade (P1/P2/P3) e a janela do
  resumo semanal;
- `dashboards/*.json`: os painéis, com os mesmos marcadores.

O que NÃO está, de propósito: contact points (e-mails e Telegram são
identificadores de pessoas), URL do stack, token e UIDs das fontes de dados.
Chegam pelo ambiente:

    GRAFANA_URL, GRAFANA_SA_TOKEN       secrets dos environments do Actions
    GRAFANA_DS_SUPABASE                 UID da fonte Postgres do ambiente
    GRAFANA_DS_MIMIR, GRAFANA_DS_LOKI   opcionais; padrão: as fontes que todo
                                        stack do Grafana Cloud traz

Marcadores (`${NOME}`, só maiúsculas, para não colidir com as variáveis do
próprio Grafana): o valor vem de `params.yaml` (blocos `observabilidade` e
`custo`) ou de constantes do código que o alerta diário já usa, para a regra e
o resto do sistema não divergirem. Marcador que ocupa a string inteira vira
número; marcador desconhecido é erro. A lista está em `constantes()`.

Dois ambientes num stack só (decisão 8 do Passo 8.9):

- `prod`: pasta "SaúdeJá", regras sobre `deployment_environment="prod"` e a
  fonte Supabase de produção, rotas na árvore padrão de notificação. Roda no
  fim do deploy em `main` e por `workflow_dispatch`.
- `dev`: só para verificar regras e painéis com dado de dev. Cópias numa
  pasta própria, com UID `-dev`, e cada regra entrega direto ao contact point
  de dev (`notification_settings`), sem passar pela árvore de produção, que o
  modo dev não toca. A regra nasce pausada e só é ligada depois de o Grafana
  devolver o campo gravado; a árvore de produção ainda tem a rota
  `ambiente=dev` como segunda barreira. `remover --ambiente dev` apaga as
  cópias ao fim.

Só biblioteca padrão e PyYAML, como `scripts/smoke_deploy.py`.

Uso:
    python scripts/grafana_provisionar.py validar
    python scripts/grafana_provisionar.py aplicar --ambiente prod
    python scripts/grafana_provisionar.py conferir --ambiente prod
    python scripts/grafana_provisionar.py remover --ambiente dev
"""
import argparse
import copy
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import alerta_observabilidade  # noqa: E402
import logging_config  # noqa: E402
import observabilidade  # noqa: E402

DIR_GRAFANA = REPO_ROOT / "infra" / "grafana"
DIR_ALERTAS = DIR_GRAFANA / "alertas"
DIR_PAINEIS = DIR_GRAFANA / "dashboards"
ARQUIVO_NOTIFICACOES = DIR_GRAFANA / "notificacoes.yaml"
ARQUIVO_PARAMS = REPO_ROOT / "params.yaml"

# As fontes que todo stack do Grafana Cloud traz, com UID fixo.
DS_MIMIR_PADRAO = "grafanacloud-prom"
DS_LOKI_PADRAO = "grafanacloud-logs"
DS_EXPRESSAO = "__expr__"

SEVERIDADES = frozenset({"P1", "P2", "P3"})
AREAS = frozenset({"operacao", "modelo", "custo", "lgpd"})
_MARCADOR = re.compile(r"\$\{([A-Z0-9_]+)\}")
_HORARIO = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_UID = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")
_DURACAO = re.compile(r"^(\d+)([smh])$")


@dataclass(frozen=True)
class Ambiente:
    nome: str  # valor de `deployment_environment` e do rótulo `ambiente`
    pasta_uid: str
    pasta_titulo: str
    sufixo_uid: str
    prefixo_titulo: str
    # Nome do check do Synthetic Monitoring (feito à mão na P6, fora deste
    # script): vira o rótulo `job` das métricas da sonda.
    check_sm: str


AMBIENTES = {
    "prod": Ambiente("prod", "saudeja", "SaúdeJá", "", "", "SaudeJa prod"),
    "dev": Ambiente("dev", "saudeja-dev", "SaúdeJá (dev)", "-dev", "[dev] ", "SaudeJa dev"),
}


class ErroDeProvisionamento(RuntimeError):
    """Configuração inválida ou o Grafana recusou um pedido. A mensagem nunca
    traz o token."""


# --- constantes e marcadores ----------------------------------------------------


def carregar_params() -> dict[str, Any]:
    return yaml.safe_load(ARQUIVO_PARAMS.read_text(encoding="utf-8"))


def _numero_positivo(bloco: dict[str, Any], chave: str, nome_do_bloco: str) -> float:
    valor = bloco.get(chave)
    if isinstance(valor, bool) or not isinstance(valor, int | float) or valor <= 0:
        raise ErroDeProvisionamento(
            f"params.yaml: {nome_do_bloco}.{chave} precisa ser um número positivo "
            f"(está {valor!r}); a regra sairia sem limite e nunca dispararia"
        )
    return float(valor)


def _texto_do_numero(valor: float) -> str:
    return str(int(valor)) if float(valor).is_integer() else repr(float(valor))


def constantes(
    params: dict[str, Any], ambiente: Ambiente, fontes: dict[str, str]
) -> dict[str, Any]:
    """Valor de cada marcador. `fontes` traz os UIDs de `supabase`, `mimir` e
    `loki`."""
    obs = params.get("observabilidade") or {}
    disparos = obs.get("disparos") or {}
    custo = params.get("custo") or {}

    horario = obs.get("fila_d2_horario_limite")
    if not isinstance(horario, str) or not _HORARIO.match(horario):
        raise ErroDeProvisionamento(
            f"params.yaml: observabilidade.fila_d2_horario_limite precisa ser HH:MM "
            f"(está {horario!r})"
        )
    variacao = _numero_positivo(disparos, "variacao", "observabilidade.disparos")
    fracao = _numero_positivo(obs, "fracao_de_alerta", "observabilidade")
    cota_mb = _numero_positivo(obs, "cota_banco_supabase_mb", "observabilidade")
    preco = _numero_positivo(custo, "sms_unitario_usd", "custo")
    orcamento = _numero_positivo(custo, "orcamento_mensal_usd", "custo")
    cambio = _numero_positivo(custo, "cambio_usd_brl", "custo")
    duracao_ms = alerta_observabilidade.DURACAO_MAXIMA_JOB_D2_MS

    return {
        "PASTA": ambiente.pasta_titulo,
        "AMBIENTE": ambiente.nome,
        "CHECK_SM": ambiente.check_sm,
        "DS_SUPABASE": fontes["supabase"],
        "DS_MIMIR": fontes["mimir"],
        "DS_LOKI": fontes["loki"],
        "FILA_D2_HORARIO_LIMITE": horario,
        "DISPAROS_VARIACAO": variacao,
        "DISPAROS_VARIACAO_PCT": round(variacao * 100),
        "DISPAROS_PISO": _numero_positivo(
            disparos, "piso_media_diaria", "observabilidade.disparos"
        ),
        "DISPAROS_JANELA_DIAS": int(
            _numero_positivo(disparos, "janela_dias", "observabilidade.disparos")
        ),
        "FRACAO_DE_ALERTA_PCT": round(fracao * 100),
        "CUSTO_SMS_UNITARIO_USD": preco,
        "CUSTO_ORCAMENTO_USD": orcamento,
        "CUSTO_LIMITE_USD": round(fracao * orcamento, 2),
        "CAMBIO_USD_BRL": cambio,
        "COTA_BANCO_SUPABASE_MB": int(cota_mb),
        "BANCO_LIMITE_BYTES": int(fracao * cota_mb * 1024 * 1024),
        "P95_PREDICAO_MS": _numero_positivo(obs, "p95_predicao_ms", "observabilidade"),
        "JOB_D2_DURACAO_MAXIMA_MS": duracao_ms,
        "JOB_D2_DURACAO_MAXIMA_MIN": round(duracao_ms / 60_000),
        "LIMITE_LEITURA_SLO": observabilidade.LIMITE_LEITURA_SLO,
        "TIPOS_DO_SLO_SQL": ", ".join(f"'{t}'" for t in observabilidade.TIPOS_DO_SLO),
        "REGEX_PII_LOGQL": logging_config.regex_de_pii_re2(),
    }


def substituir(valor: Any, valores: dict[str, Any]) -> Any:
    """Troca os marcadores em toda string da estrutura. A string que é só um
    marcador numérico vira o número (limite de threshold); dentro de texto, o
    número vai sem `.0`."""

    def trocar(casamento: re.Match) -> str:
        nome = casamento.group(1)
        if nome not in valores:
            raise ErroDeProvisionamento(f"marcador desconhecido: ${{{nome}}}")
        bruto = valores[nome]
        return _texto_do_numero(bruto) if isinstance(bruto, int | float) else str(bruto)

    if isinstance(valor, str):
        inteiro = _MARCADOR.fullmatch(valor)
        if inteiro and isinstance(valores.get(inteiro.group(1)), int | float):
            return valores[inteiro.group(1)]
        return _MARCADOR.sub(trocar, valor)
    if isinstance(valor, list):
        return [substituir(item, valores) for item in valor]
    if isinstance(valor, dict):
        return {chave: substituir(item, valores) for chave, item in valor.items()}
    return valor


# --- regras ------------------------------------------------------------------------


def carregar_grupos(valores: dict[str, Any]) -> list[dict[str, Any]]:
    """Grupos de todas as `alertas/*.yaml`, com os marcadores trocados. As
    âncoras do YAML (uma consulta usada por duas regras) já chegam resolvidas."""
    grupos: list[dict[str, Any]] = []
    for arquivo in sorted(DIR_ALERTAS.glob("*.yaml")):
        conteudo = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
        for grupo in conteudo.get("groups") or []:
            grupos.append({**substituir(copy.deepcopy(grupo), valores), "_arquivo": arquivo.name})
    return grupos


def segundos(duracao: str) -> int:
    casamento = _DURACAO.match(str(duracao))
    if not casamento:
        raise ErroDeProvisionamento(f"duração inválida: {duracao!r} (use 30s, 5m, 1h)")
    return int(casamento.group(1)) * {"s": 1, "m": 60, "h": 3600}[casamento.group(2)]


def _consultas(regra: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for d in regra.get("data") or [] if d.get("datasourceUid") != DS_EXPRESSAO]


def validar_grupos(grupos: list[dict[str, Any]], valores: dict[str, Any]) -> None:
    """O que o Grafana não confere e o projeto exige: toda regra com
    `severidade`, `area` e `ambiente`; toda consulta de métrica ou de log
    filtrada pelo ambiente; toda consulta ao banco na fonte do ambiente."""
    problemas: list[str] = []
    uids: set[str] = set()
    nomes: set[str] = set()
    filtro = f'deployment_environment="{valores["AMBIENTE"]}"'
    for grupo in grupos:
        if grupo["name"] in nomes:
            problemas.append(f"grupo repetido: {grupo['name']}")
        nomes.add(grupo["name"])
        intervalo = segundos(grupo.get("interval", ""))
        if intervalo % 10:
            problemas.append(f"{grupo['name']}: intervalo precisa ser múltiplo de 10 s")
        for regra in grupo.get("rules") or []:
            uid = regra.get("uid", "")
            onde = f"{grupo['_arquivo']}::{uid}"
            if not _UID.match(uid) or uid in uids:
                problemas.append(f"{onde}: uid inválido ou repetido")
            uids.add(uid)
            rotulos = regra.get("labels") or {}
            if rotulos.get("severidade") not in SEVERIDADES:
                problemas.append(f"{onde}: rótulo severidade fora de {sorted(SEVERIDADES)}")
            if rotulos.get("area") not in AREAS:
                problemas.append(f"{onde}: rótulo area fora de {sorted(AREAS)}")
            if rotulos.get("ambiente") != valores["AMBIENTE"]:
                problemas.append(f"{onde}: rótulo ambiente diferente de {valores['AMBIENTE']}")
            if regra.get("condition") not in {d.get("refId") for d in regra.get("data") or []}:
                problemas.append(f"{onde}: condition não aponta para um refId")
            consultas = _consultas(regra)
            if not consultas:
                problemas.append(f"{onde}: regra sem consulta")
            for consulta in consultas:
                fonte = consulta.get("datasourceUid")
                modelo = consulta.get("model") or {}
                if fonte == valores["DS_SUPABASE"]:
                    if "observabilidade." not in modelo.get("rawSql", ""):
                        problemas.append(f"{onde}: SQL fora do schema observabilidade")
                elif fonte in (valores["DS_MIMIR"], valores["DS_LOKI"]):
                    if filtro not in modelo.get("expr", ""):
                        problemas.append(f"{onde}: consulta sem o filtro {filtro}")
                else:
                    problemas.append(f"{onde}: fonte de dados desconhecida {fonte!r}")
    if problemas:
        raise ErroDeProvisionamento("regras inválidas:\n- " + "\n- ".join(problemas))


def regra_para_api(
    regra: dict[str, Any],
    grupo: str,
    ambiente: Ambiente,
    contato_dev: str,
) -> dict[str, Any]:
    """Do formato de arquivo para o corpo do endpoint de provisionamento."""
    corpo = {
        "uid": regra["uid"] + ambiente.sufixo_uid,
        "title": ambiente.prefixo_titulo + regra["title"],
        "condition": regra["condition"],
        "data": regra["data"],
        "noDataState": regra.get("noDataState", "OK"),
        "execErrState": regra.get("execErrState", "Error"),
        "for": regra.get("for", "0s"),
        "labels": regra.get("labels") or {},
        "annotations": regra.get("annotations") or {},
        "isPaused": bool(regra.get("isPaused", False)),
        "folderUID": ambiente.pasta_uid,
        "ruleGroup": grupo,
        "orgID": 1,
    }
    if ambiente.nome != "prod":
        # Direto ao contact point de dev: a regra de verificação nunca passa
        # pela árvore de produção (e nunca chega à gestora da clínica).
        corpo["notification_settings"] = {"receiver": contato_dev}
    return corpo


# --- notificações e painéis -----------------------------------------------------


def carregar_notificacoes() -> dict[str, Any]:
    return yaml.safe_load(ARQUIVO_NOTIFICACOES.read_text(encoding="utf-8"))


def contatos_exigidos(notificacoes: dict[str, Any], ambiente: Ambiente) -> set[str]:
    """Em produção, todos: a árvore também tem a rota `ambiente=dev`."""
    if ambiente.nome == "prod":
        return {*notificacoes["contatos"].values(), notificacoes["contato_dev"]}
    return {notificacoes["contato_dev"]}


def arvore_com_as_rotas(atual: dict[str, Any], notificacoes: dict[str, Any]) -> dict[str, Any]:
    """A árvore padrão com as rotas do projeto na frente. A raiz e as rotas de
    terceiros ficam; a rota que entrega a um contact point do projeto sai e dá
    lugar à versão do repositório (inclusive a filha provisória do Synthetic
    Monitoring, criada à mão)."""
    nossos = {*notificacoes["contatos"].values(), notificacoes["contato_dev"]}
    raiz = {k: v for k, v in atual.items() if k != "provenance"}
    de_terceiros = [r for r in raiz.get("routes") or [] if r.get("receiver") not in nossos]
    return {**raiz, "routes": [*copy.deepcopy(notificacoes["rotas"]), *de_terceiros]}


def carregar_paineis(valores: dict[str, Any], ambiente: Ambiente) -> list[dict[str, Any]]:
    paineis = []
    for arquivo in sorted(DIR_PAINEIS.glob("*.json")):
        painel = substituir(json.loads(arquivo.read_text(encoding="utf-8")), valores)
        painel["uid"] = painel["uid"] + ambiente.sufixo_uid
        painel["title"] = ambiente.prefixo_titulo + painel["title"]
        painel["id"] = None
        paineis.append(painel)
    return paineis


# --- cliente HTTP -----------------------------------------------------------------

# (método, url, cabeçalhos, corpo) -> (status, texto). Injetável nos testes.
Transporte = Callable[[str, str, dict[str, str], bytes | None], tuple[int, str]]


def _contexto_tls() -> ssl.SSLContext:
    """Com o `certifi` instalado (vem com o httpx, no `.venv`), valida pelo
    pacote de raízes dele. No Windows, o Python com OpenSSL 1.1.1 monta a cadeia
    dos stacks do Grafana Cloud (Let's Encrypt YE2 -> Root YE) pelo repositório do
    sistema e chega a uma raiz cruzada vencida: "certificate has expired", com o
    certificado válido (conferido em 2026-10-17). No runner do Actions, só com
    PyYAML, fica o repositório do Ubuntu, que valida a mesma cadeia."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def transporte_http(
    metodo: str, url: str, cabecalhos: dict[str, str], corpo: bytes | None
) -> tuple[int, str]:
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ErroDeProvisionamento("GRAFANA_URL precisa ser https")
    pedido = urllib.request.Request(url, data=corpo, method=metodo, headers=cabecalhos)  # noqa: S310 -- esquema conferido acima
    try:
        with urllib.request.urlopen(pedido, timeout=30, context=_contexto_tls()) as resposta:  # noqa: S310
            return resposta.status, resposta.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        # A causa (`reason`) é o que diferencia nome errado, certificado e
        # rede. Só a classe e o texto do erro: nem a URL nem o cabeçalho com o
        # token entram na mensagem, que vai para o log do Actions.
        causa = getattr(exc, "reason", exc)
        detalhe = getattr(causa, "verify_message", None) or getattr(causa, "strerror", None)
        dica = {
            "gaierror": "o host de GRAFANA_URL não resolve: confira o endereço (só o "
            "https://<stack>.grafana.net, sem caminho nem aspas)",
            "SSLCertVerificationError": "certificado recusado: instale o certifi "
            "(`pip install certifi`) ou confira se há proxy inspecionando HTTPS",
            "TimeoutError": "sem resposta em 30 s: rede ou firewall",
            "ConnectionRefusedError": "conexão recusada: rede ou firewall",
        }.get(type(causa).__name__, "")
        raise ErroDeProvisionamento(
            f"Grafana inalcançável ({type(causa).__name__}"
            + (f": {detalhe}" if detalhe else "")
            + ")"
            + (f" -- {dica}" if dica else "")
        ) from None


class Grafana:
    def __init__(self, url: str, token: str, transporte: Transporte = transporte_http) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._transporte = transporte

    def pedir(
        self,
        metodo: str,
        caminho: str,
        corpo: Any = None,
        aceitos: tuple[int, ...] = (200,),
    ) -> tuple[int, Any]:
        cabecalhos = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        dados = None
        if corpo is not None:
            cabecalhos["Content-Type"] = "application/json"
            dados = json.dumps(corpo).encode("utf-8")
        status, texto = self._transporte(metodo, self._url + caminho, cabecalhos, dados)
        if status not in aceitos:
            dica = " (o service account precisa do papel Admin)" if status == 403 else ""
            raise ErroDeProvisionamento(
                f"{metodo} {caminho}: HTTP {status}{dica} -- {texto[:300]}"
            )
        try:
            return status, json.loads(texto) if texto else None
        except json.JSONDecodeError:
            return status, texto


# --- operações ----------------------------------------------------------------------


def _fontes_do_ambiente() -> dict[str, str]:
    supabase = (os.environ.get("GRAFANA_DS_SUPABASE") or "").strip()
    if not supabase:
        raise ErroDeProvisionamento(
            "defina GRAFANA_DS_SUPABASE (variável do environment): UID da fonte "
            "Postgres do Supabase deste ambiente"
        )
    return {
        "supabase": supabase,
        "mimir": (os.environ.get("GRAFANA_DS_MIMIR") or "").strip() or DS_MIMIR_PADRAO,
        "loki": (os.environ.get("GRAFANA_DS_LOKI") or "").strip() or DS_LOKI_PADRAO,
    }


def _conferir_fontes(grafana: Grafana, fontes: dict[str, str]) -> None:
    for papel, uid in fontes.items():
        status, _ = grafana.pedir("GET", f"/api/datasources/uid/{uid}", aceitos=(200, 404))
        if status == 404:
            raise ErroDeProvisionamento(f"fonte de dados {papel} (UID {uid!r}) não existe no stack")


def _conferir_contatos(grafana: Grafana, exigidos: set[str]) -> None:
    _, pontos = grafana.pedir("GET", "/api/v1/provisioning/contact-points")
    existentes = {p.get("name") for p in pontos or []}
    faltando = sorted(exigidos - existentes)
    if faltando:
        raise ErroDeProvisionamento(
            "contact points a criar no Grafana antes do provisionamento: " + ", ".join(faltando)
        )


def _garantir_pasta(grafana: Grafana, ambiente: Ambiente) -> None:
    status, _ = grafana.pedir("GET", f"/api/folders/{ambiente.pasta_uid}", aceitos=(200, 404))
    if status == 404:
        grafana.pedir(
            "POST",
            "/api/folders",
            {"uid": ambiente.pasta_uid, "title": ambiente.pasta_titulo},
        )


def _grupos_na_pasta(grafana: Grafana, ambiente: Ambiente) -> set[str]:
    _, regras = grafana.pedir("GET", "/api/v1/provisioning/alert-rules")
    return {r["ruleGroup"] for r in regras or [] if r.get("folderUID") == ambiente.pasta_uid}


def _caminho_do_grupo(ambiente: Ambiente, grupo: str) -> str:
    return (
        f"/api/v1/provisioning/folder/{ambiente.pasta_uid}/rule-groups/"
        f"{urllib.parse.quote(grupo, safe='')}"
    )


def _aplicar_grupo(
    grafana: Grafana,
    ambiente: Ambiente,
    nome: str,
    intervalo: int,
    regras: list[dict[str, Any]],
) -> None:
    """Em produção, um PUT. Em dev, a regra nasce pausada e só é ligada depois
    de o Grafana devolver o `notification_settings` gravado: uma versão que o
    ignorasse em silêncio mandaria o alerta de dev pela árvore de produção."""
    caminho = _caminho_do_grupo(ambiente, nome)

    def enviar(pausadas: bool) -> None:
        corpo_das_regras = [{**r, "isPaused": pausadas or r["isPaused"]} for r in regras]
        grafana.pedir(
            "PUT",
            caminho,
            {
                "title": nome,
                "folderUid": ambiente.pasta_uid,
                "interval": intervalo,
                "rules": corpo_das_regras,
            },
        )

    if ambiente.nome == "prod":
        enviar(pausadas=False)
        return
    enviar(pausadas=True)
    _, gravado = grafana.pedir("GET", caminho)
    esperados = {r["uid"]: r["notification_settings"]["receiver"] for r in regras}
    lidos = {
        r.get("uid"): (r.get("notification_settings") or {}).get("receiver")
        for r in (gravado or {}).get("rules") or []
    }
    if lidos != esperados:
        grafana.pedir("DELETE", caminho, aceitos=(200, 204))
        raise ErroDeProvisionamento(
            f"o Grafana não gravou o contact point de dev nas regras de `{nome}`; o grupo "
            "foi apagado antes de ser ligado, para não alertar pela árvore de produção"
        )
    enviar(pausadas=False)


def aplicar(grafana: Grafana, ambiente: Ambiente, valores: dict[str, Any]) -> list[str]:
    """Aplica tudo. Devolve as linhas do resumo. As checagens (fontes,
    contact points) vêm antes de qualquer escrita: configuração incompleta não
    deixa o stack pela metade."""
    grupos = carregar_grupos(valores)
    validar_grupos(grupos, valores)
    notificacoes = carregar_notificacoes()
    paineis = carregar_paineis(valores, ambiente)

    _conferir_fontes(
        grafana,
        {papel: valores[f"DS_{papel.upper()}"] for papel in ("supabase", "mimir", "loki")},
    )
    _conferir_contatos(grafana, contatos_exigidos(notificacoes, ambiente))
    _garantir_pasta(grafana, ambiente)

    resumo = []
    declarados = set()
    for grupo in grupos:
        nome = grupo["name"]
        declarados.add(nome)
        regras = [
            regra_para_api(r, nome, ambiente, notificacoes["contato_dev"])
            for r in grupo["rules"]
        ]
        _aplicar_grupo(grafana, ambiente, nome, segundos(grupo["interval"]), regras)
        resumo.append(f"grupo `{nome}`: {len(regras)} regra(s)")
    for obsoleto in sorted(_grupos_na_pasta(grafana, ambiente) - declarados):
        grafana.pedir("DELETE", _caminho_do_grupo(ambiente, obsoleto), aceitos=(200, 204))
        resumo.append(f"grupo `{obsoleto}` removido (saiu do repositório)")

    if ambiente.nome == "prod":
        for intervalo in notificacoes["intervalos"]:
            nome = urllib.parse.quote(intervalo["name"], safe="")
            status, _ = grafana.pedir(
                "GET", f"/api/v1/provisioning/mute-timings/{nome}", aceitos=(200, 404)
            )
            if status == 404:
                grafana.pedir(
                    "POST", "/api/v1/provisioning/mute-timings", intervalo, aceitos=(201, 200)
                )
            else:
                grafana.pedir(
                    "PUT",
                    f"/api/v1/provisioning/mute-timings/{nome}",
                    intervalo,
                    aceitos=(200, 202),
                )
        _, atual = grafana.pedir("GET", "/api/v1/provisioning/policies")
        grafana.pedir(
            "PUT",
            "/api/v1/provisioning/policies",
            arvore_com_as_rotas(atual or {}, notificacoes),
            aceitos=(200, 202),
        )
        resumo.append(f"políticas: {len(notificacoes['rotas'])} rota(s) por criticidade")

    for painel in paineis:
        grafana.pedir(
            "POST",
            "/api/dashboards/db",
            {
                "dashboard": painel,
                "folderUid": ambiente.pasta_uid,
                "overwrite": True,
                "message": "provisionado por scripts/grafana_provisionar.py",
            },
        )
        resumo.append(f"painel `{painel['title']}`")
    return resumo


def remover(grafana: Grafana, ambiente: Ambiente) -> list[str]:
    """Só dev: apaga as cópias de verificação (regras, painéis e a pasta)."""
    if ambiente.nome == "prod":
        raise ErroDeProvisionamento("remover só existe para dev: produção sai por PR")
    resumo = []
    for grupo in sorted(_grupos_na_pasta(grafana, ambiente)):
        grafana.pedir("DELETE", _caminho_do_grupo(ambiente, grupo), aceitos=(200, 204))
        resumo.append(f"grupo `{grupo}` removido")
    status, _ = grafana.pedir(
        "DELETE", f"/api/folders/{ambiente.pasta_uid}", aceitos=(200, 404)
    )
    resumo.append(
        "pasta e painéis de dev removidos" if status == 200 else "pasta de dev já não existia"
    )
    return resumo


def conferir(grafana: Grafana, valores: dict[str, Any]) -> list[str]:
    """Só leitura: as métricas e os rótulos de que as regras dependem chegaram
    ao Mimir e ao Loki? Responde a pergunta do plano sobre
    `deployment_environment` (promovido a rótulo pelo Grafana Cloud, ou só em
    `target_info`) com o que o stack de fato guarda nos últimos 14 dias."""
    seletor = urllib.parse.quote('{__name__=~"saudeja_.+"}', safe="")
    mimir = f"/api/datasources/proxy/uid/{valores['DS_MIMIR']}/api/v1/label"
    _, nomes = grafana.pedir("GET", f"{mimir}/__name__/values?match[]={seletor}")
    _, ambientes = grafana.pedir("GET", f"{mimir}/deployment_environment/values?match[]={seletor}")
    # Loki: sem `since`, a API de rótulos olha só as últimas 6 horas, e o job
    # roda uma vez por dia. A contagem por ambiente usa o filtro de rótulo,
    # que casa `deployment_environment` indexado ou em structured metadata --
    # o mesmo que a regra de PII e os painéis usam.
    loki = f"/api/datasources/proxy/uid/{valores['DS_LOKI']}/loki/api/v1"
    _, servicos_loki = grafana.pedir("GET", f"{loki}/label/service_name/values?since=7d")
    contagem = urllib.parse.quote(
        'sum by (service_name, deployment_environment) '
        '(count_over_time({service_name=~"saudeja-.+"} | deployment_environment!="" [7d]))',
        safe="",
    )
    _, linhas_loki = grafana.pedir("GET", f"{loki}/query?query={contagem}")

    def valores_de(resposta: Any) -> list[str]:
        return sorted(resposta.get("data") or []) if isinstance(resposta, dict) else []

    def series_de(resposta: Any) -> list[str]:
        resultado = ((resposta or {}).get("data") or {}).get("result") or []
        return sorted(
            f"{s['metric'].get('service_name')}/{s['metric'].get('deployment_environment')}"
            f"={s['value'][1]}"
            for s in resultado
            if isinstance(resposta, dict)
        )

    usadas = sorted(
        {
            m
            for g in carregar_grupos(valores)
            for r in g["rules"]
            for c in _consultas(r)
            for m in re.findall(r"\bsaudeja_\w+", c.get("model", {}).get("expr", ""))
        }
    )
    presentes = set(valores_de(nomes))
    return [
        "Mimir, deployment_environment: "
        f"{valores_de(ambientes) or 'NENHUM (rótulo não promovido?)'}",
        *(
            f"Mimir, `{m}`: {'presente' if m in presentes else 'AUSENTE nos últimos 14 dias'}"
            for m in usadas
        ),
        f"Loki, service_name (7 dias): {valores_de(servicos_loki) or 'NENHUM'}",
        "Loki, linhas do SaúdeJá por serviço/ambiente (7 dias): "
        f"{series_de(linhas_loki) or 'NENHUMA (o log da aplicação não chega ao Loki?)'}",
    ]


def _cliente() -> Grafana:
    url = (os.environ.get("GRAFANA_URL") or "").strip()
    token = (os.environ.get("GRAFANA_SA_TOKEN") or "").strip()
    if not url or not token:
        raise ErroDeProvisionamento(
            "defina GRAFANA_URL e GRAFANA_SA_TOKEN (secrets do environment)"
        )
    # Token do GitHub (o do gatilho do job D-2) no lugar do de service account:
    # o Grafana só diria "Invalid API key". Nunca mostra o token, só o tipo.
    if token.startswith(("github_pat_", "ghp_", "gho_", "ghs_")):
        raise ErroDeProvisionamento(
            "GRAFANA_SA_TOKEN tem um token do GitHub; use o token do service account "
            "do Grafana (começa com glsa_)"
        )
    return Grafana(url, token)


def _publicar(titulo: str, linhas: list[str]) -> None:
    for linha in linhas:
        print(f"[ok] {linha}")
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(f"### {titulo}\n\n" + "".join(f"- {linha}\n" for linha in linhas))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alertas e painéis do Grafana como código")
    sub = parser.add_subparsers(dest="acao", required=True)
    sub.add_parser("validar", help="só confere os arquivos, sem rede (CI)")
    for acao in ("aplicar", "conferir", "remover"):
        p = sub.add_parser(acao)
        p.add_argument("--ambiente", choices=sorted(AMBIENTES), required=True)
    args = parser.parse_args(argv)

    try:
        params = carregar_params()
        if args.acao == "validar":
            for ambiente in AMBIENTES.values():
                fontes = {
                    "supabase": "ds-supabase", "mimir": DS_MIMIR_PADRAO, "loki": DS_LOKI_PADRAO
                }
                valores = constantes(params, ambiente, fontes)
                validar_grupos(carregar_grupos(valores), valores)
                carregar_paineis(valores, ambiente)
            _publicar("Grafana: arquivos válidos", ["regras, notificações e painéis"])
            return 0
        ambiente = AMBIENTES[args.ambiente]
        grafana = _cliente()
        if args.acao == "remover":
            _publicar(f"Grafana ({ambiente.nome}): remoção", remover(grafana, ambiente))
            return 0
        valores = constantes(params, ambiente, _fontes_do_ambiente())
        if args.acao == "conferir":
            _publicar(
                f"Grafana ({ambiente.nome}): o que chegou ao stack", conferir(grafana, valores)
            )
            return 0
        _publicar(
            f"Grafana ({ambiente.nome}): provisionamento", aplicar(grafana, ambiente, valores)
        )
    except ErroDeProvisionamento as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
