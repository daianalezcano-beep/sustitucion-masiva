# Integración futura en Drive-TP-NX-App (NO se hace todavía)

Cuando la herramienta funcione bien, pasa a vivir dentro de Drive-TP-NX-App y su configuración pasa a ser la general. Mientras se
prueba se trabaja SOLO en este repo y no se modifica nada del otro. Este documento lista qué habría que llevar; está pensado para
que sea una copia con ediciones mínimas.

## Ya es compatible (decisiones tomadas a propósito)

- **Mismo contrato app↔script:** variables `TOURPLAN_*`, abort cooperativo por archivo bandera (`TOURPLAN_STOP_FILE`) y salida 75; el
  script corre como subproceso.
- **Misma configuración por PC:** `common/user_config.py` es copia idéntica; usuario/contraseña de Tourplan y `headless` se leen del
  mismo `~/.tourplan-nx-app/config.json`. La URL del Sheet se guarda en `sheet_urls["substitute_doble"]`, o sea la misma clave que
  tendría la entrada en el `SCRIPTS` de esa app: en la Configuración general aparecería sola.
- **Mismos helpers copiados sin cambios:** `common/abort.py`, `sheets_client.py`, `user_config.py`, `chrome_bootstrap.py`.

## Qué habría que llevar

| De este repo | A Drive-TP-NX-App | Nota |
|---|---|---|
| `scripts/substitute_doble/substitute_doble.py` | `scripts/substitute_doble/` | |
| `common/logica.py`, `ejecucion.py`, `tp_selenium.py`, `sheets_batch.py` | `common/` | Son archivos nuevos: no pisan nada |
| `tests/` | `tests/` (si el otro repo adopta pruebas) | |
| `app.py` | **no se copia entero**: se reusan `render_parametros`, `render_en_vivo` y `lanzar` como una pantalla más | |

## Ediciones que pediría en `app.py` de la otra app (hoy no existen)

1. Entrada nueva en `SCRIPTS`: categoría "Reservas", `sheet: "SUBSTITUTE"`, `modo_options` lectura/aplicar (lectura por defecto).
2. **Parámetros extra por corrida** (la app no tiene un mecanismo genérico): fecha Desde obligatoria, Hasta (31/12/2049),
   Service Types con pickup/drop-off, "solo la fila nº", y el comparador de reportes antes/después. Se arman con
   `common/ejecucion.armar_entorno`.
3. **Confirmación antes de cada OK:** el script publica `confirmacion_pendiente.txt` en `TOURPLAN_CONFIRM_DIR` y espera
   `confirmacion_respuesta.txt`; la app muestra los botones Continuar / Cancelar (ver `render_en_vivo`).
4. **Código de salida 76** (corrida detenida por sustitutos sin asignar): mensaje propio en la app.
5. `_clasificar_estado`: `ERROR_COLGADAS` y `ERROR_REVISAR` ya cuentan como Error (empiezan con `ERROR`); `DUPLICADO` cae en "Otro".
6. Pestaña `SUBSTITUTE_DETALLE` de salida (solo agregar) y, mientras haya reservas en `FASE` IDA_EN_CURSO / IDA / VUELTA, no arrancar.
7. Carpeta de salida por corrida (`salida/<modo>_<fecha-hora>/`, con `consola.log`).

Antes de portar: confirmar la primera corrida real en modo aplicar, cargar la lista de provisorios en el código y resolver los
pendientes del README.
