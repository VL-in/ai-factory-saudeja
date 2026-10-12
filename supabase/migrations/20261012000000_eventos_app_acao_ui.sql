-- SaudeJa - eventos_app passa a medir as acoes da interface (Passo 8.9 do
-- plano de implementacao, P2).
--
-- tipo 'acao_ui': cada acao do funcionario ou do paciente na UI (carregar a
-- fila, registrar desfecho, cadastro, pedidos do titular, login) vira um
-- evento com latencia e desfecho. Ate aqui so' a predicao e o job eram
-- medidos, e uma falha de banco na UI virava mensagem na tela sem rastro.
-- `detalhe.acao` diz qual acao (allowlist de src/observabilidade.py).
--
-- status 'recusado': a acao terminou numa recusa esperada (senha errada,
-- formulario invalido, desfecho fora do prazo). Nao e' falha do sistema e nao
-- pode entrar na taxa de erro que alerta a mantenedora.
--
-- A aba "Observabilidade" e o alerta diario leem so' os tipos do SLO
-- (predicao, job_d2, erro; observabilidade.TIPOS_DO_SLO): 'acao_ui' sai a cada
-- interacao e ocuparia o limite de linhas da janela.
--
-- ADITIVA: so' alarga os dois `check`. O codigo antigo nunca grava os valores
-- novos, entao pode ir no mesmo deploy do codigo que os usa, aplicada antes do
-- sync.

alter table eventos_app drop constraint eventos_app_tipo_check;
alter table eventos_app add constraint eventos_app_tipo_check
    check (tipo in ('predicao', 'job_d2', 'erro', 'acao_ui'));

alter table eventos_app drop constraint eventos_app_status_check;
alter table eventos_app add constraint eventos_app_status_check
    check (status in ('ok', 'erro', 'recusado'));
