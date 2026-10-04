"""Alerta ativo sobre `eventos_app` (src/alerta_observabilidade.py, ADR-006)."""
from pathlib import Path

import yaml

import alerta_observabilidade as alerta
import observabilidade


def _evento(tipo, origem, duracao_ms, status="ok", excecao=None):
    return {
        "tipo": tipo,
        "origem": origem,
        "duracao_ms": duracao_ms,
        "status": status,
        "detalhe": {"excecao": excecao} if excecao else {},
    }


def _saudavel(**mudancas):
    base = {
        "janela_horas": 24,
        "predicoes_por_origem": {"job": 40},
        "p95_ms_por_origem": {"job": 12.0},
        "execucoes_job_d2": 1,
        "duracao_max_job_d2_ms": 30_000,
        "predicoes_gravadas": 40,
        "predicoes_com_explicacao": 40,
    }
    base.update(mudancas)
    return alerta.Medicoes(**base)


def test_medicoes_separam_origem_e_contam_erros_por_classe():
    eventos = [
        *(_evento(observabilidade.TIPO_PREDICAO, "job", ms) for ms in range(1, 101)),
        _evento(observabilidade.TIPO_PREDICAO, "processo", 1400),
        _evento(observabilidade.TIPO_JOB_D2, "job", 45_000),
        _evento(observabilidade.TIPO_ERRO, "job", None, "erro", "ErroEnvioInfobip"),
        _evento(observabilidade.TIPO_ERRO, "job", None, "erro", "ErroEnvioInfobip"),
    ]
    m = alerta.medicoes_dos_eventos(
        eventos, 24, predicoes_gravadas=100, predicoes_com_explicacao=99
    )
    assert m.predicoes_por_origem == {"job": 100, "processo": 1}
    assert m.p95_ms_por_origem["job"] == observabilidade.percentil(range(1, 101), 95)
    assert m.p95_ms_por_origem["processo"] == 1400.0
    assert m.erros == 2
    assert m.excecoes == {"ErroEnvioInfobip": 2}
    assert m.execucoes_job_d2 == 1
    assert m.duracao_max_job_d2_ms == 45_000
    assert m.cobertura_explicacao == 0.99
    assert not m.truncado


def test_sem_predicao_a_cobertura_nao_existe_em_vez_de_ser_zero():
    m = alerta.medicoes_dos_eventos([], 24, predicoes_gravadas=0, predicoes_com_explicacao=0)
    assert m.cobertura_explicacao is None


def test_janela_saudavel_nao_alerta():
    assert alerta.avaliar(_saudavel()) == []


def test_predicao_sem_explicacao_viola_o_slo_4():
    assert alerta.avaliar(_saudavel(predicoes_com_explicacao=39))


def test_um_unico_erro_ja_alerta_e_nomeia_a_classe():
    (linha,) = alerta.avaliar(_saudavel(erros=1, excecoes={"ViolacaoDoContrato": 1}))
    assert "ViolacaoDoContrato=1" in linha


def test_dia_sem_predicao_nao_e_violacao_do_slo_4():
    assert alerta.avaliar(_saudavel(predicoes_gravadas=0, predicoes_com_explicacao=0)) == []


def test_job_d2_sem_execucao_registrada_alerta():
    assert alerta.avaliar(_saudavel(execucoes_job_d2=0, duracao_max_job_d2_ms=None))


def test_job_d2_lento_alerta_antes_do_timeout():
    limite = alerta.DURACAO_MAXIMA_JOB_D2_MS
    assert alerta.avaliar(_saudavel(duracao_max_job_d2_ms=limite)) == []
    assert alerta.avaliar(_saudavel(duracao_max_job_d2_ms=limite + 1))
    workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "job_d2.yml"
    timeout_min = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["job-d2"][
        "timeout-minutes"
    ]
    assert limite < timeout_min * 60 * 1000, "o alerta tem de vir antes do timeout do job_d2.yml"


def test_p95_interativo_lento_nao_alerta():
    """Fora do compromisso de produção desde 2026-09-30 (SLO §2)."""
    lento = _saudavel(
        predicoes_por_origem={"job": 40, "processo": 30},
        p95_ms_por_origem={"job": 12.0, "processo": 9000.0},
    )
    assert alerta.avaliar(lento) == []


def test_resumo_so_tem_numeros(tmp_path, monkeypatch):
    destino = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(destino))
    alerta.publicar(_saudavel(erros=1, excecoes={"ErroEnvioInfobip": 1}), ["limite x violado"])
    texto = destino.read_text(encoding="utf-8")
    assert "com alerta" in texto
    assert "ErroEnvioInfobip" in texto
    assert "limite x violado" in texto


def test_main_falha_quando_ha_alerta_ou_quando_nao_consegue_medir(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(alerta, "medir", lambda janela_horas: _saudavel())
    monkeypatch.setattr(alerta, "avaliar", lambda m: [])
    assert alerta.main([]) == 0
    monkeypatch.setattr(alerta, "avaliar", lambda m: ["violou"])
    assert alerta.main([]) == 1

    def banco_fora(janela_horas):
        raise ConnectionError("telefone 11999999999 no payload")

    monkeypatch.setattr(alerta, "medir", banco_fora)
    assert alerta.main([]) == 1
    # Só o nome da classe: a mensagem de um erro de terceiro pode ecoar PII.
    saida = capsys.readouterr().out
    assert "ConnectionError" in saida
    assert "11999999999" not in saida
