"""Control PI(D) del gimbal pan/tilt.

La salida del control es una VELOCIDAD (°/s) proporcional al error, limitada por
vel_max; así el movimiento es suave aunque el rostro salte. Incluye zona muerta,
anti-windup, límites de ángulo y comportamiento sin rostro (centrar o barrer).
Este módulo no toca hardware: entrega ángulos y hw.BusI2C los envía.
"""
import math


def _limitar(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class _Eje:
    def __init__(self, kp, ki, kd, zona, vel_max, sentido):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.zona, self.vel_max, self.sentido = zona, vel_max, sentido
        self.integral = 0.0
        self.e_prev = 0.0

    def reset(self):
        self.integral = 0.0
        self.e_prev = 0.0

    def paso(self, e, dt):
        """Error normalizado (-1..1) -> incremento de ángulo en este paso."""
        if abs(e) < self.zona:
            e = 0.0
            self.integral *= 0.9  # dentro de la zona muerta la integral se relaja
        else:
            e = e - math.copysign(self.zona, e)  # sin escalón al salir de la zona
        self.integral = _limitar(self.integral + e * dt, -0.5, 0.5)
        d = (e - self.e_prev) / dt if dt > 0 else 0.0
        self.e_prev = e
        vel = _limitar(self.kp * e + self.ki * self.integral + self.kd * d,
                       -self.vel_max, self.vel_max)
        return self.sentido * vel * dt


class ControlGimbal:
    def __init__(self, cfg):
        g = cfg["gimbal"]
        self.g = g
        self.s = g["sin_rostro"]
        self.lim_pan = tuple(g["limite_pan"])
        self.lim_tilt = tuple(g["limite_tilt"])
        self.centro = (float(g["centro_pan"]), float(g["centro_tilt"]))
        comun = dict(kp=float(g["kp"]), ki=float(g["ki"]), kd=float(g.get("kd", 0.0)),
                     zona=float(g["zona_muerta"]), vel_max=float(g["vel_max"]))
        self.eje_pan = _Eje(sentido=int(g["sentido_pan"]), **comun)
        self.eje_tilt = _Eje(sentido=int(g["sentido_tilt"]), **comun)
        self.pan, self.tilt = self.centro
        self.t_prev = None
        self.t_rostro = None
        self.t_inicio_sin = None
        self.dir_barrido = 1
        self.modo = "centrando"  # siguiendo | esperando | centrando | buscando

    def centrar(self):
        self.pan, self.tilt = self.centro
        self.eje_pan.reset()
        self.eje_tilt.reset()

    def actualizar(self, objetivo, ancho, alto, ahora):
        """objetivo: (cx, cy) del rostro en píxeles o None. Devuelve (pan, tilt)."""
        dt = 0.0 if self.t_prev is None else _limitar(ahora - self.t_prev, 0.0, 0.1)
        self.t_prev = ahora
        if objetivo is not None:
            ex = (objetivo[0] - ancho / 2) / (ancho / 2)
            ey = (objetivo[1] - alto / 2) / (alto / 2)
            self.pan += self.eje_pan.paso(ex, dt)
            self.tilt += self.eje_tilt.paso(ey, dt)
            self.t_rostro = ahora
            self.t_inicio_sin = None
            self.modo = "siguiendo"
        else:
            self.eje_pan.reset()
            self.eje_tilt.reset()
            if self.t_inicio_sin is None:
                self.t_inicio_sin = ahora
            sin = ahora - self.t_inicio_sin
            if sin < float(self.s["espera_s"]):
                self.modo = "esperando"  # quieto: la persona pudo girar la cabeza
            elif self.s.get("modo") == "barrido" and sin > float(self.s["barrido_tras_s"]):
                self.modo = "buscando"
                amp = float(self.s["barrido_amplitud"])
                self.pan += self.dir_barrido * float(self.s["vel_barrido"]) * dt
                if self.pan > self.centro[0] + amp:
                    self.dir_barrido = -1
                elif self.pan < self.centro[0] - amp:
                    self.dir_barrido = 1
                self.tilt = self._acercar(self.tilt, self.centro[1],
                                          float(self.s["vel_regreso"]) * dt)
            else:
                self.modo = "centrando"
                paso = float(self.s["vel_regreso"]) * dt
                self.pan = self._acercar(self.pan, self.centro[0], paso)
                self.tilt = self._acercar(self.tilt, self.centro[1], paso)
        self.pan = _limitar(self.pan, *self.lim_pan)
        self.tilt = _limitar(self.tilt, *self.lim_tilt)
        return self.pan, self.tilt

    @staticmethod
    def _acercar(v, meta, paso):
        if abs(meta - v) <= paso:
            return meta
        return v + math.copysign(paso, meta - v)
