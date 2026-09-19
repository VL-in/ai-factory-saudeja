# Changelog

## [v1.0] (Vanessa + Claude) - 2026-09-19

- (feat) `src/ui/app.py`: interface Streamlit, casca desenhada para crescer sem trocar de estrutura. Visão **Funcionário** em `st.tabs`: "Testar predição" (formulário manual -> probabilidade + classe prevista + threshold + `model_version`), "Explicabilidade" (contribuições SHAP da última predição, em tabela + gráfico, com a leitura de sinal explicitada -- positivo empurra para no-show), "Fila do dia" (placeholder que **nomeia** o Passo 5) e "Dev: disparo manual" (placeholder, renderizada só com `APP_ENV=dev`, default `dev` em local). Visão **Paciente** como casca desabilitada até haver banco. Cliente de predição embrulhado em `st.cache_resource` no nível do módulo -- decorar uma função interna criaria um cache novo a cada chamada, que é o mesmo que não ter cache.
- (feat) `src/ui/logic.py`: toda a lógica da UI fora do runtime do Streamlit (não importa `streamlit`), com **dois backends de predição atrás de um contrato único**: `ClientePredicaoEmProcesso` (default -- import direto de `src/inference.py`/`src/explain.py`, sem HTTP) e `ClientePredicaoAPI` (`PREDICT_BACKEND=api` -- `POST /predict` na API, `cliente_http` injetável). `ErroValidacao` (dado recusado, equivalente ao 422) e `ErroIndisponivel` (backend fora do ar) separam culpa do dado de culpa do sistema, para a UI mostrar mensagem acionável em vez de traceback; `_detalhe_422()` traduz tanto o `detail` string da API quanto a lista de erros do Pydantic. `listar_especialidades()` alimenta o selectbox a partir do mapa fixado no treino (evita 422 por digitação) e degrada para texto livre se o artefato não estiver por perto. `PREDICT_BACKEND` desconhecido falha alto em vez de cair num default silencioso.
- (docs) **Contradição resolvida**: o `PLANO-IMPLEMENTACAO.md` e o `architecture.md` §3.1 diziam que o Streamlit chama `POST /predict` via HTTP, enquanto o [ADR-005](../adr/adr-005-integracoes-implicitas.md) (b), já *Aceito*, decidira o contrário (chamada em processo). Decisão validada com a autora: manter o ADR-005 como vigente (`processo` é o default de produção) e oferecer o modo REST como ferramenta de desenvolvimento/diagnóstico, travando a divergência com um teste de paridade. Emenda registrada no ADR-005, nota do plano e §3.1 do `architecture.md` reescrita.
- (refactor) `_calcular_model_version()` saiu de `src/api/main.py` para `inference.calcular_model_version()`: a partir da API, a UI e a coluna `model_version` de `predicoes` precisam produzir a **mesma** string para o mesmo artefato -- ter a função em um só lugar é o que garante isso. Comportamento inalterado (sha256, 12 caracteres).
- (feat) Novo `requirements/ui.txt` (`-r base.txt` + `streamlit==1.56.0` + `httpx`), somado em `requirements/dev.txt`. Fica separado de `api.txt` para a imagem da API isolada (`infra/api/dockerfile`) continuar enxuta (cold start, SLO §2); a imagem combinada mais adiante instalará os dois. `.env.example` documenta `APP_ENV`, `PREDICT_BACKEND` e `API_BASE_URL`.
- (test) `tests/test_ui_logic.py` (20 testes): **paridade entre os backends** (mesmo payload -> mesma probabilidade, classe, threshold, `model_version` e explicação nos dois caminhos) -- a única coisa que impede a UI de mostrar um número diferente do que a API entrega a terceiros; **contrato UI -> API** (`PacienteConsultaIn(**montar_payload(...))`, que quebra antes de virar 422 na tela se o formulário parar de produzir algum campo); especialidade desconhecida -> `ErroValidacao` nos dois backends; modelo ausente / API fora do ar / HTTP 500 -> `ErroIndisponivel`; 422 do Pydantic traduzido para mensagem legível; modelo carregado uma vez só. Sem mock pesado, fiel à filosofia do repo: o backend em processo usa o `data/model.pkl` real e o REST usa um `TestClient` do próprio app FastAPI como transporte -- só as falhas de rede são simuladas.
- (test) `tests/test_ui_smoke.py` (6 testes) com `streamlit.testing.v1.AppTest`: app carrega sem exceção; as 4 abas existem em `APP_ENV=dev` e a de dev some em `prod`; visão Paciente carrega; placeholders nomeiam o passo que os liga; caminho feliz do formulário (submeter -> resultado na `session_state` com explicação e cards renderizados). `pytest -v -m "not integracao"` verde (**87 testes**, +26) e `ruff check src tests scripts` limpo.
- (feat) **Imagem combinada antecipada do Passo 11 para cá** (decidido com a autora): `infra/deploy/dockerfile` roda API FastAPI + Streamlit no mesmo container, do jeito que o HF Space vai rodar — o Passo 4 existe para tornar o sistema visível antes do fim do plano, e ver as peças montadas faz parte disso. Instala `requirements/api.txt`+`ui.txt` (sem `train.txt`, cold start/SLO §2) e **empacota o `data/model.pkl` na imagem** em vez de montá-lo por volume como `infra/api/dockerfile`: no Space não há DVC nem remote em runtime (decisão do Passo 9). Build sem `dvc pull` falha de propósito, em vez de gerar um container sem modelo.
- (feat) `infra/deploy/entrypoint.sh`: dois processos, sem supervisord/s6 — não há ordem de inicialização entre eles, então a dependência a mais não se pagaria. O que o script evita é o container seguir "meio vivo" com um dos dois morto: `wait -n` derruba tudo assim que qualquer um sai, e o `trap TERM INT` encerra os dois em SIGTERM.
- (feat) Serviço `app` no `docker-compose.yml` (`docker compose up -d app`): UI em 7860 (a porta que o HF Space publica por padrão), API em 8000, healthcheck em `/health` via `python -c urllib` (não há curl na `python:3.10-slim` — mesmo padrão já usado pelo `mlflow-server`). Sem bind mount de `./data` de propósito: montar o diretório local esconderia justamente o que se quer verificar, que é a imagem se sustentar sozinha.
- (feat) `.dockerignore` (contexto de build sem `.git/`, `mlruns/`, `docs/`, caches — `data/model.pkl` explicitamente mantido, a imagem de deploy precisa dele) e `.gitattributes` (`*.sh text eol=lf`: com `core.autocrlf=true` no Windows, um CRLF no shebang quebraria o entrypoint dentro do container).
- (docs) Registrado como **ponto em aberto do Passo 11** (`architecture.md` §6 e plano): o HF Space publica uma **única porta**, então UI em 7860 + API em 8000 significa que só a UI fica acessível de fora. A UI não sofre (chama o modelo em processo, ADR-005 b); quem fica sem endereço público é a API como porta de entrada de integrações externas (diagrama C2). Três saídas registradas para decidir no Passo 11, nenhuma escolhida agora.
- (resultado) Verificação manual da imagem combinada: `docker compose up -d app` → healthcheck `healthy`; `/health` 200 com `model_version` `6430cb3315da` — **o mesmo hash do host**; `POST /predict` no container devolveu `probabilidade=0.4823787274150104`, idêntica à do host, com a mesma ordenação de contribuições SHAP; UI respondendo 200 em 7860. Matando o uvicorn de dentro do container, o container inteiro saiu com código 137 e o Streamlit foi encerrado junto (nada de "meio vivo"); `docker compose stop app` encerrou em **0.9s** (trap de SIGTERM funcionando, sem esperar o SIGKILL de 10s).
- (resultado) Verificação manual ponta a ponta: `uvicorn api.main:app --app-dir src` + `streamlit run src/ui/app.py` com `PREDICT_BACKEND=api` -- `/health` e a UI respondendo, mesma `model_version` (`6430cb3315da`) nos dois; predição pelo formulário (F, 45 anos, sexta 18h, 14 dias de antecedência) devolveu `prob=0.4824`, abaixo do threshold `0.60`, com as três maiores contribuições em `dias_entre_agendamento_consulta`, `dia_de_semana` e `horario` -- coerente com a checagem de sanidade do SHAP registrada na v0.15. Especialidade inexistente e `sexo="X"` chegaram à tela como mensagem legível ("especialidade(s) desconhecida(s): [...]" e "sexo: Input should be 'F' or 'M'"), não como traceback.

## [v0.17] (Vanessa + Claude) - 2026-09-19

- (fix) src/config_projeto.py: params.yaml e caminhos de data/ resolvidos a partir de
REPO_ROOT em vez do CWD do processo -- a API iniciada fora da raiz do repo quebrava
no import; explain.explicar() passa a exigir 1 linha e ganha explicar_lote(), porque
devolver silenciosamente a explicacao da linha 0 gravaria a explicacao do paciente
errado na fila do job D-2 (SLO §4); preprocessar()/extrair_features_temporais() nao
mutam mais o DataFrame do chamador; sha256 no lugar de md5 em _calcular_model_version
(md5 derruba o startup em host FIPS)

- (chore) ruff.toml + requirements/dev.txt formalizam o lint pendente 
(architecture.md §8); 21 achados corrigidos; +9 testes de regressao



## [v0.16] (Vanessa + Claude) - 2026-09-19

- (feat) `src/api/schemas.py`: `PacienteConsultaIn` (Pydantic, valida idade/distância/dias/histórico ≥0, `sexo: Literal["F","M"]`, `data_hora_agendada: datetime`) e `PredictOut` (probabilidade, classe_prevista, threshold_usado, explicacao, `explicacao_texto: str | None` -- campo já reservado para o plug do LLM, `model_version`).
- (feat) `src/api/main.py`: API FastAPI com `/health` (status + `model_version`, hash curto de `data/model.pkl`) e `/predict`, reusando `src/inference.py` e `src/explain.py` sem duplicar lógica. Modelo, mapa de especialidade e `shap.TreeExplainer` carregados **uma vez no startup** (`lifespan`), não por request -- crítico para o SLO de latência p95<2s. `EspecialidadeDesconhecidaError` capturada e traduzida para HTTP 422 (nunca 500 nem predição sobre NaN). Threshold lido de `params.yaml` (`inference.PARAMS`, mesma fonte que `validate.py`). Bootstrap de `sys.path` no topo do módulo (mesmo padrão de `tests/conftest.py`) para funcionar independente de como é iniciado (uvicorn a partir da raiz, `TestClient` nos testes, `CMD` do container).
- (feat) `requirements.txt` dividido em `requirements/base.txt` (núcleo de ML + pytest, usado por todas as camadas), `requirements/train.txt` (`-r base.txt` + jupyter/matplotlib/imbalanced-learn) e `requirements/api.txt` (`-r base.txt` + fastapi/uvicorn/shap/httpx) -- reduz a imagem de deploy da API (cold start, SLO §2). `dockerfile` (treino) e `dvc.yaml` (deps dos stages `preprocess`/`train`/`validate`) atualizados para os novos caminhos.
- (feat) Novo `infra/api/dockerfile` (substitui o `infra/ML/dockerfile` morto já removido): instala só `requirements/api.txt`, copia `src/`+`params.yaml`, monta `data/` via volume em runtime (não empacota `model.pkl` -- isso fica para a imagem combinada de deploy).
- (test) `tests/test_api.py` com `TestClient` sobre o `data/model.pkl` real: `/health`→200; `/predict` válido→200 com `0<=probabilidade<=1` e explicação não vazia; especialidade desconhecida→422 (não 500); payload inválido (idade negativa, sexo fora de F/M)→422 via Pydantic; `classe_prevista` respeitando `threshold_usado` (monkeypatch em `inference.PARAMS["decision"]`, threshold extremo em cada direção para o teste ser determinístico). `tests/test_coerencia_repo.py::test_requirements_nao_tem_pacotes_duplicados` adaptado para checar os três arquivos de `requirements/` em vez do antigo arquivo único. `pytest -v -m "not integracao"` verde (52 testes).
- (resultado) Verificação manual ponta a ponta: `uvicorn api.main:app --app-dir src` local + `curl` em `/health`/`/predict`, e também via `docker build -f infra/api/dockerfile .` + `docker run` com `data/` montado -- mesmo resultado nos dois casos (`model_version` idêntico, mesma probabilidade/explicação para o mesmo payload).
- (fix) `src/validate.py`: `roc_auc_score` levanta `ValueError` (em vez de só avisar) em `scikit-learn==1.5.2` (pin real de `requirements/base.txt`) quando `y_test` tem uma única classe -- derrubava o stage `validate` inteiro nesse caso-limite, em vez de só marcar a métrica como não confiável (como já acontecia com `precision_1`/`recall_1`/`f1_1`). `roc_auc`/`pr_auc` agora reaproveitam a mesma flag que já disparava a tag `aviso_split` e caem para `0.0` nesse cenário, em vez de recalcular sem essa proteção. `tests/test_validate.py::test_validar_sinaliza_aviso_quando_classe_positiva_ausente` (pré-existente) passou a cobrir esse caminho de verdade -- antes só não quebrava por acaso, em ambientes com uma versão de scikit-learn mais recente/tolerante que a pinada.
- (resultado) `dvc repro` rodado após a divisão de `requirements.txt` (dep dos stages `preprocess`/`train`/`validate` mudou de arquivo) e o fix acima: `data/model.pkl` saiu **byte-a-byte idêntico** (mesmos hiperparâmetros, mesmo `random_state`), só o `run_id` do MLflow mudou (nova run reexecutada); `dvc.lock` e `dvc status` consistentes (`Data and pipelines are up to date`).

## [v0.15] (Vanessa + Claude) - 2026-09-19

- (feat) `src/explain.py`: `construir_explicador(model)` cria um `shap.TreeExplainer` (espaço de margem/log-odds, `model_output="raw"`); `explicar(explainer, X)` retorna a lista `{"feature", "contribuicao"}` da linha, ordenada por `abs(contribuicao)` desc e JSON-serializável (`float` nativo). Cálculo síncrono dentro do módulo de inferência (volume baixo, `TreeExplainer` é da ordem de milissegundos) para garantir cobertura de 100% das predições por construção, conforme SLO §4. `_valores_classe_positiva()` normaliza a saída de `shap_values()` (observado como `ndarray (n, features)` já da classe positiva com `LGBMClassifier` binário + `shap==0.49.1`, mas escrito defensivamente contra variações de versão -- lista `[neg, pos]` ou `ndarray 3D`).
- (feat) Plug inativo para explicação em linguagem natural (preparação somente, sem ativar agora): interface `ExplicadorLLM` (ABC, método `explicar_em_texto(contribuicoes, contexto) -> str | None`) e `ExplicadorLLMDesativado` (default -- retorna `None`, nunca abre rede).
- (feat) `requirements.txt`: adicionado `shap==0.52.0` (compatível com `lightgbm`/`scikit-learn` já fixados).
- (test) `tests/test_explain.py`: teste de **aditividade** (`soma(contribuicoes) + expected_value == margem prevista por model.predict(X, raw_score=True)`, tolerância `1e-6`) -- prova matemática de que a explicação é fiel ao modelo, não uma heurística separada; teste de shape (nº de contribuições == nº de features); teste de ordenação por `abs(contribuicao)` desc; teste de serialização JSON; teste do plug inativo confirmando que nenhum socket é aberto (`monkeypatch` em `socket.socket.connect`) e que `ExplicadorLLM` não pode ser instanciada diretamente (ABC). `pytest -v -m "not integracao"` verde (46 testes).
- (resultado) Checagem de sanidade manual (não trava CI -- dataset sintético pequeno demais para assert rígido): rodando `explicar()` sobre 5 linhas reais amostradas de `data/consultas-historicas.csv`, as features de maior contribuição absoluta são consistentemente `horario`, `dia_de_semana` e `dias_entre_agendamento_consulta` -- coerente com a hipótese de negócio herdada da Camila (concentração de no-show em sextas no fim do expediente, `features.temporais` em `params.yaml`).

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
