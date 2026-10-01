"""
SaúdeJá — comportamento do job D-2 por linha, sem banco (revisão do Passo 10,
2026-09-29). O repositório é trocado por um falso em memória; o modelo é o
`data/model.pkl` real, como em `tests/test_inference.py`.

O que se trava aqui:
- **quarentena**: uma linha que não pode ser predita não aborta a fila. Antes,
  só `KeyError`/`EspecialidadeDesconhecidaError` eram capturadas -- um
  `TypeError` derrubava o resto da fila, que com a janela de um dia só nunca
  mais era predita;
- **lembrete mesmo sem predição**, como exceção rastreável
  (`enviado_sem_predicao`), por decisão da autora: a falta custa R$ 180, o SMS
  custa centavos;
- **`lembrete_enviado`** marcado em todo envio bem-sucedido -- é o que o
  re-treino usa contra o feedback loop e o que impede reenvio na janela de
  três dias.
"""
import pytest

from jobs import inferencia_diaria as job


class _RepositorioFalso:
    def __init__(self, pendentes):
        self.pendentes = pendentes
        self.predicoes = []
        self.mensagens = []
        self.lembretes = []

    def buscar_agendamentos_d2_pendentes(self, data_referencia):
        return self.pendentes

    def gravar_predicao(self, **kwargs):
        self.predicoes.append(kwargs)

    def registrar_mensagem(self, id_agendamento, canal, status_envio):
        self.mensagens.append((id_agendamento, status_envio))

    def marcar_lembrete_enviado(self, id_agendamento):
        self.lembretes.append(id_agendamento)


class _Espiao:
    canal = "espiao"

    def __init__(self):
        self.telefones = []

    def enviar_lembrete(self, telefone, mensagem):
        self.telefones.append(telefone)


def _agendamento(id_agendamento, **extra):
    base = {
        "id": id_agendamento,
        "especialidade": "cardiologia",
        "distancia_km": 5.5,
        "data_hora_agendada": "2026-10-02T13:00:00+00:00",
        "dias_entre_agendamento_consulta": 14,
        "historico_noshow": 1,
        "pacientes": {
            "data_nascimento": "1980-01-01",
            "sexo": "F",
            "id_paciente_externo": f"hash-{id_agendamento}",
            "telefone": f"55119876543{id_agendamento[-2:]}",
        },
    }
    base.update(extra)
    return base


@pytest.fixture
def repositorio(monkeypatch):
    def _instalar(pendentes, threshold=0.0):
        falso = _RepositorioFalso(pendentes)
        for nome in (
            "buscar_agendamentos_d2_pendentes",
            "gravar_predicao",
            "registrar_mensagem",
            "marcar_lembrete_enviado",
        ):
            monkeypatch.setattr(job.repositories, nome, getattr(falso, nome))
        monkeypatch.setitem(job.inference.PARAMS, "decision", {"threshold": threshold})
        return falso

    return _instalar


def test_linha_invalida_vai_para_quarentena_sem_abortar_a_fila(repositorio):
    # `distancia_km=None` -- antes um TypeError em float(), fora da lista de
    # exceções capturadas, que abortava tudo o que vinha depois; hoje o
    # contrato de features (Passo 10.3) o recusa antes de chegar ao modelo.
    falso = repositorio(
        [_agendamento("a01", distancia_km=None), _agendamento("a02")], threshold=1.01
    )
    resultado = job.processar_dia(cliente_mensageria=_Espiao())

    assert resultado["predicoes_gravadas"] == 1
    assert [p["id_agendamento"] for p in falso.predicoes] == ["a02"]
    assert [e["id_agendamento"] for e in resultado["erros"]] == ["a01"]


def test_paciente_sem_predicao_recebe_lembrete_como_excecao(repositorio):
    falso = repositorio([_agendamento("a01", especialidade="neurologia")], threshold=1.01)
    espiao = _Espiao()
    resultado = job.processar_dia(cliente_mensageria=espiao)

    assert espiao.telefones == ["5511987654301"]
    assert falso.mensagens == [("a01", job.STATUS_ENVIADO_SEM_PREDICAO)]
    assert falso.lembretes == ["a01"]
    assert resultado["lembretes_sem_predicao"] == 1
    assert falso.predicoes == []


def test_alto_risco_marca_lembrete_e_baixo_risco_nao(repositorio):
    falso = repositorio([_agendamento("a01")], threshold=0.0)
    job.processar_dia(cliente_mensageria=_Espiao())
    assert falso.mensagens == [("a01", "enviado")]
    assert falso.lembretes == ["a01"]

    falso = repositorio([_agendamento("a02")], threshold=1.01)
    resultado = job.processar_dia(cliente_mensageria=_Espiao())
    assert falso.mensagens == [("a02", "nao_enviado")]
    assert falso.lembretes == []
    assert resultado["lembretes_sem_predicao"] == 0


def test_falha_de_envio_nao_marca_lembrete_para_o_dia_seguinte_tentar_de_novo(repositorio):
    falso = repositorio([_agendamento("a01", pacientes={"sexo": "F"})], threshold=1.01)
    resultado = job.processar_dia(cliente_mensageria=_Espiao())

    assert falso.mensagens == [("a01", "falha_envio")]
    assert falso.lembretes == []
    assert resultado["mensagens_disparadas"] == 0


# --- Passo 10.3: contrato de features no job ----------------------------------


def test_agendamento_fora_do_dominio_e_predito_e_marcado(repositorio):
    falso = repositorio([_agendamento("a01", distancia_km=120)], threshold=1.01)
    resultado = job.processar_dia(cliente_mensageria=_Espiao())

    assert [p["fora_do_dominio"] for p in falso.predicoes] == [True]
    assert resultado["fora_do_dominio"] == 1
    assert resultado["quarentena"] == 0


def test_violacao_do_contrato_vai_para_quarentena_com_motivo_sem_o_valor(repositorio):
    falso = repositorio([_agendamento("a01", historico_noshow=-3)], threshold=1.01)
    resultado = job.processar_dia(cliente_mensageria=_Espiao())

    assert falso.predicoes == []
    assert resultado["quarentena"] == 1
    motivo = resultado["erros"][0]["motivo"]
    assert motivo.startswith("historico_noshow:")
    assert "-3" not in motivo


def test_excecao_fora_do_contrato_so_expoe_a_classe(repositorio):
    """`str(exc)` de um ValueError de data traz a data de nascimento: o
    `resultado["erros"]` é exibido na aba de dev e não pode carregá-la."""
    paciente = {"data_nascimento": "1980-13-45", "sexo": "F", "telefone": "5511987654301"}
    repositorio([_agendamento("a01", pacientes=paciente)], threshold=1.01)
    resultado = job.processar_dia(cliente_mensageria=_Espiao())

    assert resultado["erros"][0]["motivo"] == "ValueError"


# --- Passo 10.5: pré e pós-checagem do caminho agendado -------------------------


def _resultado(**contadores):
    base = {
        "agendamentos_encontrados": 10,
        "predicoes_gravadas": 10,
        "quarentena": 0,
        "fora_do_dominio": 0,
        "mensagens_disparadas": 2,
        "lembretes_sem_predicao": 0,
        "falhas_de_envio": 0,
        "erros": [],
    }
    base.update(contadores)
    return base


def test_pos_checagem_de_um_dia_normal_nao_reclama():
    assert job.pos_checagem(_resultado()) == ([], [])


def test_pos_checagem_falha_quando_a_contabilidade_nao_fecha():
    falhas, _ = job.pos_checagem(_resultado(predicoes_gravadas=8, quarentena=1))
    assert any("contabilidade" in f for f in falhas)


def test_pos_checagem_falha_quando_a_fila_inteira_foi_para_a_quarentena():
    """Defeito sistêmico: e a regra do lembrete sem predição mandou SMS para
    a fila inteira. Tem de acordar alguém (Healthchecks /fail)."""
    falhas, _ = job.pos_checagem(
        _resultado(
            predicoes_gravadas=0, quarentena=10, mensagens_disparadas=10, lembretes_sem_predicao=10
        )
    )
    assert any("sistêmico" in f for f in falhas)


def test_pos_checagem_avisa_quarentena_parcial_e_disparo_alto_sem_falhar():
    falhas, avisos = job.pos_checagem(
        _resultado(
            predicoes_gravadas=9, quarentena=1, mensagens_disparadas=5, lembretes_sem_predicao=1
        )
    )
    assert falhas == []
    texto = " | ".join(avisos)
    assert "quarentena" in texto
    assert "taxa de disparo" in texto  # 4 de 9 por risco > 30%


def test_resumo_vai_para_o_step_summary_so_com_contadores(tmp_path, monkeypatch, capsys):
    destino = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(destino))
    resultado = _resultado(erros=[{"id_agendamento": "a01", "motivo": "ValueError"}])

    job._publicar_resumo(resultado, ["falha x"], ["aviso y"], "abc123")

    texto = destino.read_text(encoding="utf-8")
    assert "| quarentena | 0 |" in texto
    assert "a01" not in texto
    saida = capsys.readouterr().out
    assert "::error::falha x" in saida and "::warning::aviso y" in saida


def test_pre_checagem_recusa_threshold_diferente_do_campeao(monkeypatch):
    monkeypatch.setattr(job.repositories, "verificar_conexao", lambda: None)
    monkeypatch.setitem(job.inference.PARAMS, "decision", {"threshold": 0.35})

    with pytest.raises(job.campeao.ModeloNaoCampeao, match="threshold"):
        job.pre_checagem()


def test_pre_checagem_passa_com_o_campeao_do_repositorio(monkeypatch):
    conexoes = []
    monkeypatch.setattr(job.repositories, "verificar_conexao", lambda: conexoes.append(1))

    assert job.pre_checagem() == job.campeao.carregar_campeao()["model_version"]
    assert conexoes == [1]
