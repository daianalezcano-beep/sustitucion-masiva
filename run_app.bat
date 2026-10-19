@echo off
setlocal EnableDelayedExpansion
set "PROJECT_DIR=%~dp0"

REM Doble sustitucion de costos - app local. Mismo esquema que la otra app de Tourplan:
REM el entorno de Python se crea en una ruta corta bajo la carpeta de usuario (no dentro del
REM proyecto) para no pasar el limite de ~260 caracteres de las rutas de Windows.
set "VENV_DIR=%USERPROFILE%\.sustitucion-masiva-venv"

cd /d "%PROJECT_DIR%"

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Preparando la app por primera vez, puede tardar unos minutos...

    set "PYCMD="
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PYCMD=py -3"

    if not defined PYCMD (
        python --version >nul 2>&1
        if not errorlevel 1 set "PYCMD=python"
    )

    if not defined PYCMD (
        python3 --version >nul 2>&1
        if not errorlevel 1 set "PYCMD=python3"
    )

    if not defined PYCMD (
        echo No se encontro Python instalado en esta PC.
        echo Instalalo desde https://www.python.org/downloads/, tildando "Add python.exe to PATH" durante la instalacion, y volve a ejecutar.
        pause
        exit /b 1
    )

    echo Usando: !PYCMD!
    !PYCMD! -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo No se pudo crear el entorno de Python. Verifica la instalacion de Python y volve a intentar.
        pause
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

REM Streamlit pregunta un email la primera vez que se usa en una PC; se precarga vacio para que
REM el doble clic no parezca "no hacer nada".
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    echo [general] > "%USERPROFILE%\.streamlit\credentials.toml"
    echo email = "" >> "%USERPROFILE%\.streamlit\credentials.toml"
)

REM Solo escucha en esta PC: la app no tiene login propio.
streamlit run app.py --server.address=localhost

pause
