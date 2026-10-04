"""Create the "Farever France" shortcut on the Desktop and in the Start menu.

It starts the app through pythonw.exe — the same Python, without a console
window (the app then logs to %LOCALAPPDATA%\\FareverFrance\\meter.log). Run by
"Installer Farever France.cmd"; safe to run again, it only rewrites the shortcuts.

The icon is copied to %LOCALAPPDATA%\\FareverFrance under a name made of its
content (farevermeter-<hash>.ico), and the shortcuts point there. Windows'
icon cache keys on the path: pointed at assets\\farevermeter.ico, a shortcut
keeps showing the old icon after the file changes. A new icon is a new path,
so it is always read. A pin on the taskbar (a copy of the shortcut) is
rewritten too."""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "FareverFrance"
PINNED = (Path(os.environ.get("APPDATA") or Path.home()) / "Microsoft"
          / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar"
          / "Farever France.lnk")


def _icon():
    """The project's icon, copied under a name its content decides."""
    src = ROOT / "assets" / "farevermeter.ico"
    tag = hashlib.sha1(src.read_bytes()).hexdigest()[:10]
    DATA_HOME.mkdir(parents=True, exist_ok=True)
    out = DATA_HOME / f"farevermeter-{tag}.ico"
    if not out.exists():
        shutil.copyfile(src, out)
    for old in DATA_HOME.glob("farevermeter-*.ico"):
        if old != out:
            try:
                old.unlink()
            except OSError:
                pass
    return out


def main():
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        sys.exit(f"pythonw.exe introuvable à côté de {sys.executable}")
    script = ROOT / "meter" / "farever_meter.py"
    icon = _icon()

    def ps(s):
        return "'" + str(s).replace("'", "''") + "'"

    links = ("@((Join-Path ([Environment]::GetFolderPath('Desktop')) "
             "'Farever France.lnk'), (Join-Path "
             "([Environment]::GetFolderPath('Programs')) 'Farever France.lnk')"
             + (f", {ps(PINNED)}" if PINNED.exists() else "") + ")")
    cmd = (
        "$s = New-Object -ComObject WScript.Shell; "
        f"foreach ($p in {links}) {{ "
        "$l = $s.CreateShortcut($p); "
        f"$l.TargetPath = {ps(pyw)}; "
        f"$l.Arguments = {ps(chr(34) + str(script) + chr(34))}; "
        f"$l.WorkingDirectory = {ps(ROOT)}; "
        f"$l.IconLocation = {ps(str(icon) + ',0')}; "
        "$l.Description = 'Farever France : compteur et suivi pour Farever'; "
        "$l.Save(); Write-Output $p }; "
        "ie4uinit.exe -show")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"La création des raccourcis a échoué :\n{r.stderr}")
    for line in r.stdout.splitlines():
        print(f"  {line}")


if __name__ == "__main__":
    main()
