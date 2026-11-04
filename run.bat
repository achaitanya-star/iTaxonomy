@echo off
setlocal
python -c "import openpyxl" >nul 2>nul
if errorlevel 1 python -m pip install -r requirements.txt
python server.py
pause
