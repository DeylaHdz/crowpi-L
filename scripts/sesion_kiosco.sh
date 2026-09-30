#!/usr/bin/env bash
# Sesión X mínima del modo kiosco: sin escritorio, sin salvapantallas, solo el demo.
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
xset s off -dpms s noblank 2>/dev/null
xsetroot -solid black 2>/dev/null
cd "$RAIZ"
exec python3 -m crowpi_demo
