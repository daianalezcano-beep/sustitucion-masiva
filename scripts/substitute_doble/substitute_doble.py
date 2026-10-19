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

ESTA VERSIÓN (v0.1) IMPLEMENTA SOLO EL MODO LECTURA (Fase 1): por cada fila
PENDIENTE abre Substitute Service, aplica el filtro, lee Results y escribe lo
leído en SUBSTITUTE_DETALLE. NUNCA hace click en ningún OK ni sustituye nada.
El modo "aplicar" (ida y vuelta) se agrega en la Fase 2.

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
# v0.1 (2026-10-19): primera versión, solo modo lectura. SIN CORRER contra
#   Tourplan ni contra el Sheet; la lógica pura (common/logica.py) sí tiene
#   pruebas por simulación (tests/test_logica.py).

VERSION = "0.1"
VERSION_FECHA = "2026-10-19"

import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from selenium.webdriver.common.by import By

from common import logica as L
from common import tp_selenium as tp
from common.abort import ABORT_EXIT_CODE, AbortadoPorUsuario, chequear_abort
from common.sheets_batch import agregar_filas_batch, asegurar_columnas_tolerante
from common.sheets_client import actualizar_fila_sheet, cargar_sheet, conectar_sheets
from common.user_config import CREDENTIALS_PATH as _CRED_DEFAULT
from common.user_config import TOKEN_PATH as _TOKEN_DEFAULT
from common.tp_selenium import dump, dump_html, esperar_fin_carga, jc, pausa, set_val, set_val_con_blur, ss

# ── CONFIG ──
MODO = os.environ.get("TOURPLAN_MODO", "lectura")
SHEET_URL = os.environ.get("TOURPLAN_SHEET_URL", "")
HOJA = os.environ.get("TOURPLAN_HOJA", "SUBSTITUTE")
HOJA_PROV = os.environ.get("TOURPLAN_HOJA_PROVISORIOS", "PROVISORIOS")
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
# Reusar el diálogo entre filas ahorra ~15 s por fila pero NO está validado: Found podría
# quedar con el valor de la fila anterior. Default: abrir la pantalla de cero en cada fila.
REUSAR_PANTALLA = os.environ.get("TOURPLAN_REUSAR_PANTALLA", "0").strip() in ("1", "true", "True")

COL_ESTADO, COL_OBS, COL_TS = "ESTADO", "OBSERVACIONES", "TIMESTAMP"
COLS_INPUT = ["LOCATION", "SUPPLIER", "SERVICE TYPE", "CODIGO", COL_ESTADO]
COLS_DET = ["TIMESTAMP", "CORRIDA", "LOCATION", "SUPPLIER", "SERVICE TYPE", "CODIGO", "DESCRIPTION",
            "BOOKING NAME", "BOOKING REF", "TRAVEL DATE", "DATE", "STATUS_ORIGINAL", "PUDO_ORIGINAL",
            "PICKUP_TEXTO", "DROPOFF_TEXTO", "REMARKS", "FASE", "RESULTADO", "OBSERVACIONES"]

URL_OPERATIONS = f"{tp.BASE_URL}/#/operations/OperationsBookings"
URL_HOME = f"{tp.BASE_URL}/#/home"
BOOKING_STATUS_EXCLUIR = "CX - Cancelled"


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


def completar_lista(driver, n, nombre, valor):
    """Location/Service/Supplier: tipear, esperar la lista, elegir la fila de
    CÓDIGO IDÉNTICO (td.code) y leer el valor final. Sin coincidencia exacta
    no se toca nada más."""
    dlg = dialog_subs(driver)
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
    final = (dialog_subs(driver).find_element(
        By.CSS_SELECTOR, f"div.col1 li:nth-of-type({n}) input").get_attribute("value") or "").strip()
    if final.upper() != valor.upper() and not final.upper().startswith(valor.upper() + " - "):
        raise ErrorFila("ERROR: campo distinto",
                        f"{nombre}: pedí '{valor}' y quedó '{final}'")
    return final


def completar_code(driver, codigo):
    dlg = dialog_subs(driver)
    cod = dlg.find_element(By.CSS_SELECTOR, "#optionCode > tp-validator > input")
    set_val_con_blur(driver, cod, codigo)
    pausa(2)
    esperar_fin_carga(driver)

    def descripcion():
        d = dialog_subs(driver).find_element(By.CSS_SELECTOR, "input.tpdescription-optiondescription")
        return (d.get_attribute("value") or "").strip()

    desc = esperar(descripcion, 8)
    if not desc:
        raise ErrorFila("ERROR: código", f"Code '{codigo}': Description quedó vacía (código inexistente o datos previos mal)")
    final = (dialog_subs(driver).find_element(
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
    """Filter -> Analysis -> Results (la secuencia de las grabaciones)."""
    for css in ("#tptablabel-bookinganalysis", "#tptablabel-resultsTab"):
        jc(driver, driver.find_element(By.CSS_SELECTOR, css))
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
    pu = do = ("", "")
    for r in f.pop("pudo_filas", []):
        etiqueta = (r[0] if r else "").lower()
        if etiqueta.startswith("pickup"):
            pu = L.texto_pudo(r[1:])
        elif etiqueta.startswith("dropoff") or etiqueta.startswith("drop-off"):
            do = L.texto_pudo(r[1:])
    f["pickup_fecha_hora"], f["pickup_texto"] = pu
    f["dropoff_fecha_hora"], f["dropoff_texto"] = do
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


def probar_seleccion_total(driver, found):
    """Selecciona TODO con el checkbox del encabezado de Results, verifica que
    Selected == Found y deselecciona (verificando Selected == 0). Es la misma
    mecánica que usará el modo aplicar, pero sin tocar nunca OK. Prueba el
    <label>, el <i> y el <input> del encabezado, en ese orden, hasta que uno
    seleccione (la grabación clickeó el <i> y el <label>)."""
    candidatos = driver.execute_script(JS_ENCABEZADO)
    if not candidatos:
        raise ErrorFila("ERROR: selección", "No encontré el checkbox del encabezado de Results")
    usado = None
    for nombre, el in zip(("label", "i", "input"), candidatos):
        jc(driver, el)
        pausa(1.5)
        sel = leer_contadores(driver)[1]
        if sel == found:
            usado = (nombre, el)
            break
        if sel not in (0, None):
            raise ErrorFila("ERROR: selección", f"Select all dejó Selected={sel} con Found={found} (click en {nombre})")
    if usado is None:
        raise ErrorFila("ERROR: selección", f"Ningún click en el encabezado seleccionó las {found} fila(s) (Selected=0)")
    jc(driver, usado[1])
    pausa(1.5)
    sel2 = leer_contadores(driver)[1]
    if sel2 != 0:
        raise ErrorFila("ERROR: selección", f"No pude deseleccionar: Selected={sel2} tras el segundo click ({usado[0]})")
    return usado[0]


# ── Fila del input ───────────────────────────────────────────────────────────

def procesar_fila_lectura(driver, ctx, row):
    """Modo lectura de UNA combinación. Devuelve (observaciones, filas_detalle,
    found, tiempos_por_paso). Lanza ErrorFila si algo no cierra; en ese caso
    no se escribe detalle."""
    marcas, t0 = {}, time.time()

    def marca(nombre):
        nonlocal t0
        marcas[nombre] = round(time.time() - t0, 1)
        t0 = time.time()

    loc, sup, stype, cod = (str(row.get(c) or "").strip().upper()
                            for c in ("LOCATION", "SUPPLIER", "SERVICE TYPE", "CODIGO"))
    if not (loc and sup and stype and cod):
        raise ErrorFila("ERROR: faltan datos", "Faltan LOCATION / SUPPLIER / SERVICE TYPE / CODIGO")
    prov = L.provisorio_de(row, ctx["mapa_prov"])
    texto_prov = f"{prov[0]}/{prov[1]}" if prov else "SIN PROVISORIO"

    preparar_pantalla(driver, ctx)
    marca("abrir")
    ubic = completar_lista(driver, 1, "Location", loc)
    srv = completar_lista(driver, 2, "Service", stype)
    prv = completar_lista(driver, 3, "Supplier", sup)
    desc = completar_code(driver, cod)
    desde = escribir_fecha(driver, "serviceDateFrom", ctx["desde"])
    hasta = escribir_fecha(driver, "serviceDateTo", ctx["hasta"])
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

    filas, modo_lectura = leer_resultados(driver, found) if found else ([], "vacia")
    marca("leer")
    found_fin = leer_contadores(driver)[0]
    if found_fin != found:
        raise ErrorFila("ERROR: Results", f"Found cambió durante la lectura ({found} -> {found_fin})")
    if len(filas) != found:
        dump(driver, f"lectura_incompleta_{cod}")
        raise ErrorFila("ERROR: lectura incompleta",
                        f"Found={found} pero se leyeron {len(filas)} filas (modo {modo_lectura})")
    fuera = L.filas_fuera_del_filtro(filas, loc, stype, sup, cod)
    if fuera:
        raise ErrorFila("ERROR: Results", f"Results trae filas que no son {loc}/{stype}/{sup}/{cod}: "
                        f"{[(f['location'], f['service'], f['supplier'], f['code']) for f in fuera]}")
    filas = [normalizar_fila(dict(f)) for f in filas]

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

    detalle = [{
        "TIMESTAMP": ahora(), "CORRIDA": ctx["corrida"], "LOCATION": loc, "SUPPLIER": sup,
        "SERVICE TYPE": stype, "CODIGO": cod, "DESCRIPTION": f["description"],
        "BOOKING NAME": f["booking_name"], "BOOKING REF": f["booking_ref"],
        "TRAVEL DATE": f["travel_date"], "DATE": f["date"], "STATUS_ORIGINAL": f["status"],
        "PUDO_ORIGINAL": f["pudo_raw"], "PICKUP_TEXTO": f["pickup_texto"],
        "DROPOFF_TEXTO": f["dropoff_texto"], "REMARKS": "", "FASE": "LECTURA",
        "RESULTADO": "LEIDA", "OBSERVACIONES": "",
    } for f in filas]
    return obs, detalle, found, marcas


# ── Sheets ───────────────────────────────────────────────────────────────────

def escribir_resultado_fila(ws, row_idx, columnas, estado=None, observaciones=None):
    valores = {COL_TS: ahora()}
    if estado is not None:
        valores[COL_ESTADO] = estado
    if observaciones is not None:
        valores[COL_OBS] = observaciones
    actualizar_fila_sheet(ws, row_idx, columnas, valores)


CANON_INPUT = COLS_INPUT + [COL_OBS, COL_TS]
CANON_PROV = ["LOCATION", "SERVICE TYPE", "SUPPLIER_PROV", "CODIGO_PROV"]


def cargar_hojas():
    """Lee las tres hojas. Los encabezados se aceptan con guion bajo o espacio y en cualquier
    combinación de mayúsculas/minúsculas ('SERVICE_TYPE' == 'SERVICE TYPE')."""
    ws = conectar_sheets(SHEET_URL, HOJA, CREDENTIALS_PATH, TOKEN_PATH)
    asegurar_columnas_tolerante(ws, [COL_OBS, COL_TS])
    filas, columnas = cargar_sheet(ws)
    columnas = L.canonizar_columnas(columnas, CANON_INPUT)
    filas = [L.canonizar_fila(f, CANON_INPUT) for f in filas]
    faltan = [c for c in COLS_INPUT if c not in columnas]
    if faltan:
        raise ValueError(f"Faltan columnas {faltan} en la hoja '{HOJA}'. Detectadas: {columnas}")
    print(f"Sheet '{HOJA}': {len(filas)} fila(s)")

    mapa = {}
    try:
        ws_prov = conectar_sheets(SHEET_URL, HOJA_PROV, CREDENTIALS_PATH, TOKEN_PATH)
        f_prov, c_prov = cargar_sheet(ws_prov)
        c_prov_canon = L.canonizar_columnas(c_prov, CANON_PROV)
        f_prov = [L.canonizar_fila(f, CANON_PROV) for f in f_prov]
        mapa = L.mapa_provisorios(f_prov)
        print(f"Hoja '{HOJA_PROV}': columnas leídas {c_prov} · {len(f_prov)} fila(s) con datos · "
              f"{len(mapa)} válida(s) (con las 4 columnas completas)")
        sin_columna = [c for c in CANON_PROV if c not in c_prov_canon]
        if sin_columna:
            print(f"⚠ A la hoja '{HOJA_PROV}' le faltan las columnas {sin_columna} (se esperan {CANON_PROV}).")
        elif f_prov and not mapa:
            print(f"⚠ '{HOJA_PROV}' tiene filas pero ninguna con las 4 columnas completas. Primera fila leída: "
                  f"{ {k: v for k, v in f_prov[0].items() if not str(k).startswith('__')} }")
        if mapa:
            print("  Provisorios mapeados: " + ", ".join(f"{a}/{b}→{c}/{d}" for (a, b), (c, d) in sorted(mapa.items())))
    except ValueError as e:
        print(f"⚠ {e}\n  Sigo sin mapa de provisorios (solo afecta al aviso 'SIN PROVISORIO').")

    ws_det = cols_det = None
    if ESCRIBIR_DETALLE:
        ws_det = conectar_sheets(SHEET_URL, HOJA_DET, CREDENTIALS_PATH, TOKEN_PATH)
        asegurar_columnas_tolerante(ws_det, COLS_DET)
        cols_det = ws_det.row_values(1)
    return ws, filas, columnas, mapa, ws_det, cols_det


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print(f"  📌 VERSION {VERSION} ({VERSION_FECHA}) · MODO={MODO} · VELOCIDAD={tp.VELOCIDAD}")
    print("  Doble sustitución — Substitute Service (modo lectura: nunca clickea OK)")
    print("=" * 70)
    if MODO != "lectura":
        raise SystemExit(f"MODO='{MODO}' todavía no está implementado (Fase 2). Solo MODO=lectura.")
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
    carpeta, corrida = tp.preparar_carpeta_corrida("lectura")
    print(f"Corrida {corrida} · salida (capturas, HTML, JSON y consola.log): {os.path.abspath(carpeta)}")
    ws, filas, columnas, mapa, ws_det, cols_det = cargar_hojas()

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

    pares, faltan_prov = L.pares_sin_provisorio(unicas, mapa)
    print(f"\nCombinaciones (LOCATION, SERVICE TYPE) del input: {len(pares)}")
    if faltan_prov:
        print(f"⚠ SIN PROVISORIO en PROVISORIOS ({len(faltan_prov)}): "
              + ", ".join(f"{a}/{b}" for a, b in faltan_prov))
        print("  → hay que crear esos options (código 000000) antes del modo aplicar.")
    else:
        print("✅ Todas las combinaciones tienen provisorio mapeado.")
    print(f"Filas PENDIENTE a procesar: {len(unicas)}")
    if not unicas:
        print("\n⛔ Sin filas PENDIENTE para procesar.")
        return

    ctx = {"corrida": corrida, "mapa_prov": mapa, "desde": desde,
           "hasta": hasta, "abierta": False, "panel_pendiente": PROBAR_PANEL, "panel": None}

    print("\n⚠ Mientras corre, NO toques la ventana de Chrome (cualquier click a mano puede alterar la lectura).")
    driver = tp.crear_driver()
    sesion_iniciada = abortado = False
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
        print(f"📄 Sheet: {SHEET_URL}")
        print(f"📁 Salida de esta corrida: {os.path.abspath(tp.SS_DIR)}")

    if abortado:
        sys.exit(ABORT_EXIT_CODE)


if __name__ == "__main__":
    main()
