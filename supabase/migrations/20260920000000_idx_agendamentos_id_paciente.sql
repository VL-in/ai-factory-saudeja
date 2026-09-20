-- SaudeJa - Passo 6: indice para o embed pacientes(...) em
-- buscar_agendamentos_d2_pendentes (src/db/repositories.py).
--
-- Postgres nao indexa colunas de FK automaticamente. agendamentos.id_paciente
-- ja era usada em joins (buscar_fila_do_dia), mas o Passo 6 passou a rodar
-- esse embed diariamente sobre a fila D-2 -- sem indice, cada execucao do
-- job faria sequential scan em agendamentos para casar com pacientes.

create index idx_agendamentos_id_paciente on agendamentos (id_paciente);
