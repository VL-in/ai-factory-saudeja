# Changelog

Todas as mudanças relevantes deste projeto são registradas aqui.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Registramos o que muda para quem usa o sistema: comportamento visível, correções de
bugs, mudanças de API/interface/esquema de banco e avisos de descontinuação. Detalhe
interno sem efeito observável (testes, lint, refatoração, verificação de release) fica
nos commits; as decisões de arquitetura ficam nos [ADRs](../adr/).

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
