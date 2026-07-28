@echo off
title Robox Printer - Drag & Drop Print
echo.
echo  Drag a G-code file from Cura onto this window
echo  to process and print it on your Robox.
echo.
echo  Or just press Enter to process a sample.
echo.
set /p FILE="File: "
set FILE=%FILE:"=%

if "%FILE%"=="" goto :eof
if not exist "%FILE%" (
    echo File not found!
    pause
    exit /b 1
)

cd /d "%~dp0"
echo.
echo Processing for Single Material head...
echo.
python -m robox_print print "%FILE%" --head-type RBX01-SM --output "%TEMP%\robox_processed.gcode"
if %ERRORLEVEL% NEQ 0 goto :error

echo.
echo Dry-run complete. To print with printer connected, remove --dry-run.
pause
exit /b 0

:error
echo.
echo Failed. Make sure your printer is connected via USB.
pause
