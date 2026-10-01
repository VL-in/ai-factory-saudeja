"""
SaúdeJá — acesso a dados (Passo 5), sobre o schema de
`supabase/migrations/20260919000000_init.sql` (pacientes, agendamentos,
predicoes, mensagens_disparadas).

Cada função encapsula uma consulta/gravação específica que a UI (Passo 4) e o
job D-2 (Passo 6) precisam -- nada de query builder genérico aqui, só os
acessos que o produto de fato usa (ver PLANO-IMPLEMENTACAO.md, Passo 5).
"""
from datetime import date, datetime, timedelta
from typing import Any, cast

from postgrest.types import CountMethod

from config_projeto import fuso_da_clinica, hoje_na_clinica
from db.client import obter_client

Linha = dict[str, Any]


def _linhas(dados: object) -> list[Linha]:
    """`resposta.data` de um select/insert/update do PostgREST: sempre um
    array JSON de objetos. O `supabase-py` o tipa como JSON genérico (`bool |
    str | ... | None`); o cast aqui é a fronteira que diz ao type-check o que o
    PostgREST garante, em vez de espalhar `# type: ignore` pelo módulo."""
    return cast(list[Linha], dados or [])


def _primeira(dados: object) -> Linha:
    """Primeira linha devolvida por um insert/upsert/update com retorno."""
    return _linhas(dados)[0]


def _intervalo_do_dia(dia: date) -> tuple[str, str]:
    """[início, fim) do dia **no fuso da clínica**, para filtrar
    `data_hora_agendada` (timestamptz) por data civil.

    Não em UTC: a clínica é brasileira (UTC-3) e "fila do dia 19/09" são as
    consultas de 19/09 em São Paulo. Uma janela UTC cobriria das 21h de 18/09
    às 21h de 19/09 no horário local -- deslocamento que faria o job D-2
    pular agendamentos do fim do dia e a fila mostrar os do dia anterior.
    Cada extremo é construído com datetime.combine (em vez de somar um
    timedelta ao início), para a meia-noite civil continuar exata mesmo se o
    fuso voltar a ter horário de verão."""
    fuso = fuso_da_clinica()
    inicio = datetime.combine(dia, datetime.min.time(), tzinfo=fuso)
    fim = datetime.combine(dia + timedelta(days=1), datetime.min.time(), tzinfo=fuso)
    return inicio.isoformat(), fim.isoformat()


def verificar_conexao() -> None:
    """Consulta mais barata que ainda prova o que interessa: que as
    credenciais autenticam E que o schema esperado está aplicado (uma chave
    válida contra um banco sem as migrations também é um banco inútil).
    Levanta a exceção original do supabase-py -- quem chama decide o que
    fazer (ui/logic.py::status_banco traduz para a sidebar)."""
    obter_client().table("pacientes").select("id").limit(1).execute()


def inserir_paciente(
    id_paciente_externo: str,
    data_nascimento: date,
    sexo: str,
    telefone: str,
    nome_completo: str | None = None,
) -> dict:
    """Upsert por `id_paciente_externo` (unique -- ver migration): recadastrar
    o mesmo paciente atualiza os dados em vez de duplicar a linha.

    `id_paciente_externo` já chega como o hash do CPF (gerado em
    `ui/logic.py::_id_paciente_externo_de_cpf`), nunca o CPF em si -- esta
    função só persiste o que recebe. `data_nascimento` substitui `idade`
    (Sec4.1/cadastro realista): a idade em si é calculada sob demanda por
    `features.calcular_idade`, nunca gravada. `telefone` (Passo 7, migration
    `20260920020000_telefone_paciente.sql`) já chega normalizado
    (`ui/logic.py::normalizar_telefone`) -- é a exceção deliberada à
    minimização de PII: sem contato, o job D-2 não tem para onde mandar o
    lembrete real via Infobip.

    `nome_completo` (migration `20260928000000_nome_paciente.sql`, ADR-007) é a
    segunda exceção: a "Fila do dia" é operada por uma pessoa, que precisa
    chamar o paciente pelo nome -- o hash não serve para isso. Opcional na
    assinatura porque as linhas cadastradas antes dela realmente não têm nome
    para recuperar, e porque `None` não pode sobrescrever um nome já gravado
    (ver `_sem_sobrescrever_com_nulo` abaixo)."""
    client = obter_client()
    registro = {
        "id_paciente_externo": id_paciente_externo,
        "data_nascimento": data_nascimento.isoformat(),
        "sexo": sexo,
        "telefone": telefone,
    }
    if nome_completo:
        # Chave ausente, não `None`: o upsert é por `id_paciente_externo`, e
        # mandar `nome_completo: None` APAGARIA o nome de um paciente que já o
        # tinha, num recadastro feito por um caminho que não coleta nome.
        registro["nome_completo"] = nome_completo.strip()
    resposta = (
        client.table("pacientes")
        .upsert(registro, on_conflict="id_paciente_externo")
        .execute()
    )
    return _primeira(resposta.data)


def contar_no_shows_anteriores(id_paciente: str) -> int:
    """Historico_noshow deixou de ser digitado no cadastro (o paciente não
    tem como, nem deveria, autodeclarar isso): é contado a partir dos
    agendamentos passados deste paciente marcados como `status='no_show'`
    nesta clínica. Paciente novo (ou sem nenhum 'no_show' registrado ainda)
    começa em 0 -- zero é o valor correto, não um placeholder."""
    client = obter_client()
    resposta = (
        client.table("agendamentos")
        .select("id", count=CountMethod.exact)
        .eq("id_paciente", id_paciente)
        .eq("status", "no_show")
        .execute()
    )
    return resposta.count or 0


def inserir_agendamento(
    id_paciente: str,
    especialidade: str,
    distancia_km: float,
    data_hora_agendada: datetime,
    dias_entre_agendamento_consulta: int,
    historico_noshow: int,
) -> Linha:
    client = obter_client()
    resposta = (
        client.table("agendamentos")
        .insert(
            {
                "id_paciente": id_paciente,
                "especialidade": especialidade,
                "distancia_km": distancia_km,
                "data_hora_agendada": data_hora_agendada.isoformat(),
                "dias_entre_agendamento_consulta": dias_entre_agendamento_consulta,
                "historico_noshow": historico_noshow,
            }
        )
        .execute()
    )
    return _primeira(resposta.data)


_COLUNAS_AGENDAMENTO_PARA_INFERENCIA = (
    "id, especialidade, distancia_km, data_hora_agendada, "
    "dias_entre_agendamento_consulta, historico_noshow, "
    "pacientes(data_nascimento, sexo, id_paciente_externo, telefone)"
)


def buscar_agendamentos_d2_pendentes(data_referencia: date) -> list[Linha]:
    """Agendamentos ainda `agendado`, **de amanhã até D+2**, sem predição
    gravada e sem lembrete já enviado -- a fila do job diário (Passo 6).

    A janela era exatamente D+2 até a revisão do Passo 10 (2026-09-29), e isso
    tinha dois buracos: (a) se o cron falhasse ou fosse descartado num dia, os
    pacientes daquele dia nunca eram preditos -- no dia seguinte já estavam em
    D+1, fora da janela; (b) um agendamento feito com menos de dois dias de
    antecedência nunca entrava em janela nenhuma. Com [amanhã, D+2], um dia
    perdido é recuperado sozinho na execução seguinte. Hoje fica de fora: o
    lembrete no próprio dia da consulta chegaria tarde demais para servir.

    O filtro é idempotente em sequência: quem já tem `predicoes` ou já recebeu
    lembrete (`lembrete_enviado`, inclusive o enviado sem predição por dado
    inválido) não volta. Um agendamento em quarentena cujo envio falhou volta
    no dia seguinte, e é o que se quer -- é uma nova tentativa de envio.

    Seleciona só as colunas que o job consome, em vez de
    `select("*, pacientes(*)")`. O embed depende do índice em
    `agendamentos.id_paciente` (migration
    `20260920000000_idx_agendamentos_id_paciente.sql`)."""
    client = obter_client()
    inicio, _ = _intervalo_do_dia(data_referencia + timedelta(days=1))
    _, fim = _intervalo_do_dia(data_referencia + timedelta(days=2))

    agendamentos = _linhas(
        client.table("agendamentos")
        .select(_COLUNAS_AGENDAMENTO_PARA_INFERENCIA)
        .gte("data_hora_agendada", inicio)
        .lt("data_hora_agendada", fim)
        .eq("status", "agendado")
        .eq("lembrete_enviado", False)
        .order("data_hora_agendada")
        .execute()
        .data
    )
    if not agendamentos:
        return []

    ids = [a["id"] for a in agendamentos]
    predicoes_existentes = _linhas(
        client.table("predicoes").select("id_agendamento").in_("id_agendamento", ids).execute().data
    )
    ids_com_predicao = {p["id_agendamento"] for p in predicoes_existentes}
    return [a for a in agendamentos if a["id"] not in ids_com_predicao]


def marcar_lembrete_enviado(id_agendamento: str) -> None:
    """Grava no próprio agendamento que o paciente recebeu lembrete (revisão
    do Passo 10). Em `agendamentos`, e não só em `mensagens_disparadas`, por
    dois motivos: `mensagens_disparadas` é purgada em 365 dias (LGPD §5), e o
    re-treino precisa do dado enquanto o agendamento existir -- o SMS é uma
    intervenção que muda o desfecho (feedback loop); e é o filtro que impede
    reenvio na janela de três dias do job."""
    obter_client().table("agendamentos").update({"lembrete_enviado": True}).eq(
        "id", id_agendamento
    ).execute()


def gravar_predicao(
    id_agendamento: str,
    probabilidade: float,
    classe_prevista: int,
    threshold_usado: float,
    explicacao_shap: list[dict[str, Any]],
    model_version: str,
    explicacao_texto: str | None = None,
    fora_do_dominio: bool | None = None,
) -> Linha:
    """`fora_do_dominio` (Passo 10.3, migration
    `20260930000000_fora_do_dominio.sql`): o agendamento tinha algum campo que
    o modelo não viu no treino (distância > 50 km, antecedência > 90 dias...).
    A predição é gravada mesmo assim -- o contrato manda marcar, nunca
    rejeitar -- e a marca aparece na fila do dia. `None` é "não verificado",
    que é o que as predições anteriores à coluna carregam."""
    client = obter_client()
    resposta = (
        client.table("predicoes")
        .insert(
            {
                "id_agendamento": id_agendamento,
                "probabilidade": probabilidade,
                "classe_prevista": classe_prevista,
                "threshold_usado": threshold_usado,
                "explicacao_shap": explicacao_shap,
                "explicacao_texto": explicacao_texto,
                "model_version": model_version,
                "fora_do_dominio": fora_do_dominio,
            }
        )
        .execute()
    )
    return _primeira(resposta.data)


def registrar_mensagem(id_agendamento: str, canal: str, status_envio: str) -> Linha:
    """Auditoria de disparo (SLA §6) -- uma linha por agendamento processado
    pelo job D-2 (Passo 6), enviado ou não: `status_envio` distingue
    'enviado' (probabilidade acima do threshold, lembrete pago disparado) de
    'nao_enviado' (abaixo do threshold, nenhum custo de mensageria), então a
    tabela sempre reflete a decisão tomada, não só os envios reais."""
    client = obter_client()
    resposta = (
        client.table("mensagens_disparadas")
        .insert({"id_agendamento": id_agendamento, "canal": canal, "status_envio": status_envio})
        .execute()
    )
    return _primeira(resposta.data)


STATUSES_DESFECHO = ("concluido", "no_show", "cancelado")


class DesfechoForaDePrazo(Exception):
    """Tentativa de registrar desfecho de consulta que ainda não aconteceu."""


def atualizar_status_agendamento(
    id_agendamento: str, status: str, hoje: date | None = None
) -> Linha:
    """Registra o desfecho real de um agendamento (Passo 9.0), acionado pela
    aba "Fila do dia". É a fonte do `historico_noshow` automático do cadastro
    e, via `export_treino.py`, do dataset real do re-treino mensal (Passo 9.1).

    **Só para consulta de hoje ou anterior** (revisão do Passo 10): marcar
    no-show numa consulta futura gravaria um rótulo que não aconteceu no
    dataset de treino e ainda inflaria o `historico_noshow` dos próximos
    cadastros do paciente. A UI já desabilita os botões; aqui é a defesa em
    profundidade -- o filtro de data vai no próprio UPDATE, então não há
    janela entre checar e gravar. Nenhuma linha afetada vira erro explícito.

    Não revalida `status` contra `STATUSES_DESFECHO` -- o check constraint do
    Postgres já rejeita valor fora do domínio."""
    client = obter_client()
    _, fim_de_hoje = _intervalo_do_dia(hoje or hoje_na_clinica())
    resposta = (
        client.table("agendamentos")
        .update({"status": status})
        .eq("id", id_agendamento)
        .lt("data_hora_agendada", fim_de_hoje)
        .execute()
    )
    if not resposta.data:
        raise DesfechoForaDePrazo(
            "desfecho só pode ser registrado para consulta de hoje ou anterior"
        )
    return _primeira(resposta.data)


_COLUNAS_AGENDAMENTO_PARA_EXPORT = (
    "id, especialidade, distancia_km, data_hora_agendada, "
    "dias_entre_agendamento_consulta, historico_noshow, status, lembrete_enviado, "
    "pacientes(id_paciente_externo, data_nascimento, sexo)"
)


def buscar_agendamentos_com_desfecho() -> list[Linha]:
    """Agendamentos com desfecho real conhecido (`concluido` ou `no_show`,
    Passo 9.0) -- fonte de dado real de `src/export_treino.py`. Sem
    `telefone` no select, mesma minimização de PII de
    `buscar_agendamentos_d2_pendentes`: o export nunca deve ter como emitir
    essa coluna, mesmo por acidente."""
    client = obter_client()
    return _linhas(
        client.table("agendamentos")
        .select(_COLUNAS_AGENDAMENTO_PARA_EXPORT)
        .in_("status", ["concluido", "no_show"])
        .order("data_hora_agendada")
        .execute()
        .data
    )


def contar_consultas_sem_desfecho(antes_de: date) -> int:
    """Consultas de antes de `antes_de` (dia civil da clínica) ainda
    `agendado`: o desfecho não foi registrado. Completude de rótulo do
    re-treino (Passo 10.3), medida no export porque só o banco a enxerga."""
    inicio, _ = _intervalo_do_dia(antes_de)
    resposta = (
        obter_client()
        .table("agendamentos")
        .select("id", count=CountMethod.exact)
        .eq("status", "agendado")
        .lt("data_hora_agendada", inicio)
        .execute()
    )
    return resposta.count or 0


# Lista explícita, em vez do `*, pacientes(*), predicoes(*)` que vigorava até
# 2026-09-28 (ADR-007). O curinga fazia TODA coluna nova de `pacientes` fluir
# para a camada de UI sem ninguém decidir isso: `telefone` já ia (sem uso, já
# que `ItemFila` não o carrega) e `nome_completo` passaria a ir por acidente,
# não por escolha. Numa tabela que guarda PII por exceção, a lista de colunas é
# a fronteira -- e é ela que o teste de coerência consegue inspecionar.
_COLUNAS_FILA_DO_DIA = (
    "id, especialidade, data_hora_agendada, status, "
    "pacientes(id_paciente_externo, nome_completo), "
    "predicoes(criado_em, probabilidade, classe_prevista, explicacao_shap, "
    "explicacao_texto, model_version, fora_do_dominio)"
)


def buscar_fila_do_dia(dia: date | None = None) -> list[Linha]:
    """Agendamentos do dia (default hoje) com paciente e última predição
    embutidos, ordenados por probabilidade desc -- quem ainda não tem
    predição (job ainda não rodou/D-2 não bateu) vai para o fim da fila, não
    para o topo, já que não há risco calculado para ordenar.

    Traz `nome_completo` (ADR-007) e **não** traz `telefone`: o funcionário
    precisa chamar o paciente pelo nome, não discar para ele -- quem envia
    mensagem é o job D-2, por outro caminho e com outro select."""
    client = obter_client()
    inicio, fim = _intervalo_do_dia(dia or hoje_na_clinica())

    agendamentos = _linhas(
        client.table("agendamentos")
        .select(_COLUNAS_FILA_DO_DIA)
        .gte("data_hora_agendada", inicio)
        .lt("data_hora_agendada", fim)
        .order("data_hora_agendada")
        .execute()
        .data
    )

    def _probabilidade(agendamento: Linha) -> float:
        predicoes = agendamento.get("predicoes") or []
        if not predicoes:
            return -1.0
        return max(p["probabilidade"] for p in predicoes)

    return sorted(agendamentos, key=_probabilidade, reverse=True)


# --- observabilidade de aplicação (Passo 8.5, ADR-006) -------------------------


def inserir_evento_app(
    tipo: str,
    status: str,
    origem: str,
    duracao_ms: int | None = None,
    model_version: str | None = None,
    detalhe: dict[str, Any] | None = None,
) -> Linha:
    """Grava uma linha em `eventos_app` (migration
    `20260921000000_eventos_app.sql`). Chamada **só** pelo worker de
    `src/observabilidade.py`, nunca direto do caminho de uma predição: é lá que
    mora a garantia de que o registro não bloqueia nem levanta.

    Não sanitiza `detalhe` aqui -- isso já aconteceu em
    `observabilidade.sanitizar_detalhe`, antes de o evento entrar na fila, para
    o dado proibido nunca chegar a existir num objeto a caminho do banco."""
    client = obter_client()
    resposta = (
        client.table("eventos_app")
        .insert(
            {
                "tipo": tipo,
                "status": status,
                "origem": origem,
                "duracao_ms": duracao_ms,
                "model_version": model_version,
                "detalhe": detalhe or {},
            }
        )
        .execute()
    )
    return _primeira(resposta.data)


def buscar_eventos_app(desde: datetime, limite: int = 5000) -> list[Linha]:
    """Eventos a partir de `desde`, mais recentes primeiro.

    `limite` é explícito porque a agregação (p95) é feita em Python sobre estas
    linhas -- o PostgREST não expõe `percentile_cont`. Quem chama compara
    `len(...)` com o limite para saber se a janela foi truncada: um p95
    calculado sobre uma fatia silenciosamente cortada mentiria, e é justamente
    um número que vai para o pitch (SLO §2)."""
    client = obter_client()
    return _linhas(
        client.table("eventos_app")
        .select("criado_em, tipo, origem, duracao_ms, status, model_version, detalhe")
        .gte("criado_em", desde.isoformat())
        .order("criado_em", desc=True)
        .limit(limite)
        .execute()
        .data
    )


def ultimo_evento_app(tipo: str, status: str | None = None) -> Linha | None:
    """Evento mais recente de um tipo -- usado para "última execução
    bem-sucedida do job D-2" na aba de observabilidade (SLO §5). O
    dead-man's-switch externo (Healthchecks.io) cobre o caso em que o job nem
    chega a rodar; este aqui é a visão de dentro, para o funcionário."""
    client = obter_client()
    consulta = client.table("eventos_app").select("*").eq("tipo", tipo)
    if status:
        consulta = consulta.eq("status", status)
    linhas = _linhas(consulta.order("criado_em", desc=True).limit(1).execute().data)
    return linhas[0] if linhas else None


def contar_predicoes_e_explicacoes(desde: datetime) -> tuple[int, int]:
    """(predições gravadas, predições com `explicacao_shap`) desde `desde` --
    numerador e denominador do SLO §4 (100% das predições com explicação),
    contados no banco (`count='exact'`) em vez de trazer as linhas: a coluna é
    um jsonb que pode ser grande, e aqui só interessa quantas existem."""
    client = obter_client()
    total = (
        client.table("predicoes")
        .select("id", count=CountMethod.exact)
        .gte("criado_em", desde.isoformat())
        .execute()
        .count
        or 0
    )
    com_explicacao = (
        client.table("predicoes")
        .select("id", count=CountMethod.exact)
        .gte("criado_em", desde.isoformat())
        .not_.is_("explicacao_shap", "null")
        .execute()
        .count
        or 0
    )
    return total, com_explicacao


def purgar_dados_derivados(anteriores_a: datetime) -> dict[str, int]:
    """Retenção das tabelas **derivadas** (`predicoes`,
    `mensagens_disparadas`), política do Passo 8 / `docs/LGPD.md` §5.

    Pendurada na mesma purga diária de `eventos_app` pelo mesmo motivo do
    ADR-006: o job já roda uma vez por dia, e agendador novo é peça de infra a
    manter. Devolve o que saiu de cada tabela, para o log do job dizer o que
    aconteceu em vez de um total indistinto.

    `pacientes`/`agendamentos` não entram -- ver
    `config_projeto.retencao_dados_derivados_dias`. Apagar `predicoes` não afeta
    o re-treino: `src/export_treino.py` lê o desfecho de `agendamentos.status`,
    nunca a probabilidade que o modelo previu.
    """
    client = obter_client()
    removidas: dict[str, int] = {}
    for tabela in ("predicoes", "mensagens_disparadas"):
        linhas = (
            client.table(tabela)
            .delete()
            .lt("criado_em", anteriores_a.isoformat())
            .execute()
            .data
        )
        removidas[tabela] = len(linhas or [])
    return removidas


def purgar_eventos_app(anteriores_a: datetime) -> int:
    """Política de retenção do ADR-006 (a tabela divide o teto do free tier do
    Supabase com os dados do produto). Executada pelo job diário, não por
    pg_cron: o job já roda uma vez por dia e um agendador novo seria mais uma
    peça de infra a manter. Devolve quantas linhas saíram."""
    client = obter_client()
    removidas = (
        client.table("eventos_app")
        .delete()
        .lt("criado_em", anteriores_a.isoformat())
        .execute()
        .data
    )
    return len(removidas or [])
