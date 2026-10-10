-- SaudeJa - consentimento em tabela de eventos e pedidos do titular (ADR-009).
--
-- eventos_consentimento: prova do consentimento (LGPD art. 8, par. 2) do
-- lembrete (art. 7, I), do treino com desfechos reais (art. 11, I) e da
-- transferencia internacional (art. 33, VIII). Cada concessao ou revogacao e'
-- um evento; o consentimento vigente e' o ultimo evento de cada finalidade
-- (src/consentimento.py). Guardar so' o estado atual em colunas de `pacientes`
-- foi descartado no ADR-009: a revogacao apagaria a prova da concessao.
--
-- SO' DE INSERCAO NO BANCO, NAO POR CONVENCAO: o backend usa a chave secreta,
-- que ignora RLS mas nao ignora trigger. O trigger abaixo recusa update e
-- delete, com duas excecoes:
--   1. o `on delete set null` da eliminacao do paciente, que chega aqui como
--      update de `id_paciente` para null -- o trigger grava `eliminado_em`;
--   2. o delete de evento eliminado ha' mais de 365 dias (purga do ADR-009).
-- Por isso a FK e' `set null`, nao `cascade`: na eliminacao, os eventos perdem
-- o vinculo com o cadastro e ficam 365 dias como prova de que o consentimento
-- existia e de quando foi revogado (LGPD.md secao 5). `id_paciente_externo`
-- (hash do CPF) e' o que liga a prova ao titular depois disso; e' pseudonimo,
-- nao identificador direto, e nunca e' usado como filtro de consulta (guarda
-- de URL, LGPD.md 8.1).
--
-- pedidos_titular: pedidos do art. 18 e do art. 20. So' pedidos confirmados
-- pela clinica (controladora) sao executados pelo workflow de purga. Pedido
-- executado nao guarda vinculo com o paciente: fica so' tipo, situacao e
-- datas, como prova de atendimento sem dado pessoal. O `set null` cobre a
-- eliminacao; na revogacao o paciente continua existindo, e o check abaixo
-- obriga o codigo a anular o vinculo ao marcar o pedido como executado.
--
-- SEM COLUNA DE IDENTIFICADOR DIRETO (guarda de
-- tests/test_coerencia_repo.py::test_migrations_sql_sem_coluna_proibida_de_pii).
--
-- ADITIVA: duas tabelas novas, que o codigo anterior nao le. Pode ir no mesmo
-- deploy do codigo que as usa, aplicada antes do sync.

create table eventos_consentimento (
    id uuid primary key default gen_random_uuid(),
    id_paciente uuid references pacientes (id) on delete set null,
    id_paciente_externo text not null,
    finalidade text not null
        check (finalidade in ('lembrete', 'treino', 'transferencia_internacional')),
    acao text not null check (acao in ('concedido', 'revogado')),
    versao_termo text not null,
    -- autoagendamento: o proprio paciente, na tela de agendamento;
    -- clinica: registrado pela equipe no balcao ou por telefone;
    -- pedido_titular: revogacao executada pelo workflow de purga.
    canal text not null check (canal in ('autoagendamento', 'clinica', 'pedido_titular')),
    -- Crianca (art. 14, par. 1): o consentimento e' dado pelos pais ou pelo
    -- responsavel legal.
    consentido_por text not null check (consentido_por in ('titular', 'responsavel_legal')),
    -- Funcionario que registrou (auth.users.id). Nulo no autoagendamento. Sem
    -- FK para o schema auth: a conta do funcionario e' apagada no desligamento,
    -- e a prova do consentimento do paciente nao pode depender dela.
    registrado_por uuid,
    registrado_em timestamptz not null default now(),
    -- Preenchido pelo trigger quando o paciente e' eliminado. Base da purga
    -- de 365 dias; nunca gravado pelo codigo.
    eliminado_em timestamptz
);

create index idx_eventos_consentimento_id_paciente on eventos_consentimento (id_paciente);
-- Purga diaria: eventos eliminados ha' mais de 365 dias.
create index idx_eventos_consentimento_eliminado_em on eventos_consentimento (eliminado_em)
    where eliminado_em is not null;

create function eventos_consentimento_so_insercao() returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'UPDATE' then
        if old.id_paciente is not null
           and new.id_paciente is null
           and old.eliminado_em is null
           and (to_jsonb(new) - 'id_paciente' - 'eliminado_em')
               = (to_jsonb(old) - 'id_paciente' - 'eliminado_em') then
            new.eliminado_em := now();
            return new;
        end if;
        raise exception 'eventos_consentimento e'' so'' de insercao (ADR-009)';
    end if;

    if old.eliminado_em is not null and old.eliminado_em < now() - interval '365 days' then
        return old;
    end if;
    raise exception 'eventos_consentimento: so'' evento eliminado ha'' mais de 365 dias pode ser apagado (ADR-009)';
end;
$$;

create trigger trg_eventos_consentimento_so_insercao
    before update or delete on eventos_consentimento
    for each row execute function eventos_consentimento_so_insercao();

create table pedidos_titular (
    id uuid primary key default gen_random_uuid(),
    id_paciente uuid references pacientes (id) on delete set null,
    -- A revogacao do consentimento da transferencia internacional nao tem tipo
    -- proprio: equivale a `eliminacao` (LGPD.md 4.2).
    tipo text not null
        check (tipo in ('eliminacao', 'revogacao_treino', 'revogacao_lembrete', 'revisao_decisao')),
    situacao text not null default 'pendente'
        check (situacao in ('pendente', 'confirmado_pela_clinica', 'executado', 'recusado')),
    criado_em timestamptz not null default now(),
    confirmado_em timestamptz,
    executado_em timestamptz,
    constraint pedidos_titular_executado_sem_vinculo
        check (situacao <> 'executado' or id_paciente is null)
);

create index idx_pedidos_titular_situacao on pedidos_titular (situacao);

-- Mesmo padrao das demais tabelas: RLS habilitado e sem policies -- so' a
-- chave secreta do backend le e grava.
alter table eventos_consentimento enable row level security;
alter table pedidos_titular enable row level security;
