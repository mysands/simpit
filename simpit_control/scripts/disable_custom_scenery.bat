@echo off
REM disable_custom_scenery.bat
REM ============================================================
REM Spec:
REM   1. Look under XPLANE_FOLDER for "Custom Scenery".
REM        - exists  -> rename to "Custom Scenery DISABLED"
REM        - missing -> ERROR
REM   2. Then look for "Custom Scenery DEFAULT".
REM        - exists  -> rename to "Custom Scenery"
REM        - missing -> create empty "Custom Scenery DEFAULT",
REM                     then rename to "Custom Scenery"
REM
REM End state: a fresh "Custom Scenery" folder is in place
REM (either the saved DEFAULT, or empty); the previous active
REM scenery is preserved as "Custom Scenery DISABLED".
REM
REM Required env: XPLANE_FOLDER
REM Optional env: SIM_EXE_NAME (used as a safety check)
REM ============================================================

REM EnableDelayedExpansion lets us read errorlevel inside if-blocks
REM via !errorlevel! - %errorlevel% would expand at parse time.
setlocal EnableDelayedExpansion

REM -- Pre-flight ----------------------------------------------
if "%XPLANE_FOLDER%"=="" (
    echo ERROR: XPLANE_FOLDER not set 1>&2
    exit /b 1
)

if not exist "%XPLANE_FOLDER%\" (
    echo ERROR: XPLANE_FOLDER does not exist: %XPLANE_FOLDER% 1>&2
    exit /b 1
)

set "ENABLED=%XPLANE_FOLDER%\Custom Scenery"
set "DISABLED=%XPLANE_FOLDER%\Custom Scenery DISABLED"
set "DEFAULT=%XPLANE_FOLDER%\Custom Scenery DEFAULT"

echo [disable_custom_scenery] XPLANE_FOLDER=%XPLANE_FOLDER%

REM Refuse to touch folders if X-Plane has them open
if not "%SIM_EXE_NAME%"=="" (
    tasklist /fi "imagename eq %SIM_EXE_NAME%" 2>nul | find /i "%SIM_EXE_NAME%" >nul
    if !errorlevel!==0 (
        echo ERROR: %SIM_EXE_NAME% is running; quit X-Plane before disabling scenery 1>&2
        exit /b 1
    )
)

REM Refuse to clobber an existing DISABLED - fail early with a clear message
if exist "%DISABLED%" (
    echo ERROR: "Custom Scenery DISABLED" already exists; remove it manually before re-running 1>&2
    exit /b 1
)

REM -- Step 1: Custom Scenery -> Custom Scenery DISABLED -------
if not exist "%ENABLED%\" (
    echo ERROR: "Custom Scenery" not found under XPLANE_FOLDER; cannot disable 1>&2
    exit /b 1
)

echo [disable_custom_scenery] renaming "Custom Scenery" to "Custom Scenery DISABLED"
ren "%ENABLED%" "Custom Scenery DISABLED"
if !errorlevel! neq 0 (
    echo ERROR: rename of "Custom Scenery" failed ^(errorlevel !errorlevel!^) 1>&2
    exit /b 1
)

REM -- Step 2: Custom Scenery DEFAULT -> Custom Scenery --------
if exist "%DEFAULT%\" (
    echo [disable_custom_scenery] renaming "Custom Scenery DEFAULT" to "Custom Scenery"
    ren "%DEFAULT%" "Custom Scenery"
    if !errorlevel! neq 0 (
        echo ERROR: rename of "Custom Scenery DEFAULT" failed ^(errorlevel !errorlevel!^) 1>&2
        exit /b 1
    )
) else (
    echo [disable_custom_scenery] "Custom Scenery DEFAULT" not present; creating empty then renaming
    mkdir "%DEFAULT%"
    if !errorlevel! neq 0 (
        echo ERROR: mkdir of "Custom Scenery DEFAULT" failed ^(errorlevel !errorlevel!^) 1>&2
        exit /b 1
    )
    ren "%DEFAULT%" "Custom Scenery"
    if !errorlevel! neq 0 (
        echo ERROR: rename of newly created "Custom Scenery DEFAULT" failed ^(errorlevel !errorlevel!^) 1>&2
        exit /b 1
    )
)

echo [disable_custom_scenery] OK: scenery disabled; previous content saved as "Custom Scenery DISABLED"
exit /b 0
