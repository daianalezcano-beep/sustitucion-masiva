# Guía de puesta en marcha — Actualización de costos (para compartir con el equipo)

Esta guía es para dejar la app funcionando en la PC de cada persona. Para el uso diario ver `docs/guia_rapida.md`.

## Cómo está armada (en una línea)
Cada persona corre la app **en su propia PC** (se abre en el navegador, solo en esa PC, sin login propio). La app maneja Chrome para operar en
Tourplan y usa un Google Sheet como lista de trabajo. Lo personal (usuario de Tourplan, permisos de Google) queda en la carpeta del usuario de
Windows, nunca en el repo.

## 1. Qué necesita cada PC (una sola vez)
| Qué | Para qué | Cómo comprobarlo |
|---|---|---|
| Windows con **Python 3** (instalado con "Add python.exe to PATH") | `run_app.bat` crea el entorno y arranca la app | abrir `cmd` y escribir `python --version` |
| **Google Chrome** (instalación normal) | la app lo maneja para entrar a Tourplan | que exista `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| **Git para Windows** en el PATH | `actualizar_app.bat` trae las novedades | `git --version` en `cmd` |
| Internet y acceso a `github.com` | instalar dependencias, bajar el driver de Chrome y actualizar | |
| **Usuario de Tourplan** de la persona | la app entra con ese usuario (cada corrida usa una licencia mientras corre) | |
| **Cuenta de Google** con acceso al Sheet | leer y escribir la lista de trabajo | |

## 2. Qué se prepara una sola vez para todo el equipo
1. **El Google Sheet de trabajo**, compartido con la cuenta de Google de cada persona como **Editor**. Tiene que tener dos pestañas con
   estos nombres exactos:
   - `SUBSTITUTE` — la entrada. Columnas: `LOCATION`, `SUPPLIER`, `SERVICE TYPE`, `CODIGO` y `ESTADO` (se aceptan con guion bajo o en minúscula).
     `OBSERVACIONES` y `TIMESTAMP` se agregan solas.
   - `SUBSTITUTE_DETALLE` — la salida, una línea por reserva. Puede estar vacía: las columnas se agregan solas.
2. **El archivo `credentials.json` de Google** (permiso para que la app use Sheets). Es el mismo que usa la otra app de Tourplan del equipo
   (Drive-TP-NX-App): si ya lo tienen, se copia tal cual a cada PC (paso 3.4). Si no existe, hay que crearlo una vez en Google Cloud Console
   (proyecto con la **API de Google Sheets** activada y un **ID de cliente OAuth de tipo "Aplicación de escritorio"**) y descargar el JSON.
   *Este es el único paso que no puedo verificar desde el código: lo hace quien administra la cuenta de Google.*
3. **Dónde vive el código.** El repo es `https://github.com/daianalezcano-beep/sustitucion-masiva`. Hoy tiene una sola rama
   (`claude/substitute-doble`); antes de compartirlo conviene dejar una rama principal (`main`) con esta versión, para que todos clonen lo mismo.
   Cada persona puede clonar el repo en su PC, o puede haber una copia compartida en una carpeta de red (el código ya contempla ese caso: el
   entorno de Python y la configuración personal se guardan en la carpeta de usuario de cada PC, no en la carpeta compartida).

## 3. Instalación en cada PC (15–20 minutos, la primera vez)
1. **Traer la carpeta.** Con Git instalado, en `cmd`:
   `git clone https://github.com/daianalezcano-beep/sustitucion-masiva`
   (debe ser un clon; una copia bajada como ZIP no se puede actualizar con `actualizar_app.bat`).
2. **Primer arranque:** doble clic en `run_app.bat`. Crea el entorno de Python en `%USERPROFILE%\.sustitucion-masiva-venv`, instala todo y abre la
   app en el navegador (dirección `http://localhost:8501` o similar). Tarda unos minutos solo esta vez. No cerrar la ventana negra: es la app.
3. **Configuración** (⚙️ en la app): usuario y clave de Tourplan, y el enlace del Google Sheet. Se guarda en
   `%USERPROFILE%\.tourplan-nx-app\config.json` (en texto plano, solo en esa PC). Dejar destildado "Correr sin ventana de Chrome".
4. **Permiso de Google:** copiar `credentials.json` a la carpeta `%USERPROFILE%\.tourplan-nx-app\` (si esa carpeta ya existe por la otra app,
   ya está). La primera vez que la app use el Sheet se abre una ventana del navegador para elegir la cuenta y dar permiso; después queda guardado
   (`token.json`) y no vuelve a pedir nada.
5. **Prueba de humo** (sin modificar nada): modo **Solo revisar**, "Solo la fila nº" = el número de una fila PENDIENTE del Sheet, fechas
   razonables, y **Ejecutar**. Tiene que abrir Chrome, entrar a Tourplan, leer la combinación y escribir el resultado en `OBSERVACIONES`.
   Recién después, usar **Aplicar cambios**, la primera vez con una sola combinación y revisando los costos con el reporte antes/después.

## 4. Uso y mantenimiento
- **Uso diario:** ver `docs/guia_rapida.md`.
- **Actualizar:** cerrar la app y doble clic en `actualizar_app.bat`; después `run_app.bat` otra vez. Hacerlo antes de cada tanda grande.
- **Dónde quedan los resultados:** carpeta `salida\` dentro del proyecto (una subcarpeta por corrida con capturas y `consola.log`). La app muestra el
  historial y permite descargar cada log.
- **Ambiente:** la URL de Tourplan en Parámetros viene con **producción**. Para probar contra test hay que cambiarla a mano. Ojo: el ambiente de test
  pierde la venta al sustituir; sirve para probar el flujo, no para validar precios.

## 5. Problemas frecuentes
| Síntoma | Qué hacer |
|---|---|
| "No se encontró Python" | Instalar Python desde python.org marcando "Add python.exe to PATH" y repetir `run_app.bat`. |
| "No se encontró Chrome" | Instalar Chrome de forma normal (el script no lo instala solo en Windows). |
| "No encontré el archivo de credenciales OAuth" | Falta `credentials.json` en `%USERPROFILE%\.tourplan-nx-app\` (paso 3.4). |
| Error de permisos o "403" al leer el Sheet | El Sheet no está compartido con esa cuenta de Google como Editor. |
| "No encontré la pestaña SUBSTITUTE" (o `SUBSTITUTE_DETALLE`) | Falta la pestaña o el nombre no es exacto. |
| "Faltan columnas…" | Revisar los encabezados de `SUBSTITUTE` (paso 2.1). |
| Falla el login de Tourplan o dice que no hay licencias | Revisar usuario y clave en ⚙️ Configuración; si no hay licencias libres, reintentar más tarde (cada corrida usa una mientras dura). |
| `actualizar_app.bat`: "no es un clon de git" | La carpeta se bajó como ZIP: clonarla con `git clone` (paso 3.1). |
| `actualizar_app.bat`: "no se encontró git" | Instalar Git para Windows. |
| La corrida terminó con **🛑 Corrida detenida** | Puede haber una reserva en el servicio temporal `000000`. No volver a correr hasta revisarlo con quien mantiene la herramienta. |
| La app no abre en el navegador | Abrir a mano la dirección que muestra la ventana negra (ej. `http://localhost:8501`). |

## 6. Seguridad y buenas prácticas
- La app solo escucha en la propia PC y no tiene login: cada persona usa **su** PC y **su** usuario de Tourplan.
- La clave de Tourplan queda en texto plano en `config.json` y el permiso de Google en `token.json`: no compartir esos archivos ni copiar la carpeta
  `.tourplan-nx-app` entre personas (solo `credentials.json` se comparte).
- No correr dos veces a la vez con el mismo usuario de Tourplan. No tocar la ventana de Chrome mientras corre.
- Antes de aplicar en producción: modo **Solo revisar**, y después de aplicar, el **reporte antes/después** para controlar costo y venta.

## 7. Pendientes antes de compartir con todo el equipo
- Dejar una rama principal (`main`) con la versión final y avisar al equipo que clone esa.
- `actualizar_app.bat` busca, además de `git` en el PATH, un Git portátil en la ruta de una persona puntual: en las demás PCs hace falta Git instalado.
- Elegir quién mantiene la herramienta (recibe los logs, ve los `ERROR_COLGADAS`) y quién administra el `credentials.json` y el Sheet.
- El encabezado del `README.md` todavía dice que el modo aplicar no se corrió contra Tourplan; está desactualizado (se corrió en test y producción).
