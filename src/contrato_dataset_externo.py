"""
SaúdeJá — contrato do dataset externo de desfecho real (Kaggle
`joniarroba/noshowappointments`, CC BY-NC-SA 4.0, rede pública de Vitória-ES,
2016).

O que este módulo garante, e para quem:

- **Para o script de adaptação**, que ainda vai ser escrito: as regras que o dado adaptado precisa
  cumprir antes de ser gravado (o mesmo papel do `validate_data` para o dataset
  de treino). As primitivas de mapeamento que não podem divergir entre quem
  gera e quem confere (antecedência em dias, filtro de população) moram aqui.
- **Para os testes de dados** (`tests/test_dataset_externo_dados.py`), que rodam
  cada `verificar_*` contra os arquivos reais em `data/externo/` quando eles
  existem.

Cada `verificar_*` devolve uma lista de violações (vazia = aprovado), e não
para na primeira, para o relatório mostrar tudo de uma vez. **As mensagens
dizem coluna, regra e contagem, nunca o valor de uma linha** -- a mesma regra
de `contrato_features.ViolacaoDoContrato`: o dado é público e pseudonimizado,
mas continua sendo dado de paciente.

O dataset entra como a origem `externa`, separada de `semente` e `producao`:
desfecho real, mas não do SaúdeJá. As três features que o Kaggle não tem
(`especialidade`, `distancia_km`, horário) são sorteadas da distribuição da
semente, **independentes do desfecho** -- `verificar_imputadas` confere as
duas coisas.
"""

import hashlib
import json
import re
from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

import contrato_features
from validate_data import psi

RAIZ = Path(__file__).resolve().parents[1]
DIR_EXTERNO = RAIZ / "data" / "externo"
CAMINHO_BRUTO = DIR_EXTERNO / "raw" / "medical-appoint-no-shows2016.csv"
CAMINHO_POOL = DIR_EXTERNO / "pool-adaptado.csv"
CAMINHO_RESERVADO = DIR_EXTERNO / "teste-reservado.csv"
CAMINHO_AMOSTRA = DIR_EXTERNO / "consultas-externas.csv"
CAMINHO_MANIFESTO = DIR_EXTERNO / "manifesto.json"
CAMINHO_LICENCA = DIR_EXTERNO / "LICENCA-E-ORIGEM.md"

LICENCA = "CC BY-NC-SA 4.0"
URL_FONTE = "https://www.kaggle.com/datasets/joniarroba/noshowappointments"

# --- bruto ----------------------------------------------------------------
COLUNAS_BRUTO = (
    "PatientId",
    "AppointmentID",
    "Gender",
    "ScheduledDay",
    "AppointmentDay",
    "Age",
    "Neighbourhood",
    "Scholarship",
    "Hipertension",
    "Diabetes",
    "Alcoholism",
    "Handcap",
    "SMS_received",
    "No-show",
)
LINHAS_BRUTO = 110_527
COLUNAS_BINARIAS_BRUTO = (
    "Scholarship",
    "Hipertension",
    "Diabetes",
    "Alcoholism",
    "SMS_received",
)
HANDCAP_MAXIMO = 4
PERIODO_CONSULTAS = (date(2016, 4, 29), date(2016, 6, 8))

# --- adaptado -------------------------------------------------------------
# As colunas do export de produção (`export_treino.COLUNAS_SAIDA`, conferido
# por teste) mais as duas da origem. Repetidas aqui, e não importadas, porque o
# export puxa o cliente do banco -- e quem confere um CSV não precisa dele.
COLUNAS_DO_EXPORT = (
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
    "lembrete_enviado",
)
COLUNA_ORIGEM = "origem"
COLUNA_IMPUTADAS = "colunas_imputadas"
COLUNAS_ADAPTADO = (*COLUNAS_DO_EXPORT, COLUNA_ORIGEM, COLUNA_IMPUTADAS)
ORIGEM_EXTERNA = "externa"
COLUNAS_IMPUTADAS = ("especialidade", "distancia_km", "horario")
VALOR_COLUNAS_IMPUTADAS = ";".join(COLUNAS_IMPUTADAS)
PREFIXO_CONSULTA = "K-"
PADRAO_ID_CONSULTA = re.compile(r"^K-\d+$")
# Pseudônimo do PatientId: sha256 com sal local, 16 hex. Não carrega o id
# original (que é numérico e vinha do sistema da prefeitura).
PADRAO_ID_PACIENTE = re.compile(r"^K[0-9a-f]{16}$")
# Nenhum nome do bruto sobrevive (dado de saúde, quase-identificador, ids
# originais), nem coluna de PII do SaúdeJá.
COLUNAS_PII_PROIBIDAS = ("nome", "telefone", "cpf", "data_nascimento", "email")
MINUTOS_DA_GRADE = (0, 30)

# --- regras estatísticas e de partição -------------------------------------
# alfa baixo porque o pool tem ~70 mil linhas: com independência verdadeira o
# p-valor é uniforme, e 0,001 erra 1 em 1.000 por teste.
ALFA_INDEPENDENCIA = 0.001
# A mesma régua do validate_data, mais apertada: o dado imputado foi sorteado
# da própria semente, então PSI alto quer dizer sorteio errado, não drift.
PSI_MAXIMO_IMPUTADA = 0.1
FAIXA_LINHAS_RESERVADO = (8_000, 12_000)
TOLERANCIA_PREVALENCIA_AMOSTRA = 0.04
TOLERANCIA_PREVALENCIA_RESERVADO = 0.02


def _faixa_de_dominio(nome: str) -> contrato_features.Faixa:
    for campo in contrato_features.CAMPOS_NUMERICOS:
        if campo.nome == nome:
            return campo.dominio
    raise KeyError(nome)


# A população é a que o job D-2 prediz (amanhã a D+2: antecedência >= 1) e a
# que o modelo viu (domínio do contrato): as faixas vêm do contrato, não daqui.
FAIXA_IDADE_POPULACAO = _faixa_de_dominio("idade")
FAIXA_ANTECEDENCIA_POPULACAO = _faixa_de_dominio("dias_entre_agendamento_consulta")


# --------------------------------------------------------------------------
# primitivas compartilhadas com o script de adaptação
# --------------------------------------------------------------------------
def antecedencia_em_dias(bruto: pd.DataFrame) -> pd.Series:
    """Dias entre a DATA do agendamento e a DATA da consulta, como escritas no
    bruto. O sufixo `Z` do Kaggle não é tratado como UTC: converter para o fuso
    de São Paulo moveria agendamentos feitos depois das 21h para o dia
    anterior, e não há como saber se o `Z` é real."""
    consulta = pd.to_datetime(bruto["AppointmentDay"].astype(str).str[:10], format="%Y-%m-%d")
    agendamento = pd.to_datetime(bruto["ScheduledDay"].astype(str).str[:10], format="%Y-%m-%d")
    return (consulta - agendamento).dt.days


def mascara_populacao(bruto: pd.DataFrame) -> pd.Series:
    dias = antecedencia_em_dias(bruto)
    idade = bruto["Age"]
    return dias.between(
        FAIXA_ANTECEDENCIA_POPULACAO.minimo, FAIXA_ANTECEDENCIA_POPULACAO.maximo
    ) & idade.between(FAIXA_IDADE_POPULACAO.minimo, FAIXA_IDADE_POPULACAO.maximo)


def remocoes_por_filtro(bruto: pd.DataFrame) -> dict[str, int]:
    """Quantas linhas do bruto cada filtro de população remove, em ORDEM:
    cada linha conta só no primeiro filtro que a pega, então a soma é
    exatamente o que `mascara_populacao` deixa de fora. A auditoria e o
    manifesto da adaptação usam esta mesma função -- os números batem por
    construção, não por coincidência."""
    dias = antecedencia_em_dias(bruto)
    idade = bruto["Age"]
    ant, faixa_idade = FAIXA_ANTECEDENCIA_POPULACAO, FAIXA_IDADE_POPULACAO
    filtros = (
        ("antecedencia_negativa", dias < 0),
        ("mesmo_dia", dias == 0),
        (f"antecedencia_acima_de_{ant.maximo}", dias > ant.maximo),
        ("idade_invalida", idade < faixa_idade.minimo),
        (f"idade_acima_de_{faixa_idade.maximo}", idade > faixa_idade.maximo),
    )
    restante = pd.Series(True, index=bruto.index)
    contagens = {}
    for nome, mascara in filtros:
        removidas = restante & mascara
        contagens[nome] = int(removidas.sum())
        restante &= ~removidas
    return contagens


def pool_de_simulacao(pool: pd.DataFrame, reservado: pd.DataFrame) -> pd.DataFrame:
    """O pool sem nenhum paciente do teste reservado: é daqui que o teste de
    sinal e a simulação tiram as linhas de treino. O reservado só é tocado na
    avaliação (risco R12 do ADR-014)."""
    pacientes_reservados = set(reservado["id_paciente"])
    return pool[~pool["id_paciente"].isin(pacientes_reservados)].reset_index(drop=True)


def amostra_estratificada(df: pd.DataFrame, n: int, random_state: int) -> pd.DataFrame:
    """`n` linhas de `df` com a mesma proporção de `no_show` (arredondada).
    Usada para a amostra de treino oficial e para as amostras extras do teste
    de robustez (risco R11): as duas precisam sortear do mesmo jeito."""
    positivos = round(n * float(df["no_show"].mean()))
    partes = [
        df[df["no_show"] == 1].sample(n=positivos, random_state=random_state),
        df[df["no_show"] == 0].sample(n=n - positivos, random_state=random_state),
    ]
    return pd.concat(partes).sample(frac=1, random_state=random_state).reset_index(drop=True)


def sha256_do_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def carregar_adaptado(caminho: Path) -> pd.DataFrame:
    return pd.read_csv(caminho, dtype={"id_consulta": str, "id_paciente": str})


def carregar_manifesto(caminho: Path) -> dict[str, Any]:
    with open(caminho, encoding="utf-8") as arquivo:
        conteudo: dict[str, Any] = json.load(arquivo)
    return conteudo


def _hora(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["data_hora_agendada"]).dt.hour


def _fora_de(valores: pd.Series, permitidos: Collection[Any]) -> int:
    return int((~valores.isin(list(permitidos))).sum())


# --------------------------------------------------------------------------
# bruto (Fase 1)
# --------------------------------------------------------------------------
def verificar_bruto(df: pd.DataFrame) -> list[str]:
    if tuple(df.columns) != COLUNAS_BRUTO:
        faltando = sorted(set(COLUNAS_BRUTO) - set(df.columns))
        sobrando = sorted(set(df.columns) - set(COLUNAS_BRUTO))
        return [f"bruto: colunas diferentes do esperado (faltando {faltando}, a mais {sobrando})"]

    violacoes = []
    if len(df) != LINHAS_BRUTO:
        violacoes.append(f"bruto: {len(df)} linhas, esperado {LINHAS_BRUTO} (outra versão?)")
    nulos = {c: int(n) for c, n in df.isna().sum().items() if n}
    if nulos:
        violacoes.append(f"bruto: valores nulos por coluna {nulos}")
    if (dup := int(df["AppointmentID"].duplicated().sum())) > 0:
        violacoes.append(f"bruto: {dup} AppointmentID duplicado(s)")
    if (n := _fora_de(df["No-show"], ("Yes", "No"))) > 0:
        violacoes.append(f"bruto: {n} linha(s) com No-show fora de Yes/No")
    if (n := _fora_de(df["Gender"], contrato_features.SEXOS_VALIDOS)) > 0:
        violacoes.append(f"bruto: {n} linha(s) com Gender fora de F/M")
    for coluna in COLUNAS_BINARIAS_BRUTO:
        if (n := _fora_de(df[coluna], (0, 1))) > 0:
            violacoes.append(f"bruto: {n} linha(s) com {coluna} fora de 0/1")
    if (n := _fora_de(df["Handcap"], range(HANDCAP_MAXIMO + 1))) > 0:
        violacoes.append(f"bruto: {n} linha(s) com Handcap fora de 0..{HANDCAP_MAXIMO}")
    for coluna in ("ScheduledDay", "AppointmentDay"):
        datas = pd.to_datetime(df[coluna].astype(str).str[:10], format="%Y-%m-%d", errors="coerce")
        if (n := int(datas.isna().sum())) > 0:
            violacoes.append(f"bruto: {n} data(s) ilegível(is) em {coluna}")
        if coluna == "AppointmentDay":
            inicio, fim = (pd.Timestamp(d) for d in PERIODO_CONSULTAS)
            if (n := int((~datas.between(inicio, fim)).sum())) > 0:
                violacoes.append(f"bruto: {n} consulta(s) fora de {PERIODO_CONSULTAS}")
    return violacoes


# --------------------------------------------------------------------------
# adaptado: pool, teste reservado e amostra (Fases 2 e 3)
# --------------------------------------------------------------------------
def verificar_adaptado(
    df: pd.DataFrame, semente: pd.DataFrame, especialidades: Collection[str]
) -> list[str]:
    """Schema e regra de negócio de qualquer arquivo no formato adaptado."""
    colunas = tuple(df.columns)
    proibidas = sorted((set(COLUNAS_BRUTO) | set(COLUNAS_PII_PROIBIDAS)) & set(colunas))
    if proibidas:
        return [f"adaptado: colunas proibidas presentes {proibidas}"]
    if colunas != COLUNAS_ADAPTADO:
        return [
            f"adaptado: colunas {list(colunas)} diferentes de {list(COLUNAS_ADAPTADO)} "
            "(nome ou ordem)"
        ]
    if df.empty:
        return ["adaptado: arquivo sem linhas"]

    violacoes = []
    nulos = {c: int(n) for c, n in df.isna().sum().items() if n}
    if nulos:
        violacoes.append(f"adaptado: valores nulos por coluna {nulos}")
        return violacoes

    ids = df["id_consulta"].astype(str)
    if (dup := int(ids.duplicated().sum())) > 0:
        violacoes.append(f"adaptado: {dup} id_consulta duplicado(s)")
    if (n := int((~ids.str.fullmatch(PADRAO_ID_CONSULTA)).sum())) > 0:
        violacoes.append(f"adaptado: {n} id_consulta fora do padrão {PADRAO_ID_CONSULTA.pattern}")
    pacientes = df["id_paciente"].astype(str)
    if (n := int((~pacientes.str.fullmatch(PADRAO_ID_PACIENTE)).sum())) > 0:
        violacoes.append(
            f"adaptado: {n} id_paciente fora do padrão {PADRAO_ID_PACIENTE.pattern} "
            "(pseudônimo com sal, não o PatientId)"
        )
    if (n := _fora_de(df[COLUNA_ORIGEM], (ORIGEM_EXTERNA,))) > 0:
        violacoes.append(f"adaptado: {n} linha(s) com origem diferente de '{ORIGEM_EXTERNA}'")
    if (n := _fora_de(df[COLUNA_IMPUTADAS], (VALOR_COLUNAS_IMPUTADAS,))) > 0:
        violacoes.append(
            f"adaptado: {n} linha(s) com colunas_imputadas diferente de '{VALOR_COLUNAS_IMPUTADAS}'"
        )
    for coluna in ("no_show", "lembrete_enviado"):
        if (n := _fora_de(df[coluna], (0, 1))) > 0:
            violacoes.append(f"adaptado: {n} linha(s) com {coluna} fora de 0/1")

    # Regra de negócio do produto: a mesma quarentena do export e do job D-2
    # (faixas, sexo, especialidade do cadastro, grade da clínica).
    quarentena = contrato_features.violacoes_do_dataframe(df, especialidades)
    if quarentena:
        violacoes.append(
            "adaptado: linhas fora do contrato de features: "
            + contrato_features.resumir_violacoes(quarentena)
        )

    # População: domínio do treino (mais estreito que a regra de negócio).
    for coluna, faixa in (
        ("idade", FAIXA_IDADE_POPULACAO),
        ("dias_entre_agendamento_consulta", FAIXA_ANTECEDENCIA_POPULACAO),
    ):
        fora = int((~df[coluna].between(faixa.minimo, faixa.maximo)).sum())
        if fora:
            violacoes.append(
                f"adaptado: {fora} linha(s) com {coluna} fora da população ({faixa.descrever()})"
            )

    quando = pd.to_datetime(df["data_hora_agendada"], errors="coerce")
    if quando.isna().any():
        violacoes.append("adaptado: data_hora_agendada ilegível")
    else:
        inicio, fim = (pd.Timestamp(d) for d in PERIODO_CONSULTAS)
        if (n := int((~quando.dt.normalize().between(inicio, fim)).sum())) > 0:
            violacoes.append(
                f"adaptado: {n} consulta(s) fora do período do bruto {PERIODO_CONSULTAS} "
                "(a data é a real, só a hora é imputada)"
            )
        if (n := _fora_de(quando.dt.minute, MINUTOS_DA_GRADE)) > 0:
            violacoes.append(f"adaptado: {n} horário(s) fora dos slots de 30 min")

    # Sorteio da semente: todo valor imputado precisa existir na semente.
    distancias_semente = set(np.round(semente["distancia_km"].astype(float), 2))
    if (n := _fora_de(np.round(df["distancia_km"].astype(float), 2), distancias_semente)) > 0:
        violacoes.append(
            f"adaptado: {n} distancia_km que não existe na semente "
            "(o sorteio deve reamostrar os valores da semente)"
        )
    return violacoes


def verificar_imputadas(
    df: pd.DataFrame,
    semente: pd.DataFrame,
    alfa: float = ALFA_INDEPENDENCIA,
    psi_maximo: float = PSI_MAXIMO_IMPUTADA,
) -> list[str]:
    """As features imputadas não podem carregar sinal (independência do
    desfecho) nem denunciar a origem (mesma distribuição da semente)."""
    violacoes = []
    if df["no_show"].nunique() < 2:
        return ["imputadas: o arquivo precisa ter as duas classes para testar independência"]

    hora = _hora(df)
    for nome, valores in (("especialidade", df["especialidade"]), ("horario", hora)):
        tabela = pd.crosstab(valores, df["no_show"])
        if tabela.shape[0] < 2:
            violacoes.append(f"imputadas: {nome} com uma categoria só")
            continue
        p = float(stats.chi2_contingency(tabela).pvalue)
        if p < alfa:
            violacoes.append(
                f"imputadas: {nome} associado ao desfecho (qui-quadrado p={p:.2g} < {alfa}); "
                "o sorteio não pode depender de no_show"
            )
    distancia = df["distancia_km"].astype(float)
    p = float(
        stats.mannwhitneyu(distancia[df["no_show"] == 1], distancia[df["no_show"] == 0]).pvalue
    )
    if p < alfa:
        violacoes.append(
            f"imputadas: distancia_km associada ao desfecho (Mann-Whitney p={p:.2g} < {alfa})"
        )

    referencia_hora = _hora(semente)
    for nome, ref, atual, categorica in (
        ("especialidade", semente["especialidade"], df["especialidade"], True),
        ("distancia_km", semente["distancia_km"], df["distancia_km"], False),
        ("horario", referencia_hora, hora, True),
    ):
        valor = psi(ref.reset_index(drop=True), atual.reset_index(drop=True), categorica)
        if valor > psi_maximo:
            violacoes.append(
                f"imputadas: PSI de {nome} contra a semente = {valor:.3f} > {psi_maximo} "
                "(a distribuição denuncia a origem da linha)"
            )
    return violacoes


def verificar_fidelidade_ao_bruto(
    pool: pd.DataFrame, bruto: pd.DataFrame, removidas_pelo_contrato: int = 0
) -> list[str]:
    """Recalcula a população direto do bruto e confere que o pool tem
    exatamente as mesmas consultas nas colunas que NÃO são imputadas: mesma
    distribuição conjunta de (idade, sexo, antecedência, dia da semana,
    desfecho, SMS). Pega filtro errado, troca de rótulo (`No-show` "Yes" é
    falta), antecedência calculada com fuso e SMS desalinhado, sem precisar do
    sal do pseudônimo."""
    mascara = mascara_populacao(bruto)
    esperado = int(mascara.sum()) - removidas_pelo_contrato
    violacoes = []
    if len(pool) != esperado:
        violacoes.append(f"fidelidade: pool com {len(pool)} linhas, o bruto filtrado dá {esperado}")
    populacao = bruto[mascara]
    pacientes_bruto = int(populacao["PatientId"].nunique())
    pacientes_pool = int(pool["id_paciente"].nunique())
    if removidas_pelo_contrato == 0 and pacientes_pool != pacientes_bruto:
        violacoes.append(
            f"fidelidade: {pacientes_pool} pacientes no pool, {pacientes_bruto} no bruto filtrado "
            "(pseudônimo não é 1 para 1 com o PatientId)"
        )
    if removidas_pelo_contrato:
        return violacoes

    do_bruto = pd.DataFrame(
        {
            "idade": populacao["Age"].to_numpy(),
            "sexo": populacao["Gender"].to_numpy(),
            "dias": antecedencia_em_dias(populacao).to_numpy(),
            "dia_semana": pd.to_datetime(
                populacao["AppointmentDay"].astype(str).str[:10]
            ).dt.dayofweek.to_numpy(),
            "no_show": (populacao["No-show"] == "Yes").astype(int).to_numpy(),
            "lembrete": populacao["SMS_received"].to_numpy(),
        }
    )
    do_pool = pd.DataFrame(
        {
            "idade": pool["idade"].to_numpy(),
            "sexo": pool["sexo"].to_numpy(),
            "dias": pool["dias_entre_agendamento_consulta"].to_numpy(),
            "dia_semana": pd.to_datetime(pool["data_hora_agendada"]).dt.dayofweek.to_numpy(),
            "no_show": pool["no_show"].to_numpy(),
            "lembrete": pool["lembrete_enviado"].to_numpy(),
        }
    )
    contagem_bruto = do_bruto.value_counts()
    contagem_pool = do_pool.value_counts()
    alinhado = pd.concat([contagem_bruto, contagem_pool], axis=1).fillna(0)
    diferentes = int((alinhado.iloc[:, 0] != alinhado.iloc[:, 1]).sum())
    if diferentes:
        violacoes.append(
            f"fidelidade: {diferentes} combinação(ões) de (idade, sexo, antecedência, dia da "
            "semana, no_show, lembrete) com contagem diferente entre pool e bruto"
        )
    return violacoes


def verificar_particao(
    pool: pd.DataFrame,
    reservado: pd.DataFrame,
    amostra: pd.DataFrame,
    n_amostra: int,
    faixa_reservado: tuple[int, int] = FAIXA_LINHAS_RESERVADO,
    tolerancia_amostra: float = TOLERANCIA_PREVALENCIA_AMOSTRA,
    tolerancia_reservado: float = TOLERANCIA_PREVALENCIA_RESERVADO,
) -> list[str]:
    """Teste reservado por paciente, amostra de treino de pacientes disjuntos,
    e as duas como recortes fiéis do pool (mesmas linhas, mesmos valores)."""
    violacoes = []
    pool_idx = pool.set_index("id_consulta")
    for nome, parte in (("reservado", reservado), ("amostra", amostra)):
        fora = int((~parte["id_consulta"].isin(pool_idx.index)).sum())
        if fora:
            violacoes.append(f"partição: {fora} linha(s) do {nome} que não estão no pool")
            continue
        recorte = pool_idx.loc[parte["id_consulta"]].reset_index()[list(parte.columns)]
        divergentes = int(
            (recorte.reset_index(drop=True) != parte.reset_index(drop=True)).any(axis=1).sum()
        )
        if divergentes:
            violacoes.append(
                f"partição: {divergentes} linha(s) do {nome} com valores diferentes do pool "
                "(re-imputação depois de particionar?)"
            )

    comuns = set(reservado["id_paciente"]) & set(amostra["id_paciente"])
    if comuns:
        violacoes.append(
            f"partição: {len(comuns)} paciente(s) no teste reservado e na amostra de treino"
        )
    pacientes_reservado = set(reservado["id_paciente"])
    do_pool = pool[pool["id_paciente"].isin(pacientes_reservado)]
    if len(do_pool) != len(reservado):
        violacoes.append(
            f"partição: os pacientes do reservado têm {len(do_pool)} consultas no pool e o "
            f"reservado tem {len(reservado)} (o corte precisa ser por paciente)"
        )

    if len(amostra) != n_amostra:
        violacoes.append(f"partição: amostra com {len(amostra)} linhas, params pede {n_amostra}")
    minimo, maximo = faixa_reservado
    if not minimo <= len(reservado) <= maximo:
        violacoes.append(
            f"partição: reservado com {len(reservado)} linhas, fora de [{minimo}, {maximo}]"
        )
    prevalencia = float(pool["no_show"].mean())
    for nome, parte, tolerancia in (
        ("amostra", amostra, tolerancia_amostra),
        ("reservado", reservado, tolerancia_reservado),
    ):
        taxa = float(parte["no_show"].mean())
        if abs(taxa - prevalencia) > tolerancia:
            violacoes.append(
                f"partição: taxa de falta do {nome} {taxa:.3f} longe da do pool "
                f"{prevalencia:.3f} (tolerância {tolerancia})"
            )
    return violacoes


# --------------------------------------------------------------------------
# manifesto, licença, params e o dataset oficial
# --------------------------------------------------------------------------
CHAVES_MANIFESTO = (
    "versao_script",
    "gerado_em",
    "licenca",
    "fonte",
    "random_state",
    "bruto",
    "filtros",
    "pool",
    "reservado",
    "amostra",
)


def verificar_manifesto(
    manifesto: Mapping[str, Any],
    sha256_bruto: str | None = None,
    pool: pd.DataFrame | None = None,
    reservado: pd.DataFrame | None = None,
    amostra: pd.DataFrame | None = None,
) -> list[str]:
    """O manifesto descreve o que foi gerado, e os números batem com os
    arquivos. `filtros` são remoções SEQUENCIAIS (cada linha conta uma vez),
    então bruto - filtros = pool."""
    faltando = [c for c in CHAVES_MANIFESTO if c not in manifesto]
    if faltando:
        return [f"manifesto: chaves ausentes {faltando}"]

    violacoes = []
    if manifesto["licenca"] != LICENCA:
        violacoes.append(f"manifesto: licença deve ser '{LICENCA}'")
    if manifesto["fonte"] != URL_FONTE:
        violacoes.append(f"manifesto: fonte deve ser {URL_FONTE}")
    if not isinstance(manifesto["random_state"], int):
        violacoes.append("manifesto: random_state precisa ser inteiro")
    bruto = manifesto["bruto"]
    if bruto.get("linhas") != LINHAS_BRUTO:
        violacoes.append(f"manifesto: bruto.linhas deve ser {LINHAS_BRUTO}")
    if sha256_bruto is not None and bruto.get("sha256") != sha256_bruto:
        violacoes.append("manifesto: sha256 do bruto não bate com o arquivo em disco")
    removidas = sum(int(v) for v in manifesto["filtros"].values())
    if bruto.get("linhas", 0) - removidas != manifesto["pool"].get("linhas"):
        violacoes.append("manifesto: bruto.linhas - soma(filtros) != pool.linhas")

    for nome, df, campos in (
        ("pool", pool, ("linhas", "positivos", "pacientes")),
        ("reservado", reservado, ("linhas", "pacientes")),
        ("amostra", amostra, ("linhas", "positivos")),
    ):
        if df is None:
            continue
        real = {
            "linhas": len(df),
            "positivos": int(df["no_show"].sum()),
            "pacientes": int(df["id_paciente"].nunique()),
        }
        for campo in campos:
            if manifesto[nome].get(campo) != real[campo]:
                violacoes.append(f"manifesto: {nome}.{campo} não bate com o arquivo")
    return violacoes


def verificar_licenca(texto: str) -> list[str]:
    exigidos = (LICENCA, URL_FONTE, "2016", "Vitória", "não são pacientes do SaúdeJá")
    return [
        f"licença: LICENCA-E-ORIGEM.md não menciona '{trecho}'"
        for trecho in exigidos
        if trecho not in texto
    ]


def verificar_params_externa(params: Mapping[str, Any]) -> list[str]:
    """Bloco `dados.externa` do params.yaml (Fase 0). Desligado por default:
    o dado externo não entra no treino oficial até o ADR ser aceito e a
    Fase 7 ligar o parâmetro -- e então esta regra muda junto."""
    externa = params.get("dados", {}).get("externa")
    if not isinstance(externa, Mapping):
        return ["params: bloco dados.externa ausente"]
    violacoes = []
    if externa.get("habilitada") is not False:
        violacoes.append("params: dados.externa.habilitada deve ser false até a Fase 7")
    n = externa.get("n_amostra")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        violacoes.append("params: dados.externa.n_amostra deve ser inteiro positivo")
    rs = externa.get("random_state")
    if not isinstance(rs, int) or isinstance(rs, bool):
        violacoes.append("params: dados.externa.random_state deve ser inteiro")
    return violacoes


def verificar_treino_sem_externa(treino: pd.DataFrame, habilitada: bool) -> list[str]:
    """Enquanto `dados.externa.habilitada` for false, nenhuma linha externa
    pode estar no dataset oficial (`data/consultas-treino.csv`) -- nem colada
    à mão, nem por um export que a juntou sem o parâmetro."""
    if habilitada:
        return []
    violacoes = []
    externas = int(treino["id_consulta"].astype(str).str.startswith(PREFIXO_CONSULTA).sum())
    if externas:
        violacoes.append(
            f"treino: {externas} linha(s) com id externo ('{PREFIXO_CONSULTA}') e "
            "dados.externa.habilitada false"
        )
    if COLUNA_ORIGEM in treino.columns:
        n = int((treino[COLUNA_ORIGEM] == ORIGEM_EXTERNA).sum())
        if n:
            violacoes.append(f"treino: {n} linha(s) com origem externa e o parâmetro desligado")
    return violacoes
