#!/usr/bin/env bash
# Arranque automático con recuperación ante fallos (systemd).
#   bash scripts/instalar_servicios.sh              modo ESCRITORIO: el demo se abre al iniciar sesión
#   bash scripts/instalar_servicios.sh --kiosco     modo KIOSCO: sin escritorio, solo el demo (ahorra ~1 GB RAM)
#   bash scripts/instalar_servicios.sh --desinstalar  revierte TODO lo que este script cambió
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
U="$USER"; UID_="$(id -u)"
RESPALDO=/var/lib/crowpi
MODO="escritorio"
case "${1:-}" in --kiosco) MODO="kiosco" ;; --desinstalar) MODO="desinstalar" ;; esac

confirmar() { read -r -p "$1 [s/N] " r; [[ "$r" =~ ^[sS]$ ]]; }

desinstalar() {
  echo "Se va a: detener y borrar crowpi-llm, crowpi-demo (usuario) y crowpi-kiosco;"
  echo "restaurar el arranque gráfico, Xwrapper.config y el autologin si fueron cambiados."
  confirmar "¿Continuar?" || exit 0
  systemctl --user disable --now crowpi-demo.service 2>/dev/null || true
  rm -f ~/.config/systemd/user/crowpi-demo.service ~/.config/autostart/crowpi-demo.desktop
  systemctl --user daemon-reload || true
  sudo systemctl disable --now crowpi-kiosco.service crowpi-llm.service 2>/dev/null || true
  sudo rm -f /etc/systemd/system/crowpi-kiosco.service /etc/systemd/system/crowpi-llm.service
  [ -f $RESPALDO/default.target ] && sudo systemctl set-default "$(cat $RESPALDO/default.target)"
  [ -f $RESPALDO/Xwrapper.config ] && sudo cp $RESPALDO/Xwrapper.config /etc/X11/Xwrapper.config
  [ -f $RESPALDO/gdm3-custom.conf ] && sudo cp $RESPALDO/gdm3-custom.conf /etc/gdm3/custom.conf
  sudo rm -rf $RESPALDO
  sudo systemctl daemon-reload
  echo "Revertido. Reinicia para volver al escritorio normal."
}
[ "$MODO" = desinstalar ] && { desinstalar; exit 0; }

[ -x "$RAIZ/third_party/llama.cpp/build/bin/llama-server" ] || \
  echo "AVISO: el modelo de visión no está instalado; el demo arrancará en 'modo visión' (solo seguimiento)."

echo "Modo: $MODO. Cambios que se harán:"
echo "  + /etc/systemd/system/crowpi-llm.service   (modelo de visión, reinicio automático, arranca al encender)"
if [ "$MODO" = escritorio ]; then
  echo "  + ~/.config/systemd/user/crowpi-demo.service (demo con watchdog, reinicio automático)"
  echo "  + ~/.config/autostart/crowpi-demo.desktop    (lo lanza al iniciar sesión)"
  echo "  ? inicio de sesión automático en GDM para '$U' (se pregunta aparte; respaldo en $RESPALDO)"
else
  echo "  + paquetes: xinit xserver-xorg-legacy x11-xserver-utils"
  echo "  ~ /etc/X11/Xwrapper.config (permitir X sin escritorio; respaldo en $RESPALDO)"
  echo "  + /etc/systemd/system/crowpi-kiosco.service (X mínimo + demo en tty7, watchdog)"
  echo "  ~ arranque por defecto: multi-user.target (sin escritorio; se guarda el anterior)"
fi
echo "Revertir: bash scripts/instalar_servicios.sh --desinstalar"
confirmar "¿Aplicar?" || exit 0

sudo mkdir -p $RESPALDO
chmod +x "$RAIZ"/scripts/*.sh "$RAIZ/scripts/crowpi"

sudo tee /etc/systemd/system/crowpi-llm.service >/dev/null <<EOF
[Unit]
Description=CrowPi - modelo de vision local (llama-server)
After=local-fs.target
StartLimitIntervalSec=0

[Service]
User=$U
WorkingDirectory=$RAIZ
ExecStart=$RAIZ/scripts/iniciar_llm.sh
Restart=always
RestartSec=5
# el seguimiento tiene prioridad: el modelo cede CPU y es el primero en caer si falta memoria
Nice=5
OOMScoreAdjust=500

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now crowpi-llm.service

if [ "$MODO" = escritorio ]; then
  mkdir -p ~/.config/systemd/user ~/.config/autostart
  cat > ~/.config/systemd/user/crowpi-demo.service <<EOF
[Unit]
Description=CrowPi - demo (camara, seguimiento, pantalla)
StartLimitIntervalSec=0

[Service]
Type=notify
NotifyAccess=all
WorkingDirectory=$RAIZ
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 -m crowpi_demo
Restart=always
RestartSec=3
WatchdogSec=20
TimeoutStartSec=60
EOF
  cat > ~/.config/autostart/crowpi-demo.desktop <<EOF
[Desktop Entry]
Type=Application
Name=CrowPi demo
Exec=sh -c "systemctl --user import-environment DISPLAY XAUTHORITY; systemctl --user restart crowpi-demo.service"
X-GNOME-Autostart-enabled=true
EOF
  systemctl --user daemon-reload
  if [ -f /etc/gdm3/custom.conf ] && ! grep -q "^AutomaticLogin=$U" /etc/gdm3/custom.conf; then
    if confirmar "¿Activar inicio de sesión automático para '$U' (necesario para arrancar sin teclado)?"; then
      sudo cp /etc/gdm3/custom.conf $RESPALDO/gdm3-custom.conf
      sudo sed -i -e '/^\s*#\?\s*AutomaticLoginEnable/d' -e '/^\s*#\?\s*AutomaticLogin=/d' \
        -e "s/^\[daemon\]/[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=$U/" /etc/gdm3/custom.conf
    fi
  fi
  echo "Listo. Arranca ahora con:  scripts/crowpi iniciar   (o reinicia el equipo)"
else
  sudo apt-get install -y xinit xserver-xorg-legacy x11-xserver-utils
  [ -f /etc/X11/Xwrapper.config ] && [ ! -f $RESPALDO/Xwrapper.config ] && \
    sudo cp /etc/X11/Xwrapper.config $RESPALDO/Xwrapper.config
  printf 'allowed_users=anybody\nneeds_root_rights=yes\n' | sudo tee /etc/X11/Xwrapper.config >/dev/null
  sudo tee /etc/systemd/system/crowpi-kiosco.service >/dev/null <<EOF
[Unit]
Description=CrowPi - kiosco (X minimo + demo)
After=systemd-user-sessions.service crowpi-llm.service
Conflicts=getty@tty7.service
StartLimitIntervalSec=0

[Service]
User=$U
PAMName=login
TTYPath=/dev/tty7
StandardInput=tty
StandardOutput=journal
StandardError=journal
Type=notify
NotifyAccess=all
WatchdogSec=30
TimeoutStartSec=120
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=$RAIZ
ExecStart=/usr/bin/xinit $RAIZ/scripts/sesion_kiosco.sh -- :0 vt7 -nolisten tcp -nocursor -keeptty
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  [ -f $RESPALDO/default.target ] || systemctl get-default | sudo tee $RESPALDO/default.target >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable crowpi-kiosco.service
  sudo systemctl set-default multi-user.target
  echo "Listo. Reinicia el equipo: arrancará directo al demo, sin escritorio."
  echo "Para una terminal: Ctrl+Alt+F3. Para volver al escritorio: --desinstalar"
fi
