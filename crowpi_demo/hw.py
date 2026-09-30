"""Hardware del kit: un solo hilo es dueño del bus I2C 7.

En el bus 7 conviven el RP2040 (0x66: servos Y matriz RGB) y el STC8 (0x2D:
botones por ADC y buzzer). Todo pasa por este hilo con prioridad:
servos > botones > buzzer > matriz RGB. Así la matriz nunca retrasa al gimbal.

Protocolo tomado de las lecciones de Elecrow (servo_class.py, ADC_IIC_lib.py,
elecrow_ws281x_jetson.py). Si no hay I2C (p.ej. en una PC), el demo sigue sin servos.
"""
import logging
import threading
import time

log = logging.getLogger("hw")

try:
    from smbus2 import SMBus
except ImportError:  # sin smbus2: se trabaja sin hardware
    SMBus = None

# RP2040 (0x66)
SERVO_INIT = 0xFF
SERVO_ON = 0x11
SERVO_OFF = 0x12
SERVO_ANGULO = 0x13
RGB_FILL = 2
RGB_SHOW = 0
RGB_BRILLO = 3
# STC8 (0x2D)
STC8 = 0x2D
STC8_LEER = 0x2C
BUZZER_ON = 0x5C
BUZZER_OFF = 0x6C
BOTONES = {1: (741, 781), 2: (800, 852), 3: (860, 910), 4: (911, 960)}

COLORES = {  # estado -> (r, g, b) para la matriz 8x8
    "iniciando": (40, 40, 40),
    "siguiendo": (0, 60, 0),
    "buscando": (40, 25, 0),
    "escuchando": (0, 0, 90),
    "pensando": (60, 0, 60),
    "hablando": (0, 50, 60),
    "respaldo": (70, 0, 0),
    "apagado": (0, 0, 0),
}


class BusI2C(threading.Thread):
    def __init__(self, cfg, al_boton=None):
        super().__init__(name="i2c", daemon=True)
        self.g = cfg["gimbal"]
        self.cfg_rgb = cfg.get("rgb", {})
        self.botones_on = cfg.get("controles", {}).get("botones_stc8", True)
        self.al_boton = al_boton
        self.addr = int(self.g["direccion"])
        self.can_pan = int(self.g["canal_pan"])
        self.can_tilt = int(self.g["canal_tilt"])
        self._lock = threading.Lock()
        self._objetivo = None     # (pan, tilt) enteros deseados
        self._enviado = (None, None)
        self._rgb = None          # color pendiente
        self._rgb_actual = None
        self._bip_hasta = 0.0
        self._bip_pedido = False
        self._parar = threading.Event()
        self.bus = None
        self.ok = False
        self.errores_seguidos = 0
        self._ultimo_boton = 0

    # --- API para otros hilos (no bloquea) ---
    def poner_angulos(self, pan, tilt):
        with self._lock:
            self._objetivo = (int(round(pan)), int(round(tilt)))

    def poner_estado_rgb(self, estado):
        if self.cfg_rgb.get("habilitado", True):
            with self._lock:
                self._rgb = COLORES.get(estado, COLORES["apagado"])

    def bip(self):
        with self._lock:
            self._bip_pedido = True

    def parar(self):
        self._parar.set()

    # --- hilo ---
    def _abrir(self):
        if SMBus is None:
            raise RuntimeError("smbus2 no instalado")
        self.bus = SMBus(int(self.g["i2c_bus"]))
        if self.g.get("habilitado", True):
            self.bus.write_byte(self.addr, SERVO_INIT)
            time.sleep(0.05)
            mn, mx = int(self.g["pulso_min_us"]), int(self.g["pulso_max_us"])
            for canal in (self.can_tilt, self.can_pan):
                self.bus.write_i2c_block_data(
                    self.addr, SERVO_ON, [canal, mn >> 8, mn & 0xFF, mx >> 8, mx & 0xFF])
                time.sleep(0.05)
        if self.cfg_rgb.get("habilitado", True):
            self._rgb_cmd(RGB_BRILLO, brillo=int(self.cfg_rgb.get("brillo", 20)))
        self._enviado = (None, None)
        self._rgb_actual = None
        self.ok = True
        self.errores_seguidos = 0
        log.info("I2C listo (bus %s, gimbal=%s)", self.g["i2c_bus"], self.g.get("habilitado", True))

    def _cerrar(self):
        self.ok = False
        try:
            if self.bus:
                self.bus.close()
        except Exception:
            pass
        self.bus = None

    def _rgb_cmd(self, func, r=0, g=0, b=0, brillo=0, first=0, count=0):
        # mismo formato que PixelStrip.trans() de Elecrow: 12 bytes con cmd = número de pixeles
        self.bus.write_block_data(self.addr, 64, [func, 0, r, g, b, 0, 0, brillo, first, count, 0, 0])
        time.sleep(0.005)

    def _servos(self):
        with self._lock:
            obj = self._objetivo
        if obj is None or not self.g.get("habilitado", True):
            return
        pan, tilt = obj
        if pan != self._enviado[0]:
            self.bus.write_i2c_block_data(self.addr, SERVO_ANGULO, [self.can_pan, pan])
        if tilt != self._enviado[1]:
            self.bus.write_i2c_block_data(self.addr, SERVO_ANGULO, [self.can_tilt, tilt])
        self._enviado = (pan, tilt)

    def _leer_botones(self):
        self.bus.write_byte(STC8, STC8_LEER)
        time.sleep(0.001)
        self.bus.read_byte(STC8)
        datos = self.bus.read_i2c_block_data(STC8, 0, 24)
        texto = "".join(chr(b) for b in datos)
        try:
            valor = float(texto[16:20])  # índice 4 (bloques de 4 caracteres) = botones
        except ValueError:
            return
        boton = 0
        for n, (lo, hi) in BOTONES.items():
            if lo <= valor <= hi:
                boton = n
        if boton != self._ultimo_boton:
            self._ultimo_boton = boton
            if boton and self.al_boton:
                self.al_boton(boton)

    def _buzzer(self, ahora):
        with self._lock:
            pedido, self._bip_pedido = self._bip_pedido, False
        if pedido:
            self.bus.write_byte(STC8, BUZZER_ON)
            self._bip_hasta = ahora + 0.08
        elif self._bip_hasta and ahora >= self._bip_hasta:
            self.bus.write_byte(STC8, BUZZER_OFF)
            self._bip_hasta = 0.0

    def _matriz(self):
        with self._lock:
            color = self._rgb
        if color is None or color == self._rgb_actual:
            return
        self._rgb_cmd(RGB_FILL, *color, first=0, count=64)
        self._rgb_cmd(RGB_SHOW)
        self._rgb_actual = color

    def run(self):
        periodo = 1.0 / float(self.g.get("envios_por_s", 50))
        t_botones = t_rgb = 0.0
        while not self._parar.is_set():
            if not self.ok:
                try:
                    self._abrir()
                except Exception as e:
                    log.warning("I2C no disponible (%s); reintento en 5 s. El demo sigue sin servos.", e)
                    self._cerrar()
                    self._parar.wait(5)
                    continue
            ahora = time.monotonic()
            try:
                self._servos()
                if self.botones_on and ahora - t_botones > 0.1:
                    t_botones = ahora
                    try:
                        self._leer_botones()
                    except OSError:
                        pass  # un fallo de botones no debe reiniciar el bus
                self._buzzer(ahora)
                if ahora - t_rgb > 0.3:
                    t_rgb = ahora
                    self._matriz()
                self.errores_seguidos = 0
            except OSError as e:
                self.errores_seguidos += 1
                if self.errores_seguidos in (1, 10):
                    log.warning("Error I2C (%s), seguidos=%d", e, self.errores_seguidos)
                if self.errores_seguidos >= 10:
                    log.error("Demasiados errores I2C; reinicializando el bus")
                    self._cerrar()
            self._parar.wait(periodo)
        self._apagar()

    def _apagar(self):
        """Al salir: centrar, apagar matriz y soltar los servos."""
        if not self.ok:
            return
        try:
            if self.g.get("habilitado", True):
                self.bus.write_i2c_block_data(self.addr, SERVO_ANGULO,
                                              [self.can_pan, int(self.g["centro_pan"])])
                self.bus.write_i2c_block_data(self.addr, SERVO_ANGULO,
                                              [self.can_tilt, int(self.g["centro_tilt"])])
                time.sleep(0.4)
            if self.cfg_rgb.get("habilitado", True):
                self._rgb_cmd(RGB_FILL, 0, 0, 0, first=0, count=64)
                self._rgb_cmd(RGB_SHOW)
            if self.g.get("habilitado", True):
                for canal in (self.can_pan, self.can_tilt):
                    self.bus.write_i2c_block_data(self.addr, SERVO_OFF, [canal])
        except OSError:
            pass
        self._cerrar()


class SensorTactil(threading.Thread):
    """Sensor táctil del kit (GPIO BCM). Llama al_tocar() en cada flanco de subida."""

    def __init__(self, pin_bcm, al_tocar):
        super().__init__(name="tactil", daemon=True)
        self.pin = pin_bcm
        self.al_tocar = al_tocar
        self._parar = threading.Event()

    def run(self):
        try:
            import Jetson.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.IN)
        except Exception as e:
            log.warning("Sensor táctil no disponible (%s)", e)
            return
        previo = 0
        t_ultimo = 0.0
        try:
            while not self._parar.is_set():
                nivel = GPIO.input(self.pin)
                ahora = time.monotonic()
                if nivel and not previo and ahora - t_ultimo > 0.8:  # antirrebote
                    t_ultimo = ahora
                    self.al_tocar()
                previo = nivel
                self._parar.wait(0.03)
        finally:
            try:
                GPIO.cleanup(self.pin)
            except Exception:
                pass

    def parar(self):
        self._parar.set()
