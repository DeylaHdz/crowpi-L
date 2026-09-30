"""Captura única de la cámara. Es el ÚNICO lugar que abre la cámara CSI.

Un hilo lee frames sin parar (appsink drop=true, así nunca se acumula retraso) y
guarda solo el más reciente. Tracker, pantalla y VLM leen de aquí.
Si la cámara deja de entregar frames, se cierra y se vuelve a abrir sola.
"""
import logging
import threading
import time

import cv2

log = logging.getLogger("camara")


def pipeline_csi(c):
    return (
        f"nvarguscamerasrc sensor-id={c['sensor_id']} ! "
        f"video/x-raw(memory:NVMM), width={c['captura_ancho']}, height={c['captura_alto']}, "
        f"framerate={c['fps']}/1, format=NV12 ! "
        f"nvvidconv flip-method={c['flip']} ! "
        f"video/x-raw, width={c['salida_ancho']}, height={c['salida_alto']}, format=BGRx ! "
        "videoconvert ! video/x-raw, format=BGR ! "
        "appsink drop=true max-buffers=1 sync=false"
    )


class Camara(threading.Thread):
    def __init__(self, cfg):
        super().__init__(name="camara", daemon=True)
        self.c = cfg["camara"]
        self._lock = threading.Lock()
        self._frame = None
        self._n = 0
        self._t = 0.0
        self._parar = threading.Event()
        self.estado = "iniciando"  # iniciando | ok | reconectando
        self.reconexiones = 0

    def _abrir(self):
        if self.c.get("fuente", "csi") == "usb":
            cap = cv2.VideoCapture(int(self.c.get("usb_dispositivo", 0)))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.c["salida_ancho"])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.c["salida_alto"])
            return cap
        return cv2.VideoCapture(pipeline_csi(self.c), cv2.CAP_GSTREAMER)

    def run(self):
        limite = float(self.c.get("reconectar_tras_s", 3))
        while not self._parar.is_set():
            cap = self._abrir()
            if not cap.isOpened():
                self.estado = "reconectando"
                log.error("No se pudo abrir la cámara; reintento en 2 s "
                          "(si persiste: sudo systemctl restart nvargus-daemon)")
                cap.release()
                self._parar.wait(2)
                continue
            log.info("Cámara abierta")
            t_ok = time.monotonic()
            while not self._parar.is_set():
                ok, frame = cap.read()
                ahora = time.monotonic()
                if not ok or frame is None:
                    if ahora - t_ok > limite:
                        log.warning("Sin frames por %.1f s; reabriendo cámara", ahora - t_ok)
                        break
                    time.sleep(0.01)
                    continue
                if self.c.get("fuente") == "usb" and frame.shape[1] != self.c["salida_ancho"]:
                    frame = cv2.resize(frame, (self.c["salida_ancho"], self.c["salida_alto"]))
                t_ok = ahora
                self.estado = "ok"
                with self._lock:
                    self._frame = frame
                    self._n += 1
                    self._t = ahora
            cap.release()
            if not self._parar.is_set():
                self.estado = "reconectando"
                self.reconexiones += 1
                self._parar.wait(1)

    def ultimo(self):
        """(frame, número de frame, instante monotónico). frame puede ser None."""
        with self._lock:
            return self._frame, self._n, self._t

    def parar(self):
        self._parar.set()
