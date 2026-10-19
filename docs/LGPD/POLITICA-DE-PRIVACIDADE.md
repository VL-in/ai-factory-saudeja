# POLÍTICA DE PRIVACIDADE DO SAÚDEJÁ: AGENDAMENTO E LEMBRETE DE CONSULTA

> **Rascunho, sem validação jurídica.** A tela de agendamento a exibe, sem este aviso, a partir de um link. Não publicar em produção nem enviar a clínicas antes de cumprir as condições listadas no fim deste documento. A análise técnica que sustenta cada afirmação está em [LGPD.md](LGPD.md).

**Vigência:** a partir de [DATA DE PUBLICAÇÃO]. **Versão:** 0.3.

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
| **SaúdeJá Tecnologia em Saúde S.A.** | Controladora: decide por conta própria | Base de aprimoramento do modelo de previsão, com os dados de quem autorizou, porque o modelo atende mais de uma clínica; contas de acesso dos funcionários das clínicas |

Na prática: para dúvidas sobre o seu atendimento, fale com a clínica. Para dúvidas sobre o aprimoramento do modelo ou sobre a sua conta de funcionário, fale com o SaúdeJá (seção 9).

## 2 QUAIS DADOS TRATAMOS

**Se você é paciente:**

- **Nome completo e telefone**, informados por você no cadastro;
- **CPF**: usado só para identificar você. Ele **não é guardado**: o sistema o transforma num código (hash) e guarda apenas o código;
- **Data de nascimento e sexo**;
- **Dados da consulta**: especialidade, data e hora, distância informada até a clínica e antecedência do agendamento;
- **Histórico de comparecimento nesta clínica**: se você compareceu, faltou ou cancelou consultas anteriores;
- **Dados gerados pelo sistema**: a estimativa de risco de falta, a explicação dessa estimativa e o registro de envio do lembrete;
- **Ciência dos documentos**: a versão desta política e dos Termos de uso que você declarou conhecer ao agendar.

**Se você autorizar o aprimoramento do modelo**, uma cópia de parte desses dados vai para uma base separada, do SaúdeJá: idade na data da consulta, sexo, dados da consulta, histórico de comparecimento, o resultado da consulta (se você compareceu ou faltou) e se recebeu lembrete. Essa base **não** tem nome, telefone, CPF nem data de nascimento. Você aparece nela por um código calculado a partir do CPF com uma chave secreta, que só o SaúdeJá guarda: com ele, atendemos o seu pedido de exclusão sem precisar guardar o CPF.

A especialidade e o histórico de consultas revelam informação sobre a sua saúde. Por isso, tratamos esses dados como **dados pessoais sensíveis** (art. 5º, II, e art. 11 da LGPD). O sistema **não** guarda diagnóstico, prontuário, exame nem medicamento.

**Se você é funcionário de uma clínica:** e-mail de login, senha (guardada só em forma cifrada, como hash) e registros técnicos de acesso mantidos pelo provedor de autenticação, que podem incluir endereço IP e navegador.

## 3 PARA QUE USAMOS E COM QUAL BASE LEGAL

| Finalidade | Dados | Base legal (LGPD) |
|---|---|---|
| Identificar você e registrar o agendamento | Nome, código do CPF, data de nascimento, sexo | Execução de contrato e tutela da saúde (art. 7º, V e VIII) |
| Estimar o risco de falta, para que a clínica confirme a sua presença | Dados da consulta, histórico de comparecimento, idade e sexo | Tutela da saúde, em procedimento realizado por serviço de saúde (art. 11, II, f) |
| Enviar lembrete por SMS antes da consulta | Telefone, data e hora da consulta. A especialidade não aparece na mensagem | Execução do contrato de atendimento com a clínica (art. 7º, V) |
| Aprimorar o modelo de previsão com os desfechos reais das consultas | Os dados da base de aprimoramento (seção 2), **sem** nome, telefone ou CPF | Seu consentimento específico e destacado (art. 11, I), dado ao SaúdeJá |
| Controlar o acesso dos funcionários ao sistema | E-mail e senha do funcionário | Legítimo interesse do SaúdeJá (art. 7º, IX) |

O que **não** fazemos com os seus dados:

- não vendemos nem compartilhamos para publicidade;
- não enviamos mensagens de marketing: o único SMS é o lembrete da consulta que você agendou;
- a estimativa de risco **não** é usada para recusar, cobrar, encarecer, remarcar ou substituir o seu atendimento.

O lembrete faz parte do atendimento que você agendou com a clínica e não depende de autorização sua. Se não quiser recebê-lo, peça à clínica (art. 18, § 2º, da LGPD).

O agendamento não depende de nenhuma autorização sua: ele faz parte do atendimento da clínica. A única autorização é a do aprimoramento do modelo, numa caixa própria e sempre desmarcada. Você pode recusar ou revogar essa autorização a qualquer momento, e a recusa não afeta o seu agendamento nem o seu atendimento. Se revogar, os seus dados de aprimoramento são apagados.

Antes do cadastro, a tela pede que você confirme que está ciente desta política e dos Termos de uso. Essa confirmação não é uma autorização: é a forma de garantir que você recebeu as informações antes de digitar os seus dados.

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
| Supabase | Banco de dados e autenticação | Todos os dados da seção 2, inclusive a base de aprimoramento, guardada separada dos dados da clínica | Brasil (São Paulo) |
| Hugging Face | Hospedagem da aplicação usada no cadastro e pela equipe | Os dados digitados ou exibidos na tela, inclusive nome, CPF antes da conversão em código e telefone. Não guarda os dados depois do processamento | Estados Unidos |
| GitHub (Microsoft) | Execução da previsão diária e do aprimoramento mensal do modelo | Na previsão diária: data de nascimento, sexo, dados da consulta, histórico de comparecimento e telefone. No aprimoramento: só a base de aprimoramento, de quem autorizou. Nunca nome nem CPF. Não guarda os dados depois do processamento | Fora do Brasil, em geral nos Estados Unidos |
| Microsoft Azure | Armazenamento das versões do conjunto de aprimoramento do modelo | Os dados da base de aprimoramento, com o código calculado com chave secreta. Nunca nome, telefone ou CPF | Brasil |
| Infobip | Envio do SMS de lembrete | Telefone e texto do lembrete, com a data e a hora da consulta. A mensagem não cita a especialidade | Brasil |

Também podemos compartilhar dados quando a lei ou uma ordem judicial exigir.

## 6 TRANSFERÊNCIA INTERNACIONAL

Todos os seus dados ficam armazenados no Brasil. **Ao usar a tela de agendamento, os dados que você digita são processados fora do Brasil**, em geral nos Estados Unidos, por dois fornecedores da seção 5:

| Fornecedor | País | Para quê | Dados | Por quanto tempo |
|---|---|---|---|---|
| Hugging Face | Estados Unidos | Hospedar a tela de agendamento e a tela da equipe da clínica | Os dados digitados ou exibidos na tela, inclusive nome, CPF antes da conversão em código e telefone; o e-mail de login do funcionário | Só enquanto a tela é usada: não guarda os dados depois |
| GitHub | Fora do Brasil, em geral Estados Unidos | Calcular todo dia a previsão de falta e enviar o lembrete; aprimorar o modelo uma vez por mês; executar os pedidos de exclusão | Na previsão diária: data de nascimento, sexo, dados da consulta, histórico de comparecimento e telefone. No aprimoramento: só a base de aprimoramento, de quem autorizou. Nunca nome nem CPF | Só durante cada execução: não guarda os dados depois |

**Como a transferência é protegida.** O SaúdeJá adotou, no contrato com cada um desses fornecedores, as cláusulas-padrão contratuais aprovadas pela ANPD, sem nenhuma alteração (art. 33, II, b, da LGPD, e Resolução CD/ANPD nº 19/2024). Elas obrigam o fornecedor a proteger os dados no mesmo nível exigido no Brasil, a usá-los só conforme as instruções recebidas e a respeitar os seus direitos.

**Quem responde.** Nos dados do agendamento, a clínica é a controladora, e o SaúdeJá, a operadora que contrata os fornecedores (seção 1). No aprimoramento do modelo e nas contas de funcionário, o SaúdeJá é o controlador. Fale com a clínica pelos canais de atendimento dela, ou com o encarregado do SaúdeJá (seção 9).

**Uso compartilhado.** Os fornecedores não usam os dados para finalidade própria nem os repassam a outras empresas (seção 12). O SaúdeJá não compartilha esses dados com ninguém além dos fornecedores da seção 5.

**Segurança.** A conexão é protegida por HTTPS, os fornecedores não guardam os dados depois do processamento, e o GitHub nunca recebe nome nem CPF. As demais medidas estão na seção 10.

**Seus direitos.** Os direitos da seção 8 valem também para os dados processados fora do Brasil. Você pode pedir, à clínica ou ao encarregado do SaúdeJá (seção 9), a íntegra das cláusulas usadas na transferência. Respondemos em até **15 dias**.

Antes do cadastro, a tela pede que você confirme que está ciente desse processamento. **Se não quiser que os seus dados sejam processados fora do Brasil, agende com a clínica, por telefone ou no balcão**: o seu atendimento não é afetado. O aprimoramento do modelo, também feito fora do Brasil e com as mesmas cláusulas, só usa os dados de quem marcou a autorização própria dele (seção 3).

## 7 POR QUANTO TEMPO GUARDAMOS

| Dado | Prazo |
|---|---|
| Cadastro e histórico de consultas | Enquanto a clínica mantiver o seu registro de atendimento. A exclusão é feita a pedido da clínica |
| Estimativa de risco, explicação e registro de lembrete | 90 dias, com exclusão automática |
| Base de aprimoramento do modelo | Cada consulta fica até 90 dias depois da data dela. As versões do conjunto de treino geradas a partir da base são apagadas 90 dias depois de substituídas, num máximo aproximado de 210 dias |
| Registro das suas autorizações e revogações do aprimoramento | Enquanto você não revogar nem pedir a eliminação. Depois disso, fica mais 365 dias, só com o código calculado com chave secreta, a finalidade e as datas, como prova de que a autorização existiu e de quando você a revogou |
| Conta de funcionário | Enquanto durar o vínculo com a clínica. É excluída quando a clínica comunica o desligamento |
| Registros técnicos do sistema | Não contêm dados pessoais |
| Registro de envio do SMS no fornecedor | 3 meses, no fornecedor de SMS |
| Cópias de segurança | O banco de dados não tem cópia de segurança. Uma versão apagada do conjunto de treino ainda pode ser recuperada por 1 dia, por segurança, e depois é removida. Cópias nos computadores da equipe técnica são apagadas em até 7 dias depois de saírem do armazenamento principal |

Quando você revoga a autorização do aprimoramento do modelo ou pede a eliminação dos seus dados de aprimoramento, eles são apagados da base de aprimoramento e das versões do conjunto de treino que os contêm. A eliminação do seu cadastro na clínica é um pedido à clínica; ao fazê-lo, diga se quer eliminar também os dados de aprimoramento.

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

**Como pedir:** se você é paciente, faça o pedido à clínica onde agendou; ela o encaminha ao SaúdeJá quando necessário. Para pedidos sobre o aprimoramento do modelo, você também pode escrever diretamente ao encarregado do SaúdeJá (seção 9). Para pedidos sobre conta de funcionário, escreva ao encarregado. Para confirmar que o pedido é seu, pediremos o CPF, que será convertido em código e não ficará guardado. Respondemos em até **15 dias**.

Se não ficar satisfeito com a resposta, você pode reclamar à Autoridade Nacional de Proteção de Dados (ANPD), em https://www.gov.br/anpd.

## 9 ENCARREGADO DE DADOS (DPO)

- **Nome:** Vanessa Lin
- **E-mail:** v.lin@gmail.com

## 10 SEGURANÇA

Entre as medidas que adotamos estão:

- o CPF não é armazenado;
- só funcionários com conta individual, criada pelo administrador, veem a fila de pacientes; a sessão expira após 30 minutos sem uso;
- o banco de dados só pode ser lido pelo servidor da aplicação;
- dados em trânsito são protegidos por HTTPS, e os provedores cifram os dados armazenados;
- nome, CPF, telefone e e-mail são removidos automaticamente dos registros técnicos do sistema;
- os dados de aprimoramento do modelo ficam numa base separada, sem nome, telefone nem CPF, e só entram nela com a sua autorização: o próprio banco de dados recusa a linha de quem não autorizou;
- o código que identifica você na base de aprimoramento é calculado com uma chave secreta, e não pode ser revertido em CPF sem ela.

Se ocorrer um incidente de segurança que possa trazer risco ou dano relevante a você, a clínica, ou o SaúdeJá quando for o controlador, comunicará você e a ANPD, conforme o art. 48 da LGPD.

## 11 CRIANÇAS E ADOLESCENTES

Criança é a pessoa com até 12 anos incompletos, e adolescente, a pessoa entre 12 e 18 anos (Estatuto da Criança e do Adolescente, art. 2º). Os dados de crianças e adolescentes são tratados no seu melhor interesse (art. 14 da LGPD).

O agendamento de criança deve ser feito por um dos pais ou pelo responsável legal, que também confirma a ciência desta política e dos Termos de uso e dá ou recusa a autorização do aprimoramento do modelo (art. 14, § 1º). O telefone informado deve ser o do responsável. Esta política foi escrita em linguagem simples também para que pais e responsáveis entendam o tratamento (art. 14, § 6º).

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

Quando esta política mudar, a versão nova aparece na tela de agendamento, e a confirmação de ciência vale para ela. Se a mudança alterar a finalidade do aprimoramento do modelo, pediremos a sua autorização de novo.

---

<!--
CONDIÇÕES PARA PUBLICAR (remover este bloco ao publicar):
1. Coleta e registro do consentimento do aprimoramento do modelo na tela de agendamento, na base de treino separada (ADR-010). Sem isso, a linha "Seu consentimento" da seção 3 não é verdadeira.
2. Workflow de purga do ADR-009 implementado. Sem isso, os prazos do conjunto de treino na seção 7 não são cumpridos.
3. Contas pagas com DPA e termo aditivo das cláusulas-padrão da ANPD assinado com o GitHub e o Hugging Face (LGPD.md, subseção 4.2, Quadro 6D). Sem isso, a seção 6 não é verdadeira. Nos dados da clínica, quem publica a seção 6 é a clínica, como controladora (Regulamento de Transferência Internacional, art. 17).
4. Procedimento de revisão (PROCEDIMENTO-REVISAO.md) acordado em contrato com a clínica, incluindo o prazo de apoio da SaúdeJá (seção 4).
5. Termos de uso (TERMOS-DE-USO.md) e esta política aprovados pela clínica no contrato do SaaS (LGPD.md, Quadro 1A).
6. Validação jurídica.
-->
