"""Windows plumbing: DPI, monitors, processes, the reset hotkey, the tray icon,
the clipboard and the single-instance lock."""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from common import (
    CLAIM_MUTEX, CLAIM_WAIT_MS, DATA_HOME, LOCK_DIR, LOCK_FILE,
    METER_IMAGE_NAMES, QUIT_WAIT_SECS, RIFTS_DIR, ROOT, STOP, _APP,
    request_stop)


# ---------------------------------------------------------------------------
# Display scaling
# ---------------------------------------------------------------------------
# Windows scales an application that never says otherwise. This one never did:
# there is no dpiAware entry in the shipped manifest and no awareness call
# anywhere, so at a 300% system scale the desktop composer bitmap-stretched
# every window to three times its size and blurred it on the way.
#
# Declaring per-monitor-v2 turns the stretching off. It also puts every
# coordinate this process handles into one space — ours AND the game's, since
# _window_rect_of_pid asks Windows for the game's rect and an unaware process
# is handed a virtualised answer.
DPI_PER_MONITOR_V2 = -4


# Windows' own reference DPI. A scale factor is whatever the monitor reports
# divided by this.
USER_DEFAULT_SCREEN_DPI = 96


def declare_dpi_awareness():
    """Opt out of Windows' bitmap stretching. Returns what was achieved.

    Must run before this process owns its first window — the tray icon's
    or a message box's — because awareness is latched at that moment and cannot
    be changed afterwards. Each fallback is a older-Windows entry point for the
    same idea, tried newest first.
    """
    if sys.platform != "win32":
        return "not windows"
    u = ctypes.windll.user32
    try:                                  # Windows 10 1703 and later
        u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if u.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(DPI_PER_MONITOR_V2)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:                                  # Windows 8.1 .. 10 1607
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:                                  # Vista .. 8.0
        return "system" if u.SetProcessDPIAware() else "unaware"
    except (AttributeError, OSError):
        return "unaware"


def display_scale():
    """The primary monitor's scale factor: 1.0 at 100%, 3.0 at 300%.

    Only meaningful once awareness is declared — an unaware process is told 96
    whatever the user chose, which is the whole point of being unaware. Used to
    migrate window positions saved by a build that had not declared it.
    """
    if sys.platform != "win32":
        return 1.0
    try:
        dc = ctypes.windll.user32.GetDC(0)
        try:
            # LOGPIXELSX = 88
            dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)
        finally:
            ctypes.windll.user32.ReleaseDC(0, dc)
        return (dpi / USER_DEFAULT_SCREEN_DPI) if dpi else 1.0
    except Exception:
        return 1.0


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _monitor_work_areas():
    """Every monitor's work area (the screen minus the taskbar), as
    (left, top, right, bottom) in physical pixels. Empty off Windows."""
    if sys.platform != "win32":
        return []
    out = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def visit(hmon, _hdc, _rect, _data):
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            out.append((r.left, r.top, r.right, r.bottom))
        return True

    try:
        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(visit), 0)
    except Exception:
        return []
    return out


def _monitor_containing(x, y):
    """The work area of the monitor holding (x, y), or None."""
    for r in _monitor_work_areas():
        if r[0] <= x < r[2] and r[1] <= y < r[3]:
            return r
    return None


def _window_rect_of_pid(pid):
    """(left, top, right, bottom) of a process's largest visible top-level
    window, or None: the game has a window once it has booted."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                           ctypes.POINTER(wintypes.DWORD)]
    best = {"area": 0, "rect": None}
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                     wintypes.LPARAM)

    def visit(hwnd, _lparam):
        wpid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value != pid or not u.IsWindowVisible(hwnd):
            return True
        r = wintypes.RECT()
        if u.GetWindowRect(hwnd, ctypes.byref(r)):
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > best["area"]:
                best["area"] = area
                best["rect"] = (r.left, r.top, r.right, r.bottom)
        return True

    try:
        u.EnumWindows(WNDENUMPROC(visit), 0)
    except Exception:
        return None
    return best["rect"] if best["area"] > 0 else None


def _process_age(pid):
    """Seconds since the process started, or None if Windows won't say."""
    if sys.platform != "win32":
        return None
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wintypes.HANDLE
    h = k.OpenProcess(0x1000, False, pid)      # QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        made, x1, x2, x3 = (wintypes.FILETIME() for _ in range(4))
        if not k.GetProcessTimes(h, ctypes.byref(made), ctypes.byref(x1),
                                 ctypes.byref(x2), ctypes.byref(x3)):
            return None
        now = wintypes.FILETIME()
        k.GetSystemTimeAsFileTime(ctypes.byref(now))
        ft = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime
        return (ft(now) - ft(made)) / 1e7
    finally:
        k.CloseHandle(h)


VK_OEM_5 = 0xDC                      # the \ key


VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12


WH_KEYBOARD_LL, WH_MOUSE_LL, HC_ACTION = 13, 14, 0


WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105


WM_MBUTTONDOWN, WM_XBUTTONDOWN = 0x0207, 0x020B


WM_HOTKEY = 0x0312


WM_REBIND = 0x0400 + 1               # WM_APP+1, posted to the hotkey thread


MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000


# Thread id of the RegisterHotKey fallback's pump, or 0 while the low-level
# hook is doing the work (which needs no re-registration at all).
REBIND_TO = [0]


# The one key the meter owns: Shift+\ by default, reset the encounter.
HK_RESET = 1


# The reset keybind, rebindable in Réglages. A dict rather than a
# constant because the hook thread reads it on every keypress: rebinding is
# then a matter of writing new values here, with no hook to tear down and
# reinstall. Mutated in place for the same reason — the thread closed over this
# object, not over the name.
RESET_BIND = {"vk": VK_OEM_5, "shift": True, "ctrl": False, "alt": False}


# Virtual-key codes whose names aren't derivable. Everything else falls back to
# its character (A-Z, 0-9 are their own VK) or a bare hex code, so an unusual
# keyboard shows something rather than nothing.
VK_NAMES = {
    0x08: "Retour arrière", 0x09: "Tab", 0x0D: "Entrée", 0x13: "Pause",
    0x14: "Verr. Maj", 0x1B: "Échap", 0x20: "Espace", 0x21: "Page préc.",
    0x22: "Page suiv.", 0x23: "Fin", 0x24: "Début", 0x25: "Gauche",
    0x26: "Haut", 0x27: "Droite", 0x28: "Bas", 0x2D: "Inser", 0x2E: "Suppr",
    0x6A: "Pavé *", 0x6B: "Pavé +", 0x6D: "Pavé -", 0x6E: "Pavé .",
    0x6F: "Pavé /",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}


VK_NAMES.update({0x60 + i: f"Pavé {i}" for i in range(10)})


VK_NAMES.update({0x70 + i: f"F{i + 1}" for i in range(24)})


# Mouse buttons are bindable too, but only these three. Left and right belong
# to the game and always will; the hook SWALLOWS whatever it fires on, and
# taking left-click away from somebody mid-fight is not a setting, it's a
# hostage situation. Middle and the two side buttons are fair game.
VK_MOUSE = {0x04: "Clic milieu", 0x05: "Souris 4", 0x06: "Souris 5"}


VK_NAMES.update(VK_MOUSE)


# Modifiers can't be the key itself, and Escape is how you back out of the
# capture — binding it would leave no way to cancel.
VK_UNBINDABLE = frozenset({0x10, 0x11, 0x12, 0x1B, 0x5B, 0x5C,
                           0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5})


def _vk_name(vk):
    if vk in VK_NAMES:
        return VK_NAMES[vk]
    if 0x30 <= vk <= 0x5A:          # 0-9 and A-Z share their ASCII codes
        return chr(vk)
    return f"VK {vk:#04x}"


def bind_label(bind=None):
    """"Shift + \\" — what the menu button and the floating hint both show, so
    they can't drift apart."""
    b = bind or RESET_BIND
    parts = [n for n, k in (("Ctrl", "ctrl"), ("Alt", "alt"), ("Maj", "shift"))
             if b.get(k)]
    parts.append(_vk_name(b.get("vk", VK_OEM_5)))
    return " + ".join(parts)


class _KBD(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


class _MSLL(ctypes.Structure):
    _fields_ = [("pt_x", wintypes.LONG), ("pt_y", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _ResetHotkey:
    """The reset key, on its own thread with its own message pump: a
    low-level keyboard hook (and a mouse one, for the side buttons) that
    fires and swallows the bound key only while Farever has the focus, every
    other key left to the game. Without the hook, a global RegisterHotKey
    (whatever has the focus). `game_pid` is a callable: the game can start,
    close and start again while the meter runs."""

    def __init__(self, callbacks, game_pid):
        self.callbacks = callbacks
        self.game_pid = game_pid
        self.keys_down = set()          # a held key fires once
        u = self.u = ctypes.windll.user32
        self.HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                           wintypes.WPARAM, wintypes.LPARAM)
        u.SetWindowsHookExW.restype = ctypes.c_void_p
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, self.HOOKPROC,
                                        ctypes.c_void_p, wintypes.DWORD]
        u.CallNextHookEx.restype = ctypes.c_ssize_t
        u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                     wintypes.WPARAM, wintypes.LPARAM]
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetAsyncKeyState.restype = ctypes.c_short

    # ---- the binding ----------------------------------------------------
    def _pressed(self, vk):
        return bool(self.u.GetAsyncKeyState(vk) & 0x8000)

    def _fg_pid(self):
        h = self.u.GetForegroundWindow()
        if not h:
            return 0
        pid = wintypes.DWORD()
        self.u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        return pid.value

    def _matches(self, vk):
        """The bound key, Farever in front, and every modifier exactly as
        bound (Shift+\\ must not fire on Ctrl+Shift+\\). RESET_BIND is read
        each time: a rebind takes effect at once."""
        b = RESET_BIND
        return (vk == b.get("vk") and self._fg_pid() == self.game_pid()
                and self._pressed(VK_SHIFT) == bool(b.get("shift"))
                and self._pressed(VK_CONTROL) == bool(b.get("ctrl"))
                and self._pressed(VK_MENU) == bool(b.get("alt")))

    def _fire(self, key=HK_RESET):
        cb = self.callbacks.get(key)
        if cb:
            try:
                cb()
            except Exception as e:
                print("[hotkey]", e, file=sys.stderr)

    # ---- the low-level hooks ----------------------------------------------
    def _on_key(self, code, wparam, lparam):
        if code == HC_ACTION:
            vk = ctypes.cast(lparam, ctypes.POINTER(_KBD))[0].vkCode
            if wparam in (WM_KEYUP, WM_SYSKEYUP):
                self.keys_down.discard(vk)
            elif wparam in (WM_KEYDOWN, WM_SYSKEYDOWN) \
                    and vk not in self.keys_down:
                self.keys_down.add(vk)
                if self._matches(vk):
                    self._fire()
                    return 1                # swallowed
        return self.u.CallNextHookEx(None, code, wparam, lparam)

    def _on_mouse(self, code, wparam, lparam):
        # every mouse move comes through here (a thousand a second on a
        # 1000 Hz mouse): anything that isn't a button press leaves first
        if code == HC_ACTION and wparam in (WM_MBUTTONDOWN, WM_XBUTTONDOWN):
            vk = 0x04                       # middle button
            if wparam == WM_XBUTTONDOWN:    # side button 1 or 2: 0x05, 0x06
                ms = ctypes.cast(lparam, ctypes.POINTER(_MSLL))[0]
                vk += (ms.mouseData >> 16) & 0xFFFF
            if self._matches(vk):
                self._fire()
                return 1
        return self.u.CallNextHookEx(None, code, wparam, lparam)

    def _pump(self):
        msg = wintypes.MSG()
        while self.u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self.u.TranslateMessage(ctypes.byref(msg))
            self.u.DispatchMessageW(ctypes.byref(msg))

    def run(self):
        u = self.u
        # kept on the instance: a callback collected while hooked crashes
        self._procs = (self.HOOKPROC(self._on_key),
                       self.HOOKPROC(self._on_mouse))
        module = ctypes.windll.kernel32.GetModuleHandleW(None)
        if u.SetWindowsHookExW(WH_KEYBOARD_LL, self._procs[0], module, 0):
            # always installed: a mouse button can be bound at any moment,
            # and a hook can only be added from this thread
            if not u.SetWindowsHookExW(WH_MOUSE_LL, self._procs[1], module, 0):
                print("[meter] mouse hook failed; mouse buttons can't be "
                      "bound.", file=sys.stderr)
            print("[meter] focus-conditional hotkeys active.", file=sys.stderr)
            self._pump()
        else:
            self._run_global()

    # ---- without the hook ---------------------------------------------------
    def _register(self):
        u = self.u
        u.UnregisterHotKey(None, HK_RESET)
        mods = MOD_NOREPEAT
        for key, mod in (("shift", MOD_SHIFT), ("ctrl", MOD_CONTROL),
                         ("alt", MOD_ALT)):
            if RESET_BIND.get(key):
                mods |= mod
        if not u.RegisterHotKey(None, HK_RESET, mods,
                                RESET_BIND.get("vk", VK_OEM_5)):
            print(f"[meter] {bind_label()} unavailable (another app owns "
                  "it): the encounter still resets on a zone change or after "
                  "a lull, but the manual reset won't fire.", file=sys.stderr)

    def _run_global(self):
        print("[meter] LL hook failed; using global RegisterHotKey fallback.",
              file=sys.stderr)
        self._register()
        # a hotkey belongs to the thread that registered it: a rebind is
        # posted here (WM_REBIND) for this thread to redo it
        REBIND_TO[0] = ctypes.windll.kernel32.GetCurrentThreadId()
        u, msg = self.u, wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_REBIND:
                self._register()
                continue
            if msg.message == WM_HOTKEY:
                self._fire(msg.wParam)
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))


def start_hotkeys(callbacks: dict, target_pid):
    """The reset key's thread (see _ResetHotkey). `target_pid`: the game's
    pid, or a callable giving it."""
    if sys.platform != "win32":
        return
    game_pid = target_pid if callable(target_pid) else (lambda: target_pid)
    hotkey = _ResetHotkey(callbacks, game_pid)
    threading.Thread(target=hotkey.run, daemon=True, name="hotkeys").start()


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------
# Closing the window quits, but the tray icon is also there before the
# window opens and when it is hidden: the clean exit is always reachable,
# never the Task Manager's force-kill that leaves a half-attached agent in
# the game.
#
# Hand-rolled on ctypes rather than pystray: the file already talks to user32
# directly for click-through, hotkeys and window enumeration, and a tray icon is
# one window and one message pump. It also keeps `pip install frida` as the only
# thing a from-source run needs.
ICON_FILE = ROOT / "assets" / "farevermeter.ico"


WM_TRAY = 0x0400 + 1                      # WM_APP + 1


NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2


NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10




WM_DESTROY, WM_CLOSE, WM_COMMAND = 0x0002, 0x0010, 0x0111


WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205


MF_STRING, MF_SEPARATOR = 0x0000, 0x0800


TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100


IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010


SM_CXSMICON, SM_CYSMICON = 49, 50


TRAY_QUIT, TRAY_LOG, TRAY_PARSES = 1001, 1002, 1003


TRAY_SETTINGS = 1004


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    """The full (Vista+) layout. cbSize is set to sizeof(), which is what tells
    the shell which version it's being handed."""
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
                ("hBalloonIcon", wintypes.HICON)]


class TrayIcon:
    """A notification-area icon whose menu holds the clean shutdown.

    Owns a hidden window on its own thread: tray callbacks are window messages,
    and they're delivered to the thread that created the window, so it needs a
    pump of its own."""

    def __init__(self, on_quit, tip="Farever France"):
        self.on_quit = on_quit
        self.tip = tip
        self.hwnd = None
        self._ready = threading.Event()
        self._thread = None

    # ---- lifecycle ----
    def start(self):
        if sys.platform != "win32":
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="tray")
        self._thread.start()
        # Bounded: a tray that fails to come up must not stop the meter from
        # starting — the in-game menu's Quit button is the other way out.
        self._ready.wait(timeout=5.0)

    def stop(self):
        """Called from the app's loop on the way out. PostMessage rather than
        a direct call because the window belongs to the tray thread."""
        if self.hwnd:
            try:
                ctypes.windll.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass

    # ---- internals ----
    def _load_icon(self):
        u = ctypes.windll.user32
        if ICON_FILE.is_file():
            h = u.LoadImageW(None, str(ICON_FILE), IMAGE_ICON,
                             u.GetSystemMetrics(SM_CXSMICON),
                             u.GetSystemMetrics(SM_CYSMICON), LR_LOADFROMFILE)
            if h:
                return h
            print(f"[tray] couldn't load {ICON_FILE} — using the stock icon.",
                  file=sys.stderr)
        return u.LoadIconW(None, ctypes.c_wchar_p(32512))   # IDI_APPLICATION

    def _notify(self, action, data):
        return bool(ctypes.windll.shell32.Shell_NotifyIconW(action,
                                                            ctypes.byref(data)))

    def _base_data(self):
        d = NOTIFYICONDATAW()
        d.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        d.hWnd = self.hwnd
        d.uID = 1
        return d

    def _add(self):
        d = self._base_data()
        d.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        d.uCallbackMessage = WM_TRAY
        d.hIcon = self.hicon
        d.szTip = self.tip
        self._notify(NIM_ADD, d)

    def _menu(self):
        u = ctypes.windll.user32
        m = u.CreatePopupMenu()
        u.AppendMenuW(m, MF_STRING, TRAY_SETTINGS, "Afficher Farever France")
        u.AppendMenuW(m, MF_STRING, TRAY_PARSES, "Ouvrir le dossier des failles")
        u.AppendMenuW(m, MF_STRING, TRAY_LOG, "Ouvrir le dossier du journal")
        u.AppendMenuW(m, MF_SEPARATOR, 0, None)
        u.AppendMenuW(m, MF_STRING, TRAY_QUIT, "Arrêter le compteur")
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        # Required by TrackPopupMenu, or the menu refuses to close when the user
        # clicks away from it.
        u.SetForegroundWindow(self.hwnd)
        cmd = u.TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                               pt.x, pt.y, 0, self.hwnd, None)
        u.PostMessageW(self.hwnd, 0x0000, 0, 0)     # WM_NULL, same reason
        u.DestroyMenu(m)
        return cmd

    def _on_command(self, cmd):
        if cmd == TRAY_QUIT:
            self.on_quit()
        elif cmd == TRAY_SETTINGS:
            # the tray's own thread: queued onto the app's loop
            app = _APP["ref"]
            if app is not None:
                app._enqueue(app.open_settings_from_tray)()
        elif cmd == TRAY_LOG:
            try:
                DATA_HOME.mkdir(parents=True, exist_ok=True)
                os.startfile(DATA_HOME)
            except Exception as e:
                print(f"[tray] couldn't open {DATA_HOME}: {e}", file=sys.stderr)
        elif cmd == TRAY_PARSES:
            try:
                RIFTS_DIR.mkdir(parents=True, exist_ok=True)
                os.startfile(RIFTS_DIR)
            except Exception as e:
                print(f"[tray] couldn't open {RIFTS_DIR}: {e}", file=sys.stderr)

    @staticmethod
    def _prototypes():
        """Declare every call this class makes.

        Not optional on 64-bit: ctypes defaults an unprototyped argument to C
        int, so any handle or pointer — a module handle, an lParam carrying a
        struct — overflows on the way through. Declared here in one place
        rather than at each call site, because the failure mode is a call that
        looks correct and raises at runtime on some machines and not others."""
        u, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        k32.GetModuleHandleW.restype = ctypes.c_void_p
        k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM]
        u.RegisterClassW.restype = wintypes.ATOM
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
        u.LoadImageW.restype = ctypes.c_void_p
        u.LoadImageW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                 wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                 wintypes.UINT]
        u.LoadIconW.restype = ctypes.c_void_p
        u.LoadIconW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        u.CreatePopupMenu.restype = ctypes.c_void_p
        u.AppendMenuW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                  ctypes.c_size_t, wintypes.LPCWSTR]
        u.TrackPopupMenu.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     wintypes.HWND, ctypes.c_void_p]
        u.DestroyMenu.argtypes = [ctypes.c_void_p]
        shell = ctypes.windll.shell32
        shell.Shell_NotifyIconW.restype = wintypes.BOOL
        shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                            ctypes.POINTER(NOTIFYICONDATAW)]

    def _run(self):
        u = ctypes.windll.user32
        self._prototypes()
        # Explorer drops every tray icon when it restarts and broadcasts this to
        # ask for them back. Without it an explorer crash silently costs the
        # user their only way to stop the meter.
        taskbar_created = u.RegisterWindowMessageW("TaskbarCreated")

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAY:
                if lparam in (WM_RBUTTONUP, WM_LBUTTONUP):
                    self._on_command(self._menu())
                return 0
            if msg == WM_COMMAND:
                # The popup is read with TPM_RETURNCMD, so a click comes back
                # from _menu() rather than through here. This is the standard
                # route into the same commands for anything that drives the icon
                # by message — and it's how the shutdown path gets tested
                # without a human clicking the menu.
                self._on_command(wparam & 0xFFFF)
                return 0
            if msg == taskbar_created:
                self._add()
                return 0
            if msg == WM_CLOSE:
                d = self._base_data()
                self._notify(NIM_DELETE, d)
                u.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                u.PostQuitMessage(0)
                return 0
            return u.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)      # kept alive: Windows holds a raw
        cls = WNDCLASSW()                     # pointer to it for the window's life
        cls.lpfnWndProc = self._wndproc
        cls.lpszClassName = "FareverFranceTray"
        cls.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        try:
            if not u.RegisterClassW(ctypes.byref(cls)):
                raise OSError(ctypes.get_last_error())
            u.CreateWindowExW.restype = wintypes.HWND
            self.hwnd = u.CreateWindowExW(0, "FareverFranceTray", "Farever France tray",
                                          0, 0, 0, 0, 0, None, None,
                                          cls.hInstance, None)
            if not self.hwnd:
                raise OSError("CreateWindowExW failed")
            self.hicon = self._load_icon()
            self._add()
        except Exception as e:
            print(f"[tray] icon unavailable ({e}): close the window to stop "
                  "the meter.", file=sys.stderr)
            self._ready.set()
            return
        print("[meter] tray icon active.", file=sys.stderr)
        self._ready.set()
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))


def copy_text_to_clipboard(text):
    """Put text on the Windows clipboard. True on success."""
    if sys.platform != "win32":
        return False
    data = (str(text) + "\0").encode("utf-16-le")
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = (ctypes.c_void_p,)
    k32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
    k32.GlobalFree.argtypes = (ctypes.c_void_p,)
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.SetClipboardData.argtypes = (wintypes.UINT, ctypes.c_void_p)
    h = k32.GlobalAlloc(0x0002, len(data))          # GMEM_MOVEABLE
    if not h:
        return False
    ctypes.memmove(k32.GlobalLock(h), data, len(data))
    k32.GlobalUnlock(h)
    for _ in range(5):
        if u32.OpenClipboard(0):
            break
        time.sleep(0.05)
    else:
        k32.GlobalFree(h)
        return False
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(13, ctypes.c_void_p(h)):   # CF_UNICODETEXT
            k32.GlobalFree(h)
            return False
        return True
    finally:
        u32.CloseClipboard()


def copy_image_to_clipboard(img):
    """Put a PIL image on the Windows clipboard as CF_DIB — the format every
    paste target understands. A BMP file is a 14-byte header ahead of a DIB,
    so the conversion is a save and a slice, no encoder gymnastics."""
    import io
    if sys.platform != "win32":
        raise OSError("image clipboard is Windows-only")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    dib = buf.getvalue()[14:]

    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = (ctypes.c_void_p,)
    k32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
    k32.GlobalFree.argtypes = (ctypes.c_void_p,)
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.SetClipboardData.argtypes = (wintypes.UINT, ctypes.c_void_p)

    GMEM_MOVEABLE = 0x0002
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
    if not h:
        raise OSError("GlobalAlloc failed")
    p = k32.GlobalLock(h)
    ctypes.memmove(p, dib, len(dib))
    k32.GlobalUnlock(h)
    # The clipboard is one shared lock; whoever synced it last (clipboard
    # managers love to) can hold it for a beat. Brief retries beat failing.
    for attempt in range(5):
        if u32.OpenClipboard(0):
            break
        time.sleep(0.05)
    else:
        k32.GlobalFree(h)
        raise OSError("clipboard is held by another window")
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(8, ctypes.c_void_p(h)):    # CF_DIB
            k32.GlobalFree(h)
            raise OSError("SetClipboardData failed")
        # Ownership of `h` passed to the system on success — no free here.
    finally:
        u32.CloseClipboard()


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


PROCESS_TERMINATE = 0x0001


STILL_ACTIVE = 259


def _open_process(pid, access):
    if sys.platform != "win32" or pid <= 0:
        return None
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    return k32.OpenProcess(access, 0, pid) or None


def _close_handle(h):
    k32 = ctypes.windll.kernel32
    k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    k32.CloseHandle(h)


def _process_alive(pid):
    """True while `pid` is still running. Note this can't be os.kill(pid, 0):
    on Windows os.kill TERMINATES the target instead of probing it."""
    h = _open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not h:
        return False
    try:
        k32 = ctypes.windll.kernel32
        k32.GetExitCodeProcess.argtypes = (ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_ulong))
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        return bool(ok) and code.value == STILL_ACTIVE
    finally:
        _close_handle(h)


def _process_image(pid):
    """Full path of a pid's executable, or "". Guards the force-kill path: a
    stale lock file can name a pid Windows has since recycled onto something
    else entirely, and that must not be what gets terminated."""
    h = _open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not h:
        return ""
    try:
        k32 = ctypes.windll.kernel32
        k32.QueryFullProcessImageNameW.argtypes = (
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong))
        size = ctypes.c_ulong(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return buf.value if ok else ""
    finally:
        _close_handle(h)


def _quit_flag(pid):
    return LOCK_DIR / f"quit-{pid}"


def quit_requested():
    """Has a newly-started instance asked us to stand down? Polled by the
    app's loop, so the answer is acted on within one refresh tick."""
    try:
        return _quit_flag(os.getpid()).exists()
    except OSError:
        return False


def watch_for_quit_request():
    """Poll the stand-down flag on a background thread, for the stretches where
    nothing else is polling it.

    The app checks the flag on its refresh tick, but not before it exists:
    without this, an instance still starting ignores the request and is
    force-killed twelve seconds later, the outcome the polite handover
    exists to avoid."""
    def work():
        while not STOP.is_set():
            if quit_requested():
                print("[meter] a newer instance asked us to exit — standing "
                      "down.", file=sys.stderr)
                request_stop()
                return
            time.sleep(0.25)

    threading.Thread(target=work, daemon=True, name="quit-watch").start()


def _stop_instance(pid):
    """Ask pid to exit, and wait. The request is a file the running overlay
    polls — it returns from its mainloop and takes main()'s normal shutdown
    path, unloading the hook and detaching. Force-killing is the fallback only,
    because that's what leaves a half-attached agent in the game."""
    flag = _quit_flag(pid)
    try:
        flag.write_text("quit")
    except OSError:
        return
    deadline = time.monotonic() + QUIT_WAIT_SECS
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            print(f"[meter] pid {pid} shut down cleanly.", file=sys.stderr)
            break
        time.sleep(0.25)
    else:
        print(f"[meter] pid {pid} didn't respond within {QUIT_WAIT_SECS:.0f}s — "
              "forcing it. If the hook then fails to attach, fully close "
              "Farever and reopen it.", file=sys.stderr)
        h = _open_process(pid, PROCESS_TERMINATE)
        if h:
            ctypes.windll.kernel32.TerminateProcess.argtypes = (ctypes.c_void_p,
                                                                ctypes.c_uint)
            ctypes.windll.kernel32.TerminateProcess(h, 1)
            _close_handle(h)
    try:
        flag.unlink()
    except OSError:
        pass


def _acquire_claim_mutex():
    """Take the system-wide lock covering the read-decide-write in
    claim_single_instance(), returning a handle to release afterwards.

    Without it that sequence races itself. Stopping the previous instance takes
    up to QUIT_WAIT_SECS, and the winner's own pid isn't written to the lock
    file until after that — so a meter started inside the gap reads the same
    stale pid, shuts down the same already-dying instance, and declares itself
    the survivor too. Two live meters, each certain it's the only one. Starting
    the game from Steam while a shortcut launch is still settling is enough to
    hit it."""
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    try:
        h = k32.CreateMutexW(None, False, CLAIM_MUTEX)
        if not h:
            return None
        # Long enough to outlast a full QUIT_WAIT_SECS handover ahead of us.
        # A timeout isn't fatal — carrying on unserialised is exactly the old
        # behaviour, so the worst case is no worse than before.
        k32.WaitForSingleObject(h, CLAIM_WAIT_MS)
        return h
    except Exception as e:
        print(f"[meter] claim lock unavailable ({e}) — continuing.",
              file=sys.stderr)
        return None


def _release_claim_mutex(h):
    if not h:
        return
    k32 = ctypes.windll.kernel32
    try:
        k32.ReleaseMutex(h)
        k32.CloseHandle(h)
    except Exception:
        pass


def claim_single_instance():
    """Become the only meter running, then record ourselves in the lock file.

    Two overlays on screen at once is confusing enough; two hooks in the game is
    worse. The usual way into it is launching a *second copy* of this script
    from a different folder while the first is still up — which is why the lock
    lives in LOCK_DIR rather than beside the script."""
    try:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return                       # no lock dir => skip the mechanism entirely

    claim = _acquire_claim_mutex()
    try:
        _claim_locked()
    finally:
        _release_claim_mutex(claim)


def _claim_locked():
    """The claim itself. Only ever runs one-at-a-time system-wide."""
    try:
        other = json.loads(LOCK_FILE.read_text())
    except Exception:
        other = {}
    pid = int(other.get("pid") or 0)
    if pid and pid != os.getpid() and _process_alive(pid):
        image = _process_image(pid)
        # A meter is either a python interpreter running the script or the
        # installed executable, and BOTH have to be recognised from either side
        # — the case this exists for is one build being started while the other
        # is already up, and each has to see the other as a meter to displace.
        name = Path(image).name.lower()
        if "python" in name or name in METER_IMAGE_NAMES:
            print(f"[meter] another meter is already running (pid {pid}, "
                  f"{other.get('script') or 'unknown script'}) — asking it to "
                  "exit ...", file=sys.stderr)
            _stop_instance(pid)
        else:
            print(f"[meter] ignoring a stale lock: pid {pid} is "
                  f"{image or 'something unidentifiable'}, not a meter.",
                  file=sys.stderr)

    try:
        LOCK_FILE.write_text(json.dumps({
            "pid": os.getpid(),
            "script": str(Path(__file__).resolve()),
            "started": time.time(),
        }))
    except OSError:
        pass


def release_instance_lock():
    for p in (LOCK_FILE, _quit_flag(os.getpid())):
        try:
            p.unlink()
        except OSError:
            pass



# ---------------------------------------------------------------------------
# The game's window, for the overlays
# ---------------------------------------------------------------------------
def game_window(pid):
    """(hwnd, (x, y, w, h), minimised) of the largest visible top-level window
    of process `pid` — the game's — or None. Physical pixels."""
    if not pid or sys.platform != "win32":
        return None
    u = ctypes.windll.user32
    best = [None, 0]
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def one(h, _l):
        if not u.IsWindowVisible(h):
            return True
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value != pid:
            return True
        r = wintypes.RECT()
        if not u.GetWindowRect(h, ctypes.byref(r)):
            return True
        area = (r.right - r.left) * (r.bottom - r.top)
        if area > best[1]:
            best[0], best[1] = (h, (r.left, r.top, r.right - r.left,
                                    r.bottom - r.top)), area
        return True
    try:
        u.EnumWindows(proto(one), 0)
    except OSError:
        return None
    if best[0] is None:
        return None
    h, rect = best[0]
    return h, rect, bool(u.IsIconic(h))


def foreground_pid():
    """The process whose window has the keyboard focus, or 0."""
    if sys.platform != "win32":
        return 0
    u = ctypes.windll.user32
    h = u.GetForegroundWindow()
    if not h:
        return 0
    p = wintypes.DWORD()
    u.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value
