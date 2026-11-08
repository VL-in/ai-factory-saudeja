"""
SaúdeJá — área da equipe na interface em NiceGUI: login e "Fila do dia".

Substitui os testes de login e da fila de `tests/test_ui_smoke.py` (linhas E1 a
E16 da matriz da migração). O banco é trocado por funções falsas em
`ui.logic`, o mesmo módulo que a casca chama; sem elas, o Supabase fica
"não configurado" (fixture `ambiente_da_interface`), como nos testes antigos.
"""
import asyncio
import dataclasses
import threading
import time
from datetime import datetime, timedelta, timezone

from apoio_web import entrar_na_equipe, sessao_de_teste, textos
from nicegui import ElementFilter, ui

from ui import logic
from web import dados, sessao
from web.paginas import equipe


def _item_fila(n, probabilidade, classe, **kwargs):
    campos = {
        "id_agendamento": f"ag-{n}",
        "id_paciente_externo": f"{n:064x}",
        "especialidade": "cardiologia",
        "data_hora_agendada": datetime(2026, 10, 2, 9 + n, 0),
        "probabilidade": probabilidade,
        "classe_prevista": classe,
        "explicacao": [{"feature": "historico_noshow", "valor": 3, "contribuicao": 0.8}],
        "model_version": "abc123",
    }
    return logic.ItemFila(**{**campos, **kwargs})


def _contar_buscas(monkeypatch, fila=None):
    chamadas = []

    def _buscar(dia):
        chamadas.append(dia)
        return list(fila if fila is not None else [_item_fila(0, 0.81, 1)])

    monkeypatch.setattr(logic, "buscar_fila_do_dia", _buscar)
    return chamadas


def _abas(usuario) -> list[str]:
    return textos(usuario, marker="aba-principal")


# --- login (E1 a E7) ----------------------------------------------------------------


async def test_equipe_sem_login_mostra_so_o_formulario_de_login(usuario):
    """E1. A "Fila do dia" carrega nome de paciente (ADR-007): sem login,
    nenhuma aba é montada, nem escondida."""
    await usuario.open("/equipe")

    await usuario.should_see(marker="login-email")
    await usuario.should_see(marker="login-senha")
    assert _abas(usuario) == []
    await usuario.should_not_see("Fila do dia")


async def test_login_sem_supabase_configurado_mostra_erro_amigavel(usuario):
    """E2."""
    await usuario.open("/equipe")
    usuario.find(marker="login-email").type("recepcao@clinica.test")
    usuario.find(marker="login-senha").type("senha-qualquer")
    usuario.find(marker="login-entrar").click()

    await usuario.should_see("Não foi possível entrar", retries=20)
    assert textos(usuario, marker="aviso-erro")
    assert _abas(usuario) == []


async def test_login_com_campos_vazios_avisa_sem_chamar_o_supabase(usuario):
    """E3. O aviso é de preenchimento, não falha do sistema."""
    await usuario.open("/equipe")
    usuario.find(marker="login-entrar").click()

    await usuario.should_see("Informe e-mail e senha", retries=20)
    assert textos(usuario, marker="aviso-erro") == []


async def test_login_bem_sucedido_libera_as_abas(usuario, monkeypatch):
    """E4."""
    monkeypatch.setattr(logic, "autenticar_funcionario", lambda email, senha: sessao_de_teste())
    _contar_buscas(monkeypatch)
    await usuario.open("/equipe")
    usuario.find(marker="login-email").type("recepcao@clinica.test")
    usuario.find(marker="login-senha").type("senha-correta")
    usuario.find(marker="login-entrar").click()

    await usuario.should_see(kind=ui.tabs, retries=20)
    assert _abas(usuario) == list(equipe.ABAS_FUNCIONARIO)
    await usuario.should_see(kind=ui.label, content="recepcao@clinica.test")
    await usuario.should_not_see(marker="login-email")


async def test_tela_do_paciente_nao_exige_login(usuario):
    """E5. O autoagendamento continua aberto: a porta é só na frente dos dados
    da clínica."""
    await usuario.open("/paciente")

    await usuario.should_see("Cadastro e agendamento")
    await usuario.should_not_see(marker="login-email")


async def test_sair_encerra_a_sessao_e_descarta_a_ultima_predicao(usuario, monkeypatch):
    """E6. A última predição fica na aba para "Explicabilidade": quem entra
    depois na mesma aba não pode herdá-la."""
    _contar_buscas(monkeypatch)
    await entrar_na_equipe(usuario)
    with usuario:
        sessao.aba()[sessao.CHAVE_RESULTADO] = "predição de quem saiu"

    usuario.find("Sair").click()

    await usuario.should_see(marker="login-email", retries=20)
    with usuario:
        assert sessao.CHAVE_FUNCIONARIO not in sessao.aba()
        assert sessao.CHAVE_RESULTADO not in sessao.aba()
    assert _abas(usuario) == []


async def test_sessao_inativa_volta_ao_login_na_proxima_acao(usuario, monkeypatch):
    """E7, pela ação: a sessão parada há mais de 30 min é encerrada no clique."""
    _contar_buscas(monkeypatch)
    await entrar_na_equipe(usuario)
    parada = datetime.now(timezone.utc) - logic.INATIVIDADE_MAXIMA - timedelta(minutes=1)
    with usuario:
        sessao.aba()[sessao.CHAVE_FUNCIONARIO] = sessao_de_teste(ultimo_uso=parada)

    usuario.find(marker="atualizar-fila").click()

    await usuario.should_see("sem uso", retries=20)
    await usuario.should_see(marker="login-email")
    assert _abas(usuario) == []


async def test_sessao_inativa_volta_ao_login_pelo_timer_sem_clique(usuario, monkeypatch):
    """E7, pelo timer: a tela parada com nome de paciente volta ao login mesmo
    sem ninguém clicar (o Streamlit só percebia no clique seguinte)."""
    monkeypatch.setattr(equipe, "INTERVALO_DA_TELA_S", 0.1)
    _contar_buscas(monkeypatch)
    await entrar_na_equipe(usuario)
    parada = datetime.now(timezone.utc) - logic.INATIVIDADE_MAXIMA - timedelta(minutes=1)
    with usuario:
        sessao.aba()[sessao.CHAVE_FUNCIONARIO] = sessao_de_teste(ultimo_uso=parada)

    await usuario.should_see("sem uso", retries=20)
    assert _abas(usuario) == []


async def test_abrir_a_pagina_conta_como_uso_e_renova_a_sessao(usuario, monkeypatch):
    _contar_buscas(monkeypatch)
    quase = datetime.now(timezone.utc) - logic.INATIVIDADE_MAXIMA + timedelta(minutes=1)
    await entrar_na_equipe(usuario, sessao_de_teste(ultimo_uso=quase))

    with usuario:
        assert sessao.aba()[sessao.CHAVE_FUNCIONARIO].ultimo_uso > quase


# --- Fila do dia (E8 a E16) -----------------------------------------------------------


async def test_fila_sem_supabase_configurado_mostra_erro_amigavel(usuario):
    """E8."""
    await entrar_na_equipe(usuario)

    await usuario.should_see("Não foi possível consultar a fila", retries=20)


async def test_fila_com_agendamentos_mostra_os_contadores(usuario, monkeypatch):
    """E9."""
    fila = [
        _item_fila(0, 0.81, 1, nome_completo="Paciente Teste Um"),
        _item_fila(1, 0.12, 0, fora_do_dominio=True),
        logic.ItemFila(
            id_agendamento="ag-2",
            id_paciente_externo="f" * 64,
            especialidade="dermatologia",
            data_hora_agendada=datetime(2026, 10, 2, 15, 0),
            probabilidade=None,
            classe_prevista=None,
        ),
    ]
    _contar_buscas(monkeypatch, fila)
    await entrar_na_equipe(usuario)

    await usuario.should_see(marker="metrica-Agendamentos", retries=20)
    assert textos(usuario, marker="metrica-Agendamentos") == ["3"]
    assert textos(usuario, marker="metrica-Alto-risco") == ["1"]
    assert textos(usuario, marker="metrica-Sem-predição") == ["1"]
    await usuario.should_not_see("Não foi possível consultar a fila")
    await usuario.should_see("Paciente pediu revisão da decisão de lembrete?")


async def test_duas_abas_e_varias_acoes_na_mesma_data_vao_uma_vez_ao_banco(
    usuario, outra_aba, monkeypatch
):
    """E10. O cache é do processo: a mesma data dentro de 60 s, em qualquer aba
    e com qualquer número de cliques, é uma ida ao banco (e um ponto em
    `acao="carregar_fila"`)."""
    chamadas = _contar_buscas(monkeypatch)
    await entrar_na_equipe(usuario)
    await usuario.should_see(marker="tabela-fila", retries=20)
    for _ in range(3):
        usuario.find(marker="atualizar-fila").click()
    await entrar_na_equipe(outra_aba)
    await outra_aba.should_see(marker="tabela-fila", retries=20)

    assert len(chamadas) == 1


async def test_escrita_pela_tela_invalida_a_fila_e_o_aviso_some_depois(usuario, monkeypatch):
    """E11 e E14. O desfecho grava no banco: a fila é lida de novo na hora,
    sem esperar o prazo do cache. A confirmação é uma notificação, e não fica
    na tela depois da recarga."""
    chamadas = _contar_buscas(monkeypatch)
    registrados = []
    monkeypatch.setattr(
        logic,
        "atualizar_status_agendamento",
        lambda id_, status, data_hora_agendada=None: registrados.append((id_, status)),
    )
    await entrar_na_equipe(usuario)
    await usuario.should_see(marker="tabela-fila", retries=20)

    usuario.find(marker="tabela-fila").trigger("rowClick", [{}, {"id": "ag-0"}, 0])
    await usuario.should_see(marker="detalhe-paciente", retries=20)
    usuario.find("Paciente faltou").click()

    await usuario.should_see("Desfecho registrado: paciente faltou.", retries=20)
    assert registrados == [("ag-0", logic.STATUS_NO_SHOW)]
    assert len(chamadas) == 2
    with usuario:
        persistentes = ElementFilter(kind=ui.label, content="Desfecho registrado")
        assert list(persistentes) == []


async def test_falha_do_banco_no_desfecho_avisa_e_ainda_invalida_a_fila(usuario, monkeypatch):
    chamadas = _contar_buscas(monkeypatch)

    def _falhar(*_args, **_kwargs):
        raise logic.ErroPersistencia(
            f"Não foi possível registrar o desfecho. {logic.MENSAGEM_TENTE_DE_NOVO}",
            detalhe="timeout depois do commit",
        )

    monkeypatch.setattr(logic, "atualizar_status_agendamento", _falhar)
    await entrar_na_equipe(usuario)
    await usuario.should_see(marker="tabela-fila", retries=20)
    usuario.find(marker="tabela-fila").trigger("rowClick", [{}, {"id": "ag-0"}, 0])
    await usuario.should_see(marker="detalhe-paciente", retries=20)

    usuario.find("Cancelada").click()

    await usuario.should_see("Não foi possível registrar o desfecho", retries=20)
    await usuario.should_not_see("timeout depois do commit")
    assert len(chamadas) == 2


async def test_falha_do_banco_na_fila_nao_fica_guardada(usuario, monkeypatch):
    """E12. Banco fora do ar não prende o erro na tela por um prazo inteiro: o
    "Atualizar" tenta de novo."""
    respostas = [
        logic.ErroPersistencia(
            f"Não foi possível consultar a fila do dia. {logic.MENSAGEM_TENTE_DE_NOVO}",
            detalhe="fora do ar",
        ),
        [_item_fila(0, 0.81, 1)],
    ]

    def _buscar(dia):
        resposta = respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    monkeypatch.setattr(logic, "buscar_fila_do_dia", _buscar)
    await entrar_na_equipe(usuario)
    await usuario.should_see("Não foi possível consultar a fila", retries=20)

    usuario.find(marker="atualizar-fila").click()

    await usuario.should_see(marker="metrica-Agendamentos", retries=20)
    assert textos(usuario, marker="metrica-Agendamentos") == ["1"]
    await usuario.should_not_see("Não foi possível consultar a fila")


async def test_fila_mostra_status_e_previsao_incerta_em_linguagem_da_recepcao(
    usuario, monkeypatch
):
    """E13."""
    _contar_buscas(
        monkeypatch, [_item_fila(0, 0.81, 1, fora_do_dominio=True, status=logic.STATUS_NO_SHOW)]
    )
    await entrar_na_equipe(usuario)
    await usuario.should_see(marker="tabela-fila", retries=20)

    with usuario:
        (tabela,) = ElementFilter(marker="tabela-fila")
        linha = tabela.rows[0]
        rotulos = [coluna["label"] for coluna in tabela.columns]
    assert linha["status"] == "Faltou"
    assert linha["incerto"] is True
    assert "Fora do domínio" not in rotulos
    assert "Previsão incerta" in tabela.slots["body-cell-risco"].template


async def test_detalhe_mostra_os_fatores_em_portugues_e_sem_log_odds(usuario, monkeypatch):
    _contar_buscas(monkeypatch)
    await entrar_na_equipe(usuario)
    await usuario.should_see(marker="tabela-fila", retries=20)

    usuario.find(marker="tabela-fila").trigger("rowClick", [{}, {"id": "ag-0"}, 0])

    await usuario.should_see(marker="motivo", retries=20)
    assert textos(usuario, marker="motivo") == ["Faltas anteriores: aumenta o risco de falta"]
    await usuario.should_see("Detalhes técnicos")
    await usuario.should_not_see("historico_noshow")


async def test_consulta_futura_nao_oferece_registro_de_desfecho(usuario, monkeypatch):
    futura = datetime.now() + timedelta(days=3)
    _contar_buscas(monkeypatch, [_item_fila(0, 0.81, 1, data_hora_agendada=futura)])
    await entrar_na_equipe(usuario)
    await usuario.should_see(marker="tabela-fila", retries=20)

    usuario.find(marker="tabela-fila").trigger("rowClick", [{}, {"id": "ag-0"}, 0])

    await usuario.should_see("só pode ser registrado no dia da consulta", retries=20)
    await usuario.should_not_see("Paciente faltou")


async def test_desfecho_ja_registrado_fica_desabilitado(usuario, monkeypatch):
    _contar_buscas(monkeypatch, [_item_fila(0, 0.81, 1, status=logic.STATUS_CONCLUIDO)])
    await entrar_na_equipe(usuario)
    await usuario.should_see(marker="tabela-fila", retries=20)

    usuario.find(marker="tabela-fila").trigger("rowClick", [{}, {"id": "ag-0"}, 0])
    await usuario.should_see("Consulta realizada", retries=20)

    with usuario:
        (realizada,) = ElementFilter(kind=ui.button, content="Consulta realizada")
        (faltou,) = ElementFilter(kind=ui.button, content="Paciente faltou")
        assert not realizada.enabled and faltou.enabled


async def test_trocar_a_data_le_a_fila_da_data_nova(usuario, monkeypatch):
    chamadas = _contar_buscas(monkeypatch)
    await entrar_na_equipe(usuario)
    await usuario.should_see(marker="tabela-fila", retries=20)

    with usuario:
        (calendario,) = ElementFilter(marker="data-da-fila")
        calendario.value = "2026-10-02"
    await usuario.should_see("02/10/2026", retries=20)

    assert chamadas[-1].isoformat() == "2026-10-02"


async def test_tela_aberta_recarrega_a_fila_do_cache_sem_renovar_a_sessao(usuario, monkeypatch):
    """E15. O timer de 60 s relê a fila (do cache, se válido), para a tela
    parada não segurar o nome de um paciente eliminado por mais de 60 s. Um
    timer não é uso: a sessão continua contando a inatividade."""
    monkeypatch.setattr(equipe, "INTERVALO_DA_TELA_S", 0.1)
    chamadas = _contar_buscas(monkeypatch)
    await entrar_na_equipe(usuario)
    await usuario.should_see(marker="tabela-fila", retries=20)
    with usuario:
        uso_antes = sessao.aba()[sessao.CHAVE_FUNCIONARIO].ultimo_uso

    dados.cache_da_fila.invalidar()  # o prazo de 60 s venceu
    await asyncio.sleep(0.5)

    assert len(chamadas) >= 2
    with usuario:
        assert sessao.aba()[sessao.CHAVE_FUNCIONARIO].ultimo_uso == uso_antes


async def test_acao_lenta_nao_trava_as_outras_abas(usuario, outra_aba, monkeypatch):
    """E16. Com o banco lento, a aba que espera a fila não segura o event
    loop: a outra aba abre a tela do paciente nesse meio-tempo."""
    comecou, liberar = threading.Event(), threading.Event()

    def _buscar_devagar(dia):
        comecou.set()
        liberar.wait(timeout=5)
        return [_item_fila(0, 0.81, 1)]

    monkeypatch.setattr(logic, "buscar_fila_do_dia", _buscar_devagar)
    await usuario.open("/equipe")
    with usuario:
        sessao.entrar(sessao_de_teste())
    await usuario.open("/equipe", esperar=False)
    await asyncio.to_thread(comecou.wait, 5)

    inicio = time.monotonic()
    await outra_aba.open("/paciente")
    await outra_aba.should_see("Cadastro e agendamento")
    duracao = time.monotonic() - inicio

    liberar.set()
    await usuario.should_see(marker="tabela-fila", retries=30)
    assert duracao < 1.0


def test_sessao_expirou_nao_renova(monkeypatch):
    """O timer usa `sessao_expirou`, que só confere: renovar ali manteria a
    sessão viva para sempre com a tela aberta."""
    agora = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    funcionario = sessao_de_teste(ultimo_uso=agora - logic.INATIVIDADE_MAXIMA)
    estado = {sessao.CHAVE_FUNCIONARIO: funcionario}
    monkeypatch.setattr(sessao, "aba", lambda: estado)

    assert sessao.sessao_expirou(agora) is False
    assert sessao.sessao_expirou(agora + timedelta(seconds=1)) is True
    assert estado[sessao.CHAVE_FUNCIONARIO] is funcionario
    assert dataclasses.replace(funcionario) == funcionario


async def test_abrir_a_area_da_equipe_le_a_fila_uma_vez(usuario, monkeypatch):
    """O timer do NiceGUI dispara já na criação, por padrão; o da tela não
    pode, ou cada abertura da página iria duas vezes ao banco."""
    chamadas = _contar_buscas(monkeypatch)
    await entrar_na_equipe(usuario)
    await asyncio.sleep(0.2)

    assert len(chamadas) == 1
