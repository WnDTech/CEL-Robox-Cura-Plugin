@echo off
title CEL Robox - Copy Definitions
echo.
echo This will add the CEL Robox printer to Cura.
echo.
echo Press any key to install, or close this window to cancel.
pause >nul

set CURA_VER=5.13
if exist "%APPDATA%\cura\5.13" set CURA_VER=5.13
if exist "%APPDATA%\cura\5.12" set CURA_VER=5.12
if exist "%APPDATA%\cura\5.11" set CURA_VER=5.11
if exist "%APPDATA%\cura\5.10" set CURA_VER=5.10
if exist "%APPDATA%\cura\5.9" set CURA_VER=5.9
if exist "%APPDATA%\cura\5.8" set CURA_VER=5.8
if exist "%APPDATA%\cura\5.7" set CURA_VER=5.7
if exist "%APPDATA%\cura\5.6" set CURA_VER=5.6
if exist "%APPDATA%\cura\5.5" set CURA_VER=5.5
if exist "%APPDATA%\cura\5.4" set CURA_VER=5.4

mkdir "%APPDATA%\cura\%CURA_VER%\definitions" 2>nul
mkdir "%APPDATA%\cura\%CURA_VER%\extruders" 2>nul

copy /Y "%~dp0cel_robox.def.json" "%APPDATA%\cura\%CURA_VER%\definitions\" >nul
copy /Y "%~dp0cel_robox_extruder_0.def.json" "%APPDATA%\cura\%CURA_VER%\extruders\" >nul

echo.
echo Done! Restart Cura, then add printer: CEL -^> CEL Robox
echo.
pause
