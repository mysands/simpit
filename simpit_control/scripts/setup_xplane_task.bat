@echo off
REM setup_xplane_task.bat  —  Create SimPit\LaunchXPlane scheduled task.
REM Required env: XPLANE_FOLDER  SIM_EXE_NAME
REM
REM Strategy: Try creating the task without elevation first (works when
REM the slave agent runs as the logged-in user via Task Scheduler).
REM If that fails, fall back to a manual-elevation path.
REM
REM If NEITHER works, run this bat manually on the slave machine:
REM   Right-click -> Run as administrator

setlocal

if "%XPLANE_FOLDER%"=="" (
    echo ERROR: XPLANE_FOLDER not set >&2
    exit /b 1
)
if "%SIM_EXE_NAME%"=="" (
    echo ERROR: SIM_EXE_NAME not set >&2
    exit /b 1
)

set XP_EXE=%XPLANE_FOLDER%%SIM_EXE_NAME%
if not exist "%XP_EXE%" (
    echo ERROR: executable not found: %XP_EXE% >&2
    exit /b 1
)

REM ── Check if task already exists ──────────────────────────────────────────
schtasks /query /tn "SimPit\LaunchXPlane" >nul 2>&1
if %errorlevel%==0 (
    echo Task SimPit\LaunchXPlane already exists.
    echo Updating it with current path: %XP_EXE%
)

REM ── Attempt 1: create as current user, no elevation needed ─────────────────
REM /ru ""  = run as the current logged-in user (no password required)
REM /it     = only run when user is logged in interactively  
REM /f      = overwrite if exists
schtasks /create ^
    /tn "SimPit\LaunchXPlane" ^
    /tr "\"%XP_EXE%\"" ^
    /sc once /st 00:00 ^
    /ru "" /it /f >nul 2>&1

if %errorlevel%==0 (
    echo SUCCESS: Task created as current user.
    echo X-Plane path: %XP_EXE%
    echo.
    echo SimPit\LaunchXPlane is ready. Use Launch X-Plane from Control.
    exit /b 0
)

echo Attempt 1 failed ^(may need elevation^). Trying elevated path...

REM ── Attempt 2: re-launch this script elevated via PowerShell runas ─────────
REM This will show a UAC dialog on the SLAVE screen ^(not Control^).
REM Watch the slave monitor for the UAC prompt.
echo.
echo *** A UAC dialog should appear on the slave screen. ***
echo *** Click Yes to allow the task to be created.      ***
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Start-Process cmd.exe -ArgumentList '/c \"%~f0\"' -Verb RunAs -Wait" ^
    2>&1

if %errorlevel%==0 (
    echo Elevated setup completed.
    exit /b 0
)

REM ── Attempt 3: nothing worked — instruct manual run ───────────────────────
echo.
echo ERROR: Could not create task automatically. >&2
echo. >&2
echo MANUAL FIX: On the slave machine, right-click this file and >&2
echo choose "Run as administrator": >&2
echo   %~f0 >&2
echo. >&2
exit /b 1
