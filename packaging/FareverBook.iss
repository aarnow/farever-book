; Inno Setup script for Farever Book.
;
; Compiled by packaging/build.ps1, which passes AppVersion from meter/common.py:
;
;   ISCC.exe /DAppVersion=2.1 packaging\FareverBook.iss
;
; Installs per-user under %LOCALAPPDATA%\Programs: no UAC prompt.

#ifndef AppVersion
  #define AppVersion "0.0"
#endif

#define AppName "Farever Book"
#define AppExe "FareverBook.exe"

[Setup]
; Never regenerate: it's how Windows recognises an upgrade.
AppId={{8B4B1F2E-9C6A-4E7D-93A5-2F1D6C0B7A34}
AppName={#AppName}
AppVersion={#AppVersion}
; {code:InstallDir}: the former name's folder (FareverFrance) moves to
; FareverBook beside it, any other previous folder is kept
DefaultDirName={code:InstallDir}
UsePreviousAppDir=no
DefaultGroupName={#AppName}
; the Start menu's "Farever France" folder of the former name is not reused
UsePreviousGroup=no
DisableProgramGroupPage=yes
DisableDirPage=auto
; under `lowest`, {autopf} is %LOCALAPPDATA%\Programs
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=FareverBook-{#AppVersion}-Setup
SetupIconFile=..\assets\fareverbook.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The app must be stopped from its tray icon to detach its hook cleanly: the
; Restart Manager would force-kill it. [Code] asks instead.
CloseApplications=no

[Languages]
; English first: the default when Windows is in neither language
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[CustomMessages]
english.DesktopIcon=Create a &desktop shortcut
french.DesktopIcon=Créer un raccourci sur le &Bureau
english.Shortcuts=Shortcuts:
french.Shortcuts=Raccourcis :
english.LogFolder=Farever Book log folder
french.LogFolder=Dossier du journal de Farever Book
english.LaunchNow=Launch Farever Book now
french.LaunchNow=Lancer Farever Book maintenant
english.StillOpen=Farever Book is still open.%n%nRight-click its icon in the notification area (near the clock, click the ^ arrow if you don't see it) and choose "Stop the meter".%n%nStopping it this way detaches it cleanly from Farever. Then click Retry to continue %1.
french.StillOpen=Farever Book est encore ouvert.%n%nFais un clic droit sur son icône dans la zone de notification (près de l'horloge, clique sur la flèche ^ si tu ne la vois pas) et choisis « Arrêter le compteur ».%n%nL'arrêter ainsi le détache proprement de Farever. Clique ensuite sur Réessayer pour continuer %1.
english.TheInstall=the installation
french.TheInstall=l'installation
english.TheUninstall=the uninstallation
french.TheUninstall=la désinstallation

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:Shortcuts}"

[Files]
; the whole PyInstaller onedir output (exe + _internal)
Source: "..\dist\FareverBook\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:LogFolder}"; Filename: "{localappdata}\FareverBook"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchNow}"; Flags: nowait postinstall skipifsilent
; the app's own update (/SILENT): started again by itself
Filename: "{app}\{#AppExe}"; Flags: nowait skipifnotsilent

[InstallDelete]
; what the app's former name (Farever France) left behind: its executable and
; shortcuts (an upgrade keeps the folder it was installed in)
Type: files; Name: "{app}\FareverFrance.exe"
Type: filesandordirs; Name: "{autoprograms}\Farever France"
Type: files; Name: "{autodesktop}\Farever France.lnk"

[UninstallDelete]
; The bundle only: %LOCALAPPDATA%\FareverBook (the user's data) is kept,
; an upgrade uninstalls too.
Type: filesandordirs; Name: "{app}\_internal"

[Code]
{ The tray icon's hidden window class (TrayIcon._run in meter/winsys.py):
  more reliable than the executable name. }
const
  TrayClass = 'FareverBookTray';
  { the former name's, while an old version may still be the one running }
  OldTrayClass = 'FareverFranceTray';

function MeterRunning(): Boolean;
begin
  Result := (FindWindowByClassName(TrayClass) <> 0)
            or (FindWindowByClassName(OldTrayClass) <> 0);
end;

function AskToStopMeter(const Verb: String; Silent: Boolean): Boolean;
var
  i: Integer;
begin
  Result := True;
  { The app's own update (silent) closes it right after: wait up to 10 s
    first. }
  for i := 1 to 40 do
  begin
    if not MeterRunning() then
      Exit;
    Sleep(250);
  end;
  { Silent: a suppressed MsgBox returns Retry and would loop forever. }
  if Silent then
  begin
    Result := False;
    Exit;
  end;
  while MeterRunning() do
  begin
    if MsgBox(FmtMessage(CustomMessage('StillOpen'), [Verb]),
              mbError, MB_RETRYCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8B4B1F2E-9C6A-4E7D-93A5-2F1D6C0B7A34}_is1';
  OldDirName = 'FareverFrance';

{ Where the previous version is installed, or ''. }
function PreviousDir(): String;
begin
  if not RegQueryStringValue(HKCU, UninstallKey, 'Inno Setup: App Path', Result) then
    Result := '';
  Result := RemoveBackslashUnlessRoot(Result);
end;

function IsOldDir(const Dir: String): Boolean;
begin
  Result := (Dir <> '') and (CompareText(ExtractFileName(Dir), OldDirName) = 0);
end;

function InstallDir(Param: String): String;
var
  Prev: String;
begin
  Prev := PreviousDir();
  if Prev = '' then
    Result := ExpandConstant('{autopf}\FareverBook')
  else if IsOldDir(Prev) then
    Result := AddBackslash(ExtractFileDir(Prev)) + 'FareverBook'
  else
    Result := Prev;
end;

var
  MovedFrom: String;

{ The former name's folder, once the new one is installed: only the app's
  files were there (the player's data lives in %LOCALAPPDATA%). }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    MovedFrom := PreviousDir();
  if (CurStep = ssPostInstall) and IsOldDir(MovedFrom)
     and (CompareText(MovedFrom, RemoveBackslashUnlessRoot(ExpandConstant('{app}'))) <> 0)
     and (FileExists(MovedFrom + '\FareverBook.exe') or FileExists(MovedFrom + '\FareverFrance.exe')) then
    DelTree(MovedFrom, True, True, True);
end;

function InitializeSetup(): Boolean;
begin
  Result := AskToStopMeter(CustomMessage('TheInstall'), WizardSilent);
end;

function InitializeUninstall(): Boolean;
begin
  Result := AskToStopMeter(CustomMessage('TheUninstall'), UninstallSilent);
end;
