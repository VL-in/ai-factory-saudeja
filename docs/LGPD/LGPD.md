---
# yaml-language-server: $schema=https://lgpd.md/schema.json
# Declaração estruturada no formato lgpd.md (spec 1.0). O corpo abaixo é a análise
# completa; estes campos a resumem e precisam ser mantidos em sincronia com ela.
spec: "1.0"
atualizado: "2026-10-09"
expira: "2027-04-09"
status: "em-adequação"
controlador:
  nome: "SaúdeJá Tecnologia em Saúde S.A."
  contato: "dpo@saudeja.com.br"
encarregado:
  nome: "Encarregado de Dados (DPO) da SaúdeJá — nome a definir"
  contato: "dpo@saudeja.com.br"
direitos:
  canal: "dpo@saudeja.com.br"
  prazo: 15
politica:
  url: "https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/LGPD/LGPD.md"
  idioma: "pt-br"
  atualizada: "2026-10-09"
bases_legais:
  - finalidade: "Identificação do paciente e execução do agendamento (em nome da clínica-cliente, controladora)"
    base: "execução de contrato"
    artigo: "Art. 7, V"
    dados: ["id_paciente_externo (hash sha256 do CPF)", "sexo", "nome_completo", "data_nascimento"]
  - finalidade: "Identificação do paciente no atendimento realizado pela clínica (em nome da clínica-cliente, controladora)"
    base: "tutela da saúde"
    artigo: "Art. 7, VIII"
    dados: ["id_paciente_externo (hash sha256 do CPF)", "sexo", "nome_completo", "data_nascimento"]
  - finalidade: "Envio de lembrete de confirmação de presença na consulta agendada"
    base: "consentimento"
    artigo: "Art. 7, I"
    dados: ["telefone", "canal e status de envio do lembrete"]
  - finalidade: "Contato com o paciente sobre o atendimento agendado"
    base: "legítimo interesse"
    artigo: "Art. 7, IX"
    dados: ["telefone"]
  - finalidade: "Atributos operacionais usados na previsão de risco de falta"
    base: "legítimo interesse"
    artigo: "Art. 7, IX"
    dados: ["distancia_km", "dias_entre_agendamento_consulta"]
  - finalidade: "Previsão de risco de falta para confirmar a presença do paciente no atendimento agendado"
    base: "tutela da saúde"
    artigo: "Art. 11, II, f"
    dados: ["especialidade", "status do agendamento", "historico_noshow", "data_hora_agendada", "probabilidade de no-show", "explicação SHAP"]
  - finalidade: "Melhoramento do algoritmo de previsão de falta: re-treino com os desfechos reais exportados para o dataset de treino (SaúdeJá controladora quando o modelo é compartilhado entre clínicas)"
    base: "consentimento"
    artigo: "Art. 11, I"
    dados: ["linhas de produção do dataset de treino (desfecho no_show, lembrete_enviado e atributos da consulta)"]
  - finalidade: "Autenticação e segurança de acesso da equipe da clínica (SaúdeJá como controladora)"
    base: "legítimo interesse"
    artigo: "Art. 7, IX"
    dados: ["e-mail de login do funcionário", "hash da senha do funcionário"]
transferencia_internacional:
  transfere: true
  destinos:
    - "Fora do Brasil — runner hospedado do GitHub Actions (job D-2 diário e re-treino mensal, sem gravação persistente)"
    - "Estados Unidos — Hugging Face Space (aplicação: processa nome, CPF antes do hash, telefone e dados da consulta, sem gravação persistente)"
    - "Local a confirmar — Infobip (telefone e texto do lembrete)"
  mecanismo: "Art. 33, VIII — consentimento específico e destacado do paciente, com informação prévia do caráter internacional, para o GitHub Actions e o Hugging Face; coleta ainda não implementada (seção 4.2). O art. 33, II, b não é atendido pelos contratos das contas atuais (seção 4.1). O remote do DVC está em Brazil South desde 09/10/2026 (seção 4.3)"
---

# PROTEÇÃO DE DADOS PESSOAIS NO SAÚDEJÁ: CLASSIFICADOR DE NO-SHOW

Este documento registra a análise técnica do tratamento de dados pessoais no SaúdeJá: quais dados existem, onde ficam, com que base legal, por quanto tempo e como são protegidos. Os dispositivos legais citados sem indicação de fonte são da Lei Geral de Proteção de Dados Pessoais (LGPD), Lei nº 13.709/2018 (Brasil, 2018). Os campos estruturados no início do arquivo seguem a especificação lgpd.md (Lgpd.md, 2025).

**Situação:** em adequação e **sem validação jurídica**. O documento não deve ser usado como compromisso externo antes de ser validado por quem responde juridicamente pelo tratamento.

A semente do dataset de treino é sintética (`data/AVISO-DADOS-SINTETICOS.md`); as linhas de produção que o re-treino acrescenta a ela são dados reais (seção 2). Ainda assim, o documento descreve o sistema como se todo dado fosse real, conforme exige o [BRIEFING](../BRIEFING.md), do qual é insumo para o item 4 (riscos de LGPD no pitch ao Conselho, Semana 16).

## 1 PAPÉIS NO TRATAMENTO DE DADOS

**Quadro 1 – Agentes de tratamento (art. 5º, VI e VII)**

| Papel | Agente | Dados | Responsabilidade |
|---|---|---|---|
| Controlador | Clínica-cliente | De paciente | Decide a finalidade e a forma do tratamento. Mantém a relação com o paciente, presta o atendimento e responde pelo prontuário |
| Operador | SaúdeJá Tecnologia em Saúde S.A. | De paciente | Trata os dados em nome da clínica, nos limites do contrato do SaaS, sem finalidade própria |
| Controlador | SaúdeJá Tecnologia em Saúde S.A. | De paciente, no treino de modelo compartilhado entre clínicas | Decide a finalidade do treino quando os desfechos de uma clínica melhoram o modelo que atende outras |
| Controlador | SaúdeJá Tecnologia em Saúde S.A. | De conta do funcionário | Decide como a plataforma autentica quem acessa a fila do dia: Supabase Auth, cadastro fechado e sessão de 30 min |

Fonte: elaboração própria (2026).

**Camadas da SaúdeJá nos dados de paciente.** Na predição, no lembrete e na fila do dia, a SaúdeJá é operadora: trata os dados em nome da clínica, para o atendimento daquela clínica. No treino, o papel depende de quem o modelo atende. O schema não tem `id_clinica`, mas o [ADR-008](../adr/adr-008-canario-do-modelo.md) parte do volume somado das clínicas. Se os dados da clínica A treinam o modelo que atende a clínica B, a finalidade do treino é da SaúdeJá, que passa a ser controladora desse tratamento. Nesse caso, o termo de consentimento coletado pela clínica precisa citar a SaúdeJá. Se cada clínica tiver o próprio modelo, o treino volta a ser tratamento em nome da clínica, e a SaúdeJá continua operadora.

Consequências:

a) a base legal dos dados de paciente é da clínica (seção 3). A SaúdeJá não cria finalidade nova: os dados de paciente servem à priorização de risco de falta e, com consentimento específico do titular, ao melhoramento do modelo que faz essa priorização (seção 3). No treino de modelo compartilhado, a base legal e o atendimento aos pedidos de revogação passam a ser responsabilidade da SaúdeJá. O dataset de treino sai pseudonimizado;

b) pedidos de titular paciente chegam pela clínica (seção 6);

c) os registros do atendimento (`pacientes`, `agendamentos`) só são apagados a pedido da clínica. Os dados derivados (`predicoes`, `mensagens_disparadas`) são gerados em nome da clínica, com janela de retenção fixada pela SaúdeJá no contrato de operação (seção 5);

d) nos dados de conta do funcionário, cabem à SaúdeJá a base legal, a retenção, o atendimento ao titular e a comunicação de incidente (seções 3, 5, 6 e 10).

## 2 INVENTÁRIO E CLASSIFICAÇÃO DOS DADOS

O schema foi desenhado com minimização de dados desde a origem ([architecture.md](../architecture.md), seção 4.1). O CPF nunca é persistido: a tela de cadastro o converte no hash sha256 que identifica o paciente (`src/ui/logic.py::_id_paciente_externo_de_cpf`). O nome completo é gravado desde 28/09/2026 ([ADR-007](../adr/adr-007-nome-do-paciente.md)); ver subseção 2.1.

**Critério de classificação.** Idade, sexo e distância, isolados, são dados pessoais comuns. A sensibilidade vem de dois elementos:

- a `especialidade`, porque saber que alguém consulta cardiologia é informação sobre a saúde dessa pessoa;
- o fato de existir um agendamento médico, registrado em `data_hora_agendada`, `status` e `historico_noshow`.

Por isso a classificação é feita pelo registro, não pela coluna isolada: o art. 11, § 1º, aplica o regime de dado sensível a todo tratamento que revele dado sensível. A predição e a explicação SHAP herdam essa sensibilidade. Toda coluna nova em `agendamentos` ou `predicoes` deve ser tratada como sensível, salvo justificativa em contrário. O sistema não guarda diagnóstico, prontuário, exame nem medicamento, por decisão de desenho: o modelo não precisa deles para prever falta.

**Quadro 2 – Inventário de dados pessoais**

| Dado | Local | Classificação | Observação |
|---|---|---|---|
| `id_paciente_externo` | `pacientes` (Supabase) | Pessoal pseudonimizado (arts. 12 e 13) | Hash sha256 do CPF. Não é anônimo: pode ser revertido por força bruta sobre o espaço de CPFs válidos |
| `sexo`, `data_nascimento` | `pacientes` | Pessoal | A idade é calculada na hora da predição e não é gravada |
| `telefone` | `pacientes` | Pessoal, identificador direto | Exceção à minimização (subseção 2.1) |
| `nome_completo` | `pacientes` | Pessoal, identificador direto | Exceção à minimização (subseção 2.1) |
| `especialidade` | `agendamentos` | Sensível (art. 5º, II) | Revela a área de saúde consultada |
| `status` (`concluido`, `no_show`, `cancelado`), `historico_noshow`, `data_hora_agendada` | `agendamentos` | Sensível (art. 5º, II; art. 11, § 1º) | Revelam se, quando e quantas vezes a pessoa buscou ou recebeu atendimento de saúde |
| `distancia_km`, `dias_entre_agendamento_consulta` | `agendamentos` | Pessoal | — |
| Probabilidade de no-show e explicação SHAP | `predicoes` | Sensível derivado (art. 11, § 1º; art. 12, § 2º) | Perfil comportamental calculado a partir de entradas sensíveis. A explicação expõe os valores dessas entradas |
| Canal e status de envio do lembrete | `mensagens_disparadas` | Pessoal | Auditoria da decisão do modelo (seção 5) |
| Latência, contadores, nome de classe de exceção | `eventos_app` | Não pessoal | Nenhuma coluna de dado pessoal, por construção ([ADR-006](../adr/adr-006-observabilidade.md)) |
| Semente do dataset de treino (`data/consultas-historicas.csv`) | Repositório e Azure Blob Storage, via DVC | Não pessoal | 380 consultas sintéticas herdadas, sem titular real. Fixa: nunca recebe linha nova |
| Linhas de produção do dataset de treino (`data/consultas-treino.csv`) | Azure Blob Storage, via DVC | Sensível pseudonimizado (art. 11, § 1º; arts. 12 e 13) | Desfechos reais registrados pela clínica (`no_show`, `lembrete_enviado`) e atributos da consulta, acrescentados à semente por `src/export_treino.py`. Só existem a partir do primeiro export com desfecho real |
| `model.pkl` | Azure Blob Storage, via DVC | Derivado, sem linha de titular | Guarda parâmetros agregados, não registros. Mantido versionado para rollback do modelo (seção 5) |
| E-mail de login e hash da senha do funcionário | `auth.users` (Supabase Auth, mesmo projeto e região) | Pessoal (art. 5º, I); titular é o funcionário | E-mail corporativo é dado pessoal, porque a conta é individual. A senha é guardada como hash pelo Supabase. Tabelas de paciente em `public` continuam sem coluna de e-mail |

Fonte: elaboração própria (2026).

### 2.1 Exceções à minimização

`telefone` e `nome_completo` são identificadores diretos gravados de propósito, pelo princípio da necessidade (art. 6º, III).

**Quadro 3 – Justificativa das exceções à minimização**

| Coluna | O que deixa de funcionar sem ela |
|---|---|
| `telefone` | O disparo do lembrete pago, que é o produto. Não há para onde mandar a mensagem |
| `nome_completo` | A aba "Fila do dia". Sem o nome, a coluna "Paciente" mostrava o hash de 64 caracteres, e quem atende não sabia de quem era a linha. É dessa informação que depende o registro do desfecho (`concluido`, `no_show`), que alimenta o re-treino mensal |

Fonte: elaboração própria (2026).

Quem vê o nome é funcionário da clínica controladora: já tem relação com o paciente, já detém o prontuário e assinou termo de confidencialidade. Nenhum destinatário novo passa a ter acesso ao dado.

**Quadro 4 – Barreiras à saída do nome do paciente**

| Saída | Mecanismo, travado por teste automatizado |
|---|---|
| Log de aplicação | `nome` é chave proibida no filtro de `src/logging_config.py` (seção 8) |
| Código que gera log | Guarda sobre a AST de `src/*.py` (seção 8) |
| `eventos_app` | Lista fechada de campos permitidos em `detalhe` ([ADR-006](../adr/adr-006-observabilidade.md)) |
| Dataset de treino (sai do Brasil, seção 4) | `COLUNAS_SAIDA` de `src/export_treino.py` |
| API `/predict` | Campo inexistente em `src/api/schemas.py` |
| SMS via Infobip | A consulta do job D-2 não traz a coluna. O texto do lembrete também não cita a especialidade, que é dado sensível: só data e hora (`tests/test_skew_features.py::test_sms_nao_revela_a_especialidade`) |
| LLM via TrueFoundry (integração opcional, ainda não ligada) | `explain.contexto_sem_pii`, aplicada pela classe-base de `ExplicadorLLM` |

Fonte: elaboração própria (2026), a partir do [ADR-007](../adr/adr-007-nome-do-paciente.md).

O nome é armazenado em texto puro, com a mesma proteção do `telefone` (seção 7). Cifrar a coluna não o protegeria do LLM: a chamada ao provedor sai do mesmo processo que lê o nome para desenhar a tela, e a chave estaria na mesma memória. O que o protege é não entrar no prompt. `explicar_em_texto` sanitiza o contexto antes de delegar a `_gerar_texto`, e um teste impede que alguma subclasse pule essa etapa. Cifrar só `nome_completo` e deixar `telefone` em claro seria incoerente. A alternativa foi recusada no ADR-007, que registra o caminho de reversão.

## 3 BASES LEGAIS

Os dados sensíveis se apoiam no art. 11 e os dados pessoais comuns, no art. 7º.

**Quadro 5 – Base legal por dado**

| Dado | Art. 7º | Art. 11 |
|---|---|---|
| `id_paciente_externo`, `sexo`, `nome_completo`, `data_nascimento` | V (execução de contrato); VIII (tutela da saúde) | Não se aplica |
| `telefone` | I (consentimento); IX (legítimo interesse) | Não se aplica |
| Canal e status de envio do lembrete | I (consentimento) | Não se aplica |
| `distancia_km`, `dias_entre_agendamento_consulta` | IX (legítimo interesse) | Não se aplica |
| `especialidade` | Não se aplica | II, f (tutela da saúde) |
| `status`, `historico_noshow`, `data_hora_agendada` | Não se aplica | II, f (tutela da saúde) |
| Probabilidade de no-show e explicação SHAP | Não se aplica | II, f (tutela da saúde) |
| Desfechos reais exportados para o dataset de treino (linha de produção: `no_show`, `lembrete_enviado` e atributos da consulta) | Não se aplica | I (consentimento específico e destacado, para a finalidade de melhoramento do algoritmo) |
| E-mail de login e hash da senha do funcionário (SaúdeJá controladora) | IX (legítimo interesse) | Não se aplica |

Fonte: elaboração própria (2026), com base em Brasil (2018).

**Tutela da saúde (art. 11, II, f).** A alínea exige que o tratamento seja feito *exclusivamente* para tutela da saúde, em procedimento realizado por serviço de saúde. A predição existe para que a clínica confirme a presença do paciente no atendimento que ele mesmo agendou. A única decisão que ela toma é enviar ou não um lembrete. Usá-la para cobrança, overbooking, encaixe de outro paciente ou recusa de atendimento seria finalidade econômica da clínica, e exigiria rever a base legal antes da implementação.

**Consentimento descartado para a predição.** Na predição, o consentimento do art. 11, I, poderia ser revogado a qualquer momento, e o sistema teria de manter pacientes que agendam mas não podem ser priorizados, sem ganho de proteção. As anotações herdadas (`docs/herdado/notas-camila.md`) previam "consentimento + execução de contrato". A execução de contrato foi mantida para a identificação do paciente; o consentimento ficou restrito ao lembrete e ao treino. A divergência é intencional.

**Consentimento no treino (art. 11, I).** Reusar o desfecho real para treinar o modelo é finalidade secundária: não serve ao atendimento daquele paciente, e sim ao melhoramento do algoritmo para atendimentos futuros. Por isso não cabe no "exclusivamente" da alínea f, e se apoia no consentimento do titular ou de seu responsável legal, dado de forma específica e destacada para essa finalidade. A revogação tira o paciente do treino, mas não da predição nem do lembrete, que têm base própria. Obrigações, ainda não cumpridas pelo sistema (subseção 9.2, itens 19 a 21):

a) coletar o consentimento separado dos demais termos do cadastro e registrar quando e como foi dado (art. 8º, § 2º);

b) exportar para o dataset só as linhas de pacientes com consentimento vigente;

c) com a revogação, retirar as linhas do titular do próximo export e apagar as versões do dataset que as contêm ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)).

O consentimento é registrado na tabela `eventos_consentimento`, só de inserção: cada concessão ou revogação é um evento com finalidade, versão do termo, data e funcionário que registrou, e o consentimento vigente é o último evento de cada finalidade. A tabela serve de prova (art. 8º, § 2º) e de trilha de auditoria ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)). A coleta entrará na tela de agendamento numa etapa posterior.

**Consentimento no lembrete (art. 7º, I).** O lembrete é comunicação sobre o atendimento agendado, não marketing. `src/messaging/client.py` tem uma única operação, `enviar_lembrete`, e oferecer serviço pelo mesmo canal exigiria outra base legal. Usar consentimento cria três obrigações que o sistema ainda não cumpre (subseção 9.2, itens 13 e 14):

a) provar que o consentimento foi obtido, ônus que é do controlador (art. 8º, § 2º);

b) aceitar a revogação a qualquer momento, por procedimento gratuito e facilitado (art. 8º, § 5º), e parar os lembretes a partir dela;

c) encerrar, com a revogação, o tratamento baseado no consentimento (art. 15, III).

**`data_nascimento`.** Fica nos incisos V e VIII, e não no II (obrigação legal): o sistema usa a data só para calcular a idade que alimenta a predição, e nenhuma norma obriga o sistema a tratá-la.

**Legítimo interesse (art. 7º, IX).** Exige finalidade legítima, apenas os dados estritamente necessários e transparência ao titular (art. 10). O teste de balanceamento ainda não está documentado (subseção 9.2, item 15).

**Conta do funcionário.** A base é o legítimo interesse da SaúdeJá. A execução de contrato (art. 7º, V) não se aplica, porque exige contrato do qual o titular seja parte, e o funcionário não é parte do contrato do SaaS.

## 4 TRANSFERÊNCIA INTERNACIONAL DE DADOS

O banco de produção (Supabase) fica em São Paulo (`sa-east-1`), e o remote do DVC (Azure Blob Storage), em Brazil South desde 09/10/2026 (subseção 4.3): todo dado pessoal persistente do sistema fica armazenado no Brasil. Três tratamentos acontecem fora do país.

**Quadro 6 – Tratamento de dados fora do Brasil**

| Destino | Dados | Natureza |
|---|---|---|
| Runner hospedado do GitHub Actions | Job D-2 (diário, desde 30/09/2026): data de nascimento, sexo, especialidade, distância, antecedência, histórico de faltas e telefone. Re-treino (mensal): as linhas de produção do dataset, com hash do CPF e desfecho. Nunca nome nem CPF. O canário e o alerta de observabilidade leem só contagens | Processamento. O que é gravado no runner é descartado ao fim da execução. Continua fora do Brasil depois da migração do remote |
| Hugging Face Space (Estados Unidos) | Tudo o que a interface recebe ou exibe: nome, CPF antes da conversão em hash, telefone, data de nascimento, sexo, dados da consulta, predição e explicação | Processamento, sem gravação persistente. É o destino com mais dados, porque é onde o paciente digita o cadastro |
| Infobip (local a confirmar) | Telefone e texto do lembrete, com data e hora da consulta | Envio do SMS. A Infobip guarda o log com o texto por 3 meses por padrão |

Fonte: elaboração própria (2026), a partir do [ADR-005](../adr/adr-005-integracoes-implicitas.md).

Para o pitch, a formulação correta é: **os dados são armazenados no Brasil, mas são processados fora dele: a aplicação no Hugging Face, e o job diário e o re-treino no GitHub Actions.**

**Enquadramento.** O mecanismo pretendido era o art. 33, II, b (cláusulas-padrão contratuais), por meio do acordo de processamento de dados (DPA) de cada provedor. Desde 23/08/2025, cláusula contratual só ampara transferência internacional se adotar, integralmente e sem alterações, o texto das cláusulas-padrão aprovado pela Autoridade Nacional de Proteção de Dados (ANPD), num instrumento assinado por exportador e importador, ou se tiver cláusula específica aprovada por ela (Brasil, 2024). **Nenhuma das contas atuais cumpre isso** (subseção 4.1), e o mecanismo está em aberto (subseção 9.2, item 16).

**Mitigação pelo conteúdo.** O export (`src/export_treino.py`) nunca inclui telefone, nome ou CPF. Isso é verificado por `tests/test_coerencia_repo.py::test_export_treino_sem_coluna_proibida_de_pii`. O identificador do paciente é o hash do CPF. Restam idade derivada, sexo, especialidade, distância, antecedência e desfecho.

A mitigação tem limite: as linhas de produção pseudonimizadas continuam sendo dado pessoal, porque o art. 12 só equipara a anônimo o dado cuja anonimização é irreversível. Especialidade, desfecho e histórico de faltas continuam sendo dados sensíveis. A pseudonimização reduz a gravidade de um incidente, mas não tira a transferência do escopo do art. 33.

**Alternativas.** Cada destino pode sair do exterior por um caminho diferente:

a) **remote do DVC**: **executada em 09/10/2026** (subseção 4.3). A região é da conta de armazenamento, não do container, e não muda depois de criada: a migração exigiu uma conta nova. Nenhum código dependia da região ([ADR-005](../adr/adr-005-integracoes-implicitas.md)), e o armazenamento custa praticamente o mesmo;

b) **job D-2**: executar a imagem do job como Azure Container Apps Job em Brazil South, disparado pelo agendador do próprio Azure ou por um `az containerapp job start` vindo do GitHub Actions. No segundo caso, o runner só dá a ordem, e nenhum dado de paciente passa por ele. Alguns minutos por dia cabem na cota gratuita mensal do Container Apps (180.000 vCPU-s e 360.000 GiB-s por assinatura). Runner auto-hospedado no Brasil foi descartado: com o repositório público, um PR de fork pode executar código nele;

c) **aplicação**: hospedar a mesma imagem do Space em Brazil South. No Azure App Service B1 Linux, a tabela pública de preços indicava US$ 0,02 por hora em 09/10/2026, cerca de US$ 15 por mês, sem hibernação. No Container Apps com uma réplica sempre ativa de 0,5 vCPU e 1 GiB, o custo fica entre US$ 10 e US$ 35 por mês, conforme o tempo em uso ativo. Escalar até zero custa quase nada, mas cada primeira visita espera a imagem subir.

Somadas, as três mudanças custam de US$ 15 a US$ 35 por mês a mais, contra o orçamento de US$ 100 por mês do [BRIEFING](../BRIEFING.md), menos os US$ 9 do Hugging Face Pro se o Space for desligado. O crédito do Azure for Students (US$ 100 por 12 meses) não sustenta essa hospedagem por um ano inteiro, e a oferta é limitada a uso educacional (subseção 4.1). Os valores são estimativas da tabela de preços e não incluem o registro da imagem nem o log do Azure Monitor, que deve ficar desligado ou com retenção curta, pelo mesmo motivo da seção 8.

### 4.1 Acordos de tratamento de dados (DPA) das contas atuais

Cada provedor trata dados em nome da SaúdeJá e é seu suboperador (art. 39). O DPA é o contrato que define as obrigações dele. Para amparar transferência internacional, o DPA precisa ainda trazer o texto das cláusulas-padrão da ANPD. A verificação abaixo foi feita em 09/10/2026, sobre os textos públicos de cada provedor.

**Quadro 6A – DPA por conta**

| Conta | Há DPA? | Como se obtém | Cláusulas-padrão da ANPD | Observação |
|---|---|---|---|---|
| Azure for Students | Em princípio, sim: a assinatura é regida pelo Microsoft Customer Agreement, que incorpora o Data Protection Addendum (DPA) da Microsoft pelos Product Terms | Automático, sem assinatura nem negociação. A confirmar no portal, em *Cost Management + Billing*, qual contrato está ligado à assinatura | **Não.** A versão em inglês de 22/05/2026 só traz as cláusulas da União Europeia, do Reino Unido e da Suíça | A oferta se destina a educação e pesquisa não comercial, com US$ 100 de crédito por 12 meses. Esgotado o crédito, a assinatura é desativada, e com ela o remote do DVC, o que quebra o re-treino e o rollback de modelo. Uma operação real exige assinatura paga |
| GitHub Free | **Não.** O DPA do GitHub faz parte do GitHub Customer Agreement, que cobre organizações em planos pagos (Team, Enterprise). A conta gratuita é regida só pelos Termos de Serviço e pela Declaração de Privacidade | Mover o repositório para uma organização no plano Team | **Não** (versão de outubro de 2025) | O runner do job D-2 processa telefone e dados da consulta sem nenhum contrato de operador |
| Hugging Face Pro | **Não.** O DPA só é oferecido no plano Enterprise | Plano Enterprise | Não | O Pro é assinatura individual, sem cláusula de tratamento de dados. Os servidores ficam nos Estados Unidos |
| Supabase Free (São Paulo) | **Sim.** O DPA complementa os Termos de Serviço e vale com o aceite deles | Automático. Cópia assinada pelo painel, em *Legal documents*, via PandaDoc | **Não** (versão de junho de 2026). Os dados ficam no Brasil, então isso só pesaria se houvesse acesso remoto a partir do exterior | O plano gratuito não tem backup (seção 5) |
| Infobip (teste gratuito) | O DPA faz parte do contrato comercial. No teste gratuito, não há contrato negociado | Contrato pago | A confirmar | O teste dura 60 dias e só envia para 1 número verificado, com remetente de teste. **Não consegue enviar lembrete a paciente**: a operação real exige conta paga, e com ela o DPA |

Fonte: elaboração própria (2026), com base em Microsoft (2026a, 2026b), GitHub (2025), Supabase (2026) e Infobip (2026a, 2026b).

**Conclusão.** Com as contas atuais, só o Supabase tem DPA, e nenhum provedor oferece as cláusulas-padrão da ANPD. Em contas gratuitas ou individuais não há como negociar um aditivo. Restam três caminhos, combináveis por destino:

a) **tirar o destino do exterior** (alternativas a a c, acima). Resolve a transferência, mas não a falta de DPA: a hospedagem no Brasil ainda precisa de contrato de operador;

b) **consentimento específico e destacado para a transferência** (art. 33, VIII), com informação prévia do caráter internacional. Combina com o treino, que já depende de consentimento (seção 3). Não serve para a aplicação nem para o job D-2: um paciente que recusasse não poderia ser atendido pelo sistema;

c) **cláusulas-padrão da ANPD assinadas com o provedor** (art. 33, II, b). Só é realista com contrato corporativo.

O art. 33, IX, não resolve: ele cobre as hipóteses do art. 7º, II, V e VI, e não as bases do art. 11 em que se apoiam os dados de saúde.

No protótipo, com a semente sintética e a SaúdeJá fictícia, nenhum titular real é afetado. Antes de operar com pacientes reais, o mecanismo de cada destino precisa estar definido aqui e na [política de privacidade](POLITICA-DE-PRIVACIDADE.md), seção 6.

### 4.2 Mecanismo adotado

Decisão de 09/10/2026: o remote do DVC vai para Brazil South (migração executada no mesmo dia, subseção 4.3); o job D-2, o re-treino e a aplicação continuam no GitHub Actions e no Hugging Face, fora do Brasil.

**Quadro 6B – Mecanismo por destino**

| Destino | Mecanismo | Situação |
|---|---|---|
| Remote do DVC | Nenhum: não há transferência | Em Brazil South desde 09/10/2026. A conta antiga, no Chile, nunca guardou dado de titular (subseção 4.3) |
| GitHub Actions e Hugging Face (pacientes) | Consentimento específico e destacado do titular para a transferência, com informação prévia do caráter internacional, distinto das demais finalidades (art. 33, VIII) | Coleta não implementada |
| Hugging Face (conta do funcionário) | Mesmo inciso, no primeiro acesso | Coleta não implementada. Mecanismo frágil (ver abaixo) |
| Infobip | A definir com o contrato pago | O teste gratuito não envia lembrete a paciente (subseção 4.1) |

Fonte: elaboração própria (2026), com base em Brasil (2018).

**Por que consentimento.** As cláusulas-padrão (art. 33, II, b) dependem de contrato que as contas atuais não oferecem (subseção 4.1). O art. 33, IX, só cobre transferências necessárias às hipóteses do art. 7º, II, V e VI. A identificação do paciente se apoia no inciso V, mas a predição e o treino tratam dado de saúde, cuja base está no art. 11, que o inciso IX não alcança. Também não há decisão de adequação da ANPD para os Estados Unidos (art. 33, I). Resta o consentimento.

**Política de privacidade e consentimento têm papéis diferentes.** A política não é base legal: ela cumpre o dever de transparência (art. 9º) e traz a informação prévia que o inciso VIII exige. O mecanismo é o consentimento, coletado como ação própria do titular. Os dois são necessários.

**Requisitos da coleta:**

a) **ação separada e destacada**: uma caixa própria para a transferência, distinta do consentimento do lembrete e do treino, e nunca pré-marcada;

b) **informação prévia**: países (Estados Unidos e outros onde os provedores operam), provedores, dados e finalidade de cada um, e o fato de que esses provedores não adotaram as cláusulas-padrão da ANPD;

c) **antes de o dado sair do país**: o formulário de cadastro roda no próprio Space. A caixa de consentimento fica fora do formulário, e os campos só aparecem depois dela. Os campos de um `st.form` só são enviados ao servidor no envio do formulário;

d) **registro como prova**: evento em `eventos_consentimento`, com a finalidade `transferencia_internacional` ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md));

e) **em nome da clínica**: a clínica é a controladora, e o termo cita a SaúdeJá e os provedores. A clínica autoriza a SaúdeJá, no contrato, a usar esses suboperadores (art. 39).

**Consequências para quem recusa ou revoga.** O sistema inteiro passa pelo Space e pelo runner. O paciente que recusa é atendido fora do sistema, pelo agendamento da própria clínica, e nenhum dado dele é cadastrado. A LGPD admite condicionar o serviço ao tratamento, desde que o titular seja informado com destaque (art. 9º, § 3º), mas a recusa não pode impedir o atendimento médico: só o uso do sistema. A revogação equivale a um pedido de eliminação, porque o sistema não consegue tratar o paciente sem transferir os dados.

**Limites:**

- o consentimento é revogável a qualquer momento, e a operação depende de cada titular manter o seu;
- na conta do funcionário, o consentimento é frágil: ele é pedido ao empregado de uma clínica que adotou o sistema, e um empregado nem sempre tem liberdade real para recusar. Fica como risco residual (subseção 9.2, item 26);
- consentimento não substitui contrato de operador: GitHub e Hugging Face continuam sem DPA (subseção 4.1).

### 4.3 Migração do remote do DVC para Brazil South

Executada em 09/10/2026. Os identificadores da conta, do container e da URL ficam só no `.env` e nos secrets, nunca neste documento.

**Quadro 6C – Configuração da conta de armazenamento do remote**

| Configuração | Valor | Motivo |
|---|---|---|
| Região | Brazil South | Elimina a transferência internacional do armazenamento |
| Redundância | LRS | GRS e RA-GRS copiariam cada blob para a região pareada, South Central US, nos Estados Unidos |
| Serviço e tipo | Blob Storage, `StorageV2`, sem namespace hierárquico | O DVC endereça por hash e não usa pastas |
| Camada de acesso | Hot | O remote é lido todo dia, e a camada Cool cobra exclusão antecipada de blobs com menos de 30 dias |
| Acesso de rede | Público, de todas as redes | Os runners do GitHub mudam de IP a cada execução; uma lista de IPs exigiria credencial capaz de alterar a rede da conta. A proteção fica na credencial (subseção 9.1, item 12) |
| Segurança | Só HTTPS, TLS mínimo 1.2, sem acesso anônimo a blob, sem replicação entre tenants | Proteção em trânsito e contra exposição pública |
| Proteção de dados | Soft delete de blob de 1 dia, versionamento de blob e change feed desligados | Seção 5.1. A confirmar com `az storage account blob-service-properties show` |

Fonte: elaboração própria (2026).

**Passos executados:**

1. **Conta nova** em Brazil South, com o container e o prefixo de mesmo nome dos anteriores. Por isso `DVC_REMOTE_URL` e o `.dvc/config.local` não mudaram: a conta é lida só da credencial (`BlobEndpoint` da connection string).
2. **Cópia integral** do prefixo do remote, com `azcopy copy` de conta para conta e SAS temporárias. A cópia não precisou filtrar por retenção: o dataset de treino só teve uma versão no histórico do git, idêntica à semente sintética, então o remote antigo nunca guardou linha de titular.
3. **SAS novas, assinadas com chaves diferentes**: leitura (key1, `rl`) no secret de repositório e no `.env` de quem desenvolve; escrita do re-treino (key2, `rlcw`) no environment `production`. Nenhuma tem permissão de exclusão.
4. **Chaves da conta antiga rotacionadas**, o que invalidou todas as SAS emitidas por ela. O CI em `dev` passou depois da rotação, baixando o modelo da conta nova.

**Pendente:**

5. Acompanhar as execuções agendadas do job D-2 e do canário em produção, que usam a mesma SAS de leitura. O job D-2 não tem disparo de teste em dev: ele roda só no environment `production` e processa a fila real.
6. Testar a SAS de escrita com um upload avulso, antes do re-treino de 01/11/2026.
7. Apagar a conta antiga. Depois de apagada, ela ainda pode ser recuperada por até 14 dias, se nenhuma outra com o mesmo nome for criada. Como ela nunca guardou dado de titular, esse prazo não afeta a retenção.

## 5 RETENÇÃO E TÉRMINO DO TRATAMENTO

Pelo princípio da necessidade (art. 6º, III) e pelas regras de término do tratamento (arts. 15 e 16), nenhum dado fica guardado por tempo indeterminado sem justificativa.

**Quadro 7 – Retenção por tabela ou artefato**

| Tabela ou artefato | Retenção | Execução | Justificativa |
|---|---|---|---|
| `eventos_app` | 90 dias (`OBSERVABILIDADE_RETENCAO_DIAS`) | `purgar_eventos_antigos()`, no job diário | Sem dado pessoal. O limite vem do teto do plano gratuito (ADR-006), não da LGPD |
| `predicoes` | 90 dias (`RETENCAO_DADOS_DERIVADOS_DIAS`) | `purgar_dados_derivados_antigos()`, no job diário | Dado sensível derivado. Rastreabilidade e auditoria do modelo: cobre o canário (até 21 dias, mais o desfecho; ADR-008) e a janela de 30 dias do SLO de explicação, com folga para investigar um modelo promovido. A janela não deve crescer sem justificativa nova |
| `mensagens_disparadas` | 90 dias (mesma variável) | Idem | Auditoria da decisão do modelo: registra, por agendamento, se o lembrete foi enviado ou não. Falta base para guardar o registro depois da revogação do consentimento (subseção 9.2, item 14) |
| `pacientes`, `agendamentos` | Sem purga por iniciativa da SaúdeJá | — | O registro do atendimento pertence à clínica, e a exclusão é feita a pedido dela (seção 6) |
| Semente do dataset de treino | Sem prazo | — | Dado sintético, sem titular. Tem uma única versão, sempre vigente, e o workflow de purga nunca a apaga |
| Linhas de produção do dataset de treino, inclusive os recortes `train_raw.pkl` e `test.pkl` derivados dele | Entram no export até 90 dias depois da consulta. Cada versão do dataset é apagada 90 dias depois de deixar de ser vigente em `main`. Máximo de cerca de 210 dias no remote | Workflow de purga ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)), **não implementado** (subseção 9.2, item 20) | Melhoramento do algoritmo, com consentimento (seção 3), e auditoria do modelo promovido. Um modelo mais antigo continua reversível pelo `model.pkl`, mas não pode mais ser retreinado com os mesmos dados |
| `model.pkl` e artefatos do canário | A versão vigente não expira. As demais são apagadas 90 dias depois de deixarem de ser vigentes | Workflow de purga ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)), **não implementado** | Rollback do modelo ([SLO](../SLO.md), seção 5): o campeão anterior deixa de ser vigente na promoção e fica disponível por 90 dias. Não contém linha de titular |
| `auth.users` | Enquanto durar o vínculo do funcionário com a clínica | Manual, pelo administrador, a pedido da clínica no desligamento | A conta de um ex-funcionário daria acesso indevido a nomes de pacientes |
| Sessões e auditoria de login do Supabase Auth | Definida pelo provedor | Provedor | Podem conter IP e user agent (subseção 9.2, item 18) |
| `eventos_consentimento` | Enquanto o paciente existir. Depois da eliminação, 365 dias sem vínculo com o cadastro | Workflow de purga ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)), **não implementado** | Prova de que o consentimento existia e de quando foi revogado (art. 8º, § 2º; art. 16, I). Guarda o hash do CPF, sem o qual a prova não se liga ao titular. O prazo é menor que o de prescrição (5 anos) e precisa de validação jurídica e de previsão no contrato com a clínica |
| Backup do banco | Não há | — | O plano gratuito do Supabase não tem backup. Para a LGPD, o que é apagado some de vez; para a disponibilidade, é um risco (subseção 9.2, item 23). No plano Pro, haveria 7 dias de backup diário, e um dado apagado continuaria no backup por esse prazo |
| Versões no remote do DVC apagadas pela purga | 1 dia (soft delete de blob) | Provedor | O soft delete de blob está ligado com prazo de 1 dia: um blob apagado pela purga continua recuperável por mais 1 dia e depois é removido pelo Azure. Protege contra uma exclusão por engano, como a do `model.pkl` vigente (ADR-009). O versionamento de blob precisa continuar desligado: ligado, cada exclusão guardaria uma versão anterior sem prazo |
| Cópias locais do DVC (cache e workspace de quem desenvolve) | Cache: só o que o workspace usa. Workspace: até a próxima execução da purga local depois de a versão sair do remote. Versão local nunca publicada: 90 dias | `scripts/purgar_dvc_local.py`, executado por quem desenvolve depois de cada `git pull` e pelo menos uma vez por semana | Medida organizacional, não automática: depende de quem desenvolve executar o script. O atraso máximo declarado é de 7 dias além do remote (subseção 9.2, item 25) |
| Artefato `gate-report` do re-treino (GitHub Actions) | 90 dias (padrão do GitHub para repositório público) | Provedor | Métricas e contagens do gate. Os bloqueios citam até cinco `id_consulta`, que são uuids internos, sem dado do paciente |
| Cópia do banco de produção (`supabase db dump`) | Não é feita | — | A documentação do Supabase recomenda dumps periódicos no plano gratuito, mas um dump guardaria o banco inteiro fora de qualquer purga. Se for necessário num incidente, é apagado ao fim da recuperação |
| Log de envio da Infobip | 3 meses por padrão | Provedor | Contém o telefone e o texto do lembrete. A redução do prazo é gratuita e deve ser pedida ao contratar a conta paga |
| Log de API do Supabase | Definida pelo plano | Provedor | Registra a URL de cada consulta. Não contém dado pessoal em claro (seção 8) |

Fonte: elaboração própria (2026).

As purgas rodam no job diário que já existe, sem agendador novo, pelo mesmo critério do ADR-006. Uma falha de purga não derruba o job, e a janela de 90 dias absorve uma execução perdida. Apagar `predicoes` remove também a explicação SHAP, o que não afeta o [SLO](../SLO.md) (seção 4, medido em janelas de 24 h, 7 d e 30 d), a fila do dia nem o re-treino: o re-treino lê o desfecho de `agendamentos.status` e o lembrete de `agendamentos.lembrete_enviado`, que não são purgados.

### 5.1 Cópias e backups

Um dado só é eliminado quando todas as suas cópias são apagadas. O quadro compara cada cópia com a retenção do dado principal.

**Quadro 7A – Retenção das cópias**

| Cópia | Dado principal | Atraso da cópia | Cumpre? |
|---|---|---|---|
| Soft delete de blob no remote do DVC | Versões do dataset e dos recortes no remote | 1 dia | Sim, com o atraso declarado. Depende do workflow de purga do ADR-009: até ele existir, nenhuma versão é apagada, e o problema está no dado principal, não na cópia (subseção 9.2, item 20) |
| Cache local do DVC | Idem | Nenhum: o cache local guarda menos que o remote | Sim, se a purga local for executada |
| Workspace local (`data/consultas-treino.csv`, `data/interim/*.pkl`) | Idem | Até 7 dias | Sim, com o atraso declarado, se a purga local for executada na frequência combinada |
| Backup do Supabase | `pacientes`, `agendamentos`, `predicoes`, `mensagens_disparadas`, `eventos_app` | Não há backup | Sim. A exclusão no banco é definitiva |
| Runner do GitHub e Hugging Face Space | Tudo o que processam | Nenhum: o disco é descartado ao fim da execução ou no restart | Sim |
| Log de envio da Infobip | `mensagens_disparadas` (90 dias) | Até 3 meses depois do envio, e até 3 meses depois de uma eliminação | Na expiração, sim: 3 meses equivalem aos 90 dias. Na eliminação, não: o log do titular fica no provedor até vencer, salvo pedido de exclusão à Infobip |
| Log de API do Supabase e log do GitHub Actions | — | Prazo do provedor | Não guardam dado pessoal em claro (seção 8) |
| MLflow local (`mlruns/`, `data/mlflow.db`) e imagens Docker | — | — | Fora do escopo: o MLflow registra parâmetros, métricas e o `model.pkl`, e nenhuma imagem copia o dataset (`.dockerignore`) |

Fonte: elaboração própria (2026).

**Conclusão.** Com o soft delete de 1 dia e a purga local, as cópias passam a seguir a retenção do dado principal, com atrasos declarados de 1 dia (remote) e até 7 dias (máquinas de quem desenvolve). Restam três lacunas: o workflow de purga do remote ainda não existe (ADR-009), a purga local depende de disciplina de quem desenvolve, e o log da Infobip sobrevive à eliminação do titular.

## 6 DIREITOS DO TITULAR

O titular paciente exerce seus direitos pela clínica, que aciona a SaúdeJá. O funcionário exerce os direitos sobre a própria conta diretamente com a SaúdeJá (seção 11).

**Quadro 8 – Atendimento aos direitos do titular (art. 18)**

| Direito | Como é atendido | Limitação |
|---|---|---|
| Confirmação e acesso (I, II) | Consulta pelo `id_paciente_externo`, recalculado a partir do CPF | Busca por nome não identifica o titular com segurança (homônimos, grafia) |
| Correção (III) | Alteração direta em `pacientes` ou `agendamentos` | — |
| Portabilidade (V) | Export das linhas do titular | Sem ferramenta dedicada |
| Eliminação (VI) | Pedido em `pedidos_titular`, confirmado pela clínica e executado pelo workflow de purga: `delete` em `pacientes` (o cascade remove agendamentos, predições e mensagens) e exclusão das versões do dataset que contêm o titular. Os eventos de consentimento ficam 365 dias sem vínculo, como prova ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)) | **Não implementado**: hoje é operação manual no banco (seção 9, item 4) |
| Revogação do consentimento (IX) | Evento `revogado` em `eventos_consentimento`. No treino, o workflow de purga também apaga as versões do dataset que contêm o titular ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)) | **Não implementado** (subseção 9.2, itens 13 e 19) |
| Oposição (§ 2º) | Vale para os dados tratados por legítimo interesse (Quadro 5). Avaliada caso a caso | Sem procedimento definido |
| Revisão de decisão automatizada (art. 20) | A clínica, controladora da decisão, recebe o pedido, revisa e responde; a SaúdeJá dá apoio técnico pelo canal do encarregado ([PROCEDIMENTO-REVISAO.md](PROCEDIMENTO-REVISAO.md); roteiro na tela "Fila do dia"). A explicação SHAP de cada predição (SLO, seção 4: 100% de cobertura) é o insumo da revisão, não a revisão. A decisão é só enviar ou não um lembrete, e não nega, condiciona nem encarece o atendimento | Depois de 90 dias, a predição é apagada e só os critérios gerais do modelo podem ser explicados. O risco é baixo pelo tipo de decisão, não por precisão do modelo |

Fonte: elaboração própria (2026).

## 7 MEDIDAS DE SEGURANÇA

O quadro lista apenas medidas implementadas e verificadas (art. 46).

**Quadro 9 – Medidas técnicas de segurança**

| Medida | Onde | Garantia |
|---|---|---|
| Minimização no schema | `supabase/migrations/` | CPF e e-mail de paciente não têm coluna. `telefone` e `nome_completo` são autorizados só na migration que os introduziu (`test_coerencia_repo.py::test_migrations_sql_sem_coluna_proibida_de_pii`) |
| Pseudonimização | `src/ui/logic.py::_id_paciente_externo_de_cpf` | O CPF vira hash sha256 antes de chegar ao banco |
| Idade não persistida | `src/features.py::calcular_idade` | Guarda-se `data_nascimento`; a idade é calculada na predição |
| Colunas explícitas na fila do dia | `repositories._COLUNAS_FILA_DO_DIA` | Coluna nova de `pacientes` só chega à UI por decisão explícita |
| Prompt do LLM sem dado pessoal | `src/explain.py::contexto_sem_pii` | Sanitização feita pela classe-base, não por cada implementação |
| RLS em todas as tabelas, sem policies | Migrations | Só a chave secreta do backend lê dados; vazar a URL do projeto não dá acesso |
| Login da equipe | `src/ui/logic.py::autenticar_funcionario`, [architecture.md](../architecture.md#7-security-considerations), seção 7 | Telas só depois de e-mail e senha. Cadastro fechado, sessão de 30 min, mensagem de erro que não revela se o e-mail existe (`test_ui_smoke.py`, `test_db.py`) |
| Segredos fora do git | `.env.example` | Documenta variáveis, nunca valores, incluindo a URL do remote do DVC |
| Credencial mínima do remote do DVC | Secrets do GitHub e `.env` local | SAS de container, nunca account key: leitura (`rl`) em todo lugar, escrita sem exclusão (`rlcw`) só no re-treino (`test_coerencia_repo.py::test_workflows_usam_sas_do_azure_e_so_o_retreino_escreve`) |
| TLS em trânsito | HTTPS do Space; `supabase-py` | — |
| Criptografia em repouso | Supabase e Azure Blob Storage | Herdada do provedor (seção 9, item 7) |
| Log sem dado pessoal | `src/logging_config.py` | Seção 8 |
| Observabilidade sem dado pessoal | `src/observabilidade.py` | `detalhe` com campos permitidos fixos. Exceção registra só o nome da classe |
| Erro de provedor sem dado pessoal | `src/messaging/client.py::ErroEnvioInfobip` | Guarda status HTTP e `messageId`, não o corpo da resposta, que ecoava o telefone |

Fonte: elaboração própria (2026).

## 8 AUSÊNCIA DE DADO PESSOAL EM LOGS

O [SLO](../SLO.md) (seção 6) exige zero ocorrência de dado pessoal em log. Como o log de execução do Hugging Face Space é efêmero, apagado a cada restart e sem busca, não há log de produção para auditar. A garantia é preventiva, em três camadas ([ADR-006](../adr/adr-006-observabilidade.md)):

a) **filtro de redação** (`src/logging_config.py`), aplicado a todos os handlers do processo, inclusive os do `uvicorn` e do `streamlit`, que geram a maior parte do log real. Dados com formato reconhecível (CPF, telefone, e-mail, IP) são detectados por padrão. O nome não tem formato, então o filtro reconhece a chave que o anuncia (`nome=`, `"nome":`, `extra={"nome": ...}`);

b) **guarda estática** (`tests/test_coerencia_repo.py::test_nenhuma_chamada_de_log_em_src_referencia_campo_de_pii`), que percorre a AST de `src/*.py` e falha se alguma chamada de log referenciar campo proibido. A guarda tem controle negativo próprio;

c) **varredura de log** (`scripts/auditoria_lgpd.py`), usada no smoke test do deploy e na auditoria de logs do GitHub Actions. Usa as mesmas regras do filtro (`REGRAS_DE_PII`), sem cópia.

Quando a redação falha, a linha é substituída por um marcador, em vez de sair sem redação. A regra numérica erra para o lado de redigir a mais: o IP de cliente também é dado pessoal. Os scripts do pipeline de treino (`preprocess`, `train`, `validate`) ficam fora do escopo, porque processam só o dataset pseudonimizado e a saída deles é lida no terminal e no MLflow.

### 8.1 Logs dos provedores

O filtro de redação só alcança o que passa pelo processo Python. Os provedores guardam o próprio log, do lado deles:

a) **log de API do Supabase**: registra a URL de cada requisição ao PostgREST, e o valor de um filtro de consulta viaja nessa URL (`?coluna=eq.valor`). O corpo de um POST (insert, upsert, `rpc`) não entra. Hoje as consultas filtram só por uuid interno, status e data, e nenhuma por dado pessoal. A guarda `tests/test_coerencia_repo.py::test_nenhuma_consulta_ao_supabase_filtra_por_dado_pessoal` falha se alguma consulta em `src/` filtrar por `id_paciente_externo`, `telefone`, `nome_completo`, `data_nascimento`, CPF ou e-mail. Uma busca por dado pessoal tem de ir no corpo de uma chamada `rpc()` a uma função do Postgres. O IP registrado é o do servidor da aplicação ou do runner, não o do paciente;

b) **log do Postgres**: no Supabase, o padrão é `log_statement = 'ddl'`, que registra só mudanças de schema, além dos erros. Um erro de restrição pode citar o valor da chave violada; a única chave única com dado pessoal é `id_paciente_externo` (hash), gravada por upsert, que não gera esse erro. Ligar `pgaudit` ou `log_statement = 'all'` passaria a registrar os valores das consultas, e não deve ser feito nas tabelas de paciente;

c) **log do Supabase Auth**: guarda o e-mail e o IP do funcionário em cada login. É inerente à autenticação (subseção 9.2, item 18);

d) **log da Infobip**: guarda o telefone e o texto do lembrete por 3 meses (seção 5). O texto não cita a especialidade (Quadro 4).

**Requisitos para uma futura consulta ao banco feita por LLM** (seção 12, item c). Valem os mesmos princípios, aplicados na estrutura:

- a ferramenta usa um papel próprio do Postgres, só de leitura, com acesso a views sem colunas de dado pessoal, nunca a chave secreta do backend nem SQL livre;
- busca de paciente específico só por `rpc()`, com o valor no corpo;
- o resultado passa por `explain.contexto_sem_pii` antes de chegar ao prompt, como já acontece na explicação em texto;
- o registro de uso vai para `eventos_app`, com a lista fechada de campos do [ADR-006](../adr/adr-006-observabilidade.md), sem a pergunta nem a resposta.

## 9 RISCOS RESIDUAIS E PENDÊNCIAS

Cada item deve constar do slide de risco do pitch.

1. **Transferência internacional das linhas de produção do dataset** (seção 4). O armazenamento saiu do exterior: o remote do DVC está em Brazil South desde 09/10/2026 (subseção 4.3). O processamento continua fora: o re-treino lê essas linhas no runner do GitHub. Mitigada pelo conteúdo, não eliminada.
2. **Reidentificação direta em caso de vazamento** ([ADR-007](../adr/adr-007-nome-do-paciente.md)). O banco expõe nome, telefone, especialidade e desfecho. O pitch não pode afirmar "não temos nome de paciente". A formulação correta é: "temos nome, restrito à tela da equipe, com sete saídas fechadas e testadas" (Quadro 4).
3. **Tela da recepção visível na sala de espera.** A fila do dia mostra nomes. A mitigação é um aviso na tela, não um mecanismo: a posição do monitor é decisão da clínica. A opção de abreviar o nome foi recusada por custo de operação (ADR-007).
4. **Eliminação de titular manual.** O `on delete cascade` funciona, mas não há interface nem script. É aceitável em protótipo, não em operação real. Resolvido por desenho no [ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md), ainda não implementado.
5. **Linhas de produção em versões antigas do dataset.** O risco começa no primeiro `dvc push` depois de um export com desfecho real: a partir daí, cada versão no remote guarda esses pacientes. Enquanto só existir a semente sintética, não há titular exposto. Com o [ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md), as versões expiram 90 dias depois de substituídas, e a eliminação apaga na hora as versões que contêm o titular. Fica o custo: commits antigos passam a apontar para blobs que não existem, e o `dvc pull` neles falha.
6. **Autenticação sem autorização** (desde 28/09/2026; [architecture.md](../architecture.md#7-security-considerations), seção 7). Cada pessoa que vê nomes tem conta individual, criada só pelo administrador. Criar conta apenas para quem assinou o termo de confidencialidade é processo da clínica. Continuam abertos:
   - não há RBAC nem autorização no banco: toda conta vê tudo, e o RLS não contém um bug da UI;
   - o login da equipe pode ser travado de fora, porque o limite de tentativas do Supabase Auth é por IP e todo login chega pelo IP do servidor;
   - a tela aberta não se fecha sozinha: a sessão expira em 30 min, mas a fila continua visível até alguém interagir;
   - a API `/predict` não tem autenticação; não lê o banco nem devolve dado de paciente;
   - o cadastro fechado no projeto remoto é configuração manual, feita no painel do Supabase no primeiro deploy e não versionada: se for desfeita por engano, nada no repositório detecta.
7. **Criptografia em repouso herdada do provedor**, sem verificação própria.
8. **Ausência de validação jurídica** deste documento.
9. **Escopo de sensibilidade.** Se o produto passar a aceitar motivo da consulta, diagnóstico ou medicação, esta análise deve ser refeita por inteiro.

### 9.1 Riscos acrescentados pelo CI/CD

10. **Log público do job D-2.** O repositório e o log do GitHub Actions são públicos, e o job processa telefone. O filtro de redação (seção 8) passa a ser a última barreira. O job registra só contadores e `id_agendamento`, e o CI varre o log da imagem de deploy. Fica sem mitigação um bug de terceiro que registre o corpo de uma requisição antes de o filtro ser instalado.
11. **Processamento por mais um operador fora do Brasil**: o runner do GitHub (seção 4).
12. **Credencial do remote do DVC.** CI, deploy, job D-2, canário e a máquina de quem desenvolve usam SAS só de leitura do container, assinada com a key1. A escrita fica só no re-treino, com SAS assinada com a key2, que cria e grava arquivos mas não apaga. A account key não chega a nenhum workflow nem ao `.env` local, e PR de fork não recebe secrets. Um vazamento da SAS de escrita se resolve rotacionando a key2, sem derrubar a leitura.

### 9.2 Riscos acrescentados pela revisão das bases legais

13. **Consentimento sem prova nem revogação** (seção 3). Não há registro de quando e como o consentimento foi dado, nem campo de revogação consultado pelo job D-2. Hoje o lembrete seria disparado para quem revogou. Decidido, ainda não implementado: tabela `eventos_consentimento` e filtro no job D-2, ligado só quando a coleta entrar na tela de agendamento ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)).
14. **Retenção de `mensagens_disparadas` após a revogação** (seção 5). A auditoria do modelo é finalidade da operação, não do titular. Ou a retenção de 90 dias ganha base própria, ou a revogação deve apagar ou anonimizar os registros do titular.
15. **Legítimo interesse sem teste de balanceamento documentado** (art. 10), tanto para os dados de paciente quanto para a conta do funcionário. A ANPD pode exigir relatório de impacto (art. 10, § 3º).
16. **Transferência internacional sem mecanismo implementado** (seção 4). Verificado em 09/10/2026: nenhuma das contas atuais tem as cláusulas-padrão da ANPD, e só o Supabase tem DPA (subseção 4.1). Decidido: remote do DVC para Brazil South, migrado em 09/10/2026, e consentimento do art. 33, VIII, para GitHub e Hugging Face (subseção 4.2). A coleta do consentimento ainda não foi implementada.
17. **Pacientes menores de idade** (art. 14). O sistema não distingue menores, e não está definido de quem é o `telefone` cadastrado: do adolescente ou do responsável. No segundo caso, é dado de um terceiro que não está no inventário. Para criança, o consentimento do treino deve ser dado por pelo menos um dos pais ou pelo responsável legal (art. 14, § 1º).
18. **Registros do Supabase Auth.** Podem guardar IP e user agent do funcionário. A confirmar e, se confirmado, incluir no Quadro 2.
19. **Consentimento do treino sem mecanismo** (seção 3). `src/export_treino.py` exporta todo desfecho registrado, sem filtro de consentimento, e não há campo para registrar consentimento ou revogação. Decidido, ainda não implementado: a mesma tabela de eventos e um filtro no export, ligado junto com a coleta ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)).
20. **Retenção de 90 dias do DVC não implementada** (seção 5). O export não limita a janela de consultas, e as versões antigas não são apagadas do remote. A SAS de escrita do re-treino continua sem permissão de exclusão (item 12); a exclusão fica com um workflow próprio, com SAS separada, restrito a `main` ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)).
21. **Modelo compartilhado entre clínicas** (seção 1). Enquanto não houver `id_clinica` no schema nem decisão sobre um modelo por clínica, o treino deve ser tratado como finalidade da SaúdeJá, que é controladora desse tratamento. O termo de consentimento coletado pela clínica precisa citar a SaúdeJá e essa finalidade.
22. **Contas incompatíveis com operação real** (subseção 4.1). O Azure for Students se destina a uso educacional e é desativado quando o crédito acaba; o teste gratuito da Infobip não envia SMS a paciente; GitHub Free e Hugging Face Pro não têm DPA. Adequado ao protótipo, não a pacientes reais.
23. **Banco sem backup** (seção 5). O plano gratuito do Supabase não tem backup: um incidente que apague dados não tem recuperação.
24. **Política de privacidade não publicada.** Há um rascunho em [POLITICA-DE-PRIVACIDADE.md](POLITICA-DE-PRIVACIDADE.md), com as condições para publicação. O campo `politica.url` do frontmatter aponta para este documento, que não serve de compromisso externo, e o aviso exibido na tela de agendamento ainda não menciona o treino do modelo, a transferência internacional nem o canal do encarregado.
25. **Purga local depende de quem desenvolve** (subseção 5.1). `scripts/purgar_dvc_local.py` aplica a retenção nas cópias locais do DVC, mas só quando é executado. Nada no repositório detecta uma máquina que nunca o executa. Mitigação: restringir o acesso de leitura ao remote a quem precisa e revogar a credencial de quem sai do projeto.
26. **Consentimento como mecanismo de transferência** (subseção 4.2). É revogável a qualquer momento, e a revogação tira o paciente do sistema. Na conta do funcionário, a liberdade de recusar é questionável. O mecanismo resolve a base legal da transferência, mas não a falta de contrato de operador com GitHub e Hugging Face.

## 10 INCIDENTES

Uma exposição de dado pessoal é comunicada à clínica e ao encarregado em até 72 h da ciência ([SLA](../SLA.md), seção 6; art. 48). Nos dados de paciente, a SaúdeJá, como operadora, informa o controlador o que foi exposto, quantos titulares foram afetados, quando e o que foi feito; a comunicação à ANPD e aos titulares cabe à clínica. Nos dados de conta do funcionário, a SaúdeJá é controladora e faz ela mesma essa comunicação.

Para investigar, a SaúdeJá tem `eventos_app` (90 dias, sobrevive ao restart do Space), `mensagens_disparadas` (90 dias) e o histórico do GitHub Actions. Não há log de aplicação retido (seção 8; ADR-006, risco 2).

## 11 CONTATO

- **Encarregado de Dados (DPO):** `dpo@saudeja.com.br`. O endereço é fictício, porque a SaúdeJá é a empresa fictícia do [BRIEFING](../BRIEFING.md). Substituir por um contato real antes de qualquer uso externo.
- **Responsável técnico:** Vanessa Hoysan Lin (ML Engineer, time de Produto).

## 12 REVISÃO

Revisar este documento quando:

a) uma coluna nova entrar em `supabase/migrations/`;

b) a região de qualquer armazenamento mudar;

c) uma integração externa nova passar a receber dado de paciente. O LLM opcional (TrueFoundry) é o próximo candidato;

d) o [SLO](../SLO.md), o [SLA](../SLA.md) ou o [architecture.md](../architecture.md) mudarem;

e) a data de `expira`, no frontmatter, se aproximar.

O frontmatter segue o JSON Schema do lgpd.md (Lgpd.md, 2025) e deve ser atualizado junto com o corpo: `bases_legais` espelha o Quadro 5, e `transferencia_internacional` espelha o Quadro 6.

**Quadro 10 – Histórico de revisões**

| Data | Seções | Alteração |
|---|---|---|
| 27/09/2026 | Todas | Primeira versão, junto da redação de dados pessoais no log |
| 28/09/2026 | 2, 2.1, 6, 7, 9 | Nome do paciente gravado e visível à equipe (ADR-007) |
| 28/09/2026 | 2, 7, 9 | Login da equipe |
| 30/09/2026 | 4, 9.1 | Job D-2 no GitHub Actions e log público |
| 09/10/2026 | 5, 10 | Retenção de `predicoes` e `mensagens_disparadas` reduzida de 365 para 90 dias |
| 09/10/2026 | 1, 3, 4, 5, 6, 9 | Camada de controladora da SaúdeJá no treino de modelo compartilhado; consentimento em tabela de eventos; retenção de 90 dias no DVC; workflow de purga (ADR-009) |
| 09/10/2026 | 1, 2, 3, 4, 5, 6, 9 | Semente sintética separada das linhas de produção do dataset; consentimento (art. 11, I) para o treino com desfechos reais; retenção de 90 dias das linhas de produção |
| 09/10/2026 | Todas | Frontmatter lgpd.md; SaúdeJá controladora da conta do funcionário; classificação pelo registro de agendamento; bases legais por dado; consentimento no lembrete; art. 33, II, b; subseção 9.2; formatação ABNT |
| 09/10/2026 | 2.1, 4, 4.1, 5, 6, 8.1, 9.2 | DPA por conta e mecanismo de transferência em aberto; Space e Infobip no Quadro 6; custo de levar job e aplicação para o Brasil; SMS sem especialidade; eventos de consentimento retidos 365 dias após a eliminação; backup e logs de provedores na retenção; procedimento de revisão do art. 20; guarda contra filtro por dado pessoal nas consultas ao Supabase; requisitos para consulta por LLM |
| 09/10/2026 | 4, 4.2, 4.3, 5, 5.1, 9.2 | Remote do DVC para Brazil South (decidido) e consentimento do art. 33, VIII, para GitHub e Hugging Face; re-treino incluído no processamento do runner; soft delete de blob de 1 dia; purga das cópias locais do DVC (`scripts/purgar_dvc_local.py`); verificação das cópias e backups contra a retenção |
| 09/10/2026 | Frontmatter, 4, 4.2, 4.3, 7, 9, 9.1, 9.2 | Remote do DVC migrado para Brazil South (conta nova, LRS, SAS novas, chaves da conta antiga rotacionadas); a conta antiga nunca guardou dado de titular; o armazenamento sai do Quadro 6 |

Fonte: elaboração própria (2026).

<h2 align="center">REFERÊNCIAS</h2>

BRASIL. Autoridade Nacional de Proteção de Dados. **Resolução CD/ANPD nº 19, de 23 de agosto de 2024**. Aprova o Regulamento de Transferência Internacional de Dados e o conteúdo das cláusulas-padrão contratuais. Diário Oficial da União: seção 1, Brasília, DF, 23 ago. 2024. Disponível em: https://www.gov.br/anpd/pt-br/acesso-a-informacao/institucional/atos-normativos/regulamentacoes_anpd/resolucao-cd-anpd-no-19-de-23-de-agosto-de-2024. Acesso em: 9 out. 2026.

BRASIL. **Lei nº 13.709, de 14 de agosto de 2018**. Lei Geral de Proteção de Dados Pessoais (LGPD). Brasília, DF: Presidência da República, [2019]. Disponível em: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm. Acesso em: 9 out. 2026.

GITHUB. **GitHub Data Protection Agreement**. [*S. l.*]: GitHub, out. 2025. Disponível em: https://github.com/customer-terms/github-data-protection-agreement. Acesso em: 9 out. 2026.

INFOBIP. **Free trial**. [*S. l.*]: Infobip, 2026a. Disponível em: https://www.infobip.com/docs/essentials/getting-started/free-trial. Acesso em: 9 out. 2026.

INFOBIP. **Data retention notice**. [*S. l.*]: Infobip, 2026b. Disponível em: https://www.infobip.com/policies/data-retention-notice. Acesso em: 9 out. 2026.

LGPD.MD. **Especificação lgpd.md**: versão 1.0. [*S. l.*]: lgpd.md, 2025. Disponível em: https://lgpd.md/spec. Acesso em: 9 out. 2026.

MICROSOFT. **Microsoft Products and Services Data Protection Addendum**. [*S. l.*]: Microsoft, 22 maio 2026a. Disponível em: https://www.microsoft.com/licensing/docs/view/Microsoft-Products-and-Services-Data-Protection-Addendum-DPA. Acesso em: 9 out. 2026.

MICROSOFT. **Azure for Students**: offer details. [*S. l.*]: Microsoft, 2026b. Disponível em: https://azure.microsoft.com/en-us/pricing/offers/ms-azr-0170p. Acesso em: 9 out. 2026.

SUPABASE. **Data Processing Addendum**. [*S. l.*]: Supabase, jun. 2026. Disponível em: https://supabase.com/legal/dpa. Acesso em: 9 out. 2026.
