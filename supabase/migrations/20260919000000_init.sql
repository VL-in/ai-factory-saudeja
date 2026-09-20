-- SaudeJa - Passo 5: schema inicial (pacientes, agendamentos, predicoes, mensagens_disparadas)
--
-- Minimizacao de PII por design (BRIEFING.md / architecture.md 4.1): `pacientes`
-- guarda so `id_paciente_externo` (referencia ao sistema core da clinica) e
-- atributos demograficos nao identificaveis -- nunca nome/CPF/email/telefone.
-- tests/test_coerencia_repo.py faz grep nestas migrations e falha se essas
-- colunas aparecerem.
--
-- RLS habilitado em todas as tabelas, sem policies: o backend (API/job/UI em
-- processo) acessa via SUPABASE_KEY = service_role, que ignora RLS por
-- construcao do Supabase. Isso bloqueia por padrao qualquer acesso via chave
-- anon/authenticated (ex.: se a URL do projeto vazar), sem exigir desenhar
-- policies que este projeto nao usa (nao ha auth de usuario final ainda --
-- debt conhecido em architecture.md 9).

create table pacientes (
    id uuid primary key default gen_random_uuid(),
    id_paciente_externo text not null unique,
    idade smallint not null check (idade >= 0 and idade <= 120),
    sexo text not null check (sexo in ('F', 'M')),
    criado_em timestamptz not null default now()
);

create table agendamentos (
    id uuid primary key default gen_random_uuid(),
    id_paciente uuid not null references pacientes (id) on delete cascade,
    especialidade text not null,
    distancia_km numeric not null check (distancia_km >= 0),
    data_hora_agendada timestamptz not null,
    dias_entre_agendamento_consulta smallint not null check (dias_entre_agendamento_consulta >= 0),
    historico_noshow smallint not null default 0 check (historico_noshow >= 0),
    status text not null default 'agendado' check (status in ('agendado', 'concluido', 'cancelado')),
    criado_em timestamptz not null default now()
);

-- Consulta do job D-2 (buscar_agendamentos_d2_pendentes): agendamentos de uma
-- data especifica ainda sem predicao gravada.
create index idx_agendamentos_data_hora_agendada on agendamentos (data_hora_agendada);

create table predicoes (
    id uuid primary key default gen_random_uuid(),
    id_agendamento uuid not null references agendamentos (id) on delete cascade,
    probabilidade numeric not null check (probabilidade >= 0 and probabilidade <= 1),
    classe_prevista smallint not null check (classe_prevista in (0, 1)),
    threshold_usado numeric not null check (threshold_usado >= 0 and threshold_usado <= 1),
    explicacao_shap jsonb not null,
    -- Plug do LLM (Passo 2/13): fica NULL ate ExplicadorLLMTrueFoundry ser ativado.
    explicacao_texto text,
    model_version text not null,
    criado_em timestamptz not null default now()
);

create index idx_predicoes_id_agendamento on predicoes (id_agendamento);

create table mensagens_disparadas (
    id uuid primary key default gen_random_uuid(),
    id_agendamento uuid not null references agendamentos (id) on delete cascade,
    canal text not null,
    status_envio text not null,
    criado_em timestamptz not null default now()
);

create index idx_mensagens_disparadas_id_agendamento on mensagens_disparadas (id_agendamento);

alter table pacientes enable row level security;
alter table agendamentos enable row level security;
alter table predicoes enable row level security;
alter table mensagens_disparadas enable row level security;
