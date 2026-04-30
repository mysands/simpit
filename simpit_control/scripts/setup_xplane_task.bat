@echo off
REM setup_xplane_task.bat  —  Create SimPit\LaunchXPlane scheduled task.
REM Required env: XPLANE_FOLDER  SIM_EXE_NAME
REM
REM Does NOT require elevation. schtasks /create /ru "" /it creates a
REM task for the current user without admin rights on Windows 10/11.

setlocal

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

echo [DEBUG] creating scheduled task...
schtasks /create ^
    /tn "SimPit\LaunchXPlane" ^
    /tr "\"%XP_EXE%\"" ^
    /sc once /st 00:00 ^
    /ru "" /it /f
echo [DEBUG] schtasks exit code: %errorlevel%

if %errorlevel%==0 (
    echo SUCCESS: Task SimPit\LaunchXPlane created.
    exit /b 0
)

echo ERROR: schtasks /create failed with code %errorlevel% >&2
echo Run this script manually as Administrator on the slave machine. >&2
exit /b 1
