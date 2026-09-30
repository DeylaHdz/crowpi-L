"""Cliente del modelo visión-lenguaje (llama-server de llama.cpp, API OpenAI local).

El modelo corre en su propio servicio (crowpi-llm) y queda residente en memoria.
Aquí solo se envía un JPEG pequeño del frame + la pregunta, y se limpia la respuesta
(español, máximo 2 frases). Todo por 127.0.0.1: nada sale del maletín.
"""
import base64
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request

import cv2

log = logging.getLogger("vlm")

_PALABRAS_EN = set("the a an is are this that there with of and in on image shows person wearing "
                   "i see background looks appears".split())
_PALABRAS_ES = set("el la los las un una es son hay con de y en se veo imagen persona lleva "
                   "fondo parece que del al".split())


def parece_ingles(texto):
    palabras = re.findall(r"[a-záéíóúñ]+", texto.lower())
    en = sum(p in _PALABRAS_EN for p in palabras)
    es = sum(p in _PALABRAS_ES for p in palabras)
    return en > es + 1


def limpiar(texto, max_frases=2):
    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.S)
    texto = re.sub(r"[*#_`>]+", "", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    frases = re.split(r"(?<=[.!?])\s+", texto)
    return " ".join(frases[:max_frases]).strip()


class ClienteVLM:
    def __init__(self, cfg):
        self.v = cfg["vlm"]
        self.url = self.v["url"].rstrip("/")

    def salud(self):
        """'listo' | 'cargando' | 'caido'"""
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=1.5) as r:
                return "listo" if r.status == 200 else "cargando"
        except urllib.error.HTTPError as e:
            return "cargando" if e.code == 503 else "caido"
        except Exception:
            return "caido"

    def _imagen_b64(self, frame):
        ancho = int(self.v.get("ancho_imagen", 640))
        h, w = frame.shape[:2]
        if w > ancho:
            frame = cv2.resize(frame, (ancho, int(h * ancho / w)), interpolation=cv2.INTER_AREA)
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return base64.b64encode(jpg.tobytes()).decode()

    def _chat(self, img_b64, pregunta, extra_sistema=""):
        cuerpo = {
            "messages": [
                {"role": "system", "content": self.v["prompt_sistema"] + extra_sistema},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + img_b64}},
                    {"type": "text", "text": pregunta},
                ]},
            ],
            "max_tokens": int(self.v.get("max_tokens", 90)),
            "temperature": float(self.v.get("temperatura", 0.2)),
            "stream": False,
        }
        req = urllib.request.Request(self.url + "/v1/chat/completions",
                                     data=json.dumps(cuerpo).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=float(self.v.get("timeout_s", 60))) as r:
            datos = json.load(r)
        return datos["choices"][0]["message"]["content"] or ""

    def preguntar(self, frame, pregunta):
        """Devuelve (respuesta_limpia, segundos). Lanza excepción si el servicio falla."""
        t0 = time.monotonic()
        img = self._imagen_b64(frame)
        texto = limpiar(self._chat(img, pregunta))
        if not texto or parece_ingles(texto):
            log.info("Respuesta vacía o en inglés; reintento forzando español")
            texto = limpiar(self._chat(img, pregunta + " Responde solo en español.",
                                       " IMPORTANTE: contesta únicamente en español."))
        return texto or "No alcancé a distinguir bien la imagen. ¿Lo intentamos de nuevo?", \
            time.monotonic() - t0


class AsistenteVLM(threading.Thread):
    """Hilo que atiende una petición a la vez; si llega otra mientras piensa, se ignora."""

    def __init__(self, cfg, al_responder, al_fallar):
        super().__init__(name="vlm", daemon=True)
        self.cliente = ClienteVLM(cfg)
        self.al_responder = al_responder
        self.al_fallar = al_fallar
        self._pedido = None
        self._cv = threading.Condition()
        self.ocupado = False
        self.latencias = []

    def pedir(self, frame, pregunta, origen):
        with self._cv:
            if self.ocupado or self._pedido is not None:
                return False
            self._pedido = (frame.copy(), pregunta, origen)
            self.ocupado = True
            self._cv.notify()
            return True

    def run(self):
        while True:
            with self._cv:
                while self._pedido is None:
                    self._cv.wait()
                frame, pregunta, origen = self._pedido
            try:
                texto, seg = self.cliente.preguntar(frame, pregunta)
                self.latencias = (self.latencias + [seg])[-50:]
                log.info("VLM %.1f s (%s): %s", seg, origen, texto)
                self.al_responder(texto, origen, seg)
            except Exception as e:
                log.error("Fallo del VLM: %s", e)
                self.al_fallar(str(e))
            finally:
                with self._cv:
                    self._pedido = None
                    self.ocupado = False
