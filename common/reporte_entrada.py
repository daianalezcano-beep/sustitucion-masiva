"""
reporte_entrada.py — Arma la lista de combinaciones a procesar a partir de un reporte Service Status de Tourplan.

Sin Tourplan ni Streamlit. El reporte trae una línea por servicio de reserva; acá se descartan los servicios cancelados (que la
herramienta no puede sustituir), se deduplica por combinación (Location + Supplier + Service Type + Option) y se separan las
combinaciones que no se pueden procesar (sin provisorio definido o ya presentes en la hoja). El resultado se muestra como vista
previa y, si la persona confirma, se carga al Sheet como PENDIENTE (eso lo hace app.py).

Columnas del reporte (el primer "Status" es el estado del servicio y el segundo el de la reserva): Status, Supplier, Service Date,
Service (= Service Type), Location, Option (= Product Code), Reference, Status (2)...
"""

import unicodedata
from collections import Counter

from common import comparador as C
from common import logica as L

COLUMNAS_REQUERIDAS = ("Status", "Supplier", "Service Date", "Service", "Location", "Option", "Reference")
ESTADOS_EXCLUIDOS = ("CX",)        # servicios cancelados: Tourplan no permite sustituirlos
# Proveedores "por asignar" (ej. GUIAPO, "Guia por Asignar"): no se procesan, porque el costo correcto se carga cuando se asigna el proveedor real.
PROVEEDORES_POR_ASIGNAR = ("GUIAPO",)
# Estimación de tiempo (medida en producción, 2026-10-20): segundos fijos de la corrida, por combinación, por reserva y por estado extra.
SEG_FIJOS, SEG_COMBINACION, SEG_RESERVA, SEG_ESTADO_EXTRA = 75, 22, 1.5, 12


def leer_reporte(contenido):
    """Contenido del CSV -> lista de dicts por línea. ValueError si no parece un reporte Service Status."""
    filas = C.leer_reporte(contenido)
    if not filas:
        raise ValueError("El archivo no tiene líneas.")
    faltan = [c for c in COLUMNAS_REQUERIDAS if c not in filas[0]]
    if faltan:
        raise ValueError(f"No parece un reporte Service Status: faltan las columnas {faltan} (columnas del archivo: {list(filas[0])}).")
    return filas


def _n(v):
    return str(v or "").strip().upper()


def es_por_asignar(f):
    """Línea cuyo proveedor es un 'por asignar': por código (PROVEEDORES_POR_ASIGNAR) o porque SupplierName dice 'por asignar'."""
    nombre = unicodedata.normalize("NFKD", str(f.get("SupplierName") or "")).encode("ascii", "ignore").decode().upper()
    return _n(f.get("Supplier")) in PROVEEDORES_POR_ASIGNAR or "POR ASIGNAR" in nombre


def segundos_estimados(combinaciones):
    """combinaciones: lista de dicts con 'RESERVAS' y 'N_ESTADOS'. Estimación, no medición."""
    total = SEG_FIJOS if combinaciones else 0
    for c in combinaciones:
        total += SEG_COMBINACION + SEG_RESERVA * c["RESERVAS"] + SEG_ESTADO_EXTRA * max(c["N_ESTADOS"] - 1, 0)
    return int(total)


def armar_plan(filas, provisorios, en_hoja=None, estados_excluidos=ESTADOS_EXCLUIDOS):
    """filas: líneas del reporte. provisorios: {(LOCATION, SERVICE TYPE): proveedor}. en_hoja: {(LOCATION, SUPPLIER,
    SERVICE TYPE, CODIGO): ESTADO} de lo que ya está en la hoja SUBSTITUTE. Devuelve {'resumen', 'a_cargar', 'descartadas'}."""
    en_hoja = {tuple(_n(x) for x in k): v for k, v in (en_hoja or {}).items()}
    excluidos = {_n(e) for e in estados_excluidos}
    lineas_excluidas, incompletas, por_asignar, grupos = Counter(), 0, 0, {}
    for f in filas:
        clave = (_n(f.get("Location")), _n(f.get("Supplier")), _n(f.get("Service")), _n(f.get("Option")))
        estado = _n(f.get("Status"))
        if estado in excluidos:
            lineas_excluidas[estado] += 1
            continue
        if es_por_asignar(f):
            por_asignar += 1
            continue
        if not all(clave):
            incompletas += 1
            continue
        g = grupos.setdefault(clave, {"estados": Counter(), "fechas": []})
        g["estados"][estado or "(vacío)"] += 1
        d = C.fecha(f.get("Service Date", ""))
        if d:
            g["fechas"].append(d)
    a_cargar, descartadas = [], []
    # Orden: Location, Service Type, Supplier, Code. Así las combinaciones seguidas comparten campos del filtro y el script solo
    # recarga lo que cambia (y el provisorio de un par Location + Service Type se usa de corrido).
    for (loc, sup, stype, cod) in sorted(grupos, key=lambda k: (k[0], k[2], k[1], k[3])):
        g = grupos[(loc, sup, stype, cod)]
        fila = {"LOCATION": loc, "SUPPLIER": sup, "SERVICE TYPE": stype, "CODIGO": cod, "RESERVAS": sum(g["estados"].values()),
                "ESTADOS": ", ".join(f"{e} {n}" for e, n in sorted(g["estados"].items())), "N_ESTADOS": len(g["estados"]),
                "DESDE": min(g["fechas"]).strftime("%d/%m/%Y") if g["fechas"] else "",
                "HASTA": max(g["fechas"]).strftime("%d/%m/%Y") if g["fechas"] else ""}
        motivo = None
        if L.provisorio_para(loc, stype, provisorios) is None:
            motivo = f"Sin provisorio definido para {loc}/{stype}"
        elif (loc, sup, stype, cod) in en_hoja:
            motivo = f"Ya está en la hoja (estado: {en_hoja[(loc, sup, stype, cod)] or 'vacío'})"
        (descartadas if motivo else a_cargar).append(dict(fila, MOTIVO=motivo) if motivo else fila)
    fechas = [d for g in grupos.values() for d in g["fechas"]]
    resumen = {
        "Líneas del reporte": len(filas),
        "Líneas excluidas (servicio cancelado)": sum(lineas_excluidas.values()),
        "Líneas excluidas (proveedor por asignar)": por_asignar,
        "Líneas incompletas": incompletas,
        "Combinaciones a cargar": len(a_cargar),
        "Reservas a cargar": sum(c["RESERVAS"] for c in a_cargar),
        "Combinaciones descartadas": len(descartadas),
        "Segundos estimados": segundos_estimados(a_cargar),
        "Desde": min(fechas) if fechas else None,
        "Hasta": max(fechas) if fechas else None,
    }
    return {"resumen": resumen, "a_cargar": a_cargar, "descartadas": descartadas}


def filas_para_hoja(a_cargar):
    """Las combinaciones a cargar como filas de la hoja SUBSTITUTE (ESTADO = PENDIENTE)."""
    return [{"LOCATION": c["LOCATION"], "SUPPLIER": c["SUPPLIER"], "SERVICE TYPE": c["SERVICE TYPE"], "CODIGO": c["CODIGO"],
             "ESTADO": "PENDIENTE"} for c in a_cargar]


def texto_duracion(segundos):
    h, m = divmod(int(round(segundos / 60)), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"
