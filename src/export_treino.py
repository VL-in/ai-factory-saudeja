"""
SaúdeJá — export dos desfechos reais da **base de treino** para o dataset que
alimenta o gate de re-treino mensal.

Pré-requisito que este módulo resolve: `data/consultas-historicas.csv` é
fixo em ~380 linhas e `RANDOM_STATE`/`TEST_SIZE` são fixos em
`src/preprocess.py` -- sem dado fresco, todo re-treino mensal produziria a
mesma métrica e o gate nunca bloquearia nem promoveria. Este script monta
`data/consultas-treino.csv` = semente herdada da Camila (intocada, para
preservar rastreabilidade de origem) + produção real.

**A produção vem só da base de treino** (schema `treino`, ADR-010), nunca do
banco da clínica: lá só existem as consultas de quem consentiu (art. 11, I),
gravadas no agendamento sem nome, telefone, CPF nem data de nascimento, com o
desfecho e o lembrete sincronizados pelo job D-2. Este módulo importa só
`db.repositorio_treino` -- `tests/test_coerencia_repo.py` falha se ele passar
a importar `db.repositories`. O re-treino roda fora do Brasil, e por isso não
pode ver dado de quem não consentiu. `id_paciente` é o pseudônimo HMAC do
titular (`src/pseudonimo.py`) e `id_consulta`, o da consulta; nenhum dos dois
se reverte em CPF sem a chave, que o re-treino não tem.

**Janela de 90 dias** (`config_projeto.janela_treino_dias`, ADR-009): só
consultas com até 90 dias entram no export, e a base de treino não guarda
mais que isso.

**`historico_noshow` é o valor GRAVADO no agendamento**, não recalculado: o
job D-2 prediz com ele, e o dataset de treino carrega exatamente o valor de
serving, que não vaza futuro por construção. **`idade` é a da data da
consulta**, calculada no agendamento -- a data de nascimento não vai para a
base de treino.

**Contrato de features** (`src/contrato_features.py`, 10.3): linha fora da
regra de negócio (idade impossível, fora da grade, especialidade que o
cadastro não oferece) fica **fora** do export, contada e identificada pelo
`id_consulta` -- a mesma quarentena que o job D-2 aplica. Sem isso, o stage
`validate_data` barraria o dataset inteiro por causa de uma linha.

**Completude de rótulo** também é medida aqui, e não no `validate_data`: uma
consulta passada ainda `agendado` só é visível no banco. É alerta, não
bloqueio -- mas, se a clínica registra só as faltas e esquece as presenças, o
dataset fica enviesado para o no-show, e isso precisa aparecer no resumo.

Nunca inclui `telefone` nem outra PII -- guardado automaticamente por
`tests/test_coerencia_repo.py::test_export_treino_sem_coluna_proibida_de_pii`.
"""
import os
from datetime import datetime, time, timedelta, timezone
from typing import Any

import pandas as pd

import contrato_features
from config_projeto import (
    caminho_de_env,
    carregar_params,
    fuso_da_clinica,
    hoje_na_clinica,
    janela_treino_dias,
    para_horario_da_clinica,
)
from db import repositorio_treino

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
    # Se o paciente recebeu lembrete antes da consulta:
    # o SMS é uma intervenção, e sem este registro o re-treino aprenderia que
    # o perfil de alto risco "comparece" justamente porque foi lembrado
    # (feedback loop). Não é feature -- `preprocess.py` não o seleciona --, é o
    # dado que permite tratar o efeito depois. Vazio na semente (desconhecido).
    "lembrete_enviado",
]


def inicio_da_janela(agora: datetime | None = None) -> datetime:
    """Primeiro instante que entra no export: `janela_treino_dias()` atrás."""
    return (agora or datetime.now(timezone.utc)) - timedelta(days=janela_treino_dias())


def montar_dataset_producao(consultas: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    """Monta o recorte de produção (só os desfechos reais, sem a semente) --
    schema igual a `data/consultas-historicas.csv`, sem as linhas fora do
    contrato (ver `montar_dataset_producao_com_quarentena`).

    `consultas` é injetável para os testes: mesma forma que
    `db.repositorio_treino.consultas_para_export()` devolve, sem depender de
    Supabase de verdade (mesma filosofia de `tests/test_job_inferencia.py`)."""
    return montar_dataset_producao_com_quarentena(consultas)[0]


def montar_dataset_producao_com_quarentena(
    consultas: list[dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, list[tuple[Any, contrato_features.ViolacaoDoContrato]]]:
    """(recorte de produção, linhas deixadas de fora pelo contrato)."""
    if consultas is None:
        consultas = repositorio_treino.consultas_para_export(inicio_da_janela())

    linhas = []
    for c in consultas:
        # Hora da CLÍNICA, não a UTC que o Postgres devolve: a semente guarda
        # hora local, e misturar as duas ensinaria ao modelo horários (11h-21h)
        # que a clínica não atende.
        quando = para_horario_da_clinica(c["data_hora_agendada"])
        linhas.append(
            {
                "id_consulta": c["id_consulta"],
                "id_paciente": c["id_titular"],
                "idade": c["idade"],
                "sexo": c["sexo"],
                "especialidade": c["especialidade"],
                "distancia_km": c["distancia_km"],
                "dias_entre_agendamento_consulta": c["dias_entre_agendamento_consulta"],
                # O valor com que o job D-2 predisse -- ver docstring do módulo.
                "historico_noshow": c["historico_noshow"],
                "no_show": int(c["status"] == "no_show"),
                "data_hora_agendada": quando,
                "lembrete_enviado": int(bool(c.get("lembrete_enviado"))),
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
    consultas: list[dict[str, Any]] | None = None,
    producao: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Semente (intocada, herdada da Camila) + produção real.
    Enquanto o volume de produção for pequeno, o resultado continua dominado
    pela semente sintética -- esperado, não um bug: o que o gate mede segue
    sendo ruído até a clínica acumular desfechos reais. `producao` já
    montada dispensa `consultas`."""
    semente = pd.read_csv(caminho_semente or CONSULTAS_HISTORICAS_PATH)
    if producao is None:
        producao = montar_dataset_producao(consultas)
    if producao.empty:
        # Enquanto não há desfecho real registrado, o dataset é a semente,
        # ponto -- concatenar com um DataFrame vazio (dtype object por
        # default) mudaria os dtypes das colunas numéricas da semente à toa.
        return semente
    return pd.concat([semente, producao], ignore_index=True)


def medir_completude_de_rotulo(com_desfecho: int) -> dict[str, Any]:
    """Consultas da janela, de antes de hoje, que ainda estão `agendado` na
    base de treino -- desfecho que a clínica não registrou (ou que o job D-2
    ainda não sincronizou). Não entram no export (sem rótulo), mas a proporção
    diz se o que entrou é representativo."""
    inicio_de_hoje = datetime.combine(hoje_na_clinica(), time.min, tzinfo=fuso_da_clinica())
    pendentes = repositorio_treino.contar_sem_desfecho(inicio_da_janela(), inicio_de_hoje)
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
    consultas = repositorio_treino.consultas_para_export(inicio_da_janela())
    producao, quarentena = montar_dataset_producao_com_quarentena(consultas)
    dataset = montar_dataset_treino(producao=producao)
    dataset.to_csv(caminho_saida, index=False)

    completude = medir_completude_de_rotulo(len(consultas))
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
