-- SaudeJa - nome do paciente, para a "Fila do dia" ser operavel por quem
-- atende no balcao (decisao da autora, 2026-09-28; racional completo no
-- ADR-007).
--
-- ISTO REVERTE UMA DECISAO DOCUMENTADA, e o comentario existe para isso nao
-- passar como "mais uma coluna". A migration
-- 20260920010000_cadastro_pacientes.sql afirma que "o CPF em si (e o nome
-- completo, tambem coletado na tela) NUNCA sao gravados aqui". Continua
-- verdadeiro para o CPF; deixa de ser para o nome.
--
-- Por que a reversao se sustenta: minimizacao de PII (architecture.md Sec4.1)
-- existe para nao guardar o que o produto nao precisa. A coluna "Paciente" da
-- fila mostrava o hash sha256 de 64 caracteres -- inutil para o funcionario,
-- que precisa chamar a pessoa pelo nome. A partir do momento em que a fila e'
-- operada por uma pessoa, o nome passa a ser dado NECESSARIO, exatamente como
-- o telefone passou a ser no Passo 7 (20260920020000_telefone_paciente.sql).
-- O funcionario e' preposto da clinica, que e' a CONTROLADORA (docs/LGPD.md
-- Sec1) e ja' detem o prontuario: a base legal nao muda (Art. 11, II, "f").
--
-- CPF SEGUE NUNCA GRAVADO. id_paciente_externo continua sendo o hash sha256
-- dele (src/ui/logic.py::_id_paciente_externo_de_cpf), e a guarda de
-- tests/test_coerencia_repo.py segue proibindo cpf/email em qualquer
-- migration -- ela passou a permitir esta coluna, e SO ela, e SO neste
-- arquivo.
--
-- O que impede o nome de sair daqui (cada item com teste travando, ADR-007):
--   - log de aplicacao      -> `nome` e' chave proibida no filtro de
--                              src/logging_config.py e na guarda de AST
--   - eventos_app           -> allowlist fechada de `detalhe` (ADR-006)
--   - export de treino      -> COLUNAS_SAIDA de src/export_treino.py
--   - resposta da API       -> nao esta' em src/api/schemas.py
--   - LLM (Passo 13)        -> src/explain.py sanitiza o `contexto` do prompt
--   - SMS via Infobip       -> select do job D-2 nao traz a coluna
--
-- NULLABLE de proposito, ao contrario do telefone. A migration do telefone
-- fez backfill com digitos aleatorios; aqui backfill seria FABRICAR nome de
-- paciente, o que e' pior que nao ter o dado. As linhas cadastradas antes
-- desta migration realmente nao tem nome para recuperar -- ele nunca foi
-- gravado --, e a interface mostra o inicio do hash nesses casos, dizendo que
-- e' cadastro antigo em vez de inventar uma pessoa.

alter table pacientes add column nome_completo text;

-- Limite superior para a coluna nao virar campo de texto livre arbitrario, e
-- inferior para nao aceitar " " como nome. Validacao de forma continua em
-- src/ui/logic.py, nao no banco -- mesma divisao do telefone.
alter table pacientes add constraint pacientes_nome_completo_check
    check (nome_completo is null or char_length(btrim(nome_completo)) between 2 and 120);
