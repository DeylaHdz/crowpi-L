#!/usr/bin/env bash
# Ajustes de sistema OPCIONALES, cada uno reversible. Todos piden confirmación.
#   bash scripts/sistema.sh estado
#   bash scripts/sistema.sh memoria  aplicar|revertir   ZRAM off + swapfile 4 GB en microSD (swappiness 10)
#   bash scripts/sistema.sh logs     aplicar|revertir   journald limitado a 50 MB (menos escrituras en la SD)
#   bash scripts/sistema.sh energia  aplicar|revertir   modo de energía MAXN SUPER
set -euo pipefail
RESPALDO=/var/lib/crowpi
SWAP=/swapfile_crowpi
confirmar() { read -r -p "$1 [s/N] " r; [[ "$r" =~ ^[sS]$ ]]; }

estado() {
  echo "== Memoria";  free -m; swapon --show
  echo "== ZRAM";     systemctl is-enabled nvzramconfig 2>/dev/null || echo "(sin nvzramconfig)"
  echo "== swappiness: $(sysctl -n vm.swappiness)"
  echo "== Energía";  sudo nvpmodel -q 2>/dev/null | tail -2
  echo "== journald"; journalctl --disk-usage
}

memoria() {
  case "$1" in
  aplicar)
    echo "Se va a: desactivar ZRAM (nvzramconfig), crear $SWAP de 4 GB en la microSD,"
    echo "activarlo al arrancar (/etc/fstab) y poner vm.swappiness=10 (swap solo de respaldo)."
    confirmar "¿Aplicar?" || return 0
    sudo mkdir -p $RESPALDO
    sudo systemctl disable --now nvzramconfig 2>/dev/null || true
    if [ ! -f $SWAP ]; then
      sudo fallocate -l 4G $SWAP && sudo chmod 600 $SWAP && sudo mkswap $SWAP >/dev/null
    fi
    sudo swapon $SWAP 2>/dev/null || true
    grep -q "^$SWAP " /etc/fstab || echo "$SWAP none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
    echo "vm.swappiness=10" | sudo tee /etc/sysctl.d/90-crowpi.conf >/dev/null
    sudo sysctl -q -p /etc/sysctl.d/90-crowpi.conf
    swapon --show ;;
  revertir)
    confirmar "¿Quitar el swapfile y reactivar ZRAM?" || return 0
    sudo swapoff $SWAP 2>/dev/null || true
    sudo sed -i "\#^$SWAP #d" /etc/fstab
    sudo rm -f $SWAP /etc/sysctl.d/90-crowpi.conf
    sudo sysctl -q vm.swappiness=60
    sudo systemctl enable --now nvzramconfig 2>/dev/null || true
    swapon --show ;;
  esac
}

logs() {
  local f=/etc/systemd/journald.conf.d/90-crowpi.conf
  case "$1" in
  aplicar)
    echo "Se va a crear $f: SystemMaxUse=50M, RuntimeMaxUse=30M, SyncIntervalSec=5m."
    confirmar "¿Aplicar?" || return 0
    sudo mkdir -p "$(dirname $f)"
    printf '[Journal]\nSystemMaxUse=50M\nRuntimeMaxUse=30M\nSyncIntervalSec=5m\n' | sudo tee $f >/dev/null
    sudo systemctl restart systemd-journald
    sudo journalctl --vacuum-size=50M ;;
  revertir)
    sudo rm -f $f && sudo systemctl restart systemd-journald && echo "Revertido." ;;
  esac
}

energia() {
  case "$1" in
  aplicar)
    local id
    id=$(grep -oP 'POWER_MODEL ID=\K\d+(?= NAME=MAXN_SUPER)' /etc/nvpmodel.conf | head -1 || true)
    [ -n "$id" ] || { echo "Este equipo no tiene modo MAXN_SUPER en /etc/nvpmodel.conf"; return 1; }
    local actual
    actual=$(sudo nvpmodel -q | grep -oP '^\d+$' | tail -1 || true)
    echo "Se va a cambiar el modo de energía de '$actual' a MAXN_SUPER (id $id). Más FPS, más calor:"
    echo "asegúrate de que el ventilador funcione."
    confirmar "¿Aplicar?" || return 0
    sudo mkdir -p $RESPALDO
    [ -f $RESPALDO/nvpmodel ] || echo "$actual" | sudo tee $RESPALDO/nvpmodel >/dev/null
    yes | sudo nvpmodel -m "$id" || true
    sudo nvpmodel -q ;;
  revertir)
    [ -f $RESPALDO/nvpmodel ] || { echo "No hay modo guardado."; return 1; }
    yes | sudo nvpmodel -m "$(cat $RESPALDO/nvpmodel)" || true
    sudo rm -f $RESPALDO/nvpmodel
    sudo nvpmodel -q ;;
  esac
}

case "${1:-estado}" in
  estado)  estado ;;
  memoria|logs|energia)
    [[ "${2:-}" =~ ^(aplicar|revertir)$ ]] || { echo "Uso: $0 $1 aplicar|revertir"; exit 1; }
    "$1" "$2" ;;
  *) echo "Uso: $0 estado | memoria|logs|energia aplicar|revertir"; exit 1 ;;
esac
