"""
SaúdeJá — testes de src/agenda_clinica.py: grade de horários que o cadastro
do paciente (Passo 5, src/ui/app.py) usa para nunca oferecer um agendamento
fora da jornada da clínica -- a mesma regra que gerou
data_hora_agendada no dataset histórico (scripts/gerar_timestamp_sintetico.py).
"""
from datetime import date, time, timedelta

import agenda_clinica

# 2026-01-05 é a mesma segunda-feira de referência usada em
# scripts/gerar_timestamp_sintetico.py (ANCORA) -- somar múltiplos de 7 dias
# garante cair sempre no mesmo dia da semana, sem depender de contar o
# calendário à mão.
_ANCORA_SEGUNDA = date(2026, 1, 5)
SEGUNDA = _ANCORA_SEGUNDA
SABADO = _ANCORA_SEGUNDA + timedelta(days=5)
DOMINGO = _ANCORA_SEGUNDA + timedelta(days=6)


def test_domingo_nao_tem_horario_nenhum():
    assert agenda_clinica.horarios_do_dia(DOMINGO) == []


def test_sabado_so_vai_ate_11h30():
    horarios = agenda_clinica.horarios_do_dia(SABADO)

    assert horarios[0] == time(8, 0)
    assert horarios[-1] == time(11, 30)
    assert time(12, 0) not in horarios


def test_dia_util_pula_o_horario_de_almoco():
    horarios = agenda_clinica.horarios_do_dia(SEGUNDA)

    assert time(11, 30) in horarios
    assert time(12, 0) not in horarios
    assert time(12, 30) not in horarios
    assert time(13, 0) in horarios
    assert horarios[0] == time(8, 0)
    assert horarios[-1] == time(18, 0)


def test_horario_valido_confere_dia_e_hora_juntos():
    assert agenda_clinica.horario_valido(SEGUNDA, time(9, 0)) is True
    assert agenda_clinica.horario_valido(SEGUNDA, time(12, 30)) is False
    assert agenda_clinica.horario_valido(DOMINGO, time(9, 0)) is False
    assert agenda_clinica.horario_valido(SABADO, time(14, 0)) is False


def test_proximo_dia_valido_pula_domingo_para_segunda():
    assert agenda_clinica.proximo_dia_valido(DOMINGO) == DOMINGO + timedelta(days=1)


def test_proximo_dia_valido_mantem_dia_que_ja_atende():
    assert agenda_clinica.proximo_dia_valido(SEGUNDA) == SEGUNDA
    assert agenda_clinica.proximo_dia_valido(SABADO) == SABADO
