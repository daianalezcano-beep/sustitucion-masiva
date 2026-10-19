"""
sheets_client.py — Sheets I/O compartido por los scripts vendorizados
de TP-NX-App.

Port directo de sheets_client.py de TP Documentación (mismo repo,
app_documentacion/): asegurar_columnas/cargar_sheet/actualizar_fila_sheet
son EXACTAMENTE las mismas, sin cambiar nada de su comportamiento. Lo
único propio de este módulo es que conectar_sheets() recibe los paths
de credenciales como parámetro (en vez de importarlos de un
config_store fijo), porque acá cada script arma su URL de Sheet en
tiempo de ejecución en vez de tener una config única por app.

Primera vez que se corre en una máquina: abre el navegador para elegir
cuenta y dar permiso de acceso a Sheets. Después de ese primer OK, el
token queda guardado en token_path y no vuelve a pedir nada — salvo que
se revoque el permiso o se borre ese archivo.
"""

import copy
import os
import time

import gspread
from gspread.exceptions import APIError
from gspread.utils import rowcol_to_a1
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Segundos de espera entre reintentos cuando Sheets responde 429 (cuota
# de "Write requests per minute per user", ~60/min). La cuota se renueva
# por minuto, así que los últimos reintentos esperan ~1 minuto.
_ESPERAS_429 = (10, 20, 30, 60, 60)


def _con_reintentos_429(funcion, *args, **kwargs):
    """Ejecuta una escritura a Sheets; si la API responde 429 (cuota
    excedida) espera y reintenta. Cualquier otro error se propaga igual."""
    for espera in _ESPERAS_429:
        try:
            return funcion(*args, **kwargs)
        except APIError as e:
            if getattr(e.response, "status_code", None) != 429:
                raise
            print(f"[Sheets] Cuota de escritura excedida (429). "
                  f"Reintentando en {espera}s...")
            time.sleep(espera)
    return funcion(*args, **kwargs)


def _cargar_credenciales(credentials_path, token_path):
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    f"No encontré el archivo de credenciales OAuth en '{credentials_path}'. "
                    "Completá la pantalla de Configuración de la app (ver README.md) para "
                    "generar tu credentials.json.")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return creds


def conectar_sheets(sheet_url, hoja, credentials_path, token_path):
    """Autentica (abriendo el navegador la primera vez en esta máquina)
    y devuelve el worksheet de la pestaña pedida."""
    creds = _cargar_credenciales(credentials_path, token_path)
    gc = gspread.authorize(creds)

    sh = gc.open_by_url(sheet_url)
    for ws in sh.worksheets():
        if ws.title.strip().lower() == hoja.strip().lower():
            return ws
    raise ValueError(
        f"No encontré la pestaña {hoja!r} en el Sheet {sh.title!r}. "
        f"Pestañas disponibles: {[w.title for w in sh.worksheets()]}")


def asegurar_columnas(ws, columnas_requeridas):
    """Agrega al final de la hoja las columnas de columnas_requeridas que
    todavía no existan — nunca renombra ni reordena las que ya tenía."""
    headers = ws.row_values(1)
    faltantes = [c for c in columnas_requeridas if c not in headers]
    if faltantes:
        _con_reintentos_429(ws.update, range_name="A1", values=[headers + faltantes])


def cargar_sheet(ws):
    """Lee el Sheet completo, siempre fresco (nunca cacheado entre
    corridas). Devuelve (filas, columnas):
      - filas: lista de dicts (clave = encabezado de columna), cada uno
        con "__row_idx__" = número de fila real en el Sheet.
      - columnas: lista de encabezados en el orden del Sheet.
    Filas completamente vacías se descartan."""
    valores = ws.get_all_values()
    if not valores:
        return [], []
    columnas = valores[0]
    filas = []
    for row_idx, fila_valores in enumerate(valores[1:], start=2):
        if not any(c.strip() for c in fila_valores):
            continue
        fila = dict(zip(columnas, fila_valores))
        fila["__row_idx__"] = row_idx
        filas.append(fila)
    return filas, columnas


def actualizar_fila_sheet(ws, row_idx, columnas, valores):
    """Escribe una o más columnas de UNA fila puntual con un solo
    batch_update. Columnas que no existen en el Sheet se ignoran en
    silencio.

    Si row_idx cae fuera de la grilla actual de la hoja (confirmado en
    corrida real: una hoja de solo-agregar como VIGENCIAS llega al
    límite default de 1000 filas de una pestaña nueva de Sheets y la
    API devuelve "Range ... exceeds grid limits"), se agranda la hoja
    antes de escribir — por default gspread nunca hace esto solo.

    valores: dict {nombre_de_columna: valor}
    """
    updates = []
    for nombre_columna, valor in valores.items():
        if nombre_columna not in columnas:
            continue
        col_idx = columnas.index(nombre_columna) + 1
        updates.append({"range": rowcol_to_a1(row_idx, col_idx), "values": [[valor]]})
    if updates:
        if row_idx > ws.row_count:
            _con_reintentos_429(ws.add_rows, row_idx - ws.row_count)
        # ws.batch_update() modifica cada dict de "updates" in-place (le
        # antepone el nombre de la hoja al "range"), y absolute_range_name()
        # no es idempotente: si la lista ya mutada se reusa en un reintento
        # (p. ej. tras un 429), el nombre de la hoja se antepone de nuevo y
        # el "range" queda corrupto ("'HOJA'!'HOJA'!J69"), lo que hace fallar
        # el reintento con un 400 en vez de la escritura que se esperaba
        # lograr. Por eso cada intento recibe una copia nueva.
        _con_reintentos_429(lambda: ws.batch_update(copy.deepcopy(updates)))


def agregar_fila_sheet(ws, columnas, valores):
    """Agrega una fila nueva al final, por nombre de columna (no por
    posición fija) — a diferencia de ws.append_row(), no depende de que
    el Sheet tenga las columnas en un orden exacto, y evita el caso
    confirmado en que gspread ubica la fila nueva lejos de donde
    corresponde si hay alguna celda suelta más a la derecha de la tabla
    (rompe la detección de rango que usa append_row). Columnas que no
    existen en el Sheet se ignoran en silencio (mismo comportamiento
    que actualizar_fila_sheet).

    columnas: encabezados actuales del Sheet (ej. ws.row_values(1)).
    valores: dict {nombre_de_columna: valor}.
    Devuelve el row_idx de la fila escrita.

    El próximo row_idx se cachea en el propio objeto ws en vez de volver
    a leer el Sheet entero (ws.get_all_values()) en cada llamada: un
    script que agrega muchas filas seguidas en la misma corrida (ej.
    Exportar Notas, una fila por producto × nota) terminaba haciendo esa
    misma cantidad de lecturas a la API de Sheets y se quedaba sin cuota
    ("Quota exceeded ... Read requests per minute"). Solo la primera
    llamada de la corrida lee el Sheet; las siguientes cuentan en
    memoria, asumiendo que nada más le agrega filas a esta hoja en
    paralelo durante la misma corrida (cierto para las hojas de salida
    de estos scripts, que son de un solo escritor por vez).
    """
    row_idx = getattr(ws, "_siguiente_row_idx", None)
    if row_idx is None:
        row_idx = len(ws.get_all_values()) + 1
    actualizar_fila_sheet(ws, row_idx, columnas, valores)
    ws._siguiente_row_idx = row_idx + 1
    return row_idx
