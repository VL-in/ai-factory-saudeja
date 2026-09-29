# ADR-008: Login da equipe da clínica com e-mail e senha no Supabase Auth, só como prova de identidade

## Status
Aceito (2026-09-28)

## Contexto

A interface tinha dois perfis, "Paciente" e "Funcionário da clínica", escolhidos num seletor da barra lateral — sem nenhuma verificação. Qualquer pessoa com a URL do Space via a "Fila do dia". O [`LGPD.md` §9](../LGPD.md) classificava isso como **o risco residual mais grave** do sistema, e o [ADR-007](adr-007-nome-do-paciente.md) o agravou: a fila passou a mostrar o nome do paciente, com a justificativa de que quem vê é preposto da clínica e assinou termo de confidencialidade. Sem login, nada no sistema verificava isso. Autenticação deixou de ser item de roadmap e passou a ser pré-requisito da própria decisão do ADR-007.

**Forças em jogo:**

1. **O Supabase já é o banco**, na mesma região (`sa-east-1`) e sob o mesmo contrato. O Supabase Auth não traz fornecedor, DPA ou transferência internacional novos.
2. **O backend lê tudo com a chave secreta**, ignorando o RLS (todas as tabelas têm RLS habilitado **sem policies**). O job D-2 e a API continuam precisando disso. Transformar o login em autorização no banco exigiria escrever policies para todas as tabelas, e isso é outro projeto.
3. **O Streamlit é um processo só para todos os navegadores.** Qualquer estado de módulo (o client singleton de `src/db/client.py`, por exemplo) é compartilhado entre todos os funcionários conectados.
4. **O `supabase-py` escuta os próprios eventos de auth.** Depois de um `sign_in_with_password`, ele troca o header `Authorization` do client pelo JWT do usuário. Isso foi verificado contra o Supabase local antes de decidir, e não só lido no código-fonte: um paciente visível pelo singleton (1 linha) **deixou de ser visível (0 linhas)** depois de um login feito nesse mesmo client. Se o login acontecesse no singleton, o backend passaria a consultar como `authenticated`, o RLS sem policies devolveria tabelas vazias e o login de um funcionário mudaria a identidade das consultas de todos os outros.

## Decisão

**A visão do funcionário exige e-mail e senha, verificados pelo Supabase Auth do mesmo projeto.** A visão do paciente continua aberta, porque é o autoagendamento.

- **O login só prova identidade. Não autoriza nada no banco.** A senha é conferida pelo Supabase Auth, e os dados continuam sendo lidos com a chave secreta do backend. O RLS não muda. O token devolvido pelo Supabase é revogado na hora (`sign_out` com escopo `local`, para não derrubar o mesmo funcionário em outro navegador) e **não é guardado**: `SessaoFuncionario` tem só o id do usuário, o e-mail e o horário do último uso.
- **Cada tentativa de login usa um client descartável** (`db.client.criar_client_autenticacao`), com `persist_session` e `auto_refresh_token` desligados, e nunca o singleton. Dois testes travam isso: um unitário (`test_ui_logic.py::test_login_usa_client_descartavel_nunca_o_singleton_do_backend`) e um de integração contra o Supabase local, que confirma que o backend continua enxergando as tabelas depois de um login real (`test_db.py::test_login_real_nao_troca_a_identidade_das_consultas_do_backend`).
- **A sessão mora em `st.session_state`**: memória do servidor, por conexão, sem cookie e sem token no navegador. Recarregar a página pede login de novo.
- **30 minutos sem interação encerram a sessão** (`logic.INATIVIDADE_MAXIMA`). O objetivo é que quem senta no balcão depois do turno não herde a sessão de quem saiu. "Sair" e a expiração apagam também a última predição guardada para a aba "Explicabilidade".
- **Não há cadastro aberto.** `enable_signup = false` no Supabase. A conta é criada pelo administrador com `scripts/criar_funcionario.py` (API de admin, com e-mail já confirmado, porque o projeto não tem SMTP próprio) e desativada pelo Dashboard (apagar ou banir o usuário).
- **A mensagem de erro não distingue "e-mail não existe" de "senha errada".** O Supabase já responde igual, e a tradução em `logic._traduzir_erro_de_auth` não separa os dois. Distinguir diria a quem tenta adivinhar quais e-mails são da equipe.
- **Sem RBAC.** Toda conta vê as mesmas abas. A aba "Dev: disparo manual" continua controlada por `APP_ENV`, não por papel.

## Consequências

**Positivas**

- O risco nº 6 do `LGPD.md` §9 deixa de ser "qualquer um com a URL vê a fila" e passa a ser "quem tem conta vê a fila". A justificativa do ADR-007 fica verificável na parte técnica: só entra quem tem conta. A outra parte (só recebe conta quem assinou o termo) é processo da clínica, mas agora tem um lugar concreto onde ser aplicada.
- O Supabase Auth registra cada login no próprio log de auditoria (Dashboard → Authentication → Logs). Isso responde "quem acessou e quando" sem depender do log do Space, que é efêmero ([ADR-006](adr-006-observabilidade.md)).
- Nenhum segredo novo: usa `SUPABASE_URL` e `SUPABASE_SECRET_KEY`, que já existiam.

**Negativas, e assumidas**

- **O limite de tentativas de login do Supabase Auth é por IP, e todo login chega pelo mesmo IP**, o do servidor do Streamlit. Quem tentar senhas em massa pela tela pode travar o login da equipe inteira por alguns minutos. A interface mostra o que está acontecendo ("muitas tentativas… aguarde"), mas isso não impede o travamento. Um limite por sessão no Streamlit não resolveria, porque basta recarregar a página. CAPTCHA (que o Supabase suporta) exigiria um componente de navegador que o Streamlit não oferece nativamente. **Fica como risco declarado.**
- **Recarregar a página ou abrir outra aba pede login de novo.** Manter a sessão entre recargas exigiria guardar token em cookie no navegador, por meio de componente de terceiro, o que aumenta a superfície que esta decisão acabou de reduzir.
- **Não há autorização por papel nem no banco.** Um funcionário logado vê tudo o que qualquer outro vê. Continuar com a chave secreta também significa que um bug na UI não é contido pelo RLS.
- **A API FastAPI continua sem autenticação.** `/predict` não lê o banco: recebe o payload e devolve a predição, então não expõe dado de paciente. Mesmo assim, é uma porta aberta, e é a primeira coisa a fechar se a API ganhar um consumidor externo real.
- **Dado pessoal novo: o e-mail do funcionário**, em `auth.users` (schema gerenciado pelo Supabase). É dado de funcionário, não de paciente, e está registrado no inventário do [`LGPD.md` §2](../LGPD.md). A regra "e-mail nunca tem coluna" continua valendo para as tabelas de paciente em `public`.
- **O cadastro fechado precisa ser configurado à mão no projeto remoto.** O `supabase/config.toml` vale só para o Supabase local. No projeto real, a chave "Allow new users to sign up" fica no Dashboard e entra no checklist mais adiante. O risco de esquecer é pequeno, porque a UI nunca envia chave nenhuma ao navegador e, portanto, ninguém de fora tem uma chave para chamar `signup`. Mesmo assim, cadastro aberto num sistema de equipe é um erro de configuração.

## Alternativas consideradas

**Senha única compartilhada** (em `st.secrets`). É o mais simples e resolveria "qualquer um com a URL". **Descartada**: sem identidade individual não há auditoria de quem entrou, e desligar um funcionário obriga a trocar a senha de todos.

**`streamlit-authenticator`** (usuários com hash bcrypt num YAML). **Descartada**: incluir alguém exige redeploy com o YAML alterado, o arquivo de contas vira mais um segredo a gerir fora do banco, e é uma dependência nova para fazer o que o Supabase já faz.

**`st.login` nativo do Streamlit (OIDC com Google/Microsoft).** Daria SSO e MFA "de graça" se a clínica usar Google Workspace ou Microsoft 365. **Descartada por ora**: o pedido foi login e senha, e OIDC exige um provedor de identidade externo registrado com o endereço público do Space, que ainda não existe. **É o caminho a reavaliar** se a clínica já tiver um desses provedores.

**Usar o JWT do funcionário para ler os dados, com policies de RLS.** É o desenho certo a longo prazo: autorização no banco e contenção de bug da UI pelo RLS. **Descartada agora** porque o job D-2 e a API continuam precisando da chave secreta, porque escrever policies para todas as tabelas é uma mudança de outra ordem, e porque o ganho real só aparece com multi-clínica ou RBAC, que o produto ainda não tem. Ficam registradas as duas coisas que esse caminho exigiria: o token passaria a ser guardado por sessão, e cada consulta da UI usaria um client por sessão em vez do singleton.

**Space privado no Hugging Face.** **Descartada**: exigiria uma conta HF por funcionário da clínica e amarraria o controle de acesso da equipe à plataforma de hospedagem.
