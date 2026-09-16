# SaudeJa — Classificador de no-show em agendamentos médicos

> Status: em desenvolvimento — (AI Factory: Build, Deploy and Showcase)

## Visão Geral

SaudeJá é um SaaS voltado para clínicas particulares de saúde de médio e pequeno porte que oferece serviço de agendamento online, prontuário, faturamento e comunicação com pacientes. Atualmente visa desenvolver uma funcionalidade para prevê pacientes que se ausenta de agendamentos médicos usando o classificador LightGBM. O repositório foi herdado com um pipeline de treinamento que resulta em um modelo com desempenho de Acurácia 78% no test set, F1-score para classe positiva de 0,65 e ROC-AUC de 0,81, dentro da disciplina AI Factory: Build, Deploy and Showcase.


## Problema

O no-show custa em média **R$ 180 por consulta perdida** e a taxa nacional gira em torno de 25-35%. Para uma clínica média (1.500 consultas/mês), isso significa R$ 70k-100k de receita evaporando todo mês. A primeira tentativa de reduzir o no-show foi de envio de lembrete para todos os pacientes, o que resultou em alto custo, sendo insustentável para clínicas com orçamento menor. A proposta atual é treinamento de um algoritmo que classifica os pacientes com alto chance de não comparecimento e apenas disparar lembrete para estes, reduzindo, dessa forma, em até 70% o gasto com mensageria.

## Arquitetura

Veja o diagrama C4 nível 1 e 2 em [`docs/architecture.md`](docs/architecture.md).
Decisões arquiteturais relevantes estão registradas em [`docs/adr/`](docs/adr/).

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

### Fluxo de treino/ajuste do modelo

1. Se o dataset (`data/consultas-historicas.csv`) mudou — novas consultas, correção de registros etc. —, atualize o arquivo e rode `dvc add data/consultas-historicas.csv` para gerar um novo hash e atualizar o `.dvc` correspondente. Pule este passo se só o código/hiperparâmetros mudaram.
2. Altere o que for preciso (ex.: `src/preprocess.py`, `src/train.py`, `src/validate.py`, hiperparâmetros, `requirements.txt`) e rode `dvc repro` — o comando reexecuta apenas os stages afetados pela mudança (DVC detecta isso pelas `deps`/`params` de cada stage), atualiza `dvc.lock` e regenera `data/model.pkl`.
3. Confira os resultados no MLflow UI (`http://localhost:5000`, run com a tag `pipeline_arquitetura`) e no `dvc.lock` atualizado. O histórico de métricas fica só no MLflow -- não há `metrics.json` local para comparar.
4. Se o modelo/resultado for o esperado, faça commit do código alterado junto com `dvc.yaml`, `dvc.lock` e o `.dvc` do dataset, se houver, para deixar rastreável qual dado e qual código geraram qual modelo.
5. Rode `dvc push` para enviar os artefatos rastreados (dataset e/ou modelo) ao remote configurado em `.dvc/config` (hoje um caminho local, trocar por um remote persistente antes de usar em equipe).

> Evite `dvc add .` na raiz do repositório: além de conflitar com os `outs` já gerenciados pelos stages do pipeline, é um path com bug conhecido no Windows. Para rastrear um dataset novo, use `dvc add data/<arquivo>`.

O histórico dos runs fica registrado no MLflow remote, que roda no container Docker `saudeja-mlflow-server` (dados persistidos nos volumes `mlflow-db` e `mlflow-artifacts`).



## Roadmap

- [x] Etapa 1: adoção do protótipo
- [x] Etapa 2: escolha da stack (ADR-001)
- [ ] Etapa 3: arquitetura C4 + ADR-002 + repositório
- [ ] Etapa 4: deploy manual
- [ ] Etapa 5: CI/CD
- (etc.)

## Autor

Vanessa Hoysan Lin