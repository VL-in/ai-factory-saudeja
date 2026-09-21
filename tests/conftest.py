import sys
from pathlib import Path

import pandas as pd
import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import observabilidade


def _descartar_evento(**evento):
    """Destino nulo de `eventos_app` durante os testes."""


@pytest.fixture(autouse=True)
def observabilidade_sem_destino_real():
    """Nenhum teste grava em `eventos_app` de verdade (Passo 8.5).

    Sem isto, qualquer teste que faça uma predição enfileira um evento e o
    worker de `src/observabilidade.py` cai no destino default -- que resolve
    `SUPABASE_URL`/`SUPABASE_SECRET_KEY`, e o `.env` local de quem desenvolve
    aponta para o projeto REAL: a suíte rápida sujaria a tabela de produção e
    ainda deixaria um client conectado em cache para os testes seguintes.
    Quem precisa observar o que foi registrado troca o destino por um espião
    próprio (tests/test_observabilidade.py); o teardown aqui devolve o nulo.
    """
    observabilidade.definir_escritor(_descartar_evento)
    yield
    observabilidade.flush(timeout=2)
    observabilidade.definir_escritor(_descartar_evento)


@pytest.fixture
def df_consultas():
    """Amostra sintética com o mesmo schema de data/consultas-historicas.csv."""
    return pd.DataFrame(
        {
            "id_consulta": [1, 2, 3, 4, 5, 6],
            "id_paciente": ["P1", "P2", "P3", "P4", "P5", "P6"],
            "idade": [52, 22, 34, 61, 45, 29],
            "sexo": ["F", "F", "M", "M", "F", "M"],
            "especialidade": [
                "cardiologia",
                "clinica geral",
                "cardiologia",
                "dermatologia",
                "clinica geral",
                "dermatologia",
            ],
            "distancia_km": [7.4, 11.3, 3.2, 15.0, 5.5, 9.8],
            "dias_entre_agendamento_consulta": [14, 65, 7, 30, 21, 3],
            "historico_noshow": [2, 1, 0, 3, 0, 1],
            "no_show": [0, 0, 1, 0, 1, 0],
            "data_hora_agendada": [
                "2026-01-05 09:00:00",  # segunda
                "2026-01-06 14:30:00",  # terca
                "2026-01-09 18:00:00",  # sexta, fim de expediente (no_show=1)
                "2026-01-08 10:00:00",  # quinta
                "2026-01-09 17:30:00",  # sexta, fim de expediente (no_show=1)
                "2026-01-10 08:30:00",  # sabado
            ],
        }
    )


@pytest.fixture
def df_consultas_smote():
    """Amostra maior que df_consultas -- SMOTE-NC (k_neighbors=3) precisa de
    amostras suficientes na classe minoritária do fold de treino."""
    n = 40
    especialidades = ["cardiologia", "clinica geral", "dermatologia"]
    return pd.DataFrame(
        {
            "id_consulta": range(1, n + 1),
            "id_paciente": [f"P{i}" for i in range(1, n + 1)],
            "idade": [20 + (i % 50) for i in range(n)],
            "sexo": ["F" if i % 2 == 0 else "M" for i in range(n)],
            "especialidade": [especialidades[i % 3] for i in range(n)],
            "distancia_km": [round(2 + (i % 15) * 1.3, 1) for i in range(n)],
            "dias_entre_agendamento_consulta": [1 + (i % 60) for i in range(n)],
            "historico_noshow": [i % 4 for i in range(n)],
            "no_show": [1 if i % 3 == 0 else 0 for i in range(n)],
            "data_hora_agendada": [
                f"2026-01-{5 + (i % 20):02d} {8 + (i % 9):02d}:00:00" for i in range(n)
            ],
        }
    )


@pytest.fixture
def csv_consultas(tmp_path, df_consultas):
    caminho = tmp_path / "consultas-historicas.csv"
    df_consultas.to_csv(caminho, index=False)
    return caminho
