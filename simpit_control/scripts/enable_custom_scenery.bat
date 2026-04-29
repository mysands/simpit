@echo off
REM enable_custom_scenery.bat  —  Rename 'Custom Scenery DISABLED' back to 'Custom Scenery'
REM Required env: XPLANE_FOLDER  SIM_EXE_NAME
if "%XPLANE_FOLDER%"=="" (
    echo ERROR: XPLANE_FOLDER not set >&2
    exit /b 1
)
set DISABLED_DIR=%XPLANE_FOLDER%\Custom Scenery DISABLED
set ENABLED_DIR=%XPLANE_FOLDER%\Custom Scenery

REM Guard: don't touch folders if X-Plane is running
tasklist /fi "imagename eq %SIM_EXE_NAME%" 2>nul | find /i "%SIM_EXE_NAME%" >nul
if %errorlevel%==0 (
    echo ERROR: X-Plane is running — quit before toggling scenery >&2
    exit /b 1
)

if not exist "%DISABLED_DIR%" (
    echo Custom Scenery is already enabled (or folder missing).
    exit /b 0
)

if exist "%ENABLED_DIR%" (
    echo ERROR: Both 'Custom Scenery' and 'Custom Scenery DISABLED' exist >&2
    exit /b 1
)

ren "%DISABLED_DIR%" "Custom Scenery"
echo Custom Scenery enabled.
exit /b 0
