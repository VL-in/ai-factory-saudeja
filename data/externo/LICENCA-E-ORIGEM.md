# Dataset externo de desfecho real: licença e origem

**Os dados desta pasta são de 2016, da rede pública de Vitória-ES, e não são pacientes do SaúdeJá.** Eles servem para testar o pipeline e medir o sinal das features com desfecho real ([ADR-014](../../docs/adr/adr-014-dataset-externo-desfecho-real.md)). Métricas medidas sobre eles **não** são desempenho do SaúdeJá. A frase certa é "sinal em dado público real".

## Fonte e atribuição

- **Dataset:** *Medical Appointment No Shows*, de Joni Hoppen (`joniarroba/noshowappointments`).
- **Link:** https://www.kaggle.com/datasets/joniarroba/noshowappointments
- **Licença:** CC BY-NC-SA 4.0 (https://creativecommons.org/licenses/by-nc-sa/4.0/): atribuição, uso não comercial e derivados sob a mesma licença.
- **Download:** 2026-11-21, arquivo `raw/medical-appoint-no-shows2016.csv`, com sha256 `9132d3e7d0246617df9041d3764f20ad6f08e7b0d9f0997fa254fc5e52eda27d`.
- **Uso:** acadêmico. O SaúdeJá é projeto de estudo, e o modelo não atende caso real.

## O que há aqui e onde fica

| Arquivo | O que é | Onde fica |
|---|---|---|
| `raw/medical-appoint-no-shows2016.csv` | bruto do Kaggle, 110.527 consultas | DVC (`raw.dvc`), fora do git |
| `pool-adaptado.csv` | população do SaúdeJá no formato do export | DVC, fora do git |
| `teste-reservado.csv` | ~10 mil consultas de pacientes separados, só para a avaliação final | DVC, fora do git |
| `consultas-externas.csv` | amostra de treino de 1.500 consultas | DVC, fora do git |
| `manifesto.json` | sha256 do bruto, remoções por filtro, contagens, `random_state` | DVC, fora do git |

Todo derivado é obra derivada do dataset e herda a CC BY-NC-SA 4.0, com esta mesma atribuição.

## Modificações feitas sobre o original

A licença pede que as mudanças sejam indicadas. Nos derivados:

- só entram consultas de 1 a 90 dias de antecedência e idade de 0 a 85 anos;
- especialidade, distância e horário **não existem no original**: são sorteados da semente sintética do SaúdeJá;
- o horário das consultas de sexta foi rearranjado para que 17h-18h tenha 1,25 vezes a falta do resto. É um **sinal sintético declarado** (ADR-014, decisão 6), não um dado de Vitória. Desfecho, data e dia da semana são os originais.

## Tratamento de dado pessoal

O dataset é público e pseudonimizado, mas não anonimizado. Na adaptação:

- as colunas de saúde (`Hipertension`, `Diabetes`, `Alcoholism`, `Handcap`), `Scholarship` e o bairro (`Neighbourhood`) **não saem do bruto**;
- o `PatientId` vira um pseudônimo sha256 com sal local, que não está no repositório;
- os scripts imprimem só agregados, nunca uma linha.
