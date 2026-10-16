# PROCEDIMENTO DE REVISÃO DA DECISÃO AUTOMATIZADA DE LEMBRETE

Procedimento para a equipe da clínica e para o suporte da SaúdeJá atenderem o pedido do paciente de explicação e revisão da decisão automatizada (art. 20 da LGPD). A análise que fundamenta este procedimento está no [LGPD.md](LGPD.md), seções 1 e 6.

## 1 O QUE PODE SER REVISTO

Dois dias antes da consulta, o job D-2 estima a probabilidade de falta. Acima do limite de decisão, o paciente recebe um lembrete por SMS. Essa é a única decisão automatizada do sistema: **enviar ou não o lembrete**. A decisão não nega, não condiciona e não encarece o atendimento.

O paciente pode pedir:

a) **explicação**: quais critérios levaram à decisão no caso dele (art. 20, § 1º);

b) **revisão**: que a decisão seja reexaminada e, se for o caso, corrigida (art. 20, *caput*);

c) **correção** dos dados que alimentaram a decisão (art. 18, III).

## 2 QUEM FAZ O QUÊ

**Quadro 1 – Responsabilidades na revisão**

| Agente | Papel na decisão | Responsabilidade |
|---|---|---|
| Clínica | Controladora | Recebe o pedido, confirma a identidade, examina a decisão, corrige o que couber e responde ao paciente |
| SaúdeJá | Operadora | Dá apoio técnico quando a clínica pede: explica os critérios do modelo, recupera a predição pelo código do agendamento e analisa se houve erro do modelo |

Fonte: elaboração própria (2026), a partir do [LGPD.md](LGPD.md), Quadro 1.

A SaúdeJá não responde diretamente ao paciente sobre a decisão do lembrete. A exceção é o pedido sobre o uso dos desfechos para treinar o modelo compartilhado entre clínicas, tratamento em que a SaúdeJá é controladora: esse vai direto ao encarregado da SaúdeJá.

## 3 PASSO A PASSO NA CLÍNICA

O mesmo roteiro aparece na tela "Fila do dia", no quadro "Paciente pediu revisão da decisão de lembrete?".

1. **Registrar o pedido** com a data em que foi feito. O prazo de resposta é de 15 dias.
2. **Confirmar a identidade** de quem pede: CPF e data de nascimento. Se for responsável legal, confirmar também o vínculo.
3. **Localizar a decisão**: na "Fila do dia", escolher a data da consulta e selecionar a linha do paciente. A seção "Por que ... tem esse risco" mostra os fatores que aumentaram ou reduziram a probabilidade.
4. **Conferir os dados de entrada**: distância, histórico de faltas, data e hora da consulta. Dado errado é corrigido no cadastro, e a correção vale para as próximas predições.
5. **Decidir**:
   - lembrete não enviado, mas que deveria ter sido: avisar o paciente por telefone;
   - lembrete enviado sem necessidade: não há efeito a desfazer; explicar o motivo;
   - o paciente não quer mais lembretes: é oposição ao lembrete (LGPD, art. 18, § 2º), não revogação de consentimento, porque o lembrete se apoia na execução do contrato com a clínica (LGPD.md, seção 3). O sistema ainda não tem como registrar a oposição e parar os envios (LGPD.md, subseção 9.4, item 29); até lá, encaminhar o pedido à SaúdeJá pelo canal da seção 4.
6. **Responder ao paciente** em linguagem simples, explicando os fatores da decisão e o que foi feito.
7. **Pedir apoio à SaúdeJá** quando:
   - a predição não aparece na tela: ela é apagada 90 dias depois de gerada (LGPD.md, seção 5);
   - o paciente contesta o critério do modelo, e não um dado de entrada;
   - houver suspeita de erro sistemático, como vários pacientes com o mesmo problema.

## 4 PEDIDO DE APOIO À SAÚDEJÁ

- **Canal:** e-mail para o encarregado da SaúdeJá, Vanessa Lin, `v.lin@gmail.com`, com o assunto "Revisão art. 20".
- **Conteúdo:** o código do agendamento, exibido em "Detalhes técnicos" da linha, e a dúvida da clínica.
- **Não enviar:** nome, CPF, telefone nem o identificador interno do paciente. O identificador é o hash do CPF, que pode ser revertido.
- **Prazo da SaúdeJá:** resposta em até 5 dias úteis, para a clínica conseguir cumprir os 15 dias.
- **Resposta da SaúdeJá:** a probabilidade, a explicação registrada em `predicoes.explicacao_shap` e a versão do modelo, enquanto a predição existir. Depois de 90 dias, só a descrição geral dos critérios do modelo, respeitado o segredo comercial (art. 20, § 1º).

## 5 LIMITES

- A LGPD não exige que a revisão seja feita por uma pessoa: o § 3º do art. 20, que exigia revisão por pessoa natural, foi vetado na Lei nº 13.853/2019. Este procedimento põe uma pessoa da clínica na revisão por decisão de desenho, porque o dado é de saúde.
- A explicação SHAP é insumo da revisão, não a revisão. Ela mostra o peso de cada fator, mas quem decide se a decisão foi adequada é a pessoa que revisa.
- O pedido é registrado na aba "Pedidos do titular" da visão do funcionário, pelo código do agendamento, com o tipo "Revisão da decisão de lembrete" (tabela `pedidos_titular`, [ADR-009](../adr/adr-009-tratamentos-lgpd-centralizados.md)). A clínica o confirma ao começar a revisão e o conclui ao responder ao paciente; as datas, gravadas pelo banco, medem o prazo de 15 dias. O conteúdo da resposta fica no meio próprio da clínica.
