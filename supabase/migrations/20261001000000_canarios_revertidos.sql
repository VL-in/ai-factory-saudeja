-- SaudeJa - canario do modelo com rollback automatico (ADR-008).
--
-- Estado OPERACIONAL do rollback: um canario listado aqui deixa de receber
-- trafego na proxima execucao do job D-2, mesmo que `data/canario.json` ainda
-- esteja em `main`. Existe porque o job roda com `contents: read` e nao pode
-- tirar o canario do git sozinho -- o PR de reversao (canario.yml) e' o
-- registro FORMAL, este e' o que age ja'. Sem ele, entre a violacao e o merge
-- do PR, o canario seguiria decidindo uma fracao da fila.
--
-- Quem grava: src/canario.py, chamado pelo job (guardrail violado) e pelo
-- canario.yml (avaliacao diaria ou reversao manual). Nunca apagado pelo
-- codigo: e' tambem a trilha de auditoria dos rollbacks em producao, que
-- sobrevive aos 90 dias dos artifacts do Actions.
--
-- SEM NENHUMA COLUNA DE PII: `model_version` e' o sha do artefato e `motivo`
-- e' texto montado pelo codigo a partir de contagens agregadas.
--
-- ADITIVA (migration aditiva pode ir no mesmo deploy do codigo; restritiva
-- so' num deploy posterior): tabela nova, que o codigo anterior nao le.
-- Pode ir no mesmo deploy do codigo que a usa, aplicada antes do sync.

create table canarios_revertidos (
    model_version text primary key,
    motivo text not null,
    revertido_em timestamptz not null default now()
);

-- Mesmo padrao das demais tabelas: RLS habilitado e sem policies -- so' a
-- chave secreta do backend (job, canario.yml) le e grava.
alter table canarios_revertidos enable row level security;
