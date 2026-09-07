# Changelog

## [v0.7] (Vanessa) - 2026-09-07
- Adicionada suíte de testes automatizados (pytest):
  - `test_train.py`: testes unitários de `carregar_dados`, `preprocessar` e `treinar`, incluindo teste que documenta o risco de especialidade desconhecida virar NaN silencioso.
  - `test_coerencia_repo.py`: valida consistência entre `.gitignore`, arquivos rastreados pelo git, `dvc.yaml` e `dvc.lock`.
  - `test_pipeline_dvc_integracao.py`: teste de integração opcional (pulado se Docker indisponível) que roda o pipeline completo via Docker e valida os artefatos gerados.
  - Adicionadas dependências `pytest` e `pyyaml` ao `requirements.txt`.

## [v0.6] (Vanessa) - 2026-09-07
- Implementação do DVC e MLFlow para rastreabilidade do conjunto de dados e treinamento.

## [v0.5] (Camila)
- Notebook completo com EDA + treino LightGBM
- Modelo serializado em model.pkl
- Acurácia 78%, F1 da classe positiva 0.65

## [v0.4] (Camila)
- Adicionada feature distancia_km

## [v0.3] (Camila)
- Primeira versão LightGBM substituindo regressão logística
