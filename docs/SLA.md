# SLA — SaúdeJá (Classificador de No-show)

> **Status:** rascunho inicial (v0.1), derivado dos SLOs internos em [SLO.md](SLO.md) e do [BRIEFING.md](BRIEFING.md). **Precisa de validação do time de Produto e, no que envolve mensageria, do fornecedor de WhatsApp/SMS**, antes de ser apresentado como compromisso formal às clínicas ou ao Conselho de Investidores (marco da Semana 16, BRIEFING.md).

Um Service Level Agreement (SLA) é o compromisso externo — o que a SaúdeJá promete às clínicas-clientes e ao próprio negócio. Ele é sustentado pelos SLOs técnicos internos documentados em [SLO.md](SLO.md); toda cláusula abaixo remete ao SLO correspondente.

## 1. Escopo

Este SLA cobre o serviço de **priorização de risco de no-show**: a API que recebe dados de um paciente/consulta e retorna probabilidade de falta, a interface de fila do dia ordenada por risco, e o disparo condicional de lembretes pagos (WhatsApp/SMS), conforme descrito no BRIEFING.md (itens 1–3) e no diagrama de [architecture.md](architecture.md).

Está **fora de escopo**: disponibilidade dos provedores externos de WhatsApp/SMS (esse SLA cobre apenas a decisão de *quando* disparar, não a entrega da mensagem em si).

## 2. Disponibilidade do serviço

- A API de predição de risco estará disponível **≥ 99.0% do tempo**, medido mensalmente (ver [SLO §1](SLO.md#1-disponibilidade-da-api-de-inferência)).
- Em caso de indisponibilidade, a clínica pode continuar a operação manual (fila não priorizada) sem bloqueio do fluxo de agendamento — o serviço de no-show é um **complemento**, não uma dependência crítica do core do sistema (BRIEFING.md: o produto principal já existe e funciona sem o classificador).

## 3. Tempo de resposta

- A fila de risco do dia deve estar disponível para consulta pela clínica com latência **p95 < 2 segundos** por predição (ver [SLO §2](SLO.md#2-latência)).
- Em caso de cold start de infraestrutura, o pior caso tolerado é **p95 < 10 segundos** — acima disso, considerar violação de SLA.

## 4. Qualidade da priorização

- O modelo em produção deve manter, a cada ciclo de re-treino mensal, **recall da classe "no-show" ≥ 0.75** (ver [SLO §3](SLO.md#3-qualidade-do-modelo-re-treino-mensal)) — ou seja, no mínimo 75% dos pacientes que de fato faltariam devem ser corretamente sinalizados como alto risco, para que a lógica de threshold (BRIEFING.md item 3) não deixe de acionar lembrete para quem faltaria.
- Nenhum re-treino mensal pode ser promovido a produção se suas métricas de qualidade regredirem em relação ao modelo vigente (gate de rollback, ver [SLO §3](SLO.md#3-qualidade-do-modelo-re-treino-mensal)).
- Toda predição exibida à clínica deve vir acompanhada de explicação (SHAP ou equivalente) — compromisso direto com a exigência da diretoria médica registrada nas notas herdadas da Camila, ver [SLO §4](SLO.md#4-explicabilidade).

## 5. Continuidade e re-treino

- O modelo será re-treinado **mensalmente**, de forma automatizada, conforme acordado com o time de Produto (BRIEFING.md). Atrasos além de 45 dias desde o último re-treino bem-sucedido configuram violação deste SLA.

## 6. Privacidade e conformidade (LGPD)

- Nenhum dado pessoal identificável (nome, CPF) será registrado em logs de aplicação, em nenhuma circunstância (BRIEFING.md: "Sem PII em logs. Nunca."; ver [SLO §6](SLO.md#6-privacidade--lgpd-não-funcional-mas-mensurável)).
- Dados de saúde são tratados como **categoria especial** (Art. 5º, II e Art. 11 da LGPD) mesmo durante a fase de protótipo com dados sintéticos (`data/AVISO-DADOS-SINTETICOS.md`), com criptografia em repouso e base legal documentada.
- Qualquer incidente de exposição de dados sensíveis é reportado à clínica-cliente e ao DPO em até 72h, alinhado às boas práticas de resposta a incidentes da LGPD.

## 7. Limites e exclusões conhecidas (estágio de protótipo)

Estes limites devem ser comunicados explicitamente às partes interessadas — não são falhas ocultas:

- O modelo atual foi treinado sobre um dataset **sintético e pequeno** (~400 consultas) — os alvos de qualidade acima serão revalidados assim que houver dados reais de produção.
- O orçamento de infraestrutura é de **US$ 100/mês** (BRIEFING.md) — isso limita a disponibilidade prometida (item 2) e exclui redundância multi-região.
- A entrega efetiva de WhatsApp/SMS depende de fornecedores terceiros e está fora do escopo deste SLA (ver item 1).

## 8. Revisão e apresentação

Este SLA será apresentado, com números validados e riscos de LGPD explicitados, no pitch ao Conselho de Investidores na Semana 16 (BRIEFING.md, item 4). Deve ser revisado sempre que o [SLO.md](SLO.md) ou a arquitetura (`architecture.md`) mudarem.

Última geração: 2026-09-07.
