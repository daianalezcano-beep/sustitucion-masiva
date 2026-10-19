"""
sheets_batch.py — Escritura en lote a Google Sheets.

agregar_fila_sheet (sheets_client.py) escribe una fila por llamada; con la
cuota de ~60 escrituras por minuto, volcar miles de filas de detalle así
tardaría horas. Acá se agregan N filas por llamada (por nombre de columna),
reusando el reintento ante 429 de sheets_client.
"""

import copy

from common.sheets_client import _con_reintentos_429


def agregar_filas_batch(ws, columnas, filas, tamano_lote=400):
    """Agrega al final de la hoja las filas de `filas` (lista de dicts
    {columna: valor}), `tamano_lote` por llamada. Columnas que el dict no trae
    quedan vacías; claves que no existen en la hoja se ignoran. RAW: los
    valores se guardan tal cual (una fecha 01/Nov/2026 no se convierte).
    Devuelve la cantidad de filas escritas."""
    if not filas:
        return 0
    matriz = [[str(f.get(c, "") if f.get(c, "") is not None else "") for c in columnas] for f in filas]
    for i in range(0, len(matriz), tamano_lote):
        lote = matriz[i:i + tamano_lote]
        _con_reintentos_429(lambda l=lote: ws.append_rows(
            copy.deepcopy(l), value_input_option="RAW", table_range="A1"))
    return len(matriz)
