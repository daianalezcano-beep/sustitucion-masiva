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
from common import tipo_reserva as T

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
    """Conjunto de (LOCATION, SUPPLIER, SERVICE TYPE, CODIGO, TIPO) con al menos una línea en el PRE, donde TIPO sale del Department de la
    Reference ('FITS', 'GRUPOS' o '' si no está clasificado); si se dan `desde`/`hasta` (datetime), solo cuentan las líneas con Date dentro
    del rango."""
    cubiertas = set()
    for f in filas:
        d = C.fecha(f.get("Date"))
        if d is not None and ((desde and d < desde) or (hasta and d > hasta)):
            continue
        cubiertas.add((_n(f.get("Loc")), _n(f.get("Supplier")), _n(f.get("Service")), _n(f.get("Option")),
                       T.tipo_de_reserva(f.get("Reference")) or ""))
    return cubiertas


def combinaciones_faltantes(filas, pendientes, desde=None, hasta=None):
    """Combinaciones de `pendientes` (filas del Sheet con LOCATION, SUPPLIER, SERVICE TYPE, CODIGO y, si hay, TIPO) que el PRE no cubre, sin
    repetir y en el orden de la hoja. Una fila con TIPO (FITS / GRUPOS) exige líneas de ese tipo en el PRE; una sin TIPO, de cualquiera.
    Se devuelve la clave de 4 campos (o de 5, con el TIPO, si la fila lo tiene)."""
    cubiertas = combinaciones_cubiertas(filas, desde, hasta)
    cubiertas_sin_tipo = {k[:4] for k in cubiertas}
    faltan, vistas = [], set()
    for f in pendientes:
        k = L.clave_combinacion(f)
        tipo = T.normalizar_tipo(f.get("TIPO"))
        cubierta = k in cubiertas_sin_tipo if not tipo else (k + (tipo,)) in cubiertas
        clave = k + (tipo,) if tipo else k
        if not cubierta and clave not in vistas:
            vistas.add(clave)
            faltan.append(clave)
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
