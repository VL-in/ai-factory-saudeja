"""Testes de DADOS: as regras de `src/contrato_dataset_externo.py` contra os
arquivos reais de `data/externo/` (dataset Kaggle `noshowappointments`).

Cada teste roda quando o arquivo da fase correspondente existe e é pulado
(com o motivo) quando ainda não existe -- no CI, que não tem os dados, todos
são pulados; as regras em si são provadas sempre por
`tests/test_contrato_dataset_externo.py`, com dado fictício.

    pytest -m dados_externos -rs        # só estes, com o motivo dos pulados

| Fase | Arquivo                         | Testes                                   |
|------|---------------------------------|------------------------------------------|
| 0    | params.yaml (dados.externa)     | bloco do parâmetro; treino oficial limpo |
| 1    | raw/…csv, LICENCA-E-ORIGEM.md   | schema do bruto; licença                 |
| 2    | pool-adaptado.csv, manifesto    | schema/negócio, imputação, fidelidade,   |
|      |                                 | sinal declarado da sexta 17h-18h         |
| 3    | teste-reservado.csv, consultas- | schema/negócio, imputação, partição,     |
|      | externas.csv                    | manifesto completo                       |

| 6    | semente + amostra (export com   | peso da semente nos limiares 200/600,    |
|      | a origem externa ligada)        | split por grupo, positivos de produção,  |
|      |                                 | revisão de tolerância (50 positivos),    |
|      |                                 | features por paciente no pool            |

As Fases 4 e 5 não geram dataset novo: leem o pool, o teste reservado e a
amostra, que já estão cobertos aqui. As garantias delas (validação por
paciente, permutação, curva de aprendizado) são testes de código, escritos
junto com o código.
"""

from pathlib import Path

import pandas as pd
import pytest

import contrato_dataset_externo as cde
from config_projeto import carregar_params

pytestmark = pytest.mark.dados_externos

RAIZ = Path(__file__).resolve().parents[1]
PARAMS = carregar_params()
ESPECIALIDADES = PARAMS["cadastro"]["especialidades"]


def _exigir(caminho: Path, fase: str) -> Path:
    if not caminho.exists():
        pytest.skip(f"{fase} ainda não gerou {caminho.relative_to(RAIZ)}")
    return caminho


@pytest.fixture(scope="module")
def bruto():
    return pd.read_csv(_exigir(cde.CAMINHO_BRUTO, "Fase 1"))


@pytest.fixture(scope="module")
def semente():
    return pd.read_csv(_exigir(RAIZ / "data" / "consultas-historicas.csv", "dvc pull"))


@pytest.fixture(scope="module")
def pool():
    return cde.carregar_adaptado(_exigir(cde.CAMINHO_POOL, "Fase 2"))


@pytest.fixture(scope="module")
def manifesto():
    return cde.carregar_manifesto(_exigir(cde.CAMINHO_MANIFESTO, "Fase 2"))


@pytest.fixture(scope="module")
def reservado():
    return cde.carregar_adaptado(_exigir(cde.CAMINHO_RESERVADO, "Fase 3"))


@pytest.fixture(scope="module")
def amostra():
    return cde.carregar_adaptado(_exigir(cde.CAMINHO_AMOSTRA, "Fase 3"))


@pytest.fixture(scope="module")
def parametro_externa():
    externa = PARAMS.get("dados", {}).get("externa")
    if externa is None:
        pytest.skip("Fase 0 ainda não criou dados.externa no params.yaml")
    return externa


# --- Fase 0 ---------------------------------------------------------------
def test_params_tem_o_bloco_externa_desligado(parametro_externa):
    assert cde.verificar_params_externa(PARAMS) == []


def test_treino_oficial_nao_tem_linha_externa_com_o_parametro_desligado():
    treino = pd.read_csv(
        _exigir(RAIZ / "data" / "consultas-treino.csv", "dvc pull"), dtype={"id_consulta": str}
    )
    habilitada = bool(PARAMS.get("dados", {}).get("externa", {}).get("habilitada", False))
    assert cde.verificar_treino_sem_externa(treino, habilitada) == []


# --- Fase 1 ---------------------------------------------------------------
def test_bruto_tem_o_schema_e_os_dominios_do_kaggle(bruto):
    assert cde.verificar_bruto(bruto) == []


def test_licenca_e_origem_documentadas():
    texto = _exigir(cde.CAMINHO_LICENCA, "Fase 1").read_text(encoding="utf-8")
    assert cde.verificar_licenca(texto) == []


# --- Fase 2 ---------------------------------------------------------------
def test_pool_cumpre_schema_e_regra_de_negocio(pool, semente):
    assert cde.verificar_adaptado(pool, semente, ESPECIALIDADES) == []


def test_pool_imputacao_independente_do_desfecho_e_com_a_cara_da_semente(pool, semente):
    assert cde.verificar_imputadas(pool, semente) == []


def test_pool_tem_o_sinal_declarado_da_sexta_na_razao_do_params(pool, manifesto):
    razao = float(PARAMS["dados"]["externa"]["razao_sinal_sexta"])
    assert cde.verificar_sinal_sexta(pool, razao) == []
    assert manifesto["sinal_sexta"]["razao_alvo"] == razao


def test_pool_fiel_ao_bruto(pool, bruto, manifesto):
    removidas = int(manifesto["filtros"].get("quarentena_contrato", 0))
    assert cde.verificar_fidelidade_ao_bruto(pool, bruto, removidas) == []


def test_manifesto_bate_com_o_bruto_e_o_pool(manifesto, bruto, pool):
    sha = cde.sha256_do_arquivo(cde.CAMINHO_BRUTO)
    assert cde.verificar_manifesto(manifesto, sha, pool=pool) == []


# --- Fase 3 ---------------------------------------------------------------
@pytest.mark.parametrize("nome", ["reservado", "amostra"])
def test_particoes_cumprem_schema_e_regra_de_negocio(nome, request, semente):
    df = request.getfixturevalue(nome)
    assert cde.verificar_adaptado(df, semente, ESPECIALIDADES) == []


@pytest.mark.parametrize("nome", ["reservado", "amostra"])
def test_particoes_imputacao_independente_do_desfecho(nome, request, semente):
    df = request.getfixturevalue(nome)
    assert cde.verificar_imputadas(df, semente) == []


def test_particao_por_paciente_e_amostra_do_tamanho_do_params(
    pool, reservado, amostra, parametro_externa
):
    n_amostra = int(parametro_externa["n_amostra"])
    assert cde.verificar_particao(pool, reservado, amostra, n_amostra) == []


def test_manifesto_bate_com_todos_os_arquivos(manifesto, pool, reservado, amostra):
    sha = cde.sha256_do_arquivo(cde.CAMINHO_BRUTO)
    assert cde.verificar_manifesto(manifesto, sha, pool, reservado, amostra) == []


# --- Fase 6: regras da Etapa 5 com a amostra real ------------------------------
def _dataset_com_externa(semente, amostra, n_externa):
    import export_treino

    return export_treino.montar_dataset_treino(
        caminho_semente=str(RAIZ / "data" / "consultas-historicas.csv"),
        consultas=[],
        externa=amostra.head(n_externa)[export_treino.COLUNAS_SAIDA],
        params={"dados": {"externa": {"habilitada": True}}},
    )


@pytest.mark.parametrize(
    ("n_externa", "peso"), [(199, 1.0), (200, 0.3), (599, 0.3), (600, 0.0), (1500, 0.0)]
)
def test_peso_da_semente_com_linhas_externas_reais(semente, amostra, n_externa, peso):
    import validate_data

    df = _dataset_com_externa(semente, amostra, n_externa)
    relatorio = validate_data.validar(df, semente=semente)
    assert relatorio.resumo["linhas_externas"] == n_externa
    assert relatorio.resumo["linhas_de_producao"] == 0
    assert relatorio.resumo["regras_dado_real"]["peso_da_semente"] == peso


def test_semente_mais_amostra_passa_no_gate_de_dados_e_aciona_a_revisao_de_tolerancia(
    semente, amostra
):
    import retrain_gate
    import validate_data

    df = _dataset_com_externa(semente, amostra, len(amostra))
    relatorio = validate_data.validar(df, semente=semente)
    assert relatorio.aprovado, relatorio.bloqueios
    regras = relatorio.resumo["regras_dado_real"]
    assert regras["modo_de_split"] == "por_grupo"  # id de paciente estável
    assert regras["positivos_de_producao_no_fold"] == 0  # externa não é a clínica
    positivos = relatorio.resumo["positivos_no_fold_de_teste"]
    assert positivos >= retrain_gate.POSITIVOS_PARA_REVISAR_TOLERANCIA
    avisos = retrain_gate._avisos_de_tolerancia(PARAMS["gate"]["tolerancia"], positivos)
    assert avisos and "revisar SLO" in avisos[0]


def test_features_por_paciente_point_in_time_no_pool(pool):
    import regras_dado_real

    f = regras_dado_real.features_por_paciente(pool)
    assert len(f) == len(pool)
    assert (f["taxa_faltas_anteriores"].between(0, 1)).all()
    com_anteriores = f["consultas_anteriores"] > 0
    assert (f.loc[com_anteriores, "dias_desde_ultima_consulta"] > 0).all()
    # nunca mais faltas anteriores que consultas anteriores
    assert (
        f["taxa_faltas_anteriores"] * f["consultas_anteriores"] <= f["consultas_anteriores"]
    ).all()
