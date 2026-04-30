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
set XPLANE_FOLDER=%XPLANE_FOLDER%
if not "%XPLANE_FOLDER:~-1%"=="\" set XPLANE_FOLDER=%XPLANE_FOLDER%\

set XP_EXE=%XPLANE_FOLDER%%SIM_EXE_NAME%
echo [DEBUG] XP_EXE=%XP_EXE%

if not exist "%XP_EXE%" (
    echo ERROR: executable not found: %XP_EXE% >&2
    exit /b 1
)
echo [DEBUG] executable found OK

REM Use XML-based task creation to avoid password prompts and
REM interactive mode issues with schtasks /ru ""
echo [DEBUG] writing task XML...
set TASK_XML=%TEMP%\simpit_launch_xplane.xml
(
echo ^<?xml version="1.0" encoding="UTF-16"?^>
echo ^<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"^>
echo   ^<Triggers/^>
echo   ^<Principals^>
echo     ^<Principal id="Author"^>
echo       ^<LogonType^>InteractiveToken^</LogonType^>
echo       ^<RunLevel^>HighestAvailable^</RunLevel^>
echo     ^</Principal^>
echo   ^</Principals^>
echo   ^<Settings^>
echo     ^<MultipleInstancesPolicy^>IgnoreNew^</MultipleInstancesPolicy^>
echo     ^<DisallowStartIfOnBatteries^>false^</DisallowStartIfOnBatteries^>
echo     ^<StopIfGoingOnBatteries^>false^</StopIfGoingOnBatteries^>
echo     ^<ExecutionTimeLimit^>PT1H^</ExecutionTimeLimit^>
echo     ^<Priority^>7^</Priority^>
echo   ^</Settings^>
echo   ^<Actions^>
echo     ^<Exec^>
echo       ^<Command^>%XP_EXE%^</Command^>
echo       ^<WorkingDirectory^>%XPLANE_FOLDER%^</WorkingDirectory^>
echo     ^</Exec^>
echo   ^</Actions^>
echo ^</Task^>
) > "%TASK_XML%"

echo [DEBUG] registering task via SchTasks XML import...
schtasks /create /tn "SimPit\LaunchXPlane" /xml "%TASK_XML%" /f
set SCHTASKS_ERR=%errorlevel%
del "%TASK_XML%" 2>nul
echo [DEBUG] schtasks exit code: %SCHTASKS_ERR%

if %SCHTASKS_ERR%==0 (
    echo SUCCESS: Task SimPit\LaunchXPlane created.
    echo X-Plane path: %XP_EXE%
    exit /b 0
)

echo ERROR: schtasks /create failed with code %SCHTASKS_ERR% >&2
echo Run this script manually as Administrator on the slave machine. >&2
exit /b 1
