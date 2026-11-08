"""
SaúdeJá — apoio aos testes da interface em NiceGUI: a aba simulada.

`UsuarioDaAba` mantém o mesmo `tab_id` entre páginas. O `User` do NiceGUI
sorteia um `tab_id` a cada `open()`; o navegador não faz isso: o id fica no
`sessionStorage` da aba e acompanha a navegação, e é por ele que a sessão da
equipe e a ciência do paciente (`app.storage.tab`) sobrevivem à troca de
página. Sem esta classe, o teste afirmaria o contrário do que acontece no
navegador (tests/web/test_simulador.py prova a diferença).
"""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from nicegui import Client, ElementFilter
from nicegui.nicegui import _on_handshake
from nicegui.testing.user import User

from ui import logic
from web import sessao


class UsuarioDaAba(User):
    """`User` do NiceGUI com um `tab_id` só, como uma aba de navegador."""

    def __init__(self, cliente_http: httpx.AsyncClient) -> None:
        super().__init__(cliente_http)
        self.tab_id = str(uuid4())

    async def open(
        self, path: str, *, clear_forward_history: bool = True, esperar: bool = True
    ) -> Client:
        """Abre a página e, com `esperar`, só volta depois de ela terminar de
        montar. O NiceGUI devolve o HTML na hora e continua montando a página
        depois que o websocket conecta (`await client.connected()`); sem a
        espera, o teste procuraria elementos que ainda não existem."""
        resposta = await self.http_client.get(path, follow_redirects=True)
        assert resposta.status_code == 200, f"esperava 200, veio {resposta.status_code}"
        if resposta.headers.get("X-Nicegui-Content") != "page":
            raise ValueError(f"esperava uma página, veio {resposta.text[:200]}")
        client_id = resposta.text.split("'client_id': '", 1)[1].split("'", 1)[0]
        # O navegador descarta a página anterior ao navegar, e os timers dela
        # param. O `User` do NiceGUI a mantém viva, e o timer de uma página que
        # ninguém vê continuaria agindo sobre o `app.storage.tab` da aba.
        if self.client is not None and self.client.id != client_id:
            self.client.delete()
        self.client = Client.instances[client_id]
        await _on_handshake(
            f"test-{uuid4()}",
            {"client_id": self.client.id, "tab_id": self.tab_id, "document_id": str(uuid4())},
        )
        self.back_history.append(path)
        if clear_forward_history:
            self.forward_history.clear()
        self._patch_outbox_emit_function()
        if esperar:
            await _esperar_montagem_da_pagina(path)
        return self.client


def sessao_de_teste(ultimo_uso: datetime | None = None) -> logic.SessaoFuncionario:
    return logic.SessaoFuncionario(
        id_usuario="00000000-0000-0000-0000-000000000001",
        email="recepcao@clinica.test",
        ultimo_uso=ultimo_uso or datetime.now(timezone.utc),
    )


async def entrar_na_equipe(
    usuario: UsuarioDaAba, funcionario: logic.SessaoFuncionario | None = None
) -> None:
    """O mesmo estado que um login bem-sucedido deixa na aba (o login em si
    tem testes próprios). A página é reaberta na mesma aba, como o navegador
    faz ao recarregar."""
    await usuario.open("/equipe")
    with usuario:
        sessao.entrar(funcionario or sessao_de_teste())
    await usuario.open("/equipe")


def textos(usuario: UsuarioDaAba, marker: str | None = None, kind=None) -> list[str]:
    """Textos dos elementos com o marcador (ou do tipo) na página atual."""
    with usuario:
        return [
            getattr(elemento, "text", "") or elemento.props.get("label", "")
            for elemento in ElementFilter(marker=marker, kind=kind)
        ]


async def _esperar_montagem_da_pagina(rota: str, timeout: float = 10.0) -> None:
    """A tarefa de montagem leva o nome da rota (`page.py` do NiceGUI). Só a
    da rota aberta: a montagem lenta de outra aba não pode segurar esta."""
    nome = f'wait for result of page "{rota}"'
    pendentes = [
        tarefa for tarefa in asyncio.all_tasks() if tarefa.get_name() == nome and not tarefa.done()
    ]
    if pendentes:
        await asyncio.wait(pendentes, timeout=timeout)
