# ADR-016: Regra de decisão do lembrete por orçamento da fila

## Status
Aceito - 2026-11-22. O mecanismo está no código. O campeão em produção continua decidindo pelo threshold de 0,6: o modo novo só chega a ele por promoção (ver "Decisão", item 5).

As palavras-chave **DEVE**, **NÃO DEVE** e **PODE** seguem o sentido da [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

## Contexto
O job D-2 decidia o lembrete pago linha por linha: avisava quem tinha probabilidade >= `decision.threshold` (0,6). Esse corte tem três problemas.

- **A fração avisada não é controlada.** Ela depende da escala da probabilidade e da fila do dia. O campeão (LightGBM + SMOTE-NC) avisa de 16% a 29% da fila só por diferença de amostra (20% em média na validação cruzada, 29% no fold oficial).
- **O corte só funciona porque o balanceamento infla as probabilidades.** O [ADR-015](adr-015-familia-do-modelo-e-calibracao.md) mostrou que, com a calibração, quase nenhuma probabilidade passa de 0,6: o disparo cai a zero e o gate bloquearia o modelo pela regressão de `recall_1` e `f1_1`, sem ele ordenar pior.
- **O objetivo do BRIEFING é um orçamento, não um corte.** Um SMS custa cerca de R$ 0,30 e uma falta R$ 180. Por valor esperado, avisar todo mundo seria o certo. O "cortar 70% do custo de mensageria" só tem sentido como restrição: avisar os 30% de maior risco.

## Decisão
1. **Dois modos de decisão** (`decision.modo` em `params.yaml`, `src/decisao.py`):
   - `threshold`: o de antes.
   - `top_fracao`: avisa os K pacientes de maior probabilidade da fila, com K = ⌈`fracao_da_fila` × fila⌉ (0,30). O arredondamento é para cima porque errar para o lado de avisar custa centavos, e para o outro, R$ 180.
   - Duas válvulas opcionais no `top_fracao`, na escala da probabilidade: `probabilidade_minima` (0,15) bloqueia o envio mesmo com orçamento sobrando, e `probabilidade_certa` (0,50) força o envio mesmo além da fração. Na logística calibrada (validação cruzada da semente), 0,15 barra cerca de 2% da fila e 0,50 força cerca de 1%. A mediana é 0,26 e a taxa base é 0,27. Sem calibração as válvulas cortam no lugar errado, e o gate avisa.
2. **A decisão é sobre a fila inteira.** O job prediz e explica todos os agendamentos antes de decidir. Cada braço (campeão e canário) aplica a sua regra à parte da fila que recebeu. Quem vai para a quarentena recebe o lembrete sem predição e não ocupa vaga do orçamento.
3. **Cada modelo decide pela regra com que foi aprovado.** O gate grava a regra do `params.yaml` no registro do desafiante (bloco `decisao` de `canario.json` e de `champion_metrics.json`). O job aplica a regra do registro, e não a do `params.yaml`. Um campeão registrado antes desta decisão é lido como `threshold`, com o `decision.threshold` conferido pela pré-checagem. Assim, mudar o `decision.modo` muda o próximo desafiante, e o modelo em produção fica intacto até uma promoção.
4. **Auditoria e critério.**
   - `predicoes` ganha `posicao_na_fila` e `modo_decisao`. No `top_fracao`, `threshold_usado` é o corte que a fila produziu.
   - O `validate` mede `recall_at_fracao` (recall avisando a fração de maior risco do fold), `lift_at_fracao` e Brier.
   - O gate decide por `recall_at_fracao` e `roc_auc`, que não dependem da escala da probabilidade. `recall_1` e `f1_1` ficam só informativos.
   - No canário que decide por `top_fracao`, a taxa de disparo vira informativa (emenda do [ADR-008](adr-008-canario-do-modelo.md)).
5. **Promoção única.** A troca para a regra nova **DEVE** ir junto com a família de modelo do ADR-015, numa promoção só: `model.tipo: logistica`, `balancing.strategy: class_weight`, `model.calibracao: sigmoid` e `decision.modo: top_fracao`, pelo re-treino, gate e canário de sempre. O `champion_metrics.json` foi complementado antes, no mesmo modelo e fold, com `recall_at_fracao` e o bloco `cv`, para o critério novo não liberar a promoção sem mérito.
6. A API `/predict` **DEVE** continuar respondendo pelo threshold: uma predição avulsa não tem fila.

## Evidência (2026-11-22)
**Fold de teste oficial** (76 linhas, 21 positivos; o campeão reproduz os números registrados):

| Configuração | `roc_auc` | `recall_at_fracao` | `lift_at_fracao` | `recall_1` @0,6 | Brier |
|---|---|---|---|---|---|
| Campeão (LightGBM + SMOTE-NC) | 0,643 | 0,429 | 1,42 | 0,429 | 0,229 |
| Logística + `class_weight` | 0,707 | 0,619 | 2,05 | 0,524 | 0,223 |
| Logística + `class_weight` + sigmoide | 0,707 | 0,619 | 2,05 | 0,000 | 0,188 |

**Validação cruzada repetida na semente** (5 × 5, média):

| Configuração | `roc_auc` | `recall_at_fracao` [IC 95%] | `lift_at_fracao` | Brier |
|---|---|---|---|---|
| Campeão | 0,555 | 0,340 [0,260; 0,421] | 1,12 | 0,244 |
| Logística + `class_weight` + sigmoide | 0,619 | 0,448 [0,358; 0,538] | 1,48 | 0,194 |

Leitura:

- Com o critério novo, o campeão comparado com ele mesmo passa: o critério não bloqueia o que está em produção. O desafiante calibrado passa por mérito (+0,19 em `recall_at_fracao`, +0,064 em `roc_auc` no fold oficial). Pelo critério antigo, seria bloqueado pelo `recall_1` em zero.
- Na validação cruzada, o lift do campeão tem intervalo que inclui 1 (avisar ao acaso). O da logística calibrada não inclui.
- A curva de custo por fração da fila, com a premissa de eficácia do lembrete, está no [SLA §4.1](../SLA.md#41-curva-de-custo-por-fração-da-fila).

## Consequências
Positivas:

- O custo de mensageria passa a ser o configurado (30% da fila), qualquer que seja a calibração do modelo.
- A calibração pode ser ligada sem zerar o disparo, e a probabilidade mostrada passa a ser a frequência real de faltas.
- O gate compara modelos pelo que eles ordenam, não pela escala da probabilidade.
- A regra viaja com o modelo, e a mudança de modo passa pelo mesmo gate e canário que a mudança de modelo.

Negativas e riscos aceitos:

- **Fila pequena dá fração grosseira.** Numa fila de 7, 30% vira 3 lembretes (43%). O aviso de taxa de disparo do job não conta o arredondamento como excesso.
- **O orçamento é por execução do job**, não por mês. A fila de um dia com muitas consultas de alto risco recebe só a fração dela. O orçamento mensal (US$ 100) continua sendo acompanhado pelo alerta de custo.
- **Comparar braços com regras diferentes.** No canário, o guardrail que decide passa a ser a falta não avisada. Ela favorece o braço que avisa mais, porque a fração dele de baixo risco é menor e mais limpa. Com o campeão no threshold (16% a 29%) e o canário em 30%, o viés é a favor do canário. É aceito porque a taxa de disparo deixou de ser escolha do modelo, mas a leitura do resumo do canário precisa levar isso em conta.
- **Empate na fronteira do K** é decidido pela ordem de chegada da fila. Uma linha não avisada pode ter a mesma probabilidade do corte, e a `posicao_na_fila` desempata a auditoria.
- A migration de `predicoes` precisa ser aplicada antes do deploy do código.

## Alternativas consideradas
- **Recalibrar o threshold** para a escala do modelo calibrado (por exemplo, 0,35). A fração avisada continuaria variando com a fila e com cada re-treino, e o número precisaria ser revisto a cada promoção.
- **Decidir por valor esperado** (avisar quando P(falta) × eficácia × R$ 180 > custo do SMS). Com a razão de cerca de 600 para 1, avisaria todo mundo, o que viola o orçamento do BRIEFING. Volta a ter sentido quando a eficácia do lembrete for medida e houver canais de custos diferentes.
- **Orçamento mensal acumulado** (K variável conforme o gasto do mês). É mais fiel ao teto em dólar, mas faria o mesmo paciente ser avisado ou não conforme o dia do mês. Fica para quando houver volume de várias clínicas.
- **Ler o modo do `params.yaml` para o campeão.** Seria mais simples, mas qualquer edição do `params.yaml` mudaria o modelo em produção sem gate. Com a guarda estrita, a promoção pelo canário ficaria impossível: o job recusaria rodar durante todo o canário.

## Referências
- [ADR-008](adr-008-canario-do-modelo.md) (emenda de 2026-11-22), [ADR-015](adr-015-familia-do-modelo-e-calibracao.md).
- [SLA §4.1](../SLA.md#41-curva-de-custo-por-fração-da-fila): curva de custo com a premissa declarada.

## Changelog
- 2026-11-22: aceito, com o mecanismo implementado e o campeão complementado no critério novo. A promoção da configuração recomendada fica pendente.
