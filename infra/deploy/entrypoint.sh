#!/bin/bash
# SaudeJa -- um container, dois processos (Passo 11, antecipado no Passo 4).
#
# Por que os dois na mesma imagem: o Streamlit chama o modelo EM PROCESSO
# (ADR-005 b), entao ele nao precisa da API para funcionar; a API existe ao
# lado dele para servir integracoes externas ao ecossistema Saude Ja (diagrama
# C2 do architecture.md). Dois servicos always-on separados custariam o dobro
# contra um orcamento de US$100/mes (BRIEFING.md).
#
# Sem supervisord/s6 de proposito: sao dois processos sem ordem de
# inicializacao entre si (nenhum depende do outro para subir), entao o custo
# de mais uma dependencia na imagem nao se paga. O que NAO pode acontecer e o
# container seguir "meio vivo" com um dos dois morto -- dai o `wait -n`, que
# derruba tudo assim que qualquer um dos dois sai.
set -uo pipefail

encerrar() {
    trap - TERM INT
    kill -TERM "${PID_API:-}" "${PID_UI:-}" 2>/dev/null || true
    wait 2>/dev/null
}
trap encerrar TERM INT

echo "[entrypoint] APP_ENV=${APP_ENV:-dev} PREDICT_BACKEND=${PREDICT_BACKEND:-processo}"
echo "[entrypoint] API em :${API_PORT:-8000} | UI em :${UI_PORT:-7860}"

python -m uvicorn api.main:app \
    --app-dir src \
    --host 0.0.0.0 \
    --port "${API_PORT:-8000}" &
PID_API=$!

python -m streamlit run src/ui/app.py \
    --server.address 0.0.0.0 \
    --server.port "${UI_PORT:-7860}" \
    --server.headless true &
PID_UI=$!

if wait -n; then
    codigo=0
else
    codigo=$?
fi
echo "[entrypoint] um dos processos terminou (codigo ${codigo}) -- encerrando o container"
encerrar
exit "${codigo}"
