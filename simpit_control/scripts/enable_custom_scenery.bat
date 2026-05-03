@echo off
REM enable_custom_scenery.bat
REM ============================================================
REM Symmetric inverse of disable_custom_scenery.
REM
REM Spec:
REM   1. Look under XPLANE_FOLDER for "Custom Scenery DISABLED".
REM        - exists  -> proceed
REM        - missing -> ERROR (nothing to re-enable)
REM   2. Look for "Custom Scenery".
REM        - exists  -> rename to "Custom Scenery DEFAULT"
REM                     (preserves baseline for next disable;
REM                      refuses if "Custom Scenery DEFAULT"
REM                      already exists, to avoid clobbering)
REM        - missing -> skip (user manually removed it, or
REM                     disable was never run)
REM   3. Rename "Custom Scenery DISABLED" -> "Custom Scenery".
REM
REM End state: user scenery active as "Custom Scenery"; the
REM baseline (if any) preserved as "Custom Scenery DEFAULT";
REM no "Custom Scenery DISABLED". disable can be run again
REM cleanly.
REM
REM Required env: XPLANE_FOLDER
REM Optional env: SIM_EXE_NAME (used as a safety check)
REM ============================================================

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

echo [enable_custom_scenery] XPLANE_FOLDER=%XPLANE_FOLDER%

REM Refuse to touch folders if X-Plane has them open
if not "%SIM_EXE_NAME%"=="" (
    tasklist /fi "imagename eq %SIM_EXE_NAME%" 2>nul | find /i "%SIM_EXE_NAME%" >nul
    if !errorlevel!==0 (
        echo ERROR: %SIM_EXE_NAME% is running; quit X-Plane before enabling scenery 1>&2
        exit /b 1
    )
)

REM -- Step 1: confirm DISABLED exists -------------------------
if not exist "%DISABLED%\" (
    echo ERROR: "Custom Scenery DISABLED" not found; nothing to re-enable 1>&2
    exit /b 1
)

REM -- Step 2: Custom Scenery -> Custom Scenery DEFAULT --------
REM (preserves the baseline so the next disable has a DEFAULT to swap in)
if exist "%ENABLED%\" (
    if exist "%DEFAULT%\" (
        echo ERROR: both "Custom Scenery" and "Custom Scenery DEFAULT" exist; cannot preserve baseline >&2
        echo        remove or rename one of them manually before re-running 1>&2
        exit /b 1
    )
    echo [enable_custom_scenery] preserving baseline: "Custom Scenery" to "Custom Scenery DEFAULT"
    ren "%ENABLED%" "Custom Scenery DEFAULT"
    if !errorlevel! neq 0 (
        echo ERROR: rename of "Custom Scenery" failed ^(errorlevel !errorlevel!^) 1>&2
        exit /b 1
    )
) else (
    echo [enable_custom_scenery] no current "Custom Scenery"; nothing to preserve as DEFAULT
)

REM -- Step 3: Custom Scenery DISABLED -> Custom Scenery -------
echo [enable_custom_scenery] renaming "Custom Scenery DISABLED" to "Custom Scenery"
ren "%DISABLED%" "Custom Scenery"
if !errorlevel! neq 0 (
    echo ERROR: rename of "Custom Scenery DISABLED" failed ^(errorlevel !errorlevel!^) 1>&2
    exit /b 1
)

echo [enable_custom_scenery] OK: scenery enabled
exit /b 0
