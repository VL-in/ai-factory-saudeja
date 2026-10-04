"""
Varredura de PII em log ("Blindagem LGPD").

Ferramenta de verificação **local**, deliberadamente não a evidência principal
do SLO §6. O ADR-006 já registrou por quê: o log de runtime do Hugging Face
Space é efêmero (restart ou rebuild apaga, sem busca e sem retenção), então no
dia do pitch não haverá log de produção nenhum para varrer. A garantia de
zero-PII é preventiva -- o filtro de `src/logging_config.py` e as guardas
estáticas de `tests/test_coerencia_repo.py`. Este script serve para:

1. conferir o resultado de um smoke test local ponta a ponta do deploy antes
   do pitch, que é o momento em que existe log de verdade para olhar;
2. auditar o log de um workflow do GitHub Actions baixado como artifact;
3. fechar o ciclo do próprio filtro -- varrer a saída de um processo já
   configurado deve dar zero achado, e é assim que se prova que o filtro está
   instalado no processo de verdade, não só no teste unitário.

Os padrões vêm de `src/logging_config.py`, não de uma cópia local: duas listas
de regex sobre o mesmo requisito divergiriam na primeira mudança, e a que
divergisse em silêncio seria justamente a que dá o veredito.

Uso:
    python scripts/auditoria_lgpd.py caminho/do/log.txt [outro.log ...]
    python scripts/auditoria_lgpd.py logs/            # varre recursivamente
    docker compose logs app | python scripts/auditoria_lgpd.py -

Saída: uma linha por achado (arquivo, número de linha, regra e um excerto
**mascarado**) e código de saída 1 se houver qualquer achado. O excerto nunca
mostra o valor encontrado -- um relatório de auditoria que imprime o CPF que
achou é o próprio vazamento com outro nome.
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from logging_config import MARCADOR, REGRAS_DE_PII  # noqa: E402

EXTENSOES_DE_LOG = (".log", ".txt", ".json", ".jsonl", ".ndjson")
CARACTERES_DE_CONTEXTO = 40


def auditar_linha(linha: str) -> list[tuple[str, str]]:
    """(regra, excerto mascarado) para cada achado da linha.

    O critério de achado é "a redação mudaria este trecho?" -- a mesma função
    de substituição que o filtro usa, aplicada aqui só para comparar. Isso
    resolve de graça o falso positivo da contagem de dígitos (um `duracao_ms`
    não é telefone, e a substituição devolve o trecho intacto).

    Valor já redigido também não é achado: `nome=[REDIGIDO]` continua casando
    a regra de chave -- é a chave que ela procura --, mas é justamente a prova
    de que a blindagem funcionou.
    """
    achados = []
    for regra, padrao, substituir in REGRAS_DE_PII:
        for casamento in padrao.finditer(linha):
            trecho = casamento.group(0)
            if MARCADOR in trecho or substituir(casamento) == trecho:
                continue
            achados.append((regra, _mascarar(linha, casamento.start(), casamento.end())))
    return achados


def _mascarar(linha: str, inicio: int, fim: int) -> str:
    """Contexto à volta do achado com o achado em si substituído -- localiza o
    problema no arquivo sem reimprimir o dado."""
    antes = linha[max(0, inicio - CARACTERES_DE_CONTEXTO) : inicio]
    depois = linha[fim : fim + CARACTERES_DE_CONTEXTO]
    return f"...{antes}<<PII>>{depois}...".strip()


def auditar_texto(texto: str, origem: str) -> list[dict]:
    resultados = []
    for numero, linha in enumerate(texto.splitlines(), start=1):
        for regra, excerto in auditar_linha(linha):
            resultados.append(
                {"origem": origem, "linha": numero, "regra": regra, "excerto": excerto}
            )
    return resultados


def _expandir(caminhos: list[str]) -> list[Path]:
    """Diretório vira os arquivos de log dentro dele (recursivo). Extensão
    filtrada para uma pasta de logs com binário/artefato dentro não virar
    varredura de bytes aleatórios cheia de falso positivo."""
    arquivos = []
    for bruto in caminhos:
        caminho = Path(bruto)
        if caminho.is_dir():
            arquivos.extend(
                sorted(f for f in caminho.rglob("*") if f.suffix.lower() in EXTENSOES_DE_LOG)
            )
        else:
            arquivos.append(caminho)
    return arquivos


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "caminhos",
        nargs="+",
        help="arquivos e/ou diretórios de log; use '-' para ler da entrada padrão",
    )
    args = parser.parse_args(argv)

    achados = []
    if args.caminhos == ["-"]:
        achados = auditar_texto(sys.stdin.read(), "<stdin>")
    else:
        for arquivo in _expandir(args.caminhos):
            if not arquivo.exists():
                print(f"[erro] arquivo não encontrado: {arquivo}", file=sys.stderr)
                return 2
            achados.extend(
                auditar_texto(
                    arquivo.read_text(encoding="utf-8", errors="replace"), str(arquivo)
                )
            )

    for achado in achados:
        print(f"{achado['origem']}:{achado['linha']}: [{achado['regra']}] {achado['excerto']}")

    if achados:
        print(
            f"\n[falha] {len(achados)} possível(is) ocorrência(s) de PII -- "
            "SLO §6 exige 0. Ver docs/LGPD.md.",
            file=sys.stderr,
        )
        return 1

    print("[ok] nenhuma ocorrência de PII encontrada (SLO §6)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
