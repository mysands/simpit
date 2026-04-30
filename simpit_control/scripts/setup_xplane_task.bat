@echo off
REM setup_xplane_task.bat  —  Create SimPit\LaunchXPlane scheduled task.
REM Required env: XPLANE_FOLDER  SIM_EXE_NAME

setlocal

echo [DEBUG] setup_xplane_task starting
echo [DEBUG] XPLANE_FOLDER=%XPLANE_FOLDER%
echo [DEBUG] SIM_EXE_NAME=%SIM_EXE_NAME%

if "%XPLANE_FOLDER%"=="" (
    echo ERROR: XPLANE_FOLDER not set >&2
    exit /b 1
)
if "%SIM_EXE_NAME%"=="" (
    echo ERROR: SIM_EXE_NAME not set >&2
    exit /b 1
)

set XP_EXE=%XPLANE_FOLDER%%SIM_EXE_NAME%
echo [DEBUG] XP_EXE=%XP_EXE%

if not exist "%XP_EXE%" (
    echo ERROR: executable not found: %XP_EXE% >&2
    exit /b 1
)
echo [DEBUG] executable found OK

REM Check elevation via net session
net session >nul 2>&1
if %errorlevel%==0 (
    echo [DEBUG] running elevated
) else (
    echo [DEBUG] NOT elevated, errorlevel=%errorlevel%
)

REM Attempt 1: create as current user, no elevation needed
echo [DEBUG] attempting schtasks /create...
schtasks /create ^
    /tn "SimPit\LaunchXPlane" ^
    /tr "\"%XP_EXE%\"" ^
    /sc once /st 00:00 ^
    /ru "" /it /f
echo [DEBUG] schtasks exit code: %errorlevel%

if %errorlevel%==0 (
    echo SUCCESS: Task SimPit\LaunchXPlane created.
    echo X-Plane path: %XP_EXE%
    exit /b 0
)

echo ERROR: schtasks /create failed with code %errorlevel% >&2
echo [DEBUG] attempting elevated fallback via PowerShell runas...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Start-Process cmd.exe -ArgumentList '/c \"%~f0\"' -Verb RunAs -Wait" ^
    2>&1
echo [DEBUG] powershell runas exit code: %errorlevel%

if %errorlevel%==0 (
    echo Elevated setup completed.
    exit /b 0
)

echo ERROR: Could not create task automatically. >&2
echo MANUAL FIX: Right-click this file on the slave and Run as administrator: >&2
echo   %~f0 >&2
exit /b 1
