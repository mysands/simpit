@echo off
REM build_all.bat — build both exes then compile the Inno Setup installer.
REM Run from the repo root.

pushd "%~dp0"

echo.
echo ============================================================
echo  Step 1/3: Build simpit-control.exe
echo ============================================================
call build_control.bat
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  Step 2/3: Build simpit-slave.exe
echo ============================================================
call build_slave.bat
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  Step 3/3: Compile Inno Setup installer
echo ============================================================

set ISCC=
for /f "delims=" %%i in ('where ISCC.exe 2^>nul') do if not defined ISCC set "ISCC=%%i"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"

if not defined ISCC (
    echo ERROR: Inno Setup 6 not found. Install from https://jrsoftware.org/isinfo.php
    goto :fail
)

"%ISCC%" simpit-control-installer.iss
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  ALL DONE
echo  Installer: %~dp0dist\installer\SimPitControlSetup.exe
echo ============================================================
popd
exit /b 0

:fail
echo.
echo ============================================================
echo  BUILD FAILED. See output above.
echo ============================================================
popd
exit /b 1
