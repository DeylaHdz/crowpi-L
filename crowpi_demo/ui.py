"""Pantalla de feria a pantalla completa (OpenCV + Pillow, sin navegador: ahorra RAM).

Los textos (con acentos) se dibujan con Pillow solo cuando cambian; cada frame solo
se pega el video, el recuadro, la animación y los FPS, que es barato.
"""
import math
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

VENTANA = "CrowPi L AI Starter Kit"

# Paleta (RGB para Pillow)
FONDO = (13, 19, 27)
PANEL = (23, 32, 44)
BORDE = (38, 52, 68)
ACENTO = (0, 196, 170)
TEXTO = (236, 241, 245)
TENUE = (138, 153, 168)
AVISO = (240, 170, 60)
ERROR = (235, 90, 80)
# BGR para OpenCV
CAJA_BGR = (120, 220, 60)
ACENTO_BGR = (170, 196, 0)

X_VID, Y_VID, W_VID, H_VID = 24, 96, 960, 540
Y_PIE = 652


class Estado:
    """Lo que la pantalla necesita saber; lo actualiza app.py."""

    def __init__(self):
        self.inicio = True
        self.lineas_inicio = []       # [(texto, estado)] estado: ok | espera | error
        self.titulo_panel = "Lo que veo"
        self.respuesta = ""
        self.pensando = False
        self.escuchando = False
        self.nivel_mic = 0.0
        self.subtitulo = ""
        self.chip = ""
        self.chip_color = ACENTO
        self.pista = ""
        self.aviso = ""               # línea pequeña bajo el panel (p.ej. modo respaldo)


class Pantalla:
    def __init__(self, cfg):
        u = cfg["ui"]
        self.u = u
        self.W, self.H = int(u["ancho"]), int(u["alto"])
        self.mostrar_fps = u.get("mostrar_fps", True)
        self._fuentes = {}
        self._clave = None
        self._capa = None
        cv2.namedWindow(VENTANA, cv2.WINDOW_NORMAL)
        self.pantalla_completa = bool(u.get("pantalla_completa", True))
        self._aplicar_modo()

    def _aplicar_modo(self):
        if self.pantalla_completa:
            cv2.setWindowProperty(VENTANA, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        else:
            cv2.setWindowProperty(VENTANA, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(VENTANA, self.W, self.H)

    def alternar_pantalla_completa(self):
        self.pantalla_completa = not self.pantalla_completa
        self._aplicar_modo()

    def _f(self, tam, negrita=False):
        clave = (tam, negrita)
        if clave not in self._fuentes:
            try:
                self._fuentes[clave] = ImageFont.truetype(
                    self.u["fuente_negrita" if negrita else "fuente"], tam)
            except OSError:
                self._fuentes[clave] = ImageFont.load_default()
        return self._fuentes[clave]

    @staticmethod
    def _envolver(d, texto, fuente, ancho):
        lineas = []
        for parrafo in texto.split("\n"):
            actual = ""
            for palabra in parrafo.split():
                prueba = (actual + " " + palabra).strip()
                if d.textlength(prueba, font=fuente) <= ancho:
                    actual = prueba
                else:
                    if actual:
                        lineas.append(actual)
                    actual = palabra
            lineas.append(actual)
        return lineas

    # ---------- capa estática (texto) ----------
    def _dibujar_capa(self, s):
        img = Image.new("RGB", (self.W, self.H), FONDO)
        d = ImageDraw.Draw(img)
        if s.inicio:
            self._capa_inicio(d, s)
        else:
            self._capa_demo(d, s)
        return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)

    def _encabezado(self, d, s):
        d.text((28, 12), self.u["titulo"], font=self._f(34, True), fill=TEXTO)
        d.text((30, 56), self.u["lema"], font=self._f(19), fill=ACENTO)
        if s.chip:
            f = self._f(18, True)
            w = d.textlength(s.chip, font=f)
            x1 = self.W - 150 - w
            d.rounded_rectangle((x1 - 34, 28, self.W - 130, 62), radius=17, fill=PANEL,
                                outline=s.chip_color, width=2)
            d.ellipse((x1 - 22, 39, x1 - 10, 51), fill=s.chip_color)
            d.text((x1, 34), s.chip, font=f, fill=TEXTO)

    def _capa_inicio(self, d, s):
        titulo = self.u["titulo"]
        f = self._f(52, True)
        d.text(((self.W - d.textlength(titulo, font=f)) / 2, 150), titulo, font=f, fill=TEXTO)
        f2 = self._f(24)
        d.text(((self.W - d.textlength(self.u["lema"], font=f2)) / 2, 222), self.u["lema"],
               font=f2, fill=ACENTO)
        f3 = self._f(30, True)
        d.text(((self.W - d.textlength("Iniciando…", font=f3)) / 2, 300), "Iniciando…",
               font=f3, fill=TEXTO)
        f4 = self._f(22)
        y = 470
        x = (self.W - 420) / 2
        for texto, est in s.lineas_inicio:
            color = {"ok": ACENTO, "error": ERROR}.get(est, TENUE)
            # indicador dibujado (no depende de que la fuente tenga ✓)
            if est == "ok":
                d.ellipse((x, y + 7, x + 16, y + 23), fill=color)
            else:
                d.ellipse((x, y + 7, x + 16, y + 23), outline=color, width=2)
            d.text((x + 30, y), texto, font=f4, fill=color)
            y += 36

    def _capa_demo(self, d, s):
        self._encabezado(d, s)
        # marco del video
        d.rounded_rectangle((X_VID - 4, Y_VID - 4, X_VID + W_VID + 4, Y_VID + H_VID + 4),
                            radius=8, outline=BORDE, width=3)
        # panel lateral
        px1, px2 = X_VID + W_VID + 24, self.W - 24
        d.rounded_rectangle((px1, Y_VID - 4, px2, Y_VID + H_VID + 4), radius=12, fill=PANEL,
                            outline=BORDE, width=2)
        d.text((px1 + 22, Y_VID + 16), s.titulo_panel, font=self._f(24, True), fill=ACENTO)
        ancho = px2 - px1 - 44
        y = Y_VID + 62
        if s.escuchando:
            cuerpo, color = "Te escucho… haz tu pregunta.", TEXTO
        elif s.pensando:
            cuerpo, color = "Pensando…", TENUE
        else:
            cuerpo, color = s.respuesta or "Presiona Enter y te digo qué estoy viendo.", \
                (TEXTO if s.respuesta else TENUE)
        f = self._f(24)
        for linea in self._envolver(d, cuerpo, f, ancho)[:14]:
            d.text((px1 + 22, y), linea, font=f, fill=color)
            y += 34
        if s.aviso:
            fa = self._f(16)
            ya = Y_VID + H_VID - 60
            for linea in self._envolver(d, s.aviso, fa, ancho)[:3]:
                d.text((px1 + 22, ya), linea, font=fa, fill=AVISO)
                ya += 21
        # pie: subtítulo de lo que dijo la persona + pista
        if s.subtitulo:
            fs = self._f(24, True)
            texto = self._envolver(d, "Tú: " + s.subtitulo, fs, self.W - 60)[0]
            d.text((28, Y_PIE), texto, font=fs, fill=TEXTO)
        if s.pista:
            d.text((28, self.H - 40), s.pista, font=self._f(18), fill=TENUE)

    # ---------- por frame ----------
    def mostrar(self, s, frame=None, caja=None, fps=None):
        clave = (s.inicio, tuple(s.lineas_inicio), s.titulo_panel, s.respuesta, s.pensando,
                 s.escuchando, s.subtitulo, s.chip, s.chip_color, s.pista, s.aviso)
        if clave != self._clave:
            self._capa = self._dibujar_capa(s)
            self._clave = clave
        img = self._capa.copy()
        t = time.monotonic()
        if s.inicio:
            self._girador(img, self.W // 2, 400, t)
        else:
            if frame is not None:
                fh, fw = frame.shape[:2]
                if (fw, fh) != (W_VID, H_VID):
                    frame = cv2.resize(frame, (W_VID, H_VID))
                img[Y_VID:Y_VID + H_VID, X_VID:X_VID + W_VID] = frame
                if caja is not None:
                    x, y, w, h = caja
                    sx, sy = W_VID / fw, H_VID / fh
                    p1 = (X_VID + int(x * sx), Y_VID + int(y * sy))
                    p2 = (X_VID + int((x + w) * sx), Y_VID + int((y + h) * sy))
                    self._esquinas(img, p1, p2)
            px = X_VID + W_VID + 24 + 40
            if s.pensando:
                self._girador(img, px + 110, Y_VID + 260, t)
            elif s.escuchando:
                self._barras(img, px, Y_VID + 150, s.nivel_mic, t)
        if self.mostrar_fps and fps is not None:
            cv2.putText(img, f"{fps:4.1f} FPS", (self.W - 118, 50), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (150, 150, 150), 1, cv2.LINE_AA)
        cv2.imshow(VENTANA, img)
        return cv2.waitKey(1)

    @staticmethod
    def _esquinas(img, p1, p2):
        """Recuadro estilo 'visor' con esquinas marcadas."""
        (x1, y1), (x2, y2) = p1, p2
        cv2.rectangle(img, p1, p2, CAJA_BGR, 1, cv2.LINE_AA)
        L = max(12, (x2 - x1) // 5)
        for (cx, cy, dx, dy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            cv2.line(img, (cx, cy), (cx + dx * L, cy), CAJA_BGR, 4, cv2.LINE_AA)
            cv2.line(img, (cx, cy), (cx, cy + dy * L), CAJA_BGR, 4, cv2.LINE_AA)

    @staticmethod
    def _girador(img, cx, cy, t):
        for i in range(10):
            a = t * 5 + i * (2 * math.pi / 10)
            brillo = (i + 1) / 10
            color = tuple(int(c * brillo) for c in ACENTO_BGR)
            cv2.circle(img, (int(cx + 34 * math.cos(a)), int(cy + 34 * math.sin(a))),
                       3 + i // 2, color, -1, cv2.LINE_AA)

    @staticmethod
    def _barras(img, x, y, nivel, t):
        for i in range(9):
            h = int(12 + min(1.0, nivel * 12) * 90 * (0.5 + 0.5 * math.sin(t * 9 + i)))
            cv2.rectangle(img, (x + i * 24, y + 60 - h // 2), (x + i * 24 + 14, y + 60 + h // 2),
                          ACENTO_BGR, -1)

    def cerrar(self):
        cv2.destroyAllWindows()
