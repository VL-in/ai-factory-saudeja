# ADR-010: Agendamento sem consentimento e base de treino separada, com pseudônimo HMAC

## Status
Aceito — 2026-10-10. Supera o [ADR-009](adr-009-tratamentos-lgpd-centralizados.md) em três pontos: onde o consentimento é registrado, o consentimento da transferência internacional e a ordem da eliminação no banco da clínica. O restante do ADR-009 continua valendo: workflow único de purga, credencial própria de exclusão, tabela de eventos só de inserção, prova por 365 dias, retenção de 90 dias no DVC e lista explícita de hashes.

## Contexto
A primeira implementação do ADR-009 colocou o consentimento da transferência internacional (art. 33, VIII) antes do formulário de agendamento. Sem marcar a caixa, o paciente não via nenhum campo e era orientado a agendar pela clínica. Na prática, a tela travava o agendamento de quem não consentia.

Isso contraria a premissa do produto:

- **o paciente tem direito de agendar.** A coleta para o agendamento, a predição e o lembrete faz parte da execução do serviço da clínica, que é a controladora (art. 7º, V; art. 11, II, f). Não depende de consentimento;
- **só a coleta para o retreino do modelo depende de consentimento** (art. 11, I), e a recusa não pode travar a tela nem impedir a consulta;
- o modelo é compartilhado entre clínicas (o [ADR-008](adr-008-canario-do-modelo.md) parte do volume somado), então o treino é finalidade da SaúdeJá, que é controladora dele ([LGPD.md](../LGPD/LGPD.md), seção 1). Os dados de treino pedem, portanto, uma base própria, com regras próprias de purga e de atendimento ao titular.

Forças em jogo:

- **Orçamento.** O plano gratuito do Supabase tem 2 projetos ativos, e dev e produção já os ocupam. Um projeto separado para o treino exigiria o plano Pro.
- **O re-treino roda fora do Brasil** (runner do GitHub). Hoje ele lê o banco da clínica inteiro com a chave secreta.
- **O hash sha256 do CPF se reverte por força bruta** (Quadro 2 do LGPD.md). Ele não serve de pseudônimo para uma base que a SaúdeJá controla e que vai ao exterior.
- **O titular precisa ser achado para a exclusão** mesmo depois de a clínica apagar o cadastro dela.
- **O pseudônimo não pode ir na URL** de uma consulta ao Supabase, que fica no log do provedor (LGPD.md, subseção 8.1).

## Decisão
O agendamento não depende de consentimento. Os dados de quem consente o treino vão, além do banco da clínica, para uma **base de treino separada, no schema `treino` do mesmo projeto Supabase**, controlada pela SaúdeJá. O titular e a consulta aparecem nela por **pseudônimo HMAC com chave secreta**.

- **Tela do paciente** (decisão da autora, D2):
  - antes do cadastro, uma caixa de **ciência** da Política de privacidade e dos Termos de uso e um botão "Continuar", bloqueado até a caixa ser marcada. A ciência é informação (art. 9º), com o processamento fora do Brasil em destaque (art. 9º, § 3º); não é consentimento;
  - os dois documentos abrem em telas próprias, por links. O texto longo saiu da tela inicial;
  - a versão dos documentos fica gravada no agendamento (`agendamentos.versao_documentos`);
  - no formulário, **uma única caixa opcional**, nunca pré-marcada, para o treino, com texto curto e em linguagem simples. O termo diz, em destaque, que o uso é feito fora do Brasil (D3), e que recusar não impede o agendamento; o detalhe, inclusive o efeito da caixa desmarcada, fica nos Termos de uso.
- **Ordem do cadastro**: primeiro o serviço da clínica (paciente e agendamento), depois a etapa de treino. A etapa de treino nunca derruba o agendamento. Se ela falhar, a confirmação avisa que a autorização não foi registrada; sem prova não há treino.
- **Schema `treino`** (D1):
  - `eventos_consentimento` (só de inserção);
  - `consultas_treino`: idade na data da consulta, sexo, especialidade, distância, antecedência, histórico de faltas gravado no agendamento, data e hora, desfecho e lembrete. Sem nome, telefone, CPF nem data de nascimento;
  - `pedidos_titular` (revogação e eliminação, sem etapa de confirmação da clínica).
- **O schema não é exposto ao PostgREST e nenhum papel tem acesso direto a ele.** O único caminho são funções `public.treino_*`, `security definer`, com `execute` só para a chave secreta. O pseudônimo vai sempre no corpo da RPC.
- **Garantias no banco**: linha de treino só entra com consentimento vigente do titular; eventos só de inserção, com o `eliminado_em` gravado apenas dentro da função de eliminação; pedidos com transições e datas gravadas pelo trigger.
- **Pseudônimo** (D4): `HMAC-SHA256(TREINO_CHAVE_PSEUDONIMO, "titular:" + CPF)` e `HMAC(..., "agendamento:" + uuid)`. A chave fica no Space e no job D-2, nunca no re-treino nem no workflow de purga. Não roda: trocá-la quebra o vínculo com as linhas e as provas.
- **Desfecho e lembrete** (D5): a "Fila do dia" e o job D-2 continuam gravando só no banco da clínica. O job D-2 sincroniza uma vez por dia o `status` e o `lembrete_enviado` dos últimos 90 dias para as linhas de treino que existem, pelo pseudônimo da consulta.
- **Export do re-treino**: lê só o schema `treino` (consultas com desfecho, até 90 dias, consentimento vigente). Não importa o repositório do banco da clínica.
- **Purga**:
  - na base de treino, a ordem é DVC → linhas de treino → pedido executado;
  - no banco da clínica, a eliminação é só o `delete` com cascade, porque o DVC deixou de receber dado da clínica;
  - o pedido da clínica pode ser encaminhado à SaúdeJá pela equipe (pelo CPF), e o encarregado registra pedidos recebidos diretamente.

## Consequências
Pros:
- O paciente agenda com ou sem consentimento; a recusa só tira os dados dele do treino.
- O dataset de treino passa a ter só dados de quem consentiu, sem nome, telefone, CPF, data de nascimento nem hash reversível do CPF. O que o runner do GitHub processa no re-treino fica coberto pelo consentimento do treino.
- A exclusão a pedido do titular funciona pela SaúdeJá, mesmo depois da eliminação do cadastro na clínica.
- A eliminação no banco da clínica fica trivial (cascade), sem dependência do DVC.
- Custo zero: sem projeto Supabase novo, sem segunda stack local no CI, sem segundo `db push`.

Cons:
- **O processamento no Hugging Face e no runner do GitHub, do lado da clínica, fica sem mecanismo do art. 33.** A ciência informa, mas não é o consentimento específico do inciso VIII, e o inciso IX não alcança os dados de saúde do art. 11 (LGPD.md, subseções 4.1 e 4.2). Aceitável no protótipo, com dados sintéticos; bloqueia a operação com paciente real até a aplicação e o job D-2 saírem do exterior ou haver cláusulas-padrão da ANPD com os provedores. *Atualização (2026-10-18): o mecanismo passa a ser as cláusulas-padrão da ANPD, em aditivo ao DPA do GitHub e do Hugging Face, na premissa de operação com contas pagas ([ADR-005](adr-005-integracoes-implicitas.md), emenda de 2026-10-18; LGPD.md, subseção 4.2).*
- **Mesma chave para os dois schemas.** A fronteira do re-treino é garantida por código e teste (o export só importa o repositório do treino), não por credencial. Com orçamento, um projeto próprio para o treino fecha isso, e o desenho por RPC facilita a troca.
- **A consulta do consentimento vigente envia o pseudônimo de quem não consentiu.** Sem dado da consulta, no corpo da RPC. É o preço de a caixa desmarcada revogar o consentimento anterior.
- **Caixa desmarcada revoga, e a revogação agora apaga as linhas de treino.** A tela não sabe quem é o paciente antes do envio, e mostrar o consentimento vigente revelaria a quem digitasse um CPF alheio.
- **Vazamento da chave do pseudônimo** reabre a ligação entre o CPF e as linhas de treino. É tratado como incidente.
- Uma caixa só para o treino e o processamento dele no exterior depende da leitura de "distinguindo claramente de outras finalidades" do art. 33, VIII. Precisa de validação jurídica. *Atualização (2026-10-18): com as cláusulas-padrão, o processamento do treino no exterior deixa de depender do art. 33, VIII; o termo só o informa, e a validação fica restrita à caixa como consentimento do art. 11, I.*

## Alternativas consideradas
- **Manter a caixa da transferência como trava**: descartado pela autora. O paciente tem direito de agendar, e a trava transformava o consentimento em condição do serviço.
- **Projeto Supabase próprio para o treino**: separação por credencial, mas custo do plano Pro (cerca de US$ 25/mês) e o dobro de configuração de deploy e de CI. Fica como evolução, se houver orçamento.
- **Token aleatório guardado no banco da clínica** no lugar do HMAC: perde o rastreio depois da eliminação na clínica e mistura os dados dos dois controladores.
- **Sincronizar o desfecho na hora, pela "Fila do dia"**: duas escritas por clique e nenhuma recuperação de falha. A sincronização diária é um caminho só, idempotente.
- **Filtrar o consentimento no export, lendo o banco da clínica**: o re-treino continuaria vendo o banco inteiro fora do Brasil, e o hash do CPF continuaria indo ao dataset.
