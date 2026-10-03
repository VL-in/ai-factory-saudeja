-- SaudeJa - contrato de features: marcar a predicao feita sobre
-- um agendamento que o modelo nao viu no treino.
--
-- O contrato (src/contrato_features.py) tem duas faixas por campo. Fora da
-- regra de negocio (idade 150, consulta as 3h de domingo) a linha vai para
-- quarentena e nao e' predita. Fora do DOMINIO DO TREINO (distancia > 50 km,
-- antecedencia > 90 dias, historico > 10 faltas...) o dado e' plausivel, so'
-- nao foi visto -- por decisao da autora, prediz e MARCA, nunca rejeita. A
-- marca aparece na "Fila do dia": a probabilidade daquela linha e' extrapolacao.
--
-- Nullable e SEM default, de proposito: `null` = "nao verificado", que e' o
-- estado honesto das predicoes gravadas antes desta coluna. Um default
-- `false` afirmaria que elas foram conferidas.
--
-- ADITIVA (migration aditiva pode ir no mesmo deploy do codigo; restritiva
-- so' num deploy posterior): o codigo antigo nao le a coluna e os inserts
-- dele gravam null. Pode ir no mesmo deploy do codigo que a usa, aplicada
-- antes do sync.

alter table predicoes add column fora_do_dominio boolean;
