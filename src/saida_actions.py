"""
SaúdeJá — saída dos scripts que rodam nos workflows do GitHub Actions: o
terminal, o resumo do run (`$GITHUB_STEP_SUMMARY`) e as saídas de passo
(`$GITHUB_OUTPUT`).

Antes deste módulo, cada script de workflow (job D-2, gate do re-treino,
canário, export, purga LGPD, alerta diário, Grafana, smoke do deploy) tinha a
sua cópia de "imprime e anexa ao resumo", e só parte delas protegia o `print`
do console cp1252 do Windows -- onde um "Δ" ou um emoji levanta
`UnicodeEncodeError` e transforma o código de saída de um gate num traceback
(visto na verificação do gate de 2026-09-21). Um lugar só, com a proteção
valendo para todos.

Só biblioteca padrão: os jobs de deploy e do Grafana instalam o mínimo.
"""
import json
import os
import sys
from pathlib import Path
from typing import Any


def imprimir(texto: str) -> None:
    """`print` que não morre no console do Windows: o caractere que o codec
    do terminal não representa vira `?`. O resumo do run, em utf-8, fica
    intacto (`anexar_ao_resumo`)."""
    try:
        print(texto)
    except UnicodeEncodeError:
        codec = sys.stdout.encoding or "ascii"
        print(texto.encode(codec, errors="replace").decode(codec, errors="replace"))


def anexar_ao_resumo(texto: str) -> None:
    """Acrescenta `texto` (e uma quebra de linha) ao resumo do run. Fora do
    Actions, sem `GITHUB_STEP_SUMMARY`, não faz nada."""
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(texto + "\n")


def publicar(texto: str) -> None:
    """O mesmo texto no terminal e no resumo do run."""
    imprimir(texto)
    anexar_ao_resumo(texto)


def escrever_saida(chave: str, valor: str) -> None:
    """Uma saída de passo (`steps.<id>.outputs.<chave>`). Fora do Actions,
    sem `GITHUB_OUTPUT`, não faz nada."""
    destino = os.environ.get("GITHUB_OUTPUT")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(f"{chave}={valor}\n")


def escrever_json_atomico(caminho: str | Path, conteudo: dict[str, Any]) -> None:
    """JSON legível (indentado, acentos como estão) gravado de uma vez: num
    arquivo temporário ao lado, trocado pelo definitivo com `os.replace`.
    Quem lê em seguida -- o passo do workflow que faz o commit, o job do dia
    seguinte -- nunca vê o arquivo pela metade."""
    tmp = f"{caminho}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(conteudo, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, caminho)
