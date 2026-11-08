# ADR-013: Interface em NiceGUI no lugar do Streamlit

## Status
Aceito — 2026-11-07. Implementado no repositório em 2026-11-07 (`src/web/`, o Streamlit saiu da imagem). Até o deploy de produção, a interface publicada continua sendo a do Streamlit. A sonda segue em `/_stcore/health`, que a interface nova também responde, como alias, até os checks do Synthetic Monitoring trocarem para `/healthz`; só então o `SLO.md` §1.1 e o [ADR-006](adr-006-observabilidade.md) mudam.

Substitui, no [ADR-001](adr-001-stack.md), a escolha do **Streamlit como frontend**. O resto do que o ADR-001 ainda mantém (Python-first, GitHub + DVC + MLflow, FastAPI) continua valendo.

## Contexto

A interface (`src/ui/app.py`, cerca de 1.100 linhas) atende dois públicos: o paciente, que se autoagenda numa tela aberta, e a equipe da clínica, que entra com login para ver a "Fila do dia", os pedidos do titular e as abas técnicas. O objetivo agora é que ela pareça um produto profissional, coisa que o Streamlit dificulta:

- **O modelo de execução não é de aplicação.** O script inteiro roda de novo a cada clique, e o `st.tabs` executa todas as abas a cada interação. Foi isso que obrigou a pôr a fila em `st.cache_data` (60 s, até 7 datas), com invalidação manual nas escritas da própria tela (`architecture.md` §3.1).
- **Pouco controle de layout e de componente.** Tabela com barra de risco e selo de status, painel de detalhe ao lado da lista, menus e diálogos ficam presos ao que o Streamlit oferece.
- **A sonda de disponibilidade depende de uma rota interna do Streamlit** (`/_stcore/health`, emenda de 2026-10-11 do ADR-006), porque `/health` na URL pública devolve o HTML dele.

A separação que já existe é o que torna a troca viável: `src/ui/logic.py` não importa `streamlit` e concentra a regra de negócio (login, sessão de 30 min, fila, desfecho, cadastro, pedidos do titular, mensagens de erro), com testes próprios. Trocar a interface é trocar a casca.

**Forças em jogo:**

- **Imagem `python:3.10-slim`, Python-first, uma mantenedora só.** Um build de frontend com Node é uma cadeia de ferramentas a mais para manter e atualizar.
- **O HF Space publica uma porta só** (ponto em aberto de `architecture.md` §6). Hoje a UI fica em 7860 e a API em 8000, que só é acessível de dentro do container. A API não tem autenticação (§7).
- **LGPD.** Nome do paciente e identificador vão ao navegador porque a tela os mostra, como hoje. O que não pode ir é a sessão do funcionário nem dado que a tela não mostra. Toda linha de log passa pelo filtro de redação (LGPD.md §8), e o uvicorn passa a logar conexões de websocket com IP e porta do cliente.
- **Orçamento de US$ 100/mês.** Uma hospedagem a mais para o frontend precisaria caber nele e entraria no LGPD.md como mais uma operadora.
- **`/app` só de leitura para o uid 1000** no Space. Nada pode depender de gravar no diretório da aplicação.

### Prova de conceito

Em 2026-11-07, a mesma tela (login + "Fila do dia", com seleção do paciente e registro do desfecho) foi montada em NiceGUI 3.2.0 e em Reflex 0.9.5, cada uma num branch descartável, sobre o mesmo `logic.py`, sem mudar nenhuma linha dele. As duas foram exercitadas no Playwright em tema claro, tema escuro e largura de celular.

| | NiceGUI (Vue + Quasar) | Reflex (React + Radix) |
|---|---|---|
| Encaixe no FastAPI | `ui.run_with(app)`, e o `lifespan` do app roda normalmente | monta o FastAPI dentro dele e não roda o `lifespan` do app montado; precisou de registro manual |
| Build | só Python | Node/bun no build: `.web/` com 182 MB, mais 113 MB de ferramentas |
| Python 3.10 | suportado | Reflex 0.10+ exige Python 3.11; a 0.9.5 já avisa que o 3.10 está depreciado |
| Padrões a desligar | nenhum | telemetria ligada por padrão, selo "Built with Reflex" no build de produção |
| Sessão | `app.storage.tab`, na memória do servidor, por aba do navegador | variáveis do estado no servidor; só as públicas vão ao navegador |
| Tráfego no websocket, mesmo fluxo | 29,7 KB | 7,9 KB |
| Tema escuro | exigiu CSS próprio (o `dark:` do Tailwind não acompanha o modo escuro do Quasar) | nativo do Radix |
| Visual | limpo, estilo Material | mais polido |

Nas duas, a sessão do funcionário e a explicação SHAP crua ficaram fora do navegador. O NiceGUI não gravou nada em disco (nenhuma pasta `.nicegui/`).

## Decisão

Adotamos o **NiceGUI** como framework da interface, no lugar do Streamlit, **num processo próprio na porta 7860**, com um app FastAPI só da interface. A API de predição continua no processo dela, na porta 8000, sem mudança de exposição.

Regras que acompanham a decisão:

1. **`logic.py` continua sendo a única camada de regra de negócio.** A casca nova só apresenta e chama `logic.py`, como `app.py` faz hoje.
2. **Sessão na memória do servidor, por aba** (`app.storage.tab`), sem cookie e sem gravar em disco. `app.storage.user` e `app.storage.browser` ficam proibidos: gravam em disco ou mandam o estado ao navegador.
3. **Chamada bloqueante sai do event loop.** Supabase, modelo e SHAP são síncronos; na casca nova, rodam em thread, com o contexto do OpenTelemetry levado junto, para o span da ação continuar ligado à sessão.
4. **A interface não carrega nada de terceiros no navegador.** Fonte, ícones e scripts são servidos pela própria aplicação: uma fonte do Google Fonts entregaria o IP de quem abre a tela a mais um operador.
5. **A rota de saúde da interface é da aplicação**, e não de um detalhe interno do framework. A troca de rota da sonda segue o que o ADR-006 já exige: 2xx e corpo `ok`.

### Por que processo próprio, e não a interface montada na API

A prova de conceito montou a interface sobre o app da API para comparar os dois frameworks em igualdade. Levar isso para produção resolveria o ponto da porta única de `architecture.md` §6 publicando `/predict` e `/docs` na URL do Space, e a API não tem autenticação. Isso é uma decisão de exposição, não de framework, e fica para quando a API ganhar um consumidor externo real. Com processo próprio, o `entrypoint.sh` continua subindo dois processos e derrubando o container se um deles cair.

## Consequências

**Positivas**

- Controle de layout e de componente do Quasar (tabela com slots, diálogos, menus, notificações), com o visual definido por CSS da própria aplicação.
- Estado de aplicação comum: a tela não roda de novo a cada clique, então o cache que existia por causa do `st.tabs` deixa de ser necessário para isso. A expiração de 60 s continua por outro motivo: é o tempo máximo que o nome de um paciente eliminado fica na memória do Space (`architecture.md` §3.1).
- Rotas próprias para a tela do paciente, os documentos legais e a área da equipe, em vez da escolha de perfil na barra lateral.
- Saúde da interface numa rota da aplicação, que pode ser testada como qualquer outra.
- Nenhuma ferramenta nova no build da imagem.

**Negativas**

- **Reescrita da casca inteira**: as telas do paciente, da ciência e dos documentos legais, da equipe e das abas técnicas. Os testes de tela (`tests/test_ui_smoke.py`, sobre o `AppTest` do Streamlit) precisam ser refeitos sobre o simulador do NiceGUI.
- **Mais superfície no log.** O uvicorn da interface loga cada conexão de websocket com IP e porta do cliente, e a regra numérica do filtro só redige sequências de 10 a 13 dígitos, menos que muitos IPv4. Essa lacuna tem de ser fechada no filtro, com teste, antes de a interface nova ir para produção.
- **Sonda, smoke e documentos mudam junto.** `/_stcore/health` aparece no `SLO.md` §1.1, no ADR-006, no check do Synthetic Monitoring, em `scripts/smoke_deploy.py` e no `deploy.yml`. A troca precisa de uma transição para não abrir um buraco na medida de uptime.
- **Tema escuro e responsividade são responsabilidade da aplicação.** O Quasar não acompanha o `dark:` do Tailwind, então o tema precisa de variáveis CSS próprias.
- **O SSO por `st.login`** (roadmap de `architecture.md` §9) deixa de existir como atalho: OIDC passa a ser feito na aplicação, se a clínica pedir.
- **Dependência de um framework menor que o Streamlit.** O NiceGUI tem comunidade e ecossistema menores que os do Streamlit; a versão fica fixada e passa pelo Dependabot como as demais.

**O que esta decisão impede ou adia**

- Não decide a porta única do Space nem a publicação da API.
- Não abre caminho para um frontend separado (SPA), que exigiria autenticação na API, CORS e outra hospedagem.

## Alternativas consideradas

- **Manter o Streamlit e investir em tema e componentes.** Descartado: tema e CSS injetado não mudam o modelo de execução, que é o que obriga o cache por aba e limita o layout. Componentes customizados do Streamlit exigem build de frontend do mesmo jeito.
- **Reflex.** Construído na prova de conceito, com o visual mais polido e menos tráfego no websocket. Descartado por exigir Python 3.11 nas versões atuais (a imagem é 3.10, e trocar a base é outra decisão, com efeito no modelo empacotado e no cold start), por pôr Node/bun no build da imagem e por não rodar o `lifespan` do app FastAPI montado. Também vem com telemetria ligada e selo de marca no build de produção, que teriam de ser desligados e conferidos a cada atualização.
- **SPA em React ou Vue, hospedada na Vercel ou servida pelo próprio Space.** Descartado agora: exige autenticação na API, que hoje não tem (`architecture.md` §7); um backend de sessão para o login, que hoje vive no processo da UI; uma segunda linguagem e um build de frontend; e, na Vercel, mais uma operadora no LGPD.md e uma hospedagem fora do orçamento reservado à mensageria.
