# PROCEDIMENTO DE ELIMINAÇÃO E REVOGAÇÃO A PEDIDO DO TITULAR

> **Rascunho, sem validação jurídica.** Complementa o [LGPD.md](LGPD.md) (seção 6) e o [ADR-010](../adr/adr-010-base-de-treino-separada.md).

Este procedimento cobre os pedidos de eliminação (LGPD, art. 18, VI) e de revogação do consentimento (art. 18, IX). O prazo de resposta é de **15 dias**, contados do registro do pedido. As datas são gravadas pelo próprio banco.

Os dados do paciente ficam em dois lugares, com controladores diferentes. Cada pedido diz respeito a um deles, ou aos dois.

| Onde | Controladora | O que guarda | Quem registra o pedido |
|---|---|---|---|
| Banco da clínica (schema `public`) | Clínica | Cadastro, agendamentos, predições e registros de lembrete | A equipe da clínica, na aba "Pedidos do titular" |
| Base de treino (schema `treino`) e versões do dataset no remote do DVC | SaúdeJá | Consultas de quem autorizou o aprimoramento, sem nome, telefone, CPF nem data de nascimento | A equipe da clínica (encaminhamento) ou o encarregado da SaúdeJá |

## 1 Antes de registrar

1. Confirme a identidade de quem pede: CPF e data de nascimento, conferidos com o cadastro. Em pedido de criança, quem pede é um dos pais ou o responsável legal.
2. Pergunte se o pedido é sobre o cadastro na clínica, sobre os dados de aprimoramento do sistema, ou sobre os dois.
3. Não anote o CPF em nenhum outro lugar. A tela e o script o convertem no pseudônimo e não o guardam.

## 2 Dados da clínica

1. Na "Fila do dia", abra um agendamento do paciente e copie o código em "Detalhes técnicos".
2. Na aba "Pedidos do titular", registre o pedido com esse código e o tipo "Eliminação do cadastro".
3. Se o paciente também pedir a eliminação dos dados de aprimoramento, marque a caixa e informe o CPF. O pedido é encaminhado à SaúdeJá. A tela não diz se havia dado de aprimoramento, para não revelar a quem digitasse um CPF alheio se aquela pessoa autorizou o treino.
4. Confirme o pedido em "Pedidos em aberto". O workflow de purga (`.github/workflows/lgpd.yml`, diário) apaga o paciente, e o cascade leva agendamentos, predições e registros de lembrete. O pedido executado perde o vínculo com o paciente e guarda só tipo, situação e datas.
5. Se o pedido for recusado (por exemplo, por obrigação legal de guarda do prontuário, que fica fora deste sistema), registre o motivo pelo meio próprio da clínica. O pedido recusado mantém o vínculo enquanto o cadastro existir, como prova de a quem a recusa foi respondida (art. 18, § 4º).

O log de envio do SMS fica no fornecedor por até 3 meses depois do envio. Para apagá-lo antes, a clínica pede à Infobip ([LGPD.md](LGPD.md), subseção 5.1).

## 3 Dados de aprimoramento (base de treino)

A SaúdeJá é a controladora, então não há confirmação da clínica: o pedido registrado é executado na execução diária seguinte do workflow de purga.

- **Pedido que chega pela clínica**: encaminhado pela aba "Pedidos do titular" (seção 2, passo 3).
- **Pedido que chega ao encarregado**: depois de confirmar a identidade, rode

  ```
  python scripts/pedido_titular_treino.py eliminacao
  ```

  ou `revogacao`. O CPF é lido sem eco e não é guardado nem impresso. O script precisa da `TREINO_CHAVE_PSEUDONIMO` de produção no `.env` local do encarregado.
- **Revogação pela tela**: quem desmarca a caixa do aprimoramento num agendamento novo revoga o consentimento, e a tela abre o pedido de revogação sozinha.

O workflow executa, para cada pedido, **nesta ordem**:

1. apaga do remote do DVC as versões do dataset que contêm o pseudônimo do titular e os recortes gerados a partir delas;
2. confere que nenhum desses blobs continua no remote. Se algum continuar, para o pedido com a pergunta *"O titular já teve o registro no DVC apagado antes de seguir?"* e o deixa pendente para a próxima execução;
3. apaga as linhas do titular na base de treino e marca os eventos de consentimento como eliminados. Eles ficam 365 dias como prova de que o consentimento existiu e de quando foi revogado, e depois a purga os apaga;
4. marca o pedido como executado. O pedido perde o pseudônimo e guarda só tipo, situação e datas.

O pedido carrega o pseudônimo até o passo 4. Se o workflow cair no meio, a próxima execução refaz tudo.

Um blob apagado do remote fica recuperável por 1 dia (soft delete), e quem desenvolve apaga as cópias locais com `scripts/purgar_dvc_local.py` ([LGPD.md](LGPD.md), subseção 5.1).

## 4 Se o workflow não puder rodar

O workflow pode ser disparado à mão em *Actions → Purga LGPD → Run workflow*, sem nenhum input: ele processa o que estiver pendente no banco.

Como último recurso, com o GitHub Actions indisponível por mais tempo do que o prazo permite, o mesmo script roda numa máquina da equipe: `python src/lgpd_purga.py`, num checkout com o histórico completo de `main`, com a credencial de exclusão do environment `lgpd` e as credenciais do Supabase de produção no ambiente. Ele segue a ordem da seção 3 sozinho.

Se for preciso apagar à mão, a ordem é a mesma:

1. apagar do remote do DVC as versões e os recortes com o titular;
2. só então, no editor SQL do painel do Supabase, executar `select public.treino_eliminar_titular('<pseudônimo>')` e `select public.treino_concluir_pedido('<id do pedido>', 'executado')`.

**Não execute o passo 2 antes do passo 1.** Na ordem certa, se algo falhar entre os dois, o titular ainda está na base de treino e o procedimento pode ser refeito.

## 5 Registro

O workflow publica só contadores no resumo do run: pedidos executados e parados, linhas apagadas e blobs apagados. Nenhum pseudônimo, hash ou identificador de paciente aparece no log, que é público.
