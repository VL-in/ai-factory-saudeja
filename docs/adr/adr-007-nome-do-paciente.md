# ADR-007: Nome do paciente persistido e visível à equipe da clínica, com fronteiras explícitas de saída

## Status
Aceito (2026-09-28)

## Contexto

A minimização de PII por design é decisão fundadora do schema deste projeto ([architecture.md §4.1](../architecture.md), [`docs/LGPD.md` §2](../LGPD.md)): `pacientes` guarda `id_paciente_externo` (hash sha256 do CPF), `data_nascimento`, `sexo` e `telefone`, e **nunca** nome/CPF/e-mail. A migration `20260920010000_cadastro_pacientes.sql` afirma isso em texto, e `tests/test_coerencia_repo.py::test_migrations_sql_sem_coluna_proibida_de_pii` falha o build se a palavra aparecer. A blindagem de PII no log acabou de reforçar a mesma regra em duas camadas novas: `nome` é chave proibida no filtro de redação de log e na guarda de AST sobre `src/`.

**O que a operação real revelou.** A aba "Fila do dia" existe para o funcionário da clínica atender a fila ordenada por risco — chamar o paciente, confirmar presença, registrar o desfecho (`concluido`/`no_show`/`cancelado`). A coluna "Paciente" mostrava o hash sha256, **64 caracteres hexadecimais**. Não é um detalhe de UX: é uma tela que não pode ser operada. Um funcionário não chama `9f86d081884c7d65…` na sala de espera, e o registro de desfecho — que é o que alimenta o re-treino mensal — depende de ele saber de quem é aquela linha.

**Três forças em jogo:**

1. **Necessidade, não conveniência.** A minimização existe para não guardar o que o produto não precisa (LGPD Art. 6º, III). A partir do momento em que a fila é operada por uma pessoa, o nome deixa de ser acessório e passa a ser dado necessário à finalidade — exatamente o argumento usado para gravar `telefone`: sem contato de envio, não existe lembrete pago; sem nome, não existe fila operável.
2. **O funcionário não é um terceiro.** Ele é preposto da clínica, que é a **controladora** ([`LGPD.md` §1](../LGPD.md)) e já detém o prontuário completo. Assinou termo de confidencialidade. A base legal não muda (Art. 11, II, "f" — tutela da saúde) e nenhum novo destinatário entra em cena.
3. **A preocupação da autora foi específica e correta**: o nome pode ficar visível para a equipe, mas não pode vazar para log, para um provedor de LLM (integração opcional) ou para qualquer outra aplicação. Isso é o que esta ADR precisa garantir por construção, não por convenção.

Uma pergunta foi levantada explicitamente e merece resposta registrada: **cifrar a coluna no banco protegeria o nome do LLM?** Não. A chamada ao LLM sai de dentro do mesmo processo que já leu (e decifraria) o nome para desenhar a tela — a chave estaria na mesma memória. Criptografia na aplicação defende contra vazamento do dump ou da chave do Supabase, que é outro risco, legítimo mas distinto. O que protege do LLM é uma fronteira no código.

## Decisão

**`pacientes.nome_completo` passa a ser persistido e exibido na aba "Fila do dia"**, com seis fronteiras de saída fechadas por construção e com teste travando cada uma.

- Migration `20260928000000_nome_paciente.sql`, coluna **nullable**. Sem backfill: as linhas anteriores realmente não têm nome a recuperar, e fabricar nome de paciente é pior que admitir a lacuna — a interface mostra `(cadastro sem nome) <8 primeiros do hash>`.
- **CPF segue nunca persistido.** `id_paciente_externo` continua sendo o hash sha256 dele.
- `select("*, pacientes(*), predicoes(*)")` da fila vira **lista explícita de colunas** (`_COLUNAS_FILA_DO_DIA`). Numa tabela que guarda PII por exceção, a lista de colunas é a fronteira; o curinga fazia toda coluna nova fluir para a UI sem ninguém decidir isso — `telefone` já ia, sem uso, e `nome_completo` passaria a ir por acidente.
- `ExplicadorLLM.explicar_em_texto` deixa de ser abstrata e passa a ser **concreta**, sanitizando o contexto (`contexto_sem_pii`) antes de delegar a `_gerar_texto`, que é o que as implementações sobrescrevem. A fronteira acontece por construção, não porque cada implementação futura lembrou de chamá-la.
- A guarda de migrations passa de "lista de colunas proibidas" para **pares (coluna, arquivo)**: `telefone` só pode aparecer na migration que o introduziu, `nome_completo` só na dele. Em qualquer outro arquivo, `nome_completo` volta a cair na proibição de `nome`.

| Porta de saída | O que a fecha | Teste |
|---|---|---|
| Log de aplicação | `nome` é chave proibida no filtro de `src/logging_config.py` | `test_logging_lgpd.py` |
| Código que loga | Guarda de AST sobre `src/*.py` | `test_coerencia_repo.py::test_nenhuma_chamada_de_log_em_src_referencia_campo_de_pii` |
| `eventos_app` | Allowlist fechada de `detalhe` ([ADR-006](adr-006-observabilidade.md)) | `test_observabilidade.py` |
| Dataset de treino (vai para fora do Brasil) | `COLUNAS_SAIDA` de `src/export_treino.py` | `test_coerencia_repo.py::test_export_treino_sem_coluna_proibida_de_pii` |
| API `/predict` (integrações externas) | Campo inexistente em `src/api/schemas.py` | `test_coerencia_repo.py::test_nome_do_paciente_nao_vaza_pelas_portas_de_saida` |
| SMS via Infobip (processador externo) | Select do job D-2 não traz a coluna | idem |
| LLM/TrueFoundry (opcional) | `explain.contexto_sem_pii`, aplicada pela classe-base | `test_explain.py` |

**Armazenamento em texto puro**, protegido pelo mesmo conjunto que `telefone` já usa: RLS habilitado sem policies (só a chave secreta do backend enxerga), TLS em trânsito, criptografia em repouso do provedor gerenciado. Cifrar `nome_completo` enquanto `telefone` — identificador direto **e** dado de contato — fica em texto puro seria incoerente; se a decisão mudar, ela tem de cobrir os dois, e é reversível a uma migration mais um módulo de cripto.

**Exposição na tela**: nome completo em toda linha, com aviso visível na aba de que a tela carrega dado pessoal e não deve ficar exposta à sala de espera. O hash continua na tela, em letra miúda, no painel de detalhe: é o único identificador comum entre a tela e o diagnóstico (onde o nome nunca aparece), e sem ele o suporte perde a ligação.

## Consequências

**Positivas**

- A "Fila do dia" passa a ser operável, o que destrava o registro de desfecho na prática — e é dele que sai o dado real do re-treino mensal.
- As fronteiras de saída deixaram de ser implícitas. O `select("*")` era um vazamento latente independente desta decisão: qualquer coluna de PII futura chegaria à UI sem revisão.
- A sanitização do contexto do LLM existe **antes** de o LLM ser ligado, não depois — a integração nasce com a fronteira pronta em vez de precisar ser auditada quando já estiver funcionando.
- `chave_de_pii` passa a ser a noção única de "campo de PII" no repositório, compartilhada por log e por LLM. Duas listas divergiriam, e a que divergisse em silêncio seria a que guarda a fronteira externa.

**Negativas, e assumidas**

- **A superfície de PII cresceu.** Um vazamento do banco agora expõe nome + telefone + especialidade + desfecho, o que permite reidentificação direta. Antes exigia quebrar o hash. É o custo real da decisão e vai para o slide de risco do pitch.
- **A tela da recepção fica exposta a quem está na sala de espera.** Mitigado por aviso, não por mecanismo — a decisão de onde posicionar o monitor é da clínica. A alternativa (nome abreviado na tabela, completo na seleção) foi considerada e recusada por custo de operação.
- **Direito de eliminação fica mais pesado.** Apagar um paciente já era operação manual (`LGPD.md` §9, risco 2); agora apagar de verdade importa mais, porque o que sobreviveria é identificável.
- **Reverte texto de duas migrations e de vários documentos.** A afirmação "o banco nunca grava nome nem CPF" deixa de ser verdadeira pela metade, e cada lugar que a repetia precisou ser corrigido — o risco aqui é documentação desatualizada afirmando garantia que o código não dá mais.

**O que esta decisão impede**: usar "não temos nome de paciente" como argumento de redução de risco no pitch. A formulação correta passa a ser "temos nome, restrito à tela da equipe, com sete portas de saída fechadas e testadas".

## Alternativas consideradas

**Não persistir; buscar o nome no sistema core da clínica.** `id_paciente_externo` foi originalmente descrito como "referência ao sistema core" — o SaúdeJá vende prontuário e agendamento, então o nome já existe lá. Seria o desenho ideal: a fila do dia resolveria o nome por consulta ao sistema de origem, e este banco seguiria sem PII de identificação. **Descartada porque esse sistema não existe neste repositório** — implementar seria forjar uma integração, e uma integração falsa é pior que uma coluna honesta. Registrado como o caminho certo se o produto for integrado ao core de verdade.

**Persistir cifrado na aplicação, com chave separada do `SUPABASE_SECRET_KEY`.** Defende contra vazamento do dump/da chave do banco — dois segredos em vez de um. **Descartada por ora** por três motivos: não protege do LLM (que é a preocupação declarada, ver Contexto); seria incoerente deixar `telefone` em texto puro, e cobrir os dois é trabalho maior; e perder a chave significa perder todos os nomes de forma irrecuperável, o que num protótipo sem rotina de gestão de chave é risco operacional maior que o que se compra. Reavaliar junto de `telefone` se o produto sair de protótipo.

**Nome abreviado na tabela ("Ana S. C."), completo só na linha selecionada.** Reduz o shoulder-surfing na recepção sem tirar o nome do funcionário — um clique. **Descartada pela autora** por custo de operação: quem atende varre a fila com o olho, e obrigar um clique por paciente atrapalha o trabalho que a tela existe para apoiar. O aviso na tela ficou como mitigação.

**Guardar só o primeiro nome.** Menos PII e suficiente para chamar alguém na sala de espera. **Descartada** porque não desambigua — duas "Ana" na mesma fila é comum, e confundir paciente numa tela de saúde é um erro pior que o risco que se evita.

**Manter o hash e treinar a equipe a usá-lo.** **Descartada** sem muita discussão: é transferir para a pessoa o custo de uma decisão de arquitetura, e a consequência previsível é a clínica manter um caderno paralelo com nome e hash — PII fora de qualquer controle, que é o oposto do objetivo.
