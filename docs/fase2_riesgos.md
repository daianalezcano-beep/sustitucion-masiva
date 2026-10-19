# Fase 2 (modo aplicar) — riesgos y cómo cortar

Estado: probada solo por simulación (53 pruebas, incluido el control de flujo ida/vuelta con un Tourplan en memoria) y
contra HTML capturado; **nunca corrida contra Tourplan**. El primer intento debe ser sobre la reserva de prueba
(`EAFT249858`, "Test Hyperguest", `BUE/GU/1ACOS1/700GUI`, 01/11/26) con `TOURPLAN_CONFIRMAR_OK=1`.

## Riesgos

| # | Riesgo | Por qué | Cómo se reduce |
|---|---|---|---|
| 1 | Selectores del modal Replacement Service sin verificar | Solo hay una captura y las grabaciones; no hay volcado de su HTML | Se vuelca el HTML del modal antes y después de llenarlo y hay captura; todos los campos se releen. Pickup/drop-off se carga **ya en la ida**, así que esos selectores se prueban antes de mover nada |
| 2 | Falla en la vuelta con las reservas ya en el provisorio | Cualquier error entre el OK de la ida y el de la vuelta | Estado `ERROR_COLGADAS`; el detalle queda en `IDA_EN_CURSO`/`IDA`/`VUELTA`; el próximo arranque se niega hasta resolverlo (ver "Cómo cortar") |
| 3 | El Summary no se conoce | Nunca vimos su texto | Se vuelca su HTML y texto; la verificación NO depende de él: se relee Results del provisorio y del original |
| 4 | Tourplan ignora servicios sin avisar (cerrados, FIT en Book On, etc.) | Según el manual | Se compara lo movido contra la foto: las no sustituidas quedan `NO_SUSTITUIDA` en el detalle |
| 5 | Se pierden pickup/drop-off/remarks o el texto del voucher | El modal no tiene fecha de pickup y no sabemos qué conserva la sustitución | Se comparan los 7 campos del panel antes/después (`REVISION_MANUAL` si difieren). El **voucher text no se puede leer**: revisarlo a mano en la reserva de prueba |
| 6 | Costo | El script no lo ve | Lo verifica la usuaria con un reporte del sistema sobre la reserva de prueba |
| 7 | Corte a mitad (Chrome, red, PC) | Entre ida y vuelta | Igual que el #2; Ctrl+C también hace logout |
| 8 | Alguien edita las reservas durante la corrida | Producción compartida | La verificación final detecta diferencias → `ERROR_REVISAR` |
| 9 | Provisorio inexistente o con restos | Se creó a mano | Validación previa (una vez por provisorio y corrida, todo el historial): si no existe o tiene reservas, la fila termina en ERROR sin tocar nada |

## Cómo cortar

- **Antes de un OK:** escribir cualquier cosa distinta de `SI` en la consola (no se ejecuta ese OK), o Ctrl+C (hace logout).
- **Abort cooperativo** (app): corta entre combinaciones; una combinación en curso termina completa.
- **Si quedaron reservas en el provisorio (`ERROR_COLGADAS`):** en Tourplan, Booking Operations → Substitute Service, filtrar el
  provisorio (`<Location>/<proveedor>/<Service Type>/000000`), seleccionar todo, OK y reemplazar por el original con el mismo Service
  Status de `SUBSTITUTE_DETALLE` (columna `STATUS_ORIGINAL`) y, si había, pickup/drop-off/remarks (`PICKUP_TEXTO`, `DROPOFF_TEXTO`,
  `REMARKS`). Después poner la `FASE` de esas filas en `VERIFICADO` o `REVISION_MANUAL`. (Un modo recuperación automático está en la hoja de ruta.)
