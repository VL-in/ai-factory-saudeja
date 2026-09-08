# Changelog

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
