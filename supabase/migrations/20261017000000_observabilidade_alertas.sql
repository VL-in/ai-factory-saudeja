-- SaudeJa - fonte da regra P1 "agendamentos que o job D-2 deixou para tras"
-- (Passo 8.9, P7).
--
-- As regras de alerta do Grafana (infra/grafana/alertas/) leem so' as views
-- deste schema. A regra "zero disparos com agenda" nao podia sair de
-- `disparos_por_dia`: um dia sem disparo e' normal (ninguem acima do
-- threshold, ou nenhuma consulta em D+2), e um P1 que dispara em dia normal
-- ensina a ignorar o P1. O que e' defeito e' outra coisa: o job rodou com
-- sucesso e um agendamento que ja' existia, dentro da janela dele, ficou sem
-- predicao e sem mensagem.
--
-- Por dia de execucao do job (dia civil da clinica), a partir da ultima
-- execucao `ok` do dia em `eventos_app`. O inicio dela e' `criado_em` menos
-- `duracao_ms`: o evento `job_d2` e' gravado no fim de `processar_dia`, e a
-- fila e' buscada no inicio. So' conta o agendamento criado antes desse inicio,
-- porque o criado depois e' da proxima execucao.
--
-- Fora da contagem, de proposito:
--   - quem tem predicao (foi processado) ou linha em `mensagens_disparadas`
--     (foi processado e o envio falhou: e' a regra de falha de SMS, P2);
--   - quem ja' recebeu lembrete (`lembrete_enviado`) ou nao esta' `agendado`.
--
-- So' a contagem sai da view: nenhuma linha por agendamento, nenhum
-- identificador, como as demais views do schema.
create view observabilidade.agendamentos_sem_processamento_por_dia as
with execucoes as (
    select distinct on (dia)
        dia,
        criado_em - coalesce(duracao_ms, 0) * interval '1 millisecond' as inicio
    from (
        select
            (criado_em at time zone 'America/Sao_Paulo')::date as dia,
            criado_em,
            duracao_ms
        from public.eventos_app
        where tipo = 'job_d2' and status = 'ok'
    ) ok
    order by dia, criado_em desc
)
select
    e.dia,
    count(a.id) as agendamentos
from execucoes e
left join public.agendamentos a
    on a.status = 'agendado'
    and not a.lembrete_enviado
    and a.criado_em < e.inicio
    and (a.data_hora_agendada at time zone 'America/Sao_Paulo')::date
        between e.dia + 1 and e.dia + 2
    and not exists (select 1 from public.predicoes p where p.id_agendamento = a.id)
    and not exists (select 1 from public.mensagens_disparadas m where m.id_agendamento = a.id)
group by e.dia;

-- Mesmas permissoes das views da P4 e da P5.
revoke all on observabilidade.agendamentos_sem_processamento_por_dia
    from public, anon, authenticated, service_role;
grant select on observabilidade.agendamentos_sem_processamento_por_dia to grafana_leitura;
