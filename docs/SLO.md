# SLOs — SaúdeJá (Classificador de No-show)

> **Status:** rascunho inicial (v0.1), gerado a partir do [BRIEFING.md](BRIEFING.md), das notas herdadas da Camila (`docs/herdado/notas-camila.md`), do código em `src/train.py` / `src/notebook.ipynb` e do [ADR-001](adr/adr-001-stack.md), que já referenciava este documento antes de ele existir. **Os alvos numéricos abaixo precisam ser validados com o time de Produto antes de virarem compromisso** — aqui eles foram inferidos das restrições conhecidas (orçamento, dataset pequeno, estágio de protótipo), não de um SLA já negociado com clínicas.

Service Level Objectives (SLOs) são as metas técnicas internas, mensuráveis, que sustentam os compromissos do [SLA.md](SLA.md). Cobrem o ciclo completo descrito no diagrama de [architecture.md](architecture.md): API de inferência, pipeline de re-treino mensal e disparo de lembretes.

## 1. Processamento da fila D-2 (batch)

> **Reescrito em 2026-09-30.** As versões anteriores das §1/§2 mediam a API `/predict` (uptime e p95 de latência). A revisão do CI/CD (2026-09-29) fixou a premissa de que a solução é de **inferência em batch**: o produto depende do job diário, do registro do desfecho pela atendente e do re-treino mensal — nada em tempo real. A API continua no repositório para cumprir o item 1 do BRIEFING, mas **saiu do caminho crítico e do compromisso de produção**. O que a clínica percebe é se a fila de amanhã foi processada a tempo, e é isso que passa a ser medido.

| Métrica | Alvo | Observação |
|---|---|---|
| Fila [amanhã, D+2] processada | **100%** dos agendamentos pendentes, **até as 09h** de São Paulo, todo dia | "Processado" = predito **ou** em quarentena com lembrete enviado como exceção (`enviado_sem_predicao`). O job roda às 08h17 (`.github/workflows/job_d2.yml`); a folga cobre o atraso que o GitHub aplica a eventos agendados. |
| Execuções do job D-2 | 1 por dia | A janela de três dias recupera sozinha um dia perdido; dois dias seguidos sem execução deixam a fila de amanhã sem lembrete. |
| Contabilidade da fila | predições = pendentes − quarentena, em 100% das execuções | Pós-checagem do próprio job (`inferencia_diaria.pos_checagem`): divergência **falha** o workflow. |
| Quarentena | 0 no dia típico; **nunca 100%** da fila | Quarentena parcial vira aviso no run. Fila inteira em quarentena é defeito sistêmico — falha o workflow e aciona o `/fail` do Healthchecks, porque, pela regra do lembrete sem predição, a fila inteira recebeu SMS. |

**De onde vem o número**: `eventos_app` (`tipo = job_d2`: horário, `agendamentos_encontrados`, `predicoes_gravadas`, `quarentena`) é a fonte de verdade; o histórico de runs do Actions e o Healthchecks.io são a prova externa de que o job rodou — inclusive do dia em que não rodou, que é o que o coletor interno não consegue registrar ([ADR-006](adr/adr-006-observabilidade.md)).

### 1.1 Disponibilidade da interface

| Métrica | Alvo | Observação |
|---|---|---|
| Uptime mensal da interface (fila do dia, cadastro) | ≥ 99.0% | Sonda externa no Space de produção (Synthetic Monitoring do Grafana Cloud, sonda em São Paulo, em `/_stcore/health` a cada 5 min, esperando status 2xx e corpo `ok` — `/health` é a API, que não é pública; ver as emendas de 2026-10-11 e 2026-10-17 no [ADR-006](adr/adr-006-observabilidade.md)). Medida desde 2026-10-17; antes disso não houve sonda. Orçamento de US$ 100/mês (BRIEFING.md) exclui redundância; o Space free hiberna, e o cold start é risco conhecido. |

## 2. Latência

| Métrica | Alvo | Observação |
|---|---|---|
| Duração do job D-2 | termina antes das 09h (≈ 40 min a partir do disparo) | Folga larga: o job processa uma fila diária de uma clínica em segundos; o que ocupa a janela é o atraso do cron do GitHub. |
| Carga da interface depois de hibernar (cold start) | < 10s | Medido no Space publicado, antes do pitch. A fila do dia lê predições **já gravadas** pelo job — nenhuma predição acontece na hora em que a atendente abre a tela. |

**Fora do compromisso de produção** (decisão de 2026-09-29): o p95 de `/predict` e da aba "Testar predição". Continuam medidos em `eventos_app` (`origem` = `api`/`processo`) como indicador de desenvolvimento — a medição de 2026-09-21 (~1,4s na primeira predição de um processo, ~10ms nas seguintes) segue valendo como referência.

## 3. Qualidade do modelo (re-treino mensal)

Baseline herdado (`docs/logs/CHANGELOG.md`, v0.5): acurácia 78%, F1 da classe positiva (no-show=1) 0.65, split 80/20 estratificado sobre ~400 linhas sintéticas.

**Alvos revisados em 2026-11-22.** Os alvos anteriores (`recall_1` ≥ 0,75 e `f1_1` ≥ 0,65) partiam do F1 0,65 herdado, que não se reproduziu: rodado de novo, o notebook dava 0,235 ([ADR-003](adr/adr-003-SMOTE-NC.md)). Além disso, os dois são medidos no threshold de 0,6, que o modelo calibrado quase nunca alcança, e por isso ficam em 0 no campeão vigente. Os alvos abaixo medem o que a regra por orçamento ([ADR-016](adr/adr-016-regra-de-decisao-por-orcamento.md)) usa: quem entra nos 30% avisados. São medidos em validação cruzada 5 × 5, com intervalo de 95% (`scripts/avaliar_cv.py`), porque o fold de teste isolado tem poucas faltas e um paciente move o recall em vários pontos.

| Métrica | Alvo | Campeão vigente (`07bcfede25ea`), CV 5 × 5 em 2026-11-22 (390 linhas) | Observação |
|---|---|---|---|
| ROC-AUC | média ≥ 0,60 | 0,609 [0,526; 0,691]: **atende na média**, o limite inferior não | Mesmo valor do piso absoluto do gate (§3.1). Passar também no limite inferior é o alvo seguinte, e depende de features com mais sinal ([ADR-014](adr/adr-014-dataset-externo-desfecho-real.md)) |
| Lift com 30% da fila (`lift_at_fracao`) | ≥ 1,3 | 1,43 [1,07; 1,79]: **atende** | Capturar pelo menos 30% a mais de faltosos que avisando 30% ao acaso. No fold isolado de 65 consultas, o campeão mede 1,26 |
| Brier | ≤ o do modelo que responde sempre a taxa base | 0,195 contra 0,198: **atende**, por pouco | Garante que a probabilidade mostrada na "Fila do dia" não é pior que dizer "27% para todo mundo" |
| `recall_1`, `f1_1` | sem alvo; informativas | ~0 | Seguem no resumo do gate para comparação histórica. A prioridade da Camila (`notas-camila.md`, "não quero deixar de avisar quem ia faltar") passa a ser medida pelo recall dentro do orçamento, `recall_at_fracao`: 0,44 [0,33; 0,55] |
| Regra de decisão | por orçamento, não por threshold fixo | `top_fracao` 0,30, válvulas 0,15 e 0,50 | Camila: "Threshold default 0.5 não serve. Calibrar com base no custo unitário do SMS vs. perda do no-show." Atendido pelo ADR-016 e pela curva de custo do [SLA §4.1](SLA.md) |

Os alvos não são critério de promoção: o gate protege contra regressão relativa (§3.1). Um re-treino que deixe o campeão abaixo deles é sinal para revisão humana, não bloqueio automático. A validação cruzada ainda não roda no `retrain.yml`: hoje ela é medida à parte, pelo comando acima.

### 3.1 Tolerância de regressão do gate de re-treino

Definida em 2026-09-21, ao especificar o gate de re-treino mensal (`src/retrain_gate.py`). Até então havia um único número (0.02, herdado da linha de ROC-AUC acima) aplicado a todas as métricas — **menor que a granularidade do próprio fold de teste**, o que faria o gate bloquear e promover por ruído de amostragem.

| Métrica | Tolerância de regressão vs. campeão | Por quê |
|---|---|---|
| `recall_1` | 0.05 | Métrica de contagem sobre ~21 positivos no fold de teste atual: o menor passo possível é 1/21 ≈ 0.048 — **um único paciente**. Uma tolerância menor que isso não distingue regressão de sorteio. |
| `f1_1` | 0.05 | Mesma natureza — deriva de precision/recall da mesma contagem de positivos. |
| `roc_auc` | 0.02 | É contínua (ordenação de probabilidades), não sofre do salto discreto acima. Valor mantido do que já vigorava. |

**A regra, não só o número** — a tolerância das métricas de contagem é aproximadamente **1/(positivos no fold de teste)**, arredondada para cima. Os 0.05 acima valem para o dataset atual: 380 linhas, 104 positivos, fold de teste de 76 linhas com **21 positivos** (conferido no `data/interim/test.pkl` ao implementar o gate, não estimado). Conforme a ingestão de desfechos reais pela interface (aba "Fila do dia") fizer o dataset crescer, o número **deve ser recalculado**: com ≥50 positivos no fold, a tolerância cai para 0.02 e o gate passa a enxergar regressões que hoje são invisíveis. Revisar a cada ciclo em que o volume de treino aumentar de forma relevante, e registrar a mudança aqui junto com o tamanho do fold que a justificou.

**Atenção — tolerância ≠ alvo**: as tolerâncias acima governam **regressão relativa** ao modelo campeão. Os alvos absolutos da §3 não são critério de promoção. Até 2026-11-22 eles eram `recall_1` ≥ 0.75 e `f1_1` ≥ 0.65, que nenhum modelo atingia, e por isso o gap ficou registrado como exceção. A exceção foi resolvida com a regra por orçamento e a revisão dos alvos (§3).

Os números do modelo vigente, **medidos** no fold de teste isolado e registrados em `data/champion_metrics.json` ao implementar o gate (2026-09-21): `recall_1` **0.429**, `f1_1` **0.419**, `roc_auc` **0.643**, no threshold 0.6. O 0.522 que este documento e o `architecture.md` citavam como "recall atual" vinha do [ADR-003](adr/adr-003-SMOTE-NC.md), medido em outra configuração (antes de `features.temporais=true`, em outro threshold) — o par "0.522 / 0.419" nunca foi de um mesmo modelo. Daqui em diante o número vigente é o do `champion_metrics.json`, que só muda quando o gate promove.

**Critério desde 2026-11-22** ([ADR-016](adr/adr-016-regra-de-decisao-por-orcamento.md)): o gate decide por **`recall_at_fracao`** (recall avisando os 30% de maior risco do fold, `decision.fracao_da_fila`; métrica de contagem, tolerância **0.05** pela mesma regra de 1/positivos) e **`roc_auc`** (0.02). As duas não dependem da escala da probabilidade, e por isso valem para modelo calibrado e para a regra por orçamento. `recall_1` e `f1_1` seguem na tabela do resumo do gate, só **informativos**: medidos no threshold de 0,6, iriam a zero com qualquer modelo calibrado. O campeão foi medido no critério novo antes da troca, no mesmo `model.pkl` e fold: `recall_at_fracao` **0.429** e `lift_at_fracao` **1.42** (21 positivos, 23 avisados de 76). Os alvos da §3 foram revisados no mesmo dia; o uso da validação cruzada no gate (tolerância calculada pelo desvio da CV) segue pendente.

**Campeão vigente desde a v3.3.1** (`07bcfede25ea`, logística + `class_weight` + sigmoide, `top_fracao`): com os primeiros desfechos de produção, o fold de teste passou a ser separado por paciente e tem **18 positivos** em 65 consultas. Nele: `recall_at_fracao` **0.389**, `lift_at_fracao` **1.26**, `roc_auc` **0.648**, Brier **0.199**; `recall_1` e `f1_1` dão 0 (probabilidade calibrada abaixo de 0,6). Com 18 positivos, um paciente move o recall em 1/18 ≈ 0.056, um pouco acima da tolerância de 0.05. A regra de 1/positivos pediria 0.06. A revisão da tolerância fica pendente aqui, porque mudar tolerância é decisão humana.

**Quem aplica a regra**: `src/retrain_gate.py` mede os positivos do fold a cada ciclo e, quando passarem de 50, emite aviso no resumo do run pedindo a revisão desta seção — sem bloquear, porque mudar tolerância é decisão humana. As tolerâncias em si vivem em `params.yaml` (`gate.tolerancia`), lidas pelo gate; não há default no código, para que nenhuma promoção aconteça por um número que ninguém revisou.

**Gate de re-treino:** se `recall_at_fracao` ou `roc_auc` regredir além da tolerância em relação ao modelo em produção, ou o `roc_auc` ficar abaixo do piso, ou a suíte de sanidade falhar, o pipeline mensal **bloqueia a promoção** (código 1 do `retrain.yml`) e mantém o modelo anterior em produção. O aprovado ainda passa pelo canário antes de virar campeão ([ADR-008](adr/adr-008-canario-do-modelo.md)). Implementado em `src/retrain_gate.py`; era o requisito da Camila.

## 4. Explicabilidade

| Métrica | Alvo | Observação |
|---|---|---|
| Cobertura de explicação por predição (SHAP ou equivalente) | 100% das predições expostas à clínica | Requisito de negócio, não só técnico: a diretora médica (Dra. Helena) não aceita "caixa preta" para decisão clínica/operacional (`notas-camila.md`). Ainda não implementado — listado como "prioridade alta" nas notas herdadas e como pendência no notebook. |

## 5. Pipeline de dados e re-treino

| Métrica | Alvo | Observação |
|---|---|---|
| Execução do re-treino mensal | 100% dos meses, automatizada (cron) | Hoje é manual (Camila rodava o notebook à mão). Requisito de negócio confirmado no BRIEFING.md ("Re-treino mensal acordado com o time de Produto"). |
| Rastreabilidade de dados e modelo | 100% dos re-treinos versionados (DVC + MLflow, conforme ADR-001) | Sem isso não há como fazer rollback de modelo com regressão de métrica. |

## 6. Privacidade / LGPD (não-funcional, mas mensurável)

| Métrica | Alvo | Observação |
|---|---|---|
| PII em logs de aplicação | 0 ocorrências | Requisito absoluto do BRIEFING.md ("Sem PII em logs. Nunca.") e das notas da Camila. Como é verificado: ver §6.1. |
| Dados sensíveis (Art. 5º/11 LGPD) em repouso | 100% criptografados | Dados de saúde são categoria especial mesmo sendo sintéticos no protótipo (`AVISO-DADOS-SINTETICOS.md`, `notas-camila.md`). Herdado do provedor gerenciado (Supabase, Azure Blob Storage), não implementado por nós — registrado como risco residual em [LGPD.md §9](LGPD.md). |
| Retenção de dados derivados | `predicoes`/`mensagens_disparadas` purgadas em ≤ 90 dias | Princípio da necessidade (Art. 6º, III). Purga no job diário; política por tabela em [LGPD.md §5](LGPD.md). |

### 6.1 Como o "zero PII em log" é verificado (revisão de 2026-09-27)

Duas correções ao texto original desta seção, decididas ao implementar a blindagem de PII no log (`src/logging_config.py`):

**(a) "Verificável via auditoria/regex nos logs" não é executável em produção.** O [ADR-006](adr/adr-006-observabilidade.md) (risco 2) já havia registrado isto: o log de runtime do Hugging Face Space é efêmero — restart ou rebuild apaga, sem busca e sem retenção. No dia do pitch não haverá log de produção para varrer. A métrica continua sendo 0 ocorrências; o que muda é o **método de verificação**, que passa a ser preventivo:

| Camada | O que cobre |
|---|---|
| Filtro de redação em todo handler do processo (`src/logging_config.py`) | O log de **terceiros** (`uvicorn`, `httpx`, `nicegui`), que é o único que existe em volume em produção e o único que não temos como reescrever |
| Guarda estática sobre a AST de `src/` (`tests/test_coerencia_repo.py`) | Código nosso passando campo de PII para uma chamada de log — o caso que não deveria nem chegar ao filtro |
| `scripts/auditoria_lgpd.py` | Varredura de log **local** (smoke test do deploy, artifact do Actions). Ferramenta de verificação, não a evidência apresentada no pitch |

**(b) A lista "(nome, CPF)" estava incompleta.** Ela vem de antes da integração com a Infobip, que acrescentou `telefone` a `pacientes` como exceção deliberada à minimização de PII — e telefone é hoje o **único identificador direto** que o runtime manipula, justamente no ponto mais exposto (a resposta de erro da Infobip ecoava o número enviado). O alvo passa a valer para nome, CPF, e-mail, telefone e IP. O inventário completo do que é tratado está em [LGPD.md §2](LGPD.md).

## 7. Features pendentes que afetam SLOs futuros

- **Dia da semana / horário da consulta**: Camila suspeita que sexta 18h tem no-show muito maior, mas a feature não foi extraída a tempo. Isso pode elevar o teto de recall/F1 atingível — revisar os alvos da seção 3 quando essa feature entrar.
- Dataset de treino é pequeno (~400 linhas sintéticas) — alvos de qualidade acima devem ser revistos quando houver volume real de produção.

## Revisão

Este documento deve ser revisado a cada marco do semestre (BRIEFING.md) e sempre que o [ADR-001](adr/adr-001-stack.md) ou a arquitetura mudar. Última geração: 2026-09-07; última revisão: 2026-11-22 (§3.1, critério do gate por `recall_at_fracao` e `roc_auc`); revisão anterior: 2026-09-30 (§1/§2 reescritas como SLOs do batch); antes: 2026-09-27 (§6/§6.1, método de verificação do zero-PII e escopo da lista de campos; §3.1 revisado em 2026-09-21 com as tolerâncias de regressão do gate e a correção das métricas do modelo vigente, agora medidas e registradas em `data/champion_metrics.json`).
