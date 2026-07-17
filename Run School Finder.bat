@echo off
cd /d "%~dp0"
title NJ School Student-Support Staff Finder
".venv\Scripts\python.exe" run.py
if errorlevel 1 (
  echo.
  echo The app did not start. See the message above.
  pause
)

