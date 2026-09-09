@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting Pinghe Launcher Lite...
python run_hellopinghe.py
echo.
echo === Program exited ===
pause
