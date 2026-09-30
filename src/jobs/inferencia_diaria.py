"""
SaúdeJá — job de inferência diária D-2 (Passo 6 do plano de implementação).

Busca no Supabase os agendamentos de amanhã até D+2 ainda sem predição (via
`db.repositories.buscar_agendamentos_d2_pendentes` -- a janela de três dias
recupera sozinha um dia em que o cron não rodou), roda a predição+
explicação reaproveitando `src/inference.py`/`src/explain.py` -- os mesmos
módulos que a API (Passo 3) usa, sem lógica de predição duplicada --, grava
o resultado em `predicoes` e decide o disparo de lembrete pago conforme o
threshold de `params.yaml`, sempre registrando a decisão (enviado ou não)
em `mensagens_disparadas` para auditoria (SLA §6).

Importa o modelo em processo (decisão do ADR-005 b), não via HTTP à API --
mesmo motivo do Streamlit: o job roda como parte da imagem do HF Space
(architecture.md §3.2.2), sem depender de a API estar de pé.

Rodar como script (`python src/jobs/inferencia_diaria.py`) ou via
`main()` -- a aba "Dev: disparo manual" do Streamlit (Passo 4) chama esta
última para acompanhar o pipeline sem CLI/cron separados.
"""
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

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
from messaging.client import ErroEnvioInfobip, InfobipClient, StubMessagingClient  # noqa: E402

logger = logging.getLogger(__name__)

# Lembrete enviado a um paciente que NÃO pôde ser predito (dado inválido,
# falha do modelo). Decisão da autora na revisão do Passo 10: a falta custa
# R$ 180 e o SMS custa centavos, então na dúvida o paciente é lembrado -- mas
# como exceção rastreável, com status próprio em `mensagens_disparadas`, e não
# confundida com o disparo normal por risco.
STATUS_ENVIADO_SEM_PREDICAO = "enviado_sem_predicao"


class ProvedorMensageriaDesconhecido(Exception):
    """MESSAGING_PROVIDER aponta para um provedor sem implementação -- erro
    explícito em vez de cair no stub silenciosamente, para não mascarar uma
    configuração de produção errada."""


def obter_cliente_mensageria():
    """`stub` (default) nunca faz rede; `infobip` (Passo 7) envia SMS de
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


def _payload_de_agendamento(agendamento: dict) -> dict:
    """`pacientes.data_nascimento` (não `idade` -- ver migration
    20260920010000_cadastro_pacientes.sql) precisa virar idade aqui, na hora
    da predição, calculada em relação à data da própria consulta -- mesmo
    conceito que `idade` já representa no dataset histórico de treino."""
    paciente = agendamento["pacientes"]
    # Hora da clínica, não a UTC que o Postgres devolve (revisão do Passo 10):
    # sem isto o modelo via `horario` 3h adiantado e o SMS dizia 21:00 para
    # uma consulta das 18:00.
    data_hora_agendada = para_horario_da_clinica(agendamento["data_hora_agendada"])
    data_nascimento = date.fromisoformat(paciente["data_nascimento"])
    return {
        "idade": calcular_idade(data_nascimento, data_hora_agendada.date()),
        "sexo": paciente["sexo"],
        "especialidade": agendamento["especialidade"],
        "distancia_km": float(agendamento["distancia_km"]),
        "dias_entre_agendamento_consulta": agendamento["dias_entre_agendamento_consulta"],
        "historico_noshow": agendamento["historico_noshow"],
        "data_hora_agendada": data_hora_agendada,
    }


def processar_dia(data_referencia: date | None = None, cliente_mensageria=None) -> dict:
    """Roda o pipeline D-2 uma vez para `data_referencia` (default hoje na
    clínica). Devolve contagens (não levanta por agendamento individual malformado
    -- um payload ruim não pode travar a fila inteira do dia) para a aba de
    dev/logs mostrarem o que aconteceu."""
    data_referencia = data_referencia or hoje_na_clinica()
    with observabilidade.medir(
        observabilidade.TIPO_JOB_D2, origem=observabilidade.ORIGEM_JOB
    ) as observado:
        resultado = _processar_fila(data_referencia, cliente_mensageria, observado)
    return resultado


def _processar_fila(data_referencia: date, cliente_mensageria, observado: dict) -> dict:
    """Corpo do job, separado de `processar_dia` só para a instrumentação do
    Passo 8.5 (ADR-006) envolver a execução inteira -- inclusive a busca no
    banco e uma falha antes do primeiro agendamento -- sem indentar a lógica
    de negócio dentro de um `with`. `observado` é o dicionário cedido por
    `observabilidade.medir`: o que for escrito em `observado["detalhe"]` vira
    o `detalhe` do evento `job_d2` gravado ao final."""
    pendentes = repositories.buscar_agendamentos_d2_pendentes(data_referencia)

    resultado = {
        "agendamentos_encontrados": len(pendentes),
        "predicoes_gravadas": 0,
        "mensagens_disparadas": 0,
        "lembretes_sem_predicao": 0,
        "erros": [],
    }
    observado["detalhe"]["agendamentos_encontrados"] = len(pendentes)
    if not pendentes:
        return resultado

    model_path = caminho_de_env("MODEL_PATH", "data/model.pkl")
    model, mapa_especialidade = inference.carregar_modelo(model_path)
    explainer = construir_explicador(model)
    model_version = inference.calcular_model_version(model_path)
    observado["model_version"] = model_version
    threshold = float(inference.PARAMS["decision"]["threshold"])
    cliente_mensageria = cliente_mensageria or obter_cliente_mensageria()

    for agendamento in pendentes:
        inicio = time.perf_counter()
        try:
            payload = _payload_de_agendamento(agendamento)
            X = inference.construir_features(payload, mapa_especialidade)
            probabilidade = float(inference.predizer(model, X)[0])
            contribuicoes = explicar(explainer, X)
        except Exception as exc:
            # Quarentena: QUALQUER falha ao predizer uma linha (especialidade
            # fora do mapa, nulo inesperado, tipo errado vindo do banco) fica
            # restrita àquela linha. Antes só KeyError/Especialidade eram
            # capturadas, e um TypeError abortava o resto da fila -- que, com a
            # janela de um dia só, ficava sem predição para sempre.
            _registrar_quarentena(agendamento, exc, model_version, resultado)
            _enviar_lembrete(
                agendamento, cliente_mensageria, resultado, STATUS_ENVIADO_SEM_PREDICAO
            )
            continue

        classe_prevista = int(probabilidade >= threshold)
        # Mede só o trecho de modelo (features + predição + SHAP), o mesmo que
        # a rota /predict executa, para o p95 do SLO §2 comparar caminhos
        # equivalentes -- a gravação no banco e o envio de SMS que vêm a seguir
        # são custo do job, não da predição.
        observabilidade.registrar_evento(
            tipo=observabilidade.TIPO_PREDICAO,
            origem=observabilidade.ORIGEM_JOB,
            duracao_ms=round((time.perf_counter() - inicio) * 1000),
            model_version=model_version,
        )

        repositories.gravar_predicao(
            id_agendamento=agendamento["id"],
            probabilidade=probabilidade,
            classe_prevista=classe_prevista,
            threshold_usado=threshold,
            explicacao_shap=contribuicoes,
            model_version=model_version,
        )
        resultado["predicoes_gravadas"] += 1

        if classe_prevista:
            _enviar_lembrete(agendamento, cliente_mensageria, resultado, "enviado")
        else:
            repositories.registrar_mensagem(
                id_agendamento=agendamento["id"],
                canal=cliente_mensageria.canal,
                status_envio="nao_enviado",
            )

    observado["detalhe"].update(
        {
            "predicoes_gravadas": resultado["predicoes_gravadas"],
            "mensagens_disparadas": resultado["mensagens_disparadas"],
            "lembretes_sem_predicao": resultado["lembretes_sem_predicao"],
            "erros": len(resultado["erros"]),
        }
    )
    return resultado


def _registrar_quarentena(agendamento: dict, exc: Exception, model_version, resultado) -> None:
    """Agendamento que não pôde ser predito: registra e deixa o job seguir."""
    resultado["erros"].append({"id_agendamento": agendamento["id"], "motivo": str(exc)})
    # Só a CLASSE da exceção vai para `eventos_app`: `str(exc)` traz o dado do
    # agendamento junto, e a tabela de observabilidade não guarda dado de
    # paciente (ADR-006).
    observabilidade.registrar_evento(
        tipo=observabilidade.TIPO_ERRO,
        status=observabilidade.STATUS_ERRO,
        origem=observabilidade.ORIGEM_JOB,
        model_version=model_version,
        detalhe={"excecao": exc.__class__.__name__},
    )
    # `id_agendamento` no log de propósito (Passo 8): é uuid interno, não PII,
    # e sem ele o diagnóstico de "por que este paciente não tem predição" não
    # sai do lugar. `str(exc)` fica de fora -- carrega o dado do agendamento.
    logger.warning(
        "agendamento sem predição",
        extra={"id_agendamento": agendamento["id"], "excecao": exc.__class__.__name__},
    )


def _mensagem_de_lembrete(agendamento: dict) -> str:
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


def _enviar_lembrete(agendamento: dict, cliente_mensageria, resultado: dict, status: str) -> None:
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
        resultado["erros"].append({"id_agendamento": agendamento["id"], "motivo": str(exc)})
        # `str(exc)` só para `ErroEnvioInfobip`, seguro desde o Passo 8 (a
        # mensagem deixou de embutir o corpo da resposta, que ecoava o
        # telefone). Das demais, só a classe.
        motivo = str(exc) if isinstance(exc, ErroEnvioInfobip) else exc.__class__.__name__
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


def purgar_dados_derivados_antigos() -> dict:
    """Retenção de `predicoes`/`mensagens_disparadas` (Passo 8, `docs/LGPD.md`
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


def main() -> dict:
    # Antes de tudo (Passo 8): o job é o único processo que toca telefone de
    # paciente (`enviar_lembrete`), então nenhuma linha dele pode sair antes de
    # o filtro de redação estar instalado.
    configurar_logging()
    resultado = processar_dia()
    purgados = purgar_eventos_antigos()
    derivados_purgados = purgar_dados_derivados_antigos()
    # Processo curto: sem o flush, os eventos enfileirados morreriam junto com
    # o processo antes de o worker daemon gravá-los (ver observabilidade.flush).
    observabilidade.flush()
    # Uma linha JSON em vez do `print` de antes: o job roda por cron no
    # GitHub Actions (Passo 10), onde a única saída que sobra é stdout -- e o
    # resumo precisa ser grep-ável junto do resto do log, não um formato só
    # dele. Contadores, nunca a lista de erros: ela carrega `motivo` por
    # agendamento, que é texto de exceção.
    logger.info(
        "job D-2 concluído",
        extra={
            "agendamentos_encontrados": resultado["agendamentos_encontrados"],
            "predicoes_gravadas": resultado["predicoes_gravadas"],
            "mensagens_disparadas": resultado["mensagens_disparadas"],
            "lembretes_sem_predicao": resultado["lembretes_sem_predicao"],
            "erros": len(resultado["erros"]),
            "eventos_purgados": purgados,
            "derivados_purgados": derivados_purgados,
        },
    )
    return resultado


if __name__ == "__main__":
    main()
