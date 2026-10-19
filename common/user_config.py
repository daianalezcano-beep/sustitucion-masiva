"""
user_config.py — Configuración local por persona/PC de TP-NX-App.

Guarda, en ~/.tourplan-nx-app/config.json — FUERA del repo/carpeta
compartida por todo el equipo —:
  - "sheet_urls": la URL default de Sheet por script (una entrada por
    key del dict SCRIPTS de app.py).
  - "headless": si los scripts corren con la ventana de Chrome visible
    (default, False) o sin ventana (True) — mismo campo que ya usa
    config_store.py de TP Documentación.
  - "tp_usuario" / "tp_password": credenciales de Tourplan, para no
    tener que tipearlas en cada script. Quedan en texto plano en este
    archivo local (nunca se commitea ni se comparte).

app.py y common/ viven en una carpeta de red única para todo el
equipo; un config al lado del código (como config_store.py de TP
Documentación, donde cada quien tiene su propia instalación completa)
se pisaría entre personas acá. Mismo tipo de solución que ya usa
run_app.bat para el venv (instalar fuera del repo por el límite de
rutas de Windows), aplicado a los datos personales en vez de al
entorno de Python.

No hay selector de usuario en la UI: siempre es 1 persona = 1 PC = 1
instancia local, así que la identidad la da la carpeta del perfil de
Windows/Mac de quien corre la app, no un login dentro de la app.
"""

import json
import os

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".tourplan-nx-app")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
CREDENTIALS_PATH = os.path.join(CONFIG_DIR, "credentials.json")
TOKEN_PATH = os.path.join(CONFIG_DIR, "token.json")

VALORES_DEFAULT = {
    "sheet_urls": {},
    "headless": False,
    "tp_usuario": "",
    "tp_password": "",
}


def cargar():
    """Devuelve la config guardada en esta PC, completando con los
    defaults cualquier clave que todavía no exista (por ejemplo, si se
    suma un campo nuevo en una versión futura de la app)."""
    if not os.path.exists(CONFIG_PATH):
        return dict(VALORES_DEFAULT)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        datos = json.load(f)
    cfg = dict(VALORES_DEFAULT)
    cfg.update(datos)
    return cfg


def guardar(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def sheet_url_default(script_key):
    return cargar().get("sheet_urls", {}).get(script_key, "")


def headless_default():
    return bool(cargar().get("headless", False))


def tp_credenciales_default():
    cfg = cargar()
    return cfg.get("tp_usuario", ""), cfg.get("tp_password", "")
