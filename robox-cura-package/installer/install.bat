@echo off
title CEL Robox Cura Installer
echo ===============================================
echo  CEL Robox - Cura Integration Installer
echo ===============================================
echo.

:: Get Cura version
set CURA_VER=5.13
set /p CURA_VER="Cura version (default: 5.13): "

set CURA_APPDATA=%APPDATA%\cura\%CURA_VER%
set CURA_DEF=%CURA_APPDATA%\definitions
set CURA_EXT=%CURA_APPDATA%\extruders
set CURA_PLUG=%CURA_APPDATA%\plugins\RoboxPrint
set CEL_DIR=%USERPROFILE%\Documents\CEL Robox
set COMMON_DIR=%CEL_DIR%\Common

echo.
echo Installing to: %CURA_APPDATA%

:: Step 1: Create folders
echo [1/5] Creating directories...
mkdir "%CURA_DEF%" 2>nul
mkdir "%CURA_EXT%" 2>nul
mkdir "%CURA_PLUG%" 2>nul
mkdir "%CURA_PLUG%\resources\robox_print" 2>nul
mkdir "%CEL_DIR%" 2>nul
mkdir "%COMMON_DIR%" 2>nul

:: Step 2: Copy printer definitions
echo [2/5] Installing printer definitions...
copy /Y "%~dp0definitions\cel_robox.def.json" "%CURA_DEF%\" >nul
copy /Y "%~dp0definitions\cel_robox_extruder_0.def.json" "%CURA_EXT%\" >nul
echo      - CEL Robox printer added to Cura definitions

:: Step 3: Install Cura plugin
echo [3/5] Installing Cura plugin...
copy /Y "%~dp0plugin\plugin.json" "%CURA_PLUG%\" >nul
copy /Y "%~dp0plugin\__init__.py" "%CURA_PLUG%\" >nul
copy /Y "%~dp0plugin\RoboxOutputDevicePlugin.py" "%CURA_PLUG%\" >nul
copy /Y "%~dp0plugin\resources\robox_print\*.py" "%CURA_PLUG%\resources\robox_print\" >nul
echo      - RoboxPrint plugin v1.0.0 installed

:: Step 4: Install Python backend (pyserial)
echo [4/5] Checking Python dependencies...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo      WARNING: Python not found. Download from https://www.python.org/downloads/
    echo      Then run: pip install pyserial
) else (
    python -m pip install pyserial -q >nul 2>&1
    echo      - pyserial installed
)

:: Step 5: Copy Common resources
echo [5/5] Setting up Common resources...
if exist "%~dp0..\CEL Robox\Common\Macros" (
    xcopy /E /I /Y "%~dp0..\CEL Robox\Common\*" "%COMMON_DIR%\" >nul
    echo      - Printer profiles, heads, macros copied
) else (
    echo      - Common resources not found (use robox_drop_print.bat to configure)
)

:: Verify installation
echo.
echo ===============================================
echo  Installation Complete
echo ===============================================
echo.
echo  Installed:
echo    Printer definition: %CURA_DEF%\cel_robox.def.json
echo    Extruder definition: %CURA_EXT%\cel_robox_extruder_0.def.json
echo    Plugin: %CURA_PLUG%
echo.
echo  Next steps:
echo    1. Restart Cura
echo    2. Settings -^> Printers -^> Add Printer
echo    3. Look for "CEL" manufacturer -^> "CEL Robox"
echo    4. Connect your Robox via USB -^> "Print with Robox" appears
echo.
echo  If printer definition doesn't appear, use:
echo    Custom FDM Printer (210x150x100mm, Marlin)
echo.
echo  The plugin auto-detects your Robox and adds
echo  "Print with Robox" to the print menu.
echo.
pause
