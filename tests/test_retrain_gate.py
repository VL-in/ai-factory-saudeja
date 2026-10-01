"""
Testes do gate de promoção do re-treino mensal (Passo 9.1).

O gate decide se um modelo novo substitui o que está em produção — errar
para o lado permissivo publica um modelo pior, e errar para o restritivo
congela o produto. Por isso a decisão vive em funções puras
(`comparar`/`decidir`) e é exercitada aqui sem Docker, sem Azure e sem
GitHub: o MLflow é um sqlite em `tmp_path` (mesmo padrão de
`tests/test_train.py`) e o campeão é um arquivo de fixture.

O que cada bloco protege:
- **bloqueio/promoção**: os dois desfechos centrais, conferindo também que
  o arquivo do campeão fica *byte a byte* intacto quando bloqueia (é o que
  mantém o modelo anterior em produção "por construção, não por convenção").
- **regressão dentro da tolerância**: prova que o número vem de
  `params.yaml` (SLO §3.1) e não está hardcoded — o mesmo delta promove com
  tolerância 0.05 e bloqueia com 0.0.
- **falhas operacionais**: run inexistente, métrica ausente, campeão
  corrompido e tolerância não declarada não podem "passar batido" — um gate
  que não consegue medir nunca deve promover.
- **sem re-treino efetivo**: mês sem desfecho novo falha (decisão de
  2026-09-21), em vez de reproduzir a mesma métrica e trocar a
  `model_version` em produção à toa.
"""
import json

import joblib
import mlflow
import numpy as np
import pytest

import retrain_gate as gate
from sanidade_modelo import ResultadoSanidade

METRICAS_CAMPEAO = {
    "recall_1": 0.4286,
    "f1_1": 0.4186,
    "roc_auc": 0.6433,
    "accuracy": 0.6711,
}


def _criar_run(tracking_uri: str, metricas: dict, nome_experimento: str) -> str:
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(nome_experimento)
    with mlflow.start_run() as run:
        mlflow.log_metrics(metricas)
    return run.info.run_id


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Gate apontado inteiramente para dentro de tmp_path: MLflow sqlite,
    champion, run_id, model.pkl (bytes quaisquer -- só alimentam o sha256 de
    `model_version`), fold de teste e o .dvc do dataset."""
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow-gate.db'}"

    champion_path = tmp_path / "champion_metrics.json"
    run_id_path = tmp_path / "mlflow_run_id.txt"
    model_path = tmp_path / "model.pkl"
    test_path = tmp_path / "test.pkl"
    dvc_path = tmp_path / "consultas-treino.csv.dvc"

    model_path.write_bytes(b"modelo-desafiante")
    joblib.dump({"y": np.array([1] * 21 + [0] * 55)}, test_path)
    dvc_path.write_text(
        "outs:\n- md5: 9024cbcd0c3dcba3bdcbb35c8b7f4667\n  path: consultas-treino.csv\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(gate, "MLFLOW_TRACKING_URI", tracking_uri)
    monkeypatch.setattr(gate, "CHAMPION_PATH", str(champion_path))
    monkeypatch.setattr(gate, "RUN_ID_PATH", str(run_id_path))
    monkeypatch.setattr(gate, "MODEL_PATH", str(model_path))
    monkeypatch.setattr(gate, "TEST_PATH", str(test_path))
    monkeypatch.setattr(gate, "DATASET_DVC_PATH", str(dvc_path))
    monkeypatch.setattr(gate, "RELATORIO_DADOS_PATH", str(tmp_path / "relatorio_dados.json"))
    # O model.pkl daqui são bytes quaisquer: a suíte de sanidade e a taxa de
    # disparo (Passo 10.4) têm testes próprios abaixo, com modelo de verdade.
    monkeypatch.setattr(gate, "verificar_sanidade", lambda *_: ResultadoSanidade())
    monkeypatch.setattr(gate, "taxa_de_disparo_projetada", lambda *_: 0.29)

    class Ambiente:
        pass

    amb = Ambiente()
    amb.tracking_uri = tracking_uri
    amb.champion_path = champion_path
    amb.run_id_path = run_id_path
    amb.model_path = model_path
    amb.test_path = test_path
    amb.tmp_path = tmp_path

    def semear_champion(metricas=None):
        champion_path.write_text(
            json.dumps({"metricas": metricas or METRICAS_CAMPEAO}, indent=2),
            encoding="utf-8",
        )
        return champion_path

    def semear_run(metricas, nome="teste-gate"):
        run_id = _criar_run(tracking_uri, metricas, nome)
        run_id_path.write_text(run_id)
        return run_id

    amb.semear_champion = semear_champion
    amb.semear_run = semear_run
    return amb


# --------------------------------------------------------------------------
# bloqueio e promoção
# --------------------------------------------------------------------------
def test_bloqueia_e_nao_toca_no_campeao_quando_regride_alem_da_tolerancia(ambiente):
    ambiente.semear_champion()
    antes = ambiente.champion_path.read_bytes()
    # recall_1 cai 0.19 -- muito além da tolerância de 0.05
    ambiente.semear_run(
        {"recall_1": 0.2381, "f1_1": 0.2500, "roc_auc": 0.5500}, nome="gate-pior"
    )

    codigo = gate.main(["--sem-repro"])

    assert codigo == gate.SAIDA_BLOQUEADO
    assert ambiente.champion_path.read_bytes() == antes, (
        "campeão foi reescrito num ciclo bloqueado -- o modelo anterior deixaria de "
        "estar em produção por construção"
    )


def test_promove_e_reescreve_o_campeao_quando_melhora(ambiente):
    ambiente.semear_champion()
    run_id = ambiente.semear_run(
        {"recall_1": 0.5714, "f1_1": 0.5000, "roc_auc": 0.7100, "pr_auc": 0.48},
        nome="gate-melhor",
    )

    codigo = gate.main(["--sem-repro"])

    assert codigo == gate.SAIDA_PROMOVIDO
    champion = json.loads(ambiente.champion_path.read_text(encoding="utf-8"))
    assert champion["metricas"]["recall_1"] == pytest.approx(0.5714)
    assert champion["mlflow_run_id"] == run_id
    # métricas fora do gate viajam junto (servem ao pitch do Passo 12),
    # mesmo sem serem critério de promoção
    assert champion["metricas"]["pr_auc"] == pytest.approx(0.48)
    # o threshold em que as métricas foram medidas fica registrado: comparar
    # recall_1 de thresholds diferentes seria comparar outra coisa
    assert champion["decision_threshold"] == gate.PARAMS["decision"]["threshold"]
    assert champion["dataset"]["md5_dvc"] == "9024cbcd0c3dcba3bdcbb35c8b7f4667"
    assert champion["dataset"]["positivos_no_fold_de_teste"] == 21
    assert len(champion["model_version"]) == 12


def test_bootstrap_sem_campeao_promove_e_cria_o_arquivo(ambiente):
    assert not ambiente.champion_path.exists()
    ambiente.semear_run(
        {"recall_1": 0.4286, "f1_1": 0.4186, "roc_auc": 0.6433}, nome="gate-bootstrap"
    )

    codigo = gate.main(["--sem-repro"])

    assert codigo == gate.SAIDA_PROMOVIDO
    champion = json.loads(ambiente.champion_path.read_text(encoding="utf-8"))
    assert champion["metricas"]["recall_1"] == pytest.approx(0.4286)
    assert "bootstrap" in champion["motivo"]


# --------------------------------------------------------------------------
# a tolerância vem do params.yaml, não do código
# --------------------------------------------------------------------------
def test_regressao_dentro_da_tolerancia_promove(ambiente):
    """Um único paciente a menos no recall (1/21 ~= 0.048) é ruído de
    amostragem, não regressão -- SLO §3.1."""
    ambiente.semear_champion()
    ambiente.semear_run(
        {"recall_1": 0.3810, "f1_1": 0.4000, "roc_auc": 0.6400}, nome="gate-ruido"
    )

    codigo = gate.main(["--sem-repro"])

    assert codigo == gate.SAIDA_PROMOVIDO
    champion = json.loads(ambiente.champion_path.read_text(encoding="utf-8"))
    assert champion["metricas"]["recall_1"] == pytest.approx(0.3810)


def test_o_mesmo_delta_bloqueia_com_tolerancia_zero(ambiente):
    """Prova que o número é lido de params.yaml e não está hardcoded: o
    mesmo desafiante do teste anterior, com tolerância 0.0, bloqueia."""
    metricas_desafiante = {"recall_1": 0.3810, "f1_1": 0.4000, "roc_auc": 0.6400}

    decisao_frouxa = gate.decidir(
        {"metricas": METRICAS_CAMPEAO},
        metricas_desafiante,
        tol={"recall_1": 0.05, "f1_1": 0.05, "roc_auc": 0.02},
    )
    decisao_estrita = gate.decidir(
        {"metricas": METRICAS_CAMPEAO},
        metricas_desafiante,
        tol={"recall_1": 0.0, "f1_1": 0.0, "roc_auc": 0.0},
    )

    assert decisao_frouxa.promover
    assert not decisao_estrita.promover
    assert "recall_1" in decisao_estrita.motivo


def test_tolerancia_ausente_em_params_e_erro_em_vez_de_default_silencioso():
    with pytest.raises(gate.ErroGate, match=r"gate\.tolerancia"):
        gate.tolerancias({"gate": {"tolerancia": {"recall_1": 0.05}}})


def test_params_do_repositorio_declaram_as_tres_metricas_do_gate():
    tol = gate.tolerancias()
    assert set(tol) == set(gate.METRICAS_DO_GATE)
    # SLO §3.1: as de contagem em 0.05 (~1/21 positivos), roc_auc em 0.02
    assert tol["recall_1"] == 0.05
    assert tol["f1_1"] == 0.05
    assert tol["roc_auc"] == 0.02


# --------------------------------------------------------------------------
# falhas operacionais nunca promovem
# --------------------------------------------------------------------------
def test_run_inexistente_no_mlflow_levanta_erro(ambiente):
    ambiente.semear_champion()
    ambiente.run_id_path.write_text("run-que-nunca-existiu")

    with pytest.raises(gate.ErroGate, match="não encontrada"):
        gate.main(["--sem-repro"])

    assert json.loads(ambiente.champion_path.read_text())["metricas"] == METRICAS_CAMPEAO


def test_run_sem_as_metricas_do_gate_levanta_erro(ambiente):
    ambiente.semear_champion()
    ambiente.semear_run({"accuracy": 0.8}, nome="gate-incompleto")

    with pytest.raises(gate.ErroGate, match="validate"):
        gate.main(["--sem-repro"])


def test_campeao_corrompido_levanta_erro_em_vez_de_promover(ambiente):
    ambiente.champion_path.write_text("{isto não é json", encoding="utf-8")
    ambiente.semear_run(
        {"recall_1": 0.9, "f1_1": 0.9, "roc_auc": 0.9}, nome="gate-corrompido"
    )

    with pytest.raises(gate.ErroGate, match="JSON"):
        gate.main(["--sem-repro"])


def test_campeao_sem_bloco_de_metricas_levanta_erro(ambiente):
    ambiente.champion_path.write_text(json.dumps({"model_version": "abc"}), encoding="utf-8")

    with pytest.raises(gate.ErroGate, match="metricas"):
        gate.carregar_champion()


def test_run_id_vazio_nao_promove(ambiente):
    ambiente.semear_champion()
    ambiente.run_id_path.write_text("   ")

    with pytest.raises(gate.ErroGate, match="run do MLflow"):
        gate.main(["--sem-repro"])


# --------------------------------------------------------------------------
# sem re-treino efetivo (decisão de 2026-09-21: falha)
# --------------------------------------------------------------------------
def test_dataset_inalterado_falha_sem_tocar_no_campeao(ambiente, monkeypatch):
    ambiente.semear_champion()
    ambiente.semear_run(
        {"recall_1": 0.4286, "f1_1": 0.4186, "roc_auc": 0.6433}, nome="gate-noop"
    )
    antes = ambiente.champion_path.read_bytes()
    # `dvc repro` sem nada a reexecutar: o run_id gravado não muda
    monkeypatch.setattr(gate, "_dvc_repro", lambda: None)

    relatorio = ambiente.tmp_path / "relatorio.json"
    codigo = gate.main(["--relatorio-json", str(relatorio)])

    assert codigo == gate.SAIDA_SEM_RETREINO
    assert ambiente.champion_path.read_bytes() == antes
    conteudo = json.loads(relatorio.read_text(encoding="utf-8"))
    assert conteudo["promovido"] is False
    assert "mesmo hash" in conteudo["motivo"]


def test_run_id_novo_depois_do_repro_segue_para_a_comparacao(ambiente, monkeypatch):
    """Contraprova do teste acima: quando o treino de fato reexecuta (run_id
    novo), o gate não para no código 2 -- ele compara."""
    ambiente.semear_champion()
    ambiente.run_id_path.write_text("run-do-ciclo-anterior")

    def repro_que_treina():
        ambiente.semear_run(
            {"recall_1": 0.5714, "f1_1": 0.5000, "roc_auc": 0.7100}, nome="gate-novo"
        )

    monkeypatch.setattr(gate, "_dvc_repro", repro_que_treina)

    assert gate.main([]) == gate.SAIDA_PROMOVIDO


# --------------------------------------------------------------------------
# evidência que sobrevive ao workflow + aviso de tolerância
# --------------------------------------------------------------------------
def test_resumo_do_bloqueio_carrega_o_comparativo_e_vai_para_o_step_summary(
    ambiente, monkeypatch, tmp_path
):
    ambiente.semear_champion()
    ambiente.semear_run(
        {"recall_1": 0.1000, "f1_1": 0.1500, "roc_auc": 0.5000}, nome="gate-resumo"
    )
    summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert gate.main(["--sem-repro"]) == gate.SAIDA_BLOQUEADO

    texto = summary.read_text(encoding="utf-8")
    assert "bloqueada" in texto
    assert "`recall_1`" in texto and "0.1000" in texto and "0.4286" in texto
    assert "campeão **não** foi alterado" in texto


def test_avisa_para_revisar_a_tolerancia_quando_o_fold_cresce():
    tol = {"recall_1": 0.05, "f1_1": 0.05, "roc_auc": 0.02}
    metricas = {"recall_1": 0.5, "f1_1": 0.5, "roc_auc": 0.7}

    sem_aviso = gate.decidir({"metricas": METRICAS_CAMPEAO}, metricas, tol, positivos_no_fold=21)
    com_aviso = gate.decidir({"metricas": METRICAS_CAMPEAO}, metricas, tol, positivos_no_fold=80)

    assert sem_aviso.avisos == []
    assert len(com_aviso.avisos) == 1
    assert "80 positivos" in com_aviso.avisos[0]
    # avisar não é bloquear: mudar tolerância é decisão humana (SLO §3.1)
    assert com_aviso.promover


def test_positivos_no_fold_ausente_nao_derruba_o_gate(tmp_path):
    assert gate.positivos_no_fold_de_teste(str(tmp_path / "nao-existe.pkl")) is None


def test_champion_do_repositorio_e_legivel_pelo_gate():
    """O arquivo versionado tem que ser exatamente o que o gate sabe ler --
    ele é editado à mão com facilidade (é JSON, está em git) e um campo
    renomeado só apareceria no meio do workflow mensal.

    Não se compara `model_version` com o `data/model.pkl` do workspace de
    propósito: num ciclo bloqueado o modelo do workspace é o desafiante, e o
    campeão continua sendo o de antes -- divergir ali é o comportamento
    correto, não uma quebra."""
    champion = gate.carregar_champion()
    assert champion is not None, "data/champion_metrics.json não está no repositório"
    for metrica in gate.METRICAS_DO_GATE:
        assert isinstance(champion["metricas"][metrica], float)
    assert champion["decision_threshold"] == gate.PARAMS["decision"]["threshold"]


# --------------------------------------------------------------------------
# Passo 10.4: piso absoluto, suíte de sanidade, taxa de disparo, pipeline
# --------------------------------------------------------------------------
PISO = {"roc_auc": 0.60}
TOL = {"recall_1": 0.05, "f1_1": 0.05, "roc_auc": 0.02}


def test_piso_absoluto_bloqueia_mesmo_sem_regressao_relativa():
    """Efeito catraca: cada promoção pode regredir até a tolerância em relação
    ao campeão da vez. Aqui o delta (-0.01) cabe na tolerância (0.02), mas o
    desafiante fica abaixo do piso -- e é bloqueado."""
    campeao = {"metricas": {"recall_1": 0.43, "f1_1": 0.42, "roc_auc": 0.605}}
    desafiante = {"recall_1": 0.43, "f1_1": 0.42, "roc_auc": 0.595}

    decisao = gate.decidir(campeao, desafiante, TOL, piso=PISO)

    assert decisao.codigo_saida == gate.SAIDA_BLOQUEADO
    assert "piso absoluto" in decisao.motivo
    assert not any(c["regrediu"] for c in decisao.comparacoes)


def test_piso_absoluto_vale_tambem_no_bootstrap():
    decisao = gate.decidir(None, {"recall_1": 0.5, "f1_1": 0.5, "roc_auc": 0.55}, TOL, piso=PISO)

    assert not decisao.promover
    assert decisao.codigo_saida == gate.SAIDA_BLOQUEADO


def test_piso_vem_de_params_yaml():
    assert gate.pisos() == {"roc_auc": 0.60}


def test_suite_de_sanidade_reprovada_bloqueia_e_nao_toca_no_campeao(ambiente, monkeypatch):
    ambiente.semear_champion()
    antes = ambiente.champion_path.read_bytes()
    # métricas melhores que o campeão: só a sanidade pode bloquear
    ambiente.semear_run({"recall_1": 0.57, "f1_1": 0.50, "roc_auc": 0.71}, nome="gate-insano")
    monkeypatch.setattr(
        gate,
        "verificar_sanidade",
        lambda *_: ResultadoSanidade(falhas=["o modelo é constante: devolve 0.5000"]),
    )

    assert gate.main(["--sem-repro"]) == gate.SAIDA_BLOQUEADO
    assert ambiente.champion_path.read_bytes() == antes


def test_taxa_de_disparo_alta_avisa_mas_nao_bloqueia(ambiente, monkeypatch, tmp_path):
    ambiente.semear_champion()
    ambiente.semear_run({"recall_1": 0.57, "f1_1": 0.50, "roc_auc": 0.71}, nome="gate-caro")
    monkeypatch.setattr(gate, "taxa_de_disparo_projetada", lambda *_: 0.45)
    relatorio = tmp_path / "relatorio.json"

    assert gate.main(["--sem-repro", "--relatorio-json", str(relatorio)]) == gate.SAIDA_PROMOVIDO

    conteudo = json.loads(relatorio.read_text(encoding="utf-8"))
    assert conteudo["taxa_disparo_projetada"] == 0.45
    assert any("taxa de disparo projetada" in a for a in conteudo["avisos"])
    campeao = json.loads(ambiente.champion_path.read_text(encoding="utf-8"))
    assert campeao["taxa_disparo_projetada"] == 0.45


def test_taxa_de_disparo_projetada_com_o_modelo_real(tmp_path):
    """Contra o data/model.pkl de verdade, sobre um fold sintético montado da
    grade da suíte de sanidade (o test.pkl do DVC não existe no CI)."""
    import inference
    import sanidade_modelo

    model, mapa = inference.carregar_modelo("data/model.pkl")
    X = inference.construir_features_lote(sanidade_modelo.grade_de_casos(sorted(mapa)), mapa)
    fold = tmp_path / "test.pkl"
    joblib.dump({"X": X, "y": np.zeros(len(X))}, fold)

    taxa = gate.taxa_de_disparo_projetada("data/model.pkl", str(fold))

    esperado = float((inference.predizer(model, X) >= gate.PARAMS["decision"]["threshold"]).mean())
    assert taxa == pytest.approx(esperado)
    assert 0.0 <= taxa <= 1.0


def test_dataset_barrado_pelo_validate_data_vira_codigo_3_com_o_motivo(ambiente, monkeypatch):
    """`dvc repro` falhando no stage validate_data: o resumo diz o que barrou,
    em vez de um traceback indistinguível de um bloqueio por regressão."""
    import subprocess

    ambiente.semear_champion()
    antes = ambiente.champion_path.read_bytes()
    (ambiente.tmp_path / "relatorio_dados.json").write_text(
        json.dumps(
            {
                "aprovado": False,
                "bloqueios": ["distribuição: taxa de positivos 3.0% fora da faixa [10%, 50%]"],
                "alertas": [],
                "resumo": {},
            }
        ),
        encoding="utf-8",
    )

    def repro_que_falha():
        raise subprocess.CalledProcessError(1, ["dvc", "repro"])

    monkeypatch.setattr(gate, "_dvc_repro", repro_que_falha)
    relatorio = ambiente.tmp_path / "relatorio.json"

    assert gate.main(["--relatorio-json", str(relatorio)]) == gate.SAIDA_FALHA_PIPELINE
    assert ambiente.champion_path.read_bytes() == antes
    conteudo = json.loads(relatorio.read_text(encoding="utf-8"))
    assert "taxa de positivos" in conteudo["motivo"]
