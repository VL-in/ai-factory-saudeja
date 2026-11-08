"""
SaúdeJá — endereços das páginas da interface, num lugar só.

As páginas se apontam umas para as outras (o início para o paciente e a equipe,
os documentos de volta ao agendamento), e `documentos` é importado por
`paciente`: com a rota de cada página só no módulo dela, a volta dos documentos
viraria import circular.
"""

INICIO = "/"
PACIENTE = "/paciente"
EQUIPE = "/equipe"
