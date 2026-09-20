# SaudeJa — Classificador de no-show em agendamentos médicos

> Status: em desenvolvimento — (AI Factory: Build, Deploy and Showcase)

## Visão Geral

SaudeJá é um SaaS voltado para clínicas particulares de saúde de médio e pequeno porte que oferece serviço de agendamento online, prontuário, faturamento e comunicação com pacientes. Atualmente visa desenvolver uma funcionalidade para prevê pacientes que se ausenta de agendamentos médicos usando o classificador LightGBM. O repositório foi herdado com um pipeline de treinamento que resulta em um modelo com desempenho de Acurácia 78% no test set, F1-score para classe positiva de 0,65 e ROC-AUC de 0,81, dentro da disciplina AI Factory: Build, Deploy and Showcase.


## Problema

O no-show custa em média **R$ 180 por consulta perdida** e a taxa nacional gira em torno de 25-35%. Para uma clínica média (1.500 consultas/mês), isso significa R$ 70k-100k de receita evaporando todo mês. A primeira tentativa de reduzir o no-show foi de envio de lembrete para todos os pacientes, o que resultou em alto custo, sendo insustentável para clínicas com orçamento menor. A proposta atual é treinamento de um algoritmo que classifica os pacientes com alto chance de não comparecimento e apenas disparar lembrete para estes, reduzindo, dessa forma, em até 70% o gasto com mensageria.

## Arquitetura

Veja o diagrama C4 nível 1 e 2 em [`docs/architecture.md`](docs/architecture.md).
Decisões arquiteturais relevantes estão registradas em [`docs/adr/`](docs/adr/).

A solução busca cumprir os seguintes funcionalidades:
 - O paciente acessa a área de pacientes, realizar o cadastro de informações junto ao agendamento.
 - O funcionário da clínica acessa a área de funcionários e consulta a lista de pacientes por data (por default, aparece os do dia de hoje) junto ao probabilidade de no-show.
 - O funcionário da clínica pode pedir ao LLM para consultar um paciente em específico.
 - O modelo de predição de no-show consulta o banco de dados para fazer predição automática de pacientes com maior probabilidade de no-show 2 dias antes da consulta acontecer (exemplo: no dia 15/09 o sistema resgata os pacientes do dia 17/09).
 - O modelo retorna uma lista de pacientes com probabilidade no-show.
 - É disparado uma mensagem de confirmação para os pacientes dessa lista via Zenvia.

A solução permite, portanto, que o paciente acessa o portal do Saude Já e consegue fazer o cadastro e o agendamento de consulta. Essas informações são armazenados no banco de dados relacional e tanto o interface quanto o modelo de predição consegue acessar. Além disso, de forma automática, é disparado um pipeline de  busca de pacientes que tem a consulta marcada para dois dias depois e já aciona a inferencia para predição de no-show. O modelo, então, retorna com uma lista de pacientes que atenderam o critério de no-show. O dado de no-show potencial é guardado de volta no banco. Para finalizar o pipeline, o disparo de mensageria é acionado para pacientes que foram classificado como alto chance de no-show.

# Como usar o repositório

## Pipeline de treino (DVC/MLFLOW)

O pipeline de treino é versionado com DVC e segregado em 3 stages independentes, cada um rodando em um container Docker próprio a partir da mesma imagem (veja `dvc.yaml`):

1. **`preprocess`** (`src/preprocess.py`) — carrega `data/consultas-historicas.csv`, aplica o feature engineering e faz o split treino/teste estratificado. Não toca no MLflow. Gera `data/interim/train_raw.pkl`, `data/interim/test.pkl` e `data/interim/mapa_especialidade.json`.
2. **`train`** (`src/train.py`) — aplica o SMOTE-NC **somente** sobre o fold de treino gerado no passo anterior (o fold de teste nunca é balanceado, evitando vazamento) e treina o LightGBM. Abre a run no MLflow remoto, loga hiperparâmetros/modelo/tag `pipeline_arquitetura` e grava o `run_id` em `data/interim/mlflow_run_id.txt` para a etapa seguinte. Gera `data/model.pkl`.
3. **`validate`** (`src/validate.py`) — carrega o modelo e o fold de teste isolado, calcula as métricas (accuracy, ROC-AUC, PR-AUC, F1/precision/recall por classe) e **reabre a mesma run do MLflow** (via `run_id`) para logá-las junto do treino. O histórico de métricas vive só no MLflow — não há artefato local de métricas versionado.

Como os 3 stages rodam em containers `--rm` separados, os artefatos intermediários trafegam pelo volume `data/` montado em todos eles (`data/interim/`).


### Subindo o MLflow server (Docker)

Os stages `train` e `validate` do `dvc.yaml` já builda a imagem, sobem o `mlflow-server` via `docker compose` e rodam o container correspondente na network `saudeja-net`, apontando `MLFLOW_TRACKING_URI` para `http://mlflow-server:5000` (nome do serviço, não `localhost`, já que os containers se comunicam pela rede Docker). O stage `preprocess` não depende do MLflow e não sobe o compose.

1. Suba o servidor MLflow remoto (se ainda não estiver de pé):
   ```powershell
   docker compose up -d mlflow-server
   ```
   Builda a imagem a partir de `dockerfile.mlflow` e sobe em `localhost:5000` (backend sqlite em `/mlflow/db`, artifacts em `/mlflow/artifacts`, ambos em volumes nomeados persistentes).
2. Rode o experimento via DVC (builda a imagem de treino, garante o mlflow-server no ar e roda o container de treino registrando no MLflow):
   ```powershell
   dvc exp run
   ```
   ou, para reexecutar o pipeline completo sem criar um experimento novo:
   ```powershell
   dvc repro
   ```
3. Confira o resultado do run no MLflow UI: `http://localhost:5000`.

> Se `docker compose up -d mlflow-server` for rodado a partir de um terminal Bash, note que o `docker run` embutido nos `cmd` dos stages `train`/`validate` usa sintaxe `%cd%` (cmd.exe) no mount de volume — funciona normalmente quando o DVC dispara o comando pelo shell do sistema, mas não copie esse comando manualmente para um terminal Bash.

### `dvc run` x `dvc repro` x `dvc exp run`

| Comando | Quando usar | Efeito |
|---|---|---|
| `dvc run` | Criar um stage **novo** no `dvc.yaml` | Não reexecuta o pipeline existente — só define um stage |
| `dvc repro` | Reexecutar o pipeline após mudar código/dados/hiperparâmetros de forma definitiva | Roda os stages afetados e sobrescreve `dvc.lock` diretamente, sem manter as tentativas anteriores |
| `dvc exp run` | Tuning manual de hiperparâmetros, testar variações | Roda o pipeline como experimento isolado (não altera `dvc.lock` do workspace); use `dvc exp show` para comparar métricas entre rodadas antes de promover uma com `dvc exp apply`/`dvc repro` |

Para ajuste de hiperparâmetros (ex.: `params.yaml`), prefira `dvc exp run` — permite comparar várias rodadas sem sujar o `dvc.lock` a cada tentativa. Use `dvc repro` só quando já decidiu a mudança e quer consolidá-la no pipeline principal.

### Fluxo de treino/ajuste do modelo

1. Se o dataset (`data/consultas-historicas.csv`) mudou — novas consultas, correção de registros etc. —, atualize o arquivo e rode `dvc add data/consultas-historicas.csv` para gerar um novo hash e atualizar o `.dvc` correspondente. Pule este passo se só o código/hiperparâmetros mudaram.
2. Altere o que for preciso (ex.: `src/preprocess.py`, `src/train.py`, `src/validate.py`, hiperparâmetros, `requirements/train.txt`):
   - Se for **tuning de hiperparâmetros** (`params.yaml`) ou qualquer mudança ainda em exploração, rode `dvc exp run` para cada variação e compare os resultados com `dvc exp show` antes de decidir qual manter (ver seção acima).
   - Uma vez decidida a mudança (hiperparâmetro final ou alteração de código/dados), rode `dvc repro` — reexecuta apenas os stages afetados (DVC detecta isso pelas `deps`/`params` de cada stage), atualiza `dvc.lock` e regenera `data/model.pkl`. Se a mudança veio de um experimento já rodado com `dvc exp run`, use `dvc exp apply <exp>` para trazê-la ao workspace em vez de repetir o treino.
3. Confira os resultados no MLflow UI (`http://localhost:5000`, run com a tag `pipeline_arquitetura`) e no `dvc.lock` atualizado. O histórico de métricas fica só no MLflow -- não há `metrics.json` local para comparar.
4. Se o modelo/resultado for o esperado, faça commit do código alterado junto com `dvc.yaml`, `dvc.lock` e o `.dvc` do dataset, se houver, para deixar rastreável qual dado e qual código geraram qual modelo.
5. Rode `dvc push` para enviar os artefatos rastreados (dataset e/ou modelo) ao remote configurado em `.dvc/config` (hoje um caminho local, trocar por um remote persistente antes de usar em equipe).

> Evite `dvc add .` na raiz do repositório: além de conflitar com os `outs` já gerenciados pelos stages do pipeline, é um path com bug conhecido no Windows. Para rastrear um dataset novo, use `dvc add data/<arquivo>`.

O histórico dos runs fica registrado no MLflow remote, que roda no container Docker `saudeja-mlflow-server` (dados persistidos nos volumes `mlflow-db` e `mlflow-artifacts`).

### Tuning de hiperparâmetros (GridSearch)

`src/tune.py` roda `GridSearchCV` sobre um Pipeline `SMOTENC` + `LGBMClassifier`, com cross-validation estratificada (5 folds) aplicada **somente** sobre o fold de treino gerado por `preprocess.py` — o fold de teste isolado nunca é usado no tuning, mesma garantia dos demais stages. O SMOTE-NC entra como um step do pipeline (não antes do CV), para o balanceamento ser recalculado a cada fold e não vazar amostras sintéticas entre treino/validação da CV. Scoring multi-métrica (`f1_1`, `recall_1`, `pr_auc`), com refit em `f1_1` — a métrica de interesse do projeto (ADR-003).

Este script **não é um stage do `dvc.yaml`** — é exploratório, no mesmo espírito de `scripts/gerar_timestamp_sintetico.py`. Re-otimizar hiperparâmetros a cada re-treino mensal automatizado geraria instabilidade de modelo sem ganho comprovado; a escolha de hiperparâmetros é revisada por um humano e só é promovida a `params.yaml` manualmente.

```powershell
docker build -t saudeja-train -f dockerfile .
docker run --rm -v "%cd%/data:/app/data" -e MLFLOW_TRACKING_URI=http://mlflow-server:5000 --network saudeja-net saudeja-train src/tune.py
```

Imprime e loga no MLflow (run com tag `pipeline_arquitetura=gridsearch-tuning`) os melhores hiperparâmetros encontrados e as métricas médias de CV. Para promover um resultado: atualize `model.*` em `params.yaml` com os valores encontrados e rode `dvc exp run` para validar oficialmente no fold de teste isolado (`validate.py`) antes de decidir manter ou não — a métrica de CV é uma estimativa, não a métrica de decisão final.

> **Nota de resultado (2026-09-16):** rodado contra o dataset atual (~380 linhas sintéticas), o GridSearch encontrou `learning_rate=0.05, max_depth=4, num_leaves=16` (mantendo `n_estimators=120`) como melhor combinação por CV (`f1_1` médio ≈0.40). Validado no fold de teste isolado, esse resultado (`f1_1=0.372`) na verdade **performou pior** que os hiperparâmetros já em `params.yaml` (`f1_1=0.419`) — sinal de que, com um dataset tão pequeno (fold de teste de ~76 linhas), a variância entre CV e holdout supera o ganho que o tuning fino de hiperparâmetros consegue entregar. Os hiperparâmetros atuais foram mantidos; o script fica disponível para re-rodar quando houver mais volume de dados reais de produção.

## Dependências (`requirements/`)

Divididas por camada para manter a imagem de deploy da API enxuta (cold start, SLO §2):

| Arquivo | Usado por | Conteúdo |
|---|---|---|
| `requirements/base.txt` | todas as camadas | pandas, scikit-learn, lightgbm, mlflow, pyyaml, pytest |
| `requirements/train.txt` | `dockerfile` (stages `preprocess`/`train`/`validate`/`tune`) | `-r base.txt` + jupyter, matplotlib, imbalanced-learn |
| `requirements/api.txt` | `infra/api/dockerfile` | `-r base.txt` + fastapi, uvicorn, shap, httpx |
| `requirements/ui.txt` | interface Streamlit (`src/ui/`) e imagem combinada do deploy | `-r base.txt` + streamlit, httpx |
| `requirements/dev.txt` | desenvolvimento local e CI (nenhuma imagem Docker) | `-r train.txt` + `-r api.txt` + `-r ui.txt` + ruff |

Para desenvolver localmente com a suíte de testes completa (pipeline + API + interface) e o lint:

```powershell
pip install -r requirements/dev.txt
```

## Lint e testes

```powershell
ruff check src tests scripts    # regras em ruff.toml
pytest                          # testes de integração ficam de fora por padrão (pytest.ini)
pytest -m integracao            # sobe serviços reais efêmeros (Docker/MLflow)
```

`ruff check --fix` aplica as correções automáticas. O CI (Passo 10) roda exatamente os mesmos comandos, sem flags extras, para local e CI não divergirem.

## API de predição (FastAPI)

`src/api/main.py` expõe `/health` (status + `model_version`, um hash curto de `data/model.pkl`) e `/predict` (recebe os dados de um agendamento, devolve probabilidade de no-show + explicação SHAP), reaproveitando `src/inference.py` (Passo 1) e `src/explain.py` (Passo 2) sem duplicar lógica. O modelo é carregado uma única vez no startup (`lifespan`), não a cada request, para atender o SLO de latência p95<2s.

Rodar localmente (a partir da raiz do repositório, com `requirements/api.txt` instalado):

```powershell
python -m uvicorn api.main:app --reload --app-dir src
```

`--app-dir src` é necessário porque os módulos em `src/` (`inference.py`, `explain.py`) são importados "soltos" (sem prefixo de pacote) -- mesma convenção que os stages do pipeline já usam ao rodar como `python src/<script>.py`.

```powershell
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/predict -H "Content-Type: application/json" -d "{\"idade\":45,\"sexo\":\"F\",\"especialidade\":\"cardiologia\",\"distancia_km\":5.5,\"dias_entre_agendamento_consulta\":14,\"historico_noshow\":1,\"data_hora_agendada\":\"2026-01-09T18:00:00\"}"
```

Ou via Docker (`infra/api/dockerfile`, monta `data/` para ler o `model.pkl` já treinado):

```powershell
docker build -t saudeja-api -f infra/api/dockerfile .
docker run --rm -p 8000:8000 -v "%cd%/data:/app/data" saudeja-api
```


## Interface (Streamlit)

`src/ui/app.py` é a interface da clínica: visão **Funcionário** em abas — "Testar predição" (formulário manual), "Explicabilidade" (contribuições SHAP da última predição), "Fila do dia" (Passo 5: agendamentos do dia ordenados por risco, com resumo de quantos são alto risco/sem predição e a explicação SHAP gravada de cada paciente ao selecionar a linha) e "Dev: disparo manual" (placeholder até o job D-2, Passo 6, visível só com `APP_ENV=dev`) — e visão **Paciente** (Passo 5: cadastro + agendamento persistidos no Supabase). A sidebar mostra o estado da conexão com o Supabase ao lado do backend de predição. Toda a lógica fica em `src/ui/logic.py`, que não importa `streamlit` e por isso é testável sem o runtime dele.

**Datas sempre no fuso da clínica** (`TIMEZONE_CLINICA`, default `America/Sao_Paulo`): "fila do dia 19/09" são as consultas de 19/09 em São Paulo, e o cadastro grava `data_hora_agendada` com o fuso explícito. O container roda em UTC — sem isso, depois das 21h a fila do dia e o job D-2 trabalhariam com a data errada, e os horários apareceriam 3h deslocados. `dias_entre_agendamento_consulta` não é perguntado no cadastro: é derivado da data escolhida (`logic.dias_ate_consulta`), porque é feature do modelo e um valor digitado poderia contradizer a própria data da consulta.

```powershell
streamlit run src/ui/app.py
```

### Como a interface obtém a predição

`PREDICT_BACKEND` escolhe entre duas implementações do mesmo contrato, ambas convergindo para `src/inference.py` (sem lógica de predição duplicada):

| Valor | O que faz | Quando usar |
|---|---|---|
| `processo` (default) | Importa `src/inference.py`/`src/explain.py` direto, sem HTTP | Produção e uso normal — decisão do [ADR-005](docs/adr/adr-005-integracoes-implicitas.md) (b): a UI não depende de a API estar de pé nem paga round-trip HTTP dentro do mesmo container |
| `api` | Chama `POST /predict` da API FastAPI (`API_BASE_URL`, default `http://127.0.0.1:8000`) | Desenvolvimento/diagnóstico — exercita a API do Passo 3 do formulário até a resposta |

`tests/test_ui_logic.py::test_backends_produzem_a_mesma_predicao` trava os dois caminhos na mesma resposta (probabilidade, classe, threshold, `model_version` e explicação) para o mesmo payload.

Para testar a interface contra a API de verdade, em dois terminais:

```powershell
python -m uvicorn api.main:app --app-dir src            # terminal 1
$env:PREDICT_BACKEND="api"; streamlit run src/ui/app.py # terminal 2
```

Variáveis relevantes (documentadas em `.env.example`): `APP_ENV` (`dev` expõe a aba de disparo manual; use `prod` no deploy), `PREDICT_BACKEND`, `API_BASE_URL`, `MODEL_PATH`, `SUPABASE_URL`/`SUPABASE_SECRET_KEY` (fila do dia e cadastro), `TIMEZONE_CLINICA`.

Para rodar interface e API juntas em container, do jeito que vão para produção, veja a seção seguinte.

## Banco de dados (Supabase)

`src/db/client.py` (client único, `supabase-py`) e `src/db/repositories.py` (`inserir_paciente`, `inserir_agendamento`, `buscar_agendamentos_d2_pendentes`, `gravar_predicao`, `buscar_fila_do_dia`) sobre o schema de `supabase/migrations/`. Minimização de PII por design: `pacientes` guarda só `id_paciente_externo` + atributos demográficos não identificáveis, nunca nome/CPF/email/telefone — guardado automaticamente por `tests/test_coerencia_repo.py::test_migrations_sql_sem_coluna_proibida_de_pii`. RLS habilitado em todas as tabelas, sem policies (só a chave secreta do backend acessa, ver `.env.example`).

### Ambiente local (Supabase CLI)

```powershell
supabase start   # sobe Postgres+PostgREST+Studio local via Docker, aplica supabase/migrations/
supabase status  # mostra API_URL e SECRET_KEY locais
supabase stop    # derruba os containers (dados locais persistem no volume até `supabase stop --no-backup`)
```

`tests/test_db.py` (marcado `integracao`) lê `supabase status -o json` para descobrir a URL/chave locais automaticamente — não precisa configurar `.env` para rodar os testes, só ter `supabase start` de pé:

```powershell
pytest -m integracao tests/test_db.py -v
```

### Ambiente real (projeto na nuvem)

`SUPABASE_URL`/`SUPABASE_SECRET_KEY` no `.env` (ver `.env.example`) apontam para o projeto Supabase real — `SUPABASE_SECRET_KEY` é a chave **secreta** (`sb_secret_...`, formato novo que substitui o JWT `service_role`), nunca a `publishable` (`sb_publishable_...`, equivalente à antiga `anon`), porque o backend (UI em processo, job D-2) precisa ignorar RLS. Projeto criado na região São Paulo (`sa-east-1`) — dados permanecem no Brasil, sem transferência internacional (ver [ADR-005](docs/adr/adr-005-integracoes-implicitas.md)).

Para aplicar as migrations num projeto remoto (fora do fluxo local acima): `supabase link --project-ref <ref>` seguido de `supabase db push`.


## Aplicação completa em container (API + Streamlit)

A imagem de `infra/deploy/dockerfile` roda **os dois processos no mesmo container**, do jeito que o Hugging Face Space vai rodar (Passo 11). Ela foi antecipada para o Passo 4 justamente para dar para ver o conjunto montado — e não só cada peça isolada por `pytest`/`curl`.

```powershell
docker compose up -d app      # builda e sobe; aguarde o healthcheck ficar "healthy"
```

| Endereço | O quê |
|---|---|
| http://localhost:7860 | Interface Streamlit (7860 é a porta que o HF Space publica por padrão) |
| http://localhost:8000/health | API FastAPI — status + `model_version` |
| http://localhost:8000/docs | OpenAPI da API (integrações externas, diagrama C2) |

```powershell
docker compose logs -f app
docker compose stop app
```

O que essa imagem prova, e as imagens isoladas não provam:

- **O `model.pkl` viaja dentro da imagem**, não montado por volume (`infra/api/dockerfile` monta; esta empacota). No Space não há DVC nem remote em runtime. Por isso o build falha se você não tiver rodado `dvc pull`/`dvc repro` antes — falha proposital, melhor que um container sem modelo.
- **UI e API servem a mesma predição**: `model_version` idêntico nos dois e a mesma probabilidade para o mesmo payload, dentro e fora do container.
- **O container não fica "meio vivo"**: `infra/deploy/entrypoint.sh` derruba tudo assim que qualquer um dos dois processos sai (`wait -n`) e encerra os dois em SIGTERM — sem supervisord, que não se pagaria para dois processos sem ordem de inicialização entre si.

Variáveis úteis no `docker-compose.yml`: `APP_ENV` (`dev` local mostra a aba de disparo manual; o Space usa `prod`), `PREDICT_BACKEND` (`processo` por padrão — troque para `api` se quiser que a UI fale com a API deste mesmo container por HTTP).

Para buildar/rodar sem o compose:

```powershell
docker build -t saudeja-app -f infra/deploy/dockerfile .
docker run --rm -e APP_ENV=dev -p 7860:7860 -p 8000:8000 saudeja-app
```

> As três imagens do repositório têm papéis distintos: `dockerfile` (treino, stages do DVC), `infra/api/dockerfile` (só a API, enxuta, modelo por volume) e `infra/deploy/dockerfile` (API + UI + modelo, a que vai para o Space).

## Roadmap

- [x] Etapa 1: adoção do protótipo
- [x] Etapa 2: escolha da stack (ADR-001)
- [ ] Etapa 3: arquitetura C4 + ADR-002 + repositório
- [ ] Etapa 4: deploy manual
- [ ] Etapa 5: CI/CD
- (etc.)

## Autor

Vanessa Hoysan Lin