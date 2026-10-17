-- SaudeJa - custo da mensageria para o Grafana (Passo 8.9, P5).
--
-- A Infobip cobra por SEGMENTO de SMS, nao por lembrete. Um texto so' com o
-- alfabeto GSM cabe em 160 caracteres; com uma letra fora dele (a com acento
-- agudo, a com til, c cedilha) a mensagem inteira vai em UCS-2 e cabe em 70 --
-- o lembrete atual tem 72 e sai em dois. O job D-2 calcula os segmentos de cada envio
-- (`messaging.client.segmentos_sms`) e os grava aqui; contar lembretes daria
-- metade do custo.
--
-- Aditiva: a coluna e' nula nas linhas antigas e nas que nao foram cobradas
-- (`nao_enviado`, `falha_envio`).
alter table public.mensagens_disparadas
    add column segmentos smallint check (segmentos > 0);

-- --- custo: envios cobrados por mes e projecao ------------------------------
-- O preco NAO mora aqui: fica em `params.yaml` (bloco `custo`), e o
-- provisionamento do Grafana o copia para o painel e para o alerta, que
-- multiplicam `segmentos` e `segmentos_projetados`. Uma fonte so' de preco.
--
-- Por canal porque o preco e' por canal: `custo.sms_unitario_usd` vale para
-- `sms`; o `stub` (dev e testes) nunca custa e aparece so' para conferir a view
-- em dev. `disparos_sem_segmentos` sao os envios gravados antes desta migration
-- (so' no primeiro mes): ficam fora de `segmentos`, e o painel os mostra para
-- ninguem ler o mes de transicao como completo.
--
-- Projecao: segmentos ate' hoje / dias decorridos x dias do mes. Mes fechado
-- tem todos os dias decorridos, entao a projecao e' o proprio total.
create view observabilidade.envios_cobrados_por_mes as
select
    mes,
    canal,
    disparos,
    segmentos,
    disparos_sem_segmentos,
    dias_decorridos,
    dias_do_mes,
    round(segmentos::numeric / dias_decorridos * dias_do_mes, 2) as segmentos_projetados
from (
    select
        mes,
        canal,
        disparos,
        segmentos,
        disparos_sem_segmentos,
        (mes + interval '1 month')::date - mes as dias_do_mes,
        -- `greatest` so' por defesa: linha com `criado_em` no futuro daria
        -- zero dias e divisao por zero.
        greatest(
            least(
                (now() at time zone 'America/Sao_Paulo')::date,
                (mes + interval '1 month')::date - 1
            ) - mes + 1,
            1
        ) as dias_decorridos
    from (
        select
            date_trunc('month', criado_em at time zone 'America/Sao_Paulo')::date as mes,
            canal,
            count(*) as disparos,
            coalesce(sum(segmentos), 0) as segmentos,
            count(*) filter (where segmentos is null) as disparos_sem_segmentos
        from public.mensagens_disparadas
        where status_envio in ('enviado', 'enviado_sem_predicao')
        group by 1, 2
    ) por_mes
) com_dias;

-- Mesmas permissoes das views da P4. O `grant on all tables` daquela migration
-- so' valeu para as views que ja existiam.
revoke all on observabilidade.envios_cobrados_por_mes
    from public, anon, authenticated, service_role;
grant select on observabilidade.envios_cobrados_por_mes to grafana_leitura;
