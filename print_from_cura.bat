@echo off
title Robox Print Tool
echo ===============================================
echo  CEL Robox - Print from Cura
echo ===============================================
echo.

set GCODE_DIR=%USERPROFILE%\Documents\CEL Robox\GCode
if not exist "%GCODE_DIR%" mkdir "%GCODE_DIR%"

echo Choose your head type:
echo [1] Dual Material 0.4mm (RBX01-DM)
echo [2] Single Material 0.3/0.8mm (RBX01-SM)
echo [3] SingleLite (RBXDV-S1)
set /p HEAD="Enter 1-3 [1]: "
if "%HEAD%"=="" set HEAD=1
if "%HEAD%"=="1" set HEAD_TYPE=RBX01-DM
if "%HEAD%"=="2" set HEAD_TYPE=RBX01-SM
if "%HEAD%"=="3" set HEAD_TYPE=RBXDV-S1

echo.
set /p FILE="Drag G-code file here and press Enter: "

set FILE=%FILE:"=%
if not exist "%FILE%" (
    echo Error: File not found!
    pause
    exit /b 1
)

echo.
echo ===============================================
echo  Processing for %HEAD_TYPE%...
echo ===============================================
cd /d "%~dp0"
python -m robox_print print "%FILE%" --head-type %HEAD_TYPE% --output "%GCODE_DIR%\processed.gcode"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Dry-run processing successful!
    echo Processed G-code saved to: %GCODE_DIR%\processed.gcode
    echo.
    echo To print for real, make sure your printer is connected
    echo and run this command in a terminal:
    echo.
    echo cd "%~dp0"
    echo python -m robox_print print "%FILE%" --head-type %HEAD_TYPE%
)

echo.
pause
