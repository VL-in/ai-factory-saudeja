"""
SaúdeJá — testes de src/export_treino.py (inclui o contrato de
features e o `historico_noshow` gravado no cadastro).

`montar_dataset_producao`/`montar_dataset_treino` recebem `consultas` já
prontas (mesma forma que `db.repositorio_treino.consultas_para_export()`
devolveria) -- sem depender de Supabase de verdade, mesma filosofia de
`tests/test_job_inferencia.py` para o job D-2.
"""
import sys
from pathlib import Path

import pandas as pd

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import export_treino


def _consulta(id_consulta, id_titular, idade, data_hora_agendada, status, **kwargs):
    """Linha da base de treino (schema `treino`, ADR-010): idade já na data
    da consulta e pseudônimos no lugar dos identificadores."""
    base = {
        "id_consulta": id_consulta,
        "id_titular": id_titular,
        "idade": idade,
        "sexo": "F",
        "especialidade": "cardiologia",
        "distancia_km": 5.5,
        "data_hora_agendada": data_hora_agendada,
        "dias_entre_agendamento_consulta": 14,
        "historico_noshow": 0,
        "status": status,
        "lembrete_enviado": False,
    }
    base.update(kwargs)
    return base


def test_montar_dataset_producao_vazio_sem_agendamentos():
    df = export_treino.montar_dataset_producao([])
    assert list(df.columns) == export_treino.COLUNAS_SAIDA
    assert len(df) == 0


def test_no_show_derivado_do_status():
    agendamentos = [
        _consulta("A1", "P1", 36, "2026-01-05 10:00:00", "concluido"),
        _consulta("A2", "P2", 36, "2026-01-06 10:00:00", "no_show"),
    ]
    df = export_treino.montar_dataset_producao(agendamentos)
    df = df.set_index("id_consulta")
    assert df.loc["A1", "no_show"] == 0
    assert df.loc["A2", "no_show"] == 1


def test_idade_e_identificadores_vem_da_base_de_treino_sem_recalculo():
    """A idade já chega calculada na data da consulta (a data de nascimento
    não vai para a base de treino), e o paciente aparece pelo pseudônimo do
    titular, nunca pelo hash do CPF do banco da clínica."""
    consultas = [_consulta("c-hmac", "t-hmac", 25, "2026-06-15 10:00:00", "concluido")]

    linha = export_treino.montar_dataset_producao(consultas).iloc[0]

    assert linha["idade"] == 25
    assert linha["id_paciente"] == "t-hmac"
    assert linha["id_consulta"] == "c-hmac"


def test_lembrete_enviado_vai_para_o_dataset():
    """Contra o feedback loop: o re-treino precisa saber quem foi lembrado."""
    consultas = [
        _consulta("A1", "P1", 36, "2026-01-05 10:00:00", "concluido", lembrete_enviado=True),
        _consulta("A2", "P1", 36, "2026-01-06 10:00:00", "concluido"),
    ]

    df = export_treino.montar_dataset_producao(consultas).set_index("id_consulta")

    assert df.loc["A1", "lembrete_enviado"] == 1
    assert df.loc["A2", "lembrete_enviado"] == 0


def test_consentimento_nao_vira_coluna_do_dataset():
    assert not [c for c in export_treino.COLUNAS_SAIDA if "consent" in c]


def test_historico_noshow_e_o_valor_gravado_no_cadastro_nao_recalculado():
    """O dataset de treino carrega o `historico_noshow` com que o
    job D-2 predisse (o gravado no cadastro). Até aqui ele era recontado a
    partir dos desfechos -- e uma falta acontecida entre o agendamento e a
    consulta entrava no recálculo, mas não na predição: skew treino-serving.

    A1 é uma falta anterior a A2, mas A2 foi agendada antes de A1 acontecer
    (gravou 0): o treino tem de ver 0, como o modelo viu."""
    agendamentos = [
        _consulta("A1", "P1", 36, "2026-01-05 10:00:00", "no_show"),
        _consulta(
            "A2", "P1", 36, "2026-01-20 10:00:00", "concluido", historico_noshow=0
        ),
        _consulta(
            "A3", "P2", 36, "2026-01-30 10:00:00", "concluido", historico_noshow=3
        ),
    ]
    df = export_treino.montar_dataset_producao(agendamentos).set_index("id_consulta")

    assert df.loc["A2", "historico_noshow"] == 0
    assert df.loc["A3", "historico_noshow"] == 3


def test_linha_fora_do_contrato_fica_fora_do_export_e_e_identificada():
    """Mesma quarentena do job D-2: uma linha impossível não entra no dataset
    de treino, e o export diz qual foi -- pelo id do agendamento, sem o valor."""
    agendamentos = [
        _consulta("A1", "P1", 36, "2026-01-05 10:00:00", "concluido"),
        # domingo: a clínica não abre
        _consulta("A2", "P1", 36, "2026-02-01 10:00:00", "no_show"),
        # especialidade que o cadastro não oferece
        _consulta(
            "A3", "P2", 36, "2026-01-06 10:00:00", "concluido", especialidade="xxx"
        ),
    ]
    df, quarentena = export_treino.montar_dataset_producao_com_quarentena(agendamentos)

    assert list(df["id_consulta"]) == ["A1"]
    assert {ident for ident, _ in quarentena} == {"A2", "A3"}
    motivos = {ident: violacao.campo for ident, violacao in quarentena}
    assert motivos == {"A2": "data_hora_agendada", "A3": "especialidade"}
    assert all("P1" not in str(v) and "P2" not in str(v) for _, v in quarentena)


def test_data_ilegivel_vai_para_a_quarentena_sem_derrubar_o_export():
    """A data era convertida antes do contrato: uma linha ilegível derrubava o
    export mensal inteiro, com o valor na mensagem do ValueError (log público
    do re-treino). Agora quem lê a data é o contrato, que só diz o campo."""
    agendamentos = [
        _consulta("A1", "P1", 36, "2026-01-05T13:00:00+00:00", "concluido"),
        _consulta("A2", "P2", 36, "05/01/2026 às 10h", "no_show"),
    ]

    df, quarentena = export_treino.montar_dataset_producao_com_quarentena(agendamentos)

    assert list(df["id_consulta"]) == ["A1"]
    # A hora UTC do banco continua virando a hora da clínica no dataset.
    assert list(df["data_hora_agendada"]) == ["2026-01-05 10:00:00"]
    [(ident, violacao)] = quarentena
    assert ident == "A2" and violacao.campo == "data_hora_agendada"
    assert "05/01/2026" not in str(violacao)


def test_producao_nunca_emite_coluna_de_telefone_ou_outra_pii():
    agendamentos = [
        _consulta(
            "A1", "P1", 36, "2026-01-01 10:00:00", "concluido", telefone="5511999999999"
        )
    ]
    df = export_treino.montar_dataset_producao(agendamentos)
    colunas_proibidas = {"telefone", "nome", "cpf", "email"}
    assert not (colunas_proibidas & set(df.columns))


def test_montar_dataset_treino_junta_semente_e_producao(tmp_path):
    semente = pd.DataFrame(
        {
            "id_consulta": [1, 2],
            "id_paciente": ["P1057", "P1044"],
            "idade": [52, 22],
            "sexo": ["F", "F"],
            "especialidade": ["cardiologia", "clinica geral"],
            "distancia_km": [7.4, 11.3],
            "dias_entre_agendamento_consulta": [14, 65],
            "historico_noshow": [2, 1],
            "no_show": [0, 0],
            "data_hora_agendada": ["2026-01-09 14:00:00", "2026-01-07 18:00:00"],
        }
    )
    caminho_semente = tmp_path / "consultas-historicas.csv"
    semente.to_csv(caminho_semente, index=False)

    agendamentos = [
        _consulta("A1", "P1", 36, "2026-02-02 10:00:00", "no_show"),
    ]

    dataset = export_treino.montar_dataset_treino(
        caminho_semente=str(caminho_semente), consultas=agendamentos
    )

    assert len(dataset) == len(semente) + 1
    assert set(export_treino.COLUNAS_SAIDA) <= set(dataset.columns)


def test_montar_dataset_treino_sem_producao_reproduz_so_a_semente(tmp_path):
    """Enquanto ninguém registrou desfecho real, o dataset de treino deve ser
    idêntico à semente -- não um bug, o estado esperado do produto ainda sem
    volume de produção."""
    semente = pd.DataFrame(
        {
            "id_consulta": [1],
            "id_paciente": ["P1057"],
            "idade": [52],
            "sexo": ["F"],
            "especialidade": ["cardiologia"],
            "distancia_km": [7.4],
            "dias_entre_agendamento_consulta": [14],
            "historico_noshow": [2],
            "no_show": [0],
            "data_hora_agendada": ["2026-01-09 14:00:00"],
        }
    )
    caminho_semente = tmp_path / "consultas-historicas.csv"
    semente.to_csv(caminho_semente, index=False)

    dataset = export_treino.montar_dataset_treino(
        caminho_semente=str(caminho_semente), consultas=[]
    )

    pd.testing.assert_frame_equal(dataset.reset_index(drop=True), semente)


# --- smoke do ponto de entrada do retrain.yml ------------------------------------


def test_main_do_retrain_grava_o_csv_e_publica_quarentena_e_completude(
    tmp_path, monkeypatch, capsys
):
    """`python src/export_treino.py` é o primeiro passo do re-treino mensal e
    só tinha as funções internas testadas. Smoke do `main()` com o banco
    trocado por um falso: grava o CSV que o `dvc add` seguinte versiona e
    publica no summary as duas coisas que a autora precisa ver no run -- a
    quarentena e o alerta de completude de rótulo."""
    semente = tmp_path / "consultas-historicas.csv"
    pd.DataFrame(
        {
            "id_consulta": [1],
            "id_paciente": ["P1057"],
            "idade": [52],
            "sexo": ["F"],
            "especialidade": ["cardiologia"],
            "distancia_km": [7.4],
            "dias_entre_agendamento_consulta": [14],
            "historico_noshow": [2],
            "no_show": [0],
            "data_hora_agendada": ["2026-01-09 14:00:00"],
        }
    ).to_csv(semente, index=False)
    agendamentos = [
        _consulta("A1", "P1", 36, "2026-01-05 10:00:00", "concluido"),
        _consulta("A2", "P1", 36, "2026-02-01 10:00:00", "no_show"),  # domingo
    ]
    janelas = []
    monkeypatch.setattr(export_treino, "CONSULTAS_HISTORICAS_PATH", str(semente))
    monkeypatch.setattr(
        export_treino.repositorio_treino,
        "consultas_para_export",
        lambda desde: janelas.append(desde) or agendamentos,
    )
    monkeypatch.setattr(
        export_treino.repositorio_treino, "contar_sem_desfecho", lambda desde, antes_de: 2
    )
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    saida = tmp_path / "consultas-treino.csv"

    linhas = export_treino.main(str(saida))

    assert linhas == 2  # semente + A1; A2 ficou em quarentena
    assert len(pd.read_csv(saida)) == 2
    publicado = summary.read_text(encoding="utf-8")
    assert publicado.strip() == capsys.readouterr().out.strip()
    assert "fora do contrato" in publicado
    assert "alerta de completude" in publicado and "50%" in publicado  # 2 de 2+2
    # Só a janela de treino: consultas com até 90 dias (ADR-009).
    from datetime import datetime, timedelta, timezone

    esperado = datetime.now(timezone.utc) - timedelta(days=90)
    assert abs(janelas[0] - esperado) < timedelta(minutes=5)
