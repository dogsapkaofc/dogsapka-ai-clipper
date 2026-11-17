; Inno Setup script - compile with ISCC.exe packaging\installer.iss (after build_windows.ps1)
#define AppName "DOGSAPKA AI CLIPPER"
#define AppVersion "0.1.0"
#define AppExe "DOGSAPKA AI CLIPPER.exe"

[Setup]
AppId={{EF5324CD-89DA-4DCC-BA6F-36737DE0F29A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=DOGSAPKA
DefaultDirName={autopf}\DOGSAPKA AI CLIPPER
DefaultGroupName=DOGSAPKA AI CLIPPER
OutputDir=..\dist\installer
OutputBaseFilename=DOGSAPKA_AI_CLIPPER_Setup_{#AppVersion}
SetupIconFile=dogsapka.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "hungarian"; MessagesFile: "compiler:Languages\Hungarian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\DOGSAPKA AI CLIPPER\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
; FFmpeg / FFprobe / Streamlink are placed in dist\...\tools by build_windows.ps1

[Icons]
Name: "{group}\DOGSAPKA AI CLIPPER"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall DOGSAPKA AI CLIPPER"; Filename: "{uninstallexe}"
Name: "{autodesktop}\DOGSAPKA AI CLIPPER"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,DOGSAPKA AI CLIPPER}"; Flags: nowait postinstall skipifsilent

; User data (%LOCALAPPDATA%\DOGSAPKA_AI_CLIPPER: clips, exports, settings) is intentionally NOT removed on uninstall.
