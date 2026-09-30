#!/usr/bin/env bash
# Arranca el modelo de visión (llama-server) solo en 127.0.0.1. Lo usa el servicio crowpi-llm.
# Variables opcionales: LLM_CTX (contexto, 4096), LLM_PUERTO (8080), LLM_HILOS (4)
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
ENV="$RAIZ/models/vlm/activo.env"
[ -f "$ENV" ] || { echo "No hay modelo instalado. Corre: bash scripts/instalar_llm.sh"; exit 1; }
# shellcheck disable=SC1090
source "$ENV"
exec "$RAIZ/third_party/llama.cpp/build/bin/llama-server" \
  -m "$MODELO" --mmproj "$MMPROJ" \
  -ngl all -c "${LLM_CTX:-4096}" -np 1 -t "${LLM_HILOS:-4}" \
  --host 127.0.0.1 --port "${LLM_PUERTO:-8080}" --no-webui
