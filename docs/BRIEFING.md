# Briefing — SaúdeJá

**Disciplina:** AI Factory: Deploy and Exhibit (4DPJC174)
**Empresa-cliente (fictícia):** SaúdeJá Tecnologia em Saúde S.A.
**Setor:** Healthtech B2B — software de gestão para clínicas particulares brasileiras
**Sua função:** ML Engineer Júnior, alocado(a) no time de Produto

---

## A empresa

SaúdeJá é uma healthtech brasileira de Série A (US$ 12M levantados há alguns anos), com cerca de 80 funcionários, sede em São Paulo e clientes em 11 estados. Vende uma plataforma SaaS para clínicas particulares de pequeno e médio porte: agendamento online, prontuário, faturamento e comunicação com pacientes.

**Missão declarada:** "Reduzir o no-show (paciente que falta à consulta sem avisar) nas clínicas brasileiras". O no-show custa em média **R$ 180 por consulta perdida** e a taxa nacional gira em torno de 25-35%. Para uma clínica média (1.500 consultas/mês), isso significa R$ 70k-100k de receita evaporando todo mês.

## O problema

A primeira tentativa do produto foi mandar **lembrete por SMS + WhatsApp para 100% dos pacientes 24h antes**. Funcionou — mas o custo unitário das mensagens (WhatsApp Business + SMS) explodiu, e a margem virou negativa nas clínicas menores.

**Hipótese atual:** se conseguirmos prever *quais* pacientes têm alta probabilidade de faltar, mandamos lembretes (e até ligamos) só para esses, mantendo o efeito e cortando 70% do custo de mensageria.

## O que você está herdando

A **Camila**, cientista de dados sênior, treinou um classificador de no-show em LightGBM em um Jupyter notebook ao longo do fim do ano passado. Ela saiu da empresa em janeiro (foi pra Nubank). O que ficou:

- `notebook.ipynb` — EDA + treino + serialização. Roda. Acurácia 78%, F1 da classe positiva 0.65.
- `model.pkl` — modelo serializado com joblib.
- `data/consultas-historicas.csv` — ~400 consultas históricas (dados **sintéticos** anonimizados pela própria Camila para o protótipo).
- `train.py` — script meio-pronto que ela começou a tirar do notebook.
- `docs/notas-camila.md` — anotações soltas em markdown.

**Nunca saiu do notebook.** Não tem API, não tem interface, não tem deploy, não tem monitoramento, não tem pipeline de re-treino.

## Expectativas (semestre)

1. **API** que recebe um paciente (idade, sexo, especialidade, distância, dias até consulta, histórico de no-show) e devolve a probabilidade de no-show.
2. **Interface mínima** para a clínica visualizar a fila do dia ordenada por risco.
3. **Lógica de negócio:** só dispara lembrete pago (WhatsApp/SMS) se prob ≥ threshold (calibrar).
4. **Pitch final** para o Conselho de Investidores na Semana 16: custo de infra, ganho de receita projetado, riscos LGPD.

## Restrições

- **Orçamento de infra:** US$ 100/mês. Treino já foi feito; você paga só inferência + hospedagem.
- **LGPD — categoria especial.** Dados de saúde são dados sensíveis (Art. 5º, II e Art. 11 da LGPD). Mesmo sendo sintéticos no protótipo, o aluno deve tratar **como se fossem reais** desde já.
- **Re-treino mensal** acordado com o time de Produto — você precisará deixar o pipeline executável.
- **Sem PII em logs.** Nunca.