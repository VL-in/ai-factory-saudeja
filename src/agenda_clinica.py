"""
SaúdeJá — regras de funcionamento da clínica (Passo 4/5: cadastro do
paciente precisa oferecer só datas/horários que a clínica de fato atende).

Fonte de verdade das regras: scripts/gerar_timestamp_sintetico.py, que gerou
`data_hora_agendada` do dataset histórico a partir desta mesma grade. Um
agendamento fora dela (domingo, antes das 8h, dentro do almoço, sábado à
tarde) cairia fora do domínio que o modelo aprendeu -- não é só um detalhe de
UX, é o que evita treino/produção verem distribuições diferentes de
dia_de_semana/horario (src/features.py::extrair_features_temporais).

Regra de negócio (não genérica, mesma grade do script):
- seg-sex: 08:00-11:30 e 13:00-18:00, slots de 30 min (sem almoço 12h-13h).
- sábado: 08:00-11:30, slots de 30 min.
- domingo: fechado.
"""
from datetime import date, time, timedelta

DIA_SABADO = 5  # date.weekday(): 0=segunda ... 6=domingo
DIA_DOMINGO = 6


def _slots(inicio_h: int, inicio_m: int, fim_h: int, fim_m: int) -> list[time]:
    slots = []
    h, m = inicio_h, inicio_m
    while (h, m) <= (fim_h, fim_m):
        slots.append(time(h, m))
        m += 30
        if m == 60:
            m = 0
            h += 1
    return slots


SLOTS_UTEIS = _slots(8, 0, 11, 30) + _slots(13, 0, 18, 0)  # segunda a sexta
SLOTS_SABADO = _slots(8, 0, 11, 30)  # sábado


def horarios_do_dia(dia: date) -> list[time]:
    """Slots válidos para `dia` -- lista vazia se for domingo (clínica
    fechada). O cadastro (src/ui/app.py) usa isto para oferecer um selectbox
    em vez de um campo de hora livre."""
    dia_semana = dia.weekday()
    if dia_semana == DIA_DOMINGO:
        return []
    if dia_semana == DIA_SABADO:
        return list(SLOTS_SABADO)
    return list(SLOTS_UTEIS)


def horario_valido(dia: date, hora: time) -> bool:
    """Checagem de defesa em profundidade antes de gravar -- o formulário já
    restringe as opções, mas o dado pode chegar de outro caminho (API/job de
    outro caller no futuro)."""
    return hora in horarios_do_dia(dia)


def proximo_dia_valido(a_partir_de: date) -> date:
    """`a_partir_de` se a clínica abrir nesse dia, senão o próximo dia que
    abrir -- usado para a data default do formulário nunca cair num domingo
    sem horário nenhum disponível."""
    dia = a_partir_de
    while not horarios_do_dia(dia):
        dia += timedelta(days=1)
    return dia
