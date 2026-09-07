# SaudeJa — Classificador de no-show em agendamentos médicos

> Status: em desenvolvimento — (AI Factory: Build, Deploy and Showcase)

## Visão Geral

SaudeJá é um SaaS voltado para clínicas particulares de saúde de médio e pequeno porte que oferece serviço de agendamento online, prontuário, faturamento e comunicação com pacientes. Atualmente visa desenvolver uma funcionalidade para prevê pacientes que se ausenta de agendamentos médicos usando o classificador LightGBM. O repositório foi herdado com um pipeline de treinamento que resulta em um modelo com desempenho de Acurácia 78% no test set, F1-score para classe positiva de 0,65 e ROC-AUC de 0,81, dentro da disciplina AI Factory: Build, Deploy and Showcase.


## Problema

O no-show custa em média **R$ 180 por consulta perdida** e a taxa nacional gira em torno de 25-35%. Para uma clínica média (1.500 consultas/mês), isso significa R$ 70k-100k de receita evaporando todo mês. A primeira tentativa de reduzir o no-show foi de envio de lembrete para todos os pacientes, o que resultou em alto custo, sendo insustentável para clínicas com orçamento menor. A proposta atual é treinamento de um algoritmo que classifica os pacientes com alto chance de não comparecimento e apenas disparar lembrete para estes, reduzindo, dessa forma, em até 70% o gasto com mensageria.

## Arquitetura

Veja o diagrama C4 nível 1 e 2 em [`docs/architecture.md`](docs/architecture.md).
Decisões arquiteturais relevantes estão registradas em [`docs/adr/`](docs/adr/).

## Pipeline de treino (DVC)

O pipeline de treino é versionado com DVC e executado dentro de um container Docker (veja `dvc.yaml`). Fluxo de uso para quem for treinar/ajustar o modelo:

1. Se o dataset (`data/consultas-historicas.csv`) mudou — novas consultas, correção de registros etc. —, atualize o arquivo e rode `dvc add data/consultas-historicas.csv` para gerar um novo hash e atualizar o `.dvc` correspondente. Pule este passo se só o código/hiperparâmetros mudaram.
2. Altere o que for preciso (ex.: `src/train.py`, hiperparâmetros, `requirements.txt`) e rode `dvc repro` — o comando reexecuta o pipeline (build + run do container), atualiza `dvc.lock` e regenera `data/model.pkl` e `data/mlflow.db` a partir do dataset atual (novo ou não).
3. Confira os resultados do treino: métricas no MLflow (`data/mlflow.db`, ex. via `mlflow ui --backend-store-uri sqlite:///data/mlflow.db`) e o `dvc.lock` atualizado com os novos hashes.
4. Se o modelo/resultado for o esperado, faça commit do código alterado junto com `dvc.lock` e o `.dvc` do dataset, se houver (`git add dvc.lock data/consultas-historicas.csv.dvc <arquivos alterados>` e commit) para deixar rastreável qual dado e qual código geraram qual modelo.
5. Rode `dvc push` para enviar os artefatos rastreados (dataset e/ou modelo) ao remote configurado em `.dvc/config` (hoje um caminho local, trocar por um remote persistente antes de usar em equipe).

> Evite `dvc add .` na raiz do repositório: além de conflitar com os `outs` já gerenciados pelo stage `train`, é um path com bug conhecido no Windows. Para rastrear um dataset novo, use `dvc add data/<arquivo>`.

## Roadmap

- [x] Etapa 1: adoção do protótipo
- [x] Etapa 2: escolha da stack (ADR-001)
- [ ] Etapa 3: arquitetura C4 + ADR-002 + repositório
- [ ] Etapa 4: deploy manual
- [ ] Etapa 5: CI/CD
- (etc.)

## Autor

Vanessa Hoysan Lin