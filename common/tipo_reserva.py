"""
tipo_reserva.py — Clasificación de las reservas en FITS / GRUPOS según su Department.

Sin Tourplan ni Streamlit. En Tourplan la Reference empieza con la sucursal (2 letras) y el Department (2 letras): `EAGP246283` es
sucursal `EA`, Department `GP` (Groups); `EAFI...` es `FI` (Fit Bookings); `TDGR...` es sucursal `TD`, Department `GR`. El filtro
«Department» de Substitute Service permite entonces procesar por separado las reservas individuales y las de grupo (en las de
grupo, en producción, la doble sustitución dejó la venta en cero: ver docs/incidente_2026-10-27_venta_en_reservas_de_grupo.md).

La tabla sale de la configuración de Tourplan (para cada Department, si aplica a grupos y/o a individuales). Un Department que
aplica a ambos o a ninguno NO se puede clasificar: sus reservas no se procesan hasta clasificarlo (editar esta tabla).
"""

TIPO_FITS, TIPO_GRUPOS, TIPO_AMBOS = "FITS", "GRUPOS", "AMBOS"
TIPOS_DE_FILA = (TIPO_FITS, TIPO_GRUPOS)
MODOS = (TIPO_FITS, TIPO_GRUPOS, TIPO_AMBOS)

# Solo individuales / solo grupos, según Tourplan.
DEPARTAMENTOS_FITS = frozenset({"FG", "FI", "FM", "FT", "MI", "OP", "PE", "PI", "PU"})
DEPARTAMENTOS_GRUPOS = frozenset({"GP", "GR", "IN"})
# Sin clasificar (no se procesan): marcados para ambos (BO, CF, FL, PR, SL, US) o para ninguno (el vacío, AC, RD).
DEPARTAMENTOS_SIN_CLASIFICAR = frozenset({"", "AC", "BO", "CF", "FL", "PR", "RD", "SL", "US"})

_SINONIMOS = {"FIT": TIPO_FITS, "FITS": TIPO_FITS, "INDIVIDUAL": TIPO_FITS, "INDIVIDUALES": TIPO_FITS,
              "GRUPO": TIPO_GRUPOS, "GRUPOS": TIPO_GRUPOS, "GROUP": TIPO_GRUPOS, "GROUPS": TIPO_GRUPOS}


def departamento_de(referencia):
    """Department de una Reference (letras 3 y 4), en mayúsculas; vacío si la referencia es demasiado corta."""
    ref = str(referencia or "").strip().upper()
    return ref[2:4] if len(ref) >= 4 else ""


def tipo_de_departamento(departamento):
    """'FITS', 'GRUPOS' o None (sin clasificar)."""
    dep = str(departamento or "").strip().upper()
    if dep in DEPARTAMENTOS_FITS:
        return TIPO_FITS
    if dep in DEPARTAMENTOS_GRUPOS:
        return TIPO_GRUPOS
    return None


def tipo_de_reserva(referencia):
    """'FITS', 'GRUPOS' o None (Department sin clasificar) para una Reference."""
    return tipo_de_departamento(departamento_de(referencia))


def normalizar_tipo(valor):
    """Valor de la columna TIPO del Sheet -> 'FITS', 'GRUPOS' o '' (vacío: fila anterior a esta columna). None si no se entiende."""
    v = str(valor or "").strip().upper()
    return "" if not v else _SINONIMOS.get(v)


def departamentos_para(tipo):
    """Departments que se tildan en el filtro para ese tipo de fila ('' = fila sin TIPO: FITS y GRUPOS juntos, nunca los sin clasificar)."""
    if tipo == TIPO_FITS:
        return DEPARTAMENTOS_FITS
    if tipo == TIPO_GRUPOS:
        return DEPARTAMENTOS_GRUPOS
    return DEPARTAMENTOS_FITS | DEPARTAMENTOS_GRUPOS


def fila_corresponde(tipo_fila, modo):
    """¿Una fila del Sheet con ese TIPO se procesa en ese modo de la app? En AMBOS, todas (también las anteriores a la columna TIPO);
    en FITS o GRUPOS, solo las del mismo tipo."""
    return True if modo == TIPO_AMBOS else tipo_fila == modo
