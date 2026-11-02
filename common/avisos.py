"""
avisos.py — Aviso al terminar una corrida: notificación de escritorio y sonido de la PC donde corre la app.

La app corre en la propia PC, así que el aviso sale del sistema operativo y no depende de que la pestaña del navegador esté a la vista.
Nada de esto puede romper la app: si falta la librería o el sistema no lo soporta, se ignora (devuelve qué se pudo hacer).
"""

import subprocess
import sys


def _notificar_escritorio(titulo, mensaje):
    from plyer import notification
    notification.notify(title=titulo, message=mensaje, app_name="Actualización de costos", timeout=15)


def _sonar():
    if sys.platform.startswith("win"):
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    elif sys.platform == "darwin":
        subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
    else:
        raise OSError("sonido no disponible en este sistema")


def avisar(titulo, mensaje, escritorio=True, sonido=True):
    """Devuelve {'escritorio': bool, 'sonido': bool} (True = se pudo)."""
    hecho = {"escritorio": False, "sonido": False}
    if escritorio:
        try:
            _notificar_escritorio(titulo, mensaje)
            hecho["escritorio"] = True
        except Exception:
            pass
    if sonido:
        try:
            _sonar()
            hecho["sonido"] = True
        except Exception:
            pass
    return hecho
