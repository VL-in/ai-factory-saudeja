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
- o sinal declarado da sexta 17h-18h (emenda do ADR-014) chega na razão
  pedida trocando só a hora entre consultas de sexta;
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
    pool, sinal = adaptador.injetar_sinal_sexta(cenario["pool"], 1.25, 42)
    manifesto = adaptador.montar_manifesto(pool, cenario["filtros"], "abc", 42, sinal_sexta=sinal)
    assert cde.verificar_manifesto(manifesto, "abc", pool=pool) == []
    # o manifesto de um pool sem o sinal não bate com o pool injetado
    sem_sinal = adaptador.montar_manifesto(cenario["pool"], cenario["filtros"], "abc", 42)
    assert any("sinal_sexta" in v for v in cde.verificar_manifesto(sem_sinal, "abc", pool=pool))


# --------------------------------------------------------------------------
# sinal declarado da sexta 17h-18h (emenda do ADR-014)
# --------------------------------------------------------------------------
def _pool_grande(n=20_000, seed=7):
    """Pool fictício só com o que o sinal usa, grande o bastante para a razão
    não sofrer com arredondamento. Horário independente do desfecho."""
    rng = np.random.default_rng(seed)
    dias = list(pd.date_range("2016-05-02", "2016-05-28"))
    linhas = []
    for _ in range(n):
        dia = dias[rng.integers(len(dias))]
        slots = agenda_clinica.horarios_do_dia(dia.date())
        if not slots:
            continue
        linhas.append(f"{dia.date()} {slots[rng.integers(len(slots))].strftime('%H:%M')}:00")
    return pd.DataFrame(
        {
            "id_consulta": [f"K-{i}" for i in range(len(linhas))],
            "data_hora_agendada": linhas,
            "no_show": (rng.random(len(linhas)) < 0.28).astype(int),
        }
    )


@pytest.mark.parametrize("razao", [1.25, 1.5, 0.8])
def test_sinal_da_sexta_chega_na_razao_pedida(razao):
    pool = _pool_grande()
    injetado, resumo = adaptador.injetar_sinal_sexta(pool, razao, 42)
    assert abs(cde.razao_sinal_sexta(pool) - 1) < 0.15  # sem sinal antes
    assert abs(cde.razao_sinal_sexta(injetado) - razao) < 0.01
    assert cde.verificar_sinal_sexta(injetado, razao) == []
    assert resumo["razao_depois"] == round(cde.razao_sinal_sexta(injetado), 4)
    assert (resumo["trocas"] > 0) == (razao > 1)


def test_sinal_da_sexta_so_troca_a_hora_entre_consultas_de_sexta():
    pool = _pool_grande()
    injetado, _ = adaptador.injetar_sinal_sexta(pool, 1.25, 42)
    antes = pd.to_datetime(pool["data_hora_agendada"])
    depois = pd.to_datetime(injetado["data_hora_agendada"])
    # desfecho, ids e data (logo o dia da semana) intactos
    pd.testing.assert_series_equal(injetado["no_show"], pool["no_show"])
    pd.testing.assert_series_equal(injetado["id_consulta"], pool["id_consulta"])
    assert (antes.dt.normalize() == depois.dt.normalize()).all()
    # fora da sexta nada muda; na sexta, o mesmo conjunto de horários
    sexta = antes.dt.dayofweek == cde.DIA_SEXTA
    assert (antes[~sexta] == depois[~sexta]).all()
    assert (antes[sexta] != depois[sexta]).any()
    assert sorted(antes[sexta].dt.strftime("%H:%M")) == sorted(depois[sexta].dt.strftime("%H:%M"))
    # a taxa de falta da sexta como um todo é a real
    assert injetado.loc[sexta, "no_show"].mean() == pool.loc[sexta, "no_show"].mean()


def test_sinal_da_sexta_e_deterministico_e_nao_mexe_na_imputacao(cenario):
    um, _ = adaptador.injetar_sinal_sexta(cenario["pool"], 1.25, 42)
    dois, _ = adaptador.injetar_sinal_sexta(cenario["pool"], 1.25, 42)
    pd.testing.assert_frame_equal(um, dois)
    for coluna in ("especialidade", "distancia_km"):
        assert um[coluna].tolist() == cenario["pool"][coluna].tolist()


def test_pool_com_o_sinal_passa_no_contrato(cenario):
    """A independência do horário é testada fora da sexta: o sinal
    declarado não vira violação de imputação, e o PSI do horário não muda."""
    pool, _ = adaptador.injetar_sinal_sexta(cenario["pool"], 1.25, 42)
    assert cde.verificar_imputadas(pool, cenario["semente"]) == []
    assert (
        adaptador.conferir(
            pool, cenario["bruto"], cenario["semente"], ESPECIALIDADES, cenario["filtros"]
        )
        == []
    )


def test_sinal_impossivel_e_recusado():
    pool = _pool_grande(n=2_000)
    with pytest.raises(ValueError, match="trocas pedidas"):
        adaptador.injetar_sinal_sexta(pool, 50.0, 42)


def test_pool_sem_o_sinal_declarado_e_acusado():
    violacoes = cde.verificar_sinal_sexta(_pool_grande(), 1.25)
    assert any("longe da declarada" in v for v in violacoes), violacoes


def test_sal_curto_e_recusado(monkeypatch):
    monkeypatch.setenv(adaptador.VARIAVEL_SAL, "curto")
    with pytest.raises(SystemExit):
        adaptador.ler_sal()


# --------------------------------------------------------------------------
# partição (Fase 3)
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def particao(cenario):
    reservado, amostra = adaptador.particionar(
        cenario["pool"], n_amostra=200, random_state=42, alvo_reservado=300
    )
    return reservado, amostra


def test_particao_por_paciente_passa_no_contrato(cenario, particao):
    reservado, amostra = particao
    violacoes = cde.verificar_particao(
        cenario["pool"],
        reservado,
        amostra,
        n_amostra=200,
        faixa_reservado=(250, 300),
        tolerancia_amostra=0.04,
        tolerancia_reservado=0.08,  # 300 linhas: o desvio-padrão da taxa é ~0,026
    )
    assert violacoes == []


def test_nenhum_paciente_do_reservado_no_pool_de_simulacao_nem_na_amostra(cenario, particao):
    reservado, amostra = particao
    simulacao = cde.pool_de_simulacao(cenario["pool"], reservado)
    assert not set(reservado["id_paciente"]) & set(simulacao["id_paciente"])
    assert not set(reservado["id_paciente"]) & set(amostra["id_paciente"])
    assert len(simulacao) + len(reservado) == len(cenario["pool"])


def test_particao_nao_resorteia_a_imputacao(cenario, particao):
    """A amostra é recorte do pool: mesmos valores imputados linha a linha."""
    _, amostra = particao
    do_pool = cenario["pool"].set_index("id_consulta").loc[amostra["id_consulta"]]
    assert (do_pool["data_hora_agendada"].to_numpy() == amostra["data_hora_agendada"]).all()


def test_particao_e_deterministica(cenario, particao):
    de_novo = adaptador.particionar(cenario["pool"], 200, 42, alvo_reservado=300)
    pd.testing.assert_frame_equal(de_novo[0], particao[0])
    pd.testing.assert_frame_equal(de_novo[1], particao[1])
