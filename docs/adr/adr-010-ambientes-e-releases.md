# ADR-010: Ambientes dev e produção separados e releases SemVer

## Status
Aceito — 2026-10-03, antes do primeiro deploy. Muda o gatilho e o destino do `deploy.yml` e o lugar dos secrets de todos os workflows que tocam produção.

## Contexto
Até aqui havia um projeto Supabase remoto só. O `.env` de quem desenvolve e o Space apontavam para ele. Um `supabase db push` rodado da máquina de desenvolvimento era uma migration de produção, sem ensaio possível. Isso já causou um incidente em 2026-09-28, quando a interface quebrou com `42703` porque a migration tinha sido aplicada só no banco local. Os secrets ficavam no nível do repositório, então qualquer workflow podia lê-los, inclusive um `workflow_dispatch` disparado num branch qualquer.

O deploy rodava em todo push em `main`, sem número de versão. O CHANGELOG numerava as entregas (`v1.0` a `v1.14`), mas nenhuma virou tag, e não havia como apontar qual commit estava em produção.

Forças em jogo:
- Orçamento de US$ 100/mês: o free tier do Supabase admite dois projetos, e o do Hugging Face, vários Spaces.
- Os jobs agendados (job D-2, canário, re-treino) rodam a partir de `main` e leem o banco de produção. Agendamento do GitHub só roda no branch padrão.
- Os PRs automáticos do re-treino e do canário trocam o modelo em produção, chegam direto em `main` e não passam por `dev`.
- LGPD: dado de paciente real não pode ir para um ambiente de teste.

## Decisão
**Dois ambientes, cada um com Space, projeto Supabase e secrets próprios**, como *environments* do GitHub:

| | `dev` | `production` |
|---|---|---|
| Branch | `dev` | `main` |
| Supabase | o projeto que já existia (dado de teste) | projeto novo, que nasce vazio e recebe as migrations do primeiro deploy |
| Space | Space de dev | Space de produção |
| Quem usa | `deploy.yml` em push em `dev` | `deploy.yml` em push em `main`; `job_d2.yml`, `canario.yml` e `retrain.yml` com `deployment: false` |

- O remote do DVC é **compartilhado**. Os artefatos são endereçados por conteúdo, então o mesmo `model.pkl` serve aos dois ambientes. A SAS só de leitura fica no repositório, porque o CI roda sem environment. A SAS de escrita fica só em `production`, onde está o único job que faz `dvc push`. Ela tem permissão de criar blobs, mas não de sobrescrever nem apagar, e é assinada com a outra chave da conta, para poder ser revogada sem derrubar a de leitura. A account key não chega a nenhum workflow.
- O Supabase de dev recebe **só dado de teste**. Nenhum dado de paciente real sai de produção para dev.
- **`main` é produção, e cada deploy de produção é uma release SemVer.** A versão é o primeiro cabeçalho `## [vX.Y.Z]` do CHANGELOG. Antes de migrar o banco, o deploy confere que a versão é nova e que `[Não publicado]` está vazio. Com o Space no ar, cria a tag e a Release com a seção do CHANGELOG como notas (`scripts/versao_release.py`). O CI confere a mesma coisa no PR para `main`, para o erro aparecer antes do merge. Os PRs automáticos que trocam o modelo sobem o PATCH sozinhos.
- **O que cada número significa:**
  - MAJOR: mudança incompatível para quem usa ou integra, como o contrato da API ou uma migration destrutiva já liberada pela regra do 10.1.
  - MINOR: funcionalidade nova compatível, inclusive migration aditiva.
  - PATCH: correção ou troca do modelo campeão.

  A primeira release de produção é a **v2.0.0**. As versões `v1.x` do CHANGELOG são anteriores ao SemVer e não viram tag.
- **Sem revisor obrigatório em `production`.** O revisor valeria também para os jobs agendados e os pararia todo dia esperando aprovação. O ponto humano é o merge do PR em `main`, com proteção de branch. Para o primeiro deploy, o revisor entra temporariamente, enquanto os jobs agendados ainda estão desligados.

## Alternativas consideradas
- **Deploy disparado pela tag ou pela Release publicada.** Descartada porque os jobs agendados continuariam rodando o código de `main`. Esse código pode estar à frente da última release e rodaria contra um schema de produção ainda não migrado. Para fechar esse buraco, os três jobs teriam de fazer checkout da última tag, e os PRs do canário e do re-treino teriam de nascer dela. Seriam mais peças pelo mesmo resultado.
- **Só environments do GitHub, com um banco.** Descartada: separa os secrets, mas a migration continua chegando à produção sem ensaio.
- **Branching do Supabase** (banco efêmero por PR). É recurso de plano pago.
- **Space de dev privado.** O smoke do deploy consulta a URL pública sem token. O Space de dev fica público, protegido pelo login da equipe, e só com dado de teste.

## Consequências
- **Prós**:
  - Toda migration roda primeiro no Supabase de dev, no push em `dev`, com o mesmo `supabase db push` que vai rodar em produção.
  - Credencial de produção não existe no `.env` de desenvolvimento nem nos jobs de dev.
  - Cada deploy de produção tem tag e Release no GitHub, então dá para saber exatamente qual commit está no ar e voltar a ele.
  - O histórico de deploys na aba Environments mostra só deploys, e não as execuções diárias.
- **Contras**:
  - Há dois projetos para manter iguais na configuração de Auth: cadastro desligado e senha mínima de 8 caracteres. As contas da equipe também são criadas nos dois.
  - O free tier do Supabase pausa um projeto depois de uma semana sem atividade. O de dev pode pausar entre ciclos de desenvolvimento, e o push seguinte em `dev` falha na migration até o projeto ser reativado no Dashboard. O de produção recebe o job D-2 todo dia.
  - Os PRs automáticos acrescentam uma seção ao CHANGELOG em `main`, e o merge de volta em `dev` pode dar conflito nesse arquivo. O conflito se resolve mantendo as duas seções.
  - Todo push em `dev` builda e redeploya o Space de dev, o que gasta minutos de Actions. Em repositório público, isso não custa nada.
