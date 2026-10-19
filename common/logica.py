"""
logica.py — Lógica pura (sin Selenium ni Sheets) de la doble sustitución.

Todo lo que se puede decidir sin tocar Tourplan vive acá para poder probarlo
por simulación (tests/test_logica.py): fechas, deduplicado del input, mapa de
provisorios, lectura progresiva de grillas con scroll y análisis de claves.
"""

from collections import Counter
from datetime import datetime

MESES = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
         "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
MESES_INV = {v: k for k, v in MESES.items()}

# Columnas de datos de Results (orden del listado) → clase tpcol-* de cada td.
COLUMNAS_RESULTS = (
    ("location", "locationcode"),
    ("service", "servicecode"),
    ("supplier", "suppliercode"),
    ("code", "optioncode"),
    ("description", "optiondescription"),
    ("booking_name", "bookingname"),
    ("booking_ref", "bookingreference"),
    ("travel_date", "traveldate"),
    ("date", "servicedate"),
    ("status", "servicestatus"),
)


# ── Fechas ───────────────────────────────────────────────────────────────────

def parsear_fecha(txt):
    """DD/MM/YY, DD/MM/YYYY o DD/Mon/YYYY (formato que normaliza Tourplan).
    Año de 2 dígitos → 20YY (Tourplan interpreta 49 como 2049, confirmado)."""
    if not txt:
        return None
    if isinstance(txt, datetime):
        return txt
    partes = str(txt).strip().split("/")
    if len(partes) != 3:
        return None
    try:
        dia = int(partes[0])
        mes = MESES.get(partes[1][:3].capitalize()) or int(partes[1])
        anio = int(partes[2])
        if anio < 100:
            anio += 2000
        return datetime(anio, mes, dia)
    except (ValueError, TypeError):
        return None


def fmt_ddmmyy(dt):
    """Formato que SÍ acepta el input de fecha de Tourplan. Con año de 4
    dígitos (DD/MM/YYYY) el campo lo rechaza y vuelve a la fecha de hoy
    (confirmado por diagnóstico), así que siempre se escribe con 2 dígitos."""
    return f"{dt.day:02d}/{dt.month:02d}/{dt.year % 100:02d}"


def fmt_tp(dt):
    return f"{dt.day:02d}/{MESES_INV[dt.month]}/{dt.year}"


def validar_rango(desde, hasta):
    """Devuelve un mensaje de error o None. Ambas fechas ya parseadas."""
    if desde is None:
        return "Fecha Desde inválida o vacía"
    if hasta is None:
        return "Fecha Hasta inválida"
    for nombre, dt in (("Desde", desde), ("Hasta", hasta)):
        if not 2000 <= dt.year <= 2099:
            return f"Fecha {nombre} fuera de 2000–2099 (no se puede escribir con año de 2 dígitos)"
    if hasta < desde:
        return f"Fecha Hasta ({fmt_tp(hasta)}) anterior a Desde ({fmt_tp(desde)})"
    return None


# ── Input (hoja SUBSTITUTE) y provisorios ───────────────────────────────────

def _n(v):
    return str(v or "").strip().upper()


def clave_combinacion(fila):
    return (_n(fila.get("LOCATION")), _n(fila.get("SUPPLIER")),
            _n(fila.get("SERVICE TYPE")), _n(fila.get("CODIGO")))


def deduplicar_input(filas):
    """Marca como repetida (sin procesar) toda fila cuya combinación
    (LOCATION, SUPPLIER, SERVICE TYPE, CODIGO) ya apareció antes.
    Devuelve (unicas, repetidas) donde repetidas = [(fila, fila_original)].
    Solo mira filas completas: una fila con campos vacíos nunca es 'repetida'
    (se rechaza después, por datos faltantes)."""
    vistas, unicas, repetidas = {}, [], []
    for f in filas:
        k = clave_combinacion(f)
        if not all(k):
            unicas.append(f)
        elif k in vistas:
            repetidas.append((f, vistas[k]))
        else:
            vistas[k] = f
            unicas.append(f)
    return unicas, repetidas


def mapa_provisorios(filas):
    """{(LOCATION, SERVICE TYPE): (SUPPLIER_PROV, CODIGO_PROV)} desde la hoja
    PROVISORIOS. Se ignoran filas incompletas."""
    mapa = {}
    for f in filas:
        k = (_n(f.get("LOCATION")), _n(f.get("SERVICE TYPE")))
        v = (_n(f.get("SUPPLIER_PROV")), _n(f.get("CODIGO_PROV")))
        if all(k) and all(v):
            mapa[k] = v
    return mapa


def provisorio_de(fila, mapa):
    return mapa.get((_n(fila.get("LOCATION")), _n(fila.get("SERVICE TYPE"))))


def pares_sin_provisorio(filas, mapa):
    """Combinaciones (LOCATION, SERVICE TYPE) distintas del input y cuáles no
    tienen provisorio. Devuelve (todos_ordenados, faltantes_ordenados)."""
    pares = sorted({(_n(f.get("LOCATION")), _n(f.get("SERVICE TYPE")))
                    for f in filas if _n(f.get("LOCATION")) and _n(f.get("SERVICE TYPE"))})
    return pares, [p for p in pares if p not in mapa]


# ── Results ──────────────────────────────────────────────────────────────────

def fusionar_ventana(acumulado, visibles):
    """Lectura progresiva de una grilla con virtual scroll, por contenido (nunca
    por índice). Cada llamada recibe las filas visibles en la posición actual
    del scroll; se busca el mayor solapamiento entre el final de lo acumulado y
    el comienzo de lo visible y solo se agrega lo nuevo. Con filas idénticas
    consecutivas el solapamiento puede ser ambiguo: por eso quien llama debe
    comparar el total contra Found y rechazar la lectura si no coincide."""
    tope = min(len(acumulado), len(visibles))
    for k in range(tope, 0, -1):
        if acumulado[len(acumulado) - k:] == visibles[:k]:
            return acumulado + visibles[k:]
    return acumulado + visibles


def clave_reserva(fila):
    return (fila["booking_ref"], fila["code"], fila["travel_date"])


def analizar_duplicados(filas):
    """Claves repetidas (Booking Ref + Code + Travel Date). Informa cuántas hay
    y cuántas difieren en Status o en PU/DO, que son las que no se pueden
    reponer a ciegas en la vuelta."""
    grupos = {}
    for f in filas:
        grupos.setdefault(clave_reserva(f), []).append(f)
    dup = {k: v for k, v in grupos.items() if len(v) > 1}
    return {
        "claves": len(dup),
        "filas": sum(len(v) for v in dup.values()),
        "difieren_estado": sum(len({x["status"] for x in v}) > 1 for v in dup.values()),
        "difieren_pudo": sum(len({x["pudo_raw"] for x in v}) > 1 for v in dup.values()),
    }


def conteo_estados(filas):
    return dict(Counter(f["status"] for f in filas).most_common())


def filas_fuera_del_filtro(filas, location, service, supplier, code):
    """Filas de Results que no corresponden al filtro pedido (señal de que la
    pantalla no es la esperada). Devuelve las primeras 3 para el mensaje."""
    esperado = (_n(location), _n(service), _n(supplier), _n(code))
    return [f for f in filas
            if (_n(f["location"]), _n(f["service"]), _n(f["supplier"]), _n(f["code"])) != esperado][:3]


def texto_pudo(parte):
    """['01/Nov/2026 00:00', 'texto'] → ('01/Nov/2026 00:00', 'texto')."""
    parte = list(parte) + ["", ""]
    return parte[0].strip(), parte[1].strip()
