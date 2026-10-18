"""
SaúdeJá — alertas, políticas e painéis do Grafana como código.

Sem rede: os arquivos de `infra/grafana/` são validados como o script os
aplicaria, e o `aplicar` roda contra um Grafana falso em memória que registra
cada pedido. O que se prova:

1. Toda regra tem `severidade`, `area` e o filtro do ambiente; alerta só para
   produção.
2. Os limites vêm do `params.yaml` e das constantes do código, sem cópia.
3. O modo dev nunca toca a árvore de notificação de produção.
4. Configuração incompleta (contact point, fonte, preço) falha antes de
   qualquer escrita no stack.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import grafana_provisionar as gp

FONTES = {"supabase": "uid-supabase", "mimir": gp.DS_MIMIR_PADRAO, "loki": gp.DS_LOKI_PADRAO}


def _valores(ambiente: str = "prod", params: dict | None = None) -> dict:
    return gp.constantes(params or gp.carregar_params(), gp.AMBIENTES[ambiente], FONTES)


def _regras_cruas() -> list[tuple[str, dict]]:
    return [
        (arquivo.name, regra)
        for arquivo in sorted(gp.DIR_ALERTAS.glob("*.yaml"))
        for grupo in yaml.safe_load(arquivo.read_text(encoding="utf-8"))["groups"]
        for regra in grupo["rules"]
    ]


# --- os arquivos -------------------------------------------------------------------


def test_arquivos_validos_nos_dois_ambientes(capsys):
    assert gp.main(["validar"]) == 0


def test_toda_regra_tem_severidade_area_e_filtro_de_ambiente():
    """Alerta só para `prod`, conferido no arquivo
    versionado, antes de qualquer marcador ser trocado."""
    regras = _regras_cruas()
    assert len(regras) >= 15
    for arquivo, regra in regras:
        onde = f"{arquivo}::{regra['uid']}"
        rotulos = regra["labels"]
        assert rotulos["severidade"] in gp.SEVERIDADES, onde
        assert rotulos["area"] in gp.AREAS, onde
        assert rotulos["ambiente"] == "${AMBIENTE}", onde
        for consulta in regra["data"]:
            fonte = consulta["datasourceUid"]
            if fonte == gp.DS_EXPRESSAO:
                continue
            if fonte == "${DS_SUPABASE}":
                assert "observabilidade." in consulta["model"]["rawSql"], onde
            else:
                assert fonte in ("${DS_MIMIR}", "${DS_LOKI}"), onde
                assert 'deployment_environment="${AMBIENTE}"' in consulta["model"]["expr"], onde


def test_a_matriz_de_criticidade_tem_os_p1_esperados():
    """Os P1 da matriz que saem de regra do repositório (o do Space fora do ar
    é do Synthetic Monitoring, configurado à mão)."""
    severidades = {r["uid"]: r["labels"]["severidade"] for _a, r in _regras_cruas()}
    assert {
        "saudeja-fila-d2-nao-processada",
        "saudeja-fila-inteira-em-quarentena",
        "saudeja-agendamentos-esquecidos",
        "saudeja-pii-em-log",
        "saudeja-pii-em-eventos-app",
    } == {uid for uid, s in severidades.items() if s == "P1"}
    assert severidades["saudeja-disparos-modelo"] == "P2"
    assert severidades["saudeja-disparos-agenda"] == "P3"
    assert severidades["saudeja-custo-projetado"] == "P2"


@pytest.mark.parametrize("ambiente", sorted(gp.AMBIENTES))
def test_validacao_recusa_consulta_sem_filtro_de_ambiente(ambiente):
    """Controle negativo da validação que o `aplicar` faz antes de escrever."""
    valores = _valores(ambiente)
    grupos = gp.carregar_grupos(valores)
    metricas = next(g for g in grupos if g["name"] == "operacao-metricas")
    consulta = metricas["rules"][0]["data"][0]["model"]
    consulta["expr"] = consulta["expr"].replace(f'deployment_environment="{ambiente}"', "")
    with pytest.raises(gp.ErroDeProvisionamento, match="sem o filtro"):
        gp.validar_grupos(grupos, valores)


def test_validacao_recusa_uid_que_estoura_40_caracteres_com_o_sufixo_de_dev():
    """O Grafana recusa UID com mais de 40 caracteres, e o de dev ganha `-dev`:
    pegar isso no `validar` do CI, não no meio de um `aplicar`."""
    valores = _valores("prod")
    grupos = gp.carregar_grupos(valores)
    grupos[0]["rules"][0]["uid"] = "saudeja-" + "x" * 29  # 37 + "-dev" = 41
    with pytest.raises(gp.ErroDeProvisionamento, match="mais de 40 caracteres"):
        gp.validar_grupos(grupos, valores)
    grupos[0]["rules"][0]["uid"] = "saudeja-" + "x" * 28  # 36 + "-dev" = 40
    gp.validar_grupos(grupos, valores)


def test_validacao_recusa_rotulo_fora_da_matriz():
    valores = _valores()
    grupos = gp.carregar_grupos(valores)
    grupos[0]["rules"][0]["labels"]["severidade"] = "P0"
    with pytest.raises(gp.ErroDeProvisionamento, match="severidade"):
        gp.validar_grupos(grupos, valores)


def test_regras_e_paineis_usam_o_fuso_da_clinica(monkeypatch):
    """O dia das regras é o dia civil da clínica, o mesmo das views."""
    import config_projeto

    monkeypatch.delenv("TIMEZONE_CLINICA", raising=False)
    fuso = str(config_projeto.fuso_da_clinica())
    arquivos = [*gp.DIR_ALERTAS.glob("*.yaml"), *gp.DIR_PAINEIS.glob("*.json")]
    for arquivo in arquivos:
        fusos = set(re.findall(r"at time zone '([^']+)'", arquivo.read_text(encoding="utf-8")))
        assert fusos <= {fuso}, f"{arquivo.name} usa {fusos}"


# --- marcadores e limites -------------------------------------------------------------


def test_marcador_que_ocupa_a_string_vira_numero_e_dentro_de_texto_sem_ponto_zero():
    valores = {"N": 80.0, "X": 0.0291, "T": "prod"}
    assert gp.substituir(["${N}", "acima de ${N}", "${X}", "${T}"], valores) == [
        80.0, "acima de 80", 0.0291, "prod"
    ]


def test_marcador_desconhecido_e_erro():
    with pytest.raises(gp.ErroDeProvisionamento, match="NAO_EXISTE"):
        gp.substituir({"a": "x ${NAO_EXISTE}"}, {})


def _regra(valores: dict, uid: str) -> dict:
    return next(
        r for g in gp.carregar_grupos(valores) for r in g["rules"] if r["uid"] == uid
    )


def _limite(regra: dict):
    expressao = next(d for d in regra["data"] if d["refId"] == regra["condition"])["model"]
    return expressao["conditions"][0]["evaluator"]["params"][0]


def test_limites_vem_do_params_e_das_constantes_do_codigo():
    import alerta_observabilidade

    params = gp.carregar_params()
    valores = _valores(params=params)
    custo, obs = params["custo"], params["observabilidade"]

    regra_custo = _regra(valores, "saudeja-custo-projetado")
    assert _limite(regra_custo) == pytest.approx(
        obs["fracao_de_alerta"] * custo["orcamento_mensal_usd"]
    )
    assert repr(float(custo["sms_unitario_usd"])) in regra_custo["data"][0]["model"]["rawSql"]
    assert _limite(_regra(valores, "saudeja-job-d2-lento")) == (
        alerta_observabilidade.DURACAO_MAXIMA_JOB_D2_MS
    )
    fila = _regra(valores, "saudeja-fila-d2-nao-processada")
    assert f"time '{obs['fila_d2_horario_limite']}'" in fila["data"][0]["model"]["rawSql"]


def test_regra_de_pii_do_loki_e_a_do_filtro_de_log():
    import logging_config

    regra = _regra(_valores(), "saudeja-pii-em-log")
    assert f"|~ `{logging_config.regex_de_pii_re2()}`" in regra["data"][0]["model"]["expr"]


@pytest.mark.parametrize(
    "chave,valor",
    [
        ("sms_unitario_usd", None),
        ("orcamento_mensal_usd", "cem"),
        ("cambio_usd_brl", 0),
    ],
)
def test_custo_vazio_ou_invalido_falha_em_vez_de_criar_regra_muda(chave, valor):
    params = gp.carregar_params()
    params["custo"][chave] = valor
    with pytest.raises(gp.ErroDeProvisionamento, match=chave):
        _valores(params=params)


def test_horario_limite_fora_do_formato_falha():
    params = gp.carregar_params()
    params["observabilidade"]["fila_d2_horario_limite"] = "13h"
    with pytest.raises(gp.ErroDeProvisionamento, match="HH:MM"):
        _valores(params=params)


# --- painéis ----------------------------------------------------------------------


def _alvos(painel: dict) -> list[dict]:
    return [alvo for p in painel["panels"] for alvo in p.get("targets", [])]


def test_paineis_so_usam_as_fontes_do_ambiente_e_filtram_o_ambiente():
    for arquivo in sorted(gp.DIR_PAINEIS.glob("*.json")):
        painel = json.loads(arquivo.read_text(encoding="utf-8"))
        assert painel["uid"].startswith("saudeja-"), arquivo.name
        for alvo in _alvos(painel):
            fonte = alvo["datasource"]["uid"]
            assert fonte in ("${DS_SUPABASE}", "${DS_MIMIR}", "${DS_LOKI}"), arquivo.name
            if fonte == "${DS_SUPABASE}":
                assert "observabilidade." in alvo["rawSql"] or "select 0" in alvo["rawSql"]
            elif "probe_all_success" not in alvo["expr"]:
                # A sonda do Synthetic Monitoring se separa pelo nome do check.
                assert 'deployment_environment="${AMBIENTE}"' in alvo["expr"], arquivo.name


@pytest.mark.parametrize("ambiente", sorted(gp.AMBIENTES))
def test_paineis_saem_sem_marcador_e_com_uid_do_ambiente(ambiente):
    paineis = gp.carregar_paineis(_valores(ambiente), gp.AMBIENTES[ambiente])
    assert len(paineis) == 6
    texto = json.dumps(paineis, ensure_ascii=False)
    assert not gp._MARCADOR.search(texto)
    sufixo = gp.AMBIENTES[ambiente].sufixo_uid
    assert all(p["uid"].endswith(sufixo) and p["id"] is None for p in paineis)
    assert f'deployment_environment=\\"{ambiente}\\"' in texto


# --- aplicar contra um Grafana falso -------------------------------------------------

CONTATOS = ["saudeja-p1", "saudeja-p2", "saudeja-p2-modelo", "saudeja-p3", "saudeja-dev"]
ROTA_DE_TERCEIRO = {"receiver": "irm", "object_matchers": [["time", "=", "outro"]]}
ROTA_PROVISORIA_DO_SM = {
    "receiver": "saudeja-p1",
    "object_matchers": [["namespace", "=", "synthetic_monitoring"], ["job", "=", "SaudeJa prod"]],
}


class GrafanaFalso:
    """Registra cada pedido. Devolve o grupo de regras como foi gravado, menos
    o `notification_settings` quando `ignora_notificacao` (uma versão do
    Grafana que não conhecesse o campo)."""

    def __init__(
        self, contatos=CONTATOS, pasta_existe=False, grupos_existentes=(), ignora_notificacao=False
    ):
        self.pedidos: list[tuple[str, str, object]] = []
        self.contatos = contatos
        self.pasta_existe = pasta_existe
        self.grupos_existentes = list(grupos_existentes)
        self.ignora_notificacao = ignora_notificacao
        self.grupos_gravados: dict[str, dict] = {}

    def __call__(self, metodo, url, cabecalhos, corpo):
        assert cabecalhos["Authorization"] == "Bearer token-secreto"
        caminho = urlsplit(url).path
        dados = json.loads(corpo) if corpo else None
        self.pedidos.append((metodo, caminho, dados))
        if metodo == "PUT" and "/rule-groups/" in caminho:
            self.grupos_gravados[caminho] = dados
        if metodo == "GET":
            return self._get(caminho)
        if metodo == "POST" and caminho == "/api/v1/provisioning/mute-timings":
            return 201, "{}"
        return 200, "{}"

    def _get(self, caminho):
        if caminho.startswith("/api/datasources/uid/"):
            return 200, json.dumps({"name": "fonte"})
        if caminho == "/api/v1/provisioning/contact-points":
            return 200, json.dumps([{"name": n} for n in self.contatos])
        if caminho.startswith("/api/folders/"):
            return (200, "{}") if self.pasta_existe else (404, '{"message": "not found"}')
        if caminho == "/api/v1/provisioning/alert-rules":
            return 200, json.dumps(
                [{"folderUID": pasta, "ruleGroup": g} for pasta, g in self.grupos_existentes]
            )
        if caminho.startswith("/api/v1/provisioning/mute-timings/"):
            return 404, "{}"
        if "/rule-groups/" in caminho:
            grupo = json.loads(json.dumps(self.grupos_gravados[caminho]))
            if self.ignora_notificacao:
                for regra in grupo["rules"]:
                    regra.pop("notification_settings")
            return 200, json.dumps(grupo)
        if caminho == "/api/v1/provisioning/policies":
            return 200, json.dumps(
                {
                    "receiver": "grafana-default-email",
                    "group_by": ["grafana_folder", "alertname"],
                    "provenance": "",
                    "routes": [ROTA_PROVISORIA_DO_SM, ROTA_DE_TERCEIRO],
                }
            )
        raise AssertionError(f"GET inesperado: {caminho}")

    def escritas(self):
        return [(m, c, d) for m, c, d in self.pedidos if m != "GET"]


def _aplicar(falso: GrafanaFalso, ambiente: str = "prod") -> list[str]:
    grafana = gp.Grafana("https://stack.exemplo", "token-secreto", falso)
    return gp.aplicar(grafana, gp.AMBIENTES[ambiente], _valores(ambiente))


def test_aplicar_em_producao_cria_pasta_grupos_rotas_e_paineis():
    falso = GrafanaFalso(grupos_existentes=[("saudeja", "grupo-antigo"), ("outra", "x")])
    _aplicar(falso)
    escritas = falso.escritas()

    assert ("POST", "/api/folders", {"uid": "saudeja", "title": "SaúdeJá"}) in escritas
    grupos = {c.rsplit("/", 1)[1]: d for m, c, d in escritas if "/rule-groups/" in c and m == "PUT"}
    assert set(grupos) == {g["name"] for g in gp.carregar_grupos(_valores())}
    regra = grupos["operacao-banco"]["rules"][0]
    assert regra["folderUID"] == "saudeja" and "notification_settings" not in regra
    assert grupos["operacao-metricas"]["interval"] == 60
    # O grupo que saiu do repositório sai do stack; o de outra pasta fica.
    apagados = [c for m, c, _d in escritas if m == "DELETE"]
    assert apagados == ["/api/v1/provisioning/folder/saudeja/rule-groups/grupo-antigo"]
    assert ("POST", "/api/v1/provisioning/mute-timings") in [(m, c) for m, c, _d in escritas]
    assert len([c for m, c, _d in escritas if c == "/api/dashboards/db"]) == 6


def test_arvore_de_producao_preserva_raiz_e_terceiros_e_troca_a_rota_provisoria():
    falso = GrafanaFalso()
    _aplicar(falso)
    ((arvore,),) = [[d] for m, c, d in falso.escritas() if c == "/api/v1/provisioning/policies"]

    assert arvore["receiver"] == "grafana-default-email"
    assert arvore["group_by"] == ["grafana_folder", "alertname"]
    assert "provenance" not in arvore
    receptores = [r["receiver"] for r in arvore["routes"]]
    # As do projeto na frente (a primeira que casa decide), a de terceiro fica,
    # a provisória do SM sai: o P1 do check casa `severidade=P1` direto. A de
    # dev vem antes de todas: `ambiente=dev` nunca chega a produção.
    assert receptores == ["saudeja-dev", "saudeja-p1", "saudeja-p2", "saudeja-p3", "irm"]
    assert arvore["routes"][0]["object_matchers"] == [["ambiente", "=", "dev"]]
    assert ROTA_PROVISORIA_DO_SM not in arvore["routes"]
    assert arvore["routes"][2]["routes"][0]["receiver"] == "saudeja-p2-modelo"
    assert arvore["routes"][3]["mute_time_intervals"] == ["saudeja-fora-do-resumo-semanal"]


def test_p3_so_notifica_na_segunda_das_9h_as_10h():
    """O silêncio cobre a semana inteira menos a janela do resumo."""
    (intervalo,) = gp.carregar_notificacoes()["intervalos"]
    janelas = intervalo["time_intervals"]
    assert all(j["location"] == "America/Sao_Paulo" for j in janelas)
    assert janelas[0] == {
        "weekdays": ["sunday", "tuesday:saturday"],
        "location": "America/Sao_Paulo",
    }
    assert janelas[1]["weekdays"] == ["monday"]
    assert janelas[1]["times"] == [
        {"start_time": "00:00", "end_time": "09:00"},
        {"start_time": "10:00", "end_time": "24:00"},
    ]


def test_faixas_de_dias_do_silencio_estao_na_ordem_do_alertmanager():
    """A semana do Alertmanager vai de domingo a sábado e "a:b" exige a <= b;
    faixa invertida ("tuesday:sunday") o Grafana recusa com 400."""
    semana = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
    for intervalo in gp.carregar_notificacoes()["intervalos"]:
        for janela in intervalo["time_intervals"]:
            for dias in janela.get("weekdays", []):
                inicio, _, fim = dias.partition(":")
                assert semana.index(inicio) <= semana.index(fim or inicio), dias


def test_contact_point_faltando_falha_antes_de_qualquer_escrita():
    falso = GrafanaFalso(contatos=["saudeja-p1"])
    with pytest.raises(gp.ErroDeProvisionamento, match="saudeja-p2, saudeja-p2-modelo, saudeja-p3"):
        _aplicar(falso)
    assert falso.escritas() == []


def test_dev_entrega_direto_ao_contato_de_dev_e_nao_toca_a_arvore_de_producao():
    falso = GrafanaFalso()
    _aplicar(falso, "dev")
    escritas = falso.escritas()

    caminhos = [c for _m, c, _d in escritas]
    assert not any("policies" in c or "mute-timings" in c for c in caminhos)
    assert ("POST", "/api/folders", {"uid": "saudeja-dev", "title": "SaúdeJá (dev)"}) in escritas
    # Cada grupo: gravado pausado, conferido, e só então ligado.
    puts = [(c, d) for m, c, d in escritas if m == "PUT" and "/rule-groups/" in c]
    assert len(puts) == 2 * len(gp.carregar_grupos(_valores("dev")))
    for (c1, pausado), (c2, ligado) in zip(puts[::2], puts[1::2], strict=True):
        assert c1 == c2
        assert all(r["isPaused"] for r in pausado["rules"])
        assert not any(r["isPaused"] for r in ligado["rules"])
    regras = [r for _c, d in puts[1::2] for r in d["rules"]]
    assert regras and all(
        r["uid"].endswith("-dev")
        and r["title"].startswith("[dev] ")
        and r["folderUID"] == "saudeja-dev"
        and r["labels"]["ambiente"] == "dev"
        and r["notification_settings"] == {"receiver": "saudeja-dev"}
        for r in regras
    )
    texto = json.dumps(regras)
    assert 'deployment_environment=\\"dev\\"' in texto
    assert 'deployment_environment=\\"prod\\"' not in texto


def test_dev_sem_o_contato_gravado_apaga_o_grupo_pausado_e_falha():
    """Um Grafana que ignorasse `notification_settings` mandaria o alerta de dev
    pela árvore de produção. O grupo nunca chega a ser ligado."""
    falso = GrafanaFalso(ignora_notificacao=True)
    with pytest.raises(gp.ErroDeProvisionamento, match="contact point de dev"):
        _aplicar(falso, "dev")
    puts = [d for m, c, d in falso.escritas() if m == "PUT" and "/rule-groups/" in c]
    assert len(puts) == 1 and all(r["isPaused"] for r in puts[0]["rules"])
    assert [m for m, c, _d in falso.escritas() if "/rule-groups/" in c] == ["PUT", "DELETE"]


def test_remover_so_existe_em_dev():
    grafana = gp.Grafana("https://stack.exemplo", "token-secreto", GrafanaFalso())
    with pytest.raises(gp.ErroDeProvisionamento, match="só existe para dev"):
        gp.remover(grafana, gp.AMBIENTES["prod"])

    falso = GrafanaFalso(grupos_existentes=[("saudeja-dev", "operacao-banco"), ("saudeja", "x")])
    gp.remover(gp.Grafana("https://stack.exemplo", "token-secreto", falso), gp.AMBIENTES["dev"])
    assert [c for m, c, _d in falso.escritas() if m == "DELETE"] == [
        "/api/v1/provisioning/folder/saudeja-dev/rule-groups/operacao-banco",
        "/api/folders/saudeja-dev",
    ]


def test_erro_do_grafana_diz_o_status_e_nunca_o_token():
    def recusa(metodo, url, cabecalhos, corpo):
        return 403, '{"message": "permissions needed: alert.provisioning:write"}'

    grafana = gp.Grafana("https://stack.exemplo", "token-secreto", recusa)
    with pytest.raises(gp.ErroDeProvisionamento) as erro:
        grafana.pedir("GET", "/api/v1/provisioning/policies")
    assert "HTTP 403" in str(erro.value) and "Admin" in str(erro.value)
    assert "token-secreto" not in str(erro.value)


def test_url_do_stack_sem_https_e_recusada():
    with pytest.raises(gp.ErroDeProvisionamento, match="https"):
        gp.transporte_http("GET", "http://stack.exemplo/api/health", {}, None)


def test_aplicar_sem_a_fonte_do_supabase_no_ambiente_falha_com_instrucao(monkeypatch, capsys):
    monkeypatch.setenv("GRAFANA_URL", "https://stack.exemplo")
    monkeypatch.setenv("GRAFANA_SA_TOKEN", "token-secreto")
    monkeypatch.delenv("GRAFANA_DS_SUPABASE", raising=False)

    assert gp.main(["aplicar", "--ambiente", "prod"]) == 1
    assert "GRAFANA_DS_SUPABASE" in capsys.readouterr().out


# --- conferir (só leitura) ------------------------------------------------------

METRICAS_NO_STACK = ["saudeja_acao_total", "saudeja_erro_total", "saudeja_sms_total"]


def _transporte_de_conferencia(corpo_da_contagem_do_loki: str):
    """Mimir e Loki como o `conferir` os consulta. A contagem do Loki chega
    como veio: JSON, ou o texto de um proxy no meio do caminho."""

    def transporte(metodo, url, cabecalhos, corpo):
        assert metodo == "GET", "o conferir é só leitura"
        caminho = urlsplit(url).path
        if caminho.endswith("/label/__name__/values"):
            return 200, json.dumps({"data": METRICAS_NO_STACK})
        if caminho.endswith("/label/deployment_environment/values"):
            return 200, json.dumps({"data": ["prod"]})
        if caminho.endswith("/label/service_name/values"):
            return 200, json.dumps({"data": ["saudeja-job-d2"]})
        if caminho.endswith("/loki/api/v1/query"):
            return 200, corpo_da_contagem_do_loki
        raise AssertionError(f"GET inesperado: {caminho}")

    return transporte


def test_conferir_diz_o_que_chegou_e_o_que_falta_no_stack():
    serie = {
        "metric": {"service_name": "saudeja-job-d2", "deployment_environment": "prod"},
        "value": [1760000000, "42"],
    }
    contagem = json.dumps({"data": {"result": [serie]}})
    grafana = gp.Grafana("https://stack.exemplo", "t", _transporte_de_conferencia(contagem))

    texto = "\n".join(gp.conferir(grafana, _valores()))

    assert "deployment_environment: ['prod']" in texto
    assert "`saudeja_sms_total`: presente" in texto
    assert "`saudeja_job_d2_agendamentos_total`: AUSENTE" in texto
    assert "saudeja-job-d2/prod=42" in texto


def test_conferir_com_resposta_do_loki_que_nao_e_json_nao_quebra():
    """`Grafana.pedir` devolve o texto cru quando o corpo não é JSON. A
    contagem do Loki chamava `.get` antes de conferir o tipo, e o `conferir`
    morria com AttributeError em vez de dizer que nada chegou."""
    grafana = gp.Grafana(
        "https://stack.exemplo", "t", _transporte_de_conferencia("<html>502 Bad Gateway</html>")
    )

    linhas = gp.conferir(grafana, _valores())

    assert any(linha.startswith("Loki, linhas") and "NENHUMA" in linha for linha in linhas)
