@echo off
REM actualizar_app.bat — doble click para traer los cambios nuevos del
REM repo (git pull) de la app de Doble sustitucion, sin tener que volver a
REM descargar la carpeta entera cada vez que hay modificaciones. Mismo patron
REM que el de la otra app de Tourplan.
REM
REM Requiere que esta carpeta sea un clon de git (no una copia bajada
REM como ZIP) — si todavia la tenes como ZIP, pedile a quien te la paso
REM que te mande el link del repo y clonalo una sola vez con:
REM   git clone <url-del-repo>
REM despues de eso este boton ya te sirve siempre.

setlocal EnableDelayedExpansion
cd /d "%~dp0"

if not exist ".git" (
    echo Esta carpeta no es un clon de git ^(no tiene carpeta .git^).
    echo No se puede actualizar con este boton porque no sabe de que repo bajar los cambios.
    echo.
    echo Pedile a quien te paso la app el link del repo y clonalo una sola vez con:
    echo    git clone ^<url-del-repo^>
    echo A partir de ahi, este boton ya te va a andar siempre.
    pause
    exit /b 1
)

REM Probamos "git" por PATH primero ^(la instalacion normal de Git para
REM Windows lo deja disponible asi^); si no esta, probamos la ruta de
REM Git portatil usada en la app de Documentacion, por si la PC ya la
REM tiene instalada ahi.
set "GIT_EXE="
git --version >nul 2>&1
if not errorlevel 1 set "GIT_EXE=git"

if not defined GIT_EXE (
    if exist "C:\Users\daiana.lezcano\PortableGit\cmd\git.exe" (
        set "GIT_EXE=C:\Users\daiana.lezcano\PortableGit\cmd\git.exe"
    )
)

if not defined GIT_EXE (
    echo No se encontro git instalado en esta PC.
    echo Instalalo desde https://git-scm.com/download/win y volve a ejecutar este boton.
    pause
    exit /b 1
)

REM La carpeta puede vivir en una unidad de red compartida por todo el
REM equipo (ej. NAS) — por default, git no confia en carpetas que no
REM detecta como "propias" del usuario y tira "detected dubious
REM ownership" en vez de actualizar. Se agrega una excepcion para ESTA
REM carpeta puntual la primera vez (a diferencia de "config --add"
REM solo, este chequeo evita que el .gitconfig acumule la misma linea
REM de nuevo en cada doble clic).
"%GIT_EXE%" config --global --get-all safe.directory | findstr /L /C:"%CD%" >nul
if errorlevel 1 "%GIT_EXE%" config --global --add safe.directory "%CD%"

"%GIT_EXE%" pull

pause
