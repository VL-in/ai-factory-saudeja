# Notas soltas — Camila

Bagunça mental antes das férias. Quem pegar isso depois: boa sorte 🫶

- **Re-treino mensal**: o modelo precisa rodar de novo todo mês com os dados frescos das clínicas. Hoje é manual (eu mesma rodava o notebook). Tem que virar pipeline — cron + script + alerta se a métrica cair.
- **Acurácia 78% é OK** mas o **F1 da classe positiva (no-show=1) tá em 0.65**. Pro caso de uso de mandar lembrete, o que importa é recall da classe 1 (não quero deixar de avisar quem ia faltar). Investigar **SMOTE / class_weight='balanced' / threshold tuning**. Tem espaço fácil pra subir.
- **Explicabilidade**: não tive tempo de plugar SHAP. A diretora médica (Dra. Helena) **vai pedir** — ela não aceita "caixa preta" pra decisão clínica/operacional. Prioridade alta.
- **LGPD**: dados de saúde = **categoria especial** (Art. 11). Mesmo o protótipo usando dados sintéticos, qualquer pipeline real precisa: criptografia em repouso, log sem PII, base legal documentada (consentimento + execução de contrato), DPO ciente. **Não loga CPF nem nome em lugar nenhum**, nunca.
- **Feature que falta**: dia da semana e horário da consulta. Suspeito que sexta 18h tem no-show MUITO maior. Não consegui puxar do banco a tempo.
- **Threshold default 0.5 não serve**. Calibrar com base no custo unitário do SMS vs. perda do no-show.

— Camila, no fim do ano passado