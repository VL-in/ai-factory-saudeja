# LGPD — SaúdeJá (Classificador de No-show)

> **Status:** primeira versão, 2026-09-27, escrita junto da blindagem de PII no log (`src/logging_config.py`). Fecha duas pendências que decisões anteriores declararam explicitamente e adiaram para cá: a base legal da transferência internacional do remote do DVC ([ADR-005](adr/adr-005-integracoes-implicitas.md), emenda de 2026-09-21) e a política de retenção dos dados derivados. É insumo direto do item 4 do [BRIEFING.md](BRIEFING.md) (riscos LGPD no pitch ao Conselho, Semana 16).
>
> **Precisa de validação jurídica antes de virar compromisso externo.** O que está aqui é a análise técnica de quem construiu o sistema: qual dado existe, onde ele fica, por quanto tempo e o que o protege. O enquadramento legal está fundamentado, mas não substitui parecer de quem responde por ele.

O BRIEFING trata dados de saúde como categoria especial **desde o protótipo**, mesmo com dataset sintético (`data/AVISO-DADOS-SINTETICOS.md`). Este documento segue essa regra: descreve o sistema como se todo dado fosse real, porque é assim que ele vai operar.

---

## 1. Papéis: quem é controlador e quem é operador

Distinção que organiza todo o resto, e que estava implícita na arquitetura sem nunca ter sido escrita:

| Papel (LGPD Art. 5º) | Quem | O que decide |
|---|---|---|
| **Controlador** (VI) | A **clínica-cliente** | Por que e como os dados dos pacientes dela são tratados. É ela que tem a relação com o paciente, que presta o atendimento e que responde pelo prontuário. |
| **Operador** (VII) | **SaúdeJá Tecnologia em Saúde S.A.** | Trata os dados **em nome da clínica**, nos limites do contrato de prestação do SaaS. Não decide finalidade própria. |

Três consequências práticas:

1. **A base legal é da clínica, não nossa** (§3). O que nos cabe é operar dentro dela e não criar finalidade nova por conta própria — é por isso que o dataset de treino sai pseudonimizado e que nenhum dado de paciente é usado para outra coisa que não a priorização de risco de no-show daquela mesma clínica.
2. **Pedido de titular chega pela clínica** (§6). O paciente não tem relação com a SaúdeJá; ele tem com a clínica, que nos aciona.
3. **O que a SaúdeJá pode apagar por iniciativa própria é limitado** (§5). O registro do atendimento é do controlador. Dado *derivado* — a probabilidade que o nosso modelo calculou, o registro de que um lembrete foi disparado — é gerado por nós, para nossa finalidade de operação, e aí sim a retenção é decisão nossa.

---

## 2. Inventário: que dado existe, e por que ele é sensível

O produto foi desenhado com minimização de PII desde o schema (`architecture.md` §4.1), então o inventário é curto de propósito. **O CPF nunca é persistido**: a tela de cadastro o coleta e o converte no hash sha256 que identifica o paciente (`src/ui/logic.py::_id_paciente_externo_de_cpf`). O **nome completo passou a ser gravado em 2026-09-28** ([ADR-007](adr/adr-007-nome-do-paciente.md)) — ver §2.1, que explica por que e o que limita a saída dele.

| Dado | Onde fica | Classificação |
|---|---|---|
| `id_paciente_externo` (hash sha256 de CPF) | `pacientes` (Supabase) | **Pessoal pseudonimizado** — Art. 12/13. Não é anônimo: o hash é reversível por força bruta sobre o espaço de CPFs válidos. |
| `data_nascimento`, `sexo` | `pacientes` | Pessoal |
| `telefone` | `pacientes` | **Pessoal, identificador direto** — exceção deliberada da minimização: sem contato não existe o produto (lembrete pago). |
| `nome_completo` | `pacientes` | **Pessoal, identificador direto** — segunda exceção deliberada (ADR-007): sem nome a fila do dia não é operável por quem atende. Ver §2.1 |
| `especialidade` do agendamento | `agendamentos` | **Sensível (Art. 5º, II)** — ver abaixo |
| `distancia_km`, `dias_entre_agendamento_consulta`, `historico_noshow`, `data_hora_agendada` | `agendamentos` | Pessoal (operacional) |
| `status` do agendamento (`concluido`/`no_show`/`cancelado`) | `agendamentos` | **Sensível** — comparecimento a consulta é dado referente à saúde |
| Probabilidade de no-show + explicação SHAP | `predicoes` | Pessoal derivado (inferência sobre dado sensível) |
| Canal e status de envio do lembrete | `mensagens_disparadas` | Pessoal (auditoria de comunicação, SLA §6) |
| Latência, contadores, nome de classe de exceção | `eventos_app` | **Não pessoal** — sem nenhuma coluna de PII por construção ([ADR-006](adr/adr-006-observabilidade.md)) |
| Dataset de treino (`data/consultas-treino.csv`) e `model.pkl` | Azure Blob Storage, via DVC | Pessoal pseudonimizado — ver §4 |
| E-mail de login e hash da senha **do funcionário** | `auth.users` (schema do Supabase Auth, mesmo projeto e região) | Pessoal, **de funcionário, não de paciente** ([ADR-008](adr/adr-008-login-da-equipe.md)). Existe para verificar quem acessa a fila do dia. A regra "e-mail nunca tem coluna" continua valendo para as tabelas de paciente em `public`. A senha é guardada pelo Supabase como hash, nunca por nós |

### 2.1 As duas exceções à minimização, e o que as justifica

`telefone` e `nome_completo` ([ADR-007](adr/adr-007-nome-do-paciente.md)) são identificadores diretos gravados de propósito. O critério é o mesmo nos dois casos, e é o princípio da **necessidade** (Art. 6º, III), não conveniência:

| Coluna | Sem ela, o que deixa de funcionar |
|---|---|
| `telefone` | O disparo de lembrete pago — que é o produto inteiro. Não há para onde mandar mensagem. |
| `nome_completo` | A aba "Fila do dia". A coluna "Paciente" mostrava o hash sha256 de **64 caracteres**: ninguém chama `9f86d081884c7d65…` na sala de espera, e o registro de desfecho (`concluido`/`no_show`, de onde sai o dado do re-treino mensal) depende de quem atende saber de quem é a linha. |

**Nenhum destinatário novo entra em cena.** O funcionário que vê o nome é preposto da **clínica**, que é a controladora (§1): ele já tem a relação com o paciente, já detém o prontuário e assinou termo de confidencialidade. A base legal não muda (§3, Art. 11, II, "f").

**O que impede o nome de sair da tela da equipe** — sete portas, cada uma com teste automatizado travando (tabela completa no ADR-007):

| Porta | Mecanismo |
|---|---|
| Log de aplicação | `nome` é chave proibida no filtro de `src/logging_config.py` (§8) |
| Código que loga | Guarda sobre a AST de `src/*.py` (§8) |
| `eventos_app` | Allowlist fechada de `detalhe` ([ADR-006](adr/adr-006-observabilidade.md)) |
| Dataset de treino (sai do Brasil, §4) | `COLUNAS_SAIDA` de `src/export_treino.py` |
| API `/predict` (integrações externas) | Campo inexistente em `src/api/schemas.py` |
| SMS via Infobip (processador externo) | Select do job D-2 não traz a coluna |
| **LLM/TrueFoundry** (integração opcional, ainda não ligada) | `explain.contexto_sem_pii`, aplicada pela classe-base de `ExplicadorLLM` |

A última merece nota, porque foi a preocupação que motivou a decisão: **cifrar a coluna no banco não protegeria o nome do LLM.** A chamada ao provedor sai de dentro do mesmo processo que já leu o nome para desenhar a tela — a chave estaria na mesma memória. O que protege é o nome não entrar no dicionário que vira prompt, e isso é garantido por construção: `explicar_em_texto` é concreta e sanitiza o contexto antes de delegar à implementação, que sobrescreve `_gerar_texto`. Um teste impede qualquer subclasse de pular essa fronteira.

**Armazenamento em texto puro**, com a mesma proteção que `telefone` já tinha (§7): RLS sem policies, acesso só pela chave secreta do backend, TLS em trânsito, criptografia em repouso do provedor. Cifrar `nome_completo` deixando `telefone` em claro seria incoerente; a alternativa foi avaliada e recusada no ADR-007, com o caminho de reversão registrado.

**Onde exatamente está a sensibilidade** — vale ser preciso, porque isso muda o enquadramento e o argumento do pitch. Idade, sexo e distância, isolados, são dado pessoal comum. O que torna o tratamento sujeito ao Art. 11 é a combinação de dois campos: **`especialidade`** (saber que alguém consulta cardiologia é informação sobre a saúde dessa pessoa) e **o próprio fato de haver um agendamento médico**. Não há diagnóstico, prontuário, exame ou medicamento no sistema — e essa é uma escolha de desenho, não uma sorte: o modelo não precisa deles para prever falta.

---

## 3. Base legal

Dois níveis, porque a LGPD trata dado sensível em artigo separado.

**Dado sensível (Art. 11).** A hipótese aplicável é o **Art. 11, II, alínea "f" — tutela da saúde, em procedimento realizado por serviços de saúde**. O tratamento acontece dentro da operação de uma clínica, para viabilizar o atendimento que o próprio paciente agendou: prever quem provavelmente vai faltar existe para que a clínica consiga confirmar a presença dessa pessoa. Não é a hipótese de consentimento (Art. 11, I) — que seria mais frágil aqui, por exigir consentimento específico e destacado para uma finalidade que o paciente já buscou ao agendar.

**Dado pessoal comum (Art. 7º).** **Art. 7º, V — execução de contrato** entre a clínica e o paciente (o agendamento em si) e, para a operação da SaúdeJá como operadora, a execução do contrato de prestação do SaaS com a clínica.

**Sobre a nota herdada.** As anotações da Camila (`docs/herdado/notas-camila.md`) registram "base legal documentada (consentimento + execução de contrato)". A parte de execução de contrato se confirma; a de consentimento **não foi mantida** para o dado sensível, por duas razões: consentimento exigiria destaque e especificidade por finalidade (Art. 11, I) e seria revogável a qualquer momento, o que na prática significaria manter um caminho em que o paciente segue agendando mas não pode ser priorizado — complexidade sem ganho de proteção, já que a alínea "f" cobre exatamente esta situação. Registrado aqui como divergência consciente em relação às notas herdadas, não como esquecimento.

**O disparo do lembrete.** É comunicação sobre o próprio atendimento agendado (confirmação de presença), não marketing — mesma base do Art. 7º, V. A fronteira importa: usar o mesmo canal para oferecer serviço seria outra finalidade e exigiria outra base legal. O sistema não faz isso, e `src/messaging/client.py` só tem uma operação (`enviar_lembrete`).

---

## 4. Transferência internacional (Art. 33)

**O fato.** O banco de produção (Supabase) fica em **São Paulo (`sa-east-1`)**: pacientes, agendamentos, predições, mensagens e eventos não saem do Brasil. O remote do DVC, porém, é um container de **Azure Blob Storage na região Chile Central** — e é para lá que vão o **dataset de treino** (`data/consultas-treino.csv`, que inclui os desfechos reais registrados pela clínica de consultas) e o **`model.pkl`** derivado dele.

Isso é transferência internacional de dado derivado de dado de saúde, e precisa ser dito em voz alta. **A afirmação "os dados do SaúdeJá não saem do Brasil" é verdadeira para o banco de produção e falsa para o artefato de treino.** O pitch tem de usar a formulação correta.

**Enquadramento.** Art. 33, II, alínea "d" — cláusulas contratuais padrão: o tratamento se apoia no acordo de processamento de dados (DPA) do provedor de nuvem, que é o instrumento contratual que acompanha o serviço e prevê as garantias de proteção exigidas pela lei. A transferência é necessária à finalidade (re-treino mensal acordado com Produto, BRIEFING) e o provedor é o mesmo tanto para o dado quanto para o artefato.

**O que limita a exposição é o conteúdo, não a região:**

- O export (`src/export_treino.py`) **nunca inclui telefone, nome ou CPF** — e isso é verificado automaticamente, não por convenção (`tests/test_coerencia_repo.py::test_export_treino_sem_coluna_proibida_de_pii`).
- `id_paciente` no dataset é o hash sha256 do CPF.
- Sobram atributos demográficos (idade derivada, sexo), operacionais (especialidade, distância, antecedência) e o desfecho.
- Não há identificador direto no arquivo que sai do país.

**O que não é mitigado, e precisa constar do risco residual (§9):** um dataset pseudonimizado continua sendo dado pessoal (Art. 12 só equipara a anônimo o que é irreversível), e `especialidade` + desfecho continuam sendo dado sensível. Pseudonimização reduz a gravidade de um incidente; não tira a transferência do escopo do Art. 33.

**A alternativa segue aberta e custa pouco.** O [ADR-005](adr/adr-005-integracoes-implicitas.md) registra o caminho: criar um container em Brazil South, apontar `DVC_REMOTE_URL`/`.dvc/config.local` para ele e rodar `dvc push`. Nada no código depende da região. Enquanto a decisão atual vigora, este documento é o registro dela.

**Processamento do job D-2 no GitHub Actions** (2026-09-30, [ADR-005](adr/adr-005-integracoes-implicitas.md)). O job diário roda num runner hospedado pelo GitHub, fora do Brasil, e lê do banco o que a predição e o envio precisam: data de nascimento, sexo, especialidade, distância, antecedência, histórico de faltas e o **telefone** para o lembrete — nunca nome nem CPF (o select do job não traz `nome_completo`). Nada é gravado no runner além do `model.pkl`, e ele é descartado ao fim da execução. É a mesma classe de transferência que o Space já implicava, mas **um operador a mais** (o GitHub), com a mesma base: Art. 33, II, "d", pelas cláusulas do contrato do provedor. A formulação do pitch fica mais estreita: "os dados **armazenados** não saem do Brasil" continua verdadeira para o banco; o processamento diário acontece fora.

---

## 5. Retenção e término do tratamento

Princípio da necessidade (Art. 6º, III) e término do tratamento (Art. 15/16): dado não fica guardado indefinidamente só porque o banco aguenta.

| Tabela / artefato | Retenção | Quem executa | Por quê |
|---|---|---|---|
| `eventos_app` | **90 dias** (`OBSERVABILIDADE_RETENCAO_DIAS`) | `purgar_eventos_antigos()`, no job diário | Não contém dado pessoal; a janela existe pelo teto do free tier (ADR-006), não por LGPD |
| `predicoes` | **365 dias** (`RETENCAO_DADOS_DERIVADOS_DIAS`) | `purgar_dados_derivados_antigos()`, no job diário | Dado derivado, gerado por nós. Após o ciclo anual não serve mais a nenhuma finalidade: o re-treino lê o desfecho de `agendamentos.status`, nunca a probabilidade prevista |
| `mensagens_disparadas` | **365 dias** (mesma variável) | idem | Auditoria de disparo é compromisso do SLA §6, cobrável dentro de um ciclo contratual anual |
| `pacientes`, `agendamentos` | **Não purgados por iniciativa da SaúdeJá** | — | Registro do atendimento é do controlador (§1). Exclusão acontece por pedido da clínica (§6), não por política nossa |
| Dataset de treino e `model.pkl` (DVC) | Versionado, sem purga automática | — | Ver risco residual em §9 |

**Onde a política roda**: pendurada no job diário que já existe, não num agendador novo — mesmo critério do ADR-006 (observabilidade não deve adicionar peça de infra a manter). Falha de purga não derruba o job; a janela de 365 dias tem folga de sobra para uma execução perdida não virar retenção indevida.

**Efeito colateral aceito e documentado**: apagar `predicoes` remove também a explicação SHAP daquelas predições. Isso não afeta o SLO §4 (cobertura de explicação é medida sobre janelas de 24h/7d/30d) nem a "Fila do dia" (que é do dia), nem o re-treino.

---

## 6. Direitos do titular (Art. 18)

Como operadora, a SaúdeJá não atende o titular diretamente — atende a clínica, que é quem recebe o pedido. O que existe de capacidade técnica hoje:

| Direito | Como é atendido | Limite conhecido |
|---|---|---|
| Confirmação e acesso (I, II) | Consulta pelo `id_paciente_externo` (hash do CPF do titular) devolve tudo o que existe sobre ele nas quatro tabelas | O caminho canônico segue sendo o CPF, recalculando o hash. Desde o ADR-007 há `nome_completo` gravado, mas busca por nome não é confiável para atender um pedido de titular (homônimo, grafia) — serve para operar a fila, não para identificar juridicamente |
| Correção (III) | Alteração direta em `pacientes`/`agendamentos` | — |
| Eliminação (VI) | `on delete cascade` de `pacientes` remove agendamentos, predições e mensagens em uma operação | **Não há interface para isso**: hoje é operação manual no banco. Pendência declarada em §9 |
| Portabilidade (V) | Export das linhas do titular | Sem ferramenta dedicada; ver §9 |
| Revisão de decisão automatizada (Art. 20) | **Atendido por desenho**: toda predição vem com explicação SHAP (SLO §4, 100% de cobertura), e a decisão que o modelo toma é *enviar ou não um lembrete* — não nega, condiciona ou encarece atendimento | Vale registrar no pitch: é o que torna o risco de decisão automatizada baixo aqui, e não uma promessa de que o modelo acerta |

---

## 7. Segurança e medidas técnicas (Art. 46)

O que já está implementado e verificado, não o que se pretende fazer:

| Medida | Onde | Garantia |
|---|---|---|
| Minimização por design | `supabase/migrations/` | CPF e e-mail não têm coluna, sem exceção. As duas exceções (`telefone`, `nome_completo`) são autorizadas por par (coluna, migration) -- a coluna só vale no arquivo que a introduziu. Guarda automatizada: `test_coerencia_repo.py::test_migrations_sql_sem_coluna_proibida_de_pii` |
| Fronteira de PII para fora do sistema | `src/explain.py::contexto_sem_pii` | O contexto do prompt do LLM é sanitizado pela classe-base, não por convenção de cada implementação (ADR-007) |
| Colunas explícitas na leitura da fila | `repositories._COLUNAS_FILA_DO_DIA` | O `select("*")` anterior fazia toda coluna nova de `pacientes` fluir para a UI sem decisão -- `telefone` já ia, sem uso |
| Pseudonimização | `src/ui/logic.py::_id_paciente_externo_de_cpf` | CPF vira hash sha256 antes de chegar ao banco |
| Idade nunca persistida | `src/features.py::calcular_idade` | Guarda-se `data_nascimento`; a idade é calculada na hora da predição |
| RLS em todas as tabelas, sem policies | migrations | URL do projeto vazada não dá acesso: só a chave secreta do backend enxerga algo |
| Login da equipe (identidade, não autorização) | `src/ui/logic.py::autenticar_funcionario`, [ADR-008](adr/adr-008-login-da-equipe.md) | Visão do funcionário só depois de e-mail e senha no Supabase Auth. Sem cadastro aberto. Sessão expira depois de 30 min sem uso. A mensagem de erro não revela se o e-mail existe. `test_ui_smoke.py` trava que nenhuma aba é renderizada sem login; `test_db.py` trava, contra o Supabase real, que o login não troca a identidade das consultas do backend |
| Segredos fora do git | `.env.example` documenta variáveis, nunca valores | Inclui a **URL** do remote do DVC, não só a credencial (o nome do container é informação de reconhecimento) |
| TLS em trânsito | HTTPS do Space, `supabase-py` → Postgres gerenciado | — |
| Criptografia em repouso | Responsabilidade do Supabase gerenciado e do Azure Blob Storage | Herdada do provedor, não implementada por nós |
| Zero PII em log | `src/logging_config.py` | Ver §8 |
| Observabilidade sem PII | `src/observabilidade.py` | `detalhe jsonb` restrito a allowlist fechada; exceção registra só o *nome da classe*, nunca a mensagem |
| Erro de provedor sem PII | `src/messaging/client.py::ErroEnvioInfobip` | A mensagem carrega (status HTTP, `messageId`), não o corpo da resposta — que ecoava o telefone do destinatário |

---

## 8. "Sem PII em logs. Nunca." — como isso é garantido

O requisito é absoluto no BRIEFING e mensurável no [SLO §6](SLO.md) (0 ocorrências). A forma de garanti-lo mudou em relação ao que o SLO previa, e a mudança está registrada no [ADR-006](adr/adr-006-observabilidade.md):

**O SLO dizia "verificável via auditoria/regex nos logs". Não é executável em produção.** O log de runtime do Hugging Face Space é efêmero — restart ou rebuild apaga, sem busca e sem retenção. No dia do pitch não haverá log de produção para varrer. A garantia é, portanto, **preventiva**, em três camadas:

1. **Filtro de redação em todo handler do processo** (`src/logging_config.py`). Mora no handler, não num logger nosso, e é aplicado a **todos** os handlers já instalados — inclusive os do `uvicorn`, que põe `propagate=False` e os do `streamlit`. Isso importa porque o log que de fato existe em volume em produção não é o nosso: é o de acesso (que carrega IP de cliente) e o de erro dessas bibliotecas. Duas famílias de regra, porque PII tem duas naturezas: CPF/telefone/e-mail/IP têm forma reconhecível; **nome não tem** — "Ana Souza" é indistinguível de qualquer par de palavras —, então o que se reconhece é a chave que o anuncia (`nome=`, `"nome":`, `extra={"nome": ...}`).
2. **Guarda estática sobre o código** (`tests/test_coerencia_repo.py::test_nenhuma_chamada_de_log_em_src_referencia_campo_de_pii`). Percorre a AST de todo `src/*.py`, acha as chamadas de log e falha se alguma referenciar campo proibido — por variável, atributo, índice ou chave de `extra`. Mesmo espírito da guarda que já bloqueia coluna proibida nas migrations: a proibição vale desde a estrutura, não por convenção de revisão. Tem controle negativo próprio, porque uma varredura que deixa de reconhecer as chamadas daria verde sem verificar nada.
3. **`scripts/auditoria_lgpd.py`** — varredura de log, rebaixada de evidência principal a **ferramenta de verificação**. Serve para o smoke test local do deploy (o momento em que existe log de verdade), para auditar log baixado do GitHub Actions e para fechar o ciclo: a saída de um processo já configurado, varrida por ele, dá zero achado. Compartilha as regras com o filtro (`REGRAS_DE_PII`), não uma segunda cópia — duas listas de regex sobre o mesmo requisito divergiriam, e a que divergisse em silêncio seria justamente a que dá o veredito.

**Direção de erro escolhida**: quando a redação falha, a linha é substituída por um marcador visível em vez de sair crua; e a regra numérica redige de mais em vez de de menos — um IP de cliente no log de acesso do uvicorn é redigido, o que não é dano colateral, já que endereço IP é dado pessoal.

**Fora do escopo, de propósito**: os scripts do pipeline de treino (`preprocess`/`train`/`validate`) seguem usando `print`. Rodam sobre dataset pseudonimizado, fora do caminho de qualquer dado de paciente vivo, e a saída deles é lida por humano no terminal e no MLflow.

---

## 9. Riscos residuais e pendências declaradas

Nada aqui é surpresa oculta — cada item é para constar do slide de risco do pitch:

1. **Transferência internacional do artefato de treino** (§4). Mitigada pelo conteúdo (pseudonimizado, sem contato), não eliminada. Reversível a baixo custo se a decisão mudar.
2. **A superfície de reidentificação cresceu com o nome gravado** ([ADR-007](adr/adr-007-nome-do-paciente.md), §2.1). Um vazamento do banco agora expõe nome + telefone + especialidade + desfecho, o que identifica a pessoa diretamente; antes exigia quebrar o hash de CPF. É o custo assumido de ter uma fila do dia operável, e vai para o slide de risco do pitch em voz alta — **o pitch não pode mais usar "não temos nome de paciente" como argumento de redução de risco**. A formulação correta: "temos nome, restrito à tela da equipe, com sete portas de saída fechadas e testadas".
3. **A tela da recepção fica visível para a sala de espera.** A "Fila do dia" mostra a fila inteira com nome, e quem espera pode ler o monitor — exposição a terceiros que não assinaram confidencialidade nenhuma. Mitigado por aviso na própria tela, **não por mecanismo**: onde posicionar o monitor é decisão da clínica. A alternativa (nome abreviado na tabela, completo só na linha selecionada) foi avaliada e recusada por custo de operação (ADR-007).
4. **Eliminação de titular é operação manual.** O `on delete cascade` existe e funciona, mas não há interface nem script para o pedido do Art. 18, VI. Enquanto o volume é o de um protótipo, é aceitável; não é aceitável em operação real.
5. **Dataset versionado não tem purga.** O DVC guarda histórico por construção — é o que dá rastreabilidade de qual dado gerou qual modelo (SLO §5). Um pedido de eliminação de titular não alcança as versões antigas do dataset sem reescrever o histórico. Tensão real entre rastreabilidade de ML e Art. 18, VI, sem solução implementada.
6. **Autenticação sem autorização** ([ADR-008](adr/adr-008-login-da-equipe.md), 2026-09-28). Até esta data, qualquer pessoa com a URL do Space via a fila do dia. Hoje a visão do funcionário exige e-mail e senha, e só o administrador cria contas. Isso fecha a parte técnica da justificativa do ADR-007: quem vê o nome tem uma conta individual. A outra parte (a conta só é criada para quem assinou o termo de confidencialidade) é processo da clínica. **O que continua aberto:**
   - **Não há RBAC nem autorização no banco.** Toda conta vê tudo, os dados são lidos com a chave secreta e o RLS não contém um bug da UI.
   - **O login da equipe inteira pode ser travado de fora.** O limite de tentativas do Supabase Auth é por IP, e todo login chega pelo IP do servidor. Quem tentar senhas em massa pela tela bloqueia o login de todos por alguns minutos.
   - **A tela aberta não se fecha sozinha.** A sessão expira depois de 30 minutos sem uso, mas o Streamlit só reage a interação: a fila continua visível no monitor até alguém tocar na tela, e aí aparece o login. O risco 3 continua mitigado por aviso, não por mecanismo.
   - **A API FastAPI não tem autenticação.** `/predict` não lê o banco nem devolve dado de paciente, mas é uma porta aberta.
   - **O cadastro fechado do projeto remoto é configuração manual** no Dashboard do Supabase (checklist do primeiro deploy, ver [ADR-008](adr/adr-008-login-da-equipe.md)), não versionada.
7. **Criptografia em repouso é herdada do provedor**, não verificada por nós.
8. **Este documento não tem validação jurídica** (ver cabeçalho).
9. **`especialidade` é o campo que carrega a sensibilidade** (§2). Se o produto crescer para aceitar motivo da consulta, diagnóstico ou medicação, esta análise precisa ser refeita do zero — não é um "mais um campo".

---

### 9.1 Riscos acrescentados pelo CI/CD (2026-09-30)

10. **O log do job D-2 é público.** O repositório é público, e o log do GitHub Actions também. O job processa telefone de paciente; o que impede o número de aparecer no log é o filtro de redação de `src/logging_config.py` (§8), que aqui deixa de ser defesa em profundidade e vira **a última barreira**. Mitigações: o job loga só contadores e identificadores internos (`id_agendamento`), a lista de erros com motivo nunca vai para o log nem para o resumo do run, e a guarda estática sobre a AST de `src/` impede que código novo passe campo de PII a uma chamada de log. O CI ainda varre o log da imagem de deploy com `scripts/auditoria_lgpd.py`. **Não mitigado**: um bug de terceiro que logue o corpo de uma requisição antes do filtro existir no processo.
11. **Processamento fora do Brasil num operador a mais** (§4): o runner do GitHub. Sem gravação local que sobreviva ao job.
12. **Credencial do remote do DVC no CI.** O CI roda código de PR. Por isso CI, deploy e job D-2 usam uma SAS **só de leitura** (e só do container); a credencial de escrita fica só no workflow de re-treino, e também é uma SAS do container, que cria arquivos novos mas não sobrescreve nem apaga os existentes. A account key, que dá acesso à conta inteira, não chega a nenhum workflow. PR de fork não recebe secret nenhum.

## 10. Incidentes

Exposição de dado pessoal é comunicada à clínica-cliente (controladora) e ao DPO em **até 72h** da ciência, alinhado ao SLA §6 e às boas práticas do Art. 48. Como operadora, a SaúdeJá comunica o controlador; a comunicação à ANPD e aos titulares é decisão da clínica, apoiada pelas informações técnicas que fornecermos (o que foi exposto, quantos titulares, quando, o que foi feito).

O que existe de capacidade de investigação hoje: `eventos_app` (90 dias, sobrevive ao restart do Space), `mensagens_disparadas` (365 dias) e o histórico do GitHub Actions. O que **não** existe: log de aplicação retido — ver §8 e o risco 2 do ADR-006.

---

## 11. Contato

| | |
|---|---|
| Encarregado de Dados (DPO) | `dpo@saudeja.com.br` *(endereço fictício — a SaúdeJá é a empresa-cliente fictícia do [BRIEFING.md](BRIEFING.md); substituir por contato real antes de qualquer uso externo)* |
| Responsável técnico deste documento | Vanessa Hoysan Lin (ML Engineer, time de Produto) |

---

## Revisão

Revisar sempre que: (a) uma coluna nova entrar em `supabase/migrations/`, (b) a região de qualquer armazenamento mudar, (c) uma integração externa nova passar a receber dado de paciente — o LLM opcional (TrueFoundry) é o próximo candidato —, ou (d) o [SLO.md](SLO.md)/[SLA.md](SLA.md)/[architecture.md](architecture.md) mudarem. Última geração: 2026-09-27; revisado em 2026-09-28 (§2/§2.1/§6/§7/§9 — nome do paciente gravado e visível à equipe, ADR-007); revisado de novo em 2026-09-28 (§2/§7/§9 — login da equipe, ADR-008); revisado em 2026-09-30 (§4/§9.1 — job D-2 no runner do GitHub Actions e log público).
