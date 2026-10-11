"""
Registra um pedido do titular sobre a base de treino recebido diretamente
pelo encarregado da SaúdeJá (ADR-010): eliminação dos dados de aprimoramento
ou revogação do consentimento. A SaúdeJá é a controladora dessa base, então o
pedido não passa pela clínica; o workflow de purga (`lgpd.yml`) o executa na
execução diária seguinte.

O CPF é lido sem eco (`getpass`), nunca por argumento: um argumento ficaria no
histórico do shell. Ele vira o pseudônimo HMAC (`src/pseudonimo.py`, com a
`TREINO_CHAVE_PSEUDONIMO` do `.env` do encarregado) e não é guardado nem
impresso. A saída não diz se havia dado de aprimoramento daquele titular.

Confirme a identidade de quem pede antes de registrar.

Uso:
    python scripts/pedido_titular_treino.py eliminacao
    python scripts/pedido_titular_treino.py revogacao
"""
import argparse
import getpass
import sys
from collections.abc import Callable
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import db.repositorio_treino as repositorio_treino  # noqa: E402
import pseudonimo  # noqa: E402


def main(argv: list[str] | None = None, ler_cpf: Callable[[str], str] = getpass.getpass) -> int:
    parser = argparse.ArgumentParser(description="Pedido do titular sobre a base de treino")
    parser.add_argument("tipo", choices=repositorio_treino.TIPOS_PEDIDO)
    args = parser.parse_args(argv)

    if not pseudonimo.chave_configurada():
        print(f"{pseudonimo.VARIAVEL_CHAVE} ausente ou curta -- configure o .env.")
        return 1
    cpf = ler_cpf("CPF do titular (não aparece na tela): ")
    try:
        id_titular = pseudonimo.id_titular(cpf)
    except ValueError:
        print("CPF inválido.")
        return 1
    id_pedido = repositorio_treino.registrar_pedido(id_titular, args.tipo, "saudeja")
    print(f"Pedido {id_pedido} registrado; o workflow de purga o executa na próxima execução.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
