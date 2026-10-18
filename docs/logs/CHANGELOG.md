# Changelog

Todas as mudanças relevantes deste projeto são registradas aqui.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Registramos o que muda para quem usa o sistema: comportamento visível, correções de
bugs, mudanças de API/interface/esquema de banco e avisos de descontinuação. Detalhe
interno sem efeito observável (testes, lint, refatoração, verificação de release) fica
nos commits; as decisões de arquitetura ficam nos [ADRs](https://github.com/VL-in/ai-factory-saudeja/tree/main/docs/adr).

## [v2.4.0]

### Adicionado
- **Alertas do Grafana por criticidade, mantidos como código** (`infra/grafana/alertas/`, aplicados por `scripts/grafana_provisionar.py`). São 17 regras, cada uma com os rótulos `severidade` (P1, P2 ou P3), `area` (operação, modelo, custo ou LGPD) e `ambiente`. Só as de produção notificam.
  - **Críticos (P1):**
    - o job D-2 não terminou com sucesso até as 13h;
    - a fila inteira foi para a quarentena;
    - agendamentos que já existiam ficaram sem predição e sem lembrete numa execução bem-sucedida;
    - dado pessoal num log exportado ao Loki, com os mesmos padrões do filtro de redação traduzidos para o regex do Loki;
    - chave fora da lista permitida em `eventos_app`.
  - **Altos (P2):**
    - erro de banco na interface;
    - erro de predição;
    - falha de envio de SMS;
    - predição sem explicação;
    - disparos do dia fora de ±40% da média com a taxa de disparo mudando junto;
    - custo de SMS projetado acima de 80% do orçamento.
  - **Médios (P3):** disparos fora da faixa com a taxa estável (mudança na agenda), quarentena parcial e dado fora do domínio do treino, job acima de 10 min, p95 da predição acima de 2 s, leitura do SLO perto do teto de 5000 eventos e banco acima de 80% da cota gratuita.
  - Os limites ficam no bloco novo `observabilidade` do `params.yaml` e no bloco `custo`. O provisionamento falha se algum estiver vazio, em vez de criar uma regra que nunca dispara.
- **Rotas de notificação por criticidade** (`infra/grafana/notificacoes.yaml`). P1 vai ao contact point de plantão e repete a cada hora até resolver. P2 vai por e-mail e repete a cada 24 horas; com `area=modelo`, vai também à responsável pelo modelo. P3 sai num resumo, só às segundas, das 9h às 10h. Os contact points são criados à mão no Grafana, porque e-mails e Telegram não entram no repositório.
- **Seis painéis do Grafana** (`infra/grafana/dashboards/`): visão geral do SLO, interface, inferência, job D-2, mensageria e custo. Os números de curto prazo vêm do Mimir e do Loki (14 dias). Os de longo prazo vêm das views do schema `observabilidade`.
- **Workflow `grafana.yml`.** Aplica alertas, rotas e painéis no fim de cada deploy em `main`, e também por disparo manual.
  - Em dev, o disparo manual só sai do branch `dev`. Ele cria cópias de verificação numa pasta própria, que notificam apenas o contact point de dev e são apagadas com `acao=remover`.
  - `acao=conferir` lista, sem alterar nada, quais métricas e rótulos chegaram ao stack.
- **View `observabilidade.agendamentos_sem_processamento_por_dia`** (migration aditiva `20261017000000_observabilidade_alertas.sql`). Por dia de execução do job, conta os agendamentos que ele deveria ter processado e não processou. A view só traz a contagem, sem nenhuma linha por agendamento.
- **O Supabase de produção pode disparar o job D-2 às 08h17** (migration aditiva `20261017010000_gatilho_job_d2.sql`, com as extensões `pg_cron` e `http`). O agendamento do GitHub, num repositório fork, chegou com 3 a 9 horas de atraso entre 06/10 e 17/10, e o lembrete saía à tarde.
  - O disparo só acontece depois de o token do GitHub ser cadastrado no Vault do Supabase de produção. Sem ele, como em dev e no CI, nada muda.
  - O agendamento do GitHub continua como reserva. Uma segunda execução no mesmo dia não envia SMS em dobro.

## [v2.3.1] (Vanessa + Claude) - 2026-10-17

### Corrigido
- **A disponibilidade da interface passa a ser medida de verdade, pelo Synthetic Monitoring do Grafana Cloud.** A v2.2.0 anunciou monitores do UptimeRobot que nunca foram criados: de 2026-10-11 a 2026-10-17 não houve sonda externa, o uptime do [SLO §1.1](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/SLO.md#11-disponibilidade-da-interface) não foi medido e o Space pode ter hibernado. Agora há um check por Space em `/_stcore/health`, a cada 5 minutos, a partir de uma sonda em São Paulo. O check espera status 2xx e também o corpo `ok`, porque a URL pública devolve 200 com a página do Streamlit para qualquer caminho. Só produção alerta: crítico, por e-mail, com 2 falhas em 10 minutos, para que uma falha isolada durante um deploy não acione ninguém ([ADR-006](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/adr/adr-006-observabilidade.md), emenda de 2026-10-17).

## [v2.3.0] (Vanessa + Claude) - 2026-10-16

### Adicionado
- **O custo dos lembretes por SMS passa a ser medido.** A Infobip cobra por segmento de SMS, e não por lembrete. Quando o texto tem uma letra acentuada fora do alfabeto do SMS, como `á` e `ã`, cada segmento comporta 70 caracteres. O lembrete atual tem 72 e por isso sai em dois. O job D-2 grava os segmentos de cada envio na coluna nova `mensagens_disparadas.segmentos` (migration aditiva `20261016000000_observabilidade_custo.sql`) e soma o custo, em dólares, à métrica `saudeja_sms_custo_usd_total`. A view `observabilidade.envios_cobrados_por_mes` mostra, por mês e por canal, os envios cobrados, os segmentos e a projeção para o mês inteiro. O Grafana multiplica esses números pelo preço. Os preços ficam em `params.yaml`, no bloco novo `custo`: o preço de um segmento em dólares, como no contrato da Infobip (`sms_unitario_usd`, US$ 0,0291), o câmbio para mostrar o custo em reais (`cambio_usd_brl`) e o orçamento mensal (`orcamento_mensal_usd`, US$ 100). Se o preço do SMS não estiver preenchido, o lembrete é enviado normalmente e o resumo do job avisa que o custo não foi medido. O SMS simulado (stub) não tem custo.

## [v2.2.0] (Vanessa + Claude) - 2026-10-15

Esta versão reforça a proteção dos dados dos pacientes e passa a mostrar como o sistema está funcionando. O modelo de predição continua o mesmo da v2.1.0.

**Para pacientes**
- O agendamento começa pela leitura da Política de privacidade e dos Termos de uso.
- Autorizar o uso dos dados para melhorar o modelo é opcional. Quem recusa agenda e recebe o lembrete do mesmo jeito.
- O SMS de lembrete informa só a data e a hora da consulta, sem a especialidade.

**Para a clínica**
- A aba nova "Pedidos do titular" registra pedidos de eliminação de dados e de revisão da decisão de lembrete.
- A "Fila do dia" traz um roteiro para atender esses pedidos de revisão.

**Privacidade (LGPD)**
- Os dados usados para treinar o modelo ficam numa base separada. Essa base guarda só quem autorizou, sem nome, telefone, CPF nem data de nascimento.
- Uma rotina diária executa os pedidos de eliminação. Ela também apaga os dados de treino, as predições e as mensagens com mais de 90 dias.
- O dataset de treino e o modelo passam a ser armazenados no Brasil.

**Monitoramento**
- Cada ação da interface e cada execução do envio diário de lembretes podem ser acompanhadas no Grafana Cloud, sem dados pessoais.
- Os números de longo prazo, como disparos por dia e tempo de resposta, saem do banco por um acesso que só enxerga totais.
- Falhas do banco na tela e falhas de envio de SMS passam a aparecer no alerta diário.

**Atenção:** o processamento dos dados da clínica fora do Brasil ainda não tem mecanismo de transferência internacional previsto na LGPD. Até isso ser resolvido, o sistema não deve operar com pacientes reais.

Os detalhes técnicos de cada mudança estão abaixo.

### Adicionado
- **Base de treino separada, controlada pela SaúdeJá** ([ADR-010](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/adr/adr-010-base-de-treino-separada.md)). Quem autoriza o uso dos desfechos no treino vai, além do banco da clínica, para o schema `treino` do Supabase, sem nome, telefone, CPF nem data de nascimento, com o titular e a consulta identificados por pseudônimo HMAC com chave secreta. O banco recusa a consulta de quem não consentiu, e o schema só é acessado por funções do banco. A caixa do treino tem texto curto, em linguagem simples, com o processamento fora do Brasil em destaque e a garantia de que recusar não impede o agendamento; o detalhe fica nos Termos de uso. Migrations aditivas: `20261010000000_pedidos_e_ciencia.sql` e `20261011000000_schema_treino.sql`. Variável nova: `TREINO_CHAVE_PSEUDONIMO` (Space e job D-2; ver `.env.example`).
- **A tela do paciente começa pela ciência da Política de privacidade e dos Termos de uso**, abertos em telas próprias por link, e um botão "Continuar" que libera o formulário. A versão dos documentos fica gravada no agendamento. Rascunho dos [Termos de uso](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/LGPD/TERMOS-DE-USO.md).
- **Aba "Pedidos do titular" na visão do funcionário**: a clínica registra pedidos de eliminação e de revisão de decisão pelo código do agendamento, confirma ou recusa, e encaminha à SaúdeJá o pedido de eliminação dos dados de aprimoramento.
- **Workflow de purga LGPD** (`.github/workflows/lgpd.yml`, diário): executa os pedidos de revogação e eliminação, inclusive nas versões do dataset no remote do DVC, as eliminações confirmadas pela clínica, e apaga as versões do DVC e as consultas da base de treino com mais de 90 dias. Usa uma SAS de exclusão própria (`AZURE_STORAGE_CONNECTION_STRING_EXCLUSAO`), no environment `lgpd`, restrito a `main`. Roteiro em [`docs/LGPD/PROCEDIMENTO-ELIMINACAO.md`](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/LGPD/PROCEDIMENTO-ELIMINACAO.md). O encarregado registra pedidos recebidos diretamente com `scripts/pedido_titular_treino.py`.
- **As ações da interface passam a ser medidas**: carregar a fila do dia, registrar desfecho, cadastro, pedidos do titular, login e a aba de observabilidade geram eventos `acao_ui` em `eventos_app`, com latência e desfecho. Senha errada, formulário inválido e desfecho fora do prazo ficam com o status novo `recusado` e não contam como erro. A migration `20261012000000_eventos_app_acao_ui.sql` alarga os `check` de `tipo` e `status`. A aba "Observabilidade" e o alerta diário continuam lendo só predições, execuções do job e erros.
- **Telemetria para o Grafana Cloud por OpenTelemetry, desligada por padrão.** Com `OTEL_EXPORTER_OTLP_ENDPOINT` definido, a API, a interface e o job D-2 enviam logs, métricas e traces. Cada ação da interface, cada requisição da API e cada execução do job vira um trace. As chamadas ao Supabase, à Infobip e à API aparecem como etapas desse trace. No job, as etapas são busca da fila, predição, gravação e envio do lembrete, por agendamento. Antes de sair do processo, o trace perde a query string das URLs, o IP e o navegador de quem chamou e a mensagem dos erros, e fica só com a classe do erro. Sem a variável, ou com `OBSERVABILIDADE_ATIVA=false`, nada é enviado.
- **O job D-2 pode ser disparado manualmente em dev** (`gh workflow run job_d2.yml --ref dev -f ambiente=dev`), só a partir do branch `dev`, contra o Supabase de dev e sempre com o SMS simulado (stub). O agendamento diário e o disparo manual sem escolha continuam rodando só em produção.
- **O Grafana pode ler os números de longo prazo direto do banco, sem acesso a dado de paciente.** A migration `20261015000000_observabilidade_grafana.sql` cria o schema `observabilidade`, só com contagens e percentis por dia: disparos e taxa de disparo, consultas por status, predições com cobertura da explicação e fora do domínio, eventos com p50, p95 e p99 por dia e por mês, horário do último job D-2, eventos com chave fora da lista permitida e tamanho do banco. A taxa de erro não conta recusas esperadas, como senha errada. Nessas views, a predição gravada com explicação vazia conta como sem explicação. O papel novo `grafana_leitura` só lê essas views, em sessão somente leitura, e não alcança `pacientes`, `agendamentos`, `predicoes`, `mensagens_disparadas`, `eventos_app` nem a base de treino. O papel nasce sem login: a senha é definida à mão em cada projeto e fica só na fonte de dados do Grafana. O schema não é exposto pela API do Supabase.
- **Falha de banco na interface e falha de envio de SMS viram eventos de erro**, e por isso passam a aparecer no alerta diário. Antes, a primeira só mostrava a mensagem na tela e a segunda era só um aviso no log do job. O evento guarda a operação e o nome da classe do erro, nunca a mensagem.

### Modificado
- **Os eventos de `eventos_app` passam a ter `id_execucao` no `detalhe`.** No job D-2 é `gh-<número do run>-<tentativa>` do GitHub Actions, gravado mesmo com a telemetria desligada e repetido na linha de resumo do log do job. O prefixo evita que o filtro de dados pessoais apague o número, que tem 11 dígitos, como se fosse telefone ou CPF. Na interface e na API é o identificador do trace da ação ou da requisição. Ele liga a linha da tabela, que fica 90 dias, ao run e ao trace, que ficam 14 dias no Grafana.
- **O agendamento não depende mais de consentimento.** O paciente agenda e recebe o lembrete com ou sem autorização para o treino; recusar só o tira da base de treino. A caixa da transferência internacional, que escondia o formulário, deu lugar à caixa de ciência. O processamento dos dados da clínica fora do Brasil fica sem mecanismo do art. 33 e bloqueia a operação com paciente real ([LGPD.md](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/LGPD/LGPD.md), §4.2 e §9.5).
- **O encarregado de dados tem contato definido**: Vanessa Lin, `v.lin@gmail.com`, na política de privacidade, no procedimento de revisão da decisão automatizada, no documento de LGPD e no roteiro de revisão da "Fila do dia", no lugar do endereço fictício. A arquitetura e o [ADR-005](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/adr/adr-005-integracoes-implicitas.md) passam a dizer o mesmo que o ADR-010 sobre o processamento fora do Brasil: os dados são armazenados no Brasil e processados fora dele, sem mecanismo do art. 33 para os dados da clínica.
- **O re-treino lê só a base de treino**, com consultas de até 90 dias de quem consentiu. O primeiro export depois desta versão perde as linhas de produção que vinham do banco da clínica, que eram de teste. O job D-2 sincroniza o desfecho e o lembrete enviado com a base de treino uma vez por dia.
- O re-treino baixa a semente obrigatoriamente e o resto do cache do DVC com `--allow-missing`, para que uma versão apagada pela purga não quebre o ciclo.
- **O lembrete por SMS não depende de consentimento.** Ele faz parte do atendimento que o paciente agenda com a clínica (execução de contrato, LGPD art. 7º, V) e só informa data e hora, sem a especialidade; data e hora da consulta, isoladas, passam a ser tratadas como dado pessoal comum. O consentimento fica restrito ao uso dos desfechos no treino do modelo. Quem não quiser receber o SMS pede à clínica. O documento de LGPD, a política de privacidade, o procedimento de revisão e o [ADR-009](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/adr/adr-009-tratamentos-lgpd-centralizados.md) foram ajustados.
- **O README descreve o estado atual do projeto.** Ele mostra que o SaúdeJá está em produção desde a v2.0.0 e traz o roteiro com as etapas concluídas e as que faltam, incluindo as ações em aberto do post-mortem e do ensaio de rollback.
- O README passa a separar as métricas que o pipeline herdado reportava (acurácia de 78%, F1 de 0,65, ROC-AUC de 0,81) das medidas do modelo em produção no fold de teste isolado (0,67, 0,42 e 0,64). A diferença até os alvos do [SLO](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/SLO.md) será decidida na validação final.
- A seção de rollback do README cita o tempo medido no ensaio em dev (6 min 56 s) e explica como conferir o que está no ar. A seção nova "Registros de incidente e de rollback" aponta para o [PM-001](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/post-mortem/PM-001.md) e o [RB-001](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/rollbacks/RB-001-ensaio-rollback-dev.md).
- **O README traz os endereços públicos dos Spaces**: produção em <https://vl-in-saudeja-prod.hf.space> e desenvolvimento em <https://vl-in-saudeja-dev.hf.space>.
- **A disponibilidade da interface passa a ser medida pelo UptimeRobot em `/_stcore/health`**, e não em `/health`, a cada 5 minutos, com alerta só para produção ([ADR-006](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/adr/adr-006-observabilidade.md), emenda de 2026-10-11). Na URL pública, `/health` devolve a página do Streamlit com status 200 mesmo com a interface quebrada, porque a API não é publicada. Como a sonda gera tráfego a cada 5 minutos, o Space deixa de hibernar.
- **`predicoes` e `mensagens_disparadas` passam a ser apagadas após 90 dias, e não mais após 365.** É o prazo necessário para rastrear e auditar o modelo, o que inclui o canário. O re-treino não é afetado, porque lê o desfecho e o lembrete enviado em `agendamentos`, que não é apagada. Quem definiu `RETENCAO_DADOS_DERIVADOS_DIAS=365` no ambiente precisa atualizar o valor.
- O documento de LGPD separa a semente sintética do dataset de treino, que não tem titular real, das linhas de produção com desfechos reais. Essas linhas passam a exigir consentimento específico do paciente para o melhoramento do modelo (art. 11, I) e têm retenção de 90 dias. A base de treino separada e a purga implementam isso (ver "Adicionado").
- **Decidido como cumprir as obrigações de LGPD que ainda dependem de operação manual** ([ADR-009](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/adr/adr-009-tratamentos-lgpd-centralizados.md)). Um workflow próprio, com credencial de exclusão separada da do re-treino, vai tratar a expiração dos artefatos do DVC, a revogação do consentimento e a eliminação a pedido do titular. O consentimento passa a ser registrado numa tabela de eventos. Os artefatos do DVC ficam retidos por 90 dias depois de substituídos. Implementado no workflow de purga (ver "Adicionado").
- **O SMS de lembrete não cita mais a especialidade da consulta**, só a data e a hora. A especialidade é dado de saúde, e o texto fica guardado no fornecedor de SMS por 3 meses e aparece na tela bloqueada do celular do paciente.
- **A "Fila do dia" traz o roteiro para atender um pedido de revisão da decisão de lembrete** (LGPD, art. 20): como conferir a explicação e os dados do paciente, o que corrigir e quando pedir apoio à SaúdeJá. O procedimento completo está em [`docs/LGPD/PROCEDIMENTO-REVISAO.md`](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/LGPD/PROCEDIMENTO-REVISAO.md). Os "Detalhes técnicos" de cada linha passam a mostrar o código do agendamento, que é o que a clínica informa nesse pedido, em vez de dados do paciente.
- O README ganha a seção "Dados pessoais e LGPD": papéis, onde cada dado fica, proteções implementadas, direitos do titular e pendências. O link para o documento de LGPD, que estava quebrado desde a mudança de pasta, foi corrigido.
- O documento de LGPD registra o acordo de tratamento de dados de cada conta usada (Azure for Students, GitHub Free, Hugging Face Pro, Supabase Free e Infobip em teste): só o Supabase tem DPA, e nenhum provedor oferece as cláusulas-padrão da ANPD, então o mecanismo da transferência internacional está em aberto. Também estima o custo de levar o job diário e a aplicação para uma região brasileira.
- Decidido que, quando a eliminação a pedido do titular for implementada, os eventos de consentimento ficam mais 365 dias sem vínculo com o cadastro, como prova do consentimento e da revogação.
- **Purga das cópias locais do DVC** (`scripts/purgar_dvc_local.py`): reduz o cache local ao que o workspace usa e apaga do workspace a versão do dataset que o remote já apagou, junto com os recortes `train_raw.pkl` e `test.pkl` derivados dela. Quem desenvolve deve rodar o script depois de cada `git pull` e pelo menos uma vez por semana. O script nunca apaga nada do remote.
- O documento de LGPD declara o soft delete de 1 dia do remote do DVC e compara cada cópia e backup com a retenção do dado principal.
- **O dataset de treino e o modelo passam a ser armazenados no Brasil** (Azure, região Brazil South), e não mais no Chile. Com isso, todo dado pessoal guardado pelo sistema fica no Brasil; o processamento no GitHub Actions e no Hugging Face continua fora. A conta do Chile nunca guardou dado de paciente real e será apagada. Para quem desenvolve: a credencial local do DVC passa a ser a SAS de leitura da conta nova, no formato de connection string, e não mais a chave da conta (ver `.env.example`). A URL do remote não mudou.
- Rascunho da política de privacidade em [`docs/LGPD/POLITICA-DE-PRIVACIDADE.md`](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/LGPD/POLITICA-DE-PRIVACIDADE.md), ainda não publicado na interface.
- **O documento de LGPD lista as decisões da SaúdeJá que a clínica precisa aprovar para continuar controladora**: os dados que o modelo usa, o critério do lembrete, o texto do SMS, a retenção de 90 dias e os provedores fora do Brasil. Pelo guia da ANPD, esses são elementos essenciais do tratamento, e quem os decide é controlador. Enquanto o contrato do SaaS não trouxer essa aprovação, mudar uma feature ou a retenção é um risco de LGPD, não só uma mudança técnica. O treino do modelo compartilhado entre clínicas continua sendo da SaúdeJá sozinha, sem controladoria conjunta com a clínica, porque as finalidades são distintas.

## [v2.1.0] (Vanessa + Claude) - 2026-10-05

### Modificado
- **A interface abre na tela de agendamento do paciente.** Antes, quem chegava pela URL pública caía no login da equipe. Para entrar, a equipe escolhe o perfil "Funcionário da clínica" na barra lateral.
- O título da interface passa a ser só "SaúdeJá", sem "predição de no-show". O paciente não precisa saber, ao agendar, que o agendamento passa por uma avaliação de risco de falta.
- A barra lateral não mostra mais o ambiente, o modo de predição nem o status do banco, que apareciam para qualquer visitante. Esse diagnóstico passa para "Time técnico → Dev: disparo manual", que só existe no ambiente de desenvolvimento. Em produção, um banco fora do ar continua aparecendo como erro na "Fila do dia" e no cadastro.
- Botões principais e destaques em verde-azulado, nos temas claro e escuro, no lugar do vermelho padrão do Streamlit, que num contexto de saúde se confundia com aviso de erro.
- Os textos da "Fila do dia" usam a linguagem da recepção, sem termos técnicos como "job D-2" e "SHAP", e os ícones da interface seguem um único estilo.
- **O cadastro do paciente mostra todos os campos com problema de uma vez.** Antes, o agendamento parava no primeiro erro, e o paciente precisava reenviar uma vez para cada campo errado.
- **A data de nascimento passa a ser obrigatória**, entre 01/01/1900 e hoje. O formato do campo não mudou.
- O aviso de privacidade do cadastro está em linguagem simples, com o detalhe em "Como usamos seus dados": o CPF não é guardado, nome e telefone servem ao atendimento e ao contato, e os demais dados ajudam a clínica a decidir quais consultas confirmar.
- Depois de agendar, o formulário dá lugar à confirmação com a data e o horário da consulta, e ao botão "Fazer outro agendamento". A confirmação não promete lembrete, porque nem todo agendamento recebe um.
- **A visão da equipe tem duas abas: "Fila do dia" e "Time técnico".** A aba nova reúne o que não é rotina da recepção: "Observabilidade", "Testar predição", "Explicabilidade" e, só no ambiente de desenvolvimento, "Dev: disparo manual". Ela continua em produção, porque é lá que os números do SLO de produção são lidos. A explicação do risco de cada paciente continua no detalhe da linha da "Fila do dia".
- **O motivo do risco aparece em português**: os três fatores que mais pesaram na avaliação ("Faltas anteriores: aumenta o risco de falta"), no lugar da tabela de contribuições em log-odds e do gráfico que repetia a tabela. O dado cru, o identificador interno do paciente e a versão do modelo ficam em "Detalhes técnicos".
- A situação do agendamento aparece como "Agendado", "Realizada", "Faltou" ou "Cancelada", e não mais como `agendado`, `concluido`, `no_show` ou `cancelado`. A coluna "Fora do domínio" passa a se chamar "Previsão incerta".

### Corrigido
- Ao registrar o desfecho de um agendamento na "Fila do dia", a confirmação sumia antes de aparecer, e o funcionário não sabia se o clique tinha funcionado. Agora o aviso aparece no topo da fila.
- Um segundo clique em "Agendar" gravava o mesmo agendamento duas vezes, porque o formulário continuava preenchido na tela.
- A data de nascimento vinha preenchida com 01/01/1990. Quem não mexia no campo era cadastrado com essa data, e a idade errada chegava ao modelo sem ninguém perceber.
- Sem a lista de especialidades configurada, o cadastro aceitava qualquer texto como especialidade, e o erro só aparecia no job diário, com o agendamento recusado. Agora o cadastro é recusado na hora, com uma mensagem clara.

## [v2.0.0] (Vanessa + Claude) - 2026-10-04

Primeiro deploy do SaudeJá em produção. O SaudeJá prevê quais pacientes têm alta chance de faltar à consulta e manda lembrete por SMS só para eles, dois dias antes do atendimento, sem o custo de avisar todo mundo. A partir desta versão, cada publicação em produção ganha um número de versão e uma página de Release com as mudanças.

### Adicionado
- **Ambientes de desenvolvimento e de produção separados**, cada um com Space, banco e credenciais próprios ([architecture.md](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/architecture.md#6-deployment--infrastructure)). Enviar código ao branch `dev` publica no ambiente de desenvolvimento e aplica as migrations no banco de desenvolvimento primeiro. A produção usa um banco novo, que nunca recebe dado de teste.
- **Releases numeradas (SemVer).** Cada publicação em produção vira uma versão `vX.Y.Z` no GitHub, com estas notas. A versão vem do topo deste arquivo e é conferida antes de o banco de produção ser migrado. A troca do modelo pelo re-treino ou pelo canário sobe a versão PATCH automaticamente.
- **Alerta diário de observabilidade.** Todo dia às 10h17, um workflow confere as últimas 24 horas e avisa por e-mail se alguma predição ficou sem explicação, se houve erro (inclusive paciente em quarentena), se o job D-2 não rodou ou se ele levou mais de 10 minutos. Antes, essa degradação só aparecia para quem abrisse a aba "Observabilidade".

### Modificado
- A imagem de deploy roda como usuário não-root (uid 1000), a mesma condição do Space, e já traz o código compilado.
- **O deploy só termina verde quando o Space está no ar com a versão nova.** Antes, o workflow terminava logo depois de enviar os arquivos, mesmo que o build do Space falhasse. Agora ele espera o Space buildar o commit enviado e responder na URL pública, e falha se o build ou a inicialização quebrarem.
- O CI builda e testa a imagem a partir dos mesmos arquivos que vão para o Space, e não do repositório inteiro.
- Os pedidos de mudança abertos pelo re-treino e pelo canário pedem revisão à mantenedora, que passa a ser notificada. Um pedido parado bloqueia o re-treino seguinte.
- A avaliação diária do canário também avisa quando deixa de rodar, como já faziam o job D-2 e o re-treino.
- Mudanças só no `README.md` não republicam mais o Space nem exigem versão nova.
- **O re-treino grava no remote do DVC com uma credencial restrita.** O secret `AZURE_STORAGE_CONNECTION_STRING`, que levava a chave da conta Azure inteira, foi substituído por `AZURE_STORAGE_CONNECTION_STRING_ESCRITA`, no environment `production`: uma SAS só do container, que grava arquivos mas não apaga nenhum. Como cada arquivo do remote é nomeado pelo próprio conteúdo, uma regravação escreve os mesmos bytes e não altera o que já foi publicado. Nenhum workflow recebe mais a chave da conta.

### Corrigido
- O CI do GitHub falharia na primeira execução: quatro testes de observabilidade dependiam de uma variável que o próprio CI desliga.
- Os pedidos de mudança abertos pelo re-treino e pelo canário ficam presos a este repositório e não podem cair no repositório de origem do fork.
- Com o login por e-mail desligado na configuração do Supabase, a tela de login dizia "E-mail ou senha incorretos." a qualquer funcionário. Agora informa que o login está indisponível e pede para procurar o administrador. O Supabase local também voltou a aceitar login por e-mail: a trava de auto-cadastro estava desligando o provedor de e-mail inteiro.
- A aba "Observabilidade" podia falhar ao abrir, de vez em quando, com `Invalid isoformat string`. O horário da última execução do job vem do banco às vezes com menos casas decimais nos segundos, e a leitura recusava esse formato.

## [v1.14] (Vanessa + Claude) - 2026-10-01

### Adicionado
- **Canário do modelo.** O modelo novo aprovado pelo re-treino mensal não substitui mais o modelo em produção de uma vez. Primeiro, ele decide 20% da fila do job diário; o modelo atual decide o resto. Os pacientes são sorteados e ficam no mesmo modelo durante todo o período. Os dois modelos são comparados na fila real pela fração de pacientes que recebe lembrete pago e pela fração de faltas entre os pacientes classificados como baixo risco. O modelo novo só passa a valer para todos depois de pelo menos 7 dias sem ser pior que o atual além da margem ([ADR-008](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/adr/adr-008-canario-do-modelo.md)).
- **Volta automática ao modelo atual (rollback).** O job diário volta a usar só o modelo atual, já na execução em que o problema aparece, quando o modelo novo manda lembrete a muito mais pacientes, deixa passar mais faltas ou não consegue avaliar parte da fila. Ele também volta se, em 21 dias, não houver evidência de que o modelo novo é tão bom quanto o atual. Não é preciso deploy.
- Workflow `canario.yml`: avalia o canário todo dia às 09h47 e abre o pedido de promoção ou de reversão. Também pode ser disparado à mão para reverter, informando o motivo.
- Variável do repositório `CANARIO_DESLIGADO`: com `true`, o job diário ignora o canário a partir da próxima execução, sem pedido de mudança nem deploy.
- Tabela `canarios_revertidos` (migration `20261001000000_canarios_revertidos.sql`, aditiva), que registra cada reversão. **Aplicar no projeto remoto com `supabase db push`** (o primeiro deploy automático também o faz).
- `data/canario_historico.json` registra cada canário encerrado, promovido ou revertido, com a evidência da decisão. Um modelo revertido nunca volta a ser aprovado pelo re-treino.

### Modificado
- O re-treino mensal, ao aprovar um modelo, abre um pedido de **canário** em vez do pedido de promoção. A promoção direta continua quando ainda não há modelo em produção registrado ou com `canario.habilitado: false` em `params.yaml`.
- O re-treino mensal não roda enquanto houver canário em observação. Ele falha avisando (código de saída 4), porque um canário sem decisão no dia do re-treino é um pedido esperando aprovação.
- O resumo do job diário mostra, quando há canário, quantos agendamentos cada modelo decidiu.
- O modelo em produção (`data/champion_metrics.json`) passa a registrar, quando promovido depois de um canário, o período do canário e a evidência da promoção.

### Segurança
- O canário roda só no job diário. Ele não vai para o Space e não muda o modelo usado na interface. O sorteio usa o identificador já pseudonimizado do paciente, que não aparece em log. Nenhum dado novo é tratado.
- Um canário com qualquer problema (modelo que não baixou, aprovado contra outro modelo, banco fora do ar) faz o job usar só o modelo atual e terminar com falha. Nenhum paciente fica sem lembrete por causa do canário.

## [v1.13] (Vanessa + Claude) - 2026-09-30

### Adicionado
- **A "Fila do dia" marca a predição feita sobre dados que o modelo não viu no treino** (coluna "Fora do domínio", com aviso no detalhe da linha). Exemplos: distância acima de 50 km, consulta marcada com mais de 90 dias de antecedência, mais de 10 faltas anteriores. A probabilidade dessas linhas é extrapolação e merece menos confiança. Coluna nova `predicoes.fora_do_dominio` (migration `20260930000000_fora_do_dominio.sql`, aditiva); predições anteriores ficam sem a marca ("não verificado"). **Aplicar no projeto remoto com `supabase db push`** (o primeiro deploy automático também o faz): até lá, a "Fila do dia" local apontando para o banco remoto mostra o aviso de banco desatualizado.
- **O job diário passa a rodar sozinho todo dia às 08h17**, pelo GitHub Actions, e não no servidor da aplicação. Antes de enviar qualquer SMS, ele confere se o modelo e o threshold em uso são os aprovados no último re-treino; se não forem, não roda. Se a fila inteira do dia não puder ser predita, o job falha e avisa a equipe.
- **Deploy automático**: cada mudança aprovada em `main` passa pelos testes, aplica as migrations do banco e só então atualiza o Space. Só o modelo aprovado pelo re-treino chega a produção.
- O re-treino mensal informa, no resumo e no pedido de promoção, **quantos pacientes o modelo novo mandaria lembrete pago** — o número que liga o modelo ao custo de mensageria.

### Modificado
- **O job diário recusa agendamentos com dado impossível** (idade fora de 0 a 120, sexo inválido, horário fora do expediente da clínica, campo vazio) em vez de predizer sobre eles. O paciente recebe o lembrete mesmo assim, como já acontecia com os que não podiam ser preditos.
- **O re-treino mensal é bloqueado antes de treinar se o dataset estiver quebrado** (coluna faltando, valor impossível, especialidade que a clínica não oferece, poucas faltas registradas para a comparação com o modelo atual ser confiável). O motivo aparece no resumo do run.
- **O re-treino também bloqueia** um modelo novo com ROC-AUC abaixo de 0,60, mesmo que pareça melhor que o atual, e um modelo que se comporte de forma anormal (mesma probabilidade para todos, explicação que não fecha com a probabilidade).
- O dataset de re-treino passa a usar o **histórico de faltas registrado no momento do agendamento** — o mesmo que o modelo viu ao predizer — em vez de recalculá-lo depois. Agendamentos com dado impossível ficam de fora do dataset.
- As especialidades oferecidas no cadastro passam a vir da configuração (`params.yaml`), não do modelo treinado. A lista não mudou.

### Corrigido
- A aba "Dev: disparo manual" podia mostrar, no motivo de um erro, o dado do paciente que causou o erro (por exemplo, a data de nascimento ilegível). Agora mostra só o campo e a regra.
- O re-treino mensal rodando no GitHub Actions reexecutaria o pipeline inteiro todo mês, mesmo sem nenhum desfecho novo registrado, porque o fim de linha dos arquivos no Windows e no Linux era diferente. Ele passa a reconhecer corretamente o mês sem dado novo.

### Segurança
- O CI, o deploy e o job diário usam uma credencial **só de leitura** para baixar o modelo; a de escrita fica só no re-treino.
- O job diário roda num servidor do GitHub fora do Brasil e o log dele é público: ele registra só contagens e identificadores internos, e a redação de dado pessoal no log passa a ser a última barreira. Registrado em `docs/LGPD.md` §4 e §9.1.
- A aplicação deixou de consultar um serviço externo, na inicialização, para descobrir o próprio IP público, e de imprimi-lo no log.
- Pull requests passam por revisão automática de dependências vulneráveis.

## [v1.12] (Vanessa + Claude) - 2026-09-29

### Corrigido
- **O lembrete por SMS informava o horário da consulta 3 horas adiantado** (ex.: "às 21:00" para uma consulta das 18:00). O banco devolve data e hora em UTC, e o job diário não as convertia para o horário da clínica.
- **A predição do job diário usava esse mesmo horário deslocado.** Uma consulta das 18h era avaliada como se fosse às 21h, horário que o modelo nunca viu no treino: para o mesmo paciente, a probabilidade de falta caía de 0,48 para 0,24. As predições gravadas antes desta versão carregam esse erro.
- **O dataset de re-treino recebia as consultas reais com o horário em UTC.** A exportação passa a gravar o horário da clínica, o mesmo formato do dataset histórico.
- **Um agendamento com dado inválido não interrompe mais a fila do dia.** Antes, alguns tipos de erro abortavam o job e os pacientes seguintes ficavam sem predição.

### Modificado
- O job diário passa a considerar as consultas **de amanhã até daqui a dois dias** que ainda não têm predição, em vez de só as de daqui a dois dias. Um dia em que o job não rodou é recuperado na execução seguinte, e agendamentos feitos com um dia de antecedência também recebem predição.
- **Paciente que não pôde ser predito recebe o lembrete mesmo assim**, como exceção. O envio fica registrado com o status `enviado_sem_predicao`, separado do envio por risco alto.
- O cadastro só aceita consulta **entre hoje e 180 dias à frente**. Antes não havia limite, e dava para agendar no passado.
- O desfecho ("realizada", "faltou", "cancelado") só pode ser registrado **no dia da consulta ou depois**. Na fila de uma data futura, a tela explica isso no lugar dos botões.

### Adicionado
- Coluna `agendamentos.lembrete_enviado` (migration `20260929000000_lembrete_enviado.sql`), que registra se o paciente recebeu lembrete. O re-treino precisa dessa informação para distinguir "compareceu" de "compareceu porque foi lembrado". A coluna também entra no dataset de re-treino, mas ainda não é usada como feature. **Aplicar no projeto remoto com `supabase db push` antes de subir o código.**

## [v1.11] (Vanessa + Claude) - 2026-09-28

### Adicionado
- **Tela de login para a equipe da clínica.** A visão "Funcionário da clínica" passa a pedir e-mail e senha, verificados pelo Supabase Auth do mesmo projeto do banco. Antes do login nenhuma aba é exibida: nem a fila do dia, nem a predição manual, nem a observabilidade. A visão "Paciente" continua aberta ([architecture.md](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/architecture.md#7-security-considerations)).
- A barra lateral mostra com qual e-mail a pessoa está conectada e tem o botão **Sair**.
- `scripts/criar_funcionario.py` cria a conta de um funcionário. A senha é digitada no terminal, sem eco, e a conta nasce com o e-mail confirmado. Para desativar alguém, apague ou bana o usuário no Dashboard do Supabase.

### Modificado
- A sessão do funcionário é encerrada depois de **30 minutos sem interação**. Recarregar a página ou abrir outra aba também pede login de novo, porque a sessão fica na memória do servidor e não em cookie.
- "Sair" e o encerramento por inatividade apagam também a última predição mostrada na aba "Explicabilidade", para que quem entrar depois no mesmo navegador não a herde.

### Segurança
- Fecha a parte técnica do risco que o `LGPD.md` classificava como o mais grave: qualquer pessoa com a URL via a fila do dia, que mostra o nome do paciente. Os riscos que continuam abertos estão listados no `LGPD.md` §9: não há controle por perfil de acesso, a API não tem autenticação e o login da equipe inteira pode ser travado por tentativas em massa vindas de fora.
- Não há cadastro aberto: só o administrador cria contas. **No projeto Supabase remoto é preciso desligar "Allow new users to sign up" no Dashboard**, porque a configuração do repositório vale só para o ambiente local.
- A mensagem de login recusado é a mesma para e-mail inexistente e para senha errada, para não revelar quais e-mails pertencem à equipe.
- O token do Supabase Auth é revogado logo depois de conferir a senha e nunca é guardado. Os dados continuam sendo lidos com a chave do backend, sem alteração nas permissões do banco.

## [v1.10] (Vanessa + Claude) - 2026-09-28

### Adicionado
- A "Fila do dia" mostra o **nome do paciente** na coluna "Paciente", no registro de desfecho e no painel de explicação. Antes mostrava o hash de 64 caracteres, que não permitia chamar ninguém na sala de espera. O identificador interno continua visível em letra miúda, no detalhe da linha.
- O nome completo digitado no cadastro passa a ser gravado (`pacientes.nome_completo`). Cadastros feitos antes desta versão não têm nome — a tela mostra "(cadastro sem nome)" seguido do início do identificador, em vez de inventar um.
- Aviso na aba informando que a tela contém dado pessoal e não deve ficar exposta à sala de espera.

### Modificado
- **O CPF continua nunca sendo gravado.** O que mudou é só o nome; o identificador do paciente no banco segue sendo o hash do CPF.
- A consulta da fila do dia passou a pedir colunas específicas ao banco em vez de todas: o telefone do paciente deixou de ser trazido para a tela, onde não era usado.
- O cadastro recusa nome vazio ou com mais de 120 caracteres antes de gravar.

### Corrigido
- Quando o banco está atrás das migrations do repositório, a interface passa a dizer qual comando aplicar (`supabase db push` no projeto remoto, `supabase db reset` no local) em vez de mostrar o erro cru do PostgREST. Aparecia ao abrir a "Fila do dia" logo depois desta versão, porque a migration do nome precisa ser aplicada também no projeto remoto.

### Segurança
- O nome do paciente é autorizado **apenas** na tela da equipe da clínica. Ele não sai para log, para a tabela de observabilidade, para o dataset de treino (que é armazenado fora do Brasil), para a resposta da API pública, para a mensagem enviada pela Infobip nem para o provedor de LLM previsto. Cada uma dessas sete saídas tem verificação automatizada.
- A fronteira do LLM passou a ser aplicada pela própria interface de explicação: qualquer implementação futura recebe o contexto já sem dado pessoal, sem depender de lembrar de filtrá-lo.
- A verificação de PII no schema passou a autorizar cada exceção por coluna **e** arquivo: `telefone` e `nome_completo` só são aceitos nas migrations que os introduziram. CPF e e-mail seguem proibidos sem exceção.
- Registrado em `docs/LGPD.md` que a superfície de reidentificação aumentou, que a tela da recepção fica legível para quem espera e que a falta de autenticação passou a ser pré-requisito, não dívida: a justificativa para exibir o nome é o termo de confidencialidade de quem vê, e nada no sistema verifica quem está vendo.

## [v1.9] (Vanessa + Claude) - 2026-09-27

### Adicionado
- [`docs/LGPD.md`](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/LGPD.md): papéis (a clínica é controladora, a SaúdeJá é operadora), inventário do que é tratado, base legal, retenção, transferência internacional, direitos do titular, incidentes e riscos residuais. Fecha as duas pendências de LGPD que os passos anteriores declararam e adiaram.
- A aplicação passa a emitir log estruturado (uma linha JSON por registro) com redação automática de PII — nome, sobrenome, CPF, e-mail, telefone e IP. Vale também para o log das bibliotecas de terceiros (`uvicorn`, `httpx`, `streamlit`), que é o que existe em volume em produção.
- `scripts/auditoria_lgpd.py` varre arquivos, diretórios ou a entrada padrão em busca de PII e devolve código de saída 1 se achar algo. O relatório mostra a linha e a regra, nunca o valor encontrado.
- `predicoes` e `mensagens_disparadas` passam a ser purgadas após 365 dias, pela mesma execução diária que já limpava `eventos_app`. `pacientes` e `agendamentos` não são purgados: o registro do atendimento é da clínica.
- Novas variáveis de ambiente: `LOG_LEVEL`, `LOG_FORMATO` (`json` ou `texto`) e `RETENCAO_DADOS_DERIVADOS_DIAS`.

### Modificado
- O resumo de fim de execução do job D-2 deixou de ser uma frase em português e passou a ser uma linha JSON com os contadores, incluindo quantos registros cada purga removeu. Quem consumia essa saída por texto precisa ler os campos.
- A mensagem de erro de envio da Infobip mudou de conteúdo: onde antes vinha o corpo inteiro da resposta, agora vem o status HTTP e o código de erro do provedor. É o que aparece em "agendamentos com erro" na aba de dev.
- O compromisso de "nenhuma PII em log" do [SLA §6](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/SLA.md) passa a listar explicitamente e-mail, telefone e IP, não só nome e CPF. O [SLO §6](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/SLO.md) registra que a verificação é preventiva (filtro no código e teste automatizado), porque o log do ambiente de produção é apagado a cada reinício e não pode ser auditado depois.

### Segurança
- O telefone do paciente deixou de circular no texto de erro de envio. A Infobip ecoa o payload recebido no corpo de erro, e esse corpo ia inteiro para a lista de erros do job e para a tela de dev.
- O endereço IP de quem acessa passa a ser redigido no log de acesso, junto do restante da PII.
- A proibição de PII em log é verificada automaticamente sobre o próprio código: uma varredura falha se qualquer chamada de log em `src/` referenciar campo proibido.

## [v1.8] (Vanessa + Claude) - 2026-09-21

### Adicionado
- A clínica passa a registrar o desfecho de cada consulta na "Fila do dia" (`concluído`/`no_show`/`cancelado`), o que é o que alimenta o re-treino com dado real.
- Re-treino mensal automatizado com gate de promoção: o modelo novo só substitui o que está em produção se `recall_1`, `f1_1` e `roc_auc` não regredirem além da tolerância de `params.yaml` (`gate.tolerancia`). Regressão bloqueia a promoção, mantém o modelo anterior e falha o ciclo; um mês sem desfecho novo registrado também falha, em vez de re-treinar contra o mesmo dado.
- `data/champion_metrics.json` declara o modelo em produção (métricas medidas, `model_version`, threshold e identidade do dataset). É reescrito apenas quando o gate promove.
- `data/consultas-treino.csv` passa a ser o dataset de treino: a semente herdada (380 linhas, intocada) mais os desfechos reais exportados do banco, com `historico_noshow` calculado na data de cada consulta — nunca com informação posterior a ela.
- Novas variáveis de ambiente: `DVC_REMOTE_URL`, `AZURE_STORAGE_CONNECTION_STRING`, `HEALTHCHECKS_RETRAIN_URL`, `CONSULTAS_HISTORICAS_PATH` e `CONSULTAS_TREINO_PATH`.

### Modificado
- O armazenamento de dataset e modelo deixou de ser um diretório local e passou a ser Azure Blob Storage. Quem clonar o repositório precisa configurar o remote (`dvc remote add --local`) e a credencial antes de `dvc pull`/`dvc push`; sem isso as duas operações falham dizendo que o remote não existe.
- As métricas do modelo vigente registradas na documentação estavam erradas: `recall_1` é **0.429** (não 0.522, que vinha de outra configuração), com `f1_1` 0.419 e `roc_auc` 0.643 no threshold 0.6.

### Corrigido
- O `historico_noshow` calculado automaticamente no cadastro dependia de a coluna `status` receber `no_show`, e nada no produto escrevia esse valor desde que a coluna passou a aceitá-lo. Com o registro de desfecho na "Fila do dia", passa a funcionar.

### Segurança
- O export do dataset de treino nunca inclui telefone, nome ou CPF: `id_paciente` é o hash sha256 do CPF, e a restrição é verificada automaticamente.
- O dataset de treino e o modelo passam a ser armazenados fora do Brasil (container em Chile Central), ao contrário do banco de produção, que permanece em São Paulo. O que sai do país é pseudonimizado e sem dado de contato; a base legal dessa transferência é pendência declarada.

## [v1.7] (Vanessa + Claude) - 2026-09-21

### Adicionado
- Aba "Observabilidade" no Streamlit: latência p95, erros, cobertura de explicação e última execução do job, com janela de 24h/7d/30d.
- Tabela `eventos_app` no Supabase registra predições, requisições da API e execuções do job, separadas pela coluna `origem` (`api`/`processo`/`job`). `/health` fica de fora de propósito, para a sonda externa não encher a tabela de ruído.
- Retenção configurável por `OBSERVABILIDADE_RETENCAO_DIAS` (default 90), purgada pelo job diário.

### Modificado
- O registro de eventos é não bloqueante: nunca atrasa nem derruba a predição ou o job. Fila cheia descarta o evento; destino fora do ar pausa as tentativas por 60s.
- A instrumentação revelou o custo real da primeira predição de cada processo: ~1,4s (montagem do explicador SHAP) contra ~10ms nas seguintes — dentro do SLO §2 (<2s), mas apertado num ambiente que hiberna.

### Corrigido
- A suíte de UI apagava as credenciais do Supabase, mas `load_dotenv()` as redefinia no import: quem tivesse `.env` local rodava os testes contra o projeto Supabase real.

### Segurança
- `eventos_app` nunca recebe PII: as chaves de `detalhe` são restritas a uma allowlist fechada e exceções registram só o nome da classe, nunca a mensagem (que ecoaria o telefone enviado à Infobip).

## [v1.6] (Vanessa + Claude) - 2026-09-21

Versão só de documentação, sem mudança de comportamento: o [ADR-006](https://github.com/VL-in/ai-factory-saudeja/blob/main/docs/adr/adr-006-observabilidade.md) decide a observabilidade de aplicação (tabela no Supabase como fonte de verdade, aba no Streamlit, sonda de uptime e dead-man's-switch externos), descarta Langfuse/OpenTelemetry/Grafana para o núcleo e adia o Sentry. Implementado na v1.7.

## [v1.5] (Vanessa + Claude) - 2026-09-20

### Adicionado
- Envio real de lembretes por SMS via Infobip, ligado por `MESSAGING_PROVIDER=infobip` (`stub`, que nunca toca a rede, continua o default). WhatsApp Business não foi implementado: exige sender/template pré-aprovados pela Meta.
- Campo de telefone no formulário de cadastro, normalizado para o formato internacional e validado antes de gravar.

### Modificado
- `MessagingClient.enviar_lembrete` passou a receber `telefone` em vez de `id_paciente_externo`.
- `mensagens_disparadas.canal` grava o canal real (`stub`/`sms`) no lugar do `"whatsapp"` fixo.
- Nova coluna `pacientes.telefone` (migration `20260920020000_telefone_paciente.sql`): exceção deliberada à minimização de PII, porque sem contato não há para onde mandar o lembrete. Nome, CPF e e-mail continuam proibidos no banco.

### Corrigido
- Falha no envio de um paciente (Infobip fora do ar, número rejeitado, credencial inválida) vira `status_envio="falha_envio"` e não derruba o resto da fila do dia.

## [v1.4] (Vanessa + Claude) - 2026-09-20

### Adicionado
- Cadastro do paciente pede nome completo, CPF (com validação de dígito verificador) e data de nascimento.
- O horário da consulta é escolhido na grade real da clínica (seg-sex 08h-11h30/13h-18h, sáb 08h-11h30, fechado aos domingos), em vez de um campo de hora livre.
- `historico_noshow` passou a ser contado automaticamente pelos agendamentos com status `no_show`.

### Modificado
- `pacientes.idade` foi substituída por `pacientes.data_nascimento` (migration `20260920010000_cadastro_pacientes.sql`); a idade é calculada sob demanda, em relação à data da própria consulta. O contrato de `construir_features` não mudou.
- `id_paciente_externo` passou a ser o hash sha256 do CPF, gerado automaticamente.
- `agendamentos.status` aceita o valor `no_show`.

### Removido
- Campos de "identificador", idade e histórico de no-show autodeclarado do formulário de cadastro.

### Segurança
- Nome, CPF e data de nascimento não são persistidos em texto puro; o nome só aparece na confirmação em tela.

## [v1.3] (Vanessa + Claude) - 2026-09-20

### Adicionado
- Job de inferência diária D-2 (`src/jobs/inferencia_diaria.py`): busca os agendamentos D+2 ainda sem predição, grava predição e explicação SHAP e decide o disparo do lembrete conforme o threshold de `params.yaml`. Agendamento malformado é registrado e pulado, sem derrubar a fila do dia.
- Interface de mensageria (`MessagingClient`), com stub como default que nunca faz rede.
- Auditoria de disparo em `mensagens_disparadas`, incluindo quem não recebeu lembrete (SLA §6).
- A aba "Dev: disparo manual" deixou de ser placeholder e executa o job, mostrando agendamentos encontrados, predições gravadas, mensagens disparadas e erros.

### Modificado
- Índice em `agendamentos.id_paciente` (migration `20260920000000_idx_agendamentos_id_paciente.sql`): a fila do dia e o job não forçam mais sequential scan a cada execução.

## [v1.2] (Vanessa + Claude) - 2026-09-19

### Adicionado
- Fila do dia com resumo (total, alto risco, sem predição), probabilidade em barra e, ao selecionar a linha, a explicação SHAP gravada pelo job D-2.
- Status do Supabase na sidebar, como aviso: sem banco, predição manual e explicabilidade continuam funcionando.

### Modificado
- `dias_entre_agendamento_consulta` deixou de ser perguntado no cadastro e passou a ser derivado da data escolhida. O formulário de teste do funcionário mantém o campo manual.
- Predição sem explicação aparece como aviso explícito (violação do SLO §4) em vez de tabela vazia.

### Corrigido
- Fuso horário: a fila do dia e o job D-2 filtravam a data civil em UTC, então a "fila de 19/09" cobria das 21h de 18/09 às 21h de 19/09 no horário local — o job pularia agendamentos do fim do dia. O fuso da clínica passa a ser configurável por `TIMEZONE_CLINICA` (default `America/Sao_Paulo`).
- O cadastro gravava o horário sem fuso e a fila o exibia em UTC (uma consulta das 08:30 aparecia como 11:30).

## [v1.1] (Vanessa + Claude) - 2026-09-19

### Adicionado
- Persistência no Supabase (`sa-east-1`, sem transferência internacional de dados): tabelas `pacientes`, `agendamentos`, `predicoes` e `mensagens_disparadas`, com RLS habilitado e sem policies — só a chave secreta do backend acessa.
- Fila do dia e visão Paciente passaram a ler e gravar de verdade; falha de configuração ou conexão vira mensagem amigável (`ErroPersistencia`) em vez de traceback.
- `SUPABASE_URL`/`SUPABASE_SECRET_KEY` lidas do `.env` (o formato novo de chave do Supabase, `sb_secret_...`, substitui o JWT `service_role`), sem sobrescrever o que já estiver definido no ambiente.

### Segurança
- O esquema não tem coluna de nome, CPF, e-mail ou telefone: o paciente é identificado só por `id_paciente_externo` mais demografia não identificável.

## [v1.0] (Vanessa + Claude) - 2026-09-19

### Adicionado
- Interface Streamlit: visão Funcionário com as abas "Testar predição", "Explicabilidade", "Fila do dia" e "Dev: disparo manual" (renderizada só com `APP_ENV=dev`), e visão Paciente.
- `PREDICT_BACKEND` escolhe entre chamar o modelo em processo (default de produção) ou a API por HTTP (`API_BASE_URL`), modo de desenvolvimento e diagnóstico. Valor desconhecido falha alto, sem cair em default silencioso.
- Imagem combinada de deploy (`infra/deploy/dockerfile`) rodando API e UI no mesmo container, com o modelo empacotado na imagem; serviço `app` no `docker-compose.yml` (UI em 7860, API em 8000, healthcheck em `/health`). Build sem `dvc pull` falha de propósito, em vez de gerar container sem modelo.
- Novo `requirements/ui.txt`, separado de `api.txt` para a imagem da API continuar enxuta (cold start, SLO §2).

### Modificado
- Dado recusado e backend fora do ar viraram erros distintos na tela (`ErroValidacao`, `ErroIndisponivel`), com mensagem acionável — inclusive para especialidade desconhecida e sexo inválido.
- O cálculo de `model_version` saiu da API para `inference.calcular_model_version()`, para API, UI e a coluna `model_version` produzirem a mesma string para o mesmo artefato. Resultado inalterado (sha256, 12 caracteres).

## [v0.17] (Vanessa + Claude) - 2026-09-19

### Corrigido
- `params.yaml` e os caminhos de `data/` passaram a ser resolvidos a partir da raiz do repositório, não do diretório de execução: a API iniciada fora da raiz quebrava no import.
- `explain.explicar()` exige uma única linha e ganhou `explicar_lote()`. Antes devolvia em silêncio a explicação da linha 0, o que gravaria a explicação do paciente errado na fila do job D-2 (SLO §4).
- `preprocessar()` e `extrair_features_temporais()` não mutam mais o DataFrame recebido.
- `model_version` passou a usar sha256 no lugar de md5, que derruba o startup em host FIPS.

## [v0.16] (Vanessa + Claude) - 2026-09-19

### Adicionado
- API FastAPI com `/health` (status + `model_version`) e `/predict`, validando a entrada com Pydantic e reusando `src/inference.py`/`src/explain.py`. Modelo, mapa de especialidade e explicador SHAP são carregados uma vez no startup, não por requisição (SLO de latência p95 < 2s). Especialidade desconhecida devolve 422, nunca 500 nem predição sobre NaN.
- `requirements.txt` dividido em `requirements/base.txt`, `train.txt` e `api.txt`, e novo `infra/api/dockerfile`, que instala só as dependências da API e monta `data/` por volume.

### Corrigido
- `src/validate.py`: com uma única classe em `y_test`, o `roc_auc_score` do scikit-learn 1.5.2 derrubava o stage `validate` inteiro. Agora `roc_auc`/`pr_auc` caem para 0.0 e a run fica marcada com `aviso_split`, como já acontecia com as demais métricas.

## [v0.15] (Vanessa + Claude) - 2026-09-19

### Adicionado
- Explicabilidade por SHAP (`src/explain.py`): contribuições de cada feature por predição, ordenadas por impacto absoluto e prontas para serializar. O cálculo é síncrono dentro da inferência, o que garante cobertura de 100% das predições por construção (SLO §4).
- Plug inativo para explicação em linguagem natural por LLM: a implementação default retorna `None` e nunca abre rede.
- Dependência `shap`.

## [v0.14] (Vanessa + Claude) - 2026-09-19

### Adicionado
- `src/inference.py`, módulo de inferência reusável pela API e pelo job diário, sem duplicar lógica nem re-treinar. Aplica o mapa de especialidade fixado no treino em vez de recalculá-lo — recalcular, em produção com uma linha por vez, mapearia toda especialidade para `0`.
- `EspecialidadeDesconhecidaError` substitui, no caminho de inferência, o NaN silencioso gerado pelo pré-processamento.

## [v0.13] (Vanessa + Claude) - 2026-09-18

### Removido
- `infra/ML/dockerfile`: `COPY` auto-referencial sobre base divergente da imagem real do pipeline, não referenciado por `dvc.yaml` nem pelo `docker-compose.yml`. Resíduo do protótipo herdado.

### Modificado
- Documentação: ADR-001 marcado como parcialmente substituído pelo ADR-004 (banco, mensageria, gateway de LLM e plataforma de deploy); novo ADR-005 registra o scheduler D-2 via GitHub Actions e a chamada do modelo em processo; `architecture.md` completado, com o gap de recall (0,522 contra o alvo 0,75 do SLO) registrado como débito conhecido.

## [v0.12] (Vanessa + Claude) - 2026-09-16

### Adicionado
- `src/tune.py`: busca de hiperparâmetros por GridSearchCV sobre o Pipeline SMOTENC+LightGBM, com SMOTENC recalculado a cada fold para não vazar sintéticos entre treino e validação. Script exploratório, fora do `dvc.yaml`.
- Documentação: ADR-004 propõe a stack de produto e o roteiro de implementação até o produto deployável.

### Modificado
- Os hiperparâmetros de `params.yaml` não mudaram: os encontrados pelo GridSearch saíram piores no fold de teste isolado (f1_1 0,372 contra 0,419). O dataset atual (~380 linhas) é pequeno demais para o tuning fino generalizar.

## [v0.11] (Vanessa) - 2026-09-16

### Adicionado
- Threshold de decisão como parâmetro rastreável.

## [v0.10] (Vanessa + Claude) - 2026-09-15

### Modificado
- Pipeline segregado em três stages DVC: `preprocess` (carrega o CSV, aplica o feature engineering e faz o split estratificado antes de qualquer balanceamento), `train` (aplica SMOTE-NC só no fold de treino, abre a run no MLflow e grava o `run_id`) e `validate` (calcula as métricas sobre o fold de teste, nunca balanceado, reabrindo a mesma run).

## [v0.9] (Vanessa + Claude) - 2026-09-15

### Adicionado
- Coluna `data_hora_agendada` no dataset, gerada por `scripts/gerar_timestamp_sintetico.py` respeitando a grade real da clínica (seg-sex 08h-11h30/13h-18h, sábado 08h-11h30, sem domingo).
- Features temporais `dia_de_semana` e `horario` (`src/features.py`), compartilhadas entre o pipeline de treino e a inferência, sob o parâmetro `features.temporais`.

### Modificado
- `features.temporais` promovido a `true` como padrão, por ganho medido nas métricas de negócio: f1_1 0,356 → 0,409, recall_1 0,381 → 0,429, pr_auc 0,368 → 0,397.
- As categóricas passaram a ser declaradas tanto ao SMOTENC quanto ao LightGBM, evitando interpolação de valores fracionários que não existem no calendário (ex.: `dia_de_semana=2.7`).

## [v0.8] (Vanessa) - 2026-09-08

### Adicionado
- Hiperparâmetros extraídos para `params.yaml`.
- Modelo registrado e promovido no MLflow.

### Corrigido
- O MLflow não gravava as runs; passou a usar servidor remoto. `mlflow ui` não é mais necessário localmente — acesse http://localhost:5000 direto no navegador.
- Classe positiva ausente do fold de teste: a métrica 0.0 continua sendo logada (para não quebrar o pipeline), mas marcada com a tag `aviso_split` em vez de se misturar silenciosamente com métricas reais.

## [v0.7] (Vanessa) - 2026-09-07

Versão só de testes, sem mudança de comportamento: suíte pytest cobrindo o treino, a coerência entre `.gitignore`/git/`dvc.yaml`/`dvc.lock` e o pipeline completo via Docker.

## [v0.6] (Vanessa) - 2026-09-07 - feat
- Implementação do DVC e MLFlow para rastreabilidade do conjunto de dados e treinamento.

## [v0.5] (Camila)
- Notebook completo com EDA + treino LightGBM
- Modelo serializado em model.pkl
- Acurácia 78%, F1 da classe positiva 0.65

## [v0.4] (Camila)
- Adicionada feature distancia_km

## [v0.3] (Camila)
- Primeira versão LightGBM substituindo regressão logística
