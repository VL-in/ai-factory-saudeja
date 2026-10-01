"""
SaúdeJá — gate de dados do re-treino (Passo 10.3, stage `validate_data`).

Cada camada bloqueante tem um teste que a dispara de propósito e confere a
mensagem; o caminho feliz roda sobre um dataset no formato da semente. O
`main()` é exercitado com caminhos em `tmp_path`: o selo (dependência do
`preprocess` no dvc.yaml) só pode existir quando o dataset passou.
"""
import json

import numpy as np
import pandas as pd
import pytest

import validate_data

HORAS = (8, 9, 10, 11, 13, 14, 15, 16, 17)
ESPECIALIDADES = ["cardiologia", "clinica geral", "dermatologia"]


def _dataset(n=400, taxa_positivos=0.3, inicio_id=1):
    """Semente sintética válida: seg-sex de 05 a 09/01/2026, horários da
    grade, positivos suficientes para o mínimo do fold de teste (20)."""
    positivos = int(n * taxa_positivos)
    return pd.DataFrame(
        {
            "id_consulta": range(inicio_id, inicio_id + n),
            "id_paciente": [f"P{i}" for i in range(n)],
            "idade": [20 + i % 60 for i in range(n)],
            "sexo": ["F" if i % 2 else "M" for i in range(n)],
            "especialidade": [ESPECIALIDADES[i % 3] for i in range(n)],
            "distancia_km": [round(1 + (i % 40) * 1.1, 1) for i in range(n)],
            "dias_entre_agendamento_consulta": [1 + i % 80 for i in range(n)],
            "historico_noshow": [i % 5 for i in range(n)],
            "no_show": [1] * positivos + [0] * (n - positivos),
            "data_hora_agendada": [
                f"2026-01-{5 + i % 5:02d} {HORAS[i % len(HORAS)]:02d}:00:00" for i in range(n)
            ],
        }
    )


def test_dataset_valido_e_aprovado_com_resumo():
    relatorio = validate_data.validar(_dataset())

    assert relatorio.aprovado, relatorio.bloqueios
    assert relatorio.resumo["linhas"] == 400
    assert relatorio.resumo["positivos_no_fold_de_teste"] >= 20


def test_minimo_de_positivos_vem_da_tolerancia_do_gate():
    """1/tolerância (SLO §3.1): com 0.05, 20 -- não um número solto."""
    assert validate_data.minimo_de_positivos_no_fold(validate_data.PARAMS) == 20
    params = {"gate": {"tolerancia": {"recall_1": 0.02, "f1_1": 0.05}}}
    assert validate_data.minimo_de_positivos_no_fold(params) == 50


def test_coluna_ausente_bloqueia_no_schema():
    relatorio = validate_data.validar(_dataset().drop(columns=["historico_noshow"]))
    assert not relatorio.aprovado
    assert "colunas ausentes" in relatorio.bloqueios[0]


def test_nulo_e_id_duplicado_bloqueiam_no_schema():
    df = _dataset()
    df.loc[3, "idade"] = np.nan
    df.loc[5, "id_consulta"] = df.loc[4, "id_consulta"]

    bloqueios = " | ".join(validate_data.validar(df).bloqueios)

    assert "nulos" in bloqueios
    assert "duplicado" in bloqueios


def test_linha_fora_da_regra_de_negocio_bloqueia_com_o_id():
    df = _dataset()
    df.loc[7, "idade"] = 150

    relatorio = validate_data.validar(df)

    assert not relatorio.aprovado
    assert "regra de negócio" in relatorio.bloqueios[0]
    assert "'8'" in relatorio.bloqueios[0]  # id_consulta da linha 7


def test_especialidade_que_o_cadastro_nao_oferece_bloqueia():
    df = _dataset()
    df.loc[0, "especialidade"] = "neurologia"

    relatorio = validate_data.validar(df)

    assert any("especialidade" in b for b in relatorio.bloqueios)


def test_fora_do_dominio_do_treino_so_e_registrado():
    df = _dataset()
    df.loc[0, "distancia_km"] = 120.0

    relatorio = validate_data.validar(df)

    assert relatorio.aprovado
    assert relatorio.resumo["fora_do_dominio_do_treino"] == {"distancia_km": 1}


@pytest.mark.parametrize("taxa", [0.05, 0.6])
def test_taxa_de_positivos_fora_da_faixa_bloqueia(taxa):
    relatorio = validate_data.validar(_dataset(n=500, taxa_positivos=taxa))
    assert any("taxa de positivos" in b for b in relatorio.bloqueios)


def test_poucos_positivos_no_fold_de_teste_bloqueia():
    """60 positivos -> 12 no fold de teste: abaixo de 1/0.05 = 20."""
    relatorio = validate_data.validar(_dataset(n=200, taxa_positivos=0.3))

    assert relatorio.resumo["positivos_no_fold_de_teste"] < 20
    assert any("fold de teste" in b for b in relatorio.bloqueios)


def test_psi_alerta_quando_a_producao_se_afasta_da_semente_sem_bloquear():
    semente = _dataset()
    producao = _dataset(n=60, inicio_id=10_000).assign(
        distancia_km=45.0, especialidade="dermatologia"
    )
    df = pd.concat([semente, producao], ignore_index=True)

    relatorio = validate_data.validar(df, semente=semente)

    assert relatorio.aprovado
    assert relatorio.resumo["linhas_de_producao"] == 60
    assert relatorio.resumo["psi"]["distancia_km"] > 0.2
    assert any("PSI" in a for a in relatorio.alertas)


def test_sem_producao_nao_ha_psi():
    semente = _dataset()
    relatorio = validate_data.validar(semente, semente=semente)
    assert relatorio.resumo["psi"] is None


def _rodar_main(tmp_path, monkeypatch, df):
    dados = tmp_path / "consultas-treino.csv"
    df.to_csv(dados, index=False)
    selo = tmp_path / "interim" / "dados_validados.json"
    relatorio = tmp_path / "interim" / "relatorio_dados.json"
    monkeypatch.setattr(validate_data, "DATA_PATH", str(dados))
    monkeypatch.setattr(validate_data, "SEMENTE_PATH", str(dados))
    monkeypatch.setattr(validate_data, "SELO_PATH", str(selo))
    monkeypatch.setattr(validate_data, "RELATORIO_PATH", str(relatorio))
    return validate_data.main(), selo, relatorio


def test_main_aprovado_escreve_selo_e_relatorio(tmp_path, monkeypatch):
    codigo, selo, relatorio = _rodar_main(tmp_path, monkeypatch, _dataset())

    assert codigo == 0
    conteudo = json.loads(selo.read_text(encoding="utf-8"))
    assert set(conteudo) == {"dataset_md5", "linhas", "positivos"}
    assert json.loads(relatorio.read_text(encoding="utf-8"))["aprovado"] is True


def test_main_barrado_nao_escreve_selo_mas_escreve_o_relatorio(tmp_path, monkeypatch):
    """Sem selo o `preprocess` não roda (dependência no dvc.yaml); o relatório
    fica, porque é ele que o gate publica no resumo do re-treino."""
    df = _dataset()
    df.loc[0, "sexo"] = "X"

    codigo, selo, relatorio = _rodar_main(tmp_path, monkeypatch, df)

    assert codigo == 1
    assert not selo.exists()
    assert json.loads(relatorio.read_text(encoding="utf-8"))["aprovado"] is False
