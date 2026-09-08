@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
wscript.exe "%SCRIPT_DIR%one-click-deploy.vbs" %*
