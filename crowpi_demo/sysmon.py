"""Watchdog de systemd, métricas y alerta de swap. Solo escribe al log (journald)."""
import logging
import os
import socket
import threading
import time
from glob import glob

log = logging.getLogger("sistema")


def sd_notify(mensaje):
    """Aviso a systemd (READY=1, WATCHDOG=1...). No hace nada si no corre bajo systemd."""
    destino = os.environ.get("NOTIFY_SOCKET")
    if not destino:
        return
    if destino.startswith("@"):
        destino = "\0" + destino[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(destino)
            s.sendall(mensaje.encode())
    except OSError:
        pass


def memoria_mb():
    info = {}
    with open("/proc/meminfo") as f:
        for linea in f:
            k, v = linea.split(":")
            info[k] = int(v.split()[0]) // 1024
    usada = info["MemTotal"] - info["MemAvailable"]
    swap = info.get("SwapTotal", 0) - info.get("SwapFree", 0)
    return usada, info["MemTotal"], swap


def temperatura_c():
    maxima = None
    for z in glob("/sys/class/thermal/thermal_zone*"):
        try:
            with open(z + "/type") as f:
                tipo = f.read().strip().lower()
            with open(z + "/temp") as f:
                t = int(f.read()) / 1000
        except (OSError, ValueError):
            continue
        if any(k in tipo for k in ("cpu", "gpu", "soc", "tj")) and 0 < t < 125:
            maxima = t if maxima is None else max(maxima, t)
    return maxima


def _paginas_swap():
    total = 0
    with open("/proc/vmstat") as f:
        for linea in f:
            if linea.startswith(("pswpin ", "pswpout ")):
                total += int(linea.split()[1])
    return total


class Monitor(threading.Thread):
    def __init__(self, cfg, obtener_estado):
        super().__init__(name="monitor", daemon=True)
        s = cfg.get("sistema", {})
        self.cada = float(s.get("metricas_cada_s", 60))
        self.umbral_swap = float(s.get("alerta_swap_paginas_s", 256))
        self.obtener_estado = obtener_estado
        self.alerta_swap = False

    def run(self):
        try:
            previo, t_previo = _paginas_swap(), time.monotonic()
        except OSError:
            return  # no es Linux
        t_metricas = time.monotonic()
        seg_alto = 0.0
        while True:
            time.sleep(5)
            ahora = time.monotonic()
            actual = _paginas_swap()
            tasa = (actual - previo) / max(ahora - t_previo, 1e-3)
            previo, t_previo = actual, ahora
            seg_alto = seg_alto + 5 if tasa > self.umbral_swap else 0
            if seg_alto >= 60 and not self.alerta_swap:
                self.alerta_swap = True
                log.warning("ALERTA_SWAP: el sistema usa swap de forma constante (%.0f páginas/s). "
                            "El modelo es demasiado grande: cambia a LFM2.5-VL-1.6B "
                            "(scripts/instalar_llm.sh lfm).", tasa)
            elif seg_alto == 0:
                self.alerta_swap = False
            if ahora - t_metricas >= self.cada:
                t_metricas = ahora
                usada, total, swap = memoria_mb()
                temp = temperatura_c()
                e = self.obtener_estado()
                log.info("METRICAS fps=%.1f vlm_ultimo_s=%s vlm_promedio_s=%s vlm=%s "
                         "ram_mb=%d/%d swap_mb=%d swap_pag_s=%.0f temp_c=%s",
                         e["fps"], e["vlm_ultimo"], e["vlm_promedio"], e["vlm_estado"],
                         usada, total, swap, tasa, f"{temp:.1f}" if temp else "?")
