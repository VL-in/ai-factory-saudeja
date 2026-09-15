"""
Gera a coluna data_hora_agendada em data/consultas-historicas.csv.

Grade de agendamento da clinica (regra de negocio, nao generica):
- seg-sex: 08:00-11:30 e 13:00-18:00, slots de 30 min (almoco 12:00-13:00 sem
  agendamento).
- sabado: 08:00-11:30, slots de 30 min.
- domingo: fechado.

Duas camadas de geracao:
1. Distribuicao marginal realista de dia da semana / horario de agenda de
   clinica (independente do rotulo), amostrada diretamente da lista de slots
   validos -- nunca dia+hora sorteados de forma independente, o que geraria
   combinacoes impossiveis (ex.: sabado as 15h, ou 12:30 numa terca-feira).
2. Injecao controlada (15% das linhas com no_show=1) da hipotese da Camila --
   sexta no fim do expediente (17h-18h) tem mais no-show. Isso e uma escolha
   de design didatica, documentada em data/AVISO-DADOS-SINTETICOS.md, nao e
   uma medicao real. O percentual (15%) foi calibrado comparando 35/20/15/10%
   via simulacao: mantem o sinal claramente acima da media geral e do resto
   do dataset sem deixar o recorte "sexta 17h-18h" pequeno demais (esparso)
   nem proximo de uma separacao deterministica entre classes.

O valor persistido no CSV e sempre um timestamp unico (data_hora_agendada,
formato "YYYY-MM-DD HH:MM:SS"), do jeito que um sistema de agendamento real
registraria no banco. dia_de_semana e horario sao dados derivados -- nao sao
gravados aqui, so extraidos em codigo no pre-processamento
(ver src/features.py), para nao correr risco de training-serving skew.
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

RANDOM_STATE = 42  # mesma seed usada em src/train.py
ANCORA = datetime(2026, 1, 5)  # segunda-feira de referencia

rng = np.random.default_rng(RANDOM_STATE)
df = pd.read_csv("data/consultas-historicas.csv")
n = len(df)

# --- 1. grade de slots validos (30 em 30 min) ---
def _slots(inicio_h, inicio_m, fim_h, fim_m):
    slots = []
    h, m = inicio_h, inicio_m
    while (h, m) <= (fim_h, fim_m):
        slots.append((h, m))
        m += 30
        if m == 60:
            m = 0
            h += 1
    return slots

SLOTS_UTEIS = _slots(8, 0, 11, 30) + _slots(13, 0, 18, 0)  # 19 slots
SLOTS_SABADO = _slots(8, 0, 11, 30)  # 8 slots

HORAS_UTEIS = np.array([h for h, _ in SLOTS_UTEIS])
MINUTOS_UTEIS = np.array([m for _, m in SLOTS_UTEIS])
HORAS_SABADO = np.array([h for h, _ in SLOTS_SABADO])
MINUTOS_SABADO = np.array([m for _, m in SLOTS_SABADO])

# 3 picos de popularidade (independentes do rotulo): manha, inicio de tarde
# pos-almoco e fim de expediente -- fim de dia costuma ser concorrido por
# quem so pode ir apos o trabalho, nao e exclusividade de sexta-feira.
PESOS_SLOT_UTEIS = np.array(
    [2, 3, 6, 7, 6, 4, 3, 2,       # 08:00-11:30 (pico ~09:30)
     6, 7, 6, 4, 3, 3, 3, 4,       # 13:00-16:30 (pico ~13:30, vale meio da tarde)
     6, 7, 6],                     # 17:00-18:00 (pico fim de expediente)
    dtype=float,
)
PESOS_SLOT_UTEIS /= PESOS_SLOT_UTEIS.sum()

PESOS_SLOT_SABADO = np.array([3, 4, 6, 6, 5, 4, 3, 2], dtype=float)
PESOS_SLOT_SABADO /= PESOS_SLOT_SABADO.sum()

# --- 2. distribuicao marginal de dia da semana (sem olhar para no_show) ---
dias_semana = [0, 1, 2, 3, 4, 5]  # 0=segunda ... 5=sabado (sem domingo)
pesos_dia = [0.19, 0.20, 0.20, 0.18, 0.15, 0.08]  # soma 1.00

dia_amostrado = rng.choice(dias_semana, size=n, p=pesos_dia)

idx_sabado = dia_amostrado == 5
idx_util = ~idx_sabado

hora_amostrada = np.empty(n, dtype=int)
minuto_amostrado = np.empty(n, dtype=int)

slot_idx_util = rng.choice(len(SLOTS_UTEIS), size=idx_util.sum(), p=PESOS_SLOT_UTEIS)
hora_amostrada[idx_util] = HORAS_UTEIS[slot_idx_util]
minuto_amostrado[idx_util] = MINUTOS_UTEIS[slot_idx_util]

slot_idx_sabado = rng.choice(len(SLOTS_SABADO), size=idx_sabado.sum(), p=PESOS_SLOT_SABADO)
hora_amostrada[idx_sabado] = HORAS_SABADO[slot_idx_sabado]
minuto_amostrado[idx_sabado] = MINUTOS_SABADO[slot_idx_sabado]

# --- 3. injecao controlada da hipotese (sexta fim de expediente -> mais no-show) ---
SLOTS_NOITE_SEXTA = [(17, 0), (17, 30), (18, 0)]

idx_no_show = df.index[df["no_show"] == 1]
n_reforcar = int(0.15 * len(idx_no_show))
idx_reforcar = rng.choice(idx_no_show, size=n_reforcar, replace=False)

slot_noite = rng.choice(len(SLOTS_NOITE_SEXTA), size=n_reforcar)
dia_amostrado[idx_reforcar] = 4  # sexta
hora_amostrada[idx_reforcar] = [SLOTS_NOITE_SEXTA[i][0] for i in slot_noite]
minuto_amostrado[idx_reforcar] = [SLOTS_NOITE_SEXTA[i][1] for i in slot_noite]

# --- 4. materializa como timestamp unico (formato tipo banco de dados) ---
timestamps = [
    ANCORA + timedelta(days=int(d), hours=int(h), minutes=int(m))
    for d, h, m in zip(dia_amostrado, hora_amostrada, minuto_amostrado)
]
df["data_hora_agendada"] = pd.Series(timestamps).dt.strftime("%Y-%m-%d %H:%M:%S")

df.to_csv("data/consultas-historicas.csv", index=False)
print(f"[ok] data_hora_agendada gerada para {n} linhas "
      f"({n_reforcar} reforcadas para sexta no fim do expediente)")
