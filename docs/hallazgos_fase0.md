# Hallazgos del diagnóstico (Fase 0) — Substitute Service

Fuente: corrida real de `diag_substitute.py` v0.1 contra Tourplan producción
(2026-10-19), caso controlado `BUE / GU / 1ACOS1 / 700GUI`, 01/11/26.
Etiqueta **[DIAG]** = confirmado por esa corrida. **[?]** = sigue sin verificar.

## Navegación
- URL directa `…/tourplannx/#/operations/OperationsBookings` funciona logueada **[DIAG]**.
  No hace falta pasar por el menú. Título de la página: "Booking Operations".
- Menú de `#operationsview` (ícono `#operationsview nav tp-nav > div > div > div > div`):
  1 SUBSTITUTE SERVICE, 2 UPDATE PICKUP/DROPOFF, 3 BOOK ON, 4 ICOM ACTIVITY,
  5 RECALCULATE BOOKINGS, 6 RECALCULATE SERVICES, 7 PRODUCT SETUP, 8 OPERATIONS REPORTS,
  9 PRODUCT REPORTS **[DIAG]**. Ítem 1 = `#operationsview nav li:nth-of-type(1) > div > div`.
- Camino por menú desde Home (por si hiciera falta): menú → OPERATIONS (ítem 5) → submenú con
  BULK DOCUMENTATION, BOOKING OPERATIONS, SERVICE OPERATIONS, PCM OPERATIONS **[DIAG]**.
- Pantalla correcta = último `tp-dialog` que contiene `tp-substitute-service`, con
  `#serviceDateFrom` y `#serviceDateTo` **[DIAG]**. Pestañas: `#tptablabel-filterTab`,
  `#tptablabel-bookinganalysis`, `#tptablabel-resultsTab`.

## Filter
- Location / Service / Supplier: `div.col1 li:nth-of-type(1|2|3) input` dentro del diálogo.
  La lista de sugerencias es una tabla dentro del mismo `li`: `tr > td.code` + `td.description`;
  la primera fila trae la clase `selectedRow` **[DIAG]**.
- La búsqueda es por texto, no solo por código: `BUE` devuelve BUE, CLP, CRO, PBA. Elegir por
  `td.code` idéntico, nunca la fila resaltada **[DIAG]**. Valor final del campo con el
  formato `CODIGO - Descripción` (ej. `BUE - Buenos Aires`).
- Code: `#optionCode > tp-validator > input` (+ blur). Description y Comment (solo lectura):
  `input.tpdescription-optiondescription` / `input.tpdescription-optioncomment` **[DIAG]**.
- **Fechas** `#serviceDateFrom` / `#serviceDateTo`: el visible es `div > input:nth-of-type(2)`;
  el primero (`input.tphidden`) guarda el valor normalizado `DD/Mon/YYYY` **[DIAG]**.
  - `31/12/49` → `31/Dec/2049` (año de 2 dígitos: 49 = 2049); `01/01/20` → `01/Jan/2020` **[DIAG]**.
  - **`31/12/2049` (4 dígitos) NO se acepta**: el normalizado vuelve a la fecha de hoy **[DIAG]**.
    Escribir siempre `DD/MM/YY` y verificar leyendo el input oculto, parseado.
  - "Invalid characters!" = `aria-label` de un input de fecha con la clase `tpinvalid` (vacío o
    no interpretable). Desaparece al quedar una fecha válida **[DIAG]**. Ambos campos arrancan vacíos.
- Estados (grupos `div.legend` "Booking Status" / "Service Status" / "Branch" / "Department"),
  todos tildados por defecto **[DIAG]**. Estructura: `li.list-row` →
  `input#list-<grupo>-undefined-item-N` (con su `<i>`) + `label.tplabel-list[for=…]` con el texto.
  - Ubicar por el **texto** del label, no por el índice ni por los atributos `_ngcontent-*`.
  - Booking Status (10): All, CC- CXL with Charges, **CX - Cancelled** (item-2), IC, IQ, KK, MI, OK, QU, RQ.
  - Service Status (24 en DOM; el diagnóstico solo volcó 18, nombres sin código): All, Cancel Hyperg c/IVA,
    Cancellation Charge (×2), Cancellation On Req, Cancelled, Cancelled Ext Source, Cancelled-No Charge,
    Complimentary, Complimentary Latam, Confirmed LK, Confirmed OK, Free Sale Confirmed, Initial,
    Modification On Req, Not Confirmed-DataRQ, OK Tráfico, On Request, + 6 sin volcar.
  - Los grupos Service Status y Department tienen scroll interno.
- Decisión vigente: destildar solo **Booking Status → CX - Cancelled**.

## Results
- Columnas (`th`): 1 `tp-grid-column-zero` (vacía), 2 checkbox `tpcol-checkbox tpcol-selected`, y luego
  LocationCode, ServiceCode, SupplierCode, ProductCode, ProductDescription, BookingName,
  BookingReference, TravelDate, ServiceDate, ServiceStatus, PudoDetails **[DIAG]**.
- Clases de `td` (filas): `tpcol-locationcode`, `tpcol-servicecode`, `tpcol-suppliercode`,
  `tpcol-optioncode`, `tpcol-optiondescription`, `tpcol-bookingname`, `tpcol-bookingreference`,
  `tpcol-traveldate`, `tpcol-servicedate`, `tpcol-servicestatus`, `tpcol-pudodetails`; el checkbox es
  `td.tpcol-checkbox.tpcol-substituteservice` **[DIAG]**. Formato de fecha: `01/Nov/2026`.
- `tpcol-pudodetails` contiene una tabla anidada: filas `Pickup:` / `Dropoff:` con 3 `td` (etiqueta,
  `DD/Mon/YYYY HH:MM`, y un tercero, probablemente el texto) **[DIAG; el tercero estaba vacío]**.
  Cuidado: las filas internas son `tr` dentro de la fila de datos; contar solo `tr` con `td.tpcol-optioncode`.
- Contadores: `tp-label[tplabeltype="Found"]` / `[tplabeltype="Selected"]` → el valor está en el
  `label` de la celda `td.align-right` de la misma fila **[DIAG]** (Found=1, Selected=0 en reposo).
- Panel derecho: `#selectServiceLine` (checkbox "Substitute Service"), `#pickupDate`, `#pickupTime`,
  `#pickupRemarks`, `#dropoffDate`, `#dropoffTime`, `#dropoffRemarks`, `Remarks`, botón Edit Details.
  Todo deshabilitado/solo lectura sin selección **[DIAG]**. Qué muestra al seleccionar una fila **[?]**.
- Virtual scroll de Results **[?]**: el caso tenía 1 fila y no hubo contenedor con scroll.

## Sin verificar todavía
- Qué muestra el panel derecho al seleccionar una fila (y si trae el texto de pickup/dropoff).
- Comportamiento de Results con muchas filas (virtual scroll).
- Selectores del modal (Retain Sell Price / Retain Voucher Text) y texto del Summary.
- Los 6 ítems de Service Status que no se volcaron.
- La reserva de la prueba figura como `EAFT249858` ("Test Hyperguest - Test"), estado `IT`.
