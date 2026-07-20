@echo off
REM build_ortho_agent.bat — build simpit-ortho-agent.exe via PyInstaller spec.
REM
REM Run from the repo root. The spec file (simpit-ortho-agent.spec)
REM carries all flags; this script just invokes it cleanly.

pushd "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: python not on PATH.
    popd & exit /b 1
)

python -m PyInstaller --clean simpit-ortho-agent.spec
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
echo  Output: %~dp0dist\simpit-ortho-agent.exe
echo ============================================================
popd
