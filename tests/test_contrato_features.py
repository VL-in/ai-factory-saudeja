"""
SaúdeJá — contrato de features. Sem banco e sem modelo: é a regra
pura que o job, o export, o `preprocess` e o `validate_data` aplicam.

O que se trava aqui:
- **coerção explícita**: "45" e 45.0 viram 45; 45.5, NaN, None e booleano são
  recusados -- nunca truncados nem trocados por 0;
- **as duas faixas**: fora da regra de negócio recusa; fora do domínio do
  treino prediz e marca;
- **a grade da clínica**, no fuso da clínica (a hora UTC do banco é convertida
  antes de ser julgada);
- **a mensagem de erro não carrega o valor**, porque vai para log e tela.
"""
import math
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

import contrato_features as contrato
from config_projeto import fuso_da_clinica

ESPECIALIDADES = {"cardiologia", "clinica geral"}


def _payload(**troca):
    base = {
        "idade": 45,
        "sexo": "F",
        "especialidade": "cardiologia",
        "distancia_km": 5.5,
        "dias_entre_agendamento_consulta": 14,
        "historico_noshow": 1,
        # sexta 02/10/2026, 18h em São Paulo, no formato que o PostgREST devolve
        "data_hora_agendada": "2026-10-02T21:00:00+00:00",
    }
    base.update(troca)
    return base


def test_payload_valido_passa_coagido_e_sem_marca():
    linha = contrato.validar_payload(_payload(), ESPECIALIDADES)

    assert not linha.fora_do_dominio
    assert linha.valores["idade"] == 45 and isinstance(linha.valores["idade"], int)
    assert isinstance(linha.valores["distancia_km"], float)
    quando = linha.valores["data_hora_agendada"]
    assert (quando.hour, quando.utcoffset()) == (
        18,
        datetime(2026, 10, 2, tzinfo=fuso_da_clinica()).utcoffset(),
    )


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("45", 45), (45.0, 45), (np.int64(45), 45), (np.float64(45.0), 45), (" 45 ", 45)],
)
def test_coercao_explicita_de_inteiro(valor, esperado):
    linha = contrato.validar_payload(_payload(idade=valor), ESPECIALIDADES)
    assert linha.valores["idade"] == esperado


@pytest.mark.parametrize("valor", [45.5, math.nan, None, True, "quarenta", float("inf")])
def test_inteiro_invalido_e_recusado_nunca_truncado(valor):
    with pytest.raises(contrato.ViolacaoDoContrato) as exc:
        contrato.validar_payload(_payload(idade=valor), ESPECIALIDADES)
    assert exc.value.campo == "idade"


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("idade", -5),
        ("idade", 150),
        ("distancia_km", -0.1),
        ("dias_entre_agendamento_consulta", -1),
        ("dias_entre_agendamento_consulta", 181),
        ("historico_noshow", -1),
        ("sexo", "X"),
        ("sexo", None),
        ("especialidade", "neurologia"),
        ("data_hora_agendada", None),
        ("data_hora_agendada", "ontem"),
    ],
)
def test_fora_da_regra_de_negocio_e_recusado(campo, valor):
    with pytest.raises(contrato.ViolacaoDoContrato) as exc:
        contrato.validar_payload(_payload(**{campo: valor}), ESPECIALIDADES)
    assert exc.value.campo == campo


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("idade", 90),
        ("distancia_km", 450.0),
        ("dias_entre_agendamento_consulta", 120),
        ("dias_entre_agendamento_consulta", 0),
        ("historico_noshow", 12),
    ],
)
def test_fora_do_dominio_do_treino_e_predito_e_marcado(campo, valor):
    """Decisão da autora: marcar, nunca rejeitar."""
    linha = contrato.validar_payload(_payload(**{campo: valor}), ESPECIALIDADES)
    assert linha.campos_fora_do_dominio == (campo,)
    assert linha.fora_do_dominio


@pytest.mark.parametrize(
    "quando",
    [
        "2026-10-04 10:00:00",  # domingo
        "2026-10-02 12:00:00",  # almoço
        "2026-10-03 14:00:00",  # sábado à tarde
        "2026-10-02 07:30:00",  # antes de abrir
        "2026-10-02 10:15:00",  # fora do slot de 30 min
        "2026-10-03T00:00:00+00:00",  # sexta 21h em São Paulo -- só a UTC parece válida
    ],
)
def test_fora_da_grade_da_clinica_e_recusado(quando):
    with pytest.raises(contrato.ViolacaoDoContrato) as exc:
        contrato.validar_payload(_payload(data_hora_agendada=quando), ESPECIALIDADES)
    assert exc.value.campo == "data_hora_agendada"


def test_hora_utc_do_banco_e_julgada_no_fuso_da_clinica():
    """13:00 UTC é 10:00 em São Paulo: dentro da grade. Julgada em UTC, a
    mesma linha cairia no almoço e iria para a quarentena à toa."""
    linha = contrato.validar_payload(
        _payload(data_hora_agendada="2026-10-02T13:00:00+00:00"), ESPECIALIDADES
    )
    assert linha.valores["data_hora_agendada"].hour == 10


def test_mensagem_de_violacao_nao_carrega_o_valor():
    """Ela vai para o resultado do job, para o log e para a aba de dev."""
    with pytest.raises(contrato.ViolacaoDoContrato) as exc:
        contrato.validar_payload(_payload(idade="1980-13-45"), ESPECIALIDADES)
    assert "1980" not in str(exc.value)


def test_dataframe_lista_todas_as_violacoes_pelo_id_da_linha():
    df = pd.DataFrame(
        [
            {**_payload(), "id_consulta": 1},
            {**_payload(idade=150), "id_consulta": 2},
            {**_payload(sexo="X"), "id_consulta": 3},
        ]
    )
    violacoes = contrato.violacoes_do_dataframe(df, ESPECIALIDADES)

    assert [(i, v.campo) for i, v in violacoes] == [(2, "idade"), (3, "sexo")]
    assert "2 linha(s)" in contrato.resumir_violacoes(violacoes)


def test_coercao_de_colunas_nao_toca_no_dataframe_ja_tipado():
    """O `train_raw.pkl` do DVC é um pickle: reescrever uma coluna idêntica
    pode mudar o md5 do artefato e disparar um re-treino sem mudança no dado."""
    df = pd.DataFrame(
        {
            "idade": [45, 30],
            "distancia_km": [5.5, 2.0],
            "dias_entre_agendamento_consulta": [14, 7],
            "historico_noshow": [1, 0],
        }
    )
    assert contrato.coagir_colunas_numericas(df) is df


def test_coercao_de_colunas_converte_o_que_chega_como_texto():
    df = pd.DataFrame({"idade": ["45", "30"], "distancia_km": [5, 2]})
    convertido = contrato.coagir_colunas_numericas(df)

    assert str(convertido["idade"].dtype) == "int64"
    assert str(convertido["distancia_km"].dtype) == "float64"
    assert df["idade"].tolist() == ["45", "30"]  # não muta o do chamador


def test_coercao_de_colunas_recusa_nulo_em_vez_de_virar_zero():
    df = pd.DataFrame({"idade": [45.0, np.nan]})
    with pytest.raises(contrato.ViolacaoDoContrato):
        contrato.coagir_colunas_numericas(df)
