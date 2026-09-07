# SLOs — SaúdeJá (Classificador de No-show)

> **Status:** rascunho inicial (v0.1), gerado a partir do [BRIEFING.md](BRIEFING.md), das notas herdadas da Camila (`docs/herdado/notas-camila.md`), do código em `src/train.py` / `src/notebook.ipynb` e do [ADR-001](adr/adr-001-stack.md), que já referenciava este documento antes de ele existir. **Os alvos numéricos abaixo precisam ser validados com o time de Produto antes de virarem compromisso** — aqui eles foram inferidos das restrições conhecidas (orçamento, dataset pequeno, estágio de protótipo), não de um SLA já negociado com clínicas.

Service Level Objectives (SLOs) são as metas técnicas internas, mensuráveis, que sustentam os compromissos do [SLA.md](SLA.md). Cobrem o ciclo completo descrito no diagrama de [architecture.md](architecture.md): API de inferência, pipeline de re-treino mensal e disparo de lembretes.

## 1. Disponibilidade da API de inferência

| Métrica | Alvo | Observação |
|---|---|---|
| Uptime mensal da API `/predict` | ≥ 99.0% | Compatível com orçamento de US$ 100/mês (BRIEFING.md) — não justifica multi-região/HA cara. Cold start de plataformas serverless (Hugging Face Spaces / Inference Endpoints / Modal, avaliadas no ADR-001) é um risco explícito para esse número. |
| Erro 5xx | < 1% das requisições | Medido por janela de 30 dias. |

**Por que não 99.9%:** o ADR-001 já sinaliza o cold start do provedor de deploy escolhido como risco conhecido; e o orçamento de US$ 100/mês (BRIEFING.md) exclui redundância de infraestrutura.

## 2. Latência

| Métrica | Alvo | Observação |
|---|---|---|
| p95 de latência de `/predict` (requisição já aquecida) | < 2s | Modelo é um LightGBM leve (`src/train.py`), inferência em si é da ordem de milissegundos; a folga cobre overhead de rede/serialização. |
| p95 incluindo cold start (serverless) | < 10s | Só aplicável se a stack final usar plataforma serverless com scale-to-zero (ADR-001). Reavaliar se inviabilizar a UX da fila do dia (BRIEFING.md, item 2). |

## 3. Qualidade do modelo (re-treino mensal)

Baseline herdado (`docs/logs/CHANGELOG.md`, v0.5): acurácia 78%, F1 da classe positiva (no-show=1) 0.65, split 80/20 estratificado sobre ~400 linhas sintéticas.

| Métrica | Alvo | Observação |
|---|---|---|
| Recall da classe positiva (no-show=1) | ≥ 0.75 | Prioridade explícita da Camila (`notas-camila.md`): "o que importa é recall da classe 1 (não quero deixar de avisar quem ia faltar)". Acima do baseline atual — motiva SMOTE / `class_weight='balanced'` / threshold tuning, já listados como próximos passos no notebook. |
| F1 da classe positiva | ≥ 0.65 (baseline) → alvo de melhoria contínua | Não regredir abaixo do herdado a cada re-treino mensal. |
| ROC-AUC | reportado a cada re-treino, sem regressão > 0.02 vs. mês anterior | Gate de alerta do pipeline de re-treino (ver notas-camila.md: "cron + script + alerta se a métrica cair"). |
| Threshold de decisão | calibrado por custo, não fixo em 0.5 | Camila: "Threshold default 0.5 não serve. Calibrar com base no custo unitário do SMS vs. perda do no-show." Ver também item 3 do BRIEFING.md. |

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
| PII (nome, CPF) em logs de aplicação | 0 ocorrências | Requisito absoluto do BRIEFING.md ("Sem PII em logs. Nunca.") e das notas da Camila. Verificável via auditoria/regex nos logs. |
| Dados sensíveis (Art. 5º/11 LGPD) em repouso | 100% criptografados | Dados de saúde são categoria especial mesmo sendo sintéticos no protótipo (`AVISO-DADOS-SINTETICOS.md`, `notas-camila.md`). |

## 7. Features pendentes que afetam SLOs futuros

- **Dia da semana / horário da consulta**: Camila suspeita que sexta 18h tem no-show muito maior, mas a feature não foi extraída a tempo. Isso pode elevar o teto de recall/F1 atingível — revisar os alvos da seção 3 quando essa feature entrar.
- Dataset de treino é pequeno (~400 linhas sintéticas) — alvos de qualidade acima devem ser revistos quando houver volume real de produção.

## Revisão

Este documento deve ser revisado a cada marco do semestre (BRIEFING.md) e sempre que o [ADR-001](adr/adr-001-stack.md) ou a arquitetura mudar. Última geração: 2026-09-07.
