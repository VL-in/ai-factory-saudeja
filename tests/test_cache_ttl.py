"""
SaúdeJá — testes do cache com prazo de validade da interface (src/ui/cache_ttl.py).

É o que sustenta duas regras do `architecture.md` §3.1: o nome de um paciente
eliminado fica no máximo 60 s na memória do Space, e `acao="carregar_fila"`
mede só as idas ao banco (no máximo uma por data por prazo, qualquer que seja o
número de abas).
"""
import threading
import time
from datetime import date

import pytest

from ui.cache_ttl import CacheTTL


class Relogio:
    def __init__(self) -> None:
        self.agora = 1000.0

    def __call__(self) -> float:
        return self.agora


def _carga_contada(chamadas: list, valor):
    def carregar():
        chamadas.append(valor)
        return valor

    return carregar


def test_guarda_dentro_do_prazo_e_volta_a_carregar_depois_dele():
    relogio = Relogio()
    cache = CacheTTL(ttl_s=60, max_entradas=7, relogio=relogio)
    chamadas: list = []

    assert cache.obter(date(2026, 11, 7), _carga_contada(chamadas, "fila")) == "fila"
    relogio.agora += 59
    assert cache.obter(date(2026, 11, 7), _carga_contada(chamadas, "fila")) == "fila"
    assert len(chamadas) == 1

    relogio.agora += 1
    cache.obter(date(2026, 11, 7), _carga_contada(chamadas, "fila nova"))
    assert chamadas == ["fila", "fila nova"]


def test_entrada_vencida_sai_da_memoria_na_poda_sem_ninguem_ler():
    """O prazo é de memória, não só de leitura: o nome de um paciente
    eliminado não pode continuar no processo esperando a próxima leitura."""
    relogio = Relogio()
    cache = CacheTTL(ttl_s=60, max_entradas=7, relogio=relogio)
    cache.obter("a", lambda: "nome do paciente")

    relogio.agora += 60

    assert cache.podar() == 1
    assert "a" not in cache
    assert len(cache) == 0


def test_guarda_no_maximo_sete_datas_e_descarta_a_mais_antiga():
    cache = CacheTTL(ttl_s=60, max_entradas=7, relogio=Relogio())
    for dia in range(1, 9):
        cache.obter(date(2026, 11, dia), lambda dia=dia: dia)

    assert len(cache) == 7
    assert date(2026, 11, 1) not in cache
    assert date(2026, 11, 8) in cache


def test_invalida_uma_data_ou_todas():
    cache = CacheTTL(ttl_s=60, max_entradas=7, relogio=Relogio())
    cache.obter("a", lambda: 1)
    cache.obter("b", lambda: 2)

    cache.invalidar("a")
    assert "a" not in cache and "b" in cache

    cache.invalidar()
    assert len(cache) == 0


def test_excecao_nao_fica_guardada():
    """Banco fora do ar não prende o erro na tela por um prazo inteiro."""
    cache = CacheTTL(ttl_s=60, max_entradas=7, relogio=Relogio())

    def falhar():
        raise RuntimeError("fora do ar")

    with pytest.raises(RuntimeError):
        cache.obter("a", falhar)
    assert "a" not in cache
    assert cache.obter("a", lambda: "voltou") == "voltou"


def test_duas_abas_ao_mesmo_tempo_fazem_uma_ida_ao_banco():
    cache = CacheTTL(ttl_s=60, max_entradas=7)
    chamadas = []

    def carregar_devagar():
        chamadas.append(1)
        time.sleep(0.2)
        return "fila"

    resultados = []
    threads = [
        threading.Thread(target=lambda: resultados.append(cache.obter("d", carregar_devagar)))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert resultados == ["fila"] * 4
    assert len(chamadas) == 1


@pytest.mark.parametrize(("ttl_s", "max_entradas"), [(0, 7), (60, 0)])
def test_prazo_e_tamanho_precisam_ser_positivos(ttl_s, max_entradas):
    with pytest.raises(ValueError):
        CacheTTL(ttl_s=ttl_s, max_entradas=max_entradas)
