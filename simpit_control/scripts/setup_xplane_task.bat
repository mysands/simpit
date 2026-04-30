@echo off
REM setup_xplane_task.bat  —  Create SimPit\LaunchXPlane scheduled task.
REM Required env: XPLANE_FOLDER  SIM_EXE_NAME

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

REM Auto-append trailing backslash if missing
if not "%XPLANE_FOLDER:~-1%"=="\" set XPLANE_FOLDER=%XPLANE_FOLDER%\

set XP_EXE=%XPLANE_FOLDER%%SIM_EXE_NAME%
echo [DEBUG] XP_EXE=%XP_EXE%

if not exist "%XP_EXE%" (
    echo ERROR: executable not found: %XP_EXE% >&2
    exit /b 1
)
echo [DEBUG] executable found OK

echo [DEBUG] registering task via PowerShell...
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
    "$action = New-ScheduledTaskAction -Execute '%XP_EXE%' -WorkingDirectory '%XPLANE_FOLDER%';" ^
    "$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2);" ^
    "$principal = New-ScheduledTaskPrincipal -LogonType Interactive -RunLevel Highest -UserId $env:USERNAME;" ^
    "Register-ScheduledTask -TaskName 'SimPit\LaunchXPlane' -Action $action -Settings $settings -Principal $principal -Force;" ^
    "exit $LASTEXITCODE"

echo [DEBUG] powershell exit code: %errorlevel%
if %errorlevel%==0 (
    echo SUCCESS: Task SimPit\LaunchXPlane created.
    echo X-Plane: %XP_EXE%
    exit /b 0
)

echo ERROR: Failed to create task, exit code %errorlevel% >&2
exit /b 1
