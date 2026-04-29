@echo off
REM disable_custom_scenery.bat  —  Rename 'Custom Scenery' to 'Custom Scenery DISABLED'
REM Required env: XPLANE_FOLDER  SIM_EXE_NAME
if "%XPLANE_FOLDER%"=="" (
    echo ERROR: XPLANE_FOLDER not set >&2
    exit /b 1
)
set DISABLED_DIR=%XPLANE_FOLDER%\Custom Scenery DISABLED
set ENABLED_DIR=%XPLANE_FOLDER%\Custom Scenery
set DEFAULT_DIR=%XPLANE_FOLDER%\Custom Scenery\DEFAULT

REM Guard: don't touch folders if X-Plane is running
tasklist /fi "imagename eq %SIM_EXE_NAME%" 2>nul | find /i "%SIM_EXE_NAME%" >nul
if %errorlevel%==0 (
    echo ERROR: X-Plane is running — quit before toggling scenery >&2
    exit /b 1
)

if exist "%DISABLED_DIR%" (
    echo Custom Scenery is already disabled.
    exit /b 0
)

if not exist "%ENABLED_DIR%" (
    REM Fresh install — create empty DEFAULT so XP12 doesn't complain
    mkdir "%ENABLED_DIR%"
    mkdir "%DEFAULT_DIR%"
    echo Created empty Custom Scenery\DEFAULT for fresh install.
)

ren "%ENABLED_DIR%" "Custom Scenery DISABLED"
echo Custom Scenery disabled.
exit /b 0
