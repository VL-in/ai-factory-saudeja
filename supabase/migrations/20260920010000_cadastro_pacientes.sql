-- SaudeJa - cadastro do paciente mais fiel a uma clinica de verdade.
--
-- Idade -> data de nascimento: `idade` guardada isolada ficaria desatualizada
-- com o tempo (uma linha nunca mais tocada faria o paciente "nao envelhecer");
-- pacientes agora guarda a data de nascimento, e a idade continua sendo
-- calculada em codigo (src/features.py::calcular_idade), na hora da predicao,
-- nunca persistida como coluna.
--
-- Identificador automatico: id_paciente_externo deixa de ser texto livre
-- digitado pelo paciente na tela -- passa a ser gerado automaticamente a
-- partir do CPF (hash sha256, ver src/ui/logic.py). O CPF em si (e o nome
-- completo, tambem coletado na tela) NUNCA sao gravados aqui -- minimizacao
-- de PII por design (architecture.md Sec4.1) continua valendo: guarda
-- automatizada em
-- tests/test_coerencia_repo.py::test_migrations_sql_sem_coluna_proibida_de_pii,
-- que segue proibindo qualquer coluna de nome/cpf/email/telefone.
--
-- historico_noshow automatico: agendamentos ganha o status 'no_show' para
-- que o proximo cadastro do MESMO paciente conte os no-shows passados de
-- verdade (repositories.contar_no_shows_anteriores) em vez de pedir ao
-- proprio paciente para se autodeclarar faltoso na tela de cadastro.

alter table pacientes add column data_nascimento date;

-- Backfill para linhas ja existentes (dados sinteticos de teste ate aqui, Passo
-- 5) a partir da idade antiga -- aproximado (anos completos contados de hoje),
-- mas evita destruir o dado sem alternativa: idade em si ja era so uma
-- aproximacao tambem. Sem backfill, o "not null" abaixo falharia contra
-- qualquer linha ja cadastrada.
update pacientes set data_nascimento = (current_date - (idade || ' years')::interval)::date
where data_nascimento is null;

alter table pacientes alter column data_nascimento set not null;
alter table pacientes add constraint pacientes_data_nascimento_check check (data_nascimento <= current_date);

alter table pacientes drop column idade;

alter table agendamentos drop constraint agendamentos_status_check;
alter table agendamentos add constraint agendamentos_status_check
    check (status in ('agendado', 'concluido', 'cancelado', 'no_show'));
