@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting SchoolHub...
python run_schoolhub.py
echo.
echo === Program exited ===
pause
