"""
SaúdeJá — tela inicial (`/`): a porta de entrada pública.

Antes, `/` era direto o formulário do paciente, e a área da equipe ficava num
link discreto no rodapé dele. Agora quem chega pela URL do Space escolhe o
caminho: **paciente** (ciência, cadastro e agendamento, em `/paciente`, sem
login) ou **funcionário** (login e abas, em `/equipe`). As duas etapas seguintes
continuam como eram; esta tela só as apresenta.

Três decisões:

- **Nada de dado nesta página.** Nenhum campo, nenhuma leitura do banco: a
  ciência da Política e dos Termos continua sendo a primeira coisa da tela do
  paciente, antes de qualquer campo (ADR-010), e esta tela não muda isso.
- **A imagem de fundo é servida pela própria aplicação** (`estatico/`), como o
  favicon: nada de terceiros no navegador (ADR-013, `tema.py`). Ela entra por
  CSS constante, atrás de um véu na cor do fundo do tema, que deixa o texto
  legível no claro e no escuro e esmaece a foto (mais forte no celular, onde o
  texto cobre a foto inteira).
- **Os dois acessos são links, não botões com `ui.navigate`**: funcionam com
  Tab e Enter, abrem em nova aba com o botão do meio e não dependem do
  websocket para navegar.
"""
import mimetypes

from nicegui import app, ui

import documentos_legais
from web import rotas, tema
from web.paginas.documentos import ROTAS as ROTAS_DOS_DOCUMENTOS

ROTA = rotas.INICIO
URL_IMAGEM_DE_FUNDO = "/estatico/inicio-fundo.webp"
CAMINHO_IMAGEM_DE_FUNDO = tema.CAMINHO_FAVICON.parent / "inicio-fundo.webp"

FRASE_DE_VALOR = "Feito para o seu bem-estar"
SUBTITULO = "Agende sua consulta pela internet, em poucos passos e sem precisar de login."

# Véu sobre a foto: opaco à esquerda, onde fica o texto, e esmaecendo para a
# direita, onde está a foto. Usa as variáveis do tema, então troca sozinho no
# modo escuro. CSS constante (regra D7): nenhum dado entra aqui.
CSS_INICIO = (
    "<style>"
    ".sj-inicio{position:relative;isolation:isolate;overflow:hidden;display:flex;"
    "align-items:center;min-height:calc(100svh - 64px)}"
    f'.sj-inicio::before{{content:"";position:absolute;inset:0;z-index:-2;'
    f'background:url("{URL_IMAGEM_DE_FUNDO}") 70% center/cover no-repeat}}'
    '.sj-inicio::after{content:"";position:absolute;inset:0;z-index:-1;'
    "background:linear-gradient(90deg,var(--sj-fundo) 0%,"
    "color-mix(in srgb,var(--sj-fundo) 92%,transparent) 38%,"
    "color-mix(in srgb,var(--sj-fundo) 55%,transparent) 70%,"
    "color-mix(in srgb,var(--sj-fundo) 35%,transparent) 100%)}"
    "@media (max-width:767px){.sj-inicio::after{"
    "background:color-mix(in srgb,var(--sj-fundo) 86%,transparent)}}"
    ".sj-inicio-selo{display:inline-flex;align-items:center;gap:.4rem;padding:.3rem .75rem;"
    "border-radius:999px;font-size:.8rem;font-weight:600;color:var(--q-primary);"
    "background:color-mix(in srgb,var(--q-primary) 12%,var(--sj-superficie))}"
    ".sj-inicio-titulo{font-size:clamp(2.4rem,6vw,4rem);font-weight:700;line-height:1.05;"
    "letter-spacing:-.025em;max-width:13ch;margin:0}"
    ".sj-acesso{display:flex;align-items:center;gap:1rem;flex:1 1 260px;min-height:44px;"
    "padding:1.1rem 1.25rem;border-radius:1rem;text-decoration:none;"
    "border:1px solid var(--sj-borda);background:var(--sj-superficie);color:var(--sj-texto);"
    "box-shadow:0 1px 2px rgba(15,23,42,.06);transition:transform .15s,box-shadow .15s}"
    ".sj-acesso:hover{transform:translateY(-2px);box-shadow:0 10px 28px rgba(15,23,42,.14)}"
    ".sj-acesso:focus-visible{outline:3px solid var(--q-primary);outline-offset:3px}"
    ".sj-acesso--principal{background:var(--q-primary);border-color:transparent;color:#fff}"
    ".sj-acesso--principal .sj-acesso-ajuda{color:rgba(255,255,255,.88)}"
    ".sj-acesso-icone{display:grid;place-items:center;width:3rem;height:3rem;flex-shrink:0;"
    "border-radius:.8rem;background:color-mix(in srgb,currentColor 14%,transparent)}"
    ".sj-acesso-ajuda{color:var(--sj-suave)}"
    "@media (prefers-reduced-motion:reduce){.sj-acesso{transition:none}"
    ".sj-acesso:hover{transform:none}}"
    "</style>"
)


def registrar() -> None:
    # O `mimetypes` do Python 3.10 só conhece `.webp` se a máquina tiver um
    # `/etc/mime.types` que o liste, e a imagem `python:3.10-slim` não tem: a
    # foto sairia como `text/plain`.
    mimetypes.add_type("image/webp", ".webp")
    # Arquivo único, não a pasta: `estatico/` publicaria tudo o que fosse
    # posto nela, e aqui só a imagem do fundo é para o navegador.
    app.add_static_file(local_file=CAMINHO_IMAGEM_DE_FUNDO, url_path=URL_IMAGEM_DE_FUNDO)

    @ui.page(ROTA, title="SaúdeJá")
    async def pagina_inicio() -> None:
        tema.aplicar_tema()
        ui.add_head_html(CSS_INICIO)
        # Sem o respiro padrão do NiceGUI em volta do conteúdo: a foto vai de
        # borda a borda.
        ui.query(".nicegui-content").classes("p-0")
        await ui.context.client.connected()
        tema.cabecalho()
        with (
            ui.element("section").classes("sj-inicio w-full").mark("inicio"),
            ui.column().classes("w-full max-w-6xl mx-auto px-4 sm:px-8 py-12 gap-6"),
        ):
            with ui.element("div").classes("sj-inicio-selo"):
                ui.icon("favorite").classes("text-base")
                ui.label("SaúdeJá")
            ui.label(FRASE_DE_VALOR).classes("sj-inicio-titulo").mark("frase-de-valor")
            ui.label(SUBTITULO).classes("text-lg sj-suave max-w-md")
            with ui.row().classes("w-full max-w-2xl gap-4 mt-2"):
                _acesso(
                    rotas.PACIENTE,
                    icone="event_available",
                    titulo="Sou paciente",
                    ajuda="Agendar uma consulta",
                    marca="acesso-paciente",
                    principal=True,
                )
                _acesso(
                    rotas.EQUIPE,
                    icone="badge",
                    titulo="Sou funcionário",
                    ajuda="Entrar na área da equipe",
                    marca="acesso-equipe",
                )
            with ui.row().classes("gap-x-4 gap-y-1 mt-4 text-xs"):
                for documento in documentos_legais.DOCUMENTOS:
                    ui.link(
                        documentos_legais.TITULOS[documento],
                        ROTAS_DOS_DOCUMENTOS[documento],
                    ).classes("sj-suave")


def _acesso(
    destino: str, *, icone: str, titulo: str, ajuda: str, marca: str, principal: bool = False
) -> None:
    classes = "sj-acesso sj-acesso--principal" if principal else "sj-acesso"
    with ui.link(target=destino).classes(classes).mark(marca):
        with ui.element("span").classes("sj-acesso-icone"):
            ui.icon(icone).classes("text-2xl")
        with ui.column().classes("gap-0 grow"):
            ui.label(titulo).classes("text-lg font-semibold leading-tight")
            ui.label(ajuda).classes("text-sm sj-acesso-ajuda")
        ui.icon("arrow_forward").classes("text-xl")
