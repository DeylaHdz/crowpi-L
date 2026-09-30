#!/usr/bin/env python3
"""Prueba independiente del gimbal (sin cámara ni IA).

  python3 scripts/prueba_servos.py                 centra y hace un barrido lento dentro de los límites
  python3 scripts/prueba_servos.py --interactivo   mueve con teclas para encontrar los límites reales

En modo interactivo: a/d = pan -/+ , w/s = tilt +/- , A/D/W/S = pasos de 10°,
c = centro, p = imprimir ángulos, q = salir. Acércate al tope con cuidado: si el
servo zumba o se atora, retrocede 5° y anota ese valor en config.yaml.
"""
import argparse
import sys
import termios
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smbus2 import SMBus  # noqa: E402

from crowpi_demo import config  # noqa: E402
from crowpi_demo.hw import SERVO_ANGULO, SERVO_INIT, SERVO_OFF, SERVO_ON  # noqa: E402


class Gimbal:
    def __init__(self, g):
        self.g = g
        self.bus = SMBus(int(g["i2c_bus"]))
        self.addr = int(g["direccion"])
        self.bus.write_byte(self.addr, SERVO_INIT)
        time.sleep(0.05)
        mn, mx = int(g["pulso_min_us"]), int(g["pulso_max_us"])
        for c in (g["canal_tilt"], g["canal_pan"]):
            self.bus.write_i2c_block_data(self.addr, SERVO_ON, [int(c), mn >> 8, mn & 0xFF, mx >> 8, mx & 0xFF])
            time.sleep(0.05)

    def mover(self, pan, tilt):
        self.bus.write_i2c_block_data(self.addr, SERVO_ANGULO, [int(self.g["canal_pan"]), int(pan)])
        self.bus.write_i2c_block_data(self.addr, SERVO_ANGULO, [int(self.g["canal_tilt"]), int(tilt)])

    def rampa(self, desde, hasta, eje_pan, fijo, vel=20.0):
        paso = 1 if hasta > desde else -1
        for a in range(int(desde), int(hasta) + paso, paso):
            self.mover(a, fijo) if eje_pan else self.mover(fijo, a)
            time.sleep(1.0 / vel)

    def soltar(self):
        for c in (self.g["canal_pan"], self.g["canal_tilt"]):
            self.bus.write_i2c_block_data(self.addr, SERVO_OFF, [int(c)])
        self.bus.close()


def leer_tecla():
    fd = sys.stdin.fileno()
    viejo = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, viejo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interactivo", action="store_true")
    args = ap.parse_args()
    g = config.cargar()["gimbal"]
    cp, ct = int(g["centro_pan"]), int(g["centro_tilt"])
    (p0, p1), (t0, t1) = g["limite_pan"], g["limite_tilt"]
    gim = Gimbal(g)
    print(f"Centro pan={cp} tilt={ct} | límites pan {p0}-{p1}, tilt {t0}-{t1}")
    gim.mover(cp, ct)
    time.sleep(1)
    try:
        if not args.interactivo:
            print("Barrido PAN (horizontal)...")
            gim.rampa(cp, p0, True, ct); gim.rampa(p0, p1, True, ct); gim.rampa(p1, cp, True, ct)
            print("Barrido TILT (vertical)...")
            gim.rampa(ct, t0, False, cp); gim.rampa(t0, t1, False, cp); gim.rampa(t1, ct, False, cp)
            print("OK. Si algún extremo topó o zumbó, ajusta limite_pan/limite_tilt en config.yaml.")
            print("Revisa también el sentido: al subir 'pan' la cámara debe girar hacia un lado fijo;")
            print("si el seguimiento se aleja del rostro, invierte sentido_pan / sentido_tilt.")
            return
        pan, tilt = cp, ct
        print("a/d w/s (mayúsculas = 10°), c centro, p imprimir, q salir")
        while True:
            k = leer_tecla()
            paso = 10 if k.isupper() else 1
            k = k.lower()
            if k == "q":
                break
            if k == "a": pan -= paso
            elif k == "d": pan += paso
            elif k == "w": tilt += paso
            elif k == "s": tilt -= paso
            elif k == "c": pan, tilt = cp, ct
            pan, tilt = max(0, min(180, pan)), max(0, min(180, tilt))
            gim.mover(pan, tilt)
            print(f"\rpan={pan:3d}  tilt={tilt:3d}   ", end="", flush=True)
        print()
    finally:
        gim.mover(cp, ct)
        time.sleep(0.5)
        gim.soltar()


if __name__ == "__main__":
    main()
