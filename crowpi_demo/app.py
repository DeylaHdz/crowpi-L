"""Aplicación principal: une cámara, rostros, gimbal, VLM, voz y pantalla.

Diseño para feria: el hilo principal (video + seguimiento + pantalla) nunca espera
al VLM ni a la voz. Si el VLM falla o no existe, el demo entra en "modo visión"
(solo seguimiento) y se recupera solo cuando el servicio vuelve.
"""
import argparse
import logging
import os
import queue
import signal
import sys
import threading
import time

from . import config
from .camera import Camara
from .face import DetectorRostros
from .gimbal import ControlGimbal
from .hw import BusI2C, SensorTactil
from .sysmon import Monitor, sd_notify
from .ui import ACENTO, AVISO, TENUE, Estado, Pantalla
from .vlm import AsistenteVLM, ClienteVLM
from .voice import Oido, Voz

log = logging.getLogger("app")
TECLAS_ENTER = {10, 13, 141}
SIN_FRAMES_SALIR_S = 25  # si la cámara se congela tanto tiempo, salir y que systemd reinicie


class App:
    def __init__(self, cfg, args):
        self.cfg = cfg
        self.args = args
        self.s = Estado()
        self.acciones = queue.Queue()
        self.salir = False
        self.fps = 0.0
        self.vlm_estado = "caido" if args.sin_vlm else "cargando"
        self.t_arranque = time.monotonic()
        self.t_ultimo_vlm = time.monotonic()
        self.t_vlm_caido = None

        if args.sin_gimbal:
            cfg["gimbal"]["habilitado"] = False
        self.camara = Camara(cfg)
        self.detector = DetectorRostros(cfg)
        self.gimbal = ControlGimbal(cfg)
        self.bus = BusI2C(cfg, al_boton=self._boton)
        self.voz = Voz(cfg)
        self.oido = Oido(cfg)
        self.asistente = AsistenteVLM(cfg, self._respuesta, self._fallo_vlm)
        self.cliente = ClienteVLM(cfg)
        self.monitor = Monitor(cfg, self._metricas)
        pin = cfg.get("controles", {}).get("sensor_tactil_bcm")
        self.tactil = SensorTactil(int(pin), lambda: self.acciones.put(("voz", "tactil"))) \
            if pin is not None else None
        self.pantalla = None if args.sin_pantalla else Pantalla(cfg)

    # ---------- eventos desde otros hilos ----------
    def _boton(self, n):
        self.acciones.put({1: ("describir", "boton"), 2: ("voz", "boton"),
                           3: ("centrar", None), 4: ("silencio", None)}[n])

    def _respuesta(self, texto, origen, seg):
        self.s.respuesta = texto
        self.s.pensando = False
        self.t_ultimo_vlm = time.monotonic()
        if origen != "auto" or self.cfg["vlm"].get("hablar_auto"):
            self.voz.decir(texto)

    def _fallo_vlm(self, error):
        self.s.pensando = False
        self.s.respuesta = "Me tardé más de lo normal en pensar. ¿Lo intentamos otra vez?"
        self.t_ultimo_vlm = time.monotonic()

    def _metricas(self):
        lat = self.asistente.latencias
        return {"fps": self.fps, "vlm_estado": self.vlm_estado,
                "vlm_ultimo": f"{lat[-1]:.1f}" if lat else "-",
                "vlm_promedio": f"{sum(lat) / len(lat):.1f}" if lat else "-"}

    def _vigilar_vlm(self):
        """Consulta /health cada 2 s en su propio hilo (nunca bloquea la pantalla)."""
        previo = None
        while not self.salir:
            est = self.cliente.salud()
            if est != previo:
                log.info("Estado del modelo de visión: %s", est)
                previo = est
            self.vlm_estado = est
            if est == "caido" and self.t_vlm_caido is None:
                self.t_vlm_caido = time.monotonic()
            elif est != "caido":
                self.t_vlm_caido = None
            time.sleep(2)

    def _leer_terminal(self):
        """Enter en la terminal = describir; texto = pregunta escrita (útil por SSH)."""
        for linea in sys.stdin:
            linea = linea.strip()
            if linea.lower() in ("q", "salir"):
                self.salir = True
                return
            self.acciones.put(("pregunta", linea) if linea else ("describir", "teclado"))

    def _escuchar(self):
        try:
            self.voz.callar()
            self.bus.bip()
            self.s.escuchando = True
            texto = self.oido.escuchar(al_nivel=lambda n: setattr(self.s, "nivel_mic", n))
        except Exception as e:
            log.error("Error al escuchar: %s", e)
            texto = ""
        finally:
            self.s.escuchando = False
        if texto:
            log.info("Pregunta por voz: %s", texto)
            self.acciones.put(("pregunta", texto))
        else:
            self.s.subtitulo = "(no alcancé a escucharte; acércate al micrófono)"

    # ---------- acciones (en el hilo principal) ----------
    def _pedir_vlm(self, frame, pregunta, origen, subtitulo):
        if frame is None:
            return
        if self.vlm_estado != "listo":
            self.s.respuesta = ("El asistente de visión se está preparando. "
                                "Mientras tanto, sigo tu rostro con la cámara.")
            return
        if self.asistente.pedir(frame, pregunta, origen):
            if subtitulo is not None:
                self.s.subtitulo = subtitulo
            self.s.pensando = True
            self.voz.callar()

    def _atender(self, accion, frame):
        tipo, dato = accion
        if tipo == "describir":
            self._pedir_vlm(frame, self.cfg["vlm"]["prompt_describir"], dato,
                            None if dato == "auto" else "¿Qué ves?")
        elif tipo == "pregunta":
            self._pedir_vlm(frame, dato + " (Contesta en español, máximo dos frases.)",
                            "pregunta", dato)
        elif tipo == "voz":
            if self.oido.disponible and not self.s.escuchando and not self.asistente.ocupado:
                threading.Thread(target=self._escuchar, daemon=True).start()
            elif not self.oido.disponible:
                self._atender(("describir", dato), frame)
        elif tipo == "centrar":
            self.gimbal.centrar()
        elif tipo == "silencio":
            self.voz.silencio = not self.voz.silencio
            if self.voz.silencio:
                self.voz.callar()
            log.info("Voz %s", "silenciada" if self.voz.silencio else "activada")

    def _tecla(self, k):
        if k < 0:
            return
        k8 = k & 0xFF
        if k8 in TECLAS_ENTER:
            self.acciones.put(("describir", "teclado"))
        elif k8 in (ord(" "), ord("v")):
            self.acciones.put(("voz", "teclado"))
        elif k8 == ord("c"):
            self.acciones.put(("centrar", None))
        elif k8 == ord("m"):
            self.acciones.put(("silencio", None))
        elif k8 == ord("f") and self.pantalla:
            self.pantalla.alternar_pantalla_completa()
        elif k8 in (27, ord("q")) and self.cfg["controles"].get("permitir_salir", True):
            self.salir = True

    # ---------- estado visible ----------
    def _actualizar_textos(self, hay_rostro, frame_ok):
        s, v = self.s, self.vlm_estado
        en_arranque = time.monotonic() - self.t_arranque
        max_arranque = float(self.cfg["vlm"].get("arranque_max_s", 300))
        caido_s = time.monotonic() - self.t_vlm_caido if self.t_vlm_caido else 0

        if s.inicio:
            fin_vlm = (v == "listo" or self.args.sin_vlm or en_arranque > max_arranque
                       or caido_s > 45)
            if frame_ok and fin_vlm:
                s.inicio = False
                log.info("Arranque terminado en %.1f s (modelo: %s)", en_arranque, v)
            gimbal = ("ok" if self.bus.ok else "espera") if self.cfg["gimbal"].get("habilitado") \
                else "error"
            s.lineas_inicio = [
                ("Cámara", "ok" if frame_ok else "espera"),
                (f"Detección de rostros ({self.detector.backend})", "ok"),
                ("Gimbal" if gimbal != "error" else "Gimbal desactivado", gimbal),
                ("Modelo de visión" + ("" if v == "listo" else " (cargando)"),
                 "ok" if v == "listo" else "espera"),
                ("Voz" if self.voz.disponible else "Voz: solo en pantalla",
                 "ok" if self.voz.disponible else "espera"),
            ]
            return

        respaldo = v != "listo"
        if respaldo:
            s.chip, s.chip_color = "Modo visión", AVISO
            s.aviso = ("El asistente de visión se está reiniciando. "
                       "El seguimiento sigue funcionando.") if not self.args.sin_vlm else ""
        elif not frame_ok:
            s.chip, s.chip_color = "Reconectando cámara", AVISO
        elif hay_rostro:
            s.chip, s.chip_color, s.aviso = "Siguiendo tu rostro", ACENTO, ""
        elif self.gimbal.modo == "buscando":
            s.chip, s.chip_color, s.aviso = "Buscando personas", TENUE, ""
        else:
            s.chip, s.chip_color, s.aviso = "Listo", TENUE, ""
        pista = "Presiona ENTER y te digo qué ve la cámara"
        if self.oido.disponible:
            pista += "  ·  Toca el sensor táctil para preguntarme por voz"
        s.pista = pista

        if s.escuchando:
            rgb = "escuchando"
        elif s.pensando:
            rgb = "pensando"
        elif self.voz.hablando:
            rgb = "hablando"
        elif respaldo:
            rgb = "respaldo"
        else:
            rgb = "siguiendo" if hay_rostro else "buscando"
        self.bus.poner_estado_rgb(rgb)

    # ---------- bucle principal ----------
    def correr(self):
        self.camara.start()
        self.bus.start()
        self.bus.poner_estado_rgb("iniciando")
        self.asistente.start()
        self.monitor.start()
        if self.tactil:
            self.tactil.start()
        if not self.args.sin_vlm:
            threading.Thread(target=self._vigilar_vlm, daemon=True).start()
        if sys.stdin and sys.stdin.isatty():
            threading.Thread(target=self._leer_terminal, daemon=True).start()
            print("Terminal: Enter = describir la escena; escribe una pregunta y Enter para "
                  "preguntar; q = salir.")
        sd_notify("READY=1")

        ultimo_n, caja, frame = -1, None, None
        t_fps, cuenta_fps = time.monotonic(), 0
        t_watchdog = 0.0
        intervalo_auto = float(self.cfg["vlm"].get("intervalo_auto_s", 0))
        while not self.salir:
            ahora = time.monotonic()
            f, n, t_frame = self.camara.ultimo()
            frame_ok = f is not None and ahora - t_frame < 2
            if n != ultimo_n and f is not None:
                ultimo_n, frame = n, f
                alto, ancho = frame.shape[:2]
                caja = self.detector.elegir_objetivo(self.detector.detectar(frame))
                objetivo = (caja[0] + caja[2] / 2, caja[1] + caja[3] / 2) if caja else None
                pan, tilt = self.gimbal.actualizar(objetivo, ancho, alto, ahora)
                self.bus.poner_angulos(pan, tilt)
                cuenta_fps += 1
            if ahora - t_fps >= 1.0:
                self.fps = cuenta_fps / (ahora - t_fps)
                t_fps, cuenta_fps = ahora, 0

            if f is not None and ahora - t_frame > SIN_FRAMES_SALIR_S:
                log.error("La cámara no entrega frames desde hace %d s; saliendo para que "
                          "systemd reinicie el demo", SIN_FRAMES_SALIR_S)
                self.cerrar()
                os._exit(3)  # el hilo de cámara puede estar bloqueado en GStreamer

            while not self.acciones.empty():
                self._atender(self.acciones.get_nowait(), frame)
            if (intervalo_auto > 0 and not self.s.inicio and self.vlm_estado == "listo"
                    and not self.asistente.ocupado and not self.s.escuchando
                    and ahora - self.t_ultimo_vlm > intervalo_auto):
                self.t_ultimo_vlm = ahora
                self._atender(("describir", "auto"), frame)

            self._actualizar_textos(caja is not None, frame_ok)
            if self.pantalla:
                self._tecla(self.pantalla.mostrar(self.s, frame if frame_ok else None,
                                                  caja if frame_ok else None, self.fps))
            else:
                time.sleep(0.005)
            if ahora - t_watchdog > 1.0:
                t_watchdog = ahora
                sd_notify("WATCHDOG=1")
        self.cerrar()

    def cerrar(self):
        log.info("Cerrando demo")
        sd_notify("STOPPING=1")
        self.voz.callar()
        self.bus.parar()
        self.bus.join(timeout=2)
        self.camara.parar()
        if self.tactil:
            self.tactil.parar()
        if self.pantalla:
            self.pantalla.cerrar()


def main():
    p = argparse.ArgumentParser(description="Demo CrowPi L AI Starter Kit")
    p.add_argument("--config", help="ruta a config.yaml")
    p.add_argument("--sin-vlm", action="store_true", help="solo seguimiento (probar gimbal)")
    p.add_argument("--sin-gimbal", action="store_true", help="no mover los servos")
    p.add_argument("--sin-pantalla", action="store_true", help="sin ventana (pruebas por SSH)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not args.sin_pantalla and not os.environ.get("DISPLAY"):
        log.error("No hay pantalla (variable DISPLAY vacía). Si estás por SSH usa "
                  "'DISPLAY=:0 python3 -m crowpi_demo' o '--sin-pantalla'.")
        sys.exit(2)
    cfg = config.cargar(args.config)
    app = App(cfg, args)
    signal.signal(signal.SIGTERM, lambda *_: setattr(app, "salir", True))
    try:
        app.correr()
    except KeyboardInterrupt:
        app.cerrar()
