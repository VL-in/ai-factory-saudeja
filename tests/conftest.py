import sys
from pathlib import Path

import pandas as pd
import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


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
        }
    )


@pytest.fixture
def csv_consultas(tmp_path, df_consultas):
    caminho = tmp_path / "consultas-historicas.csv"
    df_consultas.to_csv(caminho, index=False)
    return caminho
