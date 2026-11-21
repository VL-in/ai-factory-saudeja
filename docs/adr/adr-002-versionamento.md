# ADR-002: Versionamento e rastreabilidade do pipeline de pre-processamento e treino de modelo

## Status
Aceito

## Contexto
É necessário implementar pipeline de versionamento e rastreabilidade de pre-processamento e treino de modelo.
Adendo (10/11/2026): foi detectado inconsistências de valores de desempenho descritas nos documentos herdados (BRIEFING.md e notebook.ipynb). Os valores documentados e obtidos ao rodar o modelo estão descritas a seguir:


|Métrica (classe positiva, 76 amostras de teste)| Textos | Notebook |
|--------|-------|-------|
|Acurácia|	78%|	65,8%|
|F1|0.65|0.235|
|Precisão|	não citada |	0.308|
|Recall|	não citado |	0.190 (4 de 21 no-shows detectados)|
|ROC-AUC|	0.81|	0.549, perto do acaso (0.5)|

Em decorrência de impossibilidade de rastreio do dado histórico de desempenho para averiguar a veracidade dos dados, foi adotado uso de stack de versionamento para evitar futuras ocorrências que impede rastreabilidade e auditoria dos dados e modelos.

## Decisão
Usar DVC + MLFlow + Docker + Git.

## Consequências
Pros:
    - Garante rastrabilidade e auditoria futura do código.
    - Implementar conhecimento adquirido da disciplina MLOps.
    - Pipeline gratuíta.

Cons:
    - Não se sabe se afeta a escolha da plataforma de deploy.


## Alternativas consideradas
As outras opções pagas foram descartadas pela não previsão de gasto com isso por enquanto. As opções escolhidas são familiares para a estudante e servem para aprofundar o uso dessas ferramentas.
