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

**`historico_noshow` é o valor GRAVADO no cadastro** (Passo 10.3), não
recalculado. Até aqui ele era recontado ponto a ponto, com os no-shows
anteriores à própria consulta -- sem vazar futuro, mas também sem bater com o
que o modelo viu: o job D-2 prediz com o valor gravado quando o paciente
agendou, e um no-show acontecido entre o agendamento e a consulta entrava no
recálculo e não na predição (skew treino-serving). O dataset de treino passa a
carregar exatamente o valor de serving, que não vaza futuro por construção:
foi calculado no momento do cadastro.

**Contrato de features** (`src/contrato_features.py`, 10.3): linha fora da
regra de negócio (idade impossível, fora da grade, especialidade que o
cadastro não oferece) fica **fora** do export, contada e identificada pelo
`id` do agendamento -- a mesma quarentena que o job D-2 aplica. Sem isso, o
stage `validate_data` barraria o dataset inteiro por causa de uma linha.

**Completude de rótulo** também é medida aqui, e não no `validate_data`: uma
consulta passada ainda `agendado` só é visível no banco. É alerta, não
bloqueio -- mas, se a clínica registra só as faltas e esquece as presenças, o
dataset fica enviesado para o no-show, e isso precisa aparecer no resumo.

Nunca inclui `telefone` (PII): `id_paciente` é `pacientes.id_paciente_externo`
(hash sha256 do CPF, já gerado no cadastro, Passo 5), nunca o CPF em si --
guardado automaticamente por
`tests/test_coerencia_repo.py::test_export_treino_sem_coluna_proibida_de_pii`.
"""
import os
from typing import Any

import pandas as pd

import contrato_features
from config_projeto import (
    caminho_de_env,
    carregar_params,
    hoje_na_clinica,
    para_horario_da_clinica,
)
from db import repositories
from features import calcular_idade

PARAMS = carregar_params()

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


def montar_dataset_producao(agendamentos: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    """Monta o recorte de produção (só os desfechos reais, sem a semente) --
    schema igual a `data/consultas-historicas.csv`, sem `telefone`, sem as
    linhas fora do contrato (ver `montar_dataset_producao_com_quarentena`).

    `agendamentos` é injetável para os testes: mesma forma que
    `db.repositories.buscar_agendamentos_com_desfecho()` devolve, sem
    depender de Supabase de verdade (mesma filosofia de
    `tests/test_job_inferencia.py`)."""
    return montar_dataset_producao_com_quarentena(agendamentos)[0]


def montar_dataset_producao_com_quarentena(
    agendamentos: list[dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, list[tuple[Any, contrato_features.ViolacaoDoContrato]]]:
    """(recorte de produção, linhas deixadas de fora pelo contrato)."""
    if agendamentos is None:
        agendamentos = repositories.buscar_agendamentos_com_desfecho()

    linhas = []
    for a in agendamentos:
        paciente = a["pacientes"]
        # Hora da CLÍNICA, não a UTC que o Postgres devolve: a semente guarda
        # hora local, e misturar as duas ensinaria ao modelo horários (11h-21h)
        # que a clínica não atende.
        quando = para_horario_da_clinica(a["data_hora_agendada"])
        linhas.append(
            {
                "id_consulta": a["id"],
                "id_paciente": paciente["id_paciente_externo"],
                "idade": calcular_idade(
                    pd.Timestamp(paciente["data_nascimento"]).date(), quando.date()
                ),
                "sexo": paciente["sexo"],
                "especialidade": a["especialidade"],
                "distancia_km": a["distancia_km"],
                "dias_entre_agendamento_consulta": a["dias_entre_agendamento_consulta"],
                # O valor com que o job D-2 predisse -- ver docstring do módulo.
                "historico_noshow": a["historico_noshow"],
                "no_show": int(a["status"] == "no_show"),
                "data_hora_agendada": quando,
                "lembrete_enviado": int(bool(a.get("lembrete_enviado"))),
            }
        )

    if not linhas:
        return pd.DataFrame(columns=COLUNAS_SAIDA), []

    df = pd.DataFrame(linhas)
    quarentena = contrato_features.violacoes_do_dataframe(
        df, PARAMS["cadastro"]["especialidades"]
    )
    if quarentena:
        fora = {identificador for identificador, _ in quarentena}
        df = df[~df["id_consulta"].isin(fora)]
    df = df.assign(
        data_hora_agendada=[q.strftime("%Y-%m-%d %H:%M:%S") for q in df["data_hora_agendada"]]
    )
    return df[COLUNAS_SAIDA].reset_index(drop=True), quarentena


def montar_dataset_treino(
    caminho_semente: str | None = None,
    agendamentos: list[dict[str, Any]] | None = None,
    producao: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Semente (intocada, herdada da Camila) + produção real (Passo 9.0).
    Enquanto o volume de produção for pequeno, o resultado continua dominado
    pela semente sintética -- esperado, não um bug (ver PLANO-IMPLEMENTACAO,
    Passo 9.0). `producao` já montada dispensa `agendamentos`."""
    semente = pd.read_csv(caminho_semente or CONSULTAS_HISTORICAS_PATH)
    if producao is None:
        producao = montar_dataset_producao(agendamentos)
    if producao.empty:
        # Enquanto não há desfecho real registrado, o dataset é a semente,
        # ponto -- concatenar com um DataFrame vazio (dtype object por
        # default) mudaria os dtypes das colunas numéricas da semente à toa.
        return semente
    return pd.concat([semente, producao], ignore_index=True)


def medir_completude_de_rotulo(com_desfecho: int) -> dict[str, Any]:
    """Consultas de antes de hoje que ainda estão `agendado` -- desfecho que a
    clínica não registrou. Não entram no export (sem rótulo), mas a proporção
    diz se o que entrou é representativo."""
    pendentes = repositories.contar_consultas_sem_desfecho(hoje_na_clinica())
    total = pendentes + com_desfecho
    return {
        "consultas_passadas_sem_desfecho": pendentes,
        "proporcao_sem_desfecho": round(pendentes / total, 4) if total else 0.0,
    }


def _publicar(texto: str) -> None:
    print(texto)
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(texto + "\n")


def main(caminho_saida: str | None = None) -> int:
    caminho_saida = caminho_saida or CONSULTAS_TREINO_PATH
    agendamentos = repositories.buscar_agendamentos_com_desfecho()
    producao, quarentena = montar_dataset_producao_com_quarentena(agendamentos)
    dataset = montar_dataset_treino(producao=producao)
    dataset.to_csv(caminho_saida, index=False)

    completude = medir_completude_de_rotulo(len(agendamentos))
    linhas = [
        "### Export do dataset de re-treino",
        "",
        f"- {len(dataset)} linha(s) em {caminho_saida} (semente + {len(producao)} de produção)",
    ]
    if quarentena:
        linhas.append(
            "- **fora do contrato, deixadas de fora:** "
            + contrato_features.resumir_violacoes(quarentena)
        )
    if completude["consultas_passadas_sem_desfecho"]:
        linhas.append(
            f"- **alerta de completude:** {completude['consultas_passadas_sem_desfecho']} "
            f"consulta(s) passada(s) sem desfecho registrado "
            f"({completude['proporcao_sem_desfecho']:.0%} das consultas passadas) -- se a "
            "clínica registra só as faltas, o dataset fica enviesado para o no-show"
        )
    _publicar("\n".join(linhas))
    return len(dataset)


if __name__ == "__main__":
    main()
