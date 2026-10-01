"""Create the "Farever France" shortcut on the Desktop and in the Start menu.

It starts the app through pythonw.exe — the same Python, without a console
window (the app then logs to %LOCALAPPDATA%\\FareverMeter\\meter.log). Run by
"Installer Farever France.cmd"; safe to run again, it only rewrites the shortcuts."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        sys.exit(f"pythonw.exe introuvable à côté de {sys.executable}")
    script = ROOT / "meter" / "farever_meter.py"
    icon = ROOT / "assets" / "farevermeter.ico"

    def ps(s):
        return "'" + str(s).replace("'", "''") + "'"

    cmd = (
        "$s = New-Object -ComObject WScript.Shell; "
        "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), "
        "[Environment]::GetFolderPath('Programs'))) { "
        # the shortcut's name before the app was renamed (Farever+)
        "Remove-Item -LiteralPath (Join-Path $dir 'Farever+.lnk') "
        "-ErrorAction SilentlyContinue; "
        "$l = $s.CreateShortcut((Join-Path $dir 'Farever France.lnk')); "
        f"$l.TargetPath = {ps(pyw)}; "
        f"$l.Arguments = {ps(chr(34) + str(script) + chr(34))}; "
        f"$l.WorkingDirectory = {ps(ROOT)}; "
        f"$l.IconLocation = {ps(icon)}; "
        "$l.Description = 'Farever France : compteur et suivi pour Farever'; "
        "$l.Save(); Write-Output (Join-Path $dir 'Farever France.lnk') }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"La création des raccourcis a échoué :\n{r.stderr}")
    for line in r.stdout.splitlines():
        print(f"  {line}")


if __name__ == "__main__":
    main()
