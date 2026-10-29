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

- **Pantalla:** encabezado con el engranaje de Configuración y chips de estado; barra de corrida pegada arriba mientras corre (o al terminar); cola de trabajo; dos pasos —**1 · Ejecutar** y **2 · Comparar**, con su estado en la etiqueta—; consola e historial. Los dos pasos se dibujan siempre y se oculta el inactivo (así no se pierde lo cargado). Color principal azul en `.streamlit/config.toml`; el rojo queda para Aplicar y errores.
- **Configuración** (engranaje; una vez por PC; se comparte con la otra app de Tourplan; se abre sola si falta el usuario): usuario y contraseña de Tourplan y URL del Sheet (la URL del Sheet se carga solo acá).
- **Paso 1 · Ejecutar** (una tarjeta con 4 secciones: combinaciones, modo y fechas, PRE, ejecutar): modo (solo revisar / aplicar), servicios desde (obligatoria; avisa si es anterior a hoy) y hasta (por defecto
  31/12/2049), URL de Tourplan, "solo la fila nº" para probar una combinación.
- **Modo aplicar:** la app ya no pide confirmación manual (se quitó el checkbox): las verificaciones del script (provisorio libre antes y después, foto original vs. final) la reemplazan. Con `TOURPLAN_CONFIRMAR_OK=1` (solo desde la consola) el script se detiene **una sola vez por combinación**, antes del OK de la ida, y la app muestra la pregunta con
  **Continuar / Cancelar** (y la captura del modal). La vuelta se hace sola: frenar entre ida y vuelta dejaría reservas en el provisorio.
- **Cargar combinaciones desde un reporte de Tourplan:** sección 1 del paso Ejecutar (opción «Cargar un reporte nuevo»). Se sube el reporte **Service Status** (CSV); la app saca los servicios
  cancelados (`CX`), deja una línea por combinación (Location + Supplier + Service Type + Option), descarta las que no tienen provisorio, ya están en la
  hoja o tienen código genérico (`700` o `600` + tres letras, ej. `700GUI`: valor cero que se manualiza), y muestra una vista previa (combinaciones, reservas, tiempo estimado, rango de fechas). **Solo al confirmar** las agrega a `SUBSTITUTE` como
  `PENDIENTE` (ordenadas por Location, Service Type, Supplier y código, para que el script recargue lo mínimo); después se ejecuta como siempre.
  Lógica en `common/reporte_entrada.py`.
- **Cola de trabajo:** se lee sola al abrir la página, al analizar un reporte (la hoja ya se leyó para detectar duplicados) y al confirmar una carga; **Refrescar** la vuelve a leer a mano. Mientras corre se vuelve a leer la hoja cada ~10 s y se muestra el avance (`combinación k de N`). En modo lectura las filas
  siguen `PENDIENTE` por diseño (solo cambian los errores), así que el contador no se mueve: el avance se ve en la barra.
- **Comparar reportes antes / después:** paso 2 · Comparar. Se suben los dos reportes Product Analysis (CSV, exportados con las mismas
  opciones), se toca **Comparar** y muestra por línea cuánto cambió el costo y si cambió la venta (Retail / Agent), con las líneas con venta modificada
  primero y un CSV descargable. Lógica en `common/comparador.py` (acepta `;` o `,` y fechas `21-oct-26` o `21-Oct-2026`; no guarda los archivos).
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
set TOURPLAN_FILA=2
python scripts\substitute_doble\substitute_doble.py
```

- `TOURPLAN_FECHA_DESDE` es obligatoria (DD/MM/YY). `TOURPLAN_FECHA_HASTA` por defecto `31/12/49` (= 2049).
- `TOURPLAN_FILA=<n>` procesa solo esa fila del Sheet (para la primera prueba); sin ella, todas las PENDIENTE.
- En modo lectura el script también prueba "Select all" con el checkbox del encabezado de Results: verifica `Selected == Found` y
  deselecciona, sin tocar nunca OK (`TOURPLAN_PROBAR_SELECCION=0` lo desactiva). Es la misma mecánica que usará el modo aplicar.
- En modo lectura también se **prueba el filtro Service Status**: por cada estado que aparece en Results (sin filtrar) vuelve a consultar con solo ese
  estado tildado y comprueba que devuelve exactamente esas reservas; el resultado queda en OBSERVACIONES (`filtro_estado={...}`).
  No modifica nada (`TOURPLAN_PROBAR_FILTRO_ESTADO=0` lo desactiva).
- Hojas del Sheet: `SUBSTITUTE` (entrada) y `SUBSTITUTE_DETALLE` (salida). Los provisorios **no** están en el Sheet: viven en el código
  (`PROVISORIOS_POR_LOCATION` en `substitute_doble.py`): hay uno por Location + Service Type (116 combinaciones, solo options
  Z*NO USAR*), siempre con proveedor `1EURO1` y código `000000`. Si la Location + Service Type de una fila no está en esa tabla, la fila
  termina en `ERROR: sin provisorio` sin abrir Tourplan.
- Los encabezados del Sheet se aceptan con guion bajo o espacio y en cualquier combinación de mayúsculas
  (`SERVICE_TYPE` = `SERVICE TYPE`).
- Usa las credenciales de Google de `~/.tourplan-nx-app/` (las mismas de Drive-TP-NX-App).
- Cada corrida crea su propia carpeta `salida\lectura_AAAAMMDD-HHMMSS\` (el diagnóstico, `diag_out\diag_AAAAMMDD-HHMMSS\`) con
  capturas, HTML, `panel_probe.json` y `consola.log` (todo lo que imprime el script). Para mandar una corrida, comprimir esa subcarpeta.
- En modo lectura la fila queda `PENDIENTE` con el conteo en `OBSERVACIONES`; solo cambian los errores, `DUPLICADO` y `DESCARTADA` (código genérico 700/600 + 3 letras, que el script no procesa aunque esté cargado como PENDIENTE).

## Modo aplicar (Fase 2) — MODIFICA reservas en producción

```
set TOURPLAN_MODO=aplicar
set TOURPLAN_FECHA_DESDE=01/11/26
set TOURPLAN_FECHA_HASTA=01/11/26
set TOURPLAN_FILA=2
python scripts\substitute_doble\substitute_doble.py
```

- **Antes de tocar nada** (verificación previa): para cada Location + Service Type del input comprueba que su provisorio
  (`<Location>/1EURO1/<Service Type>/000000`) existe y no tiene reservas (todo el historial). Un par sin provisorio utilizable deja sus filas en
  ERROR; si algún provisorio tiene reservas (sustitutos sin asignar) **no se procesa ninguna combinación**.
- Por cada combinación: si su provisorio ya se usó antes en la corrida, lo vuelve a revisar antes de empezar → foto de las reservas del original
  (columnas del listado, incluida la celda PU/DO) → **ida** original →
  provisorio → verifica el provisorio → **vuelta** provisorio → original (repone estado de servicio y pickup/drop-off/remarks) →
  verificación final contra la foto (si el original quedó idéntico a la foto no se vuelve a consultar el provisorio: quedó vacío por construcción) → `ESTADO` `OK` / `ERROR_REVISAR` / `ERROR_COLGADAS`.
- `TOURPLAN_CONFIRMAR_OK=1` (default): se detiene una vez por combinación, antes del OK de la ida, y espera que se escriba `SI` en la consola (pensado para las
  primeras pruebas; con `0` no pregunta).
- El script no arranca si `SUBSTITUTE_DETALLE` tiene reservas en `FASE` `IDA_EN_CURSO` / `IDA` / `VUELTA` (una corrida anterior cortada). Que los
  `000000` estén libres lo comprueba el propio script en Tourplan (verificación previa); ya no hay que tildar nada a mano.
- Sin la app, `TOURPLAN_CONFIRMAR_OK=1` pide escribir `SI` en la consola; con la app (`TOURPLAN_CONFIRM_DIR`) la confirmación es por botones.
- **Tiempos:** el script usa esperas activas (espera a que el campo, el diálogo o el listado estén listos) en lugar de pausas fijas, y al volver a
  buscar solo completa los campos del filtro que difieren de lo que la pantalla ya tiene (lee el valor real de cada campo; si cambia Location,
  Service o Supplier recarga también los que dependen de él). Al volver de Results acepta un Found solo si el listado cambió o pasó un mínimo fijo.
- **Sin tope de reservas.** Si la combinación tiene varios estados de servicio (columna STATUS de Results, con códigos como IT, OK,
  OT), se procesa **un estado por vez** usando el filtro Service Status de Tourplan (solo ese estado tildado) y "Select all". El
  glosario código → texto del filtro está en `SERVICE_STATUS` (`substitute_doble.py`); `CC` y `LC` se llaman igual en el filtro y se
  distinguen mirando qué código devuelve Results. Un estado fuera del glosario (o vacío) rechaza la combinación antes de tocar nada.
- **Pickup / drop-off / Remarks:** se leen de la celda PU/DO del listado (ya no del panel derecho). Solo se analizan y reponen en los
  Service Types definidos en el script (`PUDO_SERVICE_TYPES_FIJOS`, hoy ninguno; ej. `{"CO", "TR"}`; la variable `TOURPLAN_PUDO_SERVICE_TYPES` permite sumar otros solo para probar). En los demás se asume que no corresponden:
  si igual hay datos, la combinación se rechaza sin tocar nada. En los habilitados, dentro de cada estado los datos deben ser iguales
  (un solo modal los repone); si no, se rechaza (pendiente de resolver; depende de comprobar si Tourplan los conserva solo).
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

- **La venta (validado en producción, 2026-10-20):** con una línea de venta distinta de cero (`GUIAPO/HRDID`, Retail 47,60, Agent 38,08, Comm 20 %)
  y Retain Sell Price tildado, la sustitución a mano al `000000` y la vuelta dejaron Retail, Agent y Comm idénticos; el costo pasó de 30,94 a 0,00 y
  volvió a 30,94 (recalculado con la tarifa). Es un solo caso (GU, cotización QU). **En el ambiente de test** (actualizado a una versión nueva de
  Tourplan) la misma operación, con el script y a mano, **pierde la venta** (Retail/Agent de 99,30 a 0,00). Riesgo: que producción reciba esa versión.
  El script no lee precios y no lo detectaría: pendiente un control automático (Product Analysis antes/después: el costo puede cambiar, la venta no;
  si cambia, detener la corrida) y consultar a Tourplan. No usar test para validar precios.
- **Service Types en los que analizar pickup / drop-off / Remarks:** ya no se configura en la app: se define en el script (`PUDO_SERVICE_TYPES_FIJOS`, hoy vacío = ninguno)
  y, si en un Service Type no habilitado aparecen esos datos, la combinación se rechaza. Hay que darle una vuelta (cómo se define, dónde se guarda,
  qué default conviene y qué hacer cuando los datos difieren entre reservas) antes de usarlo en volumen. Anotado, sin cambios por ahora.
- **Servicios cancelados (CX) excluidos:** Tourplan no deja sustituir un servicio con Service Status `CX` (en producción, 2026-10-20, rechazó con
  `Error (Service status () does not exist.)`; la reserva quedó sin cambios y el provisorio vacío). Por eso el script deja destildado en el filtro
  Service Status el ítem `Cancelled` (`SERVICE_STATUS_EXCLUIDOS`) y esas reservas no entran en la corrida (la consola dice `Service Status: todos
  menos Cancelled`). Si aun así un estado no se sustituye, la combinación termina en `ERROR: no sustituida` (no frena la corrida), se sigue con los
  demás estados y OBSERVACIONES indica cuáles sí y cuáles no (`POR ESTADO: IT sustituido (2 de 2), OT NO sustituido (1)`).
- **Provisorios:** el listado ya está en el código (116 combinaciones Location + Service Type bajo `1EURO1`). Si se crea uno nuevo en
  Tourplan hay que agregarlo a `PROVISORIOS_POR_LOCATION`. Se eliminó el provisorio de prueba `BUE/GUIAPO/GU/000000`.
- Selectores del modal Replacement Service que **no están en las grabaciones**: los campos de pickup/drop-off/Remarks y los Retain
  (se buscan por texto/ids del panel y se vuelcan HTML y captura antes de cada OK); confirmar en la primera corrida real.
- Soporte de combinaciones con varios estados de servicio, más reservas y datos de pickup distintos (hoy se rechazan).
- Modo recuperación (devolver al original lo que quedó en un provisorio), solo si se pide.
- Medir tiempos reales y optimizar (esperas por polling, reusar la pantalla entre combinaciones): ~55 s por combinación en lectura.
