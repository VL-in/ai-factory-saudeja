# ADR-001: Adoção de Stacks para o classificador no-show do Saúde Já

## Status
Proposto - 2026-09-06

## Contexto
Qual é o problema que estamos resolvendo? Qual é o estado atual?
Por que precisamos decidir agora? Quais são as forças em jogo
(prazo, custo, time, compliance etc.)?

O projeto herdado possui apenas o script de EDA e treinamento que resulta em um modelo pickle, a partir do notebook Jupyter. É preciso escolher os stacks que melhor atendem os SLOs descritos em \docs\SLO.md e a arquitetura descrita em \docs\architecture.md. Consideraremos alternativas de stack envolvendo Python-first ou Typescript-first. O orçamento mensal deve caber dentro de USD $100. O projeto atual está alinhado com o propósito da aluna, que busca vaga de ML engineer (e soluções agenticas, mas em outros projetos desenvolvidos) na área de biotecnologia.

## Decisão
Adoção do stack Python-first, usando GitHub, DVC e MLFlow para versionamento e rastrabilidade, Streamlit como frontend, Supabase ou ChromaDB + DuckDB como banco de dado, FastAPI para chamadas RESTful, n8n para disparo de mensageria, HuggingFace Spaces / Hugging Face Inference Endpoints ou Modal para deployment e Langfuse para observabilidade.

## Consequências
Pros:
    - Stack Python-first para refinamento do modelo (melhorar o F1).
    - A equipe possui mais experiência em Python.
    - Streamlit, FastAPI, n8n (exceto disparo de mensagens) e Langfuse possuem custo zero.
    - Git, DVC e MLFlow garantem rastreabilidade com zero custo para desenvolvimento inicial do modelo.

Cons:
    - Caso exija integração da interface com o ecossistema do Saúde Já, exige adaptação da UI desenvolvida para Streamlit.
    - Coldstart da plataforma de deployment escolhida pode interferir na experiência do usuário.
    - Inexperiência em avaliar se o stack escolhido atende todos os SLOs (exemplo: treinamento/deploy automático de dados em escala grande).


## Alternativas consideradas
    - As stacks que não envolvem desenvolvimento de interface como produto foram descartadas por não estar alinhado com os SLOs acordado. 
    - O stack Typescript-first foi descartada por não contemplar a parte de treinamento de modelo.