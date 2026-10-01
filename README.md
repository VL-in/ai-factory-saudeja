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
 - É disparado uma mensagem de confirmação para os pacientes dessa lista via Infobip.

A solução permite, portanto, que o paciente acessa o portal do Saude Já e consegue fazer o cadastro e o agendamento de consulta. Essas informações são armazenados no banco de dados relacional e tanto o interface quanto o modelo de predição consegue acessar. Além disso, de forma automática, é disparado um pipeline de  busca de pacientes que tem a consulta marcada para dois dias depois e já aciona a inferencia para predição de no-show. O modelo, então, retorna com uma lista de pacientes que atenderam o critério de no-show. O dado de no-show potencial é guardado de volta no banco. Para finalizar o pipeline, o disparo de mensageria é acionado para pacientes que foram classificado como alto chance de no-show.

# Como usar o repositório

## Pipeline de treino (DVC/MLFLOW)

O pipeline de treino é versionado com DVC e segregado em 4 stages independentes, cada um rodando em um container Docker próprio a partir da mesma imagem (veja `dvc.yaml`):

0. **`validate_data`** (`src/validate_data.py`, Passo 10.3) — o gate de dados: barra o dataset **antes de treinar** por schema (colunas, nulos, `id_consulta` único), regra de negócio do contrato de features (`src/contrato_features.py`, com as especialidades do cadastro de `params.yaml`) e distribuição (taxa de positivos entre 10% e 50%; no mínimo 1/tolerância do gate de positivos no fold de teste). Registra, sem bloquear, as linhas fora do domínio do treino e o PSI produção × semente. Gera `data/interim/dados_validados.json` (o selo, dependência do `preprocess` — só carrega o que depende do dado, então mudar uma checagem não re-treina o modelo) e `data/interim/relatorio_dados.json` (lido pelo gate de re-treino para o resumo do run).
1. **`preprocess`** (`src/preprocess.py`) — carrega `data/consultas-treino.csv`, aplica o contrato de features (coerção explícita de tipo + regra de negócio, como defesa em profundidade), o feature engineering e faz o split treino/teste estratificado. Não toca no MLflow. Gera `data/interim/train_raw.pkl`, `data/interim/test.pkl` e `data/interim/mapa_especialidade.json`.
2. **`train`** (`src/train.py`) — aplica o SMOTE-NC **somente** sobre o fold de treino gerado no passo anterior (o fold de teste nunca é balanceado, evitando vazamento) e treina o LightGBM. Abre a run no MLflow remoto, loga hiperparâmetros/modelo/tag `pipeline_arquitetura` e grava o `run_id` em `data/interim/mlflow_run_id.txt` para a etapa seguinte. Gera `data/model.pkl`.
3. **`validate`** (`src/validate.py`) — carrega o modelo e o fold de teste isolado, calcula as métricas (accuracy, ROC-AUC, PR-AUC, F1/precision/recall por classe) e **reabre a mesma run do MLflow** (via `run_id`) para logá-las junto do treino. O histórico de métricas vive só no MLflow — não há artefato local de métricas versionado.

Como os 4 stages rodam em containers `--rm` separados, os artefatos intermediários trafegam pelo volume `data/` montado em todos eles (`data/interim/`).


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
5. Rode `dvc push` para enviar os artefatos rastreados (dataset e/ou modelo) ao remote `azure` — um container privado no Azure Blob Storage (ver [Remote do DVC (Azure Blob Storage)](#remote-do-dvc-azure-blob-storage) para a configuração local exigida antes do primeiro push).

> Evite `dvc add .` na raiz do repositório: além de conflitar com os `outs` já gerenciados pelos stages do pipeline, é um path com bug conhecido no Windows. Para rastrear um dataset novo, use `dvc add data/<arquivo>`.

O histórico dos runs fica registrado no MLflow remote, que roda no container Docker `saudeja-mlflow-server` (dados persistidos nos volumes `mlflow-db` e `mlflow-artifacts`).

### Remote do DVC (Azure Blob Storage)

Os artefatos rastreados pelo DVC (`data/consultas-historicas.csv` e `data/model.pkl`) ficam num **container privado** no Azure Blob Storage. O `.dvc/config` é versionado e guarda só `[core] remote = azure`: a URL do remote carrega o nome da conta e do container onde o dataset de treino — que contém desfechos reais de pacientes, ainda que pseudonimizados — está armazenado, e por isso fica fora do git. Ela vive em `DVC_REMOTE_URL` (`.env` na máquina de quem desenvolve, secret no GitHub Actions). As credenciais são duas, pelo menor privilégio: a de escrita (`AZURE_STORAGE_CONNECTION_STRING`) só no re-treino, o único workflow que faz `dvc push`, e uma SAS só de leitura (`AZURE_STORAGE_CONNECTION_STRING_LEITURA`) no CI, no deploy e no job D-2.

Antes do primeiro `dvc push`/`dvc pull` numa máquina nova, três passos — cada um falha de um jeito diferente se for pulado:

```powershell
# 1. DVC com o extra do Azure. Sem ele: "azure is supported, but requires
#    'dvc-azure' to be installed". Instale no venv do projeto, não no Python global.
pip install -r requirements/dvc.txt

# 2. URL do remote no config local (`.dvc/config.local`, ignorado pelo git).
#    Sem ela: o DVC diz que o remote 'azure' não existe.
dvc remote add --local -f azure $env:DVC_REMOTE_URL

# 3. Credencial no ambiente — o DVC lê da variável, não do `.env`.
#    Sem ela: "Authentication to Azure Blob Storage requires either
#    account_name or connection_string".
$env:AZURE_STORAGE_CONNECTION_STRING = "<connection string da conta>"
```

Dois erros do lado do Azure, que não são de configuração do repositório:

- **`AuthorizationFailure` / "This request is not authorized to perform this operation"** — o firewall da storage account não libera o IP de quem está chamando. No portal: *Storage account → Networking → Firewalls and virtual networks → Add your client IP address*. A regra é por IP, então volta a aparecer quando a conexão troca de IP.
- **`No such container`** — o DVC não cria o container; ele precisa existir, e **privado** (sem acesso anônimo de leitura).


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
| `requirements/dvc.txt` | `dvc pull` do CI, do deploy e do job D-2 (Passo 10.5) | `dvc[azure]` |
| `requirements/dev.txt` | desenvolvimento local e CI (nenhuma imagem Docker) | `-r train.txt` + `-r api.txt` + `-r ui.txt` + `-r dvc.txt` + ruff + mypy |

Para desenvolver localmente com a suíte de testes completa (pipeline + API + interface) e o lint:

```powershell
pip install -r requirements/dev.txt
```

## Lint e testes

```powershell
ruff check src tests scripts    # regras em ruff.toml (inclui S, flake8-bandit)
mypy src scripts                # type-check estático, configuração em mypy.ini
pytest                          # testes de integração ficam de fora por padrão (pytest.ini)
pytest -m integracao            # sobe serviços reais efêmeros (Supabase local, Docker/MLflow)
```

`ruff check --fix` aplica as correções automáticas. O CI (Passo 10) roda exatamente os mesmos comandos, sem flags extras, para local e CI não divergirem.

O `mypy` (Passo 10.6) é **gradual, em dois níveis**: os módulos da fronteira escalar — onde uma data vinda como string do banco, um `None` de repositório ou um threshold trocado chegariam ao paciente — exigem anotação em toda função (`disallow_untyped_defs`); o resto do `src/` (assinaturas `DataFrame -> DataFrame`) só tem verificado o que já está anotado. DataFrame é responsabilidade do contrato de features em runtime, não do type-check. E o mypy **não** pega erro de fuso: `datetime` com e sem fuso são o mesmo tipo para ele.

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

`src/ui/app.py` é a interface da clínica: visão **Funcionário** (atrás de login, ver abaixo) em abas — "Testar predição" (formulário manual), "Explicabilidade" (contribuições SHAP da última predição), "Fila do dia" (Passo 5: agendamentos do dia ordenados por risco, com resumo de quantos são alto risco/sem predição e a explicação SHAP gravada de cada paciente ao selecionar a linha), "Observabilidade" (Passo 8.5: p95 de latência, erros, cobertura de explicação e última execução do job, lidos de `eventos_app`) e "Dev: disparo manual" (Passo 6: dispara `src/jobs/inferencia_diaria.py::processar_dia()` e mostra agendamentos/predições/mensagens, visível só com `APP_ENV=dev`) — e visão **Paciente** (Passo 5: cadastro + agendamento persistidos no Supabase). A sidebar mostra o estado da conexão com o Supabase ao lado do backend de predição. Toda a lógica fica em `src/ui/logic.py`, que não importa `streamlit` e por isso é testável sem o runtime dele.

**Datas sempre no fuso da clínica** (`TIMEZONE_CLINICA`, default `America/Sao_Paulo`): "fila do dia 19/09" são as consultas de 19/09 em São Paulo, e o cadastro grava `data_hora_agendada` com o fuso explícito. O container roda em UTC — sem isso, depois das 21h a fila do dia e o job D-2 trabalhariam com a data errada, e os horários apareceriam 3h deslocados. `dias_entre_agendamento_consulta` não é perguntado no cadastro: é derivado da data escolhida (`logic.dias_ate_consulta`), porque é feature do modelo e um valor digitado poderia contradizer a própria data da consulta.

**Cadastro do paciente fiel à realidade de uma clínica** (2026-09-20): a tela pede nome completo, CPF, telefone e data de nascimento — não mais um "identificador" digitado livremente, nem idade, nem histórico de no-show autodeclarado. **O CPF não é persistido**: `id_paciente_externo` é o hash sha256 dele (`logic._id_paciente_externo_de_cpf`), gerado automaticamente; e a idade usada como feature é sempre calculada a partir da data de nascimento (`features.calcular_idade`), nunca gravada. Duas exceções deliberadas são gravadas: telefone (Passo 7, normalizado por `logic.normalizar_telefone` — é para onde o lembrete real via Infobip vai) e **nome completo** (2026-09-28, [ADR-007](docs/adr/adr-007-nome-do-paciente.md) — a "Fila do dia" é operada por quem atende no balcão, e o hash de 64 caracteres não serve para chamar ninguém). `historico_noshow` deixou de ser campo do formulário: é contado a partir dos agendamentos passados do próprio paciente com `status='no_show'` (`repositories.contar_no_shows_anteriores`). Data e horário da consulta só oferecem os slots que a clínica de fato atende (`src/agenda_clinica.py`, mesma grade usada para gerar `data_hora_agendada` no dataset histórico — seg-sex 08h-11h30/13h-18h, sáb 08h-11h30, fechado aos domingos), para o cadastro nunca produzir um agendamento fora do domínio que o modelo aprendeu.

```powershell
streamlit run src/ui/app.py
```

### Login da equipe

A visão **Funcionário** pede e-mail e senha, verificados pelo **Supabase Auth** do mesmo projeto do banco ([ADR-008](docs/adr/adr-008-login-da-equipe.md)). A visão **Paciente** continua aberta, porque é o autoagendamento. A sessão fica na memória do servidor, sem cookie: recarregar a página pede login de novo, e 30 minutos sem interação encerram a sessão. "Sair" (na barra lateral) apaga também a última predição da aba "Explicabilidade".

Não há cadastro aberto. A conta de cada funcionário é criada pelo administrador, com a senha digitada no terminal sem eco:

```powershell
python scripts/criar_funcionario.py recepcao@clinica.com.br
```

O script usa `SUPABASE_URL`/`SUPABASE_SECRET_KEY` do `.env`, ou seja, cria a conta no projeto para onde o `.env` aponta. Para **desativar** alguém: Supabase Dashboard → Authentication → Users → apagar ou banir o usuário.

O login só **prova identidade**. Os dados continuam sendo lidos com a chave secreta do backend, e o RLS não muda. A tentativa de login usa um client descartável e nunca o singleton de `src/db/client.py`: depois do sign-in, o `supabase-py` troca o `Authorization` do client pelo JWT do usuário, e no singleton isso deixaria o backend inteiro sem enxergar as tabelas (RLS sem policies) para todos os navegadores conectados.

> **No projeto Supabase remoto, dois ajustes são manuais.** O `supabase/config.toml` (`enable_signup = false`, `minimum_password_length = 8`) vale só para o Supabase local. No remoto:
>
> - desligue o cadastro aberto ("Allow new users to sign up");
> - suba o tamanho mínimo de senha de 6 para **8** ("Minimum password length").
>
> Os dois ficam no Dashboard, em Authentication → Sign In / Providers → Email. O mínimo só vale para senhas criadas ou trocadas depois do ajuste. Os dois também podem ser aplicados pela Management API, com o `SUPABASE_ACCESS_TOKEN` e o `SUPABASE_PROJECT_REF` do `.env` (Passo 10.1):
>
> ```powershell
> $uri = "https://api.supabase.com/v1/projects/$env:SUPABASE_PROJECT_REF/config/auth"
> $headers = @{ Authorization = "Bearer $env:SUPABASE_ACCESS_TOKEN" }
> Invoke-RestMethod -Method Patch -Uri $uri -Headers $headers -ContentType "application/json" `
>   -Body '{"password_min_length": 8, "disable_signup": true}'
> Invoke-RestMethod -Uri $uri -Headers $headers | Select-Object password_min_length, disable_signup
> ```
>
> **Não use `supabase config push` para isso.** Ele envia a seção `[auth]` inteira do `config.toml`, com valores de ambiente local como `site_url = "http://127.0.0.1:3000"`, e sobrescreveria a configuração de produção.

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

Variáveis relevantes (documentadas em `.env.example`): `APP_ENV` (`dev` expõe a aba de disparo manual; use `prod` no deploy), `PREDICT_BACKEND`, `API_BASE_URL`, `MODEL_PATH`, `SUPABASE_URL`/`SUPABASE_SECRET_KEY` (fila do dia, cadastro e login da equipe), `TIMEZONE_CLINICA`.

Para rodar interface e API juntas em container, do jeito que vão para produção, veja a seção seguinte.

## Banco de dados (Supabase)

`src/db/client.py` (client único, `supabase-py`) e `src/db/repositories.py` (`inserir_paciente`, `contar_no_shows_anteriores`, `inserir_agendamento`, `buscar_agendamentos_d2_pendentes`, `gravar_predicao`, `registrar_mensagem`, `buscar_fila_do_dia`) sobre o schema de `supabase/migrations/`. Minimização de PII por design: `pacientes` guarda `id_paciente_externo` (hash sha256 do CPF, gerado em `ui/logic.py`) + atributos demográficos não identificáveis (`data_nascimento`, `sexo`) + duas exceções deliberadas, `telefone` (Passo 7 — sem contato não há para onde mandar o lembrete real) e `nome_completo` ([ADR-007](docs/adr/adr-007-nome-do-paciente.md) — sem nome a fila do dia não é operável). **CPF e e-mail nunca**, sem exceção — guardado automaticamente por `tests/test_coerencia_repo.py::test_migrations_sql_sem_coluna_proibida_de_pii`, que autoriza cada exceção por par (coluna, migration): a coluna só vale no arquivo que a introduziu. RLS habilitado em todas as tabelas, sem policies (só a chave secreta do backend acessa, ver `.env.example`).

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

> **Migration nova exige os dois bancos.** `supabase db reset` aplica só no local — é lá que `pytest -m integracao` roda. O `.env` da máquina de quem desenvolve normalmente aponta para o **projeto remoto**, então a interface continua falando com um banco sem a coluna nova até `supabase db push` rodar. O sintoma é um erro de coluna inexistente (`42703`) ao abrir a aba que usa a coluna; desde 2026-09-28 a UI reconhece esse código e diz qual comando rodar, em vez de mostrar o erro cru do PostgREST. O mesmo vale para o deploy do Passo 11: o Space fala com o projeto remoto.

## Job de inferência diária (D-2)

`src/jobs/inferencia_diaria.py` (`processar_dia()`/`main()`) fecha o loop do produto: busca no Supabase os agendamentos de amanhã até dois dias à frente ainda sem predição (`buscar_agendamentos_d2_pendentes` — a janela de três dias recupera sozinha um dia em que o job não rodou), roda predição+explicação reaproveitando `src/inference.py`/`src/explain.py` (Passos 1/2 — mesmos módulos que a API usa, sem lógica duplicada), grava em `predicoes` e decide o disparo de lembrete pago conforme `decision.threshold` (`params.yaml`), sempre registrando a decisão (`enviado`/`nao_enviado`) em `mensagens_disparadas` para auditoria (SLA §6). Agendamento que não pode ser predito vai para quarentena sem interromper a fila, e o paciente recebe o lembrete mesmo assim, como exceção (`enviado_sem_predicao`). Todo lembrete enviado marca `agendamentos.lembrete_enviado`, que o re-treino usa para não confundir "compareceu" com "compareceu porque foi lembrado". Datas lidas do banco (UTC) são convertidas para o fuso da clínica antes de virar feature ou texto do SMS. Modelo carregado em processo, não via HTTP à API (ADR-005 b).

**Contrato de features** (Passo 10.3, `src/contrato_features.py`): cada agendamento é coagido e conferido antes de virar feature. Fora da **regra de negócio** (idade fora de 0–120, sexo fora de F/M, especialidade que o modelo não conhece, antecedência acima de 180 dias, horário fora da grade da clínica, valor nulo) vai para a quarentena, com o campo e a regra no motivo — nunca o valor. Fora do **domínio do treino** (distância acima de 50 km, antecedência fora de 1–90 dias, idade acima de 85, histórico acima de 10) é predito e marcado em `predicoes.fora_do_dominio`, que aparece como coluna na "Fila do dia". Na carga, o modelo tem nomes, ordem e categóricas das features conferidos contra o que o código monta.

**Onde roda** (Passo 10.5): no runner do GitHub Actions (`.github/workflows/job_d2.yml`), às 08h17 de São Paulo, e não no Space — ver a emenda do Passo 10 no [ADR-005](docs/adr/adr-005-integracoes-implicitas.md). `main()` acrescenta ao que a aba de dev faz: **pré-checagem** antes de qualquer SMS (o `model.pkl` e o `decision.threshold` são os do campeão em `data/champion_metrics.json`, e o banco responde) e **pós-checagem** depois (predições = pendentes − quarentena; fila inteira em quarentena é defeito sistêmico e falha o run), com o resumo em contadores no `$GITHUB_STEP_SUMMARY`.

Mensageria: `src/messaging/client.py` define a interface `enviar_lembrete(telefone, mensagem)`; `StubMessagingClient` (default, nunca faz rede) e `InfobipClient` (real, `MESSAGING_PROVIDER=infobip`, Passo 7) — canal SMS, autenticação por API Key da Infobip. `pacientes.telefone` (migration `20260920020000_telefone_paciente.sql`) é a exceção deliberada à minimização de PII (§4.1): sem contato de envio, o lembrete real não tem para onde ir. Falha de envio de um paciente (`ErroEnvioInfobip`) é registrada como `status_envio="falha_envio"` sem derrubar a fila do dia.

Rodar manualmente (a partir da raiz do repositório, com o Supabase e o modelo disponíveis):

```powershell
python src/jobs/inferencia_diaria.py   # recusa rodar se o model.pkl local não for o campeão
```

A aba **"Dev: disparo manual"** do Streamlit (`APP_ENV=dev`) chama `processar_dia()` pelo mesmo caminho e mostra agendamentos encontrados, predições gravadas e mensagens disparadas na tela, sem exigir CLI/cron — sem a pré-checagem do campeão, de propósito: é ferramenta de desenvolvimento.

`tests/test_job_inferencia.py` (marcado `integracao`, mesma convenção do Supabase local do `test_db.py`) semeia agendamentos D+2 sintéticos, roda o job com um cliente de mensageria "espião" e confere que `predicoes`/`mensagens_disparadas` batem com o threshold e que o disparo só acontece para quem cruzou o threshold:

```powershell
pytest -m integracao tests/test_job_inferencia.py -v
```

## Observabilidade de aplicação (Passo 8.5)

`src/observabilidade.py` registra em `eventos_app` (Supabase, migration `20260921000000_eventos_app.sql`) uma linha por predição servida e por execução do job — é de onde saem os números **medidos** do SLO §2 (latência) e §4 (cobertura de explicação), em vez de estimados na véspera do pitch. A decisão completa, com o que foi descartado e por quê, está no [ADR-006](docs/adr/adr-006-observabilidade.md).

O princípio que organiza o desenho: **o coletor não pode morar dentro daquilo que ele mede**. Métrica coletada dentro do HF Space some junto com o Space quando ele hiberna — inclusive a evidência de que caiu. Daí a divisão entre a camada interna (esta tabela, fonte de verdade) e as sondas externas (uptime em `/health` e dead-man's-switch dos jobs, ligadas nos Passos 10/11, quando houver URL pública).

| Propriedade | Como é garantida |
|---|---|
| Não entra no caminho crítico | `registrar_evento` só enfileira (`put_nowait`); um worker daemon grava fora da requisição. Fila cheia **descarta** — perder métrica é aceitável, atrasar a predição do funcionário não |
| Falha não derruba nada | Nenhuma exceção de gravação escapa do módulo (mesma filosofia do `ErroEnvioInfobip`). Destino fora do ar pausa as tentativas por 60s em vez de pagar timeout por evento |
| Nunca carrega PII | `detalhe` (jsonb) só aceita chaves de uma allowlist fechada: contadores, rota, status HTTP e *nome de classe* de exceção — nunca a mensagem, que no caso da Infobip ecoa o telefone do paciente |
| Mede o caminho que importa | Os três caminhos de predição são instrumentados (`origem` = `api`/`processo`/`job`). Medir só `/predict` daria o p95 de uma rota que, por decisão do [ADR-005](docs/adr/adr-005-integracoes-implicitas.md) (b), quase não recebe tráfego |

`/health` fica **fora** da instrumentação de propósito: a sonda externa bate nela de minutos em minutos, e registrar cada batida encheria a tabela de ruído. Retenção (`OBSERVABILIDADE_RETENCAO_DIAS`, default 90 dias) é aplicada por uma purga que roda junto do job diário, sem agendador novo. `OBSERVABILIDADE_ATIVA=false` desliga o registro por completo.

> **Medição achada na verificação**: a primeira predição de um processo custa ~1,4s (montagem do `TreeExplainer` do SHAP) e as seguintes ~10ms. Dentro do SLO §2 (<2s), mas apertado — e num Space que hiberna, **toda** predição depois de acordar paga esse custo. É exatamente o tipo de número que o Passo 12 precisa ter medido, não estimado.

Rodar os testes (os de round-trip exigem `supabase start`, mesma convenção de `test_db.py`):

```powershell
pytest tests/test_observabilidade.py -v
pytest -m integracao tests/test_observabilidade.py -v
```

## Blindagem LGPD (Passo 8)

Dados de saúde são categoria especial (Art. 5º, II e Art. 11 da LGPD) e o BRIEFING manda tratá-los como reais desde o protótipo. [`docs/LGPD.md`](docs/LGPD.md) é o documento de referência: papéis (a clínica é **controladora**, a SaúdeJá é **operadora**), inventário do que é tratado, base legal, retenção, transferência internacional, direitos do titular, incidentes e — explicitamente — os riscos residuais que vão para o pitch.

O requisito operacional é um: **"Sem PII em logs. Nunca."** (BRIEFING; 0 ocorrências no SLO §6). Ele é garantido de forma **preventiva**, não por auditoria posterior — o log de runtime do HF Space é efêmero (restart apaga, sem busca, sem retenção), então no dia do pitch não haverá log de produção para varrer ([ADR-006](docs/adr/adr-006-observabilidade.md)).

`src/logging_config.py` instala o log estruturado e o filtro de redação. Duas decisões explicam a forma dele:

- **O filtro mora no handler, e é aplicado a todo handler do processo.** O log que existe em volume em produção não é o nosso: é o de acesso (com IP de cliente) e o de erro do `uvicorn`, do `httpx` e do `streamlit`. O `uvicorn` põe `propagate=False` nos loggers dele, então nem o handler da raiz os alcançaria — `configurar_logging()` passeia pelos handlers já instalados e blinda cada um.
- **Duas famílias de regra, porque PII tem duas naturezas.** CPF, telefone, e-mail e IP têm forma reconhecível. **Nome não tem** — "Ana Souza" é indistinguível de qualquer par de palavras —, então o que se reconhece é a chave que o anuncia (`nome=`, `"nome":`, `extra={"nome": ...}`).

Chamado uma vez por entrypoint: `lifespan` da API, `main()` do job e o topo de `src/ui/app.py`. Os scripts do pipeline de treino seguem com `print` de propósito (rodam sobre dataset pseudonimizado, saída lida por humano).

```powershell
$env:LOG_FORMATO="texto"   # legível no terminal; a redação continua valendo
$env:LOG_LEVEL="DEBUG"
```

Varredura de log (ferramenta de verificação local, **não** a evidência principal):

```powershell
python scripts/auditoria_lgpd.py caminho/do/log.txt   # ou um diretório
docker compose logs app | python scripts/auditoria_lgpd.py -
```

Código de saída 1 se achar algo, para pendurar num smoke test sem ninguém precisar ler a saída. O relatório mostra arquivo, linha, regra e um excerto **mascarado** — nunca o valor encontrado, senão a auditoria é o vazamento com outro nome. As regras são as mesmas do filtro (`logging_config.REGRAS_DE_PII`), não uma segunda cópia.

A prova automatizada fica em dois lugares:

| Onde | O que garante |
|---|---|
| `tests/test_logging_lgpd.py` | A redação pega as quatro formas de PII do produto, **não** estraga os identificadores pseudonimizados de que o diagnóstico depende (`id_agendamento`, hash de CPF, `model_version`, `duracao_ms`), alcança handler de terceiro instalado antes, redige traceback preservando a classe da exceção, e o ciclo fecha: a saída do handler configurado, varrida pelo script, dá zero achado |
| `tests/test_coerencia_repo.py::test_nenhuma_chamada_de_log_em_src_referencia_campo_de_pii` | Percorre a AST de todo `src/*.py` e falha se alguma chamada de log referenciar campo proibido — por variável, atributo, índice ou chave de `extra`. Tem controle negativo próprio, porque varredura que deixa de reconhecer as chamadas daria verde sem verificar nada |

**Retenção** (`RETENCAO_DADOS_DERIVADOS_DIAS`, default 365): `predicoes` e `mensagens_disparadas` são purgadas pelo job diário, junto da purga de `eventos_app` que já existia — sem agendador novo. `pacientes`/`agendamentos` ficam de fora de propósito: o registro do atendimento é do controlador, e apagá-lo por conta própria seria a operadora decidindo sobre dado que não é dela ([`docs/LGPD.md`](docs/LGPD.md) §5).

> **Correção que veio junto**: `ErroEnvioInfobip` embutia o corpo da resposta da Infobip na mensagem — e a Infobip ecoa o payload enviado, **com o telefone do paciente**. Essa string ia para `resultado["erros"]` do job e era exibida crua na aba de dev. Agora a mensagem carrega (status HTTP, `messageId`), que é o que identifica a causa; o corpo completo fica em `exc.corpo_bruto`, que não deve ser logado nem exibido.

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

## CI/CD (GitHub Actions, Passo 10)

| Workflow | Quando | O que faz |
|---|---|---|
| `ci.yml` | PR para `dev`/`main`; chamado pelo deploy; `workflow_dispatch` | `ruff` → `mypy` → `dvc pull data/model.pkl` → `pytest`; build da imagem de deploy com smoke e varredura de PII no log; integração (Supabase local + pipeline de treino no container) em PR que toque banco/job/export e sempre em `main` |
| `deploy.yml` | push em `main` (exceto `docs/**`); `workflow_dispatch` | CI → guarda do campeão → staging com lista fechada → `supabase db push` → sync para o HF Space |
| `job_d2.yml` | todo dia às 08h17 de São Paulo; `workflow_dispatch` | job D-2 no runner, com pré e pós-checagem e Healthchecks |
| `retrain.yml` | dia 1 às 03h17 de São Paulo; `workflow_dispatch` | `validate_data` → treino → gate (regressão, piso de `roc_auc`, suíte de sanidade) → PR de promoção com o CI disparado nele |
| `dependency-review.yml` | PR | dependência vulnerável bloqueia o PR |

**O deploy nunca sobe o checkout**: monta `build/space/` só com o que a imagem precisa (Dockerfile, README do Space, `requirements/{base,api,ui}.txt`, `src/`, `params.yaml`, `entrypoint.sh`, `data/model.pkl`) e sincroniza esse diretório. Subir o repositório levaria a URL do remote do DVC e o dataset de treino para um Space público.

**Só o campeão vai para produção**: o deploy e o job D-2 conferem, com `python src/campeao.py`, que o sha do `model.pkl` e o `decision.threshold` batem com `data/champion_metrics.json`. Mudar o modelo ou o threshold sem passar pelo gate de re-treino reprova os dois.

Secrets e variáveis que os workflows esperam (documentados em `.env.example`, nunca com valor):

| Nome | Tipo | Usado por |
|---|---|---|
| `DVC_REMOTE_URL` | secret | todos |
| `AZURE_STORAGE_CONNECTION_STRING_LEITURA` | secret (SAS `rl` do container) | `ci`, `deploy`, `job_d2` |
| `AZURE_STORAGE_CONNECTION_STRING` | secret (escrita) | só `retrain` |
| `SUPABASE_URL`, `SUPABASE_SECRET_KEY` | secret | `job_d2`, `retrain` |
| `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`, `SUPABASE_PROJECT_REF` | secret | `deploy` |
| `HF_TOKEN` (fine-grained) e `HF_SPACE_ID` | secret e variável do environment `production` | `deploy` |
| `MESSAGING_PROVIDER`, `INFOBIP_REMETENTE` | variável | `job_d2` (falha se `MESSAGING_PROVIDER` não estiver definido) |
| `INFOBIP_BASE_URL`, `INFOBIP_CHAVE_API` | secret | `job_d2` |
| `HEALTHCHECKS_JOB_D2_URL`, `HEALTHCHECKS_RETRAIN_URL` | secret | `job_d2`, `retrain` |

Para validar os workflows localmente: `docker run --rm -v "${PWD}:/repo" -w /repo rhysd/actionlint:latest`.

### Como consultar a avaliação de um re-treino

O MLflow do re-treino sobe e morre junto com o run do workflow, então **não guarda histórico**. A avaliação de cada ciclo fica em quatro lugares:

| Onde | Quais ciclos | O que tem |
|---|---|---|
| **Resumo do run** (Actions → "Re-treino mensal" → run → *Summary*) | todos: promovido, bloqueado, sem dado novo, pipeline falhou | export (linhas de produção, linhas fora do contrato, completude de rótulo); campeão × desafiante com delta e tolerância; taxa de disparo projetada; avisos da suíte de sanidade; validação de dados (bloqueios, PSI, fora do domínio) |
| **Artifact `gate-report.json`** | todos | o mesmo em JSON, com todas as métricas do desafiante e o relatório do `validate_data` |
| **PR de promoção** | só os promovidos | métricas, taxa de disparo, avisos e link para o run |
| **`data/champion_metrics.json` no git** | só os promovidos | histórico permanente de cada campeão: métricas, `model_version`, threshold, md5 do dataset, positivos no fold de teste, motivo |

```powershell
# runs do re-treino e o comparativo de um deles
gh run list -w retrain.yml -R VL-in/ai-factory-saudeja
gh run download <run-id> -n gate-report -R VL-in/ai-factory-saudeja

# histórico dos campeões promovidos
git log -p -- data/champion_metrics.json
```

O desempenho **em produção** fica no Supabase: cada linha de `predicoes` registra o `model_version` que a fez, e a aba **Observabilidade** e o resumo do `job_d2.yml` mostram a quarentena e a taxa de disparo reais de cada dia.

**Limites conhecidos:**
- A avaliação de um desafiante **bloqueado** só existe no artifact, que expira (90 dias, o padrão do GitHub). Depois disso ela se perde.
- Não há visão de tendência entre meses: para comparar, é preciso baixar os JSONs um a um.
- Não há medida de precision/recall **reais** por `model_version`, cruzando `predicoes` com o desfecho registrado. Quando houver, ela precisa separar os pacientes que receberam lembrete (`lembrete_enviado`), porque o SMS muda o desfecho.

## Roadmap

- [x] Etapa 1: adoção do protótipo
- [x] Etapa 2: escolha da stack (ADR-001)
- [x] Etapa 3: arquitetura C4 + ADR-002 + repositório
- [ ] Etapa 4: deploy manual
- [ ] Etapa 5: CI/CD
- (etc.)

## Autor

Vanessa Hoysan Lin