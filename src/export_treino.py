"""
SaúdeJá — export dos desfechos reais registrados pela clínica (Passo 9.0)
para o dataset de treino que alimenta o gate de re-treino mensal (Passo 9.1).

Pré-requisito que este módulo resolve: `data/consultas-historicas.csv` é
fixo em ~380 linhas e `RANDOM_STATE`/`TEST_SIZE` são fixos em
`src/preprocess.py` -- sem dado fresco, todo re-treino mensal produziria a
mesma métrica e o gate nunca bloquearia nem promoveria. A clínica passa a
registrar o desfecho de cada agendamento na aba "Fila do dia"
(`db.repositories.atualizar_status_agendamento`, `ui/app.py`), e este script
lê esses desfechos e monta `data/consultas-treino.csv` = semente herdada da
Camila (intocada, para preservar rastreabilidade de origem) + produção real.

Ponto de atenção deliberado -- **historico_noshow é point-in-time**: ao
contrário de `db.repositories.contar_no_shows_anteriores` (que conta TODOS
os `no_show` de um paciente até hoje, correto para decidir o próximo
cadastro), cada linha exportada aqui só pode contar no-shows ANTERIORES à
própria `data_hora_agendada` -- reusar `contar_no_shows_anteriores` vazaria
informação do futuro para dentro de um dataset histórico de treino.

Nunca inclui `telefone` (PII): `id_paciente` é `pacientes.id_paciente_externo`
(hash sha256 do CPF, já gerado no cadastro, Passo 5), nunca o CPF em si --
guardado automaticamente por
`tests/test_coerencia_repo.py::test_export_treino_sem_coluna_proibida_de_pii`.
"""
import pandas as pd

from config_projeto import caminho_de_env, para_horario_da_clinica
from db import repositories
from features import calcular_idade

CONSULTAS_HISTORICAS_PATH = caminho_de_env(
    "CONSULTAS_HISTORICAS_PATH", "data/consultas-historicas.csv"
)
CONSULTAS_TREINO_PATH = caminho_de_env("CONSULTAS_TREINO_PATH", "data/consultas-treino.csv")

COLUNAS_SAIDA = [
    "id_consulta",
    "id_paciente",
    "idade",
    "sexo",
    "especialidade",
    "distancia_km",
    "dias_entre_agendamento_consulta",
    "historico_noshow",
    "no_show",
    "data_hora_agendada",
    # Se o paciente recebeu lembrete antes da consulta (revisão do Passo 10):
    # o SMS é uma intervenção, e sem este registro o re-treino aprenderia que
    # o perfil de alto risco "comparece" justamente porque foi lembrado
    # (feedback loop). Não é feature -- `preprocess.py` não o seleciona --, é o
    # dado que permite tratar o efeito depois. Vazio na semente (desconhecido).
    "lembrete_enviado",
]


def _historico_noshow_point_in_time(df: pd.DataFrame) -> pd.Series:
    """Para cada linha, conta quantos agendamentos ANTERIORES (por
    `data_hora_agendada`) do MESMO paciente têm `status='no_show'` -- nunca
    os de depois, que ainda não tinham acontecido no momento daquela
    consulta. `df` já contém só agendamentos com desfecho conhecido
    (concluido/no_show), então todo `no_show` relevante já está nele -- não
    é preciso uma segunda consulta ao banco."""
    ordenado = df.sort_values("data_hora_agendada", kind="stable")
    eh_no_show = (ordenado["status"] == "no_show").astype(int)
    # cumsum inclui a própria linha; subtrair eh_no_show da linha exclui-a.
    contagem = eh_no_show.groupby(ordenado["id_paciente"]).cumsum() - eh_no_show
    return contagem.reindex(df.index)


def montar_dataset_producao(agendamentos: list[dict] | None = None) -> pd.DataFrame:
    """Monta o recorte de produção (só os desfechos reais, sem a semente) --
    schema igual a `data/consultas-historicas.csv`, sem `telefone`.

    `agendamentos` é injetável para os testes: mesma forma que
    `db.repositories.buscar_agendamentos_com_desfecho()` devolve, sem
    depender de Supabase de verdade (mesma filosofia de
    `tests/test_job_inferencia.py`)."""
    if agendamentos is None:
        agendamentos = repositories.buscar_agendamentos_com_desfecho()

    if not agendamentos:
        return pd.DataFrame(columns=COLUNAS_SAIDA)

    linhas = []
    for a in agendamentos:
        paciente = a["pacientes"]
        linhas.append(
            {
                "id_consulta": a["id"],
                "id_paciente": paciente["id_paciente_externo"],
                "sexo": paciente["sexo"],
                "data_nascimento": paciente["data_nascimento"],
                "especialidade": a["especialidade"],
                "distancia_km": a["distancia_km"],
                "dias_entre_agendamento_consulta": a["dias_entre_agendamento_consulta"],
                "status": a["status"],
                "no_show": int(a["status"] == "no_show"),
                # Hora da CLÍNICA, não a UTC que o Postgres devolve: a semente
                # guarda hora local, e misturar as duas ensinaria ao modelo
                # horários (11h-21h) que a clínica não atende.
                "data_hora_agendada": pd.Timestamp(
                    para_horario_da_clinica(a["data_hora_agendada"])
                ),
                "lembrete_enviado": int(bool(a.get("lembrete_enviado"))),
            }
        )

    df = pd.DataFrame(linhas)
    df["idade"] = [
        calcular_idade(pd.Timestamp(nascimento).date(), consulta.date())
        for nascimento, consulta in zip(
            df["data_nascimento"], df["data_hora_agendada"], strict=True
        )
    ]
    df["historico_noshow"] = _historico_noshow_point_in_time(df)
    df["data_hora_agendada"] = df["data_hora_agendada"].dt.strftime("%Y-%m-%d %H:%M:%S")

    return df[COLUNAS_SAIDA]


def montar_dataset_treino(
    caminho_semente: str | None = None, agendamentos: list[dict] | None = None
) -> pd.DataFrame:
    """Semente (intocada, herdada da Camila) + produção real (Passo 9.0).
    Enquanto o volume de produção for pequeno, o resultado continua dominado
    pela semente sintética -- esperado, não um bug (ver PLANO-IMPLEMENTACAO,
    Passo 9.0)."""
    semente = pd.read_csv(caminho_semente or CONSULTAS_HISTORICAS_PATH)
    producao = montar_dataset_producao(agendamentos)
    if producao.empty:
        # Enquanto não há desfecho real registrado, o dataset é a semente,
        # ponto -- concatenar com um DataFrame vazio (dtype object por
        # default) mudaria os dtypes das colunas numéricas da semente à toa.
        return semente
    return pd.concat([semente, producao], ignore_index=True)


def main(caminho_saida: str | None = None) -> int:
    caminho_saida = caminho_saida or CONSULTAS_TREINO_PATH
    dataset = montar_dataset_treino()
    dataset.to_csv(caminho_saida, index=False)
    print(
        f"[ok] {len(dataset)} linha(s) exportadas para {caminho_saida} "
        f"(semente + produção real)"
    )
    return len(dataset)


if __name__ == "__main__":
    main()
