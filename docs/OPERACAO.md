# Operação do SaúdeJá

Guia de quem mantém o sistema em produção: pontos de decisão humana, ciclo de vida do modelo, alertas e rollback. Para instalar e rodar localmente, veja o [README](../README.md).

## Sumário

- [Onde o humano entra](#onde-o-humano-entra)
- [Ciclo de vida do modelo](#ciclo-de-vida-do-modelo)
- [Alertas](#alertas)
- [Rollback](#rollback)
- [Registros de incidente e de rollback](#registros-de-incidente-e-de-rollback)

## Onde o humano entra

| Quando | Quem | O que fazer | Se ninguém fizer |
|---|---|---|---|
| Todo dia, depois das consultas | Equipe da clínica | Registrar na "Fila do dia" quem compareceu e quem faltou | O re-treino não tem dado novo e falha (código 2). Esses desfechos são os rótulos do modelo |
| PR `dev` → `main` | Mantenedora | Revisar, subir a versão no CHANGELOG e fazer o merge | Nada vai para produção |
| PR do re-treino (canário) | Mantenedora | Conferir a tabela campeão × desafiante e a taxa de disparo projetada | O canário não entra |
| PR de promoção ou reversão do canário | Mantenedora | Conferir a evidência de produção e fazer o merge | Promoção: o modelo novo não vai ao ar. Nos dois casos, o próximo re-treino falha com código 4 enquanto houver canário em `main` |
| Workflow vermelho, ping ausente no Healthchecks ou alerta do Grafana | Mantenedora | Ler o resumo do run ou o alerta (ver [Alertas](#alertas)) | O problema continua em silêncio |
| Antes de o token do gatilho do job D-2 vencer | Mantenedora | Gerar um token fine-grained novo (só este repositório, "Actions: Read and write") e trocá-lo no Vault do Supabase de produção com `vault.update_secret` | O job volta a depender do cron do GitHub, atrasado, e o alerta P1 "fila D-2 não processada até 13h" dispara |
| Depois de merge do robô em `main` | Mantenedora | Back-merge `main` → `dev` (`gh pr create --base dev --head main`) | Conflito no CHANGELOG no próximo PR de release |

A proteção de `main` (PR obrigatório e checks do CI) é o que torna o merge um ponto de controle. O *environment* `production` não tem revisor obrigatório, porque ele também pararia os jobs agendados todo dia esperando aprovação.

## Ciclo de vida do modelo

O re-treino e o canário rodam a partir de `main` (o agendamento do GitHub só roda no branch padrão), e os PRs deles vão direto para `main`, sem passar por `dev`:

1. **Dia 1, `retrain.yml`.** Remonta o dataset com os desfechos registrados pela clínica, re-treina e compara com o campeão. Se o desafiante não regredir, publica o modelo no remote do DVC e abre o PR que o coloca como **canário**. O merge desse PR não redeploya o Space.
2. **Todo dia, `job_d2.yml` + `canario.yml`.** O job manda 20% da fila (sorteada por paciente) para o canário. A avaliação diária decide entre aguardar, promover ou reverter ([ADR-008](adr/adr-008-canario-do-modelo.md)).
3. **Promoção.** PR que reescreve `data/champion_metrics.json`, sobe a versão PATCH e, no merge, dispara o deploy de produção.
4. **Reversão.** Age sozinha: o job do dia seguinte já manda 100% da fila ao campeão. O PR só formaliza a decisão e impede o gate de aprovar o mesmo modelo de novo.

Os PRs automáticos pedem revisão a `vars.REVISOR_PRS_MODELO` ou, sem ela, ao dono do repositório. Códigos de saída do `retrain.yml`: gate bloqueou (1), sem dado novo (2), pipeline falhou (3), canário em observação (4).

## Alertas

Toda falha de workflow manda e-mail pelo GitHub. Cada workflow agendado também pinga o próprio check no Healthchecks.io e manda `/fail` quando falha. Se o GitHub desativar os crons por inatividade do repositório, é a **ausência** do ping que avisa ([ADR-006](adr/adr-006-observabilidade.md)).

| Workflow | Check do Healthchecks | O que significa a falha |
|---|---|---|
| `job_d2.yml` | `HEALTHCHECKS_JOB_D2_URL` | Pré ou pós-checagem do job: modelo que não é o campeão, contabilidade da fila divergente ou fila inteira em quarentena |
| `canario.yml` | `HEALTHCHECKS_CANARIO_URL` | A avaliação ou a abertura do PR falhou |
| `retrain.yml` | `HEALTHCHECKS_RETRAIN_URL` | Ver os códigos de saída em [Ciclo de vida do modelo](#ciclo-de-vida-do-modelo) |
| `alerta_observabilidade.yml` | `HEALTHCHECKS_ALERTA_URL` | Limite violado nas últimas 24h (erros, cobertura de explicação, latência) ou banco ilegível |
| `lgpd.yml` | `HEALTHCHECKS_LGPD_URL` | A purga falhou ou deixou de rodar, e pedidos do titular podem vencer o prazo de 15 dias |

Os números do dia a dia ficam na aba "Time técnico → Observabilidade" da área da equipe.

### Grafana Cloud

A interface, a API e o job D-2 enviam logs, métricas e traces ao Grafana Cloud por OpenTelemetry ([ADR-012](adr/adr-012-observabilidade-grafana-cloud-opentelemetry.md)). O envio fica desligado sem `OTEL_EXPORTER_OTLP_ENDPOINT` ou com `OBSERVABILIDADE_ATIVA=false`. Antes de sair do processo, o que é enviado passa pelo mesmo filtro de dados pessoais do log, e os traces perdem query string, IP e mensagem de erro. O Grafana guarda esses dados por 14 dias. Para os números de longo prazo, ele lê o Supabase pelo papel `grafana_leitura`, que só enxerga as views de totais do schema `observabilidade`. O preço do SMS e o orçamento ficam no bloco `custo` do [`params.yaml`](../params.yaml).

Os seis painéis ficam em [`infra/grafana/dashboards/`](../infra/grafana/dashboards/) e estão publicados por link (tabela no [README](../README.md#painéis-públicos)). As regras de alerta ficam em [`infra/grafana/`](../infra/grafana/) e são aplicadas pelo `grafana.yml` no fim de cada deploy em `main`. São 17 regras, cada uma com os rótulos `severidade`, `area` e `ambiente`, e só as de produção notificam:

| Criticidade | Exemplos | Notificação |
|---|---|---|
| P1 (crítico) | job D-2 sem sucesso até 13h, fila inteira em quarentena, agendamento deixado sem predição e sem lembrete, dado pessoal num log do Loki, chave fora da lista permitida em `eventos_app` | contact point de plantão, repetido a cada hora até resolver |
| P2 (alto) | erro de banco na interface, erro de predição, falha de SMS, predição sem explicação, disparos fora de ±40% da média, custo projetado acima de 80% do orçamento | e-mail, repetido a cada 24 h; com `area=modelo`, vai também à responsável pelo modelo |
| P3 (médio) | quarentena parcial, dado fora do domínio do treino, job acima de 10 min, p95 da predição acima de 2 s, banco perto da cota gratuita | resumo às segundas, das 9h às 10h |

Os limites ficam nos blocos `observabilidade` e `custo` do `params.yaml`. Os contact points são criados à mão no Grafana, porque e-mails não entram no repositório. A regra de dado pessoal no Loki foi testada de ponta a ponta em dev; as demais ainda não tiveram um alerta real por nível, e até lá os canais do GitHub e do Healthchecks continuam valendo.

### Disponibilidade

A disponibilidade do Space é medida de fora, pelo Synthetic Monitoring do Grafana Cloud, com uma sonda em São Paulo:

| Check | Rota | Intervalo | Alerta |
|---|---|---|---|
| `SaudeJa prod` | `https://vl-in-saudeja-prod.hf.space/_stcore/health` | 5 min | crítico (P1) com 2 falhas em 10 min, por e-mail à mantenedora; é a medida do [SLO §1.1](SLO.md#11-disponibilidade-da-interface) |
| `SaudeJa dev` | `https://vl-in-saudeja-dev.hf.space/_stcore/health` | 5 min | nenhum; só histórico, sem SLO |

Os dois são checks HTTP que esperam status 2xx **e** o corpo `ok`. Exigir 2 falhas seguidas evita alerta por uma falha isolada, como a troca de container num deploy. Com uma batida a cada 5 minutos o Space não fica ocioso tempo bastante para hibernar.

A rota de saúde da interface é `/healthz`, a mesma do smoke do deploy (`scripts/smoke_deploy.py`). `/_stcore/health` é um alias mantido pela interface só para esses dois checks, que ainda apontam para a rota da interface antiga ([ADR-013](adr/adr-013-interface-nicegui.md)). Quando os checks trocarem para `/healthz` no Grafana, o alias pode sair.

## Rollback

Escolha pelo que precisa voltar:

| O que voltar | Como | Age em | Precisa de PR? |
|---|---|---|---|
| Canário, na hora | variável `CANARIO_DESLIGADO=true` | próximo job D-2 | não (temporário) |
| Canário, de vez | `canario.yml` com `acao=reverter` | próximo job D-2 | sim, para registrar |
| Campeão já promovido | restaurar os ponteiros do campeão anterior | merge do PR | sim |
| Código ou interface | revert num branch a partir de `main` | merge do PR | sim |

### Canário (modelo em observação, ainda não promovido)

1. **Desligar na hora.** Defina a variável no environment `production`. O job D-2 seguinte ignora `data/canario/` e manda 100% da fila ao campeão. O canário continua em `main`, e por isso o re-treino seguinte sai com código 4: use isto como freio enquanto decide, não como solução.

   ```powershell
   gh variable set CANARIO_DESLIGADO --env production --body "true"
   ```

2. **Reverter de vez.** Rode o `canario.yml` com um motivo. Ele grava a reversão no banco (vale já no job seguinte, com ou sem merge), registra o modelo em `data/canario_historico.json` para o gate nunca mais aprová-lo e abre o PR.

   ```powershell
   gh workflow run canario.yml -f acao=reverter -f motivo="taxa de disparo acima do esperado"
   ```

3. Faça o merge do PR de reversão. Se tiver usado o passo 1, volte a variável para vazio depois do merge (`gh variable set CANARIO_DESLIGADO --env production --body ""`).

### Campeão (modelo já promovido)

Não há botão. Restaure, num PR para `main`, os três arquivos que apontam para o campeão anterior:

```powershell
git switch main; git pull
git switch -c rollback/modelo-anterior
# O commit que promoveu o modelo é o mais recente desta lista; o campeão
# anterior está no pai dele (<sha>^1).
git log --oneline --first-parent main -- data/champion_metrics.json
git checkout <sha>^1 -- data/champion_metrics.json dvc.lock data/consultas-treino.csv.dvc
python scripts/versao_release.py subir-patch --item "Rollback do modelo campeão para a versão anterior."
git add data/champion_metrics.json dvc.lock data/consultas-treino.csv.dvc docs/logs/CHANGELOG.md
git commit -m "(fix) data/champion_metrics.json, dvc.lock: rollback do modelo campeão"
git push -u origin rollback/modelo-anterior
gh pr create --base main
```

- O modelo antigo continua no remote do DVC por 90 dias depois de substituído: o workflow de purga ([ADR-009](adr/adr-009-tratamentos-lgpd-centralizados.md)) só apaga versões que deixaram de ser vigentes em `main` há mais tempo que isso. O rollback do campeão continua possível dentro dessa janela.
- O merge dispara o deploy, que confere o hash do modelo restaurado contra o `champion_metrics.json` antes de subir o Space. O job D-2 do dia seguinte já usa o modelo restaurado.
- O `champion_metrics.json` também guarda o threshold. Se `decision.threshold` em `params.yaml` mudou depois da promoção, restaure o valor antigo no mesmo PR, ou o deploy e o job D-2 recusam o modelo.
- Esse caminho não registra o modelo descartado em `data/canario_historico.json`, então o gate pode aprová-lo de novo num re-treino futuro.
- **Não use `git revert` do PR de promoção.** Ele apagaria a versão do CHANGELOG, e o deploy recusa uma versão que já tem tag. Na promoção de um canário, o revert também recriaria `data/canario/` e reativaria o modelo que acabou de ser tirado.

### Código ou interface

1. Num branch a partir de `main`, reverta o commit (`git revert <sha>`, ou `git revert -m 1 <sha>` se for o merge de um PR).
2. Se o revert desfez a seção da versão no CHANGELOG, restaure o arquivo (`git checkout HEAD~1 -- docs/logs/CHANGELOG.md`) e acrescente uma versão PATCH nova: `python scripts/versao_release.py subir-patch --item "Rollback de ..."`.
3. Abra o PR para `main` e, depois do merge, faça o back-merge `main` → `dev`.

- **Migrations não voltam com o código.** O `supabase db push` só aplica migrations novas. Se a versão revertida trouxe uma migration aditiva (coluna ou tabela nova), o código antigo convive com ela. Se a migration foi destrutiva, o caminho é uma correção para frente, não um revert.
- O `workflow_dispatch` do `deploy.yml` não serve para voltar a uma tag antiga: fora de `main`, ele escolhe o environment `dev`. Em `main`, ele só reenvia o commit atual.
- Este caminho foi ensaiado em `dev` no [RB-001](rollbacks/RB-001-ensaio-rollback-dev.md): 6 min 56 s do push do revert até o Space saudável, quase tudo de CI. Em produção, some o CI do PR e o do deploy.
- Para conferir o que está no ar, compare o digest da árvore do Space, não o SHA do commit: o `hub-sync` cria um commit novo a cada sync, mesmo com conteúdo idêntico. Os comandos estão no RB-001.

## Registros de incidente e de rollback

| Tipo | Pasta | Registros |
|---|---|---|
| Post-mortem | [`post-mortem/`](post-mortem/) | [PM-001](post-mortem/PM-001.md): falhas no primeiro deploy de dev (04/10/2026, SEV-3) |
| Relatório de rollback | [`rollbacks/`](rollbacks/) | [RB-001](rollbacks/RB-001-ensaio-rollback-dev.md): ensaio de rollback de código em dev (05/10/2026) |

Cada registro termina com uma tabela de ações.
