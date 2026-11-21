"""src/contrato_dataset_externo.py -- as regras conferidas contra dados FICTÍCIOS.

Os testes de dados (`tests/test_dataset_externo_dados.py`) rodam as mesmas
regras contra os arquivos reais de `data/externo/`, mas só quando eles existem.
Este arquivo roda sempre (inclusive no CI, que não tem os dados) e prova duas
coisas para cada regra: o dado bem formado passa, e a falha que ela promete
pegar é de fato pegada (cada mutação abaixo quebra exatamente uma regra).

Nenhuma linha do Kaggle está aqui: o bruto, a semente e o adaptador de
referência são gerados com números aleatórios de semente fixa."""

import hashlib

import numpy as np
import pandas as pd
import pytest

import agenda_clinica
import contrato_dataset_externo as cde
from config_projeto import carregar_params

ESPECIALIDADES = carregar_params()["cadastro"]["especialidades"]
# Distribuição propositalmente desigual: o sorteio uniforme precisa
# ser distinguível do sorteio da semente (teste de PSI).
PESOS_ESPECIALIDADE = np.array([0.27, 0.20, 0.15, 0.12, 0.10, 0.09, 0.07])
SAL = "sal-de-teste"


# --------------------------------------------------------------------------
# geradores fictícios
# --------------------------------------------------------------------------
def _dias_uteis(inicio, fim):
    return [d for d in pd.date_range(inicio, fim) if agenda_clinica.horarios_do_dia(d.date())]


def _semente(rng, n=380):
    dias = _dias_uteis("2026-01-05", "2026-01-10")
    datas = []
    for _ in range(n):
        dia = dias[rng.integers(len(dias))]
        slots = agenda_clinica.horarios_do_dia(dia.date())
        slot = slots[rng.integers(len(slots))]
        datas.append(f"{dia.date()} {slot.strftime('%H:%M')}:00")
    return pd.DataFrame(
        {
            "especialidade": rng.choice(ESPECIALIDADES, size=n, p=PESOS_ESPECIALIDADE),
            "distancia_km": np.clip(np.round(rng.gamma(2.0, 6.0, size=n), 1), 0.6, 50.0),
            "data_hora_agendada": datas,
        }
    )


def _bruto(rng, n=3000):
    dias = _dias_uteis(*cde.PERIODO_CONSULTAS)
    consulta = pd.to_datetime([dias[i] for i in rng.integers(len(dias), size=n)])
    antecedencia = np.where(
        rng.random(n) < 0.3, 0, rng.integers(-1, 121, size=n)
    )  # mesmo dia, negativa, 1-90 e > 90
    agendamento = consulta - pd.to_timedelta(antecedencia, unit="D")
    hora = rng.integers(7, 20, size=n)
    return pd.DataFrame(
        {
            "PatientId": rng.integers(1, n // 2, size=n).astype(float) * 1e6,
            "AppointmentID": rng.permutation(np.arange(5_000_000, 5_000_000 + n)),
            "Gender": rng.choice(["F", "M"], size=n),
            "ScheduledDay": [
                f"{d.date()}T{h:02d}:15:00Z" for d, h in zip(agendamento, hora, strict=True)
            ],
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


def _pseudonimo(patient_id):
    return "K" + hashlib.sha256(f"{SAL}{patient_id}".encode()).hexdigest()[:16]


def _adaptar(bruto, semente, rng):
    """Adaptador de REFERÊNCIA, mínimo: o que o contrato espera do script
    real da Fase 2 (que terá o point-in-time completo e os testes próprios)."""
    falta = (bruto["No-show"] == "Yes").astype(int)
    ordem = bruto.assign(_falta=falta).sort_values("AppointmentDay", kind="stable")
    historico = (ordem.groupby("PatientId")["_falta"].cumsum() - ordem["_falta"]).reindex(
        bruto.index
    )

    mascara = cde.mascara_populacao(bruto)
    pop = bruto[mascara]
    slots_semente = pd.to_datetime(semente["data_hora_agendada"]).dt.strftime("%H:%M")
    datas = []
    for dia_txt in pop["AppointmentDay"].str[:10]:
        dia = pd.Timestamp(dia_txt).date()
        validos = {s.strftime("%H:%M") for s in agenda_clinica.horarios_do_dia(dia)}
        candidatos = slots_semente[slots_semente.isin(validos)].to_numpy()
        datas.append(f"{dia} {candidatos[rng.integers(len(candidatos))]}:00")
    n = len(pop)
    return pd.DataFrame(
        {
            "id_consulta": [f"K-{i}" for i in pop["AppointmentID"]],
            "id_paciente": [_pseudonimo(p) for p in pop["PatientId"]],
            "idade": pop["Age"].to_numpy(),
            "sexo": pop["Gender"].to_numpy(),
            "especialidade": rng.choice(semente["especialidade"].to_numpy(), size=n),
            "distancia_km": rng.choice(semente["distancia_km"].to_numpy(), size=n),
            "dias_entre_agendamento_consulta": cde.antecedencia_em_dias(pop).to_numpy(),
            "historico_noshow": historico[mascara].astype(int).to_numpy(),
            "no_show": falta[mascara].to_numpy(),
            "data_hora_agendada": datas,
            "lembrete_enviado": pop["SMS_received"].to_numpy(),
            "origem": cde.ORIGEM_EXTERNA,
            "colunas_imputadas": cde.VALOR_COLUNAS_IMPUTADAS,
        }
    )[list(cde.COLUNAS_ADAPTADO)]


def _particionar(pool, rng, alvo_reservado=300, n_amostra=200):
    pacientes = rng.permutation(pool["id_paciente"].unique())
    contagem = pool["id_paciente"].value_counts()
    acumulado = contagem.reindex(pacientes).cumsum()
    do_reservado = set(acumulado[acumulado <= alvo_reservado].index)
    reservado = pool[pool["id_paciente"].isin(do_reservado)]
    resto = pool[~pool["id_paciente"].isin(do_reservado)]
    amostra = resto.sample(n=n_amostra, random_state=0)
    return reservado.reset_index(drop=True), amostra.reset_index(drop=True)


def _manifesto(pool, reservado, amostra, sha="abc", removidas_contrato=0):
    # filtros sequenciais: bruto - filtros = pool
    removidas_populacao = cde.LINHAS_BRUTO - len(pool) - removidas_contrato
    return {
        "versao_script": "teste",
        "gerado_em": "2026-11-21T00:00:00",
        "licenca": cde.LICENCA,
        "fonte": cde.URL_FONTE,
        "random_state": 42,
        "bruto": {"arquivo": "x.csv", "sha256": sha, "linhas": cde.LINHAS_BRUTO},
        "filtros": {
            "fora_da_populacao": removidas_populacao,
            "quarentena_contrato": removidas_contrato,
        },
        "pool": {
            "linhas": len(pool),
            "positivos": int(pool["no_show"].sum()),
            "pacientes": int(pool["id_paciente"].nunique()),
        },
        "reservado": {
            "linhas": len(reservado),
            "pacientes": int(reservado["id_paciente"].nunique()),
        },
        "amostra": {"linhas": len(amostra), "positivos": int(amostra["no_show"].sum())},
    }


@pytest.fixture(scope="module")
def cenario():
    rng = np.random.default_rng(20261121)
    semente = _semente(rng)
    bruto = _bruto(rng)
    pool = _adaptar(bruto, semente, rng)
    reservado, amostra = _particionar(pool, rng)
    return {
        "semente": semente,
        "bruto": bruto,
        "pool": pool,
        "reservado": reservado,
        "amostra": amostra,
    }


def _copia(cenario, nome):
    return cenario[nome].copy()


# --------------------------------------------------------------------------
# primitivas
# --------------------------------------------------------------------------
def test_colunas_do_contrato_sao_as_do_export_de_producao():
    import export_treino

    assert list(cde.COLUNAS_DO_EXPORT) == export_treino.COLUNAS_SAIDA


def test_antecedencia_usa_a_data_escrita_sem_converter_o_z_para_o_fuso():
    # 23h30 "Z" convertido para São Paulo cairia no mesmo dia da consulta (0)
    bruto = pd.DataFrame(
        {"ScheduledDay": ["2016-05-02T23:30:00Z"], "AppointmentDay": ["2016-05-03T00:00:00Z"]}
    )
    assert cde.antecedencia_em_dias(bruto).tolist() == [1]


@pytest.mark.parametrize(
    ("dias", "idade", "entra"),
    [
        (0, 30, False),  # mesmo dia: o job D-2 nunca prediz
        (1, 30, True),
        (90, 30, True),
        (91, 30, False),
        (-1, 30, False),
        (10, -1, False),
        (10, 0, True),
        (10, 85, True),
        (10, 86, False),
    ],
)
def test_mascara_da_populacao_segue_o_dominio_do_contrato(dias, idade, entra):
    consulta = pd.Timestamp("2016-05-20")
    agendamento = consulta - pd.Timedelta(days=dias)
    bruto = pd.DataFrame(
        {
            "ScheduledDay": [f"{agendamento.date()}T10:00:00Z"],
            "AppointmentDay": [f"{consulta.date()}T00:00:00Z"],
            "Age": [idade],
        }
    )
    assert cde.mascara_populacao(bruto).tolist() == [entra]


def test_remocoes_por_filtro_somam_o_que_a_mascara_deixa_de_fora(cenario):
    bruto = cenario["bruto"]
    remocoes = cde.remocoes_por_filtro(bruto)
    assert sum(remocoes.values()) == int((~cde.mascara_populacao(bruto)).sum())
    assert remocoes["mesmo_dia"] > 0 and remocoes["antecedencia_acima_de_90"] > 0


def test_remocoes_por_filtro_contam_cada_linha_uma_vez_so():
    # mesmo dia E idade invalida: conta so no primeiro filtro (mesmo_dia)
    bruto = pd.DataFrame(
        {
            "ScheduledDay": ["2016-05-20T10:00:00Z"],
            "AppointmentDay": ["2016-05-20T00:00:00Z"],
            "Age": [-1],
        }
    )
    remocoes = cde.remocoes_por_filtro(bruto)
    assert remocoes["mesmo_dia"] == 1
    assert remocoes["idade_invalida"] == 0


# --------------------------------------------------------------------------
# bruto
# --------------------------------------------------------------------------
@pytest.fixture
def bruto_com_tamanho_oficial(cenario, monkeypatch):
    monkeypatch.setattr(cde, "LINHAS_BRUTO", len(cenario["bruto"]))
    return _copia(cenario, "bruto")


def test_bruto_bem_formado_passa(bruto_com_tamanho_oficial):
    assert cde.verificar_bruto(bruto_com_tamanho_oficial) == []


def test_bruto_de_outro_tamanho_e_acusado(cenario):
    assert any("linhas" in v for v in cde.verificar_bruto(_copia(cenario, "bruto")))


def _set(coluna, valor):
    def mutar(df):
        df[coluna] = df[coluna].astype(object)
        df.loc[df.index[0], coluna] = valor
        return df

    return mutar


@pytest.mark.parametrize(
    ("mutacao", "esperado"),
    [
        (lambda df: df.drop(columns=["Handcap"]), "colunas diferentes"),
        (_set("Age", None), "nulos"),
        (lambda df: df.assign(AppointmentID=df["AppointmentID"].iloc[0]), "duplicado"),
        (_set("No-show", "Maybe"), "No-show"),
        (_set("Gender", "X"), "Gender"),
        (_set("SMS_received", 2), "SMS_received"),
        (_set("Handcap", 5), "Handcap"),
        (_set("ScheduledDay", "ontem"), "ilegível"),
        (_set("AppointmentDay", "2016-07-01T00:00:00Z"), "fora de"),
    ],
)
def test_bruto_mal_formado_e_acusado(bruto_com_tamanho_oficial, mutacao, esperado):
    violacoes = cde.verificar_bruto(mutacao(bruto_com_tamanho_oficial))
    assert any(esperado in v for v in violacoes), violacoes


# --------------------------------------------------------------------------
# adaptado: schema e regra de negócio
# --------------------------------------------------------------------------
def test_adaptado_bem_formado_passa(cenario):
    for nome in ("pool", "reservado", "amostra"):
        assert cde.verificar_adaptado(cenario[nome], cenario["semente"], ESPECIALIDADES) == []


@pytest.mark.parametrize(
    ("mutacao", "esperado"),
    [
        (lambda df: df.drop(columns=["origem"]), "nome ou ordem"),
        (lambda df: df[list(reversed(df.columns))], "nome ou ordem"),
        (lambda df: df.assign(Neighbourhood="X"), "proibidas"),
        (lambda df: df.assign(Diabetes=0), "proibidas"),
        (lambda df: df.assign(telefone="0"), "proibidas"),
        (_set("idade", None), "nulos"),
        (lambda df: df.assign(id_consulta=df["id_consulta"].iloc[0]), "duplicado"),
        (_set("id_consulta", "5000001"), "id_consulta fora do padrão"),
        (_set("id_paciente", "29872499824296"), "pseudônimo"),
        (_set("origem", "producao"), "origem diferente"),
        (_set("colunas_imputadas", ""), "colunas_imputadas"),
        (_set("no_show", 2), "no_show fora"),
        (_set("lembrete_enviado", 3), "lembrete_enviado fora"),
        (_set("sexo", "X"), "contrato"),
        (_set("especialidade", "neurologia"), "contrato"),
        (_set("data_hora_agendada", "2016-05-01 09:00:00"), "contrato"),  # domingo
        (_set("data_hora_agendada", "2016-05-03 12:30:00"), "contrato"),  # almoço
        (_set("data_hora_agendada", "2016-05-07 14:00:00"), "contrato"),  # sábado à tarde
        (_set("idade", 90), "população"),
        (_set("dias_entre_agendamento_consulta", 0), "população"),
        (_set("data_hora_agendada", "2026-01-06 09:00:00"), "período"),
        (_set("distancia_km", 49.99), "não existe na semente"),
    ],
)
def test_adaptado_mal_formado_e_acusado(cenario, mutacao, esperado):
    df = mutacao(_copia(cenario, "pool"))
    violacoes = cde.verificar_adaptado(df, cenario["semente"], ESPECIALIDADES)
    assert any(esperado in v for v in violacoes), violacoes


# --------------------------------------------------------------------------
# adaptado: imputação independente do desfecho e com a cara da semente
# --------------------------------------------------------------------------
def test_imputacao_independente_e_com_a_distribuicao_da_semente_passa(cenario):
    assert cde.verificar_imputadas(cenario["pool"], cenario["semente"]) == []


def _imputacao_que_vaza(coluna, valor_para_faltoso):
    def mutar(df):
        df.loc[df["no_show"] == 1, coluna] = valor_para_faltoso(df)
        return df

    return mutar


@pytest.mark.parametrize(
    ("mutacao", "esperado"),
    [
        (_imputacao_que_vaza("especialidade", lambda df: "cardiologia"), "especialidade associado"),
        (
            _imputacao_que_vaza("distancia_km", lambda df: df["distancia_km"].max()),
            "distancia_km associada",
        ),
        (
            _imputacao_que_vaza(
                "data_hora_agendada",
                lambda df: df.loc[df["no_show"] == 1, "data_hora_agendada"].str[:10] + " 17:00:00",
            ),
            "horario associado",
        ),
    ],
)
def test_imputacao_que_depende_do_desfecho_e_acusada(cenario, mutacao, esperado):
    df = mutacao(_copia(cenario, "pool"))
    violacoes = cde.verificar_imputadas(df, cenario["semente"])
    assert any(esperado in v for v in violacoes), violacoes


def test_imputacao_uniforme_denuncia_a_origem_pelo_psi(cenario):
    rng = np.random.default_rng(1)
    df = _copia(cenario, "pool")
    df["especialidade"] = rng.choice(ESPECIALIDADES, size=len(df))
    df["distancia_km"] = np.round(rng.uniform(0.6, 50.0, size=len(df)), 1)
    violacoes = cde.verificar_imputadas(df, cenario["semente"])
    assert any("PSI de especialidade" in v for v in violacoes), violacoes
    assert any("PSI de distancia_km" in v for v in violacoes), violacoes


def test_imputacao_sem_as_duas_classes_e_acusada(cenario):
    df = _copia(cenario, "pool").assign(no_show=0)
    assert any("duas classes" in v for v in cde.verificar_imputadas(df, cenario["semente"]))


# --------------------------------------------------------------------------
# fidelidade ao bruto
# --------------------------------------------------------------------------
def test_pool_fiel_ao_bruto_passa(cenario):
    assert cde.verificar_fidelidade_ao_bruto(cenario["pool"], cenario["bruto"]) == []


@pytest.mark.parametrize(
    ("mutacao", "esperado"),
    [
        (lambda df: df.assign(no_show=1 - df["no_show"]), "combinação"),  # "Yes" lido ao contrário
        (
            lambda df: df.assign(
                dias_entre_agendamento_consulta=df["dias_entre_agendamento_consulta"] + 1
            ),
            "combinação",
        ),  # antecedência com fuso
        (
            lambda df: df.assign(
                lembrete_enviado=df["lembrete_enviado"].sample(frac=1, random_state=3).to_numpy()
            ),
            "combinação",
        ),
        (lambda df: df.iloc[1:], "linhas"),
        (lambda df: df.assign(id_paciente=df["id_paciente"].iloc[0]), "pacientes"),
    ],
)
def test_pool_infiel_ao_bruto_e_acusado(cenario, mutacao, esperado):
    df = mutacao(_copia(cenario, "pool"))
    violacoes = cde.verificar_fidelidade_ao_bruto(df, cenario["bruto"])
    assert any(esperado in v for v in violacoes), violacoes


def test_fidelidade_desconta_as_linhas_da_quarentena_do_contrato(cenario):
    df = _copia(cenario, "pool").iloc[2:]
    assert cde.verificar_fidelidade_ao_bruto(df, cenario["bruto"], removidas_pelo_contrato=2) == []


# --------------------------------------------------------------------------
# partição
# --------------------------------------------------------------------------
PARTICAO_DE_TESTE = {"faixa_reservado": (250, 300), "tolerancia_amostra": 0.15}


def _particao(cenario, reservado=None, amostra=None, n_amostra=200):
    return cde.verificar_particao(
        cenario["pool"],
        cenario["reservado"] if reservado is None else reservado,
        cenario["amostra"] if amostra is None else amostra,
        n_amostra,
        tolerancia_reservado=0.15,
        **PARTICAO_DE_TESTE,
    )


def test_particao_por_paciente_passa(cenario):
    assert _particao(cenario) == []


def test_paciente_no_reservado_e_na_amostra_e_acusado(cenario):
    reservado, amostra = _copia(cenario, "reservado"), _copia(cenario, "amostra")
    pool = cenario["pool"]
    paciente = reservado["id_paciente"].iloc[0]
    linha = pool[pool["id_paciente"] == paciente].iloc[[0]]
    reservado = reservado[reservado["id_consulta"] != linha["id_consulta"].iloc[0]]
    amostra = pd.concat([amostra.iloc[1:], linha], ignore_index=True)
    violacoes = _particao(cenario, reservado=reservado, amostra=amostra)
    assert any("no teste reservado e na amostra" in v for v in violacoes), violacoes
    assert any("por paciente" in v for v in violacoes), violacoes


@pytest.mark.parametrize(
    ("parte", "mutacao", "esperado"),
    [
        ("amostra", lambda df: df.iloc[1:], "params pede"),
        (
            "reservado",
            lambda df: df.assign(distancia_km=df["distancia_km"] + 0.1),
            "valores diferentes",
        ),
        ("amostra", lambda df: _set("id_consulta", "K-1")(df), "não estão no pool"),
        ("amostra", lambda df: df.assign(no_show=1), "taxa de falta"),
        ("reservado", lambda df: df.iloc[:100], "fora de"),
    ],
)
def test_particao_mal_feita_e_acusada(cenario, parte, mutacao, esperado):
    df = mutacao(_copia(cenario, parte))
    if parte == "amostra":
        violacoes = _particao(cenario, amostra=df)
    else:
        violacoes = _particao(cenario, reservado=df)
    assert any(esperado in v for v in violacoes), violacoes


# --------------------------------------------------------------------------
# manifesto, licença, params e treino oficial
# --------------------------------------------------------------------------
def test_manifesto_coerente_passa(cenario):
    m = _manifesto(cenario["pool"], cenario["reservado"], cenario["amostra"])
    assert (
        cde.verificar_manifesto(m, "abc", cenario["pool"], cenario["reservado"], cenario["amostra"])
        == []
    )


@pytest.mark.parametrize(
    ("mutacao", "esperado"),
    [
        (lambda m: m.pop("filtros"), "chaves ausentes"),
        (lambda m: m.update(licenca="CC BY 4.0"), "licença"),
        (lambda m: m["bruto"].update(sha256="outro"), "sha256"),
        (lambda m: m["filtros"].update(fora_da_populacao=0), "soma(filtros)"),
        (lambda m: m["pool"].update(positivos=-1), "pool.positivos"),
        (lambda m: m["amostra"].update(linhas=1), "amostra.linhas"),
        (lambda m: m.update(random_state="42"), "random_state"),
    ],
)
def test_manifesto_incoerente_e_acusado(cenario, mutacao, esperado):
    m = _manifesto(cenario["pool"], cenario["reservado"], cenario["amostra"])
    mutacao(m)
    violacoes = cde.verificar_manifesto(
        m, "abc", cenario["pool"], cenario["reservado"], cenario["amostra"]
    )
    assert any(esperado in v for v in violacoes), violacoes


TEXTO_LICENCA = (
    f"Fonte: {cde.URL_FONTE}. Licença {cde.LICENCA}. Consultas de 2016 da rede pública "
    "de Vitória-ES; não são pacientes do SaúdeJá."
)


def test_licenca_completa_passa():
    assert cde.verificar_licenca(TEXTO_LICENCA) == []


def test_licenca_sem_a_fonte_e_acusada():
    violacoes = cde.verificar_licenca(TEXTO_LICENCA.replace(cde.URL_FONTE, ""))
    assert violacoes == [f"licença: LICENCA-E-ORIGEM.md não menciona '{cde.URL_FONTE}'"]


@pytest.mark.parametrize(
    ("externa", "esperado"),
    [
        ({"habilitada": False, "n_amostra": 1500, "random_state": 42}, None),
        (None, "ausente"),
        ({"habilitada": True, "n_amostra": 1500, "random_state": 42}, "habilitada"),
        ({"habilitada": False, "n_amostra": "1500", "random_state": 42}, "n_amostra"),
        ({"habilitada": False, "n_amostra": 0, "random_state": 42}, "n_amostra"),
        ({"habilitada": False, "n_amostra": 1500, "random_state": True}, "random_state"),
    ],
)
def test_bloco_externa_do_params(externa, esperado):
    params = {"dados": {} if externa is None else {"externa": externa}}
    violacoes = cde.verificar_params_externa(params)
    if esperado is None:
        assert violacoes == []
    else:
        assert any(esperado in v for v in violacoes), violacoes


def test_treino_oficial_com_linha_externa_e_parametro_desligado_e_acusado():
    treino = pd.DataFrame({"id_consulta": ["1", "2", "K-5000001"]})
    assert cde.verificar_treino_sem_externa(treino, habilitada=False) != []
    assert cde.verificar_treino_sem_externa(treino, habilitada=True) == []
    assert cde.verificar_treino_sem_externa(treino.iloc[:2], habilitada=False) == []


def test_treino_oficial_com_origem_externa_e_acusado():
    treino = pd.DataFrame({"id_consulta": ["1", "2"], "origem": ["semente", "externa"]})
    violacoes = cde.verificar_treino_sem_externa(treino, habilitada=False)
    assert any("origem externa" in v for v in violacoes), violacoes
