# ADR-003: Melhoria do F1 por meio de ____

## Status
Aceito - 2026-09-08. Emendado em 2026-11-21: a conclusão foi medida num único split e não se sustenta na validação cruzada repetida (ver a Emenda no fim deste documento).

## Contexto
Melhoria da métrica F1 da classe positiva, que é a métrica de interesse do projeto. Atualmente, o modelo tem F1 = 0.65, e queremos melhorar isso. As proppostas de melhoria incluem comparação entre método SMOTE e balanceamento de classe (class_weight=balaced).
Um adendo importante:
Foi verificado que o projeto herdado possui, na verdade, possui F1 de 0.235 para a classe predita (no-show = 1), e não 0.61 como indica nas anotações da Camila.
Por precaução, foi baixado o dataset novamente do repo de origem, executando o notebook Jupyter em Google Colab para confirmar os valores.

![alt text](adr-003-discrepancia.png)

Presseguiremos com a melhoria do F1 com as abordagens mencionadas. A condição "balanced=none" corresponde ao treinamento base herdado (igual ao descrito no notebook.ipynb da Camila).

## Decisão
A melhor método de melhoria do F1 da classe positiva foi por meio de SMOTE-NC.

### Metodologia:

Passo 1 — Criar params.yaml
Extrai os hiperparâmetros fixos do train.py e adiciona a nova dimensão que queremos comparar: a estratégia de balanceamento.

Passo 2 — Dependência nova
Adicionar imbalanced-learn em requirements.txt (é o pacote que traz SMOTENC), e copiar params.yaml para dentro da imagem em dockerfile.

Passo 3 — Declarar os params no dvc.yaml

Passo 4 — Ajustar train.py
Inclui os três caminhos de balanceamento (none/balancing=class_weight/balancing=smotenc)

Passo 5 — Rodar os três experimentos
Com dvc exp run, você sobrescreve o params.yaml só para aquela execução, sem tocar no Git:


- dvc exp run -S balancing.strategy=none            # baseline, sem tratamento
- dvc exp run -S balancing.strategy=class_weight
- dvc exp run -S balancing.strategy=smotenc -S balancing.k_neighbors=3


Passo 6 — Comparar os resultados
No terminal, via DVC (rápido, resume tudo numa tabela):

- dvc exp show --only-changed

Mostra params (balancing.strategy) e métricas (f1_1, pr_auc, recall_1...) das três execuções lado a lado.

No MLflow UI, para análise mais profunda (curvas, artefatos, comparação visual):

Acesse http://localhost:5000 diretamente no navegador; é o próprio servidor MLflow servindo a UI.

Filtre o experimento saudeja-no-show, selecione os 3 runs e use "Compare" — dá pra ver f1_1, pr_auc, recall_1 em gráfico de barras lado a lado, filtrando por params.balancing.

#### F1 da classe positiva nas três condições testadas
![alt text](adr-003-f1_1.png)

#### Recall da classe positiva nas três condições testadas
![alt text](adr-003-recall_1.png)

#### ROC_AUC nas três condições testadas
![alt text](adr-003-roc_auc.png)

#### Todos as metricas avaliados para classe psoitiva (none/balanced/SMOTE-NC)
![alt text](adr-003-roc_auc.png)


Experimentos gerados:

NONE (baseline): http://localhost:5000/#/experiments/1/runs/b392dfb413554472b88c7448c576b444

class_weight: http://localhost:5000/#/experiments/1/runs/4ebbb8db627b4695962ec5204bf10a09

smotenc : http://localhost:5000/#/experiments/1/runs/7be193263fa949e692b4baeef0f42cac

smotenc_2 : http://localhost:5000/#/experiments/1/runs/6c2f69fbae3e4a69b73861059f1067c7


Passo 7 — Promover o vencedor
Depois de decidir (com base em f1_1 da classe positiva + pr_auc, priorizando o que faz mais sentido para o custo de negócio — falso negativo de no-show custa mais que falso positivo):


- dvc exp apply <nome do experimento>
- git add params.yaml dvc.yaml dvc.lock requirements.txt dockerfile src/train.py
- git commit -m "..."

Isso materializa a escolha como um commit normal, e os outros experimentos ficam guardados no cache do DVC (recuperáveis com dvc exp show) sem terem sujado o histórico.

### Conclusão

O SMOTE-NC mostrou o melhor desempenho dentre as condições testadas, com F1_1 de 0.387 (comparado ao 0.261 do treinamento base) e recall de 0.522 (comparado a 0.261 do baseline).

## Consequências
    Pro:
    - F1_1 e recall_1 melhorado.


## Alternativas consideradas
O SMOTE clássico e as suas outras variações foram consideradas:
- Borderline-SMOTE/ADASYN: Fazem sentido quando o desbalanceamento é severo (ex. 95/5) e há muita sobreposição de classes. Aqui o desbalanceamento é moderado (73/27) — a agressividade extra tende a criar ruído artificial sem ganho real. Descartada.
- SMOTEN (só categóricas): não serve, porque a maioria das features aqui é numérica.
- K-Means SMOTE: agrupa antes de gerar sintéticos; com poucas dezenas de positivos por grupo, os clusters ficam instáveis e pouco confiáveis.
- SVMSMOTE: tende a instabilidade em amostras pequenas.

## Emenda (2026-11-21): reavaliação com validação cruzada repetida

As conclusões acima foram medidas num único split 80/20 (`random_state=42`), com 76 linhas e 21 positivos no fold de teste. Nesse tamanho, um paciente move `recall_1` em cerca de 0,048, e trocar só a semente do split leva o `roc_auc` do mesmo pipeline de 0,51 a 0,68. A diferença de `f1_1` entre SMOTE-NC e baseline (0,387 contra 0,261) cabe dentro dessa variação.

A mesma comparação foi refeita com `scripts/avaliar_cv.py`: `RepeatedStratifiedKFold` (5 folds × 5 repetições) sobre as 380 linhas de `data/consultas-treino.csv`, com o pré-processamento do pipeline, os hiperparâmetros de `params.yaml`, o balanceamento dentro de cada fold e o threshold vigente (0,6). Média ± desvio-padrão dos 25 folds e intervalo de 95% com a correção de Nadeau-Bengio (os folds não são independentes):

| Configuração | roc_auc | IC 95% | pr_auc | f1_1 @0,6 | recall_1 @0,6 | recall com 30% da fila avisada |
|---|---|---|---|---|---|---|
| LightGBM + SMOTE-NC (vigente) | 0,555 ± 0,060 | 0,488 a 0,622 | 0,351 | 0,263 | 0,234 | 0,340 |
| LightGBM + `class_weight="balanced"` | 0,548 ± 0,071 | 0,469 a 0,628 | 0,334 | 0,237 | 0,210 | 0,348 |
| LightGBM sem balanceamento | 0,552 ± 0,072 | 0,472 a 0,632 | 0,341 | 0,033 | 0,019 | 0,360 |

Referências do acaso: `roc_auc` 0,5, `pr_auc` 0,274 (a prevalência) e recall de 0,30 avisando 30% da fila ao sorteio.

O que muda na leitura desta decisão:

- **O SMOTE-NC não supera o `class_weight`.** As três condições têm a mesma capacidade de ordenar pacientes por risco (`roc_auc` e `pr_auc` iguais dentro do desvio). O balanceamento só desloca as probabilidades para cima, o que muda quantos passam do threshold. O ganho de `f1_1` e `recall_1` registrado em 2026-09-08 é o efeito desse deslocamento somado à sorte do split, e o `class_weight` obtém o mesmo deslocamento sem gerar linhas sintéticas.
- **O intervalo do `roc_auc` vigente inclui 0,5.** Na semente sintética atual, não há evidência estatística de que o modelo ordene melhor que o sorteio.
- **Nenhuma das três condições tem Brier melhor que o modelo que responde sempre a taxa base (0,199).** As probabilidades não estão calibradas, o que precisa ser resolvido antes de qualquer decisão por custo que use a probabilidade.

O SMOTE-NC continua em produção até que um desafiante o substitua pelo fluxo normal (gate e, se a família do modelo mudar, ADR próprio). A partir desta emenda, comparação entre configurações de modelo usa `scripts/avaliar_cv.py`, e o fold de teste isolado (`dvc exp run`) fica para registrar o número oficial da configuração escolhida.

