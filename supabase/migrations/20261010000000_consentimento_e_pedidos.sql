-- SaudeJa - consentimento em tabela de eventos e pedidos do titular (ADR-009).
--
-- eventos_consentimento: prova do consentimento (LGPD art. 8, par. 2) do
-- treino com desfechos reais (art. 11, I) e da transferencia internacional
-- (art. 33, VIII). O lembrete nao tem finalidade aqui: a base dele e' a
-- execucao do contrato do paciente com a clinica (art. 7, V), sem
-- consentimento (LGPD.md secao 3). Cada concessao ou revogacao e'
-- um evento; o consentimento vigente e' o ultimo evento de cada finalidade
-- (src/consentimento.py). Guardar so' o estado atual em colunas de `pacientes`
-- foi descartado no ADR-009: a revogacao apagaria a prova da concessao.
--
-- SO' DE INSERCAO NO BANCO, NAO POR CONVENCAO: o backend usa a chave secreta,
-- que ignora RLS mas nao ignora trigger. O trigger abaixo:
--   - na insercao, exige o paciente, recusa `eliminado_em` e grava ele mesmo
--     `registrado_em` (relogio do banco) e `id_paciente_externo` (copiado de
--     `pacientes`). Sem isso, um insert com `eliminado_em` retroativo seria
--     apagavel na hora, um evento sem paciente nunca seria apagavel, e um
--     `registrado_em` ou um hash vindos do cliente mudariam qual evento e' o
--     vigente ou a quem a prova pertence;
--   - recusa update e delete, com duas excecoes:
--     1. o `on delete set null` da eliminacao do paciente, que chega aqui como
--        update de `id_paciente` para null -- o trigger grava `eliminado_em`.
--        So' vale se o paciente ja' nao existir: o mesmo update feito pelo
--        codigo com o paciente cadastrado desvincularia a prova e iniciaria a
--        contagem dos 365 dias;
--     2. o delete de evento eliminado ha' mais de 365 dias (purga do ADR-009);
--   - recusa truncate, que nao dispara trigger por linha.
-- Por isso a FK e' `set null`, nao `cascade`: na eliminacao, os eventos perdem
-- o vinculo com o cadastro e ficam 365 dias como prova de que o consentimento
-- existia e de quando foi revogado (LGPD.md secao 5). `id_paciente_externo`
-- (hash do CPF) e' o que liga a prova ao titular depois disso; e' pseudonimo,
-- nao identificador direto, e nunca e' usado como filtro de consulta (guarda
-- de URL, LGPD.md 8.1). Como o trigger o copia de `pacientes`, a aplicacao nem
-- o envia ao registrar um consentimento.
--
-- pedidos_titular: pedidos do art. 18 e do art. 20. So' pedidos confirmados
-- pela clinica (controladora) sao executados pelo workflow de purga. Pedido
-- executado nao guarda vinculo com o paciente: fica so' tipo, situacao e
-- datas, como prova de atendimento sem dado pessoal. O `set null` cobre a
-- eliminacao; na revogacao o paciente continua existindo, e o check abaixo
-- obriga o codigo a anular o vinculo ao marcar o pedido como executado.
-- Pedido recusado MANTEM o vinculo enquanto o cadastro existir: a clinica
-- precisa provar a quem respondeu a recusa e por que (art. 18, par. 4), e o
-- vinculo aponta para um cadastro que ela ja' guarda. Na eliminacao, o
-- `set null` o anula como em qualquer outro pedido.
--
-- A situacao e as datas do pedido tambem sao garantidas pelo banco: o pedido
-- nasce `pendente` e com paciente, so' anda por pendente -> confirmado ->
-- executado ou pendente -> recusado, o tipo nao muda, o vinculo so' pode ser
-- anulado, e `criado_em`, `confirmado_em`, `executado_em` e `recusado_em` sao
-- gravadas pelo trigger com o relogio do banco (o valor enviado e' ignorado).
-- O prazo de 15 dias do atendimento e' medido por essas datas.
--
-- ORDEM DA ELIMINACAO: primeiro apagar no remote do DVC as versoes do dataset
-- que contem o titular, depois o delete em `pacientes`. O delete anula o
-- vinculo dos eventos e dos pedidos, e `pacientes` e' o unico lugar onde o
-- hash do CPF e' encontrado pelo uuid. Com o delete antes, o hash se perde e
-- as versoes do dataset com o titular nao podem mais ser achadas.
--
-- SEM COLUNA DE IDENTIFICADOR DIRETO (guarda de
-- tests/test_coerencia_repo.py::test_migrations_sql_sem_coluna_proibida_de_pii).
--
-- ADITIVA: duas tabelas novas, que o codigo anterior nao le. Pode ir no mesmo
-- deploy do codigo que as usa, aplicada antes do sync.

create table eventos_consentimento (
    id uuid primary key default gen_random_uuid(),
    id_paciente uuid references pacientes (id) on delete set null,
    -- Copiado de `pacientes` pelo trigger na insercao; o valor enviado pelo
    -- cliente e' ignorado.
    id_paciente_externo text not null,
    finalidade text not null
        check (finalidade in ('treino', 'transferencia_internacional')),
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
    -- Relogio do banco, gravado pelo trigger: o valor enviado e' ignorado.
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
    if tg_op = 'INSERT' then
        if new.id_paciente is null then
            raise exception 'eventos_consentimento: evento sem paciente (ADR-009)';
        end if;
        if new.eliminado_em is not null then
            raise exception 'eventos_consentimento: eliminado_em so'' e'' gravado pelo trigger (ADR-009)';
        end if;
        select p.id_paciente_externo into new.id_paciente_externo
          from public.pacientes p
         where p.id = new.id_paciente;
        if not found then
            raise exception 'eventos_consentimento: paciente inexistente (ADR-009)';
        end if;
        new.registrado_em := now();
        return new;
    end if;

    if tg_op = 'TRUNCATE' then
        raise exception 'eventos_consentimento e'' so'' de insercao: truncate recusado (ADR-009)';
    end if;

    if tg_op = 'UPDATE' then
        -- Na eliminacao, a FK age depois de a linha de `pacientes` ter sido
        -- apagada na mesma transacao, entao o `not exists` a distingue do
        -- mesmo update feito pelo codigo com o paciente ainda cadastrado.
        if old.id_paciente is not null
           and new.id_paciente is null
           and old.eliminado_em is null
           and not exists (select 1 from public.pacientes p where p.id = old.id_paciente)
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
    before insert or update or delete on eventos_consentimento
    for each row execute function eventos_consentimento_so_insercao();

create trigger trg_eventos_consentimento_sem_truncate
    before truncate on eventos_consentimento
    for each statement execute function eventos_consentimento_so_insercao();

create table pedidos_titular (
    id uuid primary key default gen_random_uuid(),
    id_paciente uuid references pacientes (id) on delete set null,
    -- A revogacao do consentimento da transferencia internacional nao tem tipo
    -- proprio: equivale a `eliminacao` (LGPD.md 4.2). O lembrete tambem nao:
    -- sem consentimento, nao ha' o que revogar, e a oposicao ao SMS (art. 18,
    -- par. 2) e' atendida pela clinica, caso a caso.
    tipo text not null
        check (tipo in ('eliminacao', 'revogacao_treino', 'revisao_decisao')),
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
alter table eventos_consentimento enable row level security;
alter table pedidos_titular enable row level security;
