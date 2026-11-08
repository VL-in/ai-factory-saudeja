"""
SaúdeJá — apoio aos testes da interface em NiceGUI: a aba simulada.

`UsuarioDaAba` mantém o mesmo `tab_id` entre páginas. O `User` do NiceGUI
sorteia um `tab_id` a cada `open()`; o navegador não faz isso: o id fica no
`sessionStorage` da aba e acompanha a navegação, e é por ele que a sessão da
equipe e a ciência do paciente (`app.storage.tab`) sobrevivem à troca de
página. Sem esta classe, o teste afirmaria o contrário do que acontece no
navegador (tests/web/test_simulador.py prova a diferença).
"""
from uuid import uuid4

import httpx
from nicegui import Client
from nicegui.nicegui import _on_handshake
from nicegui.testing.user import User


class UsuarioDaAba(User):
    """`User` do NiceGUI com um `tab_id` só, como uma aba de navegador."""

    def __init__(self, cliente_http: httpx.AsyncClient) -> None:
        super().__init__(cliente_http)
        self.tab_id = str(uuid4())

    async def open(self, path: str, *, clear_forward_history: bool = True) -> Client:
        resposta = await self.http_client.get(path, follow_redirects=True)
        assert resposta.status_code == 200, f"esperava 200, veio {resposta.status_code}"
        if resposta.headers.get("X-Nicegui-Content") != "page":
            raise ValueError(f"esperava uma página, veio {resposta.text[:200]}")
        client_id = resposta.text.split("'client_id': '", 1)[1].split("'", 1)[0]
        self.client = Client.instances[client_id]
        await _on_handshake(
            f"test-{uuid4()}",
            {"client_id": self.client.id, "tab_id": self.tab_id, "document_id": str(uuid4())},
        )
        self.back_history.append(path)
        if clear_forward_history:
            self.forward_history.clear()
        self._patch_outbox_emit_function()
        return self.client
