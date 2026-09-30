"""Voz 100% local: TTS con piper y STT con whisper.cpp.

Ambos son opcionales. Si no están instalados, o no hay micrófono, el demo sigue
funcionando: la respuesta se muestra en pantalla y se pregunta con Enter.
El audio grabado va a /dev/shm (RAM), nunca a la microSD.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import wave

import numpy as np

from .config import ruta

log = logging.getLogger("voz")
WAV_TMP = "/dev/shm/crowpi_pregunta.wav"


def _pulse_activo():
    return shutil.which("pactl") is not None and \
        subprocess.run(["pactl", "info"], capture_output=True, timeout=3).returncode == 0


class Voz:
    """Habla un texto con piper -> aplay. Una frase nueva interrumpe la anterior."""

    def __init__(self, cfg):
        v = cfg["voz"]
        self.bin = ruta(v["piper_bin"])
        self.modelo = ruta(v["piper_voz"])
        self.salida = v.get("salida_audio") or ""
        self.disponible = (v.get("tts", "auto") != "no" and self.bin.exists()
                           and self.modelo.exists() and shutil.which("aplay") is not None)
        self.silencio = False
        self._procs = []
        self._lock = threading.Lock()
        self.hablando = False
        self.tasa = 22050
        if self.disponible:
            try:
                with open(str(self.modelo) + ".json", encoding="utf-8") as f:
                    self.tasa = int(json.load(f)["audio"]["sample_rate"])
            except Exception:
                pass
            log.info("TTS listo (piper, %s)", self.modelo.name)
        else:
            log.info("TTS no disponible: las respuestas solo se mostrarán en pantalla")

    def callar(self):
        with self._lock:
            for p in self._procs:
                if p.poll() is None:
                    p.kill()
            self._procs = []
            self.hablando = False

    def decir(self, texto):
        if not self.disponible or self.silencio or not texto:
            return
        self.callar()
        threading.Thread(target=self._decir, args=(texto,), daemon=True).start()

    def _decir(self, texto):
        aplay = ["aplay", "-q", "-r", str(self.tasa), "-f", "S16_LE", "-t", "raw", "-c", "1"]
        if self.salida:
            aplay += ["-D", self.salida]
        try:
            with self._lock:
                self.hablando = True
                p1 = subprocess.Popen([str(self.bin), "--model", str(self.modelo), "--output_raw"],
                                      cwd=str(self.bin.parent), stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                p2 = subprocess.Popen(aplay, stdin=p1.stdout, stderr=subprocess.DEVNULL)
                p1.stdout.close()
                self._procs = [p1, p2]
            p1.stdin.write(texto.encode("utf-8"))
            p1.stdin.close()
            p2.wait(timeout=60)
        except Exception as e:
            log.warning("No se pudo reproducir voz: %s", e)
        finally:
            with self._lock:
                self.hablando = False


class Oido:
    """Graba una pregunta (hasta silencio) y la transcribe con whisper.cpp."""

    def __init__(self, cfg):
        v = cfg["voz"]
        self.v = v
        self.bin = ruta(v["whisper_bin"])
        self.modelo = ruta(v["whisper_modelo"])
        self.dispositivo = None
        self.disponible = False
        if v.get("stt", "auto") == "no":
            return
        if not (self.bin.exists() and self.modelo.exists() and shutil.which("arecord")):
            log.info("STT no instalado (whisper.cpp); preguntas por voz desactivadas")
            return
        self.dispositivo = self._buscar_microfono(v.get("microfono", "auto"))
        self.disponible = self.dispositivo is not None
        log.info("Micrófono: %s", self.dispositivo or "no encontrado (usa Enter para preguntar)")

    @staticmethod
    def _buscar_microfono(conf):
        if conf and conf != "auto":
            return conf
        try:
            salida = subprocess.run(["arecord", "-l"], capture_output=True, text=True,
                                    timeout=5).stdout
        except Exception:
            return None
        tarjetas = re.findall(r"card (\d+): [^\n]*?\[([^\]]*)\], device (\d+)", salida)
        if not tarjetas:
            return None
        if _pulse_activo():
            return "default"  # con escritorio, PulseAudio es dueño de la tarjeta
        usb = [t for t in tarjetas if "usb" in t[1].lower()]
        card, _, dev = (usb or tarjetas)[0]
        return f"plughw:{card},{dev}"

    def escuchar(self, al_nivel=None):
        """Graba hasta silencio o grabar_max_s. Devuelve el texto (o '')."""
        tasa, bloque = 16000, 1600  # bloques de 100 ms
        max_bloques = int(float(self.v["grabar_max_s"]) * 10)
        fin_silencio = int(float(self.v["silencio_fin_s"]) * 10)
        umbral = float(self.v["umbral_voz"])
        proc = subprocess.Popen(["arecord", "-q", "-D", self.dispositivo, "-f", "S16_LE",
                                 "-r", str(tasa), "-c", "1", "-t", "raw"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        audio, hubo_voz, silencio = [], False, 0
        try:
            for _ in range(max_bloques):
                datos = proc.stdout.read(bloque * 2)
                if not datos:
                    break
                muestras = np.frombuffer(datos, dtype=np.int16)
                audio.append(muestras)
                nivel = float(np.sqrt(np.mean((muestras / 32768.0) ** 2)))
                if al_nivel:
                    al_nivel(nivel)
                if nivel > umbral:
                    hubo_voz, silencio = True, 0
                elif hubo_voz:
                    silencio += 1
                    if silencio >= fin_silencio:
                        break
        finally:
            proc.kill()
            proc.wait()
        if not hubo_voz:
            return ""
        with wave.open(WAV_TMP, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(tasa)
            w.writeframes(np.concatenate(audio).tobytes())
        try:
            r = subprocess.run([str(self.bin), "-m", str(self.modelo), "-f", WAV_TMP,
                                "-l", "es", "-nt", "-np", "-t", "4"],
                               capture_output=True, text=True, timeout=30)
            texto = re.sub(r"\[.*?\]|\(.*?\)", "", r.stdout)  # quita [música], (ruido)...
            return re.sub(r"\s+", " ", texto).strip()
        finally:
            try:
                os.remove(WAV_TMP)
            except OSError:
                pass
