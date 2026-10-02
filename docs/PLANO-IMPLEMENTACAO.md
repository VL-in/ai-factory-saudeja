# Plano de Implementação — SaudeJá: do pipeline de treino ao produto deployável

> **Status:** em execução. Última geração: 2026-09-18 (atualizado em 2026-09-21 com o Passo 8.5 — observabilidade de aplicação; em 2026-09-29 com a revisão do Passo 10 contra o repositório e a documentação do GitHub Actions; em 2026-10-01 com o Passo 10.7 — canário do modelo com rollback automático, [ADR-009](adr/adr-009-canario-do-modelo.md)). Complementa [`architecture.md`](architecture.md) (o "o quê"/"por quê" da arquitetura) com o "como e em que ordem construir" — cada passo abaixo é uma fatia vertical testável, com critério de verificação explícito, que deve ser commitada em git antes de avançar para a próxima.

## Contexto

O repositório tem hoje um pipeline de ML maduro (DVC + MLflow + LightGBM + SMOTE-NC, 3 stages testados) mas **nenhuma camada de aplicação**: sem API, sem banco, sem interface, sem mensageria, sem explicabilidade, sem automação de re-treino. [`architecture.md`](architecture.md) e os [ADRs](adr/) já desenham a arquitetura-alvo (diagramas C4 nível 1/2), e [`BRIEFING.md`](BRIEFING.md)/[`SLA.md`](SLA.md)/[`SLO.md`](SLO.md) definem requisitos de negócio concretos (API + interface + threshold calibrado + explicabilidade obrigatória + LGPD + re-treino mensal automatizado com gate de rollback), com pitch para o Conselho de Investidores na Semana 16.

Este plano constrói essa camada em **fatias verticais testáveis**: cada passo entrega uma parte funcional da arquitetura com um critério de verificação explícito antes de avançar para o próximo — nunca "big bang". A ordem prioriza o núcleo que tem SLA (predição → explicação → API → **interface Streamlit já como casca cedo, para servir de harness de teste visual** → banco → job → mensageria → LGPD → observabilidade → re-treino → CI/CD → deploy → validação final) e deixa a integração de LLM (TrueFoundry) como etapa opcional ao final, por não ser exigida pelo SLA/SLO. A interface entra logo depois da API (Passo 4) propositalmente: assim, cada capacidade nova (banco, job, mensageria) é plugada numa aba já existente e testada visualmente assim que fica pronta, em vez de só ser validada por `pytest`/`curl` até o fim do plano.

**Decisões já validadas com a autora do projeto:**
- Banco de dados: **SDK oficial `supabase-py`** (alinhado ao ADR-004), não camada Postgres genérica.
- ADR-004 é a decisão de stack vigente; **ADR-001 será marcado como superseded** nos pontos onde conflita (banco, mensageria, observabilidade, deploy).
- LLM/TrueFoundry é **opcional**, só depois do núcleo (predição+fila+mensageria+deploy) estar funcional e testado.
- SHAP (Passo 2) já nasce com um **plug inativo** para um dia ser traduzido em texto por LLM (ativado no Passo 13).
- A interface Streamlit (Passo 4) inclui uma aba de desenvolvedor para **disparar o pipeline de inferência manualmente** e acompanhar o pipeline sem depender de CLI/cron separados.
- CI/CD usa a GitHub Action oficial **`huggingface/huggingface-sync-action`** para sincronizar `main` → Hugging Face Space (Passo 10/11), não um script de sync caseiro. *(2026-09-29: a action foi renomeada para `huggingface/hub-sync` e sobe arquivos por HTTP, não por git — o que muda o que vai ao Space. Ver a revisão do Passo 10.)*
- Observabilidade de **aplicação** (distinta da observabilidade de **ML**, que é o MLflow do ADR-004): três camadas gratuitas — Supabase como fonte de verdade, sonda externa de uptime e dead-man's-switch para os jobs agendados. Langfuse fica fora do núcleo, reservado ao Passo 13; OpenTelemetry e Grafana Cloud descartados. Ver [ADR-006](adr/adr-006-observabilidade.md) e o Passo 8.5.

**Risco a não decidir agora, só monitorar**: recall atual da classe positiva é 0.522 (ADR-003), abaixo do alvo do SLO (≥0.75). Não é bug — é limite do dataset sintético pequeno (380 linhas). O plano cria os checkpoints certos para revisitar isso (Passo 6, com dados fluindo pelo job, e Passo 12, fechamento para o pitch) em vez de decidir threshold às cegas agora.

**Convenção de execução (ciclo de cada passo)**: implementar → rodar a verificação automatizada do passo → verificar manualmente (quando o passo tiver esse componente) → **commitar em git** (só o que pertence àquele passo, com mensagem descrevendo o que foi entregue e o que foi verificado) → só então avançar para o próximo passo. Nenhum passo começa antes do commit do anterior. Isso mantém o histórico do repositório como registro fiel do progresso e dá pontos de checkpoint humano reais (revisar/testar cada incremento antes de autorizar o próximo).

---

## Passo 0 — Reconciliar arquitetura e documentação

**Objetivo**: eliminar a ambiguidade entre ADRs e destravar todos os passos seguintes com uma referência única sem contradição.

- `docs/adr/adr-001-stack.md`: `Status` → `Superseded por ADR-004` nos pontos que ele resolve (banco, mensageria, LLM gateway, deploy); mantém válida a parte não contestada (Streamlit + FastAPI + DVC/MLflow).
- `docs/adr/adr-004-decisão-técnica.md`: `Status` → `Aceito`; preencher a seção "Para deploy, foram considerados: -" (hoje vazia); registrar decisão de observabilidade: MLflow (já existe) cobre o pipeline de ML; Langfuse fica reservado só para tracing do LLM (Passo 13, opcional), não bloqueante; n8n descartado em favor do job agendado (Passo 6) + Infobip direto.
  - **Emenda (2026-09-21)**: este item cobriu a observabilidade **de ML**. A observabilidade **de aplicação em produção** (uptime, latência, execução dos jobs, auditoria de PII) ficou em aberto e agora tem ADR próprio — [ADR-006](adr/adr-006-observabilidade.md), implementado no Passo 8.5. O ADR-001 fica superseded também no ponto "Langfuse para observabilidade", que ele tratava como ferramenta geral do produto.
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

**Minimização de PII por design**: tabela `pacientes` guarda `id_paciente_externo` (referência ao sistema core da clínica) + atributos demográficos não identificáveis — **nunca CPF nem e-mail**, reduzindo a superfície de risco LGPD estruturalmente, não só por convenção de logging (Passo 8). Duas exceções deliberadas foram abertas depois, cada uma por dado ser necessário à finalidade e com ADR/migration própria: `telefone` (Passo 7) e `nome_completo` ([ADR-007](adr/adr-007-nome-do-paciente.md), 2026-09-28).

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

**Ajuste posterior (2026-09-28) — nome do paciente na "Fila do dia" ([ADR-007](adr/adr-007-nome-do-paciente.md))**

A "Fila do dia" entregue neste passo mostrava o **hash sha256 de 64 caracteres** na coluna "Paciente". Não era um problema de UX: era uma tela que não podia ser operada — ninguém chama `9f86d081884c7d65…` na sala de espera, e o registro de desfecho do Passo 9.0 (`concluido`/`no_show`, de onde sai o dado real do re-treino) depende de quem atende saber de quem é a linha. Decisão da autora, com o racional completo e as alternativas recusadas no ADR-007:

- `pacientes.nome_completo` passa a ser persistido (migration `20260928000000_nome_paciente.sql`, nullable, **sem backfill** — fabricar nome de paciente é pior que admitir a lacuna, e a UI mostra o início do hash nas linhas antigas). **CPF segue nunca gravado.**
- O critério é o mesmo que abriu a exceção do `telefone` no Passo 7: **necessidade**, não conveniência. Sem contato não há lembrete; sem nome não há fila operável. E nenhum destinatário novo entra em cena — o funcionário é preposto da controladora e já detém o prontuário.
- A guarda de migrations deixou de ser "lista de colunas proibidas" e passou a autorizar por **par (coluna, arquivo)**: `telefone` só vale na migration que o introduziu, `nome_completo` só na dele; em qualquer outro arquivo, `nome_completo` volta a cair na proibição de `nome`. CPF e e-mail seguem proibidos sem exceção.
- **Dois achados colaterais, ambos corrigidos**: (a) `buscar_fila_do_dia` usava `select("*, pacientes(*), predicoes(*)")` — o curinga já trazia `telefone` para a camada de UI sem nenhum uso, e faria o mesmo com qualquer coluna de PII futura; virou lista explícita de colunas, que é a fronteira que um teste consegue inspecionar. (b) `ExplicadorLLM.explicar_em_texto` era abstrata, então cada implementação futura teria de lembrar de sanitizar o contexto do prompt; passou a ser concreta, aplicando `contexto_sem_pii` antes de delegar a `_gerar_texto`. A fronteira do LLM existe **antes** do Passo 13, não depois dele estar funcionando.
- **Registrado porque foi perguntado**: cifrar a coluna no banco **não** protegeria o nome do LLM — a chamada sai do mesmo processo que já leu o nome para desenhar a tela, com a chave na mesma memória. Cripto na aplicação defende contra vazamento do dump/da chave do Supabase, que é outro risco; foi avaliada e adiada no ADR-007 (teria de cobrir `telefone` também, e perder a chave é perder todos os nomes).
- **Custo assumido**, em `LGPD.md` §9: a superfície de reidentificação cresceu (vazamento do banco agora expõe nome + telefone + especialidade + desfecho), a tela da recepção fica legível para a sala de espera (mitigado por aviso, não por mecanismo) e o risco de ausência de autenticação **piorou** — a justificativa para exibir o nome é que quem vê assinou confidencialidade, e nada no sistema verifica isso hoje. Fechar autenticação virou pré-requisito da própria decisão em operação real.

**Verificado**: `ruff` limpo, **222 testes** rápidos (baseline 205 ao fim do Passo 8) e integração real contra o Supabase local depois de `supabase db reset` — 10 testes de `test_db.py` (round-trip do nome até o rótulo da tela, recadastro sem nome não apagando o nome gravado, e a fila não trazendo `telefone` do banco) mais 6 de `test_job_inferencia.py`/`test_observabilidade.py`, todos verdes. A guarda das portas de saída tem controle negativo conferido: acrescentar `nome_completo` ao select do job D-2 faz o teste falhar.

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

**Ajuste vindo do [ADR-006](adr/adr-006-observabilidade.md) (2026-09-21)**: o log de runtime do HF Space é **efêmero** (restart ou rebuild apaga, sem busca e sem retenção), então a auditoria a posteriori prevista no SLO §6 não é executável em produção. A garantia de zero-PII passa a ser **preventiva**: o teste estático sobre `src/` descrito acima é a evidência principal, e `scripts/auditoria_lgpd.py` vira ferramenta de verificação local, não a prova apresentada no pitch.

### Estado: implementado em 2026-09-27 — o que mudou em relação ao texto acima

O texto do passo foi confrontado com o repositório real antes de implementar, e quatro coisas nele não se sustentavam. As quatro decisões foram tomadas com a autora.

1. **O passo, como escrito, seria cerimonial — não existia log nenhum para filtrar.** `src/` não tinha uma única chamada de `logging`: só `print` nos scripts do pipeline. Um filtro de redação sem call site é código morto, o mesmo problema que o Passo 9 pegou no gate. Duas consequências:
   - **O filtro mora no handler e é aplicado a TODO handler do processo**, não num logger próprio do projeto. O log que de fato existe em volume em produção é o de **terceiros** — `uvicorn` (acesso, com IP de cliente, e erro), `httpx`, `supabase-py`, `streamlit` —, e o `uvicorn` põe `propagate=False` nos loggers dele, de modo que nem o handler da raiz os alcançaria. `aplicar_filtro_em_handlers_existentes()` passeia pelos handlers já instalados e blinda cada um. É a peça que faz a garantia valer para o log que não temos como reescrever.
   - **Os três entrypoints passaram a logar de verdade**: `configurar_logging()` no `lifespan` da API, no `main()` do job e no topo de `src/ui/app.py`, mais as poucas linhas que fazem sentido existir (startup com `model_version`, resumo do job, falha por agendamento com só a classe da exceção). O `print` final do job virou uma linha JSON — ele roda por cron no Actions (Passo 10), onde stdout é a única saída que sobra, e o resumo precisa ser grep-ável junto do resto. Os scripts do pipeline de treino seguem com `print` de propósito: rodam sobre dataset pseudonimizado, fora do caminho de qualquer dado de paciente vivo.

2. **Havia um vetor de PII em log concreto já no código, e ele foi corrigido na origem.** `ErroEnvioInfobip` embutia `resposta.text` na mensagem — e a Infobip ecoa o payload enviado no corpo de erro, com o `to`, que é o **telefone do paciente**. Essa string ia inteira para `resultado["erros"]` do job, era exibida crua por `st.json` na aba de dev e vazaria em qualquer `logger.warning(f"...{exc}")` futuro. A mensagem passa a carregar o par (status HTTP, `messageId` da Infobip) — que é o que identifica a causa, e foi assim que se diagnosticou `EC_ACCOUNT_NOT_PROVISIONED_FOR_CHANNEL` no Passo 7. O corpo completo fica em `exc.corpo_bruto`, documentado como "não logar nem exibir". O filtro cobre o caso de alguém esquecer; o atributo existe para não perder capacidade de diagnosticar uma falha nova.

3. **A lista de campos a redigir ("`nome`/`cpf`") estava incompleta** — é de antes do Passo 7, que acrescentou `telefone` ao schema como exceção deliberada à minimização de PII. Telefone é hoje o único identificador direto que o runtime manipula. A lista passa a ser nome/sobrenome/CPF/e-mail/telefone/IP, com duas famílias de regra, porque PII tem duas naturezas: CPF, telefone, e-mail e IP têm **forma** reconhecível; **nome não tem** — "Ana Souza" é indistinguível de qualquer par de palavras —, então o que se reconhece é a **chave** que o anuncia (`nome=`, `"nome":`, `extra={"nome": ...}`). Emendado no [SLO §6.1](SLO.md) e no [SLA §6](SLA.md).

4. **Duas pendências de LGPD declaradas em passos anteriores foram fechadas aqui, e não estavam no texto do passo.** (a) A **base legal da transferência internacional** do remote do DVC em Chile Central (Art. 33, II, "d" — cláusulas contratuais padrão do DPA do provedor), que o [ADR-005](adr/adr-005-integracoes-implicitas.md) adiou explicitamente para o Passo 8. (b) A **política de retenção**, que só existia para `eventos_app` (90 dias): `predicoes` e `mensagens_disparadas` passam a ser purgadas em 365 dias (`RETENCAO_DADOS_DERIVADOS_DIAS`), pela mesma purga diária, sem agendador novo. `pacientes`/`agendamentos` ficam de fora de propósito — e é o que obrigou a escrever o que estava implícito na arquitetura: a **clínica é controladora e a SaúdeJá é operadora**, então o registro do atendimento não é nosso para apagar.

**O que `docs/LGPD.md` entrega além do previsto** (o texto pedia "base legal, retenção, contato DPO"): papéis controlador/operador e as três consequências práticas disso; inventário com a localização exata da sensibilidade (`especialidade` + o fato do agendamento — não há diagnóstico, exame ou prontuário no sistema, e isso é desenho, não sorte); a divergência consciente em relação às notas da Camila (consentimento **não** foi mantido como base legal do dado sensível, porque a alínea "f" do Art. 11, II cobre a situação sem a fragilidade da revogabilidade); direitos do titular com o limite de cada um; e §9 com os riscos residuais nomeados, entre eles a tensão real entre rastreabilidade de ML (DVC guarda histórico por construção) e o Art. 18, VI.

**Verificado**: `pytest` rápido verde (**205 testes**, 30 novos em `test_logging_lgpd.py` + 2 em `test_coerencia_repo.py` + 2 em `test_messaging.py`), `ruff` limpo, e um smoke real fora da suíte — processo que instala handler de terceiro **antes** de `configurar_logging()`, emite PII por quatro caminhos (mensagem, `extra`, traceback, handler de terceiro) e tem a saída varrida por `scripts/auditoria_lgpd.py`: zero achado, com `id_agendamento`, hash de CPF, `model_version` e `duracao_ms` preservados. O mesmo script sobre um log não blindado devolve os achados e código de saída 1.

**Pendência conhecida**: `tests/test_ui_smoke.py` não roda no ambiente local por falta de `streamlit` instalado no `.venv` (`requirements/ui.txt` não aplicado) — condição anterior a este passo. As duas linhas adicionadas a `src/ui/app.py` (`configurar_logging()` no topo) só são exercitadas onde o smoke test roda; o CI do Passo 10 instala `requirements/dev.txt`, que inclui `ui.txt`.

---

## Passo 8.5 — Observabilidade de aplicação

> Numerado como 8.5 de propósito: renumerar os Passos 9-13 quebraria dezenas de referências cruzadas neste plano, no README e nos ADRs, sem ganho algum.

**Objetivo**: tornar os números do SLO coletáveis em produção, em vez de estimados na véspera do pitch. Vem depois do Passo 8 porque reaproveita o logging estruturado criado lá, e antes do Passo 9 porque o gate de re-treino já precisa de um canal de alerta e de prova de execução. A decisão completa, com alternativas descartadas, está no [ADR-006](adr/adr-006-observabilidade.md).

**Princípio que organiza o desenho**: o coletor não pode morar dentro daquilo que ele mede. Métrica coletada dentro do Space some junto com o Space quando ele hiberna ou cai — inclusive a evidência de que caiu. Daí a separação entre camada interna (Supabase, fonte de verdade) e camadas externas (sondas).

- Nova migration `supabase/migrations/*_eventos_app.sql`: tabela `eventos_app` (id, `criado_em`, `tipo` — ex. `predicao`/`job_d2`/`erro`, `duracao_ms`, `status`, `model_version`, `detalhe jsonb`). **Sem nenhuma coluna de PII** — a guarda de `tests/test_coerencia_repo.py` já cobre isso automaticamente e não precisa ser estendida.
- `src/db/repositories.py`: `registrar_evento(...)` e as leituras agregadas que a aba consome (p95 de `duracao_ms` por período, contagem de erros, cobertura de SHAP).
- Middleware do FastAPI em `src/api/main.py` e instrumentação equivalente em `src/jobs/inferencia_diaria.py`, gravando um evento por predição/execução. **Escrita não bloqueante** — o registro não pode entrar no caminho crítico da latência que ele existe para medir (SLO §2).
- Nova aba **"Observabilidade"** em `src/ui/app.py`: p95 de latência, contagem de erros, cobertura de explicação (`predicoes.explicacao_shap IS NOT NULL`), última execução bem-sucedida do job D-2. É daqui que saem os números do Passo 12.
- Contas gratuitas nos dois serviços externos, ambos recebendo apenas sinal binário (nenhum dado de paciente): **UptimeRobot** (ou Better Stack) batendo em `/health` do Space, e **Healthchecks.io** como dead-man's-switch dos jobs agendados. A configuração efetiva depende da URL pública e acontece no Passo 11; os workflows que pingam são criados no Passo 10.
- Alerta reusa `src/messaging` (Passo 7) — nenhum canal novo. **Emendado em 2026-09-21 (decisão 4 do Passo 9)**: o alerta sai por Healthchecks/GitHub Actions, não por `src/messaging` — `enviar_lembrete(telefone, mensagem)` foi desenhada para paciente, por SMS pago, e não há destinatário de equipe cadastrado em lugar nenhum. Continua valendo o princípio (nenhum canal novo), muda qual canal existente é reaproveitado.

**Verificação**: `tests/test_observabilidade.py` — `registrar_evento` faz round-trip no Supabase local (marcado `integracao`, mesma convenção de `test_db.py`); teste confirmando que falha ao gravar evento **não derruba** a predição nem o job (observabilidade quebrada degrada, não interrompe — mesma filosofia do `ErroEnvioInfobip` do Passo 7); teste de que o p95 calculado bate com um conjunto sintético de eventos de latência conhecida. Manual: abrir a aba "Observabilidade" depois de rodar o disparo manual do Passo 6 e conferir que os eventos aparecem.

**Política de retenção**: definir no momento da migration (ex. descartar eventos com mais de N meses) — a tabela cresce a cada request e divide o teto do free tier com os dados do produto. Registrado como risco no ADR-006.

### Estado: implementado em 2026-09-21 — o que mudou em relação ao texto acima

Seis ajustes decididos ao implementar, todos registrados como emendas ao [ADR-006](adr/adr-006-observabilidade.md):

1. **Não depende do Passo 8.** O texto acima posicionava o 8.5 depois do Passo 8 "porque reaproveita o logging estruturado criado lá" — mas o Passo 8 ainda não foi executado (`src/logging_config.py` não existe). A instrumentação foi escrita sem nenhuma dependência dele: a fonte de verdade é a tabela, não o log. Isso na verdade *reforça* a decisão do ADR-006 de que o log do Space é efêmero demais para servir de evidência — e, de quebra, não emite nenhuma linha nova de log, então não abre superfície de PII antes de o Passo 8 existir.
2. **O caminho em processo também é instrumentado**, não só o middleware da API. Medir apenas `/predict` calcularia o p95 do SLO §2 sobre a rota que, por decisão do [ADR-005](adr/adr-005-integracoes-implicitas.md) (b), quase não recebe tráfego — a predição que o funcionário espera acontece em processo (`ui/logic.py::ClientePredicaoEmProcesso`) e no job. A coluna `origem` (`api`/`processo`/`job`) mantém os caminhos separáveis em vez de somados num número só.
3. **`/health` fica fora da instrumentação.** A sonda externa bate nela de minutos em minutos; registrar cada batida somaria milhares de linhas por mês que não dizem nada sobre latência de predição, ocupando o free tier. Uptime é medido por quem sonda, de fora — que é o próprio princípio do ADR.
4. **A guarda de PII precisou ser estendida, ao contrário do que o texto acima previa.** `tests/test_coerencia_repo.py` faz grep por *nome de coluna* nas migrations; `detalhe jsonb` aceita qualquer chave e passaria batido. A proteção passou a ser uma allowlist fechada em `src/observabilidade.py` (contadores, rota, status HTTP e *nome de classe* de exceção — nunca mensagem de erro, que no caso da Infobip ecoa o telefone do paciente), com teste de runtime e checagem estática sobre os call sites de `src/`.
5. **Retenção roda no job diário** (`purgar_eventos_antigos`, `OBSERVABILIDADE_RETENCAO_DIAS`, default 90), não em `pg_cron`: o job já é diário, e adicionar agendador é adicionar infra a manter — o mesmo critério que descartou Grafana/OTel no ADR.
6. **Sondas externas e alerta continuam pendentes**, como o próprio plano previa: UptimeRobot e Healthchecks.io dependem de URL pública (Passo 11) e os workflows que pingam são do Passo 10. O que o 8.5 entrega é a camada interna inteira, de ponta a ponta.

**Verificado**: `pytest` rápido verde (**150 testes**, 17 novos em `test_observabilidade.py`), `ruff` limpo, round-trip e purga contra o Supabase local (`-m integracao`), e a aba "Observabilidade" renderizando com e sem banco disponível.

**Achado colateral (corrigido junto)**: `tests/test_ui_smoke.py` apagava `SUPABASE_URL`/`SUPABASE_SECRET_KEY` com `monkeypatch.delenv` para isolar a UI do banco — mas `config_projeto` chama `load_dotenv()` no import, e variável *apagada* é exatamente o caso em que o dotenv a redefine. Quem tivesse `.env` local rodava a suíte de UI contra o projeto Supabase **real**. Trocado por string vazia (a chave existe, o dotenv não sobrescreve, `db/client.py` trata como ausente). `tests/conftest.py` ganhou uma fixture autouse que aponta o destino de `eventos_app` para um escritor nulo durante toda a suíte, pelo mesmo motivo.

---

## Passo 9 — Re-treino mensal automatizado + gate de rollback + deploy automático do modelo promovido

**Problema a resolver aqui, não só "rodar o treino de novo"**: o MLflow do repo hoje só existe como `docker-compose` local/efêmero (sobe, treina, os stages leem, nada persiste além do volume local). Para o CI (GitHub Actions, mensal) conseguir comparar "modelo novo" contra "modelo campeão" **entre execuções**, e para a API em produção (Passo 11) carregar o modelo promovido **sem depender de um MLflow sempre-no-ar** (custo/operação fora do orçamento de US$100/mês), a fonte de verdade do "campeão" precisa ser **versionada no próprio repositório**, não só numa run de MLflow que só existe durante o workflow.

> **Revisão do passo contra o repositório real (2026-09-21)**: antes de implementar, o texto original foi confrontado com o estado do repo e apoiava-se em quatro premissas que ele não sustenta — não havia dado fresco algum para re-treinar, o `model.pkl` não tem como chegar ao Space, a tolerância do gate era menor que a granularidade do fold de teste, e o canal de alerta escolhido não tinha destinatário. As quatro decisões estão na seção **"Decisões de 2026-09-21"** no fim deste passo, e o corpo abaixo já as reflete. Continua em aberto um quinto ponto, técnico, também registrado lá.

### 9.0 — Pré-requisito: dados frescos de verdade (ingestão pela interface)

Sem isto o resto do passo é cerimonial: com `data/consultas-historicas.csv` fixo em 380 linhas e `RANDOM_STATE=42`/`TEST_SIZE=0.20` fixos em `src/preprocess.py`, todo re-treino mensal produz **métricas idênticas** — o gate nunca bloqueia, nunca promove, e o SLO §5 é cumprido na forma e não no conteúdo.

- **A clínica registra o desfecho na aba "Fila do dia"** (Passo 4/5): cada linha ganha a ação de marcar `concluido` / `no_show` / `cancelado`. Novo `repositories.atualizar_status_agendamento(id_agendamento, status)`. A coluna `agendamentos.status` já aceita `no_show` desde a migration `20260920010000_cadastro_pacientes.sql`, e `repositories.contar_no_shows_anteriores` já lê dela — **mas nada no produto nunca escreveu esse valor**. Ou seja: além de destravar o re-treino, isto conserta um buraco que já existia desde o Passo 5 (o `historico_noshow` automático do cadastro só começa a funcionar de verdade aqui).
- **Novo `src/export_treino.py`**: lê do Supabase os agendamentos com desfecho conhecido (`concluido`/`no_show`) e monta linhas no mesmo esquema de `data/consultas-historicas.csv` (`idade` via `features.calcular_idade` sobre `data_nascimento`, `no_show` derivado do `status`).
  - **Point-in-time obrigatório no `historico_noshow`**: cada linha exportada conta apenas os `no_show` **anteriores à `data_hora_agendada` daquela linha**. `contar_no_shows_anteriores` conta todos — correto para um cadastro novo, vazamento de futuro num export histórico. Função separada, não reuso direto.
  - **Sem PII**: `telefone` nunca entra no export; `id_paciente` já é o hash sha256 do CPF (Passo 5). Estender `tests/test_coerencia_repo.py` para cobrir as colunas que o export produz, pelo mesmo critério já aplicado às migrations.
- **`data/consultas-treino.csv` = semente + produção** passa a ser o `DATA_PATH` do stage `preprocess`. `data/consultas-historicas.csv` fica **intocado** como semente herdada da Camila — rastreabilidade de qual dado veio de onde, e nada de sobrescrever o único dataset conhecido do projeto. O arquivo novo é versionado por DVC como o atual (`dvc add`), e o `dvc.yaml` passa a depender dele.
- **Enquanto o volume de produção for pequeno**, o dataset continua dominado pela semente sintética — o que o gate mede segue sendo ruído até a clínica acumular desfechos reais. Isso é esperado e deve ser dito assim no pitch (Passo 12), não maquiado.

### 9.1 — Gate

> **Implementado em 2026-09-21.** `src/retrain_gate.py`, `data/champion_metrics.json` (semeado com as métricas medidas do modelo vigente), `params.yaml` (`gate.tolerancia`), `.github/workflows/retrain.yml`, `.dvc/config` (remote Azure) e `tests/test_retrain_gate.py` (17 testes). Quatro decisões novas e dois achados, na seção **"Decisões e achados de 2026-09-21 (ao implementar o 9.1)"** no fim deste passo.


- Novo `data/champion_metrics.json` (git-tracked, pequeno) — snapshot das métricas do modelo atualmente em produção (`recall_1`, `f1_1`, `roc_auc`, `model_version`/`run_id` de origem). Atualizado **só** quando o gate promove.
- `src/retrain_gate.py`: sobe o `mlflow-server` via `docker-compose` (mesmo mecanismo já usado hoje pelos stages `train`/`validate` — efêmero, só durante o workflow), roda o pipeline contra os dados frescos de 9.0, lê as métricas da nova run no MLflow, compara contra `data/champion_metrics.json` (não contra uma run "campeã" persistida no MLflow, que não sobreviveria entre execuções mensais); bloqueia promoção se `recall_1`/`f1_1`/`roc_auc` regredirem além das tolerâncias **por métrica** do SLO §3 (0.05 / 0.05 / 0.02 — ver decisão 3).
  - **Se passar**: sobrescreve `data/champion_metrics.json`; `data/model.pkl` e `dvc.lock` são atualizados pelo `dvc repro`, e `dvc push` envia o artefato ao remote Azure (decisão 2). O workflow faz `git commit` + `git push` do que é **de fato versionado em git** — `champion_metrics.json`, `dvc.lock` e o `.dvc` do dataset — em branch dedicada + PR automático, para manter revisão humana leve sem travar a automação (SLO §5 exige "100% dos meses, automatizada", não "sem rastro"). O `model.pkl` **não** vai nesse commit: ele é `out` do DVC e está coberto por `*.pkl` no `.gitignore`; quem o carrega é o `dvc pull` do `deploy.yml` (decisão 2).
  - **Se bloquear**: `champion_metrics.json`/`model.pkl` **não mudam** (modelo anterior continua em produção por construção, não por convenção), e o workflow **falha** (exit ≠ 0). A evidência da rejeição precisa sobreviver ao workflow: o comparativo campeão × desafiante vai para o `$GITHUB_STEP_SUMMARY` e como artifact JSON do run — taguear a run no MLflow efêmero não serve, porque ele é destruído junto com o job, e isso é exatamente o que o [ADR-006](adr/adr-006-observabilidade.md) proíbe ("o coletor não pode morar dentro daquilo que ele mede"). Alerta via Healthchecks/Actions (decisão 4).
  - Caso "bootstrap" (primeiro ciclo, `champion_metrics.json` ainda não existe): promove direto, sem comparação, e cria o arquivo — registrando explicitamente que o primeiro campeão **já viola os alvos absolutos do SLO §3** (`recall_1` 0.522 < 0.75, `f1_1` 0.419 < 0.65). O gate protege contra **regressão relativa**; o gap absoluto é exceção documentada, não critério de promoção.
- `.github/workflows/retrain.yml` (cron mensal + `workflow_dispatch` manual para teste). **Pinga o Healthchecks.io ao concluir com sucesso** (Passo 8.5): workflow agendado do GitHub é desativado automaticamente após inatividade prolongada do repositório, e sem o dead-man's-switch o re-treino mensal pode parar de rodar em silêncio — violando o SLA §5 sem que ninguém perceba (ver [ADR-006](adr/adr-006-observabilidade.md)).
- **Fecha o loop até produção**: como o Passo 11 empacota `data/model.pkl` direto na imagem do HF Space, o merge do PR de promoção em `main` dispara o `deploy.yml` do Passo 10, que faz `dvc pull` do modelo promovido (Azure) e sincroniza o Space via `huggingface/hub-sync` (o antigo `huggingface-sync-action`), a partir de um diretório de staging com lista fechada — não é preciso a API em produção falar com um MLflow ao vivo em nenhum momento, nem existe um segundo mecanismo de deploy além do já usado para código.

**Verificação**: `tests/test_retrain_gate.py` — duas runs MLflow fake (`sqlite:///{tmp_path}`, mesmo padrão de `test_train.py`) comparadas contra um `champion_metrics.json` de fixture, uma pior confirma bloqueio (arquivo/`model.pkl` inalterados), uma melhor confirma promoção (arquivo atualizado); caso bootstrap (sem `champion_metrics.json`) promove sem travar o primeiro ciclo; caso de regressão **dentro** da tolerância promove (prova que o número do SLO §3 está sendo lido, não hardcoded). Para 9.0: teste de que o export nunca emite coluna de PII e teste point-in-time (um paciente com `no_show` posterior à consulta exportada não pode aparecer no `historico_noshow` daquela linha). Teste de integração (`integracao`): rodar `retrain.yml` via `workflow_dispatch` numa branch de teste, confirmar que o PR automático é aberto com `champion_metrics.json`/`dvc.lock` atualizados, mergear, confirmar que o HF Space (Passo 11) rebuilda e `/health` reporta a nova `model_version`. **O ping do Healthchecks não é verificável aqui** — o check só existe a partir do Passo 11, que tem a URL pública; fica pendência declarada, como o Passo 8.5 já fez com as sondas externas.

**O que foi verificado de fato em 2026-09-21** (os três caminhos, rodados contra o repositório real, não só em teste): `dvc repro` sem dado novo → código 2, campeão intacto; comparação contra o campeão semeado → código 0 com deltas 0.0000 nas três métricas; campeão inflado para `recall_1` 0.75/`f1_1` 0.65/`roc_auc` 0.80 → código 1, com o comparativo no resumo e o arquivo do campeão **não** reescrito. Mais `ruff check` limpo e a suíte completa (178 testes) verde.

**Pendências declaradas** (dependem de coisas que ainda não existem, não de código): o `dvc push` real exige o container Azure criado e a credencial em `.env`/secrets — até lá o `dvc push`/`dvc pull` falha dizendo que o remote `azure` não existe, que é o comportamento desejado; o teste de integração do workflow (rodar `retrain.yml` por `workflow_dispatch`, conferir o PR, mergear e ver o Space rebuildar) depende dos Passos 10/11; e o ping do Healthchecks só é verificável a partir do Passo 11, mesma pendência que o Passo 8.5 já declarou para as sondas externas (com `HEALTHCHECKS_RETRAIN_URL` vazio o workflow pula o ping em vez de falhar).

**Fechamento do risco de recall**: aqui a decisão do Passo 6 (threshold calibrado por custo) vira política formal do gate — se a decisão for "aceitar o gap" do SLO, isso fica registrado como exceção documentada em `champion_metrics.json`/CHANGELOG, não travando promoções indefinidamente.

### Decisões de 2026-09-21 (tomadas com a autora, antes de implementar)

**1. A ingestão de dados reais entra pela interface** (detalhada em 9.0). A alternativa seria declarar "dado real é pré-requisito não atendido" e entregar só o andaime do gate — descartada porque o produto já tem todas as peças para fechar o ciclo (a coluna `no_show` existe, a fila do dia existe, o hash do paciente existe) e faltava só a clínica poder dizer o que aconteceu na consulta. Sem isso o re-treino mensal seria uma automação que roda todo mês para reproduzir o mesmo número.

**2. Persistência do `model.pkl` via Azure Blob Storage.** O remote DVC atual é `/tmp/dvc-remote` (caminho local, `.dvc/config`) — inacessível ao GitHub Actions e ao HF Space, o que hoje torna a verificação deste passo literalmente inexecutável: `infra/deploy/dockerfile` faz `COPY data/model.pkl` e falha sem o arquivo, que está coberto por `*.pkl` no `.gitignore`.
- `.dvc/config` (versionado) guarda só a URL `azure://<container>/<prefixo>`; **credencial nunca entra ali** — vai por variável de ambiente (`.env` local, secret no GitHub Actions). As variáveis ficam documentadas em `.env.example`.
- `dvc[azure]` precisa entrar em `requirements/dev.txt` (usado pelo CI): hoje o `dvc` **não está declarado em nenhum requirements** do repo, só no ambiente local da autora.
- **Região Brazil South**, pelo mesmo argumento que sustentou `sa-east-1` no [ADR-005](adr/adr-005-integracoes-implicitas.md) e o hash de CPF na v1.4: depois de 9.0, o dataset versionado no remote deixa de ser sintético puro e passa a conter dados de saúde de pacientes reais, ainda que pseudonimizados. Registrar como emenda ao ADR-005 ao implementar.
- **Como o modelo chega ao Space**: o `deploy.yml` (Passo 10) roda `dvc pull data/model.pkl` antes do sync e força a inclusão do arquivo no espelho enviado ao Space (`git add -f`; são 225 KB, não precisa de LFS). A exceção ao `.gitignore` vale só para o branch espelhado — `main` continua sem binário. A alternativa (o Space baixar do Azure no build) foi descartada: exigiria credencial Azure como secret do Space e contraria a decisão do Passo 4/11 de que o modelo viaja *dentro* da imagem.
  - **Emendado em 2026-09-29 (revisão do Passo 10, achado 1)**: não existe "branch espelhado" nem `git add -f` — a action de sync sobe o diretório por `hf upload` (HTTP), sem git. O `model.pkl` chega ao Space por estar num diretório de *staging* montado pelo `deploy.yml` com lista fechada de arquivos. Continua valendo o essencial desta decisão: `dvc pull` só de `data/model.pkl` no deploy, modelo dentro da imagem, nenhuma credencial Azure no Space.

**3. Tolerâncias por métrica, registradas no SLO §3.** A tolerância única de 0.02 herdada do SLO era menor que a granularidade do fold: com ~76 linhas de teste e ~21 positivos, o menor passo possível em `recall_1` é 1/21 ≈ 0.048 — um único paciente. O gate bloquearia ou promoveria por ruído de amostragem, fenômeno que o próprio README já documenta na nota do GridSearch (CV `f1_1`≈0.40 vs. holdout 0.372 vs. 0.419). Valores adotados: `recall_1` 0.05, `f1_1` 0.05, `roc_auc` 0.02 (mantido — é contínua, não sofre do problema de contagem). **O SLO §3 registra a regra, não só o número**: a tolerância das métricas de contagem é ≈ 1/(positivos no fold de teste), a ser revisada a cada crescimento relevante do dataset vindo de 9.0 — quando o fold tiver ≥50 positivos, ela cai para 0.02 e o gate passa a detectar regressões que hoje são invisíveis.

**4. Alerta de gate bloqueado por Healthchecks/Actions, não por `src/messaging`.** Reusar a mensageria seria abusar da abstração: `enviar_lembrete(telefone, mensagem)` foi desenhada para paciente, por SMS pago na conta trial da Infobip, e `mensagens_disparadas` é auditoria de envio a paciente (SLA §6), não de operação — sem falar que não existe destinatário de equipe cadastrado em lugar nenhum. O gate bloqueado falha o workflow (notificação nativa do GitHub ao autor/watchers), escreve o comparativo no `$GITHUB_STEP_SUMMARY` e **deixa de pingar** o check de sucesso do Healthchecks (ou pinga o endpoint `/fail`), que dispara o alerta por e-mail. Nenhum canal novo, nenhum custo, e nada de SMS para avisar engenheiro. Isso emenda a linha "Alerta reusa `src/messaging`" do Passo 8.5.

**5. Os stages do `dvc.yaml` passam a rodar por `docker compose run`** (aplicado em 2026-09-21, junto com esta revisão). Os três stages embutiam `docker run --rm -v "%cd%/data:/app/data"` — sintaxe do `cmd.exe`, e o próprio README avisava que não se podia copiá-la para um terminal Bash. Em `ubuntu-latest` o `%cd%` não expande, o Docker cria um diretório com esse nome literal em vez de montar o `data/`, e `src/retrain_gate.py` **não poderia chamar `dvc repro`** — o passo inteiro assumia uma coisa que não funciona.

O `docker-compose.yml` ganhou um serviço `train` (build do `dockerfile`, tag `saudeja-train` mantida, `volumes: ["./data:/app/data"]`), e os `cmd` viraram `docker compose run --rm --build -T -e ... train src/<etapa>.py`. O Compose resolve `./data` **relativo ao próprio arquivo**, nos dois sistemas operacionais — o mesmo comando vale em PowerShell, em Bash e no runner do GitHub Actions. O `-T` desabilita a alocação de TTY, que não existe em CI. `docker-compose.yml` entrou nas `deps` dos três stages, já que agora ele participa da definição de como cada um roda (efeito colateral aceito: mexer no serviço `app` também invalida os stages de treino).

Descartadas: **wrapper `scripts/run_stage.py`** montando o `docker run` com `os.getcwd()` — resolve, mas tira o comando de vista do `dvc.yaml` e acrescenta um arquivo só para contornar sintaxe de shell; **executar os scripts nativamente no CI** (sem Docker) — criaria dois caminhos de treino que divergem em silêncio, contra a convenção do Passo 10 de que CI e local rodam os mesmos comandos; **caminho relativo no `docker run`** (`-v ./data:...`) — depende da versão do Docker Engine e falha de forma confusa nas antigas.

**Reconciliar o `dvc.lock` com `dvc commit`, não com `dvc repro`.** O `cmd` de cada stage fica gravado no `dvc.lock`, então os três aparecem como alterados. `dvc repro` resolveria, mas reexecutaria o pipeline inteiro por uma mudança que não altera o que roda dentro do container — e teria três efeitos indesejados: (a) regeraria `data/model.pkl`, cujo sha256 **é** o `model_version` (`src/inference.py::calcular_model_version`), exposto em `/health`, gravado em `predicoes.model_version` e, a partir deste passo, em `champion_metrics.json` — um ajuste de sintaxe apareceria como troca de modelo em produção; (b) abriria uma run nova no MLflow para um treino que não mudou nada, sujando o histórico que o gate lê; (c) exigiria Docker e `mlflow-server` de pé para nada. `dvc commit` reescreve o lock com os outs atuais sem executar, preservando o `model.pkl` byte a byte.

A ressalva vale registrar porque não generaliza: `dvc commit` é uma afirmação de que os outputs no workspace correspondem ao novo comando. Isso é verdade aqui (mesma imagem, mesmo script, só muda como o volume é montado) e seria **falso** na opção descartada de rodar nativo no CI, onde o ambiente de execução muda de fato — ali o correto seria `dvc repro`.

### Decisões e achados de 2026-09-21 (ao implementar o 9.1)

**6. A URL do remote também fica fora do git, não só a credencial.** A decisão 2 previa `.dvc/config` versionado guardando a URL `azure://<container>/<prefixo>`. Na implementação a autora optou por tirar a URL também: ela carrega o nome da conta/container onde o dataset de treino — que depois do 9.0 contém desfechos reais — está armazenado, e isso é reconhecimento de graça para quem encontrar o repositório, sem nenhum ganho em troca. `.dvc/config` versionado passou a ter só `[core] remote = azure`; a URL vem de `.dvc/config.local` (ignorado pelo git pelo próprio `.dvc/.gitignore`) localmente e do secret `DVC_REMOTE_URL` no Actions, que escreve o `config.local` no runner (`dvc remote add --local`). Custo aceito: um clone novo não consegue `dvc pull` sem esse passo — e falha dizendo exatamente isso.

**7. Região Chile Central, não Brazil South.** Contraria a decisão 2, que previa Brazil South pelo mesmo argumento que sustentou `sa-east-1` no ADR-005. A escolha é da autora e está implementada, mas **não é neutra**: o dataset de treino e o `model.pkl` passam a atravessar fronteira, o que sob a LGPD é transferência internacional de dado derivado de dado de saúde (Art. 33), mesmo pseudonimizado. O que mitiga é o conteúdo (sem telefone/nome/CPF, `id_paciente` é hash sha256 de CPF), não a região. Registrado como emenda do Passo 9.1 no [ADR-005](adr/adr-005-integracoes-implicitas.md), com a base legal e as cláusulas do provedor como pendência do `docs/LGPD.md` (Passo 8) — e com o caminho de reversão escrito lá (criar container em Brazil South, repontar `DVC_REMOTE_URL`, `dvc push`; nada no código depende da região). Consequência para o Passo 12: o pitch não pode afirmar "os dados não saem do Brasil" sem qualificar — continua verdade para o banco, deixou de ser para o artefato de treino.

**8. "Nenhum re-treino efetivo" falha o workflow (código de saída 2).** Caso que o texto do passo não previa: `dvc repro` é content-addressed, então num mês sem desfecho novo registrado pela clínica o dataset tem o mesmo hash e nenhum stage reexecuta — não existe desafiante para comparar. Três saídas foram consideradas: sair 0 em silêncio (esconde um mês inteiro de operação parada), `dvc repro -f` (reproduz a mesma métrica e gera um `model.pkl` novo, trocando a `model_version` de `/health` e de `predicoes` sem mudança real — exatamente o que o 9.0 existiu para evitar) e falhar. A autora escolheu **falhar**: um mês sem desfecho registrado é problema de operação da clínica, e precisa aparecer. O gate detecta o caso comparando o `run_id` gravado antes e depois do `dvc repro`, o que também protege contra um risco menos óbvio: no runner o MLflow é efêmero, então um `run_id` restaurado do cache do DVC apontaria para uma run que nunca existiu ali.

**9. `champion_metrics.json` semeado agora, em vez de bootstrap no primeiro ciclo.** O arquivo entra versionado já preenchido com as métricas **medidas** da run que gerou o `model.pkl` vigente (lidas do tracking server, não estimadas). Assim o primeiro re-treino real já exercita a comparação em vez de promover sem comparar, e o repositório passa a declarar qual é o campeão em produção. O caminho de bootstrap continua implementado e coberto por teste, para o caso de o arquivo ser perdido.

**Achado colateral 1 — o "recall atual" documentado não era o do modelo vigente.** `architecture.md` §9 e o SLO §3.1 citavam `recall_1` 0.522 (vindo do [ADR-003](adr/adr-003-SMOTE-NC.md)) ao lado de `f1_1` 0.419 (vindo da run atual) — o par nunca foi de um mesmo modelo. O 0.522 foi medido antes de `features.temporais=true` e em outro threshold; a run que gerou o `model.pkl` em produção dá `recall_1` **0.429**, `f1_1` 0.419 e `roc_auc` 0.643 no threshold 0.6. Os dois documentos foram corrigidos, e daqui em diante o número vigente é o do `champion_metrics.json`, que só muda quando o gate promove. Importa além da precisão: era esse par de números que o Passo 12 usaria para discutir o gap de recall no pitch.

**Achado colateral 2 — o resumo do gate derrubava o gate no Windows.** Na primeira execução real, o comparativo (que usava emoji e `Δ`) levantou `UnicodeEncodeError` no console cp1252 e transformou um código de saída 2 num traceback com código 1 — ou seja, o passo que *registra* a decisão destruía a decisão. Corrigido nas duas pontas: o resumo não usa mais caractere fora do Latin-1, e `_publicar_resumo` degrada para `errors="replace"` em vez de propagar a exceção. O arquivo do `$GITHUB_STEP_SUMMARY` continua sendo escrito em UTF-8.

**Onde o gate *não* manda.** O texto do 9.1 descrevia o módulo fazendo `dvc push`, commit e PR. Na implementação isso ficou no `retrain.yml`: o `src/retrain_gate.py` decide e reescreve o campeão, e tudo que exige credencial (remote do DVC, token do git, Healthchecks) vive no workflow. O ganho é o gate ser testável sem Azure e sem GitHub — os 17 testes rodam com um MLflow sqlite em `tmp_path` e um campeão de fixture. O `dvc repro`, sim, continua dentro do módulo (é ele que sobe o `mlflow-server` pelos stages, como o passo previa).

---

## Passo 10 — CI/CD (GitHub Actions + sync para Hugging Face Hub)

> **Revisão do passo contra o repositório real e a documentação do GitHub Actions (2026-09-29)**: antes de implementar, o texto abaixo foi confrontado com o repositório, com a [documentação do GitHub Actions](https://docs.github.com/pt/actions), com os templates que o GitHub sugere para este repo e com o código da action de sync do Hugging Face. Seis premissas não se sustentam — a mais grave é que o sync, como descrito, enviaria o dataset de treino e a URL do remote do DVC para o Space. Os achados, a forma resultante dos workflows e a única decisão ainda em aberto (onde roda o job D-2) estão em **"Revisão de 2026-09-29"** no fim deste passo, depois do 10.1, e **substituem** o corpo abaixo onde divergem.

**Decisão de mecanismo de deploy** (define como o Passo 9 e o Passo 11 se conectam): usar a GitHub Action oficial de sync para o Hugging Face Hub (`huggingface/huggingface-sync-action`, conforme [docs do Hub sobre GitHub Actions](https://huggingface.co/docs/hub/repositories-github-actions) e a action do [GitHub Marketplace](https://github.com/marketplace/actions/sync-github-to-hugging-face-hub)) — a cada push em `main` (incluindo o merge do PR automático de re-treino do Passo 9), o GitHub Actions espelha o repo para o HF Space, que rebuilda sozinho. Isso substitui qualquer script manual de "git remote"/sync caseiro.

- `.github/workflows/ci.yml`: lint (formalizar `ruff` em `requirements.txt`/config, já há `.ruff_cache/` local), `pytest` (respeita `pytest.ini`, só testes rápidos por padrão), job opcional com serviço Postgres/Supabase local do GitHub Actions para os testes `integracao`. Dispara em PR (não faz deploy).
- `.github/workflows/deploy.yml`: usa `huggingface/huggingface-sync-action` para sincronizar `main` → o repo do HF Space sempre que houver push em `main` (token do Space como secret do GitHub, `HF_TOKEN`). Só roda **depois** do `ci.yml` passar (via `workflow_run` ou job dependente), para nunca sincronizar um estado quebrado.
- `.github/workflows/job_d2.yml` (scheduler diário do Passo 6, ADR-005 a): além de disparar o job, **pinga o Healthchecks.io ao concluir** — mesma proteção descrita no Passo 9. O GitHub Actions serve aqui melhor como *sonda* de observabilidade do que como fonte dela: o histórico de runs é a prova auditável do SLO §5, mas não diz nada sobre a aplicação em si (Passo 8.5).

**Verificação**: abrir PR de teste, confirmar que `ci.yml` dispara e passa; quebrar um teste de propósito, confirmar que o CI falha e `deploy.yml` não roda; mergear um PR válido em `main` e confirmar que `deploy.yml` sincroniza e o HF Space rebuilda automaticamente (sem passo manual).

### 10.1 — Migrations de banco no deploy

> Acrescentado em 2026-09-28, a partir de um incidente real — não estava previsto no texto original do passo.

**O buraco que este sub-passo fecha.** O texto acima descreve um deploy que sincroniza **código** e nada mais. Nada em `infra/deploy/dockerfile` aplica migrations — o diretório `supabase/` nem entra na imagem, e não há CLI do Supabase dentro dela. Resultado: **o deploy é automático e a migration é manual**. Basta um PR com migration ser mergeado sem ninguém rodar `supabase db push` para o código de produção rodar contra schema antigo.

Isso não é hipótese. Aconteceu em 2026-09-28, ao ligar `pacientes.nome_completo` ([ADR-007](adr/adr-007-nome-do-paciente.md)): a migration foi aplicada no Supabase local — onde `pytest -m integracao` roda —, os testes passaram, e a interface quebrou com `42703 column pacientes_1.nome_completo does not exist`, porque o `.env` da máquina aponta para o projeto **remoto**. Em desenvolvimento o custo foi um erro na tela de quem escreveu o código. Em produção o erro apareceria para a clínica, num Space cujo log é efêmero (ADR-006) e apaga a própria evidência.

**Dois agravantes específicos deste projeto:**

1. **Não existe separação dev/prod de banco.** Há um único projeto Supabase remoto: é o mesmo que o `.env` de desenvolvimento aponta e o mesmo que o Space vai apontar. Um `supabase db push` rodado da máquina de quem desenvolve **é** uma migration de produção, sem ensaio prévio possível.
2. **A ordem está invertida em relação ao padrão seguro.** Como o banco é compartilhado, a migration muda produção *antes* de o código que precisa dela ser deployado. Para coluna nullable isso é inofensivo (código antigo a ignora). Mas duas migrations já no repositório **não** são retrocompatíveis — `20260920010000_cadastro_pacientes.sql` faz `drop column idade` e `20260920020000_telefone_paciente.sql` faz `alter column telefone set not null`. Qualquer uma delas aplicada com o código antigo ainda no ar quebraria produção na hora.

**Decisão**: `deploy.yml` aplica as migrations **antes** do sync ao Space, e a ordem do workflow passa a ser `ci.yml` verde → `supabase db push` → sync. Aplicar antes, e não depois, porque o Space rebuilda sozinho assim que recebe o espelho: entre o sync e o container novo no ar não há janela em que dê para rodar a migration com segurança.

```yaml
# esboço, dentro do job de deploy, antes do passo de sync
# env no nivel do JOB, nao so' do `db push`: o `supabase link` tambem precisa
# do token, do ref e da senha (corrigido na revisao de 2026-09-29, achado 8)
env:
  SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}
  SUPABASE_DB_PASSWORD: ${{ secrets.SUPABASE_DB_PASSWORD }}
  SUPABASE_PROJECT_REF: ${{ secrets.SUPABASE_PROJECT_REF }}
steps:
  - uses: supabase/setup-cli@v1
    with:
      version: <versao fixa>   # nao `latest`: o CLI muda o comportamento do db push entre versoes
  - run: supabase link --project-ref "$SUPABASE_PROJECT_REF"
  - run: supabase db push
```

Três secrets novos: `SUPABASE_ACCESS_TOKEN` (token pessoal do CLI, **não** a chave do projeto), `SUPABASE_DB_PASSWORD` e `SUPABASE_PROJECT_REF`. O ref entra como secret, não como valor versionado, pela mesma razão que a URL do remote do DVC ficou fora de `.dvc/config` (Passo 9.1): identificador de infraestrutura em repositório é reconhecimento de graça. Documentar em `.env.example` como variável, nunca com valor.

**Regra de compatibilidade, que vale mais que o passo de workflow** — com um banco só e deploy automático, a disciplina de escrever migration tem de ser a de sistema em produção:

| Tipo de migration | Quando pode ir | Exemplo no repo |
|---|---|---|
| **Aditiva** (coluna nullable, tabela nova, índice) | Junto do deploy, antes do código. Código antigo ignora o que não conhece | `20260928000000_nome_paciente.sql`, `20260921000000_eventos_app.sql` |
| **Destrutiva ou restritiva** (`drop column`, `set not null`, `check` mais estreito) | **Nunca no mesmo deploy que o código que a exige.** Vai num deploy posterior, depois de o código que não depende mais da coluna já estar no ar | `drop column idade` e `set not null` em `telefone` — ambas passariam batido pelo workflow acima e quebrariam produção |

Ou seja: o passo de workflow resolve o **esquecimento**, não a **incompatibilidade**. A segunda é decisão de quem escreve a migration, e é por isso que está escrita aqui em vez de virar só YAML.

**Alternativas consideradas.** (a) Guarda no `ci.yml` que falha o PR quando há migration nova sem registro de aplicação: impede o merge silencioso, mas não aplica nada — continua exigindo passo manual, e o modo de falha vira "PR travado" em vez de "produção quebrada", o que é melhor mas não resolve. (b) Só documentar como checklist de release do Passo 11: é exatamente o que existe hoje, e foi o que falhou. Ficam as duas como reforço opcional, não como substituto.

**Verificação**: abrir PR com uma migration aditiva de brincadeira (ex. coluna `_teste_deploy` nullable), mergear e confirmar que (i) `supabase db push` roda antes do sync, (ii) a coluna existe no projeto remoto ao fim do workflow, (iii) o Space rebuilda depois disso e não antes. Depois, `supabase db push` de uma migration que a remove, para não deixar resíduo. Conferir também o caminho de falha: apontar `SUPABASE_DB_PASSWORD` para um valor errado e confirmar que o deploy **falha antes do sync**, em vez de sincronizar código contra schema não migrado.

### Revisão de 2026-09-29 (antes de implementar) — achados e decisões

**Como foi feita.** Quatro fontes, nesta ordem: (1) o repositório (`tests/`, `requirements/`, `infra/deploy/dockerfile`, `.github/workflows/retrain.yml`); (2) a [documentação do GitHub Actions](https://docs.github.com/pt/actions), em especial eventos, `GITHUB_TOKEN`, workflows reutilizáveis, simultaneidade, ambientes e segredos; (3) os templates que a página *Actions → New workflow* oferece a este repo — lidos direto de [`actions/starter-workflows`](https://github.com/actions/starter-workflows), de onde a página os tira, já que ela exige login; (4) o `action.yml` da action de sync e o código do `huggingface_hub` que ela chama. Nada aqui está implementado ainda.

**Estado do repositório no GitHub em 2026-09-29** — o ponto de partida real, não o que o texto do passo presume:

- Repositório **público**. Três consequências que atravessam os achados abaixo: log de Actions é visível a qualquer pessoa; PR vindo de fork (existe o `upstream`) e PR do Dependabot **não recebem secrets**; CodeQL e dependency review são gratuitos.
- `retrain.yml` já está em `main` e **ativo**, mas o repositório tem **zero secrets**, `main` sem proteção e nenhum *environment*. O cron dispara em **2026-10-01 06:00 UTC** e vai falhar no `dvc remote add` com `DVC_REMOTE_URL` vazio — sem Healthchecks configurado, o único aviso é o e-mail de falha do GitHub. **Ação antes dessa data**: configurar os secrets do Passo 9.1 ou `gh workflow disable retrain.yml` até o Passo 11.

#### Achados críticos (mudam o desenho)

**1. O sync, como descrito, enviaria o dataset de treino e a URL do remote para o Space.** A action foi renomeada — `huggingface/huggingface-sync-action` virou **`huggingface/hub-sync`** (v0.3.0) — e não faz `git push`: roda `hf upload` do diretório do runner, por HTTP. Três consequências:
- **O `git add -f` "no branch espelhado" da decisão 2 do Passo 9 não existe** — não há git no caminho (emendado lá).
- **O `.gitignore` não protege nada.** A action só exclui `.git*` da raiz e `.git/`/`.github/` aninhados. O Hub aplicaria um `.gitignore` *da raiz* presente no commit ou já hospedado no Space — e a action exclui justamente esse arquivo do commit. E mesmo que ele valesse, os arquivos sensíveis estão em `.gitignore` **aninhados** (`.dvc/.gitignore`, `data/.gitignore`), que o Hub não lê; o da raiz, se valesse, derrubaria o `model.pkl` (`*.pkl`) e o build quebraria no `COPY`.
- **O que subiria**: se o `deploy.yml` fizer `dvc remote add --local` e `dvc pull` no checkout, sobem `.dvc/config.local` (a URL do remote — contra a decisão 6 do Passo 9.1), `.dvc/cache/` e, com `dvc pull` sem alvo, `data/consultas-treino.csv` — desfechos reais pseudonimizados — para um Space que o Passo 11 prevê **público** e hospedado fora do Brasil. Seria uma porta de saída nova, não prevista no [`LGPD.md`](LGPD.md) §2.1. Além disso, `docs/`, `tests/`, `supabase/` e `scripts/` iriam junto sem nenhum papel no runtime.

**Decisão**: o `deploy.yml` monta um **diretório de staging com lista fechada** e passa só ele à action (`subdirectory:`). O `dvc pull` é sempre `dvc pull data/model.pkl`, nunca sem alvo. Lista fechada, e não lista de exclusão, pelo mesmo motivo da allowlist de `eventos_app` (Passo 8.5): arquivo novo no repositório não vai para produção sem alguém decidir que vai.

**2. O Space buildaria a imagem errada, ou nenhuma.** O SDK Docker do Space procura `Dockerfile` na raiz e lê o front-matter do `README.md` (`sdk: docker`, `app_port: 7860`). Na raiz do repositório está `dockerfile`, a imagem de **treino**; a de deploy é `infra/deploy/dockerfile`, e o README do projeto não tem front-matter. O staging do achado 1 resolve os dois: copia `infra/deploy/dockerfile` como `Dockerfile` e gera um `README.md` próprio do Space — sem acrescentar ao README do GitHub um bloco YAML que ele renderizaria como tabela. Conteúdo do staging, derivado dos `COPY` da imagem: `Dockerfile`, `README.md`, `requirements/`, `src/`, `params.yaml`, `infra/deploy/entrypoint.sh`, `data/model.pkl`.

**3. Os gatilhos de CI e deploy se contradizem.** O `ci.yml` "dispara em PR"; o `deploy.yml` "só roda depois do `ci.yml` passar". Um push em `main` não dispara o `ci.yml`, então um `workflow_run` esperando por ele nunca acordaria. E `workflow_run` traz armadilhas próprias: dispara também quando o CI falha (exige `if: conclusion == 'success'`), faz checkout do HEAD de `main` e não do SHA que foi testado (duas fusões seguidas deployam código não testado), e só existe a partir do arquivo no branch padrão.

**Decisão**: `ci.yml` declara `workflow_call` e o `deploy.yml` o chama como primeiro job, com o deploy em `needs:`. Mesmo SHA testado e deployado, falha do CI bloqueia por construção, e o CI não roda duas vezes no push. Assim a ordem do 10.1 fica explícita num único workflow: CI → `supabase db push` → sync.

**4. O `pytest` padrão falha no CI sem o `model.pkl`.** `test_api.py`, `test_inference.py`, `test_explain.py` e `test_ui_logic.py` carregam o `data/model.pkl` real e **não** são `integracao`. O modelo não está no git, então o CI precisa de `dvc pull data/model.pkl` — e, com ele, de credencial do Azure — antes do `pytest`. O texto do passo não previa nada disso. Dois desdobramentos:
- **Menor privilégio no Azure**: a `AZURE_STORAGE_CONNECTION_STRING` do Passo 9.1 carrega a *account key*, que dá escrita na conta inteira, e o CI roda código de PR. CI, deploy e job D-2 recebem uma **SAS só de leitura**; a credencial de escrita fica só no `retrain.yml`, o único que faz `dvc push`.
- **PR sem secrets** (fork, Dependabot): o `dvc pull` falha. Aceito conscientemente — o projeto tem uma autora e o Dependabot pode ter secrets próprios (*Dependabot secrets*). A alternativa (pular esses testes sem modelo) esconderia exatamente o teste de paridade UI × API.

**5. O `job_d2.yml` não diz onde o job roda — decisão pendente da autora.** O [ADR-005](adr/adr-005-integracoes-implicitas.md) (a) e o `architecture.md` §3.2.2 dizem que o workflow "chama o endpoint/job" do Space. Isso não se sustenta: não existe endpoint de job (a API tem só `/health` e `/predict`), a API nem terá porta pública (porta única do Space, Passo 11), o Space hiberna — o motivo de o ADR-005 ter tirado o agendador de dentro dele — e um endpoint público que dispara SMS pago exigiria autenticação própria.

- **(a) Rodar `python src/jobs/inferencia_diaria.py` no runner — recomendada.** O job já é em processo (ADR-005 b), então roda igual fora do Space. Consequências a aceitar:
  - Secrets do Supabase e da Infobip passam a existir também no Actions, além do Space.
  - **Log público**: o log do Actions deste repositório é visível a qualquer pessoa. O Passo 8 já mitiga — filtro de redação em todo handler do processo, resumo como uma linha JSON, `ErroEnvioInfobip` sem o corpo da resposta —, mas aqui o filtro deixa de ser defesa em profundidade e vira a **última barreira**. Vale registrar como risco residual no `LGPD.md` §9.
  - Dado de paciente (data de nascimento, sexo, especialidade, telefone para o envio) processado num runner fora do Brasil. É a mesma classe de transferência que o Space já implica, mas é um operador a mais (GitHub) — emenda no ADR-005 e no `LGPD.md` §2.1.
  - **Modelo**: baixar o `model.pkl` do próprio Space (`hf download`, token de leitura) em vez de `dvc pull`. Garante que o job usa exatamente o modelo em produção, não o de `main`, que pode estar à frente de um deploy que falhou. E dispensa credencial do Azure no job.
  - **`concurrency` obrigatório**: `buscar_agendamentos_d2_pendentes` só é idempotente em sequência (filtra quem já tem predição). Cron e `workflow_dispatch` simultâneos veriam a mesma fila e mandariam SMS em dobro.
- **(b) Endpoint de disparo no Space** — descartável pelos motivos acima, a menos que surja outro consumidor para ele.

Decidida a opção, emendar o ADR-005 (a) e o `architecture.md` §3.2.2, que hoje descrevem a chamada ao Space.

**6. O PR de promoção do re-treino não teria CI — e travaria se `main` exigir o check.** O `retrain.yml` já registra a ressalva (PR aberto com `GITHUB_TOKEN` não dispara outros workflows), mas a consequência vai além de "o CI não roda": com proteção de branch exigindo o `ci.yml`, o PR fica pendente para sempre. A documentação abre uma exceção à regra do `GITHUB_TOKEN` para `workflow_dispatch`. **Decisão**: o `ci.yml` também aceita `workflow_dispatch`, e o `retrain.yml` roda `gh workflow run ci.yml --ref "$branch"` depois de abrir o PR (permissão `actions: write`). O check roda no mesmo commit e satisfaz a proteção, sem PAT.

#### Achados médios

7. **O gate pode ser contornado pelo deploy.** Qualquer merge que altere o `dvc.lock` — um `dvc repro` manual commitado — deploya um modelo que nunca passou pelo gate do Passo 9.1. **Guarda no `deploy.yml`**, depois do `dvc pull` e antes do sync: os 12 primeiros hex do sha256 do `model.pkl` têm de bater com `champion_metrics.json` → `model_version`; se não baterem, o deploy falha. É a mesma regra do 9.1 ("só o campeão vai para produção") aplicada no único ponto por onde produção muda.
8. **O esboço do 10.1 não rodaria.** Os secrets estavam só no step do `db push`, e o `supabase link` do step anterior também precisa do token, do ref e da senha. Corrigido no esboço acima: `env` no nível do job e versão do CLI fixa em vez de `latest`.
9. **Um "serviço Postgres" não serve para os testes `integracao`.** Eles leem `supabase status -o json` e falam com o PostgREST via `supabase-py` — precisam do stack do Supabase, não de um Postgres cru. O job opcional usa `supabase/setup-cli` + `supabase start` (Docker existe no `ubuntu-latest`), e também precisa do `model.pkl` (`test_job_inferencia.py`, `test_observabilidade.py`). Roda em push para `main` e por `workflow_dispatch`, sem bloquear PR: é lento, e o CI rápido já cobre a lógica.
10. **Texto desatualizado no corpo do passo.** "Formalizar `ruff` em `requirements.txt`" já está feito (`ruff.toml` + `requirements/dev.txt`), e não existe `requirements.txt`. O `deploy.yml` não lista `workflow_dispatch`, mas o Passo 11 depende dele para o primeiro deploy.
11. **Branches de PR.** O trabalho acontece em `dev` e em branches de feature; o `ci.yml` roda em PR para `dev` **e** para `main`, não só `main`.

#### Achados baixos

12. **Cron no minuto zero.** A documentação avisa que eventos agendados atrasam — e podem ser descartados — no início de cada hora. O `retrain.yml` usa `"0 6 1 * *"`; ele e o `job_d2.yml` passam a usar um minuto "quebrado" (ex. `17`). Workflow agendado só roda a partir do branch padrão.
13. **Versões das actions.** `actions/checkout@v4`, `setup-python@v5` e `upload-artifact@v4` rodam em Node 20, que o GitHub está descontinuando; os templates atuais já usam `checkout@v6`/`v7`. Um `.github/dependabot.yml` (ecossistemas `github-actions` e `pip`) mantém isso em dia sem esforço.
14. **Cadeia de confiança do deploy.** O `hub-sync` recebe o `HF_TOKEN` e instala o `hf` CLI na versão mais recente: fixar a action por SHA e o CLI por `hf_version`. `HF_TOKEN` *fine-grained*, com escrita só neste Space, guardado num *environment* `production`, que ainda dá o histórico de deploys na aba do GitHub — evidência útil ao pitch.
15. **Rebuild por mudança só de documentação.** Cada sync rebuilda o Space (cold start, SLO §2). `paths-ignore` para `docs/**` — sem ignorar `dvc.lock` nem `data/champion_metrics.json`, que são justamente o merge do PR de promoção.
16. **`HEALTHCHECKS_JOB_D2_URL`** falta no `.env.example`, que hoje só documenta o do re-treino.

#### Templates sugeridos pelo GitHub, avaliados

| Template | Uso | Por quê |
|---|---|---|
| Python application | ✅ base do `ci.yml` | Adaptado: `ruff` no lugar de `flake8`, `requirements/dev.txt`, `setup-python` atual com cache, `dvc pull data/model.pkl` |
| Dependency review | ✅ novo, em PR | Gratuito em repo público; bloqueia PR que traz dependência vulnerável |
| CodeQL | ✅ via *default setup* (Settings), sem arquivo | Gratuito em repo público; Python não precisa de build |
| Bandit | ❌ | Coberto pelas regras `S` (flake8-bandit) do `ruff`, sem workflow a mais |
| Python package (matriz), Pylint, Publish Python Package | ❌ | As imagens fixam 3.10; `ruff` já cobre o lint; o projeto não é biblioteca |
| Docker image / Docker publish | ❌ | Quem builda é o Space; não há registry |
| Stale, Greetings, Labeler, deploys Azure/AWS | ❌ | Projeto de uma autora; o deploy é no Hugging Face |

#### Forma resultante

| Arquivo | Gatilhos | O que faz |
|---|---|---|
| `ci.yml` | PR para `dev`/`main`, `workflow_dispatch`, `workflow_call` | `ruff check src tests scripts` → `dvc pull data/model.pkl` (SAS de leitura) → `pytest`. Job `integracao` separado (achado 9) |
| `deploy.yml` | push em `main` (com `paths-ignore`), `workflow_dispatch` | `ci` (reutilizado) → `supabase db push` (10.1) → `dvc pull data/model.pkl` → guarda do campeão → staging → `hub-sync`. `environment: production`, `concurrency` sem cancelamento |
| `job_d2.yml` | cron diário, `workflow_dispatch` | Conforme a decisão do achado 5; `concurrency` obrigatório; ping do Healthchecks em sucesso e `/fail` em falha |
| `dependency-review.yml` | PR | Template do GitHub, sem adaptação |
| `retrain.yml` (existente) | — | Ajustes: `gh workflow run ci.yml` no PR de promoção (achado 6), minuto do cron (12), versões (13) |
| `.github/dependabot.yml` | — | `github-actions` + `pip` |

```yaml
# esboço do núcleo do deploy.yml (achados 1-3 e 7)
jobs:
  ci:
    uses: ./.github/workflows/ci.yml
    secrets: inherit
  deploy:
    needs: ci
    environment: production
    concurrency: { group: deploy-space, cancel-in-progress: false }
    steps:
      - uses: actions/checkout@<sha>
      # ... supabase db push (10.1) ...
      - run: dvc remote add --local azure "$DVC_REMOTE_URL" && dvc pull data/model.pkl
      - name: Só o campeão vai para produção
        run: |
          esperado=$(jq -r .model_version data/champion_metrics.json)
          obtido=$(sha256sum data/model.pkl | cut -c1-12)
          test "$esperado" = "$obtido" || { echo "::error::model.pkl ($obtido) não é o campeão ($esperado)"; exit 1; }
      - name: Staging com lista fechada
        run: |
          mkdir -p build/space/data build/space/infra/deploy
          cp infra/deploy/dockerfile build/space/Dockerfile
          cp -r requirements src params.yaml build/space/
          cp infra/deploy/entrypoint.sh build/space/infra/deploy/
          cp data/model.pkl build/space/data/
          # README.md do Space (front-matter: sdk: docker, app_port: 7860) gerado aqui
      - uses: huggingface/hub-sync@<sha>
        with:
          github_repo_id: ${{ github.repository }}
          huggingface_repo_id: ${{ vars.HF_SPACE_ID }}
          hf_token: ${{ secrets.HF_TOKEN }}
          space_sdk: docker
          subdirectory: build/space
          hf_version: <versao fixa>
```

**Verificação acrescida** (soma-se à do corpo do passo e à do 10.1):
- Depois do primeiro sync, listar os arquivos do Space e confirmar que são **exatamente** os do staging — nenhum `.dvc/`, nenhum `data/*.csv`, nenhum `docs/`. Controle negativo: criar um arquivo fora da lista e confirmar que ele não sobe.
- Commitar um `dvc.lock` cujo `model.pkl` difere do campeão e confirmar que o deploy **falha na guarda, antes do sync**.
- Rodar o `retrain.yml` por `workflow_dispatch` e confirmar que o PR de promoção recebe o check do `ci.yml`.
- Disparar o `job_d2.yml` duas vezes seguidas e confirmar que a segunda espera a primeira e nenhum paciente recebe SMS em dobro.
- Quebrar um teste de propósito e confirmar que o job `deploy` aparece como **pulado** — não "não disparado", já que agora é o mesmo workflow.

**Pendências** (não bloqueiam o início do passo):
- ~~Decisão da autora sobre o achado 5 e, com ela, as emendas no ADR-005 (a), no `architecture.md` §3.2.2 e no `LGPD.md` §2.1/§9.~~ Decidido na 2ª revisão (opção a, job no runner); emendas feitas em 2026-09-30 (ADR-005, `architecture.md` §3.2.2/§6, `LGPD.md` §4/§9.1).
- ~~`architecture.md` §6, o Passo 9.1 ("Fecha o loop até produção") e o Passo 11 ainda citam `huggingface/huggingface-sync-action` pelo nome antigo — atualizar junto da implementação.~~ Feito em 2026-09-30.
- Secrets do `retrain.yml` ou desativação dele **antes de 2026-10-01** (ver o estado do repositório acima).

### Revisão de 2026-09-29 (2ª) — inferência só em batch e gates por camada

**Premissa da autora**: a solução é de **inferência em batch**. Não faz sentido inferir em tempo real: o produto só precisa (a) do job diário, (b) do registro do desfecho real pela atendente no dia da consulta e (c) do re-treino mensal com os dados coletados no banco. Por isso, a pipeline de pré-processamento precisa transformar o dado cru do banco em feature com a tipagem correta, e o CI/CD precisa de gates em quatro camadas: script, dados, features e modelo.

**Confronto com o repositório — o que apareceu:**

1. **Bug de fuso (crítico, corrigido no 10.2).** O Postgres devolve `timestamptz` em UTC. A fila do dia já convertia o valor ([`test_db.py`](../tests/test_db.py), regressão da fila), mas o job D-2 e o export não. Medido com o `model.pkl` real, para uma consulta das 18h em SP: o modelo via `horario=21`, que o treino nunca viu, e a probabilidade caía de 0,482 para 0,241. O export gravava a hora UTC no dataset de treino, e o SMS dizia "21:00" ao paciente. O `test_job_inferencia.py` não pegava o bug porque só comparava a ordem entre dois pacientes, e os dois deslocavam juntos.
2. **Janela de um dia só.** O job olhava exatamente D+2. Se o cron falhasse num dia, aqueles pacientes nunca eram preditos. Um agendamento feito com menos de dois dias de antecedência nunca entrava em janela nenhuma.
3. **Uma linha ruim abortava a fila.** Só `KeyError`/`EspecialidadeDesconhecidaError` eram capturadas.
4. **Nenhuma validação entre o dado cru e a feature.** `construir_features` prediz sem erro sobre idade −5 ou 150, sexo `'X'` (vira NaN), distância 450 km (o modelo trata como 50), 400 dias de antecedência (o modelo trata como 90) e data nula. As proteções existentes estão espalhadas: Pydantic só na API, checks `>= 0` no banco, widgets da UI.
5. **Rótulo.** Dava para marcar no-show em consulta futura. O SMS é uma intervenção que muda o desfecho, e nada registrava quem o recebeu — risco de feedback loop no re-treino.
6. **O gate do modelo promoveria um modelo que marca a fila inteira.** Com 21 positivos em 76 linhas, marcar todos dá `recall_1` 1,0 e `f1_1` 0,433, acima dos 0,419 do campeão, e um deslocamento das probabilidades que preserva a ordem mantém o `roc_auc`. Há também o efeito catraca: cada promoção pode regredir até a tolerância em relação ao campeão da vez. E o threshold escapa do gate: o job lê `params.yaml`, e a guarda do achado 7 cobre só o sha do modelo.
7. **O plano afirma que o Bandit está coberto pelas regras `S` do ruff**, mas o `ruff.toml` não as seleciona.

**Decisões da autora:**

| # | Tema | Decisão |
|---|---|---|
| 1 | API `/predict` | **Mantida**, para cumprir o item 1 do BRIEFING. A interpretação registrada aqui: o código, os testes e o `infra/api/` continuam no repositório, mas a API sai do caminho crítico e dos SLOs de produção, que passam a medir o batch. *Se a API também deve continuar publicada no Space, isso reabre o dilema da porta única do Passo 11.* |
| 2 | Horário do job D-2 | Job único às **08h de SP**. O custo do Actions não depende da hora, e na madrugada o SMS chegaria às 3h. Com isso, fica adotada a opção (a) do achado 5: o job roda no runner. O re-treino continua às 03:00 de SP. |
| 3 | Paciente não predito | **Recebe o lembrete mesmo assim, como exceção** (`status_envio = enviado_sem_predicao`). Janela do job: **de amanhã até D+2**, sem predição e sem lembrete. |
| 4 | Números | Fora do domínio do treino: **só marcar, nunca rejeitar** (distância > 50 km, dias > 90 etc.). **180 dias** é a antecedência máxima de agendamento. **Taxa de disparo sem teto**: só notificação. **Piso absoluto de `roc_auc` = 0,60** no gate. |
| 5 | Escopo | Tudo **dentro do Passo 10**, fatiado nos sub-passos abaixo. |
| 6 | Rótulo | Desfecho **só para consulta de hoje ou anterior**. Consulta sem desfecho continua fora do export. Contra o feedback loop, **`agendamentos.lembrete_enviado`** é gravado em todo envio e exportado. |

#### 10.2 — Correções do batch D-2 e do rótulo — **implementado em 2026-09-29**

- `config_projeto.para_horario_da_clinica` converte qualquer data/hora para o fuso da clínica; valor sem fuso é tratado como já local. É aplicado no job (payload e texto do SMS), no export e em `features.extrair_features_temporais` — nesta última, como defesa para qualquer timestamp com fuso que chegue ao modelo, inclusive pela API.
- `buscar_agendamentos_d2_pendentes`: janela [amanhã, D+2], `status = agendado`, sem predição e `lembrete_enviado = false`. O nome da função foi mantido para não espalhar a mudança pelos chamadores.
- Job: **quarentena por linha**, que captura qualquer exceção ao predizer, registra o evento e o log só com a classe da exceção, e envia o lembrete como `enviado_sem_predicao`. Se o próprio agendamento estiver malformado, o SMS cai numa mensagem genérica. Falha de envio não marca `lembrete_enviado`, para a execução do dia seguinte tentar de novo. Contador `lembretes_sem_predicao` no resultado, no log e na allowlist de `eventos_app`.
- Migration **aditiva** `20260929000000_lembrete_enviado.sql` (`boolean not null default false`). Pode ir no mesmo deploy do código, aplicada antes do sync (regra do 10.1). O export ganhou a coluna `lembrete_enviado`, que **não é feature** — é o dado que permite tratar o efeito do SMS depois; na semente fica vazia (desconhecido).
- Desfecho: `logic.pode_registrar_desfecho` (data no fuso da clínica). A UI troca os botões por um aviso em datas futuras, e `repositories.atualizar_status_agendamento` filtra por data no próprio `UPDATE` e levanta `DesfechoForaDePrazo` quando nenhuma linha é afetada.
- Regra de 180 dias: `agenda_clinica.PRAZO_MAXIMO_AGENDAMENTO_DIAS`, com `min_value`/`max_value` no `date_input` do cadastro e validação em `cadastrar_paciente_e_agendamento`, que passa a recusar consulta no passado. **O check no banco fica para um deploy separado** (migration restritiva, regra do 10.1), e vai no 10.3.
- `dvc.lock` reconciliado com `dvc commit`, não com `dvc repro`, pelo mesmo raciocínio da decisão 5 do Passo 9: `features.py`/`config_projeto.py` são deps do `preprocess`, mas o dataset versionado não tem fuso, então as saídas não mudam — conferido comparando `train_raw.pkl`/`test.pkl`/`mapa_especialidade.json` gerados de novo com os versionados. `model.pkl` e `model_version` intactos.

**Verificado**: `ruff` limpo; **275 testes** rápidos (eram 255); 19 de integração contra o Supabase local, com a migration aplicada por `supabase migration up`. Os testes novos são:
- `tests/test_skew_features.py`: a mesma linha no formato do PostgREST pelos caminhos do job e do export → `preprocessar` tem de dar o mesmo vetor, com `horario=18`. Controle negativo conferido: sem a conversão, 4 dos 5 testes selecionados falham.
- `tests/test_job_d2_unitario.py`: quarentena sem abortar a fila, lembrete sem predição, marcação de `lembrete_enviado`, falha de envio sem marcação.
- Testes de prazo em `test_ui_logic.py`.
- `test_db.py`: janela [amanhã, D+2], desfecho futuro recusado pelo banco.
- `test_job_inferencia.py`: passa a comparar a probabilidade **gravada** com a do payload local, não só a ordem.

**Pendências operacionais**: `supabase db push` da migration nova no projeto remoto **antes** de subir o código. As predições já gravadas no remoto foram feitas com o horário deslocado.

#### 10.3 — Contrato de features e gate de dados — **implementado em 2026-09-30**

- Módulo único de contrato (ex. `src/contrato_features.py`, pandera ou pydantic) aplicado no export, no `preprocess` e no job, com coerção explícita de tipo. Duas faixas por campo:

  | Campo | Regra de negócio (fora → quarentena) | Domínio do treino (fora → prediz e marca `fora_do_dominio`) |
  |---|---|---|
  | idade | inteiro, 0–120 | 0–85 |
  | sexo / especialidade | {F, M} / mapa do modelo | — |
  | distancia_km | ≥ 0 | até 50 |
  | dias | inteiro, 0–180 | 1–90 |
  | historico_noshow | inteiro ≥ 0 | até 10 |
  | data_hora_agendada | com fuso, dentro da grade da clínica | seg–sáb, 8h–18h |

  A flag `fora_do_dominio` é gravada em `predicoes` (migration aditiva) e aparece na fila.
- Check no banco `dias_entre_agendamento_consulta <= 180`, em deploy **posterior** ao do código do 10.2.
- Stage `validate_data` no `dvc.yaml`, antes do `preprocess`, para que o `dvc repro` do re-treino falhe antes de treinar. Checa:
  - schema: colunas, dtypes, nulos e `id_consulta` único;
  - domínio: as regras do contrato;
  - distribuição: taxa de positivos entre 10% e 50%, mínimo de positivos no fold de teste, especialidade nova, horários dentro da grade;
  - completude de rótulo: consultas passadas ainda `agendado`.
  
  PSI por feature (produção × semente) entra só como alerta no resumo do run.
- Export com o `historico_noshow` **gravado no cadastro** (o que o modelo viu em produção), em vez de recalculado.
- Lista de especialidades do cadastro vinda de configuração, com teste de que ela está contida no mapa do modelo.
- Na carga do modelo, conferir nomes, ordem e categóricas contra `booster_.feature_name()`.

#### 10.4 — Gates do modelo — **implementado em 2026-09-30**

- **Piso absoluto** de `roc_auc` = 0,60 (`params.yaml`, `gate.piso`), além da comparação relativa.
- **Taxa de disparo projetada** no fold de teste: reportada no resumo e no PR de promoção, com notificação (sem bloqueio) acima de 30%, que é o "cortar 70%" do BRIEFING. O campeão atual marca 22 de 76 (29%).
- **Suíte de sanidade e casos limítrofes** rodada pelo `retrain_gate.py` sobre o **desafiante**, antes de reescrever o campeão (bloqueia a promoção), e pelo CI contra o `model.pkl` de `main`:
  - saída em [0, 1], sem NaN, determinística e não constante;
  - aditividade do SHAP;
  - a política do contrato para idade negativa/150/float, antecedência acima de 180 dias, distância acima de 450 km e NaN.
  
  Expectativas direcionais (mais `historico_noshow` não reduz o risco) entram só como aviso.
- **Threshold amarrado ao campeão**: o job exige `decision.threshold` igual a `champion_metrics.decision_threshold`.

#### 10.5 — Workflows — **implementado em 2026-09-30**

A forma resultante da 1ª revisão continua valendo, com estes acréscimos:
- **`ci.yml`**:
  - regras `S` no `ruff`;
  - `mypy src scripts` (10.6), logo depois do `ruff` e antes do `dvc pull`/`pytest`;
  - testes de skew e do validador de dados;
  - suíte do modelo;
  - `docker build` da imagem de deploy com smoke (`/_stcore/health`);
  - integração obrigatória em PR que toque `src/jobs`, `src/db`, `src/export_treino.py` ou `supabase/`.
- **`job_d2.yml`**:
  - cron `17 11 * * *` (08h17 de SP, minuto "quebrado" do achado 12);
  - `dvc pull data/model.pkl` com SAS só de leitura;
  - pré-checagem: sha do modelo e threshold batem com o campeão, e o banco está alcançável;
  - pós-checagem: predições = pendentes − quarentena, e aviso se a taxa de disparo ou de quarentena sair da faixa — uma quarentena de 100% indica defeito sistêmico e mandaria SMS à fila inteira;
  - `/fail` no Healthchecks.
- **`retrain.yml`**: `validate_data` e o gate completo passam a bloquear antes do PR.
- **SLO §1/§2**: reescritos como SLOs do batch — fila [amanhã, D+2] 100% processada até as 09h, uma execução por dia. A latência de `/predict` sai do compromisso de produção (decisão 1).
- Emendar o ADR-005 (a) e o `architecture.md` §3.2.2/§6 com a execução no runner.

#### 10.6 — Type-check estático (mypy) — **implementado em 2026-09-30**

> Acrescentado em 2026-09-30, a pedido da autora. A camada "script" dos quatro gates da 2ª revisão só tinha o `ruff`, que olha cada arquivo isoladamente e não verifica tipos atravessando a fronteira entre módulos.

**O que o type-check pega neste repositório — e o que não pega.** Medido contra o código atual: das ~203 funções de `src/` e `scripts/`, ~130 já declaram tipo de retorno. Sem anotação nenhuma: `train.py`, `tune.py`, `preprocess.py`, `validate.py`, `ui/app.py`, `api/main.py` e `scripts/gerar_timestamp_sintetico.py` (`api/schemas.py` aparece com zero `def` porque é só modelo Pydantic — é o arquivo mais bem tipado do repo). O código já usa genéricos nativos (`list[time]`, `dict`) e tem um único `from typing import`, então não há dívida de sintaxe a pagar antes: o mypy entra sobre um código que já está no estilo do `target-version = "py310"`.

- **Não pega o bug crítico do 10.2.** Para o mypy, `datetime` naive e `datetime` com fuso são o mesmo tipo — `para_horario_da_clinica(valor) -> datetime` continuaria válida recebendo qualquer um dos dois. A proteção contra deslocamento de fuso permanece sendo o teste de regressão e a convenção de `config_projeto` registrada no `architecture.md` §8. **O type-check não deve ser anunciado como rede para fuso.**
- **Pega a fronteira escalar entre banco/configuração e modelo**, que hoje não tem guarda nenhuma: `calcular_idade(data_nascimento: date, referencia: date)` chamada com a string que o Supabase devolve no JSON, retorno `None` de repositório usado sem checagem, chave trocada em desempacotamento. É exatamente o tipo de erro que só aparece em runtime, no job diário, sobre um paciente real.
- **Não cobre DataFrame, e não deve tentar.** `pandas` não traz `py.typed` (o `pandas-stubs` é pacote separado e ruidoso) e `sklearn`, `shap` e `joblib` não têm stubs. Além disso `df: pd.DataFrame` não diz nada sobre colunas, dtypes ou domínio — que é precisamente o que o **10.3** garante em runtime. Divisão de trabalho resultante: **mypy na fronteira escalar, contrato do 10.3 na fronteira tabular**, sem sobreposição. Consequência prática: **não** adotar `pandas-stubs` neste passo.

**Decisão de ferramenta**: `mypy`, pinado em `requirements/dev.txt` ao lado do `ruff` (versão exata resolvida na implementação). Não `pyright`/`basedpyright`: exigem Node no runner e não entram em `requirements/dev.txt`, o que quebraria a regra do §8 de que o CI roda o mesmo comando que a máquina local, sem flags extras.

**Configuração**: arquivo `mypy.ini` dedicado, pelo mesmo motivo já documentado no `ruff.toml` — este repo não é um pacote Python instalável, e um `pyproject.toml` na raiz confundiria pip/build tools.

- `python_version = 3.10`, alinhado ao `target-version` do `ruff`.
- `mypy_path = src`, `explicit_package_bases = true`, `namespace_packages = true` — **obrigatórios, não estilo**: não existe um único `__init__.py` em `src/`, e `db`, `api`, `ui`, `jobs` e `messaging` são pacotes implícitos (PEP 420). Sem essas três opções o mypy deriva o nome do módulo do caminho do arquivo e `src/db/client.py` e `src/messaging/client.py` colidem no mesmo módulo `client` — o gate morre num erro de duplicata, antes de verificar tipo algum. É o mesmo problema que obrigou `src = ["src", "tests", "scripts"]` no `ruff.toml`, e a implementação deve confirmá-lo executando o mypy (ele ainda não está instalado; a colisão é dedução do layout, não medição).
- `ignore_missing_imports` **por módulo** (`sklearn.*`, `shap.*`, `joblib`, `imblearn.*`), nunca global: assim uma dependência nova sem stub aparece como erro em vez de passar calada.
- `types-PyYAML` em `requirements/dev.txt` (pinado): `yaml.safe_load` é a porta de entrada do `params.yaml` em `config_projeto.carregar_params`, o ponto mais central do repo a ficar sem tipo.
- Sem ação para `numpy`, `lightgbm`, `streamlit`, `mlflow`, `supabase`, `fastapi` e `pydantic` — todos já distribuem `py.typed`.

**Adoção gradual, em dois níveis** (ligar `disallow_untyped_defs` no repo inteiro de uma vez travaria o passo):

| Nível | Módulos | Por quê |
|---|---|---|
| `disallow_untyped_defs = true` | `contrato_features.py` (nasce assim, 10.3), `config_projeto.py`, `agenda_clinica.py`, `db/client.py`, `db/repositories.py`, `export_treino.py`, `jobs/inferencia_diaria.py`, `messaging/client.py`, `observabilidade.py`, `api/schemas.py`, `retrain_gate.py` | Fronteira escalar: datas, identificadores, telefone, threshold, retorno de repositório. É onde o tipo carrega invariante de negócio e onde o erro chega ao paciente |
| Baseline (só o que o mypy achar no código já anotado) | `train.py`, `tune.py`, `preprocess.py`, `validate.py`, `features.py`, `inference.py`, `explain.py`, `ui/`, `scripts/` | Assinaturas essencialmente `DataFrame -> DataFrame`, onde o mypy não agrega e a garantia vem do 10.3 |

`tests/` fica fora do comando por ora: fixtures sem anotação e dublês trocados por `monkeypatch` geram erro em volume sem proteger invariante de produção. Reavaliar depois de o `src/` estar estável no nível 1.

**Ordem obrigatória de implementação**: zerar antes de ligar o gate. O PR que adiciona o step ao `ci.yml` é o mesmo que traz `mypy src scripts` a zero erro no nível escolhido — senão o `ci.yml` nasce vermelho e bloqueia o Passo 10 inteiro, inclusive o `deploy.yml`, que depende dele. Se o volume no nível 1 for grande, **encurtar a lista fechada**, nunca afrouxar com `--ignore-errors` global.

**Verificação**:
- `mypy src scripts` passa local e no CI com o comando idêntico, sem flags extras (regra do §8).
- Introduzir de propósito `calcular_idade("1990-01-01", hoje)` e confirmar que o CI falha nessa linha.
- Confirmar que a colisão `db/client.py` × `messaging/client.py` **não** aparece — prova de que `mypy_path`/`explicit_package_bases` estão corretos.
- Remover a anotação de retorno de uma função da lista fechada e confirmar que o `disallow_untyped_defs` reprova.
- Controle negativo: um `df` sem anotação em `train.py` continua passando — o baseline é intencional, não esquecimento.
- Confirmar que o step roda **antes** do `dvc pull`, isto é, que uma quebra de tipo falha sem consumir SAS nem baixar modelo.

**Documentação a emendar junto da implementação**: `architecture.md` §8 "Code Quality Tools" (hoje cita só o `ruff`) e o README §"Lint e testes" (acrescentar a linha do `mypy` e o `mypy.ini`). Não gera ADR — é escolha de ferramental dentro de uma decisão de qualidade já registrada, não tradeoff de arquitetura.


#### Estado: 10.1 e 10.3–10.6 implementados em 2026-09-30 — verificação do 10.2 e o que mudou em relação ao texto acima

**Verificação do 10.2, antes de seguir.** Conferido contra o repositório: `ruff` limpo, 275 testes rápidos e 19 de integração verdes, `dvc status` limpo e `model.pkl` = campeão (`6430cb3315da`). Um achado, corrigido aqui: ao ampliar a quarentena para `except Exception`, o job passou a pôr `str(exc)` de **qualquer** exceção em `resultado["erros"]`, que a aba de dev exibe crua — o mesmo vetor que o Passo 8 fechou para a Infobip. Um `ValueError` de data de nascimento ilegível, por exemplo, traz a data. O motivo agora é campo + regra (`ViolacaoDoContrato`), status HTTP + `messageId` (`ErroEnvioInfobip`) ou só o nome da classe, com teste travando.

**Dois achados colaterais, ambos anteriores a este passo:**

1. **O re-treino no Actions nunca reconheceria "nenhum dado novo".** O DVC 3 calcula o md5 das dependências sobre os bytes do arquivo, sem normalizar fim de linha. Com `core.autocrlf=true`, o checkout da autora tinha CRLF; o do runner Linux tem LF. Nenhum md5 do `dvc.lock` bateria no runner, todo `dvc repro` reexecutaria o pipeline inteiro, e o código 2 do gate (decisão 8 do Passo 9.1) nunca dispararia — cada mês geraria uma run nova e um PR de promoção com delta zero. Corrigido: `.gitattributes` força LF em `*.py`, `*.txt`, `*.yml`, `*.yaml`, `dockerfile*` e `dvc.lock`; os arquivos de trabalho foram convertidos (nenhuma mudança de conteúdo para o git) e os quatro stages reconciliados com `dvc commit`. Os md5 do lock agora são os do blob do git, que é o que o runner vê. `tests/test_coerencia_repo.py::test_dependencias_do_dvc_estao_em_lf_no_checkout` pega o arquivo novo que um editor de Windows grave com CRLF.
2. **`tests/test_pipeline_dvc_integracao.py` estava quebrado desde a segregação dos stages** (Passo 9): rodava a imagem de treino sem comando nenhum e falhava sem dizer por quê. Reescrito para rodar as quatro etapas do `dvc.yaml` em sequência, no container, sobre a semente. É ele que prova que o `validate_data` roda na imagem de treino.

**Decisões tomadas ao implementar** (onde o texto acima não decidia, ou onde a implementação divergiu dele):

| # | Tema | O que foi feito | Por quê |
|---|---|---|---|
| 1 | Biblioteca do contrato | Python puro (`src/contrato_features.py`), nem pandera nem pydantic | O contrato tem duas faixas por campo — recusa e **marca** — e nenhum dos dois expressa a segunda nativamente. O pydantic também não está na imagem de treino, onde o `validate_data` roda |
| 2 | Mensagem de violação | Campo e regra, nunca o valor | Ela vai para o resultado do job, para o log público do Actions e para a aba de dev |
| 3 | Completude de rótulo | Medida no **export**, não no `validate_data` | O stage lê o CSV, que só contém consultas com desfecho: a consulta passada ainda `agendado` só é visível no banco. É alerta, não bloqueio |
| 4 | Saídas do `validate_data` | Duas: o **selo** (`dados_validados.json`, dependência do `preprocess`) e o **relatório** (`relatorio_dados.json`, lido pelo gate) | O selo só carrega md5/linhas/positivos. Se o relatório fosse a dependência, mudar uma checagem mudaria o arquivo e re-treinaria o modelo sem mudança no dado |
| 5 | Mínimo de positivos no fold | `ceil(1/tolerância)` do gate = **20** (o fold vigente tem 21) | Derivado da regra do SLO §3.1, não um número novo. Faixa de positivos (10%–50%) e corte de PSI (0,2) ficam em `params.yaml` (`dados`) |
| 6 | Linha fora do contrato no export | Fica **fora** do dataset, contada e identificada pelo `id` | A mesma quarentena do job. Deixar o `validate_data` barrar o dataset inteiro por uma linha travaria o re-treino do mês |
| 7 | `predicoes.fora_do_dominio` | Nullable, **sem default** | `null` = "não verificado", o estado honesto das predições anteriores. Um default `false` afirmaria que elas foram conferidas |
| 8 | Lista de especialidades | `params.yaml` → `cadastro.especialidades` | Testada contra o mapa do modelo em produção (CI) e contra o do desafiante (suíte de sanidade) |
| 9 | Entrada da suíte de sanidade | Grade determinística montada do contrato | O CI baixa só o `model.pkl`; o `test.pkl` não existe lá. Dois **avisos** a mais que o texto previa: direção do histórico e domínio do contrato ≠ `feature_infos` do modelo (a marca passaria a mentir depois de um re-treino com dado novo) |
| 10 | Taxa de disparo | Calculada pelo gate, não pelo stage `validate` | Mexer no `validate.py` invalidaria o stage, que reabre a run do MLflow pelo `run_id` — e no runner o MLflow é efêmero. Vai para o resumo, o relatório, o PR e o próprio `champion_metrics.json` (`taxa_disparo_projetada`) |
| 11 | Pipeline que falha | Código de saída **3** no gate, com o motivo do `validate_data` no resumo | Antes, um `dvc repro` quebrado virava traceback com código 1 — indistinguível de um bloqueio por regressão |
| 12 | Guarda do campeão | `src/campeao.py`, só biblioteca padrão; `calcular_model_version` mudou para lá e é reexportada por `inference` | O deploy instala só o DVC e roda a guarda como script. A guarda confere sha **e** threshold — no deploy também, não só no job |
| 13 | Quarentena de 100% | **Falha** o job, além de avisar | É defeito sistêmico, e pela regra do lembrete sem predição a fila inteira recebeu SMS: precisa do `/fail` do Healthchecks para acordar alguém, mesmo que num dia de fila de uma linha seja alarme falso |
| 14 | `MESSAGING_PROVIDER` no job D-2 | Variável **obrigatória** no workflow (falha se ausente) | O stub não é inofensivo em produção: "envia" e o job marca `lembrete_enviado`, que o re-treino lê como "foi lembrado" |
| 15 | Ordem do deploy | Guarda do campeão e staging **antes** do `supabase db push` | São checagens sem efeito colateral: um deploy que não vai acontecer não deve deixar o banco migrado. A migração continua antes do sync (10.1) |
| 16 | DVC nos workflows | `requirements/dvc.txt`, incluído pelo `dev.txt` | Deploy, job D-2 e imagem instalam só o DVC, com o pin num lugar só |
| 17 | Log do Streamlit na imagem | `STREAMLIT_BROWSER_SERVER_ADDRESS=localhost` no `infra/deploy/dockerfile` | Achado pelo smoke: sem ele o Streamlit consulta um serviço externo e imprime o IP público da máquina no log ("External URL"), e a varredura de PII do CI reprovava a imagem. Só muda a URL impressa e o CORS de upload, que a UI não usa |
| 18 | CI sem o dataset | Teste da grade no dataset real pula quando ele não está no disco; a guarda de coerência aceita dependência versionada por `.dvc`; a integração baixa também a semente | O CI baixa só o `model.pkl` (regra "nunca sem alvo"). A exceção da integração é porque o teste do pipeline treina sobre a semente — o runner é descartável e nada dele sobe para lugar nenhum |
| 19 | Versões fixas | Actions por SHA (checkout v7.0.1, setup-python v7.0.0, upload-artifact v7.0.1, dependency-review v5.0.0, setup-cli v3.0.1, hub-sync v0.3.0), Supabase CLI 2.117.0 (a da autora), `hf` 2.0.0, mypy 2.3.1 | Achados 13 e 14. O Dependabot mantém os SHAs |
| 20 | `concurrency` no `ci.yml` | Não tem | Ele é chamado como workflow reutilizável pelo deploy, que tem a sua; um grupo no nível do workflow chamado é armadilha à toa |

**O check no banco `dias_entre_agendamento_consulta <= 180` não foi criado**, de propósito: o código do 10.2 ainda não está em produção, e migration restritiva só pode ir num deploy **posterior** ao do código que a exige (regra do 10.1). Quando o primeiro deploy tiver acontecido, o PR seguinte leva:

```sql
-- restritiva: deploy POSTERIOR ao do codigo do 10.2 (regra do Passo 10.1)
alter table agendamentos
  add constraint agendamentos_antecedencia_maxima
  check (dias_entre_agendamento_consulta <= 180) not valid;
-- `not valid` nao confere as linhas antigas no ALTER (que falharia se houver
-- alguma acima de 180); conferir e corrigir antes de validar:
-- alter table agendamentos validate constraint agendamentos_antecedencia_maxima;
```

**Verificado:**
- `ruff check src tests scripts` (agora com as regras S) e `mypy src scripts` limpos. Controles do 10.6 conferidos: sem `mypy.ini`, o mypy morre em "Duplicate module named client" (a colisão era real, não dedução); `calcular_idade("1990-01-01", hoje)` reprova com `arg-type`; função sem anotação num módulo do nível 1 reprova com `no-untyped-def`; num módulo baseline, passa.
- **368 testes rápidos** (eram 275) e **22 de integração**: 21 contra o Supabase local, com a migration nova aplicada por `supabase migration up`, e o pipeline completo no container.
- `dvc repro validate_data` no container: aprovado, 21 positivos no fold de teste (mínimo 20). `train_raw.pkl`/`test.pkl`/`mapa_especialidade.json` regerados com o contrato no `preprocess` são **byte a byte** os versionados; `model.pkl` e `model_version` intactos; `dvc status` limpo.
- Suíte de sanidade sobre o campeão vigente: zero falhas, zero avisos.
- Imagem de deploy: build, `/_stcore/health` e `/health` em ~4s, e `scripts/auditoria_lgpd.py` sobre o log do container sem achado (depois da decisão 17).
- Os cinco workflows passam no `actionlint` sem erro; as expressões `jq` do corpo do PR de promoção foram testadas.

**Pendências — dependem de GitHub/Hugging Face, não de código** (o checklist do Passo 11 parte daqui):
- **Antes de 2026-10-01 06:00 UTC (hoje à noite)**: o `retrain.yml` de `main` ainda é o antigo e dispara nesse horário com zero secrets. Configurar os secrets ou `gh workflow disable retrain.yml` — continua valendo o aviso da revisão de 2026-09-29.
- Secrets e variáveis da tabela do README ("CI/CD"), incluindo a SAS só de leitura e o environment `production` com `HF_TOKEN`/`HF_SPACE_ID`; proteção de `main` exigindo os checks do `ci.yml`; CodeQL pelo *default setup* em Settings.
- As verificações que só existem com o repositório no GitHub (as da 1ª revisão e do 10.1): PR de teste, CI quebrado deixando o deploy pulado, migração aditiva de brincadeira, lista de arquivos do Space igual à do staging, PR de promoção recebendo o check, `job_d2.yml` disparado duas vezes sem SMS em dobro.
- **`supabase db push` das migrations `20260929000000` e `20260930000000` no projeto remoto.** O primeiro deploy faz isso sozinho; até lá, a "Fila do dia" local apontando para o remoto mostra o aviso de schema desatualizado (`42703`), porque o select passou a pedir `predicoes.fora_do_dominio`.
- **Threshold recalibrado não tem caminho até produção.** Com o dataset inalterado, o gate sai com código 2 e não reescreve o campeão; a pré-checagem do job recusa um `params.yaml` com outro threshold. A decisão do Passo 12 sobre o gap de recall precisa decidir também o caminho: um modo do gate que só re-mede no threshold novo, ou um PR revisado que reescreve o campeão com as métricas re-medidas.
- **Sugestão, não implementada:** um disjuntor antes do envio — se a quarentena passar de um limite no meio da fila, parar de mandar lembrete sem predição. Hoje a pós-checagem detecta a quarentena de 100%, mas depois de os SMS terem saído. Mudaria a decisão 3 da 2ª revisão, por isso fica com a autora.


### 10.7 — Canário do modelo com rollback automático ([ADR-009](adr/adr-009-canario-do-modelo.md)) — **implementado em 2026-10-01**

> **Decisão da autora (2026-10-01)**: o rollback depois da promoção é feito por **canário**. A objeção de volume (com uma clínica só, ~10 agendamentos/dia no canário não dão poder estatístico) não vale para o produto, que atende várias clínicas (BRIEFING). Blue-green no Space foi descartado (sem roteador; o proxy seria operador novo vendo PII), e a sombra fica como etapa anterior possível — ver as alternativas no ADR-009.

**O problema que o passo resolve.** O gate do 9.1 mede o desafiante offline e promove direto: o merge do PR leva o modelo a 100% da fila. O "gate de rollback" do SLA §3 impede promover um modelo pior **no fold de teste**, mas não havia volta depois da promoção, e o fold não mede os dois números que pagam a conta: quantos pacientes da fila real o modelo manda para SMS pago e quantos pacientes de baixo risco faltam sem aviso.

#### Plano — traçado contra o repositório

O ponto de partida que orienta tudo: **a inferência que conta é a batch, no runner** (2ª revisão do Passo 10). O canário mora no job D-2; o Space, o deploy e o `data/model.pkl` não mudam de papel.

| Peça | Onde | O que muda |
|---|---|---|
| Estado do canário | `data/canario.json` + `data/canario/` (novo) | Registro (versão, campeão base, métricas offline, fração, hash do `dvc.lock` de `main` no início) e o modelo por `.dvc` próprio. Mais as **cópias** do `dvc.lock` e do `.dvc` do dataset do treino do canário (`*.salvo`, extensão que o DVC não coleta). Em `main`, o `dvc.lock` continua sendo o do campeão — é ele que o deploy, o CI e o job leem |
| Decisão e ciclo de vida | `src/canario.py` (novo, mypy nível 1) | Sorteio por paciente, guardrails, `iniciar`/`promover`/`reverter`/`verificar`, preparação para o job e CLI |
| Gate | `src/retrain_gate.py` | Aprovado + campeão existente + `canario.habilitado` → `canario.iniciar` em vez de reescrever o campeão. **Código 4**: canário ativo, o re-treino nem roda o `dvc repro`. Modelo já revertido → código 1. Desafiante idêntico ao campeão → código 2 |
| Job D-2 | `src/jobs/inferencia_diaria.py` | `main()` chama `canario.preparar_para_job` depois da pré-checagem do campeão. `processar_dia` roteia por braço e grava `predicoes.model_version` e o threshold de cada braço; o resultado ganha `por_braco`, e a pós-checagem ganha o guardrail de quarentena. A aba de dev roda só com o campeão |
| Banco | `supabase/migrations/20261001000000_canarios_revertidos.sql` (aditiva) | Tabela `canarios_revertidos`: o estado que tira o canário da fila **já na execução seguinte**, antes de o PR chegar a `main`. Sem PII |
| Repositório | `src/db/repositories.py` | `estatisticas_de_modelo` (contagens com `count='exact'`/`head=True`, sem o corte de 1000 linhas do PostgREST), `canario_revertido`, `registrar_canario_revertido` (idempotente) |
| Parâmetros | `params.yaml` → `canario` | Fração, dias mínimo/máximo, pisos de amostra, z crítico e margens. Sem default no código, pelo mesmo critério das tolerâncias do gate. Nenhuma chave nova é dependência do `dvc.yaml` (`dvc status` limpo) |
| Workflows | `retrain.yml`, `job_d2.yml`, `canario.yml` (novo), `ci.yml`, `deploy.yml` | Ver "Coerência" abaixo |

**Guardrails** (decididos em `canario.decidir`, função pura):

| Guardrail | Medida | Margem | Por quê |
|---|---|---:|---|
| `taxa_disparo` | `classe_prevista = 1` / predições, por `model_version` | 5 p.p. | O custo de mensageria do BRIEFING |
| `falta_nao_avisada` | `no_show` / desfechos registrados, só entre os de baixo risco | 5 p.p. | O erro que custa R$ 180 e o único desfecho que o SMS não contamina |
| `quarentena` | quarentena / agendamentos do braço, **por execução** | 2 p.p. | Quarentena não gera linha em `predicoes`. Modelo novo com mapa de especialidade diferente mandaria uma especialidade inteira para o lembrete sem predição |

Teste de diferença de proporções com o ajuste de Agresti-Caffo e z unilateral de 95%. **Violado** = o canário é pior que o campeão além da margem, com significância. **Não inferior** = é pior por menos que a margem, com significância. Abaixo de 30 por braço, nenhum guardrail é avaliado. O campeão é medido a partir da **primeira predição do canário**, não da aprovação no gate: antes do merge, ele decidia a fila sozinho.

| Situação | Ação |
|---|---|
| Algum guardrail violado (a qualquer momento) | **reverter** |
| ≥ 7 dias, ≥ 300 predições e ≥ 300 desfechos de baixo risco no canário, todos não inferiores | **promover** |
| ≥ 21 dias sem a condição acima | **reverter** (sem evidência; o conservador é o campeão) |
| Demais casos | aguardar |

**Ciclo de vida:**

```
retrain.yml (dia 1)  gate aprova + há campeão -> data/canario/ + dvc add + dvc push -> PR "canario/inicio-..."
        merge ------>  job_d2.yml (diário)  pré-checagem do canário: verificar -> revertido no banco? -> guardrails
                                            violado -> grava canarios_revertidos, fila 100% campeão, run vermelho
                                            ok      -> 20% da fila (por paciente) no canário
                       canario.yml (diário, 09h47)  avaliar -> aguardar | promover | reverter
                                            promover -> PR "canario/promover-<versão>": campeão + dvc.lock restaurado
                                                        merge -> deploy.yml (Space recebe o modelo)
                                            reverter -> grava canarios_revertidos + PR "canario/reverter-<versão>":
                                                        remove data/canario/, registra no histórico (o gate não o aprova de novo)
```

#### Coerência com o CI/CD e com o re-treino

- **"Só o campeão vai para produção" continua literal.** `data/model.pkl` e `champion_metrics.json` em `main` são sempre o campeão. A guarda do `src/campeao.py` no deploy e no job não mudou. O canário é um segundo artefato com registro e verificação próprios (`canario.py verificar`), e só o job o carrega.
- **O canário não entra no Space.** O staging é uma lista fechada e não o copia (há teste para isso). Os paths do canário entram no `paths-ignore` do deploy: abrir ou reverter um canário não rebuilda o Space. A promoção muda `champion_metrics.json` e `dvc.lock`, e por isso deploya. Também há teste.
- **CI no PR do canário.** O `ci.yml` (disparado por `workflow_dispatch`, mesmo mecanismo do achado 6) baixa o modelo do canário e roda `canario.py verificar --sanidade`. O modelo tem de ter sido publicado, ser o registrado, ter sido aprovado contra o campeão atual e passar na mesma suíte de sanidade que o gate e o CI aplicam ao campeão. `src/canario.py` entrou no filtro que torna a integração obrigatória.
- **Credenciais.** Nada novo além do que cada workflow já tinha. O `canario.yml` usa a SAS **só de leitura**: a promoção troca o ponteiro (`dvc.lock`) para um artefato que o `retrain.yml`, o único com escrita, já publicou ao abrir o canário.
- **Re-treino.**
  - O código 0 do gate continua significando "aprovado". O `retrain.yml` decide entre o PR do canário e o PR de promoção direta pela presença de `data/canario.json`.
  - O PR do canário **não** leva `dvc.lock` nem o `.dvc` do dataset. Por isso o próximo re-treino reexecuta o pipeline contra o dataset exportado, como antes.
  - Depois de um rollback, um mês sem desfecho novo reproduziria o mesmo modelo, já aprovado uma vez. A lista de revertidos o bloqueia (código 1).
  - Depois de uma promoção, o lock restaurado é o do treino do canário. O "nenhum re-treino efetivo" (código 2) continua funcionando.
- **Ordem no tempo.** `dias_maximos` (21) é menor que o intervalo do re-treino. Um canário que chega ao dia 1 sem decisão é pendência humana, um PR sem merge, e o código 4 a torna visível. **Interação com o SLO §5** (re-treino executado em 100% dos meses): um mês com código 4 aparece como run falho do `retrain.yml`. É proposital, para a pendência não ficar silenciosa, mas no cálculo do SLO deve ser lido como "re-treino adiado por canário pendente", e não como pipeline quebrado. Por isso o motivo vai para o resumo do run.
- **Migration.** Aditiva, pode ir no mesmo deploy do código (regra do 10.1). Mas o job **não depende** dela para rodar sem canário: as funções novas só são chamadas com `data/canario.json` presente.
- **Dois pontos de decisão, uma regra.** O job e o `canario.yml` chamam a mesma `canario.avaliar` com o mesmo `params.yaml`. A gravação em `canarios_revertidos` é idempotente, e a primeira decisão é a que vale.

#### Verificado em 2026-10-01

- `ruff check src tests scripts` e `mypy src scripts` limpos; `actionlint` sem erro nos seis workflows; `dvc status` limpo.
- **417 testes rápidos** (eram 368): `tests/test_canario.py` (40, incluindo o job com canário e o `main()` com canário quebrado), os do gate com canário (6) e as guardas de workflow em `test_coerencia_repo.py` (3).
- **Integração**: 23 de 24 verdes contra o Supabase local, incluindo as duas novas (contagens por braço com desfecho só de baixo risco; `canarios_revertidos` idempotente). A que falhou é anterior a este passo e está nos achados abaixo.
- **Ensaio ponta a ponta contra o Supabase local**, com um desafiante de bytes diferentes e comportamento idêntico:
  - o `main()` do job dividiu 145 predições em 116 do campeão e 29 do canário;
  - com o canário mandando lembrete a 100% da fila dele, a execução seguinte gravou o rollback (`taxa_disparo` 100% x 0%, z = 27,7) e mandou a fila 100% para o campeão;
  - a execução depois dessa só avisou ("revertido... até o PR ser mesclado");
  - `canario.py reverter` removeu os arquivos e pôs o modelo na lista de revertidos.

#### Achados ao implementar (não são do canário, mas afetam o argumento dele)

1. **O job D-2 quebra com o volume que justifica o canário** (anterior a este passo). `buscar_agendamentos_d2_pendentes` filtra as predições existentes com `.in_("id_agendamento", ids)`, que manda **todos** os ids da janela na URL. Com ~300 agendamentos pendentes, o PostgREST respondeu `414 URI too long` e o job morreu antes de predizer qualquer um (reproduzido no ensaio). Com várias clínicas, isso é o caso normal. Correção sugerida, fora deste passo: consultar em lotes (ex. 100 ids) ou trocar por um `not exists` numa view/RPC. **Precisa ser resolvido antes de habilitar várias clínicas.**
2. **Login local quebrado pelo `supabase/config.toml`** (anterior, ADR-008). `[auth.email] enable_signup = false` faz o CLI subir o GoTrue com `GOTRUE_EXTERNAL_EMAIL_ENABLED=false` ("Email logins are disabled"). O que se queria é `[auth] enable_signup = false`, mantendo o provedor de e-mail ligado. Só aparece depois de reiniciar o stack local, por isso `test_login_real_nao_troca_a_identidade_das_consultas_do_backend` passava antes. Não afeta o projeto remoto, que é configurado pelo Dashboard.
3. **Supabase local no Windows**: as portas 54321–54324 caíram numa faixa reservada pelo Windows (`netsh interface ipv4 show excludedportrange protocol=tcp` mostra 54269–54368). Os containers subiam sem publicar as portas. Correção, em PowerShell de administrador: `net stop winnat; net start winnat` e depois `supabase start`.

#### Pendências

- **Variável `CANARIO_DESLIGADO`** (Settings → Variables, vazia ou `false`) e a migration `20261001000000` no projeto remoto. O primeiro deploy faz o `db push`.
- **Ensaio real no Passo 11**: com o repositório no GitHub, rodar o `retrain.yml` por `workflow_dispatch` com um desafiante aprovado e confirmar a sequência completa: PR do canário com o check do CI, merge sem rebuild do Space, job com `por_braco` no resumo, `canario.yml` avaliando, rollback manual por `workflow_dispatch` com o PR de reversão. É evidência para o pitch.
- **Volume**: com o piloto de uma clínica, o canário reverte por prazo. Até a segunda clínica entrar, decidir entre manter (o modelo não evolui), aumentar `fracao`/`dias_maximos` ou `canario.habilitado: false`.
- **Contrato com a clínica**: informar que parte da fila é decidida por um modelo em observação (ADR-009, Cons).
- O achado 1 acima, antes de qualquer cliente além do piloto.

---

## Passo 11 — Deploy no Hugging Face Space

**Decisão**: um único HF Space (SDK Docker) rodando Streamlit + FastAPI juntos (Streamlit chama o modelo em processo), evitando pagar por dois serviços always-on. Supabase free tier + HF Space free/community tier mantêm custo perto de US$0, deixando a margem dos US$100/mês para custo real de mensageria Infobip e eventual upgrade de CPU se o cold start violar o SLO de <10s.

- ~~`infra/deploy/dockerfile` (API+UI combinados, `requirements/api.txt`+`ui.txt`, sem `requirements/train.txt`), carregando `data/model.pkl` empacotado na imagem (não de um MLflow ao vivo — decisão do Passo 9)~~ — **feito no Passo 4** (antecipado), junto com `infra/deploy/entrypoint.sh`, o serviço `app` do `docker-compose.yml`, `.dockerignore` e `.gitattributes`. Verificado localmente: `/health` 200 com a `model_version` esperada, UI em 7860, mesma probabilidade dentro e fora do container, container morre inteiro se um dos processos cair e para em <1s no SIGTERM.
- Resta aqui: front-matter YAML do HF Space, `APP_ENV=prod`, segredos configurados na UI do Space (não versionados), e a checagem de usuário não-root/permissões de escrita que o Space exige.
- **Porta única do Space**: o HF Space (SDK Docker) publica só uma porta (`app_port`, default 7860). Hoje a imagem expõe UI em 7860 e API em 8000 — em produção só a primeira ficaria acessível. A UI não sofre (chama o modelo em processo, ADR-005 b); quem fica sem endereço público é a API como porta de entrada para integrações externas (diagrama C2). Decidir entre: (a) expor só a UI e adiar a API pública para quando houver um consumidor externo real, (b) proxy reverso na frente dos dois na porta publicada, (c) publicar a API e servir a UI por outro caminho. Registrar a escolha como emenda ao ADR-005 ou ADR novo, conforme o peso.
- **Ligar as sondas externas do Passo 8.5**, que só agora têm URL pública para apontar: monitor do UptimeRobot em `/health` do Space e checks do Healthchecks.io para o job D-2 e o re-treino. Vale anotar que o Space free **hiberna por inatividade** — o monitor batendo de minutos em minutos mantém o container acordado como efeito colateral, o que melhora o cold start percebido (SLO §2) mas mascara o comportamento real de hibernação. Decidir conscientemente se isso é desejável antes de medir o cold start para o pitch.
- **Antes do primeiro sync**, conferir que o projeto Supabase remoto está com todas as migrations aplicadas (`supabase db push`) e que os três secrets do sub-passo [10.1](#101--migrations-de-banco-no-deploy) existem no repositório — senão o primeiro deploy sincroniza código contra schema antigo, que é o incidente de 2026-09-28 acontecendo com a clínica na frente.
- **Login da equipe no projeto remoto** ([ADR-008](adr/adr-008-login-da-equipe.md)): desligar "Allow new users to sign up" no Dashboard do Supabase (Authentication → Sign In / Providers), subir o tamanho mínimo de senha para 8 e criar as contas da equipe com `scripts/criar_funcionario.py`, antes de divulgar a URL do Space. O `supabase/config.toml` vale só para o Supabase local. Com o cadastro aberto, a tela de login seria só decoração.
- Deploy inicial: criar o HF Space e rodar manualmente o `deploy.yml` do Passo 10 (`workflow_dispatch`) para o primeiro sync via `huggingface/hub-sync` (a action cria o Space se ele não existir, com `--exist-ok`). Dali em diante, todo push em `main` (deploy manual de código ou promoção automática do Passo 9) usa o mesmo workflow — não há um segundo mecanismo de deploy a manter.

**Verificação**: Space público respondendo `/health` 200 com a `model_version` esperada; smoke test manual fim a fim (criar agendamento → job via `workflow_dispatch` → predição+explicação visível na fila do Streamlit → log de decisão de mensageria); medição manual do cold start vs. SLO <10s; smoke test do loop de deploy automático (merge de um PR de promoção do Passo 9 → confirmar que o Space rebuilda sozinho, sem passo manual).

---

## Passo 12 — Validação final para o pitch (Semana 16)

- `scripts/medir_latencia.py` (mede p95 de `/predict` contra o deploy real); atualizar `docs/SLA.md`/`docs/SLO.md` com coluna "medido" ao lado de "alvo"; `docs/LGPD.md` (Passo 8) como anexo de riscos.
- **De onde vem cada número** (Passo 8.5, [ADR-006](adr/adr-006-observabilidade.md)): §1 uptime e 5xx → relatório do UptimeRobot; §2 p95 → `eventos_app` acumulada em produção, com `medir_latencia.py` servindo de contraprova pontual; §3 → MLflow; §4 → query em `predicoes`; §5 → histórico de runs do GitHub Actions + Healthchecks.io; §6 → teste estático preventivo (não auditoria de log, que é inexecutável no Space — ver Passo 8). Números medidos ao longo da operação, não coletados na véspera.

**Verificação**: todos os números do SLA/SLO coletados e documentados (uptime, p95, recall/f1/roc-auc do MLflow, 100% cobertura SHAP via `predicoes.explicacao_shap IS NOT NULL`, 0 PII em log); **decisão final sobre o gap de recall registrada explicitamente** (threshold recalibrado ou gap aceito e documentado) antes da apresentação.

---

## Passo 13 (opcional, pós-núcleo) — LLM/TrueFoundry

> **Revisão do passo antes de implementar (2026-09-29)**: o escopo foi ampliado — o funcionário passa a buscar paciente individual ou a fila de uma data e a pedir a interpretação do SHAP numa aba de chat — e, ao confrontar essa ampliação com o repositório, três pedidos colidiam com decisões vigentes (CPF nunca persistido, LLM como fronteira sem PII do [ADR-007](adr/adr-007-nome-do-paciente.md), `telefone` fora da fila). As decisões estão em **"Decisões de 2026-09-29"** no fim deste passo e **substituem** o corpo abaixo onde divergem. A ordem do plano foi mantida: o passo só começa depois do commit do Passo 12.

Só depois do Passo 12 (núcleo funcional, testado e deployado). Consulta de paciente específico pelo funcionário (item do README, não exigido por SLA/SLO).

- `src/llm/client.py`: mesmo padrão interface+stub do Passo 7 (`StubLLMClient` para dev/test, `TrueFoundryClient` real atrás de env flag). Prompt montado só com dados já pseudonimizados do banco (Passo 5), nunca PII.
- **Ativação do plug do Passo 2**: trocar `ExplicadorLLMDesativado` por `ExplicadorLLMTrueFoundry` em `src/explain.py`, implementando `explicar_em_texto()` — recebe as contribuições SHAP já calculadas (Passo 2) + contexto pseudonimizado e devolve uma frase em português para a clínica (ex. "risco alto principalmente por 3 faltas anteriores e distância de 18km"). Passa a preencher `explicacao_texto` em `predicoes` (antes sempre `NULL`).
- Nova aba/caixa "Consulta ao LLM" em `src/ui/app.py` (Passo 4), onde o funcionário pergunta livremente sobre um paciente específico da fila.
- **É aqui — e só aqui — que o Langfuse volta à mesa** (ADR-004, [ADR-006](adr/adr-006-observabilidade.md)): tracing de prompt/completion, tokens e custo por chamada é exatamente o que ele foi feito para fazer, e passa a existir um LLM para observar. Avaliar self-host vs. cloud considerando que o prompt trafega dados já pseudonimizados (nunca PII) mas ainda assim sairia do Brasil — decisão a registrar quando o passo for executado.

**Verificação**: `tests/test_llm.py` com o stub, garantindo que o contexto montado nunca contém campos proibidos (reusa helper do Passo 8); teste de contrato stub/real. Teste específico de `explicar_em_texto()`: dado um conjunto fixo de contribuições SHAP sintéticas, o texto gerado (via stub determinístico, não chamando o TrueFoundry real em CI) menciona a feature de maior `abs(contribuicao)` — prova que a explicação em texto é fiel ao SHAP, não uma alucinação desconectada dos números.

### Decisões de 2026-09-29 (tomadas com a autora, antes de implementar)

**Escopo pedido**: uma aba "Assistente" na visão do funcionário (atrás do login do [ADR-008](adr/adr-008-login-da-equipe.md)) que (a) busca paciente individual ou a fila de uma data, (b) traz os dados do paciente para o chat com nome abreviado e CPF/telefone parcialmente ocultos e (c) interpreta o SHAP do paciente quando pedido.

**1. Ordem: o deploy fecha primeiro.** Passos 10 → 10.1 → 11 → 12 antes deste. O Passo 13 não depende tecnicamente de nenhum deles, mas compete pelo mesmo tempo até o pitch da Semana 16, e o que tem SLA é o núcleo. Fica registrado aqui para a ampliação de escopo não se perder até lá.

**2. O LLM decide o que buscar; a aplicação busca e exibe.** É a decisão que organiza as outras. Se o LLM redigisse a resposta com nome/CPF/telefone, esses dados trafegariam no prompt e na completion até o TrueFoundry e o provedor por trás dele — a porta nº 7 do ADR-007 aberta pelo lado de dentro. Por isso:
- As buscas são **ferramentas da aplicação** chamadas pelo LLM (`buscar_fila(data)`, `buscar_por_cpf(ref)`, `explicar(ref_paciente)`), executadas localmente contra `src/db/repositories.py`.
- O LLM só recebe **referências opacas** ("paciente #3") e dado já pseudonimizado (especialidade, idade, probabilidade, contribuições SHAP). A UI resolve a referência e desenha o card do paciente com os campos exibíveis; o identificador nunca passa pelo modelo.
- **Dois históricos por sessão**: o de exibição (com PII, em `st.session_state`, apagado no "Sair" e na expiração, como `_encerrar_sessao` já faz com a última predição) e o enviado ao LLM (só referências).
- **Um único ponto de saída** monta todas as mensagens enviadas ao provedor. `contexto_sem_pii` filtra por *chave* de dicionário e não alcança texto livre — o chat precisa de guarda própria, travada por teste.

**3. CPF: continua nunca persistido, e nada de coluna nova.** Não há CPF no banco para mascarar. Gravar a máscara usual (`***.456.789-**`) ao lado do hash seria pior que não ter hash: ela revela 6 dos 9 dígitos que determinam o CPF, restam 1.000 candidatos e o sha256 se reverte em milissegundos — a pseudonimização viraria cosmética (hoje o custo é ~10⁹ tentativas, [`LGPD.md` §2](LGPD.md)). O que se faz em vez disso:
- **Busca por CPF**: o CPF digitado é interceptado **localmente** antes de qualquer envio (tem forma reconhecível, mesmas regras de `logging_config.REGRAS_DE_PII`), convertido pelo hash de `logic._id_paciente_externo_de_cpf` e trocado por referência opaca na mensagem que segue ao LLM.
- A tela pode ecoar **mascarado só o CPF que o funcionário acabou de digitar** (vem do input, não do banco).
- Na fila de uma data, o desambiguador continua sendo o início do hash, já exibido no painel de detalhe (ADR-007).

**4. Telefone: fora da aba, por ora.** `buscar_fila_do_dia` deixa `telefone` de fora de propósito ("o funcionário precisa chamar o paciente pelo nome, não discar para ele"), e nenhuma finalidade para exibi-lo foi definida. As ferramentas do chat usam selects explícitos sem a coluna. Reabrir exige finalidade declarada, pelo mesmo critério de necessidade que abriu as exceções do Passo 7 e do ADR-007.

**5. Nome: busca fora do chat; exibição abreviada montada localmente.** Nome digitado em texto livre iria para o LLM, e nome não tem forma reconhecível ([`LGPD.md` §8](LGPD.md)) — não há regex que o intercepte com segurança. A busca por nome fica num **campo próprio da aba** (ou restrita aos nomes da fila da data selecionada), resolvida por filtro determinístico, sem LLM. O nome abreviado ("Ana S. C.") é produzido no card pela UI a partir de `nome_completo`; o modelo nunca o vê. Vale anotar a honestidade do desenho: a busca não precisa de LLM — onde ele agrega valor de fato é na interpretação do SHAP.

**6. Interpretação do SHAP sob demanda, não em lote no job D-2.** Substitui o item "passa a preencher `explicacao_texto` em `predicoes`" do corpo acima, que implicitamente mandaria 100% da fila ao provedor todo dia. Sob demanda, só sai o paciente que o funcionário pediu — minimização (Art. 6º, III). O texto gerado pode ser gravado em `predicoes.explicacao_texto` como cache, via `ExplicadorLLM.explicar_em_texto` (fronteira do ADR-007 mantida). O prompt enquadra o texto como "o que o modelo considerou", não como causa da falta: SHAP não é causal, e com `recall_1` 0.429 uma frase fluente projetaria mais confiança do que o modelo tem.

**7. Observabilidade sem Langfuse.** Evento próprio em `eventos_app` (`origem`/`tipo` de LLM: latência, tokens, nome de classe de exceção), com a allowlist de `src/observabilidade.py` estendida. Langfuse segue fora: guardaria prompt/completion, o que é mais uma transferência internacional. A latência do LLM fica **fora** do p95 do SLO §2 (que é de predição) e medida à parte; LLM indisponível degrada só a aba, nunca a fila.

**Pendências para quando o passo for executado** (não bloqueiam nada agora):
- **ADR-010** (o 009 foi usado pelo canário do Passo 10.7) registrando a decisão 2 e as alternativas recusadas (LLM redigindo a resposta com PII; CPF parcial persistido; busca por nome dentro do chat).
- **Transferência internacional e operador novo**: mesmo sem identificador, o que vai ao LLM inclui `especialidade` e probabilidade de falta — dado derivado de dado de saúde ([`LGPD.md` §2](LGPD.md)). Verificar região e DPA do TrueFoundry e do provedor por trás dele (Art. 33/39) e revisar `LGPD.md` §2.1 (o chat é uma porta de saída nova), §4 e §9. Deixa de valer, para esta porta, o "nenhum destinatário novo" do ADR-007.
- **Alcance de acesso**: busca por CPF alcança o histórico inteiro do paciente, além da fila de uma data. Sem RBAC (ADR-008), avaliar uma trilha de "quem consultou qual paciente".
- Secrets novos (chave/URL do TrueFoundry) em `.env.example` e nos secrets do Space.

**Verificação acrescida**: cliente LLM espião recebendo todas as mensagens de uma conversa que busca por CPF, por nome (campo) e pela fila de uma data — nenhuma mensagem contém nome conhecido do banco, CPF ou telefone, com controle negativo (injetar o nome no ponto de saída faz o teste falhar); "Sair" e expiração apagam os dois históricos; LLM fora do ar mantém as demais abas funcionando (`test_ui_smoke.py`).

---

## Arquivos críticos já mapeados

- `src/preprocess.py` (`COLUNAS_CATEGORICAS`, `preprocessar`) — base do Passo 1
- `src/features.py` (`extrair_features_temporais`) — reuso direto no Passo 1
- `src/train.py` (formato de `data/model.pkl`, linha 95) — contrato que o Passo 1 precisa respeitar
- `params.yaml` (`decision.threshold`, hiperparâmetros) — fonte única de verdade para API/job/gate
- `tests/conftest.py` — fixtures e padrão de teste (sem mocks pesados) a seguir em todos os passos novos
- `docs/architecture.md`, `docs/adr/adr-004-decisão-técnica.md` — alvo do Passo 0
- `pytest.ini` — já define marcador `integracao`, usar nos testes de DB/job/deploy
- `src/ui/app.py`/`src/ui/logic.py` (criados no Passo 4) — casca de abas reaproveitada e preenchida de verdade nos Passos 5-7, 8.5 e 13
- `docs/adr/adr-006-observabilidade.md` — decisão de observabilidade de aplicação (Passo 8.5), com o levantamento de cobertura nativa do GitHub Actions/HF Space e as alternativas descartadas (Langfuse, OpenTelemetry, Grafana Cloud)

## Como validar o plano ponta a ponta

A cada passo, os critérios de verificação descritos já são o teste — não há uma validação única no final. A interface Streamlit (Passo 4 em diante) funciona como harness de verificação visual contínuo, crescendo junto com o backend. O plano só é considerado "produto pronto" quando o Passo 12 fecha com os números do SLA/SLO medidos contra o deploy real (não estimados), incluindo a decisão explícita sobre o gap de recall.
