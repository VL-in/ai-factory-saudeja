# ADR-009: Workflow único para os tratamentos LGPD, com credencial própria de exclusão e consentimento em tabela de eventos

## Status
Aceito — 2026-10-09. Ainda não implementado. Complementa a política de retenção do [LGPD.md](../LGPD/LGPD.md) (seção 5) e não altera o [ADR-008](adr-008-canario-do-modelo.md).

## Contexto
A revisão das bases legais do [LGPD.md](../LGPD/LGPD.md) (09/10/2026) criou obrigações que o sistema ainda não cumpre:

- O lembrete se apoia em consentimento (art. 7º, I), e o treino com desfechos reais também (art. 11, I). Não há registro de quando o consentimento foi dado nem campo de revogação. Pelo art. 8º, § 2º, o ônus de provar o consentimento é do controlador.
- As linhas de produção do dataset de treino têm prazo de retenção, mas o export não limita a janela de consultas, e nada apaga versões antigas do remote do DVC.
- A eliminação a pedido do titular (art. 18, VI) é uma operação manual no banco e não alcança as versões do dataset que já estão no remote.

Forças em jogo:
- **A credencial de escrita do re-treino não pode apagar.** É uma decisão de segurança do LGPD.md (subseção 9.1, item 12): um workflow que roda código de PR não deve conseguir destruir o histórico. Cumprir a retenção exige alguém com permissão de exclusão.
- **O repositório é público.** Inputs de `workflow_dispatch` e logs do Actions são visíveis. O hash do CPF é reversível por força bruta (Quadro 2 do LGPD.md), então nem ele pode aparecer num disparo ou num log.
- **O DVC endereça por conteúdo.** Cada versão do dataset é o CSV inteiro, guardado sob o hash do arquivo. Não dá para tirar uma linha de uma versão antiga sem mudar o hash e quebrar o `.dvc` daquele commit.
- **O environment `production` não pode ter revisor obrigatório**, porque os jobs agendados (D-2, canário, re-treino) o usam e parariam esperando aprovação.
- **Para os dados de paciente, a SaúdeJá é operadora.** Ela só elimina por instrução da clínica (art. 39). A exceção é o treino de um modelo compartilhado entre clínicas, em que a SaúdeJá passa a ser controladora (LGPD.md, seção 1).
- Orçamento de US$ 100/mês e o princípio do [ADR-006](adr-006-observabilidade.md) de não acrescentar peça de infraestrutura.

## Decisão
Os tratamentos LGPD que exigem exclusão ficam centralizados num **workflow próprio, `lgpd.yml`**, com credencial de exclusão separada da credencial de escrita. O consentimento é registrado numa **tabela de eventos** no Supabase.

- **Disparo**: agendado (diário) e `workflow_dispatch` **sem nenhum input** que identifique o titular. O workflow processa o que estiver pendente no banco.
- **Credencial**: environment novo, `lgpd`, restrito ao branch `main`. Código de PR nunca chega a ele. Guarda uma SAS do container com leitura, listagem e **exclusão**, sem escrita, e com data de validade, o que força a rotação.
- **Pedidos do titular**: tabela `pedidos_titular` no Supabase, com tipo (`eliminacao`, `revogacao_treino`, `revogacao_lembrete`), situação (`pendente`, `confirmado_pela_clinica`, `executado`, `recusado`) e datas.
  - Só pedidos confirmados pela clínica são executados. A SaúdeJá pode receber o pedido do paciente, mas o encaminha à controladora.
  - Depois da execução, o pedido perde o vínculo com o paciente e guarda só tipo, situação e datas, como prova de atendimento sem dado pessoal.
- **Consentimento**: tabela `eventos_consentimento`, **só de inserção**, com paciente, finalidade (`lembrete`, `treino`), ação (`concedido`, `revogado`), versão do termo, canal, data e funcionário que registrou. O consentimento vigente é o último evento de cada finalidade. A coleta entra na tela de agendamento numa etapa posterior. Os filtros de consentimento no export e no job D-2 só são ligados junto com essa coleta: ligados antes, nenhum paciente teria consentimento registrado, os lembretes parariam e o treino ficaria sem linhas de produção.
- **Três fluxos no mesmo workflow**:

  | Fluxo | Gatilho | Supabase | Remote do DVC |
  |---|---|---|---|
  | Expiração | Agenda diária | — | Apaga versões que deixaram de ser vigentes há mais de 90 dias |
  | Revogação do consentimento do treino | Pedido confirmado | Evento `revogado` | Apaga as versões do dataset que contêm o titular |
  | Eliminação | Pedido confirmado | `delete` em `pacientes` (o cascade apaga agendamentos, predições, mensagens e eventos de consentimento) | Apaga as versões do dataset que contêm o titular |

- **Retenção no DVC: 90 dias.**
  - Toda versão de artefato (dataset, `model.pkl`, artefatos do canário) é apagada 90 dias depois de deixar de ser referenciada pelo HEAD de `main`.
  - A versão vigente nunca expira; só é apagada por eliminação ou revogação.
  - O export passa a incluir só consultas com até 90 dias.
- **Lista explícita de hashes, nunca `dvc gc`**: o workflow calcula os blobs a apagar a partir do histórico git dos arquivos `.dvc` e do `dvc.lock`. Recusa qualquer hash referenciado pelo HEAD de `main`, salvo na eliminação ou na revogação, e nunca toca na semente sintética.
- **O re-treino deixa de depender da versão vigente do dataset no remote**: o `retrain.yml` passa a puxar com alvo (`dvc pull data/consultas-historicas.csv`), porque o dataset é sempre remontado a partir do banco. Assim, apagar a versão vigente na eliminação não quebra o próximo re-treino.
- **O `model.pkl` não é retreinado na eliminação.** Ele não guarda linha de titular (Quadro 2 do LGPD.md). O risco residual de inferência de pertencimento fica registrado no LGPD.md, seção 9.
- **Logs**: só contadores (pedidos processados, blobs apagados). Nenhum identificador de paciente, nem o hash do CPF.

## Consequências
Pros:
- Os direitos de eliminação e revogação passam a ser atendidos por mecanismo, não por operação manual no banco. Fecha os itens 4, 13, 19 e 20 do LGPD.md, seção 9, e reduz o item 5.
- A política "o CI não apaga nada" do item 12 continua valendo para o CI, o deploy, o job D-2 e o re-treino. A única credencial que apaga está num environment que código de PR não alcança.
- A tabela de eventos é prova de consentimento (art. 8º, § 2º) e trilha de auditoria: guarda o histórico inteiro, inclusive revogações, em vez de só o estado atual.
- Os pedidos ficam registrados com data, o que permite medir o prazo de atendimento declarado no frontmatter do LGPD.md (15 dias).
- Nenhuma peça de infraestrutura nova: o workflow roda no Actions, o estado fica no Supabase (duas tabelas aditivas) e a exclusão usa a API do Blob Storage que já existe. Custo de infraestrutura praticamente zero.

Cons:
- **Uma credencial de exclusão a mais para guardar e rotacionar.** Se vazar, apaga o histórico do remote. É mitigado pela restrição a `main`, pela ausência de escrita (não substitui arquivos) e pela validade curta.
- **Versões antigas deixam de ser reproduzíveis.** `dvc pull` num commit com mais de 90 dias falha. O rollback de modelo continua possível, porque o campeão anterior deixou de ser vigente na promoção, bem dentro da janela.
- **A retenção efetiva de uma linha de produção passa de 90 dias.** A linha pode entrar no export até 90 dias depois da consulta, ficar na versão vigente durante o ciclo mensal e ainda permanecer 90 dias depois de a versão ser substituída: cerca de 210 dias no total. O LGPD.md declara esse máximo.
- **Commits antigos ficam com ponteiros para blobs que não existem.** É esperado e precisa estar documentado, para não ser confundido com falha do remote.
- **A eliminação depende de a clínica confirmar o pedido.** Se ela demorar, o prazo de 15 dias pode estourar sem que a SaúdeJá consiga agir.
- Mais estado para manter coerente: o cálculo dos hashes precisa conhecer todos os arquivos `.dvc`, inclusive as cópias que o canário guarda em `data/canario/`. Um arquivo esquecido apagaria um modelo vigente; por isso a recusa de hashes do HEAD é a guarda principal e precisa de teste próprio.

## Alternativas consideradas
- **Dar permissão de exclusão à SAS do re-treino**: descartado. Desfaz a proteção do item 12, porque um PR que altere o `retrain.yml` passaria a poder apagar o histórico.
- **Regra de ciclo de vida do Azure Blob Storage** (apagar blobs por idade): nenhuma credencial em workflow, mas descartado. O remote é endereçado por conteúdo, então a regra apagaria também o `model.pkl` vigente e a semente, a menos que cada artefato fosse para um container separado. Também não resolve a eliminação a pedido, que não depende de idade.
- **`dvc gc --cloud`**: descartado como mecanismo. Apaga tudo o que não está referenciado nos commits indicados, e uma chamada com os parâmetros errados apaga o modelo de produção. A lista explícita de hashes é verificável e testável.
- **Reescrever as versões antigas sem a linha do titular**: descartado. Muda o hash de cada versão e quebra o `.dvc` de todos os commits afetados, sem preservar a rastreabilidade que justificaria guardar a versão.
- **Consentimento em colunas de `pacientes`**: mais simples, mas descartado. Guarda só o estado atual: uma revogação apaga o registro do consentimento anterior, que é justamente a prova exigida pelo art. 8º, § 2º.
- **Executar os pedidos com o identificador no `workflow_dispatch`**: descartado. O repositório é público, e o input apareceria na página do run.
- **Runbook manual** (eliminação no banco e no remote feita à mão pelo administrador): continua possível como último recurso, mas não escala, não deixa trilha e depende de alguém lembrar das versões no remote.
