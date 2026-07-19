@echo off
rem SimPit ortho scenery mount - interim launcher until the ortho agent
rem supervises the mount itself (see simpit_ortho_agent handoff).
rem Flags mirror ortho_agent.json / the Ortho Cache dialog in Control:
rem cache mode FULL, 50 GB cap, effectively-infinite max-age (0 would
rem purge primed atlases), rc on localhost only.
title SimPit Ortho Mount (X:)
echo Mounting randhawanas:XPlane12/Custom Scenery as X: ...
rem Perf flags: ortho DSFs open thousands of tiny .ter files; long
rem dir/attr cache + fast-fingerprint avoid an SMB round trip per open.
rem Cleaner poll stays SHORT (2m): longer intervals let the cache balloon
rem past the cap, then a giant eviction burst deletes textures X-Plane
rem still has memory-mapped -> EXCEPTION_IN_PAGE_ERROR crash.
"%LOCALAPPDATA%\Microsoft\WinGet\Links\rclone.exe" mount "randhawanas:XPlane12/Custom Scenery" X: ^
  --vfs-cache-mode full ^
  --vfs-cache-max-size 160G ^
  --vfs-cache-max-age 8760h ^
  --vfs-cache-poll-interval 2m ^
  --vfs-fast-fingerprint ^
  --dir-cache-time 12h ^
  --attr-timeout 60s ^
  --log-file "%LOCALAPPDATA%\Programs\Simpit\ortho_mount.log" --log-level INFO ^
  --rc --rc-addr 127.0.0.1:5572 --rc-no-auth
echo.
echo Mount exited (code %ERRORLEVEL%). Window stays open so you can read any error.
pause
