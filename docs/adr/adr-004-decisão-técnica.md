# ADR-004: Decisão técnica - escolha de banco de dado, plataforma de host e deploy e disparo de mensageria

## Status
Aceito — 2026-09-18. Supersede parcialmente o [ADR-001](adr-001-stack.md) nos pontos de banco de dados, mensageria, gateway de LLM e plataforma de deploy (ver Status do ADR-001).

## Contexto
Definição de serviços e plataformas para start da construção da aplicação:

1 - Escolha de banco de dado relacional para armazenamento em grande escala de informações de cadastro, históricos de consultas e outros dados relevantes para clínicas particulares.
2- Escolha da interface que vai hospedar a aplicação.
3- Escolha da plataforma de deploy.
4- Escolha da plataforma de automação/provedor de mensageria.
5- Escolha da plataforma de gateway para LLM.
6- Escolha de comunicação do modelo de predição via API.

Orçamento previsto não deve estourar $100 dólares/mês.

## Decisão

Implementamos o Streamlit como interface UX da aplicação, o qual comunica com o Supabase para cadastro de informações. O modelo de predição é empacotado com FastAPI e, por meio de chamadas RESTful com o banco de dados e interface Streamlit, fará predição e retornará o resultado para Streamlit. A mensageria é disparado pela Infobip quando o paciente é classificado como potencial no-show.
O deploy da aplicação é feita no Hugging Face Space.

Observabilidade: o MLflow cobre o rastreamento de experimentos/métricas de ML — não é substituído nem duplicado por outra ferramenta. O Langfuse fica reservado só para tracing do LLM/TrueFoundry, implementado mais adiante, e não é bloqueante para o núcleo do produto. O disparo de lembretes é feito pelo job agendado (D-2, via GitHub Actions cron) chamando a Infobip diretamente, sem camada extra de automação visual — reduz peça móvel e custo de operação para um fluxo que é, na prática, uma chamada HTTP condicional.

## Consequências
Pros:
- Stack alinhado ao desenvolvimento com linguagem Python.
- Orçamento mensal de acordo com o previsto.


Cons:
- Verificar a região de armazenamento dos dados do Supabase - algumas regiões de server pode não atender ao LGPD. Pode considerar self host se a infraestrutura permitir.

## Alternativas consideradas
Notas de 1 a 5 e pesos de 1 a 5; 1 = mais desfavorável ou menos importante no escopo do projeto. O total de cada opção é a soma de nota × peso.

Para banco de dados, foram consideradas:
 - Neon: apesar de atender como PostgreSQL database, a curva de aprendizado parece maior: necessita configurar camada de API e auth separadamente. Scale-to-zero pareceu interessante para redução de custo, seria interessante avaliar quando estiver mais tempo. Descartado.
 - Firebase: estrutura de base de dados no formato documental (schemaless JSON); consultas são rasas e carece parte relacional (caracteristica desejável para banco de dados de clínicas); cold start varia dependendo da região; lock-in maior (Supabase possibilita self host, arquitetura portátil). Descartado.
 - ChromaDB + DuckDB seria uma alternativa interessante, mas a escalabilidade deixa a desejar. Descartado.

| Critério | Peso | Supabase | Neon | Firebase | ChromaDB + DuckDB |
|---|:---:|:---:|:---:|:---:|:---:|
| Custo mensal vs. orçamento | 5 | 4 | 5 | 3 | 3 |
| Curva de aprendizado vs. perfil | 4 | 4 | 3 | 2 | 2 |
| Escala vs. volume esperado | 4 | 4 | 5 | 4 | 1 |
| Lock-in vs. tolerância ao risco | 2 | 4 | 5 | 1 | 3 |
| Alinhamento com o projeto adotado | 5 | 5 | 4 | 1 | 2 |
| Cold start | 4 | 5 | 3 | 4 | 5 |
| Auth embutido | 3 | 5 | 2 | 4 | 1 |
| **Total ponderado (Σ nota × peso)** |    | **120** | **105** | **74** | **66** |

Para frontend, foram considerados:
 - Next.js/React: falta de conhecimento em JS e TypeScript; apesar de ser interessante em deixar o interface mais profissional, esta não seria a primeira opção para o projeto (num produto, talvez migraria para esta solução). Descartado.

| Critério | Peso | Streamlit | Next.js/React |
|---|:---:|:---:|:---:|
| Curva de aprendizado vs. perfil | 5 | 5 | 1 |
| Integração com o stack Python/ML | 4 | 5 | 3 |
| Custo e deploy no Hugging Face Space | 4 | 5 | 3 |
| UX/acabamento para as clínicas | 3 | 2 | 5 |
| Escala de usuários simultâneos | 2 | 2 | 5 |
| **Total ponderado (Σ nota × peso)** |    | **75** | **54** |


Para mensageria, foram considerados:
 - Zenvia: empresa brasileira, com conformidade a LGPD. Descartado por não ter plano gratuito (começa em 20 dolares por mês).

| Critério | Peso | Infobip | Zenvia |
|---|:---:|:---:|:---:|
| Custo mensal vs. orçamento | 5 | 4 | 2 |
| Conformidade LGPD e presença no Brasil | 4 | 4 | 5 |
| Integração com o stack (API REST, Python) | 4 | 5 | 4 |
| Entregabilidade de SMS no Brasil | 3 | 4 | 5 |
| **Total ponderado (Σ nota × peso)** |    | **68** | **61** |
