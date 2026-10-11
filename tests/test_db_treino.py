"""
SaúdeJá — base de treino (schema `treino`, ADR-010) contra o Supabase CLI
local. Mesmo pré-requisito de tests/test_db.py: `supabase start` na raiz.

Cada teste usa pseudônimos próprios (uuid aleatório), porque os eventos de
consentimento são só de inserção e não há como limpá-los entre testes: é
justamente uma das garantias testadas aqui.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from postgrest.exceptions import APIError

from test_db import _status_supabase_local

VERSAO = "teste-int"


@pytest.fixture
def treino(monkeypatch):
    status = _status_supabase_local()
    monkeypatch.setenv("SUPABASE_URL", status["API_URL"])
    monkeypatch.setenv("SUPABASE_SECRET_KEY", status["SECRET_KEY"])

    import db.client as client_module
    import db.repositorio_treino as repositorio_treino

    client_module._cliente = None
    yield repositorio_treino
    client_module._cliente = None


def _pseudonimo() -> str:
    return uuid.uuid4().hex


def _consentir(repo, id_titular, acao="concedido"):
    repo.registrar_consentimentos(
        id_titular, [("treino", acao)], VERSAO, "autoagendamento", "titular"
    )


def _inserir(repo, id_titular, dias=-1, id_consulta=None):
    id_consulta = id_consulta or _pseudonimo()
    repo.inserir_consulta(
        id_consulta=id_consulta,
        id_titular=id_titular,
        idade=40,
        sexo="F",
        especialidade="cardiologia",
        distancia_km=5.5,
        dias_entre_agendamento_consulta=7,
        historico_noshow=1,
        data_hora_agendada=datetime.now(timezone.utc) + timedelta(days=dias),
    )
    return id_consulta


def _exportadas(repo, ids):
    desde = datetime.now(timezone.utc) - timedelta(days=365)
    return [c for c in repo.consultas_para_export(desde) if c["id_consulta"] in ids]


# --- a fronteira do schema --------------------------------------------------------


@pytest.mark.integracao
def test_nem_a_chave_secreta_le_o_schema_treino_diretamente(treino):
    """Decisão D1: mesma chave, schema fechado. O único caminho são as RPCs."""
    import db.client as client_module

    client = client_module.obter_client()
    with pytest.raises(APIError):
        client.schema("treino").table("consultas_treino").select("*").execute()
    # A tabela de eventos saiu do banco da clínica.
    with pytest.raises(APIError):
        client.table("eventos_consentimento").select("*").execute()


@pytest.mark.integracao
def test_chave_publicavel_nao_executa_nenhuma_funcao_do_treino(treino):
    from supabase import create_client

    status = _status_supabase_local()
    publico = create_client(status["API_URL"], status["PUBLISHABLE_KEY"])
    for funcao, parametros in [
        ("treino_eventos_do_titular", {"p_id_titular": "x"}),
        ("treino_pedidos_pendentes", {}),
        ("treino_purgar", {"p_retencao_consultas_dias": 0}),
    ]:
        with pytest.raises(APIError, match="permission denied"):
            publico.rpc(funcao, parametros).execute()


# --- consentimento e linha de treino -------------------------------------------------


@pytest.mark.integracao
def test_linha_de_treino_sem_consentimento_e_recusada_pelo_banco(treino):
    titular = _pseudonimo()

    with pytest.raises(APIError, match="sem consentimento vigente"):
        _inserir(treino, titular)

    _consentir(treino, titular)
    _consentir(treino, titular, "revogado")

    with pytest.raises(APIError, match="sem consentimento vigente"):
        _inserir(treino, titular)


@pytest.mark.integracao
def test_no_empate_de_instante_a_revogacao_vence_no_banco(treino):
    """Mesma regra de `consentimento._ordem`: dois eventos do mesmo insert têm
    o mesmo `now()`, e sem consentimento é o lado seguro."""
    titular = _pseudonimo()
    treino.registrar_consentimentos(
        titular,
        [("treino", "concedido"), ("treino", "revogado")],
        VERSAO,
        "autoagendamento",
        "titular",
    )

    with pytest.raises(APIError, match="sem consentimento vigente"):
        _inserir(treino, titular)


@pytest.mark.integracao
def test_consentimento_desfecho_e_export_fazem_round_trip(treino):
    import consentimento

    titular = _pseudonimo()
    _consentir(treino, titular)
    eventos = treino.eventos_do_titular(titular)
    assert consentimento.tem_consentimento(eventos, consentimento.FINALIDADE_TREINO)

    id_consulta = _inserir(treino, titular)
    # Reenvio do mesmo agendamento não duplica.
    assert not treino.inserir_consulta(
        id_consulta, titular, 40, "F", "cardiologia", 5.5, 7, 1,
        datetime.now(timezone.utc),
    )
    # Sem desfecho, não vai ao export.
    assert _exportadas(treino, {id_consulta}) == []

    item = {"id_consulta": id_consulta, "status": "no_show", "lembrete_enviado": True}
    assert treino.sincronizar_desfechos([item, {**item, "id_consulta": _pseudonimo()}]) == 1
    # Idempotente: o mesmo lote não muda nada.
    assert treino.sincronizar_desfechos([item]) == 0

    [linha] = _exportadas(treino, {id_consulta})
    assert linha["status"] == "no_show"
    assert linha["lembrete_enviado"] is True
    assert linha["id_titular"] == titular
    assert "data_nascimento" not in linha
    treino.eliminar_titular(titular)


@pytest.mark.integracao
def test_titular_que_revogou_sai_do_export_antes_da_purga(treino):
    titular = _pseudonimo()
    _consentir(treino, titular)
    id_consulta = _inserir(treino, titular)
    treino.sincronizar_desfechos(
        [{"id_consulta": id_consulta, "status": "concluido", "lembrete_enviado": False}]
    )
    assert _exportadas(treino, {id_consulta})

    _consentir(treino, titular, "revogado")

    assert _exportadas(treino, {id_consulta}) == []
    treino.eliminar_titular(titular)


@pytest.mark.integracao
def test_completude_conta_so_consultas_passadas_sem_desfecho(treino):
    titular = _pseudonimo()
    _consentir(treino, titular)
    agora = datetime.now(timezone.utc)
    antes = treino.contar_sem_desfecho(agora - timedelta(days=90), agora)

    _inserir(treino, titular, dias=-2)
    _inserir(treino, titular, dias=+2)

    assert treino.contar_sem_desfecho(agora - timedelta(days=90), agora) == antes + 1
    treino.eliminar_titular(titular)


# --- eliminação e purga ---------------------------------------------------------------


@pytest.mark.integracao
def test_eliminar_titular_apaga_as_linhas_e_guarda_a_prova(treino):
    """A prova fica, marcada como eliminada: some do vigente, e a purga de
    hoje não a alcança (365 dias)."""
    titular = _pseudonimo()
    _consentir(treino, titular)
    ids = {_inserir(treino, titular), _inserir(treino, titular)}
    treino.sincronizar_desfechos(
        [{"id_consulta": i, "status": "concluido", "lembrete_enviado": False} for i in ids]
    )

    assert treino.eliminar_titular(titular) == 2

    assert _exportadas(treino, ids) == []
    assert treino.eventos_do_titular(titular) == []
    assert treino.purgar(90)["eventos"] == 0
    # Para voltar ao treino, o titular precisa consentir de novo.
    with pytest.raises(APIError, match="sem consentimento vigente"):
        _inserir(treino, titular)
    _consentir(treino, titular)
    _inserir(treino, titular)
    treino.eliminar_titular(titular)


@pytest.mark.integracao
def test_purga_tira_as_consultas_fora_da_janela(treino):
    titular = _pseudonimo()
    _consentir(treino, titular)
    antiga = _inserir(treino, titular, dias=-100)
    recente = _inserir(treino, titular, dias=-10)
    treino.sincronizar_desfechos(
        [
            {"id_consulta": i, "status": "concluido", "lembrete_enviado": False}
            for i in (antiga, recente)
        ]
    )

    assert treino.purgar(90)["consultas"] >= 1

    assert [c["id_consulta"] for c in _exportadas(treino, {antiga, recente})] == [recente]
    treino.eliminar_titular(titular)


# --- pedidos do titular ---------------------------------------------------------------


@pytest.mark.integracao
def test_pedido_vai_de_pendente_a_executado_uma_vez_so(treino):
    titular = _pseudonimo()
    id_pedido = treino.registrar_pedido(titular, "revogacao", "autoagendamento")

    pendentes = {p["id"]: p for p in treino.pedidos_pendentes()}
    assert pendentes[id_pedido]["id_titular"] == titular
    assert pendentes[id_pedido]["tipo"] == "revogacao"

    assert treino.marcar_pedido_executado(id_pedido)
    assert not treino.marcar_pedido_executado(id_pedido)
    assert not treino.recusar_pedido(id_pedido)
    assert id_pedido not in {p["id"] for p in treino.pedidos_pendentes()}


@pytest.mark.integracao
def test_pedido_recusado_sai_da_fila(treino):
    id_pedido = treino.registrar_pedido(_pseudonimo(), "eliminacao", "saudeja")

    assert treino.recusar_pedido(id_pedido)
    assert not treino.marcar_pedido_executado(id_pedido)
    assert id_pedido not in {p["id"] for p in treino.pedidos_pendentes()}


@pytest.mark.integracao
def test_tipo_ou_canal_de_pedido_fora_do_dominio_e_recusado_pelo_banco(treino):
    import db.client as client_module

    client = client_module.obter_client()
    for tipo, canal in [("eliminacao", "telefone_da_recepcao"), ("apagar", "saudeja")]:
        with pytest.raises(APIError):
            client.rpc(
                "treino_registrar_pedido",
                {"p_id_titular": _pseudonimo(), "p_tipo": tipo, "p_canal": canal},
            ).execute()


def test_tipo_e_canal_de_pedido_desconhecidos_sao_recusados_antes_do_banco():
    import db.repositorio_treino as repositorio_treino

    with pytest.raises(ValueError):
        repositorio_treino.registrar_pedido("x", "apagar", "saudeja")
    with pytest.raises(ValueError):
        repositorio_treino.registrar_pedido("x", "eliminacao", "whatsapp")
