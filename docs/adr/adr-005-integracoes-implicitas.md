# ADR-005: Decisões de integração implícitas (scheduler, chamada ao modelo, região do Supabase)

## Status
Aceito — 2026-09-18

## Contexto
Alguns dos seguintes detalhes técnicos (nao inclusas no ADR-004) precisam ser resolvidas:
- COmo o job diário é disparado;
- como Streamlit/job/API se comunicam com o modelo;
- região de supabase.

## Decisão

**Scheduler D-2 via GitHub Actions cron, não processo interno.** O Hugging Face Space (tier free/community) pode hibernar por inatividade, então um agendador rodando dentro do próprio Space não tem garantia de disparo no horário certo. O disparo diário do job de inferência é feito por um workflow do GitHub Actions (`cron` + `workflow_dispatch` para testes manuais), que chama o endpoint/job independentemente do Space estar ou não "acordado" no momento do disparo.

**Streamlit e o job chamam o modelo em processo; a API FastAPI é para integrações externas.** `src/ui/app.py` e `src/jobs/inferencia_diaria.py` importam `src/inference.py` diretamente (mesmo processo/imagem, sem round-trip HTTP interno) — evita depender da API estar de pé para a própria aplicação funcionar e reduz latência interna. A API FastAPI  continua exposta via REST, mas seu papel é servir integrações externas ao sistema Saúde Já (ex.: outro serviço da clínica consultando `/predict`), conforme já desenhado no diagrama C2 do [`architecture.md`](../architecture.md).



### Emenda (2026-09-19, Passo 4) — como a UI materializa essa decisão

A implementação da interface (Passo 4) expôs uma contradição entre este ADR e o texto do [`PLANO-IMPLEMENTACAO.md`](../PLANO-IMPLEMENTACAO.md) (Passo 4) / [`architecture.md`](../architecture.md) §3.1, que descreviam a UI chamando `POST /predict` por HTTP. A decisão acima **continua valendo** e foi materializada assim: `src/ui/logic.py` define uma interface única de cliente de predição com duas implementações — `ClientePredicaoEmProcesso` (**default**, import direto de `src/inference.py`, conforme esta ADR) e `ClientePredicaoAPI` (HTTP), escolhidas pela variável `PREDICT_BACKEND`.

O modo REST não é uma segunda arquitetura: é ferramenta de desenvolvimento/diagnóstico, que permite exercitar a API do Passo 3 do formulário até a resposta durante o desenvolvimento, e conferir na UI o mesmo caminho que uma integração externa percorre. Produção (Passo 11) roda em `processo`. O custo dessa flexibilidade é uma função a mais e o risco de os dois caminhos divergirem — travado por `tests/test_ui_logic.py::test_backends_produzem_a_mesma_predicao`, que exige probabilidade, classe, threshold, `model_version` e explicação idênticos nos dois backends para o mesmo payload.

### Emenda (2026-09-19, Passo 5) — confirmação da região do Supabase (item c)

Projeto Supabase criado na região **São Paulo (`sa-east-1`)** — dados de pacientes/agendamentos/predições permanecem no Brasil, sem transferência internacional a justificar. Simplifica a base legal a documentar em `docs/LGPD.md` (Passo 8): não há necessidade de cláusulas de transferência internacional para o núcleo do produto (o LLM opcional, se usar um provedor fora do Brasil, é avaliado separadamente naquele passo).

Migrations em `supabase/migrations/` (convenção do Supabase CLI, aplicada automaticamente por `supabase start`/`supabase db reset`), não em `db/migrations/` como sugeria — ajuste técnico, sem impacto de decisão de produto.

### Emenda (2026-09-21, Passo 9.1) — remote do DVC em Azure Blob Storage, e o que isso faz com o argumento de região

O Passo 9.1 precisou decidir **onde o `model.pkl` e o dataset de treino ficam armazenados**, e isso toca esta ADR por dois motivos: ela é a que fixou a região do banco (`sa-east-1`, "dados permanecem no Brasil") e a que decidiu que o modelo é chamado em processo, o que implica que ele precisa estar *dentro* da imagem do Space.

**O problema concreto**: o remote do DVC era `/tmp/dvc-remote`, um caminho local. O GitHub Actions e o HF Space não têm acesso a ele, e `infra/deploy/dockerfile` faz `COPY data/model.pkl` de um arquivo coberto por `*.pkl` no `.gitignore` — ou seja, a verificação do Passo 9 era literalmente inexecutável: não havia caminho pelo qual o modelo promovido chegasse a produção.

**Decisão**: remote em **Azure Blob Storage**, container na região **Chile Central** (escolha da autora, 2026-09-21). Nem a credencial nem a URL entram em `.dvc/config`, que é versionado: lá fica só `[core] remote = azure`, e a URL vem de `.dvc/config.local` (ignorado pelo git) ou do secret `DVC_REMOTE_URL` no Actions. A credencial vai por `AZURE_STORAGE_CONNECTION_STRING`. O modelo chega ao Space pelo `dvc pull` do `deploy.yml` (Passo 10), que força o arquivo no espelho enviado ao Hub — a alternativa (o Space baixar do Azure no build) exigiria credencial Azure como secret do Space e contraria a decisão de que o modelo viaja dentro da imagem.

**A consequência que precisa estar escrita**: Chile Central está **fora do Brasil**. O argumento que sustentou `sa-east-1` na emenda do Passo 5 — "não há transferência internacional a justificar" — **deixa de valer para este artefato**. O que vai para o remote é o dataset de treino, que depois do Passo 9.0 contém desfechos reais de consultas, e o `model.pkl` derivado dele. Sob a LGPD isso é transferência internacional de dado derivado de dado de saúde (Art. 33), mesmo pseudonimizado.

O que **mitiga** é o conteúdo, não a região: o export (`src/export_treino.py`) nunca inclui telefone, nome ou CPF; `id_paciente` é hash sha256 de CPF; sobram atributos demográficos não identificáveis (idade derivada, sexo), operacionais (especialidade, distância, dias de antecedência) e o desfecho. Não há identificador direto no arquivo que sai do país. O que **não** mitiga: um dataset pseudonimizado continua sendo dado pessoal para a LGPD, e a base legal + as cláusulas contratuais padrão do provedor precisam ser registradas em `docs/LGPD.md` (Passo 8) — pendência declarada aqui, não resolvida.

A escolha é **reversível a baixo custo** e vale registrar o caminho: criar um container em Brazil South, apontar `DVC_REMOTE_URL`/`config.local` para ele e rodar `dvc push` de novo. Nada no código depende da região; o que depende dela é a análise de LGPD do Passo 8 e o slide de risco do Passo 12 (o BRIEFING trata dado de saúde como categoria especial desde o protótipo). Se a decisão for mantida, o pitch precisa dizer isso em voz alta em vez de afirmar que "os dados não saem do Brasil" — o que continua verdadeiro para o banco de produção e passou a ser falso para o artefato de treino.

**Pendência fechada (2026-09-27, Passo 8)**: a decisão foi **manter Chile Central**, e a base legal ficou registrada em [`LGPD.md` §4](../LGPD.md) — Art. 33, II, alínea "d" (cláusulas contratuais padrão, via o acordo de processamento de dados do provedor de nuvem). O que o Passo 8 acrescentou à análise acima: um dataset pseudonimizado **não** é equiparado a anônimo (Art. 12 só faz isso para o irreversível), e o hash sha256 de CPF é reversível por força bruta sobre o espaço de CPFs válidos — a pseudonimização reduz a gravidade de um incidente, não tira a transferência do escopo do Art. 33. O risco residual está nomeado em `LGPD.md` §9, item 1, e a formulação correta para o pitch está fixada em `architecture.md` §7. O caminho de reversão para Brazil South segue válido e documentado acima.

## Consequências
Pros:
- Scheduler externo (GitHub Actions) remove uma dependência de disponibilidade do próprio Space.
- Chamada em processo simplifica o caminho crítico (UI/job não dependem de rede interna nem de a API estar de pé).


Cons:
- Duplica a lógica de invocação do modelo entre "em processo" (UI/job) e "via REST" (API para terceiros) — mitigado por ambos chamarem o mesmo `src/inference.py` como única fonte de verdade e, a partir do Passo 4, por um teste de paridade automatizado entre os dois caminhos (ver emenda acima).
- Dependência do GitHub Actions como scheduler externo introduz acoplamento a disponibilidade do GitHub (aceitável dado o orçamento e a criticidade baixa de atraso de minutos no disparo D-2).

## Alternativas consideradas
- Scheduler interno ao Space (ex.: `APScheduler` num processo de background do próprio container): descartado por depender do Space estar acordado no horário exato, o que o tier free não garante.
- UI e job chamando a API FastAPI via HTTP mesmo internamente (em vez de import direto): descartado por adicionar uma dependência de rede/disponibilidade desnecessária dentro do mesmo processo/imagem, sem ganho de desacoplamento relevante neste estágio do projeto.
