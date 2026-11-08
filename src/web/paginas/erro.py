"""
SaúdeJá — o que a tela mostra quando algo sai do previsto.

Sem este módulo, o NiceGUI responde com a página de erro dele: em inglês
("This page doesn't exist.", "Server error") e com o nome e a mensagem da
exceção, por exemplo `FileNotFoundError: ... /app/docs/LGPD/...`. A tela do
paciente é a porta pública, e o detalhe da falha é da equipe técnica, que o
recebe no log (o NiceGUI loga toda exceção com o traceback, pelo mesmo filtro
de redação dos demais logs, LGPD.md §8).

Três situações, três ganchos do NiceGUI:

- **endereço que não existe** (404): página própria, com o caminho de volta
  ao início;
- **exceção ao montar uma página** (`app.on_page_exception`) ou numa rota que
  não é página (`exception_handler(Exception)`): página própria, com status 500;
- **exceção num clique, depois de a página aberta** (`app.on_exception`): a
  página continua de pé e uma notificação avisa que a ação não foi concluída.
  Antes, a pessoa ficava sem resposta nenhuma.

As falhas previstas (banco fora do ar, formulário inválido) continuam tratadas
em cada tela, com a mensagem própria; isto é a rede de baixo.
"""
import contextlib

from fastapi import Request
from fastapi.responses import Response
from nicegui import Client, app, ui

from web import rotas, tema

TITULO_NAO_ENCONTRADA = "Página não encontrada"
TEXTO_NAO_ENCONTRADA = "O endereço digitado não existe ou mudou."
TITULO_ERRO_INESPERADO = "Algo deu errado"
TEXTO_ERRO_INESPERADO = (
    "Não foi possível abrir esta página agora. Tente de novo em alguns minutos. "
    "Se o problema continuar, fale com a clínica."
)
MENSAGEM_ACAO_NAO_CONCLUIDA = (
    "Não foi possível concluir esta ação. Tente de novo em alguns minutos. "
    "Se o problema continuar, fale com a clínica."
)


def registrar() -> None:
    app.on_page_exception(_pagina_de_erro_inesperado)
    app.on_exception(_avisar_acao_nao_concluida)
    app.add_exception_handler(404, _resposta_nao_encontrada)
    app.add_exception_handler(Exception, _resposta_erro_inesperado)


def conteudo_de_erro(titulo: str, texto: str) -> None:
    """Sem o cabeçalho das outras páginas: ele lê a preferência de tema em
    `app.storage.tab`, que só existe com o websocket conectado, e a página de
    erro é entregue antes disso."""
    tema.aplicar_tema()
    with ui.column().classes("w-full max-w-xl mx-auto px-4 py-16 items-center gap-3"):
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.icon("medical_services", color="primary").classes("text-2xl")
            ui.label("SaúdeJá").classes("text-lg font-bold tracking-tight")
        ui.label(titulo).classes("text-2xl font-semibold text-center").mark("erro-titulo")
        ui.label(texto).classes("text-sm sj-suave text-center").mark("erro-texto")
        with ui.link(target=rotas.INICIO).classes("flex items-center gap-1 text-primary").mark(
            "erro-voltar"
        ):
            ui.icon("arrow_back")
            ui.label("Voltar ao início")


def _pagina_de_erro_inesperado() -> None:
    # Sem a exceção como argumento, de propósito: a página não tem o que fazer
    # com ela, e o NiceGUI já a loga logo em seguida (`handle_exception`).
    conteudo_de_erro(TITULO_ERRO_INESPERADO, TEXTO_ERRO_INESPERADO)


def _pagina_isolada(request: Request, titulo: str, texto: str, status: int) -> Response:
    with Client(ui.page(""), request=request) as cliente:
        conteudo_de_erro(titulo, texto)
    return cliente.build_response(request, status)


async def _resposta_nao_encontrada(request: Request, _exc: Exception) -> Response:
    # Sem log do endereço: a query string de um link digitado pode trazer
    # qualquer coisa, e um 404 não é falha do sistema.
    return _pagina_isolada(request, TITULO_NAO_ENCONTRADA, TEXTO_NAO_ENCONTRADA, 404)


async def _resposta_erro_inesperado(request: Request, _exc: Exception) -> Response:
    # O traceback sai pelo servidor: o Starlette relança a exceção depois de
    # usar esta resposta, e o uvicorn a loga.
    return _pagina_isolada(request, TITULO_ERRO_INESPERADO, TEXTO_ERRO_INESPERADO, 500)


def _avisar_acao_nao_concluida() -> None:
    """Chamado pelo NiceGUI em toda exceção não tratada, depois do log. Só
    avisa quando há uma página aberta para receber a notificação. Exceção de
    tarefa de fundo não tem página, e a da montagem já vira página de erro.
    O aviso nunca pode levantar: isso esconderia a exceção original."""
    with contextlib.suppress(Exception):
        cliente = ui.context.client
        if cliente.has_socket_connection:
            ui.notify(MENSAGEM_ACAO_NAO_CONCLUIDA, type="negative")
