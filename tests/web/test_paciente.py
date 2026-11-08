"""
SaúdeJá — tela do paciente e documentos legais na interface em NiceGUI (linhas
C1 a C10 da matriz da migração, mais a entrada padrão).
"""
import pytest
from apoio_web import esperar_ate, textos
from nicegui import ElementFilter, ui

import consentimento
import documentos_legais
from ui import logic
from web import sessao

CPF_VALIDO = "111.444.777-35"


async def _dar_ciencia(usuario) -> None:
    usuario.find(marker="ciencia").click()
    usuario.find(marker="continuar").click()
    await usuario.should_see(marker="agendar", retries=20)


async def _preencher_cadastro_valido(usuario) -> None:
    await _dar_ciencia(usuario)
    usuario.find(marker="paciente-nome").type("Paciente de Teste")
    usuario.find(marker="paciente-cpf").type(CPF_VALIDO)
    usuario.find(marker="paciente-telefone").type("(11) 98765-4321")
    # Sem valor padrão: precisa ser preenchida, no formato da tela antiga.
    usuario.find(marker="paciente-nascimento").type("1990/01/01")


def _valor(usuario, marker):
    with usuario:
        (elemento,) = ElementFilter(marker=marker)
        return elemento.value


def _habilitado(usuario, marker) -> bool:
    with usuario:
        (elemento,) = ElementFilter(marker=marker)
        return elemento.enabled


def _espiar_cadastro(monkeypatch, resposta=None):
    chamadas = []

    def _cadastrar(**campos):
        chamadas.append(campos)
        return dict(resposta or {})

    monkeypatch.setattr(logic, "cadastrar_paciente_e_agendamento", _cadastrar)
    return chamadas


def _textos_da_tela(usuario) -> str:
    with usuario:
        partes = []
        for elemento in ElementFilter():
            partes.append(str(getattr(elemento, "text", "") or ""))
            partes.append(str(elemento.props.get("label", "") or ""))
        return " ".join(partes)


async def test_entrada_e_a_tela_do_paciente_com_ciencia_desmarcada(usuario):
    """A URL pública é a porta do autoagendamento: quem chega vê a ciência, não
    o login da equipe, e o título não anuncia a classificação de risco."""
    await usuario.open("/")

    assert _valor(usuario, "ciencia") is False
    assert not _habilitado(usuario, "continuar")
    with usuario:
        assert usuario.client.page.title == "SaúdeJá"


async def test_sem_ciencia_nenhum_campo_existe_e_continuar_fica_bloqueado(usuario):
    """C6. A tela roda fora do Brasil: nenhum campo é montado antes da
    ciência, nem a data da consulta."""
    await usuario.open("/")

    with usuario:
        assert list(ElementFilter(kind=ui.input)) == []
        assert list(ElementFilter(kind=ui.select)) == []
    await usuario.should_not_see(marker="agendar")
    assert textos(usuario, marker="aviso-info") == []
    await usuario.should_see(marker="fora-do-brasil")

    usuario.find(marker="ciencia").click()

    assert _habilitado(usuario, "continuar")
    with usuario:
        assert list(ElementFilter(kind=ui.input)) == []  # só depois do clique

    usuario.find(marker="continuar").click()

    await usuario.should_see(marker="paciente-nome", retries=20)
    await usuario.should_see(marker="paciente-data")


@pytest.mark.parametrize("documento", documentos_legais.DOCUMENTOS)
async def test_link_abre_o_documento_e_a_ciencia_sobrevive_a_volta(usuario, documento):
    """C7. A caixa marcada vive em `app.storage.tab`, que acompanha a
    navegação da aba."""
    await usuario.open("/")
    usuario.find(marker="ciencia").click()

    usuario.find(marker=f"abrir-{documento}").click()

    await usuario.should_see(marker="documento", retries=20)
    with usuario:
        (texto,) = ElementFilter(marker="documento")
        conteudo = texto.content
    assert documentos_legais.texto_para_tela(documento)[:60] in conteudo
    assert "Rascunho" not in conteudo
    await usuario.should_not_see(marker="ciencia")

    usuario.find(marker="voltar").click()  # há dois links de volta; o clique usa o primeiro

    await usuario.should_see(marker="ciencia", retries=20)
    assert _valor(usuario, "ciencia") is True
    assert _habilitado(usuario, "continuar")


async def test_visao_paciente_carrega_o_formulario_e_falha_do_banco_e_generica(usuario):
    """C1. Sem Supabase, um cadastro válido falha com mensagem amigável; o
    motivo técnico (variável de ambiente ausente) fica no log e em
    `eventos_app`, não na tela pública."""
    await usuario.open("/")
    await _preencher_cadastro_valido(usuario)

    usuario.find(marker="agendar").click()

    await usuario.should_see(marker="aviso-erro", retries=40)
    (erro,) = textos(usuario, marker="aviso-erro")
    assert "cadastrar" in erro.lower()
    assert "SUPABASE" not in erro and ".env" not in erro


async def test_cadastro_em_branco_lista_todos_os_problemas_sem_tocar_o_banco(usuario):
    """C2."""
    await usuario.open("/")
    await _dar_ciencia(usuario)

    usuario.find(marker="agendar").click()

    await usuario.should_see("Corrija os campos", retries=20)
    assert textos(usuario, marker="aviso-erro") == []  # não chegou à persistência
    (aviso,) = textos(usuario, marker="aviso-aviso")
    for problema in ("Nome inválido", "CPF inválido", "Telefone inválido", "data de nascimento"):
        assert problema in aviso


def _definir_valor(usuario, marker, valor) -> None:
    """O valor como o navegador o manda ao servidor a cada tecla, sem o
    `blur` que faria o `ui.number` aplicar o mínimo e o máximo do campo."""
    with usuario:
        (elemento,) = ElementFilter(marker=marker)
        elemento.value = valor


@pytest.mark.parametrize(
    "distancia, problema",
    [(None, "Informe a distância"), (-3.0, "Distância inválida"), (900.0, "Distância inválida")],
    ids=["vazia", "negativa", "acima-de-500"],
)
async def test_distancia_fora_do_contrato_nao_conclui_o_cadastro(usuario, distancia, problema):
    """Regressão da troca do Streamlit: o limite do campo só vale no `blur`,
    e a distância negativa chegava ao banco e virava falha, contada como erro.
    Agora é recusa na tela, e campo vazio não vira 0 km."""
    await usuario.open("/")
    await _preencher_cadastro_valido(usuario)
    _definir_valor(usuario, "paciente-distancia", distancia)

    usuario.find(marker="agendar").click()

    await usuario.should_see("Corrija os campos", retries=20)
    (aviso,) = textos(usuario, marker="aviso-aviso")
    assert problema in aviso
    assert textos(usuario, marker="aviso-erro") == []  # não chegou à persistência
    await usuario.should_not_see(marker="confirmacao")
    assert _habilitado(usuario, "agendar")


async def test_data_de_nascimento_comeca_vazia(usuario):
    """C3. Um 01/01/1990 pré-preenchido e esquecido virava idade errada no
    modelo."""
    await usuario.open("/")
    await _dar_ciencia(usuario)

    assert _valor(usuario, "paciente-nascimento") == ""


async def test_campos_de_data_mantem_o_formato_da_tela_antiga(usuario):
    """Formato de data é entrada do modelo: não muda com a troca de interface."""
    await usuario.open("/")
    await _dar_ciencia(usuario)

    proxima = logic.proxima_data_disponivel()
    assert _valor(usuario, "paciente-data") == proxima.strftime("%Y/%m/%d")
    assert _valor(usuario, "paciente-horario") is not None


async def test_agendamento_confirmado_troca_o_formulario_pela_confirmacao(usuario, monkeypatch):
    """C4. A confirmação é neutra (nem todo agendamento recebe lembrete), e a
    ciência volta desmarcada no agendamento seguinte."""
    chamadas = _espiar_cadastro(monkeypatch)
    await usuario.open("/")
    await _preencher_cadastro_valido(usuario)

    usuario.find(marker="agendar").click()

    await usuario.should_see(marker="confirmacao", retries=20)
    assert len(chamadas) == 1
    (confirmacao,) = textos(usuario, marker="confirmacao")
    assert confirmacao.startswith("Agendamento confirmado")
    assert "lembrete" not in confirmacao.lower()
    await usuario.should_not_see(marker="agendar")

    usuario.find(marker="novo-agendamento").click()

    await usuario.should_see(marker="ciencia", retries=20)
    await usuario.should_not_see(marker="confirmacao")
    assert _valor(usuario, "ciencia") is False
    assert not _habilitado(usuario, "continuar")
    await usuario.should_not_see(marker="agendar")

    await _dar_ciencia(usuario)

    assert _valor(usuario, "paciente-nome") == ""


async def test_aviso_de_privacidade_do_paciente_sem_jargao_tecnico(usuario):
    """C5. Nas duas etapas: a da ciência e a do formulário."""
    await usuario.open("/")
    na_ciencia = _textos_da_tela(usuario)
    await _dar_ciencia(usuario)
    no_formulario = _textos_da_tela(usuario)

    for textos_da_etapa in (na_ciencia, no_formulario):
        for jargao in ("hash", "ADR", "Infobip", "LLM", "dataset", "features do modelo", ".md"):
            assert jargao not in textos_da_etapa
    with usuario:
        assert list(ElementFilter(kind=ui.expansion)) == []


async def test_caixa_do_treino_comeca_desmarcada_e_e_a_unica_do_formulario(usuario):
    """C8. O agendamento e o lembrete são parte do atendimento da clínica (art.
    7º, V; LGPD.md §3): a única autorização opcional é a do treino."""
    await usuario.open("/")
    await _dar_ciencia(usuario)

    assert _valor(usuario, "paciente-treino") is False
    with usuario:
        caixas = list(ElementFilter(kind=ui.checkbox))
        trechos = list(ElementFilter(marker="trecho-do-termo"))
    assert [c._markers for c in caixas] == [["paciente-treino"]]
    # O texto versionado do termo, inteiro, com o processamento fora do Brasil
    # em negrito (art. 9º, § 3º), e não com os asteriscos do markdown.
    assert "".join(t.text for t in trechos) == consentimento.TERMO_TREINO.replace("**", "")
    (destaque,) = [t.text for t in trechos if "font-bold" in t.classes]
    assert destaque == "Esse uso é feito fora do Brasil."


async def test_agendar_repassa_a_caixa_do_treino_e_a_versao_dos_documentos(usuario, monkeypatch):
    """C9."""
    chamadas = _espiar_cadastro(monkeypatch)
    await usuario.open("/")
    await _preencher_cadastro_valido(usuario)
    usuario.find(marker="paciente-treino").click()

    usuario.find(marker="agendar").click()

    await esperar_ate(lambda: chamadas)
    assert chamadas[0]["consentimentos"] == {consentimento.FINALIDADE_TREINO: True}
    assert chamadas[0]["versao_documentos"] == documentos_legais.versao()
    assert chamadas[0]["data_nascimento"].isoformat() == "1990-01-01"


@pytest.mark.parametrize(
    ("marcada", "trecho"),
    [(True, "você pode autorizar no próximo agendamento"), (False, "peça à clínica")],
)
async def test_falha_no_treino_confirma_o_agendamento_e_avisa(
    usuario, monkeypatch, marcada, trecho
):
    """C10."""
    _espiar_cadastro(monkeypatch, {"falha_no_treino": True})
    await usuario.open("/")
    await _preencher_cadastro_valido(usuario)
    if marcada:
        usuario.find(marker="paciente-treino").click()

    usuario.find(marker="agendar").click()

    await usuario.should_see(marker="confirmacao", retries=20)
    (confirmacao,) = textos(usuario, marker="confirmacao")
    assert confirmacao.startswith("Agendamento confirmado")
    assert trecho in confirmacao


async def test_ciencia_perdida_na_poda_da_aba_volta_a_ser_cobrada(usuario, monkeypatch):
    """A aba parada 30 min é podada da memória (web/sessao.py). O formulário
    ainda aberto não grava sem a ciência: `logic` a cobra como segunda
    barreira."""
    chamadas = []
    monkeypatch.setattr(
        logic.repositories, "inserir_paciente", lambda **kw: chamadas.append(kw) or {"id": "x"}
    )
    await usuario.open("/")
    await _preencher_cadastro_valido(usuario)
    with usuario:
        sessao.aba().pop(sessao.CHAVE_CIENCIA_CONFIRMADA)

    usuario.find(marker="agendar").click()

    await usuario.should_see("Política de privacidade e dos Termos de uso", retries=20)
    assert chamadas == []
