"""
SaúdeJá — skew treino-serving a partir do dado CRU do banco (bug de
fuso corrigido em 2026-09-29).

`tests/test_inference.py` já trava `construir_features` x `preprocessar`, mas
começa num dicionário pronto. O bug que esta revisão achou estava antes dele:
o Postgres devolve `timestamptz` em UTC ("...T21:00:00+00:00" para uma
consulta das 18h em São Paulo), e o job e o export liam essa hora sem
converter -- o modelo via `horario=21` (que o treino nunca viu), o dataset de
treino recebia hora UTC e o SMS dizia 21:00 ao paciente. Nenhum teste pegava,
porque nenhum partia do formato que o banco de fato devolve.

Aqui a mesma linha, no formato do PostgREST, percorre os dois caminhos --
serving (job D-2) e treino (export -> CSV -> `preprocessar`) -- e os vetores
de features têm de ser idênticos. Sem banco: roda no CI rápido.
"""
import io
from datetime import date, datetime

import pandas as pd
import pytest

import export_treino
import inference
import preprocess
from config_projeto import fuso_da_clinica, para_horario_da_clinica
from features import extrair_features_temporais
from jobs import inferencia_diaria as job

# Sexta-feira 02/10/2026, 18h em São Paulo = 21h UTC -- o horário de maior
# no-show nas notas da Camila, e o que o bug empurrava para fora da grade.
CONSULTA_UTC = "2026-10-02T21:00:00+00:00"


def _linha_do_banco(**extra) -> dict:
    """Mesma forma que o PostgREST devolve para o select do job/export:
    timestamptz em UTC, `numeric` como número, `date` como string."""
    linha = {
        "id": "a1",
        "especialidade": "cardiologia",
        "distancia_km": 5,  # numeric inteiro chega como int, não float
        "data_hora_agendada": CONSULTA_UTC,
        "dias_entre_agendamento_consulta": 14,
        "historico_noshow": 0,
        "status": "no_show",
        "lembrete_enviado": False,
        "pacientes": {
            "id_paciente_externo": "hash",
            "data_nascimento": "1980-10-03",
            "sexo": "F",
            "telefone": "5511987654321",
        },
    }
    linha.update(extra)
    return linha


def test_para_horario_da_clinica_converte_utc_e_mantem_hora_local_sem_fuso():
    convertido = para_horario_da_clinica(CONSULTA_UTC)
    assert (convertido.hour, convertido.date()) == (18, date(2026, 10, 2))

    local = para_horario_da_clinica("2026-10-02 18:00:00")
    assert local.hour == 18
    assert local.utcoffset() == datetime(2026, 10, 2, tzinfo=fuso_da_clinica()).utcoffset()

    assert para_horario_da_clinica(pd.Timestamp(CONSULTA_UTC)).hour == 18


def test_features_temporais_usam_a_hora_da_clinica_para_timestamp_com_fuso():
    df = pd.DataFrame({"data_hora_agendada": [datetime.fromisoformat(CONSULTA_UTC)]})
    features = extrair_features_temporais(df)
    assert features.loc[0, "horario"] == 18
    assert features.loc[0, "dia_de_semana"] == 4  # sexta


@pytest.mark.parametrize("features_temporais", [False, True])
def test_linha_do_banco_gera_as_mesmas_features_no_job_e_no_treino(features_temporais, monkeypatch):
    monkeypatch.setitem(preprocess.PARAMS, "features", {"temporais": features_temporais})
    linha = _linha_do_banco()

    # caminho de treino: export -> CSV (strings) -> preprocessar
    exportado = export_treino.montar_dataset_producao([linha])
    csv = pd.read_csv(io.StringIO(exportado.to_csv(index=False)))
    X_treino, _, mapa = preprocess.preprocessar(csv)

    # caminho de serving: job D-2
    payload = job._payload_de_agendamento(linha)
    X_serving = inference.construir_features(payload, mapa, features_temporais=features_temporais)

    pd.testing.assert_frame_equal(
        X_serving.reset_index(drop=True), X_treino.reset_index(drop=True), check_dtype=False
    )
    if features_temporais:
        assert X_serving.loc[0, "horario"] == 18


def test_export_grava_a_hora_da_clinica_e_se_houve_lembrete():
    exportado = export_treino.montar_dataset_producao([_linha_do_banco(lembrete_enviado=True)])
    assert exportado.loc[0, "data_hora_agendada"] == "2026-10-02 18:00:00"
    assert exportado.loc[0, "lembrete_enviado"] == 1


def test_sms_informa_o_horario_da_clinica():
    mensagem = job._mensagem_de_lembrete(_linha_do_banco())
    assert "02/10/2026 às 18:00" in mensagem


def test_sms_de_agendamento_malformado_cai_na_mensagem_generica():
    mensagem = job._mensagem_de_lembrete(_linha_do_banco(data_hora_agendada=None))
    assert "consulta" in mensagem
    assert "None" not in mensagem
