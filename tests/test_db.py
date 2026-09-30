"""
SaúdeJá — testes de integração de src/db/ (Passo 5) contra o Supabase CLI
local (`supabase start`), mesma filosofia já usada para o MLflow em
tests/test_train.py (serviço real efêmero, não mock pesado -- ver
PLANO-IMPLEMENTACAO.md, Passo 5).

Pré-requisito para rodar: `supabase start` na raiz do repositório (aplica
`supabase/migrations/` automaticamente). Marcados `integracao`
(pytest.ini) -- não rodam por padrão.
"""
import json
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

import agenda_clinica
from config_projeto import fuso_da_clinica, hoje_na_clinica

REPO_ROOT = Path(__file__).resolve().parents[1]

# Horário fixo dentro da grade de atendimento (README: seg-sex 08h-18h), em
# vez de "agora + N dias": ancorar na hora corrente fazia a suíte falhar
# quando rodada depois das 21h em São Paulo, porque o agendamento caía no dia
# civil seguinte em UTC. O bug de fuso que isso expôs está corrigido em
# repositories._intervalo_do_dia; o horário fixo mantém o teste determinístico.
HORA_DA_CONSULTA = time(10, 0)


# --- intervalo do dia: função pura, não precisa de banco -----------------------


def test_intervalo_do_dia_usa_o_fuso_da_clinica_nao_utc():
    """Regressão do bug que fazia a fila do dia e o job D-2 trabalharem com a
    data errada: a janela tem que começar à meia-noite em São Paulo (03:00
    UTC), não à meia-noite UTC."""
    from db.repositories import _intervalo_do_dia

    inicio, fim = _intervalo_do_dia(date(2026, 9, 19))

    assert inicio.startswith("2026-09-19T00:00:00-03:00")
    assert fim.startswith("2026-09-20T00:00:00-03:00")


def test_intervalo_do_dia_respeita_timezone_clinica_configurada(monkeypatch):
    monkeypatch.setenv("TIMEZONE_CLINICA", "UTC")
    from db.repositories import _intervalo_do_dia

    inicio, _ = _intervalo_do_dia(date(2026, 9, 19))

    assert inicio.startswith("2026-09-19T00:00:00+00:00")


def test_consulta_do_fim_do_dia_cai_na_fila_do_dia_certo():
    """Caso concreto que o filtro em UTC errava: uma consulta às 21h30 de
    19/09 em São Paulo é 00h30 de 20/09 em UTC -- ela pertence à fila do dia
    19, não à do dia 20."""
    from db.repositories import _intervalo_do_dia

    consulta = datetime.combine(date(2026, 9, 19), time(21, 30), tzinfo=fuso_da_clinica())
    inicio, fim = _intervalo_do_dia(date(2026, 9, 19))

    assert datetime.fromisoformat(inicio) <= consulta < datetime.fromisoformat(fim)


# --- repositórios contra o Supabase local (integracao) -------------------------


def _status_supabase_local() -> dict:
    saida = subprocess.run(
        ["supabase", "status", "-o", "json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(saida.stdout)


@pytest.fixture
def db(monkeypatch):
    """Aponta src/db/client.py para o Supabase local e limpa as tabelas de
    teste ao final -- `supabase/migrations/...init.sql` dá cascade em
    agendamentos, então apagar pacientes/agendamentos basta."""
    status = _status_supabase_local()
    monkeypatch.setenv("SUPABASE_URL", status["API_URL"])
    monkeypatch.setenv("SUPABASE_SECRET_KEY", status["SECRET_KEY"])

    import db.client as client_module

    client_module._cliente = None
    client = client_module.obter_client()

    yield client

    zero = "00000000-0000-0000-0000-000000000000"
    client.table("agendamentos").delete().neq("id", zero).execute()
    client.table("pacientes").delete().neq("id", zero).execute()
    client_module._cliente = None


DATA_NASCIMENTO_PADRAO = date(1985, 6, 15)


TELEFONE_PADRAO = "5511987654321"


def _criar_paciente_e_agendamento(repositories, dias: int, id_externo: str):
    paciente = repositories.inserir_paciente(
        id_paciente_externo=id_externo,
        data_nascimento=DATA_NASCIMENTO_PADRAO,
        sexo="F",
        telefone=TELEFONE_PADRAO,
    )
    data_hora = datetime.combine(
        hoje_na_clinica() + timedelta(days=dias), HORA_DA_CONSULTA, tzinfo=fuso_da_clinica()
    )
    agendamento = repositories.inserir_agendamento(
        id_paciente=paciente["id"],
        especialidade="cardiologia",
        distancia_km=5.5,
        data_hora_agendada=data_hora,
        dias_entre_agendamento_consulta=14,
        historico_noshow=1,
    )
    return paciente, agendamento


@pytest.mark.integracao
def test_inserir_paciente_e_upsert_por_id_externo(db):
    import db.repositories as repositories

    p1 = repositories.inserir_paciente(
        id_paciente_externo="EXT-1",
        data_nascimento=date(1990, 3, 1),
        sexo="F",
        telefone=TELEFONE_PADRAO,
    )
    p2 = repositories.inserir_paciente(
        id_paciente_externo="EXT-1",
        data_nascimento=date(1991, 3, 1),
        sexo="F",
        telefone=TELEFONE_PADRAO,
    )

    assert p1["id"] == p2["id"]
    assert p2["data_nascimento"] == "1991-03-01"


@pytest.mark.integracao
def test_nome_do_paciente_faz_round_trip_e_chega_na_fila_do_dia(db):
    """ADR-007: a "Fila do dia" mostrava o hash sha256 de 64 caracteres na
    coluna "Paciente". Prova o caminho inteiro -- gravação, select explícito da
    fila e o rótulo que a tela desenha."""
    import db.repositories as repositories
    from ui import logic

    paciente, _ = _criar_paciente_e_agendamento_com_nome(
        repositories, dias=0, id_externo="EXT-NOME", nome_completo="Ana Souza Costa"
    )
    assert paciente["nome_completo"] == "Ana Souza Costa"

    fila = logic.buscar_fila_do_dia(hoje_na_clinica())
    item = next(i for i in fila if i.id_paciente_externo == "EXT-NOME")

    assert item.nome_completo == "Ana Souza Costa"
    assert item.rotulo_paciente == "Ana Souza Costa"


@pytest.mark.integracao
def test_recadastro_sem_nome_nao_apaga_o_nome_ja_gravado(db):
    """O upsert é por `id_paciente_externo`. Mandar `nome_completo: None` num
    recadastro feito por um caminho que não coleta nome apagaria o nome de quem
    já o tinha -- perda silenciosa de dado que a clínica depende para operar a
    fila. `inserir_paciente` omite a chave em vez de enviar nulo."""
    import db.repositories as repositories

    repositories.inserir_paciente(
        id_paciente_externo="EXT-PRESERVA",
        data_nascimento=DATA_NASCIMENTO_PADRAO,
        sexo="F",
        telefone=TELEFONE_PADRAO,
        nome_completo="Bruno Lima",
    )
    depois = repositories.inserir_paciente(
        id_paciente_externo="EXT-PRESERVA",
        data_nascimento=DATA_NASCIMENTO_PADRAO,
        sexo="F",
        telefone=TELEFONE_PADRAO,
    )

    assert depois["nome_completo"] == "Bruno Lima"


@pytest.mark.integracao
def test_fila_do_dia_nao_traz_telefone_do_banco(db):
    """O select explícito (ADR-007) é a fronteira: antes, `pacientes(*)`
    trazia `telefone` para a camada de UI sem nenhum uso -- e faria o mesmo com
    qualquer coluna de PII futura."""
    import db.repositories as repositories

    _criar_paciente_e_agendamento_com_nome(
        repositories, dias=0, id_externo="EXT-SEM-TEL", nome_completo="Carla Dias"
    )

    linhas = repositories.buscar_fila_do_dia(hoje_na_clinica())
    paciente_embutido = next(
        linha["pacientes"]
        for linha in linhas
        if linha["pacientes"]["id_paciente_externo"] == "EXT-SEM-TEL"
    )

    assert set(paciente_embutido) == {"id_paciente_externo", "nome_completo"}


def _criar_paciente_e_agendamento_com_nome(repositories, dias, id_externo, nome_completo):
    paciente = repositories.inserir_paciente(
        id_paciente_externo=id_externo,
        data_nascimento=DATA_NASCIMENTO_PADRAO,
        sexo="F",
        telefone=TELEFONE_PADRAO,
        nome_completo=nome_completo,
    )
    data_hora = datetime.combine(
        hoje_na_clinica() + timedelta(days=dias), HORA_DA_CONSULTA, tzinfo=fuso_da_clinica()
    )
    agendamento = repositories.inserir_agendamento(
        id_paciente=paciente["id"],
        especialidade="cardiologia",
        distancia_km=5.5,
        data_hora_agendada=data_hora,
        dias_entre_agendamento_consulta=14,
        historico_noshow=1,
    )
    return paciente, agendamento


@pytest.mark.integracao
def test_inserir_agendamento_aparece_na_fila_do_dia(db):
    import db.repositories as repositories

    _, agendamento = _criar_paciente_e_agendamento(repositories, dias=0, id_externo="EXT-2")

    fila = repositories.buscar_fila_do_dia(hoje_na_clinica())

    ids_na_fila = [a["id"] for a in fila]
    assert agendamento["id"] in ids_na_fila
    encontrado = next(a for a in fila if a["id"] == agendamento["id"])
    assert encontrado["pacientes"]["id_paciente_externo"] == "EXT-2"
    assert encontrado["predicoes"] == []


@pytest.mark.integracao
def test_buscar_agendamentos_d2_pendentes_cobre_de_amanha_ate_d2(db):
    """Janela [amanhã, D+2] (revisão do Passo 10): um dia em que o cron não
    rodou é recuperado na execução seguinte, e agendamento feito com um dia de
    antecedência também é predito. Hoje e D+3 ficam de fora."""
    import db.repositories as repositories

    hoje = hoje_na_clinica()
    _criar_paciente_e_agendamento(repositories, dias=0, id_externo="EXT-3-HOJE")
    _, agendamento_d1 = _criar_paciente_e_agendamento(repositories, dias=1, id_externo="EXT-3-D1")
    _, agendamento_d2 = _criar_paciente_e_agendamento(repositories, dias=2, id_externo="EXT-3")
    _criar_paciente_e_agendamento(repositories, dias=3, id_externo="EXT-4")

    pendentes = repositories.buscar_agendamentos_d2_pendentes(hoje)
    assert [a["id"] for a in pendentes] == [agendamento_d1["id"], agendamento_d2["id"]]

    repositories.gravar_predicao(
        id_agendamento=agendamento_d2["id"],
        probabilidade=0.8,
        classe_prevista=1,
        threshold_usado=0.5,
        explicacao_shap=[{"feature": "historico_noshow", "contribuicao": 0.4}],
        model_version="abc123",
    )
    # Quem já recebeu lembrete sem predição (quarentena) também não volta.
    repositories.marcar_lembrete_enviado(agendamento_d1["id"])

    assert repositories.buscar_agendamentos_d2_pendentes(hoje) == []


@pytest.mark.integracao
def test_desfecho_so_e_gravado_para_consulta_de_hoje_ou_anterior(db):
    import db.repositories as repositories

    _, de_hoje = _criar_paciente_e_agendamento(repositories, dias=0, id_externo="EXT-DESF-0")
    _, de_amanha = _criar_paciente_e_agendamento(repositories, dias=1, id_externo="EXT-DESF-1")

    gravado = repositories.atualizar_status_agendamento(de_hoje["id"], "no_show")
    assert gravado["status"] == "no_show"
    with pytest.raises(repositories.DesfechoForaDePrazo):
        repositories.atualizar_status_agendamento(de_amanha["id"], "no_show")

    linha = db.table("agendamentos").select("status").eq("id", de_amanha["id"]).execute().data
    assert linha[0]["status"] == "agendado"


@pytest.mark.integracao
def test_fila_do_dia_ordenada_por_probabilidade_desc_com_sem_predicao_por_ultimo(db):
    import db.repositories as repositories

    _, alto_risco = _criar_paciente_e_agendamento(repositories, dias=0, id_externo="EXT-5")
    _, baixo_risco = _criar_paciente_e_agendamento(repositories, dias=0, id_externo="EXT-6")
    _, sem_predicao = _criar_paciente_e_agendamento(repositories, dias=0, id_externo="EXT-7")

    repositories.gravar_predicao(
        id_agendamento=alto_risco["id"],
        probabilidade=0.9,
        classe_prevista=1,
        threshold_usado=0.5,
        explicacao_shap=[],
        model_version="abc123",
    )
    repositories.gravar_predicao(
        id_agendamento=baixo_risco["id"],
        probabilidade=0.1,
        classe_prevista=0,
        threshold_usado=0.5,
        explicacao_shap=[],
        model_version="abc123",
    )

    fila = repositories.buscar_fila_do_dia(hoje_na_clinica())
    ordem = [a["id"] for a in fila]

    assert ordem.index(alto_risco["id"]) < ordem.index(baixo_risco["id"])
    assert ordem.index(baixo_risco["id"]) < ordem.index(sem_predicao["id"])


# --- camada da UI sobre o banco (src/ui/logic.py) ------------------------------


@pytest.mark.integracao
def test_fila_da_ui_mostra_o_horario_no_fuso_da_clinica(db):
    """Regressão: o Postgres devolve `timestamptz` normalizado em UTC, então
    a fila exibia 13:00 para uma consulta das 10:00 da clínica."""
    import db.repositories as repositories
    from ui import logic

    _criar_paciente_e_agendamento(repositories, dias=0, id_externo="EXT-UI-1")

    item = next(i for i in logic.buscar_fila_do_dia(hoje_na_clinica()))

    assert item.data_hora_agendada.timetz().replace(tzinfo=None) == HORA_DA_CONSULTA
    assert item.data_hora_agendada.utcoffset() == fuso_da_clinica().utcoffset(
        datetime.combine(hoje_na_clinica(), HORA_DA_CONSULTA)
    )


@pytest.mark.integracao
def test_fila_da_ui_carrega_a_explicacao_gravada_com_a_predicao(db):
    """SLO §4 na tela: a fila precisa conseguir mostrar o porquê de cada
    risco a partir do que o job D-2 gravou, sem recalcular nem reconsultar."""
    import db.repositories as repositories
    from ui import logic

    _, agendamento = _criar_paciente_e_agendamento(repositories, dias=0, id_externo="EXT-UI-2")
    contribuicoes = [
        {"feature": "dias_entre_agendamento_consulta", "contribuicao": 0.77},
        {"feature": "historico_noshow", "contribuicao": 0.33},
    ]
    repositories.gravar_predicao(
        id_agendamento=agendamento["id"],
        probabilidade=0.78,
        classe_prevista=1,
        threshold_usado=0.6,
        explicacao_shap=contribuicoes,
        model_version="6430cb3315da",
    )

    item = next(
        i
        for i in logic.buscar_fila_do_dia(hoje_na_clinica())
        if i.id_paciente_externo == "EXT-UI-2"
    )

    assert item.tem_predicao
    assert item.probabilidade == pytest.approx(0.78)
    assert item.explicacao == contribuicoes
    assert item.model_version == "6430cb3315da"
    assert item.explicacao_texto is None  # plug do LLM inativo (Passo 13)


@pytest.mark.integracao
def test_cadastro_pela_ui_grava_no_fuso_certo_e_deriva_a_antecedencia(db):
    """O cadastro é a outra ponta do mesmo bug: gravar naive faria o Postgres
    interpretar o horário no fuso do servidor (UTC no container)."""
    from ui import logic

    cpf_teste = "111.444.777-35"  # CPF com dígito verificador válido
    id_externo_esperado = logic._id_paciente_externo_de_cpf(cpf_teste)
    data_consulta = agenda_clinica.proximo_dia_valido(hoje_na_clinica() + timedelta(days=30))

    logic.cadastrar_paciente_e_agendamento(
        cpf=cpf_teste,
        telefone=TELEFONE_PADRAO,
        data_nascimento=date(1998, 4, 20),
        sexo="F",
        especialidade="cardiologia",
        distancia_km=18.0,
        data_consulta=data_consulta,
        hora_consulta=HORA_DA_CONSULTA,
        nome_completo="Daniela Reis",
    )

    item = next(
        i
        for i in logic.buscar_fila_do_dia(data_consulta)
        if i.id_paciente_externo == id_externo_esperado
    )

    assert item.data_hora_agendada.timetz().replace(tzinfo=None) == HORA_DA_CONSULTA
    # O nome digitado na tela chega à fila; o CPF, não -- o que identifica o
    # paciente no banco é o hash dele (ADR-007).
    assert item.nome_completo == "Daniela Reis"
    paciente = db.table("pacientes").select("*").execute().data[0]
    assert "cpf" not in paciente
    agendamento = (
        db.table("agendamentos").select("dias_entre_agendamento_consulta").execute().data[0]
    )
    assert agendamento["dias_entre_agendamento_consulta"] == (
        data_consulta - hoje_na_clinica()
    ).days


# --- login do funcionário contra o Supabase Auth local (ADR-008) --------------

SENHA_DE_TESTE = "senha-de-teste-123"


@pytest.fixture
def funcionario(db):
    """Conta criada pela API de admin -- o mesmo caminho de
    scripts/criar_funcionario.py, já que o cadastro aberto fica desligado."""
    from uuid import uuid4

    email = f"funcionario-{uuid4().hex[:8]}@clinica.test"
    criado = db.auth.admin.create_user(
        {"email": email, "password": SENHA_DE_TESTE, "email_confirm": True}
    )
    yield email
    db.auth.admin.delete_user(criado.user.id)


@pytest.mark.integracao
def test_login_real_nao_troca_a_identidade_das_consultas_do_backend(db, funcionario):
    """Regressão de desenho (ADR-008) contra o Supabase de verdade: depois do
    login, o backend continua enxergando as tabelas. Se o sign-in acontecesse
    no singleton, o supabase-py trocaria o Authorization pelo JWT do usuário e
    o RLS sem policies devolveria vazio -- para todos os navegadores."""
    import db.repositories as repositories
    from ui import logic

    _criar_paciente_e_agendamento(repositories, dias=0, id_externo="EXT-LOGIN")

    sessao = logic.autenticar_funcionario(funcionario, SENHA_DE_TESTE)

    assert sessao.email == funcionario
    visiveis = (
        db.table("pacientes").select("id").eq("id_paciente_externo", "EXT-LOGIN").execute().data
    )
    assert len(visiveis) == 1


@pytest.mark.integracao
def test_senha_errada_e_email_inexistente_sao_indistinguiveis_no_supabase_real(db, funcionario):
    from ui import logic

    mensagens = []
    for email, senha in ((funcionario, "senha-errada"), ("ninguem@clinica.test", SENHA_DE_TESTE)):
        with pytest.raises(logic.ErroCredenciais) as exc:
            logic.autenticar_funcionario(email, senha)
        mensagens.append(str(exc.value))

    assert mensagens == [logic.MENSAGEM_CREDENCIAIS_INVALIDAS] * 2
