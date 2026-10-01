# Changelog

Todas as mudanças relevantes deste projeto são registradas aqui.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Registramos o que muda para quem usa o sistema: comportamento visível, correções de
bugs, mudanças de API/interface/esquema de banco e avisos de descontinuação. Detalhe
interno sem efeito observável (testes, lint, refatoração, verificação de release) fica
nos commits; as decisões de arquitetura ficam nos [ADRs](../adr/).

## [v1.13] (Vanessa + Claude) - 2026-09-30

### Adicionado
- **A "Fila do dia" marca a predição feita sobre dados que o modelo não viu no treino** (coluna "Fora do domínio", com aviso no detalhe da linha). Exemplos: distância acima de 50 km, consulta marcada com mais de 90 dias de antecedência, mais de 10 faltas anteriores. A probabilidade dessas linhas é extrapolação e merece menos confiança. Coluna nova `predicoes.fora_do_dominio` (migration `20260930000000_fora_do_dominio.sql`, aditiva); predições anteriores ficam sem a marca ("não verificado"). **Aplicar no projeto remoto com `supabase db push`** (o primeiro deploy automático também o faz): até lá, a "Fila do dia" local apontando para o banco remoto mostra o aviso de banco desatualizado.
- **O job diário passa a rodar sozinho todo dia às 08h17**, pelo GitHub Actions, e não no servidor da aplicação. Antes de enviar qualquer SMS, ele confere se o modelo e o threshold em uso são os aprovados no último re-treino; se não forem, não roda. Se a fila inteira do dia não puder ser predita, o job falha e avisa a equipe.
- **Deploy automático**: cada mudança aprovada em `main` passa pelos testes, aplica as migrations do banco e só então atualiza o Space. Só o modelo aprovado pelo re-treino chega a produção.
- O re-treino mensal informa, no resumo e no pedido de promoção, **quantos pacientes o modelo novo mandaria lembrete pago** — o número que liga o modelo ao custo de mensageria.

### Modificado
- **O job diário recusa agendamentos com dado impossível** (idade fora de 0 a 120, sexo inválido, horário fora do expediente da clínica, campo vazio) em vez de predizer sobre eles. O paciente recebe o lembrete mesmo assim, como já acontecia com os que não podiam ser preditos.
- **O re-treino mensal é bloqueado antes de treinar se o dataset estiver quebrado** (coluna faltando, valor impossível, especialidade que a clínica não oferece, poucas faltas registradas para a comparação com o modelo atual ser confiável). O motivo aparece no resumo do run.
- **O re-treino também bloqueia** um modelo novo com ROC-AUC abaixo de 0,60, mesmo que pareça melhor que o atual, e um modelo que se comporte de forma anormal (mesma probabilidade para todos, explicação que não fecha com a probabilidade).
- O dataset de re-treino passa a usar o **histórico de faltas registrado no momento do agendamento** — o mesmo que o modelo viu ao predizer — em vez de recalculá-lo depois. Agendamentos com dado impossível ficam de fora do dataset.
- As especialidades oferecidas no cadastro passam a vir da configuração (`params.yaml`), não do modelo treinado. A lista não mudou.

### Corrigido
- A aba "Dev: disparo manual" podia mostrar, no motivo de um erro, o dado do paciente que causou o erro (por exemplo, a data de nascimento ilegível). Agora mostra só o campo e a regra.
- O re-treino mensal rodando no GitHub Actions reexecutaria o pipeline inteiro todo mês, mesmo sem nenhum desfecho novo registrado, porque o fim de linha dos arquivos no Windows e no Linux era diferente. Ele passa a reconhecer corretamente o mês sem dado novo.

### Segurança
- O CI, o deploy e o job diário usam uma credencial **só de leitura** para baixar o modelo; a de escrita fica só no re-treino.
- O job diário roda num servidor do GitHub fora do Brasil e o log dele é público: ele registra só contagens e identificadores internos, e a redação de dado pessoal no log passa a ser a última barreira. Registrado em `docs/LGPD.md` §4 e §9.1.
- A aplicação deixou de consultar um serviço externo, na inicialização, para descobrir o próprio IP público, e de imprimi-lo no log.
- Pull requests passam por revisão automática de dependências vulneráveis.

## [v1.12] (Vanessa + Claude) - 2026-09-29

### Corrigido
- **O lembrete por SMS informava o horário da consulta 3 horas adiantado** (ex.: "às 21:00" para uma consulta das 18:00). O banco devolve data e hora em UTC, e o job diário não as convertia para o horário da clínica.
- **A predição do job diário usava esse mesmo horário deslocado.** Uma consulta das 18h era avaliada como se fosse às 21h, horário que o modelo nunca viu no treino: para o mesmo paciente, a probabilidade de falta caía de 0,48 para 0,24. As predições gravadas antes desta versão carregam esse erro.
- **O dataset de re-treino recebia as consultas reais com o horário em UTC.** A exportação passa a gravar o horário da clínica, o mesmo formato do dataset histórico.
- **Um agendamento com dado inválido não interrompe mais a fila do dia.** Antes, alguns tipos de erro abortavam o job e os pacientes seguintes ficavam sem predição.

### Modificado
- O job diário passa a considerar as consultas **de amanhã até daqui a dois dias** que ainda não têm predição, em vez de só as de daqui a dois dias. Um dia em que o job não rodou é recuperado na execução seguinte, e agendamentos feitos com um dia de antecedência também recebem predição.
- **Paciente que não pôde ser predito recebe o lembrete mesmo assim**, como exceção. O envio fica registrado com o status `enviado_sem_predicao`, separado do envio por risco alto.
- O cadastro só aceita consulta **entre hoje e 180 dias à frente**. Antes não havia limite, e dava para agendar no passado.
- O desfecho ("realizada", "faltou", "cancelado") só pode ser registrado **no dia da consulta ou depois**. Na fila de uma data futura, a tela explica isso no lugar dos botões.

### Adicionado
- Coluna `agendamentos.lembrete_enviado` (migration `20260929000000_lembrete_enviado.sql`), que registra se o paciente recebeu lembrete. O re-treino precisa dessa informação para distinguir "compareceu" de "compareceu porque foi lembrado". A coluna também entra no dataset de re-treino, mas ainda não é usada como feature. **Aplicar no projeto remoto com `supabase db push` antes de subir o código.**

## [v1.11] (Vanessa + Claude) - 2026-09-28

### Adicionado
- **Tela de login para a equipe da clínica.** A visão "Funcionário da clínica" passa a pedir e-mail e senha, verificados pelo Supabase Auth do mesmo projeto do banco. Antes do login nenhuma aba é exibida: nem a fila do dia, nem a predição manual, nem a observabilidade. A visão "Paciente" continua aberta ([ADR-008](../adr/adr-008-login-da-equipe.md)).
- A barra lateral mostra com qual e-mail a pessoa está conectada e tem o botão **Sair**.
- `scripts/criar_funcionario.py` cria a conta de um funcionário. A senha é digitada no terminal, sem eco, e a conta nasce com o e-mail confirmado. Para desativar alguém, apague ou bana o usuário no Dashboard do Supabase.

### Modificado
- A sessão do funcionário é encerrada depois de **30 minutos sem interação**. Recarregar a página ou abrir outra aba também pede login de novo, porque a sessão fica na memória do servidor e não em cookie.
- "Sair" e o encerramento por inatividade apagam também a última predição mostrada na aba "Explicabilidade", para que quem entrar depois no mesmo navegador não a herde.

### Segurança
- Fecha a parte técnica do risco que o `LGPD.md` classificava como o mais grave: qualquer pessoa com a URL via a fila do dia, que mostra o nome do paciente. Os riscos que continuam abertos estão listados no `LGPD.md` §9: não há controle por perfil de acesso, a API não tem autenticação e o login da equipe inteira pode ser travado por tentativas em massa vindas de fora.
- Não há cadastro aberto: só o administrador cria contas. **No projeto Supabase remoto é preciso desligar "Allow new users to sign up" no Dashboard**, porque a configuração do repositório vale só para o ambiente local.
- A mensagem de login recusado é a mesma para e-mail inexistente e para senha errada, para não revelar quais e-mails pertencem à equipe.
- O token do Supabase Auth é revogado logo depois de conferir a senha e nunca é guardado. Os dados continuam sendo lidos com a chave do backend, sem alteração nas permissões do banco.

## [v1.10] (Vanessa + Claude) - 2026-09-28

### Adicionado
- A "Fila do dia" mostra o **nome do paciente** na coluna "Paciente", no registro de desfecho e no painel de explicação. Antes mostrava o hash de 64 caracteres, que não permitia chamar ninguém na sala de espera. O identificador interno continua visível em letra miúda, no detalhe da linha.
- O nome completo digitado no cadastro passa a ser gravado (`pacientes.nome_completo`). Cadastros feitos antes desta versão não têm nome — a tela mostra "(cadastro sem nome)" seguido do início do identificador, em vez de inventar um.
- Aviso na aba informando que a tela contém dado pessoal e não deve ficar exposta à sala de espera.

### Modificado
- **O CPF continua nunca sendo gravado.** O que mudou é só o nome; o identificador do paciente no banco segue sendo o hash do CPF.
- A consulta da fila do dia passou a pedir colunas específicas ao banco em vez de todas: o telefone do paciente deixou de ser trazido para a tela, onde não era usado.
- O cadastro recusa nome vazio ou com mais de 120 caracteres antes de gravar.

### Corrigido
- Quando o banco está atrás das migrations do repositório, a interface passa a dizer qual comando aplicar (`supabase db push` no projeto remoto, `supabase db reset` no local) em vez de mostrar o erro cru do PostgREST. Aparecia ao abrir a "Fila do dia" logo depois desta versão, porque a migration do nome precisa ser aplicada também no projeto remoto.

### Segurança
- O nome do paciente é autorizado **apenas** na tela da equipe da clínica. Ele não sai para log, para a tabela de observabilidade, para o dataset de treino (que é armazenado fora do Brasil), para a resposta da API pública, para a mensagem enviada pela Infobip nem para o provedor de LLM previsto. Cada uma dessas sete saídas tem verificação automatizada.
- A fronteira do LLM passou a ser aplicada pela própria interface de explicação: qualquer implementação futura recebe o contexto já sem dado pessoal, sem depender de lembrar de filtrá-lo.
- A verificação de PII no schema passou a autorizar cada exceção por coluna **e** arquivo: `telefone` e `nome_completo` só são aceitos nas migrations que os introduziram. CPF e e-mail seguem proibidos sem exceção.
- Registrado em `docs/LGPD.md` que a superfície de reidentificação aumentou, que a tela da recepção fica legível para quem espera e que a falta de autenticação passou a ser pré-requisito, não dívida: a justificativa para exibir o nome é o termo de confidencialidade de quem vê, e nada no sistema verifica quem está vendo.

## [v1.9] (Vanessa + Claude) - 2026-09-27

### Adicionado
- [`docs/LGPD.md`](../LGPD.md): papéis (a clínica é controladora, a SaúdeJá é operadora), inventário do que é tratado, base legal, retenção, transferência internacional, direitos do titular, incidentes e riscos residuais. Fecha as duas pendências de LGPD que os passos anteriores declararam e adiaram.
- A aplicação passa a emitir log estruturado (uma linha JSON por registro) com redação automática de PII — nome, sobrenome, CPF, e-mail, telefone e IP. Vale também para o log das bibliotecas de terceiros (`uvicorn`, `httpx`, `streamlit`), que é o que existe em volume em produção.
- `scripts/auditoria_lgpd.py` varre arquivos, diretórios ou a entrada padrão em busca de PII e devolve código de saída 1 se achar algo. O relatório mostra a linha e a regra, nunca o valor encontrado.
- `predicoes` e `mensagens_disparadas` passam a ser purgadas após 365 dias, pela mesma execução diária que já limpava `eventos_app`. `pacientes` e `agendamentos` não são purgados: o registro do atendimento é da clínica.
- Novas variáveis de ambiente: `LOG_LEVEL`, `LOG_FORMATO` (`json` ou `texto`) e `RETENCAO_DADOS_DERIVADOS_DIAS`.

### Modificado
- O resumo de fim de execução do job D-2 deixou de ser uma frase em português e passou a ser uma linha JSON com os contadores, incluindo quantos registros cada purga removeu. Quem consumia essa saída por texto precisa ler os campos.
- A mensagem de erro de envio da Infobip mudou de conteúdo: onde antes vinha o corpo inteiro da resposta, agora vem o status HTTP e o código de erro do provedor. É o que aparece em "agendamentos com erro" na aba de dev.
- O compromisso de "nenhuma PII em log" do [SLA §6](../SLA.md) passa a listar explicitamente e-mail, telefone e IP, não só nome e CPF. O [SLO §6](../SLO.md) registra que a verificação é preventiva (filtro no código e teste automatizado), porque o log do ambiente de produção é apagado a cada reinício e não pode ser auditado depois.

### Segurança
- O telefone do paciente deixou de circular no texto de erro de envio. A Infobip ecoa o payload recebido no corpo de erro, e esse corpo ia inteiro para a lista de erros do job e para a tela de dev.
- O endereço IP de quem acessa passa a ser redigido no log de acesso, junto do restante da PII.
- A proibição de PII em log é verificada automaticamente sobre o próprio código: uma varredura falha se qualquer chamada de log em `src/` referenciar campo proibido.

## [v1.8] (Vanessa + Claude) - 2026-09-21

### Adicionado
- A clínica passa a registrar o desfecho de cada consulta na "Fila do dia" (`concluído`/`no_show`/`cancelado`), o que é o que alimenta o re-treino com dado real.
- Re-treino mensal automatizado com gate de promoção: o modelo novo só substitui o que está em produção se `recall_1`, `f1_1` e `roc_auc` não regredirem além da tolerância de `params.yaml` (`gate.tolerancia`). Regressão bloqueia a promoção, mantém o modelo anterior e falha o ciclo; um mês sem desfecho novo registrado também falha, em vez de re-treinar contra o mesmo dado.
- `data/champion_metrics.json` declara o modelo em produção (métricas medidas, `model_version`, threshold e identidade do dataset). É reescrito apenas quando o gate promove.
- `data/consultas-treino.csv` passa a ser o dataset de treino: a semente herdada (380 linhas, intocada) mais os desfechos reais exportados do banco, com `historico_noshow` calculado na data de cada consulta — nunca com informação posterior a ela.
- Novas variáveis de ambiente: `DVC_REMOTE_URL`, `AZURE_STORAGE_CONNECTION_STRING`, `HEALTHCHECKS_RETRAIN_URL`, `CONSULTAS_HISTORICAS_PATH` e `CONSULTAS_TREINO_PATH`.

### Modificado
- O armazenamento de dataset e modelo deixou de ser um diretório local e passou a ser Azure Blob Storage. Quem clonar o repositório precisa configurar o remote (`dvc remote add --local`) e a credencial antes de `dvc pull`/`dvc push`; sem isso as duas operações falham dizendo que o remote não existe.
- As métricas do modelo vigente registradas na documentação estavam erradas: `recall_1` é **0.429** (não 0.522, que vinha de outra configuração), com `f1_1` 0.419 e `roc_auc` 0.643 no threshold 0.6.

### Corrigido
- O `historico_noshow` calculado automaticamente no cadastro dependia de a coluna `status` receber `no_show`, e nada no produto escrevia esse valor desde que a coluna passou a aceitá-lo. Com o registro de desfecho na "Fila do dia", passa a funcionar.

### Segurança
- O export do dataset de treino nunca inclui telefone, nome ou CPF: `id_paciente` é o hash sha256 do CPF, e a restrição é verificada automaticamente.
- O dataset de treino e o modelo passam a ser armazenados fora do Brasil (container em Chile Central), ao contrário do banco de produção, que permanece em São Paulo. O que sai do país é pseudonimizado e sem dado de contato; a base legal dessa transferência é pendência declarada.

## [v1.7] (Vanessa + Claude) - 2026-09-21

### Adicionado
- Aba "Observabilidade" no Streamlit: latência p95, erros, cobertura de explicação e última execução do job, com janela de 24h/7d/30d.
- Tabela `eventos_app` no Supabase registra predições, requisições da API e execuções do job, separadas pela coluna `origem` (`api`/`processo`/`job`). `/health` fica de fora de propósito, para a sonda externa não encher a tabela de ruído.
- Retenção configurável por `OBSERVABILIDADE_RETENCAO_DIAS` (default 90), purgada pelo job diário.

### Modificado
- O registro de eventos é não bloqueante: nunca atrasa nem derruba a predição ou o job. Fila cheia descarta o evento; destino fora do ar pausa as tentativas por 60s.
- A instrumentação revelou o custo real da primeira predição de cada processo: ~1,4s (montagem do explicador SHAP) contra ~10ms nas seguintes — dentro do SLO §2 (<2s), mas apertado num ambiente que hiberna.

### Corrigido
- A suíte de UI apagava as credenciais do Supabase, mas `load_dotenv()` as redefinia no import: quem tivesse `.env` local rodava os testes contra o projeto Supabase real.

### Segurança
- `eventos_app` nunca recebe PII: as chaves de `detalhe` são restritas a uma allowlist fechada e exceções registram só o nome da classe, nunca a mensagem (que ecoaria o telefone enviado à Infobip).

## [v1.6] (Vanessa + Claude) - 2026-09-21

Versão só de documentação, sem mudança de comportamento: o [ADR-006](../adr/adr-006-observabilidade.md) decide a observabilidade de aplicação (tabela no Supabase como fonte de verdade, aba no Streamlit, sonda de uptime e dead-man's-switch externos), descarta Langfuse/OpenTelemetry/Grafana para o núcleo e adia o Sentry. Implementado na v1.7.

## [v1.5] (Vanessa + Claude) - 2026-09-20

### Adicionado
- Envio real de lembretes por SMS via Infobip, ligado por `MESSAGING_PROVIDER=infobip` (`stub`, que nunca toca a rede, continua o default). WhatsApp Business não foi implementado: exige sender/template pré-aprovados pela Meta.
- Campo de telefone no formulário de cadastro, normalizado para o formato internacional e validado antes de gravar.

### Modificado
- `MessagingClient.enviar_lembrete` passou a receber `telefone` em vez de `id_paciente_externo`.
- `mensagens_disparadas.canal` grava o canal real (`stub`/`sms`) no lugar do `"whatsapp"` fixo.
- Nova coluna `pacientes.telefone` (migration `20260920020000_telefone_paciente.sql`): exceção deliberada à minimização de PII, porque sem contato não há para onde mandar o lembrete. Nome, CPF e e-mail continuam proibidos no banco.

### Corrigido
- Falha no envio de um paciente (Infobip fora do ar, número rejeitado, credencial inválida) vira `status_envio="falha_envio"` e não derruba o resto da fila do dia.

## [v1.4] (Vanessa + Claude) - 2026-09-20

### Adicionado
- Cadastro do paciente pede nome completo, CPF (com validação de dígito verificador) e data de nascimento.
- O horário da consulta é escolhido na grade real da clínica (seg-sex 08h-11h30/13h-18h, sáb 08h-11h30, fechado aos domingos), em vez de um campo de hora livre.
- `historico_noshow` passou a ser contado automaticamente pelos agendamentos com status `no_show`.

### Modificado
- `pacientes.idade` foi substituída por `pacientes.data_nascimento` (migration `20260920010000_cadastro_pacientes.sql`); a idade é calculada sob demanda, em relação à data da própria consulta. O contrato de `construir_features` não mudou.
- `id_paciente_externo` passou a ser o hash sha256 do CPF, gerado automaticamente.
- `agendamentos.status` aceita o valor `no_show`.

### Removido
- Campos de "identificador", idade e histórico de no-show autodeclarado do formulário de cadastro.

### Segurança
- Nome, CPF e data de nascimento não são persistidos em texto puro; o nome só aparece na confirmação em tela.

## [v1.3] (Vanessa + Claude) - 2026-09-20

### Adicionado
- Job de inferência diária D-2 (`src/jobs/inferencia_diaria.py`): busca os agendamentos D+2 ainda sem predição, grava predição e explicação SHAP e decide o disparo do lembrete conforme o threshold de `params.yaml`. Agendamento malformado é registrado e pulado, sem derrubar a fila do dia.
- Interface de mensageria (`MessagingClient`), com stub como default que nunca faz rede.
- Auditoria de disparo em `mensagens_disparadas`, incluindo quem não recebeu lembrete (SLA §6).
- A aba "Dev: disparo manual" deixou de ser placeholder e executa o job, mostrando agendamentos encontrados, predições gravadas, mensagens disparadas e erros.

### Modificado
- Índice em `agendamentos.id_paciente` (migration `20260920000000_idx_agendamentos_id_paciente.sql`): a fila do dia e o job não forçam mais sequential scan a cada execução.

## [v1.2] (Vanessa + Claude) - 2026-09-19

### Adicionado
- Fila do dia com resumo (total, alto risco, sem predição), probabilidade em barra e, ao selecionar a linha, a explicação SHAP gravada pelo job D-2.
- Status do Supabase na sidebar, como aviso: sem banco, predição manual e explicabilidade continuam funcionando.

### Modificado
- `dias_entre_agendamento_consulta` deixou de ser perguntado no cadastro e passou a ser derivado da data escolhida. O formulário de teste do funcionário mantém o campo manual.
- Predição sem explicação aparece como aviso explícito (violação do SLO §4) em vez de tabela vazia.

### Corrigido
- Fuso horário: a fila do dia e o job D-2 filtravam a data civil em UTC, então a "fila de 19/09" cobria das 21h de 18/09 às 21h de 19/09 no horário local — o job pularia agendamentos do fim do dia. O fuso da clínica passa a ser configurável por `TIMEZONE_CLINICA` (default `America/Sao_Paulo`).
- O cadastro gravava o horário sem fuso e a fila o exibia em UTC (uma consulta das 08:30 aparecia como 11:30).

## [v1.1] (Vanessa + Claude) - 2026-09-19

### Adicionado
- Persistência no Supabase (`sa-east-1`, sem transferência internacional de dados): tabelas `pacientes`, `agendamentos`, `predicoes` e `mensagens_disparadas`, com RLS habilitado e sem policies — só a chave secreta do backend acessa.
- Fila do dia e visão Paciente passaram a ler e gravar de verdade; falha de configuração ou conexão vira mensagem amigável (`ErroPersistencia`) em vez de traceback.
- `SUPABASE_URL`/`SUPABASE_SECRET_KEY` lidas do `.env` (o formato novo de chave do Supabase, `sb_secret_...`, substitui o JWT `service_role`), sem sobrescrever o que já estiver definido no ambiente.

### Segurança
- O esquema não tem coluna de nome, CPF, e-mail ou telefone: o paciente é identificado só por `id_paciente_externo` mais demografia não identificável.

## [v1.0] (Vanessa + Claude) - 2026-09-19

### Adicionado
- Interface Streamlit: visão Funcionário com as abas "Testar predição", "Explicabilidade", "Fila do dia" e "Dev: disparo manual" (renderizada só com `APP_ENV=dev`), e visão Paciente.
- `PREDICT_BACKEND` escolhe entre chamar o modelo em processo (default de produção) ou a API por HTTP (`API_BASE_URL`), modo de desenvolvimento e diagnóstico. Valor desconhecido falha alto, sem cair em default silencioso.
- Imagem combinada de deploy (`infra/deploy/dockerfile`) rodando API e UI no mesmo container, com o modelo empacotado na imagem; serviço `app` no `docker-compose.yml` (UI em 7860, API em 8000, healthcheck em `/health`). Build sem `dvc pull` falha de propósito, em vez de gerar container sem modelo.
- Novo `requirements/ui.txt`, separado de `api.txt` para a imagem da API continuar enxuta (cold start, SLO §2).

### Modificado
- Dado recusado e backend fora do ar viraram erros distintos na tela (`ErroValidacao`, `ErroIndisponivel`), com mensagem acionável — inclusive para especialidade desconhecida e sexo inválido.
- O cálculo de `model_version` saiu da API para `inference.calcular_model_version()`, para API, UI e a coluna `model_version` produzirem a mesma string para o mesmo artefato. Resultado inalterado (sha256, 12 caracteres).

## [v0.17] (Vanessa + Claude) - 2026-09-19

### Corrigido
- `params.yaml` e os caminhos de `data/` passaram a ser resolvidos a partir da raiz do repositório, não do diretório de execução: a API iniciada fora da raiz quebrava no import.
- `explain.explicar()` exige uma única linha e ganhou `explicar_lote()`. Antes devolvia em silêncio a explicação da linha 0, o que gravaria a explicação do paciente errado na fila do job D-2 (SLO §4).
- `preprocessar()` e `extrair_features_temporais()` não mutam mais o DataFrame recebido.
- `model_version` passou a usar sha256 no lugar de md5, que derruba o startup em host FIPS.

## [v0.16] (Vanessa + Claude) - 2026-09-19

### Adicionado
- API FastAPI com `/health` (status + `model_version`) e `/predict`, validando a entrada com Pydantic e reusando `src/inference.py`/`src/explain.py`. Modelo, mapa de especialidade e explicador SHAP são carregados uma vez no startup, não por requisição (SLO de latência p95 < 2s). Especialidade desconhecida devolve 422, nunca 500 nem predição sobre NaN.
- `requirements.txt` dividido em `requirements/base.txt`, `train.txt` e `api.txt`, e novo `infra/api/dockerfile`, que instala só as dependências da API e monta `data/` por volume.

### Corrigido
- `src/validate.py`: com uma única classe em `y_test`, o `roc_auc_score` do scikit-learn 1.5.2 derrubava o stage `validate` inteiro. Agora `roc_auc`/`pr_auc` caem para 0.0 e a run fica marcada com `aviso_split`, como já acontecia com as demais métricas.

## [v0.15] (Vanessa + Claude) - 2026-09-19

### Adicionado
- Explicabilidade por SHAP (`src/explain.py`): contribuições de cada feature por predição, ordenadas por impacto absoluto e prontas para serializar. O cálculo é síncrono dentro da inferência, o que garante cobertura de 100% das predições por construção (SLO §4).
- Plug inativo para explicação em linguagem natural por LLM: a implementação default retorna `None` e nunca abre rede.
- Dependência `shap`.

## [v0.14] (Vanessa + Claude) - 2026-09-19

### Adicionado
- `src/inference.py`, módulo de inferência reusável pela API e pelo job diário, sem duplicar lógica nem re-treinar. Aplica o mapa de especialidade fixado no treino em vez de recalculá-lo — recalcular, em produção com uma linha por vez, mapearia toda especialidade para `0`.
- `EspecialidadeDesconhecidaError` substitui, no caminho de inferência, o NaN silencioso gerado pelo pré-processamento.

## [v0.13] (Vanessa + Claude) - 2026-09-18

### Removido
- `infra/ML/dockerfile`: `COPY` auto-referencial sobre base divergente da imagem real do pipeline, não referenciado por `dvc.yaml` nem pelo `docker-compose.yml`. Resíduo do protótipo herdado.

### Modificado
- Documentação: ADR-001 marcado como parcialmente substituído pelo ADR-004 (banco, mensageria, gateway de LLM e plataforma de deploy); novo ADR-005 registra o scheduler D-2 via GitHub Actions e a chamada do modelo em processo; `architecture.md` completado, com o gap de recall (0,522 contra o alvo 0,75 do SLO) registrado como débito conhecido.

## [v0.12] (Vanessa + Claude) - 2026-09-16

### Adicionado
- `src/tune.py`: busca de hiperparâmetros por GridSearchCV sobre o Pipeline SMOTENC+LightGBM, com SMOTENC recalculado a cada fold para não vazar sintéticos entre treino e validação. Script exploratório, fora do `dvc.yaml`.
- Documentação: ADR-004 propõe a stack de produto e o `PLANO-IMPLEMENTACAO.md` detalha os passos até o produto deployável.

### Modificado
- Os hiperparâmetros de `params.yaml` não mudaram: os encontrados pelo GridSearch saíram piores no fold de teste isolado (f1_1 0,372 contra 0,419). O dataset atual (~380 linhas) é pequeno demais para o tuning fino generalizar.

## [v0.11] (Vanessa) - 2026-09-16

### Adicionado
- Threshold de decisão como parâmetro rastreável.

## [v0.10] (Vanessa + Claude) - 2026-09-15

### Modificado
- Pipeline segregado em três stages DVC: `preprocess` (carrega o CSV, aplica o feature engineering e faz o split estratificado antes de qualquer balanceamento), `train` (aplica SMOTE-NC só no fold de treino, abre a run no MLflow e grava o `run_id`) e `validate` (calcula as métricas sobre o fold de teste, nunca balanceado, reabrindo a mesma run).

## [v0.9] (Vanessa + Claude) - 2026-09-15

### Adicionado
- Coluna `data_hora_agendada` no dataset, gerada por `scripts/gerar_timestamp_sintetico.py` respeitando a grade real da clínica (seg-sex 08h-11h30/13h-18h, sábado 08h-11h30, sem domingo).
- Features temporais `dia_de_semana` e `horario` (`src/features.py`), compartilhadas entre o pipeline de treino e a inferência, sob o parâmetro `features.temporais`.

### Modificado
- `features.temporais` promovido a `true` como padrão, por ganho medido nas métricas de negócio: f1_1 0,356 → 0,409, recall_1 0,381 → 0,429, pr_auc 0,368 → 0,397.
- As categóricas passaram a ser declaradas tanto ao SMOTENC quanto ao LightGBM, evitando interpolação de valores fracionários que não existem no calendário (ex.: `dia_de_semana=2.7`).

## [v0.8] (Vanessa) - 2026-09-08

### Adicionado
- Hiperparâmetros extraídos para `params.yaml`.
- Modelo registrado e promovido no MLflow.

### Corrigido
- O MLflow não gravava as runs; passou a usar servidor remoto. `mlflow ui` não é mais necessário localmente — acesse http://localhost:5000 direto no navegador.
- Classe positiva ausente do fold de teste: a métrica 0.0 continua sendo logada (para não quebrar o pipeline), mas marcada com a tag `aviso_split` em vez de se misturar silenciosamente com métricas reais.

## [v0.7] (Vanessa) - 2026-09-07

Versão só de testes, sem mudança de comportamento: suíte pytest cobrindo o treino, a coerência entre `.gitignore`/git/`dvc.yaml`/`dvc.lock` e o pipeline completo via Docker.

## [v0.6] (Vanessa) - 2026-09-07 - feat
- Implementação do DVC e MLFlow para rastreabilidade do conjunto de dados e treinamento.

## [v0.5] (Camila)
- Notebook completo com EDA + treino LightGBM
- Modelo serializado em model.pkl
- Acurácia 78%, F1 da classe positiva 0.65

## [v0.4] (Camila)
- Adicionada feature distancia_km

## [v0.3] (Camila)
- Primeira versão LightGBM substituindo regressão logística
