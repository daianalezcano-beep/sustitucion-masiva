"""
sheets_batch.py — Escritura en lote a Google Sheets.

agregar_fila_sheet (sheets_client.py) escribe una fila por llamada; con la
cuota de ~60 escrituras por minuto, volcar miles de filas de detalle así
tardaría horas. Acá se agregan N filas por llamada (por nombre de columna),
reusando el reintento ante 429 de sheets_client.
"""

import copy

from common.logica import norm_enc
from common.sheets_client import _con_reintentos_429


def agregar_filas_batch(ws, columnas, filas, tamano_lote=400):
    """Agrega al final de la hoja las filas de `filas` (lista de dicts
    {columna: valor}), `tamano_lote` por llamada. Columnas que el dict no trae
    quedan vacías; claves que no existen en la hoja se ignoran. RAW: los
    valores se guardan tal cual (una fecha 01/Nov/2026 no se convierte).
    Devuelve la cantidad de filas escritas."""
    if not filas:
        return 0
    matriz = []
    for f in filas:
        nf = {norm_enc(k): v for k, v in f.items()}
        matriz.append(["" if nf.get(norm_enc(c)) is None else str(nf.get(norm_enc(c))) for c in columnas])
    for i in range(0, len(matriz), tamano_lote):
        lote = matriz[i:i + tamano_lote]
        _con_reintentos_429(lambda l=lote: ws.append_rows(
            copy.deepcopy(l), value_input_option="RAW", table_range="A1"))
    return len(matriz)


def asegurar_columnas_tolerante(ws, columnas_requeridas):
    """Como sheets_client.asegurar_columnas, pero un encabezado equivalente
    ('SERVICE_TYPE' vs 'SERVICE TYPE') cuenta como existente: no agrega duplicados.
    Nunca renombra ni reordena las columnas que ya existen."""
    headers = ws.row_values(1)
    existentes = {norm_enc(h) for h in headers}
    faltantes = [c for c in columnas_requeridas if norm_enc(c) not in existentes]
    if faltantes:
        _con_reintentos_429(ws.update, range_name="A1", values=[headers + faltantes])
    return faltantes
