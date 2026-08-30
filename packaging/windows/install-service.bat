@echo off
REM Instala Devin Mobile como servicio de Windows usando NSSM
REM Requiere: NSSM instalado (nssm.exe en PATH o en misma carpeta)
REM Ejecutar como administrador

setlocal
set SERVICE_NAME=DevinMobile
set APP_DIR=%~dp0..\..
set APP_EXE=%APP_DIR%\DevinMobile.exe

echo ========================================
echo  Instalar Devin Mobile como servicio
echo ========================================
echo.

REM Verificar admin
net session >nul 2>&1
if errorlevel 1 (
    echo ERROR: Ejecuta este script como administrador
    pause
    exit /b 1
)

REM Buscar nssm
where nssm >nul 2>&1
if errorlevel 1 (
    if exist "%~dp0nssm.exe" (
        set NSSM=%~dp0nssm.exe
    ) else (
        echo ERROR: NSSM no encontrado
        echo Descarga nssm.exe desde https://nssm.cc/download
        echo Y colocalo en esta carpeta o en el PATH
        pause
        exit /b 1
    )
) else (
    set NSSM=nssm
)

echo Instalando servicio %SERVICE_NAME%...
%NSSM% install %SERVICE_NAME% "%APP_EXE%"
%NSSM% set %SERVICE_NAME% AppDirectory "%APP_DIR%"
%NSSM% set %SERVICE_NAME% AppEnvironmentExtra DEVIN_MOBILE_PORT=8787
%NSSM% set %SERVICE_NAME% Description "Devin Mobile Dashboard - Control de sesiones Devin desde el movil"
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START
%NSSM% set %SERVICE_NAME% AppStdout "%APP_DIR%\logs\service.log"
%NSSM% set %SERVICE_NAME% AppStderr "%APP_DIR%\logs\service.log"
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 10485760

if not exist "%APP_DIR%\logs" mkdir "%APP_DIR%\logs"

echo Iniciando servicio...
%NSSM% start %SERVICE_NAME%

if errorlevel 1 (
    echo ERROR: No se pudo iniciar el servicio
    pause
    exit /b 1
)

echo.
echo Servicio instalado y iniciado correctamente.
echo El dashboard estara disponible en http://localhost:8787
echo.
pause
