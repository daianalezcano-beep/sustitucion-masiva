# Guía rápida — Actualización de costos (para quien opera la app)

**Para qué sirve.** Actualiza el costo de las reservas cuando cambió una tarifa. La app mueve las reservas de cada servicio a un servicio
temporal (`000000`) y las devuelve a su servicio original. Tourplan recalcula el costo con la tarifa vigente; la venta no debe cambiar. Los servicios
cancelados no entran en la corrida.

## Antes de empezar
1. En la hoja de Google (pestaña `SUBSTITUTE`) cargá una línea por combinación: `LOCATION`, `SUPPLIER`, `SERVICE TYPE` y `CODIGO`, con `ESTADO` = `PENDIENTE`.
2. Exportá de Tourplan el reporte **Product Analysis "antes"** en CSV.
3. La primera vez, abrí **⚙️ Configuración** y cargá el usuario y la clave de Tourplan y el enlace de la hoja.

## Correr
1. Doble clic en `run_app.bat` (antes, `actualizar_app.bat` para tener la última versión).
2. En **Parámetros** elegí el modo:
   - **Solo revisar:** lee y muestra qué se procesaría, sin modificar nada. Recomendado antes de aplicar.
   - **Aplicar cambios:** hace la sustitución real.
3. Poné **Servicios desde** (obligatoria) y, si hace falta, **hasta**. Chequeá que la URL de Tourplan sea la del ambiente que querés usar.
4. Tocá **Ejecutar**. La consola muestra el avance, con la hora de cada paso. No toques la ventana de Chrome mientras corre.
   **Abortar** corta al terminar la combinación en curso.

## Después
1. Exportá el reporte **"después"** con las mismas opciones que el "antes".
2. En **📊 Comparar reportes antes / después** subí los dos archivos y tocá **Comparar**. El costo puede cambiar; si aparece un aviso rojo de
   **venta modificada**, revisá esas líneas antes de seguir.
3. En la hoja, `ESTADO` y `OBSERVACIONES` de cada línea dicen qué pasó. El detalle por reserva está en `SUBSTITUTE_DETALLE`.

## Qué significan los estados
- `OK`: se actualizó y se verificó que todo volvió igual.
- `ERROR: no sustituida`: Tourplan no aceptó algún estado; las observaciones dicen cuáles sí y cuáles no.
- `ERROR_REVISAR`: algo quedó distinto a lo esperado; hay que mirarlo.
- `ERROR_COLGADAS` o "corrida detenida": pudo quedar una reserva en el servicio temporal. **No volver a correr hasta revisarlo con quien mantiene la herramienta.**
- `DUPLICADO`: la combinación estaba repetida en la hoja.

## Si algo sale raro
El **Historial de corridas** (abajo en la app) permite descargar el log de cada corrida. Enviarlo junto con la descripción del problema.

## Ojo con el ambiente de test
Al sustituir, el ambiente de test pierde la venta (problema de esa versión de Tourplan). Sirve para probar el flujo, no para validar precios.
