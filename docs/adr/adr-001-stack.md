# ADR-001: Adoção de Stacks para o classificador no-show do Saúde Já

## Status
Parcialmente substituído por [ADR-004](adr-004-decisão-técnica.md) — 2026-09-18.

As decisões abaixo foram **substituídas** pelo ADR-004, que resolveu com escolha concreta o que aqui ainda estava em aberto (Supabase _ou_ ChromaDB+DuckDB, n8n para mensageria, gateway de LLM não escolhido, plataforma de deploy não escolhida entre as opções listadas):
- Banco de dados: ADR-001 deixava em aberto Supabase vs. ChromaDB+DuckDB → ADR-004 decide **Supabase** (via SDK `supabase-py`).
- Mensageria: ADR-001 apontava **n8n** → ADR-004 decide **Infobip** direto (n8n descartado, ver decisão de observabilidade no próprio ADR-004).
- Gateway de LLM: não decidido aqui → ADR-004 decide **TrueFoundry**.
- Plataforma de deploy: ADR-001 deixava em aberto HF Spaces/HF Inference Endpoints/Modal → ADR-004 decide **Hugging Face Space** (SDK Docker).

Seguem **válidas** (não contestadas pelo ADR-004): Python-first, GitHub+DVC+MLflow para versionamento/rastreabilidade, Streamlit como frontend, FastAPI para as chamadas RESTful do modelo.

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