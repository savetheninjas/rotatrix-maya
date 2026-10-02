@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
    echo Install failed with exit code %ERR%.
) else (
    echo Install finished.
)
echo.
pause
exit /b %ERR%
