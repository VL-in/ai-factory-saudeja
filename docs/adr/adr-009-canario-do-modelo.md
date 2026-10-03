# ADR-009: Canário do modelo com rollback automático no job D-2

## Status
Aceito — 2026-10-01. Complementa o gate de promoção do re-treino ([`src/retrain_gate.py`](../../src/retrain_gate.py)): aprovar no gate deixa de ser promover.

## Contexto
O gate do re-treino mensal mede o desafiante **offline**, no fold de teste isolado (~76 linhas, 21 positivos), e, se não houver regressão além da tolerância, promove direto: o PR reescreve `data/champion_metrics.json` e o `dvc.lock`, e o merge leva o modelo a 100% da fila no dia seguinte. Não existia caminho de volta depois da promoção — o "gate de rollback" do SLA §3 impede promover um modelo pior, mas não desfaz um modelo que passou no gate e se comporta mal em produção.

O fold de teste não mede o que mais importa para o negócio: quantos pacientes da fila **real** o modelo manda para lembrete pago (o custo do BRIEFING, "cortar 70%") e quantos pacientes classificados como baixo risco faltam sem ter sido avisados (o erro que custa R$ 180). Esses dois números só existem com o modelo decidindo a fila.

A SaúdeJá atende várias clínicas particulares do Brasil (BRIEFING). Com o volume somado das clínicas, uma fração da fila diária basta para comparar dois modelos com significância em poucos dias — o argumento de volume que, com uma clínica só, desaconselharia o canário não se aplica ao produto.

Forças em jogo:
- Orçamento de US$ 100/mês e o princípio do ADR-006 de não acrescentar peça de infraestrutura.
- O HF Space publica uma porta só e não tem roteador ou divisão de tráfego.
- A inferência que conta é a batch, no job D-2, que roda no runner do GitHub Actions (emenda de 2026-09-30 no ADR-005). A UI só lê predições já gravadas.
- O job roda com `contents: read`: ele não consegue mexer no git sozinho.
- LGPD: o canário não pode abrir uma porta de saída de dado nova.

## Decisão
O desafiante aprovado pelo gate entra como **canário**: decide uma fração da fila do job D-2 (`canario.fracao`, hoje 20%), sorteada **por paciente** com hash estável, enquanto o campeão decide o resto. Os dois modelos são comparados no mesmo período por guardrails de **não-inferioridade** medidos no banco. O canário é promovido ou revertido por PR, e o rollback age antes do PR, no próprio job.

- **Onde vive**: `data/canario.json` (registro) e `data/canario/` (modelo por `.dvc` próprio, mais as cópias do `dvc.lock` e do `.dvc` do dataset do treino dele). `data/model.pkl` continua sendo o campeão em toda parte — deploy, Space, CI, guarda do `src/campeao.py` —, e o canário não entra no staging do deploy.
- **Guardrails** (`src/canario.py`):
  - `taxa_disparo`: fração da fila mandada para lembrete pago.
  - `falta_nao_avisada`: fração de faltas entre os pacientes de baixo risco com desfecho registrado. Só baixo risco entra, porque o lembrete altera o desfecho de quem o recebe.
  - `quarentena`: fração da fila que o modelo não conseguiu predizer, medida em cada execução.
  - Cada guardrail usa margem por métrica e um teste de diferença de proporções (Agresti-Caffo, z unilateral de 95%).
- **Decisão**:
  - Qualquer violação significativa reverte.
  - Promover exige não-inferioridade em todos os guardrails, com amostra mínima e pelo menos 7 dias.
  - Sem evidência até 21 dias, o canário é revertido: o conservador é ficar com o campeão.
- **Rollback em três camadas**:
  1. **Automático**: o job avalia os guardrails antes de rotear a fila. Na violação, grava `canarios_revertidos` no Supabase e manda a fila do dia 100% para o campeão.
  2. **Manual e instantâneo**: a variável do repositório `CANARIO_DESLIGADO=true`.
  3. **Formal**: o `canario.yml` abre o PR que remove `data/canario/` e registra o modelo em `data/canario_historico.json`, e o gate nunca mais aprova esse modelo.
- **Promoção**: PR do `canario.yml` que reescreve o campeão e restaura o `dvc.lock` do treino do canário. O merge dispara o deploy de sempre.
- **Re-treino**: com canário ativo, o gate sai com o código 4 sem treinar.

## Consequências
Pros:
- Rollback de verdade depois da promoção, sem rebuild do Space nem deploy. O campeão nunca sai de produção durante o canário, então reverter é só deixar de rotear, não restaurar nada.
- O modelo passa a ser julgado pelos números do negócio, medidos na fila real e contra o campeão no mesmo período. Sazonalidade e mudança de público afetam os dois braços igualmente.
- Nenhuma peça de infraestrutura nova: o roteamento está no job, o estado no Supabase (uma tabela aditiva) e as decisões em workflows do Actions.
- LGPD neutro: nenhum dado novo, nenhum operador novo. O sorteio usa o `id_paciente_externo`, que já é pseudônimo, e o identificador nunca vai para log.
- Um canário com qualquer problema (modelo inalcançável, campeão trocado, banco fora do ar) cai para 100% campeão e falha o run. Nunca deixa paciente sem lembrete.
- O `canarios_revertidos` e o `canario_historico.json` são trilha de auditoria permanente, ao contrário dos artifacts do Actions, que expiram em 90 dias.

Cons:
- **Uma fração dos pacientes recebe a decisão de um modelo em observação**. Ele passou no gate offline, mas ainda não foi provado na fila. Pela LGPD (Art. 20), o risco é baixo, porque a decisão é só enviar ou não um lembrete. Mesmo assim, convém informar a clínica-controladora no contrato do SaaS.
- **Com o volume de uma clínica só** (~10 agendamentos/dia no canário), a evidência não fecha em 21 dias e todo canário é revertido por prazo. O modelo deixa de evoluir até o volume crescer, ou até alguém ajustar `fracao`/`dias_maximos` ou desligar o canário (`canario.habilitado: false`, que volta à promoção direta).
- A promoção leva de 7 a 21 dias a mais e precisa de merge humano. Enquanto houver canário ativo, o re-treino do mês falha com o código 4.
- O guardrail de faltas depende de a clínica registrar o desfecho na "Fila do dia". Sem desfecho registrado, não há promoção.
- Mais estado para manter coerente. A promoção se recusa se o `dvc.lock` de `main` mudou desde o início do canário, e restaurar o lock salvo é a única forma de o modelo promovido ser alcançável.

## Alternativas consideradas
- **Blue-green no HF Space** (dois Spaces e troca de tráfego): descartado. Não há roteador; um proxy na frente veria nome do paciente e credenciais (operador novo, contra o ADR-007 e o ADR-006); a sessão do login fica em memória; e a inferência que importa nem passa pelo Space.
- **Rollback por reversão do PR de promoção**, sem canário: continua possível como último recurso, mas só age depois do estrago — o modelo ruim decide 100% da fila até alguém perceber e reverter. É reativo e manual.
- **Sombra** (o desafiante prediz tudo sem decidir nada): sem risco para o paciente, e mede taxa de disparo e quarentena. Mas não mede a falta não avisada, que exige o modelo decidir. Exigiria ainda mudar o schema, porque uma linha de sombra em `predicoes` faria o agendamento contar como "já predito" e entraria no `max()` da fila. Fica como etapa anterior possível, não como substituto.
- **Canário por clínica** em vez de por paciente: limita o impacto a clientes específicos e facilita a comunicação com a controladora. Ficou para depois por dois motivos. O schema ainda não tem a entidade clínica. E a comparação entre clínicas diferentes mistura o efeito do modelo com o perfil de cada clínica. Com o sorteio por paciente, os dois braços têm o mesmo perfil. `canario.braco()` recebe a unidade como texto, então a troca fica localizada.
- **Rollback só por PR**, com o job sem estado no banco: descartado. Entre a violação e o merge, o canário continuaria decidindo a fração dele por dias. A tabela `canarios_revertidos` é o que faz o rollback agir na execução seguinte.
