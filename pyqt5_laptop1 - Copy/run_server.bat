@echo off
cd /d "%~dp0"
call .venv\Scripts\activate
python -m Server.server_app
pause
