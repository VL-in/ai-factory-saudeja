"""
SaúdeJá — o contrato de features nas portas de entrada do dado.

O contrato (`src/contrato_features.py`) é aplicado depois da gravação: no job
D-2, que manda para a quarentena o que ele recusa, e no export do re-treino,
que deixa a linha de fora. Um agendamento que o cadastro aceita e o contrato
recusa fica sem predição e sem lembrete, e nunca vira dado de treino. A
pessoa recebe "Agendamento confirmado", e ninguém fica sabendo da falha até
olhar a quarentena.

Este arquivo liga as duas pontas: o cadastro roda de verdade (`logic`), só com
o banco trocado, e o que ele gravaria nas duas bases passa pelo contrato do
mesmo jeito que o job e o export o leem.

Desde 2026-11-08 o cadastro e a API aplicam o próprio contrato, e as
divergências que este arquivo registrava como `xfail(strict=True)` (distância
negativa, nascimento entre 1900 e 120 anos atrás, `/predict` sem contrato)
viraram testes comuns. Uma divergência nova que precise esperar correção
volta a entrar como `xfail(strict=True)`, com o motivo escrito.
"""
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import agenda_clinica
import contrato_features as contrato
import pseudonimo
from api.main import app
from db import repositories, repositorio_treino
from jobs import inferencia_diaria as job
from ui import logic

HOJE = date(2026, 11, 9)  # segunda-feira
ID_AGENDAMENTO = "0b9c6f2e-1d3a-4f5b-9c8d-7e6f5a4b3c2d"
HISTORICO_NOSHOW = 2


def _ultimo_dia_do_prazo() -> date:
    dia = HOJE + timedelta(days=agenda_clinica.PRAZO_MAXIMO_AGENDAMENTO_DIAS)
    while not agenda_clinica.horarios_do_dia(dia):
        dia -= timedelta(days=1)
    return dia


@pytest.fixture
def cadastrar(monkeypatch):
    """O cadastro da tela do paciente, com o banco das duas bases trocado por
    funções que guardam o que seria gravado. A caixa do treino vai marcada:
    é o caminho em que a linha chega também à base de treino."""
    monkeypatch.setenv(pseudonimo.VARIAVEL_CHAVE, "c" * 32)
    monkeypatch.setattr(logic, "hoje_na_clinica", lambda: HOJE)
    gravado: dict[str, dict] = {}

    def _guardar(chave, retorno):
        def _funcao(**kwargs):
            gravado[chave] = kwargs
            return retorno

        return _funcao

    monkeypatch.setattr(repositories, "inserir_paciente", _guardar("paciente", {"id": "p1"}))
    monkeypatch.setattr(repositories, "contar_no_shows_anteriores", lambda _id: HISTORICO_NOSHOW)
    monkeypatch.setattr(
        repositories, "inserir_agendamento", _guardar("agendamento", {"id": ID_AGENDAMENTO})
    )
    monkeypatch.setattr(repositorio_treino, "eventos_do_titular", lambda _id: [])
    monkeypatch.setattr(repositorio_treino, "registrar_consentimentos", lambda *a, **k: 0)
    monkeypatch.setattr(repositorio_treino, "inserir_consulta", _guardar("treino", True))
    monkeypatch.setattr(repositorio_treino, "registrar_pedido", lambda *a, **k: "pedido")

    def _cadastrar(**sobrescritos) -> dict[str, dict]:
        campos = {
            "nome_completo": "Paciente de Teste",
            "cpf": "111.444.777-35",
            "telefone": "(11) 98765-4321",
            "data_nascimento": date(1990, 1, 1),
            "sexo": "F",
            "especialidade": logic.listar_especialidades()[0],
            "distancia_km": 5.5,
            "data_consulta": HOJE + timedelta(days=7),
            "hora_consulta": time(9, 0),
            **sobrescritos,
        }
        gravado.clear()
        resultado = logic.cadastrar_paciente_e_agendamento(
            **campos,
            consentimentos={"treino": True},
            versao_documentos="politica 1.1; termos 1.0",
        )
        assert not resultado["falha_no_treino"], "a etapa de treino falhou no teste"
        return dict(gravado)

    return _cadastrar


def _linha_como_o_banco_devolve(gravado: dict[str, dict]) -> dict:
    """O agendamento como `buscar_agendamentos_d2_pendentes` o entrega ao job:
    data em texto ISO, e a data e hora da consulta em UTC, como o PostgREST
    devolve um `timestamptz`."""
    paciente, agendamento = gravado["paciente"], gravado["agendamento"]
    return {
        "pacientes": {
            "data_nascimento": paciente["data_nascimento"].isoformat(),
            "sexo": paciente["sexo"],
        },
        "especialidade": agendamento["especialidade"],
        "distancia_km": agendamento["distancia_km"],
        "dias_entre_agendamento_consulta": agendamento["dias_entre_agendamento_consulta"],
        "historico_noshow": agendamento["historico_noshow"],
        "data_hora_agendada": agendamento["data_hora_agendada"]
        .astimezone(timezone.utc)
        .isoformat(),
    }


def _violacoes(gravado: dict[str, dict]) -> dict[str, contrato.ViolacaoDoContrato]:
    """O que o contrato recusaria em cada base: no job D-2, sobre a linha da
    clínica, e no export, sobre a linha de treino. A lista de especialidades é
    a do cadastro, a mesma do `validate_data` (que ela esteja no mapa do
    modelo, o teste de coerência já garante)."""
    especialidades = logic.listar_especialidades()
    violacoes = {
        "job_d2": contrato.violacao_ou_none(
            job._payload_de_agendamento(_linha_como_o_banco_devolve(gravado)), especialidades
        ),
        "export_treino": contrato.violacao_ou_none(gravado["treino"], especialidades),
    }
    return {base: v for base, v in violacoes.items() if v is not None}


def _aceito_pelo_contrato(cadastrar, **campos) -> None:
    """O cadastro aceita, e o contrato também aceita, nas duas bases."""
    gravado = cadastrar(**campos)
    assert _violacoes(gravado) == {}


def _coerente(cadastrar, **campos) -> None:
    """Recusado na tela, ou aceito também pelo contrato. Só não pode ser
    aceito na tela e recusado depois."""
    try:
        gravado = cadastrar(**campos)
    except logic.ErroValidacaoCadastro:
        return
    assert _violacoes(gravado) == {}, "o cadastro aceita o que o contrato recusa"


# --- o que o cadastro aceita, o contrato aceita ------------------------------------


@pytest.mark.parametrize(
    "campos",
    [
        {},
        {"sexo": "M"},
        {"data_nascimento": HOJE},  # recém-nascido: a clínica atende pediatria
        {"data_nascimento": date(HOJE.year - 120, HOJE.month, HOJE.day)},  # 120 anos
        {"data_consulta": HOJE, "hora_consulta": time(8, 0)},  # antecedência 0
        {"data_consulta": _ultimo_dia_do_prazo(), "hora_consulta": time(8, 0)},
        {"distancia_km": 0.0},
        {"distancia_km": 500.0},  # o máximo do campo na tela
    ],
    ids=[
        "tipico",
        "sexo-m",
        "recem-nascido",
        "120-anos",
        "consulta-hoje",
        "ultimo-dia-do-prazo",
        "distancia-zero",
        "distancia-maxima-do-campo",
    ],
)
def test_cadastro_aceito_passa_no_contrato_do_job_e_do_export(cadastrar, campos):
    _aceito_pelo_contrato(cadastrar, **campos)


def test_toda_especialidade_oferecida_passa_no_contrato(cadastrar):
    for especialidade in logic.listar_especialidades():
        _aceito_pelo_contrato(cadastrar, especialidade=especialidade)


@pytest.mark.parametrize("dias_a_frente", [7, 12], ids=["segunda", "sabado"])
def test_todo_horario_oferecido_passa_no_contrato(cadastrar, dias_a_frente):
    """A grade do cadastro (`agenda_clinica`) é a regra de negócio do
    contrato. A antecedência e o fuso fazem o caminho todo: hora da clínica,
    UTC no banco e de volta à hora da clínica no job."""
    dia = HOJE + timedelta(days=dias_a_frente)
    horarios = logic.horarios_disponiveis(dia)
    assert horarios
    for hora in horarios:
        _aceito_pelo_contrato(cadastrar, data_consulta=dia, hora_consulta=hora)


# --- o que o contrato recusa, o cadastro recusa antes de gravar --------------------


def _recusado_sem_gravar(cadastrar, **campos) -> None:
    """Recusado como erro de preenchimento: conta como recusa, e não como
    falha do banco que dispara o alerta de erro."""
    with pytest.raises(logic.ErroValidacaoCadastro):
        cadastrar(**campos)


@pytest.mark.parametrize(
    "campos",
    [
        {"distancia_km": -3.0},
        {"distancia_km": 500.5},
        {"distancia_km": None},  # campo vazio: nunca vira 0 km
        {"sexo": "X"},
        {"data_nascimento": date(1900, 1, 2)},  # o antigo limite fixo do formulário
        # 120 anos hoje e 121 no dia da consulta: o job mede na consulta.
        {
            "data_nascimento": date(HOJE.year - 121, HOJE.month, HOJE.day) + timedelta(days=3),
            "data_consulta": HOJE + timedelta(days=7),
        },
    ],
    ids=[
        "distancia-negativa",
        "distancia-acima-de-500",
        "distancia-vazia",
        "sexo-x",
        "nascimento-1900",
        "121-anos-na-consulta",
    ],
)
def test_o_que_o_contrato_recusa_o_cadastro_recusa_antes_de_gravar(cadastrar, campos):
    _recusado_sem_gravar(cadastrar, **campos)
    _coerente(cadastrar, **campos)


# --- a API de predição -------------------------------------------------------------


@pytest.fixture(scope="module")
def cliente_api():
    with TestClient(app) as cliente:
        yield cliente


def _payload_da_api(**sobrescritos) -> dict:
    return {
        "idade": 45,
        "sexo": "F",
        "especialidade": logic.listar_especialidades()[0],
        "distancia_km": 5.5,
        "dias_entre_agendamento_consulta": 10,
        "historico_noshow": 0,
        "data_hora_agendada": datetime.combine(HOJE, time(9, 0)).isoformat(),
        **sobrescritos,
    }


def test_api_prediz_o_que_o_contrato_aceita(cliente_api):
    payload = _payload_da_api()
    assert contrato.violacao_ou_none(payload, logic.listar_especialidades()) is None

    assert cliente_api.post("/predict", json=payload).status_code == 200


@pytest.mark.parametrize(
    "sobrescritos",
    [
        {"idade": 150},
        {"dias_entre_agendamento_consulta": 400},
        {"data_hora_agendada": datetime.combine(HOJE - timedelta(days=1), time(3, 0)).isoformat()},
        {"distancia_km": 9999.0},
    ],
    ids=["idade-150", "antecedencia-400-dias", "domingo-3h", "distancia-9999"],
)
def test_api_recusa_o_que_o_contrato_recusa(cliente_api, sobrescritos):
    payload = _payload_da_api(**sobrescritos)
    assert contrato.violacao_ou_none(payload, logic.listar_especialidades()) is not None

    assert cliente_api.post("/predict", json=payload).status_code == 422
