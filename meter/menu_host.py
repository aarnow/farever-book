"""The Farever Book window: a WebView2 application window in its own process.

pywebview must own the main thread, and a crashed or hung window must not take
the frida hook down with it (unloading a wedged hook crashes the game). Pipes,
not a socket: a listening socket can trigger a Windows Firewall prompt.

THE PROTOCOL
------------
One JSON object per line, in both directions.

  meter -> here    {"t": "state",  "d": {...}}      the whole page state
                   {"t": "show"}                    bring the window forward
                   {"t": "ret",    "id": N, "r": ...}   reply to a call
                   {"t": "quit"}

  here -> meter    {"t": "ready"}
                   {"t": "call",   "id": N, "m": "...", "p": {...}}
                   {"t": "geom",   "x": .., "y": .., "w": .., "h": ..}
                   {"t": "closed"}                  the user closed the window

This module only owns the windows and the pipe: labels are computed by the
meter, buttons call back into it, and the page's behaviour lives in web/js/.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

# ---------------------------------------------------------------------------
# Everything here has to happen before `import webview`
# ---------------------------------------------------------------------------
# DPI awareness is per-process: without it the window is stretched and blurry.
if sys.platform == "win32":
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass

# One CSS pixel = one physical pixel; the user's size preference is a CSS zoom
# (setZoom in web/js/core.js), changeable without restarting WebView2.
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                      "--force-device-scale-factor=1")

import webview  # noqa: E402  (must follow the environment set-up above)

import i18n  # noqa: E402
import themes  # noqa: E402

HERE = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    WEB_DIR = Path(sys._MEIPASS) / "res" / "web"
    ICON_DIR = Path(sys._MEIPASS) / "res" / "assets" / "classes"
else:
    WEB_DIR = HERE / "web"
    ICON_DIR = HERE.parent / "assets" / "classes"

DEFAULT_W, DEFAULT_H = 1200, 800
MIN_W, MIN_H = 720, 480
SW_RESTORE = 9
SW_MINIMIZE = 6
# Frameless: the page drives move/resize itself, since WebView2 holds the mouse
# capture and the system move loop never sees it (2026-10-01: SC_MOVE did
# nothing).
SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOSIZE = 0x0004, 0x0010, 0x0001


def _log(msg):
    """The meter collects our stderr into its own log."""
    try:
        print(f"[window] {msg}", file=sys.stderr, flush=True)
    except (OSError, ValueError, AttributeError):
        pass                # no log to write to: the window carries on


class Pipe:
    """The line-JSON link to the meter. Writes are serialised behind a lock;
    reads run on one thread that never blocks on anything but stdin."""

    def __init__(self):
        self._lock = threading.Lock()
        self._waiters = {}          # call id -> (Event, [result])
        self._next_id = 1
        self.on_message = None

    def send(self, obj):
        line = json.dumps(obj, separators=(",", ":"))
        with self._lock:
            try:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            except (OSError, ValueError):
                os._exit(0)         # the meter went away

    def call(self, method, params=None, timeout=10.0):
        with self._lock:
            cid = self._next_id
            self._next_id += 1
        ev = threading.Event()
        box = [None]
        self._waiters[cid] = (ev, box)
        self.send({"t": "call", "id": cid, "m": method, "p": params or {}})
        if not ev.wait(timeout):
            self._waiters.pop(cid, None)
            _log(f"call {method} timed out")
            return None
        self._waiters.pop(cid, None)
        return box[0]

    def _resolve(self, cid, result):
        got = self._waiters.get(cid)
        if got:
            ev, box = got
            box[0] = result
            ev.set()

    def read_forever(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                _log(f"unparseable line: {line[:120]!r}")
                continue
            if msg.get("t") == "ret":
                self._resolve(msg.get("id"), msg.get("r"))
            elif self.on_message:
                try:
                    self.on_message(msg)
                except Exception as e:      # a bad message must not kill the pipe
                    _log(f"handler error on {msg.get('t')!r}: {e!r}")
        os._exit(0)                         # stdin closed: the meter stopped


class Api:
    """What the page can call. Everything here runs on a WebView2 thread."""

    def __init__(self, pipe):
        self.pipe = pipe
        self._host = None                   # private: pywebview walks public attributes

    def call(self, method, params=None):
        return self.pipe.call(method, params)

    def notify(self, method, params=None):
        """Fire and forget: a toggle whose only answer is the next state push."""
        self.pipe.send({"t": "call", "id": 0, "m": method, "p": params or {}})

    def pick_folder(self):
        """Windows' folder picker; the folder, or "" when cancelled."""
        host = self._host
        if host is None:
            return ""
        try:
            got = host.window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as e:
            _log(f"folder picker failed: {e!r}")
            return ""
        return str(got[0]) if got else ""

    def win(self, action, arg=None):
        """The page's title bar actions. Returns whether the window is
        maximised."""
        host = self._host
        return host.win(action, arg) if host is not None else False


class AppWindow:
    def __init__(self, pipe, geom):
        self.pipe = pipe
        self.api = Api(pipe)
        self.api._host = self
        self.hwnd = 0
        self._restore_rect = None          # set while maximised
        self._closing = False
        self._want = geom
        self._last_geom = None
        # the colour theme, from the launch (no flash of the default one)
        self._theme = geom.get("theme") or themes.DEFAULT
        self._lang = geom.get("lang") or i18n.DEFAULT
        i18n.set_lang(self._lang)
        self.window = webview.create_window(
            "Farever Book",
            html=_document(self._theme, self._lang),
            width=int(geom.get("w") or DEFAULT_W),
            height=int(geom.get("h") or DEFAULT_H),
            x=geom.get("x"), y=geom.get("y"),
            min_size=(MIN_W, MIN_H),
            resizable=True,
            frameless=True,
            easy_drag=False,
            background_color=themes.color("#211F3A", self._theme),
            js_api=self.api,
        )
        self.window.events.moved += self._on_geom
        self.window.events.resized += self._on_geom
        self.window.events.closing += self._on_closing
        pipe.on_message = self._on_message
        self.overlays = []
        for oid in OVERLAY_IDS:
            try:
                self.overlays.append(Overlay(oid, pipe, self._theme,
                                             self._lang))
            except Exception as e:
                _log(f"overlay {oid} unavailable: {e!r}")

    def attach(self):
        """Once the native window exists: restore the saved geometry, tell the
        meter we are ready."""
        for _ in range(400):                       # ~10s, then give up quietly
            if getattr(self.window, "native", None) is not None:
                break
            time.sleep(0.025)
        self.hwnd = _own_hwnd(self.window)
        if self.hwnd and self._want.get("w") and self._want.get("h"):
            # physical pixels; pywebview's geometry would re-apply the scale
            flags = 0x0004 | 0x0010                 # NOZORDER | NOACTIVATE
            x, y = self._want.get("x"), self._want.get("y")
            if x is None or y is None:
                x = y = 0
                flags |= 0x0002                     # NOMOVE
            ctypes.windll.user32.SetWindowPos(
                self.hwnd, 0, int(x), int(y),
                max(int(self._want["w"]), MIN_W),
                max(int(self._want["h"]), MIN_H), flags)
        for o in self.overlays:
            try:
                o.attach()
            except Exception as e:
                _log(f"overlay {o.id} attach failed: {e!r}")
        if self.overlays:
            threading.Thread(target=self._lock_loop, daemon=True,
                             name="overlay-lock").start()
        self.pipe.send({"t": "ready"})

    def _lock_loop(self):
        """Lock the shown overlays while the game holds the mouse (or
        always): looked at every LOCK_POLL_SECS, faster than a click."""
        was = None
        by_alt = False          # the cursor freed with Alt: the player's own
        u = ctypes.windll.user32
        while True:
            try:
                held = cursor_captured()
                # Alt (the game's FreeCursor) shows the mouse to use what is
                # on screen, the overlays too: only a game window (inventory,
                # map...) frees it without
                alt = bool(u.GetAsyncKeyState(0x12) & 0x8000)   # VK_MENU
                if held != was:
                    by_alt = not held and alt
                    _log(f"overlay: game cursor {'held' if held else 'free'}"
                         + (" (Alt)" if by_alt else ""))
                    was = held
                elif not held and alt:
                    by_alt = True
                window_open = not held and not by_alt
                for o in self.overlays:
                    o.set_free_hidden(o.hide_free and window_open)
                    if o.shown or o.locked:
                        o.set_locked(o.lock_mode == "always" or held)
            except Exception as e:
                _log(f"overlay lock failed: {e!r}")
            time.sleep(LOCK_POLL_SECS)

    def _on_geom(self, *_a):
        if not self.hwnd:
            return
        got = _rect(self.hwnd)
        if not got or got == self._last_geom:
            return
        self._last_geom = got
        x, y, w, h = got
        self.pipe.send({"t": "geom", "x": x, "y": y, "w": w, "h": h})

    def _on_closing(self):
        """Closing the window quits Farever Book."""
        if not self._closing:
            self._closing = True
            self._on_geom()
            self.pipe.send({"t": "closed"})

    def win(self, action, arg=None):
        u = ctypes.windll.user32
        h = self.hwnd
        if not h:
            return False
        if action == "rect":
            got = _rect(h)
            return list(got) if got else None
        if action == "move" and arg and self._restore_rect is None:
            u.SetWindowPos(h, 0, int(arg[0]), int(arg[1]), 0, 0,
                           SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)
            return False
        if action == "setrect" and arg and self._restore_rect is None:
            x, y, w, hh = (int(v) for v in arg[:4])
            u.SetWindowPos(h, 0, x, y, max(w, MIN_W), max(hh, MIN_H),
                           SWP_NOZORDER | SWP_NOACTIVATE)
            return False
        if action == "min":
            u.ShowWindow(h, SW_MINIMIZE)
        elif action == "max":
            self._toggle_max()
        elif action == "close":
            self._on_closing()
            self.window.destroy()
        return self._restore_rect is not None

    def _toggle_max(self):
        """Maximise to the work area and back (Windows' own maximise of a
        borderless window would cover the taskbar)."""
        u = ctypes.windll.user32
        flags = 0x0004 | 0x0010                    # NOZORDER | NOACTIVATE
        if self._restore_rect is not None:
            x, y, w, hh = self._restore_rect
            self._restore_rect = None
            u.SetWindowPos(self.hwnd, 0, x, y, w, hh, flags)
            return

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        mon = u.MonitorFromWindow(self.hwnd, 2)    # MONITOR_DEFAULTTONEAREST
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not u.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return
        self._restore_rect = _rect(self.hwnd)
        w = mi.rcWork
        u.SetWindowPos(self.hwnd, 0, w.left, w.top, w.right - w.left,
                       w.bottom - w.top, flags)

    def show(self):
        if not self.hwnd:
            return
        u = ctypes.windll.user32
        u.ShowWindow(self.hwnd, SW_RESTORE)
        u.SetForegroundWindow(self.hwnd)

    def _on_message(self, msg):
        t = msg.get("t")
        if t == "state":
            self._push(msg.get("d") or {})
        elif t == "show":
            self.show()
        elif t == "ov":
            for o in self.overlays:
                o.update(msg.get("d") or {})
        elif t == "model":
            try:
                self.window.evaluate_js(
                    f"window.addModel({json.dumps(msg.get('id'))}, "
                    f"{json.dumps(msg.get('d'))})")
            except Exception as e:
                _log(f"model push failed: {e!r}")
        elif t == "quit":
            self._closing = True
            try:
                self.window.destroy()
            finally:
                os._exit(0)

    def _send_collection_images(self):
        """The game's pictures, sent in batches once the page is up: inlined,
        they exceed WebView2's 2 MB HTML limit (blank window)."""
        self._images_sent = True
        try:
            self.window.evaluate_js(
                "window.addPortraits && window.addPortraits("
                + json.dumps(json.dumps({"portraits": _boss_portraits()}))
                + ")")
        except Exception as e:
            _log(f"portraits push failed: {e!r}")
        for ns, folder in (("coll", "collection_img"),
                           ("best", "bestiary_img"), ("map", "map_tiles"),
                           ("skill", "skill_img"), ("dbg", "dungeon_bg")):
            imgs = list(_analysis_images(folder).items())
            # at most 40 pictures / ~600 KB (dungeon screens are 100 KB+)
            batches, cur, size = [], [], 0
            for k, v in imgs:
                if cur and (len(cur) >= 40 or size + len(v) > 600_000):
                    batches.append(cur)
                    cur, size = [], 0
                cur.append((k, v))
                size += len(v)
            if cur:
                batches.append(cur)
            for batch in batches:
                chunk = json.dumps(dict(batch))
                try:
                    self.window.evaluate_js(
                        f"window.addImages('{ns}', {json.dumps(chunk)})")
                except Exception as e:
                    _log(f"{ns} images push failed: {e!r}")
                    break

    def _set_theme(self, theme):
        """Recolour the window and the overlays, in place."""
        self._theme = theme
        try:
            self.window.evaluate_js(
                "window.applyTheme && window.applyTheme("
                + json.dumps(themes.themed(_web("menu.css"), theme)) + ")")
        except Exception as e:
            _log(f"theme push failed: {e!r}")
        for o in self.overlays:
            o.set_theme(theme)

    def _set_lang(self, lang):
        """Another language: its catalogue to the window and the overlays,
        which draw themselves again."""
        self._lang = lang
        i18n.set_lang(lang)
        js = ("window.applyLang && window.applyLang("
              + json.dumps(_i18n_json(lang)) + ")")
        for w in [self.window] + [o.window for o in self.overlays]:
            try:
                w.evaluate_js(js)
            except Exception as e:
                _log(f"language push failed: {e!r}")

    def _push(self, data):
        """Hand one state object to the page as a JSON string argument, never
        interpolated: it carries player names off the wire."""
        # resend images once the game's data has been (re)read
        gen = data.get("dataGen")
        if gen is not None and gen != getattr(self, "_images_gen", gen):
            self._images_sent = False
        self._images_gen = gen
        if not getattr(self, "_images_sent", False):
            self._send_collection_images()
        theme = data.get("theme")
        if theme and theme != self._theme:
            self._set_theme(theme)
        lang = data.get("lang")
        if lang and lang != self._lang:
            self._set_lang(lang)
        try:
            self.window.evaluate_js(
                f"window.applyState({json.dumps(json.dumps(data))})")
        except Exception as e:
            _log(f"state push failed: {e!r}")


# ---------------------------------------------------------------------------
# The overlays over the game
# ---------------------------------------------------------------------------
# Two small windows of this process (group meter, goals): borderless, corners
# cut by a window region (pywebview's "transparent" left the rest white),
# topmost, out of the taskbar, never taking focus (WS_EX_NOACTIVATE) except
# while a goal is typed. The meter says when they show and where the game is;
# each is anchored to a side of the game's window at a distance from that edge.
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x80, 0x40000
WS_EX_NOACTIVATE = 0x08000000
# locked: the mouse goes through (a layered window, transparent to clicks),
# so a click while aiming never lands on an overlay instead of the game
WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x80000, 0x20
LWA_ALPHA = 0x2
LOCK_POLL_SECS = 0.05               # how often the game's cursor is looked at


class _CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", ctypes.c_void_p), ("pt", wintypes.POINT)]


def cursor_captured():
    """The game holds the mouse (its "focus" mode: aiming, the camera on
    the mouse): the cursor hidden, or held inside a smaller rectangle than
    the screens."""
    u = ctypes.windll.user32
    ci = _CURSORINFO()
    ci.cbSize = ctypes.sizeof(_CURSORINFO)
    if u.GetCursorInfo(ctypes.byref(ci)) and (
            not (ci.flags & 1) or not ci.hCursor):       # CURSOR_SHOWING
        return True
    clip = wintypes.RECT()
    if u.GetClipCursor(ctypes.byref(clip)):
        vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
        vw, vh = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
        if (clip.left > vx or clip.top > vy
                or clip.right < vx + vw or clip.bottom < vy + vh):
            return True
    return False
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = ctypes.c_void_p(-1)   # a handle: a plain -1 would go out as 32 bits
OVERLAY_IDS = ("meter", "goals")
OVERLAY_BG = "#1D1B33"              # the panel's colour (overlay.css --ov-bg)
OVERLAY_RADIUS = 10                 # its corners (overlay.css #ov)


class OverlayApi:
    """What an overlay's page can call. Runs on a WebView2 thread."""

    def __init__(self, pipe):
        self.pipe = pipe
        self._ov = None                     # private: see Api

    def call(self, method, params=None):
        return self.pipe.call(method, params)

    def notify(self, method, params=None):
        self.pipe.send({"t": "call", "id": 0, "m": method, "p": params or {}})

    def ov(self, action, arg=None):
        o = self._ov
        return o.act(action, arg) if o is not None else None


class Overlay:
    def __init__(self, oid, pipe, theme, lang):
        self.id = oid
        self.pipe = pipe
        self.api = OverlayApi(pipe)
        self.api._ov = self
        self.hwnd = 0
        self.size = (280, 80)
        self.shown = False
        self.dragging = False
        self.typing = None                  # the window to give the keys back to
        self._last = None
        self.game = None
        self.pos = None
        self.lock_mode = "auto"             # "auto" (in focus mode) or "always"
        self.locked = False
        self.hide_free = False              # hidden while the cursor is free
        self.free_hidden = False            # ...and it is: a game window is open
        self.wanted = False                 # the meter wants it shown
        self.window = webview.create_window(
            f"Farever Book — {oid}", html=_overlay_document(oid, theme, lang),
            width=self.size[0], height=self.size[1], x=-4000, y=-4000,
            frameless=True, easy_drag=False, resizable=False, shadow=False,
            hidden=True, on_top=True, focus=False,
            background_color=themes.color(OVERLAY_BG, theme),
            js_api=self.api)

    def attach(self):
        for _ in range(400):
            if getattr(self.window, "native", None) is not None:
                break
            time.sleep(0.025)
        self.hwnd = _own_hwnd(self.window)
        if not self.hwnd:
            _log(f"overlay {self.id}: no window handle")
            return
        u = ctypes.windll.user32
        u.ShowWindow(self.hwnd, SW_HIDE)
        ex = u.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        u.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                         (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
                          | WS_EX_LAYERED) & ~WS_EX_APPWINDOW)
        # layered, fully opaque: only WS_EX_TRANSPARENT changes (set_locked)
        u.SetLayeredWindowAttributes(self.hwnd, 0, 255, LWA_ALPHA)

    def set_free_hidden(self, on):
        """Out of the way while the game's cursor is free (a game window
        open: inventory, map...), back in its focus mode. Never while a goal
        is typed or the overlay dragged."""
        on = bool(on) and self.typing is None and not self.dragging
        if not self.hwnd or on == self.free_hidden:
            return
        self.free_hidden = on
        u = ctypes.windll.user32
        if on and self.shown:
            u.ShowWindow(self.hwnd, SW_HIDE)
            self.shown = False
        elif not on and self.wanted and not self.shown:
            self._place()
            u.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self.shown = True

    def set_locked(self, on):
        """Locked: clicks go through to the game. Never while a goal is typed
        or the overlay dragged."""
        on = bool(on) and self.typing is None and not self.dragging
        if not self.hwnd or on == self.locked:
            return
        self.locked = on
        u = ctypes.windll.user32
        ex = u.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        u.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                         ex | WS_EX_TRANSPARENT if on
                         else ex & ~WS_EX_TRANSPARENT)

    def set_theme(self, theme):
        try:
            self.window.evaluate_js(
                "window.applyTheme && window.applyTheme("
                + json.dumps(themes.themed(_web("overlay.css"), theme)) + ")")
        except Exception as e:
            _log(f"overlay {self.id} theme failed: {e!r}")

    # -- the meter's state -------------------------------------------------
    def update(self, d):
        if not self.hwnd:
            return
        data = d.get(self.id)
        if d.get("show") and data != self._last:
            self._last = data
            try:
                self.window.evaluate_js(
                    "window.applyOverlay && window.applyOverlay("
                    + json.dumps(json.dumps({self.id: data})) + ")")
            except Exception as e:
                _log(f"overlay {self.id} push failed: {e!r}")
        self.game = d.get("game")
        self.pos = (d.get("pos") or {}).get(self.id)
        self.lock_mode = d.get("lock") or "auto"
        self.hide_free = bool(d.get("hideFree"))
        u = ctypes.windll.user32
        on = (d.get("on") or {}).get(self.id, True)
        self.wanted = bool(d.get("show") and on and self.game)
        if self.wanted and not self.free_hidden:
            if not self.dragging:
                self._place()
            if not self.shown:
                u.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
                self.shown = True
        elif self.shown and self.typing is None:
            u.ShowWindow(self.hwnd, SW_HIDE)
            self.shown = False

    def _place(self):
        gx, gy, gw, gh = self.game
        w, h = self.size
        a = self.pos or (
            {"ax": "r", "dx": 24, "ay": "t", "dy": gh // 3}   # meter: right
            if self.id == "meter" else
            {"ax": "l", "dx": 24, "ay": "t", "dy": gh // 3})  # goals: left
        x = gx + a["dx"] if a["ax"] == "l" else gx + gw - w - a["dx"]
        y = gy + a["dy"] if a["ay"] == "t" else gy + gh - h - a["dy"]
        x = max(gx, min(x, gx + gw - w))
        y = max(gy, min(y, gy + gh - min(h, 60)))
        ctypes.windll.user32.SetWindowPos(
            self.hwnd, HWND_TOPMOST, int(x), int(y), int(w), int(h),
            SWP_NOACTIVATE)
        self._shape()

    def _shape(self):
        """Round the window's corners to the panel's: outside, the game."""
        w, h = self.size
        if (w, h) == getattr(self, "_shaped", None):
            return
        self._shaped = (w, h)
        d = OVERLAY_RADIUS * 2
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, d, d)
        # the window owns the region from here on: no DeleteObject
        ctypes.windll.user32.SetWindowRgn(self.hwnd, rgn, True)

    # -- what the page asks ------------------------------------------------
    def act(self, action, arg=None):
        u = ctypes.windll.user32
        h = self.hwnd
        if not h:
            return None
        if action == "rect":
            self.dragging = True
            got = _rect(h)
            return list(got) if got else None
        if action == "move" and arg:
            u.SetWindowPos(h, HWND_TOPMOST, int(arg[0]), int(arg[1]), 0, 0,
                           SWP_NOACTIVATE | SWP_NOSIZE)
            return None
        if action == "drop":
            self.dragging = False
            got = _rect(h)
            if got and self.game:
                # the meter anchors it (nearest sides, on the grid) and its
                # next push places it there
                self.pipe.send({"t": "call", "id": 0, "m": "ov_moved",
                                "p": {"id": self.id, "rect": list(got),
                                      "game": list(self.game)}})
            return None
        if action == "size" and arg:
            self.size = (max(int(arg[0]), 60), max(int(arg[1]), 24))
            if self.game and not self.dragging:
                self._place()
            else:
                u.SetWindowPos(h, 0, 0, 0, self.size[0], self.size[1],
                               SWP_NOZORDER | SWP_NOACTIVATE | 0x0002)
                self._shape()
            return None
        if action == "focus":
            self._focus(bool(arg))
        return None

    def _focus(self, on):
        """Typing a goal: take the keyboard, then hand it back to whatever had
        it (the game)."""
        u = ctypes.windll.user32
        ex = u.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        if on and self.typing is None:
            self.typing = u.GetForegroundWindow() or 0
            self.window.focus = True
            u.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex & ~WS_EX_NOACTIVATE)
            u.SetForegroundWindow(self.hwnd)
        elif not on and self.typing is not None:
            back, self.typing = self.typing, None
            self.window.focus = False
            u.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE)
            if back and back != self.hwnd:
                u.SetForegroundWindow(back)


def _web(name):
    """A file of web/, or "" (logged) when it is missing."""
    try:
        return (WEB_DIR / name).read_text(encoding="utf-8")
    except OSError as e:
        _log(f"missing web asset {name}: {e}")
        return ""


def _i18n_json(lang):
    """The page's language: {lang, dict} as JSON (i18n.py)."""
    return json.dumps({"lang": lang, "dict": i18n.catalog(lang)})


def _overlay_document(oid, theme, lang):
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            '<style id="css">' + themes.themed(_web("overlay.css"), theme)
            + "</style></head><body>"
            '<div id="ov"></div><script>window.__OVERLAY__ = '
            + json.dumps(oid) + ";window.__I18N__ = " + _i18n_json(lang)
            + ";window.__ICONS__ = "
            + json.dumps(_class_icons()) + ";</script><script>"
            + _web("js/overlay.js") + "</script></body></html>")


def _own_hwnd(window):
    if sys.platform != "win32":
        return 0
    try:
        native = getattr(window, "native", None)
        if native is not None:
            return int(native.Handle.ToInt64())
    except Exception as e:
        _log(f"handle via window.native failed ({e!r})")
    return 0


def _rect(hwnd):
    """(x, y, w, h) of the whole window, in physical pixels."""
    r = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


# Joined in this order: core first (shared helpers), boot last (starts the page).
JS_FILES = ("core", "frame", "live", "report", "dungeons", "model3d",
            "collection", "achievements", "hunt", "map", "character", "build", "boot")


def _document(theme, lang):
    """One self-contained HTML document: menu.html, menu.css (in the colour
    theme) and the JS."""
    html = (_web("menu.html")
            .replace("/*CSS*/", themes.themed(_web("menu.css"), theme))
            .replace("/*JS*/", "\n".join(_web(f"js/{f}.js")
                                         for f in JS_FILES))
            .replace("/*ICONS*/",
                     "window.__I18N__ = " + _i18n_json(lang) + ";"
                     "window.__ICONS__ = " + json.dumps(_class_icons()) + ";"
                     "window.__PORTRAITS__ = " + json.dumps(_boss_portraits())
                     + ";window.__SHEET__ = " + json.dumps(_sheet_art())
                     + ";window.__LOGO__ = " + json.dumps(_asset_uri("wordmark.png"))
                     + ";window.__CREST__ = " + json.dumps(_asset_uri("grimoire.png"))
                     + ";"))
    # WebView2 shows nothing at all for an HTML string over 2 MB.
    if len(html.encode("utf-8")) > 1_800_000:
        _log(f"page is {len(html.encode('utf-8')) // 1024} KB — close to "
             "WebView2's 2 MB limit")
    return html


def _analysis_images(name):
    """{"Mount_Wolf_01": data URI, ...}: the .webp pictures extracted into
    analysis_out/<name>/."""
    import base64
    folder = Path(os.environ.get("FAREVER_ANALYSIS")
                  or HERE.parent / "analysis_out") / name
    out = {}
    try:
        files = sorted(folder.glob("*.webp"))
    except OSError:
        return out
    for path in files:
        try:
            out[path.stem] = ("data:image/webp;base64,"
                              + base64.b64encode(path.read_bytes()).decode())
        except OSError:
            continue
    return out


def _asset_uri(name):
    """A picture of assets/ (the header's) as a data URI, or ""."""
    import base64
    try:
        return "data:image/png;base64," + base64.b64encode(
            (ICON_DIR.parent / name).read_bytes()).decode()
    except OSError:
        return ""


def _boss_portraits():
    """{"Cleodora": data URI, ...}: the dungeon bosses' portraits from
    analysis_out/boss_portraits/."""
    import base64
    folder = Path(os.environ.get("FAREVER_ANALYSIS")
                  or HERE.parent / "analysis_out") / "boss_portraits"
    out = {}
    try:
        files = sorted(folder.glob("*.png"))
    except OSError:
        return out
    for path in files:
        try:
            out[path.stem] = ("data:image/png;base64,"
                              + base64.b64encode(path.read_bytes()).decode())
        except OSError:
            continue
    return out


def _sheet_art():
    """{"slot_Head": data URI, "upgrade_pip": ..., "stat_Faith": ...}:
    the character sheet's art, cut from the game's UI (assets/charsheet/)."""
    import base64
    out = {}
    try:
        files = sorted((ICON_DIR.parent / "charsheet").iterdir())
    except OSError:
        return out
    mime = {".png": "image/png", ".webp": "image/webp"}
    for path in files:
        if path.suffix in mime:
            out[path.stem] = (f"data:{mime[path.suffix]};base64,"
                              + base64.b64encode(path.read_bytes()).decode())
    return out


def _class_icons():
    """{"warrior": data URI, ...} for the class icons that exist."""
    import base64
    out = {}
    folder = ICON_DIR
    for key in ("warrior", "mage", "priest", "rogue"):
        try:
            data = (folder / f"{key}.png").read_bytes()
        except OSError:
            continue
        out[key] = "data:image/png;base64," + base64.b64encode(data).decode()
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    geom = {}
    if len(sys.argv) > 1:
        try:
            geom = json.loads(sys.argv[1])
        except ValueError:
            pass
    pipe = Pipe()
    win = AppWindow(pipe, geom)
    threading.Thread(target=pipe.read_forever, daemon=True).start()
    webview.start(win.attach, debug=bool(os.environ.get("FAREVER_MENU_DEBUG")))
    pipe.send({"t": "closed"})


if __name__ == "__main__":
    main()
