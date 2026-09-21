-- SaudeJa - Passo 8.5: observabilidade de aplicacao (ADR-006).
--
-- Camada INTERNA da decisao do ADR-006, e a unica que guarda estado: o log de
-- runtime do HF Space e' efemero (restart/rebuild apaga, sem busca nem
-- retencao), entao os numeros do SLO Sec2/Sec4 precisam morar num lugar que
-- sobreviva ao sleep do Space. Fica no mesmo projeto Supabase (sa-east-1),
-- pelo mesmo argumento de LGPD do ADR-005: nada sai do Brasil.
--
-- SEM NENHUMA COLUNA DE PII, por construcao. `detalhe` e' jsonb livre no
-- schema, mas NAO no codigo: src/observabilidade.py so aceita chaves de uma
-- allowlist fechada (contadores, rota, status HTTP, nome de classe de
-- excecao) e nunca texto livre de mensagem de erro -- uma mensagem da Infobip,
-- por exemplo, carregaria o telefone do paciente para dentro do jsonb, que o
-- grep de nome de coluna de tests/test_coerencia_repo.py nao teria como pegar.
--
-- `tipo`  : predicao (uma predicao servida) | job_d2 (uma execucao do job) |
--           erro (falha registrada fora do caminho de uma predicao)
-- `origem`: api (middleware do FastAPI) | processo (UI chamando o modelo em
--           processo, ADR-005 b) | job (src/jobs/inferencia_diaria.py).
--           Sem isso, o p95 do SLO Sec2 misturaria caminhos com custo
--           diferente e nao diria qual deles esta lento.
--
-- RETENCAO: a tabela cresce a cada predicao e divide o teto do free tier com
-- os dados do produto (risco registrado no ADR-006). A purga roda no proprio
-- job diario (src/jobs/inferencia_diaria.py::main, OBSERVABILIDADE_RETENCAO_DIAS,
-- default 90) em vez de depender de pg_cron -- agendador novo seria mais uma
-- peca de infra a manter, e o job ja roda uma vez por dia.

create table eventos_app (
    id uuid primary key default gen_random_uuid(),
    criado_em timestamptz not null default now(),
    tipo text not null check (tipo in ('predicao', 'job_d2', 'erro')),
    origem text not null check (origem in ('api', 'processo', 'job')),
    duracao_ms integer check (duracao_ms >= 0),
    status text not null check (status in ('ok', 'erro')),
    model_version text,
    detalhe jsonb not null default '{}'::jsonb
);

-- Toda leitura da aba "Observabilidade" e a purga de retencao filtram por
-- janela de tempo recente; o indice desc evita varrer a tabela inteira
-- conforme ela acumula meses de eventos.
create index idx_eventos_app_criado_em on eventos_app (criado_em desc);
create index idx_eventos_app_tipo_criado_em on eventos_app (tipo, criado_em desc);

alter table eventos_app enable row level security;
