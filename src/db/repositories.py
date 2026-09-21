"""
SaúdeJá — acesso a dados (Passo 5), sobre o schema de
`supabase/migrations/20260919000000_init.sql` (pacientes, agendamentos,
predicoes, mensagens_disparadas).

Cada função encapsula uma consulta/gravação específica que a UI (Passo 4) e o
job D-2 (Passo 6) precisam -- nada de query builder genérico aqui, só os
acessos que o produto de fato usa (ver PLANO-IMPLEMENTACAO.md, Passo 5).
"""
from datetime import date, datetime, timedelta

from config_projeto import fuso_da_clinica, hoje_na_clinica
from db.client import obter_client


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
    id_paciente_externo: str, data_nascimento: date, sexo: str, telefone: str
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
    lembrete real via Infobip."""
    client = obter_client()
    resposta = (
        client.table("pacientes")
        .upsert(
            {
                "id_paciente_externo": id_paciente_externo,
                "data_nascimento": data_nascimento.isoformat(),
                "sexo": sexo,
                "telefone": telefone,
            },
            on_conflict="id_paciente_externo",
        )
        .execute()
    )
    return resposta.data[0]


def contar_no_shows_anteriores(id_paciente: str) -> int:
    """Historico_noshow deixou de ser digitado no cadastro (o paciente não
    tem como, nem deveria, autodeclarar isso): é contado a partir dos
    agendamentos passados deste paciente marcados como `status='no_show'`
    nesta clínica. Paciente novo (ou sem nenhum 'no_show' registrado ainda)
    começa em 0 -- zero é o valor correto, não um placeholder."""
    client = obter_client()
    resposta = (
        client.table("agendamentos")
        .select("id", count="exact")
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
) -> dict:
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
    return resposta.data[0]


_COLUNAS_AGENDAMENTO_PARA_INFERENCIA = (
    "id, especialidade, distancia_km, data_hora_agendada, "
    "dias_entre_agendamento_consulta, historico_noshow, "
    "pacientes(data_nascimento, sexo, id_paciente_externo, telefone)"
)


def buscar_agendamentos_d2_pendentes(data_referencia: date) -> list[dict]:
    """Agendamentos de `data_referencia + 2 dias` (D-2, ver
    architecture.md/glossário) ainda sem predição gravada -- o job (Passo 6)
    roda uma vez ao dia e não deve reprocessar quem já tem `predicoes`.

    Seleciona só as colunas que o job de inferência (`construir_features`)
    de fato consome -- de `agendamentos` e do `pacientes` embutido
    (data_nascimento, sexo) -- em vez de `select("*, pacientes(*)")`: evita trafegar/desserializar
    colunas sem uso (ex. `criado_em`, `status`) numa consulta que roda sobre a
    fila inteira do dia. O embed depende do índice em `agendamentos.id_paciente`
    (ver migration `20260920000000_idx_agendamentos_id_paciente.sql`) para o
    join não fazer full scan de `pacientes`."""
    client = obter_client()
    inicio, fim = _intervalo_do_dia(data_referencia + timedelta(days=2))

    agendamentos = (
        client.table("agendamentos")
        .select(_COLUNAS_AGENDAMENTO_PARA_INFERENCIA)
        .gte("data_hora_agendada", inicio)
        .lt("data_hora_agendada", fim)
        .eq("status", "agendado")
        .execute()
        .data
    )
    if not agendamentos:
        return []

    ids = [a["id"] for a in agendamentos]
    predicoes_existentes = (
        client.table("predicoes").select("id_agendamento").in_("id_agendamento", ids).execute().data
    )
    ids_com_predicao = {p["id_agendamento"] for p in predicoes_existentes}
    return [a for a in agendamentos if a["id"] not in ids_com_predicao]


def gravar_predicao(
    id_agendamento: str,
    probabilidade: float,
    classe_prevista: int,
    threshold_usado: float,
    explicacao_shap: list,
    model_version: str,
    explicacao_texto: str | None = None,
) -> dict:
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
            }
        )
        .execute()
    )
    return resposta.data[0]


def registrar_mensagem(id_agendamento: str, canal: str, status_envio: str) -> dict:
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
    return resposta.data[0]


def buscar_fila_do_dia(dia: date | None = None) -> list[dict]:
    """Agendamentos do dia (default hoje) com paciente e última predição
    embutidos, ordenados por probabilidade desc -- quem ainda não tem
    predição (job ainda não rodou/D-2 não bateu) vai para o fim da fila, não
    para o topo, já que não há risco calculado para ordenar."""
    client = obter_client()
    inicio, fim = _intervalo_do_dia(dia or hoje_na_clinica())

    agendamentos = (
        client.table("agendamentos")
        .select("*, pacientes(*), predicoes(*)")
        .gte("data_hora_agendada", inicio)
        .lt("data_hora_agendada", fim)
        .order("data_hora_agendada")
        .execute()
        .data
    )

    def _probabilidade(agendamento: dict) -> float:
        predicoes = agendamento.get("predicoes") or []
        if not predicoes:
            return -1.0
        return max(p["probabilidade"] for p in predicoes)

    return sorted(agendamentos, key=_probabilidade, reverse=True)
