"""The problem report (Aide › Un problème ?): what helps understand a
player's issue, in one text file put on the Desktop for them to send.

Private details are masked: the Windows user name in paths, and the players'
names the log mentions (the player's own and others'). Nothing is sent: the
player chooses to share the file."""
import ctypes
import os
import platform
import re
import subprocess
import tempfile
import time
import winreg
from pathlib import Path

from common import CREATE_NO_WINDOW, DATA_HOME, FROZEN, LOG_FILE, VERSION

LOG_LINES = 250          # the current run's last lines
PREV_LINES = 60          # the previous run's (the issue may be there)
LINE_MAX = 300           # a profile line lists a whole inventory: cut
PLAYER = "<joueur>"

# log lines naming a player: "profile X:", "item codex: X,", ...
_NAMED = re.compile(r"(profile |item codex: |achievements: |map progress: )"
                    r"([^\s:,]+)")
_HOLDER = re.compile(r"(off )(\S+)( to the status holder)")


def desktop_dir():
    """The Desktop, wherever the user moved it (OneDrive...)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion"
                            r"\Explorer\User Shell Folders") as k:
            d = Path(os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0]))
        if d.is_dir():
            return d
    except OSError:
        pass
    d = Path.home() / "Desktop"
    return d if d.is_dir() else DATA_HOME


def windows_version():
    """"Windows 11 Pro 24H2 (build 26100.4061)"."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            def val(name):
                try:
                    return winreg.QueryValueEx(k, name)[0]
                except OSError:
                    return ""
            name, disp = val("ProductName"), val("DisplayVersion")
            build, ubr = val("CurrentBuild"), val("UBR")
        # Windows 11 still calls itself "Windows 10" there
        if str(build).isdigit() and int(build) >= 22000:
            name = name.replace("Windows 10", "Windows 11")
        return f"{name} {disp} (build {build}.{ubr})".strip()
    except OSError:
        return platform.platform()


def antivirus():
    """The antivirus programs Windows knows of, and whether each is on."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance -Namespace root/SecurityCenter2 "
             "-ClassName AntiVirusProduct | ForEach-Object "
             "{ \"$($_.displayName)|$($_.productState)\" }"],
            capture_output=True, text=True, timeout=10,
            creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return "?"
    found = []
    for line in out.splitlines():
        name, _, state = line.strip().rpartition("|")
        if not name:
            continue
        on = state.isdigit() and (int(state) >> 12) & 0xF == 1
        found.append(f"{name} ({'actif' if on else 'inactif'})")
    return ", ".join(found) or "aucun déclaré"


def smart_app_control():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\CI\Policy") as k:
            v = winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")[0]
    except OSError:
        return "absent"
    return {0: "désactivé", 1: "ACTIVÉ", 2: "en évaluation"}.get(v, str(v))


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def mask(text, names=()):
    """The user's folder and the players' names out of `text`."""
    home = str(Path.home())
    hidden = str(Path(home).parent / "…")
    for h, rep in ((home, hidden),
                   (home.replace("\\", "/"), hidden.replace("\\", "/"))):
        text = re.sub(re.escape(h), lambda _m, r=rep: r, text, flags=re.I)
    user = os.environ.get("USERNAME") or ""
    if len(user) >= 3:
        text = re.sub(rf"\b{re.escape(user)}\b", "…", text, flags=re.I)
    text = _NAMED.sub(lambda m: m.group(1) + PLAYER, text)
    text = _HOLDER.sub(lambda m: m.group(1) + PLAYER + m.group(3), text)
    for n in sorted({n for n in names if n and len(n) >= 3}, key=len,
                    reverse=True):
        text = re.sub(rf"\b{re.escape(n)}\b", PLAYER, text)
    return text


def _tail(path, n):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln if len(ln) <= LINE_MAX else ln[:LINE_MAX] + " […]"
            for ln in lines[-n:]]


def build(ctx, names=()):
    """The report's text. `ctx`: what the app knows (lang, theme, game dir,
    the link's state, its steps, the last connection error)."""
    tmp = tempfile.gettempdir()
    out = [f"Farever Book {VERSION} — rapport du "
           f"{time.strftime('%Y-%m-%d %H:%M')}",
           "",
           "== Système ==",
           f"Windows : {windows_version()}",
           f"Application installée : {'oui' if FROZEN else 'non (sources)'}",
           f"Lancée en administrateur : {'oui' if is_admin() else 'non'}",
           f"Antivirus : {antivirus()}",
           f"Smart App Control : {smart_app_control()}",
           f"Dossier temporaire en caractères simples : "
           f"{'oui' if tmp.isascii() else 'NON (accents ou caractères spéciaux)'}",
           f"Langue : {ctx.get('lang')}, thème : {ctx.get('theme')}",
           "",
           "== Jeu ==",
           f"Dossier de Farever : {ctx.get('game_dir') or 'introuvable'}",
           f"Connexion : {ctx.get('state')}"
           + (f" — {ctx['detail']}" if ctx.get("detail") else "")]
    if ctx.get("error"):
        out.append(f"Dernière erreur : {ctx['error']}")
    for st in ctx.get("steps") or ():
        out.append(f"  [{st.get('s')}] {st.get('t')}"
                   + (f" — {st['d']}" if st.get("d") else ""))
    out += ["", f"== Journal ({LOG_FILE.name}, fin) =="]
    out += _tail(LOG_FILE, LOG_LINES)
    prev = _tail(LOG_FILE.with_suffix(".log.1"), PREV_LINES)
    if prev:
        out += ["", "== Journal du lancement précédent (fin) =="] + prev
    return mask("\n".join(out), names) + "\n"


def write(ctx, names=()):
    """The report on the Desktop; its path."""
    path = desktop_dir() / f"FareverBook-rapport-{time.strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(build(ctx, names), encoding="utf-8")
    return path


def show_in_folder(path):
    """The Explorer, the file selected."""
    subprocess.Popen(["explorer", "/select,", str(path)])
