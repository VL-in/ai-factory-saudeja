# ADR-014: Dataset externo de desfecho real como terceira origem de dado

## Status
Proposto - 2026-11-21. Passa a Aceito quando o pool adaptado, o teste reservado e a amostra de treino forem gerados e passarem nos testes de dados (`pytest -m dados_externos`), e o teste de sinal tiver resultado registrado aqui. O uso no treino oficial (`dados.externa.habilitada: true`) depende de emenda a este ADR.

As palavras-chave **DEVE**, **NÃO DEVE** e **PODE** seguem o sentido da [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119) (MUST, MUST NOT, MAY).

## Contexto
O modelo de no-show foi treinado e avaliado até aqui só com a semente sintética herdada: 380 linhas, 104 faltas (27,4%) e uma semana de datas. A emenda do [ADR-003](adr-003-SMOTE-NC.md) mostrou, em validação cruzada repetida, que o `roc_auc` do modelo vigente é 0,555 ± 0,060, com intervalo que inclui 0,5. Não há evidência de que o modelo ordene os pacientes por risco melhor que um sorteio. O dado é a causa, não o código: nenhuma feature sozinha passa de AUC 0,60, e o único recorte forte da semente ("sexta, 17h-18h") foi injetado pelo script que gerou os horários.

A saída é dado real com desfecho, e aí estão os problemas:

- **A produção tem poucos desfechos.** O SaúdeJá é projeto de estudo. O ambiente de produção existe, mas o volume real é pequeno, e a janela de 90 dias da base de treino ([ADR-009](adr-009-tratamentos-lgpd-centralizados.md), [ADR-010](adr-010-base-de-treino-separada.md)) limita o dataset a cerca de 900 linhas por clínica mesmo em regime.
- **As regras pensadas para quando o dado real chegar não podem ser testadas sem ele:** reduzir o peso da semente, contar os positivos reais no fold de teste, separar o split por paciente quando o identificador for estável, criar features por paciente sem vazar futuro.
- **Gerar mais dado sintético foi considerado e descartado.** Um gerador só reproduz o que já sabe, o modelo aprenderia o gerador, e a validação cruzada mediria esse aprendizado como se fosse sinal (ver Alternativas).

Encontramos um dataset público com desfecho real e contexto brasileiro: o *Medical Appointment No Shows* (Kaggle, `joniarroba/noshowappointments`), com consultas da rede pública de Vitória-ES em 2016, licença CC BY-NC-SA 4.0. Ele traz idade, sexo, data do agendamento, data da consulta, se o paciente recebeu SMS e se faltou.

Auditoria do arquivo baixado (sha256 começando em `9132d3e7d0246617`):

| Item | Valor |
|---|---|
| Linhas / colunas | 110.527 / 14, sem nulos, sem consulta duplicada |
| Taxa de falta (arquivo todo) | 20,2% |
| Datas de consulta | 29/04 a 08/06/2016, sem hora |
| Consultas do mesmo dia | 38.563, com 4,6% de falta |
| Antecedência de 1 a 90 dias | 71.742, com 28,5% de falta |
| População do SaúdeJá (1 a 90 dias, 0 a 85 anos) | **70.985 linhas, 28,6% de falta, 45.209 pacientes** |
| SMS na população | 49,4% receberam; nenhum SMS com antecedência de 1 ou 2 dias |
| Sábado na população | 30 consultas |

Forças em jogo:

- **Licença NC-SA.** Não comercial e com derivados sob a mesma licença. O SaúdeJá é projeto de estudo e o modelo não atende caso real (decisão da autora), então o uso é compatível. A atribuição e a licença dos derivados continuam obrigatórias.
- **Domínio diferente.** Rede pública e gratuita, 2016, outra cidade. A prevalência na população do SaúdeJá (28,6%) é próxima da semente (27,4%), mas o perfil e o motivo da falta não são os de uma clínica privada.
- **Features ausentes.** O dataset não tem especialidade, distância nem horário da consulta.
- **SMS confundido com a antecedência.** Só recebeu SMS quem marcou com 3 dias ou mais. Sem separar por antecedência, o efeito do SMS sai distorcido.
- **LGPD.** O dataset é público e pseudonimizado, mas não anonimizado: traz identificador de paciente, bairro e condições de saúde (dado sensível, art. 11). E esses pacientes não são titulares do SaúdeJá, não consentiram o treino do [ADR-010](adr-010-base-de-treino-separada.md).
- **Pipeline existente.** O `export_treino.py` reescreve o `data/consultas-treino.csv` todo mês como semente + produção. O `validate_data` chama de produção tudo que não está na semente. Uma linha externa colada no dataset seria apagada no export seguinte e, enquanto existisse, seria contada como produção da clínica.

## Decisão
Adotamos o *Medical Appointment No Shows* como uma **terceira origem de dado, `externa`**, ao lado de `semente` e `producao`. O dado externo é desfecho real, mas não é desfecho do SaúdeJá, e cada regra do pipeline diz explicitamente se conta com ele.

1. **População.** Só entram consultas com antecedência de 1 a 90 dias e idade de 0 a 85 anos, as faixas do domínio do contrato de features. Consulta do mesmo dia **NÃO DEVE** entrar: o job D-2 nunca a prediz, e mantê-la inflaria o `roc_auc` com uma separação que o produto nunca usa.
2. **Mapeamento.**
   - O id da consulta vira `K-<AppointmentID>`, e o do paciente vira um pseudônimo sha256 com sal local (`K` + 16 hexadecimais).
   - O `historico_noshow` é calculado *point-in-time*: conta as faltas do paciente em consultas anteriores à data do agendamento atual, sobre as 110 mil linhas e antes de qualquer amostragem.
   - O SMS vira `lembrete_enviado`, que **NÃO DEVE** ser feature de predição.
   - As colunas de saúde, `Scholarship` e o bairro **NÃO DEVEM** sair do bruto.
3. **Features ausentes.** Especialidade, distância e horário são sorteados da distribuição da semente, **independentes do desfecho**. O horário respeita a grade da clínica no dia real da consulta. A coluna de metadado `colunas_imputadas` registra o que foi sorteado, e os testes de sinal **DEVEM** excluir essas colunas.
4. **Tamanho.**
   - O bruto inteiro é usado só na adaptação.
   - O pool adaptado (~71 mil) serve ao teste de sinal e à simulação.
   - Um teste reservado de ~10 mil linhas, com pacientes disjuntos, serve só para a avaliação final.
   - A amostra de treino tem **1.500 linhas** (`dados.externa.n_amostra`): passa dos limiares de 200 e 600 linhas reais da regra de peso da semente, dá cerca de 85 positivos no fold de teste e fica na ordem do teto de 90 dias de uma clínica.
5. **Parâmetro desligado.** `dados.externa.habilitada` **DEVE** ser `false` até a emenda que libera o uso no treino oficial. Com ele desligado, nenhuma linha externa **PODE** estar em `data/consultas-treino.csv`.
6. **Rastreio.** Todo run do MLflow que usar a origem externa **DEVE** ter a tag `dados_externos=true`. Os derivados ficam em `data/externo/`, fora do git, versionados por DVC, com `LICENCA-E-ORIGEM.md` e um manifesto (sha256 do bruto, remoções por filtro, contagens, `random_state`).

Quais origens contam em cada regra:

| Regra | semente | externa | produção |
|---|---|---|---|
| Peso da semente: contagem de desfecho real | — | conta | conta |
| Split cronológico: dias de operação | — | não | conta |
| Positivos de produção no fold de teste | — | não | conta |
| Mínimo de positivos no fold (`validate_data`) | conta | conta | conta |
| PSI (alerta) | referência | comparada à semente | comparada à semente |
| Canário e falta não avisada ([ADR-008](adr-008-canario-do-modelo.md)) | — | não | conta |

### Metodologia

Passo 1 — Contrato antes do dado
As regras de schema e de negócio moram em `src/contrato_dataset_externo.py`, já implementado. Os testes com dado fictício (`tests/test_contrato_dataset_externo.py`) provam que cada regra pega a falha que promete pegar. Os testes de dados (`tests/test_dataset_externo_dados.py`, marcador `dados_externos`) rodam as mesmas regras contra os arquivos de `data/externo/` assim que cada um existir.

Passo 2 — Parâmetro e licença
Bloco `dados.externa` no `params.yaml` (`habilitada: false`, `n_amostra: 1500`, `random_state`). Bruto e derivados no DVC, com `data/externo/LICENCA-E-ORIGEM.md`.

Passo 3 — Adaptação
Script que lê o bruto, calcula o histórico *point-in-time*, aplica o filtro de população, mapeia as colunas, sorteia as features ausentes, passa tudo pelo contrato de features e grava o pool e o manifesto.

Passo 4 — Partição
Teste reservado por sorteio de pacientes, e amostra de treino dos pacientes restantes.

Passo 5 — Sinal
`scripts/avaliar_cv.py` com validação por paciente (`StratifiedGroupKFold`), só com as features reais e com teste de permutação. Os critérios ficam fixados antes de rodar:

- limite inferior do IC 95% do `roc_auc` acima de 0,60, o piso do gate;
- limite inferior do lift com 30% da fila acima de 1,3;
- permutação com p < 0,01.

Como os critérios são medidos, fixado junto com eles (2026-11-21, antes de qualquer rodada):

- **Conjunto:** a amostra de treino (`consultas-externas.csv`, 1.500 linhas), que é o volume do produto. O pool inteiro dá o teto do sinal e é reportado, mas não decide.
- **Modelo:** regressão logística regularizada (`C` 0,3) com `class_weight="balanced"`, a configuração simples que o diagnóstico do modelo indicou para poucas linhas. O LightGBM com `class_weight` é reportado como análise de sensibilidade, sem decidir.
- **Features:** só as reais (`idade`, `sexo`, `dias_entre_agendamento_consulta`, `historico_noshow`, `dia_de_semana`). As sorteadas ficam de fora.
- **Validação:** `StratifiedGroupKFold` por `id_paciente`, 5 folds × 5 repetições, intervalo com a correção de Nadeau-Bengio.
- **Permutação:** 200 embaralhamentos de `no_show`, cada um em 5 folds × 1 repetição, comparando a média de `roc_auc`. O p-valor é (1 + nº de permutações com AUC ≥ a real) / 201.
- **Robustez:** mais duas amostras de 1.500 com sementes diferentes. O resultado é "passa" se as três passarem nos três critérios, "não passa" se nenhuma passar, e "inconclusivo" nos outros casos.

O resultado entra neste ADR, passe ou não.

**Resultado (2026-11-21): não passa.** Pela regra pré-registrada, nenhuma das três amostras de 1.500 passou nos três critérios:

| Amostra (logística, features reais, CV por paciente 5×5) | `roc_auc` [IC 95%] | lift@30% [IC 95%] | permutação (200) |
|---|---|---|---|
| oficial (`consultas-externas.csv`) | 0,570 [0,536; 0,604] | 1,22 [1,09; 1,35] | p = 0,010 (não < 0,01) |
| semente de amostra 1 | 0,614 [0,576; 0,652] | 1,34 [1,18; 1,51] | p = 0,005 |
| semente de amostra 2 | 0,575 [0,543; 0,606] | 1,17 [1,05; 1,29] | p = 0,005 |

Leitura:

- **Existe sinal, e ele não é sorte**: as três AUCs ficam acima de 99% das permutações. Mas ele é **fraco demais** para o critério: nenhum limite inferior passa de 0,60 em `roc_auc` nem de 1,3 em lift.
- **O volume não é o gargalo.** No pool de simulação inteiro (60.985 linhas, sem nenhum paciente do reservado), o teto fica em `roc_auc` 0,580 [0,575; 0,585] com a logística e 0,595 [0,589; 0,601] com o LightGBM (lift 1,31 [1,28; 1,33]). Nem com 40 vezes mais linhas as cinco features reais chegam ao piso de 0,60.
- **A variação entre amostras é grande** (0,570 a 0,614 só trocando a semente do sorteio), o que confirma o risco R11. Na amostra oficial, as mulheres faltam 30% e os homens 25%; no pool, a diferença não existe.
- **Sensibilidade:** na amostra oficial, o LightGBM com `class_weight` fica abaixo da logística (0,529 [0,496; 0,563]). Com 1.500 linhas, o modelo simples ganha.
- **AUC univariada no pool** (0,5 é sorteio): idade 0,433 (ordena ao contrário: idosos faltam menos), antecedência 0,558, dia da semana 0,516, sexo 0,503, `historico_noshow` 0,503 (truncado, R5).
- **Comparação com a semente** (mesma configuração, sem grupo, porque o id da semente não é uma pessoa): `roc_auc` 0,617 [0,545; 0,689]. Parece mais alto, mas o intervalo é quatro vezes mais largo, e a AUC univariada do dia da semana na semente (0,599) vem do padrão "sexta 17h" injetado, que no dado real não existe (a sexta falta 29,5% contra 28,4% nos outros dias).

Consequência, como previsto na leitura do plano: **a prioridade passa a ser coletar features novas** (por paciente, item 5.5 da melhoria do modelo, e outras que o produto ainda não pede), e não só acumular linhas. Mais linhas do mesmo tipo levam o modelo de ~0,57 para ~0,58-0,60, não além disso.

Passo 6 — Simulação
Três estudos:

- curva de aprendizado com dado real, avaliada no teste reservado, para saber quantas linhas são necessárias;
- recuperação de um sinal conhecido, para provar que o pipeline não perde sinal;
- mistura da semente com dado real em pesos diferentes, para testar os limiares da regra de peso da semente.

**Resultado (2026-11-21, `scripts/simular_dados.py`, 10 sorteios por ponto, sempre avaliado no teste reservado de 10.000 consultas):**

*Curva de aprendizado, features reais.* `roc_auc` médio [faixa de 95% dos sorteios]:

| Linhas de treino | Logística | LightGBM |
|---|---|---|
| 100 | 0,522 [0,463; 0,570] | 0,528 [0,467; 0,576] |
| 200 | 0,544 | 0,538 |
| 400 | 0,557 | 0,544 |
| **900** (~90 dias de uma clínica) | **0,566** [0,550; 0,574] | **0,553** [0,547; 0,561] |
| 1.500 | 0,569 | 0,564 |
| **3.600** (~12 meses) | **0,572** [0,568; 0,576] | **0,578** [0,574; 0,583] |
| 6.000 | 0,572 | 0,583 |
| pool inteiro (60.985) | 0,574 | 0,599 |

- Nenhum tamanho de treino passa de 0,60 em nenhum sorteio. A logística satura perto de 900 linhas; o LightGBM ainda sobe, mas devagar.
- **De 90 dias para 12 meses**, o ganho é de +0,006 (logística) a +0,025 (LightGBM) em `roc_auc`, e de 1,18 para 1,17-1,23 em lift. Com estas features, guardar 12 meses em vez de 90 dias **não** muda a classe de desempenho do modelo. É o número que faltava para a decisão de LGPD da janela de treino: o argumento técnico para ampliar a janela é fraco enquanto as features forem estas.

*Recuperação de sinal conhecido.* O rótulo foi sorteado de uma logística com AUC verdadeira conhecida, sobre linhas reais do pool (inclusive especialidade e dia da semana, para exercitar o encoding). O pipeline recupera 93% a 99% da AUC do oráculo com a logística e 84% a 96% com o LightGBM, crescendo com o volume. **O pipeline não perde sinal** (não há erro de encoding nem de contrato). E dá a curva de poder: com AUC verdadeira de 0,65, a logística passa de 0,60 em 100% dos sorteios a partir de 900 linhas, e o LightGBM só a partir de 3.600. Com AUC verdadeira de 0,60, nenhum modelo passa do piso. O problema medido na Fase 4 é o sinal do dado, não o pipeline. (O modelo verdadeiro é linear, o que favorece a logística nesta comparação.)

*Mistura semente + real.* Treino com a semente (peso 1, 0,3 ou 0) mais n linhas reais, com todas as features do pipeline:

- A semente sozinha, avaliada no dado real, fica em 0,519 (logística) e 0,508 (LightGBM): o que ela ensina **não se transfere** para o dado real.
- Em todos os tamanhos (100 a 1.500 linhas reais), as faixas dos três pesos se sobrepõem. A maior diferença é de +0,013 a favor de manter a semente com 200 linhas reais na logística, dentro da variação entre sorteios. **Não há evidência de que a semente atrapalhe**, nem de que ajude.
- **Os limiares da regra de peso da semente (200, 600 e 0,3) ficam como estão.** O dado não pede limiares menores, e retirar a semente a partir de 600 linhas reais não custa nada mensurável. Limitação: no teste reservado, as features imputadas são ruído, então um padrão fabricado de horário que a semente ensine não tem como aparecer como prejuízo aqui.

## Consequências

Positivas:

- O teste de sinal e a escolha de modelo passam a ser feitos com desfecho real, e não sobre um padrão que a própria autora fabricou.
- As regras de peso da semente, de positivos reais no fold, de split por paciente e de features por paciente passam a ter dado com que ser testadas.
- A curva de aprendizado dá o número que faltava para discutir a janela de 90 dias com dado, e não com estimativa.
- O padrão fabricado da semente ("sexta, 17h-18h") fica diluído quando o dado externo entrar no treino.

Negativas e riscos aceitos:

- **Domínio diferente.** Métricas sobre o dado externo **NÃO DEVEM** ser apresentadas como desempenho do SaúdeJá. A frase certa é "sinal em dado público real". Métricas e PSI são reportados por origem.
- **Features sorteadas são ruído** nas linhas externas, e o modelo pode se ajustar a esse ruído (como já se ajusta à idade). A proteção é a regularização do modelo e a exclusão dessas colunas nos testes de sinal.
- **O histórico é truncado.** As consultas do Kaggle começam em 29/04/2016, então quase todo `historico_noshow` externo é 0. O efeito dessa feature **NÃO DEVE** ser comparado entre origens.
- **O SMS do Kaggle não mede o lembrete do SaúdeJá.** Outro mecanismo de envio, outra época, outra rede. Ele **NÃO DEVE** ser usado para calibrar a eficácia do lembrete.
- **A regra de split cronológico (mais de 60 dias de produção) não é testável com este dado.** As consultas cobrem cerca de 6 semanas, e reescrever as datas para esticar o período seria falsificar o dado. Essa regra é testada com fixture.
- **Campeão e desafiante deixam de ser comparáveis** se um for medido com dado externo e o outro não. Antes do primeiro gate com `habilitada: true`, o campeão **DEVE** ser medido de novo no dataset novo.
- **O pipeline ganha uma coluna de origem.** `export_treino.py` e `validate_data.py` passam a contar por origem, em vez de por "não está na semente".

## Alternativas consideradas

- **Gerar mais dados sintéticos.** Descartada. O gerador só contém a informação que já tinha, e o modelo aprenderia o gerador. Se o gerador partisse das 380 linhas, quase-duplicatas cairiam em folds diferentes e inflariam a validação cruzada que a emenda do [ADR-003](adr-003-SMOTE-NC.md) acabou de tornar confiável. É também a mesma ideia do SMOTE-NC, que a emenda mostrou não melhorar a ordenação.
- **Colar as linhas externas no `data/consultas-treino.csv` como se fossem produção.** Descartada. O export mensal as apagaria, o `validate_data` as contaria como produção da clínica, e as regras de canário e de positivos de produção disparariam por engano.
- **Inserir as linhas na base de treino do Supabase.** Descartada. A purga de 90 dias as apagaria, e a base de treino é, por desenho ([ADR-010](adr-010-base-de-treino-separada.md)), só de titulares do SaúdeJá que consentiram.
- **Esperar só a produção.** Descartada como única via. Com o volume de um projeto de estudo e a janela de 90 dias, o sinal real levaria meses para aparecer, e as regras ficariam sem teste até lá. A produção continua sendo a origem que importa: o dado externo é ponte, não substituto.
- **Usar as 110 mil linhas sem filtro.** Descartada. As consultas do mesmo dia têm 4,6% de falta contra 28,5% no resto, e mantê-las mediria uma separação que o produto nunca usa.
- **Preencher as features ausentes de forma uniforme.** Descartada. A distribuição diferente da semente denunciaria a origem da linha, e o modelo poderia aprender "linha com cara de externa → prevalência de 2016" (aprendizado por atalho).
- **Derivar a distância do bairro, ou marcar toda consulta como clínica geral.** Descartada. O dataset não diz onde fica a unidade de saúde, então a distância seria inventada. Uma especialidade única ficaria colada à origem e viraria o mesmo atalho do item anterior.

## Referências
- Dataset: [Medical Appointment No Shows](https://www.kaggle.com/datasets/joniarroba/noshowappointments), licença [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
- Nadeau, C.; Bengio, Y. *Inference for the Generalization Error*. Machine Learning, 52, 2003 (correção do intervalo da validação cruzada repetida).
- [ADR-003](adr-003-SMOTE-NC.md) (emenda de 2026-11-21), [ADR-008](adr-008-canario-do-modelo.md), [ADR-009](adr-009-tratamentos-lgpd-centralizados.md), [ADR-010](adr-010-base-de-treino-separada.md).
- LGPD, art. 11 (dado pessoal sensível) e art. 12 (dado anonimizado).

## Changelog
- 2026-11-21: aberto como Proposto, com a auditoria do bruto e o contrato do dataset externo já implementado.
- 2026-11-21: bloco `dados.externa` no `params.yaml` (desligado, 1.500 linhas, `random_state` 42) e pré-registro completo do teste de sinal (conjunto, modelo, features, validação, permutação e regra de robustez), antes de qualquer rodada.
- 2026-11-21: resultado do teste de sinal (Passo 5): não passa. Sinal real e significativo, mas fraco; o teto no pool inteiro fica em `roc_auc` 0,58-0,60, e a prioridade passa a ser features novas, não só volume.
- 2026-11-21: resultado da simulação (Passo 6): curva de aprendizado satura abaixo de 0,60, de 90 dias para 12 meses o ganho é de 0,006 a 0,025; o pipeline recupera 93-99% de um sinal conhecido; a semente não atrapalha nem ajuda, e os limiares 200/600/0,3 ficam.
