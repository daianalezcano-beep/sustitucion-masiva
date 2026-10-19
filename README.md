# sustitucion-masiva

Actualización de costos en reservas de Tourplan NX por doble sustitución
(Booking Operations > Substitute Service): original -> option provisorio
(`000000`) -> original, conservando el precio de venta (Retain Sell Price).

**Estado:** Fase 1 (modo lectura) implementada y **sin correr todavía contra Tourplan ni contra
el Google Sheet**. El modo aplicar (ida y vuelta) es la Fase 2.

- `scripts/substitute_doble/diag_substitute.py` — diagnóstico (Fase 0, ya corrido; ver `docs/hallazgos_fase0.md`).
- `scripts/substitute_doble/substitute_doble.py` — script principal (hoy solo `TOURPLAN_MODO=lectura`).
- `common/` — helpers (copias de Drive-TP-NX-App: abort, sheets_client, user_config, chrome_bootstrap)
  más `logica.py` (lógica pura), `tp_selenium.py` y `sheets_batch.py`.
- `tests/test_logica.py` — `python -m unittest discover -s tests -v`.

## Correr el modo lectura (Windows, cmd)

```
set TOURPLAN_USERNAME=usuario
set TOURPLAN_PASSWORD=clave
set TOURPLAN_SHEET_URL=https://docs.google.com/spreadsheets/d/<ID>/edit
set TOURPLAN_FECHA_DESDE=20/10/26
set TOURPLAN_FECHA_HASTA=31/12/49
set TOURPLAN_MAX_RESERVAS=2000
set TOURPLAN_FILA=2
python scripts\substitute_doble\substitute_doble.py
```

- `TOURPLAN_FECHA_DESDE` es obligatoria (DD/MM/YY). `TOURPLAN_FECHA_HASTA` por defecto `31/12/49` (= 2049).
- `TOURPLAN_FILA=<n>` procesa solo esa fila del Sheet (para la primera prueba); sin ella, todas las PENDIENTE.
- En modo lectura el script también prueba "Select all" con el checkbox del encabezado de Results: verifica `Selected == Found` y
  deselecciona, sin tocar nunca OK (`TOURPLAN_PROBAR_SELECCION=0` lo desactiva). Es la misma mecánica que usará el modo aplicar.
- Hojas del Sheet: `SUBSTITUTE` (entrada), `PROVISORIOS` (config), `SUBSTITUTE_DETALLE` (salida).
- Usa las credenciales de Google de `~/.tourplan-nx-app/` (las mismas de Drive-TP-NX-App).
- Cada corrida crea su propia carpeta `salida\lectura_AAAAMMDD-HHMMSS\` (el diagnóstico, `diag_out\diag_AAAAMMDD-HHMMSS\`) con
  capturas, HTML, `panel_probe.json` y `consola.log` (todo lo que imprime el script). Para mandar una corrida, comprimir esa subcarpeta.
- En modo lectura la fila queda `PENDIENTE` con el conteo en `OBSERVACIONES`; solo cambian los errores y `DUPLICADO`.

## Hoja de ruta (orden de trabajo)

1. **Fase 1 — modo lectura** (implementada, falta probarla): una fila y después un lote chico. La entrada se arma a
   mano: reporte de Tourplan → quitar duplicados → pegar las combinaciones únicas en `SUBSTITUTE` con `ESTADO=PENDIENTE`
   (columnas del reporte: Location, Supplier, Service, Option → `LOCATION`, `SUPPLIER`, `SERVICE TYPE`, `CODIGO`).
2. **Fase 2 — modo aplicar** con una sola combinación (ida y vuelta, verificación final contra la foto inicial).
3. **Fase 3 — integración con la app**: fechas Desde/Hasta, tope `MAX_RESERVAS`, check manual de que no quedan
   `000000` pendientes, plantilla del Sheet.
4. **Fase 4 — flujo optimizado**: indicar solo Location + Service Type (o Supplier) y fecha Desde; la app genera el
   reporte de Tourplan, obtiene las combinaciones del CSV y hace los reemplazos. Requiere diagnosticar antes la
   pantalla del reporte (generación y descarga).
5. **Modo recuperación** (devolver al original lo que quedó en un provisorio): solo si se pide.
