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
    en producción. Por ahora soporta combinaciones con un único estado de servicio y hasta
    TOURPLAN_PANEL_MAX reservas con los mismos datos de pickup/drop-off/Remarks (ver
    procesar_fila_aplicar). Con TOURPLAN_CONFIRMAR_OK=1 (default) se detiene antes de cada OK
    y espera que se escriba SI en la consola.

Provisorios: viven en el código (SUPPLIER_PROVISORIO). Location y Service Type son los de la
fila de entrada, el código siempre es 000000 y el proveedor depende del Service Type. Si el
provisorio no existe en Tourplan la fila termina en ERROR sin tocar nada.

Reglas de negocio y de seguridad:
  - Una fila del input = una combinación; se procesa completa y secuencialmente.
  - Filas repetidas del input (misma combinación) -> DUPLICADO, sin procesar.
  - Filtro de estados: se destilda SOLO Booking Status "CX - Cancelled" (decisión
    de la usuaria; Service Status queda como viene, todo tildado). Se verifica
    que quedó destildado ese ítem y ningún otro.
  - Fechas: "Desde" obligatoria (la ingresa el usuario en cada corrida), "Hasta"
    por defecto 31/12/49 (= 2049). Se escriben con año de 2 dígitos (el campo
    rechaza DD/MM/YYYY) y se verifica el valor normalizado que guarda el input.
  - Código exacto: en Location/Service/Supplier nunca se acepta el ítem
    resaltado; se elige la fila de código idéntico y se lee el valor final.
  - Tope MAX_RESERVAS: si Found lo supera, la fila termina en ERROR sin leer.
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
# v0.2 (2026-10-19): modo aplicar (Fase 2); provisorios en el código (se elimina la hoja PROVISORIOS).
# v0.1 (2026-10-19): primera versión, solo modo lectura. SIN CORRER contra
#   Tourplan ni contra el Sheet; la lógica pura (common/logica.py) sí tiene
#   pruebas por simulación (tests/test_logica.py).

VERSION = "0.2"
VERSION_FECHA = "2026-10-19"

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
MAX_RESERVAS = int(os.environ.get("TOURPLAN_MAX_RESERVAS", "2000"))
FILA = os.environ.get("TOURPLAN_FILA", "").strip()                  # solo esa fila del Sheet
PROBAR_PANEL = os.environ.get("TOURPLAN_PROBAR_PANEL", "1").strip() in ("1", "true", "True")
PROBAR_SELECCION = os.environ.get("TOURPLAN_PROBAR_SELECCION", "1").strip() in ("1", "true", "True")
ESCRIBIR_DETALLE = os.environ.get("TOURPLAN_ESCRIBIR_DETALLE", "1").strip() in ("1", "true", "True")
CONFIRMAR_OK = os.environ.get("TOURPLAN_CONFIRMAR_OK", "1").strip() in ("1", "true", "True")
# Si la app define esta carpeta, la confirmación se hace por archivos (botones en la app); si no, escribiendo SI en la consola.
CONFIRM_DIR = os.environ.get("TOURPLAN_CONFIRM_DIR", "").strip()
PANEL_MAX = int(os.environ.get("TOURPLAN_PANEL_MAX", "5"))
CONFIRMO_SIN_PENDIENTES = os.environ.get("TOURPLAN_CONFIRMO_SIN_PENDIENTES", "").strip().upper()
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
BOOKING_STATUS_EXCLUIR = "CX - Cancelled"

# Provisorios (viven en el código): proveedor por Service Type; Location y Service Type son los de la fila
# y el código es siempre 000000. Un Service Type que no esté acá termina en ERROR sin tocar Tourplan.
CODIGO_PROVISORIO = L.CODIGO_PROVISORIO
SUPPLIER_PROVISORIO = {"GU": "GUIAPO"}
# La validación previa de que el provisorio está libre mira todo el historial, no solo el rango de la corrida.
GUARDA_DESDE = datetime(2020, 1, 1)


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
        time.sleep(0.3)
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
        try:
            jc(driver, driver.find_element(By.CSS_SELECTOR, "#tptablabel-filterTab"))
            pausa(1.5)
            dlg = dialog_subs(driver)
            if dlg is not None and dlg.find_elements(By.CSS_SELECTOR, "div.col1 li:nth-of-type(1) input"):
                return
        except Exception:
            pass
        ctx["abierta"] = False
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
    pausa(0.4)
    set_val(driver, inp, valor)
    td = esperar(lambda: driver.execute_script(JS_TD_EXACTO, li, valor), 8)
    if td is None:
        visibles = driver.execute_script(
            "return Array.from(arguments[0].querySelectorAll('tr td.code')).map(function(t){"
            "return (t.innerText||'').trim();}).slice(0, 10);", li)
        raise ErrorFila("ERROR: sin coincidencia",
                        f"{nombre} '{valor}': ninguna fila de la lista tiene ese código exacto (vi: {visibles})")
    jc(driver, td)
    pausa(1.2)
    final = (dialogo(driver).find_element(
        By.CSS_SELECTOR, f"div.col1 li:nth-of-type({n}) input").get_attribute("value") or "").strip()
    if final.upper() != valor.upper() and not final.upper().startswith(valor.upper() + " - "):
        raise ErrorFila("ERROR: campo distinto",
                        f"{nombre}: pedí '{valor}' y quedó '{final}'")
    return final


def completar_code(driver, codigo, dialogo=None):
    dialogo = dialogo or dialog_subs
    dlg = dialogo(driver)
    cod = dlg.find_element(By.CSS_SELECTOR, "#optionCode > tp-validator > input")
    set_val_con_blur(driver, cod, codigo)
    pausa(2)
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
    pausa(1.2)
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


def destildar_booking_status(driver, texto=BOOKING_STATUS_EXCLUIR):
    """Destilda SOLO `texto` en el grupo Booking Status (se ubica por el texto
    del label, no por índice) y verifica el resultado: ese ítem destildado y
    todos los demás (salvo 'All', que Tourplan puede destildar solo) tildados."""
    dlg = dialog_subs(driver)
    estados = driver.execute_script(JS_ESTADOS, dlg, "booking status")
    if not estados:
        raise ErrorFila("ERROR: filtro de estado", "No encontré el grupo Booking Status")
    item = next((e for e in estados if e["texto"].lower() == texto.lower()), None)
    if item is None:
        raise ErrorFila("ERROR: filtro de estado", f"Booking Status no tiene el ítem '{texto}' (hay: {[e['texto'] for e in estados]})")
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
    return [e["texto"] for e in estados if e["tilda"] and e["texto"].lower() != "all"]


# ── Results ──────────────────────────────────────────────────────────────────

def buscar(driver):
    """Filter -> Results (la solapa Analysis de las grabaciones no lleva nada que completar)."""
    jc(driver, driver.find_element(By.CSS_SELECTOR, "#tptablabel-resultsTab"))
    pausa(2)
    esperar_fin_carga(driver, timeout=30)


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


def esperar_resultados(driver, timeout=60):
    """Found estable en dos lecturas seguidas, sin diálogo de carga."""
    pausa(3)
    fin = time.time() + timeout * tp.VELOCIDAD
    previo = None
    while time.time() < fin:
        esperar_fin_carga(driver, 5)
        found, selected = leer_contadores(driver)
        if found is not None and found == previo:
            return found, selected
        previo = found
        pausa(1.2)
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
        pausa(1.5)
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


# ── Panel derecho por fila ───────────────────────────────────────────────────

JS_PANEL = """
    function v(host, sel) {
        var h = document.querySelector('#resultsTab .serviceline-details #' + host);
        var e = h ? h.querySelector(sel) : null;
        return e ? (e.value || '') : null;
    }
    return {pickup_fecha: v('pickupDate', 'input.tphidden'), pickup_hora: v('pickupTime', 'input'),
            pickup_texto: v('pickupRemarks', 'input, textarea'),
            dropoff_fecha: v('dropoffDate', 'input.tphidden'), dropoff_hora: v('dropoffTime', 'input'),
            dropoff_texto: v('dropoffRemarks', 'input, textarea'), remarks: v('remarks', 'input, textarea')};
"""

JS_CHECK_FILA = """
    var tb = document.querySelector('#resultsTab tp-grid table');
    var tr = Array.from(tb.querySelectorAll('tbody > tr')).filter(function(r){
        return r.querySelector(':scope > td.tpcol-optioncode'); })[arguments[0]];
    if (!tr) return null;
    var td = tr.querySelector(':scope > td.tpcol-checkbox');
    return td ? (td.querySelector('label') || td.querySelector('i') || td.querySelector('input') || td) : null;
"""


def leer_panel_filas(driver, filas):
    """Por cada fila (en el orden en que se leyeron, todas en el DOM): la selecciona, lee el panel derecho
    (pickup/drop-off fecha, hora y texto, y Remarks de la línea) y la deselecciona verificando Selected.
    Solo para combinaciones chicas (todas las filas renderizadas)."""
    paneles = []
    for i, f in enumerate(filas):
        objetivo = driver.execute_script(JS_CHECK_FILA, i)
        if objetivo is None:
            raise ErrorFila("ERROR: panel", f"No encontré el checkbox de la fila {i + 1} de Results")
        jc(driver, objetivo)
        pausa(1.2)
        sel = leer_contadores(driver)[1]
        if sel != 1:
            raise ErrorFila("ERROR: panel", f"Al seleccionar la fila {i + 1} quedó Selected={sel} (esperaba 1)")
        panel = driver.execute_script(JS_PANEL)
        if any(v is None for v in panel.values()):
            raise ErrorFila("ERROR: panel", f"El panel derecho no tiene todos los campos esperados: {panel}")
        panel["_ref"] = f["booking_ref"]
        paneles.append(panel)
        jc(driver, objetivo)
        pausa(1.2)
        sel = leer_contadores(driver)[1]
        if sel != 0:
            raise ErrorFila("ERROR: panel", f"No pude deseleccionar la fila {i + 1}: Selected={sel}")
    return paneles


# ── Fila del input ───────────────────────────────────────────────────────────

def campos_fila(row):
    loc, sup, stype, cod = (str(row.get(c) or "").strip().upper()
                            for c in ("LOCATION", "SUPPLIER", "SERVICE TYPE", "CODIGO"))
    if not (loc and sup and stype and cod):
        raise ErrorFila("ERROR: faltan datos", "Faltan LOCATION / SUPPLIER / SERVICE TYPE / CODIGO")
    return loc, sup, stype, cod


def consultar(driver, ctx, loc, stype, sup, cod, desde_dt, hasta_dt, leer=True, marca=None):
    """Abre Substitute Service desde cero, aplica el filtro (con todas sus verificaciones), busca y,
    si leer=True, lee Results por contenido. Deja la pantalla en la pestaña Results.
    Devuelve {found, filas, modo, desc}."""
    marca = marca or (lambda nombre: None)
    preparar_pantalla(driver, ctx)
    marca("abrir")
    ubic = completar_lista(driver, 1, "Location", loc)
    srv = completar_lista(driver, 2, "Service", stype)
    prv = completar_lista(driver, 3, "Supplier", sup)
    desc = completar_code(driver, cod)
    desde = escribir_fecha(driver, "serviceDateFrom", desde_dt)
    hasta = escribir_fecha(driver, "serviceDateTo", hasta_dt)
    tildados = destildar_booking_status(driver)
    marca("filtro")
    print(f"    Filtro OK: {ubic} | {srv} | {prv} | {cod} ({desc}) | {L.fmt_tp(desde)}–{L.fmt_tp(hasta)} "
          f"| Booking Status sin '{BOOKING_STATUS_EXCLUIR}' ({len(tildados)} tildados)")

    buscar(driver)
    found, selected = esperar_resultados(driver)
    marca("buscar")
    print(f"    Found={found} Selected={selected}")
    if selected not in (0, None):
        raise ErrorFila("ERROR: Results", f"Selected={selected} antes de seleccionar nada: alguien tocó la ventana de "
                        "Chrome mientras corría el script (no hay que clickear nada a mano)")
    if found > MAX_RESERVAS:
        raise ErrorFila("ERROR: supera el tope", f"Found={found} > MAX_RESERVAS={MAX_RESERVAS}; no se leyó nada")

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
    psup = L.provisorio_para(stype, SUPPLIER_PROVISORIO)
    texto_prov = (f"{loc}/{psup}/{stype}/{CODIGO_PROVISORIO}" if psup
                  else f"SIN PROVISORIO (no hay proveedor definido para Service Type '{stype}' en el código)")

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

    dup = L.analizar_duplicados(filas)
    estados = L.conteo_estados(filas)
    obs = (f"[LECTURA {ahora()}] Found={found}; estados={estados}; "
           f"claves repetidas={dup['claves']} ({dup['filas']} filas; {dup['difieren_estado']} con estado distinto, "
           f"{dup['difieren_pudo']} con PU/DO distinto); provisorio={texto_prov}; "
           f"{sel_total}lectura={modo_lectura}; t={marcas}")
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
    pausa(1)


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
    pausa(0.4)
    set_val(driver, inp, "")
    pausa(0.4)
    set_val(driver, inp, codigo)
    td = esperar(lambda: driver.execute_script(JS_TD_TEXTO_EXACTO, li, codigo), 6)
    if td is None:
        dump_html("modal_service_status_lista", driver.execute_script("return arguments[0].outerHTML;", li))
        raise ErrorFila("ERROR: service status", f"En el modal, la lista de Service Status no tiene una fila con el texto exacto '{codigo}'")
    jc(driver, td)
    pausa(1.2)
    final = (dialog_repl(driver).find_element(By.CSS_SELECTOR, "div.col1 li:nth-of-type(7) input")
             .get_attribute("value") or "").strip()
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
    pausa(0.6)
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
    pausa(1)
    texto = driver.execute_script("return (arguments[0].innerText || '').replace(/\\s+/g, ' ').trim();", dlg)
    dump_html(f"summary_{etiqueta}", driver.execute_script("return arguments[0].outerHTML;", dlg))
    ss(driver, f"summary_{etiqueta}", forzar=True)
    print(f"    Summary {etiqueta}: {texto[:300]}")
    jc(driver, dlg.find_element(By.CSS_SELECTOR, "tp-button > button"))
    pausa(2)
    esperar_fin_carga(driver, timeout=30)
    return texto[:600]


# ── Modo aplicar: una combinación, ida y vuelta ──────────────────────────────

def provisorio_de_fila(stype):
    psup = L.provisorio_para(stype, SUPPLIER_PROVISORIO)
    if not psup:
        raise ErrorFila("ERROR: sin provisorio", f"No hay proveedor de provisorio definido para el Service Type '{stype}' "
                        f"(tabla SUPPLIER_PROVISORIO del código: {SUPPLIER_PROVISORIO})")
    return psup, CODIGO_PROVISORIO


def verificar_provisorio_libre(driver, ctx, loc, stype, psup, pcod):
    """Validación previa, UNA VEZ por provisorio y por corrida: existe en Tourplan y no tiene ninguna
    reserva en todo el historial (01/01/2020 en adelante). Restos = una corrida anterior falló."""
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
    print(f"    Provisorio {loc}/{psup}/{stype}/{pcod} existe y está libre (se verifica una vez por corrida)")


def _fase_rango(ctx, rango, fase):
    if rango and rango[0]:
        actualizar_columna_rango(ctx["ws_det"], ctx["cols_det"], "FASE", rango[0], rango[1], fase)


def procesar_fila_aplicar(driver, ctx, row):
    """Ida y vuelta COMPLETO de UNA combinación (original A -> provisorio P -> A). Devuelve
    (estado, observaciones, found). Cualquier fallo después del OK de la ida termina en ERROR_COLGADAS
    (las reservas pueden haber quedado en P; su detalle está en SUBSTITUTE_DETALLE).

    Límites de esta fase (se rechazan ANTES de tocar nada): un único estado de servicio, entre 1 y
    PANEL_MAX reservas, y todas con los mismos datos de pickup/drop-off/Remarks."""
    loc, sup, stype, cod = campos_fila(row)
    psup, pcod = provisorio_de_fila(stype)
    desde, hasta = ctx["desde"], ctx["hasta"]

    verificar_provisorio_libre(driver, ctx, loc, stype, psup, pcod)

    # 1) Foto inicial de A (11 columnas por reserva + panel derecho de cada una)
    r0 = consultar(driver, ctx, loc, stype, sup, cod, desde, hasta)
    found0, filas0 = r0["found"], r0["filas"]
    if found0 == 0:
        return "OK", f"[APLICAR {ahora()}] Found=0: no hay reservas para sustituir en el rango", 0
    estados = sorted({f["status"] for f in filas0})
    if len(estados) != 1:
        raise ErrorFila("ERROR: no soportado en esta fase", f"Hay {len(estados)} estados de servicio distintos {estados}; "
                        "esta fase solo procesa combinaciones con un único estado (no se tocó nada)")
    status0 = estados[0]
    if found0 > PANEL_MAX or r0["modo"] != "completa":
        raise ErrorFila("ERROR: no soportado en esta fase", f"{found0} reservas (modo de lectura {r0['modo']}); esta fase procesa "
                        f"hasta TOURPLAN_PANEL_MAX={PANEL_MAX} (no se tocó nada)")
    paneles0 = leer_panel_filas(driver, filas0)
    if not L.paneles_homogeneos(paneles0):
        raise ErrorFila("ERROR: no soportado en esta fase", "Las reservas tienen distintos datos de pickup/drop-off/Remarks; "
                        "esta fase necesita que sean iguales para reponerlos en un solo modal (no se tocó nada)")
    pudo0 = {k: paneles0[0][k] for k in L.CAMPOS_PANEL}
    reponer = L.necesita_pudo(pudo0)
    print(f"    Foto inicial: {found0} reserva(s), estado {status0}, pickup/drop-off a reponer: {'sí' if reponer else 'no'}")
    if any(p["pickup_fecha"] and p["pickup_fecha"] != f["date"] for p, f in zip(paneles0, filas0)):
        print("    ⚠ El pickup/drop-off original no cae en la fecha del servicio: el modal no permite fecha, "
              "la verificación final lo va a marcar para revisión manual.")

    # 2) IDA: A -> P (selecciona todo, modal, confirmación, detalle ANTES del OK, OK, summary)
    usado = seleccionar_todo(driver, found0)
    abrir_modal_reemplazo(driver, found0)
    # El pickup/drop-off también se carga en la ida: así los selectores de esos campos se prueban ANTES de
    # mover nada (si fallan, no hay nada que recuperar) y el provisorio conserva los datos como respaldo.
    llenar_modal(driver, loc, stype, psup, pcod, status0, pudo0 if reponer else None, "ida")
    if not confirmar(f"{loc}/{sup}/{stype}/{cod}: {found0} reserva(s) (estado {status0}). IDA: mover al provisorio "
                     f"{loc}/{psup}/{stype}/{pcod}{' (con pickup/drop-off/remarks)' if reponer else ''}. Después la VUELTA "
                     f"(devolverlas al original) se hace sola y se verifica contra la foto inicial. Se hace click en el OK de la ida."):
        jc(driver, dialog_repl(driver).find_element(By.CSS_SELECTOR, "tp-button.cancel > button"))
        raise ErrorFila("ERROR: cancelado", "El usuario canceló antes del OK de la ida. No se movió nada.")
    n, ini, fin = agregar_filas_batch(ctx["ws_det"], ctx["cols_det"],
                                      armar_detalle(ctx, loc, sup, stype, cod, filas0, "IDA_EN_CURSO", "", paneles0),
                                      devolver_rango=True)
    rango = (ini, fin)
    ida_ejecutada = True   # a partir de acá, cualquier fallo = ERROR_COLGADAS
    obs = []
    try:
        obs.append("summary ida: " + ok_modal_y_summary(driver, "ida"))
        _fase_rango(ctx, rango, "IDA")

        # 3) Verificar P: lo que se movió es un subconjunto exacto de lo que se leyó en A
        r1 = consultar(driver, ctx, loc, stype, psup, pcod, desde, hasta)
        found1 = r1["found"]
        if found1 > found0:
            raise ErrorFila("ERROR: provisorio", f"P tiene {found1} reservas y se movieron como máximo {found0}")
        _, sobran = L.diferencia_multiconjunto(filas0, r1["filas"], L.CAMPOS_RESERVA)
        if sobran:
            raise ErrorFila("ERROR: provisorio", f"P tiene reservas que no estaban en A: {sobran[:3]}")
        no_movidas = L.diferencia_multiconjunto(filas0, r1["filas"], L.CAMPOS_RESERVA)[0]
        print(f"    P tras la ida: Found={found1} de {found0} (no sustituidas por Tourplan: {len(no_movidas)})")
        if found1 == 0:
            _fase_rango(ctx, rango, "NO_SUSTITUIDA")
            return "ERROR_REVISAR", (f"[APLICAR {ahora()}] Tourplan no sustituyó ninguna de las {found0} reserva(s) "
                                      f"(siguen en el original). {' | '.join(obs)}"), found0

        # 4) VUELTA: P -> A (selecciona todo lo que hay en P, modal con el original, estado y pickup/drop-off)
        usado = seleccionar_todo(driver, found1)
        abrir_modal_reemplazo(driver, found1)
        llenar_modal(driver, loc, stype, sup, cod, status0, pudo0 if reponer else None, "vuelta")
        # La vuelta no pide confirmación: ya está confirmada la combinación y frenar acá dejaría reservas en el provisorio.
        obs.append("summary vuelta: " + ok_modal_y_summary(driver, "vuelta"))
        _fase_rango(ctx, rango, "VUELTA")

        # 5) Verificación final contra la foto inicial
        r2 = consultar(driver, ctx, loc, stype, sup, cod, desde, hasta)
        faltan, sobran = L.diferencia_multiconjunto(filas0, r2["filas"])
        paneles2 = leer_panel_filas(driver, r2["filas"]) if r2["found"] == found0 and r2["modo"] == "completa" else None
        if r2["found"] == found0 and not faltan and not sobran:
            # Todas las reservas de la foto están de vuelta en A y P estaba vacío antes de la ida: P quedó vacío.
            print("    P no se vuelve a consultar: el original quedó idéntico a la foto, así que P está vacío")
        else:
            r3 = consultar(driver, ctx, loc, stype, psup, pcod, desde, hasta, leer=False)
            if r3["found"] != 0:     # sustitutos sin asignar: es el caso que detiene toda la corrida
                raise ErrorFila("ERROR: provisorio", f"El provisorio quedó con {r3['found']} reserva(s) tras la vuelta")
        diferencias = []
        if r2["found"] != found0:
            diferencias.append(f"el original tiene Found={r2['found']} y antes {found0}")
        if faltan or sobran:
            diferencias.append(f"columnas distintas: antes-y-no-ahora {faltan[:2]} | ahora-y-no-antes {sobran[:2]}")
        if paneles2 is None:
            diferencias.append("no se pudo releer el panel derecho para comparar pickup/drop-off/Remarks")
        else:
            otros = [p for p in paneles2 if tuple(p[k] for k in L.CAMPOS_PANEL) != tuple(pudo0[k] for k in L.CAMPOS_PANEL)]
            if otros:
                diferencias.append(f"panel distinto al original en {len(otros)} reserva(s): ahora {otros[0]} / antes {pudo0}")
    except ErrorFila as e:
        raise ErrorFila("ERROR_COLGADAS", f"Falló después del OK de la ida ({e.estado}: {e.detalle}). Las reservas pueden haber "
                        f"quedado en el provisorio {loc}/{psup}/{stype}/{pcod}; ver SUBSTITUTE_DETALLE (fase {{IDA_EN_CURSO, IDA, VUELTA}}).")
    except Exception:
        raise ErrorFila("ERROR_COLGADAS", "Excepción después del OK de la ida; las reservas pueden estar en el provisorio "
                        f"{loc}/{psup}/{stype}/{pcod}: {traceback.format_exc(limit=3)}")

    # 6) Cierre: FASE final por reserva (las que Tourplan no sustituyó siguen en A: NO_SUSTITUIDA)
    base = "VERIFICADO" if not diferencias else "REVISION_MANUAL"
    sin_asignar = list(no_movidas)
    for i, f in enumerate(filas0):
        firma = tuple(f[c] for c in L.CAMPOS_RESERVA)
        if firma in sin_asignar:
            sin_asignar.remove(firma)
            fase = "NO_SUSTITUIDA"
        else:
            fase = base
        if rango[0]:
            actualizar_columna_rango(ctx["ws_det"], ctx["cols_det"], "FASE", rango[0] + i, rango[0] + i, fase)
    resumen = (f"[APLICAR {ahora()}] {found0} reserva(s) en A; sustituidas {found1}; no sustituidas {len(no_movidas)}; "
               f"{' | '.join(obs)}")
    if diferencias:
        return "ERROR_REVISAR", resumen + " | DIFERENCIAS: " + "; ".join(diferencias), found0
    return "OK", resumen + " | verificado: el original quedó idéntico a la foto inicial y el provisorio vacío", found0


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
    """Modo aplicar: no arranca si el detalle tiene reservas a mitad de camino (corrida anterior cortada)
    ni si la usuaria no confirmó que revisó que no quedan 000000 pendientes."""
    f_det, c_det = cargar_sheet(ws_det)
    f_det = [L.canonizar_fila(f, COLS_DET) for f in f_det]
    pend = L.detalle_pendiente(f_det)
    if pend:
        detalle = "\n".join(f"  {k[0]}/{k[1]}/{k[2]}/{k[3]} (corrida {k[4]}): {n} reserva(s)" for k, n in sorted(pend.items()))
        raise SystemExit("⛔ SUBSTITUTE_DETALLE tiene reservas a mitad de camino (FASE IDA_EN_CURSO / IDA / VUELTA): una corrida "
                         f"anterior no terminó. Revisar en Tourplan que no queden en el provisorio y corregir la FASE:\n{detalle}")
    if CONFIRMO_SIN_PENDIENTES != "SI":
        raise SystemExit("⛔ Modo aplicar: confirmá que revisaste a mano (reporte de Tourplan) que no hay códigos 000000 "
                         "pendientes de sustituir, con TOURPLAN_CONFIRMO_SIN_PENDIENTES=SI")


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
        print("⚠⚠⚠  MODO APLICAR: este script MODIFICA reservas en Tourplan (producción).")
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
    print(f"Rango de servicios: {L.fmt_tp(desde)} – {L.fmt_tp(hasta)} · tope MAX_RESERVAS={MAX_RESERVAS}")

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

    sin_prov = L.service_types_sin_provisorio(unicas, SUPPLIER_PROVISORIO)
    tipos = sorted({str(f.get("SERVICE TYPE") or "").strip().upper() for f in unicas})
    print(f"\nService Types del input: {tipos} · provisorios definidos en el código: {SUPPLIER_PROVISORIO} (código {CODIGO_PROVISORIO})")
    if sin_prov:
        print(f"⚠ Sin proveedor de provisorio definido para: {sin_prov} → esas filas terminarán en ERROR en modo aplicar.")
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
