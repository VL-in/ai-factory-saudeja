-- SaudeJa - Supabase como fonte de dados do Grafana (Passo 8.9, P4).
--
-- O plano gratuito do Grafana Cloud guarda metricas, logs e traces por 14
-- dias, e o SLO e' mensal: os paineis e alertas de longo prazo leem o Postgres
-- direto (fonte de dados Postgres do Grafana), e `eventos_app` continua sendo a
-- fonte de verdade (90 dias).
--
-- O Grafana NAO le tabela nenhuma. Ele entra pelo papel `grafana_leitura`, que
-- so' enxerga o schema `observabilidade`, e o schema so' tem VIEWS DE
-- AGREGADOS: contagens e percentis por dia, sem uma linha por paciente,
-- agendamento ou predicao. As views rodam com os direitos do dono (postgres),
-- entao o papel le o agregado sem ter `select` em `pacientes`, `agendamentos`,
-- `predicoes`, `mensagens_disparadas`, `eventos_app` nem no schema `treino`.
-- tests/test_db_observabilidade.py (-m integracao) prova as duas coisas.
--
-- O schema fica fora dos expostos pelo PostgREST (supabase/config.toml,
-- [api].schemas), e anon/authenticated/service_role nao tem `usage` nele.
--
-- O papel nasce NOLOGIN. Quem liga o login e define a senha e' a autora, a mao,
-- em cada projeto (dev e prod), e a senha so' existe na fonte de dados do
-- Grafana -- nunca no git:
--
--     alter role grafana_leitura with login password '<senha>';
--
-- Conexao pelo pooler do Supabase (usuario `grafana_leitura.<project_ref>`),
-- com sslmode=require.
--
-- Dia = dia civil da clinica (America/Sao_Paulo, o default de
-- `config_projeto.fuso_da_clinica`), como a fila do dia e o job D-2. Janela de
-- metrica em horas continua em UTC no codigo; aqui o recorte e' por dia porque
-- a regra dos +-40% de disparos e o SLO mensal sao por dia civil.
--
-- View nova neste schema precisa do proprio `grant select ... to
-- grafana_leitura` na migration que a cria: o `grant on all tables` abaixo so'
-- vale para as views que ja existem.

create schema observabilidade;
revoke all on schema observabilidade from public;

-- Papeis sao do cluster, nao do banco: um `supabase db reset` local recria o
-- banco e encontra o papel ja existente.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'grafana_leitura') then
        create role grafana_leitura nologin;
    end if;
end
$$;

-- Defesa em profundidade para quando o login estiver ligado: so' leitura,
-- consulta que nao trava o banco do free tier, poucas conexoes.
alter role grafana_leitura set default_transaction_read_only = on;
alter role grafana_leitura set statement_timeout = '30s';
alter role grafana_leitura set search_path = observabilidade;
alter role grafana_leitura connection limit 5;

-- --- mensageria: disparos e taxa de disparo por dia ---------------------------
-- Todo agendamento que o job D-2 processa termina com uma linha em
-- `mensagens_disparadas` (enviado, enviado_sem_predicao, nao_enviado ou
-- falha_envio), entao `agendamentos_processados` e' o denominador da taxa de
-- disparo. Disparo = linha cobrada pela Infobip (enviado, enviado_sem_predicao).
create view observabilidade.disparos_por_dia as
select
    dia,
    agendamentos_processados,
    disparos,
    disparos_por_risco,
    disparos_sem_predicao,
    nao_enviados,
    falhas_de_envio,
    round(disparos::numeric / nullif(agendamentos_processados, 0), 4) as taxa_de_disparo
from (
    select
        (criado_em at time zone 'America/Sao_Paulo')::date as dia,
        count(distinct id_agendamento) as agendamentos_processados,
        count(*) filter (where status_envio in ('enviado', 'enviado_sem_predicao')) as disparos,
        count(*) filter (where status_envio = 'enviado') as disparos_por_risco,
        count(*) filter (where status_envio = 'enviado_sem_predicao') as disparos_sem_predicao,
        count(*) filter (where status_envio = 'nao_enviado') as nao_enviados,
        count(*) filter (where status_envio = 'falha_envio') as falhas_de_envio
    from public.mensagens_disparadas
    group by 1
) por_dia;

-- --- agenda: consultas por dia e status --------------------------------------
-- Formato longo (uma linha por status) para nao quebrar quando o `check` de
-- status ganhar valor novo. Sem especialidade: e' dado de saude, e o painel
-- nao precisa dela. Serve a regra P1 "zero disparos com agenda".
create view observabilidade.agendamentos_por_dia as
select
    (data_hora_agendada at time zone 'America/Sao_Paulo')::date as dia_da_consulta,
    status,
    count(*) as agendamentos,
    count(*) filter (where lembrete_enviado) as com_lembrete
from public.agendamentos
group by 1, 2;

-- --- modelo: predicoes, cobertura SHAP e fora do dominio por dia -------------
-- `explicacao_shap` e' `not null` no schema, entao "tem explicacao" aqui e' uma
-- lista nao vazia de contribuicoes (`explain.explicar`). O `case` garante que
-- `jsonb_array_length` so' roda sobre array.
create view observabilidade.predicoes_por_dia as
select
    dia,
    model_version,
    predicoes,
    com_explicacao,
    round(com_explicacao::numeric / predicoes, 4) as cobertura_explicacao,
    fora_do_dominio,
    positivas
from (
    select
        (criado_em at time zone 'America/Sao_Paulo')::date as dia,
        model_version,
        count(*) as predicoes,
        count(*) filter (
            where case
                when jsonb_typeof(explicacao_shap) = 'array'
                    then jsonb_array_length(explicacao_shap) > 0
                else false
            end
        ) as com_explicacao,
        count(*) filter (where fora_do_dominio) as fora_do_dominio,
        count(*) filter (where classe_prevista = 1) as positivas
    from public.predicoes
    group by 1, 2
) por_dia;

-- --- aplicacao: eventos com percentis, por dia e por mes ---------------------
-- `acao` e' o nome da acao da UI (`detalhe->>'acao'`, conjunto fechado escrito
-- em src/ui/logic.py) e, nos demais tipos, o proprio tipo. `rota` separa as
-- origens de `erro` (falha de banco da UI por operacao, `enviar_lembrete`,
-- quarentena sem rota). Taxa de erro com `recusado` no total e nunca no
-- numerador: senha errada nao e' falha do sistema.
-- Percentil por mes nao sai de percentil por dia, por isso duas views.
create view observabilidade.eventos_por_dia as
select
    (criado_em at time zone 'America/Sao_Paulo')::date as dia,
    tipo,
    coalesce(detalhe ->> 'acao', tipo) as acao,
    detalhe ->> 'rota' as rota,
    origem,
    count(*) as eventos,
    count(*) filter (where status = 'erro') as erros,
    count(*) filter (where status = 'recusado') as recusados,
    round(count(*) filter (where status = 'erro')::numeric / count(*), 4) as taxa_de_erro,
    percentile_cont(0.50) within group (order by duracao_ms) as p50_ms,
    percentile_cont(0.95) within group (order by duracao_ms) as p95_ms,
    percentile_cont(0.99) within group (order by duracao_ms) as p99_ms
from public.eventos_app
group by 1, 2, 3, 4, 5;

create view observabilidade.eventos_por_mes as
select
    date_trunc('month', criado_em at time zone 'America/Sao_Paulo')::date as mes,
    tipo,
    coalesce(detalhe ->> 'acao', tipo) as acao,
    origem,
    count(*) as eventos,
    count(*) filter (where status = 'erro') as erros,
    count(*) filter (where status = 'recusado') as recusados,
    round(count(*) filter (where status = 'erro')::numeric / count(*), 4) as taxa_de_erro,
    percentile_cont(0.50) within group (order by duracao_ms) as p50_ms,
    percentile_cont(0.95) within group (order by duracao_ms) as p95_ms,
    percentile_cont(0.99) within group (order by duracao_ms) as p99_ms
from public.eventos_app
group by 1, 2, 3, 4;

-- --- job D-2: ultima execucao ------------------------------------------------
-- Regra P1 "fila D-2 nao processada ate' o horario-limite": o horario do
-- ultimo `job_d2` com `ok`, sem precisar de janela.
create view observabilidade.ultimo_job_d2 as
select
    max(criado_em) filter (where status = 'ok') as ultimo_ok,
    max(criado_em) as ultima_execucao
from public.eventos_app
where tipo = 'job_d2';

-- --- LGPD: chave de `detalhe` fora da allowlist ------------------------------
-- Regra P1 "PII em eventos_app". `src/observabilidade.py` so' grava chaves de
-- CHAVES_DETALHE_PERMITIDAS; uma linha aqui e' sinal de que alguem gravou
-- `eventos_app` por fora do codigo. Sai so' a contagem por dia, nunca a chave
-- nem o valor. A lista abaixo e' conferida contra a do Python por
-- tests/test_coerencia_repo.py: chave nova na allowlist pede
-- `create or replace view` numa migration nova.
-- allowlist: observabilidade.CHAVES_DETALHE_PERMITIDAS
create view observabilidade.eventos_fora_da_allowlist_por_dia as
select dia, count(*) as eventos
from (
    select
        (criado_em at time zone 'America/Sao_Paulo')::date as dia,
        case
            when jsonb_typeof(detalhe) <> 'object' then true
            else exists (
                select 1
                from jsonb_object_keys(detalhe) as chave
                where chave <> all (array[
                    'acao',
                    'agendamentos_encontrados',
                    'braco',
                    'campo_invalido',
                    'erros',
                    'eventos_purgados',
                    'excecao',
                    'falhas_de_envio',
                    'id_execucao',
                    'lembretes_sem_predicao',
                    'mensagens_disparadas',
                    'predicoes_gravadas',
                    'quarentena',
                    'rota',
                    'status_http'
                ]::text[])
            )
        end as fora
    from public.eventos_app
) classificados
where fora
group by dia;

-- --- cota do free tier -------------------------------------------------------
create view observabilidade.tamanho_do_banco as
select pg_database_size(current_database()) as bytes;

-- --- permissoes ---------------------------------------------------------------
-- Revoga antes de conceder: privilegio padrao de algum papel do Supabase nao
-- pode abrir estas views para anon/authenticated.
revoke all on all tables in schema observabilidade from public, anon, authenticated, service_role;
grant usage on schema observabilidade to grafana_leitura;
grant select on all tables in schema observabilidade to grafana_leitura;
