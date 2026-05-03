@echo off
REM build_control.bat — build simpit-control.exe via PyInstaller spec.
REM
REM Run from the repo root. The spec file (simpit-control.spec) carries
REM all flags; this script just invokes it cleanly.

pushd "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: python not on PATH.
    popd & exit /b 1
)

python -m PyInstaller --clean simpit-control.spec
if errorlevel 1 (
    echo.
    echo ============================================================
    echo  BUILD FAILED. See output above.
    echo ============================================================
    popd & exit /b 1
)

echo.
echo ============================================================
echo  BUILD COMPLETE
echo  Output: %~dp0dist\simpit-control.exe
echo ============================================================
popd
