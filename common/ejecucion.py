"""
ejecucion.py — Lógica pura (sin Streamlit ni Selenium) de la app local de la doble sustitución:
validación de parámetros, entorno del subproceso, conteo de estados de la cola y el protocolo de
confirmación por archivos entre la app y el script.

Confirmación: antes de cada OK el script escribe confirmacion_pendiente.txt en la carpeta de la corrida
y espera confirmacion_respuesta.txt (SI / NO). La app muestra el texto con dos botones y escribe la
respuesta. Así no hace falta una consola interactiva.
"""

import os
import re
import time
from datetime import datetime
from pathlib import Path

from common import logica as L

ARCHIVO_PENDIENTE = "confirmacion_pendiente.txt"
ARCHIVO_RESPUESTA = "confirmacion_respuesta.txt"
EXIT_ABORTADO = 75      # abort pedido por la usuaria (igual que el resto de los scripts)
EXIT_DETENIDA = 76      # corrida detenida por la regla de "sustitutos sin asignar"
TIMEOUT_CONFIRMACION = 3600


# ── Parámetros de la corrida ────────────────────────────────────────────────

def _como_datetime(d):
    return datetime(d.year, d.month, d.day) if d is not None else None


def validar_parametros(p):
    """Lista de problemas que impiden ejecutar (vacía = se puede ejecutar). `p` es un dict con:
    usuario, password, sheet_url, base_url, modo ('lectura'|'aplicar'), desde, hasta (date),
    fila, confirmo_sin_pendientes."""
    errores = []
    for clave, nombre in (("usuario", "el usuario de Tourplan"), ("password", "la contraseña de Tourplan"),
                          ("sheet_url", "la URL del Google Sheet"), ("base_url", "la URL de Tourplan")):
        if not str(p.get(clave) or "").strip():
            errores.append(f"Falta {nombre}" + (" (cargala en ⚙️ Configuración)." if clave != "base_url" else "."))
    if p.get("modo") not in ("lectura", "aplicar"):
        errores.append("Elegí un modo.")
    error_rango = L.validar_rango(_como_datetime(p.get("desde")), _como_datetime(p.get("hasta")))
    if error_rango:
        errores.append(error_rango + ".")
    if p.get("modo") == "aplicar" and not p.get("confirmo_sin_pendientes"):
        errores.append("Para aplicar tenés que confirmar que revisaste que no hay códigos 000000 pendientes.")
    return errores


def advertencias(p, hoy=None):
    hoy = hoy or datetime.now().date()
    out = []
    if p.get("desde") and p["desde"] < hoy:
        out.append("La fecha Desde es anterior a hoy: se incluyen servicios pasados.")
    return out


def armar_entorno(p, base_env, repo_root, run_dir, ss_dir, stop_file, credentials_path, token_path, headless=False):
    """Variables de entorno del subproceso (contrato TOURPLAN_* de los scripts). Las fechas viajan como
    DD/MM/YY, el único formato que acepta el campo de Tourplan."""
    env = dict(base_env)
    env.update({
        "TOURPLAN_USERNAME": p["usuario"], "TOURPLAN_PASSWORD": p["password"],
        "TOURPLAN_BASE_URL": p["base_url"].strip(), "TOURPLAN_SHEET_URL": p["sheet_url"].strip(),
        "TOURPLAN_MODO": p["modo"],
        "TOURPLAN_FECHA_DESDE": L.fmt_ddmmyy(_como_datetime(p["desde"])),
        "TOURPLAN_FECHA_HASTA": L.fmt_ddmmyy(_como_datetime(p["hasta"])),
        "TOURPLAN_FILA": str(int(p["fila"])) if int(p.get("fila") or 0) > 0 else "",
        "TOURPLAN_PUDO_SERVICE_TYPES": ",".join(t.upper() for t in re.split(r"[,;\s]+", p.get("pudo_service_types") or "") if t),
        "TOURPLAN_CONFIRMO_SIN_PENDIENTES": "SI" if p.get("confirmo_sin_pendientes") else "",
        "TOURPLAN_CONFIRMAR_OK": "1" if p.get("confirmar_ok", True) else "0",
        "TOURPLAN_CONFIRM_DIR": str(run_dir),
        "TOURPLAN_HEADLESS": "1" if headless else "0",
        "TOURPLAN_SS_DIR": str(ss_dir), "TOURPLAN_STOP_FILE": str(stop_file),
        "TOURPLAN_CREDENTIALS_PATH": str(credentials_path), "TOURPLAN_TOKEN_PATH": str(token_path),
        "PYTHONPATH": str(repo_root) + os.pathsep + env.get("PYTHONPATH", ""),
        "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
    })
    return env


# ── Cola de trabajo (hoja SUBSTITUTE) ───────────────────────────────────────

def clasificar_estado(valor):
    """Agrupa el valor crudo de ESTADO. Una celda vacía NO cuenta como pendiente (ningún script la procesa)."""
    v = str(valor or "").strip().upper()
    if v in ("PENDIENTE", "PENDING", "PEND"):
        return "Pendiente"
    if v.startswith("ERROR"):
        return "Error"          # incluye ERROR_COLGADAS y ERROR_REVISAR
    if v == "OK" or v.startswith("OK "):
        return "OK"
    if v == "DUPLICADO":
        return "Duplicado"
    return "Otro"


def contar_estados(filas, columna="ESTADO"):
    conteo = {"Pendiente": 0, "OK": 0, "Error": 0, "Duplicado": 0, "Otro": 0}
    for f in filas:
        conteo[clasificar_estado(f.get(columna))] += 1
    return conteo


# ── Confirmación antes de cada OK ───────────────────────────────────────────

def _escribir(ruta, texto):
    tmp = Path(str(ruta) + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, ruta)


def pedir_confirmacion(directorio, texto, timeout=TIMEOUT_CONFIRMACION, intervalo=0.5, cancelar=None):
    """Lado del script: publica la pregunta y espera SI/NO. Devuelve True solo con SI; con NO, con el
    timeout o si `cancelar()` (el abort de la app) da True devuelve False."""
    d = Path(directorio)
    pendiente, respuesta = d / ARCHIVO_PENDIENTE, d / ARCHIVO_RESPUESTA
    respuesta.unlink(missing_ok=True)
    _escribir(pendiente, texto)
    fin = time.time() + timeout
    try:
        while time.time() < fin:
            if cancelar and cancelar():
                return False
            if respuesta.exists():
                try:
                    valor = respuesta.read_text(encoding="utf-8").strip().upper()
                except OSError:
                    valor = ""
                if valor in ("SI", "NO"):
                    return valor == "SI"
            time.sleep(intervalo)
        return False
    finally:
        pendiente.unlink(missing_ok=True)
        respuesta.unlink(missing_ok=True)


def confirmacion_pendiente(directorio):
    """Lado de la app: el texto de la pregunta pendiente, o None."""
    try:
        ruta = Path(directorio) / ARCHIVO_PENDIENTE
        return ruta.read_text(encoding="utf-8") if ruta.exists() else None
    except OSError:
        return None


def responder_confirmacion(directorio, si):
    _escribir(Path(directorio) / ARCHIVO_RESPUESTA, "SI" if si else "NO")
