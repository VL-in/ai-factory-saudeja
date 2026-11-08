"""
SaúdeJá — visual da interface em NiceGUI: cores, tema escuro e peças comuns.

Três decisões (ADR-013):

- **Cores em variáveis CSS trocadas pela classe `body--dark`**, que é a que o
  Quasar põe no modo escuro. As variantes `dark:` do Tailwind não servem: dependem
  de uma classe `dark` que o Quasar não usa, e na prova de conceito o cabeçalho
  ficava branco com o texto também claro.
- **Fonte do sistema, nada de terceiros no navegador.** A prova de conceito
  carregava a Inter de `fonts.googleapis.com`, o que entregaria o IP de quem abre
  a tela a mais um operador. Vue, Quasar e os ícones vêm da própria aplicação.
- **Teal no lugar do vermelho padrão**, as mesmas cores da interface antiga
  (`#0F766E` no claro e `#0D9488` no escuro, para o contraste do texto branco nos
  botões): num contexto de saúde, botão principal vermelho é lido como erro.

Nada aqui recebe dado do banco ou do usuário como HTML (regra D7 da migração):
o CSS é constante, e os textos vão por `ui.label`, que escapa o conteúdo.
"""
from pathlib import Path

from nicegui import ui

from web import sessao

COR_PRIMARIA = "#0F766E"
COR_PRIMARIA_ESCURO = "#0D9488"
CAMINHO_FAVICON = Path(__file__).resolve().parent / "estatico" / "favicon.svg"

CSS_TEMA = """
<style>
:root{--sj-fundo:#f8fafc;--sj-superficie:#ffffff;--sj-borda:#e2e8f0;--sj-texto:#0f172a;
  --sj-suave:#64748b;--sj-aviso-fundo:#fffbeb;--sj-aviso-texto:#78350f;
  --sj-info-fundo:#eff6ff;--sj-info-texto:#1e3a8a;--sj-erro-fundo:#fef2f2;--sj-erro-texto:#991b1b;
  --sj-ok-fundo:#f0fdf4;--sj-ok-texto:#14532d}
body.body--dark{--sj-fundo:#0f172a;--sj-superficie:#1e293b;--sj-borda:#334155;--sj-texto:#f1f5f9;
  --sj-suave:#94a3b8;--sj-aviso-fundo:#422006;--sj-aviso-texto:#fde68a;
  --sj-info-fundo:#172554;--sj-info-texto:#bfdbfe;--sj-erro-fundo:#450a0a;--sj-erro-texto:#fecaca;
  --sj-ok-fundo:#052e16;--sj-ok-texto:#bbf7d0;--q-primary:#0D9488 !important}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  background:var(--sj-fundo);color:var(--sj-texto)}
.sj-cabecalho{background:var(--sj-superficie)!important;color:var(--sj-texto)!important;
  border-bottom:1px solid var(--sj-borda)}
.q-card,.q-table__card,.q-table{background:var(--sj-superficie)!important;color:var(--sj-texto)}
.q-card{box-shadow:0 1px 2px rgba(15,23,42,.06);border:1px solid var(--sj-borda)}
.q-table th{font-weight:600;color:var(--sj-suave)}
.q-field__label,.q-field__native,.q-field__input{color:var(--sj-texto)}
.sj-suave{color:var(--sj-suave)}
.sj-aviso{background:var(--sj-aviso-fundo);color:var(--sj-aviso-texto)}
.sj-info{background:var(--sj-info-fundo);color:var(--sj-info-texto)}
.sj-erro{background:var(--sj-erro-fundo);color:var(--sj-erro-texto)}
.sj-ok{background:var(--sj-ok-fundo);color:var(--sj-ok-texto)}
.sj-icone{background:color-mix(in srgb,currentColor 14%,transparent)}
.sj-documento h1{font-size:1.6rem;font-weight:700;margin:0 0 .75rem}
.sj-documento h2{font-size:1.25rem;font-weight:600;margin:1.5rem 0 .5rem}
.sj-documento h3{font-size:1.05rem;font-weight:600;margin:1.25rem 0 .5rem}
.sj-documento table{border-collapse:collapse;margin:.75rem 0}
.sj-documento th,.sj-documento td{border:1px solid var(--sj-borda);padding:.35rem .5rem}
</style>
"""

# Tipo do aviso -> classe de cor e ícone. "erro" é falha do sistema; "aviso",
# recusa do dado ou atenção; "info", orientação; "ok", confirmação.
_AVISOS = {
    "erro": ("sj-erro", "error_outline"),
    "aviso": ("sj-aviso", "warning_amber"),
    "info": ("sj-info", "info"),
    "ok": ("sj-ok", "check_circle"),
}

# Ciclo do botão de tema: automático (segue o sistema) -> escuro -> claro.
_CICLO_DO_TEMA = {None: True, True: False, False: None}
_ROTULO_DO_TEMA = {None: "automático", True: "escuro", False: "claro"}
_ICONE_DO_TEMA = {None: "brightness_auto", True: "dark_mode", False: "light_mode"}


def aplicar_tema() -> None:
    """Antes de `await client.connected()`, para o CSS sair no HTML inicial e
    a página não piscar sem estilo."""
    ui.colors(primary=COR_PRIMARIA, secondary=COR_PRIMARIA_ESCURO)
    ui.add_head_html(CSS_TEMA)


def controle_de_tema() -> None:
    """Modo escuro da aba, lembrado entre as páginas dela. Depois da conexão:
    a preferência mora em `app.storage.tab`."""
    estado = sessao.aba()
    modo = ui.dark_mode(estado.get(sessao.CHAVE_TEMA))

    def alternar() -> None:
        modo.value = _CICLO_DO_TEMA[modo.value]
        estado[sessao.CHAVE_TEMA] = modo.value
        botao.props(f"icon={_ICONE_DO_TEMA[modo.value]}")
        dica.text = f"Tema: {_ROTULO_DO_TEMA[modo.value]}"

    botao = ui.button(icon=_ICONE_DO_TEMA[modo.value], on_click=alternar).props(
        'flat round dense aria-label="Alternar tema"'
    )
    with botao:
        dica = ui.tooltip(f"Tema: {_ROTULO_DO_TEMA[modo.value]}")


def cabecalho(subtitulo: str | None = None, email: str | None = None, ao_sair=None) -> None:
    """Título neutro, sem anunciar o risco de falta: a tela do paciente é a
    porta pública, e quem agenda não precisa saber que vai ser classificado."""
    with ui.header(elevated=False).classes("sj-cabecalho px-4 sm:px-6 py-3 items-center"):
        with ui.link(target="/").classes("no-underline text-inherit"), ui.row().classes(
            "items-center gap-2 no-wrap"
        ):
            ui.icon("medical_services", color="primary").classes("text-2xl")
            ui.label("SaúdeJá").classes("text-lg font-bold tracking-tight")
        if subtitulo:
            ui.label(f"·  {subtitulo}").classes("text-sm sj-suave max-sm:hidden")
        ui.space()
        with ui.row().classes("items-center gap-2 no-wrap"):
            if email:
                ui.label(email).classes("text-sm sj-suave max-sm:hidden")
            if ao_sair is not None:
                ui.button("Sair", icon="logout", on_click=ao_sair).props("flat dense no-caps")
            controle_de_tema()


def aviso(texto: str, tipo: str = "info") -> ui.row:
    classe, icone = _AVISOS[tipo]
    classes = f"{classe} w-full items-start gap-2 rounded-lg px-3 py-2 no-wrap"
    with ui.row().classes(classes) as linha:
        ui.icon(icone).classes("text-lg shrink-0")
        ui.label(texto).classes("text-sm whitespace-pre-line").mark(f"aviso-{tipo}")
    return linha


def cartao_metrica(rotulo: str, valor: object, icone: str, cor: str, ajuda: str | None = None):
    with ui.card().classes("flex-1 min-w-[160px] p-4 rounded-xl") as cartao:
        with ui.row().classes("items-center gap-3 no-wrap"):
            # sj-icone: fundo da própria cor do ícone a 14%, legível nos dois temas
            with ui.element("div").classes(f"rounded-lg p-2 sj-icone text-{cor}-600"):
                ui.icon(icone).classes("text-2xl")
            with ui.column().classes("gap-0"):
                ui.label(rotulo).classes("text-xs uppercase tracking-wide sj-suave")
                ui.label(str(valor)).classes("text-2xl font-semibold").mark(f"metrica-{rotulo}")
        if ajuda:
            ui.tooltip(ajuda)
    return cartao
