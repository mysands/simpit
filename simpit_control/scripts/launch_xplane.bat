@echo off
REM launch_xplane.bat  —  Start X-Plane (SimPit standard script)
REM Required env: XPLANE_FOLDER  SIM_EXE_NAME
REM
REM Multiple launch variations are provided below. One is active; the rest
REM are commented out. If X-Plane doesn't appear on screen, uncomment the
REM next variation, re-sync from Control, and try again.
REM
REM VARIATION SUMMARY
REM   A (active) : start "" /B        — detached, no console window
REM   B          : start "" /NORMAL   — normal window, brings console to front
REM   C          : start "" /D "dir"  — explicit working directory
REM   D          : ShellExecute via PowerShell runas — forces elevation
REM   E          : explorer.exe launch — sidesteps UAC inheritance issues
REM -----------------------------------------------------------------------

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

REM Guard: already running?
tasklist /fi "imagename eq %SIM_EXE_NAME%" 2>nul | find /i "%SIM_EXE_NAME%" >nul
if %errorlevel%==0 (
    echo X-Plane already running.
    exit /b 0
)

REM -----------------------------------------------------------------------
REM VARIATION A (active): detached background launch, no console window.
REM Works when the slave agent already has the correct session/token.
start "" /B "%XP_EXE%"
echo Launched (A /B): %XP_EXE%
exit /b 0

REM -----------------------------------------------------------------------
REM VARIATION B: normal windowed launch. Use if A produces no window.
REM start "" /NORMAL "%XP_EXE%"
REM echo Launched (B /NORMAL): %XP_EXE%
REM exit /b 0

REM -----------------------------------------------------------------------
REM VARIATION C: set working directory explicitly to XPLANE_FOLDER.
REM X-Plane sometimes fails to find resources if CWD is wrong.
REM start "" /D "%XPLANE_FOLDER%" /B "%XP_EXE%"
REM echo Launched (C /D + /B): %XP_EXE%
REM exit /b 0

REM -----------------------------------------------------------------------
REM VARIATION D: PowerShell ShellExecute — asks Windows to launch it the
REM same way a user double-clicking the icon would. Handles UAC and
REM display-session attachment better than a raw CreateProcess.
REM powershell -NoProfile -Command "Start-Process -FilePath '%XP_EXE%' -WorkingDirectory '%XPLANE_FOLDER%'"
REM echo Launched (D PowerShell Start-Process): %XP_EXE%
REM exit /b 0

REM -----------------------------------------------------------------------
REM VARIATION E: launch via explorer.exe — completely sidesteps UAC token
REM inheritance and always attaches to the interactive desktop session.
REM explorer.exe "%XP_EXE%"
REM echo Launched (E explorer.exe): %XP_EXE%
REM exit /b 0
