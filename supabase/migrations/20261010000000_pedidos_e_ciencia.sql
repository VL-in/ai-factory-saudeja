-- SaudeJa - pedidos do titular sobre os dados da clinica e ciencia dos
-- documentos no agendamento (ADR-009, ADR-010).
--
-- Este arquivo ainda nao foi aplicado em nenhum banco remoto e foi reescrito
-- no proprio lugar: a versao anterior criava aqui `eventos_consentimento`,
-- que passou para o schema `treino` (20261011000000_schema_treino.sql). O
-- banco da clinica nao guarda consentimento: o agendamento, a predicao e o
-- lembrete se apoiam na execucao do servico da clinica (art. 7, V; art. 11,
-- II, f), e o unico consentimento do sistema, o do treino, e' da SaudeJa.
--
-- pedidos_titular: pedidos do art. 18 e do art. 20 sobre os dados da
-- clinica, que e' a controladora. So' pedidos confirmados pela clinica sao
-- executados pelo workflow de purga. Pedido executado nao guarda vinculo com
-- o paciente: fica so' tipo, situacao e datas, como prova de atendimento sem
-- dado pessoal. Pedido recusado MANTEM o vinculo enquanto o cadastro existir:
-- a clinica precisa provar a quem respondeu a recusa e por que (art. 18,
-- par. 4). Na eliminacao, o `set null` o anula como em qualquer outro pedido.
-- Pedidos sobre o treino (revogacao, eliminacao dos dados de treino) ficam em
-- `treino.pedidos_titular`, com a SaudeJa como controladora.
--
-- A eliminacao aqui e' so' o `delete` em `pacientes` com cascade: o remote do
-- DVC deixou de receber dado da clinica (o export le so' o schema `treino`),
-- entao nao ha' ordem a respeitar com ele.
--
-- A situacao e as datas do pedido sao garantidas pelo banco: o pedido nasce
-- `pendente` e com paciente, so' anda por pendente -> confirmado -> executado
-- ou pendente -> recusado, o tipo nao muda, o vinculo so' pode ser anulado, e
-- `criado_em`, `confirmado_em`, `executado_em` e `recusado_em` sao gravadas
-- pelo trigger com o relogio do banco (o valor enviado e' ignorado). O prazo
-- de 15 dias do atendimento e' medido por essas datas.
--
-- agendamentos.versao_documentos: versao da Politica de privacidade e dos
-- Termos de uso que o paciente declarou conhecer na tela de agendamento (caixa
-- de ciencia). E' a prova de que a informacao foi apresentada (art. 9), nao um
-- consentimento. Nula nos agendamentos anteriores a ela e nos feitos por
-- outro caminho. Nao e' feature do modelo e o export nao a le.
--
-- SEM COLUNA DE IDENTIFICADOR DIRETO (guarda de
-- tests/test_coerencia_repo.py::test_migrations_sql_sem_coluna_proibida_de_pii).
--
-- ADITIVA: uma tabela nova e uma coluna nula, que o codigo anterior nao le.
-- Pode ir no mesmo deploy do codigo que as usa, aplicada antes do sync.

create table pedidos_titular (
    id uuid primary key default gen_random_uuid(),
    id_paciente uuid references pacientes (id) on delete set null,
    tipo text not null check (tipo in ('eliminacao', 'revisao_decisao')),
    situacao text not null default 'pendente'
        check (situacao in ('pendente', 'confirmado_pela_clinica', 'executado', 'recusado')),
    criado_em timestamptz not null default now(),
    confirmado_em timestamptz,
    executado_em timestamptz,
    recusado_em timestamptz,
    constraint pedidos_titular_executado_sem_vinculo
        check (situacao <> 'executado' or id_paciente is null),
    constraint pedidos_titular_datas_da_situacao check (
        case situacao
            when 'pendente' then
                confirmado_em is null and executado_em is null and recusado_em is null
            when 'confirmado_pela_clinica' then
                confirmado_em is not null and executado_em is null and recusado_em is null
            when 'executado' then
                confirmado_em is not null and executado_em is not null and recusado_em is null
            when 'recusado' then
                confirmado_em is null and executado_em is null and recusado_em is not null
        end
    )
);

create index idx_pedidos_titular_situacao on pedidos_titular (situacao);

create function pedidos_titular_situacao_e_datas() returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'INSERT' then
        if new.situacao <> 'pendente' then
            raise exception 'pedidos_titular: o pedido nasce pendente (ADR-009)';
        end if;
        if new.id_paciente is null then
            raise exception 'pedidos_titular: pedido sem paciente (ADR-009)';
        end if;
        new.criado_em := now();
        new.confirmado_em := null;
        new.executado_em := null;
        new.recusado_em := null;
        return new;
    end if;

    if new.tipo is distinct from old.tipo then
        raise exception 'pedidos_titular: o tipo do pedido nao muda (ADR-009)';
    end if;
    -- Anular cobre o `set null` da eliminacao e a execucao; trocar de
    -- paciente, ou religar um pedido sem vinculo, nao.
    if new.id_paciente is distinct from old.id_paciente and new.id_paciente is not null then
        raise exception 'pedidos_titular: o vinculo com o paciente so'' pode ser anulado (ADR-009)';
    end if;

    new.criado_em := old.criado_em;
    new.confirmado_em := old.confirmado_em;
    new.executado_em := old.executado_em;
    new.recusado_em := old.recusado_em;
    if new.situacao is distinct from old.situacao then
        if old.situacao = 'pendente' and new.situacao = 'confirmado_pela_clinica' then
            new.confirmado_em := now();
        elsif old.situacao = 'pendente' and new.situacao = 'recusado' then
            new.recusado_em := now();
        elsif old.situacao = 'confirmado_pela_clinica' and new.situacao = 'executado' then
            new.executado_em := now();
        else
            raise exception 'pedidos_titular: transicao % -> % recusada (ADR-009)',
                old.situacao, new.situacao;
        end if;
    end if;
    return new;
end;
$$;

create trigger trg_pedidos_titular_situacao_e_datas
    before insert or update on pedidos_titular
    for each row execute function pedidos_titular_situacao_e_datas();

-- Mesmo padrao das demais tabelas: RLS habilitado e sem policies -- so' a
-- chave secreta do backend le e grava.
alter table pedidos_titular enable row level security;

alter table agendamentos add column versao_documentos text;
