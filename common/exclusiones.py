"""
exclusiones.py — Lista de servicios con el costo manualizado (Override), que NO se deben procesar.

Sin Tourplan ni Streamlit. Un costo manualizado es una negociación previa que la sustitución pisaría; Substitute Service no muestra esa información, así
que sale de un reporte de Tourplan (hoy una tabla dinámica; más adelante un reporte dinámico) con las columnas: Bkg. Reference, Srv. Date, Voucher #,
Srv. Type, Opt. Description, Override y Full option code (ubicación 3 + servicio 2 + proveedor 6 + código del resto: BUETR1TEP01CT437 = BUE + TR + 1TEP01 + CT437).

La lista se guarda en la pestaña EXCLUSIONES del Sheet (se reemplaza completa en cada carga). El script no procesa las filas de Results que coincidan, en referencia + fecha + código (y ubicación,
servicio y proveedor), con una línea de la lista: las destilda antes de la sustitución y las informa al final de la corrida con su motivo para revisarlas a mano. Results
no muestra el voucher: si hay más filas con esa clave que líneas en la lista, no se pueden distinguir y se excluyen todas. Los demás files de la combinación se procesan.
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
    """Líneas de la lista que caen en la combinación de esta fila del Sheet y en su tipo de reservas (una fila sin TIPO coincide con cualquiera). Sirve para
    saber si la combinación tiene alguna línea manualizada. `exclusiones`: resultado de por_combinacion()."""
    candidatas = exclusiones.get(_clave(fila.get("LOCATION"), fila.get("SUPPLIER"), fila.get("SERVICE TYPE"), fila.get("CODIGO")), [])
    tipo = T.normalizar_tipo(fila.get("TIPO"))
    return [l for l in candidatas if not tipo or l.get("TIPO", "") == tipo]


def _fecha_dd(txt):
    d = L.parsear_fecha(txt)
    return d.strftime("%d/%m/%Y") if d else str(txt or "").strip().upper()


def clave_de_resultado(f):
    """Clave de una fila de Results (dict del script: booking_ref, date, location, service, supplier, code): (referencia, fecha DD/MM/AAAA, ubicación, servicio, proveedor, código)."""
    return (str(f.get("booking_ref", "")).strip().upper(), _fecha_dd(f.get("date")), str(f.get("location", "")).strip().upper(),
            str(f.get("service", "")).strip().upper(), str(f.get("supplier", "")).strip().upper(), str(f.get("code", "")).strip().upper())


def clave_de_linea(l):
    return (str(l["REFERENCE"]).strip().upper(), _fecha_dd(l["SERVICE DATE"]), str(l["LOCATION"]).strip().upper(),
            str(l["SERVICE TYPE"]).strip().upper(), str(l["SUPPLIER"]).strip().upper(), str(l["CODIGO"]).strip().upper())


def clave_para_pantalla(f):
    """La misma clave de una fila de Results como la lee la pantalla de Tourplan (fecha DD/Mon/AAAA en mayúsculas), separada por '|': sirve para ubicar la fila en la grilla."""
    ref, fecha, loc, serv, sup, cod = clave_de_resultado(f)
    d = L.parsear_fecha(fecha)
    return "|".join([ref, (L.fmt_tp(d) if d else fecha).upper(), loc, serv, sup, cod])


def separar_resultados(filas, exclusiones, loc, sup, stype, cod):
    """Separa las filas de Results de una combinación en (a_procesar, excluidas). Se excluye toda fila de Results que coincida en referencia + fecha +
    código (y ubicación, servicio y proveedor) con una línea de la lista; si en Results hay más filas con esa clave que líneas en la lista, no se pueden
    distinguir y se excluyen todas. `excluidas`: [{'fila', 'motivo', 'lineas'}]. Sin líneas para la combinación, todo se procesa."""
    candidatas = exclusiones.get(_clave(loc, sup, stype, cod), []) if exclusiones else []
    if not candidatas:
        return list(filas), []
    en_lista = {}
    for l in candidatas:
        en_lista.setdefault(clave_de_linea(l), []).append(l)
    en_results = {}
    for f in filas:
        en_results.setdefault(clave_de_resultado(f), []).append(f)
    a_procesar, excluidas = [], []
    for f in filas:
        k = clave_de_resultado(f)
        if k not in en_lista:
            a_procesar.append(f)
            continue
        lineas, n_filas = en_lista[k], len(en_results[k])
        vouchers = ", ".join(l["VOUCHER"] for l in lineas if l.get("VOUCHER"))
        if n_filas > len(lineas):
            motivo = (f"Hay {n_filas} líneas en Tourplan con la misma referencia, fecha y código y la lista de costos manualizados tiene {len(lineas)}"
                      f"{' (voucher ' + vouchers + ')' if vouchers else ''}: no se pueden distinguir, se excluyen todas. Revisar a mano")
        else:
            motivo = f"Costo manualizado (Override) según la lista de exclusiones{' (voucher ' + vouchers + ')' if vouchers else ''}. Revisar a mano"
        excluidas.append({"fila": f, "motivo": motivo, "lineas": lineas})
    return a_procesar, excluidas


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
