@echo off
chcp 65001 >nul 2>&1
title PA Agent
cd /d "%~dp0"
python run.py
if errorlevel 1 (
    echo.
    echo [ERROR] Program exited abnormally. Please check the message above.
    pause
)
