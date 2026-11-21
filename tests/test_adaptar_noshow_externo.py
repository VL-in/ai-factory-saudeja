"""scripts/adaptar_noshow_externo.py -- adaptação do Kaggle, com dado FICTÍCIO.

Nenhuma linha do Kaggle está aqui: o bruto e a semente são gerados com
números aleatórios de semente fixa, no formato do Kaggle e do SaúdeJá.

O que se trava:
- `historico_noshow` point-in-time: falta posterior ao agendamento (ou no
  mesmo dia dele) não entra;
- o filtro de população;
- a imputação não depende do desfecho (inverter `No-show` não muda nenhum
  valor imputado) e o sábado só recebe slot da manhã;
- nenhuma coluna descartada nem o PatientId original sobrevivem;
- o contrato de features vira quarentena contada no manifesto;
- o resultado passa nas regras de `contrato_dataset_externo`."""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import agenda_clinica
import contrato_dataset_externo as cde
from config_projeto import carregar_params

_CAMINHO = Path(__file__).resolve().parents[1] / "scripts" / "adaptar_noshow_externo.py"
_spec = importlib.util.spec_from_file_location("adaptar_noshow_externo", _CAMINHO)
adaptador = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(adaptador)

ESPECIALIDADES = carregar_params()["cadastro"]["especialidades"]
SAL = "s" * 40


def _semente(rng, n=380):
    dias = list(pd.date_range("2026-01-05", "2026-01-10"))  # seg a sáb
    datas = []
    for _ in range(n):
        dia = dias[rng.integers(len(dias))]
        slots = agenda_clinica.horarios_do_dia(dia.date())
        datas.append(f"{dia.date()} {slots[rng.integers(len(slots))].strftime('%H:%M')}:00")
    return pd.DataFrame(
        {
            "id_consulta": np.arange(n),
            "especialidade": rng.choice(
                ESPECIALIDADES, size=n, p=[0.27, 0.20, 0.15, 0.12, 0.10, 0.09, 0.07]
            ),
            "distancia_km": np.clip(np.round(rng.gamma(2.0, 6.0, size=n), 1), 0.6, 50.0),
            "data_hora_agendada": datas,
        }
    )


def _bruto(rng, n=3000):
    dias = [
        d for d in pd.date_range(*cde.PERIODO_CONSULTAS) if d.dayofweek != 6
    ]  # o Kaggle não tem domingo
    consulta = pd.to_datetime([dias[i] for i in rng.integers(len(dias), size=n)])
    antecedencia = np.where(rng.random(n) < 0.3, 0, rng.integers(-1, 121, size=n))
    agendamento = consulta - pd.to_timedelta(antecedencia, unit="D")
    return pd.DataFrame(
        {
            "PatientId": [f"{p}.0" for p in rng.integers(10**12, 10**12 + n // 3, size=n)],
            "AppointmentID": rng.permutation(np.arange(5_000_000, 5_000_000 + n)),
            "Gender": rng.choice(["F", "M"], size=n),
            "ScheduledDay": [f"{d.date()}T10:15:00Z" for d in agendamento],
            "AppointmentDay": [f"{d.date()}T00:00:00Z" for d in consulta],
            "Age": rng.integers(-1, 100, size=n),
            "Neighbourhood": "BAIRRO FICTICIO",
            "Scholarship": rng.integers(0, 2, size=n),
            "Hipertension": rng.integers(0, 2, size=n),
            "Diabetes": rng.integers(0, 2, size=n),
            "Alcoholism": rng.integers(0, 2, size=n),
            "Handcap": rng.integers(0, 5, size=n),
            "SMS_received": rng.integers(0, 2, size=n),
            "No-show": np.where(rng.random(n) < 0.28, "Yes", "No"),
        }
    )


@pytest.fixture(scope="module")
def cenario():
    rng = np.random.default_rng(20261121)
    semente = _semente(rng)
    bruto = _bruto(rng)
    pool, filtros = adaptador.adaptar(bruto, semente, ESPECIALIDADES, SAL, random_state=42)
    return {"semente": semente, "bruto": bruto, "pool": pool, "filtros": filtros}


# --------------------------------------------------------------------------
# historico_noshow point-in-time
# --------------------------------------------------------------------------
def _linha(paciente, agendado, consulta, faltou):
    return {
        "PatientId": paciente,
        "ScheduledDay": f"{agendado}T09:00:00Z",
        "AppointmentDay": f"{consulta}T00:00:00Z",
        "No-show": "Yes" if faltou else "No",
    }


def test_historico_conta_so_falta_de_consulta_anterior_ao_agendamento():
    bruto = pd.DataFrame(
        [
            _linha("A", "2016-05-01", "2016-05-02", True),  # falta em 02/05
            _linha("A", "2016-05-01", "2016-05-10", False),  # agendada ANTES da falta: 0
            _linha("A", "2016-05-02", "2016-05-12", False),  # agendada NO DIA da falta: 0
            _linha("A", "2016-05-03", "2016-05-15", False),  # agendada DEPOIS da falta: 1
            _linha("A", "2016-05-20", "2016-05-25", True),  # depois de 2 consultas: 1 falta
            _linha("A", "2016-05-26", "2016-05-30", False),  # depois da 2a falta: 2
            _linha("B", "2016-05-26", "2016-05-30", False),  # outro paciente: 0
        ]
    )
    assert adaptador.historico_point_in_time(bruto).tolist() == [0, 0, 0, 1, 1, 2, 0]


def test_historico_usa_o_bruto_inteiro_antes_do_filtro_de_populacao():
    # a falta foi numa consulta do mesmo dia (fora da população), mas é
    # histórico real do paciente na consulta seguinte
    bruto = pd.DataFrame(
        [
            _linha("A", "2016-05-02", "2016-05-02", True),
            _linha("A", "2016-05-05", "2016-05-09", False),
        ]
    )
    assert adaptador.historico_point_in_time(bruto).tolist() == [0, 1]


def test_historico_do_pool_nao_depende_da_ordem_das_linhas(cenario):
    bruto = cenario["bruto"]
    embaralhado = bruto.sample(frac=1, random_state=1)
    assert (
        adaptador.historico_point_in_time(embaralhado).reindex(bruto.index)
        == adaptador.historico_point_in_time(bruto)
    ).all()


# --------------------------------------------------------------------------
# população, colunas e pseudônimo
# --------------------------------------------------------------------------
def test_pool_so_tem_a_populacao(cenario):
    pool, bruto = cenario["pool"], cenario["bruto"]
    assert len(pool) == int(cde.mascara_populacao(bruto).sum())
    assert pool["dias_entre_agendamento_consulta"].between(1, 90).all()
    assert pool["idade"].between(0, 85).all()
    assert sum(cenario["filtros"].values()) == len(bruto) - len(pool)


def test_colunas_descartadas_e_id_original_nao_sobrevivem(cenario):
    pool, bruto = cenario["pool"], cenario["bruto"]
    assert tuple(pool.columns) == cde.COLUNAS_ADAPTADO
    texto = pool.to_csv(index=False)
    assert "BAIRRO FICTICIO" not in texto
    assert not set(bruto["PatientId"]) & set(pool["id_paciente"])
    assert pool["id_paciente"].str.fullmatch(cde.PADRAO_ID_PACIENTE).all()


def test_pseudonimo_depende_do_sal():
    assert adaptador.pseudonimo("123.0", SAL) != adaptador.pseudonimo("123.0", "t" * 40)
    assert adaptador.pseudonimo("123.0", SAL) == adaptador.pseudonimo("123.0", SAL)


# --------------------------------------------------------------------------
# imputação
# --------------------------------------------------------------------------
def test_imputacao_nao_olha_o_desfecho(cenario):
    """Inverter todos os desfechos não muda nenhum valor imputado: a
    independência é estrutural, não estatística."""
    invertido = cenario["bruto"].assign(
        **{"No-show": np.where(cenario["bruto"]["No-show"] == "Yes", "No", "Yes")}
    )
    outro, _ = adaptador.adaptar(invertido, cenario["semente"], ESPECIALIDADES, SAL, 42)
    for coluna in ("especialidade", "distancia_km", "data_hora_agendada"):
        assert outro[coluna].tolist() == cenario["pool"][coluna].tolist()
    assert (outro["no_show"] == 1 - cenario["pool"]["no_show"]).all()


def test_sabado_so_recebe_slot_da_manha(cenario):
    quando = pd.to_datetime(cenario["pool"]["data_hora_agendada"])
    sabados = quando[quando.dt.dayofweek == 5]
    assert len(sabados) > 0
    validos = {s.hour * 60 + s.minute for s in agenda_clinica.SLOTS_SABADO}
    assert set(sabados.dt.hour * 60 + sabados.dt.minute) <= validos


def test_mesmo_random_state_da_o_mesmo_pool(cenario):
    de_novo, _ = adaptador.adaptar(cenario["bruto"], cenario["semente"], ESPECIALIDADES, SAL, 42)
    pd.testing.assert_frame_equal(de_novo, cenario["pool"])


# --------------------------------------------------------------------------
# contrato
# --------------------------------------------------------------------------
def test_pool_passa_nas_regras_do_contrato_do_dataset_externo(cenario):
    assert (
        adaptador.conferir(
            cenario["pool"],
            cenario["bruto"],
            cenario["semente"],
            ESPECIALIDADES,
            cenario["filtros"],
        )
        == []
    )


def test_linha_fora_do_contrato_de_features_vira_quarentena_contada(cenario):
    bruto = cenario["bruto"].copy()
    na_populacao = bruto.index[cde.mascara_populacao(bruto)][:3]
    bruto.loc[na_populacao, "Gender"] = "X"
    pool, filtros = adaptador.adaptar(bruto, cenario["semente"], ESPECIALIDADES, SAL, 42)
    assert filtros["quarentena_contrato"] == 3
    assert len(pool) == len(cenario["pool"]) - 3


def test_manifesto_bate_com_o_pool(cenario, monkeypatch):
    monkeypatch.setattr(cde, "LINHAS_BRUTO", len(cenario["bruto"]))
    manifesto = adaptador.montar_manifesto(cenario["pool"], cenario["filtros"], "abc", 42)
    assert cde.verificar_manifesto(manifesto, "abc", pool=cenario["pool"]) == []


def test_sal_curto_e_recusado(monkeypatch):
    monkeypatch.setenv(adaptador.VARIAVEL_SAL, "curto")
    with pytest.raises(SystemExit):
        adaptador.ler_sal()
