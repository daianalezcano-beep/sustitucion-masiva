"""
parametros_sheet.py — Rango de fechas de los servicios, guardado en el Sheet (pestaña PARAMETROS, dos celdas).

La pestaña PARAMETROS tiene dos filas: A1 «DESDE» / B1 la fecha, A2 «HASTA» / B2 la fecha. La app las completa sola al cargar un reporte
(con el primer y el último servicio de las combinaciones cargadas) y se pueden editar a mano, por ejemplo cuando las combinaciones se cargan
sin reporte. Se escriben como texto DD/MM/AAAA para que el idioma del Sheet no las confunda. Sin fechas, se usa «de mañana en adelante».
"""

from datetime import datetime, timedelta

from common import logica as L

HOJA = "PARAMETROS"
FIN_POR_DEFECTO = datetime(2049, 12, 31)


def rango_por_defecto(hoy=None):
    """De mañana en adelante (hasta 31/12/2049)."""
    hoy = hoy or datetime.now()
    return datetime(hoy.year, hoy.month, hoy.day) + timedelta(days=1), FIN_POR_DEFECTO


def leer_rango(valores):
    """`valores`: filas de la pestaña (lista de listas de texto). Devuelve (desde, hasta) como datetime, o None en el que falte o no se entienda."""
    encontrados = {}
    for fila in valores or []:
        if len(fila) >= 2 and str(fila[0]).strip().upper() in ("DESDE", "HASTA"):
            encontrados[str(fila[0]).strip().upper()] = L.parsear_fecha(str(fila[1]).strip())
    return encontrados.get("DESDE"), encontrados.get("HASTA")


def rango_del_plan(a_cargar):
    """(primer servicio, último servicio) de las combinaciones a cargar (campos DESDE / HASTA del plan, DD/MM/AAAA), o (None, None)."""
    desdes = [d for d in (L.parsear_fecha(c.get("DESDE")) for c in a_cargar) if d]
    hastas = [h for h in (L.parsear_fecha(c.get("HASTA")) for c in a_cargar) if h]
    return (min(desdes) if desdes else None), (max(hastas) if hastas else None)


def fusionar(actual, nuevo, hay_pendientes):
    """Rango a guardar tras cargar un reporte. Sin filas pendientes de antes, el del reporte reemplaza al anterior; con pendientes, se amplía para
    cubrir las dos tandas. (desde, hasta) siempre; los None del reporte dejan lo que había."""
    d0, h0 = actual
    d1, h1 = nuevo
    if not hay_pendientes or (d0 is None and h0 is None):
        return (d1 or d0), (h1 or h0)
    desde = min(x for x in (d0, d1) if x) if (d0 or d1) else None
    hasta = max(x for x in (h0, h1) if x) if (h0 or h1) else None
    return desde, hasta


def texto(dt):
    return dt.strftime("%d/%m/%Y") if dt else ""


# ── Lectura y escritura en el Sheet ──────────────────────────────────────────

def _hoja(ws, crear=False):
    sh = ws.spreadsheet
    for h in sh.worksheets():
        if h.title.strip().upper() == HOJA:
            return h
    if not crear:
        return None
    h = sh.add_worksheet(title=HOJA, rows=10, cols=3)
    h.update(range_name="A1", values=[["DESDE", ""], ["HASTA", ""],
                                      ["", "Fechas de los servicios a sustituir (DD/MM/AAAA). La app las completa al cargar un reporte; se pueden editar a mano."]],
             value_input_option="RAW")
    return h


def leer_del_sheet(ws_substitute):
    """(desde, hasta) guardados en el Sheet, o (None, None) si no hay pestaña, no hay fechas o no se pudo leer."""
    try:
        h = _hoja(ws_substitute)
        return leer_rango(h.get_all_values()) if h is not None else (None, None)
    except Exception:
        return None, None


def escribir_en_el_sheet(ws_substitute, desde, hasta):
    """Guarda las dos fechas (crea la pestaña si falta)."""
    h = _hoja(ws_substitute, crear=True)
    h.update(range_name="B1:B2", values=[[texto(desde)], [texto(hasta)]], value_input_option="RAW")
