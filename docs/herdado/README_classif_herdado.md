# SaúdeJá - Classificador de No-show

Notebook com classificador LightGBM pra prever no-show de paciente em consulta.

**Acurácia 78% no test set (split 80/20, random_state=42).**
**F1 classe positiva (no-show=1): 0.65**
**ROC-AUC: 0.81**

## Features usadas

- `idade` — idade do paciente (int)
- `sexo` — F/M (binarizado 0/1)
- `especialidade` — label encoding (cardiologia, dermato, ginecologia, ortopedia, oftalmo, clínica geral, pediatria)
- `distancia_km` — distância casa-clínica
- `dias_entre_agendamento_consulta` — quanto antes o paciente agendou
- `historico_noshow` — quantas vezes esse paciente já faltou

Target: `no_show` (0 = compareceu, 1 = faltou).

## Como rodar

```
jupyter notebook
```

Abre `notebook.ipynb` e roda tudo (Run All). Treino leva uns 30s no meu macbook.

Salva `model.pkl` na raiz.

## Modelo

LightGBM com:
- n_estimators=200
- learning_rate=0.05
- max_depth=6
- num_leaves=31

Tunei na mão olhando F1. Não rodei GridSearch ainda (TODO).

## TODO

- precisamos transformar isso em API pra integrar com sistema das clínicas — vou abrir ticket no Jira (SAUDEJA-241)
- SHAP pra explicabilidade (liderança médica vai pedir)
- oversampling SMOTE? classe positiva tá em ~30%, dá pra melhorar F1
- pipeline de re-treino mensal — hoje é manual
- testes? lol

---

*Camila S. — Data Science*