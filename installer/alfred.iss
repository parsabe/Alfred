; Inno Setup script: builds dist\Alfred-Setup.exe from dist\Alfred.exe.
; Installs for the current user only (no administrator rights needed).

#define AppVersion "0.1.0"

[Setup]
AppId={{6F1D2B8E-5A43-4C1E-9E0B-7A1F3C2D9B41}
AppName=Alfred
AppVersion={#AppVersion}
AppPublisher=Alfred
DefaultDirName={localappdata}\Programs\Alfred
DefaultGroupName=Alfred
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Alfred-Setup
SetupIconFile=..\packaging\alfred.ico
UninstallDisplayIcon={app}\Alfred.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Listen for ""Alfred, …"" when I sign in"; Flags: unchecked
Name: "addtopath"; Description: "Add the 'alfred' command to PATH (for terminals)"

[Files]
Source: "..\dist\Alfred.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\packaging\alfred.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Alfred"; Filename: "{app}\Alfred.exe"; Parameters: "open"; IconFilename: "{app}\alfred.ico"; Comment: "Alfred in an app window"
Name: "{group}\Alfred (hands-free)"; Filename: "{app}\Alfred.exe"; Parameters: "listen"; IconFilename: "{app}\alfred.ico"; Comment: "Listen for ""Alfred, …"""
Name: "{group}\Alfred setup"; Filename: "{app}\Alfred.exe"; Parameters: "setup"; IconFilename: "{app}\alfred.ico"
Name: "{group}\Uninstall Alfred"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Alfred"; Filename: "{app}\Alfred.exe"; Parameters: "open"; IconFilename: "{app}\alfred.ico"; Tasks: desktopicon
Name: "{userstartup}\Alfred (hands-free)"; Filename: "{app}\Alfred.exe"; Parameters: "listen"; IconFilename: "{app}\alfred.ico"; Flags: runminimized; Tasks: autostart

[Registry]
; per-user PATH entry so `alfred` works in Command Prompt / PowerShell
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\Alfred.exe"; Parameters: "setup"; Description: "Connect to your Alfred server now"; Flags: postinstall nowait

[Code]
function NeedsAddPath(Dir: string): Boolean;
var
  Paths: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Paths) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') = 0;
end;
