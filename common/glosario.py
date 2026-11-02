"""
glosario.py — Explicaciones en lenguaje simple de los estados y resultados que muestra la app (cola, Sheet y comparador).

Son textos para quien no es técnico: qué significa cada uno y qué hacer. Los usa app.py en los tooltips, en el glosario y en la guía.
"""

# Cola de trabajo (los cinco contadores).
COLA = {
    "Pendiente": "Combinaciones cargadas que todavía no se procesaron.",
    "OK": "Procesadas: el costo se actualizó y la verificación final dio igual que antes (la venta no cambia).",
    "Error": "No se pudieron procesar. El motivo está en la columna OBSERVACIONES del Sheet (abajo, qué significa cada error).",
    "Duplicado": "La misma combinación estaba repetida en el Sheet: se procesa una sola vez.",
    "Otro": "Otros estados, por ejemplo EXCLUIDA (todo con costo manualizado) o DESCARTADA (código genérico 700/600); no se procesan.",
}

# Estados de la columna ESTADO del Sheet: (estado, qué significa, qué hacer).
ESTADOS_SHEET = [
    ("PENDIENTE", "Todavía no se procesó.", "Nada: se procesa en la próxima corrida."),
    ("OK", "Se actualizó el costo y la verificación final dio igual que antes.", "Nada."),
    ("ERROR_COLGADAS", "Pudieron quedar reservas en el servicio provisorio (1EURO1 / 000000). La corrida se detiene para no empeorar.",
     "Revisar en Tourplan que no queden reservas en el provisorio y corregirlas antes de volver a correr (el detalle está en SUBSTITUTE_DETALLE)."),
    ("ERROR_REVISAR", "La sustitución terminó, pero la verificación final encontró diferencias con lo que había antes. La corrida se detiene.",
     "Revisar esas reservas en Tourplan y compararlas con el reporte PRE."),
    ("ERROR: provisorio en uso", "El servicio provisorio ya tiene reservas de otra corrida. La corrida se detiene.", "Liberarlo antes de volver a correr."),
    ("ERROR: no sustituida", "Tourplan no sustituyó alguna reserva del grupo (por ejemplo, de un estado de servicio que no admite el cambio). La corrida sigue con las demás.",
     "Revisar esa reserva en Tourplan; si hace falta, actualizarla a mano."),
    ("ERROR: sin provisorio / provisorio inexistente", "No hay provisorio definido (o no existe en Tourplan) para esa Location y Service Type.",
     "Avisar a quien mantiene la herramienta para definirlo."),
    ("ERROR: sin coincidencia / código / campo distinto / fecha", "Tourplan no encontró o no aceptó un dato de la fila (Location, Service, Supplier, código o fecha).",
     "Revisar que esos datos existan y estén bien escritos en el Sheet."),
    ("ERROR: Results / selección / pantalla / lectura incompleta", "La pantalla de Tourplan no se comportó como se esperaba (por ejemplo, alguien tocó la ventana de Chrome). "
     "Suele ocurrir antes de tocar reservas.", "Volver a correr esa fila sin tocar Chrome mientras corre."),
    ("ERROR: pickup/dropoff", "Hay datos de pickup, drop-off o Remarks que la herramienta no repone para ese tipo de servicio; no se tocó nada.",
     "Procesar esa combinación aparte, o avisar a quien mantiene la herramienta."),
    ("ERROR: filtro de estado / department", "No se pudo dejar un filtro de Tourplan como se pidió.", "Volver a correr esa fila."),
    ("ERROR: tipo inválido", "La columna TIPO tiene un valor que no se entiende (solo si se separa por tipo en la configuración).", "Corregirlo a FITS, GRUPOS o PROMO, o dejarlo vacío."),
    ("DUPLICADO", "La combinación estaba repetida en el Sheet.", "Nada: se procesó la otra fila."),
    ("EXCLUIDA", "Todas las reservas de la combinación tienen el costo manualizado (Override) según la lista de exclusiones: no se procesó nada.",
     "Revisar esas líneas y actualizar el costo a mano en Tourplan."),
    ("OK con líneas excluidas", "La combinación se procesó, pero algunas líneas se dejaron afuera por tener costo manualizado (o por no poder distinguirse de una que lo tiene). "
     "Figuran en OBSERVACIONES, en SUBSTITUTE_DETALLE (fase EXCLUIDA) y en el resumen del final de la corrida.", "Revisar esas líneas a mano."),
    ("DESCARTADA", "Código genérico (700 o 600 + tres letras): valor cero que se manualiza. No se procesa.", "Nada."),
]

# Resultados del comparador de reportes (antes / después).
COMPARACION = {
    "Venta modificada": "El Retail o el Agent de la línea cambió. No debería pasar: son las líneas para corregir en Tourplan.",
    "Costo cambió": "Cambió el costo de la línea (lo esperado) y la venta quedó igual.",
    "Otros cambios": "Cambió otro dato (estado, pax, cantidad, moneda, voucher…) sin que cambie la venta.",
    "Solo antes / Solo después": "La línea está en un reporte y no en el otro (por ejemplo, un guía asignado entre los dos reportes).",
    "Sin cambios": "La línea quedó igual.",
    "Control": "Líneas que la herramienta no toca (proveedores «por asignar» y, si se separa por tipo, el otro tipo de reservas). Si la venta también cambió ahí, la causa no es la sustitución.",
    "Tipo": "FITS (individuales), GRUPOS, PROMO (promoción) o SIN CLASIFICAR, según el Department de la reserva. Es informativo: se muestra siempre.",
}


def markdown_estados():
    filas = ["| Estado | Qué significa | Qué hacer |", "|---|---|---|"]
    filas += [f"| `{e}` | {m} | {h} |" for e, m, h in ESTADOS_SHEET]
    return "\n".join(filas)


def markdown_cola():
    return "\n".join(f"- **{k}:** {v}" for k, v in COLA.items())


def markdown_comparacion():
    return "\n".join(f"- **{k}:** {v}" for k, v in COMPARACION.items())
