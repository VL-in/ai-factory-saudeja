# ADR-002: Versionamento e rastreabilidade do pipeline de pre-processamento e treino de modelo

## Status
Aceito

## Contexto
É necessário implementar pipeline de versionamento e rastreabilidade de pre-processamento e treino de modelo.

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
