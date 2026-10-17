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
-- Extensao `http` (sincrona), e nao `pg_net`. O pg_net enfileira o pedido em
-- `net.http_request_queue` -- com o cabecalho Authorization, o token -- e o
-- Supabase abre o schema `net` a todo papel, por concessao do `supabase_admin`
-- que o `postgres` das migrations nao consegue revogar (tentado em 2026-10-17:
-- o `revoke` passa em silencio e o acesso continua). O `grafana_leitura`, com
-- login e SQL livre, leria o token. A `http` faz a chamada dentro da funcao e
-- nao grava o pedido em tabela nenhuma; o teste de integracao do papel do
-- Grafana confere que ele continua alcancando so' as views de agregados.
--
-- Resultado de cada execucao: `cron.job_run_details` (so' o `postgres` le). Status
-- diferente de 2xx vira erro ali, com o codigo; a regra P1 "fila D-2 nao
-- processada" do Grafana e' quem avisa se o gatilho parar (token expirado, por
-- exemplo).

create extension if not exists pg_cron with schema pg_catalog;
create extension if not exists http with schema extensions;
-- No Supabase, a extensao e' criada em nome do `supabase_admin`; o `postgres`
-- (dono das migrations e de quem o pg_cron herda o papel ao rodar o comando)
-- precisa destas concessoes para agendar (receita da documentacao do pg_cron
-- no Supabase).
grant usage on schema cron to postgres;
grant all privileges on all tables in schema cron to postgres;

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
    resposta extensions.http_response;
begin
    select decrypted_secret into token
    from vault.decrypted_secrets where name = 'github_job_d2_token';
    select decrypted_secret into repositorio
    from vault.decrypted_secrets where name = 'github_job_d2_repositorio';
    if token is null or repositorio is null then
        return;
    end if;
    perform extensions.http_set_curlopt('CURLOPT_TIMEOUT', '15');
    resposta := extensions.http((
        'POST',
        'https://api.github.com/repos/' || repositorio
            || '/actions/workflows/job_d2.yml/dispatches',
        array[
            extensions.http_header('Authorization', 'Bearer ' || token),
            extensions.http_header('Accept', 'application/vnd.github+json'),
            extensions.http_header('X-GitHub-Api-Version', '2022-11-28'),
            extensions.http_header('User-Agent', 'saudeja-pg-cron')
        ],
        'application/json',
        jsonb_build_object(
            'ref', 'main',
            'inputs', jsonb_build_object('ambiente', 'production')
        )::text
    )::extensions.http_request);
    -- 204 e' o sucesso do dispatch. O corpo de erro do GitHub ("Bad
    -- credentials", "Not Found") nao traz o token.
    if resposta.status not between 200 and 299 then
        raise exception 'dispatch do job D-2 recusado pelo GitHub: HTTP % %',
            resposta.status, left(coalesce(resposta.content, ''), 200);
    end if;
end
$$;

revoke all on function agendador.disparar_job_d2() from public, anon, authenticated, service_role;

-- Mesmo horario do `schedule` do job_d2.yml (tests/test_coerencia_repo.py
-- confere). O pg_cron do Supabase roda em UTC. `cron.schedule` com nome
-- substitui o agendamento de mesmo nome: reaplicar nao duplica.
select cron.schedule('job-d2', '17 11 * * *', 'select agendador.disparar_job_d2()');
