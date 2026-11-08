"""
SaúdeJá — o que a interface em NiceGUI guarda entre abas, no processo.

Os caches da interface antiga (`st.cache_data`/`st.cache_resource`) passam a
ser objetos explícitos, compartilhados por todas as abas:

- **fila do dia**: 60 s, até 7 datas (`ui.cache_ttl`). O prazo é o tempo
  máximo que o nome de um paciente eliminado fica na memória do Space
  (docs/architecture.md §3.1). As escritas da própria tela (desfecho, cadastro,
  job manual) invalidam o cache na hora; o que muda por fora (job D-2, purga
  LGPD) aparece em até 60 s. `acao="carregar_fila"` fica dentro do cache, em
  `logic`, e continua medindo só as idas ao banco;
- **resumo de observabilidade** e **status do banco**: 30 s, para o painel não
  repetir consultas de agregação a cada clique nem mentir por minutos depois de
  o banco cair;
- **cliente de predição**: um por backend, porque carrega modelo e
  TreeExplainer, e recarregar a cada predição custaria mais que a predição.

As funções daqui são síncronas e chamam o banco: a casca as executa por
`execucao.executar_em_thread`.
"""
import functools
from datetime import date

from ui import logic
from ui.cache_ttl import CacheTTL

TTL_FILA_DO_DIA_S = 60
MAX_DATAS_DA_FILA = 7
TTL_DIAGNOSTICO_S = 30

cache_da_fila: CacheTTL[date, list[logic.ItemFila]] = CacheTTL(
    ttl_s=TTL_FILA_DO_DIA_S, max_entradas=MAX_DATAS_DA_FILA
)
cache_da_observabilidade: CacheTTL[int, dict] = CacheTTL(ttl_s=TTL_DIAGNOSTICO_S, max_entradas=3)
cache_do_status_do_banco: CacheTTL[str, dict] = CacheTTL(ttl_s=TTL_DIAGNOSTICO_S, max_entradas=1)


def fila_do_dia(dia: date) -> list[logic.ItemFila]:
    """Exceção não é guardada: banco fora do ar é tentado de novo na leitura
    seguinte. `logic.buscar_fila_do_dia` é lido na hora da carga, então um
    teste que o troque por um falso é respeitado."""
    return cache_da_fila.obter(dia, lambda: logic.buscar_fila_do_dia(dia))


def invalidar_fila() -> None:
    """Depois de toda escrita feita pela tela que muda a fila, também quando
    ela falha: um erro depois do commit (timeout na resposta) deixaria a fila
    mostrando o estado anterior até o prazo. Vale para todas as abas."""
    cache_da_fila.invalidar()


def resumo_observabilidade(janela_horas: int) -> dict:
    return cache_da_observabilidade.obter(
        janela_horas, lambda: logic.resumo_observabilidade(janela_horas)
    )


def status_banco() -> dict:
    return cache_do_status_do_banco.obter("banco", logic.status_banco)


@functools.cache
def _construir_cliente(backend: str):
    return logic.obter_cliente(backend)


def cliente_de_predicao():
    return _construir_cliente(logic.backend_ativo())


def podar_caches() -> None:
    """Chamada a cada `TTL_FILA_DO_DIA_S` pelo `lifespan` da interface: sem
    ela, uma entrada vencida só sairia da memória na próxima leitura, e o
    prazo de 60 s do nome eliminado não valeria com ninguém olhando a fila."""
    for cache in (cache_da_fila, cache_da_observabilidade, cache_do_status_do_banco):
        cache.podar()


def limpar_caches() -> None:
    """Para os testes: cada um começa sem nada guardado por outro. O cliente
    de predição fica: é o modelo carregado, igual para todos."""
    for cache in (cache_da_fila, cache_da_observabilidade, cache_do_status_do_banco):
        cache.invalidar()
