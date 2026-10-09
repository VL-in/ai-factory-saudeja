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
    - "Chile — Azure Blob Storage, região Chile Central (linhas de produção do dataset de treino, pseudonimizadas, e model.pkl)"
    - "Fora do Brasil — runner hospedado do GitHub Actions (processamento do job D-2, sem gravação persistente)"
    - "Fora do Brasil — Hugging Face Space (aplicação)"
  mecanismo: "Art. 33, II, b — cláusulas-padrão contratuais (adoção do texto da Resolução CD/ANPD nº 19/2024 pelos provedores a confirmar)"
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
| SMS via Infobip | A consulta do job D-2 não traz a coluna |
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

O banco de produção (Supabase) fica em São Paulo (`sa-east-1`): pacientes, agendamentos, predições, mensagens e eventos ficam armazenados no Brasil. Três tratamentos acontecem fora do país.

**Quadro 6 – Tratamento de dados fora do Brasil**

| Destino | Dados | Natureza |
|---|---|---|
| Azure Blob Storage, região Chile Central (remote do DVC) | Linhas de produção do dataset de treino (`data/consultas-treino.csv`, com os desfechos reais registrados pela clínica) e `model.pkl`. A semente sintética também fica lá, mas não é dado pessoal | Armazenamento persistente; versões retidas por 90 dias depois de substituídas (seção 5) |
| Runner hospedado do GitHub Actions (job D-2, desde 30/09/2026) | Data de nascimento, sexo, especialidade, distância, antecedência, histórico de faltas e telefone. Nunca nome nem CPF | Processamento diário. Só o `model.pkl` é gravado, e é descartado ao fim da execução |
| Hugging Face Space | Dados da aplicação | Processamento |

Fonte: elaboração própria (2026), a partir do [ADR-005](../adr/adr-005-integracoes-implicitas.md).

Para o pitch, a formulação correta é: **os dados armazenados no banco de produção não saem do Brasil, mas o artefato de treino e o processamento diário saem.**

**Enquadramento.** A transferência se apoia no art. 33, II, b (cláusulas-padrão contratuais), por meio do acordo de processamento de dados (DPA) de cada provedor, e é necessária ao re-treino mensal acordado com Produto. Desde 23/08/2025, cláusula contratual só ampara transferência internacional se adotar o texto das cláusulas-padrão aprovado pela Autoridade Nacional de Proteção de Dados (ANPD) ou se tiver cláusula específica aprovada por ela (Brasil, 2024). Falta confirmar se os DPAs de Azure, GitHub e Hugging Face cumprem isso (subseção 9.2, item 16).

**Mitigação pelo conteúdo.** O export (`src/export_treino.py`) nunca inclui telefone, nome ou CPF. Isso é verificado por `tests/test_coerencia_repo.py::test_export_treino_sem_coluna_proibida_de_pii`. O identificador do paciente é o hash do CPF. Restam idade derivada, sexo, especialidade, distância, antecedência e desfecho.

A mitigação tem limite: as linhas de produção pseudonimizadas continuam sendo dado pessoal, porque o art. 12 só equipara a anônimo o dado cuja anonimização é irreversível. Especialidade, desfecho e histórico de faltas continuam sendo dados sensíveis. A pseudonimização reduz a gravidade de um incidente, mas não tira a transferência do escopo do art. 33.

**Alternativa.** Criar um container em Brazil South, apontar `DVC_REMOTE_URL` e `.dvc/config.local` para ele e executar `dvc push`. Nenhum código depende da região ([ADR-005](../adr/adr-005-integracoes-implicitas.md)).

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
| Linhas de produção do dataset de treino | Entram no export até 90 dias depois da consulta. Cada versão do dataset é apagada 90 dias depois de deixar de ser vigente em `main`. Máximo de cerca de 210 dias no remote | Workflow de purga ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)), **não implementado** (subseção 9.2, item 20) | Melhoramento do algoritmo, com consentimento (seção 3), e auditoria do modelo promovido. Um modelo mais antigo continua reversível pelo `model.pkl`, mas não pode mais ser retreinado com os mesmos dados |
| `model.pkl` e artefatos do canário | A versão vigente não expira. As demais são apagadas 90 dias depois de deixarem de ser vigentes | Workflow de purga ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)), **não implementado** | Rollback do modelo ([SLO](../SLO.md), seção 5): o campeão anterior deixa de ser vigente na promoção e fica disponível por 90 dias. Não contém linha de titular |
| `auth.users` | Enquanto durar o vínculo do funcionário com a clínica | Manual, pelo administrador, a pedido da clínica no desligamento | A conta de um ex-funcionário daria acesso indevido a nomes de pacientes |
| Sessões e auditoria de login do Supabase Auth | Definida pelo provedor | Provedor | Podem conter IP e user agent (subseção 9.2, item 18) |

Fonte: elaboração própria (2026).

As purgas rodam no job diário que já existe, sem agendador novo, pelo mesmo critério do ADR-006. Uma falha de purga não derruba o job, e a janela de 90 dias absorve uma execução perdida. Apagar `predicoes` remove também a explicação SHAP, o que não afeta o [SLO](../SLO.md) (seção 4, medido em janelas de 24 h, 7 d e 30 d), a fila do dia nem o re-treino: o re-treino lê o desfecho de `agendamentos.status` e o lembrete de `agendamentos.lembrete_enviado`, que não são purgados.

## 6 DIREITOS DO TITULAR

O titular paciente exerce seus direitos pela clínica, que aciona a SaúdeJá. O funcionário exerce os direitos sobre a própria conta diretamente com a SaúdeJá (seção 11).

**Quadro 8 – Atendimento aos direitos do titular (art. 18)**

| Direito | Como é atendido | Limitação |
|---|---|---|
| Confirmação e acesso (I, II) | Consulta pelo `id_paciente_externo`, recalculado a partir do CPF | Busca por nome não identifica o titular com segurança (homônimos, grafia) |
| Correção (III) | Alteração direta em `pacientes` ou `agendamentos` | — |
| Portabilidade (V) | Export das linhas do titular | Sem ferramenta dedicada |
| Eliminação (VI) | Pedido em `pedidos_titular`, confirmado pela clínica e executado pelo workflow de purga: `delete` em `pacientes` (o cascade remove agendamentos, predições, mensagens e eventos de consentimento) e exclusão das versões do dataset que contêm o titular ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)) | **Não implementado**: hoje é operação manual no banco (seção 9, item 4) |
| Revogação do consentimento (IX) | Evento `revogado` em `eventos_consentimento`. No treino, o workflow de purga também apaga as versões do dataset que contêm o titular ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)) | **Não implementado** (subseção 9.2, itens 13 e 19) |
| Oposição (§ 2º) | Vale para os dados tratados por legítimo interesse (Quadro 5). Avaliada caso a caso | Sem procedimento definido |
| Revisão de decisão automatizada (art. 20) | Toda predição tem explicação SHAP (SLO, seção 4: 100% de cobertura). A decisão é só enviar ou não um lembrete, e não nega, condiciona nem encarece o atendimento | O risco é baixo pelo tipo de decisão, não por precisão do modelo |

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

## 9 RISCOS RESIDUAIS E PENDÊNCIAS

Cada item deve constar do slide de risco do pitch.

1. **Transferência internacional das linhas de produção do dataset** (seção 4). Mitigada pelo conteúdo, não eliminada, e reversível a baixo custo.
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
12. **Credencial do remote do DVC no CI.** CI, deploy e job D-2 usam SAS só de leitura do container. A escrita fica só no re-treino, com SAS que cria arquivos mas não sobrescreve nem apaga. A account key não chega a nenhum workflow, e PR de fork não recebe secrets.

### 9.2 Riscos acrescentados pela revisão das bases legais

13. **Consentimento sem prova nem revogação** (seção 3). Não há registro de quando e como o consentimento foi dado, nem campo de revogação consultado pelo job D-2. Hoje o lembrete seria disparado para quem revogou. Decidido, ainda não implementado: tabela `eventos_consentimento` e filtro no job D-2, ligado só quando a coleta entrar na tela de agendamento ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)).
14. **Retenção de `mensagens_disparadas` após a revogação** (seção 5). A auditoria do modelo é finalidade da operação, não do titular. Ou a retenção de 90 dias ganha base própria, ou a revogação deve apagar ou anonimizar os registros do titular.
15. **Legítimo interesse sem teste de balanceamento documentado** (art. 10), tanto para os dados de paciente quanto para a conta do funcionário. A ANPD pode exigir relatório de impacto (art. 10, § 3º).
16. **DPAs sem verificação das cláusulas-padrão da ANPD** (seção 4).
17. **Pacientes menores de idade** (art. 14). O sistema não distingue menores, e não está definido de quem é o `telefone` cadastrado: do adolescente ou do responsável. No segundo caso, é dado de um terceiro que não está no inventário. Para criança, o consentimento do treino deve ser dado por pelo menos um dos pais ou pelo responsável legal (art. 14, § 1º).
18. **Registros do Supabase Auth.** Podem guardar IP e user agent do funcionário. A confirmar e, se confirmado, incluir no Quadro 2.
19. **Consentimento do treino sem mecanismo** (seção 3). `src/export_treino.py` exporta todo desfecho registrado, sem filtro de consentimento, e não há campo para registrar consentimento ou revogação. Decidido, ainda não implementado: a mesma tabela de eventos e um filtro no export, ligado junto com a coleta ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)).
20. **Retenção de 90 dias do DVC não implementada** (seção 5). O export não limita a janela de consultas, e as versões antigas não são apagadas do remote. A SAS de escrita do re-treino continua sem permissão de exclusão (item 12); a exclusão fica com um workflow próprio, com SAS separada, restrito a `main` ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)).
21. **Modelo compartilhado entre clínicas** (seção 1). Enquanto não houver `id_clinica` no schema nem decisão sobre um modelo por clínica, o treino deve ser tratado como finalidade da SaúdeJá, que é controladora desse tratamento. O termo de consentimento coletado pela clínica precisa citar a SaúdeJá e essa finalidade.

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

Fonte: elaboração própria (2026).

<h2 align="center">REFERÊNCIAS</h2>

BRASIL. Autoridade Nacional de Proteção de Dados. **Resolução CD/ANPD nº 19, de 23 de agosto de 2024**. Aprova o Regulamento de Transferência Internacional de Dados e o conteúdo das cláusulas-padrão contratuais. Diário Oficial da União: seção 1, Brasília, DF, 23 ago. 2024. Disponível em: https://www.gov.br/anpd/pt-br/acesso-a-informacao/institucional/atos-normativos/regulamentacoes_anpd/resolucao-cd-anpd-no-19-de-23-de-agosto-de-2024. Acesso em: 9 out. 2026.

BRASIL. **Lei nº 13.709, de 14 de agosto de 2018**. Lei Geral de Proteção de Dados Pessoais (LGPD). Brasília, DF: Presidência da República, [2019]. Disponível em: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm. Acesso em: 9 out. 2026.

LGPD.MD. **Especificação lgpd.md**: versão 1.0. [*S. l.*]: lgpd.md, 2025. Disponível em: https://lgpd.md/spec. Acesso em: 9 out. 2026.
