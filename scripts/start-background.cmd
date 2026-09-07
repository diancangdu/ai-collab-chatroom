@echo off
cd /d "%~dp0.."
python chatroom\background_service.py start
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8787/?project=AllAgentStudy"
