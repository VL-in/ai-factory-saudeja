# SLA — SaúdeJá (Classificador de No-show)

> **Status:** rascunho inicial (v0.1), derivado dos SLOs internos em [SLO.md](SLO.md) e do [BRIEFING.md](BRIEFING.md). **Precisa de validação do time de Produto e, no que envolve mensageria, do fornecedor de WhatsApp/SMS**, antes de ser apresentado como compromisso formal às clínicas ou ao Conselho de Investidores (marco da Semana 16, BRIEFING.md).

Um Service Level Agreement (SLA) é o compromisso externo — o que a SaúdeJá promete às clínicas-clientes e ao próprio negócio. Ele é sustentado pelos SLOs técnicos internos documentados em [SLO.md](SLO.md); toda cláusula abaixo remete ao SLO correspondente.

## 1. Escopo

Este SLA cobre o serviço de **priorização de risco de no-show**: o processamento diário da fila (predição em batch dos agendamentos dos próximos dias), a interface de fila do dia ordenada por risco, e o disparo condicional de lembretes pagos (WhatsApp/SMS), conforme descrito no BRIEFING.md (itens 1–3) e no diagrama de [architecture.md](architecture.md).

Está **fora de escopo**: disponibilidade dos provedores externos de WhatsApp/SMS (esse SLA cobre apenas a decisão de *quando* disparar, não a entrega da mensagem em si).

## 2. Disponibilidade do serviço

- **A fila de amanhã até daqui a dois dias é processada todo dia até as 09h** (predição gravada, e lembrete pago para quem cruzou o threshold), ver [SLO §1](SLO.md#1-processamento-da-fila-d-2-batch). Revisado em 2026-09-30: a solução é de inferência em **batch**, e a API de predição saiu do compromisso de produção — o que a clínica percebe é a fila processada a tempo, não a resposta de uma chamada.
- A interface (fila do dia, cadastro) estará disponível **≥ 99.0% do tempo**, medido mensalmente (ver [SLO §1.1](SLO.md#11-disponibilidade-da-interface)).
- Em caso de indisponibilidade, a clínica pode continuar a operação manual (fila não priorizada) sem bloqueio do fluxo de agendamento — o serviço de no-show é um **complemento**, não uma dependência crítica do core do sistema (BRIEFING.md: o produto principal já existe e funciona sem o classificador).

## 3. Tempo de resposta

- A fila do dia mostra predições **já calculadas** pelo job diário: abrir a tela não dispara predição. O compromisso é de carga da interface, **< 10 segundos** mesmo depois de o Space hibernar (ver [SLO §2](SLO.md#2-latência)).

## 4. Qualidade da priorização

- O modelo em produção deve manter, a cada ciclo de re-treino mensal, **recall da classe "no-show" ≥ 0.75** (ver [SLO §3](SLO.md#3-qualidade-do-modelo-re-treino-mensal)) — ou seja, no mínimo 75% dos pacientes que de fato faltariam devem ser corretamente sinalizados como alto risco, para que a lógica de threshold (BRIEFING.md item 3) não deixe de acionar lembrete para quem faltaria.
- Nenhum re-treino mensal pode ser promovido a produção se suas métricas de qualidade regredirem em relação ao modelo vigente (gate de rollback, ver [SLO §3](SLO.md#3-qualidade-do-modelo-re-treino-mensal)).
- Toda predição exibida à clínica deve vir acompanhada de explicação (SHAP ou equivalente) — compromisso direto com a exigência da diretoria médica registrada nas notas herdadas da Camila, ver [SLO §4](SLO.md#4-explicabilidade).

## 5. Continuidade e re-treino

- O modelo será re-treinado **mensalmente**, de forma automatizada, conforme acordado com o time de Produto (BRIEFING.md). Atrasos além de 45 dias desde o último re-treino bem-sucedido configuram violação deste SLA.

## 6. Privacidade e conformidade (LGPD)

- Nenhum dado pessoal identificável (nome, CPF, e-mail, telefone, IP) será registrado em logs de aplicação, em nenhuma circunstância (BRIEFING.md: "Sem PII em logs. Nunca."; ver [SLO §6/§6.1](SLO.md#6-privacidade--lgpd-não-funcional-mas-mensurável)). A lista original desta cláusula dizia apenas "nome, CPF", de antes de `telefone` entrar no schema para o envio do lembrete — telefone é hoje o único identificador direto que o runtime manipula.
- Dados de saúde são tratados como **categoria especial** (Art. 5º, II e Art. 11 da LGPD) mesmo durante a fase de protótipo com dados sintéticos (`data/AVISO-DADOS-SINTETICOS.md`), com criptografia em repouso e base legal documentada — base legal, inventário, retenção e riscos residuais em [`LGPD.md`](LGPD.md).
- Na relação com a clínica-cliente, a SaúdeJá atua como **operadora** e a clínica como **controladora** (Art. 5º, VI e VII). Pedidos de titular (Art. 18) chegam pela clínica; ver [`LGPD.md` §1 e §6](LGPD.md).
- Qualquer incidente de exposição de dados sensíveis é reportado à clínica-cliente e ao DPO em até 72h, alinhado às boas práticas de resposta a incidentes da LGPD.

## 7. Limites e exclusões conhecidas (estágio de protótipo)

Estes limites devem ser comunicados explicitamente às partes interessadas — não são falhas ocultas:

- O modelo atual foi treinado sobre um dataset **sintético e pequeno** (~400 consultas) — os alvos de qualidade acima serão revalidados assim que houver dados reais de produção.
- O orçamento de infraestrutura é de **US$ 100/mês** (BRIEFING.md) — isso limita a disponibilidade prometida (item 2) e exclui redundância multi-região.
- A entrega efetiva de WhatsApp/SMS depende de fornecedores terceiros e está fora do escopo deste SLA (ver item 1).

## 8. Revisão e apresentação

Este SLA será apresentado, com números validados e riscos de LGPD explicitados, no pitch ao Conselho de Investidores na Semana 16 (BRIEFING.md, item 4). Deve ser revisado sempre que o [SLO.md](SLO.md) ou a arquitetura (`architecture.md`) mudarem.

Última geração: 2026-09-07; última revisão: 2026-09-30 (itens 2 e 3, compromisso do batch no lugar da API); revisão anterior: 2026-09-27 (item 6, escopo da PII em log e papéis controlador/operador).
