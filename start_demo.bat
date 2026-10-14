@echo off
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
if errorlevel 1 (echo pip install failed & pause & exit /b 1)
python ui\server.py --open %*
