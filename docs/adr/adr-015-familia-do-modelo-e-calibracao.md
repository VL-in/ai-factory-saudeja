# ADR-015: Família do modelo, balanceamento e calibração

## Status
Proposto - 2026-11-22. A troca dos valores do `params.yaml` depende da decisão da autora (ver "Decisão proposta"). O código que torna a troca possível já está no repositório, com os valores do campeão como padrão.

As palavras-chave **DEVE**, **NÃO DEVE** e **PODE** seguem o sentido da [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

## Contexto
O modelo em produção é um LightGBM de 120 árvores, 32 folhas e profundidade 6, treinado com SMOTE-NC e sem calibração. A emenda do [ADR-003](adr-003-SMOTE-NC.md) mostrou, em validação cruzada repetida, que ele ordena os pacientes quase como um sorteio (`roc_auc` 0,555 ± 0,060 na semente) e que o SMOTE-NC não supera o `class_weight`. O [ADR-014](adr-014-dataset-externo-desfecho-real.md) trouxe desfecho real (Kaggle, rede pública de Vitória-ES, 2016) e mostrou que o sinal das features que o SaúdeJá coleta é fraco: o teto fica perto de 0,58-0,60.

Para comparar alternativas com o mesmo código do treino, o pipeline passou a aceitar como parâmetro:

- `model.tipo`: `lightgbm` ou `logistica` (one-hot nas categóricas e padronização nas numéricas, num `Pipeline` salvo no `model.pkl`);
- `balancing.strategy`: `smotenc`, `class_weight` ou `none`;
- `model.calibracao`: `none` ou `sigmoid` (sigmoide de Platt com CV interna; só sem SMOTE-NC);
- a regularização do LightGBM (`min_child_samples`, `reg_lambda`, `subsample`, `subsample_freq`, `colsample_bytree`);
- `features.periodo_do_dia`, que troca o horário cru por período do dia, sexta no fim do expediente e sábado.

A explicação SHAP de cada predição continua somando com a probabilidade mostrada nos dois tipos, calibrados ou não, e a suíte de sanidade do gate vale para todos.

## Evidência (2026-11-22)
O critério de escolha é a validação cruzada repetida (`scripts/avaliar_cv.py`, 5 folds × 5 repetições, intervalo de 95% com a correção de Nadeau-Bengio), com validação por paciente no dado externo. O número oficial do fold de teste isolado e o teste reservado do ADR-014 (10.000 consultas de pacientes que não entram em nenhum ajuste) entram só no fim. Todas as rodadas estão no MLflow local, no experimento de desenvolvimento `dev-melhoria-modelo` (runs com dado externo levam a tag `dados_externos=true`).

**Validação cruzada, `roc_auc` [IC 95%], todas as features, horário cru:**

| Configuração | Semente (380) | Externa (1.500, por paciente) | Semente + externa (por paciente) |
|---|---|---|---|
| Campeão: LightGBM + SMOTE-NC | 0,555 [0,488; 0,622] | 0,542 [0,506; 0,577] | 0,539 [0,506; 0,571] |
| LightGBM regularizado + `class_weight` + sigmoide | 0,570 [0,506; 0,633] | 0,582 [0,542; 0,621] | 0,571 [0,543; 0,599] |
| **Logística + `class_weight`** | **0,619** [0,549; 0,689] | **0,579** [0,543; 0,614] | **0,579** [0,556; 0,603] |
| Logística + `class_weight` + sigmoide | 0,619 | 0,579 | 0,579 |

**Fold de teste isolado** (`dvc exp run` no Docker, semente, 76 linhas e 21 positivos; o campeão reproduz exatamente os números registrados):

| Configuração | `roc_auc` | `pr_auc` | `f1_1` @0,6 | `recall_1` @0,6 | Disparo @0,6 |
|---|---|---|---|---|---|
| Campeão | 0,643 | 0,408 | 0,419 | 0,429 | 29% |
| Logística + `class_weight` | 0,707 | 0,506 | 0,550 | 0,524 | 25% |
| Logística + `class_weight` + sigmoide | 0,707 | 0,506 | 0,000 | 0,000 | 0% |
| LightGBM regularizado + `class_weight` + sigmoide | 0,638 | 0,401 | 0,000 | 0,000 | 0% |

**Teste reservado** (treino na amostra externa de 1.500; IC por bootstrap):

| Configuração | `roc_auc` | lift@30% | Brier (taxa base 0,208) |
|---|---|---|---|
| Campeão | 0,560 [0,548; 0,572] | 1,18 | 0,240 |
| Logística + `class_weight` | 0,563 [0,551; 0,576] | 1,17 | 0,249 |
| Logística + `class_weight` + sigmoide | 0,563 [0,550; 0,575] | 1,17 | **0,206** |

Leitura:

- **A logística nunca perde e às vezes ganha.** Ganha na semente (+0,064 em CV) e no fold oficial (+0,064). No dado real, empata: +0,037 em CV, com intervalos sobrepostos, e +0,003 no teste reservado. Com 300 a 1.500 linhas e sinal fraco, o modelo de menor variância é o mais seguro.
- **A calibração é a única configuração com Brier abaixo da taxa base.** Ela não muda a ordenação (a sigmoide é monótona), mas leva a probabilidade de volta à prevalência real. Sem ela, todo modelo com balanceamento tem Brier pior que responder sempre a taxa base.
- **A calibração é incompatível com o threshold fixo de 0,6.** Calibrada, quase nenhuma probabilidade passa de 0,6: o disparo vai a 0%, o `recall_1` a 0, e o gate bloquearia o desafiante pela regressão de `recall_1` e `f1_1`. O threshold de 0,6 só funciona porque o balanceamento infla as probabilidades.
- **O período do dia não traz ganho real.** Na semente, o ganho (0,619 → 0,638) vem todo da flag de sexta no fim do expediente: sem ela, o resultado cai para 0,606, abaixo de só as features reais (0,617). É o padrão "sexta 17h" que o script de horários injetou. No dado externo, o horário é sorteado e só carrega o sinal declarado da sexta (decisão 6 do ADR-014), então qualquer ganho de horário ou período ali é sintético por construção.
- **Nenhuma configuração passa do piso de 0,60 no dado real**, confirmando o ADR-014: o gargalo continua sendo as features, não o modelo.

## Decisão proposta
1. **Família e balanceamento:** trocar para `model.tipo: logistica` com `balancing.strategy: class_weight` (`C` 0,3). Ganha na semente, empata no dado real, tem menor variância e coeficientes legíveis.
2. **Calibração:** `model.calibracao` **DEVE** continuar `none` enquanto a decisão for pelo threshold fixo. Ela **DEVE** ser ligada no mesmo passo que trocar a regra de decisão para "avisar a fração da fila que o orçamento permite" (top-K), porque o top-K não depende da escala da probabilidade e a probabilidade calibrada passa a ter sentido para quem a lê.
3. **Período do dia:** `features.periodo_do_dia` **DEVE** continuar `false`. O ganho medido é o padrão fabricado. Também **NÃO DEVE** ser ligada sem um `model.pkl` treinado com ela, porque o serving lê a flag do `params.yaml` (a carga do modelo falha).
4. **Promoção:** a troca dos itens 1 a 3 no `params.yaml` vai em commit próprio, e o re-treino mensal treina, compara com o campeão no gate (a logística passa no piso de 0,60 e nas tolerâncias no fold oficial) e segue para o canário ([ADR-008](adr-008-canario-do-modelo.md)), como qualquer desafiante.

## Consequências
Positivas:

- A escolha de modelo deixa de depender de um único split e passa a ter evidência em dado real.
- Treino, validação cruzada e serving montam o modelo pelo mesmo código (`src/modelo.py`), e a assinatura, a sanidade e o SHAP valem para qualquer família.
- Com o split por paciente (quando há id estável) e a tag `dados_externos`, um experimento com o dado externo não vaza paciente nem perde a rastreabilidade.

Negativas e riscos aceitos:

- **A probabilidade da logística com `class_weight` não é calibrada** (Brier 0,238 a 0,249, pior que a taxa base), como já não é a do campeão (0,240 a 0,244). A tela não deve apresentá-la como "chance real de faltar" até a calibração entrar com o top-K.
- **O ganho no fold oficial (+0,064) é medido em 76 linhas.** Ele passa no gate, mas o número que sustenta a decisão é a CV, e no dado real o ganho é quase nulo.
- **A explicação muda de natureza.** No LightGBM, o SHAP capta interações; na logística, cada contribuição é coeficiente × (valor − média do treino). É mais simples de explicar, mas um efeito em U (como a antecedência no dado real) vira uma reta.
- **Experimentos com `dvc exp run -S` reescrevem o `params.yaml` do workspace** sem os comentários e tiram as aspas de valores como `"13:00"`, e o `dvc exp run` também reinicia o índice do git. Depois de cada sessão de experimentos, o workspace **DEVE** voltar ao estado da branch antes de qualquer commit.

## Alternativas consideradas
- **Manter o LightGBM + SMOTE-NC.** É a pior configuração em todos os conjuntos da CV, e o SMOTE-NC não melhora a ordenação (ADR-003, emenda).
- **LightGBM regularizado com `class_weight`.** Empata com a logística no dado externo (0,582 em CV), mas perde na semente e no fold oficial, e tem mais variância. Fica como desafiante para quando houver features com interação real.
- **Ligar a calibração já.** Melhora o Brier, mas zera o disparo com o threshold de 0,6 e seria bloqueada pelo gate. Depende da regra de decisão por orçamento.
- **Ligar o período do dia.** O ganho é o padrão injetado na semente e o sinal declarado no dado externo, não evidência.

## Referências
- [ADR-003](adr-003-SMOTE-NC.md) (emenda de 2026-11-21), [ADR-008](adr-008-canario-do-modelo.md), [ADR-014](adr-014-dataset-externo-desfecho-real.md).
- Platt, J. *Probabilistic Outputs for Support Vector Machines and Comparisons to Regularized Likelihood Methods*, 1999 (sigmoide de calibração).
- Nadeau, C.; Bengio, Y. *Inference for the Generalization Error*. Machine Learning, 52, 2003.

## Changelog
- 2026-11-22: aberto como Proposto, com a evidência em validação cruzada, no fold oficial e no teste reservado.
- 2026-11-22: a regra de decisão por orçamento (`decision.modo: top_fracao`) existe, no [ADR-016](adr-016-regra-de-decisao-por-orcamento.md), e com ela o gate passa a decidir por `recall_at_fracao` e `roc_auc`. A recomendação passa a ser uma promoção só, com os itens 1 e 2 juntos: logística, `class_weight`, `model.calibracao: sigmoid` e `decision.modo: top_fracao`. No fold oficial, essa configuração tem `recall_at_fracao` 0,619 contra 0,429 do campeão.
