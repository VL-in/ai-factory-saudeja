"""
SaúdeJá — cache em memória com prazo de validade, sem dependência de interface.

Substitui o `st.cache_data` da interface antiga (ADR-013). Lá ele existia por
dois motivos, e só o segundo continua:

1. o `st.tabs` reexecutava todas as abas a cada clique, e a fila do dia ia ao
   banco a cada interação. No NiceGUI a tela não roda de novo a cada clique;
2. a fila carrega `nome_completo` (ADR-007), e o prazo do cache é por quanto
   tempo o nome de um paciente eliminado pode continuar na memória do Space
   (docs/architecture.md §3.1). **Esse motivo continua**, e é por ele que a
   entrada vencida sai da memória (`podar`), e não só deixa de ser servida.

Regras que vêm da tela antiga e os testes de `tests/test_cache_ttl.py` travam:

- **exceção não é guardada**: banco fora do ar é tentado de novo na leitura
  seguinte, em vez de prender o erro na tela por um prazo inteiro;
- **no máximo `max_entradas` chaves**, descartando a mais antiga;
- **invalidação explícita**, chamada pelas escritas da própria tela;
- **uma carga por chave de cada vez**: duas abas abrindo a mesma data juntas
  fazem uma ida ao banco, não duas. É o que mantém `acao="carregar_fila"`
  medindo as idas ao banco (no máximo uma por data por prazo), qualquer que
  seja o número de abas.

O relógio é injetável para os testes não dependerem de `sleep`.
"""
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

C = TypeVar("C", bound=Hashable)
V = TypeVar("V")


class CacheTTL(Generic[C, V]):
    def __init__(
        self,
        ttl_s: float,
        max_entradas: int,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_s <= 0 or max_entradas <= 0:
            raise ValueError("ttl_s e max_entradas precisam ser positivos")
        self.ttl_s = ttl_s
        self.max_entradas = max_entradas
        self._relogio = relogio
        self._entradas: OrderedDict[C, tuple[float, V]] = OrderedDict()
        self._trava = threading.Lock()
        self._travas_de_carga: dict[C, threading.Lock] = {}

    def obter(self, chave: C, carregar: Callable[[], V]) -> V:
        """Valor guardado e ainda válido, ou o resultado de `carregar()`, que
        passa a ser guardado. Uma exceção de `carregar` sobe sem deixar nada
        no cache."""
        with self._trava:
            self._podar_sem_trava(self._relogio())
            if chave in self._entradas:
                return self._entradas[chave][1]
            trava_da_chave = self._travas_de_carga.setdefault(chave, threading.Lock())

        with trava_da_chave:
            # Quem esperava a carga de outra aba encontra o valor pronto aqui.
            with self._trava:
                if chave in self._entradas:
                    return self._entradas[chave][1]
            valor = carregar()
            with self._trava:
                self._entradas[chave] = (self._relogio(), valor)
                self._entradas.move_to_end(chave)
                while len(self._entradas) > self.max_entradas:
                    self._entradas.popitem(last=False)
            return valor

    def invalidar(self, chave: C | None = None) -> None:
        """Sem chave, descarta tudo: uma escrita pela tela pode mudar a fila de
        outra data (o cadastro do paciente, por exemplo, cai na data da
        consulta, não na que a recepção está vendo)."""
        with self._trava:
            if chave is None:
                self._entradas.clear()
            else:
                self._entradas.pop(chave, None)
            self._descartar_travas_livres()

    def podar(self) -> int:
        """Tira da memória as entradas vencidas. Devolve quantas saíram."""
        with self._trava:
            return self._podar_sem_trava(self._relogio())

    def __len__(self) -> int:
        with self._trava:
            return len(self._entradas)

    def __contains__(self, chave: object) -> bool:
        with self._trava:
            return chave in self._entradas

    def _podar_sem_trava(self, agora: float) -> int:
        vencidas = [
            chave
            for chave, (guardado_em, _) in self._entradas.items()
            if agora - guardado_em >= self.ttl_s
        ]
        for chave in vencidas:
            del self._entradas[chave]
        self._descartar_travas_livres()
        return len(vencidas)

    def _descartar_travas_livres(self) -> None:
        """Sem isto, o dicionário de travas cresceria com cada data já aberta.
        No pior caso (uma trava descartada entre o `setdefault` de uma aba e o
        `with` dela), duas abas carregam a mesma data uma vez cada: uma ida a
        mais ao banco, nunca um valor errado."""
        for chave in [c for c, t in self._travas_de_carga.items() if not t.locked()]:
            if chave not in self._entradas:
                del self._travas_de_carga[chave]
