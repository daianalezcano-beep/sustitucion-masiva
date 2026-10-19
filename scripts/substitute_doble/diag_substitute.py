"""
═══════════════════════════════════════════════════════════════════════════
  TOURPLAN NX — Diagnóstico de Booking Operations > Substitute Service (Fase 0)
═══════════════════════════════════════════════════════════════════════════

Propósito: volcar HTML + capturas + un resumen JSON para resolver lo que las
grabaciones de Chrome Recorder no muestran. NO ejecuta ninguna sustitución.

Garantías de seguridad (por construcción):
  - Nunca hace click en ningún botón OK (tp-button.ok).
  - Nunca selecciona filas de Results ni abre el modal Replacement Service.
  - Lo único que escribe son los inputs del Filter (no persiste nada en
    Tourplan) y lo único que cierra es el diálogo con EXIT.
  - Logout solo si el login fue exitoso.

Qué resuelve (secciones del prompt v2):
  (a) camino por menú desde #/home + si funciona la URL directa
  (b) estructura del desplegable de Location / Service / Supplier, y qué
      hace ArrowDown+Enter con un prefijo ambiguo
  (c) clases tpcol-* de Results, contadores Found/Selected, virtual scroll
  (d) comportamiento de los inputs de fecha ("Invalid characters!", 49→?)
  (e) inventario de controles de Filter y Analysis (ids), con las opciones
      de los desplegables de estado
  (f) NO se hace: requiere abrir el modal (OK principal). Lo vuelca a mano
      la usuaria.

Config por variables de entorno (TOURPLAN_*), igual que el resto de los
scripts; el caso controlado se cambia con DIAG_*.
"""

# ── CAMBIOS ──
# v0.1 (2026-10-19): primera versión. SIN CORRER contra Tourplan.

VERSION = "0.1"
VERSION_FECHA = "2026-10-19"

import json
import os
import re
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from common.chrome_bootstrap import find_or_prepare_chrome

# ── CONFIG ──
VELOCIDAD = float(os.environ.get("TOURPLAN_VELOCIDAD", "1.5"))
USERNAME = os.environ.get("TOURPLAN_USERNAME", "")
PASSWORD = os.environ.get("TOURPLAN_PASSWORD", "")
BASE_URL = os.environ.get("TOURPLAN_BASE_URL", "https://tourplannx.eurotur.com.ar/tourplannx").rstrip("/")
SS_DIR = os.environ.get("TOURPLAN_SS_DIR", "diag_out")
HEADLESS = os.environ.get("TOURPLAN_HEADLESS", "0").strip() in ("1", "true", "True")

# Caso controlado (una reserva conocida). Cambiar con variables de entorno.
D_LOCATION = os.environ.get("DIAG_LOCATION", "BUE")
D_SERVICE = os.environ.get("DIAG_SERVICE", "GU")
D_SUPPLIER = os.environ.get("DIAG_SUPPLIER", "1ACOS1")
D_CODIGO = os.environ.get("DIAG_CODIGO", "700GUI")
D_DESDE = os.environ.get("DIAG_FECHA_DESDE", "01/11/26")
D_HASTA = os.environ.get("DIAG_FECHA_HASTA", "01/11/26")

URL_OPERATIONS = f"{BASE_URL}/#/operations/OperationsBookings"

REPORT = {"version": VERSION, "inicio": datetime.now().isoformat(timespec="seconds")}

CHROMIUM_BIN, ver_chrome = find_or_prepare_chrome()


# ── Helpers (copiados/adaptados de modificar_rate_name_text.py) ─────────────

def crear_driver():
    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1730,1012")
    opts.add_argument("--disable-gpu")
    opts.add_argument(f"--user-data-dir={tempfile.mkdtemp(prefix='tourplan_chrome_profile_')}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.binary_location = CHROMIUM_BIN
    print(f"Chrome binary: {CHROMIUM_BIN}  ({ver_chrome})")
    drv_path = ChromeDriverManager().install()
    d = webdriver.Chrome(service=Service(executable_path=drv_path), options=opts)
    print("✅ Driver iniciado")
    return d


def jc(driver, el):
    driver.execute_script("arguments[0].click();", el)


def pausa(seg):
    time.sleep(seg * VELOCIDAD)


def ss(driver, nombre):
    os.makedirs(SS_DIR, exist_ok=True)
    nombre = re.sub(r"[^A-Za-z0-9_-]+", "_", nombre)[:60]
    p = f"{SS_DIR}/{nombre}_{int(time.time())}.png"
    try:
        driver.save_screenshot(p)
        print(f"  📸 {os.path.basename(p)}")
    except Exception as e:
        print(f"  ⚠ No se pudo guardar captura: {e}")


def dump_html(nombre, html):
    os.makedirs(SS_DIR, exist_ok=True)
    nombre = re.sub(r"[^A-Za-z0-9_-]+", "_", nombre)[:60]
    p = f"{SS_DIR}/{nombre}_{int(time.time())}.html"
    with open(p, "w", encoding="utf-8") as f:
        f.write(html or "")
    print(f"  💾 HTML: {p}")


def dump(driver, nombre):
    dump_html(nombre, driver.page_source)


def guardar_reporte():
    os.makedirs(SS_DIR, exist_ok=True)
    p = f"{SS_DIR}/diag_resumen.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(REPORT, f, ensure_ascii=False, indent=2, default=str)
    print(f"  🧾 Resumen: {p}")


def set_val(driver, el, value):
    driver.execute_script("""
        var inp = arguments[0], val = arguments[1];
        var setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(inp, val);
        inp.dispatchEvent(new Event('input',  {bubbles:true}));
        inp.dispatchEvent(new Event('change', {bubbles:true}));
        inp.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));
    """, el, value)


def set_val_con_blur(driver, el, value):
    driver.execute_script("""
        var inp = arguments[0], val = arguments[1];
        inp.focus();
        inp.dispatchEvent(new Event('focus', {bubbles:true}));
        var setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(inp, val);
        inp.dispatchEvent(new Event('input',  {bubbles:true}));
        inp.dispatchEvent(new Event('change', {bubbles:true}));
        inp.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));
        inp.blur();
        inp.dispatchEvent(new Event('blur',     {bubbles:true}));
        inp.dispatchEvent(new Event('focusout', {bubbles:true}));
    """, el, value)


def esperar_fin_carga(driver, timeout=15):
    fin = time.time() + timeout * VELOCIDAD
    while time.time() < fin:
        if not driver.find_elements(By.CSS_SELECTOR, "dialog[open]"):
            return
        time.sleep(0.3)
    print("    ⚠ El dialog 'PLEASE WAIT...' seguía abierto tras esperar")


def login(driver):
    print("🔐 Login...")
    driver.get(f"{BASE_URL}/#/login")
    pausa(6)

    def campos():
        return driver.execute_script("""
            function vis(e){return !!(e && (e.offsetWidth||e.offsetHeight
                                      ||e.getClientRects().length) && !e.disabled);}
            var txt = Array.from(document.querySelectorAll(
                "input[type='text'], input:not([type])")).filter(vis);
            var pwd = Array.from(document.querySelectorAll(
                "input[type='password']")).filter(vis);
            return [txt[0]||null, pwd[0]||null];
        """)

    u_el = p_el = None
    for _ in range(15):
        u_el, p_el = campos()
        if u_el and p_el:
            break
        pausa(2)
    if not (u_el and p_el):
        ss(driver, "login_sin_campos")
        raise Exception("No aparecieron los campos de login (usuario/password)")

    for el, valor in ((u_el, USERNAME), (p_el, PASSWORD)):
        try:
            el.clear()
            el.click()
            el.send_keys(valor)
        except Exception:
            set_val(driver, el, valor)
    time.sleep(0.5)

    clic = driver.execute_script(r"""
        function vis(e){return !!(e && (e.offsetWidth||e.offsetHeight
                                  ||e.getClientRects().length) && !e.disabled);}
        var b = Array.from(document.querySelectorAll(
            "button.login, button[type='submit'], button")).filter(vis)
            .find(function(x){return /log\s*in|ingresar|entrar|sign\s*in/i
                                     .test((x.innerText||'')) ||
                                     x.classList.contains('login');});
        if (b){ b.click(); return (b.innerText||'button.login').trim(); }
        return null;
    """)
    if not clic:
        p_el.send_keys(Keys.ENTER)
    pausa(8)
    assert "login" not in driver.current_url.lower(), "Login falló"
    print("✅ Login OK")


def logout(driver):
    print("\n🔒 Logout...")
    try:
        principal = driver.window_handles[0]
        for h in driver.window_handles[1:]:
            try:
                driver.switch_to.window(h)
                driver.close()
            except Exception:
                pass
        driver.switch_to.window(principal)
    except Exception:
        pass
    try:
        driver.get(f"{BASE_URL}/#/home")
        pausa(4)
        btn_panel = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#openUserPanel")))
        jc(driver, btn_panel)
        pausa(2)
        btn_logout = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                "div.panelHeader tp-button button, div.panelHeader button")))
        jc(driver, btn_logout)
        pausa(5)
        en_login = driver.execute_script(
            "var p=document.querySelector(\"input[type='password']\");"
            "return (p && p.offsetParent !== null) || /login/i.test(location.href);")
        print("  🔓 Logout OK" if en_login else "  ⚠ Logout no confirmado")
    except Exception as e:
        print(f"  ⚠ Error en logout: {e}")


# ── Helpers de este diagnóstico ─────────────────────────────────────────────

def dialog_subs(driver):
    """Último tp-dialog que contiene tp-substitute-service (nunca por
    nth-of-type: Angular no saca del DOM los diálogos cerrados)."""
    return driver.execute_script("""
        var ds = Array.from(document.querySelectorAll('tp-dialog'))
            .filter(function(d){ return d.querySelector('tp-substitute-service'); });
        return ds.length ? ds[ds.length - 1] : null;
    """)


def textos_menu(driver, raiz_css):
    return driver.execute_script("""
        return Array.from(document.querySelectorAll(arguments[0] + ' ul > li')).map(function(li, i){
            var t = (li.innerText || '').trim().split('\\n')[0];
            return {n: i + 1, texto: t.slice(0, 60), clase: li.className,
                    visible: !!(li.offsetWidth || li.offsetHeight)};
        });
    """, raiz_css)


def outer(driver, css, limite=200000):
    return driver.execute_script(
        "var e = document.querySelector(arguments[0]);"
        "return e ? e.outerHTML.slice(0, arguments[1]) : null;", css, limite)


def inventario(driver, raiz):
    """Todos los <li> visibles con input/select/textarea dentro del diálogo:
    etiqueta, ids, clases, atributos y valor. Sirve para Filter y Analysis."""
    return driver.execute_script("""
        var raiz = arguments[0];
        function vis(e){ return !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length); }
        var out = [];
        raiz.querySelectorAll('li').forEach(function(li){
            if (!vis(li)) return;
            var ctl = li.querySelector('input, select, textarea');
            if (!ctl) return;
            var lab = li.querySelector('label');
            var hijo = li.querySelector('[id]');
            out.push({
                etiqueta: (lab ? lab.innerText : (li.innerText || '')).trim().slice(0, 50),
                li_id: li.id || null, id_hijo: hijo ? hijo.id : null,
                tag: ctl.tagName, tipo: ctl.type || null, id: ctl.id || null,
                name: ctl.name || null, clase: ctl.className,
                valor: ctl.type === 'checkbox' ? ctl.checked : (ctl.value || ''),
                readonly: ctl.readOnly || false, disabled: ctl.disabled || false,
                aria: ctl.getAttribute('aria-label'), placeholder: ctl.placeholder || null,
                padre_clase: li.parentElement ? li.parentElement.className : null
            });
        });
        return out;
    """, raiz)


def filas_dropdown(driver, li):
    """Filas <tr> visibles dentro del <li> del campo (donde Tourplan despliega
    la lista de sugerencias, según los scripts existentes)."""
    return driver.execute_script("""
        function vis(e){ return !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length); }
        return Array.from(arguments[0].querySelectorAll('tr')).filter(vis).slice(0, 40).map(function(tr){
            return {clase_tr: tr.className,
                    tds: Array.from(tr.querySelectorAll('td')).map(function(td){
                        return {clase: td.className, texto: (td.innerText || '').trim().slice(0, 80)};
                    })};
        });
    """, li)


def li_de(driver, input_el):
    return driver.execute_script("return arguments[0].closest('li');", input_el)


# ── (a) Navegación ──────────────────────────────────────────────────────────

def hay_operationsview(driver, t=15):
    fin = time.time() + t * VELOCIDAD
    while time.time() < fin:
        if driver.find_elements(By.CSS_SELECTOR, "#operationsview"):
            return True
        time.sleep(0.5)
    return False


def seccion_a_navegacion(driver):
    R = REPORT.setdefault("a_navegacion", {})

    # a1: ¿funciona la URL directa estando logueado?
    driver.get(URL_OPERATIONS)
    pausa(5)
    esperar_fin_carga(driver)
    R["url_directa_ok"] = hay_operationsview(driver)
    R["url_directa_final"] = driver.current_url
    R["title_directa"] = driver.title
    print(f"  URL directa → #operationsview presente: {R['url_directa_ok']} ({driver.current_url})")
    ss(driver, "a1_url_directa")

    # a2: camino por menú desde #/home (solo los 2 clicks GRABADOS; luego se
    # vuelcan los ítems visibles; no se inventa ningún paso intermedio).
    driver.get(f"{BASE_URL}/#/home")
    pausa(4)
    esperar_fin_carga(driver)
    R["home_menu"] = {}
    try:
        img = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#homeview nav tp-nav img")))
        jc(driver, img)
        pausa(2.5)
        R["home_menu"]["nivel0_items"] = textos_menu(driver, "#homeview nav")
        dump_html("a2_home_menu_nivel0", outer(driver, "#homeview nav"))
        item2 = driver.find_element(By.CSS_SELECTOR, "div.nav-menu li:nth-of-type(2) label")
        R["home_menu"]["click_2_texto"] = (item2.text or "").strip()
        jc(driver, item2)
        pausa(2.5)
        R["home_menu"]["nivel1_items"] = textos_menu(driver, "#homeview nav")
        dump_html("a2_home_menu_nivel1", outer(driver, "#homeview nav"))
        ss(driver, "a2_home_menu_nivel1")
    except Exception as e:
        R["home_menu"]["error"] = str(e)
        dump(driver, "a2_home_menu_error")

    # Si la URL directa no funcionó: intentar llegar SOLO si el menú visible
    # tiene exactamente un ítem "booking operations"; si no, se corta acá.
    if not R["url_directa_ok"]:
        candidatos = driver.execute_script(r"""
            return Array.from(document.querySelectorAll('#homeview nav li label, #homeview nav li div'))
                .filter(function(e){ return e.offsetWidth && /booking\s*operations/i.test((e.innerText||'').trim())
                                            && (e.innerText||'').trim().length < 40; });
        """)
        R["candidatos_booking_operations"] = len(candidatos)
        if len(candidatos) == 1:
            jc(driver, candidatos[0])
            pausa(5)
            R["url_directa_ok"] = hay_operationsview(driver)
            R["url_tras_menu"] = driver.current_url
        if not R["url_directa_ok"]:
            raise RuntimeError("No se llegó a Booking Operations (ni URL directa ni menú sin ambigüedad). "
                               "Revisar los HTML del menú volcados.")
    else:
        driver.get(URL_OPERATIONS)
        pausa(5)
        esperar_fin_carga(driver)
        hay_operationsview(driver)

    # a3: menú de #operationsview → ítem 1 (solo si dice Substitute)
    icono = None
    for css in ("#operationsview nav tp-nav > div > div > div > div", "#operationsview nav img"):
        els = driver.find_elements(By.CSS_SELECTOR, css)
        if els:
            icono = els[0]
            R["selector_icono_menu"] = css
            break
    if icono is None:
        dump(driver, "a3_sin_icono")
        raise RuntimeError("No encontré el ícono de menú de #operationsview")
    jc(driver, icono)
    pausa(2.5)
    items = textos_menu(driver, "#operationsview nav")
    R["operations_menu_items"] = items
    dump_html("a3_operations_menu", outer(driver, "#operationsview nav"))
    ss(driver, "a3_operations_menu")

    texto1 = driver.execute_script(
        "var e=document.querySelector('#operationsview nav li:nth-of-type(1)');"
        "return e ? (e.innerText||'').trim() : '';")
    R["item1_texto"] = texto1
    if not re.search(r"substitute", texto1, re.I) or re.search(r"pcm|resource", texto1, re.I):
        raise RuntimeError(f"El ítem 1 del menú dice {texto1!r}: no es 'Substitute Service'. No se hace click.")
    el1 = driver.find_element(By.CSS_SELECTOR, "#operationsview nav li:nth-of-type(1) > div > div")
    jc(driver, el1)
    pausa(4)
    esperar_fin_carga(driver)

    # a4: verificar que es la pantalla correcta
    dlg = None
    fin = time.time() + 15 * VELOCIDAD
    while time.time() < fin and dlg is None:
        dlg = dialog_subs(driver)
        time.sleep(0.5)
    if dlg is None:
        dump(driver, "a4_sin_dialog")
        raise RuntimeError("No apareció tp-substitute-service tras clickear el ítem 1")
    tiene_fechas = driver.execute_script(
        "return !!(arguments[0].querySelector('#serviceDateFrom') && arguments[0].querySelector('#serviceDateTo'));", dlg)
    R["pantalla_correcta"] = bool(tiene_fechas)
    R["url_en_substitute"] = driver.current_url
    dump_html("a4_substitute_dialog", driver.execute_script("return arguments[0].outerHTML;", dlg))
    ss(driver, "a4_substitute_dialog")
    if not tiene_fechas:
        raise RuntimeError("tp-substitute-service sin #serviceDateFrom/#serviceDateTo: pantalla incorrecta")
    print("  ✅ Pantalla correcta: tp-substitute-service con #serviceDateFrom/#serviceDateTo")


# ── (b) Desplegables de Location / Service / Supplier + Code ────────────────

def campo_col1(driver, n):
    dlg = dialog_subs(driver)
    return dlg.find_element(By.CSS_SELECTOR, f"div.col1 li:nth-of-type({n}) input")


def probar_desplegable(driver, n, nombre, valor, prefijo=None):
    """Tipea, vuelca la lista, elige el ítem de código IDÉNTICO y lee el valor
    final. Si hay prefijo: además prueba ArrowDown+Enter (sin confiar en él) y
    registra qué quedó en el campo, para documentar el comportamiento."""
    r = {"valor_pedido": valor}
    inp = campo_col1(driver, n)
    li = li_de(driver, inp)

    if prefijo:
        set_val(driver, inp, "")
        pausa(0.5)
        set_val(driver, inp, prefijo)
        pausa(2)
        r["prefijo"] = prefijo
        r["prefijo_filas"] = filas_dropdown(driver, li)
        dump_html(f"b_{nombre}_prefijo", driver.execute_script("return arguments[0].outerHTML;", li))
        inp.send_keys(Keys.ARROW_DOWN)
        pausa(0.5)
        inp.send_keys(Keys.ENTER)
        pausa(1.5)
        r["prefijo_arrowdown_enter_valor_final"] = inp.get_attribute("value")
        print(f"  [{nombre}] '{prefijo}' + ArrowDown+Enter → '{r['prefijo_arrowdown_enter_valor_final']}'")

    inp = campo_col1(driver, n)
    li = li_de(driver, inp)
    set_val(driver, inp, "")
    pausa(0.5)
    set_val(driver, inp, valor)
    pausa(2)
    filas = filas_dropdown(driver, li)
    r["filas"] = filas
    dump_html(f"b_{nombre}_lista", driver.execute_script("return arguments[0].outerHTML;", li))
    exacta = driver.execute_script("""
        var v = arguments[1].toUpperCase();
        var rows = Array.from(arguments[0].querySelectorAll('tr')).filter(function(tr){
            return !!(tr.offsetWidth || tr.offsetHeight); });
        for (var tr of rows) {
            for (var td of tr.querySelectorAll('td')) {
                if ((td.innerText || '').trim().toUpperCase() === v) { td.click(); return true; }
            }
        }
        return false;
    """, li, valor)
    r["coincidencia_exacta_clickeada"] = exacta
    pausa(1.5)
    inp = campo_col1(driver, n)
    r["valor_final"] = inp.get_attribute("value")
    print(f"  [{nombre}] pedido '{valor}' → exacta={exacta} valor final='{r['valor_final']}' "
          f"({len(filas)} filas en la lista)")
    return r


def seccion_b_desplegables(driver):
    R = REPORT.setdefault("b_desplegables", {})
    R["location"] = probar_desplegable(driver, 1, "location", D_LOCATION)
    R["service"] = probar_desplegable(driver, 2, "service", D_SERVICE)
    R["supplier"] = probar_desplegable(driver, 3, "supplier", D_SUPPLIER, prefijo=D_SUPPLIER[:5])

    # Code: escribir, salir del campo y ver qué se llena (Description / Comment)
    dlg = dialog_subs(driver)
    cod = dlg.find_element(By.CSS_SELECTOR, "#optionCode > tp-validator > input")
    set_val_con_blur(driver, cod, D_CODIGO)
    pausa(3)
    esperar_fin_carga(driver)
    dlg = dialog_subs(driver)
    R["code"] = {
        "valor_final": dlg.find_element(By.CSS_SELECTOR, "#optionCode > tp-validator > input").get_attribute("value"),
        "inventario_tras_code": inventario(driver, dlg),
    }
    dump_html("b_tras_code", driver.execute_script("return arguments[0].outerHTML;", dlg))
    ss(driver, "b_tras_code")
    print(f"  [code] valor final='{R['code']['valor_final']}'")


# ── (d) Fechas ──────────────────────────────────────────────────────────────

def leer_campo_fecha(driver, campo_id):
    dlg = dialog_subs(driver)
    return driver.execute_script("""
        var cont = arguments[0].querySelector('#' + arguments[1]);
        if (!cont) return null;
        var inputs = Array.from(cont.querySelectorAll('input'));
        var invalid = Array.from(arguments[0].querySelectorAll('[aria-label],[title],[alt]'))
            .filter(function(e){ return /invalid/i.test((e.getAttribute('aria-label')||'') + (e.title||'') + (e.alt||'')); })
            .map(function(e){ return e.tagName + '#' + e.id + '.' + e.className + ' → ' +
                (e.getAttribute('aria-label')||e.title||e.alt); });
        var textoInvalid = Array.from(cont.closest('li').querySelectorAll('*'))
            .filter(function(e){ return e.children.length === 0 && /invalid/i.test(e.textContent||''); })
            .map(function(e){ return e.tagName + '.' + e.className + ' → ' + e.textContent.trim(); });
        return {
            inputs: inputs.map(function(i, k){ return {
                idx: k + 1, tipo: i.type, valor: i.value, clase: i.className,
                aria_label: i.getAttribute('aria-label'), aria_invalid: i.getAttribute('aria-invalid'),
                title: i.title || null, placeholder: i.placeholder || null,
                visible: !!(i.offsetWidth || i.offsetHeight)}; }),
            clase_contenedor: cont.className,
            elementos_con_invalid_en_atributo: invalid,
            elementos_con_invalid_en_texto: textoInvalid
        };
    """, dlg, campo_id)


def probar_fecha(driver, campo_id, texto):
    dlg = dialog_subs(driver)
    inp = dlg.find_element(By.CSS_SELECTOR, f"#{campo_id} > div > input:nth-of-type(2)")
    antes = inp.get_attribute("value")
    set_val_con_blur(driver, inp, texto)
    pausa(1.5)
    estado = leer_campo_fecha(driver, campo_id)
    print(f"  [{campo_id}] '{texto}' (antes '{antes}') → '{[i['valor'] for i in (estado or {}).get('inputs', [])]}'")
    return {"escrito": texto, "valor_antes": antes, "estado": estado}


def seccion_d_fechas(driver):
    R = REPORT.setdefault("d_fechas", {"pruebas": []})
    for campo, texto in (
        ("serviceDateTo", "31/12/49"),
        ("serviceDateTo", "31/12/2049"),
        ("serviceDateFrom", "01/01/20"),
        ("serviceDateTo", "31/12/49"),
        ("serviceDateFrom", D_DESDE),
        ("serviceDateTo", D_HASTA),   # queda el caso controlado para (c)
    ):
        try:
            R["pruebas"].append({"campo": campo, **probar_fecha(driver, campo, texto)})
        except Exception as e:
            R["pruebas"].append({"campo": campo, "escrito": texto, "error": str(e)})
    dump_html("d_fechas", driver.execute_script("return arguments[0].outerHTML;", dialog_subs(driver)))
    ss(driver, "d_fechas")


# ── (e) Controles de Filter y Analysis ──────────────────────────────────────

def abrir_y_listar_estado(driver, li_idx_etiqueta):
    """Abre el desplegable de un campo (ícono <i> del <li>, o el input) y
    devuelve las filas visibles; después lo cierra clickeando el diálogo
    (como en la grabación) y confirma que el valor del campo no cambió."""
    dlg = dialog_subs(driver)
    res = driver.execute_script("""
        var raiz = arguments[0], etiqueta = arguments[1];
        function vis(e){ return !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length); }
        var li = Array.from(raiz.querySelectorAll('li')).find(function(l){
            if (!vis(l)) return false;
            var lab = l.querySelector('label');
            return lab && (lab.innerText || '').trim() === etiqueta; });
        if (!li) return null;
        var ctl = li.querySelector('input');
        var antes = ctl ? ctl.value : null;
        var icono = li.querySelector('i') || ctl;
        icono.click();
        return {antes: antes};
    """, dlg, li_idx_etiqueta)
    if res is None:
        return None
    pausa(1.2)
    dlg = dialog_subs(driver)
    filas = driver.execute_script("""
        var raiz = arguments[0], etiqueta = arguments[1];
        function vis(e){ return !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length); }
        var li = Array.from(raiz.querySelectorAll('li')).find(function(l){
            var lab = l.querySelector('label');
            return lab && (lab.innerText || '').trim() === etiqueta; });
        return Array.from(li.querySelectorAll('tr')).filter(vis).slice(0, 60).map(function(tr){
            return Array.from(tr.querySelectorAll('td')).map(function(td){
                return (td.innerText || '').trim().slice(0, 60); }); });
    """, dlg, li_idx_etiqueta)
    driver.execute_script(
        "var s = arguments[0].querySelector('tp-substitute-service'); if (s) s.click();", dlg)
    pausa(0.8)
    despues = driver.execute_script("""
        var etiqueta = arguments[1];
        var li = Array.from(arguments[0].querySelectorAll('li')).find(function(l){
            var lab = l.querySelector('label');
            return lab && (lab.innerText || '').trim() === etiqueta; });
        var c = li && li.querySelector('input');
        return c ? c.value : null;
    """, dialog_subs(driver), li_idx_etiqueta)
    return {"valor_antes": res["antes"], "valor_despues": despues, "opciones": filas}


def seccion_e_controles(driver):
    R = REPORT.setdefault("e_controles", {})
    dlg = dialog_subs(driver)
    R["tabs"] = driver.execute_script("""
        return Array.from(document.querySelectorAll('[id^="tptablabel-"]')).map(function(e){
            return {id: e.id, texto: (e.innerText || '').trim(),
                    visible: !!(e.offsetWidth || e.offsetHeight)}; });
    """)
    R["filter_inventario"] = inventario(driver, dlg)
    dump_html("e_filter_tab", outer(driver, "#filterTab") or driver.execute_script(
        "return arguments[0].outerHTML;", dlg))

    # Desplegables de estado/agente/etc. (solo lectura de opciones).
    R["desplegables"] = {}
    for item in R["filter_inventario"]:
        etiqueta = item["etiqueta"]
        if re.search(r"status|agent|branch|department", etiqueta, re.I):
            try:
                R["desplegables"][etiqueta] = abrir_y_listar_estado(driver, etiqueta)
                ss(driver, f"e_desplegable_{etiqueta}")
            except Exception as e:
                R["desplegables"][etiqueta] = {"error": str(e)}
    for etiqueta, v in R["desplegables"].items():
        n = len((v or {}).get("opciones") or [])
        print(f"  [{etiqueta}] {n} opciones leídas")

    # Analysis
    try:
        tab = driver.find_element(By.CSS_SELECTOR, "#tptablabel-bookinganalysis")
        jc(driver, tab)
        pausa(2)
        dlg = dialog_subs(driver)
        R["analysis_inventario"] = inventario(driver, dlg)
        dump_html("e_analysis_tab", driver.execute_script("return arguments[0].outerHTML;", dlg))
        ss(driver, "e_analysis_tab")
    except Exception as e:
        R["analysis_error"] = str(e)


# ── (c) Results ─────────────────────────────────────────────────────────────

def seccion_c_results(driver):
    R = REPORT.setdefault("c_results", {})
    for css in ("#tptablabel-bookinganalysis", "#tptablabel-resultsTab"):
        jc(driver, driver.find_element(By.CSS_SELECTOR, css))
        pausa(2)
    esperar_fin_carga(driver, timeout=30)

    fin = time.time() + 30 * VELOCIDAD
    while time.time() < fin:
        if driver.find_elements(By.CSS_SELECTOR, "#resultsTab table tbody tr"):
            break
        time.sleep(0.5)
    pausa(1)
    ss(driver, "c_results")
    dump_html("c_resultsTab", outer(driver, "#resultsTab") or driver.page_source)

    R["grid"] = driver.execute_script("""
        var tb = document.querySelector('#resultsTab table');
        if (!tb) return null;
        var ths = Array.from(tb.querySelectorAll('thead th')).map(function(th, i){
            return {pos: i + 1, clase: th.className, texto: (th.innerText || '').trim(),
                    tiene_checkbox: !!th.querySelector('input[type=checkbox], label')}; });
        var filas = Array.from(tb.querySelectorAll('tbody tr'));
        var muestra = filas.slice(0, 3).map(function(tr){
            return Array.from(tr.children).map(function(td, i){
                return {pos: i + 1, clase: td.className, texto: (td.innerText || '').trim().slice(0, 120)}; }); });
        // contenedor con scroll interno (para detectar virtual scroll)
        var scrolls = [], n = tb;
        while (n && n !== document.body) {
            if (n.scrollHeight > n.clientHeight + 5) scrolls.push({
                tag: n.tagName, clase: n.className, scrollHeight: n.scrollHeight, clientHeight: n.clientHeight});
            n = n.parentElement;
        }
        return {n_th: ths.length, th: ths, n_tr_en_dom: filas.length, muestra_filas: muestra,
                contenedores_con_scroll: scrolls};
    """)

    # Contadores Found / Selected y panel derecho (en reposo: sin seleccionar nada)
    R["contadores"] = driver.execute_script("""
        return Array.from(document.querySelectorAll('#resultsTab *')).filter(function(e){
            return e.children.length === 0 && /found|selected/i.test(e.textContent || '');
        }).slice(0, 20).map(function(e){
            return {tag: e.tagName, clase: e.className,
                    texto: (e.textContent || '').trim().slice(0, 60),
                    padre: ((e.parentElement && e.parentElement.innerText) || '').trim().slice(0, 80)}; });
    """)
    R["panel_derecho_inventario"] = inventario(driver, driver.find_element(By.CSS_SELECTOR, "#resultsTab"))
    g = R["grid"] or {}
    print(f"  Results: {g.get('n_th')} columnas th, {g.get('n_tr_en_dom')} filas en DOM, "
          f"contadores: {[c['padre'] for c in R['contadores']][:4]}")
    for c in (g.get("th") or []):
        print(f"    th[{c['pos']}] clase='{c['clase']}' texto='{c['texto']}'")


# ── MAIN ────────────────────────────────────────────────────────────────────

def cerrar_dialogo_con_exit(driver):
    """Cierra tp-substitute-service con EXIT (nunca OK)."""
    try:
        dlg = dialog_subs(driver)
        if dlg is None:
            return
        btn = dlg.find_element(By.CSS_SELECTOR, "tp-button.cancel > button")
        jc(driver, btn)
        pausa(2)
        print("  🚪 Diálogo cerrado con EXIT")
    except Exception as e:
        print(f"  ⚠ No se pudo cerrar con EXIT: {e}")


def main():
    print("=" * 70)
    print(f"  📌 DIAG Substitute Service v{VERSION} ({VERSION_FECHA}) · VELOCIDAD={VELOCIDAD}")
    print(f"  Caso controlado: {D_LOCATION}/{D_SERVICE}/{D_SUPPLIER}/{D_CODIGO}  {D_DESDE}–{D_HASTA}")
    print("  NO ejecuta ninguna sustitución (nunca clickea OK).")
    print("=" * 70)
    if not (USERNAME and PASSWORD):
        raise ValueError("Faltan TOURPLAN_USERNAME / TOURPLAN_PASSWORD.")
    os.makedirs(SS_DIR, exist_ok=True)

    driver = crear_driver()
    sesion_iniciada = False
    try:
        login(driver)
        sesion_iniciada = True

        secciones = (
            ("(a) navegación", seccion_a_navegacion, True),   # si falla, no se sigue
            ("(b) desplegables", seccion_b_desplegables, False),
            ("(d) fechas", seccion_d_fechas, False),
            ("(e) controles Filter/Analysis", seccion_e_controles, False),
            ("(c) Results", seccion_c_results, False),
        )
        for nombre, fn, critica in secciones:
            print(f"\n── {nombre} ──")
            try:
                fn(driver)
            except Exception as e:
                REPORT.setdefault("errores", {})[nombre] = f"{e}"
                print(f"  ❌ {nombre}: {e}")
                print(traceback.format_exc(limit=3))
                ss(driver, f"error_{nombre}")
                dump(driver, f"error_{nombre}")
                if critica:
                    break
            guardar_reporte()
        cerrar_dialogo_con_exit(driver)
    finally:
        REPORT["fin"] = datetime.now().isoformat(timespec="seconds")
        guardar_reporte()
        if sesion_iniciada:
            logout(driver)
        driver.quit()
        print(f"\n🏁 Fin. Salida en: {os.path.abspath(SS_DIR)}")


if __name__ == "__main__":
    main()
