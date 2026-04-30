@echo off
REM launch_xplane.bat  —  Trigger X-Plane via SimPit scheduled task.
REM Required env: SIM_EXE_NAME
REM
REM The scheduled task "SimPit\LaunchXPlane" must be created first by
REM running setup_xplane_task (from Control). The task runs as the
REM logged-in user with an interactive token, so X-Plane appears on
REM the display regardless of how the slave agent was launched.
REM
REM If this script fails with "task not found", re-run setup_xplane_task
REM from Control to recreate it.

if "%SIM_EXE_NAME%"=="" (
    echo ERROR: SIM_EXE_NAME not set >&2
    exit /b 1
)

REM Guard: already running?
tasklist /fi "imagename eq %SIM_EXE_NAME%" 2>nul | find /i "%SIM_EXE_NAME%" >nul
if %errorlevel%==0 (
    echo X-Plane already running.
    exit /b 0
)

schtasks /run /tn "SimPit\LaunchXPlane"
if %errorlevel% neq 0 (
    echo ERROR: Could not run scheduled task "SimPit\LaunchXPlane". >&2
    echo Run setup_xplane_task from SimPit Control to create it. >&2
    exit /b 1
)

echo Launched X-Plane via SimPit\LaunchXPlane task.
exit /b 0
