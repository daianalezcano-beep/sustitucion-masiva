"""
resultado.py — Resumen de una corrida para la usuaria, a partir de la consola del script (sin Streamlit ni Selenium).

- `resumir_corrida(lineas)`: qué pasó con cada combinación (OK / a revisar / error), cuántas líneas se excluyeron y si la corrida se detuvo.
- `explicar(estado)`: cada estado técnico del script (ERROR_COLGADAS, ERROR_REVISAR…) traducido a qué pasó, si hay riesgo y qué hacer.
- `ritmo_por_combinacion` / `tiempo_estimado`: cuánto tarda una combinación según corridas anteriores (para la vista previa).
"""

import re

_HORA = re.compile(r"^\[\d\d:\d\d:\d\d\]\s*")
_FILA = re.compile(r"^Fila (\d+): (.+)$")
_RESULTADO = re.compile(r"^\s*(✅|⚠️?|❌)\s*(.*)$")
_ESTADO = re.compile(r"^(ERROR_[A-Z]+|ERROR: [^:]+|[A-ZÁÉÍÓÚ_]+)(?::\s*(.*))?$")
_FIN = re.compile(r"Duración: (\d+)m (\d+)s · filas OK=(\d+) ERROR=(\d+)")

# Estados que dejan algo en riesgo (se muestran primero y en rojo).
GRAVES = ("ERROR_COLGADAS", "ERROR: provisorio en uso")


def _limpia(linea):
    return _HORA.sub("", str(linea).rstrip("\n"))


def clave_de_estado(estado):
    """Estado crudo del script -> clave de explicar(): 'OK', 'LEIDA', 'COLGADAS', 'REVISAR', 'PROVISORIO_EN_USO', 'NO_SUSTITUIDA', 'EXCLUIDA' o 'OTRO'."""
    e = str(estado or "").strip().upper()
    if e == "OK":
        return "OK"
    if e == "LEIDA":
        return "LEIDA"
    if e.startswith("ERROR_COLGADAS"):
        return "COLGADAS"
    if e.startswith("ERROR_REVISAR"):
        return "REVISAR"
    if "PROVISORIO EN USO" in e:
        return "PROVISORIO_EN_USO"
    if "NO SUSTITUIDA" in e:
        return "NO_SUSTITUIDA"
    if e.startswith("EXCLUIDA"):
        return "EXCLUIDA"
    return "OTRO"


def resumir_corrida(lineas):
    """Lee la consola de una corrida. Devuelve:
    {'combinaciones': [{'fila', 'nombre', 'estado', 'clave', 'detalle', 'reservas'}], 'excluidas': n, 'detenida': bool, 'a_medio_camino': bool,
     'duracion': 'XmYYs' o None}. `a_medio_camino`: el script se negó a arrancar porque SUBSTITUTE_DETALLE tiene reservas a mitad de camino."""
    comb, actual = [], None
    out = {"combinaciones": comb, "excluidas": 0, "detenida": False, "a_medio_camino": False, "duracion": None}
    for crudo in lineas:
        linea = _limpia(crudo)
        m = _FILA.match(linea)
        if m:
            actual = {"fila": int(m.group(1)), "nombre": m.group(2).strip(), "estado": None, "clave": None, "detalle": "", "reservas": None}
            comb.append(actual)
            continue
        m = re.search(r"Excluidas por costo manualizado \(revisar a mano\): (\d+)", linea)
        if m:
            out["excluidas"] = int(m.group(1))
        if "CORRIDA DETENIDA" in linea:
            out["detenida"] = True
        if "SUBSTITUTE_DETALLE tiene reservas a mitad de camino" in linea:
            out["a_medio_camino"] = True
        m = _FIN.search(linea)
        if m:
            out["duracion"] = f"{m.group(1)}m {int(m.group(2)):02d}s"
        m = _RESULTADO.match(linea)
        if m and actual is not None and actual["estado"] is None:
            cuerpo = m.group(2).strip()
            leida = re.match(r"Leída: (\d+) reserva", cuerpo)
            if leida:
                actual.update(estado="LEIDA", clave="LEIDA", reservas=int(leida.group(1)))
                continue
            e = _ESTADO.match(cuerpo)
            if e:
                actual.update(estado=e.group(1).strip(), clave=clave_de_estado(e.group(1)), detalle=(e.group(2) or "").strip())
    return out


def _fila_a_revisar(x):
    return x["clave"] not in ("OK", "LEIDA", None)


def explicar(clave):
    """(qué pasó, riesgo, qué hacer) para una clave de estado; None si no hay nada que explicar (OK / LEIDA)."""
    return {
        "COLGADAS": ("Falló después de mover las reservas al provisorio.",
                     "SÍ: puede haber reservas en el provisorio (1EURO1 / 000000) con costo de prueba.",
                     "No vuelvas a ejecutar. En Tourplan, buscá el provisorio y devolvé esas reservas a su opción original; después corregí la FASE en "
                     "SUBSTITUTE_DETALLE (IDA_EN_CURSO / IDA / VUELTA) y seguí docs/fase2_riesgos.md."),
        "REVISAR": ("Las reservas volvieron, pero algo quedó distinto de lo que había antes de la corrida.",
                    "Bajo: el provisorio se verificó vacío. Lo que cambió está detallado en OBSERVACIONES (DIFERENCIAS).",
                    "Compará esas reservas en Tourplan. Si están bien (por ejemplo, otra persona las editó a la vez), pasá la fila a OK en SUBSTITUTE y volvé a ejecutar "
                    "lo que falta."),
        "PROVISORIO_EN_USO": ("El provisorio ya tenía reservas antes de empezar, así que no se procesó ninguna combinación.",
                              "SÍ: son reservas de una corrida anterior que quedaron sin asignar (en esta corrida no se movió nada).",
                              "Devolvé esas reservas a su opción original en Tourplan y volvé a ejecutar. Revisá la fase en SUBSTITUTE_DETALLE."),
        "NO_SUSTITUIDA": ("Tourplan no sustituyó las reservas de esta combinación.",
                          "No: siguen en la opción original, sin cambios.",
                          "Revisala a mano en Tourplan. Si querés reintentar, poné la fila en PENDIENTE."),
        "EXCLUIDA": ("Todas las reservas de la combinación tienen costo manualizado, así que no se tocó nada.",
                     "No.", "Actualizá esos costos a mano."),
        "OTRO": ("La combinación falló antes de mover nada.",
                 "No hay indicios de reservas en el provisorio (si las hubiera, el estado sería ERROR_COLGADAS).",
                 "Leé el detalle en OBSERVACIONES y la consola. Si se arregló la causa, poné la fila en PENDIENTE y volvé a ejecutar."),
    }.get(clave)


def explicar_bloqueo_previo():
    """Cuando el script no arranca por una corrida anterior cortada."""
    return ("El script no arrancó: SUBSTITUTE_DETALLE tiene reservas a mitad de camino de una corrida anterior.",
            "Posible: pueden haber quedado en el provisorio.",
            "Mirá en Tourplan que no quede nada en el provisorio y corregí la FASE de esas reservas en SUBSTITUTE_DETALLE (IDA_EN_CURSO / IDA / VUELTA). "
            "Después volvé a ejecutar.")


# ── Tiempo estimado (vista previa) ──────────────────────────────────────────

def ritmo_por_combinacion(logs, minimo_filas=3):
    """Segundos por combinación según corridas anteriores del mismo modo (promedio ponderado). None si no hay datos suficientes.
    `logs`: textos de consola.log; solo cuentan corridas completas con al menos `minimo_filas` combinaciones."""
    seg, filas = 0, 0
    for texto in logs:
        m = _FIN.search(texto)
        if not m:
            continue
        n = int(m.group(3)) + int(m.group(4))
        if n >= minimo_filas:
            seg += int(m.group(1)) * 60 + int(m.group(2))
            filas += n
    return seg / filas if filas else None


def tiempo_estimado(logs, n_combinaciones):
    """Segundos estimados para n combinaciones, o None si no hay corridas anteriores comparables."""
    ritmo = ritmo_por_combinacion(logs)
    return int(round(ritmo * n_combinaciones)) if ritmo and n_combinaciones else None
