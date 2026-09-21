"""
SaúdeJá — job de inferência diária D-2 (Passo 6 do plano de implementação).

Busca no Supabase os agendamentos marcados para dois dias à frente (via
`db.repositories.buscar_agendamentos_d2_pendentes`), roda a predição+
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
import os
import sys
from datetime import date, datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import db.repositories as repositories  # noqa: E402
import inference  # noqa: E402
from config_projeto import caminho_de_env, hoje_na_clinica  # noqa: E402
from explain import construir_explicador, explicar  # noqa: E402
from features import calcular_idade  # noqa: E402
from messaging.client import StubMessagingClient  # noqa: E402


class ProvedorMensageriaDesconhecido(Exception):
    """MESSAGING_PROVIDER aponta para um provedor sem implementação real
    ainda (Infobip é o Passo 7) -- erro explícito em vez de cair no stub
    silenciosamente, para não mascarar uma configuração de produção errada."""


def obter_cliente_mensageria():
    """`StubMessagingClient` é o único provedor até o Passo 7 (Infobip). O
    nome da env var já é `MESSAGING_PROVIDER` (mesma que o Passo 7 vai usar)
    para o job não precisar mudar quando o provedor real for ligado."""
    provedor = (os.environ.get("MESSAGING_PROVIDER") or "stub").strip().lower()
    if provedor == "stub":
        return StubMessagingClient()
    raise ProvedorMensageriaDesconhecido(
        f"MESSAGING_PROVIDER={provedor!r} sem implementação ainda (Passo 7) -- use 'stub'"
    )


def _payload_de_agendamento(agendamento: dict) -> dict:
    """`pacientes.data_nascimento` (não `idade` -- ver migration
    20260920010000_cadastro_pacientes.sql) precisa virar idade aqui, na hora
    da predição, calculada em relação à data da própria consulta -- mesmo
    conceito que `idade` já representa no dataset histórico de treino."""
    paciente = agendamento["pacientes"]
    data_hora_agendada = datetime.fromisoformat(agendamento["data_hora_agendada"])
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
    pendentes = repositories.buscar_agendamentos_d2_pendentes(data_referencia)

    resultado = {
        "agendamentos_encontrados": len(pendentes),
        "predicoes_gravadas": 0,
        "mensagens_disparadas": 0,
        "erros": [],
    }
    if not pendentes:
        return resultado

    model_path = caminho_de_env("MODEL_PATH", "data/model.pkl")
    model, mapa_especialidade = inference.carregar_modelo(model_path)
    explainer = construir_explicador(model)
    model_version = inference.calcular_model_version(model_path)
    threshold = float(inference.PARAMS["decision"]["threshold"])
    cliente_mensageria = cliente_mensageria or obter_cliente_mensageria()

    for agendamento in pendentes:
        try:
            payload = _payload_de_agendamento(agendamento)
            X = inference.construir_features(payload, mapa_especialidade)
        except (KeyError, inference.EspecialidadeDesconhecidaError) as exc:
            # Agendamento malformado (especialidade fora do mapa, paciente
            # sem data_nascimento/sexo): registra e segue para o próximo --
            # não é motivo para deixar a fila inteira sem predição.
            resultado["erros"].append({"id_agendamento": agendamento["id"], "motivo": str(exc)})
            continue

        probabilidade = float(inference.predizer(model, X)[0])
        classe_prevista = int(probabilidade >= threshold)
        contribuicoes = explicar(explainer, X)

        repositories.gravar_predicao(
            id_agendamento=agendamento["id"],
            probabilidade=probabilidade,
            classe_prevista=classe_prevista,
            threshold_usado=threshold,
            explicacao_shap=contribuicoes,
            model_version=model_version,
        )
        resultado["predicoes_gravadas"] += 1

        id_paciente_externo = agendamento["pacientes"]["id_paciente_externo"]
        if classe_prevista:
            cliente_mensageria.enviar_lembrete(
                id_paciente_externo=id_paciente_externo,
                mensagem=(
                    f"Olá! Confirmamos sua consulta de {agendamento['especialidade']} em "
                    f"{payload['data_hora_agendada']:%d/%m/%Y às %H:%M}. Poderá comparecer?"
                ),
            )
            repositories.registrar_mensagem(
                id_agendamento=agendamento["id"], canal="whatsapp", status_envio="enviado"
            )
            resultado["mensagens_disparadas"] += 1
        else:
            repositories.registrar_mensagem(
                id_agendamento=agendamento["id"], canal="whatsapp", status_envio="nao_enviado"
            )

    return resultado


def main() -> dict:
    resultado = processar_dia()
    print(
        f"job D-2: {resultado['agendamentos_encontrados']} agendamento(s) encontrado(s), "
        f"{resultado['predicoes_gravadas']} predição(ões) gravada(s), "
        f"{resultado['mensagens_disparadas']} mensagem(ns) disparada(s), "
        f"{len(resultado['erros'])} erro(s)"
    )
    return resultado


if __name__ == "__main__":
    main()
