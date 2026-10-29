"""
pre_reporte.py — Reporte PRE (Product Analysis exportado ANTES de la corrida): validación y guardado.

Sin Tourplan ni Streamlit. En modo aplicar la app exige adjuntar el PRE: es la única foto de costos y ventas que permite
revisar (y, si hace falta, corregir) después de la corrida. Acá se verifica que el archivo sea un Product Analysis y que cubra
las combinaciones que se van a procesar; la app lo guarda en salida/pre_reportes/ para que el comparador lo use como "antes".
"""

from datetime import datetime
from pathlib import Path

from common import comparador as C
from common import logica as L

COLUMNAS_REQUERIDAS = ("Loc", "Service", "Option", "Supplier", "Date", "Retail", "Reference")
CARPETA = "pre_reportes"


def _n(v):
    return str(v or "").strip().upper()


def leer_pre(contenido):
    """Contenido del CSV -> lista de líneas. ValueError si no parece un Product Analysis o no tiene líneas."""
    filas = C.leer_reporte(contenido)
    if not filas:
        raise ValueError("El archivo no tiene líneas.")
    faltan = [c for c in COLUMNAS_REQUERIDAS if c not in filas[0]]
    if faltan:
        raise ValueError(f"No parece un reporte Product Analysis: faltan las columnas {faltan}.")
    return filas


def combinaciones_cubiertas(filas, desde=None, hasta=None):
    """Conjunto de (LOCATION, SUPPLIER, SERVICE TYPE, CODIGO) con al menos una línea en el PRE; si se dan `desde`/`hasta`
    (datetime), solo cuentan las líneas con Date dentro del rango."""
    cubiertas = set()
    for f in filas:
        d = C.fecha(f.get("Date"))
        if d is not None and ((desde and d < desde) or (hasta and d > hasta)):
            continue
        cubiertas.add((_n(f.get("Loc")), _n(f.get("Supplier")), _n(f.get("Service")), _n(f.get("Option"))))
    return cubiertas


def combinaciones_faltantes(filas, pendientes, desde=None, hasta=None):
    """Combinaciones de `pendientes` (filas del Sheet con LOCATION, SUPPLIER, SERVICE TYPE, CODIGO) que el PRE no cubre,
    sin repetir y en el orden de la hoja."""
    cubiertas = combinaciones_cubiertas(filas, desde, hasta)
    faltan, vistas = [], set()
    for f in pendientes:
        k = L.clave_combinacion(f)
        if k not in cubiertas and k not in vistas:
            vistas.add(k)
            faltan.append(k)
    return faltan


def guardar_pre(contenido, carpeta):
    """Guarda el PRE como <carpeta>/pre_AAAAMMDD-HHMMSS.csv y devuelve la ruta."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"pre_{datetime.now():%Y%m%d-%H%M%S}.csv"
    ruta.write_bytes(contenido)
    return ruta


def ultimo_pre(carpeta):
    """Ruta del PRE guardado más reciente (None si no hay)."""
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        return None
    archivos = sorted(carpeta.glob("pre_*.csv"))
    return archivos[-1] if archivos else None
