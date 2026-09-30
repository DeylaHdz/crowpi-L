#!/usr/bin/env bash
# Paso 2: compila llama.cpp con CUDA y descarga el modelo visión-lenguaje.
# Uso: bash scripts/instalar_llm.sh [qwen|lfm]
#   qwen (predeterminado) = Qwen3-VL-2B-Instruct Q4_K_M  (~1.6 GB, mejor español)
#   lfm                   = LFM2.5-VL-1.6B Q4_K_M        (~1.3 GB, usa menos RAM)
# Variables: LLAMA_REF (versión de llama.cpp, por defecto v0.5.0), JOBS (hilos de compilación)
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RAIZ"
MODELO="${1:-qwen}"
LLAMA_REF="${LLAMA_REF:-v0.5.0}"
JOBS="${JOBS:-4}"
LLAMA_DIR="$RAIZ/third_party/llama.cpp"

case "$MODELO" in
  qwen) REPO=Qwen/Qwen3-VL-2B-Instruct-GGUF
        GGUF=Qwen3VL-2B-Instruct-Q4_K_M.gguf
        MMPROJ=mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf ;;
  lfm)  REPO=LiquidAI/LFM2.5-VL-1.6B-GGUF
        GGUF=LFM2.5-VL-1.6B-Q4_K_M.gguf
        MMPROJ=mmproj-LFM2.5-VL-1.6b-Q8_0.gguf ;;
  *) echo "Modelo desconocido: $MODELO (usa qwen o lfm)"; exit 1 ;;
esac

export PATH=/usr/local/cuda/bin:$PATH
command -v nvcc >/dev/null || { echo "ERROR: no encuentro nvcc (CUDA). ¿Está instalado JetPack completo?"; exit 1; }

if [ ! -x "$LLAMA_DIR/build/bin/llama-server" ]; then
  echo "== Compilando llama.cpp $LLAMA_REF con CUDA (tarda 30-60 min; no apagues el equipo)"
  mkdir -p third_party
  [ -d "$LLAMA_DIR" ] || git clone --depth 1 --branch "$LLAMA_REF" https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
  cmake -S "$LLAMA_DIR" -B "$LLAMA_DIR/build" \
    -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=87 -DCMAKE_BUILD_TYPE=Release \
    -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
  cmake --build "$LLAMA_DIR/build" --target llama-server -j "$JOBS"
  # libera espacio de la microSD: objetos intermedios
  find "$LLAMA_DIR/build" -name '*.o' -delete 2>/dev/null || true
fi
"$LLAMA_DIR/build/bin/llama-server" --version 2>&1 | head -3 || true

echo "== Descargando modelo $MODELO"
mkdir -p models/vlm
for f in "$GGUF" "$MMPROJ"; do
  if [ ! -s "models/vlm/$f" ]; then
    curl -fL -C - -o "models/vlm/$f.part" "https://huggingface.co/$REPO/resolve/main/$f"
    mv "models/vlm/$f.part" "models/vlm/$f"
  fi
done
cat > models/vlm/activo.env <<EOF
MODELO=$RAIZ/models/vlm/$GGUF
MMPROJ=$RAIZ/models/vlm/$MMPROJ
EOF
ls -lh models/vlm
echo
echo "Listo. Modelo activo: $MODELO. Pruébalo con:  bash scripts/iniciar_llm.sh"
echo "(en otra terminal)                           python3 -m crowpi_demo"
