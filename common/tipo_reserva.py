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
TIPO_PROMO = "PROMO"          # viajes de promoción (Department PR): pueden ser individuales o de grupo; se procesan en cualquier modo
TIPOS_DE_FILA = (TIPO_FITS, TIPO_GRUPOS, TIPO_PROMO)
MODOS = (TIPO_FITS, TIPO_GRUPOS, TIPO_AMBOS)

# Solo individuales / solo grupos, según Tourplan. FG («Grupos en FITS») figura como individual en Tourplan, pero son files que se
# desprenden de un grupo y se trabajan también desde el módulo de grupos: se tratan como GRUPOS.
DEPARTAMENTOS_FITS = frozenset({"FI", "FM", "FT", "MI", "OP", "PE", "PI", "PU"})
DEPARTAMENTOS_GRUPOS = frozenset({"FG", "GP", "GR", "IN"})
# PR («Promoción Comercial Receptivo»): viajes de promoción, tanto individuales como de grupo. No se descarta: se procesa en cualquier modo (FITS, GRUPOS
# o AMBOS; una fila ya procesada deja de estar pendiente), con su propio filtro Department. Si tiene costos manualizados, la lista de exclusiones lo cubre.
DEPARTAMENTOS_PROMO = frozenset({"PR"})
# Sin clasificar (no se procesan): marcados para ambos (BO, CF, FL, SL, US) o para ninguno (el vacío, AC, RD).
DEPARTAMENTOS_SIN_CLASIFICAR = frozenset({"", "AC", "BO", "CF", "FL", "RD", "SL", "US"})

_SINONIMOS = {"FIT": TIPO_FITS, "FITS": TIPO_FITS, "INDIVIDUAL": TIPO_FITS, "INDIVIDUALES": TIPO_FITS,
              "GRUPO": TIPO_GRUPOS, "GRUPOS": TIPO_GRUPOS, "GROUP": TIPO_GRUPOS, "GROUPS": TIPO_GRUPOS,
              "PROMO": TIPO_PROMO, "PROMOCION": TIPO_PROMO, "PROMOCIÓN": TIPO_PROMO}


def departamento_de(referencia):
    """Department de una Reference (letras 3 y 4), en mayúsculas; vacío si la referencia es demasiado corta."""
    ref = str(referencia or "").strip().upper()
    return ref[2:4] if len(ref) >= 4 else ""


def tipo_de_departamento(departamento):
    """'FITS', 'GRUPOS', 'PROMO' o None (sin clasificar)."""
    dep = str(departamento or "").strip().upper()
    if dep in DEPARTAMENTOS_PROMO:
        return TIPO_PROMO
    if dep in DEPARTAMENTOS_FITS:
        return TIPO_FITS
    if dep in DEPARTAMENTOS_GRUPOS:
        return TIPO_GRUPOS
    return None


def tipo_de_reserva(referencia):
    """'FITS', 'GRUPOS', 'PROMO' o None (Department sin clasificar) para una Reference."""
    return tipo_de_departamento(departamento_de(referencia))


def normalizar_tipo(valor):
    """Valor de la columna TIPO del Sheet -> 'FITS', 'GRUPOS', 'PROMO' o '' (vacío: fila anterior a esta columna). None si no se entiende."""
    v = str(valor or "").strip().upper()
    return "" if not v else _SINONIMOS.get(v)


def departamentos_para(tipo):
    """Departments que se tildan en el filtro para ese tipo de fila ('' = fila sin TIPO: todos los clasificados juntos, nunca los sin clasificar)."""
    if tipo == TIPO_FITS:
        return DEPARTAMENTOS_FITS
    if tipo == TIPO_GRUPOS:
        return DEPARTAMENTOS_GRUPOS
    if tipo == TIPO_PROMO:
        return DEPARTAMENTOS_PROMO
    return DEPARTAMENTOS_FITS | DEPARTAMENTOS_GRUPOS | DEPARTAMENTOS_PROMO


def fila_corresponde(tipo_fila, modo):
    """¿Una fila del Sheet con ese TIPO se procesa en ese modo de la app? En AMBOS, todas (también las anteriores a la columna TIPO); en FITS o GRUPOS,
    las del mismo tipo y las de promoción (PROMO), que sirven para cualquier modo."""
    return True if modo == TIPO_AMBOS else tipo_fila in (modo, TIPO_PROMO)
