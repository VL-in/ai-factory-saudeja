# SLOs — SaúdeJá (Classificador de No-show)

> **Status:** rascunho inicial (v0.1), gerado a partir do [BRIEFING.md](BRIEFING.md), das notas herdadas da Camila (`docs/herdado/notas-camila.md`), do código em `src/train.py` / `src/notebook.ipynb` e do [ADR-001](adr/adr-001-stack.md), que já referenciava este documento antes de ele existir. **Os alvos numéricos abaixo precisam ser validados com o time de Produto antes de virarem compromisso** — aqui eles foram inferidos das restrições conhecidas (orçamento, dataset pequeno, estágio de protótipo), não de um SLA já negociado com clínicas.

Service Level Objectives (SLOs) são as metas técnicas internas, mensuráveis, que sustentam os compromissos do [SLA.md](SLA.md). Cobrem o ciclo completo descrito no diagrama de [architecture.md](architecture.md): API de inferência, pipeline de re-treino mensal e disparo de lembretes.

## 1. Processamento da fila D-2 (batch)

> **Reescrito em 2026-09-30 (Passo 10.5).** As versões anteriores das §1/§2 mediam a API `/predict` (uptime e p95 de latência). A revisão do Passo 10 fixou a premissa de que a solução é de **inferência em batch**: o produto depende do job diário, do registro do desfecho pela atendente e do re-treino mensal — nada em tempo real. A API continua no repositório para cumprir o item 1 do BRIEFING, mas **saiu do caminho crítico e do compromisso de produção** (decisão 1 da 2ª revisão do Passo 10). O que a clínica percebe é se a fila de amanhã foi processada a tempo, e é isso que passa a ser medido.

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
| Uptime mensal da interface (fila do dia, cadastro) | ≥ 99.0% | Sonda externa no Space (Passo 11). Orçamento de US$ 100/mês (BRIEFING.md) exclui redundância; o Space free hiberna, e o cold start é risco conhecido. |

## 2. Latência

| Métrica | Alvo | Observação |
|---|---|---|
| Duração do job D-2 | termina antes das 09h (≈ 40 min a partir do disparo) | Folga larga: o job processa uma fila diária de uma clínica em segundos; o que ocupa a janela é o atraso do cron do GitHub. |
| Carga da interface depois de hibernar (cold start) | < 10s | Medido no Passo 11/12. A fila do dia lê predições **já gravadas** pelo job — nenhuma predição acontece na hora em que a atendente abre a tela. |

**Fora do compromisso de produção** (decisão 1 da 2ª revisão do Passo 10): o p95 de `/predict` e da aba "Testar predição". Continuam medidos em `eventos_app` (`origem` = `api`/`processo`) como indicador de desenvolvimento — a medição do Passo 8.5 (~1,4s na primeira predição de um processo, ~10ms nas seguintes) segue valendo como referência.

## 3. Qualidade do modelo (re-treino mensal)

Baseline herdado (`docs/logs/CHANGELOG.md`, v0.5): acurácia 78%, F1 da classe positiva (no-show=1) 0.65, split 80/20 estratificado sobre ~400 linhas sintéticas.

| Métrica | Alvo | Observação |
|---|---|---|
| Recall da classe positiva (no-show=1) | ≥ 0.75 | Prioridade explícita da Camila (`notas-camila.md`): "o que importa é recall da classe 1 (não quero deixar de avisar quem ia faltar)". Acima do baseline atual — motiva SMOTE / `class_weight='balanced'` / threshold tuning, já listados como próximos passos no notebook. |
| F1 da classe positiva | ≥ 0.65 (baseline) → alvo de melhoria contínua | Não regredir abaixo do herdado a cada re-treino mensal. |
| ROC-AUC | reportado a cada re-treino, sem regressão além da tolerância da tabela abaixo | Gate de alerta do pipeline de re-treino (ver notas-camila.md: "cron + script + alerta se a métrica cair"). |
| Threshold de decisão | calibrado por custo, não fixo em 0.5 | Camila: "Threshold default 0.5 não serve. Calibrar com base no custo unitário do SMS vs. perda do no-show." Ver também item 3 do BRIEFING.md. |

### 3.1 Tolerância de regressão do gate de re-treino

Definida em 2026-09-21, ao especificar o Passo 9 do [PLANO-IMPLEMENTACAO.md](PLANO-IMPLEMENTACAO.md). Até então havia um único número (0.02, herdado da linha de ROC-AUC acima) aplicado a todas as métricas — **menor que a granularidade do próprio fold de teste**, o que faria o gate bloquear e promover por ruído de amostragem.

| Métrica | Tolerância de regressão vs. campeão | Por quê |
|---|---|---|
| `recall_1` | 0.05 | Métrica de contagem sobre ~21 positivos no fold de teste atual: o menor passo possível é 1/21 ≈ 0.048 — **um único paciente**. Uma tolerância menor que isso não distingue regressão de sorteio. |
| `f1_1` | 0.05 | Mesma natureza — deriva de precision/recall da mesma contagem de positivos. |
| `roc_auc` | 0.02 | É contínua (ordenação de probabilidades), não sofre do salto discreto acima. Valor mantido do que já vigorava. |

**A regra, não só o número** — a tolerância das métricas de contagem é aproximadamente **1/(positivos no fold de teste)**, arredondada para cima. Os 0.05 acima valem para o dataset atual: 380 linhas, 104 positivos, fold de teste de 76 linhas com **21 positivos** (conferido no `data/interim/test.pkl` ao implementar o Passo 9.1, não estimado). Conforme a ingestão de desfechos reais pela interface (Passo 9.0) fizer o dataset crescer, o número **deve ser recalculado**: com ≥50 positivos no fold, a tolerância cai para 0.02 e o gate passa a enxergar regressões que hoje são invisíveis. Revisar a cada ciclo em que o volume de treino aumentar de forma relevante, e registrar a mudança aqui junto com o tamanho do fold que a justificou.

**Atenção — tolerância ≠ alvo**: as tolerâncias acima governam **regressão relativa** ao modelo campeão. Os alvos absolutos da tabela da §3 (`recall_1` ≥ 0.75, `f1_1` ≥ 0.65) não são critério de promoção — o modelo vigente já os viola e, se fossem, nada jamais seria promovido. O gap absoluto é exceção documentada, com fechamento previsto para o Passo 12.

Os números do modelo vigente, **medidos** no fold de teste isolado e registrados em `data/champion_metrics.json` ao implementar o Passo 9.1: `recall_1` **0.429**, `f1_1` **0.419**, `roc_auc` **0.643**, no threshold 0.6. O 0.522 que este documento e o `architecture.md` citavam como "recall atual" vinha do [ADR-003](adr/adr-003-SMOTE-NC.md), medido em outra configuração (antes de `features.temporais=true`, em outro threshold) — o par "0.522 / 0.419" nunca foi de um mesmo modelo. Daqui em diante o número vigente é o do `champion_metrics.json`, que só muda quando o gate promove.

**Quem aplica a regra**: `src/retrain_gate.py` mede os positivos do fold a cada ciclo e, quando passarem de 50, emite aviso no resumo do run pedindo a revisão desta seção — sem bloquear, porque mudar tolerância é decisão humana. As tolerâncias em si vivem em `params.yaml` (`gate.tolerancia`), lidas pelo gate; não há default no código, para que nenhuma promoção aconteça por um número que ninguém revisou.

**Gate de re-treino:** se qualquer métrica acima regredir em relação ao modelo em produção, o pipeline mensal deve **alertar e bloquear o rollout automático**, mantendo o modelo anterior em produção até revisão humana (Camila apontou isso como requisito, ainda não implementado — hoje o processo é manual).

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
| Retenção de dados derivados | `predicoes`/`mensagens_disparadas` purgadas em ≤ 365 dias | Princípio da necessidade (Art. 6º, III). Purga no job diário; política por tabela em [LGPD.md §5](LGPD.md). |

### 6.1 Como o "zero PII em log" é verificado (revisão de 2026-09-27, Passo 8)

Duas correções ao texto original desta seção, decididas ao implementar o Passo 8:

**(a) "Verificável via auditoria/regex nos logs" não é executável em produção.** O [ADR-006](adr/adr-006-observabilidade.md) (risco 2) já havia registrado isto: o log de runtime do Hugging Face Space é efêmero — restart ou rebuild apaga, sem busca e sem retenção. No dia do pitch não haverá log de produção para varrer. A métrica continua sendo 0 ocorrências; o que muda é o **método de verificação**, que passa a ser preventivo:

| Camada | O que cobre |
|---|---|
| Filtro de redação em todo handler do processo (`src/logging_config.py`) | O log de **terceiros** (`uvicorn`, `httpx`, `streamlit`), que é o único que existe em volume em produção e o único que não temos como reescrever |
| Guarda estática sobre a AST de `src/` (`tests/test_coerencia_repo.py`) | Código nosso passando campo de PII para uma chamada de log — o caso que não deveria nem chegar ao filtro |
| `scripts/auditoria_lgpd.py` | Varredura de log **local** (smoke test do Passo 11, artifact do Actions). Ferramenta de verificação, não a evidência apresentada no pitch |

**(b) A lista "(nome, CPF)" estava incompleta.** Ela vem de antes do Passo 7, que acrescentou `telefone` a `pacientes` como exceção deliberada à minimização de PII — e telefone é hoje o **único identificador direto** que o runtime manipula, justamente no ponto mais exposto (a resposta de erro da Infobip ecoava o número enviado). O alvo passa a valer para nome, CPF, e-mail, telefone e IP. O inventário completo do que é tratado está em [LGPD.md §2](LGPD.md).

## 7. Features pendentes que afetam SLOs futuros

- **Dia da semana / horário da consulta**: Camila suspeita que sexta 18h tem no-show muito maior, mas a feature não foi extraída a tempo. Isso pode elevar o teto de recall/F1 atingível — revisar os alvos da seção 3 quando essa feature entrar.
- Dataset de treino é pequeno (~400 linhas sintéticas) — alvos de qualidade acima devem ser revistos quando houver volume real de produção.

## Revisão

Este documento deve ser revisado a cada marco do semestre (BRIEFING.md) e sempre que o [ADR-001](adr/adr-001-stack.md) ou a arquitetura mudar. Última geração: 2026-09-07; última revisão: 2026-09-30 (§1/§2 reescritas como SLOs do batch, Passo 10.5); revisão anterior: 2026-09-27 (§6/§6.1, método de verificação do zero-PII e escopo da lista de campos; §3.1 revisado em 2026-09-21 com as tolerâncias de regressão do gate e a correção das métricas do modelo vigente, agora medidas e registradas em `data/champion_metrics.json`).
