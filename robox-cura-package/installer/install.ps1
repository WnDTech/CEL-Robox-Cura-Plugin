param(
    [string]$CuraVersion = "5.13"
)

$ErrorActionPreference = "Stop"

$CuraAppData = "$env:APPDATA\cura\$CuraVersion"
$DefDir = "$CuraAppData\definitions"
$ExtDir = "$CuraAppData\extruders"
$PluginDir = "$CuraAppData\plugins\RoboxPrint"
$BackendDir = "$PluginDir\resources\robox_print"
$CelDir = "$env:USERPROFILE\Documents\CEL Robox"
$CommonDir = "$CelDir\Common"
$PackageDir = Split-Path -Parent $PSScriptRoot

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host " CEL Robox - Cura Integration Installer" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Target Cura version: $CuraVersion" -ForegroundColor Gray
Write-Host "Plugin destination: $PluginDir" -ForegroundColor Gray
Write-Host ""

# Create directories
Write-Host "[1/5] Creating directories..." -NoNewline
@($DefDir, $ExtDir, $PluginDir, $BackendDir, $CelDir, $CommonDir) | ForEach-Object {
    New-Item -ItemType Directory -Path $_ -Force | Out-Null
}
Write-Host " Done" -ForegroundColor Green

# Copy printer definitions
Write-Host "[2/5] Installing printer definitions..." -NoNewline
Copy-Item "$PackageDir\definitions\cel_robox.def.json" "$DefDir\" -Force -ErrorAction SilentlyContinue
Copy-Item "$PackageDir\definitions\cel_robox_extruder_0.def.json" "$ExtDir\" -Force -ErrorAction SilentlyContinue
Write-Host " Done" -ForegroundColor Green

# Copy Cura plugin
Write-Host "[3/5] Installing Cura plugin..." -NoNewline
Copy-Item "$PackageDir\plugin\plugin.json" "$PluginDir\" -Force
Copy-Item "$PackageDir\plugin\__init__.py" "$PluginDir\" -Force
Copy-Item "$PackageDir\plugin\RoboxOutputDevicePlugin.py" "$PluginDir\" -Force
Copy-Item "$PackageDir\plugin\resources\robox_print\*.py" "$BackendDir\" -Force
Write-Host " Done" -ForegroundColor Green

# Install pyserial
Write-Host "[4/5] Python dependencies..." -NoNewline
try {
    $py = Get-Command python -ErrorAction Stop
    & $py -m pip install pyserial -q 2>&1 | Out-Null
    Write-Host " pyserial installed" -ForegroundColor Green
} catch {
    Write-Host " Python not found! Install from python.org" -ForegroundColor Yellow
}

# Copy Common resources
Write-Host "[5/5] Common resources..." -NoNewline
$sourceCommon = "$PackageDir\..\..\..\Documents\CEL Robox\Common"
if (Test-Path "$sourceCommon\Macros") {
    Copy-Item "$sourceCommon\*" "$CommonDir\" -Recurse -Force
    Write-Host " Done" -ForegroundColor Green
} else {
    Write-Host " Skipped (not bundled)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host " Installation Complete!" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Restart Cura, then go to:"
Write-Host "  Settings -> Printers -> Add Printer -> CEL -> CEL Robox"
Write-Host ""
Write-Host "Or use Custom FDM (210x150x100mm, Marlin) as fallback."
Write-Host "The plugin auto-detects Robox via USB for direct printing."
Write-Host ""
Read-Host "Press Enter to exit"
