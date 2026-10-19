# ADR-012: Observabilidade com Grafana Cloud e OpenTelemetry

## Status
Aceito — 2026-10-19. Registra a decisão de 2026-10-12, implementada da v2.2.0 (telemetria) à v2.5.0 (alertas e painéis como código, aplicados em produção em 2026-10-18).

Revê o [ADR-006](adr-006-observabilidade.md): reverte os descartes do **Grafana Cloud** e do **OpenTelemetry** e decide o destino das superfícies que ficaram sobrepostas. Do ADR-006 continuam valendo a camada interna (`eventos_app`), o Healthchecks.io, o princípio "o coletor não pode morar dentro daquilo que ele mede" e o Langfuse reservado ao LLM.

Região do stack (`sa-east-1`, São Paulo) e aceite do DPA da Grafana Labs confirmados pela autora em 2026-10-19.

## Contexto

O ADR-006 montou a observabilidade de aplicação em três camadas gratuitas: `eventos_app` no Supabase como fonte de verdade, uma sonda externa de disponibilidade e o Healthchecks.io como dead-man's-switch dos workflows agendados. Grafana Cloud e OpenTelemetry foram descartados. Um mês depois, com o produto em produção, uma análise de cobertura contra o branch `dev` (2026-10-12) mostrou o que esse desenho não alcançava:

| Pilar | O que faltava |
|---|---|
| Logs | O log do Space é efêmero e some no restart. A interface quase não logava: uma falha de banco virava mensagem amigável na tela, sem log nem evento. Nenhum campo ligava as linhas de uma mesma execução. Login sem trilha de falha |
| Métricas | Latência medida só na predição; fila do dia, desfecho, cadastro, login, Supabase e Infobip sem medida. Sem p99, e p50 só na aba. "Taxa" de erro era contagem absoluta. Falha de envio de SMS não virava erro. Custo da mensageria: nada |
| Tracing | Nenhum. Não havia como ligar os eventos `erro` e `predicao` de uma execução do job à linha `job_d2` nem ao run do Actions |
| Alertas | Sem criticidade: tudo chegava pelo mesmo e-mail de workflow falho. Erro da interface só aparecia no alerta diário, até 24 h depois. Ninguém avisava a clínica quando a fila não era processada |

Soma-se um fato operacional: a sonda de disponibilidade do ADR-006 (UptimeRobot) nunca foi criada, e de 2026-10-11 a 2026-10-17 o uptime não foi medido (emenda de 2026-10-17 do ADR-006).

### O que mudou nas objeções do ADR-006

| Objeção do ADR-006 | Situação em 2026-10-12 |
|---|---|
| Grafana Cloud "exige agente de push dentro do container" | O gateway OTLP do Grafana Cloud recebe direto do processo Python. Não há Alloy nem coletor no container |
| Grafana Cloud "mantém os dados fora do Brasil" | O stack é criado em `sa-east-1`, a mesma região do Supabase ([ADR-005](adr-005-integracoes-implicitas.md)) |
| Grafana Cloud é "desproporcional ao volume de uma fila diária" | A tabela acima é o custo de não ter: os buracos não são de volume, são de visibilidade. O plano gratuito cobre o volume de uma clínica com folga |
| OpenTelemetry "é instrumentação, não destino" | Passa a ter destino: Loki (logs), Mimir (métricas) e Tempo (traces) do mesmo stack |
| O trace "teria um span de profundidade" | O job D-2 tem etapas (`pre_checagem`, `canario`, `buscar_fila`, `predizer`, `gravar`, `enviar_lembrete` por agendamento, `purgar`, `sincronizar_treino`), e cada ação da interface chama o Supabase e a Infobip por `httpx`. Sem trace, uma lentidão no job não diz se veio do modelo, do banco ou do SMS |
| SDK e exporter "engordariam a imagem, piorando o cold start" | A sonda a cada 5 min mantém o Space acordado (emenda de 2026-10-11 do ADR-006), e o cold start quase não ocorre. Os pacotes são importados tarde e o módulo é no-op sem endpoint |

**Forças em jogo** (as do ADR-006 continuam):

- **Orçamento de US$ 100/mês**, reservado à mensageria. A observabilidade continua em US$ 0.
- **LGPD, dado de saúde.** Até aqui, os serviços externos recebiam só sinal binário (respondeu ou não, pingou ou não). O Grafana passa a receber **conteúdo**: linhas de log, atributos de métrica e spans. É a mudança de maior risco desta decisão.
- **Retenção de 14 dias no plano gratuito**, curta para um SLO mensal.
- **Limite de 10 mil séries ativas** no plano gratuito: cada valor distinto de atributo de métrica é uma série nova.
- **Uma mantenedora só**: cada superfície a mais é algo a ler e a manter.

## Decisão

Adotamos o **Grafana Cloud**, plano gratuito, com stack em `sa-east-1`, como destino de logs, métricas, traces, painéis, alertas e da sonda de disponibilidade, com instrumentação em **OpenTelemetry e envio OTLP direto** dos três processos (`saudeja-ui`, `saudeja-api`, `saudeja-job-d2`), sem coletor. `eventos_app` continua sendo a fonte de verdade de longo prazo.

### Divisão de papéis

| Camada | Onde | Retenção | Para quê |
|---|---|---|---|
| Fonte de verdade | `eventos_app` (Supabase) | 90 dias | SLO mensal, aba "Observabilidade", ligação com o trace por `id_execucao` |
| Agregados de longo prazo | views do schema `observabilidade` (Supabase), lidas pelo Grafana com o papel `grafana_leitura` | as das tabelas de origem | p50/p95/p99 mensais com `percentile_cont`, disparos e taxa de disparo, cobertura da explicação, custo do mês, tamanho do banco |
| Operacional | Loki, Mimir e Tempo (Grafana Cloud) | 14 dias | diagnóstico, painéis do dia a dia e alertas de curto prazo |
| Disponibilidade, de fora | Synthetic Monitoring (Grafana Cloud), sonda em São Paulo | 14 dias | SLO §1.1 (emenda de 2026-10-17 do ADR-006) |
| Dead-man's-switch | Healthchecks.io | — | workflow que nem chegou a rodar |

A retenção de 14 dias deixa de ser problema porque o Grafana não é a fonte de verdade: o que precisa durar um mês já está no Supabase, e o Grafana o lê por SQL. A conexão direta ao Postgres também resolve o percentil que o ADR-006 calculava em Python porque o PostgREST não expõe `percentile_cont`.

### Como o princípio do ADR-006 continua valendo

"O coletor não pode morar dentro daquilo que ele mede" vale para o **armazenamento e o alerta**, não para a emissão. O processo emite a telemetria, mas ela é guardada, lida e alertada no Grafana Cloud, que não cai com o Space. A sonda mora no Grafana e bate de fora. O Healthchecks mora fora do Grafana, e por isso continua: se o Grafana cair ou o workflow nem rodar, o silêncio do ping ainda avisa.

### Instrumentação (`src/telemetria.py`)

- **No-op por padrão.** Sem `OTEL_EXPORTER_OTLP_ENDPOINT`, com `OBSERVABILIDADE_ATIVA=false`, com `OTEL_SDK_DISABLED=true` ou sem os pacotes, nada sai do processo. É o estado do CI e da suíte de testes.
- **Telemetria quebrada degrada, não interrompe.** Os exportadores rodam em thread própria; Grafana fora do ar vira aviso no stdout, nunca erro ou latência na predição.
- **Um ponto de chamada, três destinos.** `observabilidade.medir` e `registrar_evento` gravam em `eventos_app`, registram a métrica e abrem o span na mesma chamada.
- **Catálogos fechados.** Métricas em `METRICAS`, atributos em `ATRIBUTOS_PERMITIDOS`, com cardinalidade fechada por causa do limite de séries. Identificadores (`id_agendamento`, `id_execucao`, `session.id`) vão só em span, por `IDENTIFICADORES_DE_SPAN`, nunca em métrica. Nome fora do catálogo é descartado em runtime e recusado por uma guarda AST sobre `src/`.
- **Status `recusado`** para recusa esperada (senha errada, formulário inválido, desfecho fora do prazo): conta no total e nunca no numerador da taxa de erro.
- **Dev e prod no mesmo stack**, separados pelo atributo de recurso `deployment.environment` (`APP_ENV`).

### Barreiras de LGPD

O Loki é a primeira cópia de log que não é efêmera, e o que entra lá fica 14 dias fora do Supabase. Por isso, quatro barreiras antes do envio e uma depois:

1. **Filtro de PII no handler do OTel.** O handler nasce com `FiltroRedacaoPII` (`src/logging_config.py`), e os pontos de entrada configuram a telemetria antes do logging, para o passeio de handlers também o alcançar.
2. **Span sanitizado na saída.** `_ExportadorSanitizado` reescreve todo span, inclusive os da instrumentação automática do FastAPI e do `httpx`: URL sem query string (a do PostgREST carrega filtros), sem IP, sem user agent, sem cabeçalho e sem a mensagem da exceção, só o nome da classe.
3. **Allowlist de `eventos_app`**, a mesma do ADR-006, repetida nas views SQL e guardada por um teste que compara as duas.
4. **`grafana_leitura` só lê views de agregados.** O papel nasce `NOLOGIN`, tem `select` só no schema `observabilidade` e nenhum acesso a `pacientes`, `agendamentos`, `predicoes` ou `treino`, conferido por teste de integração. O login e a senha são ligados à mão em cada ambiente e ficam só na fonte de dados do Grafana.
5. **Camada detectiva, depois do envio.** A regra P1 "Dado pessoal em log exportado ao Loki" varre o Loki com os padrões de `REGRAS_DE_PII`, e "Chave fora da allowlist em `eventos_app`" detecta bypass da allowlist. A regra do Loki foi testada de ponta a ponta em dev, com uma linha sintética enviada por OTLP sem passar pelo filtro.

O ADR-006 tinha trocado a verificação detectiva pela preventiva porque não havia log para varrer. Com o Loki há, e as duas passam a coexistir.

A Grafana Labs é operadora: recebe telemetria sem dado pessoal e lê agregados do banco. Região `sa-east-1` e DPA aceito. O acesso remoto do suporte dela é tratamento a partir do exterior; a entrada no inventário do [LGPD.md](../LGPD/LGPD.md) e na política de privacidade está pendente.

### Alertas por criticidade

Regras de alerta, rotas de notificação e painéis são código em `infra/grafana/`, aplicados por `scripts/grafana_provisionar.py` (`grafana.yml`, no fim de cada deploy em `main`). Os limites saem de `params.yaml` (blocos `observabilidade` e `custo`). Contact points, URL do stack, token e UIDs das fontes de dados ficam fora do git.

| Criticidade | Notificação | Exemplos |
|---|---|---|
| P1 crítico | plantão, repetido a cada hora até resolver | fila D-2 não processada no horário, fila inteira em quarentena, agendamento sem predição e sem lembrete, Space de produção fora por 2 sondas seguidas, dado pessoal no Loki, chave fora da allowlist |
| P2 alto | e-mail, a cada 24 h; com `area=modelo`, também à responsável pelo modelo | erro de predição, erro de banco na interface, falha de SMS, predição sem explicação, disparos fora de ±40% com a taxa de disparo junto, custo projetado acima de 80% do orçamento |
| P3 médio | resumo semanal às segundas | quarentena parcial, dado fora do domínio do treino, job acima de 10 min, p95 acima do indicador, leitura perto do teto, banco perto da cota gratuita |

Toda regra tem os rótulos `severidade`, `area` e `ambiente`; só as de produção notificam. Em dev, o provisionamento cria cópias que notificam só o contact point de dev.

### Superfícies redundantes

| Superfície | Decisão |
|---|---|
| `eventos_app` | **Fica**, como fonte de verdade de 90 dias |
| Aba "Observabilidade" do Streamlit | **Fica**, como visão rápida. O painel principal passa a ser o Grafana, que não cai junto com o Space |
| `alerta_observabilidade.yml` (alerta diário) | **Sai ao fim da paridade**, junto com o check `HEALTHCHECKS_ALERTA_URL`, quando as duas condições abaixo forem verdadeiras |
| Healthchecks.io | **Fica, com e-mail próprio.** A duplicidade com o Grafana é aceita: passar o Healthchecks a webhook no Grafana faria uma queda do Grafana silenciar também o dead-man's-switch |
| E-mail nativo do GitHub em run falho | **Fica.** É nativo e não tem custo de manutenção |
| Allowlist de `eventos_app` repetida em SQL | **Fica**, guardada pelo teste que a compara com a do Python |

**Condições para o alerta diário sair:**

1. **Duas semanas de paridade** desde a primeira aplicação em produção (2026-10-18), ou seja, não antes de 2026-11-01.
2. **Cada condição dele com equivalente no Grafana**, e um alerta real recebido em cada nível (P1, P2 e P3), não o botão "Test" do contact point:

   | Condição do alerta diário | Equivalente no Grafana |
   |---|---|
   | Cobertura da explicação abaixo de 100% | "Predição sem explicação SHAP" (P2) |
   | Evento com status `erro` | "Erro de predição", "Erro de banco na interface", "Falha de envio de SMS" (P2); "Fila D-2 inteira em quarentena" (P1); "Quarentena parcial ou dado fora do domínio" (P3) |
   | Nenhuma execução do job D-2 em 24 h | "Fila D-2 não processada até o horário-limite" (P1), além do Healthchecks |
   | Job D-2 acima de 10 min | "Job D-2 acima de 10 min" (P3) |
   | Banco ilegível | erro de execução das regras SQL (`execErrState: Error`) |

Se alguma condição não tiver equivalente no fim das duas semanas, o alerta diário fica até ela ter, e a saída é registrada em emenda a este ADR.

## Consequências

Pros:
- Os quatro pilares cobertos: logs que sobrevivem ao restart do Space, latência com p50/p95/p99 por ação e por origem, taxa de erro como razão, trace do job e de cada ação da interface, custo da mensageria e alerta por criticidade.
- Uma execução se segue de ponta a ponta: `id_execucao` (`gh-<run>-<tentativa>` no job, o trace na UI e na API) liga a linha de 90 dias em `eventos_app` ao trace e ao log de 14 dias.
- A clínica passa a ser avisada (P1) quando a fila não é processada, e o P3 não acorda ninguém.
- Custo continua US$ 0. A sonda consome cerca de 17,9 mil das 100 mil execuções mensais do Synthetic Monitoring no plano gratuito.
- Alertas e painéis versionados, revisáveis em PR e reaplicados a cada deploy.
- Lock-in baixo: como a instrumentação é OTel, trocar de backend (inclusive para um Grafana auto-hospedado) é trocar o endpoint, não o código.

Cons:
- **Conteúdo de log sai do Supabase.** O filtro de PII é a única barreira preventiva antes do Loki, e o nome do paciente só é redigido quando vem anunciado por uma chave. A regra P1 do Loki é a rede de segurança, mas detecta depois do envio, e a linha fica até 14 dias.
- Uma operadora a mais, empresa americana, com acesso remoto do suporte a partir do exterior. Ainda fora do LGPD.md e da política de privacidade.
- Dependência do plano gratuito: retenção, limite de séries e cota de execuções podem mudar. A regra P3 de cota cobre o banco; o uso do próprio Grafana ainda não é monitorado.
- Mais superfície durante a paridade: até o alerta diário sair, um mesmo problema pode chegar por GitHub, Healthchecks, alerta diário e Grafana.
- A quarentena parcial, que o alerta diário avisava no mesmo dia como falha de workflow, passa a P3, no resumo semanal. É coerente com a matriz (o paciente recebe o lembrete por exceção), mas é um aviso mais lento.
- `service.version` sai como `desconhecida` enquanto o deploy não passar `APP_VERSION` ao Space.
- O aumento de tamanho da imagem e do tempo de boot com os pacotes OTel não foi medido.
- Sem observabilidade do navegador: o Frontend Observability (Faro) exige script no `<head>`, que o Streamlit não deixa injetar.

O que esta decisão **impede**: tratar o Grafana como fonte de verdade. Nada que precise de mais de 14 dias pode depender de Loki, Mimir ou Tempo; tem de estar em `eventos_app` ou numa view do schema `observabilidade`. E nenhum atributo de métrica pode carregar identificador, sob pena de estourar o limite de séries e de levar dado de agendamento a um rótulo.

## Alternativas consideradas

- **Manter o desenho do ADR-006** (Supabase, aba e alerta diário): custo zero e nenhum dado fora do Supabase, mas deixa todos os buracos da tabela do Contexto, inclusive a falta de criticidade e o aviso à clínica. A sonda, que ele também previa, não tinha sido criada.

- **Grafana OSS auto-hospedado** (Grafana, Loki, Mimir e Tempo): manteria tudo sob controle da SaúdeJá, mas exige hospedar quatro serviços 24/7, e o projeto não tem onde. O Space hiberna, tem disco efêmero e é justamente o que se quer medir. O OnCall OSS, que faria o roteamento de plantão, foi arquivado. Fica como saída futura: com instrumentação OTel, a troca é de endpoint.

- **Azure Application Insights**: o projeto já usa o Azure (remote do DVC), e o Azure Monitor também aceita OpenTelemetry. Perde em dois pontos. O Grafana lê o Postgres do Supabase como fonte de dados e alerta sobre ela ao lado das métricas, e é dali que saem os números de longo prazo; no Azure, esses alertas exigiriam copiar os agregados para o Log Analytics. E, fora da cota de ingestão, regras de alerta e testes de disponibilidade são cobrados por unidade, o que tira a observabilidade do custo zero.

- **OpenTelemetry com coletor (Alloy) no container**: daria buffer, retry e amostragem fora do processo, mas é um processo a mais no Space, com memória e imagem maiores, que é exatamente a objeção do ADR-006. Com o volume de uma clínica, o exportador em lote do SDK basta.

- **SDK proprietário do backend em vez de OTel**: amarraria o código ao Grafana. O OTel custa o mesmo e deixa a troca barata.

- **UptimeRobot como sonda**: decidido no ADR-006 e nunca criado. O Synthetic Monitoring fica no mesmo stack dos alertas, com sonda em São Paulo e validação do corpo `ok`.

- **Healthchecks por webhook no Grafana**: um canal só, com a criticidade do Grafana, mas uma queda do Grafana silenciaria também o aviso pelo silêncio. Descartado (ver Superfícies redundantes).

- **Datadog, New Relic, Logfire e Sentry**: os motivos do ADR-006 continuam (custo, dados fora do Brasil, maturidade). O Sentry segue adiado: receberia stack trace e contexto, mais conteúdo do que o Loki recebe hoje.

- **Langfuse**: continua reservado ao tracing e à avaliação do assistente com LLM. O Grafana não o substitui; quando o assistente for integrado, a ação dele entra em `medir` como as outras (latência, erro, volume).
