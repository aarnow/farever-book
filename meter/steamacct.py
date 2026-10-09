"""The Steam account signed in on this PC, read from Steam's own files (read
only): the registry's ActiveProcess\\ActiveUser (the account's 32-bit id,
0 when Steam is closed or signed out) and config/loginusers.vdf (each
account's 64-bit id and its persona name, the one Steam shows)."""
from __future__ import annotations

import re
from pathlib import Path

# a 64-bit Steam id is this base plus the account's 32-bit id
STEAMID64_BASE = 76561197960265728


def _reg(path, name):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except (ImportError, OSError):
        return None


def _personas(steam_dir):
    """{steamid64: persona name} from loginusers.vdf, {} when unreadable."""
    try:
        text = (Path(steam_dir) / "config" / "loginusers.vdf").read_text(
            encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return {}
    out = {}
    for sid, body in re.findall(r'"(\d{17})"\s*\{([^{}]*)\}', text):
        m = re.search(r'"PersonaName"\s*"((?:[^"\\]|\\.)*)"', body)
        if m:
            out[sid] = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
    return out


def active_account():
    """{"id": steamid64, "name": persona name or None} of the account Steam
    is signed in with now, or None (Steam closed, signed out, not found)."""
    uid = _reg(r"Software\Valve\Steam\ActiveProcess", "ActiveUser")
    if not isinstance(uid, int) or uid <= 0:
        return None
    sid = str(STEAMID64_BASE + uid)
    steam_dir = _reg(r"Software\Valve\Steam", "SteamPath")
    return {"id": sid, "name": _personas(steam_dir).get(sid)}
