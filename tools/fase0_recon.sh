#!/usr/bin/env bash
# Fase 0 — Reconocimiento del CrowPi L AI Starter Kit.
# SOLO LECTURA: no instala, no cambia configuración, no mueve servos.
# Única escritura: el reporte (~50 KB) en $HOME/fase0_reporte.txt
#
# Uso (en el kit, con la sesión gráfica abierta está bien):
#   bash fase0_recon.sh
# Algunas secciones piden sudo (nvpmodel, i2cdetect); si no das la contraseña
# se omiten y el resto sigue.

OUT="${1:-$HOME/fase0_reporte.txt}"
exec > >(tee "$OUT") 2>&1

sec() { printf '\n\n########## %s ##########\n' "$*"; }
run() { printf '\n$ %s\n' "$*"; timeout 20 bash -c "$*" 2>&1 || printf '(rc=%s)\n' "$?"; }

echo "Reporte Fase 0 — $(date -Is) — host $(hostname) — usuario $USER"
sudo -v 2>/dev/null && HAVE_SUDO=1 || HAVE_SUDO=0
echo "sudo disponible: $HAVE_SUDO"

sec "1. Sistema / JetPack / L4T"
run "cat /etc/nv_tegra_release"
run "dpkg-query -W -f='\${Package} \${Version}\n' nvidia-jetpack nvidia-l4t-core cuda-runtime-* libnvinfer10 tensorrt 2>/dev/null"
run "cat /proc/device-tree/model; echo"
run "uname -a"
run "lsb_release -ds"
run "ls /usr/local | grep -i cuda"
run "python3 --version; pip3 --version"

sec "2. Energía y temperatura"
if [ "$HAVE_SUDO" = 1 ]; then run "sudo nvpmodel -q --verbose | head -20"; else run "nvpmodel -q"; fi
run "cat /etc/nvpmodel.conf | grep -E 'POWER_MODEL|PM_CONFIG' | head -20"
run "for z in /sys/class/thermal/thermal_zone*; do echo \"\$(cat \$z/type): \$(( \$(cat \$z/temp)/1000 ))C\"; done"
run "timeout 3 tegrastats --interval 1000 | head -3"

sec "3. Almacenamiento (esperado: solo microSD, sin NVMe)"
run "lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL"
run "df -h -x tmpfs -x devtmpfs"
run "ls /dev/nvme* 2>/dev/null || echo 'sin NVMe'"
run "cat /sys/block/mmcblk*/device/name /sys/block/mmcblk*/device/date 2>/dev/null"
run "findmnt -no OPTIONS /"
run "du -sh \$HOME/.cache \$HOME/jetson_Course /var/log 2>/dev/null"

sec "4. Memoria, ZRAM y swap"
run "free -m"
run "swapon --show"
run "ls /sys/block | grep zram; for z in /sys/block/zram*; do echo \$z disksize=\$(cat \$z/disksize) alg=\$(cat \$z/comp_algorithm); done"
run "systemctl is-enabled nvzramconfig 2>/dev/null; systemctl is-active nvzramconfig 2>/dev/null"
run "sysctl vm.swappiness vm.vfs_cache_pressure"
run "grep -E 'MemTotal|MemAvailable|CmaTotal|CmaFree|SwapTotal|SwapFree' /proc/meminfo"
run "cat /proc/cmdline"

sec "5. Escritorio y servicios que consumen RAM"
run "systemctl get-default"
run "cat /etc/X11/default-display-manager 2>/dev/null; loginctl list-sessions"
run "echo \$XDG_SESSION_TYPE \$DESKTOP_SESSION"
run "ps -eo rss,comm --sort=-rss | head -25"
run "systemctl list-units --type=service --state=running --no-pager | head -60"
run "systemctl is-active docker containerd snapd 2>/dev/null"

sec "6. Cámara"
run "ls -l /dev/video* 2>/dev/null"
run "v4l2-ctl --list-devices 2>/dev/null"
run "dmesg 2>/dev/null | grep -i -E 'imx219|nvcsi|tegra-camrtc' | tail -15 || sudo -n dmesg | grep -i imx219 | tail -15"
run "ls /boot/*.dtbo 2>/dev/null; grep -i -E 'OVERLAYS|FDT' /boot/extlinux/extlinux.conf"
for id in 0 1; do
  run "gst-launch-1.0 -q nvarguscamerasrc sensor-id=$id num-buffers=5 ! 'video/x-raw(memory:NVMM),width=1280,height=720,framerate=60/1' ! fakesink && echo 'sensor-id=$id OK'"
done
run "python3 -c \"import cv2; print('OpenCV', cv2.__version__); bi=cv2.getBuildInformation(); print([l.strip() for l in bi.splitlines() if 'GStreamer' in l or 'NVIDIA CUDA' in l or 'cuDNN' in l]); print('CUDA devices:', cv2.cuda.getCudaEnabledDeviceCount())\""

sec "7. I2C (servos vía RP2040 en bus 7, addr 0x66 esperado)"
run "ls /dev/i2c-*"
run "id"
if command -v i2cdetect >/dev/null; then
  if [ "$HAVE_SUDO" = 1 ]; then run "sudo i2cdetect -y -r 7"; run "sudo i2cdetect -y -r 1"
  else run "i2cdetect -y -r 7"; fi
fi
run "python3 -c 'import smbus2, Jetson.GPIO; print(\"smbus2 y Jetson.GPIO OK\")'"

sec "8. Audio (micrófono USB integrado, bocina por salida del display)"
run "lsusb"
run "arecord -l"
run "aplay -l"
run "pactl list short sources 2>/dev/null; pactl list short sinks 2>/dev/null"
run "pactl get-default-sink 2>/dev/null; pactl get-default-source 2>/dev/null"
run "cat /proc/asound/cards"

sec "9. Software de Elecrow ya instalado (lecciones, 'Start AI Chat', módulo de voz)"
run "ls -la \$HOME \$HOME/Desktop 2>/dev/null"
run "grep -l -r -i -E 'ai.?chat|voice|elecrow' \$HOME/Desktop/*.desktop /usr/share/applications/*.desktop \$HOME/.local/share/applications/*.desktop 2>/dev/null"
run "for f in \$(grep -l -r -i -E 'ai.?chat|voice' \$HOME/Desktop/*.desktop /usr/share/applications/*.desktop \$HOME/.local/share/applications/*.desktop 2>/dev/null); do echo \"== \$f\"; grep -E '^(Name|Exec|Path)=' \$f; done"
run "find \$HOME /opt -maxdepth 4 -type f \\( -iname '*chat*.py' -o -iname '*voice*.py' -o -iname '*asr*.py' -o -iname '*tts*.py' -o -iname '*speech*.py' -o -iname '*ai_readme*' \\) 2>/dev/null | head -40"
run "grep -rl -i -E 'openai|api_key|dashscope|deepseek|baidu|xfyun|whisper|vosk|piper|pyaudio|sounddevice|ollama|llama' \$HOME --include=*.py 2>/dev/null | grep -v -E '/(\\.cache|\\.local/lib|site-packages)/' | head -40"
run "ls /dev/ttyTHS* /dev/ttyACM* /dev/ttyUSB* 2>/dev/null"

sec "10. Runtimes de IA ya presentes"
run "docker --version; docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' 2>/dev/null | head"
run "which llama-server llama-cli whisper-cli piper ollama 2>/dev/null"
run "pip3 list 2>/dev/null | grep -i -E 'torch|onnx|tensorrt|ultralytics|opencv|numpy|pygame|face|jetson|llama|whisper|piper|sounddevice|pyaudio|zmq|yaml'"
run "dpkg -l | grep -i -E 'tensorrt|cudnn|deepstream' | awk '{print \$2, \$3}' | head"

sec "11. Red (para confirmar que el demo no depende de ella)"
run "nmcli -t -f NAME,TYPE,DEVICE con show --active 2>/dev/null"

echo; echo "Listo. Reporte guardado en: $OUT"
