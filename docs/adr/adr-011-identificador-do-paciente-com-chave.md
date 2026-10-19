# ADR-011: Identificador do paciente no banco da clínica com HMAC e chave própria

## Status
Aceito — 2026-10-18. Complementa o [ADR-010](adr-010-base-de-treino-separada.md), que já tinha tirado o sha256 do CPF da base de treino, e leva a mesma proteção ao banco da clínica.

**Migração executada em 18/10/2026**, nos dois ambientes, depois do deploy da v2.5.0 em cada um e sem canário ativo:

| Ambiente | Pacientes | Migrados | Conflitos | Outro formato (ignorados) |
|---|---|---|---|---|
| Dev | 17 | 14 | 0 | 3 |
| Produção | 9 | 8 | 0 | 1 |

A releitura do banco depois do `--aplicar` confirmou 0 linhas no formato antigo nos dois. A chave saiu do `.env` do encarregado ao fim do procedimento (passo 8).

## Contexto
`pacientes.id_paciente_externo` identifica o paciente sem guardar o CPF. Desde a migration `20260920010000_cadastro_pacientes.sql`, ele é o sha256 do CPF, calculado na tela de cadastro.

**Esse hash se reverte por força bruta.** Há só 10⁹ CPFs válidos: os dois últimos dígitos são verificadores, calculados a partir dos nove primeiros. Se a região fiscal é conhecida (o nono dígito), sobram 10⁸. Uma GPU comum calcula bilhões de sha256 por segundo e testa todos em menos de um segundo; um notebook em Python leva alguns minutos. Para quem tem o hash, ele vale o mesmo que o CPF. O Quadro 2 do [LGPD.md](../LGPD/LGPD.md) já registrava isso, e o ADR-010 recusou o hash como pseudônimo da base de treino pelo mesmo motivo.

Onde o hash aparece:

- **na tabela `pacientes`**, ao lado de `nome_completo`, `telefone` e `data_nascimento` em texto puro. Num vazamento da tabela, nome, CPF, nascimento e telefone juntos bastam para fraude de identidade;
- **em "Detalhes técnicos" da "Fila do dia"**, inteiro, para o suporte correlacionar tela e log. Um print num chamado já expõe o CPF;
- **no filtro de redação de log**, que o deixa passar de propósito (`src/logging_config.py`). Hoje nenhum código o registra, mas o ADR-009 já dizia que ele não pode aparecer num log público.

Truncar o hash não resolve: 8 caracteres hexadecimais são 32 bits, cerca de 4 bilhões de valores para 1 bilhão de CPFs, e quase sempre sobra um único candidato.

Há também a leitura jurídica. O art. 13, § 4º, define pseudonimização como a perda da associação com o titular "senão pelo uso de informação adicional mantida separadamente pelo controlador em ambiente controlado e seguro". Com o sha256 sem chave, essa informação adicional não existe: qualquer pessoa refaz o cálculo.

Forças em jogo:

- **O identificador é chave de negócio.** É a chave do upsert do cadastro (recadastrar o mesmo CPF atualiza a linha em vez de duplicar). O `historico_noshow`, feature do modelo, é contado pelo paciente dessa linha. O canário divide os braços por ele (`src/jobs/inferencia_diaria.py::_unidade_do_canario`).
- **O CPF não está no banco.** As linhas existentes não podem ser recalculadas a partir dele.
- **O repositório é público.** A chave não pode ficar num arquivo versionado nem num `.sql` de migration.
- **Os controladores são diferentes.** O banco da clínica é da clínica; a base de treino e a chave `TREINO_CHAVE_PSEUDONIMO` são da SaúdeJá (ADR-010).
- **A SaúdeJá não tem finalidade para chegar ao CPF.** Quem tem a chave consegue reverter o identificador pelo mesmo caminho da força bruta (10⁹ HMACs em vez de 10⁹ sha256). Cada detentor da chave é alguém capaz de chegar ao CPF.
- **O cadastro roda no Space da SaúdeJá.** O HMAC é calculado a cada paciente, então a chave tem de estar disponível ao Space.

## Decisão
O identificador passa a ser um HMAC-SHA256 do hash antigo, com uma chave secreta própria, guardada fora do banco:

```
id_paciente_externo = "hmac1:" + HMAC-SHA256(PACIENTE_CHAVE_IDENTIFICADOR, sha256(CPF só com dígitos))
```

- **Envolver o hash antigo, e não o CPF.** O HMAC é calculado sobre o que já está gravado. A migração troca cada linha sem precisar do CPF de ninguém, e o cadastro chega ao mesmo valor pelas duas etapas. É a técnica de migrar senhas de md5 para bcrypt sem pedir a senha de novo.
- **Prefixo `hmac1:`.** Os dois formatos têm 64 caracteres hexadecimais; o prefixo os separa. Torna a migração idempotente (um valor já migrado nunca é envolvido de novo) e reserva espaço para uma troca de chave futura (`hmac2:` sobre o `hmac1:`).
- **Chave própria, `PACIENTE_CHAVE_IDENTIFICADOR`**, com pelo menos 32 caracteres e diferente da `TREINO_CHAVE_PSEUDONIMO`. O código recusa as duas iguais. Uma por ambiente.
- **A chave é da clínica** (decisão da autora). A clínica é a controladora do banco, e o art. 13, § 4º, diz que a informação adicional da pseudonimização é "mantida separadamente pelo controlador". A clínica gera a chave, guarda a cópia oficial e decide sobre ela. A SaúdeJá a usa só como operadora, por instrução da clínica (art. 39), e só onde é indispensável:

  | | Detentor | Por quê |
  |---|---|---|
  | Cópia oficial e cópia de segurança | Clínica | Controladora; é quem mais perde se a chave se perder |
  | Secret do Space | SaúdeJá, como operadora | O cadastro calcula o identificador a cada paciente |
  | `.env` do encarregado da SaúdeJá | Só durante a migração | Apagada ao fim do procedimento |
  | Busca de um titular pelo CPF no banco da clínica | Clínica | Atendimento ao titular é dever da controladora; a equipe registra pedidos pelo código do agendamento, sem CPF |
  | Workflows do GitHub | Ninguém | O job D-2 só lê o identificador pronto; um teste de coerência falha se algum workflow citar a chave |

- **No fim do contrato**, a SaúdeJá apaga o secret do Space e não guarda cópia. A clínica continua com a chave e consegue recalcular o identificador de cada paciente a partir do CPF em qualquer outro sistema: os dados dela não ficam presos ao fornecedor. A cláusula de entrega e eliminação da chave entra no contrato entre a clínica e a SaúdeJá (pendência, LGPD.md, item 35).
- **Sem a chave, o cadastro falha.** Nunca volta ao sha256 sem chave. A tela mostra a mensagem genérica de falha, e o log registra `ChaveIdentificadorAusente`.
- **Migração das linhas existentes** por `scripts/migrar_id_paciente.py`, rodado pelo encarregado da SaúdeJá com a chave entregue pela clínica, e não por migration SQL, porque a chave não pode ir ao repositório. O código fica em `src/id_paciente.py`.

### Procedimento de migração (por ambiente, dev antes de produção)

1. A clínica gera a chave (`python -c "import secrets; print(secrets.token_hex(32))"`), guarda a cópia oficial e uma cópia de segurança, e a entrega à SaúdeJá por canal seguro. A perda da chave é o risco principal (ver Consequências). A SaúdeJá a grava como secret do Space e, só para a migração, no `.env` local, com o `.env` apontando para o banco do ambiente.
2. Conferir que não há canário ativo (`python src/canario.py status` em `main` atualizado). O script também recusa.
3. Fazer o deploy do código com o ADR-011. Daqui até o passo 5, um paciente antigo que se recadastrar ganha uma segunda linha.
4. `python scripts/migrar_id_paciente.py`: mostra quantas linhas migraria, sem gravar.
5. `python scripts/migrar_id_paciente.py --aplicar`: grava e relê o banco para confirmar. A saída traz só contagens e uuids internos, nunca um identificador.
6. Se houver conflito, o script lista os pares `antiga -> nova` (o mesmo titular nas duas linhas) e deixa a antiga como está. Para fundir cada par, no SQL editor do Supabase:

   ```sql
   begin;
   update agendamentos    set id_paciente = '<nova>' where id_paciente = '<antiga>';
   update pedidos_titular set id_paciente = '<nova>' where id_paciente = '<antiga>';
   delete from pacientes where id = '<antiga>';
   commit;
   ```

   Os dados de cadastro da linha nova são os mais recentes, e o histórico de faltas volta a ser contado sobre todos os agendamentos do titular.
7. Rodar o passo 5 de novo até sair com código 0.
8. Apagar a `PACIENTE_CHAVE_IDENTIFICADOR` do `.env` local. A partir daqui, a SaúdeJá só tem a chave no secret do Space.

## Consequências
Pros:
- Quem obtiver o identificador (tabela, print da tela, log) não chega ao CPF sem a chave, que fica fora do banco.
- O art. 13, § 4º, passa a se aplicar de fato: a informação adicional (a chave) é mantida à parte, pela controladora.
- A SaúdeJá deixa de ter a chave fora do Space, e a clínica pode trocar de fornecedor levando o banco e a chave.
- O cadastro, o upsert, o histórico de faltas e o canário continuam funcionando, com o mesmo identificador para o mesmo CPF.
- A migração dispensa o CPF e é idempotente.

Cons:
- **A chave não pode se perder, e a guarda é da clínica.** Sem ela, todo paciente que voltar ganha cadastro novo, o histórico de faltas dele recomeça do zero (o modelo passa a vê-lo como paciente novo), e o atendimento ao titular pelo CPF deixa de achar as linhas antigas. A chave precisa de cópia de segurança fora do Space.
- **A chave não roda.** Trocá-la exige outra migração (`hmac2:`), com as mesmas restrições desta.
- **Vazamento da chave junto com a tabela** reabre a força bruta. É tratado como incidente (LGPD.md, seção 10, e item 35 da seção 9.5).
- **A separação é de governança, não técnica.** Enquanto o cadastro rodar no Space, a SaúdeJá tem a chave e o banco ao mesmo tempo. A separação completa exigiria a clínica calcular o HMAC num serviço dela (um KMS cuja chave não sai do serviço) e a SaúdeJá receber só o resultado: fica como evolução, se a clínica tiver essa infraestrutura.
- **Mais um secret por ambiente**, configurado à mão no Space, sem verificação automática no deploy: um Space sem a chave só é percebido no primeiro cadastro (erro na tela e no alerta diário).
- **Janela entre o deploy e a migração**, em que um recadastro gera duas linhas do mesmo titular. O script detecta os pares, e a fusão é manual.
- Os identificadores em texto livre de antes do cadastro por CPF (dados sintéticos de teste) ficam como estão: o cadastro nunca os reencontraria, antes ou depois.

## Alternativas consideradas
- **HMAC direto do CPF** (`HMAC(chave, CPF)`, como a base de treino): exigiria o CPF de cada linha existente. A única forma de obtê-lo seria reverter os hashes por força bruta, o que remonta todos os CPFs na memória: é, em si, um tratamento de dado pessoal sem finalidade nova. Envolver o hash dá a mesma proteção sem isso.
- **Migração preguiçosa, no recadastro** (buscar pelos dois formatos e regravar): o banco ficaria misturado por tempo indefinido, o hash reversível de quem não volta continuaria lá, e a busca pelo formato antigo teria de ir por RPC, porque o valor não pode ir na URL (LGPD.md, subseção 8.1).
- **Chave sob guarda da SaúdeJá** (a primeira versão desta decisão): mais simples de operar, mas punha nas mãos da operadora, sem finalidade, o meio de chegar ao CPF, e deixava a clínica dependente do fornecedor para recalcular os próprios identificadores no fim do contrato.
- **Reusar a `TREINO_CHAVE_PSEUDONIMO`**: um secret a menos, mas quem tivesse uma chave teria as duas bases, de controladores diferentes. O job D-2 recebe a chave do treino e passaria a ter também a da clínica sem precisar dela.
- **Guardar a chave no Supabase Vault**: a chave ficaria no mesmo banco que os dados, e um dump do banco levaria as duas.
- **Token aleatório por paciente**, guardado numa tabela de correspondência: o cadastro precisaria achar o token a partir do CPF, o que exige guardar algo derivado do CPF de qualquer jeito.
- **Só esconder o identificador da tela e do log**: tira duas portas de saída, mas a tabela continua guardando o equivalente ao CPF.
