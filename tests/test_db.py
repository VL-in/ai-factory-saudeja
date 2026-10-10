"""
SaúdeJá — testes de integração de src/db/ contra o Supabase CLI
local (`supabase start`), mesma filosofia já usada para o MLflow em
tests/test_train.py (serviço real efêmero, não mock pesado).

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


@pytest.mark.parametrize(
    "valor",
    [
        "2026-10-04T19:46:05.01422+00:00",  # o do CI: 5 dígitos
        "2026-10-04T19:46:05.1+00:00",
        "2026-10-04T19:46:05.014220+00:00",
        "2026-10-04T19:46:05+00:00",
    ],
)
def test_ler_timestamptz_aceita_a_fracao_sem_zeros_finais(valor):
    """O PostgREST corta os zeros finais da fração, e o `fromisoformat` do
    Python 3.10 (o do CI e das imagens) recusa 5 dígitos."""
    from db.repositories import ler_timestamptz

    lido = ler_timestamptz(valor)

    assert lido.replace(microsecond=0).isoformat() == "2026-10-04T19:46:05+00:00"


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
    """Janela [amanhã, D+2]: um dia em que o cron não
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
    assert item.explicacao_texto is None  # plug do LLM inativo
    assert item.fora_do_dominio is None  # gravada sem a marca: "não verificado"


@pytest.mark.integracao
def test_marca_fora_do_dominio_faz_round_trip_ate_a_fila(db):
    """Contrato de features: o job grava a marca e a fila a mostra (migration
    20260930000000_fora_do_dominio.sql)."""
    import db.repositories as repositories
    from ui import logic

    _, agendamento = _criar_paciente_e_agendamento(repositories, dias=0, id_externo="EXT-DOM-1")
    repositories.gravar_predicao(
        id_agendamento=agendamento["id"],
        probabilidade=0.4,
        classe_prevista=0,
        threshold_usado=0.6,
        explicacao_shap=[{"feature": "distancia_km", "contribuicao": 0.1}],
        model_version="6430cb3315da",
        fora_do_dominio=True,
    )

    item = next(
        i
        for i in logic.buscar_fila_do_dia(hoje_na_clinica())
        if i.id_paciente_externo == "EXT-DOM-1"
    )
    assert item.fora_do_dominio is True


@pytest.mark.integracao
def test_conta_consultas_passadas_ainda_sem_desfecho(db):
    """Completude de rótulo do re-treino: só conta consulta de
    antes de hoje ainda `agendado` -- a de hoje ainda pode receber desfecho, e
    a que já tem desfecho não é lacuna."""
    import db.repositories as repositories

    _criar_paciente_e_agendamento(repositories, dias=-3, id_externo="EXT-ROT-1")
    _, com_desfecho = _criar_paciente_e_agendamento(repositories, dias=-2, id_externo="EXT-ROT-2")
    repositories.atualizar_status_agendamento(com_desfecho["id"], "concluido")
    _criar_paciente_e_agendamento(repositories, dias=0, id_externo="EXT-ROT-3")

    assert repositories.contar_consultas_sem_desfecho(hoje_na_clinica()) == 1


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
        consentimentos={"transferencia_internacional": True},
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


@pytest.mark.integracao
def test_cadastro_pela_ui_grava_e_revoga_consentimento_so_quando_o_estado_muda(db):
    """Passo 8.6, peça 3: o primeiro agendamento concede o que foi marcado; o
    segundo, com o lembrete desmarcado, revoga só o lembrete e não repete a
    transferência nem o treino, que continuam vigentes na mesma versão."""
    import consentimento
    import db.repositories as repositories
    from ui import logic

    data_consulta = agenda_clinica.proximo_dia_valido(hoje_na_clinica() + timedelta(days=30))
    campos = {
        "cpf": "111.444.777-35",
        "telefone": TELEFONE_PADRAO,
        "data_nascimento": date(2016, 4, 20),  # criança: consente o responsável
        "sexo": "F",
        "especialidade": "cardiologia",
        "distancia_km": 4.0,
        "data_consulta": data_consulta,
        "hora_consulta": HORA_DA_CONSULTA,
        "nome_completo": "Paciente Criança",
    }
    tudo = dict.fromkeys(consentimento.FINALIDADES, True)
    primeiro = logic.cadastrar_paciente_e_agendamento(**campos, consentimentos=tudo)
    logic.cadastrar_paciente_e_agendamento(
        **campos, consentimentos={**tudo, consentimento.FINALIDADE_LEMBRETE: False}
    )

    eventos = (
        db.table("eventos_consentimento")
        .select("finalidade, acao, canal, consentido_por, registrado_por, versao_termo")
        .eq("id_paciente", primeiro["id_paciente"])
        .order("registrado_em")
        .execute()
        .data
    )
    # Os três primeiros saem do mesmo insert, com o mesmo `now()`: a ordem
    # entre eles não é garantida.
    pares = [(e["finalidade"], e["acao"]) for e in eventos]
    assert set(pares[:3]) == {(f, "concedido") for f in consentimento.FINALIDADES}
    assert pares[3:] == [(consentimento.FINALIDADE_LEMBRETE, "revogado")]
    assert {e["canal"] for e in eventos} == {"autoagendamento"}
    assert {e["consentido_por"] for e in eventos} == {"responsavel_legal"}
    assert {e["registrado_por"] for e in eventos} == {None}
    assert {e["versao_termo"] for e in eventos} == {consentimento.VERSAO_TERMO}
    lidos = repositories.eventos_consentimento_do_paciente(primeiro["id_paciente"])
    assert not consentimento.tem_consentimento(lidos, consentimento.FINALIDADE_LEMBRETE)
    assert consentimento.tem_consentimento(lidos, consentimento.FINALIDADE_TREINO)


# --- login do funcionário contra o Supabase Auth local (architecture.md §7) ---

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
    """Regressão de desenho (docs/architecture.md §7) contra o Supabase de verdade: depois do
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


# --- canário do modelo (ADR-008) -----------------------------------------------


@pytest.mark.integracao
def test_estatisticas_de_modelo_contam_por_braco_e_so_desfecho_de_baixo_risco(db):
    """As contagens que decidem o canário: por `model_version`, desde o
    início dele, com o desfecho lido de `agendamentos` -- e só para baixo
    risco, porque o lembrete altera o desfecho de quem o recebeu."""
    import db.repositories as repositories

    # Janela larga: o relógio do Postgres no container pode estar atrás do
    # host (visto ~1 min depois de suspender o Docker Desktop), e `criado_em`
    # é o `now()` dele. O isolamento vem da `model_version` própria do teste.
    inicio = datetime.now(tz=fuso_da_clinica()) - timedelta(days=1)
    casos = [
        # (modelo, classe, desfecho)
        ("canario-x", 1, "no_show"),
        ("canario-x", 0, "no_show"),
        ("canario-x", 0, "concluido"),
        ("canario-x", 0, None),
        ("campeao-y", 0, "no_show"),
    ]
    for i, (modelo, classe, desfecho) in enumerate(casos):
        _, agendamento = _criar_paciente_e_agendamento(
            repositories, dias=-1, id_externo=f"EXT-CAN-{i}"
        )
        repositories.gravar_predicao(
            id_agendamento=agendamento["id"],
            probabilidade=0.9 if classe else 0.1,
            classe_prevista=classe,
            threshold_usado=0.6,
            explicacao_shap=[],
            model_version=modelo,
            fora_do_dominio=(i == 0),
        )
        if desfecho:
            repositories.atualizar_status_agendamento(agendamento["id"], desfecho)

    est = repositories.estatisticas_de_modelo("canario-x", inicio)

    assert {k: v for k, v in est.items() if k != "primeira_predicao"} == {
        "predicoes": 4,
        "disparos": 1,
        "fora_do_dominio": 1,
        "desfechos_baixo_risco": 2,
        "faltas_baixo_risco": 1,
    }
    assert est["primeira_predicao"] >= inicio
    depois = datetime.now(tz=fuso_da_clinica()) + timedelta(days=1)
    assert repositories.estatisticas_de_modelo("canario-x", depois)["predicoes"] == 0


@pytest.mark.integracao
def test_canario_revertido_faz_round_trip_e_a_primeira_gravacao_vale(db):
    import db.repositories as repositories

    try:
        assert repositories.canario_revertido("canario-teste-int") is None
        repositories.registrar_canario_revertido("canario-teste-int", "primeiro motivo")
        repositories.registrar_canario_revertido("canario-teste-int", "segundo motivo")

        linha = repositories.canario_revertido("canario-teste-int")
        assert linha is not None
        assert linha["motivo"] == "primeiro motivo"
    finally:
        db.table("canarios_revertidos").delete().eq("model_version", "canario-teste-int").execute()


# --- consentimento e pedidos do titular (ADR-009) ------------------------------


def _criar_paciente_com_consentimento(repositories, id_externo):
    paciente = repositories.inserir_paciente(
        id_paciente_externo=id_externo,
        data_nascimento=DATA_NASCIMENTO_PADRAO,
        sexo="F",
        telefone=TELEFONE_PADRAO,
    )
    eventos = repositories.registrar_consentimentos(
        id_paciente=paciente["id"],
        decisoes=[("lembrete", "concedido"), ("transferencia_internacional", "concedido")],
        versao_termo="teste-int",
        canal="autoagendamento",
        consentido_por="titular",
    )
    return paciente, eventos


@pytest.mark.integracao
def test_eventos_de_consentimento_fazem_round_trip_e_o_vigente_e_o_ultimo(db):
    import consentimento
    import db.repositories as repositories

    paciente, eventos = _criar_paciente_com_consentimento(repositories, "hash-consent-1")
    assert len(eventos) == 2
    assert eventos[0]["registrado_por"] is None  # autoagendamento

    repositories.registrar_consentimentos(
        id_paciente=paciente["id"],
        decisoes=[("lembrete", "revogado")],
        versao_termo="teste-int",
        canal="clinica",
        consentido_por="titular",
    )

    lidos = repositories.eventos_consentimento_do_paciente(paciente["id"])
    assert [e["acao"] for e in lidos if e["finalidade"] == "lembrete"] == [
        "concedido",
        "revogado",
    ]
    assert not consentimento.tem_consentimento(lidos, "lembrete")
    assert consentimento.tem_consentimento(lidos, "transferencia_internacional")


@pytest.mark.integracao
def test_eventos_de_consentimento_recusam_update_e_delete(db):
    """Só de inserção no banco, não por convenção: a chave secreta ignora RLS,
    mas não o trigger. Corrigir um evento apagaria a prova (art. 8º, § 2º)."""
    import db.repositories as repositories

    paciente, eventos = _criar_paciente_com_consentimento(repositories, "hash-consent-2")
    id_evento = eventos[0]["id"]

    with pytest.raises(Exception, match="so' de insercao"):
        db.table("eventos_consentimento").update({"acao": "revogado"}).eq("id", id_evento).execute()
    with pytest.raises(Exception, match="365 dias"):
        db.table("eventos_consentimento").delete().eq("id", id_evento).execute()

    acoes = [e["acao"] for e in repositories.eventos_consentimento_do_paciente(paciente["id"])]
    assert acoes == ["concedido", "concedido"]


def _evento_cru(id_paciente, **extra):
    """Linha de evento inserida direto na tabela, sem o repositório: é como um
    bug no código chegaria ao banco, e o trigger é a guarda."""
    return {
        "id_paciente": id_paciente,
        "finalidade": "treino",
        "acao": "concedido",
        "versao_termo": "teste-int",
        "canal": "autoagendamento",
        "consentido_por": "titular",
        **extra,
    }


@pytest.mark.integracao
def test_insercao_grava_o_hash_do_paciente_e_o_relogio_do_banco(db):
    """O hash e a data enviados pelo cliente são ignorados: a prova não pode
    pertencer a outro titular nem ter a data escolhida por quem grava -- o
    vigente é decidido por `registrado_em`."""
    from datetime import timezone

    import db.repositories as repositories

    paciente, eventos = _criar_paciente_com_consentimento(repositories, "hash-consent-4")
    assert all(e["id_paciente_externo"] == "hash-consent-4" for e in eventos)

    linha = (
        db.table("eventos_consentimento")
        .insert(
            _evento_cru(
                paciente["id"],
                id_paciente_externo="hash-de-outra-pessoa",
                registrado_em="1999-01-01T00:00:00+00:00",
            )
        )
        .execute()
        .data[0]
    )
    assert linha["id_paciente_externo"] == "hash-consent-4"
    registrado_em = repositories.ler_timestamptz(linha["registrado_em"])
    assert abs(registrado_em - datetime.now(timezone.utc)) < timedelta(minutes=5)


@pytest.mark.integracao
def test_insercao_recusa_evento_sem_paciente_ou_ja_eliminado(db):
    """Sem paciente, o evento nunca seria apagável (a purga depende de
    `eliminado_em`); com `eliminado_em` retroativo, seria apagável na hora."""
    import db.repositories as repositories

    paciente, _ = _criar_paciente_com_consentimento(repositories, "hash-consent-5")

    with pytest.raises(Exception, match="evento sem paciente"):
        db.table("eventos_consentimento").insert(_evento_cru(None)).execute()
    with pytest.raises(Exception, match="gravado pelo trigger"):
        db.table("eventos_consentimento").insert(
            _evento_cru(paciente["id"], eliminado_em="2000-01-01T00:00:00+00:00")
        ).execute()
    with pytest.raises(Exception, match="paciente inexistente"):
        db.table("eventos_consentimento").insert(
            _evento_cru("00000000-0000-0000-0000-000000000000")
        ).execute()


@pytest.mark.integracao
def test_desvincular_a_prova_com_o_paciente_cadastrado_e_recusado(db):
    """O `set null` só é aceito vindo da eliminação: o mesmo update feito pelo
    código desvincularia a prova e iniciaria a contagem dos 365 dias com o
    paciente ainda cadastrado."""
    import db.repositories as repositories

    paciente, eventos = _criar_paciente_com_consentimento(repositories, "hash-consent-6")

    with pytest.raises(Exception, match="so' de insercao"):
        db.table("eventos_consentimento").update({"id_paciente": None}).eq(
            "id", eventos[0]["id"]
        ).execute()

    linha = (
        db.table("eventos_consentimento")
        .select("id_paciente, eliminado_em")
        .eq("id", eventos[0]["id"])
        .execute()
        .data[0]
    )
    assert linha == {"id_paciente": paciente["id"], "eliminado_em": None}


@pytest.mark.integracao
def test_eliminar_o_paciente_mantem_a_prova_sem_vinculo_e_com_data(db):
    """`on delete set null`, não `cascade` (ADR-009): os eventos sobrevivem à
    eliminação, sem vínculo com o cadastro, e o trigger grava `eliminado_em`.
    Antes de 365 dias, nem a purga consegue apagá-los."""
    from datetime import timezone

    import db.repositories as repositories

    paciente, eventos = _criar_paciente_com_consentimento(repositories, "hash-consent-3")
    ids = [e["id"] for e in eventos]

    db.table("pacientes").delete().eq("id", paciente["id"]).execute()

    restantes = (
        db.table("eventos_consentimento")
        .select("id, id_paciente, id_paciente_externo, eliminado_em")
        .in_("id", ids)
        .execute()
        .data
    )
    assert len(restantes) == 2
    assert all(r["id_paciente"] is None for r in restantes)
    assert all(r["id_paciente_externo"] == "hash-consent-3" for r in restantes)
    assert all(r["eliminado_em"] is not None for r in restantes)

    # A purga de hoje não alcança uma eliminação de agora.
    assert repositories.purgar_provas_de_consentimento_expiradas(datetime.now(timezone.utc)) == 0
    # E nem com o relógio do chamador adiantado: o trigger compara com o now()
    # do banco e recusaria o delete inteiro.
    with pytest.raises(Exception, match="365 dias"):
        repositories.purgar_provas_de_consentimento_expiradas(
            datetime.now(timezone.utc) + timedelta(days=400)
        )


@pytest.mark.integracao
def test_pedido_do_titular_percorre_as_situacoes_e_perde_o_vinculo_ao_executar(db):
    import db.repositories as repositories

    paciente, _ = _criar_paciente_com_consentimento(repositories, "hash-pedido-1")
    pedido = repositories.registrar_pedido(paciente["id"], "revogacao_treino")
    assert pedido["situacao"] == "pendente"

    # Executar sem a clínica confirmar não muda nada.
    assert not repositories.marcar_pedido_executado(pedido["id"])
    assert repositories.confirmar_pedido(pedido["id"])
    assert not repositories.recusar_pedido(pedido["id"])  # já saiu de pendente
    assert [p["id"] for p in repositories.pedidos_confirmados()] == [pedido["id"]]

    assert repositories.marcar_pedido_executado(pedido["id"])

    linha = db.table("pedidos_titular").select("*").eq("id", pedido["id"]).execute().data[0]
    assert linha["situacao"] == "executado"
    assert linha["id_paciente"] is None
    assert linha["executado_em"] is not None
    assert repositories.pedidos_confirmados() == []
    db.table("pedidos_titular").delete().eq("id", pedido["id"]).execute()


@pytest.mark.integracao
def test_pedido_executado_com_vinculo_e_recusado_pelo_banco(db):
    """O check da migration é a guarda, não a função do repositório."""
    import db.repositories as repositories

    paciente, _ = _criar_paciente_com_consentimento(repositories, "hash-pedido-2")
    pedido = repositories.registrar_pedido(paciente["id"], "eliminacao")
    assert repositories.confirmar_pedido(pedido["id"])

    with pytest.raises(Exception, match="pedidos_titular_executado_sem_vinculo"):
        db.table("pedidos_titular").update({"situacao": "executado"}).eq(
            "id", pedido["id"]
        ).execute()
    db.table("pedidos_titular").delete().eq("id", pedido["id"]).execute()


@pytest.mark.integracao
def test_eliminacao_anula_o_vinculo_do_pedido_sem_executa_lo(db):
    """O `set null` também age nos pedidos. Um pedido confirmado cujo paciente
    já foi eliminado fica sem vínculo e ainda na fila do workflow de purga, que
    precisa tratá-lo como atendido (peça 6)."""
    import db.repositories as repositories

    paciente, _ = _criar_paciente_com_consentimento(repositories, "hash-pedido-3")
    pedido = repositories.registrar_pedido(paciente["id"], "revogacao_treino")
    assert repositories.confirmar_pedido(pedido["id"])

    db.table("pacientes").delete().eq("id", paciente["id"]).execute()

    linha = db.table("pedidos_titular").select("*").eq("id", pedido["id"]).execute().data[0]
    assert linha["id_paciente"] is None
    assert linha["situacao"] == "confirmado_pela_clinica"
    assert repositories.marcar_pedido_executado(pedido["id"])
    db.table("pedidos_titular").delete().eq("id", pedido["id"]).execute()


@pytest.mark.integracao
def test_datas_do_pedido_sao_do_relogio_do_banco(db):
    """O prazo de 15 dias é medido pelas datas do pedido: a data enviada pelo
    cliente é ignorada, na criação e em cada mudança de situação."""
    from datetime import timezone

    import db.repositories as repositories

    paciente, _ = _criar_paciente_com_consentimento(repositories, "hash-pedido-4")
    antiga = "1999-01-01T00:00:00+00:00"
    pedido = (
        db.table("pedidos_titular")
        .insert(
            {
                "id_paciente": paciente["id"],
                "tipo": "revogacao_lembrete",
                "criado_em": antiga,
                "confirmado_em": antiga,
            }
        )
        .execute()
        .data[0]
    )
    assert pedido["confirmado_em"] is None

    confirmado = (
        db.table("pedidos_titular")
        .update(
            {"situacao": "confirmado_pela_clinica", "confirmado_em": antiga, "criado_em": antiga}
        )
        .eq("id", pedido["id"])
        .execute()
        .data[0]
    )
    agora = datetime.now(timezone.utc)
    for coluna in ("criado_em", "confirmado_em"):
        instante = repositories.ler_timestamptz(confirmado[coluna])
        assert abs(instante - agora) < timedelta(minutes=5), coluna
    db.table("pedidos_titular").delete().eq("id", pedido["id"]).execute()


@pytest.mark.integracao
def test_pedido_so_anda_pelas_transicoes_previstas(db):
    import db.repositories as repositories

    paciente, _ = _criar_paciente_com_consentimento(repositories, "hash-pedido-5")
    outro, _ = _criar_paciente_com_consentimento(repositories, "hash-pedido-6")

    with pytest.raises(Exception, match="nasce pendente"):
        db.table("pedidos_titular").insert(
            {"id_paciente": paciente["id"], "tipo": "eliminacao", "situacao": "executado"}
        ).execute()
    with pytest.raises(Exception, match="pedido sem paciente"):
        db.table("pedidos_titular").insert({"id_paciente": None, "tipo": "eliminacao"}).execute()

    pedido = repositories.registrar_pedido(paciente["id"], "revogacao_treino")

    def atualizar(campos):
        db.table("pedidos_titular").update(campos).eq("id", pedido["id"]).execute()

    with pytest.raises(Exception, match="pendente -> executado"):
        atualizar({"situacao": "executado", "id_paciente": None})
    with pytest.raises(Exception, match="tipo do pedido"):
        atualizar({"tipo": "eliminacao"})
    with pytest.raises(Exception, match="so' pode ser anulado"):
        atualizar({"id_paciente": outro["id"]})

    # Recusado: data do banco e vínculo mantido (a clínica prova a quem
    # respondeu a recusa); dali o pedido não sai.
    assert repositories.recusar_pedido(pedido["id"])
    linha = db.table("pedidos_titular").select("*").eq("id", pedido["id"]).execute().data[0]
    assert linha["situacao"] == "recusado"
    assert linha["recusado_em"] is not None
    assert linha["id_paciente"] == paciente["id"]
    with pytest.raises(Exception, match="recusado -> confirmado_pela_clinica"):
        atualizar({"situacao": "confirmado_pela_clinica"})
    db.table("pedidos_titular").delete().eq("id", pedido["id"]).execute()


def test_tipo_de_pedido_desconhecido_e_recusado_antes_do_banco():
    import db.repositories as repositories

    with pytest.raises(ValueError):
        repositories.registrar_pedido("00000000-0000-0000-0000-000000000000", "apagar")
