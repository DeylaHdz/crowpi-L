"""Detección de rostros: SSD res10 300x300 (el de las lecciones del kit) con OpenCV DNN.

Usa la GPU (CUDA FP16) si OpenCV fue compilado con CUDA; si no, cae a CPU.
También elige a quién seguir: prefiere al mismo rostro del frame anterior para
no saltar entre personas.
"""
import logging

import cv2
import numpy as np

from .config import ruta

log = logging.getLogger("rostros")


class DetectorRostros:
    def __init__(self, cfg):
        c = cfg["rostros"]
        self.conf_min = float(c.get("confianza_min", 0.55))
        self.entrada = tuple(int(v) for v in c.get("entrada", (640, 360)))
        if not hasattr(cv2.dnn, "readNetFromCaffe"):
            raise RuntimeError("Este OpenCV (5.x) ya no lee modelos Caffe. Usa el OpenCV 4.x "
                               "de JetPack (sudo apt-get install python3-opencv)")
        proto, pesos = ruta(c["prototxt"]), ruta(c["pesos"])
        if not proto.exists() or not pesos.exists():
            raise FileNotFoundError(
                f"Faltan los archivos del detector ({proto.name}, {pesos.name}). "
                "Corre scripts/instalar.sh")
        self.net = cv2.dnn.readNetFromCaffe(str(proto), str(pesos))
        self.backend = "CPU"
        if c.get("usar_gpu", True):
            try:
                if cv2.cuda.getCudaEnabledDeviceCount() > 0:
                    self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
                    self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA_FP16)
                    self.net.setInput(cv2.dnn.blobFromImage(
                        np.zeros((self.entrada[1], self.entrada[0], 3), np.uint8), 1.0,
                        self.entrada, (104, 117, 123)))
                    self.net.forward()  # prueba: si CUDA falla, lo sabemos aquí
                    self.backend = "GPU"
            except Exception as e:  # OpenCV sin CUDA o error del backend
                log.warning("Detector en GPU no disponible (%s); uso CPU", e)
                self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        log.info("Detector de rostros listo en %s", self.backend)
        self._previo = None  # (cx, cy, w, h) del objetivo anterior

    def detectar(self, frame):
        """Lista de rostros (x, y, w, h, confianza) en píxeles del frame."""
        h, w = frame.shape[:2]
        # entrada con la misma proporción que el video: aplastar 16:9 a 300x300
        # (como la lección original) pierde rostros medianos y lejanos
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, self.entrada), 1.0, self.entrada,
                                     (104, 117, 123), False, False)
        self.net.setInput(blob)
        det = self.net.forward()
        caras = []
        for i in range(det.shape[2]):
            conf = float(det[0, 0, i, 2])
            if conf < self.conf_min:
                continue
            x1, y1, x2, y2 = (det[0, 0, i, 3:7] * np.array([w, h, w, h])).astype(int)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w - 1, x2), min(h - 1, y2)
            if x2 - x1 > 10 and y2 - y1 > 10:
                caras.append((x1, y1, x2 - x1, y2 - y1, conf))
        return caras

    def elegir_objetivo(self, caras):
        """Elige el rostro a seguir y suaviza su caja. Devuelve (x, y, w, h) o None."""
        if not caras:
            self._previo = None
            return None
        if self._previo is None:
            elegido = max(caras, key=lambda c: c[2] * c[3])
        else:
            pcx, pcy, pw, _ = self._previo
            # el más cercano al anterior, penalizando rostros pequeños
            elegido = min(caras, key=lambda c: (
                ((c[0] + c[2] / 2 - pcx) ** 2 + (c[1] + c[3] / 2 - pcy) ** 2) ** 0.5
                - 0.5 * c[2]))
        x, y, w, h, _ = elegido
        cx, cy = x + w / 2, y + h / 2
        if self._previo is not None:
            a = 0.6  # suavizado exponencial para que el recuadro no tiemble
            pcx, pcy, pw, ph = self._previo
            cx, cy = a * cx + (1 - a) * pcx, a * cy + (1 - a) * pcy
            w, h = a * w + (1 - a) * pw, a * h + (1 - a) * ph
        self._previo = (cx, cy, w, h)
        return int(cx - w / 2), int(cy - h / 2), int(w), int(h)
