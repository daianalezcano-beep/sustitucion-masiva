"""
═══════════════════════════════════════════════════════════════════════════
  TOURPLAN NX — Actualización de costos por doble sustitución
  (Booking Operations > Substitute Service)
═══════════════════════════════════════════════════════════════════════════

Contexto: al actualizar las tarifas de un componente, los costos de los
servicios ya cargados en reservas quedan desfasados. Substitute Service
reemplaza un servicio por otro en muchas reservas; con Retain Sell Price
tildado la venta no cambia y solo se recalcula el costo. Tourplan no permite
sustituir un servicio por sí mismo, así que se hace en dos pasos por cada
combinación Location + Supplier + Service Type + Código:
    1) original A -> provisorio P (código 000000, uno por Location x Service Type)
    2) P -> original A   (el costo se recalcula con la tarifa vigente)

Modos (TOURPLAN_MODO):
  - lectura (default, Fase 1): por cada fila PENDIENTE abre Substitute Service, aplica el
    filtro, lee Results y escribe lo leído en SUBSTITUTE_DETALLE. NUNCA hace click en ningún OK.
  - aplicar (Fase 2): hace el ida y vuelta COMPLETO sobre cada combinación y MODIFICA reservas
    en producción, sin tope de reservas (no existe ningún límite por cantidad). Si la combinación tiene varios estados de servicio se procesa un
    estado por vez, usando el filtro Service Status de Tourplan. Pickup/drop-off/Remarks solo se analizan
    y reponen en los Service Types de TOURPLAN_PUDO_SERVICE_TYPES (ver procesar_fila_aplicar). Con
    TOURPLAN_CONFIRMAR_OK=1 (default) se detiene una vez por combinación, antes de la ida.

Provisorios: viven en el código (PROVISORIOS: Location + Service Type, proveedor 1EURO1). Location y Service Type son los de la
fila de entrada, el código siempre es 000000 y el proveedor depende del Service Type. Si el
provisorio no existe en Tourplan la fila termina en ERROR sin tocar nada.

Reglas de negocio y de seguridad:
  - Una fila del input = una combinación; se procesa completa y secuencialmente.
  - Filas repetidas del input (misma combinación) -> DUPLICADO, sin procesar.
  - Filtro de estados: se destilda SOLO Booking Status "CX - Cancelled" (decisión
    de la usuaria). Service Status queda todo tildado, salvo en modo aplicar con varios estados: ahí cada
    grupo se filtra con solo su estado. Se verifica que quedó destildado ese ítem y ningún otro.
  - Fechas: "Desde" obligatoria (la ingresa el usuario en cada corrida), "Hasta"
    por defecto 31/12/49 (= 2049). Se escriben con año de 2 dígitos (el campo
    rechaza DD/MM/YYYY) y se verifica el valor normalizado que guarda el input.
  - Código exacto: en Location/Service/Supplier nunca se acepta el ítem
    resaltado; se elige la fila de código idéntico y se lee el valor final.
  - Lectura de Results por contenido (nunca por índice ni WebElements
    cacheados); el total leído debe coincidir con Found o la fila es ERROR.
  - Modo lectura: prueba además "Select all" con el checkbox del encabezado (verifica
    Selected == Found y deselecciona); nunca toca OK.
  - Modo lectura: la fila queda PENDIENTE (lista para aplicar) con el conteo en
    OBSERVACIONES; solo los errores y duplicados cambian ESTADO.
  - try: login -> filas -> finally: logout (solo si hubo sesión) + quit.
  - Abort cooperativo entre filas; salida 75 si se abortó.

Config (variables de entorno, TOURPLAN_*): ver sección CONFIG.
"""

# ── CAMBIOS ──
# v0.3 (2026-10-20): sin tope de reservas; un estado de servicio por vez con el filtro Service Status; pickup/drop-off
#   solo en los Service Types habilitados (leídos de la celda PU/DO del listado, ya no del panel derecho).
#   Optimización de tiempos (SIN validar contra Tourplan): esperas activas en lugar de pausas fijas y, al volver a
#   buscar, solo se completan los campos del filtro que difieren de los que la pantalla ya tiene.
# v0.2 (2026-10-19): modo aplicar (Fase 2); provisorios en el código (se elimina la hoja PROVISORIOS).
# v0.1 (2026-10-19): primera versión, solo modo lectura. SIN CORRER contra
#   Tourplan ni contra el Sheet; la lógica pura (common/logica.py) sí tiene
#   pruebas por simulación (tests/test_logica.py).

VERSION = "0.3"
VERSION_FECHA = "2026-10-20"

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from selenium.webdriver.common.by import By

from common import logica as L
from common import tp_selenium as tp
from common import ejecucion as E
from common.abort import ABORT_EXIT_CODE, AbortadoPorUsuario, abort_solicitado, chequear_abort
from common.sheets_batch import actualizar_columna_rango, agregar_filas_batch, asegurar_columnas_tolerante
from common.sheets_client import actualizar_fila_sheet, cargar_sheet, conectar_sheets
from common.user_config import CREDENTIALS_PATH as _CRED_DEFAULT
from common.user_config import TOKEN_PATH as _TOKEN_DEFAULT
from common.tp_selenium import dump, dump_html, esperar_fin_carga, jc, pausa, set_val, set_val_con_blur, ss

# ── CONFIG ──
MODO = os.environ.get("TOURPLAN_MODO", "lectura")
SHEET_URL = os.environ.get("TOURPLAN_SHEET_URL", "")
HOJA = os.environ.get("TOURPLAN_HOJA", "SUBSTITUTE")
HOJA_DET = os.environ.get("TOURPLAN_HOJA_DETALLE", "SUBSTITUTE_DETALLE")
CREDENTIALS_PATH = os.environ.get("TOURPLAN_CREDENTIALS_PATH", _CRED_DEFAULT)
TOKEN_PATH = os.environ.get("TOURPLAN_TOKEN_PATH", _TOKEN_DEFAULT)
FECHA_DESDE = os.environ.get("TOURPLAN_FECHA_DESDE", "")            # obligatoria
FECHA_HASTA = os.environ.get("TOURPLAN_FECHA_HASTA", "31/12/49")    # 31/12/49 = 2049
FILA = os.environ.get("TOURPLAN_FILA", "").strip()                  # solo esa fila del Sheet
PROBAR_PANEL = os.environ.get("TOURPLAN_PROBAR_PANEL", "1").strip() in ("1", "true", "True")
PROBAR_SELECCION = os.environ.get("TOURPLAN_PROBAR_SELECCION", "1").strip() in ("1", "true", "True")
# Modo lectura: por cada estado de servicio que aparece en Results, prueba el filtro Service Status (solo ese estado) y
# comprueba que devuelve exactamente esas reservas. No modifica nada. TOURPLAN_PROBAR_FILTRO_ESTADO=0 lo desactiva.
PROBAR_FILTRO_ESTADO = os.environ.get("TOURPLAN_PROBAR_FILTRO_ESTADO", "1").strip() in ("1", "true", "True")
ESCRIBIR_DETALLE = os.environ.get("TOURPLAN_ESCRIBIR_DETALLE", "1").strip() in ("1", "true", "True")
CONFIRMAR_OK = os.environ.get("TOURPLAN_CONFIRMAR_OK", "1").strip() in ("1", "true", "True")
# Si la app define esta carpeta, la confirmación se hace por archivos (botones en la app); si no, escribiendo SI en la consola.
CONFIRM_DIR = os.environ.get("TOURPLAN_CONFIRM_DIR", "").strip()
# Service Types (separados por coma) en los que SÍ se analizan y reponen pickup/drop-off/Remarks. En los demás se
# asume que no corresponden: si igual aparecen datos en el listado, la combinación se rechaza sin tocar nada.
PUDO_SERVICE_TYPES = {t.strip().upper() for t in os.environ.get("TOURPLAN_PUDO_SERVICE_TYPES", "").split(",") if t.strip()}
# Reusar la pantalla Substitute Service entre búsquedas (volver a la solapa Filter) ahorra ~15 s por
# consulta. Si la pantalla no está como se espera, se abre de cero. TOURPLAN_REUSAR_PANTALLA=0 la desactiva.
# NO está validado contra Tourplan real: Found podría quedar con el valor de la consulta anterior
# (las verificaciones de Found, de filas fuera del filtro y de fila vs. foto inicial lo detectan).
REUSAR_PANTALLA = os.environ.get("TOURPLAN_REUSAR_PANTALLA", "1").strip() in ("1", "true", "True")

COL_ESTADO, COL_OBS, COL_TS = "ESTADO", "OBSERVACIONES", "TIMESTAMP"
COLS_INPUT = ["LOCATION", "SUPPLIER", "SERVICE TYPE", "CODIGO", COL_ESTADO]
COLS_DET = ["TIMESTAMP", "CORRIDA", "LOCATION", "SUPPLIER", "SERVICE TYPE", "CODIGO", "DESCRIPTION",
            "BOOKING NAME", "BOOKING REF", "TRAVEL DATE", "DATE", "STATUS_ORIGINAL", "PUDO_ORIGINAL",
            "PICKUP_TEXTO", "DROPOFF_TEXTO", "REMARKS", "FASE", "RESULTADO", "OBSERVACIONES"]

URL_OPERATIONS = f"{tp.BASE_URL}/#/operations/OperationsBookings"
URL_HOME = f"{tp.BASE_URL}/#/home"
# Texto del Booking Status "cancelado" según el ambiente: en producción "CX - Cancelled"; en test el mismo estado se llama
# "Cancelled". Se usa el primero que exista en la lista (coincidencia exacta).
BOOKING_STATUS_EXCLUIR = ("CX - Cancelled", "Cancelled")

# Provisorios (viven en el código): uno por Location + Service Type, todos bajo el proveedor 1EURO1 y con el
# código 000000 (los options Z*NO USAR* ya creados en Tourplan). Una fila cuya Location + Service Type no esté
# acá termina en ERROR sin tocar Tourplan. Service Types separados por espacio, por Location.
CODIGO_PROVISORIO = L.CODIGO_PROVISORIO
SUPPLIER_PROVISORIO = "1EURO1"
PROVISORIOS_POR_LOCATION = {
    "BAB": "CO",
    "BRC": "CO GU ST",
    "BUE": "CM GA GU PJ ST TI TK TR",
    "CAC": "GA GU TI TK TR",
    "CAF": "GA GU ST TI TK TR",
    "CHA": "GU ST TK TR",
    "COB": "GA GU TI TR",
    "COL": "CO",
    "COR": "CO",
    "CUZ": "CO",
    "EQS": "CO",
    "FTE": "GA GU ST TI TK TN TR CH",
    "GPS": "CO",
    "HCL": "GA GU TR",
    "HUM": "GA GU TI TR",
    "IGR": "CO GA GU ST TK TR",
    "IGU": "ST TK TR",
    "JUJ": "GA GU TR",
    "MDQ": "CO",
    "MDZ": "CO",
    "NQN": "EN",
    "PAI": "CO GU TK",
    "PMC": "CO",
    "PML": "GU",
    "PMY": "CO ST",
    "PNT": "CO GU TR",
    "PUQ": "CO GU",
    "PUR": "CO GA GU ST TI TK TR",
    "PUX": "CO",
    "QLM": "TK",
    "ROS": "CO",
    "SAL": "GA TI",
    "SLA": "GA GU ST TI TK TR",
    "SUS": "GA",
    "TIL": "GA GU TI TK TR",
    "TLG": "ST",
    "TUC": "GU TR",
    "UAQ": "CO",
    "UIO": "CO",
    "USH": "GA GU ST TI TK TR CH",
    "RTU": "GU TR",
}
PROVISORIOS = {(loc, st): SUPPLIER_PROVISORIO for loc, sts in PROVISORIOS_POR_LOCATION.items() for st in sts.split()}
# La validación previa de que el provisorio está libre mira todo el historial, no solo el rango de la corrida.
GUARDA_DESDE = datetime(2020, 1, 1)

# Glosario de Service Status: el listado (Results) muestra el código y el filtro el texto. CC y LC se llaman igual
# en el filtro ("Cancellation Charge"): se distinguen mirando qué código devuelve Results (ver foto_del_grupo).
SERVICE_STATUS = {
    "CC": "Cancellation Charge", "CE": "Cancelled Ext Source", "CH": "Cancel Hyperg c/IVA", "CK": "Complimentary Latam",
    "CO": "Complimentary", "CQ": "Cancellation On Req", "CX": "Cancelled", "DQ": "Not Confirmed-DataRQ",
    "FS": "Free Sale Confirmed", "IP": "Pending Issue", "IT": "Initial", "LC": "Cancellation Charge", "LK": "Confirmed LK",
    "LX": "Cancelled-No Charge", "MQ": "Modification On Req", "OK": "Confirmed OK", "OP": "Optional", "OT": "OK Tráfico",
    "PA": "Partially Alloc Conf", "RQ": "On Request", "RT": "Reconfirmado Trafico", "WL": "Waiting List",
}


class ErrorFila(Exception):
    """Error esperado de una fila: lleva el ESTADO a escribir y el detalle."""

    def __init__(self, estado, detalle):
        super().__init__(detalle)
        self.estado = estado
        self.detalle = detalle


def ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def esperar(fn, timeout=10):
    """Polling: devuelve el primer valor truthy de fn() o None al vencer."""
    fin = time.time() + timeout * tp.VELOCIDAD
    while time.time() < fin:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(0.15)
    return None


# ── Pantalla Substitute Service ─────────────────────────────────────────────

def dialog_subs(driver):
    """Último tp-dialog con tp-substitute-service (Angular no saca del DOM los
    diálogos cerrados; nunca se ancla por nth-of-type)."""
    return driver.execute_script("""
        var ds = Array.from(document.querySelectorAll('tp-dialog'))
            .filter(function(d){ return d.querySelector('tp-substitute-service'); });
        return ds.length ? ds[ds.length - 1] : null;
    """)


def abrir_substitute(driver):
    # Pasar por Home fuerza a recargar la ruta (un driver.get con el mismo hash no recarga).
    driver.get(URL_HOME)
    pausa(2)
    driver.get(URL_OPERATIONS)
    pausa(5)
    esperar_fin_carga(driver)
    if not esperar(lambda: driver.find_elements(By.CSS_SELECTOR, "#operationsview"), 15):
        raise ErrorFila("ERROR: pantalla", "No cargó #operationsview (Booking Operations)")
    icono = None
    for css in ("#operationsview nav tp-nav > div > div > div > div", "#operationsview nav img"):
        els = driver.find_elements(By.CSS_SELECTOR, css)
        if els:
            icono = els[0]
            break
    if icono is None:
        raise ErrorFila("ERROR: pantalla", "No encontré el ícono de menú de #operationsview")
    jc(driver, icono)
    pausa(2.5)
    texto1 = driver.execute_script(
        "var e=document.querySelector('#operationsview nav li:nth-of-type(1)');"
        "return e ? (e.innerText||'').trim() : '';")
    if "substitute" not in texto1.lower() or any(x in texto1.lower() for x in ("pcm", "resource")):
        raise ErrorFila("ERROR: pantalla", f"El ítem 1 del menú dice {texto1!r}, no 'Substitute Service'")
    jc(driver, driver.find_element(By.CSS_SELECTOR, "#operationsview nav li:nth-of-type(1) > div > div"))
    pausa(4)
    esperar_fin_carga(driver)
    dlg = esperar(lambda: dialog_subs(driver), 15)
    if dlg is None:
        raise ErrorFila("ERROR: pantalla", "No apareció tp-substitute-service")
    ok = driver.execute_script(
        "return !!(arguments[0].querySelector('#serviceDateFrom') && arguments[0].querySelector('#serviceDateTo'));", dlg)
    if not ok:
        raise ErrorFila("ERROR: pantalla", "tp-substitute-service sin #serviceDateFrom/#serviceDateTo")


def preparar_pantalla(driver, ctx):
    if REUSAR_PANTALLA and ctx["abierta"]:
        motivo = ""
        try:
            jc(driver, driver.find_element(By.CSS_SELECTOR, "#tptablabel-filterTab"))
            # espera activa: la solapa Filter tiene que estar a la vista (con su campo Location visible)
            esperar(lambda: dialog_subs(driver).find_element(By.CSS_SELECTOR, "div.col1 li:nth-of-type(1) input").is_displayed(), 4)
            dlg = dialog_subs(driver)
            if dlg is not None and dlg.find_elements(By.CSS_SELECTOR, "div.col1 li:nth-of-type(1) input"):
                print("    Pantalla reutilizada: se vuelve a la solapa Filter sin reabrir Substitute Service")
                return
            motivo = "al volver a Filter no está el campo Location" if dlg is not None else "no se encontró el diálogo Substitute Service"
        except Exception as e:
            motivo = f"{type(e).__name__}: {str(e).splitlines()[0][:150] if str(e) else ''}"
        print(f"    ⚠ No se pudo reutilizar la pantalla ({motivo}): se abre Substitute Service de cero")
        ctx["abierta"] = False
    elif not REUSAR_PANTALLA:
        print("    Reutilización de pantalla desactivada (TOURPLAN_REUSAR_PANTALLA=0): se abre de cero")
    abrir_substitute(driver)
    ctx["abierta"] = True


# ── Filtro ───────────────────────────────────────────────────────────────────

JS_TD_EXACTO = """
    var v = arguments[1].toUpperCase();
    var rows = Array.from(arguments[0].querySelectorAll('tr')).filter(function(tr){
        return !!(tr.offsetWidth || tr.offsetHeight); });
    for (var tr of rows) {
        for (var td of tr.querySelectorAll('td.code')) {
            if ((td.innerText || '').trim().toUpperCase() === v) return td;
        }
    }
    return null;
"""


def completar_lista(driver, n, nombre, valor, dialogo=None):
    """Location/Service/Supplier: tipear, esperar la lista, elegir la fila de
    CÓDIGO IDÉNTICO (td.code) y leer el valor final. Sin coincidencia exacta
    no se toca nada más. `dialogo`: función que devuelve el diálogo vigente
    (por defecto Substitute Service; en el modal, dialog_repl)."""
    dialogo = dialogo or dialog_subs
    dlg = dialogo(driver)
    inp = dlg.find_element(By.CSS_SELECTOR, f"div.col1 li:nth-of-type({n}) input")
    li = driver.execute_script("return arguments[0].closest('li');", inp)
    set_val(driver, inp, "")
    time.sleep(0.15)
    set_val(driver, inp, valor)
    td = esperar(lambda: driver.execute_script(JS_TD_EXACTO, li, valor), 8)
    if td is None:
        visibles = driver.execute_script(
            "return Array.from(arguments[0].querySelectorAll('tr td.code')).map(function(t){"
            "return (t.innerText||'').trim();}).slice(0, 10);", li)
        raise ErrorFila("ERROR: sin coincidencia",
                        f"{nombre} '{valor}': ninguna fila de la lista tiene ese código exacto (vi: {visibles})")
    jc(driver, td)
    # espera activa: el campo toma el valor completo "CÓDIGO - descripción" al confirmarse la selección
    esperar(lambda: valor_lista(driver, n, dialogo).upper().startswith(valor.upper() + " - "), 5)
    final = valor_lista(driver, n, dialogo)
    if final.upper() != valor.upper() and not final.upper().startswith(valor.upper() + " - "):
        raise ErrorFila("ERROR: campo distinto",
                        f"{nombre}: pedí '{valor}' y quedó '{final}'")
    return final


def valor_lista(driver, n, dialogo=None):
    """Valor que muestra hoy el campo n (1 Location, 2 Service, 3 Supplier) del diálogo."""
    dialogo = dialogo or dialog_subs
    return (dialogo(driver).find_element(By.CSS_SELECTOR, f"div.col1 li:nth-of-type({n}) input").get_attribute("value") or "").strip()


def valor_code(driver, dialogo=None):
    dialogo = dialogo or dialog_subs
    return (dialogo(driver).find_element(By.CSS_SELECTOR, "#optionCode > tp-validator > input").get_attribute("value") or "").strip()


def descripcion_code(driver, dialogo=None):
    dialogo = dialogo or dialog_subs
    return (dialogo(driver).find_element(By.CSS_SELECTOR, "input.tpdescription-optiondescription").get_attribute("value") or "").strip()


def valor_fecha(driver, campo_id):
    """Fecha normalizada que guarda hoy el campo (input oculto), o None."""
    oculto = dialog_subs(driver).find_element(By.CSS_SELECTOR, f"#{campo_id} > div > input:nth-of-type(1)")
    return L.parsear_fecha(oculto.get_attribute("value"))


def coincide_codigo(actual, valor):
    """El campo muestra el código pedido ('BUE' o 'BUE - Buenos Aires')."""
    a, v = actual.strip().upper(), valor.strip().upper()
    return a == v or a.startswith(v + " - ")


def completar_code(driver, codigo, dialogo=None):
    dialogo = dialogo or dialog_subs
    dlg = dialogo(driver)
    cod = dlg.find_element(By.CSS_SELECTOR, "#optionCode > tp-validator > input")
    set_val_con_blur(driver, cod, codigo)
    pausa(0.8)
    esperar_fin_carga(driver)

    def descripcion():
        d = dialogo(driver).find_element(By.CSS_SELECTOR, "input.tpdescription-optiondescription")
        return (d.get_attribute("value") or "").strip()

    desc = esperar(descripcion, 8)
    if not desc:
        raise ErrorFila("ERROR: código", f"Code '{codigo}': Description quedó vacía (código inexistente o datos previos mal)")
    final = (dialogo(driver).find_element(
        By.CSS_SELECTOR, "#optionCode > tp-validator > input").get_attribute("value") or "").strip()
    if final.upper() != codigo.upper():
        raise ErrorFila("ERROR: campo distinto", f"Code: pedí '{codigo}' y quedó '{final}'")
    return desc


def escribir_fecha(driver, campo_id, dt):
    """Escribe DD/MM/YY y verifica el valor NORMALIZADO que guarda el input
    oculto (DD/Mon/YYYY), comparado como fecha. Con una fecha que el campo no
    entiende, vuelve silenciosamente a la fecha de hoy: por eso se verifica."""
    dlg = dialog_subs(driver)
    visible = dlg.find_element(By.CSS_SELECTOR, f"#{campo_id} > div > input:nth-of-type(2)")
    set_val_con_blur(driver, visible, L.fmt_ddmmyy(dt))
    esperar(lambda: valor_fecha(driver, campo_id) == dt, 3)      # espera activa a que el campo normalice la fecha
    oculto = dialog_subs(driver).find_element(By.CSS_SELECTOR, f"#{campo_id} > div > input:nth-of-type(1)")
    leido = L.parsear_fecha(oculto.get_attribute("value"))
    if leido != dt:
        raise ErrorFila("ERROR: fecha", f"{campo_id}: escribí {L.fmt_ddmmyy(dt)} ({L.fmt_tp(dt)}) y el campo quedó "
                        f"'{oculto.get_attribute('value')}'")
    return leido


JS_ESTADOS = """
    var grupo = arguments[1].toLowerCase();
    var lg = Array.from(arguments[0].querySelectorAll('div.legend')).find(function(l){
        return (l.innerText || '').trim().toLowerCase() === grupo; });
    if (!lg) return null;
    return Array.from(lg.parentElement.querySelectorAll('li.list-row')).map(function(li){
        var lab = li.querySelector('label.tplabel-list');
        var inp = li.querySelector('input[type=checkbox]');
        return {texto: (lab ? lab.innerText : '').trim(), tilda: inp ? inp.checked : null};
    });
"""

JS_CLICK_ESTADO = """
    var grupo = arguments[1].toLowerCase(), texto = arguments[2].toLowerCase();
    var lg = Array.from(arguments[0].querySelectorAll('div.legend')).find(function(l){
        return (l.innerText || '').trim().toLowerCase() === grupo; });
    if (!lg) return false;
    var li = Array.from(lg.parentElement.querySelectorAll('li.list-row')).find(function(x){
        var lab = x.querySelector('label.tplabel-list');
        return lab && (lab.innerText || '').trim().toLowerCase() === texto; });
    if (!li) return false;
    var el = li.querySelector('i') || li.querySelector('input');
    el.click();
    return true;
"""


JS_CLICK_ESTADOS_IDX = """
    var grupo = arguments[1].toLowerCase(), idxs = arguments[2];
    var lg = Array.from(arguments[0].querySelectorAll('div.legend')).find(function(l){
        return (l.innerText || '').trim().toLowerCase() === grupo; });
    if (!lg) return false;
    var lis = Array.from(lg.parentElement.querySelectorAll('li.list-row'));
    idxs.forEach(function(i){ var el = lis[i].querySelector('i') || lis[i].querySelector('input'); el.click(); });
    return true;
"""


def fijar_service_status(driver, nombre=None, nth=0):
    """Filtro Service Status. Sin `nombre`: todos los ítems tildados. Con `nombre`: solo ese ítem tildado (`nth`:
    cuál, entre los ítems de igual texto). Se tilda/destilda ítem por ítem (no se usa 'All'), se relee y se
    verifica; 'All' lo maneja Tourplan y no se toca."""
    for _ in range(3):
        items = driver.execute_script(JS_ESTADOS, dialog_subs(driver), "service status")
        if not items:
            raise ErrorFila("ERROR: filtro de estado", "No encontré el grupo Service Status")
        deseado, vistos = [], 0
        for e in items:
            if e["texto"].lower() == "all":
                deseado.append(None)
            elif nombre is None:
                deseado.append(True)
            elif e["texto"].lower() == nombre.lower():
                deseado.append(vistos == nth)
                vistos += 1
            else:
                deseado.append(False)
        if nombre is not None and nth >= vistos:
            raise ErrorFila("ERROR: filtro de estado", f"Service Status no tiene el ítem '{nombre}' (n.º {nth + 1}); "
                            f"hay: {[e['texto'] for e in items]}")
        cambiar = [i for i, (e, d) in enumerate(zip(items, deseado)) if d is not None and e["tilda"] != d]
        if not cambiar:
            return
        driver.execute_script(JS_CLICK_ESTADOS_IDX, dialog_subs(driver), "service status", cambiar)
        time.sleep(0.3)
    raise ErrorFila("ERROR: filtro de estado", f"No pude dejar el filtro Service Status en "
                    f"{'todos' if nombre is None else repr(nombre)} (quedó distinto tras 3 intentos)")


def destildar_booking_status(driver, textos=BOOKING_STATUS_EXCLUIR):
    """Destilda SOLO el ítem cancelado en el grupo Booking Status (el primero de `textos` que exista; se ubica por
    el texto del label, no por índice) y verifica el resultado: ese ítem destildado y todos los demás (salvo 'All',
    que Tourplan puede destildar solo) tildados. Devuelve (texto_destildado, ítems_tildados)."""
    dlg = dialog_subs(driver)
    estados = driver.execute_script(JS_ESTADOS, dlg, "booking status")
    if not estados:
        raise ErrorFila("ERROR: filtro de estado", "No encontré el grupo Booking Status")
    existentes = {e["texto"].lower() for e in estados}
    texto = next((t for t in ((textos,) if isinstance(textos, str) else textos) if t.lower() in existentes), None)
    if texto is None:
        raise ErrorFila("ERROR: filtro de estado", f"Booking Status no tiene ninguno de los ítems {list(textos)} "
                        f"(hay: {[e['texto'] for e in estados]})")
    item = next(e for e in estados if e["texto"].lower() == texto.lower())
    if item["tilda"]:
        if not driver.execute_script(JS_CLICK_ESTADO, dlg, "booking status", texto):
            raise ErrorFila("ERROR: filtro de estado", f"No pude clickear '{texto}'")
        pausa(1)
    estados = driver.execute_script(JS_ESTADOS, dialog_subs(driver), "booking status")
    for e in estados:
        es_objetivo = e["texto"].lower() == texto.lower()
        if es_objetivo and e["tilda"]:
            raise ErrorFila("ERROR: filtro de estado", f"'{texto}' sigue tildado tras el click")
        if not es_objetivo and e["texto"].lower() != "all" and not e["tilda"]:
            raise ErrorFila("ERROR: filtro de estado", f"Quedó destildado '{e['texto']}' además de '{texto}'")
    return texto, [e["texto"] for e in estados if e["tilda"] and e["texto"].lower() != "all"]


# ── Results ──────────────────────────────────────────────────────────────────

JS_TOKEN_RESULTADOS = """
    var tb = document.querySelector('#resultsTab tp-grid table');
    var filas = tb ? Array.from(tb.querySelectorAll('tbody > tr')).filter(function(r){
        return r.querySelector(':scope > td.tpcol-optioncode'); }) : [];
    var f = document.querySelector('#resultsTab tp-label[tplabeltype="Found"]');
    var s = document.querySelector('#resultsTab tp-label[tplabeltype="Selected"]');
    var t = function(e){ var tr = e && e.closest('tr'); return tr ? (tr.innerText || '').replace(/\\s+/g, ' ').trim() : ''; };
    return [t(f), t(s), filas.length, filas.slice(0, 3).map(function(r){ return (r.innerText || '').replace(/\\s+/g, ' ').trim(); })].join('|');
"""


def token_resultados(driver):
    """Huella del listado que se ve ahora (Found, Selected y las primeras filas): sirve para saber si Results ya se recargó."""
    try:
        return driver.execute_script(JS_TOKEN_RESULTADOS)
    except Exception:
        return None


def buscar(driver):
    """Filter -> Results (la solapa Analysis de las grabaciones no lleva nada que completar). Devuelve la huella del
    listado anterior, tomada ANTES del click, para que esperar_resultados sepa cuándo cambió."""
    token = token_resultados(driver)
    jc(driver, driver.find_element(By.CSS_SELECTOR, "#tptablabel-resultsTab"))
    time.sleep(0.3)
    esperar_fin_carga(driver, timeout=30)
    return token


JS_CONTADORES = """
    function val(tipo) {
        var l = document.querySelector('#resultsTab tp-label[tplabeltype="' + tipo + '"]');
        if (!l) return null;
        var tr = l.closest('tr');
        var v = tr && tr.querySelector('td.align-right label');
        return v ? (v.innerText || '').trim() : null;
    }
    return [val('Found'), val('Selected')];
"""


def leer_contadores(driver):
    f, s = driver.execute_script(JS_CONTADORES)
    num = lambda x: int(x.replace(".", "").replace(",", "")) if x and x.replace(".", "").replace(",", "").isdigit() else None
    return num(f), num(s)


def esperar_resultados(driver, timeout=60, token_previo=None):
    """Found estable en dos lecturas seguidas, sin diálogo de carga, y con el listado ya recargado: la huella cambió
    respecto de `token_previo` (el listado de la búsqueda anterior). Si no cambió no hay forma de distinguir 'aún no
    recargó' de 'dio lo mismo': en ese caso se espera un mínimo fijo antes de aceptarlo (nunca se acepta un Found viejo)."""
    inicio = time.time()
    minimo = 4.0 * tp.VELOCIDAD
    fin = inicio + timeout * tp.VELOCIDAD
    previo = None
    time.sleep(0.4)
    while time.time() < fin:
        esperar_fin_carga(driver, 5)
        found, selected = leer_contadores(driver)
        recargado = token_previo is None or token_resultados(driver) != token_previo or time.time() - inicio >= minimo
        if found is not None and found == previo and recargado:
            return found, selected
        previo = found
        time.sleep(0.4)
    raise ErrorFila("ERROR: Results", "El contador Found no se estabilizó (o no aparece)")


JS_FILAS = """
    var tb = document.querySelector('#resultsTab tp-grid table');
    if (!tb) return [];
    var cols = arguments[0];
    var rows = Array.from(tb.querySelectorAll('tbody > tr')).filter(function(tr){
        return tr.querySelector(':scope > td.tpcol-optioncode'); });
    return rows.map(function(tr){
        var o = {};
        cols.forEach(function(c){
            var td = tr.querySelector(':scope > td.tpcol-' + c[1]);
            o[c[0]] = td ? (td.innerText || '').trim() : '';
        });
        var pu = tr.querySelector(':scope > td.tpcol-pudodetails');
        o.pudo_raw = pu ? (pu.innerText || '').replace(/\\s+/g, ' ').trim() : '';
        // table.pudo-table: cada tr = <th>Pickup:</th><td>fecha hora</td><td>texto</td>
        o.pudo_filas = pu ? Array.from(pu.querySelectorAll('tr')).map(function(r){
            return Array.from(r.querySelectorAll('th, td')).map(function(t){ return (t.innerText || '').trim(); });
        }) : [];
        return o;
    });
"""

JS_CONTENEDOR_SCROLL = """
    var tb = document.querySelector('#resultsTab tp-grid table');
    var n = tb ? tb.parentElement : null;
    while (n && n !== document.body) {
        var cs = getComputedStyle(n);
        if (/(auto|scroll)/.test(cs.overflowY) && n.scrollHeight > n.clientHeight + 5) return n;
        n = n.parentElement;
    }
    return null;
"""


def filas_visibles(driver):
    return driver.execute_script(JS_FILAS, [list(c) for c in L.COLUMNAS_RESULTS])


def leer_resultados(driver, found):
    """Lee todas las filas por contenido. Si todo está en el DOM, una sola
    lectura; si no (virtual scroll), avanza de a 150 px y fusiona por
    solapamiento. El llamador compara el total contra Found."""
    cont = driver.execute_script(JS_CONTENEDOR_SCROLL)
    if cont is not None:
        # Siempre desde el inicio: una lectura previa pudo dejar el scroll abajo.
        driver.execute_script("arguments[0].scrollTop = 0;", cont)
        time.sleep(0.3 * tp.VELOCIDAD)
    acc = L.fusionar_ventana([], filas_visibles(driver))
    if len(acc) >= found:
        return acc, "completa"
    if cont is None:
        return acc, "sin_contenedor_de_scroll"
    estancado, iteraciones = 0, 0
    while len(acc) < found and iteraciones < found * 2 + 50:
        iteraciones += 1
        antes = driver.execute_script("return arguments[0].scrollTop;", cont)
        driver.execute_script("arguments[0].scrollTop += 150;", cont)
        time.sleep(0.25 * tp.VELOCIDAD)
        despues = driver.execute_script("return arguments[0].scrollTop;", cont)
        acc = L.fusionar_ventana(acc, filas_visibles(driver))
        estancado = estancado + 1 if despues == antes else 0
        if estancado >= 2:
            break
    return acc, "scroll"


def normalizar_fila(f):
    """Separa la celda PU/DO del listado: Pickup, Dropoff y Remarks de la línea (confirmado en una corrida
    real: 'Pickup: 01/Nov/2026 09:00 hotel prueba Dropoff: 01/Nov/2026 00:00 Remarks: texto prueba')."""
    pu = do = ("", "")
    remarks = ""
    for r in f.pop("pudo_filas", []):
        etiqueta = (r[0] if r else "").lower()
        if etiqueta.startswith("pickup"):
            pu = L.texto_pudo(r[1:])
        elif etiqueta.startswith("dropoff") or etiqueta.startswith("drop-off"):
            do = L.texto_pudo(r[1:])
        elif etiqueta.startswith("remarks"):
            remarks = next((x.strip() for x in r[1:] if x and x.strip()), "")
    f["pickup_fecha_hora"], f["pickup_texto"] = pu
    f["dropoff_fecha_hora"], f["dropoff_texto"] = do
    f["remarks"] = remarks
    return f


def sondear_panel(driver):
    """Selecciona la PRIMERA fila de Results, lee el panel derecho y la
    deselecciona (verificando el contador Selected). Nunca toca OK. Solo se
    hace una vez por corrida, para saber qué muestra el panel."""
    res = {"hecho_en": ahora()}
    objetivo = driver.execute_script("""
        var tb = document.querySelector('#resultsTab tp-grid table');
        var tr = Array.from(tb.querySelectorAll('tbody > tr')).find(function(r){
            return r.querySelector(':scope > td.tpcol-optioncode'); });
        if (!tr) return null;
        var td = tr.querySelector(':scope > td.tpcol-checkbox');
        return td ? (td.querySelector('label') || td.querySelector('i') ||
                     td.querySelector('input') || td) : null;
    """)
    if objetivo is None:
        res["error"] = "no encontré la celda checkbox de la primera fila"
        return res
    jc(driver, objetivo)
    pausa(1.5)
    res["selected_tras_seleccionar"] = leer_contadores(driver)[1]
    panel = driver.execute_script("""
        var p = document.querySelector('#resultsTab .serviceline-details');
        if (!p) return null;
        return {html: p.outerHTML.slice(0, 80000),
                controles: Array.from(p.querySelectorAll('input, textarea, select')).map(function(e){
                    var host = e.closest('[id]');
                    return {id: e.id || null, host_id: host ? host.id : null, clase: e.className,
                            tipo: e.type, valor: e.type === 'checkbox' ? e.checked : e.value,
                            disabled: e.disabled, readonly: e.readOnly}; })};
    """)
    if panel:
        dump_html("panel_derecho_con_fila_seleccionada", panel.pop("html"))
    res["panel"] = panel
    jc(driver, objetivo)
    pausa(1.5)
    res["selected_tras_deseleccionar"] = leer_contadores(driver)[1]
    res["deseleccion_ok"] = res["selected_tras_deseleccionar"] == 0
    return res


JS_ENCABEZADO = r"""
    var th = document.querySelector('#resultsTab tp-grid table thead th.tpcol-checkbox');
    if (!th) return [];
    return [th.querySelector('label'), th.querySelector('i'), th.querySelector('input')]
        .filter(function(e){ return e; });
"""


def seleccionar_todo(driver, found):
    """Selecciona TODO con el checkbox del encabezado de Results y verifica Selected == Found.
    Prueba el <label>, el <i> y el <input> del encabezado, en ese orden, hasta que uno seleccione
    (la grabación clickeó el <i> y el <label>). Devuelve (nombre, elemento) del que funcionó."""
    candidatos = driver.execute_script(JS_ENCABEZADO)
    if not candidatos:
        raise ErrorFila("ERROR: selección", "No encontré el checkbox del encabezado de Results")
    for nombre, el in zip(("label", "i", "input"), candidatos):
        jc(driver, el)
        esperar(lambda: leer_contadores(driver)[1] not in (0, None), 2.5)      # espera activa a que cambie Selected
        sel = leer_contadores(driver)[1]
        if sel == found:
            return nombre, el
        if sel not in (0, None):
            raise ErrorFila("ERROR: selección", f"Select all dejó Selected={sel} con Found={found} (click en {nombre})")
    raise ErrorFila("ERROR: selección", f"Ningún click en el encabezado seleccionó las {found} fila(s) (Selected=0)")


def deseleccionar_todo(driver, usado):
    jc(driver, usado[1])
    pausa(1.5)
    sel = leer_contadores(driver)[1]
    if sel != 0:
        raise ErrorFila("ERROR: selección", f"No pude deseleccionar: Selected={sel} tras el segundo click ({usado[0]})")


def probar_seleccion_total(driver, found):
    """Modo lectura: selecciona todo, verifica y deselecciona. Nunca toca OK."""
    usado = seleccionar_todo(driver, found)
    deseleccionar_todo(driver, usado)
    return usado[0]


# ── Fila del input ───────────────────────────────────────────────────────────

def campos_fila(row):
    loc, sup, stype, cod = (str(row.get(c) or "").strip().upper()
                            for c in ("LOCATION", "SUPPLIER", "SERVICE TYPE", "CODIGO"))
    if not (loc and sup and stype and cod):
        raise ErrorFila("ERROR: faltan datos", "Faltan LOCATION / SUPPLIER / SERVICE TYPE / CODIGO")
    return loc, sup, stype, cod


def consultar(driver, ctx, loc, stype, sup, cod, desde_dt, hasta_dt, leer=True, marca=None, service_status=None):
    """Abre Substitute Service desde cero, aplica el filtro (con todas sus verificaciones), busca y,
    si leer=True, lee Results por contenido. Deja la pantalla en la pestaña Results.
    `service_status`: None (todos los estados) o (texto del filtro, n.º de ítem de ese texto).
    Devuelve {found, filas, modo, desc}."""
    marca = marca or (lambda nombre: None)
    preparar_pantalla(driver, ctx)
    marca("abrir")
    # Solo se completa lo que difiere de lo que la pantalla ya tiene (se lee el valor real de cada campo, no se
    # supone). Location -> Service -> Supplier -> Code dependen uno del otro: si uno cambia, se recargan los siguientes.
    cambia, mantenidos = False, []

    def lista(n, nombre, valor):
        nonlocal cambia
        actual = valor_lista(driver, n)
        if not cambia and coincide_codigo(actual, valor):
            mantenidos.append(nombre)
            return actual
        cambia = True
        return completar_lista(driver, n, nombre, valor)

    ubic = lista(1, "Location", loc)
    srv = lista(2, "Service", stype)
    prv = lista(3, "Supplier", sup)
    if not cambia and coincide_codigo(valor_code(driver), cod) and descripcion_code(driver):
        desc = descripcion_code(driver)
        mantenidos.append("Code")
    else:
        desc = completar_code(driver, cod)
    fechas = []
    for campo, dt in (("serviceDateFrom", desde_dt), ("serviceDateTo", hasta_dt)):
        if valor_fecha(driver, campo) == dt:
            fechas.append(dt)
            mantenidos.append(campo.replace("serviceDate", "fecha "))
        else:
            fechas.append(escribir_fecha(driver, campo, dt))
    desde, hasta = fechas
    excluido, tildados = destildar_booking_status(driver)
    fijar_service_status(driver, *(service_status or (None, 0)))
    marca("filtro")
    print(f"    Filtro OK: {ubic} | {srv} | {prv} | {cod} ({desc}) | {L.fmt_tp(desde)}–{L.fmt_tp(hasta)} "
          f"| Booking Status sin '{excluido}' ({len(tildados)} tildados) "
          f"| Service Status: {'todos' if not service_status else 'solo ' + service_status[0]}"
          + (f" | sin recargar: {', '.join(mantenidos)}" if mantenidos else ""))

    token = buscar(driver)
    found, selected = esperar_resultados(driver, token_previo=token)
    marca("buscar")
    print(f"    Found={found} Selected={selected}")
    if selected not in (0, None):
        raise ErrorFila("ERROR: Results", f"Selected={selected} antes de seleccionar nada: alguien tocó la ventana de "
                        "Chrome mientras corría el script (no hay que clickear nada a mano)")

    filas, modo = [], ("vacia" if not found else "sin_leer")
    if leer and found:
        filas, modo = leer_resultados(driver, found)
        found_fin = leer_contadores(driver)[0]
        if found_fin != found:
            raise ErrorFila("ERROR: Results", f"Found cambió durante la lectura ({found} -> {found_fin})")
        if len(filas) != found:
            dump(driver, f"lectura_incompleta_{cod}")
            raise ErrorFila("ERROR: lectura incompleta",
                            f"Found={found} pero se leyeron {len(filas)} filas (modo {modo})")
        fuera = L.filas_fuera_del_filtro(filas, loc, stype, sup, cod)
        if fuera:
            raise ErrorFila("ERROR: Results", f"Results trae filas que no son {loc}/{stype}/{sup}/{cod}: "
                            f"{[(f['location'], f['service'], f['supplier'], f['code']) for f in fuera]}")
        filas = [normalizar_fila(dict(f)) for f in filas]
    marca("leer")
    return {"found": found, "filas": filas, "modo": modo, "desc": desc}


def armar_detalle(ctx, loc, sup, stype, cod, filas, fase, resultado, paneles=None):
    """Una fila de SUBSTITUTE_DETALLE por reserva. Con `paneles` (mismo orden que `filas`) se completan
    los textos de pickup/drop-off y REMARKS desde el panel derecho."""
    out = []
    for i, f in enumerate(filas):
        p = paneles[i] if paneles else None
        out.append({
            "TIMESTAMP": ahora(), "CORRIDA": ctx["corrida"], "LOCATION": loc, "SUPPLIER": sup,
            "SERVICE TYPE": stype, "CODIGO": cod, "DESCRIPTION": f["description"],
            "BOOKING NAME": f["booking_name"], "BOOKING REF": f["booking_ref"],
            "TRAVEL DATE": f["travel_date"], "DATE": f["date"], "STATUS_ORIGINAL": f["status"],
            "PUDO_ORIGINAL": f["pudo_raw"],
            "PICKUP_TEXTO": p["pickup_texto"] if p else f["pickup_texto"],
            "DROPOFF_TEXTO": p["dropoff_texto"] if p else f["dropoff_texto"],
            "REMARKS": p["remarks"] if p else f.get("remarks", ""), "FASE": fase, "RESULTADO": resultado, "OBSERVACIONES": "",
        })
    return out


def procesar_fila_lectura(driver, ctx, row):
    """Modo lectura de UNA combinación. Devuelve (observaciones, filas_detalle,
    found, tiempos_por_paso). Lanza ErrorFila si algo no cierra; en ese caso
    no se escribe detalle."""
    marcas, t0 = {}, time.time()

    def marca(nombre):
        nonlocal t0
        marcas[nombre] = round(time.time() - t0, 1)
        t0 = time.time()

    loc, sup, stype, cod = campos_fila(row)
    psup = L.provisorio_para(loc, stype, PROVISORIOS)
    texto_prov = (f"{loc}/{psup}/{stype}/{CODIGO_PROVISORIO}" if psup
                  else f"SIN PROVISORIO (no hay provisorio definido para {loc}/{stype} en el código)")

    r = consultar(driver, ctx, loc, stype, sup, cod, ctx["desde"], ctx["hasta"], marca=marca)
    found, filas, modo_lectura = r["found"], r["filas"], r["modo"]

    if ctx["panel_pendiente"] and found > 0:
        ctx["panel_pendiente"] = False
        try:
            ctx["panel"] = sondear_panel(driver)
            print(f"    Panel derecho: seleccionado={ctx['panel'].get('selected_tras_seleccionar')} "
                  f"deseleccion_ok={ctx['panel'].get('deseleccion_ok')}")
        except Exception as e:
            ctx["panel"] = {"error": str(e)}
            print(f"    ⚠ No se pudo sondear el panel: {e}")
        with open(os.path.join(tp.SS_DIR, "panel_probe.json"), "w", encoding="utf-8") as f:
            json.dump(ctx["panel"], f, ensure_ascii=False, indent=2, default=str)
        marca("panel")

    sel_total = ""
    if PROBAR_SELECCION and found > 0:
        via = probar_seleccion_total(driver, found)
        sel_total = f"seleccion_total=OK({found} via {via}); "
        print(f"    Select all verificado: Selected=Found={found} (click en {via}) y deselección OK")
        marca("seleccion")

    filtro_estado = ""
    if PROBAR_FILTRO_ESTADO and found > 0:
        pruebas = {}
        for st in sorted({f["status"] for f in filas}):
            filas_g = [f for f in filas if f["status"] == st]
            if st not in SERVICE_STATUS:
                pruebas[st or "(vacío)"] = "SIN GLOSARIO"
                continue
            try:
                foto_del_grupo(driver, ctx, loc, stype, sup, cod, ctx["desde"], ctx["hasta"], st, filas_g)
                pruebas[st] = f"OK({len(filas_g)})"
            except ErrorFila as e:
                pruebas[st] = f"FALLA({e.detalle[:150]})"
        filtro_estado = f"filtro_estado={pruebas}; "
        print(f"    Filtro Service Status probado por estado: {pruebas}")
        marca("filtro_estado")

    dup = L.analizar_duplicados(filas)
    estados = L.conteo_estados(filas)
    obs = (f"[LECTURA {ahora()}] Found={found}; estados={estados}; "
           f"claves repetidas={dup['claves']} ({dup['filas']} filas; {dup['difieren_estado']} con estado distinto, "
           f"{dup['difieren_pudo']} con PU/DO distinto); provisorio={texto_prov}; "
           f"{sel_total}{filtro_estado}lectura={modo_lectura}; t={marcas}")
    return obs, armar_detalle(ctx, loc, sup, stype, cod, filas, "LECTURA", "LEIDA"), found, marcas


# ── Modo aplicar: modal Replacement Service y Summary ────────────────────────

def _ultimo_dialogo(driver, componente):
    return driver.execute_script("""
        var comp = arguments[0];
        var ds = Array.from(document.querySelectorAll('tp-dialog')).filter(function(d){
            return d.querySelector(comp); });
        return ds.length ? ds[ds.length - 1] : null;
    """, componente)


def dialog_repl(driver):
    return _ultimo_dialogo(driver, "tp-replacement-service")


def dialog_summary(driver):
    return _ultimo_dialogo(driver, "tp-process-summary")


def _visible(driver, dlg):
    return bool(dlg is not None and driver.execute_script(
        "var d = arguments[0].querySelector('dialog'); return !!(d && d.open);", dlg))


def confirmar(texto):
    """Pausa humana antes de un OK. Con TOURPLAN_CONFIRMAR_OK=0 no pregunta."""
    if not CONFIRMAR_OK:
        return True
    if CONFIRM_DIR:
        print(f"\n    ⏸  ESPERANDO TU CONFIRMACIÓN EN LA APP: {texto}")
        return E.pedir_confirmacion(CONFIRM_DIR, texto, cancelar=abort_solicitado)
    if not sys.stdin or not sys.stdin.isatty():
        raise ErrorFila("ERROR: confirmación", "TOURPLAN_CONFIRMAR_OK=1 necesita una consola interactiva para escribir SI")
    print(f"\n    ⏸  {texto}")
    return input("    Escribí SI para continuar (cualquier otra cosa cancela): ").strip().upper() == "SI"


def abrir_modal_reemplazo(driver, found):
    """Con las filas ya seleccionadas (Selected == Found), OK del diálogo principal -> abre el modal."""
    sel = leer_contadores(driver)[1]
    if sel != found:
        raise ErrorFila("ERROR: selección", f"Antes del OK: Selected={sel} y se esperaban {found}")
    btn = dialog_subs(driver).find_element(By.CSS_SELECTOR, "tp-button.ok > button")
    if not btn.is_enabled():
        raise ErrorFila("ERROR: OK deshabilitado", "El OK del diálogo principal está deshabilitado con filas seleccionadas")
    jc(driver, btn)
    if not esperar(lambda: _visible(driver, dialog_repl(driver)), 15):
        raise ErrorFila("ERROR: modal", "No se abrió el modal Replacement Service")
    esperar(lambda: dialog_repl(driver).find_element(By.CSS_SELECTOR, "div.col1 li:nth-of-type(1) input").is_displayed(), 5)


JS_TD_TEXTO_EXACTO = """
    var v = arguments[1].toUpperCase();
    var rows = Array.from(arguments[0].querySelectorAll('tr')).filter(function(tr){
        return !!(tr.offsetWidth || tr.offsetHeight); });
    for (var tr of rows) {
        for (var td of tr.querySelectorAll('td')) {
            if ((td.innerText || '').trim().toUpperCase() === v) return td;
        }
    }
    return null;
"""

JS_RETAIN = """
    var raiz = arguments[0], out = {};
    var claves = ['retain sell price', 'retain voucher text'];
    Array.from(raiz.querySelectorAll('label')).forEach(function(l){
        var t = (l.innerText || '').trim().toLowerCase();
        if (claves.indexOf(t) < 0) return;
        var id = l.getAttribute('for'), inp = null;
        if (id) { inp = Array.from(raiz.querySelectorAll('input[type=checkbox]')).find(function(i){ return i.id === id; }); }
        if (!inp) { var c = l.closest('li, div'); inp = c ? c.querySelector('input[type=checkbox]') : null; }
        out[t] = inp ? inp.checked : null;
    });
    return out;
"""

JS_CHECKBOX_POR_TEXTO = """
    var raiz = arguments[0], rx = new RegExp(arguments[1], 'i');
    var l = Array.from(raiz.querySelectorAll('label')).find(function(x){ return rx.test((x.innerText || '').trim()); });
    if (!l) return [];
    var id = l.getAttribute('for'), inp = null;
    if (id) { inp = Array.from(raiz.querySelectorAll('input[type=checkbox]')).find(function(i){ return i.id === id; }); }
    if (!inp) { var c = l.closest('li, div'); inp = c ? c.querySelector('input[type=checkbox]') : null; }
    if (!inp) return [];
    var host = inp.closest('label');
    return [inp, l, host ? host.querySelector('i') : null].filter(function(e){ return e; });
"""


def completar_service_status(driver, codigo):
    """Service Status del modal: el código del listado (IT, OT, ...) se tipea y se elige la fila cuyo
    texto sea EXACTAMENTE ese código. Verifica que el campo no quede en '(Default)'."""
    dlg = dialog_repl(driver)
    inp = dlg.find_element(By.CSS_SELECTOR, "div.col1 li:nth-of-type(7) input")
    li = driver.execute_script("return arguments[0].closest('li');", inp)
    jc(driver, inp)
    time.sleep(0.15)
    set_val(driver, inp, "")
    time.sleep(0.15)
    set_val(driver, inp, codigo)
    td = esperar(lambda: driver.execute_script(JS_TD_TEXTO_EXACTO, li, codigo), 6)
    if td is None:
        dump_html("modal_service_status_lista", driver.execute_script("return arguments[0].outerHTML;", li))
        raise ErrorFila("ERROR: service status", f"En el modal, la lista de Service Status no tiene una fila con el texto exacto '{codigo}'")
    jc(driver, td)

    def valor_status():
        return (dialog_repl(driver).find_element(By.CSS_SELECTOR, "div.col1 li:nth-of-type(7) input")
                .get_attribute("value") or "").strip()
    esperar(lambda: valor_status() and "default" not in valor_status().lower(), 4)      # espera activa a que tome el valor
    final = valor_status()
    if not final or "default" in final.lower():
        raise ErrorFila("ERROR: service status", f"Service Status del modal quedó en '{final}' tras elegir '{codigo}'")
    return final


def verificar_retain(driver):
    """Retain Sell Price y Retain Voucher Text deben seguir TILDADOS (como vienen por defecto);
    se buscan por el texto del label. Si falta alguno o está destildado, no se hace OK."""
    estado = driver.execute_script(JS_RETAIN, dialog_repl(driver))
    faltan = [k for k in ("retain sell price", "retain voucher text") if estado.get(k) is not True]
    if faltan:
        raise ErrorFila("ERROR: retain", f"En el modal no están tildados (o no se encontraron): {faltan}. Estado leído: {estado}")


def marcar_checkbox(driver, regex, nombre):
    cand = driver.execute_script(JS_CHECKBOX_POR_TEXTO, dialog_repl(driver), regex)
    if not cand:
        raise ErrorFila("ERROR: pickup/dropoff", f"No encontré el checkbox '{nombre}' en el modal")
    inp = cand[0]
    if driver.execute_script("return arguments[0].checked;", inp):
        return
    for el in cand[1:] + [inp]:
        jc(driver, el)
        pausa(0.8)
        if driver.execute_script("return arguments[0].checked;", inp):
            return
    raise ErrorFila("ERROR: pickup/dropoff", f"No pude tildar '{nombre}' en el modal")


def escribir_en_host(driver, host, valor):
    """`host`: selector CSS del componente que contiene el input/textarea (ids y tptype del HTML real
    del modal: #pickupTime, tp-textarea[tptype=PickupRemarks], #remarks, ...)."""
    css = f"{host} input:not([type=hidden]), {host} textarea"
    els = dialog_repl(driver).find_elements(By.CSS_SELECTOR, css)
    if not els:
        raise ErrorFila("ERROR: pickup/dropoff", f"No encontré el campo {host} en el modal")
    set_val_con_blur(driver, els[0], valor)
    time.sleep(0.2)
    leido = (dialog_repl(driver).find_elements(By.CSS_SELECTOR, css)[0].get_attribute("value") or "").strip()
    if leido != valor:
        raise ErrorFila("ERROR: pickup/dropoff", f"{host}: escribí '{valor}' y quedó '{leido}'")


def completar_pudo_modal(driver, pudo):
    """Tilda 'Enter Pickup/Drop-off Details' y repone hora y texto de pickup/drop-off y los Remarks."""
    marcar_checkbox(driver, r"enter\s+pickup\s*/\s*drop-?off\s+details", "Enter Pickup/Drop-off Details")
    escribir_en_host(driver, "#pickupTime", pudo["pickup_hora"])
    if pudo["pickup_texto"]:
        escribir_en_host(driver, "tp-textarea[tptype=PickupRemarks]", pudo["pickup_texto"])
    escribir_en_host(driver, "#dropoffTime", pudo["dropoff_hora"])
    if pudo["dropoff_texto"]:
        escribir_en_host(driver, "tp-textarea[tptype=DropoffRemarks]", pudo["dropoff_texto"])
    if pudo["remarks"]:
        escribir_en_host(driver, "#remarks", pudo["remarks"])


def llenar_modal(driver, loc, stype, sup, cod, status, pudo, etiqueta):
    """Completa el modal Replacement Service con verificaciones campo por campo, vuelca su HTML y una
    captura (antes y después de llenarlo). NO hace OK."""
    dump_html(f"modal_{etiqueta}_inicial", driver.execute_script("return arguments[0].outerHTML;", dialog_repl(driver)))
    u = completar_lista(driver, 1, "Location (modal)", loc, dialog_repl)
    v = completar_lista(driver, 2, "Service (modal)", stype, dialog_repl)
    w = completar_lista(driver, 3, "Supplier (modal)", sup, dialog_repl)
    d = completar_code(driver, cod, dialog_repl)
    st = completar_service_status(driver, status)
    verificar_retain(driver)
    if pudo:
        completar_pudo_modal(driver, pudo)
    dump_html(f"modal_{etiqueta}_lleno", driver.execute_script("return arguments[0].outerHTML;", dialog_repl(driver)))
    ss(driver, f"modal_{etiqueta}_lleno", forzar=True)
    print(f"    Modal {etiqueta}: {u} | {v} | {w} | {cod} ({d}) | Service Status '{st}' | Retain Sell Price y "
          f"Voucher Text tildados" + (" | pickup/drop-off repuestos" if pudo else ""))


def ok_modal_y_summary(driver, etiqueta):
    """OK del modal -> Summary: vuelca su HTML y su texto (aún no sabemos qué muestra) y lo cierra con su OK."""
    btn = dialog_repl(driver).find_element(By.CSS_SELECTOR, "tp-button.ok > button")
    if not btn.is_enabled():
        raise ErrorFila("ERROR: OK deshabilitado", "El OK del modal Replacement Service está deshabilitado")
    jc(driver, btn)
    dlg = esperar(lambda: dialog_summary(driver), 30)
    if dlg is None:
        raise ErrorFila("ERROR: summary", "No apareció tp-process-summary tras el OK del modal")
    JS_TEXTO = "return (arguments[0].innerText || '').replace(/\\s+/g, ' ').trim();"
    previo = [None]

    def texto_estable():           # espera activa: el texto del Summary ya está y no cambia entre dos lecturas
        t = driver.execute_script(JS_TEXTO, dlg)
        ok = bool(t) and t == previo[0]
        previo[0] = t
        return ok
    esperar(texto_estable, 5)
    texto = driver.execute_script(JS_TEXTO, dlg)
    dump_html(f"summary_{etiqueta}", driver.execute_script("return arguments[0].outerHTML;", dlg))
    ss(driver, f"summary_{etiqueta}", forzar=True)
    print(f"    Summary {etiqueta}: {texto[:300]}")
    jc(driver, dlg.find_element(By.CSS_SELECTOR, "tp-button > button"))
    esperar(lambda: not _visible(driver, dlg), 6)      # espera activa a que se cierre el Summary
    esperar_fin_carga(driver, timeout=30)
    return texto[:600]


# ── Modo aplicar: una combinación, ida y vuelta ──────────────────────────────

def provisorio_de_fila(loc, stype):
    psup = L.provisorio_para(loc, stype, PROVISORIOS)
    if not psup:
        raise ErrorFila("ERROR: sin provisorio", f"No hay provisorio definido para {loc}/{stype} "
                        f"(tabla PROVISORIOS del código, {len(PROVISORIOS)} combinaciones Location + Service Type)")
    return psup, CODIGO_PROVISORIO


def verificar_provisorio_libre(driver, ctx, loc, stype, psup, pcod):
    """Validación previa del provisorio de una Location + Service Type: existe en Tourplan y no tiene ninguna
    reserva en todo el historial (01/01/2020 en adelante). Restos = una corrida anterior falló. Una vez comprobado,
    el par queda en ctx["provisorios_ok"] hasta que una combinación use ese provisorio (ahí se vuelve a pedir)."""
    clave = (loc, stype)
    if clave in ctx["provisorios_ok"]:
        return
    try:
        r = consultar(driver, ctx, loc, stype, psup, pcod, GUARDA_DESDE, ctx["hasta"], leer=False)
    except ErrorFila as e:
        if e.estado in ("ERROR: sin coincidencia", "ERROR: código"):
            raise ErrorFila("ERROR: provisorio inexistente",
                            f"El provisorio {loc}/{psup}/{stype}/{pcod} no se encontró en Tourplan: {e.detalle}")
        raise
    if r["found"] != 0:
        raise ErrorFila("ERROR: provisorio en uso", f"El provisorio {loc}/{psup}/{stype}/{pcod} tiene {r['found']} reserva(s) "
                        f"(desde {L.fmt_tp(GUARDA_DESDE)}): restos de una corrida anterior. No se continúa.")
    ctx["provisorios_ok"].add(clave)
    print(f"    Provisorio {loc}/{psup}/{stype}/{pcod} existe y está libre")


def _fase_rango(ctx, rango, fase):
    if rango and rango[0]:
        actualizar_columna_rango(ctx["ws_det"], ctx["cols_det"], "FASE", rango[0], rango[1], fase)


def pudo_de_fila(f):
    """Pickup/drop-off/Remarks de una fila de Results, en el formato de L.CAMPOS_PANEL (la celda PU/DO del
    listado trae lo mismo que el panel derecho, sin tener que seleccionar cada reserva)."""
    return {"pickup_fecha": L.fecha_de(f["pickup_fecha_hora"]), "pickup_hora": L.hora_de(f["pickup_fecha_hora"]),
            "pickup_texto": f["pickup_texto"], "dropoff_fecha": L.fecha_de(f["dropoff_fecha_hora"]),
            "dropoff_hora": L.hora_de(f["dropoff_fecha_hora"]), "dropoff_texto": f["dropoff_texto"],
            "remarks": f.get("remarks", "")}


def foto_del_grupo(driver, ctx, loc, stype, sup, cod, desde, hasta, status, filas_g):
    """Vuelve a consultar A con el filtro Service Status = solo `status` y comprueba que devuelve EXACTAMENTE las
    reservas de ese estado de la foto inicial. Si el texto del filtro está repetido (CC y LC se llaman igual) prueba
    cada ítem hasta que coincida. Devuelve (consulta, (texto_del_filtro, n.º de ítem))."""
    nombre = SERVICE_STATUS[status]
    intentos = 2 if list(SERVICE_STATUS.values()).count(nombre) > 1 else 1
    for nth in range(intentos):
        r = consultar(driver, ctx, loc, stype, sup, cod, desde, hasta, service_status=(nombre, nth))
        faltan, sobran = L.diferencia_multiconjunto(filas_g, r["filas"])
        if not faltan and not sobran:
            return r, (nombre, nth)
    raise ErrorFila("ERROR: filtro de estado", f"Con el filtro Service Status '{nombre}' Results no devuelve las "
                    f"{len(filas_g)} reserva(s) {status} de la foto inicial (faltan {faltan[:2]} | sobran {sobran[:2]})")


def procesar_grupo(driver, ctx, loc, sup, stype, cod, psup, pcod, status, filas_g, pudos, pudo0, reponer, filtro_ss,
                   desde, hasta, plan):
    """Ida y vuelta de UN grupo (todas las reservas de A con el mismo estado de servicio; con un solo estado, todas).
    `plan`: texto de la confirmación (None = no pedir). Devuelve un dict con lo ocurrido; después del OK de la ida
    cualquier fallo es ERROR_COLGADAS."""
    found0 = len(filas_g)
    # 2) IDA: A -> P (selecciona todo, modal, confirmación, detalle ANTES del OK, OK, summary)
    seleccionar_todo(driver, found0)
    abrir_modal_reemplazo(driver, found0)
    # El pickup/drop-off también se carga en la ida: así los selectores de esos campos se prueban ANTES de
    # mover nada (si fallan, no hay nada que recuperar) y el provisorio conserva los datos como respaldo.
    llenar_modal(driver, loc, stype, psup, pcod, status, pudo0 if reponer else None, "ida")
    if plan and not confirmar(plan):
        jc(driver, dialog_repl(driver).find_element(By.CSS_SELECTOR, "tp-button.cancel > button"))
        raise ErrorFila("ERROR: cancelado", "El usuario canceló antes del OK de la ida. No se movió nada.")
    n, ini, fin = agregar_filas_batch(ctx["ws_det"], ctx["cols_det"],
                                      armar_detalle(ctx, loc, sup, stype, cod, filas_g, "IDA_EN_CURSO", "", pudos),
                                      devolver_rango=True)
    rango = (ini, fin)
    obs = []
    try:
        obs.append(f"[{status}] summary ida: " + ok_modal_y_summary(driver, "ida"))
        _fase_rango(ctx, rango, "IDA")

        # 3) Verificar P: lo que se movió es un subconjunto exacto de lo que se leyó en A
        r1 = consultar(driver, ctx, loc, stype, psup, pcod, desde, hasta)
        found1 = r1["found"]
        if found1 > found0:
            raise ErrorFila("ERROR: provisorio", f"P tiene {found1} reservas y se movieron como máximo {found0}")
        _, sobran = L.diferencia_multiconjunto(filas_g, r1["filas"], L.CAMPOS_RESERVA)
        if sobran:
            raise ErrorFila("ERROR: provisorio", f"P tiene reservas que no estaban en A: {sobran[:3]}")
        no_movidas = L.diferencia_multiconjunto(filas_g, r1["filas"], L.CAMPOS_RESERVA)[0]
        print(f"    P tras la ida: Found={found1} de {found0} (no sustituidas por Tourplan: {len(no_movidas)})")
        if found1 == 0:
            _fase_rango(ctx, rango, "NO_SUSTITUIDA")
            return {"sin_sustituir": True, "obs": obs, "found": found0}

        # 4) VUELTA: P -> A (selecciona todo lo que hay en P, modal con el original, estado y pickup/drop-off).
        # No pide confirmación: ya está confirmada la combinación y frenar acá dejaría reservas en el provisorio.
        seleccionar_todo(driver, found1)
        abrir_modal_reemplazo(driver, found1)
        llenar_modal(driver, loc, stype, sup, cod, status, pudo0 if reponer else None, "vuelta")
        obs.append(f"[{status}] summary vuelta: " + ok_modal_y_summary(driver, "vuelta"))
        _fase_rango(ctx, rango, "VUELTA")

        # 5) Verificación final contra la foto inicial del grupo (las columnas incluyen la celda PU/DO completa)
        r2 = consultar(driver, ctx, loc, stype, sup, cod, desde, hasta, service_status=filtro_ss)
        faltan, sobran = L.diferencia_multiconjunto(filas_g, r2["filas"])
        if r2["found"] == found0 and not faltan and not sobran:
            # Todas las reservas de la foto están de vuelta en A y P estaba vacío antes de la ida: P quedó vacío.
            print("    P no se vuelve a consultar: el original quedó idéntico a la foto, así que P está vacío")
        else:
            r3 = consultar(driver, ctx, loc, stype, psup, pcod, desde, hasta, leer=False)
            if r3["found"] != 0:     # sustitutos sin asignar: es el caso que detiene toda la corrida
                raise ErrorFila("ERROR: provisorio", f"El provisorio quedó con {r3['found']} reserva(s) tras la vuelta")
        diferencias = []
        if r2["found"] != found0:
            diferencias.append(f"[{status}] el original tiene Found={r2['found']} y antes {found0}")
        if faltan or sobran:
            diferencias.append(f"[{status}] columnas distintas: antes-y-no-ahora {faltan[:2]} | ahora-y-no-antes {sobran[:2]}")
    except ErrorFila as e:
        raise ErrorFila("ERROR_COLGADAS", f"Falló después del OK de la ida ({e.estado}: {e.detalle}). Las reservas pueden haber "
                        f"quedado en el provisorio {loc}/{psup}/{stype}/{pcod}; ver SUBSTITUTE_DETALLE (fase {{IDA_EN_CURSO, IDA, VUELTA}}).")
    except Exception:
        raise ErrorFila("ERROR_COLGADAS", "Excepción después del OK de la ida; las reservas pueden estar en el provisorio "
                        f"{loc}/{psup}/{stype}/{pcod}: {traceback.format_exc(limit=3)}")

    # 6) Cierre: FASE final por reserva (las que Tourplan no sustituyó siguen en A: NO_SUSTITUIDA)
    base = "VERIFICADO" if not diferencias else "REVISION_MANUAL"
    sin_asignar = list(no_movidas)
    for i, f in enumerate(filas_g):
        firma = tuple(f[c] for c in L.CAMPOS_RESERVA)
        if firma in sin_asignar:
            sin_asignar.remove(firma)
            fase = "NO_SUSTITUIDA"
        else:
            fase = base
        if rango[0]:
            actualizar_columna_rango(ctx["ws_det"], ctx["cols_det"], "FASE", rango[0] + i, rango[0] + i, fase)
    return {"sin_sustituir": False, "obs": obs, "found": found0, "movidas": found1, "no_movidas": len(no_movidas),
            "diferencias": diferencias}


def procesar_fila_aplicar(driver, ctx, row):
    """Ida y vuelta COMPLETO de UNA combinación (original A -> provisorio P -> A). Devuelve
    (estado, observaciones, found). Cualquier fallo después del OK de la ida termina en ERROR_COLGADAS
    (las reservas pueden haber quedado en P; su detalle está en SUBSTITUTE_DETALLE).

    Sin tope de reservas. Si A tiene varios estados de servicio, se procesa un estado por vez usando el filtro
    Service Status (cada grupo: ida y vuelta completas). Pickup/drop-off/Remarks se leen de la celda PU/DO del
    listado: en los Service Types de PUDO_SERVICE_TYPES se reponen en la vuelta (deben ser iguales dentro de cada
    grupo); en los demás se asume que no corresponden y, si aparecen, la combinación se rechaza. Todos los
    rechazos ocurren ANTES de tocar nada."""
    loc, sup, stype, cod = campos_fila(row)
    psup, pcod = provisorio_de_fila(loc, stype)
    desde, hasta = ctx["desde"], ctx["hasta"]

    verificar_provisorio_libre(driver, ctx, loc, stype, psup, pcod)

    # 1) Foto inicial de A (todos los estados; las columnas del listado incluyen la celda PU/DO)
    r0 = consultar(driver, ctx, loc, stype, sup, cod, desde, hasta)
    found0, filas0 = r0["found"], r0["filas"]
    if found0 == 0:
        return "OK", f"[APLICAR {ahora()}] Found=0: no hay reservas para sustituir en el rango", 0
    estados = sorted({f["status"] for f in filas0})
    desconocidos = [e for e in estados if e not in SERVICE_STATUS]
    if desconocidos:
        raise ErrorFila("ERROR: status desconocido", f"Estados de servicio sin entrada en el glosario SERVICE_STATUS: "
                        f"{[e or '(vacío)' for e in desconocidos]} (no se tocó nada)")
    analiza_pudo = stype in PUDO_SERVICE_TYPES
    grupos = []
    for st in estados:
        filas_g = [f for f in filas0 if f["status"] == st]
        pudos = [pudo_de_fila(f) for f in filas_g]
        if analiza_pudo:
            if not L.paneles_homogeneos(pudos):
                raise ErrorFila("ERROR: no soportado en esta fase", f"Las reservas en estado {st} tienen distintos datos de "
                                "pickup/drop-off/Remarks; con un solo modal no se pueden reponer (no se tocó nada)")
            pudo0 = pudos[0]
            reponer = L.necesita_pudo(pudo0)
        else:
            if any(L.necesita_pudo(p) for p in pudos):
                raise ErrorFila("ERROR: pickup/dropoff inesperado", f"Hay reservas en estado {st} con datos de pickup/drop-off/"
                                f"Remarks y el Service Type {stype} no está habilitado para analizarlos "
                                "(se habilita en la app; no se tocó nada)")
            pudo0, reponer = None, False
        if any(p["pickup_fecha"] and p["pickup_fecha"] != f["date"] for p, f in zip(pudos, filas_g)):
            print(f"    ⚠ [{st}] El pickup/drop-off original no cae en la fecha del servicio: el modal no permite fecha, "
                  "la verificación final lo va a marcar para revisión manual.")
        grupos.append({"status": st, "filas": filas_g, "pudos": pudos, "pudo0": pudo0, "reponer": reponer})
    cuenta = ", ".join(f"{g['status']} {len(g['filas'])}" for g in grupos)
    print(f"    Foto inicial: {found0} reserva(s) en {len(grupos)} estado(s) de servicio ({cuenta}); "
          f"pickup/drop-off: {'analizado' if analiza_pudo else 'no corresponde en ' + stype}"
          + (f"; a reponer en: {[g['status'] for g in grupos if g['reponer']]}" if analiza_pudo else ""))

    plan = (f"{loc}/{sup}/{stype}/{cod}: {found0} reserva(s) en {len(grupos)} estado(s) de servicio ({cuenta}). Por cada estado: "
            f"IDA: mover al provisorio {loc}/{psup}/{stype}/{pcod}; después la VUELTA (devolverlas al original) se hace sola "
            f"y se verifica contra la foto inicial. Se hace click en el OK de la ida del primer estado y el resto sigue solo.")
    ctx["provisorios_ok"].discard((loc, stype))      # este provisorio se va a usar: antes de la próxima combinación que lo use se revisa de nuevo
    hechos, resultados = [], []
    for i, g in enumerate(grupos):
        try:
            filtro_ss = None
            if len(grupos) > 1:
                _, filtro_ss = foto_del_grupo(driver, ctx, loc, stype, sup, cod, desde, hasta, g["status"], g["filas"])
            res = procesar_grupo(driver, ctx, loc, sup, stype, cod, psup, pcod, g["status"], g["filas"], g["pudos"], g["pudo0"],
                                 g["reponer"], filtro_ss, desde, hasta, plan if i == 0 else None)
        except ErrorFila as e:
            if e.estado == "ERROR_COLGADAS" or not hechos:
                raise
            raise ErrorFila(e.estado, f"{e.detalle} (estados ya procesados sin problema: {hechos})")
        if res["sin_sustituir"]:
            return "ERROR_REVISAR", (f"[APLICAR {ahora()}] Tourplan no sustituyó ninguna de las {res['found']} reserva(s) en estado "
                                      f"{g['status']} (siguen en el original). Estados ya procesados: {hechos or 'ninguno'}. "
                                      f"{' | '.join(res['obs'])}"), found0
        hechos.append(g["status"])
        resultados.append(res)

    diferencias = [d for r in resultados for d in r["diferencias"]]
    movidas = sum(r["movidas"] for r in resultados)
    no_movidas = sum(r["no_movidas"] for r in resultados)
    obs = [o for r in resultados for o in r["obs"]]
    resumen = (f"[APLICAR {ahora()}] {found0} reserva(s) en A ({cuenta}); sustituidas {movidas}; no sustituidas {no_movidas}; "
               f"{' | '.join(obs)}")
    if diferencias:
        return "ERROR_REVISAR", resumen + " | DIFERENCIAS: " + "; ".join(diferencias), found0
    return "OK", resumen + " | verificado: el original quedó idéntico a la foto inicial y el provisorio vacío", found0


def prechequear_provisorios(driver, ctx, filas, ws, columnas, resumen):
    """Antes de tocar nada: para cada Location + Service Type del input comprueba que su provisorio existe en
    Tourplan y está libre. Las filas de un par sin provisorio utilizable terminan en ERROR (y no se procesan); si
    algún provisorio tiene reservas (sustitutos sin asignar) no se procesa NINGUNA combinación. Devuelve
    (filas que siguen, detenida)."""
    pares = {}
    for r in filas:
        try:
            loc, _, stype, _ = campos_fila(r)
        except ErrorFila:
            continue          # la fila se rechaza después, por datos faltantes
        if L.provisorio_para(loc, stype, PROVISORIOS):
            pares.setdefault((loc, stype), []).append(r)
    print(f"\nVerificación previa de provisorios: {len(pares)} combinación(es) Location + Service Type")
    descartadas, detenida = set(), False
    for (loc, stype), rows in pares.items():
        chequear_abort()
        try:
            verificar_provisorio_libre(driver, ctx, loc, stype, PROVISORIOS[(loc, stype)], CODIGO_PROVISORIO)
        except ErrorFila as e:
            ctx["abierta"] = False
            print(f"  ❌ {loc}/{stype}: {e.estado}: {e.detalle}")
            for r in rows:
                escribir_resultado_fila(ws, r["__row_idx__"], columnas, e.estado, e.detalle)
                resumen["error"] += 1
            descartadas.update(id(r) for r in rows)
            if L.detiene_corrida(e.estado):
                detenida = True
                break
    if not descartadas:
        print("  ✅ Todos los provisorios existen y están libres")
    return [r for r in filas if id(r) not in descartadas], detenida


# ── Sheets ───────────────────────────────────────────────────────────────────

def escribir_resultado_fila(ws, row_idx, columnas, estado=None, observaciones=None):
    valores = {COL_TS: ahora()}
    if estado is not None:
        valores[COL_ESTADO] = estado
    if observaciones is not None:
        valores[COL_OBS] = observaciones
    actualizar_fila_sheet(ws, row_idx, columnas, valores)


CANON_INPUT = COLS_INPUT + [COL_OBS, COL_TS]


def cargar_hojas():
    """Lee SUBSTITUTE (entrada) y abre SUBSTITUTE_DETALLE (salida). Los encabezados se aceptan con guion bajo
    o espacio y en cualquier combinación de mayúsculas/minúsculas ('SERVICE_TYPE' == 'SERVICE TYPE')."""
    ws = conectar_sheets(SHEET_URL, HOJA, CREDENTIALS_PATH, TOKEN_PATH)
    asegurar_columnas_tolerante(ws, [COL_OBS, COL_TS])
    filas, columnas = cargar_sheet(ws)
    columnas = L.canonizar_columnas(columnas, CANON_INPUT)
    filas = [L.canonizar_fila(f, CANON_INPUT) for f in filas]
    faltan = [c for c in COLS_INPUT if c not in columnas]
    if faltan:
        raise ValueError(f"Faltan columnas {faltan} en la hoja '{HOJA}'. Detectadas: {columnas}")
    print(f"Sheet '{HOJA}': {len(filas)} fila(s)")

    ws_det = cols_det = None
    if ESCRIBIR_DETALLE or MODO == "aplicar":
        ws_det = conectar_sheets(SHEET_URL, HOJA_DET, CREDENTIALS_PATH, TOKEN_PATH)
        asegurar_columnas_tolerante(ws_det, COLS_DET)
        cols_det = ws_det.row_values(1)
    return ws, filas, columnas, ws_det, cols_det


def exigir_sin_pendientes(ws_det):
    """Modo aplicar: no arranca si el detalle tiene reservas a mitad de camino (corrida anterior cortada). Que no haya
    sustitutos sin asignar en los provisorios lo comprueba prechequear_provisorios, en Tourplan, antes de procesar."""
    f_det, c_det = cargar_sheet(ws_det)
    f_det = [L.canonizar_fila(f, COLS_DET) for f in f_det]
    pend = L.detalle_pendiente(f_det)
    if pend:
        detalle = "\n".join(f"  {k[0]}/{k[1]}/{k[2]}/{k[3]} (corrida {k[4]}): {n} reserva(s)" for k, n in sorted(pend.items()))
        raise SystemExit("⛔ SUBSTITUTE_DETALLE tiene reservas a mitad de camino (FASE IDA_EN_CURSO / IDA / VUELTA): una corrida "
                         f"anterior no terminó. Revisar en Tourplan que no queden en el provisorio y corregir la FASE:\n{detalle}")


# ── MAIN ─────────────────────────────────────────────────────────────────────

class _ConHora:
    """Antepone [HH:MM:SS] a cada línea que se imprime (consola de la app y consola.log). Las líneas en blanco no
    llevan hora, y una línea que ya la trae no se vuelve a marcar (por si se instala dos veces)."""
    _CON_HORA = re.compile(r"\[\d\d:\d\d:\d\d\] ")

    def __init__(self, destino):
        self._destino = destino
        self._inicio_de_linea = True

    def write(self, texto):
        salida = []
        for parte in texto.splitlines(keepends=True):
            if self._inicio_de_linea and parte.strip() and not self._CON_HORA.match(parte):
                salida.append(time.strftime("[%H:%M:%S] "))
            salida.append(parte)
            self._inicio_de_linea = parte.endswith("\n")
        self._destino.write("".join(salida))
        return len(texto)

    def flush(self):
        self._destino.flush()

    def __getattr__(self, nombre):
        return getattr(self._destino, nombre)


def _instalar_hora():
    sys.stdout = _ConHora(sys.stdout)
    sys.stderr = _ConHora(sys.stderr)


def main():
    _instalar_hora()
    print("=" * 70)
    print(f"  📌 VERSION {VERSION} ({VERSION_FECHA}) · MODO={MODO} · VELOCIDAD={tp.VELOCIDAD}")
    print("  Doble sustitución — Substitute Service" + (" (modo lectura: nunca clickea OK)" if MODO == "lectura" else ""))
    print("=" * 70)
    if MODO not in ("lectura", "aplicar"):
        raise SystemExit(f"MODO='{MODO}' no válido: usar lectura o aplicar.")
    if MODO == "aplicar":
        print(f"⚠⚠⚠  MODO APLICAR: este script MODIFICA reservas en Tourplan ({tp.BASE_URL}).")
    if not SHEET_URL:
        raise ValueError("Falta TOURPLAN_SHEET_URL.")
    if not (tp.USERNAME and tp.PASSWORD):
        raise ValueError("Faltan TOURPLAN_USERNAME / TOURPLAN_PASSWORD.")

    desde, hasta = L.parsear_fecha(FECHA_DESDE), L.parsear_fecha(FECHA_HASTA)
    error_rango = L.validar_rango(desde, hasta)
    if error_rango:
        raise ValueError(f"{error_rango} (TOURPLAN_FECHA_DESDE='{FECHA_DESDE}', TOURPLAN_FECHA_HASTA='{FECHA_HASTA}')")
    if desde < datetime.now().replace(hour=0, minute=0, second=0, microsecond=0):
        print(f"⚠ La fecha Desde ({L.fmt_tp(desde)}) es anterior a hoy: se incluyen servicios pasados.")
    print(f"Rango de servicios: {L.fmt_tp(desde)} – {L.fmt_tp(hasta)}")

    t_inicio = time.time()
    carpeta, corrida = tp.preparar_carpeta_corrida(MODO)
    _instalar_hora()    # ahora que consola.log existe, sus líneas también llevan la hora
    print(f"Corrida {corrida} · salida (capturas, HTML, JSON y consola.log): {os.path.abspath(carpeta)}")
    ws, filas, columnas, ws_det, cols_det = cargar_hojas()
    if MODO == "aplicar":
        exigir_sin_pendientes(ws_det)

    pendientes = [r for r in filas if str(r.get(COL_ESTADO) or "").strip().upper() == "PENDIENTE"]
    unicas, repetidas = L.deduplicar_input(pendientes)
    for repetida, original in repetidas:
        escribir_resultado_fila(ws, repetida["__row_idx__"], columnas, "DUPLICADO",
                                f"Repetida: misma combinación que la fila {original['__row_idx__']}")
    if repetidas:
        print(f"Filas DUPLICADO (no se procesan): {len(repetidas)}")
    if FILA:
        unicas = [r for r in unicas if str(r["__row_idx__"]) == FILA]
        print(f"TOURPLAN_FILA={FILA}: se procesa solo esa fila ({len(unicas)} encontrada(s) PENDIENTE).")

    sin_prov = L.combinaciones_sin_provisorio(unicas, PROVISORIOS)
    print(f"\nProvisorios definidos en el código: {len(PROVISORIOS)} combinaciones Location + Service Type "
          f"(proveedor {SUPPLIER_PROVISORIO}, código {CODIGO_PROVISORIO})")
    if sin_prov:
        print(f"⚠ Sin provisorio definido para: {sin_prov} → esas filas terminarán en ERROR en modo aplicar.")
    print(f"Filas PENDIENTE a procesar: {len(unicas)}")
    if not unicas:
        print("\n⛔ Sin filas PENDIENTE para procesar.")
        return

    ctx = {"corrida": corrida, "desde": desde, "hasta": hasta, "abierta": False,
           "panel_pendiente": PROBAR_PANEL and MODO == "lectura", "panel": None,
           "ws_det": ws_det, "cols_det": cols_det, "provisorios_ok": set()}

    print("\n⚠ Mientras corre, NO toques la ventana de Chrome (cualquier click a mano puede alterar la lectura).")
    driver = tp.crear_driver()
    sesion_iniciada = abortado = detenida = False
    resumen = {"ok": 0, "error": 0, "reservas": 0}
    tiempos = []
    try:
        tp.login(driver)
        sesion_iniciada = True

        if MODO == "aplicar":
            unicas, detenida = prechequear_provisorios(driver, ctx, unicas, ws, columnas, resumen)
            if detenida:
                unicas = []       # hay sustitutos sin asignar: no se procesa ninguna combinación

        for row in unicas:
            chequear_abort()
            idx = row["__row_idx__"]
            print(f"\n{'─' * 70}\nFila {idx}: {row.get('LOCATION')}/{row.get('SUPPLIER')}/"
                  f"{row.get('SERVICE TYPE')}/{row.get('CODIGO')}")
            try:
                if MODO == "aplicar":
                    estado, obs, found = procesar_fila_aplicar(driver, ctx, row)
                    escribir_resultado_fila(ws, idx, columnas, estado, obs)
                    resumen["ok" if estado == "OK" else "error"] += 1
                    resumen["reservas"] += found
                    print(f"  {'✅' if estado == 'OK' else '⚠'} {estado}: {obs}")
                    if L.detiene_corrida(estado):
                        detenida = True
                        break
                else:
                    obs, detalle, found, marcas = procesar_fila_lectura(driver, ctx, row)
                    if ws_det is not None and detalle:
                        agregar_filas_batch(ws_det, cols_det, detalle)
                    escribir_resultado_fila(ws, idx, columnas, None, obs)   # sigue PENDIENTE
                    resumen["ok"] += 1
                    resumen["reservas"] += found
                    tiempos.append(marcas)
                    print(f"  ✅ Leída: {found} reserva(s)")
            except ErrorFila as e:
                ctx["abierta"] = False
                ss(driver, f"error_fila_{idx}", forzar=True)
                print(f"  ❌ {e.estado}: {e.detalle}")
                escribir_resultado_fila(ws, idx, columnas, e.estado, e.detalle)
                resumen["error"] += 1
                if MODO == "aplicar" and L.detiene_corrida(e.estado):
                    detenida = True
                    break
            except Exception:
                ctx["abierta"] = False
                ss(driver, f"excepcion_fila_{idx}", forzar=True)
                dump(driver, f"excepcion_fila_{idx}")
                detalle_exc = traceback.format_exc(limit=3)
                print(f"  ❌ ERROR: {detalle_exc}")
                escribir_resultado_fila(ws, idx, columnas, "ERROR", detalle_exc)
                resumen["error"] += 1
    except AbortadoPorUsuario:
        abortado = True
        print("\n⏸️  Corrida abortada por el usuario — las filas no procesadas quedan PENDIENTE.")
    finally:
        if sesion_iniciada:
            tp.logout(driver)
        driver.quit()
        dur = int(time.time() - t_inicio)
        m, s = divmod(dur, 60)
        print(f"\n🏁 Fin. Duración: {m}m {s:02d}s · filas OK={resumen['ok']} ERROR={resumen['error']} "
              f"· reservas leídas={resumen['reservas']}")
        if tiempos:
            claves = ["abrir", "filtro", "buscar", "leer"]
            prom = {k: round(sum(t.get(k, 0) for t in tiempos) / len(tiempos), 1) for k in claves}
            print(f"⏱️  Promedio por fila (s): {prom}")
        if detenida:
            print("\n🛑 CORRIDA DETENIDA: puede haber reservas en un provisorio (sustitutos sin asignar). No se procesa "
                  "ninguna otra combinación hasta resolverlo; las filas restantes quedan PENDIENTE. Ver SUBSTITUTE_DETALLE "
                  "(FASE IDA_EN_CURSO / IDA / VUELTA) y docs/fase2_riesgos.md.")
        print(f"📄 Sheet: {SHEET_URL}")
        print(f"📁 Salida de esta corrida: {os.path.abspath(tp.SS_DIR)}")

    if abortado:
        sys.exit(ABORT_EXIT_CODE)
    if detenida:
        sys.exit(E.EXIT_DETENIDA)


if __name__ == "__main__":
    main()
