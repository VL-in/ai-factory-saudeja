# Changelog

## [v0.9] (Vanessa) - 2026-09-15
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
