# Changelog

## [v0.14] (Vanessa + Claude) - 2026-09-19

- (feat) novo `src/inference.py`, módulo de inferência reusável para API e job diário importarem sem duplicar nem re-treinar. `carregar_modelo()` lê o artefato salvo por `train.py` (modelo + mapa de especialidade); `aplicar_mapa_especialidade()` **aplica** esse mapa já fixado em vez de recalculá-lo -- crítico porque `preprocess.preprocessar()` ajusta um mapa novo a cada chamada, o que em produção (uma linha por vez) sempre mapearia a especialidade recebida para `0`; `construir_features()` monta 1 linha com exatamente as colunas/ordem que `preprocessar()` produz, lendo `features.temporais` de `params.yaml` por padrão; `predizer()` retorna a probabilidade da classe positiva. `EspecialidadeDesconhecidaError` substitui, no caminho de inferência, o NaN silencioso que `preprocess.py` documenta será corrigido mais adiante. `preprocess.py`/`train.py`/`validate.py` não foram alterados.
- (test) `tests/test_inference.py`: teste de paridade treino-serving (`construir_features()` vs. `preprocess.preprocessar()` para a mesma linha, com e sem features temporais) -- detecta training-serving skew automaticamente; teste de exceção para especialidade fora do mapa; teste ponta a ponta carregando o `data/model.pkl` real (já versionado via DVC) sem re-treinar. `pytest tests/test_inference.py -v` e a suíte completa (`pytest -v`, exceto `integracao`) verdes.

## [v0.13] (Vanessa + Claude) - 2026-09-18

- (docs) Reconciliação entre ADR-001 e ADR-004. `adr-001-stack.md` marcado como parcialmente substituído por ADR-004 nos pontos de banco de dados, mensageria, gateway de LLM e plataforma de deploy -- mantém válido o que não foi contestado (Python-first, GitHub+DVC+MLflow, Streamlit, FastAPI).
- (docs) `adr-004-decisão-técnica.md`: `Status` → Aceito; registrada decisão de observabilidade -- MLflow segue cobrindo o pipeline de ML, Langfuse fica reservado só para tracing do LLM opcional.
- (docs) Novo `adr-005-integracoes-implicitas.md`: registra scheduler D-2 via GitHub Actions cron (HF Space free pode hibernar) e a decisão de Streamlit/job chamarem o modelo em processo (`src/inference.py`) enquanto a API FastAPI fica exposta via REST para integrações externas.
- (docs) `architecture.md`: preenchidas as seções §3 a §11 (removidos os placeholders `[e.g., ...]` do template), estrutura do projeto atualizada para `infra/api/`/`infra/deploy/` no lugar do `infra/ML/` morto, e o gap de recall (0.522 vs. alvo 0.75 do SLO) registrado como debt conhecido em §9.
- (fix) Removido `infra/ML/dockerfile`: `COPY` auto-referencial (copiava o próprio dockerfile em vez do código) sobre base `python:3.9-slim` divergente da imagem real do pipeline; não era referenciado por `dvc.yaml`/`docker-compose.yml`, resíduo do protótipo herdado.
- (test) `tests/test_coerencia_repo.py`: novo `test_architecture_md_sem_placeholder_generico`, que falha se `docs/architecture.md` voltar a conter o padrão `[e.g.,` -- trava regressão de placeholder não preenchido.

## [v0.12] (Vanessa + Claude) - 2026-09-16

- (feat) `src/tune.py` criado: GridSearchCV sobre Pipeline SMOTENC+LightGBM, com CV estratificado (5 folds) restrito ao fold de treino isolado por `preprocess.py` (SMOTENC dentro do Pipeline, recalculado a cada fold, para não vazar sintéticos entre treino/validação da CV). Scoring multi-métrica (`f1_1`/`recall_1`/`pr_auc`), refit em `f1_1` (métrica de interesse do projeto, ADR-003). Não é stage do `dvc.yaml` -- script exploratório, mesmo padrão de `scripts/gerar_timestamp_sintetico.py`.
- (test) `tests/test_tune.py`: mecânica do GridSearch (best_params_/cv_results_ preenchidos, isolamento do fold de teste) e teste ponta-a-ponta de `main()` logando no MLflow.
- (resultado) Comparação no fold de teste isolado: os hiperparâmetros encontrados pelo GridSearch (`learning_rate=0.05, max_depth=4, num_leaves=16`) tiveram `f1_1=0.372`, pior que os hiperparâmetros já em `params.yaml` (`f1_1=0.419`). `model.*` **não foi alterado** -- dataset atual (~380 linhas, fold de teste de ~76) é pequeno demais para o tuning fino generalizar de forma confiável do CV para o holdout. Ver nota em `README.md`.
- (docs) Adiciona ADR-004 propondo Streamlit + Supabase + TrueFoundry (LLM) + FastAPI + Infobip + Hugging Face Space como stack de produto, com alternativas descartadas.
- (docs) Adiciona PLANO-IMPLEMENTACAO.md detalhando os passos verticais para evoluir do pipeline de ML atual até o produto deployável (API, banco, interface, mensageria, LGPD, CI/CD).


## [v0.11] (Vanessa) - 2026-09-16

- (feat) Inclusão de threshold de decisão como parametro rastravel.

## [v0.10] (Vanessa + Claude) - 2026-09-15

- (feat) Pipeline segregado em 3 stages DVC:
  - reprocess (src/preprocess.py) — carrega o CSV, aplica o feature engineering e faz o split treino/teste estratificado antes de qualquer balanceamento. Não fala com o MLflow. Gera data/interim/{train_raw,test}.pkl + mapa_especialidade.json.
  - train (src/train.py, refatorado) — aplica SMOTE-NC só no fold de treino recebido do preprocess, treina o LightGBM, abre a run no MLflow remoto, loga hiperparâmetros/modelo e a tag pipeline_arquitetura=segregado-preprocess-train-validate, e grava o run_id em data/interim/mlflow_run_id.txt.
  - validate (src/validate.py) — carrega modelo + fold de teste isolado (nunca balanceado), calcula as métricas e reabre a mesma run do MLflow
- (test) dividsão de tests/test_train.py em test_preprocess.py (+ novos testes de split: sem sobreposição treino/teste, estratificação preservada, fold de teste não-balanceado), test_train.py (agora testa treinar() isolado, sem split embutido) e test_validate.py (métricas, tag aviso_split, e um teste ponta-a-ponta que confirma que treino e validação escrevem na mesma run do MLflow). 

## [v0.9] (Vanessa + Claude) - 2026-09-15
- (feat) scripts/gerar_timestamp_sintetico.py criado para gerar data_hora_agendada (380 linhas, formato "YYYY-MM-DD HH:MM:SS"), respeitando a grade real de agendamento da clínica (seg-sex 08h-11h30/13h-18h, sábado 08h-11h30, sem domingo). Reforço da hipótese da Camila (sexta no fim do expediente) calibrado em 15% das linhas com no_show=1 (26 registros em sexta 17h-18h, taxa de no-show 61,5% vs. 24,9% no resto do dataset).
- (feat) src/features.py criado com extrair_features_temporais(), compartilhada entre o pipeline de treino e a futura API de inferência.
- (feat) src/train.py: import da função; preprocessar() agora inclui dia_de_semana/horario condicionalmente a PARAMS["features"]["temporais"]; treinar() calcula cat_cols dinamicamente para declarar as categóricas tanto no SMOTENC (categorical_features) quanto no LGBMClassifier.fit(categorical_feature=...), evitando interpolação de valores fracionários que não existem no calendário (ex. dia_de_semana=2.7).
- (feat) params.yaml: novo bloco features.temporais (dvc.yaml atualizado com o novo param e com src/features.py como dependência do stage train).
- (test) fixture df_consultas com data_hora_agendada; tests/test_features.py novo (valores exatos, minutos descartados de propósito, e validação da grade real de negócio contra o CSV); teste novo test_preprocessar_inclui_features_temporais_quando_flag_ativa para o caminho com a flag ligada — sem alterar o teste existente que cobre o comportamento padrão.
- (feat) comparação via `dvc exp run -S features.temporais=false` vs. `=true` confirmou ganho nas métricas de negócio (f1_1: 0,356 → 0,409; recall_1: 0,381 → 0,429; pr_auc: 0,368 → 0,397) — features.temporais promovido a `true` como padrão do repositório.

## [v0.8] (Vanessa) - 2026-09-08
  - (feat) Extração dos hiperparametros em params.yaml
  - (feat) se a classe positiva ficar ausente do fold de teste, o código agora chama mlflow.set_tag("aviso_split", ...) e imprime um aviso — o 0.0 continua sendo logado (pra não quebrar o pipeline), mas fica marcado como não confiável em vez de se misturar silenciosamente com métricas reais.
  - (fix) o MLFlow não estava gravando as runs feitas, foi necessário configurar um servidor remoto no MLFlow. Mlflow ui não é mais necessário localmente — acesse http://localhost:5000 diretamente no navegador; é o próprio servidor MLflow servindo a UI.
  - (feat) Modelo (run: http://localhost:5000/#/experiments/1/runs/6c2f69fbae3e4a69b73861059f1067c7) registrado e promovido.

## [v0.7] (Vanessa) - 2026-09-07 - test
  - Adicionada suíte de testes automatizados (pytest):
  - `test_train.py`: testes unitários de `carregar_dados`, `preprocessar` e `treinar`, incluindo teste que documenta o risco de especialidade desconhecida virar NaN silencioso.
  - `test_coerencia_repo.py`: valida consistência entre `.gitignore`, arquivos rastreados pelo git, `dvc.yaml` e `dvc.lock`.
  - `test_pipeline_dvc_integracao.py`: teste de integração opcional (pulado se Docker indisponível) que roda o pipeline completo via Docker e valida os artefatos gerados.
  - Adicionadas dependências `pytest` e `pyyaml` ao `requirements.txt`.

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
