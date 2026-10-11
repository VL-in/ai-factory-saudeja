---
# yaml-language-server: $schema=https://lgpd.md/schema.json
# Declaração estruturada no formato lgpd.md (spec 1.0). O corpo abaixo é a análise
# completa; estes campos a resumem e precisam ser mantidos em sincronia com ela.
spec: "1.0"
atualizado: "2026-10-11"
expira: "2027-04-09"
status: "em-adequação"
controlador:
  nome: "SaúdeJá Tecnologia em Saúde S.A."
  contato: "v.lin@gmail.com"
encarregado:
  nome: "Encarregado de Dados (DPO) da SaúdeJá — Vanessa Lin"
  contato: "v.lin@gmail.com"
direitos:
  canal: "v.lin@gmail.com"
  prazo: 15
politica:
  url: "https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/LGPD/LGPD.md"
  idioma: "pt-br"
  atualizada: "2026-10-10"
bases_legais:
  - finalidade: "Identificação do paciente e execução do agendamento (em nome da clínica-cliente, controladora)"
    base: "execução de contrato"
    artigo: "Art. 7, V"
    dados: ["id_paciente_externo (hash sha256 do CPF)", "sexo", "nome_completo", "data_nascimento"]
  - finalidade: "Identificação do paciente no atendimento realizado pela clínica (em nome da clínica-cliente, controladora)"
    base: "tutela da saúde"
    artigo: "Art. 7, VIII"
    dados: ["id_paciente_externo (hash sha256 do CPF)", "sexo", "nome_completo", "data_nascimento"]
  - finalidade: "Contato com o paciente sobre o atendimento agendado, inclusive o lembrete de confirmação por SMS (só data e hora, sem especialidade)"
    base: "execução de contrato"
    artigo: "Art. 7, V"
    dados: ["telefone", "data_hora_agendada", "canal e status de envio do lembrete"]
  - finalidade: "Atributos operacionais usados na previsão de risco de falta"
    base: "legítimo interesse"
    artigo: "Art. 7, IX"
    dados: ["distancia_km", "dias_entre_agendamento_consulta"]
  - finalidade: "Previsão de risco de falta para confirmar a presença do paciente no atendimento agendado"
    base: "tutela da saúde"
    artigo: "Art. 11, II, f"
    dados: ["especialidade", "status do agendamento", "historico_noshow", "data_hora_agendada", "probabilidade de no-show", "explicação SHAP"]
  - finalidade: "Melhoramento do algoritmo de previsão de falta: base de treino separada (schema treino) e re-treino com os desfechos reais de quem consentiu (SaúdeJá controladora, modelo compartilhado entre clínicas)"
    base: "consentimento"
    artigo: "Art. 11, I"
    dados: ["consultas_treino (idade na data da consulta, sexo, especialidade, distância, antecedência, histórico de faltas, data e hora, desfecho, lembrete_enviado)", "pseudônimo HMAC do titular e da consulta", "eventos de consentimento do treino"]
  - finalidade: "Autenticação e segurança de acesso da equipe da clínica (SaúdeJá como controladora)"
    base: "legítimo interesse"
    artigo: "Art. 7, IX"
    dados: ["e-mail de login do funcionário", "hash da senha do funcionário"]
transferencia_internacional:
  transfere: true
  destinos:
    - "Fora do Brasil — runner hospedado do GitHub Actions (job D-2 diário, re-treino mensal sobre a base de treino e purga LGPD, sem gravação persistente)"
    - "Estados Unidos — Hugging Face Space (aplicação: processa nome, CPF antes do hash, telefone e dados da consulta, sem gravação persistente)"
    - "Local a confirmar — Infobip (telefone e texto do lembrete)"
  mecanismo: "Em aberto para os dados da clínica: a tela informa o processamento fora do Brasil numa caixa de ciência antes do cadastro (art. 9º, § 3º), que não é o consentimento do art. 33, VIII; o art. 33, IX não alcança os dados de saúde, e o art. 33, II, b não é atendido pelas contas atuais (seções 4.1 e 4.2). Base de treino: consentimento do treino com o processamento no exterior em destaque no termo (art. 33, VIII). Infobip: candidato ao art. 33, IX (execução de contrato, só telefone, data e hora), a validar com o contrato pago. O remote do DVC está em Brazil South desde 09/10/2026 (seção 4.3)"
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
| Controlador | SaúdeJá Tecnologia em Saúde S.A. | De paciente, na base de treino (schema `treino`) e no dataset de re-treino | Decide a finalidade do treino do modelo compartilhado entre clínicas, coleta o consentimento, atende os pedidos de revogação e eliminação dos dados de treino |
| Controlador | SaúdeJá Tecnologia em Saúde S.A. | De conta do funcionário | Decide como a plataforma autentica quem acessa a fila do dia: Supabase Auth, cadastro fechado e sessão de 30 min |

Fonte: elaboração própria (2026).

**Camadas da SaúdeJá nos dados de paciente.** Na predição, no lembrete e na fila do dia, a SaúdeJá é operadora: trata os dados em nome da clínica, para o atendimento daquela clínica. No treino, a SaúdeJá é controladora: o modelo é compartilhado entre clínicas (o [ADR-008](../adr/adr-008-canario-do-modelo.md) parte do volume somado delas), e os dados da clínica A treinam o modelo que atende a clínica B. Não é controladoria conjunta com a clínica: as finalidades são distintas (o atendimento, para a clínica; o melhoramento do modelo que atende outras clínicas, para a SaúdeJá), e a ANPD afasta a controladoria conjunta quando os objetivos diferem, ainda que sobre o mesmo conjunto de dados (Brasil, 2022). Desde 10/10/2026, os dados de treino ficam numa base separada, o schema `treino`, que só recebe a consulta de quem consentiu, e o termo do consentimento cita a SaúdeJá ([ADR-010](../adr/adr-010-base-de-treino-separada.md)). Se um dia cada clínica tiver o próprio modelo, o treino volta a ser tratamento em nome da clínica, e esta seção precisa ser revista.

**Elementos essenciais propostos pela SaúdeJá.** Pelo art. 5º, VI, controlador é a pessoa "a quem competem as decisões referentes ao tratamento de dados pessoais". O guia da ANPD identifica o controlador pelo poder de decidir os elementos essenciais do tratamento: finalidade, base legal, categorias de titulares e de dados e período de armazenamento. O operador decide só os elementos não essenciais, como as medidas técnicas. O papel decorre dos fatos, e não do nome que o contrato dá às partes (Brasil, 2022). Na predição, no lembrete e na fila do dia, a SaúdeJá definiu vários elementos essenciais ao desenhar o produto (Quadro 1A). A clínica continua controladora desses tratamentos desde que o contrato do SaaS apresente essas decisões e a clínica as aprove. Sem essa aprovação, a SaúdeJá decidiria sozinha elementos essenciais e seria controladora também desses tratamentos (subseção 9.3, item 27). As escolhas técnicas, como Supabase, LightGBM e sessão de 30 min, são elementos não essenciais e não mudam o papel.

**Quadro 1A – Elementos essenciais propostos pela SaúdeJá nos tratamentos da clínica**

| Elemento essencial | Proposta da SaúdeJá | Onde está registrada |
|---|---|---|
| Base legal de cada dado | Quadro 5 | Seção 3 |
| Categorias de dados da predição | Idade (da data de nascimento), sexo, especialidade, histórico de faltas, distância e antecedência do agendamento. Com `features.temporais`, também dia da semana e horário | `src/contrato_features.py` e `params.yaml` |
| Critério da decisão automatizada (art. 20) | Lembrete para quem o modelo prevê falta, com probabilidade igual ou maior que o corte. A clínica aprova o critério; o valor do corte é ajuste técnico dentro dele, controlado pelo gate do re-treino | `decision.threshold` em `params.yaml` |
| Conteúdo do lembrete | SMS só com data e hora, sem especialidade | Quadro 4 |
| Período de armazenamento dos dados derivados | 90 dias para `predicoes` e `mensagens_disparadas` | `RETENCAO_DADOS_DERIVADOS_DIAS` (seção 5) |
| Suboperadores e destinos fora do Brasil | GitHub, Hugging Face e Infobip | Quadro 6; autorização da clínica no contrato (subseção 4.2) |
| Textos apresentados ao paciente | Política de privacidade e Termos de uso exibidos na tela de agendamento, com a caixa de ciência | `docs/LGPD/POLITICA-DE-PRIVACIDADE.md` e `docs/LGPD/TERMOS-DE-USO.md` |

Fonte: elaboração própria (2026).

Consequências:

a) a base legal dos dados de paciente é da clínica (seção 3). A SaúdeJá não cria finalidade nova: os dados de paciente servem à priorização de risco de falta e, com consentimento específico do titular, ao melhoramento do modelo que faz essa priorização (seção 3). No treino, a base legal e o atendimento aos pedidos de revogação e eliminação são responsabilidade da SaúdeJá. A base de treino é pseudonimizada com chave secreta (seção 2);

b) pedidos de titular paciente chegam pela clínica (seção 6). Os pedidos sobre os dados de treino também podem ser feitos diretamente à SaúdeJá;

c) os registros do atendimento (`pacientes`, `agendamentos`) só são apagados a pedido da clínica. Os dados derivados (`predicoes`, `mensagens_disparadas`) são gerados em nome da clínica, com janela de retenção proposta pela SaúdeJá e aprovada pela clínica no contrato de operação (seção 5);

d) nos dados de conta do funcionário, cabem à SaúdeJá a base legal, a retenção, o atendimento ao titular e a comunicação de incidente (seções 3, 5, 6 e 10);

e) os elementos do Quadro 1A só mudam com nova aprovação da clínica. Uma feature nova, outra janela de retenção ou outro texto do lembrete passam pelo contrato antes de entrar em produção.

## 2 INVENTÁRIO E CLASSIFICAÇÃO DOS DADOS

O schema foi desenhado com minimização de dados desde a origem ([architecture.md](../architecture.md), seção 4.1). O CPF nunca é persistido: a tela de cadastro o converte no hash sha256 que identifica o paciente (`src/ui/logic.py::_id_paciente_externo_de_cpf`). O nome completo é gravado desde 28/09/2026 ([ADR-007](../adr/adr-007-nome-do-paciente.md)); ver subseção 2.1.

**Critério de classificação.** Idade, sexo e distância, isolados, são dados pessoais comuns. A sensibilidade vem de dois elementos:

- a `especialidade`, porque saber que alguém consulta cardiologia é informação sobre a saúde dessa pessoa;
- o histórico de comparecimento, registrado em `status` e `historico_noshow`, porque revela quantas vezes a pessoa recebeu ou deixou de receber atendimento de saúde.

**Data e hora da consulta são dado pessoal comum** (decisão de 10/10/2026). Isoladas, `data_hora_agendada` diz quando a pessoa tem um compromisso, mas não a área de saúde nem o histórico de atendimento. É o que permite enviar o lembrete com base na execução do contrato (seção 3): o SMS leva só telefone, data e hora. A classificação depende de o lembrete não revelar a especialidade, nem no texto nem no remetente (subseção 9.4, item 28).

Por isso a classificação é feita pelo registro, não pela coluna isolada: o art. 11, § 1º, aplica o regime de dado sensível a todo tratamento que revele dado sensível. O registro de agendamento continua sensível, porque contém a especialidade; a data e a hora só deixam de ser sensíveis quando tratadas separadas dela, como no lembrete. A predição e a explicação SHAP herdam essa sensibilidade. Toda coluna nova em `agendamentos` ou `predicoes` deve ser tratada como sensível, salvo justificativa em contrário. O sistema não guarda diagnóstico, prontuário, exame nem medicamento, por decisão de desenho: o modelo não precisa deles para prever falta.

**Quadro 2 – Inventário de dados pessoais**

| Dado | Local | Classificação | Observação |
|---|---|---|---|
| `id_paciente_externo` | `pacientes` (Supabase) | Pessoal pseudonimizado (arts. 12 e 13) | Hash sha256 do CPF. Não é anônimo: pode ser revertido por força bruta sobre o espaço de CPFs válidos |
| `sexo`, `data_nascimento` | `pacientes` | Pessoal | A idade é calculada na hora da predição e não é gravada |
| `telefone` | `pacientes` | Pessoal, identificador direto | Exceção à minimização (subseção 2.1) |
| `nome_completo` | `pacientes` | Pessoal, identificador direto | Exceção à minimização (subseção 2.1) |
| `especialidade` | `agendamentos` | Sensível (art. 5º, II) | Revela a área de saúde consultada |
| `status` (`concluido`, `no_show`, `cancelado`), `historico_noshow` | `agendamentos` | Sensível (art. 5º, II; art. 11, § 1º) | Revelam se e quantas vezes a pessoa recebeu ou deixou de receber atendimento de saúde |
| `data_hora_agendada` | `agendamentos` | Pessoal | Isolada, não revela a área de saúde nem o histórico. É o que o lembrete leva, com o telefone. No registro de agendamento, junto da especialidade, segue o regime de dado sensível (art. 11, § 1º) |
| `versao_documentos` | `agendamentos` | Pessoal | Versão da Política de privacidade e dos Termos de uso que o paciente declarou conhecer na tela de agendamento. Prova de informação (art. 9º), não consentimento |
| `distancia_km`, `dias_entre_agendamento_consulta` | `agendamentos` | Pessoal | — |
| Probabilidade de no-show e explicação SHAP | `predicoes` | Sensível derivado (art. 11, § 1º; art. 12, § 2º) | Perfil comportamental calculado a partir de entradas sensíveis. A explicação expõe os valores dessas entradas |
| Canal e status de envio do lembrete | `mensagens_disparadas` | Pessoal | Auditoria do envio do lembrete e da decisão do modelo (seção 5) |
| Latência, contadores, nome de classe de exceção | `eventos_app` | Não pessoal | Nenhuma coluna de dado pessoal, por construção ([ADR-006](../adr/adr-006-observabilidade.md)) |
| Semente do dataset de treino (`data/consultas-historicas.csv`) | Repositório e Azure Blob Storage, via DVC | Não pessoal | 380 consultas sintéticas herdadas, sem titular real. Fixa: nunca recebe linha nova |
| `treino.consultas_treino` | Supabase, schema `treino` (SaúdeJá controladora) | Sensível pseudonimizado (art. 11, § 1º; art. 13, § 4º) | Só a consulta de quem consentiu o treino: idade na data da consulta, sexo, especialidade, distância, antecedência, histórico de faltas, data e hora, desfecho e `lembrete_enviado`. Sem nome, telefone, CPF nem data de nascimento. Titular e consulta identificados por pseudônimo HMAC com chave secreta (`src/pseudonimo.py`), que não se reverte em CPF sem a chave |
| `treino.eventos_consentimento`, `treino.pedidos_titular` | Supabase, schema `treino` | Pessoal pseudonimizado | Prova do consentimento do treino e pedidos de revogação e eliminação, pelo mesmo pseudônimo |
| Linhas de produção do dataset de treino (`data/consultas-treino.csv`) | Azure Blob Storage, via DVC | Sensível pseudonimizado (art. 11, § 1º; arts. 12 e 13) | Exportadas só da base de treino por `src/export_treino.py` e acrescentadas à semente: `id_paciente` e `id_consulta` são os pseudônimos HMAC. Só existem a partir do primeiro export com desfecho real |
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
| `telefone` | V (execução de contrato) | Não se aplica |
| `data_hora_agendada` | V (execução de contrato: agendamento e lembrete) | II, f (tutela da saúde), na predição, em que é tratada junto da especialidade |
| Canal e status de envio do lembrete | V (execução de contrato) | Não se aplica |
| `distancia_km`, `dias_entre_agendamento_consulta` | IX (legítimo interesse) | Não se aplica |
| `especialidade` | Não se aplica | II, f (tutela da saúde) |
| `status`, `historico_noshow` | Não se aplica | II, f (tutela da saúde) |
| Probabilidade de no-show e explicação SHAP | Não se aplica | II, f (tutela da saúde) |
| Base de treino (`treino.consultas_treino`) e linhas de produção do dataset de treino | Não se aplica | I (consentimento específico e destacado, dado à SaúdeJá, para a finalidade de melhoramento do algoritmo) |
| `versao_documentos` (ciência da política e dos termos) | V (execução de contrato) | Não se aplica |
| E-mail de login e hash da senha do funcionário (SaúdeJá controladora) | IX (legítimo interesse) | Não se aplica |

Fonte: elaboração própria (2026), com base em Brasil (2018).

**Tutela da saúde (art. 11, II, f).** A alínea exige que o tratamento seja feito *exclusivamente* para tutela da saúde, em procedimento realizado por serviço de saúde. A predição existe para que a clínica confirme a presença do paciente no atendimento que ele mesmo agendou. A única decisão que ela toma é enviar ou não um lembrete. Usá-la para cobrança, overbooking, encaixe de outro paciente ou recusa de atendimento seria finalidade econômica da clínica, e exigiria rever a base legal antes da implementação.

**Consentimento descartado para a predição.** Na predição, o consentimento do art. 11, I, poderia ser revogado a qualquer momento, e o sistema teria de manter pacientes que agendam mas não podem ser priorizados, sem ganho de proteção. As anotações herdadas (`docs/herdado/notas-camila.md`) previam "consentimento + execução de contrato". A execução de contrato foi mantida para a identificação do paciente e passou a cobrir o lembrete; o consentimento ficou restrito ao treino ([ADR-010](../adr/adr-010-base-de-treino-separada.md)). O agendamento não depende de consentimento nenhum. A divergência é intencional.

**Consentimento no treino (art. 11, I).** Reusar o desfecho real para treinar o modelo é finalidade secundária: não serve ao atendimento daquele paciente, e sim ao melhoramento do algoritmo para atendimentos futuros. Por isso não cabe no "exclusivamente" da alínea f, e se apoia no consentimento do titular ou de seu responsável legal, dado de forma específica e destacada para essa finalidade. A revogação tira o paciente do treino, mas não da predição nem do lembrete, que têm base própria. Como o sistema cumpre as obrigações ([ADR-010](../adr/adr-010-base-de-treino-separada.md)):

a) **coleta separada e registrada**: uma caixa própria no formulário, nunca pré-marcada, com termo curto que traz em destaque o uso fora do Brasil e diz que recusar não impede o agendamento; o detalhe está nos Termos de uso. Cada concessão ou revogação é um evento em `treino.eventos_consentimento`, só de inserção, com finalidade, versão do termo, canal, quem consentiu (titular ou responsável legal) e data do relógio do banco. O vigente é o último evento. A tabela serve de prova (art. 8º, § 2º) e de trilha de auditoria;

b) **só entra no treino quem consentiu**: a consulta só vai para a base de treino com consentimento vigente, e o banco recusa a linha sem ele. O export lê só a base de treino;

c) **a revogação apaga**: desmarcar a caixa num agendamento novo, ou pedir à clínica ou à SaúdeJá, abre um pedido que o workflow de purga executa: apaga as versões do dataset que contêm o titular, os recortes delas e as linhas da base de treino.

**Execução de contrato no lembrete (art. 7º, V).** Decisão de 10/10/2026, que substitui o consentimento (art. 7º, I) previsto antes. O lembrete é comunicação sobre o atendimento que o próprio paciente agendou, não marketing: faz parte da execução do contrato de atendimento entre o paciente e a clínica, do qual o titular é parte. É a prática dos serviços de saúde, que lembram a consulta sem pedir autorização específica para isso, e a política de privacidade e os termos de uso informam o lembrete antes do agendamento (art. 9º). Três condições sustentam a base:

a) **o SMS não leva dado sensível**: só data e hora, sem a especialidade, no texto e no remetente (Quadro 4; subseção 9.4, item 28). A base é do art. 7º porque data e hora, isoladas, são dado pessoal comum (seção 2). A escolha de *quem* recebe o lembrete usa a predição, que continua na tutela da saúde (art. 11, II, f);

b) **o contrato que sustenta a base é o do paciente com a clínica**, não o contrato do SaaS. No contrato do SaaS, a clínica aprova o critério e o texto do lembrete (Quadro 1A), mas ele não é a base do lembrete, porque o paciente não é parte dele;

c) **continuar o agendamento não é consentimento.** A caixa de ciência da política e dos termos, antes do cadastro, informa o paciente, mas não vale como consentimento: autorização genérica é nula (art. 8º, § 4º). Por isso o treino, que depende de consentimento, tem caixa própria na tela.

`src/messaging/client.py` tem uma única operação, `enviar_lembrete`, e oferecer serviço pelo mesmo canal exigiria outra base legal. O paciente pode se opor ao lembrete (art. 18, § 2º): pede à clínica para não receber SMS, e a clínica avalia o pedido (seção 6).

**`data_nascimento`.** Fica nos incisos V e VIII, e não no II (obrigação legal): o sistema usa a data só para calcular a idade que alimenta a predição, e nenhuma norma obriga o sistema a tratá-la.

**Legítimo interesse (art. 7º, IX).** Exige finalidade legítima, apenas os dados estritamente necessários e transparência ao titular (art. 10). O teste de balanceamento ainda não está documentado (subseção 9.2, item 15).

**Conta do funcionário.** A base é o legítimo interesse da SaúdeJá. A execução de contrato (art. 7º, V) não se aplica, porque exige contrato do qual o titular seja parte, e o funcionário não é parte do contrato do SaaS.

## 4 TRANSFERÊNCIA INTERNACIONAL DE DADOS

O banco de produção (Supabase) fica em São Paulo (`sa-east-1`), e o remote do DVC (Azure Blob Storage), em Brazil South desde 09/10/2026 (subseção 4.3): todo dado pessoal persistente do sistema fica armazenado no Brasil. Três tratamentos acontecem fora do país.

**Quadro 6 – Tratamento de dados fora do Brasil**

| Destino | Dados | Natureza |
|---|---|---|
| Runner hospedado do GitHub Actions | Job D-2 (diário, desde 30/09/2026): data de nascimento, sexo, especialidade, distância, antecedência, histórico de faltas e telefone; e a sincronização do desfecho com a base de treino, pelo pseudônimo da consulta. Re-treino (mensal): só a base de treino, de quem consentiu, com pseudônimo HMAC e desfecho. Purga LGPD (diária): pseudônimos dos pedidos e as versões do dataset. Nunca nome nem CPF. O canário e o alerta de observabilidade leem só contagens | Processamento. O que é gravado no runner é descartado ao fim da execução. Continua fora do Brasil depois da migração do remote |
| Hugging Face Space (Estados Unidos) | Tudo o que a interface recebe ou exibe: nome, CPF antes da conversão em hash, telefone, data de nascimento, sexo, dados da consulta, predição e explicação | Processamento, sem gravação persistente. É o destino com mais dados, porque é onde o paciente digita o cadastro |
| Infobip (local a confirmar) | Telefone e texto do lembrete, com data e hora da consulta | Envio do SMS. A Infobip guarda o log com o texto por 3 meses por padrão |

Fonte: elaboração própria (2026), a partir do [ADR-005](../adr/adr-005-integracoes-implicitas.md).

Para o pitch, a formulação correta é: **os dados são armazenados no Brasil, mas são processados fora dele: a aplicação no Hugging Face, e o job diário e o re-treino no GitHub Actions.**

**Enquadramento.** O mecanismo pretendido era o art. 33, II, b (cláusulas-padrão contratuais), por meio do acordo de processamento de dados (DPA) de cada provedor. Desde 23/08/2025, cláusula contratual só ampara transferência internacional se adotar, integralmente e sem alterações, o texto das cláusulas-padrão aprovado pela Autoridade Nacional de Proteção de Dados (ANPD), num instrumento assinado por exportador e importador, ou se tiver cláusula específica aprovada por ela (Brasil, 2024). **Nenhuma das contas atuais cumpre isso** (subseção 4.1), e o mecanismo está em aberto (subseção 9.2, item 16).

**Mitigação pelo conteúdo.** O export (`src/export_treino.py`) lê só a base de treino e nunca inclui telefone, nome ou CPF. Isso é verificado por `tests/test_coerencia_repo.py::test_export_treino_sem_coluna_proibida_de_pii` e por `test_export_de_treino_le_so_a_base_de_treino`. O identificador do paciente é o pseudônimo HMAC com chave secreta, que o re-treino não tem. Restam idade, sexo, especialidade, distância, antecedência e desfecho.

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

b) **consentimento específico e destacado para a transferência** (art. 33, VIII), com informação prévia do caráter internacional. Combina com o treino, que já depende de consentimento (seção 3), e é o caminho adotado nele. Não serve para a aplicação nem para o job D-2: um paciente que recusasse não poderia ser atendido pelo sistema, e o agendamento não pode depender de consentimento ([ADR-010](../adr/adr-010-base-de-treino-separada.md));

c) **cláusulas-padrão da ANPD assinadas com o provedor** (art. 33, II, b). Só é realista com contrato corporativo.

O art. 33, IX, não resolve: ele cobre as hipóteses do art. 7º, II, V e VI, e não as bases do art. 11 em que se apoiam os dados de saúde.

No protótipo, com a semente sintética e a SaúdeJá fictícia, nenhum titular real é afetado. Antes de operar com pacientes reais, o mecanismo de cada destino precisa estar definido aqui e na [política de privacidade](POLITICA-DE-PRIVACIDADE.md), seção 6.

### 4.2 Mecanismo adotado

Decisões de 09/10/2026 e de 10/10/2026 ([ADR-010](../adr/adr-010-base-de-treino-separada.md)): o remote do DVC vai para Brazil South (migração executada em 09/10/2026, subseção 4.3); o job D-2, o re-treino e a aplicação continuam no GitHub Actions e no Hugging Face, fora do Brasil; e o agendamento não depende de consentimento.

**Quadro 6B – Mecanismo por destino**

| Destino | Mecanismo | Situação |
|---|---|---|
| Remote do DVC | Nenhum: não há transferência | Em Brazil South desde 09/10/2026. A conta antiga, no Chile, nunca guardou dado de titular (subseção 4.3) |
| Hugging Face e GitHub Actions, dados da clínica (aplicação e job D-2) | **Em aberto.** A tela informa o processamento fora do Brasil numa caixa de ciência, antes de qualquer campo (art. 9º, § 3º). A ciência não é o consentimento do art. 33, VIII | Bloqueia a operação com paciente real (subseção 9.5, item 30) |
| GitHub Actions, base de treino (re-treino e purga) | Consentimento do treino, com o processamento no exterior em destaque no próprio termo (art. 33, VIII) | Implementado em 10/10/2026. Uma caixa só para o treino e o processamento dele no exterior precisa de validação jurídica (subseção 9.5, item 31) |
| Hugging Face (conta do funcionário) | A definir | O consentimento no primeiro acesso, previsto antes, era frágil (subseção 9.2, item 26) |
| Infobip | A definir com o contrato pago. Candidato: art. 33, IX, transferência necessária à execução de contrato (art. 7º, V), porque o SMS leva só telefone, data e hora, sem dado sensível (seção 3) | O teste gratuito não envia lembrete a paciente (subseção 4.1). O inciso IX precisa de validação jurídica e da região de processamento confirmada no contrato |

Fonte: elaboração própria (2026), com base em Brasil (2018).

**Por que a ciência não resolve.** As cláusulas-padrão (art. 33, II, b) dependem de contrato que as contas atuais não oferecem (subseção 4.1). O art. 33, IX, só cobre transferências necessárias às hipóteses do art. 7º, II, V e VI. A identificação do paciente se apoia no inciso V, mas a predição trata dado de saúde, cuja base está no art. 11, que o inciso IX não alcança. Também não há decisão de adequação da ANPD para os Estados Unidos (art. 33, I). O consentimento do inciso VIII foi descartado para os dados da clínica porque tornaria o agendamento condicionado a ele. Restam tirar a aplicação e o job D-2 do exterior (seção 4, alternativas b e c) ou contratar as cláusulas-padrão.

**Requisitos da caixa de ciência** (tela de agendamento):

a) **antes de qualquer campo**: o formulário de cadastro roda no próprio Space, e os campos de um `st.form` só são enviados ao servidor no envio do formulário. A caixa e o botão "Continuar" ficam antes dele, e nenhum campo é desenhado sem a ciência;

b) **informação em destaque**: uma frase em negrito diz que os dados digitados são processados fora do Brasil, e os links abrem a Política de privacidade e os Termos de uso, com os países, os provedores, os dados de cada um, a ausência das cláusulas-padrão da ANPD e a alternativa de agendar pela clínica;

c) **registro**: a versão dos dois documentos fica em `agendamentos.versao_documentos`, como prova de que a informação foi apresentada.

**Política de privacidade e ciência têm papéis diferentes de consentimento.** A política e os termos cumprem o dever de transparência (art. 9º). Nenhum dos dois é base legal nem mecanismo do art. 33.

**Limites:**

- consentimento não substitui contrato de operador: GitHub e Hugging Face continuam sem DPA (subseção 4.1);
- o consentimento do treino é revogável a qualquer momento, e a revogação apaga os dados de treino do titular (seção 6).

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
| `mensagens_disparadas` | 90 dias (mesma variável) | Idem | Auditoria da decisão do modelo: registra, por agendamento, se o lembrete foi enviado ou não. Com o lembrete na execução de contrato (seção 3), não há revogação que encerre esse registro antes do prazo (subseção 9.2, item 14, encerrado) |
| `pacientes`, `agendamentos` | Sem purga por iniciativa da SaúdeJá | — | O registro do atendimento pertence à clínica, e a exclusão é feita a pedido dela (seção 6) |
| Semente do dataset de treino | Sem prazo | — | Dado sintético, sem titular. Tem uma única versão, sempre vigente, e o workflow de purga nunca a apaga |
| `treino.consultas_treino` | 90 dias depois da data da consulta (`JANELA_TREINO_DIAS`). Antes, se o titular revogar ou pedir a eliminação | Workflow de purga (`lgpd.yml`, diário) | Melhoramento do algoritmo, com consentimento (seção 3). O export só usa consultas da mesma janela |
| Linhas de produção do dataset de treino, inclusive os recortes `train_raw.pkl` e `test.pkl` e o relatório do gate de dados derivados dele | Entram no export até 90 dias depois da consulta. Cada versão do dataset é apagada 90 dias depois de deixar de ser vigente em `main`, ou na hora, se contiver um titular que revogou ou pediu a eliminação. Máximo de cerca de 210 dias no remote | Workflow de purga (`lgpd.yml`, [ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)) | Melhoramento do algoritmo, com consentimento (seção 3), e auditoria do modelo promovido. Um modelo mais antigo continua reversível pelo `model.pkl`, mas não pode mais ser retreinado com os mesmos dados |
| `model.pkl` e artefatos do canário | A versão vigente não expira. As demais são apagadas 90 dias depois de deixarem de ser vigentes | Workflow de purga (`lgpd.yml`) | Rollback do modelo ([SLO](../SLO.md), seção 5): o campeão anterior deixa de ser vigente na promoção e fica disponível por 90 dias. Não contém linha de titular |
| `auth.users` | Enquanto durar o vínculo do funcionário com a clínica | Manual, pelo administrador, a pedido da clínica no desligamento | A conta de um ex-funcionário daria acesso indevido a nomes de pacientes |
| Sessões e auditoria de login do Supabase Auth | Definida pelo provedor | Provedor | Podem conter IP e user agent (subseção 9.2, item 18) |
| `treino.eventos_consentimento` | Enquanto o titular não for eliminado da base de treino. Depois disso, 365 dias | Workflow de purga (`lgpd.yml`) | Prova de que o consentimento existia e de quando foi revogado (art. 8º, § 2º; art. 16, I). Guarda o pseudônimo HMAC, sem o qual a prova não se liga ao titular. O prazo é menor que o de prescrição (5 anos) e precisa de validação jurídica |
| `treino.pedidos_titular` e `pedidos_titular` | Executado: sem prazo, sem vínculo com o titular (só tipo, situação e datas). Recusado: com vínculo, como prova da resposta; no treino, apagado 365 dias depois da recusa | Workflow de purga (`lgpd.yml`) | Prova de atendimento do pedido no prazo de 15 dias |
| Backup do banco | Não há | — | O plano gratuito do Supabase não tem backup. Para a LGPD, o que é apagado some de vez; para a disponibilidade, é um risco (subseção 9.2, item 23). No plano Pro, haveria 7 dias de backup diário, e um dado apagado continuaria no backup por esse prazo |
| Versões no remote do DVC apagadas pela purga | 1 dia (soft delete de blob) | Provedor | O soft delete de blob está ligado com prazo de 1 dia: um blob apagado pela purga continua recuperável por mais 1 dia e depois é removido pelo Azure. Protege contra uma exclusão por engano, como a do `model.pkl` vigente (ADR-009). O versionamento de blob precisa continuar desligado: ligado, cada exclusão guardaria uma versão anterior sem prazo |
| Cópias locais do DVC (cache e workspace de quem desenvolve) | Cache: só o que o workspace usa. Workspace: até a próxima execução da purga local depois de a versão sair do remote. Versão local nunca publicada: 90 dias | `scripts/purgar_dvc_local.py`, executado por quem desenvolve depois de cada `git pull` e pelo menos uma vez por semana | Medida organizacional, não automática: depende de quem desenvolve executar o script. O atraso máximo declarado é de 7 dias além do remote (subseção 9.2, item 25) |
| Artefato `gate-report` do re-treino (GitHub Actions) | 90 dias (padrão do GitHub para repositório público) | Provedor | Métricas e contagens do gate. Os bloqueios citam até cinco `id_consulta`, que são uuids internos, sem dado do paciente |
| Cópia do banco de produção (`supabase db dump`) | Não é feita | — | A documentação do Supabase recomenda dumps periódicos no plano gratuito, mas um dump guardaria o banco inteiro fora de qualquer purga. Se for necessário num incidente, é apagado ao fim da recuperação |
| Log de envio da Infobip | 3 meses por padrão | Provedor | Contém o telefone e o texto do lembrete. A redução do prazo é gratuita e deve ser pedida ao contratar a conta paga |
| Log de API do Supabase | Definida pelo plano | Provedor | Registra a URL de cada consulta. Não contém dado pessoal em claro (seção 8) |

Fonte: elaboração própria (2026).

As purgas de `eventos_app`, `predicoes` e `mensagens_disparadas` rodam no job diário que já existe, sem agendador novo, pelo mesmo critério do ADR-006. As que exigem exclusão no remote do DVC e na base de treino rodam no workflow de purga (`lgpd.yml`), com credencial própria ([ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)). Uma falha de purga não derruba o job, e a janela de 90 dias absorve uma execução perdida. Apagar `predicoes` remove também a explicação SHAP, o que não afeta o [SLO](../SLO.md) (seção 4, medido em janelas de 24 h, 7 d e 30 d), a fila do dia nem o re-treino: o re-treino lê o desfecho e o lembrete da base de treino, que o job D-2 sincroniza a partir de `agendamentos`.

### 5.1 Cópias e backups

Um dado só é eliminado quando todas as suas cópias são apagadas. O quadro compara cada cópia com a retenção do dado principal.

**Quadro 7A – Retenção das cópias**

| Cópia | Dado principal | Atraso da cópia | Cumpre? |
|---|---|---|---|
| Soft delete de blob no remote do DVC | Versões do dataset e dos recortes no remote | 1 dia | Sim, com o atraso declarado |
| Cache local do DVC | Idem | Nenhum: o cache local guarda menos que o remote | Sim, se a purga local for executada |
| Workspace local (`data/consultas-treino.csv`, `data/interim/*.pkl`) | Idem | Até 7 dias | Sim, com o atraso declarado, se a purga local for executada na frequência combinada |
| Backup do Supabase | `pacientes`, `agendamentos`, `predicoes`, `mensagens_disparadas`, `eventos_app` e o schema `treino` | Não há backup | Sim. A exclusão no banco é definitiva |
| Runner do GitHub e Hugging Face Space | Tudo o que processam | Nenhum: o disco é descartado ao fim da execução ou no restart | Sim |
| Log de envio da Infobip | `mensagens_disparadas` (90 dias) | Até 3 meses depois do envio, e até 3 meses depois de uma eliminação | Na expiração, sim: 3 meses equivalem aos 90 dias. Na eliminação, não: o log do titular fica no provedor até vencer, salvo pedido de exclusão à Infobip |
| Log de API do Supabase e log do GitHub Actions | — | Prazo do provedor | Não guardam dado pessoal em claro (seção 8) |
| MLflow local (`mlruns/`, `data/mlflow.db`) e imagens Docker | — | — | Fora do escopo: o MLflow registra parâmetros, métricas e o `model.pkl`, e nenhuma imagem copia o dataset (`.dockerignore`) |

Fonte: elaboração própria (2026).

**Conclusão.** Com o soft delete de 1 dia, o workflow de purga e a purga local, as cópias passam a seguir a retenção do dado principal, com atrasos declarados de 1 dia (remote) e até 7 dias (máquinas de quem desenvolve). Restam duas lacunas: a purga local depende de disciplina de quem desenvolve, e o log da Infobip sobrevive à eliminação do titular.

## 6 DIREITOS DO TITULAR

O titular paciente exerce seus direitos pela clínica, que aciona a SaúdeJá. Sobre os dados de treino, de que a SaúdeJá é controladora, ele também pode pedir diretamente ao encarregado. O funcionário exerce os direitos sobre a própria conta diretamente com a SaúdeJá (seção 11). O roteiro está em [PROCEDIMENTO-ELIMINACAO.md](PROCEDIMENTO-ELIMINACAO.md).

**Quadro 8 – Atendimento aos direitos do titular (art. 18)**

| Direito | Como é atendido | Limitação |
|---|---|---|
| Confirmação e acesso (I, II) | Banco da clínica: consulta pelo `id_paciente_externo`, recalculado a partir do CPF. Base de treino: pelo pseudônimo HMAC, recalculado a partir do CPF com a chave | Busca por nome não identifica o titular com segurança (homônimos, grafia). Sem ferramenta dedicada para o acesso aos dados de treino |
| Correção (III) | Alteração direta em `pacientes` ou `agendamentos` | — |
| Portabilidade (V) | Export das linhas do titular | Sem ferramenta dedicada |
| Eliminação (VI) | Banco da clínica: pedido em `pedidos_titular`, registrado e confirmado pela clínica na aba "Pedidos do titular" e executado pelo workflow de purga (`delete` em `pacientes`; o cascade remove agendamentos, predições e mensagens). Base de treino: pedido em `treino.pedidos_titular`, encaminhado pela clínica ou registrado pelo encarregado, executado pelo workflow na ordem DVC → base de treino → pedido. Os eventos de consentimento ficam 365 dias como prova ([ADR-010](../adr/adr-010-base-de-treino-separada.md)) | Os dois pedidos são independentes: a eliminação na clínica não apaga os dados de treino, salvo se o paciente pedir as duas |
| Revogação do consentimento (IX) | Vale para o treino, a única finalidade com consentimento. Caixa desmarcada num agendamento novo, ou pedido à clínica ou à SaúdeJá: evento `revogado` e pedido de revogação, que o workflow executa como a eliminação dos dados de treino | A caixa desmarcada revoga mesmo sem intenção explícita de revogar (subseção 9.5, item 33) |
| Oposição (§ 2º) | Vale para os tratamentos sem consentimento (Quadro 5), como o lembrete (execução de contrato) e os dados de legítimo interesse. No lembrete, o paciente pede à clínica para não receber SMS. Avaliada caso a caso pela clínica | Sem procedimento definido, e sem campo que impeça o envio a quem se opôs |
| Revisão de decisão automatizada (art. 20) | A clínica, controladora da decisão, recebe o pedido, revisa e responde; a SaúdeJá dá apoio técnico pelo canal do encarregado ([PROCEDIMENTO-REVISAO.md](PROCEDIMENTO-REVISAO.md); roteiro na tela "Fila do dia"). A explicação SHAP de cada predição (SLO, seção 4: 100% de cobertura) é o insumo da revisão, não a revisão. A decisão é só enviar ou não um lembrete, e não nega, condiciona nem encarece o atendimento | Depois de 90 dias, a predição é apagada e só os critérios gerais do modelo podem ser explicados. O risco é baixo pelo tipo de decisão, não por precisão do modelo |

Fonte: elaboração própria (2026).

## 7 MEDIDAS DE SEGURANÇA

O quadro lista apenas medidas implementadas e verificadas (art. 46).

**Quadro 9 – Medidas técnicas de segurança**

| Medida | Onde | Garantia |
|---|---|---|
| Minimização no schema | `supabase/migrations/` | CPF e e-mail de paciente não têm coluna. `telefone` e `nome_completo` são autorizados só na migration que os introduziu (`test_coerencia_repo.py::test_migrations_sql_sem_coluna_proibida_de_pii`) |
| Pseudonimização | `src/ui/logic.py::_id_paciente_externo_de_cpf`; `src/pseudonimo.py` | O CPF vira hash sha256 antes de chegar ao banco da clínica. Na base de treino, titular e consulta são HMAC-SHA256 com chave secreta, que não se reverte sem a chave (`tests/test_pseudonimo.py`) |
| Base de treino fechada | Migration `20261011000000_schema_treino.sql` | Schema `treino` não exposto ao PostgREST e sem acesso direto de nenhum papel; só funções `treino_*`, executáveis só pela chave secreta (`tests/test_db_treino.py`). Linha de treino recusada pelo banco sem consentimento vigente |
| Export sem o banco da clínica | `src/export_treino.py` | O export importa só o repositório da base de treino (`test_coerencia_repo.py::test_export_de_treino_le_so_a_base_de_treino`) |
| Idade não persistida | `src/features.py::calcular_idade` | Guarda-se `data_nascimento`; a idade é calculada na predição |
| Colunas explícitas na fila do dia | `repositories._COLUNAS_FILA_DO_DIA` | Coluna nova de `pacientes` só chega à UI por decisão explícita |
| Prompt do LLM sem dado pessoal | `src/explain.py::contexto_sem_pii` | Sanitização feita pela classe-base, não por cada implementação |
| RLS em todas as tabelas, sem policies | Migrations | Só a chave secreta do backend lê dados; vazar a URL do projeto não dá acesso |
| Login da equipe | `src/ui/logic.py::autenticar_funcionario`, [architecture.md](../architecture.md#7-security-considerations), seção 7 | Telas só depois de e-mail e senha. Cadastro fechado, sessão de 30 min, mensagem de erro que não revela se o e-mail existe (`test_ui_smoke.py`, `test_db.py`) |
| Segredos fora do git | `.env.example` | Documenta variáveis, nunca valores, incluindo a URL do remote do DVC |
| Credencial mínima do remote do DVC | Secrets do GitHub e `.env` local | SAS de container, nunca account key: leitura (`rl`) em todo lugar, escrita sem exclusão (`rlcw`) só no re-treino, exclusão sem escrita (`rld`) só no workflow de purga, no environment `lgpd` (`test_coerencia_repo.py::test_workflows_usam_sas_do_azure_e_so_o_retreino_escreve` e `test_so_o_workflow_de_purga_apaga_e_sem_input_que_identifique_o_titular`) |
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

a) **log de API do Supabase**: registra a URL de cada requisição ao PostgREST, e o valor de um filtro de consulta viaja nessa URL (`?coluna=eq.valor`). O corpo de um POST (insert, upsert, `rpc`) não entra. Hoje as consultas filtram só por uuid interno, status e data, e nenhuma por dado pessoal. A guarda `tests/test_coerencia_repo.py::test_nenhuma_consulta_ao_supabase_filtra_por_dado_pessoal` falha se alguma consulta em `src/` filtrar por `id_paciente_externo`, `telefone`, `nome_completo`, `data_nascimento`, CPF ou e-mail. Uma busca por dado pessoal tem de ir no corpo de uma chamada `rpc()` a uma função do Postgres. É o que a base de treino faz com o pseudônimo: todo acesso a ela é RPC (`test_schema_treino_so_e_acessado_por_rpc`). O IP registrado é o do servidor da aplicação ou do runner, não o do paciente;

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

1. **Transferência internacional das linhas de produção do dataset** (seção 4). O armazenamento saiu do exterior: o remote do DVC está em Brazil South desde 09/10/2026 (subseção 4.3). O processamento continua fora: o re-treino lê essas linhas no runner do GitHub. Desde 10/10/2026, só de quem consentiu, com o processamento no exterior informado no termo, e com pseudônimo que não se reverte sem a chave.
2. **Reidentificação direta em caso de vazamento** ([ADR-007](../adr/adr-007-nome-do-paciente.md)). O banco expõe nome, telefone, especialidade e desfecho. O pitch não pode afirmar "não temos nome de paciente". A formulação correta é: "temos nome, restrito à tela da equipe, com sete saídas fechadas e testadas" (Quadro 4).
3. **Tela da recepção visível na sala de espera.** A fila do dia mostra nomes. A mitigação é um aviso na tela, não um mecanismo: a posição do monitor é decisão da clínica. A opção de abreviar o nome foi recusada por custo de operação (ADR-007).
4. **Eliminação de titular manual.** Encerrado em 10/10/2026: a clínica registra e confirma o pedido na aba "Pedidos do titular", e o workflow de purga executa ([PROCEDIMENTO-ELIMINACAO.md](PROCEDIMENTO-ELIMINACAO.md)).
5. **Linhas de produção em versões antigas do dataset.** O risco começa no primeiro `dvc push` depois de um export com desfecho real: a partir daí, cada versão no remote guarda esses pacientes. Enquanto só existir a semente sintética, não há titular exposto. Desde 10/10/2026, o workflow de purga apaga as versões 90 dias depois de substituídas, e a eliminação ou a revogação apagam na hora as versões que contêm o titular. Fica o custo: commits antigos passam a apontar para blobs que não existem, e o `dvc pull` neles falha.
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

13. **Consentimento do lembrete sem prova nem revogação.** Encerrado em 10/10/2026: o lembrete deixou de depender de consentimento e passou à execução de contrato (seção 3). Não há consentimento a provar nem revogação a respeitar no job D-2. O que resta é a oposição (Quadro 8), sem mecanismo no job (subseção 9.4, item 29).
14. **Retenção de `mensagens_disparadas` após a revogação.** Encerrado em 10/10/2026 pelo mesmo motivo do item 13: sem consentimento, não há revogação que encerre o registro antes dos 90 dias (seção 5).
15. **Legítimo interesse sem teste de balanceamento documentado** (art. 10), tanto para os dados de paciente quanto para a conta do funcionário. A ANPD pode exigir relatório de impacto (art. 10, § 3º).
16. **Transferência internacional sem mecanismo implementado** (seção 4). Verificado em 09/10/2026: nenhuma das contas atuais tem as cláusulas-padrão da ANPD, e só o Supabase tem DPA (subseção 4.1). Remote do DVC migrado para Brazil South em 09/10/2026. O consentimento do art. 33, VIII, previsto para GitHub e Hugging Face, foi substituído em 10/10/2026 por uma caixa de ciência, para que o agendamento não dependa de consentimento; o mecanismo dos dados da clínica continua em aberto (subseção 9.5, item 30).
17. **Pacientes menores de idade** (art. 14). O sistema não distingue menores, e não está definido de quem é o `telefone` cadastrado: do adolescente ou do responsável. No segundo caso, é dado de um terceiro que não está no inventário. Para criança, o consentimento do treino deve ser dado por pelo menos um dos pais ou pelo responsável legal (art. 14, § 1º).
18. **Registros do Supabase Auth.** Podem guardar IP e user agent do funcionário. A confirmar e, se confirmado, incluir no Quadro 2.
19. **Consentimento do treino sem mecanismo** (seção 3). Encerrado em 10/10/2026: caixa própria no formulário, eventos em `treino.eventos_consentimento` e base de treino que só recebe a consulta de quem consentiu ([ADR-010](../adr/adr-010-base-de-treino-separada.md)).
20. **Retenção de 90 dias do DVC não implementada** (seção 5). Encerrado em 10/10/2026: o export usa só consultas com até 90 dias, e o workflow de purga (`lgpd.yml`), com SAS de exclusão própria e restrito a `main`, apaga as versões expiradas. A SAS de escrita do re-treino continua sem permissão de exclusão (item 12).
21. **Modelo compartilhado entre clínicas** (seção 1). O treino é tratado como finalidade da SaúdeJá, e o termo de consentimento cita a SaúdeJá. Se um dia cada clínica tiver o próprio modelo, a seção 1 e o ADR-010 precisam ser revistos.
22. **Contas incompatíveis com operação real** (subseção 4.1). O Azure for Students se destina a uso educacional e é desativado quando o crédito acaba; o teste gratuito da Infobip não envia SMS a paciente; GitHub Free e Hugging Face Pro não têm DPA. Adequado ao protótipo, não a pacientes reais.
23. **Banco sem backup** (seção 5). O plano gratuito do Supabase não tem backup: um incidente que apague dados não tem recuperação.
24. **Política de privacidade não publicada.** O rascunho em [POLITICA-DE-PRIVACIDADE.md](POLITICA-DE-PRIVACIDADE.md) e os [Termos de uso](TERMOS-DE-USO.md) já aparecem na tela de agendamento, por link, e a caixa de ciência registra a versão deles. Continuam rascunhos, com as condições de publicação no fim de cada um, e o campo `politica.url` do frontmatter aponta para este documento, que não serve de compromisso externo.
25. **Purga local depende de quem desenvolve** (subseção 5.1). `scripts/purgar_dvc_local.py` aplica a retenção nas cópias locais do DVC, mas só quando é executado. Nada no repositório detecta uma máquina que nunca o executa. Mitigação: restringir o acesso de leitura ao remote a quem precisa e revogar a credencial de quem sai do projeto.
26. **Consentimento como mecanismo de transferência** (subseção 4.2). Encerrado em 10/10/2026 para os dados da clínica, que deixaram de depender de consentimento; continua valendo para a base de treino, em que a revogação só tira o titular do treino. Na conta do funcionário, o mecanismo está em aberto. Nenhum dos dois resolve a falta de contrato de operador com GitHub e Hugging Face.

### 9.3 Riscos acrescentados pela revisão dos papéis

27. **Elementos essenciais decididos pela SaúdeJá sem aprovação da clínica** (seção 1, Quadro 1A). Não há contrato do SaaS que apresente à clínica as categorias de dados do modelo, o critério do lembrete, o texto do SMS e a retenção de 90 dias. Enquanto ele não existir, a SaúdeJá decide sozinha elementos essenciais de tratamentos que este documento atribui à clínica e pode ser considerada controladora deles, com a base legal, os pedidos de titular e a comunicação de incidentes que isso implica. Nada no repositório impede um PR de mudar uma feature ou a retenção sem essa aprovação.

### 9.4 Riscos acrescentados pela revisão da base do lembrete

28. **Data e hora da consulta classificadas como dado comum** (seções 2 e 3). A base do lembrete na execução de contrato depende de o SMS não revelar dado de saúde. A ANPD não se manifestou sobre o fato de uma consulta agendada, isolado, ser dado de saúde, e uma leitura mais restritiva o trataria como sensível, sem hipótese de execução de contrato no art. 11. Mitigações: o texto do SMS cita só data e hora (`tests/test_skew_features.py::test_sms_nao_revela_a_especialidade`), e o remetente do SMS, a configurar no contrato pago da Infobip, não pode revelar a especialidade (uma clínica com nome de especialidade, como "Cardio Center", revelaria). Precisa de validação jurídica.
29. **Oposição ao lembrete sem mecanismo** (Quadro 8). O paciente que pedir à clínica para não receber SMS continua recebendo: não há campo em `pacientes` nem filtro no job D-2 que registre a oposição. Hoje a clínica só consegue atender o pedido fora do sistema.

### 9.5 Riscos acrescentados pela base de treino separada

30. **Processamento dos dados da clínica fora do Brasil sem mecanismo do art. 33** (subseção 4.2). A caixa de ciência informa, mas não é o consentimento do inciso VIII, e o inciso IX não alcança os dados de saúde. **Bloqueia a operação com paciente real** até a aplicação e o job D-2 saírem do exterior (seção 4, alternativas b e c) ou haver cláusulas-padrão da ANPD com os provedores. No protótipo, com dados sintéticos, nenhum titular real é afetado.
31. **Uma caixa só para o treino e o processamento dele no exterior.** O art. 33, VIII, pede consentimento específico "distinguindo claramente esta de outras finalidades". O termo do treino traz o processamento no exterior em destaque, mas numa caixa só. Precisa de validação jurídica; a alternativa é uma segunda caixa, que só aparece com a primeira marcada.
32. **Mesma chave secreta para os dois schemas.** O re-treino recebe a chave que também lê o banco da clínica. A fronteira é garantida por código e teste (o export só importa o repositório da base de treino), não por credencial. Um projeto Supabase próprio para o treino fecharia isso, ao custo do plano Pro.
33. **A caixa desmarcada revoga e apaga.** Quem consentiu e esquece de marcar a caixa num agendamento novo perde os dados de treino. O aviso está nos Termos de uso (seção 5), não na tela, por decisão de 10/10/2026. A tela não pode mostrar o consentimento vigente, porque o revelaria a quem digitasse um CPF alheio.
34. **Chave do pseudônimo.** Um vazamento reabre a ligação entre o CPF e as linhas de treino, por força bruta sobre os CPFs. A chave fica só nos secrets do Space e do job D-2 e no `.env` do encarregado, e não roda. Vazamento é incidente (seção 10). A consulta do consentimento vigente, a cada agendamento, envia o pseudônimo também de quem não consentiu, sem dado da consulta e no corpo da RPC.

## 10 INCIDENTES

Uma exposição de dado pessoal é comunicada à clínica e ao encarregado em até 72 h da ciência ([SLA](../SLA.md), seção 6; art. 48). Nos dados de paciente, a SaúdeJá, como operadora, informa o controlador o que foi exposto, quantos titulares foram afetados, quando e o que foi feito; a comunicação à ANPD e aos titulares cabe à clínica. Nos dados de conta do funcionário, a SaúdeJá é controladora e faz ela mesma essa comunicação.

Para investigar, a SaúdeJá tem `eventos_app` (90 dias, sobrevive ao restart do Space), `mensagens_disparadas` (90 dias) e o histórico do GitHub Actions. Não há log de aplicação retido (seção 8; ADR-006, risco 2).

## 11 CONTATO

- **Encarregado de Dados (DPO):** Vanessa Lin, `v.lin@gmail.com`. É também o canal dos titulares (frontmatter, `direitos.canal`) e o da Política de privacidade (seção 9).
- **Responsável técnico:** Vanessa Lin (ML Engineer, time de Produto), `v.lin@gmail.com`.

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
| 10/10/2026 | 1, 9.3 | Elementos essenciais propostos pela SaúdeJá e aprovados pela clínica (Quadro 1A, guia da ANPD); retenção dos dados derivados deixa de ser "fixada pela SaúdeJá"; treino compartilhado sem controladoria conjunta; risco 27 |
| 10/10/2026 | Frontmatter, 2, 3, 4.2, 5, 6, 9.2, 9.4 | Lembrete passa do consentimento (art. 7º, I) à execução do contrato do paciente com a clínica (art. 7º, V); `data_hora_agendada` reclassificada como dado pessoal comum quando tratada sem a especialidade; consentimento restrito ao treino e à transferência internacional; Infobip candidata ao art. 33, IX; oposição ao lembrete pela clínica; itens 13 e 14 encerrados; riscos 28 e 29 |
| 10/10/2026 | Frontmatter, 1, 1A, 2, 3, 4, 4.2, 5, 5.1, 6, 7, 8.1, 9, 9.5 | Agendamento sem consentimento e base de treino separada, controlada pela SaúdeJá ([ADR-010](../adr/adr-010-base-de-treino-separada.md)): schema `treino` fechado, pseudônimo HMAC com chave secreta, export só da base de treino, sincronização diária do desfecho, caixa de ciência da política e dos termos no lugar do consentimento da transferência, workflow de purga implementado; itens 4, 19 e 20 encerrados; riscos 30 a 34 |
| 11/10/2026 | Frontmatter, 11 | Contato do controlador, canal dos titulares e encarregado padronizados (Vanessa Lin, `v.lin@gmail.com`); data de atualização da política corrigida para a revisão 0.2 |

Fonte: elaboração própria (2026).

<h2 align="center">REFERÊNCIAS</h2>

BRASIL. Autoridade Nacional de Proteção de Dados. **Guia orientativo para definições dos agentes de tratamento de dados pessoais e do encarregado**: versão 2.0. Brasília, DF: ANPD, abr. 2022. Disponível em: https://www.gov.br/anpd/pt-br/documentos-e-publicacoes/Segunda_Versao_do_Guia_de_Agentes_de_Tratamento_retificada.pdf. Acesso em: 10 out. 2026.

BRASIL. Autoridade Nacional de Proteção de Dados. **Resolução CD/ANPD nº 19, de 23 de agosto de 2024**. Aprova o Regulamento de Transferência Internacional de Dados e o conteúdo das cláusulas-padrão contratuais. Diário Oficial da União: seção 1, Brasília, DF, 23 ago. 2024. Disponível em: https://www.gov.br/anpd/pt-br/acesso-a-informacao/institucional/atos-normativos/regulamentacoes_anpd/resolucao-cd-anpd-no-19-de-23-de-agosto-de-2024. Acesso em: 9 out. 2026.

BRASIL. **Lei nº 13.709, de 14 de agosto de 2018**. Lei Geral de Proteção de Dados Pessoais (LGPD). Brasília, DF: Presidência da República, [2019]. Disponível em: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm. Acesso em: 9 out. 2026.

GITHUB. **GitHub Data Protection Agreement**. [*S. l.*]: GitHub, out. 2025. Disponível em: https://github.com/customer-terms/github-data-protection-agreement. Acesso em: 9 out. 2026.

INFOBIP. **Free trial**. [*S. l.*]: Infobip, 2026a. Disponível em: https://www.infobip.com/docs/essentials/getting-started/free-trial. Acesso em: 9 out. 2026.

INFOBIP. **Data retention notice**. [*S. l.*]: Infobip, 2026b. Disponível em: https://www.infobip.com/policies/data-retention-notice. Acesso em: 9 out. 2026.

LGPD.MD. **Especificação lgpd.md**: versão 1.0. [*S. l.*]: lgpd.md, 2025. Disponível em: https://lgpd.md/spec. Acesso em: 9 out. 2026.

MICROSOFT. **Microsoft Products and Services Data Protection Addendum**. [*S. l.*]: Microsoft, 22 maio 2026a. Disponível em: https://www.microsoft.com/licensing/docs/view/Microsoft-Products-and-Services-Data-Protection-Addendum-DPA. Acesso em: 9 out. 2026.

MICROSOFT. **Azure for Students**: offer details. [*S. l.*]: Microsoft, 2026b. Disponível em: https://azure.microsoft.com/en-us/pricing/offers/ms-azr-0170p. Acesso em: 9 out. 2026.

SUPABASE. **Data Processing Addendum**. [*S. l.*]: Supabase, jun. 2026. Disponível em: https://supabase.com/legal/dpa. Acesso em: 9 out. 2026.
