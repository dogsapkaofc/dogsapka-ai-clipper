; Inno Setup script - compile with ISCC.exe packaging\installer.iss (after build_windows.ps1)
#define AppName "DOGSAPKA AI CLIPPER"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
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
; always let the user pick the drive/folder for the program (also on updates)
DisableDirPage=no
UsePreviousAppDir=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "hungarian"; MessagesFile: "compiler:Languages\Hungarian.isl"

[CustomMessages]
english.DataTitle=Data folder
english.DataSub=Where should clips, recordings and exports be stored?
english.DataText=Recordings and clips can use many gigabytes. Choose a drive with enough free space (for example D:\). A DOGSAPKA_AI_CLIPPER folder is used there. You can change this later in Settings.
english.DataLabel=Data folder:
hungarian.DataTitle=Adatmappa
hungarian.DataSub=Hová kerüljenek a klipek, felvételek és exportok?
hungarian.DataText=A felvételek és klipek sok gigabájtot foglalhatnak. Válassz olyan meghajtót, ahol van elég hely (például D:\). Ott egy DOGSAPKA_AI_CLIPPER mappát használ a program. Később a Beállításokban módosítható.
hungarian.DataLabel=Adatmappa:

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
; automatic update (silent install started by the app): start the new version again
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: WizardSilent

; User data (%LOCALAPPDATA%\DOGSAPKA_AI_CLIPPER: clips, exports, settings) is intentionally NOT removed on uninstall.


[UninstallDelete]
Type: files; Name: "{app}\data_location.txt"

[Dirs]
; the data folder must stay writable for the normal user even if setup ran as administrator
Name: "{code:GetDataDir}"; Permissions: users-modify; Check: DataPageUsed

[Code]
var
  DataPage: TInputDirWizardPage;

function UserPointerFile(): String;
begin
  Result := ExpandConstant('{userappdata}\DOGSAPKA_AI_CLIPPER\data_location.json');
end;

function DataPageUsed(): Boolean;
begin
  { once the folder was changed inside the app, the app setting wins - don't ask again }
  Result := not FileExists(UserPointerFile());
end;

function NormalizeDataDir(Dir: String): String;
begin
  Dir := RemoveBackslashUnlessRoot(Dir);
  if CompareText(ExtractFileName(Dir), 'DOGSAPKA_AI_CLIPPER') <> 0 then
    Dir := AddBackslash(Dir) + 'DOGSAPKA_AI_CLIPPER';
  Result := Dir;
end;

function GetDataDir(Param: String): String;
begin
  Result := NormalizeDataDir(DataPage.Values[0]);
end;

procedure InitializeWizard();
begin
  DataPage := CreateInputDirPage(wpSelectDir, CustomMessage('DataTitle'), CustomMessage('DataSub'),
    CustomMessage('DataText'), False, 'DOGSAPKA_AI_CLIPPER');
  DataPage.Add(CustomMessage('DataLabel'));
  DataPage.Values[0] := GetPreviousData('DataDir', ExpandConstant('{localappdata}\DOGSAPKA_AI_CLIPPER'));
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = DataPage.ID) and not DataPageUsed();
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'DataDir', DataPage.Values[0]);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
begin
  if (CurStep = ssPostInstall) and DataPageUsed() then
  begin
    SetArrayLength(Lines, 1);
    Lines[0] := GetDataDir('');
    SaveStringsToUTF8File(ExpandConstant('{app}\data_location.txt'), Lines, False);
  end;
end;
