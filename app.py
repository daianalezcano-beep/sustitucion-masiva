"""
app.py — App local (Streamlit) de la doble sustitución de costos en Tourplan NX.

Corre en la propia PC (igual que las otras apps del equipo): lanza scripts/substitute_doble/substitute_doble.py
como subproceso, muestra su consola en vivo y permite abortar.

Pantalla (de arriba hacia abajo): encabezado con el engranaje de Configuración, barra de corrida (pegada arriba mientras corre),
cola de trabajo, dos pasos («1 · Ejecutar» y «2 · Comparar»), consola e historial.

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

from common import comparador
from common import logica as L
from common import pre_reporte
from common import reporte_entrada
from common import tipo_reserva as T
from common import ejecucion as E
from common import user_config
from common.sheets_batch import agregar_filas_batch, asegurar_columnas_tolerante
from common.sheets_client import cargar_sheet, conectar_sheets

REPO_ROOT = Path(__file__).resolve().parent
SCRIPT = Path(os.environ.get("SUBSTITUTE_SCRIPT") or REPO_ROOT / "scripts" / "substitute_doble" / "substitute_doble.py")
# SUBSTITUTE_SCRIPT solo existe para probar la app contra un script falso; en el uso normal no se define.
SALIDA = REPO_ROOT / "salida"
PRODUCCION_URL = "https://tourplannx.eurotur.com.ar/tourplannx"
CLAVE_SHEET = "substitute_doble"      # clave de la URL del Sheet en ~/.tourplan-nx-app/config.json
ETIQUETA_MODO = {"lectura": "Solo revisar", "aplicar": "Aplicar cambios"}
COLS_HOJA = ["LOCATION", "SUPPLIER", "SERVICE TYPE", "CODIGO", "TIPO", "ESTADO"]
HISTORIAL_MAX = 15
TIPO_ETIQUETA = {T.TIPO_FITS: "FITS", T.TIPO_GRUPOS: "GRUPOS", T.TIPO_AMBOS: "AMBOS"}
TIPO_DESCRIPCION = {T.TIPO_FITS: "Reservas individuales (por Department).", T.TIPO_GRUPOS: "Reservas de grupo (por Department).",
                    T.TIPO_AMBOS: "Las dos, cada una con su filtro."}
DESCRIPCION_MODO = {"lectura": "Lectura. No modifica nada en Tourplan.", "aplicar": "Modifica reservas reales en Tourplan."}


# ── Estado de la corrida (vive en la sesión de Streamlit) ───────────────────

def _estado():
    if "_corrida" not in st.session_state:
        st.session_state["_corrida"] = {
            "running": False, "finished": False, "log_lines": [], "returncode": None, "proc": None,
            "stop_file": None, "run_dir": None, "abort_pedido": False, "modo": None, "inicio": None,
            "mensaje": None, "conteo": None, "conteo_error": None, "aviso_cerrado": False,
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
        filas = [L.canonizar_fila(f, COLS_HOJA) for f in filas]
        estado["conteo"] = E.contar_estados(filas)
        estado["pend_tipo"] = E.contar_pendientes_por_tipo(filas)
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

def _verificar_pre(p):
    """Antes de aplicar: el PRE tiene que ser un Product Analysis y cubrir las combinaciones PENDIENTES que se van a procesar.
    Devuelve (None, ruta_guardada) si está bien, o (mensaje_de_error, None)."""
    try:
        filas_pre = pre_reporte.leer_pre(p["pre_contenido"])
    except Exception as e:
        return f"El reporte PRE no sirve: {e}", None
    try:
        _, filas = _leer_hoja(p["sheet_url"])
    except Exception as e:
        return f"No pude leer el Sheet para verificar el PRE: {e}", None
    pendientes = [f for f in filas if E.clasificar_estado(f.get("ESTADO")) == "Pendiente"
                  and T.normalizar_tipo(f.get("TIPO")) is not None and T.fila_corresponde(T.normalizar_tipo(f.get("TIPO")), p["tipo_reservas"])
                  and (not p["fila"] or f.get("__row_idx__") == p["fila"])]
    if not pendientes:
        return f"No hay filas PENDIENTES de tipo {p['tipo_reservas']} para procesar (revisá la hoja, el tipo o el número de fila).", None
    faltan = pre_reporte.combinaciones_faltantes(filas_pre, pendientes, datetime.combine(p["desde"], datetime.min.time()),
                                                 datetime.combine(p["hasta"], datetime.min.time()))
    if faltan:
        lista = "; ".join(" / ".join(k) for k in faltan[:10]) + (f"; … (+{len(faltan) - 10})" if len(faltan) > 10 else "")
        return (f"El reporte PRE no cubre {len(faltan)} de las combinaciones PENDIENTES (Location / Supplier / Service Type / Code) "
                f"en las fechas elegidas: {lista}. Exportá el PRE con esos filtros, o sacá esas filas de PENDIENTE."), None
    return None, pre_reporte.guardar_pre(p["pre_contenido"], SALIDA / pre_reporte.CARPETA)

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
                   "stop_file": stop_file, "run_dir": run_dir, "abort_pedido": False, "aviso_cerrado": False, "modo": p["modo"],
                   "inicio": time.time(), "mensaje": None})
    threading.Thread(target=_leer_proceso, args=(proc, estado), daemon=True).start()

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

def _tabla_provisorios():
    """Los provisorios definidos en el script (Location + Service Type -> proveedor). Se importa el script solo para leer esa tabla."""
    ruta = str(REPO_ROOT / "scripts" / "substitute_doble")
    if ruta not in sys.path:
        sys.path.insert(0, ruta)
    import substitute_doble
    return dict(substitute_doble.PROVISORIOS)

def _leer_hoja(sheet_url):
    ws = conectar_sheets(sheet_url, "SUBSTITUTE", user_config.CREDENTIALS_PATH, user_config.TOKEN_PATH)
    filas, columnas = cargar_sheet(ws)
    filas = [L.canonizar_fila(f, COLS_HOJA) for f in filas]
    return ws, filas

def _analizar_reporte(contenido, sheet_url):
    filas = reporte_entrada.leer_reporte(contenido)
    _, en_hoja_filas = _leer_hoja(sheet_url)
    en_hoja = {L.clave_fila(f): f.get("ESTADO", "") for f in en_hoja_filas}
    plan = reporte_entrada.armar_plan(filas, _tabla_provisorios(), en_hoja=en_hoja)
    plan["pendientes_en_hoja"] = sum(1 for f in en_hoja_filas if E.clasificar_estado(f.get("ESTADO")) == "Pendiente")
    plan["conteo"] = E.contar_estados(en_hoja_filas)         # la hoja ya se leyó: de paso se actualiza la cola de trabajo
    plan["pend_tipo"] = E.contar_pendientes_por_tipo(en_hoja_filas)
    return plan

def _cargar_a_la_hoja(plan, sheet_url):
    """Agrega las combinaciones a la hoja SUBSTITUTE como PENDIENTE. Vuelve a leer la hoja justo antes para no duplicar
    lo que otra persona haya cargado mientras tanto. Devuelve la cantidad escrita."""
    ws, filas = _leer_hoja(sheet_url)
    ya = {L.clave_fila(f) for f in filas}
    # una fila de la hoja sin TIPO (anterior a esa columna) cubre a los dos tipos
    nuevas = [f for f in reporte_entrada.filas_para_hoja(plan["a_cargar"])
              if L.clave_fila(f) not in ya and L.clave_fila(f)[:4] + ("",) not in ya]
    asegurar_columnas_tolerante(ws, COLS_HOJA)
    return agregar_filas_batch(ws, ws.row_values(1), nuevas)




# ── Presentación ────────────────────────────────────────────────────────────

CSS = """
<style>
:root {
    --sd-surface:#ffffff; --sd-surface2:#f8fafb; --sd-ink:#1b2430; --sd-muted:#566273; --sd-line:#dce1e7; --sd-line2:#c3cbd5;
    --sd-accent:#1d5f8f; --sd-on-accent:#ffffff; --sd-accent-bg:#e7f0f7; --sd-info-line:#b7d3e6;
    --sd-danger:#b3261e; --sd-danger-solid:#b3261e; --sd-danger-bg:#fdeceb; --sd-danger-line:#f0b9b5;
    --sd-ok:#17663a; --sd-ok-bg:#e5f4ea; --sd-ok-line:#a9d6b8;
    --sd-warn:#7a5200; --sd-warn-bg:#fff3d4; --sd-warn-line:#ecd08a;
    --sd-con-bg:#f0f3f6; --sd-con-ink:#233044;
}
@media (prefers-color-scheme: dark) {
    :root {
        --sd-surface:#171d25; --sd-surface2:#1c232c; --sd-ink:#e6ebf1; --sd-muted:#9ba7b6; --sd-line:#2a323d; --sd-line2:#3a4452;
        --sd-accent:#6db3e4; --sd-on-accent:#0b1a26; --sd-accent-bg:#14283a; --sd-info-line:#2b5272;
        --sd-danger:#ff9a92; --sd-danger-solid:#c8372d; --sd-danger-bg:#3a1917; --sd-danger-line:#7a2e29;
        --sd-ok:#86d6a1; --sd-ok-bg:#15301f; --sd-ok-line:#2d6a45;
        --sd-warn:#f0c870; --sd-warn-bg:#33270b; --sd-warn-line:#6e5416;
        --sd-con-bg:#0c1015; --sd-con-ink:#cbd5e1;
    }
}
/* Sin el hueco de arriba: se esconden las partes de la barra nativa que no se usan (Deploy, menú). */
[data-testid="stToolbarActions"], [data-testid="stAppDeployButton"], [data-testid="stMainMenu"],
[data-testid="stDecoration"] { display: none !important; }
header[data-testid="stHeader"], [data-testid="stToolbar"] { background: transparent !important; height: auto !important;
    min-height: 0 !important; }
.block-container, [data-testid="stMainBlockContainer"], [data-testid="stMain"],
[data-testid="stAppViewContainer"] > section { padding-top: 0 !important; }
/* Espacio al final: si la ventana del navegador queda parcialmente tapada (barra de tareas), todavía se puede bajar hasta lo último. */
.block-container, [data-testid="stMainBlockContainer"] { padding-bottom: 12rem !important; max-width: none !important;
    padding-left: 2.5rem !important; padding-right: 2.5rem !important; }       /* aprovecha todo el ancho de la ventana */
[data-testid="stElementContainer"]:has(style) { display: none !important; }   /* los bloques <style> no dejan renglón vacío */

/* encabezado */
.sd-titulo { font-size: 22px !important; font-weight: 600; line-height: 1.2; margin: 0 !important; }
.sd-subtitulo { font-size: 13px !important; color: var(--sd-muted); margin: 0 !important; }
.sd-chips { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; }
.sd-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border: 1px solid var(--sd-line); border-radius: 99px;
    background: var(--sd-surface); font-size: 12.5px; color: var(--sd-muted); white-space: nowrap; }
.sd-chip.mal { color: var(--sd-danger); background: var(--sd-danger-bg); border-color: var(--sd-danger-line); }
.st-key-btn_config button { width: 44px; height: 44px; border-radius: 10px; font-size: 20px; padding: 0; }

/* insignias */
.sd-badge { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 99px; font-size: 12px; font-weight: 600;
    letter-spacing: .04em; white-space: nowrap; }
.sd-badge.rev, .sd-badge.run { background: var(--sd-accent-bg); color: var(--sd-accent); }
.sd-badge.apl, .sd-badge.mal { background: var(--sd-danger-bg); color: var(--sd-danger); }
.sd-badge.ok { background: var(--sd-ok-bg); color: var(--sd-ok); }
.sd-badge.warn { background: var(--sd-warn-bg); color: var(--sd-warn); }
.sd-badge.idle { background: var(--sd-surface2); color: var(--sd-muted); border: 1px solid var(--sd-line); }

/* barra de corrida: pegada arriba mientras corre o al terminar (se oculta si no hay nada que mostrar) */
div:has(> .st-key-barra_corrida) { position: sticky; top: 8px; z-index: 999; }     /* el envoltorio es el que se pega: su contenedor es toda la página */
div:has(> .st-key-barra_corrida):not(:has(.sd-run)) { display: none; }
.st-key-barra_corrida:has(.sd-run) { background: var(--sd-surface); border: 1.5px solid var(--sd-accent); border-radius: 10px;
    padding: 10px 16px; box-shadow: 0 6px 18px rgba(20,30,45,.18); }
.st-key-barra_corrida:has(.sd-run.apl) { border-color: var(--sd-danger-solid); }
.st-key-barra_corrida:has(.sd-run.ok) { border-color: var(--sd-ok-line); background: var(--sd-ok-bg); }
.st-key-barra_corrida:has(.sd-run.warn) { border-color: var(--sd-warn-line); background: var(--sd-warn-bg); }
.st-key-barra_corrida:has(.sd-run.mal) { border-color: var(--sd-danger-line); background: var(--sd-danger-bg); }
.sd-run { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; font-size: 14px; }
.sd-run .sd-barra { flex: 1; min-width: 140px; }
.sd-pct { font-variant-numeric: tabular-nums; font-weight: 600; min-width: 44px; text-align: right; }
.sd-barra { height: 10px; background: var(--sd-line); border-radius: 99px; overflow: hidden; }
.sd-barra i { display: block; height: 100%; background: var(--sd-accent); border-radius: 99px; }
.sd-barra.apl i { background: var(--sd-danger-solid); }
.st-key-abortar_barra button { border-color: var(--sd-danger-solid); color: var(--sd-danger); font-weight: 600; }

/* cola de trabajo */
.sd-cola { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
.sd-cola > div { padding: 4px 12px; border-left: 3px solid var(--sd-line); display: flex; flex-direction: column; }
.sd-cola .n { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1.15; }
.sd-cola .l { font-size: 12px; color: var(--sd-muted); }
.sd-cola .pend { border-left-color: var(--sd-accent); } .sd-cola .pend .n { color: var(--sd-accent); }
.sd-cola .mal { border-left-color: var(--sd-danger-solid); } .sd-cola .mal .n { color: var(--sd-danger); }
.sd-cola-titulo { font-size: 15px !important; font-weight: 600; margin: 0 !important; }
.sd-nota { font-size: 12px !important; color: var(--sd-muted); margin: 0 !important; }

/* pasos (navegación) */
.st-key-paso { margin-top: 6px; }
.st-key-paso [data-testid="stButtonGroup"] { width: 100%; }
.st-key-paso button { min-height: 56px; flex: 1; justify-content: flex-start; padding: 6px 18px; }

/* tarjetas */
.st-key-tarjeta_lectura, .st-key-tarjeta_aplicar, .st-key-tarjeta_comparar { background: var(--sd-surface); border-radius: 10px; }
.st-key-tarjeta_aplicar { border-color: var(--sd-danger-line) !important; }
.sd-bandera { margin: -16px -16px 14px; padding: 9px 18px; background: var(--sd-danger-bg); color: var(--sd-danger); font-weight: 600;
    font-size: 12.5px; letter-spacing: .04em; border-top: 4px solid var(--sd-danger-solid); border-bottom: 1px solid var(--sd-danger-line);
    border-radius: 9px 9px 0 0; }
.sd-seccion { font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 8px; margin: 14px 0 6px; }
.sd-num { display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; background: var(--sd-accent-bg);
    color: var(--sd-accent); font-size: 12px; font-weight: 600; flex: none; }
.sd-etiqueta { font-size: 12px; font-weight: 600; color: var(--sd-muted); letter-spacing: .04em; text-transform: uppercase; }

/* opciones de modo / origen como tarjetas seleccionables */
.st-key-modo, .st-key-origen { width: 100% !important; }
.st-key-modo [data-testid="stRadioGroup"], .st-key-origen [data-testid="stRadioGroup"] { display: grid !important;
    grid-template-columns: 1fr 1fr; gap: 10px; width: 100%; }
.st-key-modo [data-testid="stRadioGroup"] > div, .st-key-origen [data-testid="stRadioGroup"] > div { border: 1.5px solid var(--sd-line2);
    border-radius: 10px; padding: 10px 14px; background: var(--sd-surface); margin: 0; }
.st-key-modo [data-testid="stRadioGroup"] > div:has(input:checked), .st-key-origen [data-testid="stRadioGroup"] > div:has(input:checked) {
    border-color: var(--sd-accent); background: var(--sd-accent-bg); }
.st-key-tarjeta_aplicar .st-key-modo [data-testid="stRadioGroup"] > div:has(input:checked) { border-color: var(--sd-danger-solid);
    background: var(--sd-danger-bg); }

/* lista de requisitos */
.sd-reqs { list-style: none; margin: 10px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.sd-reqs li { display: flex; gap: 8px; align-items: center; font-size: 13px; }
.sd-reqs .ok { color: var(--sd-ok); } .sd-reqs .no { color: var(--sd-muted); }
.st-key-tarjeta_aplicar .sd-reqs .no { color: var(--sd-danger); }
.sd-falta { color: var(--sd-danger); font-weight: 500; font-size: 13px !important; margin-top: 6px; }
.sd-pista { color: var(--sd-muted); font-size: 12.5px !important; margin-top: 6px; }
.st-key-ejecutar_aplicar button { background: var(--sd-danger-solid); border-color: transparent; color: #fff; font-weight: 600; }
.st-key-ejecutar_aplicar button:disabled { opacity: .45; }
.st-key-confirmo_aplicar { border: 1.5px solid var(--sd-danger-line); background: var(--sd-danger-bg); border-radius: 10px; padding: 12px 14px; margin-top: 10px; }

/* veredicto y tarjetas de la comparación */
.st-key-veredicto_mal, .st-key-veredicto_ok { border: 1px solid; border-radius: 10px; padding: 12px 14px; margin: 8px 0; }
.st-key-veredicto_mal { background: var(--sd-danger-bg); border-color: var(--sd-danger-line); color: var(--sd-danger); }
.st-key-veredicto_ok { background: var(--sd-ok-bg); border-color: var(--sd-ok-line); color: var(--sd-ok); }
.sd-veredicto b { font-size: 15px; } .sd-veredicto span { color: var(--sd-ink); }
.sd-tiles { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; margin: 12px 0; }
.sd-tile { border: 1px solid var(--sd-line); border-radius: 8px; padding: 8px 12px; background: var(--sd-surface2); }
.sd-tile .n { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1.25; }
.sd-tile .l { font-size: 12px; color: var(--sd-muted); }
.sd-tile.mal { border-color: var(--sd-danger-line); background: var(--sd-danger-bg); } .sd-tile.mal .n { color: var(--sd-danger); }
.sd-tile.info .n { color: var(--sd-accent); }
.sd-control { font-size: 12.5px !important; color: var(--sd-muted); margin: 0 0 6px !important; }

/* consola */
.sd-consola {
    display: flex; flex-direction: column-reverse; overflow-y: auto; height: 240px;
    background: var(--sd-con-bg); color: var(--sd-con-ink); border: 1px solid var(--sd-line); border-radius: 8px; padding: 8px 12px;
    font-family: "IBM Plex Mono", "Source Code Pro", "SFMono-Regular", Consolas, "Courier New", monospace; font-size: 12.5px; line-height: 21px;
}
.sd-linea { white-space: pre-wrap; word-break: break-word; }
.sd-vacia { color: var(--sd-muted); }
</style>
"""


def _h(texto):
    return html.escape(str(texto))


def _insignia(texto, clase):
    return f'<span class="sd-badge {clase}">{_h(texto)}</span>'


def _pendientes(estado):
    """Filas PENDIENTE del Sheet que procesa el tipo de reservas elegido (FITS / GRUPOS / AMBOS), según la última lectura de la cola;
    None si todavía no se leyó."""
    return E.pendientes_del_modo(estado.get("pend_tipo"), st.session_state.get("tipo_reservas", T.TIPO_FITS))


# ── Configuración (una vez por PC) ──────────────────────────────────────────

@st.dialog("Configuración")
def dialogo_configuracion():
    cfg = user_config.cargar()
    with st.form("form_config"):
        st.caption("Se configura una sola vez y queda guardada en esta computadora (carpeta del usuario), no en el repositorio. "
                   "Es la misma configuración que usa la otra app de Tourplan: si ya la cargaste ahí, ya está acá.")
        usuario = st.text_input("Usuario de Tourplan", value=cfg.get("tp_usuario", ""))
        password = st.text_input("Contraseña de Tourplan", value=cfg.get("tp_password", ""), type="password")
        sheet = st.text_input("URL del Google Sheet de esta herramienta", value=cfg.get("sheet_urls", {}).get(CLAVE_SHEET, ""))
        headless = st.checkbox("Correr sin ventana de Chrome visible (no recomendado: no vas a poder mirar el modal)",
                               value=bool(cfg.get("headless", False)))
        if st.form_submit_button("Guardar", type="primary"):
            cfg.update({"tp_usuario": usuario, "tp_password": password, "headless": headless})
            cfg.setdefault("sheet_urls", {})[CLAVE_SHEET] = sheet
            user_config.guardar(cfg)
            st.rerun()


def render_encabezado():
    usuario, _ = user_config.tp_credenciales_default()
    c0, c1, c2 = st.columns([0.6, 5, 5], vertical_alignment="center")
    if c0.button("⚙️", key="btn_config", help="Configuración (usuario de Tourplan, Sheet)"):
        dialogo_configuracion()
    c1.markdown('<p class="sd-titulo">Actualización de costos</p><p class="sd-subtitulo">mediante sustitución de servicios</p>',
                unsafe_allow_html=True)
    if usuario:
        chips = f'<span class="sd-chip">✔ Usuario: {_h(usuario)}</span>'
        if _sheet_url_guardada():
            chips += '<span class="sd-chip">✔ Sheet vinculado</span>'
        else:
            chips += '<span class="sd-chip mal">Falta la URL del Sheet</span>'
    else:
        chips = '<span class="sd-chip mal">⚠ Falta configurar el usuario de Tourplan</span>'
    c2.markdown(f'<div class="sd-chips">{chips}</div>', unsafe_allow_html=True)
    if not usuario and not st.session_state.get("_config_abierta_sola"):       # la primera vez se abre sola, una vez por sesión
        st.session_state["_config_abierta_sola"] = True
        dialogo_configuracion()


# ── Barra de corrida (pegada arriba) ────────────────────────────────────────

def _pedir_abortar(estado):
    estado["abort_pedido"] = True
    try:
        estado["stop_file"].touch()
    except Exception:
        pass


def _cerrar_si_termino(estado):
    """Cuando el subproceso terminó: guarda el mensaje final, refresca la cola y vuelve a dibujar toda la página."""
    if estado["running"] and estado["finished"]:
        estado["running"] = False
        estado["mensaje"] = _mensaje_final(estado["returncode"], estado["modo"])
        estado["aviso_cerrado"] = False
        _refrescar_conteo(estado, _sheet_url_guardada())      # la cola está arriba: que refleje el resultado
        st.rerun()


def _ir_a_comparar():
    st.session_state["paso"] = "cmp"


def _cerrar_aviso(estado):
    estado["aviso_cerrado"] = True


def render_barra(estado):
    """Barra de la corrida; se refresca sola cada segundo mientras corre."""
    _cerrar_si_termino(estado)
    if estado["running"]:
        aplicar = estado["modo"] == "aplicar"
        minutos = max(0, int((time.time() - (estado["inicio"] or time.time())) // 60))
        hechas, total = _progreso(estado["log_lines"])
        avance = f"Combinación {min(hechas, total)} de {total}" if total else "Iniciando…"
        pct = int(min(max(hechas - 1, 0), total) / total * 100) if total else 0      # la combinación en curso todavía no cuenta
        c1, c2 = st.columns([9, 1.4], vertical_alignment="center")
        c1.markdown(
            f'<div class="sd-run {"apl" if aplicar else "rev"}">{_insignia("APLICANDO" if aplicar else "REVISANDO", "apl" if aplicar else "rev")}'
            f'<b>{_h(avance)}</b><span class="sd-nota">{minutos} min · no toques la ventana de Chrome</span>'
            f'<div class="sd-barra {"apl" if aplicar else ""}"><i style="width:{pct}%"></i></div><span class="sd-pct">{pct} %</span></div>',
            unsafe_allow_html=True)
        c2.button("⏹ Abortar", key="abortar_barra", disabled=estado["abort_pedido"], use_container_width=True,
                  help="Corta al terminar la combinación en curso y hace logout de Tourplan.", on_click=_pedir_abortar, args=(estado,))
        if estado["abort_pedido"]:
            st.caption("⏸️ Abortando: termina la combinación en curso (completa), hace logout y corta.")
    elif estado["mensaje"] and not estado.get("aviso_cerrado"):
        nivel, texto = estado["mensaje"]
        clase = {"success": "ok", "info": "warn", "error": "mal"}.get(nivel, "warn")
        c1, c2, c3 = st.columns([9, 1.6, 0.5], vertical_alignment="center")
        c1.markdown(f'<div class="sd-run {clase}">{_h(texto)}</div>', unsafe_allow_html=True)
        if estado["modo"] == "aplicar" and nivel == "success":
            if c2.button("Ir a Comparar →", key="ir_comparar", type="primary", use_container_width=True, on_click=_ir_a_comparar):
                st.rerun()
        if c3.button("✕", key="cerrar_aviso", help="Cerrar este aviso", on_click=_cerrar_aviso, args=(estado,)):
            st.rerun()


# ── Cola de trabajo ─────────────────────────────────────────────────────────

def _progreso(lineas):
    """(combinaciones empezadas, total) leyendo la consola del script: 'Filas PENDIENTE a procesar: N' y una línea 'Fila n: ...'
    por cada combinación que arranca. (0, 0) si todavía no se sabe."""
    total, hechas = 0, 0
    for linea in lineas:
        m = re.search(r"Filas PENDIENTE a procesar: (\d+)", linea)
        if m:
            total = int(m.group(1))
        elif re.search(r"\] Fila \d+: ", linea) or linea.startswith("Fila "):
            hechas += 1
    return hechas, total


def render_cola(estado, sheet_url):
    # Mientras corre, este panel se refresca solo (ver main): se vuelve a leer la hoja cada ~10 s.
    if estado["running"] and sheet_url and time.time() - estado.get("ultimo_conteo", 0) >= 8:
        _refrescar_conteo(estado, sheet_url)
        estado["ultimo_conteo"] = time.time()
    with st.container(border=True, key="cola_trabajo"):
        c1, c2, c3 = st.columns([2.2, 7, 1.4], vertical_alignment="center")
        plan = st.session_state.get("_plan_reporte")
        por_cargar = len(plan["a_cargar"]) if plan else 0
        nota = ("Se actualiza sola cada 10 s" if estado["running"] else "Leída del Google Sheet")
        if por_cargar:
            nota = f"+{por_cargar} al confirmar la carga del reporte"      # el análisis no escribe en el Sheet: sumá cuando se confirma
        c1.markdown(f'<p class="sd-cola-titulo">Cola de trabajo</p><p class="sd-nota">{nota}</p>', unsafe_allow_html=True)
        if estado["conteo_error"]:
            c2.error(f"No pude leer el Sheet: {estado['conteo_error']}")
        elif estado["conteo"]:
            c = estado["conteo"]
            items = [("pend", "Pendiente", c["Pendiente"]), ("", "OK", c["OK"]), ("mal" if c["Error"] > 0 else "", "Error", c["Error"]),
                     ("", "Duplicado", c["Duplicado"]), ("", "Otro", c["Otro"])]
            c2.markdown('<div class="sd-cola">' + "".join(f'<div class="{k}"><span class="n">{n}</span><span class="l">{t}</span></div>'
                                                          for k, t, n in items) + "</div>", unsafe_allow_html=True)
        else:
            c2.caption("Tocá Refrescar para ver cuántas filas hay Pendientes, OK o con Error.")
        if c3.button("🔄 Refrescar", disabled=estado["running"] or not sheet_url, use_container_width=True, key="refrescar"):
            _refrescar_conteo(estado, sheet_url)
            st.rerun()


# ── Paso 1 · Ejecutar ───────────────────────────────────────────────────────

def _seccion(numero, titulo):
    st.markdown(f'<div class="sd-seccion"><span class="sd-num">{numero}</span>{_h(titulo)}</div>', unsafe_allow_html=True)


def render_cargar_reporte(estado, bloqueado):
    """Carga de combinaciones desde un reporte Service Status: vista previa y confirmación obligatoria antes de escribir en el Sheet."""
    st.caption("Subí el reporte **Service Status** exportado de Tourplan. La app saca los servicios cancelados, deja una línea por combinación "
               "(Location + Supplier + Service Type + Option), descarta las que no se pueden procesar y te muestra el resultado. "
               "**Nada se carga hasta que confirmes.**")
    sheet_url = _sheet_url_guardada()
    archivo = st.file_uploader("Reporte Service Status (.csv)", type=["csv"], key="rep_entrada", disabled=bloqueado)
    if st.button("Analizar reporte", disabled=not (archivo and sheet_url) or bloqueado, key="analizar_reporte"):
        try:
            st.session_state["_plan_reporte"] = _analizar_reporte(archivo.getvalue(), sheet_url)
            plan_nuevo = st.session_state["_plan_reporte"]
            estado["conteo"], estado["pend_tipo"], estado["conteo_error"] = plan_nuevo["conteo"], plan_nuevo["pend_tipo"], None
            st.rerun()          # la cola de trabajo (arriba) ya muestra lo que tiene la hoja
        except Exception as e:      # un archivo que no es el reporte, o un Sheet inaccesible, no debe romper la app
            st.session_state["_plan_reporte"] = None
            st.error(f"No pude analizar el reporte: {e}")
    if not sheet_url:
        st.caption("Falta la URL del Google Sheet: se carga en ⚙️ Configuración.")
    plan = st.session_state.get("_plan_reporte")
    if not plan:
        return
    r = plan["resumen"]
    c = st.columns(6)
    c[0].metric("Líneas del reporte", r["Líneas del reporte"])
    c[1].metric("Excluidas (cancelados)", r["Líneas excluidas (servicio cancelado)"])
    c[2].metric("Excluidas (por asignar)", r["Líneas excluidas (proveedor por asignar)"])
    c[3].metric("Combinaciones a cargar", r["Combinaciones a cargar"])
    c[4].metric("Reservas", r["Reservas a cargar"])
    c[5].metric("Tiempo estimado", reporte_entrada.texto_duracion(r["Segundos estimados"]))
    if r["Desde"]:
        st.caption(f"El reporte cubre servicios del {r['Desde']:%d/%m/%Y} al {r['Hasta']:%d/%m/%Y}.")
        desde_p, hasta_p = st.session_state.get("desde"), st.session_state.get("hasta")
        if desde_p and hasta_p and (desde_p > r["Desde"].date() or hasta_p < r["Hasta"].date()):
            st.warning(f"En Servicios desde/hasta ({desde_p:%d/%m/%Y} – {hasta_p:%d/%m/%Y}) no cubren todo el rango del reporte: "
                       "esas reservas quedarían sin procesar.")
    if plan.get("pendientes_en_hoja"):
        st.warning(f"La hoja ya tiene {plan['pendientes_en_hoja']} fila(s) PENDIENTE: al ejecutar se van a procesar junto con las que cargues ahora.")
    if r.get("Líneas de Department sin clasificar"):
        st.warning(f"{r['Líneas de Department sin clasificar']} línea(s) son de un Department sin clasificar como FITS o GRUPOS "
                   "(PR, BO, CF, FL, SL, US, AC, RD o vacío): no se cargan, están en Descartadas. Se clasifican en common/tipo_reserva.py.")
    if r["Líneas incompletas"]:
        st.warning(f"{r['Líneas incompletas']} línea(s) del reporte tienen Location, Supplier, Service u Option vacíos y se ignoraron.")
    cuentas = {"cargar": len(plan["a_cargar"]), "descartadas": len(plan["descartadas"])}
    vista = st.segmented_control("Detalle del análisis", ["cargar", "descartadas"], default="cargar", required=True, key="detalle_carga",
                                 format_func=lambda k: f"{'A cargar' if k == 'cargar' else 'Descartadas'} ({cuentas[k]})",
                                 label_visibility="collapsed")
    filas = plan["a_cargar"] if vista == "cargar" else plan["descartadas"]
    if filas:
        st.dataframe([{k: v for k, v in c_.items() if k != "N_ESTADOS"} for c_ in filas], hide_index=True, use_container_width=True,
                     height="content")
    if not plan["a_cargar"]:
        st.info("No hay combinaciones nuevas para cargar.")
    elif st.button(f"✅ Confirmar: cargar {len(plan['a_cargar'])} combinación(es) al Sheet como PENDIENTE", type="primary",
                   disabled=bloqueado, key="confirmar_carga"):
        try:
            n = _cargar_a_la_hoja(plan, sheet_url)
            st.session_state["_plan_reporte"] = None
            st.session_state["_volver_a_pendientes"] = True
            estado["aviso_carga"] = f"Se cargaron {n} combinación(es) a la hoja SUBSTITUTE como PENDIENTE."
            _refrescar_conteo(estado, sheet_url)
            st.rerun()
        except Exception as e:
            st.error(f"No pude escribir en el Sheet: {e}")


def _verificar_requisitos(p, pend, pre_nombre, confirmo):
    """Lista de requisitos para ejecutar: [(estado, texto)] con estado 'ok' | 'no' | 'neutro', y la lista de lo que falta."""
    aplicar = p["modo"] == "aplicar"
    items, faltan = [], []
    config_ok = bool(p["usuario"] and p["password"] and p["sheet_url"])
    items.append(("ok" if config_ok else "no", f"Usuario de Tourplan: {p['usuario']}" if config_ok else "Usuario de Tourplan y Sheet sin configurar"))
    if not config_ok:
        faltan.append("configurar el usuario y el Sheet (engranaje)")
    if pend is None:
        items.append(("neutro", "Combinaciones pendientes: sin leer todavía (tocá Refrescar en la cola)"))
    elif p["fila"]:
        items.append(("ok", f"Se va a procesar solo la fila {p['fila']} del Sheet"))
    else:
        items.append(("ok" if pend > 0 else "no", f"Combinaciones pendientes en el Sheet ({p['tipo_reservas']}): {pend}"))
        if pend == 0:
            faltan.append("combinaciones pendientes")
    error_rango = L.validar_rango(datetime.combine(p["desde"], datetime.min.time()) if p["desde"] else None,
                                  datetime.combine(p["hasta"], datetime.min.time()) if p["hasta"] else None)
    if error_rango:
        items.append(("no", error_rango))
        faltan.append("un rango de fechas válido")
    if not str(p["base_url"] or "").strip():
        items.append(("no", "Falta la URL de Tourplan (Avanzado)"))
        faltan.append("la URL de Tourplan")
    if aplicar:
        items.append(("ok" if pre_nombre else "no", f"PRE adjuntado: {pre_nombre}" if pre_nombre else "Falta el reporte PRE (obligatorio en Aplicar)"))
        if not pre_nombre:
            faltan.append("adjuntar el reporte PRE")
        items.append(("ok" if confirmo else "no", "Confirmación de que se modifican reservas reales"))
        if not confirmo:
            faltan.append("confirmar el modo Aplicar")
    return items, faltan


def render_ejecutar(estado):
    bloqueado = estado["running"]
    hoy = date.today()
    if st.session_state.pop("_volver_a_pendientes", False):         # tras confirmar una carga se vuelve al origen «pendientes del Sheet»
        st.session_state["origen"] = "pendientes"
    if st.session_state.pop("_reiniciar_confirmo", False):          # la confirmación de Aplicar vale para una sola corrida
        st.session_state["confirmo"] = False
    aplicar = st.session_state.get("modo", "lectura") == "aplicar"
    pend = _pendientes(estado)

    with st.container(border=True, key=f"tarjeta_{'aplicar' if aplicar else 'lectura'}"):
        if aplicar:
            st.markdown('<div class="sd-bandera">⚠ MODO APLICAR · Modifica reservas reales en Tourplan</div>', unsafe_allow_html=True)

        _seccion(1, "Combinaciones a procesar")
        if estado.get("aviso_carga"):
            st.success(estado.pop("aviso_carga"))
        st.radio("Origen de las combinaciones", ["pendientes", "reporte"], key="origen", horizontal=True, disabled=bloqueado,
                 label_visibility="collapsed",
                 format_func=lambda k: "Usar las pendientes del Sheet" if k == "pendientes" else "Cargar un reporte nuevo (Service Status)",
                 captions=[f"{pend} combinaciones listas para procesar." if pend is not None else "Tocá Refrescar en la cola para ver cuántas hay.",
                           "Service Status de Tourplan. Suma combinaciones al Sheet."])
        if st.session_state.get("origen") == "reporte":
            render_cargar_reporte(estado, bloqueado)

        _seccion(2, "Modo y fechas")
        modo = st.radio("Modo", ["lectura", "aplicar"], key="modo", horizontal=True, disabled=bloqueado, label_visibility="collapsed",
                        format_func=lambda m: ETIQUETA_MODO[m], captions=[DESCRIPCION_MODO["lectura"], DESCRIPCION_MODO["aplicar"]])
        tipo_reservas = st.radio("Tipo de reservas", list(T.MODOS), key="tipo_reservas", horizontal=True, disabled=bloqueado,
                                 format_func=lambda t: TIPO_ETIQUETA[t], captions=[TIPO_DESCRIPCION[t] for t in T.MODOS],
                                 help="Separa las reservas individuales (FITS) de las de grupo por su Department en Tourplan. Cada combinación "
                                      "del Sheet tiene su TIPO: en FITS o en GRUPOS solo se procesan las filas de ese tipo; en AMBOS, todas.")
        c1, c2 = st.columns(2)
        desde = c1.date_input("Servicios desde", value=hoy + timedelta(days=1), min_value=date(2000, 1, 1), max_value=date(2099, 12, 31),
                              format="DD/MM/YYYY", disabled=bloqueado, key="desde", help="Obligatoria: se ingresa en cada corrida.")
        hasta = c2.date_input("Servicios hasta", value=date(2049, 12, 31), min_value=date(2000, 1, 1), max_value=date(2099, 12, 31),
                              format="DD/MM/YYYY", disabled=bloqueado, key="hasta",
                              help="Por defecto 31/12/2049: abarca todos los servicios desde la fecha de inicio.")
        with st.expander("Avanzado"):
            c4, c5 = st.columns([3, 1])
            base_url = c4.text_input("URL de Tourplan", value=PRODUCCION_URL, disabled=bloqueado, key="base_url",
                                     help="Viene con producción. Cambiala solo si querés probar a propósito contra otro ambiente.")
            fila = c5.number_input("Solo la fila nº (0 = todas)", min_value=0, value=0, step=1, disabled=bloqueado, key="fila",
                                   help="Para probar una combinación puntual del Sheet.")

        pre = None
        _seccion(3, "Reporte PRE")
        if modo == "aplicar":
            pre = st.file_uploader("Reporte PRE: Product Analysis exportado ANTES de la corrida (.csv) — obligatorio en Aplicar", type=["csv"],
                                   disabled=bloqueado, key="rep_pre",
                                   help="Es la única foto de costos y ventas para revisar la corrida después (y corregir si hace falta). Tiene que "
                                        "cubrir todas las combinaciones PENDIENTES, con las mismas fechas Desde/Hasta. Se guarda en salida/pre_reportes "
                                        "y el comparador lo usa como «antes».")
        else:
            st.caption("El reporte PRE se pide solo en modo Aplicar.")

        usuario, password = user_config.tp_credenciales_default()
        p = {"modo": modo, "desde": desde, "hasta": hasta, "sheet_url": _sheet_url_guardada(), "base_url": base_url, "fila": int(fila),
             "confirmar_ok": False, "usuario": usuario, "password": password, "pre_contenido": pre.getvalue() if pre else None,
             "tipo_reservas": tipo_reservas}

        _seccion(4, "Ejecutar")
        confirmo = False
        if aplicar:
            with st.container(key="confirmo_aplicar"):
                confirmo = st.checkbox("**Entiendo que esto modifica reservas reales.** Se va a trabajar sobre Tourplan en producción.",
                                       key="confirmo", disabled=bloqueado)
        items, faltan = _verificar_requisitos(p, pend, pre.name if pre else None, confirmo)
        simbolo = {"ok": "✔", "no": "🔒" if aplicar else "○", "neutro": "○"}
        st.markdown('<ul class="sd-reqs">' + "".join(f'<li class="{"ok" if e == "ok" else "no"}"><span>{simbolo[e]}</span><span>{_h(t)}</span></li>'
                                                      for e, t in items) + "</ul>", unsafe_allow_html=True)
        errores = E.validar_parametros(p)
        sin_tipo = (estado.get("pend_tipo") or {}).get("", 0)
        if sin_tipo and tipo_reservas != T.TIPO_AMBOS:
            st.caption(f"ℹ️ Hay {sin_tipo} fila(s) PENDIENTE sin TIPO (cargadas antes de esta columna): solo se procesan en el tipo AMBOS.")
        for aviso in E.advertencias(p):
            st.caption(f"⚠️ {aviso}")
        c1, c2, _ = st.columns([2, 1, 3])
        ejecutar = c1.button("🔒 Aplicar en producción" if aplicar else "▶ Ejecutar revisión", type="secondary" if aplicar else "primary",
                             key="ejecutar_aplicar" if aplicar else "ejecutar_revision", use_container_width=True,
                             disabled=bloqueado or bool(faltan) or bool(errores))
        c2.button("⏹ Abortar", key="abortar_tarjeta", disabled=not bloqueado or estado["abort_pedido"], use_container_width=True,
                  on_click=_pedir_abortar, args=(estado,))
        total = 1 if p["fila"] else pend
        if bloqueado:
            st.markdown('<div class="sd-pista">Corrida en curso: los parámetros quedan bloqueados hasta que termine o la abortes.</div>',
                        unsafe_allow_html=True)
        elif faltan:
            st.markdown(f'<div class="sd-falta">🔒 Para ejecutar falta: {_h(" · ".join(faltan))}.</div>', unsafe_allow_html=True)
        else:
            cuantas = f"en {total} combinaciones" if total is not None else "en las combinaciones PENDIENTES"
            st.markdown('<div class="sd-pista">' + (f"Va a modificar reservas reales {cuantas}. Podés abortar en cualquier momento."
                                                      if aplicar else f"Va a leer {cuantas.replace('en ', '')}. No modifica nada.") + "</div>",
                        unsafe_allow_html=True)
    if ejecutar:
        error_pre = None
        if aplicar:
            error_pre, _ = _verificar_pre(p)
        if error_pre:
            st.error(error_pre)
        else:
            lanzar(estado, p)
            st.session_state["_reiniciar_confirmo"] = True
            st.rerun()


# ── Paso 2 · Comparar ───────────────────────────────────────────────────────

ICONO_RESULTADO = {"Venta modificada": "🔴", "Costo cambió": "🔵", "Otros cambios": "🟡", "Solo antes": "⚪", "Solo después": "⚪"}
FILTROS_COMPARACION = {"venta": "Venta modificada", "costo": "Costo cambió", "otros": "Otros cambios", "solo": "Solo en uno", "todos": "Todos los cambios"}


def _lineas_del_filtro(lineas, filtro):
    if filtro == "venta":
        return [x for x in lineas if x["Resultado"] == "Venta modificada"]
    if filtro == "costo":
        return [x for x in lineas if x["Resultado"] == "Costo cambió"]
    if filtro == "otros":
        return [x for x in lineas if x["Resultado"] == "Otros cambios"]
    if filtro == "solo":
        return [x for x in lineas if x["Resultado"] in ("Solo antes", "Solo después")]
    return [x for x in lineas if x["Resultado"] != "Sin cambios"]


def _resultado_comparacion(res):
    r = res["resumen"]
    if r["Líneas antes"] == 0 or r["Líneas después"] == 0:
        st.error("Uno de los archivos no tiene líneas: revisá que sea el reporte Product Analysis en CSV.")
        return
    nv = r["Venta modificada"]
    with st.container(key="veredicto_mal" if nv else "veredicto_ok"):
        c1, c2 = st.columns([5, 1.6], vertical_alignment="center")
        if nv:
            c1.markdown(f'<div class="sd-veredicto"><b>Cambió la venta (Retail / Agent) en {nv} {"línea" if nv == 1 else "líneas"}</b><br>'
                        "<span>Son las líneas para corregir en Tourplan. El detalle está más abajo.</span></div>", unsafe_allow_html=True)
        else:
            c1.markdown(f'<div class="sd-veredicto"><b>La venta no cambió</b><br><span>Se compararon {r["Líneas antes"]} líneas. '
                        "Solo se movió el costo, como se esperaba.</span></div>", unsafe_allow_html=True)
        # La descarga va arriba, antes de las tablas largas: así está a mano aunque la página sea alta o se corte la ventana.
        c2.download_button("Descargar CSV", data=comparador.a_csv(res["lineas"]), file_name="comparacion_reportes.csv", mime="text/csv",
                           icon=":material/download:", key="descargar_comparacion", use_container_width=True)
    if nv and res.get("variaciones_venta"):
        st.caption("Variaciones del Retail más comunes: " + "; ".join(
            f"{v['Δ Retail %']:+.2f} % en {v['Líneas']} línea(s) ({v['Moneda'] or 'sin moneda'})" for v in res["variaciones_venta"][:5])
            + ". Si casi todas coinciden en el mismo porcentaje, suele ser un factor común (por ejemplo, el tipo de cambio).")
    if not nv and r["Solo en uno de los dos"]:
        st.warning(f"La venta no cambió, pero {r['Solo en uno de los dos']} línea(s) aparecen en un solo reporte.")
    tiles = [("Líneas antes → después", f"{r['Líneas antes']} → {r['Líneas después']}", ""), ("Venta modificada", nv, "mal" if nv else ""),
             ("Costo cambió", r["Costo cambió"], "info"), ("Otros cambios", r["Otros cambios"], ""),
             ("Solo en uno de los dos", r["Solo en uno de los dos"], ""), ("Sin cambios", r["Sin cambios"], "")]
    st.markdown('<div class="sd-tiles">' + "".join(f'<div class="sd-tile {k}"><div class="n">{n}</div><div class="l">{t}</div></div>'
                                                   for t, n, k in tiles) + "</div>", unsafe_allow_html=True)
    if "Control: líneas no tocadas" in r:
        n_ctl, n_ctl_mal = r["Control: líneas no tocadas"], r["Control: con venta modificada"]
        if n_ctl == 0:
            st.markdown('<p class="sd-control">Control: el reporte no tiene líneas de proveedores por asignar (que la herramienta no toca) para comparar.</p>',
                        unsafe_allow_html=True)
        elif n_ctl_mal:
            st.warning(f"Control: la venta también cambió en {n_ctl_mal} de {n_ctl} líneas que la herramienta NO toca (proveedores por asignar). "
                       "Eso apunta a una causa externa a las sustituciones (tipo de cambio, comisiones, tarifas).")
        else:
            st.markdown(f'<p class="sd-control">✔ Control: {n_ctl} líneas «por asignar» (la herramienta no las toca): la venta no cambió.</p>',
                        unsafe_allow_html=True)
    try:
        cuentas = {k: len(_lineas_del_filtro(res["lineas"], k)) for k in FILTROS_COMPARACION}
        filtro = st.pills("Filtrar el detalle", list(FILTROS_COMPARACION), default="venta" if cuentas["venta"] else "costo", required=True,
                          key="filtro_comparacion", label_visibility="collapsed",
                          format_func=lambda k: f"{FILTROS_COMPARACION[k]} ({cuentas[k]})")
        filas = _lineas_del_filtro(res["lineas"], filtro)
        if filas:
            st.dataframe([{"": ICONO_RESULTADO.get(x["Resultado"], ""), **x} for x in filas], hide_index=True, use_container_width=True,
                         height="content")
        else:
            st.success("Ninguna línea en esta categoría.")
    except Exception as e:      # si algo falla al dibujar las tablas, se dice qué (y la descarga de arriba sigue sirviendo)
        st.error(f"No pude mostrar las tablas: {type(e).__name__}: {e}")


def render_comparar(estado):
    with st.container(border=True, key="tarjeta_comparar"):
        st.markdown('<p class="sd-cola-titulo">Comparar antes / después</p><p class="sd-nota">Control de costo y venta. '
                    "El PRE de la última corrida en modo aplicar queda guardado y se puede usar como «antes».</p>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        guardado = pre_reporte.ultimo_pre(SALIDA / pre_reporte.CARPETA)
        usar_guardado = False
        c1.markdown('<span class="sd-etiqueta">Antes (PRE)</span>', unsafe_allow_html=True)
        if guardado:
            usar_guardado = c1.checkbox(
                f"Usar el PRE guardado de la última corrida ({datetime.fromtimestamp(guardado.stat().st_mtime):%d/%m/%Y %H:%M})",
                value=True, key="usar_pre_guardado")
        antes = None if usar_guardado else c1.file_uploader("Reporte ANTES (.csv)", type=["csv"], key="rep_antes", label_visibility="collapsed")
        c2.markdown('<span class="sd-etiqueta">Después (POST)</span>', unsafe_allow_html=True)
        despues = c2.file_uploader("Reporte DESPUÉS (.csv)", type=["csv"], key="rep_despues", label_visibility="collapsed")
        antes_bytes = guardado.read_bytes() if usar_guardado else (antes.getvalue() if antes else None)
        if st.button("Comparar", type="primary", disabled=not (antes_bytes and despues), key="comparar"):
            try:
                res = comparador.comparar(antes_bytes, despues.getvalue(), no_tocada=reporte_entrada.es_por_asignar)
                st.session_state["_comparacion"] = res
                st.session_state["filtro_comparacion"] = "venta" if res["resumen"]["Venta modificada"] else "costo"
                st.rerun()      # para que la etiqueta del paso (arriba) ya muestre el veredicto
            except Exception as e:      # un archivo que no es el reporte no debe romper la app
                st.session_state["_comparacion"] = None
                st.error(f"No pude leer los reportes: {e}")
        res = st.session_state.get("_comparacion")
        if res:
            _resultado_comparacion(res)


# ── Navegación por pasos ────────────────────────────────────────────────────

def _etiqueta_paso(estado, paso):
    """Texto del paso con su estado visible. La identidad del paso es fija ('run' / 'cmp'): solo cambia lo que se lee."""
    if paso == "run":
        pend = _pendientes(estado)
        cola = f" · {pend} pendientes" if pend is not None else ""
        if estado["running"]:
            return "1 · Ejecutar  🔵 Corrida en curso"
        if st.session_state.get("modo") == "aplicar":
            return "1 · Ejecutar  " + ("🟢 Aplicar · PRE adjuntado" if st.session_state.get("rep_pre") else "🔴 Aplicar · falta el PRE") + cola
        return "1 · Ejecutar  ⚪ Solo revisar" + cola
    res = st.session_state.get("_comparacion")
    if not res:
        return "2 · Comparar  ⚪ Sin comparar"
    nv = res["resumen"]["Venta modificada"]
    return "2 · Comparar  " + (f"🔴 {nv} ventas cambiaron" if nv else "🟢 La venta no cambió")


# ── Consola ─────────────────────────────────────────────────────────────────

def render_consola(estado):
    """Panel de la consola; se refresca solo cada segundo mientras corre."""
    if estado["running"]:
        texto_estado, clase = "Corriendo…", "run"
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
    elif estado["mensaje"]:
        texto_estado, clase = ("Abortada", "warn") if estado["returncode"] == E.EXIT_ABORTADO else ("Finalizada", "ok")
    else:
        texto_estado, clase = "En espera", "idle"
    hechas, total = _progreso(estado["log_lines"])
    with st.container(border=True, key="consola"):
        c1, c2 = st.columns([5, 1.4], vertical_alignment="center")
        c1.markdown(f'<span class="sd-cola-titulo">Consola</span> &nbsp; {_insignia(texto_estado, clase)}', unsafe_allow_html=True)
        c2.download_button("Descargar log", data="".join(estado["log_lines"]), file_name="consola.txt", mime="text/plain",
                           icon=":material/download:", key="descargar_consola", use_container_width=True)
        pct = int(min(max(hechas - 1, 0) if estado["running"] else hechas, total) / total * 100) if total else 0
        st.markdown(f'<div class="sd-run" style="margin-bottom:8px"><div class="sd-barra {"apl" if estado["modo"] == "aplicar" else ""}">'
                    f'<i style="width:{pct}%"></i></div><span class="sd-nota">'
                    + (f"Combinación {min(hechas, total)} de {total}" if total else "Sin corrida") + "</span></div>", unsafe_allow_html=True)
        st.markdown(html_consola(estado["log_lines"]), unsafe_allow_html=True)
        carpeta = _carpeta_de_salida(estado)
        if carpeta:
            st.caption(f"📁 Capturas, HTML y consola.log de esta corrida: `{carpeta}`")


# ── Historial de corridas ───────────────────────────────────────────────────

def _resumen_corrida(texto, en_curso=False):
    """(combinaciones, resultado, clase de la insignia) leyendo el log de una corrida."""
    total = re.search(r"Filas PENDIENTE a procesar: (\d+)", texto)
    fin = re.search(r"🏁 Fin\..*?filas OK=(\d+) ERROR=(\d+)", texto)
    if en_curso:
        return (f"? de {total.group(1)}" if total else "—"), "En curso", "run"
    hechas = f"{int(fin.group(1)) + int(fin.group(2))} de {total.group(1)}" if fin and total else (total.group(1) if total else "—")
    if "CORRIDA DETENIDA" in texto:
        return hechas, "Detenida", "mal"
    if "Corrida abortada por el usuario" in texto:
        return hechas, "Abortada", "warn"
    if not fin:
        return hechas, "Sin cierre", "warn"
    errores = int(fin.group(2))
    return hechas, (f"Con errores ({errores})" if errores else "Finalizada"), ("mal" if errores else "ok")


def render_historial(estado):
    en_curso = _carpeta_de_salida(estado) if estado["running"] else None
    with st.expander("🕘 Historial de corridas · últimas 15"):
        corridas = _historial_corridas()
        if not corridas:
            st.caption("Todavía no hay corridas guardadas en la carpeta salida.")
        else:
            cab = st.columns([2.4, 1.2, 1.4, 1.6, 1.6])
            for col, t in zip(cab, ("Fecha", "Modo", "Combinaciones", "Resultado", "")):
                col.markdown(f'<span class="sd-etiqueta">{t}</span>', unsafe_allow_html=True)
        for c in corridas:
            texto = c["log"].read_text(encoding="utf-8", errors="replace")
            actual = en_curso is not None and Path(en_curso).name == c["carpeta"].name
            combinaciones, resultado, clase = _resumen_corrida(texto, actual)
            col_fecha, col_modo, col_comb, col_res, col_desc = st.columns([2.4, 1.2, 1.4, 1.6, 1.6], vertical_alignment="center")
            col_fecha.write(c["inicio"].strftime("%d/%m/%Y %H:%M:%S"))
            col_modo.markdown(_insignia("APLICAR" if c["modo"] == "aplicar" else "REVISAR", "apl" if c["modo"] == "aplicar" else "rev"),
                              unsafe_allow_html=True)
            col_comb.write(combinaciones)
            col_res.markdown(_insignia(resultado, clase), unsafe_allow_html=True)
            col_desc.download_button("Descargar", data=texto, file_name=f"{c['carpeta'].name}.log", mime="text/plain",
                                     icon=":material/download:", key=f"hist_{c['carpeta'].name}", use_container_width=True)


# ── Página ──────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="Actualización de costos", page_icon="🔁", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    estado = _estado()
    sheet_url = _sheet_url_guardada()

    render_encabezado()

    # La primera vez que se abre la página se lee la hoja sola (después, al analizar un reporte o al confirmar una carga, y con Refrescar).
    if sheet_url and estado["conteo"] is None and not estado["conteo_error"] and not estado["running"] and not st.session_state.get("_conteo_inicial"):
        st.session_state["_conteo_inicial"] = True
        _refrescar_conteo(estado, sheet_url)

    with st.container(key="barra_corrida"):
        st.fragment(run_every=1 if estado["running"] else None)(render_barra)(estado)

    st.fragment(run_every=10 if estado["running"] else None)(render_cola)(estado, sheet_url)

    # Los dos pasos se dibujan siempre y se oculta el que no está activo: así no se pierde lo cargado (PRE, fechas, modo)
    # al cambiar de paso, cosa que pasaría si el paso inactivo no se dibujara.
    st.session_state.setdefault("paso", "run")
    st.segmented_control("Paso", ["run", "cmp"], key="paso", required=True, label_visibility="collapsed", width="stretch",
                         format_func=lambda k: _etiqueta_paso(estado, k))
    oculto = "cmp" if st.session_state.get("paso", "run") == "run" else "run"
    st.markdown(f"<style>.st-key-panel_{oculto} {{ display: none; }}</style>", unsafe_allow_html=True)
    with st.container(key="panel_run"):
        render_ejecutar(estado)
    with st.container(key="panel_cmp"):
        render_comparar(estado)

    st.fragment(run_every=1 if estado["running"] else None)(render_consola)(estado)
    render_historial(estado)


main()
