"""
Migração única do identificador do paciente no banco da clínica (ADR-011):
cada `pacientes.id_paciente_externo` ainda no formato antigo (sha256 do CPF,
reversível por força bruta) vira `hmac1:` + HMAC(chave, sha256), o mesmo valor
que o cadastro passa a gerar (`src/id_paciente.py`). O CPF não é necessário:
o HMAC é calculado sobre o que já está gravado.

Roda na máquina do encarregado, com o `.env` apontando para o banco do
ambiente e a `PACIENTE_CHAVE_IDENTIFICADOR` que a clínica, dona da chave,
entregou para este ambiente. Terminada a migração, a chave sai do `.env`. Nunca
num workflow: a chave não vai ao GitHub. A saída traz só contagens e uuids internos, nunca
um identificador, porque o formato antigo é o próprio CPF para quem o lê.

**Sem `--aplicar`, só mostra o que faria.** A troca não se desfaz: o sha256
antigo não pode ser recalculado a partir do HMAC.

Idempotente: o prefixo `hmac1:` separa o que já foi migrado, então rodar de
novo só pega o que sobrou. Duas situações param a migração de uma linha:

- **canário ativo**: o job D-2 divide os braços pelo identificador; trocá-lo no
  meio do canário embaralharia os pacientes entre campeão e canário. O script
  recusa enquanto `data/canario.json` existir no checkout (rode a partir de
  `main` atualizado);
- **conflito**: um paciente com linha antiga que se recadastrou depois do deploy
  e antes da migração ganhou uma segunda linha, já no formato novo. As duas
  são o mesmo titular; o script lista os pares de uuids e deixa a linha antiga
  como está, para a fusão manual descrita no ADR-011.

Uso:
    python scripts/migrar_id_paciente.py            # só mostra
    python scripts/migrar_id_paciente.py --aplicar  # grava
"""
import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import canario  # noqa: E402
import db.repositories as repositories  # noqa: E402
import id_paciente  # noqa: E402
from saida_actions import imprimir  # noqa: E402

# unique_violation: outra linha ganhou o mesmo identificador entre a leitura e
# a gravação (um recadastro durante a migração).
_VIOLACAO_DE_UNICIDADE = "23505"


@dataclass
class Plano:
    total: int = 0
    ja_migrados: int = 0
    outro_formato: int = 0
    trocas: list[tuple[str, str]] = field(default_factory=list)
    # (uuid da linha antiga, uuid da linha nova do mesmo titular)
    conflitos: list[tuple[str, str]] = field(default_factory=list)


def planejar(linhas: list[dict[str, Any]]) -> Plano:
    """O que a migração faria, sem gravar nada."""
    plano = Plano(total=len(linhas))
    atuais = {
        linha["id_paciente_externo"]: linha["id"]
        for linha in linhas
        if id_paciente.eh_atual(linha["id_paciente_externo"])
    }
    plano.ja_migrados = len(atuais)
    for linha in linhas:
        valor = linha["id_paciente_externo"]
        if id_paciente.eh_atual(valor):
            continue
        if not id_paciente.eh_legado(valor):
            # Texto livre de antes do cadastro por CPF (dados sintéticos de
            # teste): o cadastro nunca o reencontraria, nem antes nem depois.
            plano.outro_formato += 1
            continue
        novo = id_paciente.envelopar(valor)
        if novo in atuais:
            plano.conflitos.append((linha["id"], atuais[novo]))
        else:
            plano.trocas.append((linha["id"], novo))
    return plano


def aplicar(plano: Plano) -> int:
    """Grava as trocas. Devolve quantas foram gravadas; uma violação de
    unicidade vira conflito, sem derrubar as demais."""
    gravadas = 0
    for id_linha, novo in plano.trocas:
        try:
            if repositories.trocar_identificador_do_paciente(id_linha, novo):
                gravadas += 1
        except Exception as exc:
            if getattr(exc, "code", None) != _VIOLACAO_DE_UNICIDADE:
                raise
            # O uuid da outra linha não vem no erro; o resumo pede para
            # rodar de novo, e a releitura o encontra como conflito.
            plano.conflitos.append((id_linha, "?"))
    return gravadas


def _resumo(plano: Plano, escrever: Callable[[str], None]) -> None:
    escrever(f"pacientes: {plano.total}")
    escrever(f"  já no formato novo: {plano.ja_migrados}")
    escrever(f"  a migrar: {len(plano.trocas)}")
    escrever(f"  em conflito: {len(plano.conflitos)}")
    escrever(f"  outro formato (ignorados): {plano.outro_formato}")


def _relatar_conflitos(plano: Plano, escrever: Callable[[str], None]) -> None:
    if not plano.conflitos:
        return
    escrever("")
    escrever("Mesmo titular em duas linhas -- funda cada par como no ADR-011:")
    for antiga, nova in plano.conflitos:
        if nova == "?":
            escrever(f"  {antiga} -> recadastrado durante a migração; rode de novo")
        else:
            escrever(f"  antiga {antiga} -> nova {nova}")


def main(argv: list[str] | None = None, escrever: Callable[[str], None] = imprimir) -> int:
    parser = argparse.ArgumentParser(description="Migra id_paciente_externo para HMAC (ADR-011)")
    parser.add_argument("--aplicar", action="store_true", help="grava as trocas")
    args = parser.parse_args(argv)

    try:
        id_paciente.verificar_chave()
    except id_paciente.ChaveIdentificadorAusente as exc:
        escrever(str(exc))
        return 1
    if canario.ativo():
        escrever(
            "Canário ativo: o job D-2 divide os braços pelo identificador. "
            "Rode depois da promoção ou da reversão."
        )
        return 1

    plano = planejar(repositories.listar_identificadores_de_pacientes())
    _resumo(plano, escrever)
    if not args.aplicar:
        _relatar_conflitos(plano, escrever)
        escrever("")
        escrever("Nada foi gravado. Rode com --aplicar para migrar.")
        return 1 if plano.conflitos else 0

    gravadas = aplicar(plano)
    escrever(f"gravadas: {gravadas}")
    _relatar_conflitos(plano, escrever)

    # Releitura: confirma no banco, não na contagem local.
    restantes = planejar(repositories.listar_identificadores_de_pacientes())
    if restantes.trocas:
        escrever(f"Ainda há {len(restantes.trocas)} linha(s) no formato antigo; rode de novo.")
        return 1
    return 1 if plano.conflitos else 0


if __name__ == "__main__":
    sys.exit(main())
