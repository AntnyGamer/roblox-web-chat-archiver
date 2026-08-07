@echo off
setlocal
cd /d "%~dp0"
title Roblox Web Chat Archiver - 2026 Refresh

where powershell.exe >nul 2>nul
if %errorlevel%==0 (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0archiver.ps1"
    goto :done
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 app.py
    goto :done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python app.py
    goto :done
)

echo.
echo Neither Windows PowerShell nor Python 3 was found.
echo This should be very unusual on Windows 10/11.
echo.

:done
echo.
pause
endlocal
