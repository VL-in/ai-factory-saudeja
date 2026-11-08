"""
SaúdeJá — tela inicial (`/`): a escolha entre a área do paciente e a da equipe.
"""
from nicegui import ElementFilter, ui

from web import rotas
from web.paginas import inicio


def _destino(usuario, marker) -> str:
    with usuario:
        (link,) = ElementFilter(marker=marker)
        return link.props["href"]


async def test_entrada_mostra_a_frase_e_os_dois_acessos(usuario):
    """Quem chega pela URL do Space escolhe o caminho: paciente agenda sem
    login, funcionário entra na área da equipe."""
    await usuario.open(rotas.INICIO)

    await usuario.should_see(marker="frase-de-valor")
    await usuario.should_see(inicio.FRASE_DE_VALOR)
    assert _destino(usuario, "acesso-paciente") == rotas.PACIENTE
    assert _destino(usuario, "acesso-equipe") == rotas.EQUIPE
    with usuario:
        assert usuario.client.page.title == "SaúdeJá"


async def test_entrada_nao_monta_campo_nem_login(usuario):
    """A ciência continua sendo a primeira etapa da tela do paciente, antes de
    qualquer campo (ADR-010): o início não monta formulário nenhum."""
    await usuario.open(rotas.INICIO)

    await usuario.should_not_see(marker="ciencia")
    await usuario.should_not_see(marker="login-email")
    with usuario:
        assert list(ElementFilter(kind=ui.input)) == []


async def test_acesso_do_paciente_leva_a_ciencia(usuario):
    await usuario.open(rotas.INICIO)
    await usuario.open(_destino(usuario, "acesso-paciente"))

    await usuario.should_see("Cadastro e agendamento")
    await usuario.should_see(marker="ciencia")


async def test_acesso_do_funcionario_leva_ao_login(usuario):
    await usuario.open(rotas.INICIO)
    await usuario.open(_destino(usuario, "acesso-equipe"))

    await usuario.should_see(marker="login-email", retries=20)


def test_css_do_inicio_aponta_para_a_imagem_servida():
    """O CSS é literal (guarda de HTML cru): se a URL da imagem mudar, este
    teste pega o fundo que sumiria em silêncio."""
    assert f'url("{inicio.URL_IMAGEM_DE_FUNDO}")' in inicio.CSS_INICIO
