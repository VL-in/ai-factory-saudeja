-- SaudeJa - gatilho do job D-2 no horario (Passo 8.9, P7; problema 14 do plano).
--
-- O `schedule` do `.github/workflows/job_d2.yml` (11:17 UTC = 08:17 em Sao
-- Paulo) chegou entre 3 e 9 horas atrasado de 06 a 17/10/2026: o repositorio e'
-- um fork, e o GitHub nao garante horario para workflow agendado. O lembrete
-- chegava a tarde, e o SLO §1 ("fila processada ate' as 09h") nao era cumprido.
--
-- O `workflow_dispatch` comeca na hora. Este pg_cron, no Supabase de producao
-- (Sao Paulo), chama a API do GitHub as 11:17 UTC e dispara o mesmo workflow em
-- `main`, environment `production` -- exatamente o caminho do cron. O cron do
-- GitHub continua no YAML como reserva: se o gatilho falhar, o job ainda roda,
-- atrasado, e a execucao repetida nao manda SMS em dobro (a fila so' traz quem
-- nao tem predicao nem lembrete, e o `concurrency` do workflow serializa as
-- duas).
--
-- O token NAO esta' aqui. Ele fica no Vault do Supabase de producao, criado a
-- mao pela autora, junto do repositorio:
--
--     select vault.create_secret('<token>', 'github_job_d2_token');
--     select vault.create_secret('<dono>/<repositorio>', 'github_job_d2_repositorio');
--
-- Token fine-grained, so' deste repositorio, com "Actions: Read and write". Sem
-- os dois segredos -- no Supabase de dev, no local e no CI -- a funcao nao faz
-- nada: o disparo diario existe so' em producao, sem nenhum `if` de ambiente no
-- codigo. Em dev o job continua sendo so' por dispatch manual.
--
-- A resposta do GitHub fica em `net._http_response` por 6 horas (204 = ok);
-- a regra P1 "fila D-2 nao processada" do Grafana e' quem avisa se o gatilho
-- parar (token expirado, por exemplo).

create extension if not exists pg_cron with schema pg_catalog;
create extension if not exists pg_net with schema extensions;
-- No Supabase, a extensao e' criada em nome do `supabase_admin`; o `postgres`
-- (dono das migrations e de quem o pg_cron herda o papel ao rodar o comando)
-- precisa destas concessoes para agendar (receita da documentacao do pg_cron
-- no Supabase).
grant usage on schema cron to postgres;
grant all privileges on all tables in schema cron to postgres;

-- O pg_net nasce aberto: `usage` no schema `net` e `select` nas tabelas dele
-- chegam a todo papel (o teste de integracao do `grafana_leitura` pegou isso).
-- `net.http_request_queue` guarda o pedido ate' ele sair, COM o cabecalho
-- Authorization -- o token do GitHub --, e `net._http_response` guarda a
-- resposta. O papel do Grafana tem login com senha e SQL livre: leria o token.
-- Fica so' com o `postgres`, que e' quem chama `net.http_post` pelo pg_cron.
revoke all on schema net from public, anon, authenticated, service_role;
revoke all on all tables in schema net from public, anon, authenticated, service_role;
revoke all on all sequences in schema net from public, anon, authenticated, service_role;
revoke all on all functions in schema net from public, anon, authenticated, service_role;
grant usage on schema net to postgres;
grant execute on all functions in schema net to postgres;

-- `revoke` sem privilegio para tirar so' avisa, nao falha. Se a concessao
-- vier de um caminho que este arquivo nao alcanca, a migration para aqui
-- (inteira, numa transacao) em vez de subir com o token legivel.
do $$
declare
    papel text;
begin
    -- Alcancar uma tabela exige `usage` no schema E `select` nela, o mesmo
    -- criterio do teste de integracao.
    foreach papel in array array['anon', 'authenticated', 'service_role', 'grafana_leitura'] loop
        if has_schema_privilege(papel, 'net', 'usage')
            and (has_table_privilege(papel, 'net.http_request_queue', 'select')
                 or has_table_privilege(papel, 'net._http_response', 'select')) then
            raise exception 'o papel % ainda le as tabelas do pg_net', papel;
        end if;
    end loop;
    -- Por nome, nao por assinatura: a lista de argumentos muda entre versoes.
    if not has_schema_privilege('postgres', 'net', 'usage') or not exists (
        select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'net' and p.proname = 'http_post'
          and has_function_privilege('postgres', p.oid, 'execute')
    ) then
        raise exception 'o postgres nao alcanca net.http_post: o gatilho nao dispararia';
    end if;
end
$$;

-- Schema proprio, fora dos expostos pelo PostgREST e sem `usage` para ninguem
-- alem do dono: a funcao le um segredo do Vault.
create schema agendador;
revoke all on schema agendador from public;

-- Direitos de quem chama (sem `security definer`): o pg_cron roda o comando
-- como o papel que o agendou, o dono das migrations.
create function agendador.disparar_job_d2() returns void
language plpgsql
set search_path = ''
as $$
declare
    token text;
    repositorio text;
begin
    select decrypted_secret into token
    from vault.decrypted_secrets where name = 'github_job_d2_token';
    select decrypted_secret into repositorio
    from vault.decrypted_secrets where name = 'github_job_d2_repositorio';
    if token is null or repositorio is null then
        return;
    end if;
    perform net.http_post(
        url := 'https://api.github.com/repos/' || repositorio
            || '/actions/workflows/job_d2.yml/dispatches',
        body := jsonb_build_object(
            'ref', 'main',
            'inputs', jsonb_build_object('ambiente', 'production')
        ),
        headers := jsonb_build_object(
            'Authorization', 'Bearer ' || token,
            'Accept', 'application/vnd.github+json',
            'X-GitHub-Api-Version', '2022-11-28',
            'User-Agent', 'saudeja-pg-cron'
        ),
        timeout_milliseconds := 10000
    );
end
$$;

revoke all on function agendador.disparar_job_d2() from public, anon, authenticated, service_role;

-- Mesmo horario do `schedule` do job_d2.yml (tests/test_coerencia_repo.py
-- confere). O pg_cron do Supabase roda em UTC. `cron.schedule` com nome
-- substitui o agendamento de mesmo nome: reaplicar nao duplica.
select cron.schedule('job-d2', '17 11 * * *', 'select agendador.disparar_job_d2()');
