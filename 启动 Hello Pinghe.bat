@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting Hello Pinghe! Launcher...
python run_hellopinghe.py
echo.
echo === Program exited ===
pause
