@echo off
REM setup_xplane_task.bat  —  Create the SimPit\LaunchXPlane scheduled task.
REM Required env: XPLANE_FOLDER  SIM_EXE_NAME
REM
REM This script requires admin rights to create a scheduled task.
REM If not already elevated, it re-launches itself via PowerShell
REM runas so UAC pops up on the interactive desktop.
REM
REM Run this once per slave at setup time, and again if the task
REM is accidentally deleted.

REM ── Elevation check ────────────────────────────────────────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Not elevated — requesting UAC...
    powershell -NoProfile -Command ^
        "Start-Process cmd.exe -ArgumentList '/c \"%~f0\"' -Verb RunAs -Wait"
    exit /b %errorlevel%
)

REM ── Env validation ─────────────────────────────────────────────────────────
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
    echo ERROR: not found: %XP_EXE% >&2
    exit /b 1
)

REM ── Create task folder if needed ───────────────────────────────────────────
schtasks /query /tn "SimPit" >nul 2>&1
if %errorlevel% neq 0 (
    REM Folder doesn't exist — create a dummy task in it then delete,
    REM which is the only way to create a task folder via schtasks.exe.
    schtasks /create /tn "SimPit\_init" /tr "cmd /c exit" /sc once ^
             /st 00:00 /f >nul 2>&1
    schtasks /delete /tn "SimPit\_init" /f >nul 2>&1
)

REM ── Create or overwrite the LaunchXPlane task ───────────────────────────────
REM /ru "" means "run as the currently logged-in interactive user"
REM /it means "only run when the user is logged in interactively"
REM /rl HIGHEST elevates the task token if the user is an admin
schtasks /create ^
    /tn "SimPit\LaunchXPlane" ^
    /tr "\"%XP_EXE%\"" ^
    /sc once ^
    /st 00:00 ^
    /ru "" ^
    /it ^
    /f
if %errorlevel% neq 0 (
    echo ERROR: Failed to create scheduled task. >&2
    exit /b 1
)

echo.
echo SimPit\LaunchXPlane task created successfully.
echo X-Plane path: %XP_EXE%
echo.
echo You can now use "Launch X-Plane" from SimPit Control.
exit /b 0
