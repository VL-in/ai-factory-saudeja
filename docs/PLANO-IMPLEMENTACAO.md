# Plano de Implementação — SaudeJá: do pipeline de treino ao produto deployável

> **Status:** em execução. Última geração: 2026-09-18. Complementa [`architecture.md`](architecture.md) (o "o quê"/"por quê" da arquitetura) com o "como e em que ordem construir" — cada passo abaixo é uma fatia vertical testável, com critério de verificação explícito, que deve ser commitada em git antes de avançar para a próxima.

## Contexto

O repositório tem hoje um pipeline de ML maduro (DVC + MLflow + LightGBM + SMOTE-NC, 3 stages testados) mas **nenhuma camada de aplicação**: sem API, sem banco, sem interface, sem mensageria, sem explicabilidade, sem automação de re-treino. [`architecture.md`](architecture.md) e os [ADRs](adr/) já desenham a arquitetura-alvo (diagramas C4 nível 1/2), e [`BRIEFING.md`](BRIEFING.md)/[`SLA.md`](SLA.md)/[`SLO.md`](SLO.md) definem requisitos de negócio concretos (API + interface + threshold calibrado + explicabilidade obrigatória + LGPD + re-treino mensal automatizado com gate de rollback), com pitch para o Conselho de Investidores na Semana 16.

Este plano constrói essa camada em **fatias verticais testáveis**: cada passo entrega uma parte funcional da arquitetura com um critério de verificação explícito antes de avançar para o próximo — nunca "big bang". A ordem prioriza o núcleo que tem SLA (predição → explicação → API → **interface Streamlit já como casca cedo, para servir de harness de teste visual** → banco → job → mensageria → LGPD → re-treino → CI/CD → deploy → validação final) e deixa a integração de LLM (TrueFoundry) como etapa opcional ao final, por não ser exigida pelo SLA/SLO. A interface entra logo depois da API (Passo 4) propositalmente: assim, cada capacidade nova (banco, job, mensageria) é plugada numa aba já existente e testada visualmente assim que fica pronta, em vez de só ser validada por `pytest`/`curl` até o fim do plano.

**Decisões já validadas com a autora do projeto:**
- Banco de dados: **SDK oficial `supabase-py`** (alinhado ao ADR-004), não camada Postgres genérica.
- ADR-004 é a decisão de stack vigente; **ADR-001 será marcado como superseded** nos pontos onde conflita (banco, mensageria, observabilidade, deploy).
- LLM/TrueFoundry é **opcional**, só depois do núcleo (predição+fila+mensageria+deploy) estar funcional e testado.
- SHAP (Passo 2) já nasce com um **plug inativo** para um dia ser traduzido em texto por LLM (ativado no Passo 13).
- A interface Streamlit (Passo 4) inclui uma aba de desenvolvedor para **disparar o pipeline de inferência manualmente** e acompanhar o pipeline sem depender de CLI/cron separados.
- CI/CD usa a GitHub Action oficial **`huggingface/huggingface-sync-action`** para sincronizar `main` → Hugging Face Space (Passo 10/11), não um script de sync caseiro.

**Risco a não decidir agora, só monitorar**: recall atual da classe positiva é 0.522 (ADR-003), abaixo do alvo do SLO (≥0.75). Não é bug — é limite do dataset sintético pequeno (380 linhas). O plano cria os checkpoints certos para revisitar isso (Passo 6, com dados fluindo pelo job, e Passo 12, fechamento para o pitch) em vez de decidir threshold às cegas agora.

**Convenção de execução (ciclo de cada passo)**: implementar → rodar a verificação automatizada do passo → verificar manualmente (quando o passo tiver esse componente) → **commitar em git** (só o que pertence àquele passo, com mensagem descrevendo o que foi entregue e o que foi verificado) → só então avançar para o próximo passo. Nenhum passo começa antes do commit do anterior. Isso mantém o histórico do repositório como registro fiel do progresso e dá pontos de checkpoint humano reais (revisar/testar cada incremento antes de autorizar o próximo).

---

## Passo 0 — Reconciliar arquitetura e documentação

**Objetivo**: eliminar a ambiguidade entre ADRs e destravar todos os passos seguintes com uma referência única sem contradição.

- `docs/adr/adr-001-stack.md`: `Status` → `Superseded por ADR-004` nos pontos que ele resolve (banco, mensageria, LLM gateway, deploy); mantém válida a parte não contestada (Streamlit + FastAPI + DVC/MLflow).
- `docs/adr/adr-004-decisão-técnica.md`: `Status` → `Aceito`; preencher a seção "Para deploy, foram considerados: -" (hoje vazia); registrar decisão de observabilidade: MLflow (já existe) cobre o pipeline de ML; Langfuse fica reservado só para tracing do LLM (Passo 13, opcional), não bloqueante; n8n descartado em favor do job agendado (Passo 6) + Infobip direto.
- Nova seção/ADR-005 curto registrando decisões de integração implícitas: (a) Scheduler D-2 via **GitHub Actions cron**, não processo interno — HF Spaces free pode dormir; (b) Streamlit e o job chamam o modelo **em processo** (import direto de `src/inference.py`), a API FastAPI fica exposta via REST para integrações externas, conforme diagrama C2 já desenhado; (c) confirmar região do Supabase compatível com LGPD antes do Passo 5.
- `docs/architecture.md`: preencher §3 (Frontend=Streamlit, Backend=FastAPI+Job+Gate de re-treino), §4 (Data Stores=Supabase, tabelas do Passo 5), §5 (Infobip, TrueFoundry opcional, MLflow), §6 (HF Spaces, GitHub Actions), §7 (LGPD → remete a `docs/LGPD.md` do Passo 8), §9 (registrar o gap de recall como debt conhecido), §10/§11.
- Remover ou corrigir `infra/ML/dockerfile` (quebrado: `FROM python:3.9-slim` com `COPY` auto-referencial, não referenciado por `dvc.yaml`/`docker-compose.yml` — resíduo do protótipo herdado).

**Verificação**: novo assert em `tests/test_coerencia_repo.py` que falha se `docs/architecture.md` ainda contiver o padrão `[e.g.,`; revisão humana confirmando que ADR-001/ADR-004 não se contradizem mais; `grep -r "infra/ML" .` limpo (exceto o próprio arquivo, se corrigido em vez de removido).

---

## Passo 1 — Módulo de inferência reusável

**Objetivo**: extrair "payload cru → features → predição" para algo que API e job importem sem duplicar nem re-treinar. Crítico: `preprocess.preprocessar()` **ajusta** um novo mapa de especialidade a cada chamada — em produção precisamos **aplicar** o mapa já salvo em `data/model.pkl` (`joblib.dump({"model":..., "mapa_especialidade": mapa_esp})`, visto em `src/train.py:95`), nunca recalcular.

- Novo `src/inference.py`, reusando `COLUNAS_CATEGORICAS` (`src/preprocess.py:35`) e `extrair_features_temporais` (`src/features.py:12`, já documentada como "usada tanto pelo treino quanto pela futura API").
- `carregar_modelo(path)`, `aplicar_mapa_especialidade(df, mapa_especialidade)` (nova — levanta `EspecialidadeDesconhecidaError` em vez do NaN silencioso que `preprocess.py` documenta como comportamento de treino), `construir_features(payload: dict, mapa_especialidade, features_temporais: bool)` (monta 1 linha com exatamente as colunas/ordem que `preprocessar()` produz), `predizer(model, X)`.
- Não tocar em `preprocess.py`/`train.py`/`validate.py` — pipeline DVC continua como está.

**Verificação**: `tests/test_inference.py`, seguindo o padrão de `tests/conftest.py` (fixtures reaproveitadas, sem mocks pesados). **Teste de paridade treino-serving** (o mais importante): para uma linha do fixture `df_consultas`, `construir_features()` deve produzir as mesmas colunas/valores que `preprocess.preprocessar(df.iloc[[i]])` — detecta training-serving skew automaticamente. Teste de exceção clara para especialidade fora do mapa. Teste ponta a ponta carregando o `data/model.pkl` real (já versionado via DVC) sem re-treinar. `pytest tests/test_inference.py -v` verde antes de avançar.

---

## Passo 2 — Explicabilidade (SHAP)

**Objetivo**: cumprir SLO §4 (100% das predições com explicação — requisito bloqueante da Dra. Helena nas notas herdadas). SHAP calculado **síncrono**, dentro do módulo de inferência, a cada `/predict` e a cada execução do job — volume baixo (fila diária de uma clínica), `TreeExplainer` sobre LightGBM é da ordem de milissegundos, e cobertura de 100% é mais fácil de garantir por construção do que por job assíncrono que pode falhar.

- `src/inference.py` (ou `src/explain.py`): `construir_explicador(model)`, `explicar(explainer, X)` → lista `{"feature", "contribuicao"}` ordenada por `abs(contribuicao)` desc. Esse valor numérico bruto é o que a API/job retornam e persistem **hoje**.
- `requirements.txt`: adicionar `shap` (compatível com `lightgbm==4.5.0`/`scikit-learn==1.5.2` já fixados).
- **Plug inativo para explicação em linguagem natural via LLM** (preparação para o Passo 13, sem ativar agora): definir uma interface `ExplicadorLLM` em `src/explain.py` com um único método, ex. `explicar_em_texto(contribuicoes: list[dict], contexto: dict) -> str`, implementada por `ExplicadorLLMDesativado` (default — retorna `None`/levanta `NotImplementedError` controlado, não chama rede nenhuma) e, futuramente no Passo 13, por `ExplicadorLLMTrueFoundry` (mesmo padrão stub/real já usado para mensageria no Passo 7). `PredictOut`/o registro em `predicoes` (Passo 5) já reservam um campo opcional `explicacao_texto: str | None` desde já, para não exigir migração de schema quando o plug for ligado depois — ele só fica `None` até o Passo 13 acontecer. A ideia de produto: o SHAP continua sendo a fonte de verdade numérica (auditável, testável por aditividade); o LLM, quando ligado, só traduz essas contribuições já calculadas em uma frase para a clínica — nunca substitui nem recalcula a explicação.

**Verificação**: teste de **aditividade** SHAP (`soma(shap_values) + expected_value ≈ margem prevista`, com tolerância numérica) — prova matemática, não opinião. Teste de shape (nº de contribuições == nº de features). `explicar()` deve ser JSON-serializável (vai trafegar na API e ser persistido em coluna `jsonb`, Passo 5). Teste do plug inativo: confirmar que `ExplicadorLLMDesativado` nunca faz chamada de rede e que `PredictOut.explicacao_texto`/a coluna correspondente aceitam `None` sem quebrar serialização. Checagem de sanidade manual do SHAP (não trava CI, dataset é pequeno demais para assert rígido) documentada no CHANGELOG.

---

## Passo 3 — API FastAPI

**Objetivo**: expor `/predict` e `/health` sobre o módulo do Passo 1+2.

- `src/api/schemas.py`: Pydantic — `PacienteConsultaIn` (idade, `sexo: Literal["F","M"]`, especialidade, distancia_km≥0, dias_entre_agendamento_consulta≥0, historico_noshow≥0, data_hora_agendada) e `PredictOut` (probabilidade, classe_prevista, threshold_usado, explicacao, model_version).
- `src/api/main.py`: carrega modelo **uma vez no startup** (`lifespan`, não por request — crítico para p95<2s do SLO), lê `threshold` de `params.yaml` (mesma leitura que `validate.py` já faz). `/health` retorna status + versão do modelo carregado. `/predict` captura `EspecialidadeDesconhecidaError` → HTTP 422 com mensagem clara.
- Split `requirements.txt` → `requirements/base.txt`, `requirements/train.txt` (jupyter, matplotlib, imbalanced-learn), `requirements/api.txt` (fastapi, uvicorn, shap) — reduz imagem de deploy (cold start, SLO §2).
- Novo `infra/api/dockerfile` (substitui o `infra/ML/dockerfile` morto removido no Passo 0).

**Verificação**: `tests/test_api.py` com `TestClient` (modelo real ou fixture pequeno treinado on-the-fly com `df_consultas_smote`, sem mock pesado — mesma filosofia dos testes de MLflow existentes). Casos: `/health`→200; `/predict` válido→200 com `0<=probabilidade<=1` e `explicacao` não vazia; especialidade desconhecida→422 (não 500, não NaN); payload inválido (idade negativa, sexo fora de F/M)→422 via Pydantic; `classe_prevista` bate com `probabilidade>=threshold` lido de `params.yaml` (teste que monkeypatcha o threshold e confirma que a resposta muda). Manual: `uvicorn src.api.main:app --reload` + curl.

---

## Passo 4 — Interface Streamlit (casca inicial, consumindo a API diretamente)

**Objetivo**: ter uma superfície visual utilizável assim que a API (Passo 3) existir, para testar cada funcionalidade nova visualmente conforme ela for incorporada nos passos seguintes — em vez de validar só por `pytest`/`curl` até o fim do plano. Antes do banco (Passo 5) e do job (Passo 6) existirem, esta casca já é útil: chama `POST /predict` diretamente com um formulário manual.

- `src/ui/app.py`, visão Funcionário organizada em **abas** (`st.tabs`), desenhada para crescer sem trocar de estrutura nos passos seguintes:
  1. **"Testar predição"** — formulário manual (idade, sexo, especialidade, distância, dias até consulta, histórico de no-show, data/hora) que chama `POST /predict` da API (Passo 3) e mostra probabilidade + classe prevista. Harness de teste visual do núcleo de ML enquanto não há banco.
  2. **"Explicabilidade"** — mostra a `explicacao` (SHAP) já retornada pela mesma chamada da aba anterior — a API do Passo 3 já devolve isso, então esta aba funciona desde o primeiro dia, sem esperar o banco.
  3. **"Fila do dia"** — placeholder ("conecte o banco — Passo 5") até existir persistência; passa a listar `buscar_fila_do_dia` de verdade no Passo 5.
  4. **"Dev: disparo manual"** — aba visível só com `APP_ENV=dev`; placeholder/desabilitada até existir o job; passa a disparar `inferencia_diaria.py::main()` de verdade no Passo 6 e mostrar quantos agendamentos/predições/disparos ocorreram.
  - Visão Paciente (cadastro) fica com formulário desabilitado/placeholder até o Passo 5 (sem banco para persistir ainda).
- Lógica extraída para `src/ui/logic.py` (funções puras, chamando a API via `httpx`/`requests`, testáveis sem runtime do Streamlit).
- **Reconciliação com o ADR-005 (feita na implementação, 2026-09-19)**: o texto acima ("chamando a API via `httpx`") contradizia o [ADR-005](adr/adr-005-integracoes-implicitas.md) (b), que decidiu que o Streamlit chama o modelo **em processo**. Resolvido sem escolher um dos dois às cegas: `src/ui/logic.py` expõe um contrato único de cliente de predição com duas implementações — `ClientePredicaoEmProcesso` (default, honra o ADR-005 e o Passo 11) e `ClientePredicaoAPI` (`PREDICT_BACKEND=api`, mantém a UI como harness visual da API do Passo 3) — com teste de paridade entre elas. Ver emenda no ADR-005.

- **Ajuste de escopo (2026-09-19, decidido com a autora)**: a **imagem combinada do Passo 11** (`infra/deploy/dockerfile` + `entrypoint.sh`, API + Streamlit no mesmo container) foi antecipada para cá. Motivo: o Passo 4 existe para tornar o sistema *visível* antes do fim do plano, e ver as peças montadas como elas vão rodar em produção é parte disso — não só cada uma isolada por `pytest`/`curl`. O que fica para o Passo 11 é o que depende da plataforma (front-matter do Space, secrets, `APP_ENV=prod`, deploy real, porta única — ver lá), não o empacotamento.

**Verificação**: `streamlit.testing.v1.AppTest` em `tests/test_ui_smoke.py` (app carrega sem exceção; as 4 abas existem); testes de `src/ui/logic.py` mockando só a chamada HTTP à API (não o Streamlit); verificação manual (`streamlit run` + `uvicorn` local) preenchendo o formulário de "Testar predição" e conferindo que probabilidade+explicação aparecem corretamente — primeira validação ponta a ponta **visual** do projeto, mesmo sem banco/job ainda.

---

## Passo 5 — Banco de dados (Supabase via `supabase-py`)

**Objetivo**: schema para pacientes/agendamentos/predições, usando o SDK oficial do Supabase (decisão validada), e conectar as abas placeholder do Passo 4 a persistência de verdade.

**Minimização de PII por design**: tabela `pacientes` guarda só `id_paciente_externo` (referência ao sistema core da clínica) + atributos demográficos não identificáveis — nunca nome/CPF, reduzindo a superfície de risco LGPD estruturalmente, não só por convenção de logging (Passo 8).

- Criar projeto Supabase (free tier, região compatível com LGPD — confirmado no Passo 0) e `db/migrations/0001_init.sql`: `pacientes` (id, id_paciente_externo, idade, sexo, criado_em), `agendamentos` (id, id_paciente FK, especialidade, distancia_km, data_hora_agendada, dias_entre_agendamento_consulta, historico_noshow, status), `predicoes` (id, id_agendamento FK, probabilidade, classe_prevista, threshold_usado, explicacao_shap jsonb, explicacao_texto text nullable — plug do LLM, Passo 2/13, fica `NULL` até ser ativado, model_version, criado_em), `mensagens_disparadas` (id, id_agendamento FK, canal, status_envio, criado_em — auditoria do SLA §6).
- `src/db/client.py`: wrapper fino sobre `supabase-py` (client único, configurado via `SUPABASE_URL`/`SUPABASE_KEY`).
- `src/db/repositories.py`: `inserir_agendamento`, `buscar_agendamentos_d2_pendentes`, `gravar_predicao`, `buscar_fila_do_dia`.
- `.env.example`: adicionar `SUPABASE_URL`, `SUPABASE_KEY`.
- Ambiente de teste: **Supabase CLI local** (`supabase start`, stack Docker local com Postgres+PostgREST+Auth) — mantém a filosofia já usada no repo ("serviço real efêmero, não mock pesado", mesmo padrão do MLflow com sqlite temporário) e espelha o comportamento do `supabase-py` real melhor que um Postgres genérico.
- A aba **"Fila do dia"** e o formulário de cadastro de paciente do Passo 4 passam a usar `src/db/repositories.py` de verdade em vez do placeholder — primeiro teste visual do CRUD real.

**Verificação**: teste rápido em `tests/test_coerencia_repo.py` que faz grep nas migrations SQL e falha se aparecer coluna proibida (`nome`, `cpf`, `email`, `telefone` em texto puro) — guarda LGPD automatizável desde o schema. `tests/test_db.py` marcado `integracao` (convenção já existe em `pytest.ini`): sobe Supabase local, aplica migrations, insere agendamento sintético, roda os repositórios, confere round-trip. Manual: cadastrar um paciente pela UI (Passo 4) e confirmar que aparece no Supabase local. `pytest -m integracao tests/test_db.py -v` verde antes de avançar.

**Ajustes feitos na implementação (2026-09-19, decididos com a autora)**:
- Migrations em `supabase/migrations/` (convenção do Supabase CLI, aplicada automaticamente por `supabase start`/`supabase db reset`), não em `db/migrations/0001_init.sql` como o texto acima descrevia — `supabase init` já cria essa estrutura.
- `SUPABASE_KEY` virou `SUPABASE_SECRET_KEY`: o projeto criado usa o formato novo de API key do Supabase (`sb_publishable_...`/`sb_secret_...`), que substitui o par JWT `anon`/`service_role`. O backend usa a secreta, nunca a publishable (ver [ADR-005, emenda Passo 5](adr/adr-005-integracoes-implicitas.md), que também registra a região do projeto — São Paulo, `sa-east-1`).
- `src/db/repositories.py` ganhou `inserir_paciente` (upsert por `id_paciente_externo`) além das quatro funções listadas acima — necessária para o formulário de cadastro de paciente persistir de verdade (agendamento sempre depende de um paciente já existir).
- RLS habilitado em todas as tabelas, sem policies (só a `SUPABASE_SECRET_KEY` do backend acessa) — não estava explícito no plano, decisão tomada durante a migration inicial por ser o default seguro do Supabase.
- `python-dotenv` (já era dependência de `requirements/base.txt`, mas nunca usado) passou a ser carregado em `src/config_projeto.py` via `load_dotenv()`, para que `SUPABASE_URL`/`SUPABASE_SECRET_KEY` do `.env` cheguem ao processo em execuções locais fora de Docker (`streamlit run`, `pytest`) sem exigir exportar variáveis manualmente. Não sobrescreve variáveis já definidas no ambiente (`env_file` do `docker-compose.yml`, secrets do HF Space).

---

## Passo 6 — Job agendado (D-2)

**Objetivo**: consultar o banco, prever, gravar e decidir disparo — em processo, importando `src/inference.py` diretamente (decisão do Passo 0) — e ligar a aba "Dev: disparo manual" do Passo 4.

- `src/jobs/inferencia_diaria.py`: busca agendamentos D+2 via `buscar_agendamentos_d2_pendentes`, chama `construir_features`/`predizer`/`explicar`, grava em `predicoes`, decide `probabilidade>=threshold` e aciona mensageria (Passo 7) ou registra não-envio em `mensagens_disparadas` (auditoria). 100% da lógica de predição vem do Passo 1/2 — o job só orquestra.
- A aba **"Dev: disparo manual"** do Passo 4 passa a chamar `inferencia_diaria.py::main()` de verdade (em vez do placeholder), mostrando na tela quantos agendamentos foram encontrados, predições gravadas e disparos de mensageria — dá para acompanhar o pipeline fim a fim clicando um botão, sem orquestrar CLI/cron separadamente enquanto os próximos passos ainda estão em construção.

**Verificação**: `tests/test_job_inferencia.py` (marcado `integracao`, usa Supabase local): semeia 2 agendamentos D+2 sintéticos (um desenhado para risco alto, outro para risco baixo), roda `main()`, confere `predicoes` coerente com o threshold e que a mensageria stub só foi acionada para o de alto risco. Manual: clicar o botão da aba "Dev: disparo manual" e conferir que `predicoes`/`mensagens_disparadas` são populadas como esperado.

**Checkpoint do risco de recall**: com o job rodando fim a fim contra dados sintéticos reais, este é o momento de simular a curva de custo (R$180/no-show perdido vs. custo unitário de SMS/WhatsApp) variando o threshold — documentar o resultado em `docs/SLA.md`, sem travar o passo esperando uma decisão final (isso fecha no Passo 9/12).

---

## Passo 7 — Mensageria (Infobip) isolada e testável sem custo

- `src/messaging/client.py`: interface `enviar_lembrete(telefone, mensagem)` com `StubMessagingClient` (default — registra "seria enviado", nunca faz rede) e `InfobipClient` (real, ativado por `MESSAGING_PROVIDER=infobip`).
- A aba "Fila do dia" (Passo 4/5) pode opcionalmente mostrar o `status_envio` de `mensagens_disparadas` como coluna extra, usando os mesmos dados que o job (Passo 6) já grava — sem lógica nova, só leitura.

**Verificação**: `tests/test_messaging.py` — prova que o stub nunca chama rede (monkeypatch de `requests`/`httpx` para lançar exceção se invocado; teste passa mesmo assim); teste de contrato confirmando que stub/real expõem a mesma assinatura. Teste manual único (fora do CI) contra sandbox Infobip antes do deploy real.

---

## Passo 8 — Blindagem LGPD

- `src/logging_config.py`: logging estruturado JSON + filtro de redação de campos (`nome`/`cpf`); `docs/LGPD.md` (base legal, retenção, contato DPO — insumo direto para o pitch, item 4 do BRIEFING); `scripts/auditoria_lgpd.py` (varre logs em busca de padrões de PII).

**Verificação**: `tests/test_logging_lgpd.py` — logar payload com `nome`/`cpf` e confirmar redação (via `caplog`); extensão de `test_coerencia_repo.py` com grep estático em `src/` por f-strings de log referenciando campos proibidos. Rodar `scripts/auditoria_lgpd.py` contra logs de uma execução completa de smoke test (Passo 11) antes do pitch.

---

## Passo 9 — Re-treino mensal automatizado + gate de rollback + deploy automático do modelo promovido

**Problema a resolver aqui, não só "rodar o treino de novo"**: o MLflow do repo hoje só existe como `docker-compose` local/efêmero (sobe, treina, os stages leem, nada persiste além do volume local). Para o CI (GitHub Actions, mensal) conseguir comparar "modelo novo" contra "modelo campeão" **entre execuções**, e para a API em produção (Passo 11) carregar o modelo promovido **sem depender de um MLflow sempre-no-ar** (custo/operação fora do orçamento de US$100/mês), a fonte de verdade do "campeão" precisa ser **versionada no próprio repositório**, não só numa run de MLflow que só existe durante o workflow.

- Novo `data/champion_metrics.json` (git-tracked, pequeno) — snapshot das métricas do modelo atualmente em produção (`recall_1`, `f1_1`, `roc_auc`, `model_version`/`run_id` de origem). Atualizado **só** quando o gate promove.
- `src/retrain_gate.py`: sobe o `mlflow-server` via `docker-compose` (mesmo mecanismo já usado hoje pelos stages `train`/`validate` — efêmero, só durante o workflow), roda `dvc repro` contra dados frescos, lê as métricas da nova run no MLflow, compara contra `data/champion_metrics.json` (não contra uma run "campeã" persistida no MLflow, que não sobreviveria entre execuções mensais); bloqueia promoção se `recall_1`/`f1_1`/`roc_auc` regredirem além da tolerância do SLO §3 (0.02 para ROC-AUC).
  - **Se passar**: sobrescreve `data/champion_metrics.json`, `data/model.pkl` (via `dvc repro`/`dvc add`) e `dvc.lock` já ficam atualizados; o workflow faz `git commit` + `git push` desses artefatos versionados diretamente no repositório (branch dedicada + PR automático, para manter revisão humana leve sem travar a automação — SLO §5 exige "100% dos meses, automatizada", não "sem rastro").
  - **Se bloquear**: `data/champion_metrics.json`/`model.pkl` **não mudam** (modelo anterior continua em produção por construção, não por convenção), a run fica tagueada como rejeitada no MLflow efêmero (log/auditoria do workflow run), e um alerta é disparado via `src/messaging` (Passo 7) para revisão humana.
  - Caso "bootstrap" (primeiro ciclo, `champion_metrics.json` ainda não existe): promove direto, sem comparação, e cria o arquivo.
- `.github/workflows/retrain.yml` (cron mensal + `workflow_dispatch` manual para teste).
- **Fecha o loop até produção**: como o Passo 11 empacota `data/model.pkl` (committed/DVC-tracked) direto na imagem do HF Space, o merge do PR de promoção em `main` já dispara o `deploy.yml` do Passo 10 (`huggingface/huggingface-sync-action`), que sincroniza e faz o Space rebuildar com o `model.pkl` novo — não é preciso a API em produção falar com um MLflow ao vivo em nenhum momento, nem existe um segundo mecanismo de deploy além do já usado para código.

**Verificação**: `tests/test_retrain_gate.py` — duas runs MLflow fake (`sqlite:///{tmp_path}`, mesmo padrão de `test_train.py`) comparadas contra um `champion_metrics.json` de fixture, uma pior confirma bloqueio (arquivo/`model.pkl` inalterados), uma melhor confirma promoção (arquivo atualizado); caso bootstrap (sem `champion_metrics.json`) promove sem travar o primeiro ciclo. Teste de integração (`integracao`): rodar `retrain.yml` via `workflow_dispatch` numa branch de teste, confirmar que o PR automático é aberto com `data/model.pkl`/`champion_metrics.json`/`dvc.lock` atualizados, mergear, confirmar que o HF Space (Passo 11) rebuilda e `/health` reporta a nova `model_version`.

**Fechamento do risco de recall**: aqui a decisão do Passo 6 (threshold calibrado por custo) vira política formal do gate — se a decisão for "aceitar o gap" do SLO, isso fica registrado como exceção documentada em `champion_metrics.json`/CHANGELOG, não travando promoções indefinidamente.

---

## Passo 10 — CI/CD (GitHub Actions + sync para Hugging Face Hub)

**Decisão de mecanismo de deploy** (define como o Passo 9 e o Passo 11 se conectam): usar a GitHub Action oficial de sync para o Hugging Face Hub (`huggingface/huggingface-sync-action`, conforme [docs do Hub sobre GitHub Actions](https://huggingface.co/docs/hub/repositories-github-actions) e a action do [GitHub Marketplace](https://github.com/marketplace/actions/sync-github-to-hugging-face-hub)) — a cada push em `main` (incluindo o merge do PR automático de re-treino do Passo 9), o GitHub Actions espelha o repo para o HF Space, que rebuilda sozinho. Isso substitui qualquer script manual de "git remote"/sync caseiro.

- `.github/workflows/ci.yml`: lint (formalizar `ruff` em `requirements.txt`/config, já há `.ruff_cache/` local), `pytest` (respeita `pytest.ini`, só testes rápidos por padrão), job opcional com serviço Postgres/Supabase local do GitHub Actions para os testes `integracao`. Dispara em PR (não faz deploy).
- `.github/workflows/deploy.yml`: usa `huggingface/huggingface-sync-action` para sincronizar `main` → o repo do HF Space sempre que houver push em `main` (token do Space como secret do GitHub, `HF_TOKEN`). Só roda **depois** do `ci.yml` passar (via `workflow_run` ou job dependente), para nunca sincronizar um estado quebrado.

**Verificação**: abrir PR de teste, confirmar que `ci.yml` dispara e passa; quebrar um teste de propósito, confirmar que o CI falha e `deploy.yml` não roda; mergear um PR válido em `main` e confirmar que `deploy.yml` sincroniza e o HF Space rebuilda automaticamente (sem passo manual).

---

## Passo 11 — Deploy no Hugging Face Space

**Decisão**: um único HF Space (SDK Docker) rodando Streamlit + FastAPI juntos (Streamlit chama o modelo em processo), evitando pagar por dois serviços always-on. Supabase free tier + HF Space free/community tier mantêm custo perto de US$0, deixando a margem dos US$100/mês para custo real de mensageria Infobip e eventual upgrade de CPU se o cold start violar o SLO de <10s.

- ~~`infra/deploy/dockerfile` (API+UI combinados, `requirements/api.txt`+`ui.txt`, sem `requirements/train.txt`), carregando `data/model.pkl` empacotado na imagem (não de um MLflow ao vivo — decisão do Passo 9)~~ — **feito no Passo 4** (antecipado), junto com `infra/deploy/entrypoint.sh`, o serviço `app` do `docker-compose.yml`, `.dockerignore` e `.gitattributes`. Verificado localmente: `/health` 200 com a `model_version` esperada, UI em 7860, mesma probabilidade dentro e fora do container, container morre inteiro se um dos processos cair e para em <1s no SIGTERM.
- Resta aqui: front-matter YAML do HF Space, `APP_ENV=prod`, segredos configurados na UI do Space (não versionados), e a checagem de usuário não-root/permissões de escrita que o Space exige.
- **Porta única do Space**: o HF Space (SDK Docker) publica só uma porta (`app_port`, default 7860). Hoje a imagem expõe UI em 7860 e API em 8000 — em produção só a primeira ficaria acessível. A UI não sofre (chama o modelo em processo, ADR-005 b); quem fica sem endereço público é a API como porta de entrada para integrações externas (diagrama C2). Decidir entre: (a) expor só a UI e adiar a API pública para quando houver um consumidor externo real, (b) proxy reverso na frente dos dois na porta publicada, (c) publicar a API e servir a UI por outro caminho. Registrar a escolha como emenda ao ADR-005 ou ADR novo, conforme o peso.
- Deploy inicial: criar o HF Space e rodar manualmente o `deploy.yml` do Passo 10 (`workflow_dispatch`) para o primeiro sync via `huggingface/huggingface-sync-action`. Dali em diante, todo push em `main` (deploy manual de código ou promoção automática do Passo 9) usa o mesmo workflow — não há um segundo mecanismo de deploy a manter.

**Verificação**: Space público respondendo `/health` 200 com a `model_version` esperada; smoke test manual fim a fim (criar agendamento → job via `workflow_dispatch` → predição+explicação visível na fila do Streamlit → log de decisão de mensageria); medição manual do cold start vs. SLO <10s; smoke test do loop de deploy automático (merge de um PR de promoção do Passo 9 → confirmar que o Space rebuilda sozinho, sem passo manual).

---

## Passo 12 — Validação final para o pitch (Semana 16)

- `scripts/medir_latencia.py` (mede p95 de `/predict` contra o deploy real); atualizar `docs/SLA.md`/`docs/SLO.md` com coluna "medido" ao lado de "alvo"; `docs/LGPD.md` (Passo 8) como anexo de riscos.

**Verificação**: todos os números do SLA/SLO coletados e documentados (uptime, p95, recall/f1/roc-auc do MLflow, 100% cobertura SHAP via `predicoes.explicacao_shap IS NOT NULL`, 0 PII em log); **decisão final sobre o gap de recall registrada explicitamente** (threshold recalibrado ou gap aceito e documentado) antes da apresentação.

---

## Passo 13 (opcional, pós-núcleo) — LLM/TrueFoundry

Só depois do Passo 12 (núcleo funcional, testado e deployado). Consulta de paciente específico pelo funcionário (item do README, não exigido por SLA/SLO).

- `src/llm/client.py`: mesmo padrão interface+stub do Passo 7 (`StubLLMClient` para dev/test, `TrueFoundryClient` real atrás de env flag). Prompt montado só com dados já pseudonimizados do banco (Passo 5), nunca PII.
- **Ativação do plug do Passo 2**: trocar `ExplicadorLLMDesativado` por `ExplicadorLLMTrueFoundry` em `src/explain.py`, implementando `explicar_em_texto()` — recebe as contribuições SHAP já calculadas (Passo 2) + contexto pseudonimizado e devolve uma frase em português para a clínica (ex. "risco alto principalmente por 3 faltas anteriores e distância de 18km"). Passa a preencher `explicacao_texto` em `predicoes` (antes sempre `NULL`).
- Nova aba/caixa "Consulta ao LLM" em `src/ui/app.py` (Passo 4), onde o funcionário pergunta livremente sobre um paciente específico da fila.

**Verificação**: `tests/test_llm.py` com o stub, garantindo que o contexto montado nunca contém campos proibidos (reusa helper do Passo 8); teste de contrato stub/real. Teste específico de `explicar_em_texto()`: dado um conjunto fixo de contribuições SHAP sintéticas, o texto gerado (via stub determinístico, não chamando o TrueFoundry real em CI) menciona a feature de maior `abs(contribuicao)` — prova que a explicação em texto é fiel ao SHAP, não uma alucinação desconectada dos números.

---

## Arquivos críticos já mapeados

- `src/preprocess.py` (`COLUNAS_CATEGORICAS`, `preprocessar`) — base do Passo 1
- `src/features.py` (`extrair_features_temporais`) — reuso direto no Passo 1
- `src/train.py` (formato de `data/model.pkl`, linha 95) — contrato que o Passo 1 precisa respeitar
- `params.yaml` (`decision.threshold`, hiperparâmetros) — fonte única de verdade para API/job/gate
- `tests/conftest.py` — fixtures e padrão de teste (sem mocks pesados) a seguir em todos os passos novos
- `docs/architecture.md`, `docs/adr/adr-004-decisão-técnica.md` — alvo do Passo 0
- `pytest.ini` — já define marcador `integracao`, usar nos testes de DB/job/deploy
- `src/ui/app.py`/`src/ui/logic.py` (criados no Passo 4) — casca de abas reaproveitada e preenchida de verdade nos Passos 5-7 e 13

## Como validar o plano ponta a ponta

A cada passo, os critérios de verificação descritos já são o teste — não há uma validação única no final. A interface Streamlit (Passo 4 em diante) funciona como harness de verificação visual contínuo, crescendo junto com o backend. O plano só é considerado "produto pronto" quando o Passo 12 fecha com os números do SLA/SLO medidos contra o deploy real (não estimados), incluindo a decisão explícita sobre o gap de recall.
