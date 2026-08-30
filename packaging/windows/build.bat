@echo off
REM Build script para Devin Mobile Dashboard - Windows
REM Genera un exe con PyInstaller y un instalador con Inno Setup

setlocal
cd /d "%~dp0\..\.."

echo ========================================
echo  Devin Mobile Dashboard - Build Windows
echo ========================================
echo.

REM Verificar pyinstaller
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Instalando PyInstaller...
    pip install pyinstaller
)

REM Crear config.json.example si no existe
if not exist config.json.example (
    echo {> config.json.example
    echo   "username": "admin",>> config.json.example
    echo   "password": "cambia-esta-contrasena">> config.json.example
    echo }>> config.json.example
)

echo Compilando con PyInstaller...
pyinstaller packaging\windows\devin-mobile.spec --clean --noconfirm

if errorlevel 1 (
    echo ERROR: PyInstaller fallo
    exit /b 1
)

echo.
echo Build completado: dist\DevinMobile.exe
echo.

REM Verificar Inno Setup
where iscc >nul 2>&1
if errorlevel 1 (
    echo Inno Setup no encontrado. Instalando...
    echo Descarga Inno Setup desde https://jrsoftware.org/isdl.php
    echo O: choco install innosetup
    echo.
    echo El exe esta listo en dist\DevinMobile.exe
    echo Para crear un instalador, instala Inno Setup y ejecuta:
    echo   iscc packaging\windows\installer.iss
    exit /b 0
)

echo Creando instalador con Inno Setup...
iscc packaging\windows\installer.iss

if errorlevel 1 (
    echo WARNING: Inno Setup fallo, pero el exe esta listo
    exit /b 0
)

echo.
echo Instalador creado: packaging\windows\Output\DevinMobileSetup.exe
echo.
pause
