"""Cancelacion cooperativa de una corrida en curso.

La app (Streamlit) deja un archivo "bandera" cuando el usuario aprieta
"Abortar" y cada script chequea su existencia entre fila y fila del Excel.
Al detectarla, el script corta el procesamiento y salta directo al
logout()/driver.quit() que cada script ya tiene en su finally — asi no
queda una sesion de Tourplan colgada consumiendo una licencia. Las filas
que no llegaron a procesarse quedan en PENDIENTE, listas para retomar en
la proxima corrida.

AbortadoPorUsuario hereda de BaseException (no de Exception) a proposito:
todos los scripts atrapan errores por fila con "except Exception" para no
frenar el resto del lote, y si esta señal fuera una Exception mas quedaria
tragada como un error de fila en vez de cortar la corrida.
"""
import os
from pathlib import Path

ABORT_EXIT_CODE = 75

_STOP_FILE = os.environ.get("TOURPLAN_STOP_FILE")


class AbortadoPorUsuario(BaseException):
    pass


def abort_solicitado():
    return bool(_STOP_FILE) and Path(_STOP_FILE).exists()


def chequear_abort():
    if abort_solicitado():
        raise AbortadoPorUsuario()
