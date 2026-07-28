; CEL Robox Cura Integration Installer
; Inno Setup 6 script

#define MyAppName "CEL Robox Cura Integration"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "CEL-UK / Community"
#define MyAppURL "https://github.com/celsworthy"

[Setup]
AppId={{E5B8A1C0-3F2A-4F9E-8D7B-9C6A5B4D3E2F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={localappdata}\CEL Robox
DefaultGroupName=CEL Robox
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=.
OutputBaseFilename=CEL_Robox_Cura_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
MinVersion=0,10.0.14393

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full"; Description: "Full installation"
Name: "plugin"; Description: "Cura plugin only"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Components]
Name: "plugin"; Description: "Cura plugin + printer definitions + quality profiles"; Types: full plugin custom; Flags: fixed
Name: "backend"; Description: "Python USB backend"; Types: full custom
Name: "shortcuts"; Description: "Desktop shortcuts"; Types: full custom

[Files]
; Cura plugin files
Source: "plugin\plugin.json"; DestDir: "{userappdata}\cura\5.13\plugins\RoboxPrint"; Components: plugin; Flags: ignoreversion
Source: "plugin\__init__.py"; DestDir: "{userappdata}\cura\5.13\plugins\RoboxPrint"; Components: plugin; Flags: ignoreversion
Source: "plugin\RoboxOutputDevicePlugin.py"; DestDir: "{userappdata}\cura\5.13\plugins\RoboxPrint"; Components: plugin; Flags: ignoreversion
Source: "plugin\MonitorItem.qml"; DestDir: "{userappdata}\cura\5.13\plugins\RoboxPrint"; Components: plugin; Flags: ignoreversion

; Python backend bundled inside plugin
Source: "plugin\resources\robox_print\*.py"; DestDir: "{userappdata}\cura\5.13\plugins\RoboxPrint\resources\robox_print"; Components: plugin; Flags: ignoreversion

; Printer definitions
Source: "definitions\cel_robox.def.json"; DestDir: "{userappdata}\cura\5.13\definitions"; Components: plugin; Flags: ignoreversion
Source: "definitions\cel_robox_extruder_0.def.json"; DestDir: "{userappdata}\cura\5.13\extruders"; Components: plugin; Flags: ignoreversion

; Quality profiles
Source: "quality\cel_robox\*.inst.cfg"; DestDir: "{userappdata}\cura\5.13\quality\cel_robox"; Components: plugin; Flags: ignoreversion

; Common resources
Source: "..\..\Documents\CEL Robox\Common\Macros\*.gcode"; DestDir: "{userappdata}\..\Documents\CEL Robox\Common\Macros"; Components: plugin; Flags: ignoreversion skipifsourcedoesntexist

; Desktop tools
Source: "installer\add_printer.bat"; DestDir: "{app}"; Components: shortcuts; Flags: ignoreversion

[Icons]
Name: "{group}\CEL Robox in Cura"; Filename: "{app}\add_printer.bat"; Components: shortcuts
Name: "{group}\Uninstall CEL Robox"; Filename: "{uninstallexe}"; Components: shortcuts

[Run]
Filename: "{cmd}"; Parameters: "/C python -m pip install pyserial -q"; StatusMsg: "Installing Python USB library..."; Components: backend; Flags: runhidden

[UninstallDelete]
Type: files; Name: "{userappdata}\cura\5.13\plugins\RoboxPrint\*"
Type: files; Name: "{userappdata}\cura\5.13\definitions\cel_robox.def.json"
Type: files; Name: "{userappdata}\cura\5.13\extruders\cel_robox_extruder_0.def.json"
Type: files; Name: "{userappdata}\cura\5.13\quality\cel_robox\*.inst.cfg"
Type: dirifempty; Name: "{userappdata}\cura\5.13\quality\cel_robox"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not DirExists(ExpandConstant('{userappdata}') + '\cura\5.13') then
    begin
      if DirExists(ExpandConstant('{userappdata}') + '\cura\5.12') then
      begin
        CopyFile(ExpandConstant('{userappdata}') + '\cura\5.13\definitions\cel_robox.def.json',
                 ExpandConstant('{userappdata}') + '\cura\5.12\definitions\cel_robox.def.json', False);
        CopyFile(ExpandConstant('{userappdata}') + '\cura\5.13\extruders\cel_robox_extruder_0.def.json',
                 ExpandConstant('{userappdata}') + '\cura\5.12\extruders\cel_robox_extruder_0.def.json', False);
      end;
    end;
  end;
end;
