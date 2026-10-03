"""
SaúdeJá — job de inferência diária D-2.

Busca no Supabase os agendamentos de amanhã até D+2 ainda sem predição (via
`db.repositories.buscar_agendamentos_d2_pendentes` -- a janela de três dias
recupera sozinha um dia em que o cron não rodou), aplica o contrato de
features (`src/contrato_features.py`), roda a predição+
explicação reaproveitando `src/inference.py`/`src/explain.py` -- os mesmos
módulos que a API usa, sem lógica de predição duplicada --, grava
o resultado em `predicoes` e decide o disparo de lembrete pago conforme o
threshold de `params.yaml`, sempre registrando a decisão (enviado ou não)
em `mensagens_disparadas` para auditoria (SLA §6).

Importa o modelo em processo (decisão do ADR-005 b), não via HTTP à API. Roda
no runner do GitHub Actions (`.github/workflows/job_d2.yml`), não
no Space -- ver a emenda de 2026-09-30 no ADR-005.

`main()` é o caminho agendado e acrescenta o que a aba "Dev: disparo manual"
(que chama `processar_dia()` direto) não precisa: **pré-checagem** (o modelo e
o threshold são os do campeão, o banco responde) antes de qualquer SMS, e
**pós-checagem** (a contabilidade da fila fecha, a quarentena não foi
sistêmica) depois, com o código de saída que o workflow usa para avisar o
Healthchecks.

**Canário (ADR-009)**: só o caminho agendado o usa. Com
`data/canario.json` ativo, `main()` pede a `canario.preparar_para_job` o
contexto do canário -- que já confere os guardrails e faz o rollback
automático se algum estiver violado -- e `processar_dia` manda a fração
configurada da fila (por paciente) para o modelo em observação. Qualquer
problema com o canário cai para 100% campeão, nunca para fila sem lembrete.
A aba de dev roda só com o campeão.
"""
import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import campeao  # noqa: E402
import canario  # noqa: E402
import contrato_features  # noqa: E402
import db.repositories as repositories  # noqa: E402
import inference  # noqa: E402
import observabilidade  # noqa: E402
from config_projeto import (  # noqa: E402
    caminho_de_env,
    hoje_na_clinica,
    para_horario_da_clinica,
    retencao_dados_derivados_dias,
)
from explain import construir_explicador, explicar  # noqa: E402
from features import calcular_idade  # noqa: E402
from logging_config import configurar_logging  # noqa: E402
from messaging.client import (  # noqa: E402
    ErroEnvioInfobip,
    InfobipClient,
    MessagingClient,
    StubMessagingClient,
)

logger = logging.getLogger(__name__)

# Lembrete enviado a um paciente que NÃO pôde ser predito (dado inválido,
# falha do modelo). Decisão da autora (2026-09-29): a falta custa
# R$ 180 e o SMS custa centavos, então na dúvida o paciente é lembrado -- mas
# como exceção rastreável, com status próprio em `mensagens_disparadas`, e não
# confundida com o disparo normal por risco.
STATUS_ENVIADO_SEM_PREDICAO = "enviado_sem_predicao"

SAIDA_OK = 0
SAIDA_FALHA = 1


class ProvedorMensageriaDesconhecido(Exception):
    """MESSAGING_PROVIDER aponta para um provedor sem implementação -- erro
    explícito em vez de cair no stub silenciosamente, para não mascarar uma
    configuração de produção errada."""


def obter_cliente_mensageria() -> MessagingClient:
    """`stub` (default) nunca faz rede; `infobip` envia SMS de
    verdade via `InfobipClient`, lendo `INFOBIP_BASE_URL`/`INFOBIP_CHAVE_API`
    do ambiente."""
    provedor = (os.environ.get("MESSAGING_PROVIDER") or "stub").strip().lower()
    if provedor == "stub":
        return StubMessagingClient()
    if provedor == "infobip":
        return InfobipClient()
    raise ProvedorMensageriaDesconhecido(
        f"MESSAGING_PROVIDER={provedor!r} sem implementação -- use 'stub' ou 'infobip'"
    )


def _payload_de_agendamento(agendamento: dict[str, Any]) -> dict[str, Any]:
    """`pacientes.data_nascimento` (não `idade` -- ver migration
    20260920010000_cadastro_pacientes.sql) precisa virar idade aqui, na hora
    da predição, calculada em relação à data da própria consulta -- mesmo
    conceito que `idade` já representa no dataset histórico de treino.

    Só monta; a coerção de tipo e as regras são do contrato de features
    (`_processar_fila` o aplica logo em seguida)."""
    paciente = agendamento["pacientes"]
    # Hora da clínica, não a UTC que o Postgres devolve:
    # sem isto o modelo via `horario` 3h adiantado e o SMS dizia 21:00 para
    # uma consulta das 18:00.
    data_hora_agendada = para_horario_da_clinica(agendamento["data_hora_agendada"])
    data_nascimento = date.fromisoformat(paciente["data_nascimento"])
    return {
        "idade": calcular_idade(data_nascimento, data_hora_agendada.date()),
        "sexo": paciente["sexo"],
        "especialidade": agendamento["especialidade"],
        "distancia_km": agendamento["distancia_km"],
        "dias_entre_agendamento_consulta": agendamento["dias_entre_agendamento_consulta"],
        "historico_noshow": agendamento["historico_noshow"],
        "data_hora_agendada": data_hora_agendada,
    }


def processar_dia(
    data_referencia: date | None = None,
    cliente_mensageria: MessagingClient | None = None,
    contexto_canario: canario.ContextoCanario | None = None,
) -> dict[str, Any]:
    """Roda o pipeline D-2 uma vez para `data_referencia` (default hoje na
    clínica). Devolve contagens (não levanta por agendamento individual malformado
    -- um payload ruim não pode travar a fila inteira do dia) para a aba de
    dev/logs mostrarem o que aconteceu.

    Com `contexto_canario`, a fração dele da fila é decidida pelo canário, e o
    resultado ganha `por_braco` (contadores de cada modelo nesta execução)."""
    data_referencia = data_referencia or hoje_na_clinica()
    with observabilidade.medir(
        observabilidade.TIPO_JOB_D2, origem=observabilidade.ORIGEM_JOB
    ) as observado:
        resultado = _processar_fila(
            data_referencia, cliente_mensageria, observado, contexto_canario
        )
    return resultado


class _Braco:
    """Um modelo pronto para decidir parte da fila: o campeão sempre, o
    canário quando houver. Mesmos campos nos dois, para o laço não precisar
    saber qual está usando."""

    def __init__(
        self,
        nome: str,
        model: Any,
        mapa_especialidade: dict[str, Any],
        explainer: Any,
        model_version: str,
        threshold: float,
    ) -> None:
        self.nome = nome
        self.model = model
        self.mapa_especialidade = mapa_especialidade
        self.explainer = explainer
        self.model_version = model_version
        self.threshold = threshold


def _unidade_do_canario(agendamento: dict[str, Any]) -> str | None:
    """O paciente (`id_paciente_externo`, já pseudonimizado) é a unidade de
    divisão: estável entre as consultas dele durante o canário. Agendamento
    malformado sem o embed cai no campeão."""
    paciente = agendamento.get("pacientes")
    if not isinstance(paciente, dict) or not paciente.get("id_paciente_externo"):
        return None
    return str(paciente["id_paciente_externo"])


def _processar_fila(
    data_referencia: date,
    cliente_mensageria: MessagingClient | None,
    observado: dict[str, Any],
    contexto_canario: canario.ContextoCanario | None = None,
) -> dict[str, Any]:
    """Corpo do job, separado de `processar_dia` só para a instrumentação de
    `eventos_app` (ADR-006) envolver a execução inteira -- inclusive a busca no
    banco e uma falha antes do primeiro agendamento -- sem indentar a lógica
    de negócio dentro de um `with`. `observado` é o dicionário cedido por
    `observabilidade.medir`: o que for escrito em `observado["detalhe"]` vira
    o `detalhe` do evento `job_d2` gravado ao final."""
    pendentes = repositories.buscar_agendamentos_d2_pendentes(data_referencia)

    resultado: dict[str, Any] = {
        "agendamentos_encontrados": len(pendentes),
        "predicoes_gravadas": 0,
        "quarentena": 0,
        "fora_do_dominio": 0,
        "mensagens_disparadas": 0,
        "lembretes_sem_predicao": 0,
        "falhas_de_envio": 0,
        "erros": [],
    }
    observado["detalhe"]["agendamentos_encontrados"] = len(pendentes)
    if not pendentes:
        return resultado

    model_path = caminho_de_env("MODEL_PATH", "data/model.pkl")
    model, mapa_especialidade = inference.carregar_modelo(model_path)
    model_version = inference.calcular_model_version(model_path)
    observado["model_version"] = model_version
    bracos = {
        "campeao": _Braco(
            "campeao",
            model,
            mapa_especialidade,
            construir_explicador(model),
            model_version,
            float(inference.PARAMS["decision"]["threshold"]),
        )
    }
    if contexto_canario is not None:
        bracos["canario"] = _Braco(
            "canario",
            contexto_canario.model,
            contexto_canario.mapa_especialidade,
            contexto_canario.explainer,
            contexto_canario.model_version,
            contexto_canario.threshold,
        )
        resultado["por_braco"] = {
            nome: {
                "agendamentos": 0,
                "predicoes_gravadas": 0,
                "quarentena": 0,
                "disparos_por_risco": 0,
            }
            for nome in bracos
        }
    cliente = cliente_mensageria or obter_cliente_mensageria()

    for agendamento in pendentes:
        braco = bracos["campeao"]
        if contexto_canario is not None and contexto_canario.decide(
            _unidade_do_canario(agendamento)
        ):
            braco = bracos["canario"]
        contadores = resultado.get("por_braco", {}).get(braco.nome)
        if contadores is not None:
            contadores["agendamentos"] += 1

        inicio = time.perf_counter()
        try:
            linha = contrato_features.validar_payload(
                _payload_de_agendamento(agendamento), braco.mapa_especialidade
            )
            X = inference.construir_features(linha.valores, braco.mapa_especialidade)
            probabilidade = float(inference.predizer(braco.model, X)[0])
            contribuicoes = explicar(braco.explainer, X)
        except Exception as exc:
            # Quarentena: QUALQUER falha ao predizer uma linha (regra de
            # negócio do contrato, especialidade fora do mapa, nulo inesperado)
            # fica restrita àquela linha. Antes só KeyError/Especialidade eram
            # capturadas, e um TypeError abortava o resto da fila.
            _registrar_quarentena(agendamento, exc, braco.model_version, resultado)
            if contadores is not None:
                contadores["quarentena"] += 1
            _enviar_lembrete(agendamento, cliente, resultado, STATUS_ENVIADO_SEM_PREDICAO)
            continue

        threshold = braco.threshold
        classe_prevista = int(probabilidade >= threshold)
        # Mede só o trecho de modelo (features + predição + SHAP), o mesmo que
        # a rota /predict executa -- a gravação no banco e o envio de SMS que
        # vêm a seguir são custo do job, não da predição.
        observabilidade.registrar_evento(
            tipo=observabilidade.TIPO_PREDICAO,
            origem=observabilidade.ORIGEM_JOB,
            duracao_ms=round((time.perf_counter() - inicio) * 1000),
            model_version=braco.model_version,
        )

        repositories.gravar_predicao(
            id_agendamento=agendamento["id"],
            probabilidade=probabilidade,
            classe_prevista=classe_prevista,
            threshold_usado=threshold,
            explicacao_shap=contribuicoes,
            model_version=braco.model_version,
            fora_do_dominio=linha.fora_do_dominio,
        )
        resultado["predicoes_gravadas"] += 1
        resultado["fora_do_dominio"] += int(linha.fora_do_dominio)
        if contadores is not None:
            contadores["predicoes_gravadas"] += 1
            contadores["disparos_por_risco"] += classe_prevista

        if classe_prevista:
            _enviar_lembrete(agendamento, cliente, resultado, "enviado")
        else:
            repositories.registrar_mensagem(
                id_agendamento=agendamento["id"],
                canal=cliente.canal,
                status_envio="nao_enviado",
            )

    observado["detalhe"].update(
        {
            "predicoes_gravadas": resultado["predicoes_gravadas"],
            "quarentena": resultado["quarentena"],
            "mensagens_disparadas": resultado["mensagens_disparadas"],
            "lembretes_sem_predicao": resultado["lembretes_sem_predicao"],
            "erros": len(resultado["erros"]),
        }
    )
    return resultado


def _motivo_seguro(exc: BaseException) -> str:
    """O que pode ir para `resultado["erros"]` (exibido na aba de dev) sem
    carregar dado de paciente. `ViolacaoDoContrato` e `ErroEnvioInfobip` são
    escritas para isso (campo + regra; status HTTP + messageId). De qualquer
    outra exceção, só a classe: `str(exc)` de um `ValueError` de data, por
    exemplo, traz a data de nascimento que não pôde ser lida."""
    if isinstance(exc, contrato_features.ViolacaoDoContrato | ErroEnvioInfobip):
        return str(exc)
    return exc.__class__.__name__


def _registrar_quarentena(
    agendamento: dict[str, Any],
    exc: Exception,
    model_version: str | None,
    resultado: dict[str, Any],
) -> None:
    """Agendamento que não pôde ser predito: registra e deixa o job seguir."""
    resultado["quarentena"] += 1
    resultado["erros"].append({"id_agendamento": agendamento["id"], "motivo": _motivo_seguro(exc)})
    detalhe = {"excecao": exc.__class__.__name__}
    if isinstance(exc, contrato_features.ViolacaoDoContrato):
        detalhe["campo_invalido"] = exc.campo
    # Só a CLASSE da exceção (e, se for do contrato, o NOME do campo) vai para
    # `eventos_app`: a tabela de observabilidade não guarda dado de paciente
    # (ADR-006).
    observabilidade.registrar_evento(
        tipo=observabilidade.TIPO_ERRO,
        status=observabilidade.STATUS_ERRO,
        origem=observabilidade.ORIGEM_JOB,
        model_version=model_version,
        detalhe=detalhe,
    )
    # `id_agendamento` no log de propósito: é uuid interno, não PII,
    # e sem ele o diagnóstico de "por que este paciente não tem predição" não
    # sai do lugar.
    logger.warning(
        "agendamento sem predição",
        extra={"id_agendamento": agendamento["id"], **detalhe},
    )


def _mensagem_de_lembrete(agendamento: dict[str, Any]) -> str:
    """Texto do SMS, com data e hora no fuso da clínica. Se o próprio
    agendamento estiver malformado (é o caso da quarentena), cai numa mensagem
    genérica em vez de deixar de lembrar o paciente."""
    try:
        quando = para_horario_da_clinica(agendamento["data_hora_agendada"])
        return (
            f"Olá! Confirmamos sua consulta de {agendamento['especialidade']} em "
            f"{quando:%d/%m/%Y às %H:%M}. Poderá comparecer?"
        )
    except Exception:
        return "Olá! Lembramos da sua consulta agendada nos próximos dias. Poderá comparecer?"


def _enviar_lembrete(
    agendamento: dict[str, Any],
    cliente_mensageria: MessagingClient,
    resultado: dict[str, Any],
    status: str,
) -> None:
    """Envia o lembrete, registra a auditoria (SLA §6) e marca
    `agendamentos.lembrete_enviado` -- o registro que o re-treino usa para não
    confundir "compareceu" com "compareceu porque foi lembrado", e que impede
    reenvio na janela de três dias do job."""
    canal = cliente_mensageria.canal
    try:
        telefone = agendamento["pacientes"]["telefone"]
        cliente_mensageria.enviar_lembrete(
            telefone=telefone, mensagem=_mensagem_de_lembrete(agendamento)
        )
    except (ErroEnvioInfobip, KeyError, TypeError) as exc:
        # Falha de envio de UM paciente (número inválido, sandbox recusou,
        # Infobip fora do ar, paciente sem telefone no embed) não pode travar
        # a fila inteira -- registra e segue. Sem `lembrete_enviado`: a
        # execução do dia seguinte tenta de novo, se ainda estiver na janela.
        resultado["falhas_de_envio"] += 1
        motivo = _motivo_seguro(exc)
        resultado["erros"].append({"id_agendamento": agendamento["id"], "motivo": motivo})
        logger.warning(
            "falha ao enviar lembrete",
            extra={"id_agendamento": agendamento["id"], "canal": canal, "motivo": motivo},
        )
        repositories.registrar_mensagem(
            id_agendamento=agendamento["id"], canal=canal, status_envio="falha_envio"
        )
        return
    repositories.registrar_mensagem(
        id_agendamento=agendamento["id"], canal=canal, status_envio=status
    )
    repositories.marcar_lembrete_enviado(agendamento["id"])
    resultado["mensagens_disparadas"] += 1
    if status == STATUS_ENVIADO_SEM_PREDICAO:
        resultado["lembretes_sem_predicao"] += 1


def purgar_eventos_antigos() -> int:
    """Política de retenção de `eventos_app` (ADR-006): a tabela cresce a cada
    predição e divide o teto do free tier do Supabase com os dados do produto.

    Roda aqui, pendurada no job que já é diário, em vez de num agendador novo
    (pg_cron/workflow próprio) -- observabilidade não deve adicionar peça de
    infra a manter, que é o mesmo critério que descartou Grafana/OTel no ADR.
    Falha de purga não derruba o job: perder a limpeza de um dia é irrelevante
    perto de perder a fila de lembretes."""
    try:
        limite = observabilidade.inicio_da_janela(horas=24 * observabilidade.retencao_dias())
        return repositories.purgar_eventos_app(limite)
    except Exception:
        return 0


def purgar_dados_derivados_antigos() -> dict[str, int]:
    """Retenção de `predicoes`/`mensagens_disparadas` (`docs/LGPD.md`
    §5) -- LGPD Art. 6º, III: dado derivado não fica guardado indefinidamente
    só porque o banco aguenta.

    Roda junto da purga de `eventos_app` e falha do mesmo jeito: sem derrubar o
    job. Perder a limpeza de um dia é irrelevante perto de perder a fila de
    lembretes, e a janela (365 dias) tem folga de sobra para uma execução
    perdida não virar retenção indevida."""
    try:
        limite = observabilidade.inicio_da_janela(
            horas=24 * retencao_dados_derivados_dias()
        )
        return repositories.purgar_dados_derivados(limite)
    except Exception as exc:
        logger.warning(
            "purga de dados derivados falhou", extra={"excecao": exc.__class__.__name__}
        )
        return {}


# --------------------------------------------------------------------------
# pré e pós-checagem do caminho agendado
# --------------------------------------------------------------------------
def pre_checagem() -> str:
    """Antes de qualquer SMS: o `model.pkl` e o `decision.threshold` são os do
    campeão (`champion_metrics.json`, ver `src/campeao.py`) e o banco responde com o
    schema esperado. Levanta na primeira divergência; devolve a
    `model_version` conferida.

    O threshold conferido é o que o job vai usar (`inference.PARAMS`), não uma
    releitura do arquivo -- a checagem precisa olhar para o mesmo número que
    decide quem recebe lembrete pago."""
    versao = campeao.verificar_campeao(
        model_path=caminho_de_env("MODEL_PATH", "data/model.pkl"),
        threshold=float(inference.PARAMS["decision"]["threshold"]),
    )
    repositories.verificar_conexao()
    return versao


def taxa_de_disparo_alerta() -> float:
    return float(inference.PARAMS["gate"]["taxa_disparo_alerta"])


def pos_checagem(resultado: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(falhas, avisos) sobre o resultado de uma execução.

    Falha -- o workflow termina vermelho e o Healthchecks recebe `/fail`:
    - a contabilidade não fecha (`predições != pendentes - quarentena`): algum
      agendamento sumiu entre a busca e a gravação;
    - **toda** a fila foi para a quarentena. Não é "dado ruim" de um paciente,
      é defeito sistêmico (schema mudou, modelo incompatível) -- e, pela regra
      do lembrete sem predição, a fila inteira recebeu SMS. Precisa acordar
      alguém, mesmo que num dia de fila de uma linha só isso seja alarme falso.

    Aviso -- anotação no run, sem falhar:
    - quarentena parcial, agendamento fora do domínio do treino, falha de
      envio, e taxa de disparo por risco acima de `gate.taxa_disparo_alerta`
      (o "cortar 70%" do BRIEFING, sem teto por decisão da autora)."""
    falhas: list[str] = []
    avisos: list[str] = []
    pendentes = resultado["agendamentos_encontrados"]
    quarentena = resultado["quarentena"]
    preditos = resultado["predicoes_gravadas"]

    if preditos != pendentes - quarentena:
        falhas.append(
            f"contabilidade da fila não fecha: {pendentes} pendente(s), {quarentena} em "
            f"quarentena, mas {preditos} predição(ões) gravada(s)"
        )
    if pendentes and quarentena == pendentes:
        falhas.append(
            f"todos os {pendentes} agendamento(s) foram para a quarentena -- defeito "
            "sistêmico provável (schema, modelo ou contrato), e a fila inteira recebeu "
            "lembrete sem predição"
        )
    elif quarentena:
        avisos.append(f"{quarentena} de {pendentes} agendamento(s) em quarentena")

    if resultado["fora_do_dominio"]:
        avisos.append(
            f"{resultado['fora_do_dominio']} predição(ões) fora do domínio do treino "
            "(gravadas e marcadas na fila)"
        )
    if resultado["falhas_de_envio"]:
        avisos.append(f"{resultado['falhas_de_envio']} falha(s) de envio de lembrete")

    por_risco = resultado["mensagens_disparadas"] - resultado["lembretes_sem_predicao"]
    limite = taxa_de_disparo_alerta()
    if preditos and por_risco / preditos > limite:
        avisos.append(
            f"taxa de disparo por risco {por_risco / preditos:.0%} ({por_risco} de "
            f"{preditos}) acima de {limite:.0%}"
        )
    return falhas, avisos


def _publicar_resumo(
    resultado: dict[str, Any],
    falhas: list[str],
    avisos: list[str],
    model_version: str,
    canario_version: str | None = None,
) -> None:
    """Anotações (`::error::`/`::warning::`) e `$GITHUB_STEP_SUMMARY` quando
    roda no Actions; no terminal, as mesmas linhas servem de leitura. Só
    contadores: a lista `erros` fica de fora, como no log (LGPD)."""
    for falha in falhas:
        print(f"::error::{falha}")
    for aviso in avisos:
        print(f"::warning::{aviso}")
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destino:
        return
    titulo = "com falha" if falhas else "concluído"
    linhas = [
        f"### Job D-2 — {titulo}",
        "",
        f"modelo `{model_version}`",
        "",
        "| Contador | Valor |",
        "|---|---:|",
        *(
            f"| {chave} | {resultado[chave]} |"
            for chave in (
                "agendamentos_encontrados",
                "predicoes_gravadas",
                "quarentena",
                "fora_do_dominio",
                "mensagens_disparadas",
                "lembretes_sem_predicao",
                "falhas_de_envio",
            )
        ),
        "",
        *_linhas_do_canario(resultado, canario_version),
        *(f"- **falha:** {f}" for f in falhas),
        *(f"- aviso: {a}" for a in avisos),
    ]
    with open(destino, "a", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")


def _linhas_do_canario(resultado: dict[str, Any], canario_version: str | None) -> list[str]:
    """Contadores por braço no resumo do run, quando houve canário."""
    por_braco = resultado.get("por_braco")
    if not por_braco:
        return []
    linhas = [
        f"Canário `{canario_version}` nesta execução:",
        "",
        "| Braço | Agendamentos | Predições | Quarentena | Disparos por risco |",
        "|---|---:|---:|---:|---:|",
    ]
    for nome, c in por_braco.items():
        linhas.append(
            f"| {nome} | {c['agendamentos']} | {c['predicoes_gravadas']} | "
            f"{c['quarentena']} | {c['disparos_por_risco']} |"
        )
    return [*linhas, ""]


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Job de inferência diária D-2").parse_args(argv)
    # Antes de tudo (blindagem LGPD do log): o job é o único processo que toca telefone de
    # paciente (`enviar_lembrete`), então nenhuma linha dele pode sair antes de
    # o filtro de redação estar instalado -- e o log do Actions deste
    # repositório é público: o filtro é a última barreira.
    configurar_logging()
    model_version = pre_checagem()
    # Depois da pré-checagem do campeão (sem campeão válido não há fila) e
    # antes de qualquer SMS. Nunca levanta: um canário com problema vira falha
    # do run e a fila vai 100% para o campeão.
    preparacao = canario.preparar_para_job(model_version, params=inference.PARAMS)
    resultado = processar_dia(contexto_canario=preparacao.contexto)
    purgados = purgar_eventos_antigos()
    derivados_purgados = purgar_dados_derivados_antigos()
    # Processo curto: sem o flush, os eventos enfileirados morreriam junto com
    # o processo antes de o worker daemon gravá-los (ver observabilidade.flush).
    observabilidade.flush()
    falhas, avisos = pos_checagem(resultado)
    falhas += preparacao.falhas
    avisos += preparacao.avisos
    if preparacao.contexto is not None:
        falhas += canario.avaliar_execucao(
            preparacao.contexto, resultado.get("por_braco", {}), params=inference.PARAMS
        )
    # Uma linha JSON: no Actions a única saída que sobra é stdout, e o resumo
    # precisa ser grep-ável junto do resto do log. Contadores, nunca a lista de
    # erros: ela carrega `motivo` por agendamento.
    logger.info(
        "job D-2 concluído",
        extra={
            **{
                chave: resultado[chave]
                for chave in (
                    "agendamentos_encontrados",
                    "predicoes_gravadas",
                    "quarentena",
                    "fora_do_dominio",
                    "mensagens_disparadas",
                    "lembretes_sem_predicao",
                    "falhas_de_envio",
                )
            },
            "erros": len(resultado["erros"]),
            "eventos_purgados": purgados,
            "derivados_purgados": derivados_purgados,
            "canario": preparacao.contexto.model_version if preparacao.contexto else None,
            "por_braco": resultado.get("por_braco"),
        },
    )
    _publicar_resumo(
        resultado,
        falhas,
        avisos,
        model_version,
        canario_version=preparacao.contexto.model_version if preparacao.contexto else None,
    )
    return SAIDA_FALHA if falhas else SAIDA_OK


if __name__ == "__main__":
    sys.exit(main())
