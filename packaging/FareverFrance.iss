; Inno Setup script for Farever France.
;
; Compiled by packaging/build.ps1, which passes AppVersion in from the VERSION
; constant in meter/common.py so the two can't drift:
;
;   ISCC.exe /DAppVersion=2.1 packaging\FareverFrance.iss
;
; Installs per-user under %LOCALAPPDATA%\Programs — no UAC prompt, no admin
; rights, and nothing written outside the user's own profile. Farever doesn't
; need admin either, so asking for it would be the odd thing.

#ifndef AppVersion
  #define AppVersion "0.0"
#endif

#define AppName "Farever France"
#define AppExe "FareverFrance.exe"

[Setup]
; Stable across releases — it's how Windows knows an install is an upgrade of
; this program rather than a second copy of it. Never regenerate it.
AppId={{8B4B1F2E-9C6A-4E7D-93A5-2F1D6C0B7A34}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\FareverFrance
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
; Per-user: {autopf} resolves to %LOCALAPPDATA%\Programs under `lowest`, so the
; whole install happens without an elevation prompt.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=FareverFrance-{#AppVersion}-Setup
SetupIconFile=..\assets\farevermeter.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The meter must be stopped from its own tray icon so it can unload the Frida
; hook and detach from the game. Letting the Restart Manager close it would
; force-kill it instead, which is the exact thing that leaves a half-attached
; agent behind — so the check in [Code] handles it by asking, not by killing.
CloseApplications=no

[Languages]
; French: the app speaks only French, its installer too
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le &Bureau"; GroupDescription: "Raccourcis :"

[Files]
; The whole PyInstaller onedir output: the exe plus _internal, which carries
; Python, frida, Pillow, Tk and the meter's own data files. This is what means
; the user installs nothing else.
Source: "..\dist\FareverFrance\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Dossier du journal de Farever France"; Filename: "{localappdata}\FareverFrance"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Lancer {#AppName} maintenant"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The bundle only. %LOCALAPPDATA%\FareverFrance is deliberately left alone: it
; holds the user's parse screenshots and window positions, and an uninstall
; (including the one that precedes an upgrade) has no business deleting those.
Type: filesandordirs; Name: "{app}\_internal"

[Code]
{ The tray icon's hidden window. Its class name is registered by TrayIcon._run
  in meter/winsys.py, and is the most reliable way to tell a running meter
  from any other process that happens to share the executable name. }
const
  TrayClass = 'FareverFranceTray';

function AskToStopMeter(const Verb: String; Silent: Boolean): Boolean;
var
  i: Integer;
begin
  Result := True;
  if FindWindowByClassName(TrayClass) = 0 then
    Exit;
  { A silent run has nobody to answer the prompt below, and a suppressed MsgBox
    returns its default button — Retry — which would spin here forever. Refuse
    the run instead, so a scripted install fails visibly rather than hanging. }
  if Silent then
  begin
    Result := False;
    Exit;
  end;
  { Opened by the app's own update, which closes it right after: give it
    up to 10 s to be gone before asking anything. }
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
