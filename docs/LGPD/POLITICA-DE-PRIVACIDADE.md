# POLÍTICA DE PRIVACIDADE DO SAÚDEJÁ: AGENDAMENTO E LEMBRETE DE CONSULTA

> **Rascunho, sem validação jurídica.** Não publicar na interface nem enviar a clínicas antes de cumprir as condições listadas no fim deste documento. A análise técnica que sustenta cada afirmação está em [LGPD.md](LGPD.md).

**Vigência:** a partir de [DATA DE PUBLICAÇÃO]. **Versão:** 0.1.

Esta política explica, em linguagem simples, como o SaúdeJá trata os seus dados pessoais quando você agenda uma consulta numa clínica que usa o nosso sistema, ou quando você trabalha numa dessas clínicas e acessa o sistema. Ela segue a Lei Geral de Proteção de Dados Pessoais (LGPD), Lei nº 13.709/2018.

**Termos usados nesta política** (art. 5º da LGPD):

- **dado pessoal**: informação sobre uma pessoa identificada ou que pode ser identificada;
- **dado pessoal sensível**: dado sobre saúde, entre outros. Aqui, a especialidade e o histórico de consultas;
- **titular**: a pessoa a quem os dados se referem, como o paciente ou o funcionário;
- **controlador**: quem decide para que e como os dados são usados;
- **operador**: quem trata os dados em nome do controlador;
- **encarregado (DPO)**: a pessoa que atende os titulares e a Autoridade Nacional de Proteção de Dados (ANPD);
- **tratamento**: qualquer uso de dados, da coleta à eliminação;
- **transferência internacional**: envio de dados pessoais para outro país.

**Princípios que seguimos** (art. 6º da LGPD): usamos os dados só para finalidades informadas a você (finalidade e adequação), só os dados necessários (necessidade), com acesso fácil às informações sobre o tratamento (livre acesso e transparência), mantidos corretos (qualidade), protegidos (segurança e prevenção), sem uso discriminatório (não discriminação) e com registro do que fazemos (responsabilização e prestação de contas).

## 1 QUEM CUIDA DOS SEUS DADOS

Há dois responsáveis, e cada um responde por uma parte:

| Quem | Papel | Sobre quais dados |
|---|---|---|
| A **clínica** onde você agendou | Controladora: decide para que os seus dados são usados | Cadastro, agendamento, lembrete e previsão de falta |
| **SaúdeJá Tecnologia em Saúde S.A.** | Operadora: trata os dados em nome da clínica, seguindo as instruções dela | Os mesmos dados acima |
| **SaúdeJá Tecnologia em Saúde S.A.** | Controladora: decide por conta própria | Uso dos desfechos de consulta para aprimorar o modelo de previsão, quando o modelo atende mais de uma clínica; contas de acesso dos funcionários das clínicas |

Na prática: para dúvidas sobre o seu atendimento, fale com a clínica. Para dúvidas sobre o aprimoramento do modelo ou sobre a sua conta de funcionário, fale com o SaúdeJá (seção 9).

## 2 QUAIS DADOS TRATAMOS

**Se você é paciente:**

- **Nome completo e telefone**, informados por você no cadastro;
- **CPF**: usado só para identificar você. Ele **não é guardado**: o sistema o transforma num código (hash) e guarda apenas o código;
- **Data de nascimento e sexo**;
- **Dados da consulta**: especialidade, data e hora, distância informada até a clínica e antecedência do agendamento;
- **Histórico de comparecimento nesta clínica**: se você compareceu, faltou ou cancelou consultas anteriores;
- **Dados gerados pelo sistema**: a estimativa de risco de falta, a explicação dessa estimativa e o registro de envio do lembrete.

A especialidade e o histórico de consultas revelam informação sobre a sua saúde. Por isso, tratamos esses dados como **dados pessoais sensíveis** (art. 5º, II, e art. 11 da LGPD). O sistema **não** guarda diagnóstico, prontuário, exame nem medicamento.

**Se você é funcionário de uma clínica:** e-mail de login, senha (guardada só em forma cifrada, como hash) e registros técnicos de acesso mantidos pelo provedor de autenticação, que podem incluir endereço IP e navegador.

## 3 PARA QUE USAMOS E COM QUAL BASE LEGAL

| Finalidade | Dados | Base legal (LGPD) |
|---|---|---|
| Identificar você e registrar o agendamento | Nome, código do CPF, data de nascimento, sexo | Execução de contrato e tutela da saúde (art. 7º, V e VIII) |
| Estimar o risco de falta, para que a clínica confirme a sua presença | Dados da consulta, histórico de comparecimento, idade e sexo | Tutela da saúde, em procedimento realizado por serviço de saúde (art. 11, II, f) |
| Enviar lembrete por SMS antes da consulta | Telefone, data e hora da consulta. A especialidade não aparece na mensagem | Seu consentimento (art. 7º, I) |
| Aprimorar o modelo de previsão com os desfechos reais das consultas | Desfecho, dados da consulta, idade e sexo, **sem** nome, telefone ou CPF | Seu consentimento específico e destacado (art. 11, I) |
| Controlar o acesso dos funcionários ao sistema | E-mail e senha do funcionário | Legítimo interesse do SaúdeJá (art. 7º, IX) |

O que **não** fazemos com os seus dados:

- não vendemos nem compartilhamos para publicidade;
- não enviamos mensagens de marketing: o único SMS é o lembrete da consulta que você agendou;
- a estimativa de risco **não** é usada para recusar, cobrar, encarecer, remarcar ou substituir o seu atendimento.

Você pode recusar ou revogar a qualquer momento o consentimento do lembrete e o do aprimoramento do modelo. A recusa não afeta o seu atendimento.

## 4 PREVISÃO DE FALTA E DECISÃO AUTOMATIZADA

Dois dias antes da consulta, um modelo estatístico estima a chance de você faltar. Se a chance passa de um limite definido pela clínica, o sistema envia o lembrete automaticamente. Essa é a única decisão tomada pelo sistema: **enviar ou não um lembrete**.

Os fatores que mais pesam na estimativa são: especialidade, dia e horário da consulta, antecedência do agendamento, distância até a clínica, faltas anteriores, idade e sexo. Para cada estimativa, o sistema registra quais desses fatores aumentaram ou diminuíram o risco no seu caso.

Você tem direito a pedir informações sobre os critérios usados e a **revisão** dessa decisão (art. 20 da LGPD). Como fazer:

1. Peça à clínica, no balcão, por telefone ou pelo canal de atendimento dela. Ela vai confirmar a sua identidade.
2. Uma pessoa da equipe da clínica examina a decisão, com a explicação registrada pelo sistema, e confere se os seus dados estão corretos.
3. Se algum dado estiver errado, ele é corrigido. Se o lembrete deveria ter sido enviado, a clínica entra em contato com você.
4. A clínica responde em até **15 dias**. Quando precisar, ela pede apoio técnico ao SaúdeJá, sem enviar o seu nome, CPF ou telefone.

A explicação de cada decisão fica guardada por 90 dias (seção 7). Depois disso, a clínica pode explicar os critérios gerais do modelo, mas não o cálculo do seu caso.

## 5 COM QUEM COMPARTILHAMOS

Seus dados são acessados pela equipe da clínica, por meio de contas individuais, e pelos fornecedores que o SaúdeJá contrata para fazer o sistema funcionar. Cada fornecedor só recebe o necessário para a sua tarefa:

| Fornecedor | O que faz | Dados que recebe | Onde |
|---|---|---|---|
| Supabase | Banco de dados e autenticação | Todos os dados da seção 2 | Brasil (São Paulo) |
| Hugging Face | Hospedagem da aplicação usada no cadastro e pela equipe | Os dados digitados ou exibidos na tela, inclusive nome, CPF antes da conversão em código e telefone. Não guarda os dados depois do processamento | Estados Unidos |
| GitHub (Microsoft) | Execução da previsão diária e do aprimoramento mensal do modelo | Data de nascimento, sexo, dados da consulta, histórico de comparecimento e telefone; no aprimoramento, o desfecho e o código do CPF. Nunca nome nem CPF. Não guarda os dados depois do processamento | Fora do Brasil, em geral nos Estados Unidos |
| Microsoft Azure | Armazenamento dos dados de aprimoramento do modelo | Desfecho, dados da consulta, idade, sexo e código do CPF. Nunca nome, telefone ou CPF | Brasil |
| Infobip | Envio do SMS de lembrete | Telefone e texto do lembrete, com a data e a hora da consulta. A mensagem não cita a especialidade | [LOCAL A CONFIRMAR] |

Também podemos compartilhar dados quando a lei ou uma ordem judicial exigir.

## 6 TRANSFERÊNCIA INTERNACIONAL

Como mostra a tabela da seção 5, parte do processamento acontece fora do Brasil, em geral nos Estados Unidos. Os dados ficam armazenados no Brasil, exceto o registro de envio do SMS, guardado pelo fornecedor de SMS (seção 5).

Para o sistema funcionar, os seus dados são processados por dois fornecedores com servidores fora do Brasil, em geral nos Estados Unidos: o Hugging Face, que hospeda a tela de agendamento e a tela da equipe, e o GitHub, que calcula a previsão diária de falta e aprimora o modelo. Esses fornecedores recebem os dados descritos na seção 5, inclusive nome e telefone, e não os guardam depois do processamento. Eles não adotaram as cláusulas contratuais aprovadas pela ANPD, por isso a transferência só acontece com o seu consentimento específico, pedido antes do cadastro e separado dos demais (LGPD, art. 33, VIII). Se você não consentir, a clínica faz o seu agendamento por outro meio, e o seu atendimento não é afetado. Se revogar o consentimento depois, o seu cadastro no sistema é eliminado.

## 7 POR QUANTO TEMPO GUARDAMOS

| Dado | Prazo |
|---|---|
| Cadastro e histórico de consultas | Enquanto a clínica mantiver o seu registro de atendimento. A exclusão é feita a pedido da clínica |
| Estimativa de risco, explicação e registro de lembrete | 90 dias, com exclusão automática |
| Dados de aprimoramento do modelo | Consultas de até 90 dias atrás entram no conjunto de treino. Cada versão desse conjunto é apagada 90 dias depois de substituída, num máximo aproximado de 210 dias |
| Registro dos seus consentimentos e revogações | Enquanto o seu cadastro existir. Se você pedir a eliminação, ele fica mais 365 dias, só com o código do CPF, a finalidade e as datas, como prova de que o consentimento existiu e de quando você o revogou |
| Conta de funcionário | Enquanto durar o vínculo com a clínica. É excluída quando a clínica comunica o desligamento |
| Registros técnicos do sistema | Não contêm dados pessoais |
| Registro de envio do SMS no fornecedor | 3 meses, no fornecedor de SMS |
| Cópias de segurança | O banco de dados não tem cópia de segurança. Uma versão apagada do conjunto de treino ainda pode ser recuperada por 1 dia, por segurança, e depois é removida. Cópias nos computadores da equipe técnica são apagadas em até 7 dias depois de saírem do armazenamento principal |

Quando você revoga o consentimento do aprimoramento do modelo ou pede a eliminação dos seus dados, as versões do conjunto de treino que os contêm são apagadas.

## 8 SEUS DIREITOS

Você pode pedir, a qualquer momento e sem custo (art. 18 da LGPD):

- confirmação de que tratamos seus dados e acesso a eles;
- correção de dados incompletos, inexatos ou desatualizados;
- anonimização, bloqueio ou eliminação de dados desnecessários ou tratados em desconformidade com a lei;
- portabilidade dos dados;
- eliminação dos dados tratados com base no seu consentimento;
- informação sobre com quem compartilhamos os seus dados;
- informação sobre a possibilidade de não consentir e as consequências da recusa;
- revogação do consentimento;
- revisão da decisão automatizada (seção 4);
- oposição a tratamento feito com base em legítimo interesse.

**Como pedir:** se você é paciente, faça o pedido à clínica onde agendou; ela o encaminha ao SaúdeJá quando necessário. Para pedidos sobre o aprimoramento do modelo ou sobre conta de funcionário, escreva diretamente ao encarregado do SaúdeJá (seção 9). Para confirmar que o pedido é seu, pediremos o CPF, que será convertido em código e não ficará guardado. Respondemos em até **15 dias**.

Se não ficar satisfeito com a resposta, você pode reclamar à Autoridade Nacional de Proteção de Dados (ANPD), em https://www.gov.br/anpd.

## 9 ENCARREGADO DE DADOS (DPO)

- **Nome:** [NOME DO ENCARREGADO]
- **E-mail:** [E-MAIL REAL DO ENCARREGADO]

## 10 SEGURANÇA

Entre as medidas que adotamos estão:

- o CPF não é armazenado;
- só funcionários com conta individual, criada pelo administrador, veem a fila de pacientes; a sessão expira após 30 minutos sem uso;
- o banco de dados só pode ser lido pelo servidor da aplicação;
- dados em trânsito são protegidos por HTTPS, e os provedores cifram os dados armazenados;
- nome, CPF, telefone e e-mail são removidos automaticamente dos registros técnicos do sistema;
- os dados de aprimoramento do modelo não contêm nome, telefone nem CPF.

Se ocorrer um incidente de segurança que possa trazer risco ou dano relevante a você, a clínica, ou o SaúdeJá quando for o controlador, comunicará você e a ANPD, conforme o art. 48 da LGPD.

## 11 CRIANÇAS E ADOLESCENTES

Criança é a pessoa com até 12 anos incompletos, e adolescente, a pessoa entre 12 e 18 anos (Estatuto da Criança e do Adolescente, art. 2º). Os dados de crianças e adolescentes são tratados no seu melhor interesse (art. 14 da LGPD).

O agendamento de criança deve ser feito por um dos pais ou pelo responsável legal, que também dá ou recusa os consentimentos da seção 3 (art. 14, § 1º). O telefone informado deve ser o do responsável. Esta política foi escrita em linguagem simples também para que pais e responsáveis entendam o tratamento (art. 14, § 6º).

## 12 DEVERES DE QUEM ACESSA OS DADOS

Funcionários das clínicas e da SaúdeJá que acessam dados de pacientes:

- usam os dados só para o atendimento e para as finalidades da seção 3, nunca para fins particulares;
- não repassam dados a quem não está autorizado, nem por mensagem, e-mail ou foto da tela;
- mantêm a tela da fila longe da vista do público e encerram a sessão ao sair;
- comunicam imediatamente ao encarregado qualquer suspeita de incidente.

Os fornecedores da seção 5 tratam os dados só conforme as instruções recebidas e não podem usá-los para finalidade própria.

## 13 COOKIES

A aplicação não usa cookies de publicidade nem de rastreamento. A plataforma de hospedagem pode usar cookies técnicos, necessários para manter a conexão com a página.

## 14 ALTERAÇÕES DESTA POLÍTICA

Avisaremos na tela de agendamento quando esta política mudar. Se a mudança alterar a finalidade de um tratamento baseado em consentimento, pediremos o seu consentimento de novo.

---

<!--
CONDIÇÕES PARA PUBLICAR (remover este bloco ao publicar):
1. Coleta e registro do consentimento do lembrete e do aprimoramento do modelo na tela de agendamento (ADR-009). Sem isso, as linhas "Seu consentimento" da seção 3 não são verdadeiras.
2. Workflow de purga do ADR-009 implementado. Sem isso, os prazos do conjunto de treino na seção 7 não são cumpridos.
3. Remote do DVC migrado para Brazil South (LGPD.md, subseção 4.3); consentimento da transferência coletado antes do formulário de cadastro (LGPD.md, subseção 4.2); local de processamento da Infobip confirmado (seção 5).
4. Encarregado com nome e e-mail reais (seção 9).
5. Procedimento de revisão (PROCEDIMENTO-REVISAO.md) acordado em contrato com a clínica, incluindo o prazo de apoio da SaúdeJá (seção 4).
6. Validação jurídica.
-->
