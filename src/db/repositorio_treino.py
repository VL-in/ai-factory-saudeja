"""
SaúdeJá — acesso à base de treino (schema `treino`, ADR-010).

Módulo separado de `db.repositories` de propósito: aquele é o banco da
clínica (a SaúdeJá opera em nome dela); este é a base de treino, de que a
SaúdeJá é controladora. O export de treino importa só este módulo, e
`tests/test_coerencia_repo.py` falha se ele passar a importar o outro.

O schema `treino` não é exposto ao PostgREST e nenhum papel tem acesso direto
às tabelas: **toda operação é uma RPC** para as funções `public.treino_*` da
migration `20261011000000_schema_treino.sql`. Por isso o pseudônimo
(`src/pseudonimo.py`) sempre vai no corpo do POST, nunca na URL, que fica no
log de API do Supabase (LGPD.md §8.1). Mesmo client e mesma chave secreta do
banco da clínica (decisão D1 do ADR-010).

As regras (linha só com consentimento vigente, eventos só de inserção,
transições dos pedidos, datas do relógio do banco) estão nos triggers, não
aqui: este módulo só traduz chamadas Python em RPC.
"""
from datetime import datetime
from typing import Any, cast

from db.client import obter_client

Linha = dict[str, Any]

TIPOS_PEDIDO = ("eliminacao", "revogacao")
CANAIS_PEDIDO = ("autoagendamento", "clinica", "saudeja")


def _rpc(funcao: str, parametros: dict[str, Any] | None = None) -> Any:
    return obter_client().rpc(funcao, parametros or {}).execute().data


def _linhas(dados: object) -> list[Linha]:
    return cast(list[Linha], dados or [])


def eventos_do_titular(id_titular: str) -> list[Linha]:
    """Eventos ainda não eliminados do titular, do mais antigo ao mais recente,
    no formato que `consentimento.consentimentos_vigentes` espera."""
    return _linhas(_rpc("treino_eventos_do_titular", {"p_id_titular": id_titular}))


def registrar_consentimentos(
    id_titular: str,
    decisoes: list[tuple[str, str]],
    versao_termo: str,
    canal: str,
    consentido_por: str,
    registrado_por: str | None = None,
) -> int:
    """Um evento por par (finalidade, ação), num insert só. `registrado_em` é
    o relógio do banco (trigger). Devolve quantos eventos foram gravados."""
    if not decisoes:
        return 0
    return int(
        _rpc(
            "treino_registrar_consentimentos",
            {
                "p_id_titular": id_titular,
                "p_decisoes": [{"finalidade": f, "acao": a} for f, a in decisoes],
                "p_versao_termo": versao_termo,
                "p_canal": canal,
                "p_consentido_por": consentido_por,
                "p_registrado_por": registrado_por,
            },
        )
    )


def inserir_consulta(
    id_consulta: str,
    id_titular: str,
    idade: int,
    sexo: str,
    especialidade: str,
    distancia_km: float,
    dias_entre_agendamento_consulta: int,
    historico_noshow: int,
    data_hora_agendada: datetime,
) -> bool:
    """Linha de treino de uma consulta. O banco a recusa se o titular não tiver
    consentimento vigente. Devolve se a linha foi criada (`False` quando o
    mesmo agendamento já estava lá)."""
    return bool(
        _rpc(
            "treino_inserir_consulta",
            {
                "p_id_consulta": id_consulta,
                "p_id_titular": id_titular,
                "p_idade": idade,
                "p_sexo": sexo,
                "p_especialidade": especialidade,
                "p_distancia_km": distancia_km,
                "p_dias_entre_agendamento_consulta": dias_entre_agendamento_consulta,
                "p_historico_noshow": historico_noshow,
                "p_data_hora_agendada": data_hora_agendada.isoformat(),
            },
        )
    )


def sincronizar_desfechos(itens: list[Linha]) -> int:
    """Copia `status` e `lembrete_enviado` do banco da clínica para as linhas
    de treino que existem. Cada item: `id_consulta` (pseudônimo), `status`,
    `lembrete_enviado`. Chave sem linha (quem não consentiu) não faz nada.
    Devolve quantas linhas mudaram."""
    if not itens:
        return 0
    return int(_rpc("treino_sincronizar_desfechos", {"p_itens": itens}))


def consultas_para_export(desde: datetime) -> list[Linha]:
    """Consultas com desfecho conhecido (`concluido`/`no_show`) a partir de
    `desde`, de titulares com consentimento vigente."""
    return _linhas(_rpc("treino_consultas_para_export", {"p_desde": desde.isoformat()}))


def contar_sem_desfecho(desde: datetime, antes_de: datetime) -> int:
    """Consultas da janela ainda `agendado` -- completude de rótulo do export."""
    return int(
        _rpc(
            "treino_contar_sem_desfecho",
            {"p_desde": desde.isoformat(), "p_antes_de": antes_de.isoformat()},
        )
    )


def registrar_pedido(id_titular: str, tipo: str, canal: str) -> str:
    """Pedido sobre os dados de treino, sempre `pendente`. A SaúdeJá é a
    controladora: não há confirmação da clínica. Devolve o id do pedido."""
    if tipo not in TIPOS_PEDIDO:
        raise ValueError(f"tipo de pedido desconhecido: {tipo!r}")
    if canal not in CANAIS_PEDIDO:
        raise ValueError(f"canal de pedido desconhecido: {canal!r}")
    return str(
        _rpc(
            "treino_registrar_pedido",
            {"p_id_titular": id_titular, "p_tipo": tipo, "p_canal": canal},
        )
    )


def pedidos_pendentes() -> list[Linha]:
    """Fila do workflow de purga, do mais antigo ao mais recente."""
    return _linhas(_rpc("treino_pedidos_pendentes"))


def eliminar_titular(id_titular: str) -> int:
    """Apaga as linhas de treino do titular e marca os eventos dele como
    eliminados (ficam 365 dias como prova). **Só depois** de apagar no remote
    do DVC as versões que o contêm (ver a migration). Devolve quantas linhas
    foram apagadas."""
    return int(_rpc("treino_eliminar_titular", {"p_id_titular": id_titular}))


def marcar_pedido_executado(id_pedido: str) -> bool:
    """Executado e sem pseudônimo no mesmo update. Devolve se o pedido mudou
    (pedido que já não estava pendente não muda)."""
    return bool(_rpc("treino_concluir_pedido", {"p_id": id_pedido, "p_situacao": "executado"}))


def recusar_pedido(id_pedido: str) -> bool:
    """Recusado mantém o pseudônimo, como prova de a quem a recusa foi dada."""
    return bool(_rpc("treino_concluir_pedido", {"p_id": id_pedido, "p_situacao": "recusado"}))


def purgar(retencao_consultas_dias: int) -> dict[str, int]:
    """Consultas fora da janela, provas eliminadas há mais de 365 dias e
    recusas antigas. Cortes pelo relógio do banco."""
    resultado = _rpc("treino_purgar", {"p_retencao_consultas_dias": retencao_consultas_dias})
    return cast(dict[str, int], resultado)
