"""Carga de config.yaml y utilidades de rutas."""
import os
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent


def cargar(ruta=None):
    ruta = Path(ruta or os.environ.get("CROWPI_CONFIG") or RAIZ / "config.yaml")
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def ruta(p):
    """Ruta absoluta: expande ~ y resuelve relativas contra la raíz del repo."""
    if not p:
        return None
    p = Path(os.path.expanduser(str(p)))
    return p if p.is_absolute() else RAIZ / p
