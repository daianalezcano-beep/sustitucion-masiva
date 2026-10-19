# sustitucion-masiva

Actualización de costos en reservas de Tourplan NX por doble sustitución
(Booking Operations > Substitute Service): original -> option provisorio
(`000000`) -> original, conservando el precio de venta (Retain Sell Price).

**Estado:** Fase 1 (modo lectura) **probada contra Tourplan real con una fila**. Fase 2 (modo aplicar, ida y vuelta de UNA
combinación) y la **app local** implementadas y probadas por simulación (la app también en un navegador real contra un script
falso): **sin correr todavía el modo aplicar contra Tourplan**. Ver `docs/fase2_riesgos.md`.

- `scripts/substitute_doble/diag_substitute.py` — diagnóstico (Fase 0, ya corrido; ver `docs/hallazgos_fase0.md`).
- `scripts/substitute_doble/substitute_doble.py` — script principal (hoy solo `TOURPLAN_MODO=lectura`).
- `common/` — helpers (copias de Drive-TP-NX-App: abort, sheets_client, user_config, chrome_bootstrap)
  más `logica.py` (lógica pura), `tp_selenium.py` y `sheets_batch.py`.
- `tests/test_logica.py` — `python -m unittest discover -s tests -v`.

## App local (recomendado para probar)

Doble clic en `run_app.bat` (crea su entorno de Python la primera vez e instala lo necesario; necesita Python y Chrome). Abre la app en
el navegador. Para traer los cambios nuevos del repo, doble clic en `actualizar_app.bat` (hace `git pull`; la carpeta tiene que ser un
clon de git, y usa el `git` del PATH o el Git portátil de `C:\Users\daiana.lezcano\PortableGit`):

- **Configuración** (una vez por PC; se comparte con la otra app de Tourplan): usuario y contraseña de Tourplan y URL del Sheet (la URL del Sheet se carga solo acá, no en la pantalla principal).
- **Parámetros:** modo (solo revisar / aplicar), servicios desde (obligatoria; avisa si es anterior a hoy) y hasta (por defecto
  31/12/2049), tope de reservas por combinación, URL de Tourplan, "solo la fila nº" para probar una combinación.
- **Modo aplicar:** exige tildar que se revisó a mano que no hay códigos `000000` pendientes. Con "pedir confirmación antes de cada combinación"
  (por defecto) el script se detiene **una sola vez por combinación**, antes del OK de la ida, y la app muestra la pregunta con
  **Continuar / Cancelar** (y la captura del modal). La vuelta se hace sola: frenar entre ida y vuelta dejaría reservas en el provisorio.
- **Abortar:** corta al terminar la combinación en curso (completa) y hace logout.
- **Consola en vivo** (cada línea con la hora `[HH:MM:SS]`), descarga de la consola, **historial de corridas** (las últimas 15 de la carpeta
  `salida/`, con resultado y botón para descargar el `consola.log` de cada una), y estado de la cola (Pendiente / OK / Error / Duplicado) con **Refrescar**.
- Si la corrida termina con el aviso **🛑 Corrida detenida** (ver abajo) no se procesó ninguna otra combinación.

La app lanza `scripts/substitute_doble/substitute_doble.py` como subproceso con las mismas variables `TOURPLAN_*` de abajo.
La lógica sin interfaz está en `common/ejecucion.py` (con pruebas).

## Regla de seguridad: nunca empezar otra combinación con sustitutos sin asignar

Si una combinación termina en `ERROR_COLGADAS` (falló después de la ida: puede haber reservas en el provisorio), en `ERROR_REVISAR`
(resultado inesperado tras una sustitución real) o se detecta un provisorio con reservas (`ERROR: provisorio en uso`), la corrida
**se detiene** (código de salida 76): las filas restantes quedan `PENDIENTE` y no se empieza la ida de ninguna otra combinación. Además, al
arrancar en modo aplicar, el script se niega a correr si `SUBSTITUTE_DETALLE` tiene reservas en `FASE` `IDA_EN_CURSO` / `IDA` / `VUELTA`.
Los rechazos previos a tocar nada (`ERROR: no soportado en esta fase`, `ERROR: sin provisorio`, etc.) no detienen la corrida.

## Correr desde la consola (alternativa) — modo lectura (Windows, cmd)

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
- Hojas del Sheet: `SUBSTITUTE` (entrada) y `SUBSTITUTE_DETALLE` (salida). Los provisorios **no** están en el Sheet: viven en el código
  (`SUPPLIER_PROVISORIO` en `substitute_doble.py`): Location y Service Type son los de la fila, el código es siempre `000000`
  y el proveedor depende del Service Type (hoy `GU` → `GUIAPO`).
- Los encabezados del Sheet se aceptan con guion bajo o espacio y en cualquier combinación de mayúsculas
  (`SERVICE_TYPE` = `SERVICE TYPE`). `PROVISORIOS` necesita las 4 columnas completas en cada fila: `LOCATION`, `SERVICE TYPE`,
  `SUPPLIER_PROV`, `CODIGO_PROV` (también se acepta el formato `LOCATION`, `SUPPLIER`, `SERVICE_TYPE`, `CODIGO`, donde SUPPLIER y CODIGO
  son los del provisorio). Hay un provisorio por Location + Service Type y sirve para cualquier código de esa combinación; al arrancar el script imprime qué leyó de esa hoja.
- Usa las credenciales de Google de `~/.tourplan-nx-app/` (las mismas de Drive-TP-NX-App).
- Cada corrida crea su propia carpeta `salida\lectura_AAAAMMDD-HHMMSS\` (el diagnóstico, `diag_out\diag_AAAAMMDD-HHMMSS\`) con
  capturas, HTML, `panel_probe.json` y `consola.log` (todo lo que imprime el script). Para mandar una corrida, comprimir esa subcarpeta.
- En modo lectura la fila queda `PENDIENTE` con el conteo en `OBSERVACIONES`; solo cambian los errores y `DUPLICADO`.

## Modo aplicar (Fase 2) — MODIFICA reservas en producción

```
set TOURPLAN_MODO=aplicar
set TOURPLAN_CONFIRMO_SIN_PENDIENTES=SI
set TOURPLAN_FECHA_DESDE=01/11/26
set TOURPLAN_FECHA_HASTA=01/11/26
set TOURPLAN_FILA=2
python scripts\substitute_doble\substitute_doble.py
```

- Por cada combinación: verifica que el provisorio (`<Location>/<proveedor>/<Service Type>/000000`) existe y no tiene reservas
  (una vez por corrida, todo el historial) → foto de las reservas del original (11 columnas + panel derecho) → **ida** original →
  provisorio → verifica el provisorio → **vuelta** provisorio → original (repone estado de servicio y pickup/drop-off/remarks) →
  verificación final contra la foto (si el original quedó idéntico a la foto no se vuelve a consultar el provisorio: quedó vacío por construcción) → `ESTADO` `OK` / `ERROR_REVISAR` / `ERROR_COLGADAS`.
- `TOURPLAN_CONFIRMAR_OK=1` (default): se detiene una vez por combinación, antes del OK de la ida, y espera que se escriba `SI` en la consola (pensado para las
  primeras pruebas; con `0` no pregunta).
- `TOURPLAN_CONFIRMO_SIN_PENDIENTES=SI` es obligatorio: confirma que se revisó a mano que no quedan `000000` pendientes. Además el script
  no arranca si `SUBSTITUTE_DETALLE` tiene reservas en `FASE` `IDA_EN_CURSO` / `IDA` / `VUELTA` (una corrida anterior cortada).
- Sin la app, `TOURPLAN_CONFIRMAR_OK=1` pide escribir `SI` en la consola; con la app (`TOURPLAN_CONFIRM_DIR`) la confirmación es por botones.
- Límites de esta fase (se rechazan antes de tocar nada): un único estado de servicio por combinación, hasta `TOURPLAN_PANEL_MAX`
  (5) reservas y todas con los mismos datos de pickup/drop-off/remarks.
- `Retain Sell Price` y `Retain Voucher Text` no se tocan: antes de cada OK se verifica (por el texto del label) que sigan tildados.

## Hoja de ruta (orden de trabajo)

1. **Fase 1 — modo lectura** (probada con una fila real): falta un lote chico y un código con muchas filas. La entrada se arma a
   mano: reporte de Tourplan → quitar duplicados → pegar las combinaciones únicas en `SUBSTITUTE` con `ESTADO=PENDIENTE`
   (columnas del reporte: Location, Supplier, Service, Option → `LOCATION`, `SUPPLIER`, `SERVICE TYPE`, `CODIGO`).
2. **Fase 2 — modo aplicar** con una sola combinación (ida y vuelta, verificación final contra la foto inicial): implementada, falta correrla.
3. **Fase 3 — app local**: hecha (ver arriba). Pendiente: plantilla del Sheet.
4. **Fase 4 — flujo optimizado**: indicar solo Location + Service Type (o Supplier) y fecha Desde; la app genera el
   reporte de Tourplan, obtiene las combinaciones del CSV y hace los reemplazos. Requiere diagnosticar antes la
   pantalla del reporte (generación y descarga).
5. **Modo recuperación** (devolver al original lo que quedó en un provisorio): solo si se pide.

## Pendientes anotados

- **Listado de provisorios:** cuando estén creados todos los options `000000` (uno por Location × Service Type; con el CSV de GU son
  16 Locations: BUE, CAC, CAF, CHA, FTE, HUM, IGR, JUJ, PAI, PNT, PUR, RTU, SLA, TIL, TUC, USH), pasar la lista al código. Hoy
  `SUPPLIER_PROVISORIO = {"GU": "GUIAPO"}` define solo el proveedor por Service Type y la existencia de cada provisorio se
  valida en Tourplan al correr; con la lista, el código pasaría a aceptar solo las combinaciones Location + Service Type listadas.
- Selectores del modal Replacement Service que **no están en las grabaciones**: los campos de pickup/drop-off/Remarks y los Retain
  (se buscan por texto/ids del panel y se vuelcan HTML y captura antes de cada OK); confirmar en la primera corrida real.
- Soporte de combinaciones con varios estados de servicio, más reservas y datos de pickup distintos (hoy se rechazan).
- Modo recuperación (devolver al original lo que quedó en un provisorio), solo si se pide.
- Medir tiempos reales y optimizar (esperas por polling, reusar la pantalla entre combinaciones): ~55 s por combinación en lectura.
