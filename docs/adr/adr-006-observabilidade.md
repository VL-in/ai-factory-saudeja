# ADR-006: Observabilidade de aplicação em produção

## Status
Aceito — 2026-09-20

## Contexto

O ADR-004 resolveu observabilidade de ML: o MLflow rastreia experimentos, métricas e versões de modelo, e não é duplicado por outra ferramenta. Ficou em aberto a **observabilidade da aplicação em produção** — saber se o serviço está no ar, quanto tempo leva uma predição, se o job diário rodou, e se algum dado pessoal vazou para log.
Até então a aplicação só rodava localmente, onde `docker compose logs` basta.

**Forças em jogo:**

- **SLO mensurável em seis frentes** ([SLO.md](../SLO.md)): §1 uptime ≥99% e 5xx <1%; §2 p95 <2s (e <10s com cold start); §3 qualidade do modelo; §4 cobertura de explicação 100%; §5 re-treino mensal executado; §6 zero PII em log.
- **Orçamento de US$100/mês** (BRIEFING.md), do qual a mensageria Infobip é o único custo variável real — observabilidade não pode consumir essa margem.
- **LGPD, categoria especial.** Dados de saúde. O [ADR-005](adr-005-integracoes-implicitas.md) já registrou a escolha do Supabase em `sa-east-1` justamente para não haver transferência internacional.
- **Volume baixo.** Uma fila diária por clínica — não há escala que justifique uma plataforma de telemetria.

### Cobertura nativa das plataformas já escolhidas

Levantamento feito antes de decidir, porque a pergunta natural é "o GitHub Actions e o HF Space já não resolvem?":

| Plataforma | O que oferece | Limite |
|---|---|---|
| GitHub Actions | Histórico de runs (status, duração, commit), logs por step (~90 dias), `$GITHUB_STEP_SUMMARY`, artifacts, API `/actions/runs` | Cobre bem o SLO §5. Nada sobre a aplicação em si. Notificação de falha só por e-mail |
| Hugging Face Space | Build logs, logs de runtime (stdout/stderr), status Running/Sleeping/Error, API de status | Logs de runtime são **efêmeros** — restart ou rebuild apaga, sem busca, sem retenção, sem agregação. Sem métrica de request, latência ou erro no tier free. Filesystem efêmero |

Dois riscos operacionais concretos que esse levantamento expôs:

1. **Workflow agendado do GitHub é desativado automaticamente após período prolongado de inatividade do repositório** — o re-treino mensal pode simplesmente parar de rodar sem ninguém ser notificado, violando o SLA §5 em silêncio.
2. **Log efêmero no Space torna o SLO §6 inverificável como escrito.** O texto do SLO prevê "auditoria/regex nos logs", mas no dia do pitch não haverá log algum para varrer.

## Decisão

Adotamos **três camadas gratuitas**, organizadas por um princípio único: **o coletor não pode morar dentro daquilo que ele mede.** Métrica coletada dentro do Space desaparece junto com o Space quando ele cai ou hiberna — inclusive a evidência de que caiu.

| Camada | Ferramenta | Cobre | Dado que trafega |
|---|---|---|---|
| Interna — fonte de verdade | **Supabase**, tabela `eventos_app`, exposta por uma aba "Observabilidade" no Streamlit | SLO §2 (latência), §4 (cobertura SHAP), §6 (auditoria), decisões de mensageria | Fica em `sa-east-1`, não sai do Brasil |
| Externa — sonda de disponibilidade | **UptimeRobot** (ou Better Stack) batendo em `/health` | SLO §1 (uptime, 5xx) | Só sinal up/down |
| Externa — dead-man's-switch | **Healthchecks.io**, pingado ao fim do job D-2 e do re-treino | SLO §5 (execução dos jobs agendados) | Só ping |

O dead-man's-switch é a peça que resolve o risco 1 acima: ele alerta **pelo silêncio**, não pela falha. Se o workflow for desativado ou simplesmente não rodar, não há nada "caído" para um monitor convencional observar — mas o ping que não chega denuncia.

**A garantia de zero-PII vira controle preventivo, não detectivo.** Em consequência do risco 2, o SLO §6 passa a ser garantido por teste automatizado sobre o código (no mesmo espírito do `tests/test_coerencia_repo.py`, que já bloqueia coluna proibida nas migrations), e não por varredura posterior de log. `scripts/auditoria_lgpd.py` (Passo 8) continua útil em execução local, mas deixa de ser a evidência principal.

**Alerta reaproveita `src/messaging`** (Passo 7), já construído para o gate de re-treino do Passo 9 — não entra canal de alerta novo.

**Custo total: US$0/mês**, preservando a margem inteira para a Infobip.

## Consequências

Pros:
- Nenhum dado de paciente sai do Brasil: os serviços externos recebem exclusivamente sinal binário (respondeu/não respondeu, pingou/não pingou). A observabilidade fica coerente com a postura de LGPD do resto do produto, em vez de virar a porta dos fundos por onde o dado sensível escapa — argumento defensável no pitch da Semana 16.
- A fonte de verdade sobrevive ao sleep e ao rebuild do Space, ao contrário do log nativo.
- Os números de medição de latência passam a ser consultáveis por SQL, não coletados à mão na véspera da apresentação.
- Zero peça de infraestrutura nova a manter: o Supabase já está de pé e o dashboard é mais uma aba do Streamlit já existente.

Cons:
- O dashboard é escrito à mão — não se ganha visualização pronta, drill-down nem correlação automática entre métricas.
- Não há alerta ativo sobre latência ou taxa de erro (só sobre disponibilidade e execução de job); degradação lenta de performance só aparece se alguém olhar a aba.
- Gravar evento a cada request adiciona latência ao caminho crítico — mitigável com escrita não bloqueante, mas é um custo real sobre a métrica do SLO §2, que é justamente o que se quer medir.
- Dependência de dois SaaS gratuitos para sondagem; se algum encerrar o tier free, é preciso substituir (risco baixo, troca barata — nenhum dos dois guarda estado que importe).
- A tabela `eventos_app` cresce indefinidamente se não houver política de retenção, e o free tier do Supabase tem teto de armazenamento compartilhado com os dados do produto.

O que esta decisão **impede**: tracing distribuído e correlação automática entre serviços. É um custo aceito conscientemente porque hoje não há "entre serviços" — a aplicação é um container único, com a UI chamando o modelo em processo ([ADR-005](adr-005-integracoes-implicitas.md) b).

## Emenda (2026-09-21) — o que a implementação do Passo 8.5 mudou na decisão

A decisão acima foi mantida; três pontos dela não sobreviveram ao contato com o código e ficam corrigidos aqui.

**1. A tabela sozinha não garante ausência de PII.** O texto dizia que a guarda existente (`tests/test_coerencia_repo.py`, grep por coluna proibida nas migrations) já cobria `eventos_app` e não precisaria ser estendida. Não cobre: aquela guarda lê *nome de coluna*, e `detalhe` é `jsonb` — qualquer chave cabe lá dentro sem que nenhum grep de schema perceba. O caso concreto que fecharia o buraco tarde demais é o `str(exc)` de uma falha da Infobip, que ecoa o payload enviado com o telefone do paciente. A garantia passou a ser uma **allowlist fechada** em `src/observabilidade.py` (contadores, rota, status HTTP e *nome de classe* de exceção; nunca mensagem de erro), aplicada antes de o evento entrar na fila, com teste de runtime e checagem estática sobre os call sites de `src/`.

**2. Medir só o middleware da API mediria o caminho errado.** O SLO §2 fala de latência de predição, mas o [ADR-005](adr-005-integracoes-implicitas.md) (b) já decidiu que UI e job chamam o modelo **em processo** — no Space, `/predict` pode ficar com tráfego perto de zero, e ainda está em aberto (Passo 11) se ela terá endereço público. Um p95 calculado sobre essa rota seria um número honesto sobre algo que ninguém usa. A instrumentação cobre os três caminhos (`api`, `processo`, `job`), separados pela coluna `origem`.

**3. `/health` não é instrumentada.** A sonda externa bate nela de minutos em minutos por desenho; registrar cada batida encheria a tabela de linhas que nada dizem sobre latência e consumiria o free tier que este ADR se comprometeu a não gastar. Disponibilidade continua sendo medida de fora, que é o princípio do documento.

Decisões menores tomadas junto, todas alinhadas ao critério de "nenhuma peça de infraestrutura nova": a **retenção** (risco listado nos Cons) roda como purga dentro do job diário, não em `pg_cron`; o **p95** é calculado em Python sobre a janela lida, porque o PostgREST não expõe `percentile_cont`, e a aba avisa quando a janela foi truncada em vez de publicar um percentil de um pedaço; o módulo importa `supabase-py` **tardiamente**, porque `requirements/api.txt` não o instala (ele mora em `ui.txt`, para não pesar no cold start) e um import no topo quebraria o boot da imagem enxuta da API — que agora simplesmente roda sem registrar eventos.

Fica também registrado o que **não** foi implementado aqui, por depender de URL pública: as duas camadas externas (UptimeRobot e Healthchecks.io) e o alerta por `src/messaging`. A camada interna, que é a que guarda estado, está completa.

## Alternativas consideradas

- **Langfuse** (proposto pelo ADR-001 como observabilidade geral): descartado para o núcleo. Seu modelo de dados é trace → span → **generation**, construído em torno de prompt, completion, tokens e custo por chamada de LLM. O núcleo do SaúdeJá é um LightGBM tabular: não há prompt nem token, e a instrumentação produziria spans vazios. Soma-se a isso que o Langfuse Cloud fica em EU/US (transferência internacional de dado de saúde) e que o self-host exige Postgres + ClickHouse + worker, fora do orçamento e do que cabe num Space. **Permanece reservado para futuro implementação de LLM**, onde é a ferramenta certa para o problema certo — conforme o ADR-004 já previa.

- **OpenTelemetry**: descartado. É instrumentação, não destino — padroniza *como* emitir telemetria, mas não armazena nem exibe nada, então adotá-lo deixaria a pergunta "onde eu vejo isso?" sem resposta e ainda exigiria escolher e sustentar um backend. Seu principal valor, evitar lock-in ao permitir trocar de backend sem reescrever instrumentação, é um seguro contra um risco que não existe aqui (não há backend para trocar). E seu superpoder — seguir uma requisição atravessando múltiplos serviços — não se aplica a um container único onde a UI chama o modelo em processo: o trace teria um span de profundidade. SDK, exporter e collector ainda engordariam a imagem, piorando o cold start que o SLO §2 já trata como risco. Reavaliar se a API se separar do Streamlit em serviços distintos, ou se um backend for adotado por outro motivo.

- **Grafana Cloud (Prometheus + Loki)**: tecnicamente a opção mais completa e com free tier real, incluindo alerting. Descartada por exigir agente de push dentro do container (o Space não aceita scrape de fora), o que aumenta a imagem e agrava o cold start do SLO §2, além de manter os dados fora do Brasil. Desproporcional ao volume de uma fila diária.

- **Datadog / New Relic**: descartados por custo — consumiriam o orçamento inteiro de US$100/mês que precisa sobrar para a mensageria.

- **Logfire (Pydantic)**: atraente pela afinidade com o stack (Pydantic + FastAPI já em uso), mas continua sendo SaaS fora do Brasil e é jovem demais para virar dependência de um produto que será apresentado como compromisso de SLA.

- **Sentry** (rastreamento de exceções): **não descartado — adiado**. Cobre um buraco real, já que hoje uma exceção não tratada no Space vira uma linha de log que evapora no próximo restart. Fica fora do escopo mínimo por ser o único candidato que receberia *conteúdo* (stack trace, contexto) e não apenas sinal, exigindo `send_default_pii=False` mais scrubbing explícito em `before_send`, e — com dado de saúde real — avaliação de transferência internacional e DPA. Com dados sintéticos é defensável; reavaliar após implementação do LLM.
