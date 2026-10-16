-- SaudeJa - base de treino separada, controlada pela SaudeJa (ADR-010).
--
-- O banco da clinica (schema `public`) guarda o atendimento, em nome da
-- clinica. O schema `treino` guarda so' o que o paciente autorizou para o
-- aprimoramento do modelo (art. 11, I), com a SaudeJa como controladora:
--   - eventos_consentimento: prova do consentimento do treino (art. 8, par. 2);
--   - consultas_treino: a consulta de quem consentiu, com os atributos que o
--     modelo usa, o desfecho e o lembrete -- sem identificador direto e sem
--     data de nascimento (so' a idade na data da consulta);
--   - pedidos_titular: revogacao e eliminacao dos dados de treino.
-- Titular e consulta aparecem por pseudonimo HMAC com chave secreta
-- (src/pseudonimo.py), nunca pelo hash do CPF do banco da clinica. Nao ha' FK
-- para `public`: a base de treino sobrevive a' eliminacao do cadastro na
-- clinica, e o titular continua achavel pelo pseudonimo recalculado do CPF.
--
-- MESMO PROJETO, MESMA CHAVE, SCHEMA FECHADO (decisao D1 do ADR-010): o schema
-- nao e' exposto ao PostgREST e nenhum papel recebe `usage` nele -- nem a
-- chave secreta do backend (`service_role`) le estas tabelas diretamente. O
-- unico acesso e' pelas funcoes `public.treino_*` abaixo, `security definer`,
-- com `execute` so' para `service_role`. Consequencias:
--   - o pseudonimo vai sempre no corpo do POST da RPC, nunca na URL, que fica
--     no log de API do Supabase (LGPD.md 8.1);
--   - a fronteira do que se faz com a base de treino e' a lista de funcoes,
--     que esta migration declara e os testes de integracao exercitam;
--   - a chave publicavel nao alcanca nada (execute revogado de anon,
--     authenticated e public; o Supabase o concede por padrao em funcao nova).
--
-- GARANTIAS NO BANCO, NAO POR CONVENCAO (triggers):
--   - linha de consultas_treino so' entra com consentimento vigente do titular
--     (ultimo evento nao eliminado e' `concedido`; no empate, a revogacao
--     vence). Sem prova, nao ha' dado de treino;
--   - eventos_consentimento e' so' de insercao: o unico update aceito marca
--     `eliminado_em`, e so' dentro de `treino_eliminar_titular` (marca de
--     sessao `saudeja.eliminacao`, que o cliente nao consegue ligar: o
--     PostgREST nao expoe `set_config`); o delete so' depois de 365 dias da
--     eliminacao; truncate recusado. `registrado_em` e' o relogio do banco;
--   - pedidos nascem pendentes e com titular, so' andam por pendente ->
--     executado ou pendente -> recusado, tipo imutavel, vinculo so' anulado,
--     datas do relogio do banco, executado sem pseudonimo (check).
--
-- ORDEM DA ELIMINACAO NO TREINO: primeiro apagar no remote do DVC as versoes
-- do dataset que contem o titular, depois `treino_eliminar_titular`, depois
-- marcar o pedido como executado (que anula o pseudonimo do pedido). O pedido
-- carrega o pseudonimo ate' o fim, entao uma execucao interrompida e' refeita
-- inteira na seguinte.
--
-- SEM COLUNA DE IDENTIFICADOR DIRETO (guarda de
-- tests/test_coerencia_repo.py::test_migrations_sql_sem_coluna_proibida_de_pii).
--
-- ADITIVA: schema novo, que o codigo anterior nao le.

create schema treino;
revoke all on schema treino from public;

-- --- eventos de consentimento ------------------------------------------------

create table treino.eventos_consentimento (
    id uuid primary key default gen_random_uuid(),
    id_titular text not null,
    finalidade text not null check (finalidade in ('treino')),
    acao text not null check (acao in ('concedido', 'revogado')),
    versao_termo text not null,
    -- autoagendamento: o proprio paciente, na tela de agendamento;
    -- clinica: registrado pela equipe no balcao ou por telefone;
    -- pedido_titular: revogacao registrada a partir de um pedido.
    canal text not null check (canal in ('autoagendamento', 'clinica', 'pedido_titular')),
    -- Crianca (art. 14, par. 1): o consentimento e' dado pelos pais ou pelo
    -- responsavel legal.
    consentido_por text not null check (consentido_por in ('titular', 'responsavel_legal')),
    -- Funcionario que registrou (auth.users.id). Nulo no autoagendamento. Sem
    -- FK para o schema auth: a prova nao pode depender da conta do funcionario.
    registrado_por uuid,
    registrado_em timestamptz not null default now(),
    -- Gravado so' por treino_eliminar_titular. Base da purga de 365 dias.
    eliminado_em timestamptz
);

create index idx_treino_eventos_id_titular on treino.eventos_consentimento (id_titular);
create index idx_treino_eventos_eliminado_em on treino.eventos_consentimento (eliminado_em)
    where eliminado_em is not null;

create function treino.eventos_so_insercao() returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'INSERT' then
        if new.eliminado_em is not null then
            raise exception 'treino.eventos_consentimento: eliminado_em so'' e'' gravado na eliminacao (ADR-010)';
        end if;
        new.registrado_em := now();
        return new;
    end if;

    if tg_op = 'TRUNCATE' then
        raise exception 'treino.eventos_consentimento e'' so'' de insercao: truncate recusado (ADR-010)';
    end if;

    if tg_op = 'UPDATE' then
        if coalesce(current_setting('saudeja.eliminacao', true), '') = 'on'
           and old.eliminado_em is null
           and new.eliminado_em is not null
           and (to_jsonb(new) - 'eliminado_em') = (to_jsonb(old) - 'eliminado_em') then
            new.eliminado_em := now();
            return new;
        end if;
        raise exception 'treino.eventos_consentimento e'' so'' de insercao (ADR-010)';
    end if;

    if old.eliminado_em is not null and old.eliminado_em < now() - interval '365 days' then
        return old;
    end if;
    raise exception 'treino.eventos_consentimento: so'' evento eliminado ha'' mais de 365 dias pode ser apagado (ADR-010)';
end;
$$;

create trigger trg_treino_eventos_so_insercao
    before insert or update or delete on treino.eventos_consentimento
    for each row execute function treino.eventos_so_insercao();

create trigger trg_treino_eventos_sem_truncate
    before truncate on treino.eventos_consentimento
    for each statement execute function treino.eventos_so_insercao();

-- Consentimento vigente do treino: ultimo evento nao eliminado e' `concedido`.
-- No empate de instante, a revogacao vence (mesma regra de
-- src/consentimento.py::_ordem).
create function treino.tem_consentimento(p_id_titular text) returns boolean
language sql
stable
set search_path = ''
as $$
    select coalesce((
        select e.acao = 'concedido'
          from treino.eventos_consentimento e
         where e.id_titular = p_id_titular
           and e.finalidade = 'treino'
           and e.eliminado_em is null
         order by e.registrado_em desc, (e.acao = 'revogado') desc
         limit 1
    ), false);
$$;

-- --- consultas de quem consentiu ---------------------------------------------

create table treino.consultas_treino (
    -- HMAC do uuid do agendamento no banco da clinica: chave da
    -- sincronizacao diaria do desfecho (job D-2).
    id_consulta text primary key,
    id_titular text not null,
    -- Idade na data da consulta: a data de nascimento nao vem para ca'.
    idade smallint not null check (idade >= 0),
    sexo text not null check (sexo in ('F', 'M')),
    especialidade text not null,
    distancia_km numeric not null check (distancia_km >= 0),
    dias_entre_agendamento_consulta smallint not null check (dias_entre_agendamento_consulta >= 0),
    -- O valor com que o job D-2 prediz (gravado no agendamento): treino e
    -- serving com o mesmo numero.
    historico_noshow smallint not null check (historico_noshow >= 0),
    data_hora_agendada timestamptz not null,
    status text not null default 'agendado'
        check (status in ('agendado', 'concluido', 'no_show', 'cancelado')),
    -- O SMS muda o desfecho: sem isto o re-treino aprenderia que o alto risco
    -- "comparece" justamente porque foi lembrado (feedback loop).
    lembrete_enviado boolean not null default false,
    criado_em timestamptz not null default now()
);

create index idx_treino_consultas_id_titular on treino.consultas_treino (id_titular);
create index idx_treino_consultas_data_hora on treino.consultas_treino (data_hora_agendada);

create function treino.consultas_so_com_consentimento() returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'INSERT' then
        if not treino.tem_consentimento(new.id_titular) then
            raise exception 'treino.consultas_treino: titular sem consentimento vigente (ADR-010)';
        end if;
        new.criado_em := now();
        return new;
    end if;
    -- Depois de gravada, a linha so' recebe o desfecho e o lembrete.
    if (to_jsonb(new) - 'status' - 'lembrete_enviado')
       is distinct from (to_jsonb(old) - 'status' - 'lembrete_enviado') then
        raise exception 'treino.consultas_treino: so'' status e lembrete_enviado mudam (ADR-010)';
    end if;
    return new;
end;
$$;

create trigger trg_treino_consultas_so_com_consentimento
    before insert or update on treino.consultas_treino
    for each row execute function treino.consultas_so_com_consentimento();

-- --- pedidos do titular sobre o treino ---------------------------------------

create table treino.pedidos_titular (
    id uuid primary key default gen_random_uuid(),
    id_titular text,
    tipo text not null check (tipo in ('eliminacao', 'revogacao')),
    situacao text not null default 'pendente'
        check (situacao in ('pendente', 'executado', 'recusado')),
    -- autoagendamento: revogacao pela caixa desmarcada na tela;
    -- clinica: encaminhado pela equipe da clinica;
    -- saudeja: recebido pelo encarregado da SaudeJa.
    canal text not null check (canal in ('autoagendamento', 'clinica', 'saudeja')),
    criado_em timestamptz not null default now(),
    executado_em timestamptz,
    recusado_em timestamptz,
    constraint treino_pedidos_executado_sem_vinculo
        check (situacao <> 'executado' or id_titular is null),
    constraint treino_pedidos_datas_da_situacao check (
        case situacao
            when 'pendente' then executado_em is null and recusado_em is null
            when 'executado' then executado_em is not null and recusado_em is null
            when 'recusado' then executado_em is null and recusado_em is not null
        end
    )
);

create index idx_treino_pedidos_situacao on treino.pedidos_titular (situacao);

create function treino.pedidos_situacao_e_datas() returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'INSERT' then
        if new.situacao <> 'pendente' then
            raise exception 'treino.pedidos_titular: o pedido nasce pendente (ADR-010)';
        end if;
        if new.id_titular is null then
            raise exception 'treino.pedidos_titular: pedido sem titular (ADR-010)';
        end if;
        new.criado_em := now();
        new.executado_em := null;
        new.recusado_em := null;
        return new;
    end if;

    if new.tipo is distinct from old.tipo or new.canal is distinct from old.canal then
        raise exception 'treino.pedidos_titular: tipo e canal do pedido nao mudam (ADR-010)';
    end if;
    if new.id_titular is distinct from old.id_titular and new.id_titular is not null then
        raise exception 'treino.pedidos_titular: o vinculo com o titular so'' pode ser anulado (ADR-010)';
    end if;

    new.criado_em := old.criado_em;
    new.executado_em := old.executado_em;
    new.recusado_em := old.recusado_em;
    if new.situacao is distinct from old.situacao then
        if old.situacao = 'pendente' and new.situacao = 'executado' then
            new.executado_em := now();
        elsif old.situacao = 'pendente' and new.situacao = 'recusado' then
            new.recusado_em := now();
        else
            raise exception 'treino.pedidos_titular: transicao % -> % recusada (ADR-010)',
                old.situacao, new.situacao;
        end if;
    end if;
    return new;
end;
$$;

create trigger trg_treino_pedidos_situacao_e_datas
    before insert or update on treino.pedidos_titular
    for each row execute function treino.pedidos_situacao_e_datas();

alter table treino.eventos_consentimento enable row level security;
alter table treino.consultas_treino enable row level security;
alter table treino.pedidos_titular enable row level security;

-- --- unica porta de entrada: funcoes public.treino_* --------------------------

create function public.treino_eventos_do_titular(p_id_titular text)
returns table (finalidade text, acao text, versao_termo text, registrado_em timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
    -- Eventos eliminados nao contam: depois de uma eliminacao, o titular so'
    -- volta ao treino com um consentimento novo.
    select e.finalidade, e.acao, e.versao_termo, e.registrado_em
      from treino.eventos_consentimento e
     where e.id_titular = p_id_titular
       and e.eliminado_em is null
     order by e.registrado_em;
$$;

create function public.treino_registrar_consentimentos(
    p_id_titular text,
    p_decisoes jsonb,
    p_versao_termo text,
    p_canal text,
    p_consentido_por text,
    p_registrado_por uuid default null
) returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    gravados integer;
begin
    insert into treino.eventos_consentimento
        (id_titular, finalidade, acao, versao_termo, canal, consentido_por, registrado_por)
    select p_id_titular, d ->> 'finalidade', d ->> 'acao', p_versao_termo, p_canal,
           p_consentido_por, p_registrado_por
      from jsonb_array_elements(p_decisoes) as d;
    get diagnostics gravados = row_count;
    return gravados;
end;
$$;

create function public.treino_inserir_consulta(
    p_id_consulta text,
    p_id_titular text,
    p_idade integer,
    p_sexo text,
    p_especialidade text,
    p_distancia_km numeric,
    p_dias_entre_agendamento_consulta integer,
    p_historico_noshow integer,
    p_data_hora_agendada timestamptz
) returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
    -- `do nothing`: o mesmo agendamento enviado de novo (nova tentativa) nao
    -- duplica nem sobrescreve a linha.
    insert into treino.consultas_treino
        (id_consulta, id_titular, idade, sexo, especialidade, distancia_km,
         dias_entre_agendamento_consulta, historico_noshow, data_hora_agendada)
    values
        (p_id_consulta, p_id_titular, p_idade, p_sexo, p_especialidade, p_distancia_km,
         p_dias_entre_agendamento_consulta, p_historico_noshow, p_data_hora_agendada)
    on conflict (id_consulta) do nothing;
    return found;
end;
$$;

create function public.treino_sincronizar_desfechos(p_itens jsonb) returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    atualizadas integer;
begin
    -- So' atualiza as consultas que existem (quem consentiu) e cujo valor
    -- mudou: as demais chaves do lote nao fazem nada.
    update treino.consultas_treino c
       set status = i.status,
           lembrete_enviado = i.lembrete_enviado
      from jsonb_to_recordset(p_itens)
           as i(id_consulta text, status text, lembrete_enviado boolean)
     where c.id_consulta = i.id_consulta
       and (c.status, c.lembrete_enviado) is distinct from (i.status, i.lembrete_enviado);
    get diagnostics atualizadas = row_count;
    return atualizadas;
end;
$$;

create function public.treino_consultas_para_export(p_desde timestamptz)
returns table (
    id_consulta text,
    id_titular text,
    idade smallint,
    sexo text,
    especialidade text,
    distancia_km numeric,
    dias_entre_agendamento_consulta smallint,
    historico_noshow smallint,
    data_hora_agendada timestamptz,
    status text,
    lembrete_enviado boolean
)
language sql
stable
security definer
set search_path = ''
as $$
    -- Desfecho conhecido, dentro da janela e com consentimento vigente: o
    -- titular que revogou ja' deveria ter sido purgado, e o filtro e' a
    -- defesa para o intervalo entre a revogacao e a purga.
    select c.id_consulta, c.id_titular, c.idade, c.sexo, c.especialidade, c.distancia_km,
           c.dias_entre_agendamento_consulta, c.historico_noshow, c.data_hora_agendada,
           c.status, c.lembrete_enviado
      from treino.consultas_treino c
     where c.status in ('concluido', 'no_show')
       and c.data_hora_agendada >= p_desde
       and treino.tem_consentimento(c.id_titular)
     order by c.data_hora_agendada;
$$;

create function public.treino_contar_sem_desfecho(p_desde timestamptz, p_antes_de timestamptz)
returns integer
language sql
stable
security definer
set search_path = ''
as $$
    select count(*)::integer
      from treino.consultas_treino c
     where c.status = 'agendado'
       and c.data_hora_agendada >= p_desde
       and c.data_hora_agendada < p_antes_de;
$$;

create function public.treino_registrar_pedido(p_id_titular text, p_tipo text, p_canal text)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
    novo uuid;
begin
    insert into treino.pedidos_titular (id_titular, tipo, canal)
    values (p_id_titular, p_tipo, p_canal)
    returning id into novo;
    return novo;
end;
$$;

create function public.treino_pedidos_pendentes()
returns table (id uuid, id_titular text, tipo text, canal text, criado_em timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
    select p.id, p.id_titular, p.tipo, p.canal, p.criado_em
      from treino.pedidos_titular p
     where p.situacao = 'pendente'
     order by p.criado_em;
$$;

create function public.treino_eliminar_titular(p_id_titular text) returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    apagadas integer;
begin
    delete from treino.consultas_treino where id_titular = p_id_titular;
    get diagnostics apagadas = row_count;
    -- A prova fica 365 dias, marcada como eliminada: o titular sai do treino
    -- e so' volta com um consentimento novo.
    perform set_config('saudeja.eliminacao', 'on', true);
    update treino.eventos_consentimento
       set eliminado_em = now()
     where id_titular = p_id_titular
       and eliminado_em is null;
    perform set_config('saudeja.eliminacao', 'off', true);
    return apagadas;
end;
$$;

create function public.treino_concluir_pedido(p_id uuid, p_situacao text) returns boolean
language plpgsql
security definer
set search_path = ''
as $$
begin
    if p_situacao not in ('executado', 'recusado') then
        raise exception 'treino_concluir_pedido: situacao % invalida (ADR-010)', p_situacao;
    end if;
    -- Executado perde o pseudonimo no mesmo update (check da tabela);
    -- recusado o mantem, como prova de a quem a recusa foi respondida.
    update treino.pedidos_titular
       set situacao = p_situacao,
           id_titular = case when p_situacao = 'executado' then null else id_titular end
     where id = p_id
       and situacao = 'pendente';
    return found;
end;
$$;

create function public.treino_purgar(p_retencao_consultas_dias integer) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    consultas integer;
    eventos integer;
    recusados integer;
begin
    -- Relogio do banco nos tres cortes: a purga nao depende do relogio de
    -- quem chama, e o trigger dos eventos compara com o mesmo now().
    delete from treino.consultas_treino
     where data_hora_agendada < now() - make_interval(days => p_retencao_consultas_dias);
    get diagnostics consultas = row_count;

    delete from treino.eventos_consentimento
     where eliminado_em < now() - interval '365 days';
    get diagnostics eventos = row_count;

    -- Recusa respondida ha' mais de 365 dias: o pseudonimo nao precisa mais
    -- ficar como prova.
    delete from treino.pedidos_titular
     where situacao = 'recusado'
       and recusado_em < now() - interval '365 days';
    get diagnostics recusados = row_count;

    return jsonb_build_object(
        'consultas', consultas, 'eventos', eventos, 'pedidos_recusados', recusados
    );
end;
$$;

-- Execute so' para a chave secreta do backend. O Supabase concede execute a
-- anon e authenticated por padrao em funcao nova do schema public, e o
-- Postgres concede a public: os tres sao revogados.
do $$
declare
    f text;
begin
    foreach f in array array[
        'public.treino_eventos_do_titular(text)',
        'public.treino_registrar_consentimentos(text, jsonb, text, text, text, uuid)',
        'public.treino_inserir_consulta(text, text, integer, text, text, numeric, integer, integer, timestamptz)',
        'public.treino_sincronizar_desfechos(jsonb)',
        'public.treino_consultas_para_export(timestamptz)',
        'public.treino_contar_sem_desfecho(timestamptz, timestamptz)',
        'public.treino_registrar_pedido(text, text, text)',
        'public.treino_pedidos_pendentes()',
        'public.treino_eliminar_titular(text)',
        'public.treino_concluir_pedido(uuid, text)',
        'public.treino_purgar(integer)'
    ] loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
        execute format('grant execute on function %s to service_role', f);
    end loop;
end;
$$;
