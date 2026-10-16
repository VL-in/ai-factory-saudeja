"""
SaúdeJá — workflow de purga LGPD (src/lgpd_purga.py, ADR-009 e ADR-010).

Sem Azure nem Supabase: o remote do DVC e os repositórios são falsos em
memória, e o histórico git é montado à mão -- exceto no último bloco, que
lê um repositório git de verdade, criado num diretório temporário.
"""
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import lgpd_purga as lp

AGORA = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
TITULAR = "a" * 64
OUTRO = "b" * 64


def _dias_atras(dias):
    return AGORA - timedelta(days=dias)


def _dataset(md5):
    return lp.Referencia("data/consultas-treino.csv.dvc", "data/consultas-treino.csv", md5)


def _semente(md5="semente"):
    return lp.Referencia("data/consultas-historicas.csv.dvc", "data/consultas-historicas.csv", md5)


def _recorte(nome, md5, dataset):
    return lp.Referencia("dvc.lock", f"data/interim/{nome}", md5, (dataset,))


def _modelo(md5):
    return lp.Referencia("dvc.lock", "data/model.pkl", md5, ("treino-raw",))


class RemotoFalso:
    def __init__(self, blobs=None, falhar_ao_apagar=False, nao_apaga=False):
        # md5 -> (conteúdo, modificado em)
        self.blobs = dict(blobs or {})
        self.falhar_ao_apagar = falhar_ao_apagar
        self.nao_apaga = nao_apaga
        self.apagados = []

    def existe(self, md5):
        return md5 in self.blobs

    def ler(self, md5):
        return self.blobs[md5][0]

    def apagar(self, md5):
        if self.falhar_ao_apagar:
            raise ConnectionError("Azure fora do ar")
        if md5 not in self.blobs:
            return False
        if not self.nao_apaga:
            del self.blobs[md5]
        self.apagados.append(md5)
        return True

    def todos(self):
        return list(self.blobs)

    def modificado_em(self, md5):
        return self.blobs[md5][1]


class TreinoFalso:
    def __init__(self, pedidos=()):
        self.pedidos = [dict(p) for p in pedidos]
        self.eliminados = []
        self.executados = []
        self.janelas = []

    def pedidos_pendentes(self):
        return [p for p in self.pedidos if p["id"] not in self.executados]

    def eliminar_titular(self, id_titular):
        self.eliminados.append(id_titular)
        return 2

    def marcar_pedido_executado(self, id_pedido):
        if id_pedido in self.executados:
            return False
        self.executados.append(id_pedido)
        return True

    def purgar(self, janela):
        self.janelas.append(janela)
        return {"consultas": 0, "eventos": 0, "pedidos_recusados": 0}


class ClinicaFalsa:
    def __init__(self, pedidos=()):
        self.pedidos = list(pedidos)
        self.eliminados = []
        self.executados = []

    def pedidos_confirmados(self):
        return self.pedidos

    def eliminar_paciente(self, id_paciente):
        self.eliminados.append(id_paciente)
        return 1

    def marcar_pedido_executado(self, id_pedido):
        self.executados.append(id_pedido)
        return True


def _csv(*titulares):
    linhas = ["id_consulta,id_paciente,no_show"]
    linhas += [f"c{i},{t},0" for i, t in enumerate(titulares)]
    return ("\n".join(linhas) + "\n").encode()


# --- referências e linha do tempo --------------------------------------------------


def test_referencias_de_um_lock_ligam_saidas_as_entradas():
    lock = """
schema: '2.0'
stages:
  preprocess:
    deps:
    - path: data/consultas-treino.csv
      md5: d1
    outs:
    - path: data/interim/train_raw.pkl
      md5: r1
"""
    [ref] = lp.referencias_do_arquivo("dvc.lock", lock)

    assert (ref.saida, ref.md5, ref.entradas) == ("data/interim/train_raw.pkl", "r1", ("d1",))
    assert lp.referencias_do_arquivo("x.dvc", ": : não é yaml") == []


def test_versao_expira_90_dias_depois_de_deixar_de_ser_vigente():
    historico = lp.Historico(
        commits=[
            (_dias_atras(200), [_semente(), _dataset("v91")]),
            (_dias_atras(91), [_semente(), _dataset("v89")]),
            (_dias_atras(89), [_semente(), _dataset("vigente")]),
        ]
    )

    assert lp.hashes_expirados(historico, AGORA, 90) == {"v91"}


def test_vigente_no_head_semente_e_ponta_de_branch_nunca_expiram():
    historico = lp.Historico(
        commits=[
            (_dias_atras(400), [_semente("semente-antiga"), _dataset("velho")]),
            (_dias_atras(300), [_semente("semente-antiga"), _dataset("no-pr")]),
            (_dias_atras(200), [_dataset("vigente")]),
        ],
        pontas=[[_dataset("no-pr")]],  # PR de canário ainda aberto
    )

    expirados = lp.hashes_expirados(historico, AGORA, 90)

    assert expirados == {"velho"}
    assert {"vigente", "semente-antiga", "no-pr"} <= historico.protegidos()


def test_hash_que_volta_a_ser_vigente_conta_a_ultima_saida():
    """Rollback do modelo: o campeão anterior volta a ser referenciado."""
    historico = lp.Historico(
        commits=[
            (_dias_atras(300), [_modelo("m1")]),
            (_dias_atras(250), [_modelo("m2")]),
            (_dias_atras(120), [_modelo("m1")]),
            (_dias_atras(10), [_modelo("m3")]),
        ]
    )

    fim = historico.fim_de_vigencia()

    assert fim["m1"] == _dias_atras(10)
    assert lp.hashes_expirados(historico, AGORA, 90) == {"m2"}


def test_guarda_recusa_lista_com_hash_protegido():
    """Controle negativo: um hash vigente injetado na lista faz a purga
    falhar alto, em vez de apagar o modelo de produção."""
    remoto = RemotoFalso({"vigente": (b"x", AGORA)})

    with pytest.raises(lp.HashProtegido):
        lp.apagar_lista(remoto, {"vigente", "outro"}, {"vigente"})
    assert remoto.apagados == []


# --- pedidos do treino -----------------------------------------------------------------


def _historico_com_titular():
    return lp.Historico(
        commits=[
            (
                _dias_atras(60),
                [
                    _semente(),
                    _dataset("d-com"),
                    _recorte("train_raw.pkl", "r-com", "d-com"),
                    _recorte("test.pkl", "t-com", "d-com"),
                    _modelo("m-velho"),
                ],
            ),
            (
                _dias_atras(5),
                [
                    _semente(),
                    _dataset("d-head"),
                    _recorte("train_raw.pkl", "r-head", "d-head"),
                    _recorte("relatorio_dados.json", "rel-head", "d-head"),
                    lp.Referencia("dvc.lock", "data/interim/mapa_especialidade.json", "mapa",
                                  ("d-head",)),
                    _modelo("m-head"),
                ],
            ),
        ],
        pontas=[[_dataset("d-sem")]],
    )


def _remoto_com_titular():
    return RemotoFalso(
        {
            "semente": (_csv("P1"), AGORA),
            "d-com": (_csv(TITULAR, OUTRO), AGORA),
            "r-com": (b"pickle sem pseudonimo", AGORA),
            "t-com": (b"pickle sem pseudonimo", AGORA),
            "d-head": (_csv(TITULAR), AGORA),
            "r-head": (b"pickle", AGORA),
            "rel-head": (b"{}", AGORA),
            "mapa": (b"{}", AGORA),
            "m-velho": (b"modelo", AGORA),
            "m-head": (b"modelo", AGORA),
            "d-sem": (_csv(OUTRO), AGORA),
        }
    )


def test_eliminacao_apaga_as_versoes_com_o_titular_e_os_recortes_delas_e_so_elas():
    remoto = _remoto_com_titular()
    treino = TreinoFalso([{"id": "p1", "id_titular": TITULAR, "tipo": "eliminacao"}])
    resultado = lp.Resultado()

    lp.executar_pedidos_do_treino(remoto, _historico_com_titular(), resultado, treino)

    # Inclusive a versão vigente no HEAD, que só a eliminação pode apagar.
    assert set(remoto.apagados) == {"d-com", "r-com", "t-com", "d-head", "r-head", "rel-head"}
    assert {"semente", "m-head", "m-velho", "mapa", "d-sem"} <= set(remoto.blobs)
    assert treino.eliminados == [TITULAR]
    assert treino.executados == ["p1"]
    assert resultado.falhas == []


def test_execucao_repetida_nao_apaga_nada_a_mais_nem_falha():
    remoto = _remoto_com_titular()
    treino = TreinoFalso([{"id": "p1", "id_titular": TITULAR, "tipo": "revogacao"}])
    historico = _historico_com_titular()
    lp.executar_pedidos_do_treino(remoto, historico, lp.Resultado(), treino)
    apagados = list(remoto.apagados)

    segunda = lp.Resultado()
    lp.executar_pedidos_do_treino(remoto, historico, segunda, treino)

    assert remoto.apagados == apagados
    assert segunda.falhas == []
    assert treino.executados == ["p1"]


def test_dvc_fora_do_ar_para_o_pedido_antes_do_banco_e_a_execucao_seguinte_conclui():
    """Ordem obrigatória: com o DVC falhando, a base de treino não é tocada e
    o pedido continua pendente, com o pseudônimo, para a próxima execução."""
    historico = _historico_com_titular()
    treino = TreinoFalso([{"id": "p1", "id_titular": TITULAR, "tipo": "eliminacao"}])
    fora = _remoto_com_titular()
    fora.falhar_ao_apagar = True
    primeira = lp.Resultado()

    lp.executar_pedidos_do_treino(fora, historico, primeira, treino)

    assert treino.eliminados == []
    assert treino.executados == []
    assert primeira.pedidos_treino_parados == 1
    assert all(TITULAR not in f for f in primeira.falhas)

    lp.executar_pedidos_do_treino(_remoto_com_titular(), historico, lp.Resultado(), treino)

    assert treino.eliminados == [TITULAR]
    assert treino.executados == ["p1"]


def test_blob_que_continua_no_remote_para_o_pedido_com_a_pergunta_de_seguranca():
    remoto = _remoto_com_titular()
    remoto.nao_apaga = True
    treino = TreinoFalso([{"id": "p1", "id_titular": TITULAR, "tipo": "eliminacao"}])
    resultado = lp.Resultado()

    lp.executar_pedidos_do_treino(remoto, _historico_com_titular(), resultado, treino)

    assert treino.eliminados == []
    assert any(lp.PERGUNTA_DE_SEGURANCA in f for f in resultado.falhas)


def test_titular_sem_versao_no_dvc_e_eliminado_so_no_banco():
    remoto = _remoto_com_titular()
    treino = TreinoFalso([{"id": "p2", "id_titular": "c" * 64, "tipo": "revogacao"}])

    lp.executar_pedidos_do_treino(remoto, _historico_com_titular(), lp.Resultado(), treino)

    assert remoto.apagados == []
    assert treino.executados == ["p2"]


# --- pedidos da clínica e retenção ----------------------------------------------------


def test_pedidos_da_clinica_so_a_eliminacao_e_executada_pelo_workflow():
    clinica = ClinicaFalsa(
        [
            {"id": "e1", "id_paciente": "uuid-1", "tipo": "eliminacao"},
            {"id": "e2", "id_paciente": None, "tipo": "eliminacao"},  # já eliminado
            {"id": "r1", "id_paciente": "uuid-2", "tipo": "revisao_decisao"},
        ]
    )
    resultado = lp.Resultado()

    lp.executar_pedidos_da_clinica(resultado, clinica)

    assert clinica.eliminados == ["uuid-1"]
    assert clinica.executados == ["e1", "e2"]
    assert resultado.pedidos_clinica_executados == 2


def test_retencao_apaga_expirados_e_orfaos_antigos_e_purga_a_base_de_treino():
    historico = lp.Historico(
        commits=[
            (_dias_atras(200), [_semente(), _dataset("velho"), _modelo("m-velho")]),
            (_dias_atras(100), [_semente(), _dataset("vigente"), _modelo("m-vigente")]),
        ]
    )
    remoto = RemotoFalso(
        {
            "semente": (b"", _dias_atras(400)),
            "velho": (b"", _dias_atras(200)),
            "m-velho": (b"", _dias_atras(200)),
            "vigente": (b"", _dias_atras(10)),
            "m-vigente": (b"", _dias_atras(10)),
            "orfao-antigo": (b"", _dias_atras(120)),
            "orfao-recente": (b"", _dias_atras(20)),
        }
    )
    treino = TreinoFalso()
    resultado = lp.Resultado()

    lp.executar_retencao(remoto, historico, resultado, AGORA, treino)

    assert set(remoto.apagados) == {"velho", "m-velho", "orfao-antigo"}
    assert treino.janelas == [90]
    assert resultado.falhas == []


def test_resumo_so_tem_contadores(tmp_path, monkeypatch):
    destino = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(destino))
    remoto = _remoto_com_titular()
    remoto.falhar_ao_apagar = True
    treino = TreinoFalso([{"id": "p1", "id_titular": TITULAR, "tipo": "eliminacao"}])
    saidas = []

    resultado = lp.executar(
        remoto, _historico_com_titular(), AGORA, treino, ClinicaFalsa()
    )
    lp._publicar(resultado, saidas.append)

    texto = destino.read_text(encoding="utf-8") + "\n".join(saidas)
    assert TITULAR not in texto
    assert "pedidos do treino parados | 1" in texto


# --- histórico git de verdade ------------------------------------------------------------


def _git(raiz, *args, data=None):
    env = None
    if data is not None:
        import os

        env = {**os.environ, "GIT_AUTHOR_DATE": data, "GIT_COMMITTER_DATE": data}
    subprocess.run(["git", *args], cwd=raiz, check=True, capture_output=True, env=env)


def _commit(raiz, arquivos, data):
    for caminho, texto in arquivos.items():
        destino = raiz / caminho
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")
    _git(raiz, "add", "-A")
    _git(raiz, "commit", "-q", "-m", "x", data=data)


def test_ler_historico_de_um_repositorio_git(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    dvc = "outs:\n- md5: {}\n  path: consultas-treino.csv\n"
    _commit(
        tmp_path,
        {"data/consultas-treino.csv.dvc": dvc.format("v1"), "README": "a"},
        "2026-01-01T00:00:00+00:00",
    )
    _commit(tmp_path, {"README": "b"}, "2026-02-01T00:00:00+00:00")  # não toca o DVC
    _commit(
        tmp_path,
        {
            "data/consultas-treino.csv.dvc": dvc.format("v2"),
            "data/canario/consultas-treino.csv.dvc.salvo": dvc.format("v1"),
        },
        "2026-03-01T00:00:00+00:00",
    )

    historico = lp.ler_historico("HEAD", tmp_path)

    assert len(historico.commits) == 2
    saidas = {(r.saida, r.md5) for r in historico.commits[-1][1]}
    assert saidas == {
        ("data/consultas-treino.csv", "v2"),
        ("data/canario/consultas-treino.csv", "v1"),
    }
    # A cópia do canário mantém a versão antiga vigente.
    assert historico.fim_de_vigencia() == {"v1": None, "v2": None}


def test_remoto_dvc_le_e_apaga_pelo_layout_do_proprio_dvc(tmp_path):
    """O adaptador usa as classes do DVC: o mesmo layout (`files/md5/...`) e
    a mesma configuração de remote do `dvc push`. Aqui, com um remote local."""
    import sys

    repo, remote = tmp_path / "repo", tmp_path / "remote"
    repo.mkdir()
    remote.mkdir()
    dvc = [sys.executable, "-m", "dvc"]
    _git(repo, "init", "-q")
    for comando in (["init", "-q"], ["remote", "add", "-d", "local", str(remote)]):
        subprocess.run([*dvc, *comando], cwd=repo, check=True, capture_output=True)
    (repo / "consultas-treino.csv").write_bytes(_csv(TITULAR))
    for comando in (["add", "-q", "consultas-treino.csv"], ["push", "-q"]):
        subprocess.run([*dvc, *comando], cwd=repo, check=True, capture_output=True)
    import yaml

    md5 = yaml.safe_load((repo / "consultas-treino.csv.dvc").read_text())["outs"][0]["md5"]
    remoto = lp.RemotoDVC(repo)

    assert remoto.existe(md5)
    assert md5 in set(remoto.todos())
    assert lp.versoes_com_titular(remoto, {md5}, TITULAR) == {md5}
    assert remoto.modificado_em(md5) is not None
    assert remoto.apagar(md5)
    assert not remoto.existe(md5)
    assert not remoto.apagar(md5)  # idempotente


# --- pedido recebido pelo encarregado (scripts/pedido_titular_treino.py) ------------


def test_script_do_encarregado_registra_so_o_pseudonimo(monkeypatch, capsys):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import pedido_titular_treino as script

    monkeypatch.setenv(script.pseudonimo.VARIAVEL_CHAVE, "k" * 32)
    registrados = []
    monkeypatch.setattr(
        script.repositorio_treino,
        "registrar_pedido",
        lambda *args: registrados.append(args) or "pedido-1",
    )

    assert script.main(["eliminacao"], ler_cpf=lambda _: "111.444.777-35") == 0

    assert registrados == [(script.pseudonimo.id_titular("11144477735"), "eliminacao", "saudeja")]
    saida = capsys.readouterr().out
    assert "11144477735" not in saida and "111.444.777-35" not in saida
    assert script.main(["revogacao"], ler_cpf=lambda _: "123") == 1
