"""
SaúdeJá — migração do identificador do paciente
(scripts/migrar_id_paciente.py, ADR-011), com um banco em memória.
"""
import hashlib
import importlib.util
from pathlib import Path

import pytest

import id_paciente

_CAMINHO = Path(__file__).resolve().parents[1] / "scripts" / "migrar_id_paciente.py"
_spec = importlib.util.spec_from_file_location("migrar_id_paciente", _CAMINHO)
assert _spec and _spec.loader
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def _sha(cpf: str) -> str:
    return hashlib.sha256(cpf.encode()).hexdigest()


class _Banco:
    """`pacientes` em memória, com a unicidade de `id_paciente_externo`."""

    def __init__(self, linhas: dict[str, str]):
        self.linhas = dict(linhas)
        self.trocas = 0

    def listar(self):
        return [{"id": i, "id_paciente_externo": v} for i, v in sorted(self.linhas.items())]

    def trocar(self, id_paciente, novo):
        if novo in self.linhas.values():
            erro = Exception("duplicate key value violates unique constraint")
            erro.code = "23505"
            raise erro
        self.trocas += 1
        if id_paciente not in self.linhas:
            return False
        self.linhas[id_paciente] = novo
        return True


@pytest.fixture
def banco(monkeypatch):
    def _instalar(linhas):
        falso = _Banco(linhas)
        monkeypatch.setattr(
            script.repositories, "listar_identificadores_de_pacientes", falso.listar
        )
        monkeypatch.setattr(script.repositories, "trocar_identificador_do_paciente", falso.trocar)
        monkeypatch.setattr(script.canario, "ativo", lambda: False)
        return falso

    return _instalar


def _rodar(*argv):
    saida: list[str] = []
    codigo = script.main(list(argv), escrever=saida.append)
    return codigo, "\n".join(saida)


def test_sem_aplicar_nao_grava_nada(banco):
    falso = banco({"p1": _sha("11144477735")})

    codigo, saida = _rodar()

    assert codigo == 0
    assert falso.trocas == 0
    assert "a migrar: 1" in saida
    assert "Nada foi gravado" in saida


def test_aplicar_migra_para_o_valor_que_o_cadastro_gera(banco):
    """Depois da migração, o recadastro do mesmo CPF cai na mesma linha."""
    falso = banco({"p1": _sha("11144477735"), "p2": _sha("52998224725")})

    codigo, _ = _rodar("--aplicar")

    assert codigo == 0
    assert falso.linhas == {
        "p1": id_paciente.de_cpf("11144477735"),
        "p2": id_paciente.de_cpf("52998224725"),
    }


def test_rodar_de_novo_nao_envolve_o_que_ja_foi_migrado(banco):
    falso = banco({"p1": _sha("11144477735")})
    _rodar("--aplicar")
    migrado = dict(falso.linhas)

    codigo, saida = _rodar("--aplicar")

    assert codigo == 0
    assert falso.linhas == migrado
    assert "já no formato novo: 1" in saida
    assert "a migrar: 0" in saida


def test_texto_livre_antigo_fica_como_esta(banco):
    """Identificador digitado antes do cadastro por CPF não é sha256: o
    cadastro nunca o reencontraria, e envolvê-lo não muda isso."""
    falso = banco({"p1": "EXT-1"})

    codigo, saida = _rodar("--aplicar")

    assert codigo == 0
    assert falso.linhas == {"p1": "EXT-1"}
    assert "outro formato (ignorados): 1" in saida


def test_titular_recadastrado_antes_da_migracao_vira_conflito(banco):
    """Linha antiga e linha nova do mesmo CPF: a antiga não é tocada, e o par
    de uuids sai no relatório para a fusão manual."""
    falso = banco(
        {
            "antiga": _sha("11144477735"),
            "nova": id_paciente.de_cpf("11144477735"),
            "outra": _sha("52998224725"),
        }
    )

    codigo, saida = _rodar("--aplicar")

    assert codigo == 1
    assert falso.linhas["antiga"] == _sha("11144477735")
    assert falso.linhas["outra"] == id_paciente.de_cpf("52998224725")
    assert "antiga antiga -> nova nova" in saida


def test_recadastro_durante_a_migracao_nao_derruba_as_demais(banco, monkeypatch):
    """Violação de unicidade na gravação vira conflito; as outras linhas
    seguem."""
    falso = banco({"p1": _sha("11144477735"), "p2": _sha("52998224725")})
    trocar = falso.trocar

    def _recadastro_no_meio(id_paciente_, novo):
        if id_paciente_ == "p1":
            falso.linhas["p3"] = novo  # o paciente se recadastrou agora
        return trocar(id_paciente_, novo)

    monkeypatch.setattr(
        script.repositories, "trocar_identificador_do_paciente", _recadastro_no_meio
    )

    codigo, saida = _rodar("--aplicar")

    assert codigo == 1
    assert falso.linhas["p2"] == id_paciente.de_cpf("52998224725")
    assert "p1 -> recadastrado durante a migração" in saida


def test_outro_erro_do_banco_nao_e_engolido(banco, monkeypatch):
    banco({"p1": _sha("11144477735")})

    def _falha(*_):
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(script.repositories, "trocar_identificador_do_paciente", _falha)

    with pytest.raises(RuntimeError):
        _rodar("--aplicar")


def test_saida_nunca_traz_identificador(banco):
    """O formato antigo é o próprio CPF para quem o lê: a saída tem só
    contagens e uuids internos."""
    antigo = _sha("11144477735")
    banco({"antiga": antigo, "nova": id_paciente.de_cpf("11144477735")})

    _, saida = _rodar("--aplicar")

    assert antigo not in saida
    assert id_paciente.envelopar(antigo) not in saida
    assert "11144477735" not in saida


def test_sem_chave_recusa_antes_de_ler_o_banco(banco, monkeypatch):
    falso = banco({"p1": _sha("11144477735")})
    monkeypatch.delenv(id_paciente.VARIAVEL_CHAVE)

    codigo, saida = _rodar("--aplicar")

    assert codigo == 1
    assert id_paciente.VARIAVEL_CHAVE in saida
    assert falso.linhas == {"p1": _sha("11144477735")}


def test_canario_ativo_recusa(banco, monkeypatch):
    """O job D-2 divide os braços do canário pelo identificador: trocá-lo no
    meio embaralharia os pacientes entre campeão e canário."""
    falso = banco({"p1": _sha("11144477735")})
    monkeypatch.setattr(script.canario, "ativo", lambda: True)

    codigo, saida = _rodar("--aplicar")

    assert codigo == 1
    assert "Canário ativo" in saida
    assert falso.trocas == 0
