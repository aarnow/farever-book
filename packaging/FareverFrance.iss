; Inno Setup script for Farever France.
;
; Compiled by packaging/build.ps1, which passes AppVersion from meter/common.py:
;
;   ISCC.exe /DAppVersion=2.1 packaging\FareverFrance.iss
;
; Installs per-user under %LOCALAPPDATA%\Programs: no UAC prompt.

#ifndef AppVersion
  #define AppVersion "0.0"
#endif

#define AppName "Farever France"
#define AppExe "FareverFrance.exe"

[Setup]
; Never regenerate: it's how Windows recognises an upgrade.
AppId={{8B4B1F2E-9C6A-4E7D-93A5-2F1D6C0B7A34}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\FareverFrance
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
; under `lowest`, {autopf} is %LOCALAPPDATA%\Programs
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=FareverFrance-{#AppVersion}-Setup
SetupIconFile=..\assets\fareverfrance.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The app must be stopped from its tray icon to detach its hook cleanly: the
; Restart Manager would force-kill it. [Code] asks instead.
CloseApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le &Bureau"; GroupDescription: "Raccourcis :"

[Files]
; the whole PyInstaller onedir output (exe + _internal)
Source: "..\dist\FareverFrance\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Dossier du journal de Farever France"; Filename: "{localappdata}\FareverFrance"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Lancer {#AppName} maintenant"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The bundle only: %LOCALAPPDATA%\FareverFrance (the user's data) is kept,
; an upgrade uninstalls too.
Type: filesandordirs; Name: "{app}\_internal"

[Code]
{ The tray icon's hidden window class (TrayIcon._run in meter/winsys.py):
  more reliable than the executable name. }
const
  TrayClass = 'FareverFranceTray';

function AskToStopMeter(const Verb: String; Silent: Boolean): Boolean;
var
  i: Integer;
begin
  Result := True;
  if FindWindowByClassName(TrayClass) = 0 then
    Exit;
  { Silent: a suppressed MsgBox returns Retry and would loop forever. }
  if Silent then
  begin
    Result := False;
    Exit;
  end;
  { The app's own update closes it right after: wait up to 10 s first. }
  for i := 1 to 40 do
  begin
    if FindWindowByClassName(TrayClass) = 0 then
      Exit;
    Sleep(250);
  end;
  while FindWindowByClassName(TrayClass) <> 0 do
  begin
    if MsgBox('Farever France est encore ouvert.' + #13#10#13#10 +
              'Fais un clic droit sur son icône dans la zone de notification ' +
              '(près de l''horloge, clique sur la flèche ^ si tu ne la vois ' +
              'pas) et choisis « Arrêter le compteur ».' + #13#10#13#10 +
              'L''arrêter ainsi le détache proprement de Farever. Clique ' +
              'ensuite sur Réessayer pour continuer ' + Verb + '.',
              mbError, MB_RETRYCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := AskToStopMeter('l''installation', WizardSilent);
end;

function InitializeUninstall(): Boolean;
begin
  Result := AskToStopMeter('la désinstallation', UninstallSilent);
end;
