#!/usr/bin/env bash
# Prueba rápida de la cámara CSI: mide FPS durante 5 s y (si hay pantalla) muestra video 5 s.
# Uso: bash scripts/prueba_camara.sh [sensor_id]
ID="${1:-0}"
echo "== sensor-id=$ID: 300 frames a 1280x720@60 (debe tardar ~5 s)"
time gst-launch-1.0 -q nvarguscamerasrc sensor-id="$ID" num-buffers=300 ! \
  'video/x-raw(memory:NVMM),width=1280,height=720,framerate=60/1' ! fakesink \
  && echo "OK: la cámara funciona" \
  || { echo "FALLÓ. Prueba: sudo systemctl restart nvargus-daemon  y/o  sensor-id 1"; exit 1; }
if [ -n "${DISPLAY:-}" ]; then
  echo "== Vista previa 5 s"
  timeout 5 gst-launch-1.0 -q nvarguscamerasrc sensor-id="$ID" ! \
    'video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1' ! nvvidconv ! xvimagesink sync=false || true
fi
