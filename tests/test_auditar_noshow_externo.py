"""scripts/auditar_noshow_externo.py -- auditoria do bruto, com bruto FICTÍCIO.

Garantias: os agregados fecham entre si (filtros + população = bruto) e a
saída impressa não traz nenhum valor de linha (id de paciente, de consulta,
bairro)."""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_CAMINHO = Path(__file__).resolve().parents[1] / "scripts" / "auditar_noshow_externo.py"
_spec = importlib.util.spec_from_file_location("auditar_noshow_externo", _CAMINHO)
auditoria = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auditoria)


@pytest.fixture
def bruto():
    rng = np.random.default_rng(7)
    n = 400
    consulta = pd.Timestamp("2016-05-02") + pd.to_timedelta(rng.integers(0, 30, n), unit="D")
    antecedencia = rng.integers(-1, 100, n)
    agendamento = consulta - pd.to_timedelta(antecedencia, unit="D")
    return pd.DataFrame(
        {
            "PatientId": (rng.integers(1, 150, n) * 1e6 + 123456789).astype(float),
            "AppointmentID": np.arange(5_600_000, 5_600_000 + n),
            "Gender": rng.choice(["F", "M"], n),
            "ScheduledDay": [f"{d.date()}T10:00:00Z" for d in agendamento],
            "AppointmentDay": [f"{d.date()}T00:00:00Z" for d in consulta],
            "Age": rng.integers(-1, 100, n),
            "Neighbourhood": "BAIRRO SECRETO",
            "Scholarship": 0,
            "Hipertension": 0,
            "Diabetes": 0,
            "Alcoholism": 0,
            "Handcap": 0,
            "SMS_received": rng.integers(0, 2, n),
            "No-show": rng.choice(["Yes", "No"], n),
        }
    )


def test_agregados_fecham_com_o_bruto(bruto):
    r = auditoria.auditar(bruto)
    assert r["linhas"] == len(bruto)
    assert sum(r["remocoes_por_filtro"].values()) + r["populacao"]["linhas"] == len(bruto)
    assert sum(f["linhas"] for f in r["por_faixa_de_antecedencia"].values()) == len(bruto)
    assert sum(r["populacao"]["dia_da_semana"].values()) == r["populacao"]["linhas"]


def test_saida_impressa_nao_tem_valor_de_linha(bruto, capsys, tmp_path):
    caminho = tmp_path / "bruto.csv"
    bruto.to_csv(caminho, index=False)
    auditoria.main(["--bruto", str(caminho)])
    saida = capsys.readouterr().out
    assert "BAIRRO SECRETO" not in saida
    assert "123456789" not in saida  # sufixo de todo PatientId fictício
    assert "5600" not in saida  # prefixo de todo AppointmentID fictício
