# Guía rápida — Actualización de costos (para quien opera la app)

**Para qué sirve.** Actualiza el costo de las reservas cuando cambió una tarifa. La app mueve las reservas de cada servicio a un servicio
temporal (`000000`) y las devuelve a su servicio original. Tourplan recalcula el costo con la tarifa vigente; la venta no debe cambiar. Los servicios
cancelados no entran en la corrida.

## Antes de empezar
1. Cargá las combinaciones en la hoja de Google (pestaña `SUBSTITUTE`): a mano (`LOCATION`, `SUPPLIER`, `SERVICE TYPE`, `CODIGO` y `ESTADO` = `PENDIENTE`) o,
   más simple, desde la app: exportá de Tourplan el reporte **Service Status**, en **Combinaciones a procesar** elegí **Cargar un reporte nuevo**, subí el CSV, tocá **Analizar
   reporte**, revisá la vista previa y **Confirmar**.
2. Exportá de Tourplan el reporte **Product Analysis "antes"** en CSV.
3. La primera vez, la app abre sola **⚙️ Configuración** (el engranaje de arriba a la izquierda): cargá el usuario y la clave de Tourplan y el enlace de la hoja.

## Correr
1. Doble clic en `run_app.bat` (antes, `actualizar_app.bat` para tener la última versión).
2. Elegí el modo:
   - **Solo revisar:** lee y muestra qué se procesaría, sin modificar nada. Recomendado antes de aplicar.
   - **Aplicar cambios:** hace la sustitución real. **Exige adjuntar el reporte PRE** (Product Analysis exportado antes de la corrida, con las mismas fechas): la app verifica que cubra todas las combinaciones PENDIENTES y lo guarda para comparar después.
3. Elegí el **Tipo de reservas**: **FITS** (individuales), **GRUPOS** o **AMBOS**. Cada combinación del Sheet tiene su `TIPO` y en FITS o GRUPOS solo se procesan las filas de ese tipo; el filtro Department de Tourplan se ajusta solo. Al cargar un reporte la app arma una fila por combinación y tipo, así que con un solo Service Status alcanzan los dos tipos. Las filas viejas del Sheet sin `TIPO` solo se procesan en AMBOS. Por defecto queda FITS.
4. No hay que poner fechas: el Service Status ya viene filtrado (se sustituyen los servicios de mañana en adelante). La URL de Tourplan está en ⚙️ Configuración; arriba, el chip dice si apunta a producción.
5. Revisá la lista de requisitos y tocá **Ejecutar revisión** (o **Aplicar en producción**, que además pide tildar la confirmación). Arriba queda una barra con el avance (el botón **Abortar** está al lado de Refrescar y en la barra); la consola muestra el detalle, con la hora de cada paso. No toques la ventana de Chrome mientras corre.
   **Abortar** corta al terminar la combinación en curso.

## Después
1. Exportá el reporte **"después"** con las mismas opciones que el "antes".
2. Con el botón **Ir a Comparar** que aparece al terminar una corrida de Aplicar, o con la pestaña **Comparar reportes** (arriba, junto a **Ejecutar corrida**; no hace falta haber ejecutado antes) subí el reporte de después (el PRE de la última corrida ya está guardado) y tocá **Comparar**. El costo puede cambiar; si aparece un aviso rojo de
   **venta modificada**, revisá esas líneas antes de seguir.
3. En la hoja, `ESTADO` y `OBSERVACIONES` de cada línea dicen qué pasó. El detalle por reserva está en `SUBSTITUTE_DETALLE`.

## Qué significan los estados
- `OK`: se actualizó y se verificó que todo volvió igual.
- `ERROR: no sustituida`: Tourplan no aceptó algún estado; las observaciones dicen cuáles sí y cuáles no.
- `ERROR_REVISAR`: algo quedó distinto a lo esperado; hay que mirarlo.
- `ERROR_COLGADAS` o "corrida detenida": pudo quedar una reserva en el servicio temporal. **No volver a correr hasta revisarlo con quien mantiene la herramienta.**
- `DUPLICADO`: la combinación estaba repetida en la hoja.
- `DESCARTADA`: código genérico (`700` o `600` + tres letras, ej. `700GUI`): valor cero que se manualiza, la herramienta no lo procesa.

## Si algo sale raro
El **Historial de corridas** (abajo en la app) permite descargar el log de cada corrida. Enviarlo junto con la descripción del problema.

## Ojo con el ambiente de test
Al sustituir, el ambiente de test pierde la venta (problema de esa versión de Tourplan). Sirve para probar el flujo, no para validar precios.
