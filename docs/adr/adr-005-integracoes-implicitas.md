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
