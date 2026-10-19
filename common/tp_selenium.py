"""
tp_selenium.py — Helpers de Selenium/Tourplan compartidos por los scripts de
este repo. Copiados (sin modificar la lógica) de modificar_rate_name_text.py de
Drive-TP-NX-App: crear_driver, login, logout, set_val*, esperar_fin_carga, ss,
dump. Único cambio de fondo: logout() solo lo llama quien tenga sesión (ver
substitute_doble.py), no se hace en el finally si el login falló.

Config por variables de entorno TOURPLAN_* (con default).
"""

import os
import re
import sys
import tempfile
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from common.chrome_bootstrap import find_or_prepare_chrome

VELOCIDAD = float(os.environ.get("TOURPLAN_VELOCIDAD", "1.5"))
USERNAME = os.environ.get("TOURPLAN_USERNAME", "")
PASSWORD = os.environ.get("TOURPLAN_PASSWORD", "")
BASE_URL = os.environ.get("TOURPLAN_BASE_URL", "https://tourplannx.eurotur.com.ar/tourplannx").rstrip("/")
SS_BASE = os.environ.get("TOURPLAN_SS_DIR", "salida")
SS_DIR = SS_BASE   # carpeta de salida vigente; preparar_carpeta_corrida() la reemplaza por una subcarpeta
HEADLESS = os.environ.get("TOURPLAN_HEADLESS", "0").strip() in ("1", "true", "True")


class _Tee:
    """Duplica lo que se imprime: sigue yendo a la consola (y a la app) y además a consola.log."""

    def __init__(self, principal, archivo):
        self._principal, self._archivo = principal, archivo

    def write(self, texto):
        self._principal.write(texto)
        try:
            self._archivo.write(texto)
            self._archivo.flush()
        except Exception:
            pass
        return len(texto)

    def flush(self):
        self._principal.flush()

    def __getattr__(self, nombre):
        return getattr(self._principal, nombre)


def preparar_carpeta_corrida(prefijo):
    """Crea SS_BASE/<prefijo>_<AAAAMMDD-HHMMSS>/ y la usa para capturas, HTML, JSON y
    consola.log de esta corrida. Devuelve (carpeta, id_de_corrida)."""
    global SS_DIR
    corrida = time.strftime("%Y%m%d-%H%M%S")
    SS_DIR = os.path.join(SS_BASE, f"{prefijo}_{corrida}")
    os.makedirs(SS_DIR, exist_ok=True)
    log = open(os.path.join(SS_DIR, "consola.log"), "a", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, log)
    sys.stderr = _Tee(sys.stderr, log)
    return SS_DIR, corrida


def pausa(seg):
    time.sleep(seg * VELOCIDAD)


def jc(driver, el):
    """Click vía JavaScript — único método confiable en Angular."""
    driver.execute_script("arguments[0].click();", el)


def crear_driver():
    chromium_bin, ver_chrome = find_or_prepare_chrome()
    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1730,1012")
    opts.add_argument("--disable-gpu")
    opts.add_argument(f"--user-data-dir={tempfile.mkdtemp(prefix='tourplan_chrome_profile_')}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.binary_location = chromium_bin
    print(f"Chrome binary: {chromium_bin}  ({ver_chrome})")
    log_path = os.path.join(tempfile.gettempdir(), "chromedriver.log")
    try:
        drv_path = ChromeDriverManager().install()
        d = webdriver.Chrome(service=Service(executable_path=drv_path, log_output=log_path), options=opts)
        print("✅ Driver iniciado")
        return d
    except Exception as e:
        if os.path.exists(log_path):
            with open(log_path) as f:
                print(f"\n--- ChromeDriver log ---\n{f.read()[-3000:]}\n---")
        raise RuntimeError(f"No se pudo iniciar Chrome.\nError: {e}")


_ss_n = [0]


def ss(driver, nombre, forzar=False):
    """Captura de diagnóstico solo si hay una excepción en curso (o forzar)."""
    if not forzar and sys.exc_info()[0] is None:
        return None
    _ss_n[0] += 1
    nombre = re.sub(r"[^A-Za-z0-9_-]+", "_", nombre)[:60]
    os.makedirs(SS_DIR, exist_ok=True)
    p = f"{SS_DIR}/{_ss_n[0]:03d}_{nombre}_{int(time.time())}.png"
    try:
        driver.save_screenshot(p)
        print(f"  📸 {os.path.basename(p)}")
        return p
    except Exception as e:
        print(f"  ⚠️ Error guardando captura ({e})")
        return None


def dump_html(nombre, html):
    os.makedirs(SS_DIR, exist_ok=True)
    nombre = re.sub(r"[^A-Za-z0-9_-]+", "_", nombre)[:60]
    p = f"{SS_DIR}/{nombre}_{int(time.time())}.html"
    with open(p, "w", encoding="utf-8") as f:
        f.write(html or "")
    print(f"  💾 HTML: {p}")


def dump(driver, nombre):
    """Vuelca el HTML de la página. Nunca lanza: se usa dentro de manejadores de error, y si el
    navegador se cayó no debe romper el manejo del error ni frenar el resto del lote."""
    try:
        dump_html(nombre, driver.page_source)
    except Exception as e:
        print(f"  ⚠ No se pudo guardar el HTML ({e})")


def set_val(driver, el, value):
    driver.execute_script("""
        var inp = arguments[0], val = arguments[1];
        var setter = Object.getOwnPropertyDescriptor(
            inp.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
                                       : window.HTMLInputElement.prototype, 'value').set;
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
            inp.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
                                       : window.HTMLInputElement.prototype, 'value').set;
        setter.call(inp, val);
        inp.dispatchEvent(new Event('input',  {bubbles:true}));
        inp.dispatchEvent(new Event('change', {bubbles:true}));
        inp.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));
        inp.blur();
        inp.dispatchEvent(new Event('blur',     {bubbles:true}));
        inp.dispatchEvent(new Event('focusout', {bubbles:true}));
    """, el, value)


JS_CARGANDO = r"""
    return Array.from(document.querySelectorAll('dialog[open]')).some(function(d){
        // Los diálogos de la propia pantalla (Substitute Service, etc.) son <dialog open> dentro
        // de <tp-dialog> y están abiertos todo el tiempo: no cuentan como "cargando".
        return !d.closest('tp-dialog') || /please\s*wait/i.test(d.innerText || '');
    });
"""


def esperar_fin_carga(driver, timeout=15):
    """Espera a que cierre el <dialog> de carga ('PLEASE WAIT...'). Ignora los
    <dialog open> que viven dentro de un <tp-dialog> (la pantalla abierta), salvo
    que su texto sea PLEASE WAIT."""
    fin = time.time() + timeout * VELOCIDAD
    while time.time() < fin:
        if not driver.execute_script(JS_CARGANDO):
            return True
        time.sleep(0.3)
    print("    ⚠ El dialog de carga ('PLEASE WAIT...') seguía abierto tras esperar")
    return False


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
        ss(driver, "login_sin_campos", forzar=True)
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
    """Cierra ventanas secundarias y hace logout real (las licencias de
    Tourplan son limitadas: una sesión colgada bloquea a otra persona)."""
    print("\n🔒 Finalización: logout...")
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
        print("  🔓 Logout OK (volvió al login)" if en_login else "  ⚠ Logout no confirmado")
    except Exception as e:
        print(f"  ⚠ Error en logout: {e}")
