@echo off
chcp 65001 >nul
if "%~1"=="" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dispatch.ps1" -Action stop -Project main
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dispatch.ps1" -Action stop -Project "%~1"
)
echo.
pause
