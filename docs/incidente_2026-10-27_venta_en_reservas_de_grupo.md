# Incidente 27/10/2026 — pérdida de la venta (Retail / Agent) en reservas de grupo tras la doble sustitución

Documento para evaluar alternativas que eviten que vuelva a ocurrir. Los datos salen de: el log de la corrida (`aplicar_20261027-104208`), los reportes
Product Analysis PRE (27/10 09:43) y POST (27/10 23:00), el reporte Service Status del 27/10 y la comparación hecha con la app. Sin nombres de pasajeros.
Lo que es hipótesis está marcado como tal.

## 1. Resumen
- **Qué pasó:** la corrida en modo aplicar del 27/10 (producción) actualizó el **costo** como se esperaba, pero en las **reservas de grupo** dejó el
  **Retail y el Agent en 0** en las líneas procesadas, aunque Retain Sell Price estaba tildado.
- **Alcance:** 301 líneas en 52 reservas (27 `EAGP`, 22 `EAGR`, 2 `TDGR`, 1 `EAPR`), todas en USD. Valores perdidos (antes de la corrida): **Retail ≈ 46.324 y Agent ≈ 38.550**.
- **Qué no pasó:** las reservas individuales (`EAFI`, `EAFT`, `EAFG`) conservaron el Retail en las 467 líneas comparadas.
- **Estado:** corregido a mano a partir del reporte PRE. Tras tres rondas de correcciones, quedan **3 líneas** por ajustar (sección 7), descartados proveedores por asignar, redondeos y cambios de pax.

## 2. Qué hizo el script (y qué cambió en las reservas)
El script, por cada combinación Location + Supplier + Service Type + Option, hace **ida** (todas las reservas del original → provisorio `1EURO1/000000`) y **vuelta**
(provisorio → mismo original, mismo estado de servicio), con **Retain Sell Price** y **Retain Voucher Text** tildados (verificado en los 563 modales de ida y vuelta del log), y
verifica que el original quede idéntico a la foto inicial en las columnas del listado de Substitute Service (location, servicio, proveedor, código, reserva, fechas, estado, PU/DO).

Lo que modificó, comparando PRE y POST:

| Qué | Resultado | Esperado |
|---|---|---|
| **Costo** | recalculado con la tarifa vigente (470 líneas con costo distinto) | sí |
| **Retail y Agent en reservas de grupo (estado de reserva `KK`)** | **301 líneas a 0** (de 392 líneas de grupo; todas las procesadas que tenían venta) | **no** |
| Retail en reservas individuales (`OK`/`QU`) | se mantuvo en las 467 líneas | sí |
| **Agent en reservas individuales** | cambió (±) en 21 líneas con el Retail igual; probablemente comisión recalculada | no confirmado |
| Retail y Agent de 0 a un valor | 1 línea (0 → 42,30): una venta en 0 no se "retiene", se recalcula | discutible |
| **Costo a 0** | 10 líneas de `700GUI` (≈ 1.553 de costo antes) en 5 reservas: seguramente sin tarifa para esa fecha | no |
| Servicios `OP` (Optional) | Tourplan rechazó la sustitución (`invalid status code of ()`) en 1 combinación: sin cambios | rechazo seguro |
| Servicios `CX` (cancelados) | no se procesan (excluidos del filtro) | sí |
| Voucher, estado de servicio, pax, fechas | sin cambios en las columnas comparadas | sí |

## 3. Evidencia
- **Patrón por tipo de reserva:** prefijos `EAGP/EAGR/EAPR/TDGR` (todas con estado de reserva `KK`): 301 de 392 líneas pierden la venta; `EAFI/EAFT/EAFG` (`OK`/`QU`): 0 de 467.
- **Por estado de servicio (cruzado con Service Status):** las líneas `OT` de grupo procesadas: 240 con venta perdida, 10 que ya tenían 0, **0 retenidas**. Las `CX` (no procesadas) conservaron la venta, por eso hay reservas con "algunas líneas" perdidas.
- **Control:** las 721 líneas de proveedores por asignar (no procesadas) no cambiaron su venta: la causa no es externa (tipo de cambio, comisiones) sino la sustitución.
- **Retain tildado:** 563 de 563 modales. No es un olvido del script.
- **Pruebas anteriores que no lo mostraron:** (a) el 20/10 en test el mismo patrón apareció en reservas de grupo (`EAGP`), y se atribuyó a la versión de test; (b) en producción se probó con
  una reserva **individual** (una línea con venta, Retail 47,60 / Agent 38,08) y se retuvo; la primera reserva de prueba tenía la venta en 0, así que no probaba nada. Ninguna prueba incluyó una reserva de grupo en producción.

## 4. Causa (hipótesis, sin confirmar)
- En reservas de grupo (estado `KK`) el precio de venta parece calcularse con reglas distintas (precio de grupo / markup a nivel reserva), y **Retain Sell Price no lo conserva**. No se
  confirmó con Tourplan ni se miró el precio a nivel de la reserva de grupo (si el total de venta del grupo se perdió realmente o solo el valor por línea).
- **Hipótesis de la usuaria (observada en pruebas manuales, a confirmar):** en las reservas de grupo, al sustituir hacia el provisorio `000000` (valores en cero, sin tarifas) la venta se
  actualiza a 0 **en la ida**; en la vuelta, Retain Sell Price conserva lo que hay en ese momento (0) y la venta ya no se recupera. En las reservas individuales sí se retiene.
  Es consistente con lo observado en producción: en una reserva individual, tras la ida, el costo pasó a 0 y el Retail, el Agent y la comisión se mantuvieron. Lo que falta explicar es
  por qué los individuales retienen y los grupos no (por ejemplo, venta fijada por línea vs. calculada desde el costo o reglas del grupo).
- Pruebas propuestas para confirmarla (en una reserva de grupo que se pueda tocar): (1) solo la ida, y mirar Retail/Agent antes de la vuelta; (2) ida a un option real con venta distinta de cero,
  para ver si la venta pasa a ser la de ese option; (3) vuelta con Retain Sell Price destildado, para ver si se recalcula con la tarifa del option original (no tiene por qué coincidir con el valor negociado).
- El Agent de las reservas individuales cambia levemente: coherente con que se recalcule con la comisión vigente mientras el Retail se retiene.

## 5. Por qué los controles no lo detectaron
- El script **no lee precios**: sus verificaciones son de reservas y estados (provisorio libre, foto original vs. final), no de venta.
- El control de venta existe solo **después**, comparando reportes a mano (`Comparar reportes`), y ese reporte se exportó luego de terminar toda la corrida.
- No hubo una corrida piloto con reservas de grupo y no se filtró por estado de reserva.

## 6. Dificultades en la corrección manual (para tenerlas en cuenta)
- Varias líneas comparten reserva, proveedor, option y fecha (30 claves repetidas con alguna línea a corregir): sin el voucher es fácil cargar el valor en la línea equivocada
  (se vieron 5 pares de valores intercambiados y 2 líneas que conservaron un valor ajeno).
- Mejora ya hecha: el comparador ahora muestra voucher, pax, numeración `n de m` y empareja por voucher.

## 7. Estado de la remediación
Comparación final (PRE vs POST v2), descartando proveedores por asignar (`GUIAPO`), diferencias de redondeo y líneas con cambio de pax:

| Reserva | Línea (voucher) | Estado | Qué falta |
|---|---|---|---|
| `EAGP246283` | HRD4ID 09/11, voucher 2,946,161 (pax 11) | Retail 0 / Agent 0 (antes 209,51 / 209,51) | cargar 209,51 y 209,51 |
| `EAGP246283` | HRD4ID 09/11, voucher 2,514,301 (pax 19) | tiene 209,51 / 209,51 (antes 0 / 0) | dejar en 0 |
| `EAGR251780` | WAL3ID 25/11, voucher 2,795,305 | Agent 92,54 (antes 91,85), Retail igual | Agent 91,85 (diferencia de 0,69) |

Evolución: 323 líneas con venta modificada tras la corrida → 61 tras la primera ronda de correcciones → 26 → 20 (de las cuales 17 son descartables) → **3**.
Descartes aplicados: 13 líneas con cambio de pax entre reportes, 2 de redondeo (+0,01), 2 de proveedor por asignar (Agent distinto), y las líneas que están en un solo reporte (asignaciones de guía hechas entre los dos reportes).

## 8. Alternativas para que no vuelva a ocurrir

| # | Alternativa | Pros | Contras / riesgos |
|---|---|---|---|
| A | **Excluir reservas de grupo (estado `KK`)** del filtro de la herramienta (destildando Booking Status) y de la carga desde el reporte | Simple e inmediata; cierra el problema conocido | Los grupos quedan sin actualizar el costo: hace falta otro camino para ellos; asume que `KK` ≡ grupo |
| B | **Control automático de venta** con Product Analysis antes/después (por lote o por combinación): si Retail/Agent cambia, detener la corrida | Detecta cualquier causa, también las futuras (por ejemplo cuando producción cambie de versión) | Requiere automatizar el reporte de Tourplan (fase 4B, hoy sin selectores); cada comprobación suma tiempo |
| C | **Corrida piloto con compuerta**: antes de un lote, procesar 1–2 combinaciones por tipo de reserva (individual, grupo, cotización), exportar el reporte y comparar | Bajo costo, sin desarrollo; detecta tipos de reserva nuevos | Manual; depende de que se haga siempre |
| D | **Respaldo automático de los valores "antes"** (Retail/Agent/Costo por línea y voucher) en el Sheet antes de aplicar | Recuperación inmediata y precisa (evita el trabajo manual de armar la lista) | No previene; más datos en el Sheet |
| E | **Restaurar la venta desde la herramienta** tras la vuelta | Corrige solo, sin intervención | Depende de poder editar precios por línea desde la UI; alto esfuerzo y riesgo; no sabemos si es posible |
| F | **Usar otra función de Tourplan para recalcular costos** (por ejemplo "Recalculate Bookings", si conserva la venta) en lugar de la doble sustitución | Evita el problema de raíz si existe | Sin verificar qué hace con la venta; cambia el diseño |
| G | **Consultar a Tourplan** (soporte): por qué Retain Sell Price no conserva la venta en reservas de grupo y si hay configuración o forma de hacerlo | Puede dar la solución definitiva | Tiempo de respuesta; incertidumbre |
| I | **Camino distinto para grupos**: vuelta sin Retain Sell Price (si la prueba 3 muestra que se recalcula bien) o capturar los valores "antes" y restaurarlos | Puede permitir actualizar costos también en grupos | El valor recalculado puede no coincidir con el negociado; restaurar exige editar precios por línea; hay que probarlo |
| H | Validar en la herramienta el **estado de reserva** de cada línea antes de procesarla (leerlo del listado) y rechazar los no permitidos | Cubre cualquier lista de estados | Hay que confirmar que el listado lo trae; desarrollo |

**Recomendación:** A de inmediato (para no repetir el daño), G en paralelo (confirma la causa y si existe un camino para los grupos), C como práctica obligatoria antes de cada lote nuevo y
B como objetivo (cierra el riesgo si la causa reaparece por una actualización). D baja el costo de recuperación si algo falla. E y F solo si G lo habilita.

## 9. Decisiones pendientes
1. ¿Se excluyen los grupos (`KK`) de la herramienta hasta tener respuesta de Tourplan? (A)
2. ¿Quién consulta a Tourplan y qué se pregunta? (G) Evidencia para adjuntar: el patrón por tipo de reserva y un ejemplo antes/después de una línea de grupo.
3. ¿Se prioriza el control automático de venta (B)?
4. ¿Se revisa a nivel de la reserva de grupo si el total de venta quedó afectado (más allá de las líneas)?

## 10. Novedades posteriores

**Lo que dijo Tourplan / lo que se comprobó**
- Tourplan identificó la venta en cero en los servicios madre como un **bug de la versión** instalada en producción. Sigue sin confirmarse si el comportamiento de los grupos de este incidente es el mismo bug; no hay fecha de corrección.
- En el ambiente de **test (versión 2.7)** la herramienta Substitute Service **modifica la venta aunque "Retain Sell Price" esté tildado**. Por lo tanto, test no sirve para validar la venta de este flujo, y **es probable que producción cambie de comportamiento cuando se actualice a esa versión**: hay que repetir las pruebas después de cada actualización de Tourplan.
- Para revisar costos manualizados (override) antes de sustituir, Tourplan ofreció armar un **reporte dinámico**. Todavía no está disponible; la exclusión de líneas con costo manual queda pendiente de ese reporte.

**Plan de prueba en producción (con un tarifario/transportista con pocos servicios, FIT y grupos)**
1. Exportar Service Status y Product Analysis (PRE) del proveedor y del rango de fechas, y cargar las combinaciones desde el reporte; correr primero en Solo revisar.
2. Aplicar de a una combinación (Solo la fila nº), primero con reservas FIT y después con grupos, comparando el PRE contra el POST antes de seguir con la siguiente.
3. Revisar a mano el total de venta del servicio madre en 2 o 3 reservas (FIT y grupo) y, si hay una línea con costo manual, ver si se pisa.
4. No probar la ida sola ni la vuelta sin Retain Sell Price sobre reservas reales: dejarían reservas en el provisorio.

**Ideas a desarrollar (decididas en principio, sin implementar)**
- **Comparar el valor del servicio madre antes y después**, para asegurar que no cambia en la ejecución ni después de una eventual corrección manual. El Product Analysis **no trae ningún campo que vincule el componente con su servicio madre** (columnas: Agent, Supplier, Loc, Service, Option, Description, Date, Pax, Qty, Total, Currency, Cost, Agent, Retail, Reference, BookingName, Status, D.O.T, VoucherNo, Bkg Analysis 1–6, Consultant). Opciones: (a) totales de venta por reserva (Reference) pre/post, que se puede hacer con el reporte actual; (b) por servicio madre, que requiere un reporte de Tourplan que lo traiga (se puede pedir junto con el reporte dinámico).
- **Dividir la ejecución en FIT y grupos.** Hallazgo: **el estado de reserva `KK` no separa limpiamente grupos de FIT**. En los Service Status del 19/10 y del 27/10, `KK` aparece en referencias de grupo (`EAGP`, `EAGR`, `EAPR`, `TDGR`) pero también en `EAFG` (343 líneas el 19/10), y hay referencias de grupo con estados `QU` u `OK` (`EAGP` QU 5, `EAPR` OK 3 el 27/10). El discriminador real es el **prefijo de la Reference**, que el filtro de Substitute Service no ofrece. Caminos posibles: (a) filtros Branch o Department de la propia pantalla, si distinguen grupos (a confirmar en Tourplan); (b) **seleccionar en Results solo las filas** cuya BookingReference cumpla la regla (FIT: `EAFI`/`EAFT`/`EAFG`; grupo: `EAGP`/`EAGR`/`EAPR`/`TDGR`), en lugar de "Select all". La selección por fila serviría también para excluir líneas con costo manualizado cuando exista el reporte dinámico.
- Esto modifica la alternativa A: excluir por `KK` asume que `KK` ≡ grupo, y los datos muestran que no.

**Decisiones pendientes (actualización)**
1. Confirmar en Tourplan si Branch o Department distinguen FIT de grupos.
2. Definir la regla de FIT / grupo por prefijo (revisar `EAFG`, `EAIN`, `EAMI`, `EAFL` y los `TD*`, que no están clasificados).
3. Elegir el nivel de comparación del servicio madre: por reserva (ya posible) o por madre (necesita un reporte).

## 11. Separación FIT / grupos implementada (modo «Tipo de reservas»)
Se resolvió con el filtro **Department** de Substitute Service (la Reference empieza con sucursal + Department: `EAGP` = `GP`, `TDGR` = `GR`). Tabla de
Tourplan (Department → aplica a grupos / a individuales): solo individuales `FG FI FM FT MI OP PE PI PU`; solo grupos `GP GR IN`; marcados para ambos
`BO CF FL PR SL US`; para ninguno el vacío, `AC` y `RD`. Los de ambos o ninguno **no se procesan** hasta clasificarlos (por ejemplo `PR`, de `EAPR`, que tuvo
una reserva afectada en el incidente). La app tiene un modo **FITS / GRUPOS / AMBOS** (por defecto FITS): un solo Service Status alimenta los dos, con una
fila por combinación y tipo (columna `TIPO` en el Sheet). Detalle en el README. La separación deja listo el camino para actualizar FITS ahora y GRUPOS cuando
Tourplan corrija el bug de versión, o para probarlos por separado. **Cambia la alternativa A**: ya no hace falta excluir por estado `KK`; alcanza con no
correr el modo GRUPOS.
