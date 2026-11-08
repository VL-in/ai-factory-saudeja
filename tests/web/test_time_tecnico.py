"""
SaúdeJá — aba "Time técnico" na interface em NiceGUI (linhas T1 a T8 da matriz da
migração, mais o diagnóstico técnico fora da tela do paciente).
"""
from datetime import datetime, timezone

import pytest
from apoio_web import entrar_na_equipe, esperar_ate, textos
from nicegui import ElementFilter

from ui import logic
from web import sessao
from web.paginas import equipe, time_tecnico

SUBABAS_EM_PROD = list(time_tecnico.SUBABAS)


@pytest.fixture(autouse=True)
def sem_fila_nem_pedidos(monkeypatch):
    """As outras abas da área da equipe também montam; aqui elas ficam vazias."""
    monkeypatch.setattr(logic, "buscar_fila_do_dia", lambda dia: [])
    monkeypatch.setattr(logic, "listar_pedidos_da_clinica", lambda: [])


def _subabas(usuario) -> list[str]:
    return textos(usuario, marker="subaba")


async def test_abas_do_funcionario_existem_em_dev(usuario, monkeypatch):
    """T1. A rotina da recepção primeiro; o disparo manual do job como última
    subaba do Time técnico, só em dev."""
    monkeypatch.setenv("APP_ENV", "dev")
    await entrar_na_equipe(usuario)

    assert textos(usuario, marker="aba-principal") == list(equipe.ABAS_FUNCIONARIO)
    assert _subabas(usuario) == [*SUBABAS_EM_PROD, time_tecnico.SUBABA_DEV]


async def test_aba_de_dev_some_fora_do_ambiente_de_dev(usuario):
    """T2. O "Time técnico" continua em produção: é onde o painel do SLO de
    produção é lido (ADR-006)."""
    await entrar_na_equipe(usuario)

    assert _subabas(usuario) == SUBABAS_EM_PROD
    await usuario.should_not_see("Disparar job D-2 agora")


@pytest.mark.parametrize("app_env", [None, ""])
async def test_sem_app_env_declarado_a_aba_de_dev_nao_aparece(usuario, monkeypatch, app_env):
    """T3. O padrão é produção: a aba de dev dispara o job sem a pré-checagem
    do campeão, e um ambiente que esqueceu de declarar APP_ENV não a ganha."""
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)
    await entrar_na_equipe(usuario)

    assert _subabas(usuario) == SUBABAS_EM_PROD


async def test_diagnostico_tecnico_so_aparece_na_aba_de_dev(usuario, monkeypatch):
    """APP_ENV, backend e status do Supabase são diagnóstico de quem mantém o
    sistema: na interface antiga, chegaram a ficar na barra lateral, à vista de
    qualquer paciente."""
    await usuario.open("/")
    await usuario.should_not_see("APP_ENV")
    await usuario.should_not_see("Supabase")

    await entrar_na_equipe(usuario)
    await usuario.should_not_see("APP_ENV")

    monkeypatch.setenv("APP_ENV", "dev")
    await usuario.open("/equipe")
    await usuario.should_see(marker="diagnostico", retries=20)
    assert "APP_ENV: dev" in textos(usuario, marker="diagnostico")[0]
    await usuario.should_see("Supabase: não configurado")


async def test_observabilidade_sem_supabase_avisa_sem_derrubar_a_tela(usuario):
    """T4. Diagnóstico passivo: sem Supabase, avisa e o resto da tela segue."""
    await entrar_na_equipe(usuario)

    await usuario.should_see("Observabilidade indisponível", retries=20)
    await usuario.should_see(marker="prever")


async def test_observabilidade_com_numeros_acusa_violacao_de_slo(usuario, monkeypatch):
    """T5. p95 acima de 2 s e predição sem explicação são as duas violações
    que o painel tem de gritar."""
    resumo = {
        "janela_horas": 24,
        "predicoes": 40,
        "p50_ms": 900.0,
        "p95_ms": 2500.0,
        "erros": 2,
        "por_origem": {"job": 30, "processo": 10},
        "cobertura_explicacao": {"predicoes": 30, "com_explicacao": 29, "percentual": 29 / 30},
        "ultimo_job_d2": datetime(2026, 10, 2, 8, 17, tzinfo=timezone.utc),
        "truncado": True,
    }
    monkeypatch.setattr(logic, "resumo_observabilidade", lambda janela_horas: resumo)
    await entrar_na_equipe(usuario)

    await usuario.should_see(marker="metrica-p95-de-latência", retries=20)
    assert textos(usuario, marker="metrica-p95-de-latência") == ["2500 ms"]
    assert textos(usuario, marker="metrica-Cobertura-de-explicação-(SLO-§4)") == ["97%"]
    erros = " ".join(textos(usuario, marker="aviso-erro"))
    assert "SLO §2" in erros and "SLO §4" in erros
    assert any("truncada" in t for t in textos(usuario, marker="aviso-aviso"))


async def test_aba_dev_dispara_job_e_mostra_erro_amigavel_sem_supabase(usuario, monkeypatch):
    """T6, sem banco: o botão chama o job de verdade, e a falha vira aviso."""
    monkeypatch.setenv("APP_ENV", "dev")
    await entrar_na_equipe(usuario)

    usuario.find(marker="disparar-job").click()

    await usuario.should_see("Falha ao rodar o job", retries=40)


async def test_aba_dev_com_job_bem_sucedido_mostra_os_contadores(usuario, monkeypatch):
    """T6, com o job falso bem-sucedido."""
    monkeypatch.setenv("APP_ENV", "dev")
    resultado = {
        "agendamentos_encontrados": 5,
        "predicoes_gravadas": 5,
        "mensagens_disparadas": 2,
        "erros": [],
    }
    monkeypatch.setattr(logic, "disparar_job_diario", lambda dia=None: resultado)
    await entrar_na_equipe(usuario)

    usuario.find(marker="disparar-job").click()

    await usuario.should_see("Job concluído sem erros.", retries=20)
    assert textos(usuario, marker="metrica-Mensagens-disparadas") == ["2"]


async def test_formulario_produz_predicao_e_explicacao(usuario):
    """T7. Caminho feliz pela tela, no backend em processo (o par REST é
    coberto por tests/test_ui_logic.py com TestClient)."""
    await entrar_na_equipe(usuario)

    usuario.find(marker="prever").click()

    await esperar_ate(lambda: _resultado(usuario) is not None, timeout=60)
    resultado = _resultado(usuario)
    assert 0.0 <= resultado.probabilidade <= 1.0
    assert resultado.explicacao  # SLO §4
    await usuario.should_see(marker="metrica-Probabilidade-de-no-show", retries=20)


async def test_explicabilidade_mostra_fatores_em_portugues_na_mesma_acao(usuario):
    """T8. A explicação aparece com a predição, sem precisar recarregar, em
    português e sem log-odds na lista principal."""
    await entrar_na_equipe(usuario)

    usuario.find(marker="prever").click()

    await esperar_ate(lambda: _resultado(usuario) is not None, timeout=60)
    await usuario.should_see(marker="principais-fatores", retries=20)
    motivos = textos(usuario, marker="motivo")
    assert motivos
    assert not any("historico_noshow" in m or "log-odds" in m for m in motivos)
    await usuario.should_see("Detalhes técnicos")


async def test_sair_descarta_a_predicao_de_verdade(usuario):
    """Complemento do E6 com a predição real: quem entra depois na mesma aba
    não herda a explicação."""
    await entrar_na_equipe(usuario)
    usuario.find(marker="prever").click()
    await esperar_ate(lambda: _resultado(usuario) is not None, timeout=60)

    usuario.find("Sair").click()

    await usuario.should_see(marker="login-email", retries=20)
    assert _resultado(usuario) is None


def _resultado(usuario):
    with usuario:
        return sessao.aba().get(sessao.CHAVE_RESULTADO)


def _definir_valor(usuario, marker, valor) -> None:
    with usuario:
        (elemento,) = ElementFilter(marker=marker)
        elemento.value = valor


async def test_testar_predicao_recusa_o_que_o_job_mandaria_para_a_quarentena(usuario):
    """O formulário aplica o mesmo contrato do job D-2: sem predição sobre
    distância negativa, mesmo sem o `blur` que limitaria o campo."""
    await entrar_na_equipe(usuario)
    _definir_valor(usuario, "teste-distancia", -3.0)

    usuario.find(marker="prever").click()

    await usuario.should_see("Não foi possível prever com estes dados", retries=60)
    assert "distancia_km" in " ".join(textos(usuario, marker="aviso-aviso"))
    assert _resultado(usuario) is None


async def test_testar_predicao_avisa_quando_o_modelo_nao_viu_o_dado_no_treino(usuario):
    """Fora do domínio do treino é predito e marcado, como no job."""
    await entrar_na_equipe(usuario)
    _definir_valor(usuario, "teste-distancia", 120.0)

    usuario.find(marker="prever").click()

    await esperar_ate(lambda: _resultado(usuario) is not None, timeout=60)
    assert _resultado(usuario).campos_fora_do_dominio == ("distancia_km",)
    await usuario.should_see("Previsão incerta", retries=20)
    assert any("Distância até a clínica" in t for t in textos(usuario, marker="aviso-aviso"))
