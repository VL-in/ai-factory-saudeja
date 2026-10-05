"""
SaúdeJá — canário do modelo com rollback automático (ADR-008).

Sem banco: o repositório é trocado por um falso em memória e o modelo é o
`data/model.pkl` real (como em `tests/test_job_d2_unitario.py`). O que se
trava aqui:

- **divisão da fila**: estável por paciente, proporcional à fração e
  renovada a cada canário;
- **decisão**: violação de guardrail reverte; promover exige
  não-inferioridade com significância, amostra e dias; sem evidência até o
  prazo, reverte -- e amostra pequena nunca decide nada;
- **ciclo de vida em git**: iniciar não toca no campeão; promover restaura o
  lock do treino do canário e se recusa se `main` mudou no meio; reverter
  deixa o campeão byte a byte intacto;
- **job**: canário com qualquer problema vira falha do run e fila 100%
  campeão -- nunca fila sem lembrete; com canário saudável, a fração dele é
  predita, gravada e disparada com o modelo e o threshold dele.
"""
import json
import shutil
from datetime import datetime, timedelta

import pytest

import canario
import inference
from config_projeto import fuso_da_clinica
from jobs import inferencia_diaria as job

MODEL_PATH = "data/model.pkl"
AGORA = datetime(2026, 10, 20, 9, 0, tzinfo=fuso_da_clinica())

CFG = {
    "fracao": 0.2,
    "dias_minimos": 7,
    "dias_maximos": 21,
    "predicoes_minimas": 300,
    "desfechos_minimos": 300,
    "amostra_minima_por_braco": 30,
    "z_critico": 1.645,
    "margem": {"taxa_disparo": 0.05, "falta_nao_avisada": 0.05, "quarentena": 0.02},
}
PARAMS = {**inference.PARAMS, "canario": {"habilitado": True, **CFG}}


# --------------------------------------------------------------------------
# divisão da fila
# --------------------------------------------------------------------------
def test_braco_e_estavel_para_o_mesmo_paciente_e_canario():
    assert canario.braco("hash-1", 0.2, "v1") == canario.braco("hash-1", 0.2, "v1")


def test_braco_respeita_a_fracao():
    sorteados = sum(canario.braco(f"paciente-{i}", 0.2, "v1") for i in range(20_000))
    assert 0.18 < sorteados / 20_000 < 0.22


def test_cada_canario_sorteia_outros_pacientes():
    """A semente é a model_version: o canário seguinte não cai sempre nos
    mesmos pacientes."""
    a = {i for i in range(5_000) if canario.braco(f"p{i}", 0.2, "v1")}
    b = {i for i in range(5_000) if canario.braco(f"p{i}", 0.2, "v2")}
    assert len(a & b) / len(a) < 0.35


# --------------------------------------------------------------------------
# configuração
# --------------------------------------------------------------------------
def test_config_sem_chave_e_erro_em_vez_de_default():
    with pytest.raises(canario.ErroCanario, match="dias_maximos"):
        canario.ler_config({"canario": {k: v for k, v in CFG.items() if k != "dias_maximos"}})


def test_config_recusa_fracao_fora_de_zero_e_um():
    with pytest.raises(canario.ErroCanario, match="fracao"):
        canario.ler_config({"canario": {**CFG, "fracao": 1.0}})


def test_params_do_repositorio_sao_legiveis():
    assert canario.ler_config(inference.PARAMS)["margem"]["quarentena"] > 0


# --------------------------------------------------------------------------
# decisão (pura)
# --------------------------------------------------------------------------
def test_amostra_pequena_nunca_decide():
    linha = canario.comparar_proporcao("taxa_disparo", 10, 10, 0, 29, 0.05, CFG)
    assert linha["situacao"] == canario.SITUACAO_AMOSTRA_INSUFICIENTE


def test_canario_muito_pior_viola():
    linha = canario.comparar_proporcao("taxa_disparo", 250, 500, 400, 2000, 0.05, CFG)
    assert linha["situacao"] == canario.SITUACAO_VIOLADO


def test_canario_igual_com_amostra_grande_e_nao_inferior():
    linha = canario.comparar_proporcao("taxa_disparo", 600, 2000, 2400, 8000, 0.05, CFG)
    assert linha["situacao"] == canario.SITUACAO_NAO_INFERIOR


def test_canario_igual_com_amostra_media_e_inconclusivo():
    linha = canario.comparar_proporcao("taxa_disparo", 15, 50, 60, 200, 0.05, CFG)
    assert linha["situacao"] == canario.SITUACAO_INCONCLUSIVO


def _est(versao, n, disparos, desfechos, faltas, dias_atras=None):
    primeira = None if dias_atras is None else AGORA - timedelta(days=dias_atras)
    return canario.Estatisticas(
        model_version=versao,
        predicoes=n,
        disparos=disparos,
        desfechos_baixo_risco=desfechos,
        faltas_baixo_risco=faltas,
        primeira_predicao=primeira,
    )


def test_sem_trafego_aguarda():
    decisao = canario.decidir(_est("c", 0, 0, 0, 0), _est("b", 0, 0, 0, 0), AGORA, CFG)
    assert decisao.acao == canario.ACAO_AGUARDAR


def test_disparo_violado_reverte_mesmo_antes_dos_dias_minimos():
    decisao = canario.decidir(
        _est("c", 400, 240, 100, 20, dias_atras=2),
        _est("b", 1600, 480, 800, 160, dias_atras=2),
        AGORA,
        CFG,
    )
    assert decisao.acao == canario.ACAO_REVERTER
    assert "taxa_disparo" in decisao.motivo


def test_falta_nao_avisada_violada_reverte():
    decisao = canario.decidir(
        _est("c", 2000, 600, 1000, 400, dias_atras=10),
        _est("b", 8000, 2400, 4000, 800, dias_atras=10),
        AGORA,
        CFG,
    )
    assert decisao.acao == canario.ACAO_REVERTER
    assert "falta_nao_avisada" in decisao.motivo


def test_nao_inferior_com_dias_e_amostra_promove():
    decisao = canario.decidir(
        _est("c", 2000, 600, 1200, 240, dias_atras=8),
        _est("b", 8000, 2400, 4800, 960, dias_atras=8),
        AGORA,
        CFG,
    )
    assert decisao.acao == canario.ACAO_PROMOVER


def test_nao_inferior_antes_dos_dias_minimos_aguarda():
    decisao = canario.decidir(
        _est("c", 2000, 600, 1200, 240, dias_atras=3),
        _est("b", 8000, 2400, 4800, 960, dias_atras=3),
        AGORA,
        CFG,
    )
    assert decisao.acao == canario.ACAO_AGUARDAR


def test_sem_evidencia_ate_o_prazo_reverte():
    """Volume de uma clínica só: nada é violado, mas nada é provado. O
    conservador é ficar com o campeão."""
    decisao = canario.decidir(
        _est("c", 120, 36, 60, 12, dias_atras=21),
        _est("b", 480, 144, 240, 48, dias_atras=21),
        AGORA,
        CFG,
    )
    assert decisao.acao == canario.ACAO_REVERTER
    assert "sem evidência" in decisao.motivo


# --------------------------------------------------------------------------
# ciclo de vida dos arquivos
# --------------------------------------------------------------------------
@pytest.fixture
def repo_falso(tmp_path, monkeypatch):
    """`data/`, `dvc.lock` e o `.dvc` do dataset dentro de tmp_path."""
    champion = tmp_path / "champion_metrics.json"
    champion.write_text(
        json.dumps({"model_version": "campeao00001", "decision_threshold": 0.6}),
        encoding="utf-8",
    )
    lock = tmp_path / "dvc.lock"
    lock.write_text("stages: {campeao: 1}\n", encoding="utf-8")
    dataset = tmp_path / "consultas-treino.csv.dvc"
    dataset.write_text("outs:\n- md5: campeao\n", encoding="utf-8")
    for nome, valor in {
        "CANARIO_PATH": tmp_path / "canario.json",
        "CANARIO_DIR": tmp_path / "canario",
        "HISTORICO_PATH": tmp_path / "canario_historico.json",
        "CHAMPION_PATH": champion,
        "DVC_LOCK_PATH": lock,
        "DATASET_DVC_PATH": dataset,
    }.items():
        monkeypatch.setattr(canario, nome, str(valor))
    return tmp_path


def _iniciar(tmp_path, modelo=MODEL_PATH, campeao_base="campeao00001"):
    lock_do_treino = tmp_path / "lock-do-treino"
    lock_do_treino.write_text("stages: {canario: 1}\n", encoding="utf-8")
    dataset_do_treino = tmp_path / "dataset-do-treino.dvc"
    dataset_do_treino.write_text("outs:\n- md5: canario\n", encoding="utf-8")
    return canario.iniciar(
        modelo,
        {
            "mlflow_run_id": "run-canario",
            "motivo": "nenhuma métrica regrediu",
            "decision_threshold": 0.6,
            "metricas": {"recall_1": 0.5, "f1_1": 0.45, "roc_auc": 0.66},
            "dataset": {"md5_dvc": "canario"},
        },
        campeao_base=campeao_base,
        dvc_lock_path=lock_do_treino,
        dataset_dvc_path=dataset_do_treino,
        dvc_lock_base_sha256=canario.sha256_de_arquivo(tmp_path / "dvc.lock"),
        params=PARAMS,
    )


def test_iniciar_grava_o_canario_sem_tocar_no_campeao(repo_falso):
    antes = (repo_falso / "champion_metrics.json").read_bytes()

    registro = _iniciar(repo_falso)

    assert registro["model_version"] == inference.calcular_model_version(MODEL_PATH)
    assert registro["fracao"] == CFG["fracao"]
    assert (repo_falso / "champion_metrics.json").read_bytes() == antes
    assert canario.verificar(PARAMS) == registro["model_version"]


def test_iniciar_recusa_canario_identico_ao_campeao(repo_falso):
    versao = inference.calcular_model_version(MODEL_PATH)
    with pytest.raises(canario.ErroCanario, match="byte a byte"):
        _iniciar(repo_falso, campeao_base=versao)
    assert not canario.ativo()


def test_verificar_recusa_modelo_que_nao_e_o_registrado(repo_falso):
    _iniciar(repo_falso)
    (repo_falso / "canario" / canario.MODELO).write_bytes(b"outro modelo")

    with pytest.raises(canario.ErroCanario, match="não é o registrado"):
        canario.verificar(PARAMS)


def test_promover_reescreve_o_campeao_e_restaura_o_lock_do_treino(repo_falso):
    registro = _iniciar(repo_falso)

    novo = canario.promover({"acao": "promover"})

    campeao_gravado = json.loads((repo_falso / "champion_metrics.json").read_text("utf-8"))
    assert campeao_gravado == novo
    assert novo["model_version"] == registro["model_version"]
    assert novo["canario"]["campeao_anterior"] == "campeao00001"
    assert "canario: 1" in (repo_falso / "dvc.lock").read_text("utf-8")
    assert "md5: canario" in (repo_falso / "consultas-treino.csv.dvc").read_text("utf-8")
    assert not canario.ativo()
    assert not (repo_falso / "canario").exists()
    historico = canario.carregar_historico()["canarios"]
    assert [c["decisao"] for c in historico] == ["promovido"]


def test_promover_se_recusa_se_o_lock_de_main_mudou(repo_falso):
    _iniciar(repo_falso)
    (repo_falso / "dvc.lock").write_text("stages: {outra_mudanca: 1}\n", encoding="utf-8")

    with pytest.raises(canario.ErroCanario, match=r"dvc\.lock"):
        canario.promover()
    assert canario.ativo()


def test_promover_se_recusa_se_o_campeao_mudou(repo_falso):
    _iniciar(repo_falso, campeao_base="outro-campeao")

    with pytest.raises(canario.ErroCanario, match="campeão"):
        canario.promover()


def test_reverter_nao_toca_no_campeao_e_entra_na_lista_de_revertidos(repo_falso):
    registro = _iniciar(repo_falso)
    campeao_antes = (repo_falso / "champion_metrics.json").read_bytes()
    lock_antes = (repo_falso / "dvc.lock").read_bytes()

    canario.reverter("taxa_disparo violada")

    assert (repo_falso / "champion_metrics.json").read_bytes() == campeao_antes
    assert (repo_falso / "dvc.lock").read_bytes() == lock_antes
    assert not canario.ativo()
    assert canario.modelos_revertidos() == {registro["model_version"]}


def test_cli_reverter_sem_canario_devolve_1(repo_falso, capsys):
    assert canario.main(["reverter", "--motivo", "teste"]) == 1
    assert capsys.readouterr().out.startswith("::error::")


def test_cli_avaliar_sem_canario_escreve_nenhum_no_output(repo_falso, monkeypatch, tmp_path):
    saida = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(saida))

    assert canario.main(["avaliar"]) == 0
    assert "acao=nenhum" in saida.read_text(encoding="utf-8")


# Smoke dos comandos exatamente como os workflows os chamam (canario.yml e o
# passo "Canario ativo" do ci.yml): as funções estavam testadas, o `main()`
# que as liga às saídas do Actions não.
def test_cli_status_com_e_sem_canario(repo_falso, capsys):
    assert canario.main(["status"]) == 0
    assert "nenhum canário ativo" in capsys.readouterr().out

    registro = _iniciar(repo_falso)
    assert canario.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["model_version"] == registro["model_version"]


def test_cli_verificar_com_sanidade_aprova_canario_coerente(repo_falso, capsys):
    registro = _iniciar(repo_falso)

    assert canario.main(["verificar", "--sanidade"]) == 0
    assert f"canário {registro['model_version']} coerente" in capsys.readouterr().out


def test_cli_verificar_com_modelo_trocado_devolve_1(repo_falso, capsys):
    _iniciar(repo_falso)
    (repo_falso / "canario" / canario.MODELO).write_bytes(b"outro modelo")

    assert canario.main(["verificar"]) == 1
    assert capsys.readouterr().out.startswith("::error::")


def test_cli_avaliar_com_canario_publica_decisao_relatorio_e_resumo(
    repo_falso, banco, monkeypatch, tmp_path
):
    falso = banco()
    registro = _iniciar(repo_falso)
    saida, summary = tmp_path / "github_output", tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(saida))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    relatorio, resumo = tmp_path / "avaliacao.json", tmp_path / "avaliacao.md"

    codigo = canario.main(
        ["avaliar", "--relatorio-json", str(relatorio), "--resumo-md", str(resumo)]
    )

    assert codigo == 0
    outputs = dict(
        linha.split("=", 1) for linha in saida.read_text(encoding="utf-8").splitlines()
    )
    # Sem tráfego nenhum, o único desfecho possível é esperar.
    assert outputs == {"acao": canario.ACAO_AGUARDAR, "model_version": registro["model_version"]}
    assert json.loads(relatorio.read_text(encoding="utf-8"))["acao"] == canario.ACAO_AGUARDAR
    assert resumo.read_text(encoding="utf-8") in summary.read_text(encoding="utf-8")
    assert falso.revertidos_gravados == []  # só a decisão de reverter grava no banco


def test_cli_promover_le_a_evidencia_e_reescreve_o_campeao(repo_falso, tmp_path):
    registro = _iniciar(repo_falso)
    evidencia = tmp_path / "avaliacao.json"
    evidencia.write_text(json.dumps({"acao": "promover"}), encoding="utf-8")

    assert canario.main(["promover", "--evidencia-json", str(evidencia)]) == 0

    campeao_gravado = json.loads((repo_falso / "champion_metrics.json").read_text("utf-8"))
    assert campeao_gravado["model_version"] == registro["model_version"]
    assert not canario.ativo()


def test_cli_reverter_registra_no_banco_antes_de_encerrar(repo_falso, banco):
    """`--registrar-no-banco` é o rollback que age antes do merge do PR: o
    job da manhã seguinte já lê `canarios_revertidos`."""
    falso = banco()
    registro = _iniciar(repo_falso)

    argumentos = ["reverter", "--motivo", "rollback manual: teste", "--registrar-no-banco"]
    assert canario.main(argumentos) == 0

    assert falso.revertidos_gravados == [(registro["model_version"], "rollback manual: teste")]
    assert canario.modelos_revertidos() == {registro["model_version"]}


# --------------------------------------------------------------------------
# preparação no job: nunca levanta, na dúvida 100% campeão
# --------------------------------------------------------------------------
class _BancoFalso:
    def __init__(self, revertido=None, estatisticas=None):
        self.revertido = revertido
        self.estatisticas = estatisticas or {}
        self.revertidos_gravados = []

    def canario_revertido(self, model_version):
        return self.revertido

    def registrar_canario_revertido(self, model_version, motivo):
        self.revertidos_gravados.append((model_version, motivo))

    def estatisticas_de_modelo(self, model_version, desde):
        return self.estatisticas.get(
            model_version,
            {
                "predicoes": 0,
                "disparos": 0,
                "fora_do_dominio": 0,
                "desfechos_baixo_risco": 0,
                "faltas_baixo_risco": 0,
                "primeira_predicao": None,
            },
        )


@pytest.fixture
def banco(monkeypatch):
    import db.repositories as repositories

    def _instalar(**kwargs):
        falso = _BancoFalso(**kwargs)
        for nome in ("canario_revertido", "registrar_canario_revertido", "estatisticas_de_modelo"):
            monkeypatch.setattr(repositories, nome, getattr(falso, nome))
        return falso

    return _instalar


def test_sem_canario_nao_ha_contexto_nem_falha(repo_falso):
    prep = canario.preparar_para_job("campeao00001", params=PARAMS)
    assert (prep.contexto, prep.falhas, prep.avisos) == (None, [], [])


def test_canario_saudavel_carrega_o_modelo(repo_falso, banco):
    banco()
    registro = _iniciar(repo_falso)

    prep = canario.preparar_para_job("campeao00001", params=PARAMS, agora=AGORA)

    assert prep.falhas == []
    assert prep.contexto is not None
    assert prep.contexto.model_version == registro["model_version"]
    assert prep.contexto.fracao == CFG["fracao"]


def test_variavel_de_desligamento_tira_o_canario_sem_falhar(repo_falso, banco, monkeypatch):
    banco()
    _iniciar(repo_falso)
    monkeypatch.setenv("CANARIO_DESLIGADO", "true")

    prep = canario.preparar_para_job("campeao00001", params=PARAMS)

    assert prep.contexto is None
    assert prep.falhas == []
    assert "CANARIO_DESLIGADO" in prep.avisos[0]


def test_canario_aprovado_contra_outro_campeao_e_ignorado_com_falha(repo_falso, banco):
    banco()
    _iniciar(repo_falso)

    prep = canario.preparar_para_job("campeao-novo", params=PARAMS)

    assert prep.contexto is None
    assert "fila 100% campeão" in prep.falhas[0]


def test_modelo_do_canario_ausente_vira_falha_e_nao_excecao(repo_falso, banco):
    banco()
    _iniciar(repo_falso)
    (repo_falso / "canario" / canario.MODELO).unlink()

    prep = canario.preparar_para_job("campeao00001", params=PARAMS)

    assert prep.contexto is None
    assert prep.falhas


def test_canario_ja_revertido_no_banco_fica_fora_so_com_aviso(repo_falso, banco):
    banco(revertido={"motivo": "x", "revertido_em": "2026-10-10"})
    _iniciar(repo_falso)

    prep = canario.preparar_para_job("campeao00001", params=PARAMS)

    assert prep.contexto is None
    assert prep.falhas == []
    assert "revertido" in prep.avisos[0]


def test_guardrail_violado_faz_rollback_automatico(repo_falso, banco):
    versao = inference.calcular_model_version(MODEL_PATH)
    primeira = AGORA - timedelta(days=3)
    falso = banco(
        estatisticas={
            versao: {
                "predicoes": 400,
                "disparos": 240,
                "fora_do_dominio": 0,
                "desfechos_baixo_risco": 100,
                "faltas_baixo_risco": 20,
                "primeira_predicao": primeira,
            },
            "campeao00001": {
                "predicoes": 1600,
                "disparos": 480,
                "fora_do_dominio": 0,
                "desfechos_baixo_risco": 800,
                "faltas_baixo_risco": 160,
                "primeira_predicao": primeira,
            },
        }
    )
    _iniciar(repo_falso)

    prep = canario.preparar_para_job("campeao00001", params=PARAMS, agora=AGORA)

    assert prep.contexto is None
    assert falso.revertidos_gravados and falso.revertidos_gravados[0][0] == versao
    assert "rollback automático" in prep.falhas[0]


# --------------------------------------------------------------------------
# guardrail de quarentena, por execução
# --------------------------------------------------------------------------
def _contexto(model_version="canario-teste", threshold=0.0, fracao=0.5):
    model, mapa = inference.carregar_modelo(MODEL_PATH)
    from explain import construir_explicador

    return canario.ContextoCanario(
        model=model,
        mapa_especialidade=mapa,
        explainer=construir_explicador(model),
        model_version=model_version,
        threshold=threshold,
        fracao=fracao,
    )


def test_quarentena_total_do_canario_reverte(banco):
    falso = banco()
    falhas = canario.avaliar_execucao(
        _contexto(),
        {
            "canario": {"agendamentos": 6, "quarentena": 6},
            "campeao": {"agendamentos": 24, "quarentena": 0},
        },
        params=PARAMS,
    )
    assert falhas and falso.revertidos_gravados


def test_quarentena_igual_nos_dois_bracos_nao_reverte(banco):
    """O dado é o mesmo nos dois braços: quarentena por dado ruim aparece
    nos dois e não é culpa do canário."""
    falso = banco()
    falhas = canario.avaliar_execucao(
        _contexto(),
        {
            "canario": {"agendamentos": 60, "quarentena": 3},
            "campeao": {"agendamentos": 240, "quarentena": 12},
        },
        params=PARAMS,
    )
    assert falhas == [] and falso.revertidos_gravados == []


# --------------------------------------------------------------------------
# job D-2 com canário
# --------------------------------------------------------------------------
class _RepositorioDoJob:
    def __init__(self, pendentes):
        self.pendentes = pendentes
        self.predicoes = []
        self.mensagens = []

    def buscar_agendamentos_d2_pendentes(self, data_referencia):
        return self.pendentes

    def gravar_predicao(self, **kwargs):
        self.predicoes.append(kwargs)

    def registrar_mensagem(self, id_agendamento, canal, status_envio):
        self.mensagens.append((id_agendamento, status_envio))

    def marcar_lembrete_enviado(self, id_agendamento):
        pass


class _Espiao:
    canal = "espiao"

    def enviar_lembrete(self, telefone, mensagem):
        pass


def _agendamento(i):
    return {
        "id": f"a{i:02d}",
        "especialidade": "cardiologia",
        "distancia_km": 5.5,
        "data_hora_agendada": "2026-10-02T13:00:00+00:00",
        "dias_entre_agendamento_consulta": 14,
        "historico_noshow": 1,
        "pacientes": {
            "data_nascimento": "1980-01-01",
            "sexo": "F",
            "id_paciente_externo": f"hash-{i}",
            "telefone": f"55119876543{i:02d}",
        },
    }


@pytest.fixture
def fila(monkeypatch):
    def _instalar(n):
        falso = _RepositorioDoJob([_agendamento(i) for i in range(n)])
        for nome in (
            "buscar_agendamentos_d2_pendentes",
            "gravar_predicao",
            "registrar_mensagem",
            "marcar_lembrete_enviado",
        ):
            monkeypatch.setattr(job.repositories, nome, getattr(falso, nome))
        # campeão nunca dispara; canário (threshold 0.0) sempre dispara
        monkeypatch.setitem(job.inference.PARAMS, "decision", {"threshold": 1.01})
        return falso

    return _instalar


def test_fracao_do_canario_e_decidida_com_o_modelo_e_o_threshold_dele(fila):
    falso = fila(40)
    contexto = _contexto(fracao=0.5)

    resultado = job.processar_dia(cliente_mensageria=_Espiao(), contexto_canario=contexto)

    do_canario = [p for p in falso.predicoes if p["model_version"] == "canario-teste"]
    esperados = {
        f"a{i:02d}" for i in range(40) if canario.braco(f"hash-{i}", 0.5, "canario-teste")
    }
    assert {p["id_agendamento"] for p in do_canario} == esperados
    assert all(p["threshold_usado"] == 0.0 and p["classe_prevista"] == 1 for p in do_canario)
    enviados = {i for i, status in falso.mensagens if status == "enviado"}
    assert enviados == esperados
    por_braco = resultado["por_braco"]
    assert por_braco["canario"]["agendamentos"] == len(esperados)
    assert por_braco["canario"]["disparos_por_risco"] == len(esperados)
    assert por_braco["campeao"]["predicoes_gravadas"] == 40 - len(esperados)
    assert resultado["predicoes_gravadas"] == 40


def test_sem_canario_o_resultado_nao_muda_de_forma(fila):
    fila(3)
    resultado = job.processar_dia(cliente_mensageria=_Espiao())
    assert "por_braco" not in resultado
    assert resultado["predicoes_gravadas"] == 3


def test_canario_quebrado_falha_o_run_mas_a_fila_segue_com_o_campeao(fila, monkeypatch):
    falso = fila(2)
    # A pré-checagem do campeão tem teste próprio; aqui o threshold 1.01 da
    # fixture (que impede o campeão de disparar) a reprovaria.
    monkeypatch.setattr(job, "pre_checagem", lambda: "campeao")
    monkeypatch.setattr(
        job.canario,
        "preparar_para_job",
        lambda *a, **k: canario.Preparacao(falhas=["canário x ignorado -- fila 100% campeão"]),
    )
    monkeypatch.setattr(job, "obter_cliente_mensageria", _Espiao)
    # As purgas de retenção abririam o client do Supabase com o `.env` local
    # (que aponta para o projeto real) e o deixariam em cache para os testes
    # seguintes -- este teste não pode tocar banco nenhum.
    monkeypatch.setattr(job, "purgar_eventos_antigos", lambda: 0)
    monkeypatch.setattr(job, "purgar_dados_derivados_antigos", lambda: {})

    assert job.main([]) == job.SAIDA_FALHA
    assert len(falso.predicoes) == 2
    assert {p["model_version"] for p in falso.predicoes} == {
        inference.calcular_model_version(MODEL_PATH)
    }


def test_resumo_do_job_mostra_os_bracos(tmp_path, monkeypatch):
    destino = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(destino))
    resultado = {
        "agendamentos_encontrados": 2,
        "predicoes_gravadas": 2,
        "quarentena": 0,
        "fora_do_dominio": 0,
        "mensagens_disparadas": 1,
        "lembretes_sem_predicao": 0,
        "falhas_de_envio": 0,
        "erros": [],
        "por_braco": {
            "campeao": {
                "agendamentos": 1,
                "predicoes_gravadas": 1,
                "quarentena": 0,
                "disparos_por_risco": 0,
            },
            "canario": {
                "agendamentos": 1,
                "predicoes_gravadas": 1,
                "quarentena": 0,
                "disparos_por_risco": 1,
            },
        },
    }

    job._publicar_resumo(resultado, [], [], "campeao", canario_version="canario-v")

    texto = destino.read_text(encoding="utf-8")
    assert "Canário `canario-v`" in texto
    assert "| canario | 1 | 1 | 0 | 1 |" in texto


def test_staging_do_deploy_nao_leva_o_canario_ao_space(tmp_path):
    """O canário vive só no runner do job: o Space roda o campeão. A lista
    fechada mora em scripts/montar_staging_space.py desde 2026-10-02, então
    o teste monta o staging de verdade em vez de ler o YAML."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import montar_staging_space

    conteudo = montar_staging_space.montar(tmp_path / "space")
    assert not [c for c in conteudo if c.startswith("data/") and c != "data/model.pkl"]
    assert not [c for c in conteudo if "canario" in c and not c.startswith("src/")]


def test_copia_do_modelo_real_para_o_canario_tem_a_mesma_versao(tmp_path):
    copia = tmp_path / "model.pkl"
    shutil.copyfile(MODEL_PATH, copia)
    assert inference.calcular_model_version(copia) == inference.calcular_model_version(MODEL_PATH)
