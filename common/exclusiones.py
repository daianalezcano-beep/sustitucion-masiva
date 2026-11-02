"""
exclusiones.py — Lista de servicios con el costo manualizado (Override), que NO se deben procesar.

Sin Tourplan ni Streamlit. Un costo manualizado es una negociación previa que la sustitución pisaría; Substitute Service no muestra esa información, así
que sale de un reporte de Tourplan (hoy una tabla dinámica; más adelante un reporte dinámico) con las columnas: Bkg. Reference, Srv. Date, Voucher #,
Srv. Type, Opt. Description, Override y Full option code (ubicación 3 + servicio 2 + proveedor 6 + código del resto: BUETR1TEP01CT437 = BUE + TR + 1TEP01 + CT437).

La lista se guarda en la pestaña EXCLUSIONES del Sheet (se reemplaza completa en cada carga). El script no procesa una combinación (ubicación + servicio +
proveedor + código) que tenga alguna línea en la lista del mismo tipo de reservas: la marca EXCLUIDA y la informa al final de la corrida para revisarla a mano.
Se salta la combinación entera (y no solo la línea) porque Results no muestra el voucher y dos líneas con la misma referencia, fecha y código no se distinguen.
"""

import csv
import io
import re
import unicodedata
from datetime import datetime

from common import logica as L
from common import tipo_reserva as T

HOJA = "EXCLUSIONES"
COLUMNAS = ["REFERENCE", "SERVICE DATE", "LOCATION", "SERVICE TYPE", "SUPPLIER", "CODIGO", "TIPO", "VOUCHER", "DESCRIPCION", "CARGADA"]

_ALIAS = {
    "referencia": ("bkg. reference", "bkg reference", "booking reference", "reference", "bkg.reference"),
    "fecha": ("srv. date", "srv date", "service date", "srv.date"),
    "voucher": ("voucher #", "voucher", "voucher no", "voucher number", "voucherno"),
    "codigo_completo": ("full option code", "fulloptioncode", "full code", "option code completo"),
    "descripcion": ("opt. description", "opt description", "option description", "description"),
    "override": ("override",),
    "servicio": ("srv. type", "srv type", "service type"),
}


def _norm(txt):
    t = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode().lower().strip()
    return re.sub(r"\s+", " ", t)


def _decodificar(contenido):
    if isinstance(contenido, str):
        return contenido
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return contenido.decode(enc)
        except UnicodeDecodeError:
            continue
    return contenido.decode("utf-8", errors="replace")


def _filas_del_archivo(contenido, nombre=""):
    """Contenido de un CSV/TSV/TXT o de un .xlsx (primera hoja) -> lista de listas de texto."""
    if str(nombre).lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        libro = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
        out = []
        for fila in libro.worksheets[0].iter_rows(values_only=True):
            celdas = [c.strftime("%d/%m/%Y") if isinstance(c, datetime) else ("" if c is None else str(c).strip()) for c in fila]
            if any(celdas):
                out.append(celdas)
        return out
    texto = _decodificar(contenido).lstrip("﻿")
    primera = texto.splitlines()[0] if texto.strip() else ""
    delimitador = max(("\t", ";", ","), key=primera.count)
    return [[c.strip() for c in f] for f in csv.reader(io.StringIO(texto), delimiter=delimitador) if any(c.strip() for c in f)]


def partir_codigo_completo(codigo):
    """'BUETR1TEP01CT437' -> ('BUE', 'TR', '1TEP01', 'CT437'). None si es demasiado corto para tener código."""
    s = str(codigo or "").strip().upper()
    if len(s) < 12:
        return None
    return s[:3], s[3:5], s[5:11], s[11:]


def leer_lista(contenido, nombre=""):
    """Lee la lista de exclusiones. Devuelve {'lineas': [...], 'invalidas': [(n.º de fila, motivo)], 'total': filas leídas}. Cada línea es un dict con
    las COLUMNAS (sin CARGADA). Si hay columna Override, solo cuentan las filas con «Yes»."""
    filas = _filas_del_archivo(contenido, nombre)
    if not filas:
        raise ValueError("El archivo no tiene filas.")
    cabecera = [_norm(c) for c in filas[0]]
    col = {}
    for clave, alias in _ALIAS.items():
        for a in alias:
            if a in cabecera:
                col[clave] = cabecera.index(a)
                break
    faltan = [n for n in ("referencia", "fecha", "codigo_completo") if n not in col]
    if faltan:
        raise ValueError("No parece la lista de costos manualizados: faltan las columnas " + ", ".join(
            {"referencia": "Bkg. Reference", "fecha": "Srv. Date", "codigo_completo": "Full option code"}[n] for n in faltan) + f" (columnas del archivo: {filas[0]}).")
    lineas, invalidas = [], []
    for n, f in enumerate(filas[1:], start=2):
        dato = lambda k: f[col[k]].strip() if k in col and col[k] < len(f) else ""
        if "override" in col and _norm(dato("override")) not in ("yes", "si", "sí", "true", "1", "y"):
            continue
        partes = partir_codigo_completo(dato("codigo_completo"))
        fecha = L.parsear_fecha(dato("fecha"))
        if not dato("referencia"):
            invalidas.append((n, "sin referencia de reserva"))
        elif partes is None:
            invalidas.append((n, f"código completo no válido: «{dato('codigo_completo')}»"))
        elif fecha is None:
            invalidas.append((n, f"fecha no válida: «{dato('fecha')}»"))
        else:
            loc, serv, prov, cod = partes
            lineas.append({"REFERENCE": dato("referencia").upper(), "SERVICE DATE": fecha.strftime("%d/%m/%Y"), "LOCATION": loc, "SERVICE TYPE": serv,
                           "SUPPLIER": prov, "CODIGO": cod, "TIPO": T.tipo_de_reserva(dato("referencia")) or "", "VOUCHER": dato("voucher"),
                           "DESCRIPCION": dato("descripcion")})
    return {"lineas": lineas, "invalidas": invalidas, "total": len(filas) - 1}


def _clave(loc, sup, stype, cod):
    return tuple(str(x or "").strip().upper() for x in (loc, sup, stype, cod))


def por_combinacion(lineas):
    """{(LOCATION, SUPPLIER, SERVICE TYPE, CODIGO): [líneas]}."""
    out = {}
    for l in lineas:
        out.setdefault(_clave(l["LOCATION"], l["SUPPLIER"], l["SERVICE TYPE"], l["CODIGO"]), []).append(l)
    return out


def lineas_que_excluyen(fila, exclusiones):
    """Líneas de la lista que hacen que no se procese esta fila del Sheet (misma combinación y mismo tipo de reservas; una fila sin TIPO coincide con cualquiera).
    `exclusiones`: resultado de por_combinacion()."""
    candidatas = exclusiones.get(_clave(fila.get("LOCATION"), fila.get("SUPPLIER"), fila.get("SERVICE TYPE"), fila.get("CODIGO")), [])
    tipo = T.normalizar_tipo(fila.get("TIPO"))
    return [l for l in candidatas if not tipo or l.get("TIPO", "") == tipo]


def texto_de_exclusion(lineas, maximo=8):
    """Detalle para OBSERVACIONES / consola: 'EAGP100 21/12/2026 (voucher 123); …'."""
    partes = [f"{l['REFERENCE']} {l['SERVICE DATE']}" + (f" (voucher {l['VOUCHER']})" if l.get("VOUCHER") else "") for l in lineas[:maximo]]
    return "; ".join(partes) + (f"; … (+{len(lineas) - maximo})" if len(lineas) > maximo else "")


def resumen_de_carga(lineas):
    """(n.º de líneas, n.º de combinaciones) de una lista."""
    return len(lineas), len(por_combinacion(lineas))


# ── Lectura y escritura en el Sheet ──────────────────────────────────────────

def _hoja(ws, crear=False):
    sh = ws.spreadsheet
    for h in sh.worksheets():
        if h.title.strip().upper() == HOJA:
            return h
    if not crear:
        return None
    return sh.add_worksheet(title=HOJA, rows=100, cols=len(COLUMNAS))


def leer_del_sheet(ws_substitute):
    """Líneas guardadas en la pestaña EXCLUSIONES (lista de dicts con las COLUMNAS); [] si no hay pestaña o no se pudo leer."""
    try:
        h = _hoja(ws_substitute)
        if h is None:
            return []
        valores = h.get_all_values()
        if len(valores) < 2:
            return []
        cab = [_norm(c).upper() for c in valores[0]]
        out = []
        for f in valores[1:]:
            d = {c: (f[cab.index(c)].strip() if c in cab and cab.index(c) < len(f) else "") for c in [x.upper() for x in COLUMNAS]}
            if d["REFERENCE"] and d["CODIGO"]:
                out.append({k: d.get(k, "") for k in [x.upper() for x in COLUMNAS]})
        return out
    except Exception:
        return []


def guardar_en_el_sheet(ws_substitute, lineas, ahora=None):
    """Reemplaza el contenido de la pestaña EXCLUSIONES con la lista (crea la pestaña si falta). Se escribe como texto."""
    ahora = (ahora or datetime.now()).strftime("%d/%m/%Y %H:%M")
    h = _hoja(ws_substitute, crear=True)
    filas = [COLUMNAS] + [[l.get(c, "") if c != "CARGADA" else ahora for c in COLUMNAS] for l in lineas]
    h.clear()
    h.update(range_name="A1", values=filas, value_input_option="RAW")


def fecha_de_carga(lineas):
    """datetime de la carga de la lista (columna CARGADA 'DD/MM/AAAA HH:MM'), o None."""
    for l in lineas:
        t = str(l.get("CARGADA", "")).strip()
        if t:
            try:
                return datetime.strptime(t, "%d/%m/%Y %H:%M")
            except ValueError:
                continue
    return None
