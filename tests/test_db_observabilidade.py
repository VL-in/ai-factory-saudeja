"""
SaúdeJá — schema `observabilidade` e papel `grafana_leitura` (Passo 8.9, P4)
contra o Supabase CLI local. Mesmo pré-requisito de tests/test_db.py:
`supabase start` na raiz.

O PostgREST não serve aqui: o schema não é exposto e o que se quer provar são
privilégios de um papel do Postgres. O SQL vai pelo `psql` do próprio container
do banco, conectado como `postgres`, com as funções `has_*_privilege` do
catálogo -- o que vale qualquer que seja o papel que aplicou as migrations, e
sem ligar o login do `grafana_leitura`, que é passo manual da autora.

Valor variável vai como variável do `psql` (`-v nome=valor`, lido como
`:'nome'`), que o próprio psql cita como literal -- nunca interpolado no texto
da consulta.
"""
import calendar
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest

from test_db import _criar_paciente_e_agendamento, _status_supabase_local

REPO_ROOT = Path(__file__).resolve().parents[1]
FUSO = ZoneInfo("America/Sao_Paulo")

VIEWS = (
    "agendamentos_por_dia",
    "disparos_por_dia",
    "envios_cobrados_por_mes",
    "eventos_fora_da_allowlist_por_dia",
    "eventos_por_dia",
    "eventos_por_mes",
    "predicoes_por_dia",
    "tamanho_do_banco",
    "ultimo_job_d2",
)
# Dia sem tráfego real: os eventos semeados em `eventos_app` ficam isolados dos
# de qualquer outro teste ou execução local.
DIA_DOS_EVENTOS = "2001-02-03"


def _container_do_banco() -> str:
    config = (REPO_ROOT / "supabase" / "config.toml").read_text(encoding="utf-8")
    projeto = re.search(r'^project_id\s*=\s*"([^"]+)"', config, re.MULTILINE)
    assert projeto, "supabase/config.toml sem project_id"
    return f"supabase_db_{projeto.group(1)}"


def _sql(consulta: str, **variaveis: str) -> list[list[str]]:
    """Linhas da consulta, cada uma como lista de campos (texto). A consulta vai
    pela entrada padrão, e não por `-c`, porque o psql só substitui variáveis
    no que lê como script."""
    url = urlsplit(_status_supabase_local()["DB_URL"])
    definicoes = [arg for nome, valor in variaveis.items() for arg in ("-v", f"{nome}={valor}")]
    saida = subprocess.run(
        [
            "docker", "exec", "-i", "-e", f"PGPASSWORD={url.password}", _container_do_banco(),
            "psql", "-h", "127.0.0.1", "-U", url.username or "postgres", "-d", "postgres",
            "-X", "-q", "-tA", "-F", "\t", "-v", "ON_ERROR_STOP=1", *definicoes,
        ],
        input=consulta,
        capture_output=True,
        text=True,
        check=True,
    )
    return [linha.split("\t") for linha in saida.stdout.splitlines() if linha]


def _valor(consulta: str, **variaveis: str) -> str | None:
    linhas = _sql(consulta, **variaveis)
    return linhas[0][0] if linhas else None


@pytest.fixture
def banco(monkeypatch):
    """`src/db/client.py` apontado para o Supabase local. Apaga só os
    pacientes que o teste criou (o cascade leva agendamentos, predições e
    mensagens) e os eventos que semeou."""
    status = _status_supabase_local()
    monkeypatch.setenv("SUPABASE_URL", status["API_URL"])
    monkeypatch.setenv("SUPABASE_SECRET_KEY", status["SECRET_KEY"])

    import db.client as client_module
    import db.repositories as repositories

    client_module._cliente = None
    pacientes: list[str] = []
    eventos: list[str] = []

    class Banco:
        repo = repositories

        @staticmethod
        def agendamento(dias: int = 2) -> dict:
            paciente, agendamento = _criar_paciente_e_agendamento(
                repositories, dias, f"p4-{uuid.uuid4().hex}"
            )
            pacientes.append(paciente["id"])
            return agendamento

        @staticmethod
        def evento(**evento) -> None:
            evento.setdefault("origem", "processo")
            evento.setdefault("criado_em", f"{DIA_DOS_EVENTOS}T12:00:00-03:00")
            linha = client_module.obter_client().table("eventos_app").insert(evento).execute()
            eventos.append(linha.data[0]["id"])

    yield Banco

    client = client_module.obter_client()
    if eventos:
        client.table("eventos_app").delete().in_("id", eventos).execute()
    if pacientes:
        client.table("pacientes").delete().in_("id", pacientes).execute()
    client_module._cliente = None


# --- fronteira do papel -------------------------------------------------------


@pytest.mark.integracao
def test_grafana_leitura_so_alcanca_as_views_de_agregados():
    """Regra do Passo 8.9: o papel do Grafana só lê agregados. Alcançar uma
    relação exige `usage` no schema E `select` nela; a lista de tudo que o
    papel alcança tem de ser exatamente as views de `observabilidade` --
    nenhuma tabela de `public` (pacientes, agendamentos, predicoes,
    mensagens_disparadas, eventos_app, pedidos), do `treino` nem do `auth`."""
    alcancadas = _sql(
        """
        select n.nspname || '.' || c.relname
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        where c.relkind in ('r', 'v', 'm', 'p', 'f', 'S')
          and n.nspname not in ('pg_catalog', 'information_schema', 'pg_toast')
          and has_schema_privilege('grafana_leitura', n.oid, 'usage')
          and has_table_privilege('grafana_leitura', c.oid, 'select')
        order by 1
        """
    )
    assert [linha[0] for linha in alcancadas] == [f"observabilidade.{v}" for v in VIEWS]


@pytest.mark.integracao
def test_grafana_leitura_nao_executa_funcao_security_definer():
    """As RPCs `public.treino_*` são `security definer` e abrem o schema
    `treino`: executá-las seria contornar a fronteira das views."""
    executaveis = _sql(
        """
        select p.oid::regprocedure::text
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where p.prosecdef
          and n.nspname not in ('pg_catalog', 'information_schema')
          and has_function_privilege('grafana_leitura', p.oid, 'execute')
        """
    )
    assert executaveis == []


@pytest.mark.integracao
def test_grafana_leitura_e_papel_sem_poder_e_so_de_leitura():
    atributos = _sql(
        """
        select rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication
        from pg_roles where rolname = 'grafana_leitura'
        """
    )
    assert atributos == [["f", "f", "f", "f", "f"]]
    configuracao = _valor(
        """
        select array_to_string(setconfig, ',') from pg_db_role_setting
        where setrole = 'grafana_leitura'::regrole and setdatabase = 0
        """
    )
    assert "default_transaction_read_only=on" in (configuracao or "")
    assert "statement_timeout=30s" in (configuracao or "")


@pytest.mark.integracao
def test_papeis_da_api_nao_enxergam_o_schema_observabilidade():
    """O schema fica fora do PostgREST: nem a chave publicável (anon), nem a
    de usuário (authenticated), nem a secreta (service_role) o leem."""
    papeis = _sql(
        """
        select papel from unnest(array['anon', 'authenticated', 'service_role']) as papel
        where has_schema_privilege(papel, 'observabilidade', 'usage')
        """
    )
    assert papeis == []


# --- o que as views calculam ---------------------------------------------------


@pytest.mark.integracao
def test_eventos_por_dia_tira_recusado_do_numerador_e_da_percentis(banco):
    acao = f"teste_p4_{uuid.uuid4().hex[:8]}"
    for duracao in (10, 20, 30, 40, 50):
        banco.evento(tipo="acao_ui", status="ok", duracao_ms=duracao, detalhe={"acao": acao})
    banco.evento(tipo="acao_ui", status="erro", duracao_ms=60, detalhe={"acao": acao})
    banco.evento(tipo="acao_ui", status="recusado", duracao_ms=70, detalhe={"acao": acao})

    linha = _sql(
        """
        select eventos, erros, recusados, taxa_de_erro, p50_ms, p95_ms
        from observabilidade.eventos_por_dia where dia = :'dia' and acao = :'acao'
        """,
        dia=DIA_DOS_EVENTOS,
        acao=acao,
    )
    # 7 eventos, 1 erro: a senha errada (`recusado`) conta no total, nunca no
    # numerador. p50 de 10..70 = 40; p95 por interpolação = 67.
    assert linha == [["7", "1", "1", "0.1429", "40", "67"]]


def _fora_da_allowlist() -> int:
    return int(
        _valor(
            """
            select coalesce(sum(eventos), 0)
            from observabilidade.eventos_fora_da_allowlist_por_dia where dia = :'dia'
            """,
            dia=DIA_DOS_EVENTOS,
        )
        or 0
    )


@pytest.mark.integracao
def test_chave_de_detalhe_fora_da_allowlist_aparece_so_como_contagem(banco):
    antes = _fora_da_allowlist()
    banco.evento(tipo="erro", status="erro", detalhe={"rota": "fila_do_dia"})
    banco.evento(tipo="erro", status="erro", detalhe={"chave_desconhecida": "valor"})
    banco.evento(tipo="erro", status="erro", detalhe=["nao", "e", "objeto"])

    assert _fora_da_allowlist() - antes == 2
    colunas = _sql(
        """
        select column_name from information_schema.columns
        where table_schema = 'observabilidade'
          and table_name = 'eventos_fora_da_allowlist_por_dia'
        order by ordinal_position
        """
    )
    assert [c[0] for c in colunas] == ["dia", "eventos"]


def _disparos_de_hoje() -> list[int]:
    linha = _sql(
        """
        select agendamentos_processados, disparos, disparos_por_risco, nao_enviados,
               falhas_de_envio
        from observabilidade.disparos_por_dia where dia = :'dia'
        """,
        dia=datetime.now(FUSO).date().isoformat(),
    )
    return [int(v) for v in linha[0]] if linha else [0, 0, 0, 0, 0]


@pytest.mark.integracao
def test_disparos_por_dia_conta_agendamentos_processados_e_disparos_cobrados(banco):
    antes = _disparos_de_hoje()
    a1, a2 = banco.agendamento(), banco.agendamento()
    banco.repo.registrar_mensagem(id_agendamento=a1["id"], canal="sms", status_envio="enviado")
    banco.repo.registrar_mensagem(
        id_agendamento=a2["id"], canal="sms", status_envio="falha_envio"
    )
    banco.repo.registrar_mensagem(
        id_agendamento=a2["id"], canal="sms", status_envio="nao_enviado"
    )

    depois = _disparos_de_hoje()
    # Dois agendamentos (a2 tem duas linhas), um disparo cobrado.
    assert [d - a for d, a in zip(depois, antes, strict=True)] == [2, 1, 1, 1, 1]


@pytest.mark.integracao
def test_envios_cobrados_por_mes_soma_segmentos_e_projeta_o_mes(banco):
    """P5: o custo é segmentos x preço, e o preço fica no Grafana. Só envio
    cobrado entra; envio gravado antes da coluna `segmentos` aparece à parte.
    Um canal só deste teste isola a linha da view."""
    canal = f"teste-p5-{uuid.uuid4().hex[:8]}"
    a1, a2, a3 = banco.agendamento(), banco.agendamento(), banco.agendamento()
    registrar = banco.repo.registrar_mensagem
    registrar(id_agendamento=a1["id"], canal=canal, status_envio="enviado", segmentos=2)
    registrar(
        id_agendamento=a2["id"], canal=canal, status_envio="enviado_sem_predicao", segmentos=3
    )
    registrar(id_agendamento=a3["id"], canal=canal, status_envio="enviado")
    registrar(id_agendamento=a3["id"], canal=canal, status_envio="nao_enviado")
    registrar(id_agendamento=a3["id"], canal=canal, status_envio="falha_envio")

    ((mes, disparos, segmentos, sem_segmentos, decorridos, do_mes, projetados),) = _sql(
        """
        select mes, disparos, segmentos, disparos_sem_segmentos, dias_decorridos,
               dias_do_mes, segmentos_projetados
        from observabilidade.envios_cobrados_por_mes where canal = :'canal'
        """,
        canal=canal,
    )
    hoje = datetime.now(FUSO).date()
    dias_do_mes = calendar.monthrange(hoje.year, hoje.month)[1]
    assert (mes, disparos, segmentos, sem_segmentos) == (
        hoje.replace(day=1).isoformat(), "3", "5", "1"
    )
    assert (int(decorridos), int(do_mes)) == (hoje.day, dias_do_mes)
    assert float(projetados) == pytest.approx(5 / hoje.day * dias_do_mes, abs=0.005)


@pytest.mark.integracao
def test_segmentos_so_aceita_numero_positivo(banco):
    agendamento = banco.agendamento()
    with pytest.raises(Exception, match="segmentos_check"):
        banco.repo.registrar_mensagem(
            id_agendamento=agendamento["id"], canal="sms", status_envio="enviado", segmentos=0
        )


@pytest.mark.integracao
def test_predicoes_por_dia_mede_a_cobertura_da_explicacao_por_modelo(banco):
    versao = f"teste-p4-{uuid.uuid4().hex[:8]}"
    explicacao = [{"feature": "idade", "contribuicao": 0.1}]
    for contribuicoes in (explicacao, explicacao, []):
        banco.repo.gravar_predicao(
            id_agendamento=banco.agendamento()["id"],
            probabilidade=0.8,
            classe_prevista=1,
            threshold_usado=0.5,
            explicacao_shap=contribuicoes,
            model_version=versao,
            fora_do_dominio=False,
        )

    linha = _sql(
        """
        select predicoes, com_explicacao, cobertura_explicacao, positivas
        from observabilidade.predicoes_por_dia where model_version = :'versao'
        """,
        versao=versao,
    )
    # Lista vazia não é explicação: o `not null` do schema sozinho daria 100%.
    assert linha == [["3", "2", "0.6667", "3"]]


@pytest.mark.integracao
def test_views_de_agenda_e_de_banco_respondem(banco):
    agendamento = banco.agendamento(dias=400)
    dia = banco.repo.ler_timestamptz(agendamento["data_hora_agendada"]).astimezone(FUSO).date()
    agendados = _valor(
        """
        select agendamentos from observabilidade.agendamentos_por_dia
        where dia_da_consulta = :'dia' and status = 'agendado'
        """,
        dia=dia.isoformat(),
    )
    assert int(agendados or 0) >= 1
    assert int(_valor("select bytes from observabilidade.tamanho_do_banco") or 0) > 0
    # Sem `job_d2` não há erro, só nulos.
    assert len(_sql("select ultimo_ok, ultima_execucao from observabilidade.ultimo_job_d2")) == 1
