@echo off
title CEL Robox Control Panel
cd /d "%~dp0"
echo Starting CEL Robox Control Panel...
echo Open http://localhost:8080 in your browser
echo Press Ctrl+C to stop
python -m robox_print.robox_control
pause
