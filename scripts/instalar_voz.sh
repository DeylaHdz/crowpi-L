#!/usr/bin/env bash
# Paso 3 (opcional): voz local.
#   sin opciones : TTS con piper + voz es_MX (la cámara "dice" lo que ve)
#   --stt        : además compila whisper.cpp con CUDA para preguntas por micrófono
#   --stt-small  : igual pero con el modelo "small" (mejor precisión, ~+350 MB RAM)
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RAIZ"
STT=""; MODELO_W=ggml-base.bin
for a in "$@"; do
  case "$a" in
    --stt) STT=1 ;;
    --stt-small) STT=1; MODELO_W=ggml-small-q5_1.bin ;;
  esac
done
mkdir -p third_party models/voz

baja() {  # baja URL DESTINO
  [ -s "$2" ] && return 0
  curl -fL -C - -o "$2.part" "$1" && mv "$2.part" "$2"
}

echo "== TTS: piper (binario aarch64) + voz es_MX-claude-high"
if [ ! -x third_party/piper/piper ]; then
  baja https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz third_party/piper.tar.gz
  tar -xzf third_party/piper.tar.gz -C third_party && rm third_party/piper.tar.gz
fi
V=https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/claude/high
baja "$V/es_MX-claude-high.onnx" models/voz/es_MX-claude-high.onnx
baja "$V/es_MX-claude-high.onnx.json" models/voz/es_MX-claude-high.onnx.json
echo "Prueba de voz (deberías escucharla por la bocina del kit):"
echo "Hola, soy la cámara inteligente del CrowPi L AI Starter Kit." | \
  (cd third_party/piper && ./piper --model "$RAIZ/models/voz/es_MX-claude-high.onnx" --output_raw 2>/dev/null) | \
  aplay -q -r 22050 -f S16_LE -t raw -c 1 || echo "AVISO: no se pudo reproducir; revisa 'aplay -l' y la sección de audio del README."

if [ -n "$STT" ]; then
  echo "== STT: whisper.cpp con CUDA + $MODELO_W"
  export PATH=/usr/local/cuda/bin:$PATH
  W=third_party/whisper.cpp
  if [ ! -x "$W/build/bin/whisper-cli" ]; then
    [ -d "$W" ] || git clone --depth 1 --branch "${WHISPER_REF:-v1.9.4}" https://github.com/ggml-org/whisper.cpp "$W"
    cmake -S "$W" -B "$W/build" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=87 -DCMAKE_BUILD_TYPE=Release \
      -DWHISPER_BUILD_TESTS=OFF
    cmake --build "$W/build" --target whisper-cli -j "${JOBS:-4}"
    find "$W/build" -name '*.o' -delete 2>/dev/null || true
  fi
  baja "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$MODELO_W" "models/voz/$MODELO_W"
  if [ "$MODELO_W" != ggml-base.bin ]; then
    echo "Recuerda poner en config.yaml ->  voz.whisper_modelo: models/voz/$MODELO_W"
  fi
  echo "Micrófonos detectados:"; arecord -l || true
fi
echo "Listo."
