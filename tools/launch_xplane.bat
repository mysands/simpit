@echo off
rem SimPit X-Plane launcher - gates launch on the ortho mount being ready.
rem The rclone process starting is NOT the ready signal: with a ~200 GB
rem VFS cache it reconciles for ~3 minutes before WinFsp attaches X:.
rem The drive letter only exists once the mount is fully served, so a
rem successful read of scenery_packs.ini through X: is the ready gate.
title SimPit X-Plane Launcher
setlocal
set "READY_FILE=X:\scenery_packs.ini"
set "XPLANE_EXE=C:\X-Plane 12.1\X-Plane.exe"
set /a WAITED=0

if exist "%READY_FILE%" goto ready

echo Waiting for ortho mount (X:) to come up...
echo (rclone reconciles its cache before attaching the drive; ~3 min is normal)
:wait
if exist "%READY_FILE%" goto ready
timeout /t 5 /nobreak >nul
set /a WAITED+=5
echo   still waiting... %WAITED%s
if %WAITED% geq 600 goto timeout
goto wait

:ready
echo Ortho mount is ready. Launching X-Plane...
start "" "%XPLANE_EXE%"
exit /b 0

:timeout
echo.
echo ERROR: mount not ready after 10 minutes.
echo Is the "SimPit Ortho Mount (X:)" window running? (tools\ortho_mount.bat)
pause
exit /b 1
