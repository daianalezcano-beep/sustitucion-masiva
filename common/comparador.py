"""
comparador.py — Compara dos reportes Product Analysis de Tourplan (antes y después de una corrida).

Sin Tourplan ni Streamlit: recibe el contenido de los dos archivos CSV y devuelve, por cada línea, cuánto cambió el costo y
si cambió la venta (Retail / Agent). Acepta los dos formatos que exporta Tourplan según la configuración: separador ';' o ','
y fechas '21-oct-26' o '21-Oct-2026'. El reporte trae dos columnas llamadas "Agent" (el código del agente y el precio): la
segunda se toma como el precio.
"""

import csv
import io
import re
from collections import Counter
from datetime import datetime

TOLERANCIA = 0.005
MESES = {"ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "aug": 8,
         "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12, "dec": 12}
# Columnas que se comparan además del costo y la venta (si cambian, se informa como "otros cambios").
OTRAS = ("Status", "Pax", "Qty", "Total", "Rooms", "Nights", "Currency", "VoucherNo", "D.O.T")
CLAVE = ("Reference", "Option", "Date", "Loc", "Service", "Supplier", "Pax")


def _decodificar(contenido):
    if isinstance(contenido, str):
        return contenido
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return contenido.decode(enc)
        except UnicodeDecodeError:
            continue
    return contenido.decode("utf-8", errors="replace")


def fecha(txt):
    """'21-oct-26', '21-Oct-2026' o '21/10/2026' -> datetime (None si no se entiende)."""
    t = (txt or "").strip().lower()
    m = re.fullmatch(r"(\d{1,2})[-/ ]([a-záéíóú]{3,}|\d{1,2})[-/ ](\d{2,4})", t)
    if not m:
        return None
    dia, mes, anio = m.group(1), m.group(2), int(m.group(3))
    mes = int(mes) if mes.isdigit() else MESES.get(mes[:3])
    if not mes:
        return None
    try:
        return datetime(anio + 2000 if anio < 100 else anio, mes, int(dia))
    except ValueError:
        return None


def numero(txt):
    """'1,234.50', '1.234,50', '75.97', '75,97' -> float (None si está vacío o no es un número)."""
    t = (txt or "").strip().replace(" ", "")
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if re.fullmatch(r"-?\d+,\d{1,2}", t) else t.replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def leer_reporte(contenido):
    """Contenido del CSV -> lista de dicts por línea. Las columnas repetidas ('Agent') pasan a 'Agent', 'Agent (2)'."""
    texto = _decodificar(contenido)
    primera = texto.lstrip("﻿").splitlines()[0] if texto.strip() else ""
    delimitador = ";" if primera.count(";") > primera.count(",") else ","
    lector = csv.reader(io.StringIO(texto.lstrip("﻿")), delimiter=delimitador)
    filas = [f for f in lector if any(c.strip() for c in f)]
    if not filas:
        return []
    columnas, vistas = [], {}
    for c in filas[0]:
        c = c.strip()
        vistas[c] = vistas.get(c, 0) + 1
        columnas.append(c if vistas[c] == 1 else f"{c} ({vistas[c]})")
    return [dict(zip(columnas, (c.strip() for c in f))) for f in filas[1:]]


def _clave(f):
    partes = []
    for c in CLAVE:
        v = f.get(c, "")
        if c == "Date":
            d = fecha(v)
            v = d.strftime("%Y-%m-%d") if d else v
        partes.append(v.strip().upper())
    return tuple(partes)


def _igual_num(a, b):
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= TOLERANCIA)


def _norm_otro(col, v):
    if col in ("D.O.T",):
        d = fecha(v)
        return d.strftime("%Y-%m-%d") if d else (v or "").strip()
    n = numero(v) if col in ("Pax", "Qty", "Total", "Rooms", "Nights") else None
    return str(n) if n is not None else (v or "").strip()


def comparar(antes, despues, no_tocada=None):
    """Devuelve {'resumen': {...}, 'lineas': [...]}. Cada línea: referencia, servicio, costo y venta antes/después, y un
    'Resultado': Venta modificada / Costo cambió / Otros cambios / Sin cambios / Solo antes / Solo después.
    `no_tocada`: función opcional que dice si una línea del reporte es de las que la herramienta NO procesa (por ejemplo,
    proveedor por asignar). Sirve de grupo de control: si la venta también cambió ahí, la causa no son las sustituciones."""
    pre, post = leer_reporte(antes), leer_reporte(despues)
    grupos_post = {}
    for f in post:
        grupos_post.setdefault(_clave(f), []).append(f)
    lineas, usadas = [], set()
    for f in pre:
        lista = grupos_post.get(_clave(f), [])
        g = lista.pop(0) if lista else None
        lineas.append(_linea(f, g, no_tocada))
    for lista in grupos_post.values():
        for g in lista:
            lineas.append(_linea(None, g, no_tocada))
    orden = {"Venta modificada": 0, "Solo antes": 1, "Solo después": 1, "Otros cambios": 2, "Costo cambió": 3, "Sin cambios": 4}
    lineas.sort(key=lambda x: (orden[x["Resultado"]], x["Reference"], x["Option"], x["Fecha"]))
    cuenta = lambda r: sum(1 for x in lineas if x["Resultado"] == r)
    resumen = {
        "Líneas antes": len(pre), "Líneas después": len(post),
        "Venta modificada": cuenta("Venta modificada"),
        "Costo cambió": cuenta("Costo cambió") + sum(1 for x in lineas if x["Resultado"] in ("Venta modificada", "Otros cambios")
                                                      and x["Costo antes"] != x["Costo después"]),
        "Otros cambios": cuenta("Otros cambios"),
        "Solo en uno de los dos": cuenta("Solo antes") + cuenta("Solo después"),
        "Sin cambios": cuenta("Sin cambios"),
    }
    if no_tocada:
        control = [x for x in lineas if x["Grupo"] == "No tocada" and x["Resultado"] != "Solo antes" and x["Resultado"] != "Solo después"]
        resumen["Control: líneas no tocadas"] = len(control)
        resumen["Control: con venta modificada"] = sum(1 for x in control if x["Resultado"] == "Venta modificada")
    # Variaciones de la venta (Retail) más comunes: si casi todas las líneas cambiaron en el mismo porcentaje, apunta a un factor común
    # (por ejemplo un cambio de tipo de cambio) y no a algo propio de cada reserva.
    variaciones = Counter((x["Δ Retail %"], x["Moneda"]) for x in lineas if x["Resultado"] == "Venta modificada" and x["Δ Retail %"] is not None)
    return {"resumen": resumen, "lineas": lineas,
            "variaciones_venta": [{"Δ Retail %": p, "Moneda": m, "Líneas": n} for (p, m), n in variaciones.most_common(8)]}


def _linea(f, g, no_tocada=None):
    base = f or g
    out = {"Grupo": "No tocada" if no_tocada and no_tocada(base) else "Sustituida", "Supplier": base.get("Supplier", ""),
           "Reference": base.get("Reference", ""), "Booking": base.get("BookingName", ""), "Loc": base.get("Loc", ""),
           "Service": base.get("Service", ""), "Option": base.get("Option", ""), "Description": base.get("Description", ""),
           "Fecha": (fecha(base.get("Date", "")) or datetime.min).strftime("%d/%m/%Y") if fecha(base.get("Date", "")) else base.get("Date", "")}
    if f is None or g is None:
        out.update({"Moneda": base.get("Currency", ""), "Costo antes": None, "Costo después": None, "Δ Costo": None, "Δ %": None,
                    "Retail antes": None, "Retail después": None, "Δ Retail %": None, "Agent antes": None, "Agent después": None,
                    "Δ Agent %": None,
                    "Resultado": "Solo antes" if g is None else "Solo después", "Detalle": "no aparece en el otro reporte"})
        return out
    c0, c1 = numero(f.get("Cost")), numero(g.get("Cost"))
    r0, r1 = numero(f.get("Retail")), numero(g.get("Retail"))
    a0, a1 = numero(f.get("Agent (2)")), numero(g.get("Agent (2)"))
    venta = not (_igual_num(r0, r1) and _igual_num(a0, a1))
    otros = [f"{c}: {f.get(c, '')} → {g.get(c, '')}" for c in OTRAS if _norm_otro(c, f.get(c, "")) != _norm_otro(c, g.get(c, ""))]
    costo = not _igual_num(c0, c1)
    delta = (c1 - c0) if c0 is not None and c1 is not None else None
    pct = (delta / c0 * 100) if delta is not None and c0 else None
    pct_v = lambda a, b: round((b - a) / a * 100, 2) if a and b is not None else None
    out.update({"Moneda": g.get("Currency", ""), "Costo antes": c0, "Costo después": c1, "Δ Costo": None if delta is None else round(delta, 2),
                "Δ %": None if pct is None else round(pct, 1), "Retail antes": r0, "Retail después": r1, "Δ Retail %": pct_v(r0, r1),
                "Agent antes": a0, "Agent después": a1, "Δ Agent %": pct_v(a0, a1)})
    out["Resultado"] = ("Venta modificada" if venta else "Otros cambios" if otros else "Costo cambió" if costo else "Sin cambios")
    out["Detalle"] = "; ".join(([f"venta: Retail {r0} → {r1}, Agent {a0} → {a1}"] if venta else []) + otros)
    return out


def a_csv(lineas):
    """Las líneas comparadas como CSV (';', UTF-8 con BOM, para abrir directo en Excel)."""
    if not lineas:
        return b""
    salida = io.StringIO()
    w = csv.DictWriter(salida, fieldnames=list(lineas[0]), delimiter=";", lineterminator="\r\n")
    w.writeheader()
    w.writerows(lineas)
    return ("﻿" + salida.getvalue()).encode("utf-8")
