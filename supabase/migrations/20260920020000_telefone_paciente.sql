-- SaudeJa - Passo 7: telefone do paciente, para o lembrete real (Infobip)
-- ter para onde mandar mensagem.
--
-- Minimizacao de PII por design (architecture.md Sec4.1) segue valendo para
-- nome/CPF/email -- nunca gravados (ver
-- tests/test_coerencia_repo.py::test_migrations_sql_sem_coluna_proibida_de_pii,
-- que agora permite so esta coluna adicional). Telefone e' a excecao
-- deliberada: sem um contato de envio, o produto inteiro (disparo de
-- lembrete pago para reduzir no-show) nao existe -- e' dado indispensavel,
-- nao acessorio, diferente de nome/CPF (usados so para identificar/exibir).
--
-- Guardado como digitos normalizados com codigo do pais (formato que a API
-- de SMS da Infobip espera, ver src/messaging/client.py::InfobipClient),
-- nao formatado -- normalizacao/validacao continuam vivendo em
-- src/ui/logic.py, nao no banco.

alter table pacientes add column telefone text;

update pacientes set telefone = '55119' || lpad((random() * 100000000)::int::text, 8, '0')
where telefone is null;

alter table pacientes alter column telefone set not null;
alter table pacientes add constraint pacientes_telefone_check check (telefone ~ '^[0-9]{12,13}$');
