#!/usr/bin/env bash
# Paso 1: dependencias base + modelo de rostros. No toca swap, zram, nvpmodel ni systemd.
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RAIZ"

echo "== CrowPi demo: instalación base en $RAIZ"
[ "$(uname -m)" = "aarch64" ] || echo "AVISO: esto no parece el kit (arquitectura $(uname -m))."
[ -f /etc/nv_tegra_release ] && head -1 /etc/nv_tegra_release

echo "== Paquetes del sistema (apt)"
sudo apt-get update
sudo apt-get install -y python3-pip python3-yaml python3-pil python3-numpy \
  i2c-tools alsa-utils curl git cmake build-essential fonts-dejavu-core

echo "== Paquetes de Python (usuario)"
python3 -m pip install --user --upgrade smbus2
python3 -c "import Jetson.GPIO" 2>/dev/null || python3 -m pip install --user Jetson.GPIO

echo "== Verificando OpenCV (NO instalar opencv-python de pip: rompe CUDA/GStreamer)"
if python3 - <<'EOF'
import cv2
bi = cv2.getBuildInformation()
gst = any("GStreamer" in l and "YES" in l for l in bi.splitlines())
cuda = cv2.cuda.getCudaEnabledDeviceCount() if hasattr(cv2, "cuda") else 0
print(f"OpenCV {cv2.__version__} | GStreamer: {'sí' if gst else 'NO'} | CUDA: {'sí' if cuda else 'no (detector en CPU)'}")
raise SystemExit(0 if gst else 1)
EOF
then :; else
  echo "ERROR: OpenCV sin GStreamer. Instala el de JetPack: sudo apt-get install -y python3-opencv"
  echo "       y desinstala el de pip si existe: python3 -m pip uninstall -y opencv-python opencv-contrib-python"
  exit 1
fi

echo "== Modelo de detección de rostros"
PESOS=models/face/res10_300x300_ssd_iter_140000_fp16.caffemodel
if [ ! -f "$PESOS" ]; then
  LOCAL=$(find "$HOME" -maxdepth 5 -name res10_300x300_ssd_iter_140000_fp16.caffemodel 2>/dev/null | head -1 || true)
  if [ -n "$LOCAL" ]; then cp "$LOCAL" "$PESOS"; echo "copiado de $LOCAL"
  else curl -fL -o "$PESOS" https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20180205_fp16/res10_300x300_ssd_iter_140000_fp16.caffemodel
  fi
fi
ls -l models/face

echo "== Permisos: agregar $USER a los grupos i2c, gpio, audio y video"
for g in i2c gpio audio video; do
  getent group "$g" >/dev/null && sudo usermod -aG "$g" "$USER" || true
done
chmod +x scripts/*.sh scripts/crowpi scripts/*.py 2>/dev/null || true

cat <<EOF

Listo. Si es la primera vez, CIERRA SESIÓN o reinicia para que apliquen los grupos.
Siguientes pasos:
  1) Probar cámara:   bash scripts/prueba_camara.sh
  2) Probar servos:   python3 scripts/prueba_servos.py
  3) Seguimiento:     python3 -m crowpi_demo --sin-vlm
  4) Modelo visión:   bash scripts/instalar_llm.sh        (≈40-60 min, compila llama.cpp)
  5) Voz (opcional):  bash scripts/instalar_voz.sh [--stt]
EOF
