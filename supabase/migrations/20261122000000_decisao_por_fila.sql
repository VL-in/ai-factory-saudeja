-- SaudeJa - regra de decisao por orcamento (top-K da fila do dia).
--
-- O job D-2 passou a decidir o lembrete pago sobre a fila inteira de cada
-- braco (src/decisao.py): no modo `top_fracao`, avisa os K de maior
-- probabilidade, com K = ceil(fracao x fila). `classe_prevista` continua sendo
-- "recebeu lembrete por risco" e `threshold_usado` passa a ser o corte que a
-- fila produziu. Para auditar quem ficou dentro do orcamento, cada predicao
-- grava a posicao dela na fila ordenada por risco e a regra que decidiu.
--
-- Nullable e SEM default, de proposito (mesmo criterio de fora_do_dominio):
-- `null` = predicao anterior a estas colunas. Um default afirmaria uma regra
-- que nao foi aplicada.
--
-- ADITIVA: o codigo antigo nao le as colunas e os inserts dele gravam null.
-- Pode ir no mesmo deploy do codigo que as usa, aplicada antes do sync.

alter table predicoes
    add column posicao_na_fila integer check (posicao_na_fila >= 1),
    add column modo_decisao text check (modo_decisao in ('threshold', 'top_fracao'));
