"""
app.py — App local (Streamlit) de la doble sustitución de costos en Tourplan NX.

Corre en la propia PC (igual que las otras apps del equipo): lanza scripts/substitute_doble/substitute_doble.py
como subproceso, muestra su consola en vivo y permite abortar. Antes de cada combinación (modo aplicar) el
script se detiene y la app muestra la pregunta con dos botones: Continuar / Cancelar.

La lógica sin interfaz (validación, entorno del subproceso, estados de la cola, confirmación) está en
common/ejecucion.py, que tiene sus propias pruebas.

Correr:  streamlit run app.py   (en Windows: doble clic en run_app.bat)
"""

import html
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

from common import ejecucion as E
from common import user_config
from common.sheets_client import cargar_sheet, conectar_sheets

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT = Path(os.environ.get("SUBSTITUTE_SCRIPT") or REPO_ROOT / "scripts" / "substitute_doble" / "substitute_doble.py")
# SUBSTITUTE_SCRIPT solo existe para probar la app contra un script falso; en el uso normal no se define.
SALIDA = REPO_ROOT / "salida"
PRODUCCION_URL = "https://tourplannx.eurotur.com.ar/tourplannx"
CLAVE_SHEET = "substitute_doble"      # clave de la URL del Sheet en ~/.tourplan-nx-app/config.json
MODOS = {"Solo revisar (lectura)": "lectura", "Aplicar cambios (modifica Tourplan)": "aplicar"}


# ── Estado de la corrida (vive en la sesión de Streamlit) ───────────────────

def _estado():
    if "_corrida" not in st.session_state:
        st.session_state["_corrida"] = {
            "running": False, "finished": False, "log_lines": [], "returncode": None, "proc": None,
            "stop_file": None, "run_dir": None, "abort_pedido": False, "modo": None, "inicio": None,
            "mensaje": None, "conteo": None, "conteo_error": None,
        }
    return st.session_state["_corrida"]


def _leer_proceso(proc, estado):
    """Hilo aparte: lee el stdout del subproceso sin bloquear a Streamlit (así Abortar y los botones de
    confirmación reaccionan mientras el script sigue corriendo)."""
    for linea in proc.stdout:
        estado["log_lines"].append(linea)
    proc.wait()
    estado["returncode"] = proc.returncode
    estado["finished"] = True


def _refrescar_conteo(estado, sheet_url, hoja="SUBSTITUTE"):
    try:
        ws = conectar_sheets(sheet_url, hoja, user_config.CREDENTIALS_PATH, user_config.TOKEN_PATH)
        filas, _ = cargar_sheet(ws)
        estado["conteo"] = E.contar_estados(filas)
        estado["conteo_error"] = None
    except Exception as e:      # un fallo de refresco no debe romper la app ni una corrida en curso
        estado["conteo_error"] = str(e)


def _carpeta_de_salida(estado):
    """La subcarpeta de esta corrida (el script imprime 'salida ...: <ruta>' al arrancar)."""
    for linea in estado["log_lines"][:40]:
        m = re.search(r"salida \(capturas, HTML, JSON y consola\.log\): (.+)", linea)
        if m:
            return Path(m.group(1).strip())
    return None


# ── Presentación ────────────────────────────────────────────────────────────

CSS = """
<style>
/* Consola de altura fija; con column-reverse queda anclada a la última línea (mismo recurso que la otra app). */
.sd-consola {
    display: flex; flex-direction: column-reverse; overflow-y: auto; height: 340px;
    background: #F0F2F6; color: #31333F; border: 1px solid #D5D9E2; border-radius: 6px; padding: 8px 12px;
    font-family: "Source Code Pro", "SFMono-Regular", Consolas, "Courier New", monospace; font-size: 12.5px; line-height: 21px;
}
.sd-linea { white-space: pre-wrap; word-break: break-word; }
.sd-vacia { color: #6b7280; }
/* Sin el hueco de arriba: se esconden las partes de la barra nativa que no se usan (Deploy, menú) y se le saca
   el alto y el relleno superior (mismo criterio que la otra app). */
[data-testid="stToolbarActions"], [data-testid="stAppDeployButton"], [data-testid="stMainMenu"],
[data-testid="stDecoration"] { display: none !important; }
header[data-testid="stHeader"], [data-testid="stToolbar"] { background: transparent !important; height: auto !important;
    min-height: 0 !important; }
.block-container, [data-testid="stMainBlockContainer"], [data-testid="stMain"],
[data-testid="stAppViewContainer"] > section { padding-top: 0 !important; }
.block-container, [data-testid="stMainBlockContainer"] { padding-bottom: 1rem !important; }
[data-testid="stElementContainer"]:has(style) { display: none !important; }   /* este bloque <style> no deja renglón vacío */
.sd-subtitulo { font-size: 0.95rem; color: #6b7280; margin-top: -0.9rem; margin-bottom: 0.6rem; }
</style>
"""


def html_consola(lineas):
    """HTML de la consola: cada línea escapada, en orden inverso dentro de un contenedor column-reverse (la última
    línea queda abajo y visible). Todo en una sola línea para que Markdown no lo parta."""
    if lineas:
        cuerpo = "".join(f'<div class="sd-linea">{html.escape(l.rstrip(chr(10)))}</div>' for l in reversed(lineas[-500:]))
    else:
        cuerpo = '<div class="sd-linea sd-vacia">Sin salida todavía. Acá aparece la consola y sigue la última línea.</div>'
    return f'<div class="sd-consola">{cuerpo}</div>'


def _sheet_url_guardada():
    return user_config.cargar().get("sheet_urls", {}).get(CLAVE_SHEET, "")


# ── Configuración (una vez por PC) ──────────────────────────────────────────

def render_configuracion():
    cfg = user_config.cargar()
    with st.form("form_config"):
        st.caption("Se guarda en esta computadora (carpeta del usuario), no en el repositorio. Es la misma configuración "
                   "que usa la otra app de Tourplan: si ya la cargaste ahí, ya está acá.")
        c1, c2 = st.columns(2)
        usuario = c1.text_input("Usuario Tourplan", value=cfg.get("tp_usuario", ""))
        password = c2.text_input("Password Tourplan", value=cfg.get("tp_password", ""), type="password")
        sheet = st.text_input("URL del Google Sheet de esta herramienta", value=cfg.get("sheet_urls", {}).get(CLAVE_SHEET, ""))
        headless = st.checkbox("Correr sin ventana de Chrome visible (no recomendado: no vas a poder mirar el modal)",
                               value=bool(cfg.get("headless", False)))
        if st.form_submit_button("Guardar", type="primary"):
            cfg.update({"tp_usuario": usuario, "tp_password": password, "headless": headless})
            cfg.setdefault("sheet_urls", {})[CLAVE_SHEET] = sheet
            user_config.guardar(cfg)
            st.success("Configuración guardada.")
            st.rerun()


# ── Parámetros ──────────────────────────────────────────────────────────────

def render_parametros(estado):
    cfg = user_config.cargar()
    bloqueado = estado["running"]
    hoy = date.today()

    modo_label = st.radio("Modo", list(MODOS), horizontal=True, disabled=bloqueado, key="modo",
                          help="Solo revisar: lee Results y escribe el detalle en el Sheet; nunca hace click en OK. "
                               "Aplicar: hace la sustitución real (ida y vuelta) y MODIFICA reservas.")
    modo = MODOS[modo_label]

    c1, c2, c3 = st.columns(3)
    desde = c1.date_input("Servicios desde", value=hoy + timedelta(days=1), min_value=date(2000, 1, 1),
                          max_value=date(2099, 12, 31), format="DD/MM/YYYY", disabled=bloqueado, key="desde",
                          help="Obligatoria: se ingresa en cada corrida.")
    hasta = c2.date_input("Servicios hasta", value=date(2049, 12, 31), min_value=date(2000, 1, 1),
                          max_value=date(2099, 12, 31), format="DD/MM/YYYY", disabled=bloqueado, key="hasta",
                          help="Por defecto 31/12/2049: abarca todos los servicios desde la fecha de inicio.")
    max_reservas = c3.number_input("Tope de reservas por combinación", min_value=1, value=2000, step=100,
                                   disabled=bloqueado, key="max_reservas",
                                   help="Si una combinación tiene más reservas, termina en ERROR sin leer ni tocar nada.")

    sheet_url = _sheet_url_guardada()                              # se carga solo en ⚙️ Configuración
    c4, c5 = st.columns([3, 1])
    base_url = c4.text_input("URL de Tourplan", value=PRODUCCION_URL, disabled=bloqueado, key="base_url",
                             help="Viene con producción. Cambiala solo si querés probar a propósito contra otro ambiente.")
    fila = c5.number_input("Solo la fila nº (0 = todas)", min_value=0, value=0, step=1, disabled=bloqueado, key="fila",
                           help="Para probar una combinación puntual del Sheet.")

    confirmo, confirmar_ok, panel_max = False, True, 5
    if modo == "aplicar":
        st.warning("⚠️ **Modo aplicar: esta corrida MODIFICA reservas reales en Tourplan.** Procesa combinaciones con un único "
                   "estado de servicio y hasta 5 reservas con los mismos datos de pickup/drop-off.")
        confirmo = st.checkbox("Revisé a mano (reporte de Tourplan) que no hay códigos 000000 pendientes de sustituir",
                               disabled=bloqueado, key="confirmo")
        confirmar_ok = st.checkbox("Pedir mi confirmación antes de cada combinación (la vuelta se hace sola después de la ida; recomendado en las primeras pruebas)",
                                   value=True, disabled=bloqueado, key="confirmar_ok")
        with st.expander("Opciones avanzadas"):
            panel_max = st.number_input("Máximo de reservas por combinación en esta fase", min_value=1, max_value=20, value=5,
                                        disabled=bloqueado, key="panel_max")

    usuario, password = user_config.tp_credenciales_default()
    return {"modo": modo, "desde": desde, "hasta": hasta, "max_reservas": int(max_reservas), "sheet_url": sheet_url,
            "base_url": base_url, "fila": int(fila), "confirmo_sin_pendientes": confirmo, "confirmar_ok": confirmar_ok,
            "panel_max": int(panel_max), "usuario": usuario, "password": password}


def lanzar(estado, p):
    run_dir = Path(tempfile.mkdtemp(prefix="substitute_"))
    stop_file = run_dir / "ABORTAR.flag"
    SALIDA.mkdir(exist_ok=True)
    env = E.armar_entorno(p, os.environ.copy(), REPO_ROOT, run_dir, SALIDA, stop_file,
                          user_config.CREDENTIALS_PATH, user_config.TOKEN_PATH, headless=user_config.headless_default())
    proc = subprocess.Popen([sys.executable, str(SCRIPT)], cwd=str(REPO_ROOT), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", bufsize=1)
    estado.update({"running": True, "finished": False, "log_lines": [], "returncode": None, "proc": proc,
                   "stop_file": stop_file, "run_dir": run_dir, "abort_pedido": False, "modo": p["modo"],
                   "inicio": time.time(), "mensaje": None})
    threading.Thread(target=_leer_proceso, args=(proc, estado), daemon=True).start()


# ── Corrida en vivo ─────────────────────────────────────────────────────────

def _mensaje_final(rc, modo):
    if rc == 0:
        return ("success", "Terminó bien. Revisá el resultado en el Sheet (columnas ESTADO y OBSERVACIONES, y la hoja "
                           "SUBSTITUTE_DETALLE).")
    if rc == E.EXIT_ABORTADO:
        return ("info", "⏸️ Abortada. Las combinaciones que no llegó a procesar siguen en PENDIENTE; una combinación en curso "
                        "termina completa antes de cortar.")
    if rc == E.EXIT_DETENIDA:
        return ("error", "🛑 **Corrida detenida:** puede haber reservas en un provisorio (sustitutos sin asignar). No se procesó "
                         "ninguna otra combinación. Mirá SUBSTITUTE_DETALLE (FASE IDA_EN_CURSO / IDA / VUELTA) y "
                         "docs/fase2_riesgos.md antes de volver a correr.")
    return ("error", f"El proceso terminó con error (código {rc}). Revisá la consola.")


def render_en_vivo(estado):
    """Panel de la corrida; se refresca solo mientras corre."""
    if estado["running"] and estado["finished"]:
        estado["running"] = False
        estado["mensaje"] = _mensaje_final(estado["returncode"], estado["modo"])
        _refrescar_conteo(estado, _sheet_url_guardada())      # la cola está arriba: que refleje el resultado
        st.rerun()

    if estado["running"]:
        minutos = int((time.time() - (estado["inicio"] or time.time())) // 60)
        st.info(f"🟢 En curso ({'APLICAR' if estado['modo'] == 'aplicar' else 'lectura'}) · {minutos} min · "
                "no toques la ventana de Chrome.")
        pregunta = E.confirmacion_pendiente(estado["run_dir"]) if estado["run_dir"] else None
        if pregunta:
            st.warning("⏸️ **El script espera tu confirmación antes de mover las reservas en Tourplan (ida; la vuelta va sola).**")
            st.code(pregunta, language=None)
            carpeta = _carpeta_de_salida(estado)
            if carpeta and carpeta.exists():
                imagenes = sorted(carpeta.glob("modal_*lleno*.png"), key=lambda p: p.stat().st_mtime)
                if imagenes:
                    st.image(str(imagenes[-1]), caption="Modal tal como quedó (también lo ves en la ventana de Chrome)")
            c1, c2 = st.columns(2)
            # El panel se refresca solo cada segundo: tras responder, el script borra la pregunta y desaparece.
            if c1.button("✅ Continuar (hacer click en OK de la ida)", type="primary", key="conf_si", use_container_width=True):
                E.responder_confirmacion(estado["run_dir"], True)
                st.success("Respuesta enviada: el script hace el OK de la ida y sigue con la vuelta.")
            if c2.button("❌ Cancelar", key="conf_no", use_container_width=True):
                E.responder_confirmacion(estado["run_dir"], False)
                st.info("Respuesta enviada: se cancela esta combinación (no se mueve nada).")

    c1, c2 = st.columns([4, 1])
    c1.markdown("**Consola**" + ("" if estado["running"] else " &nbsp; En espera"), unsafe_allow_html=True)
    c2.download_button("Descargar", data="".join(estado["log_lines"]), file_name="consola.txt", mime="text/plain",
                       key="descargar_consola", use_container_width=True)
    st.markdown(html_consola(estado["log_lines"]), unsafe_allow_html=True)
    carpeta = _carpeta_de_salida(estado)
    if carpeta:
        st.caption(f"📁 Capturas, HTML y consola.log de esta corrida: `{carpeta}`")
    if estado["mensaje"]:
        getattr(st, estado["mensaje"][0])(estado["mensaje"][1])


# ── Estado de la cola ───────────────────────────────────────────────────────

def render_cola(estado, sheet_url):
    c1, c2 = st.columns([4, 1])
    c1.subheader("Cola de trabajo")
    if c2.button("🔄 Refrescar", disabled=estado["running"] or not sheet_url, use_container_width=True, key="refrescar"):
        _refrescar_conteo(estado, sheet_url)
    if estado["conteo_error"]:
        st.error(f"No pude leer el Sheet: {estado['conteo_error']}")
    elif estado["conteo"]:
        cols = st.columns(5)
        for col, (nombre, n) in zip(cols, estado["conteo"].items()):
            col.metric(nombre, n)
    else:
        st.caption("Tocá Refrescar para ver cuántas filas hay Pendientes, OK o con Error.")


# ── Historial de corridas ───────────────────────────────────────────────────

HISTORIAL_MAX = 15


def _historial_corridas(salida=None, limite=HISTORIAL_MAX):
    """Las últimas corridas guardadas en salida/<modo>_<AAAAMMDD-HHMMSS>/ que tienen consola.log, de más nueva a más vieja."""
    salida = Path(salida or SALIDA)
    if not salida.is_dir():
        return []
    corridas = []
    for carpeta in salida.iterdir():
        m = re.fullmatch(r"(lectura|aplicar)_(\d{8})-(\d{6})", carpeta.name)
        log = carpeta / "consola.log"
        if not (m and carpeta.is_dir() and log.is_file()):
            continue
        try:
            inicio = datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S")
        except ValueError:
            continue
        corridas.append({"modo": m.group(1), "inicio": inicio, "carpeta": carpeta, "log": log})
    corridas.sort(key=lambda c: c["inicio"], reverse=True)
    return corridas[:limite]


def _cierre_de_log(texto):
    """Lo que el script imprime al terminar ('Duración: ... filas OK=.. ERROR=..'), o None si la corrida no llegó al final."""
    m = re.search(r"🏁 Fin\.\s*(.+)", texto)
    return m.group(1).strip() if m else None


def render_historial(estado):
    en_curso = _carpeta_de_salida(estado) if estado["running"] else None
    with st.expander("🕘 Historial de corridas"):
        corridas = _historial_corridas()
        if not corridas:
            st.caption("Todavía no hay corridas guardadas en la carpeta salida.")
        for c in corridas:
            texto = c["log"].read_text(encoding="utf-8", errors="replace")
            actual = en_curso is not None and Path(en_curso).name == c["carpeta"].name
            cierre = "en curso" if actual else (_cierre_de_log(texto) or "sin cierre (se interrumpió o no llegó al final)")
            col_fecha, col_modo, col_res, col_desc = st.columns([2.2, 1.2, 6, 1.6])
            col_fecha.write(c["inicio"].strftime("%d/%m/%Y %H:%M:%S"))
            col_modo.write(c["modo"])
            col_res.write(cierre)
            col_desc.download_button("Descargar", data=texto, file_name=f"{c['carpeta'].name}.log", mime="text/plain",
                                     icon=":material/download:", key=f"hist_{c['carpeta'].name}", use_container_width=True)


# ── Página ──────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="Actualización de costos", page_icon="🔁", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    estado = _estado()

    st.title("Actualización de costos")
    st.markdown('<div class="sd-subtitulo">mediante sustitución de servicios</div>', unsafe_allow_html=True)

    render_cola(estado, _sheet_url_guardada())

    with st.expander("⚙️ Configuración (usuario de Tourplan, Sheet)", expanded=not user_config.tp_credenciales_default()[0]):
        render_configuracion()

    # Los parámetros se pueden plegar; al arrancar una corrida se pliegan solos para dejar lugar a la consola.
    with st.expander("Parámetros", expanded=not estado["running"]):
        p = render_parametros(estado)

    errores = E.validar_parametros(p)
    for aviso in E.advertencias(p):
        st.caption(f"⚠️ {aviso}")
    if errores and not estado["running"]:
        for e in errores:
            st.caption(f"▫️ {e}")

    c1, c2, _ = st.columns([1, 1, 3])
    ejecutar = c1.button("▶ Ejecutar", type="primary", disabled=estado["running"] or bool(errores), use_container_width=True)
    abortar = c2.button("⏹ Abortar", disabled=not estado["running"] or estado["abort_pedido"], use_container_width=True,
                        help="Corta al terminar la combinación en curso y hace logout de Tourplan.")
    if ejecutar:
        lanzar(estado, p)
        st.rerun()
    if abortar:
        estado["abort_pedido"] = True
        try:
            estado["stop_file"].touch()
        except Exception:
            pass
    if estado["abort_pedido"] and estado["running"]:
        st.warning("⏸️ Abortando: termina la combinación en curso (completa), hace logout y corta.")

    run_every = 1 if estado["running"] else None
    st.fragment(run_every=run_every)(render_en_vivo)(estado)
    render_historial(estado)


main()
