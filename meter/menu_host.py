"""The Farever+ window: a WebView2 application window in its own process.

WHY A SECOND PROCESS
--------------------
pywebview refuses to run anywhere but the main thread, and a window that
crashed or hung must never be able to take the frida hook down with it —
unloading a wedged hook is how this game gets crashed. So the meter (the
engine: game link, aggregation, saved data) and this window live in two
processes and talk over a pipe.

WHY PIPES AND NOT A SOCKET
--------------------------
A process that opens a listening socket can make Windows Firewall put a dialog
in front of somebody who is mid-raid. stdin/stdout carry the same JSON and ask
nobody's permission.

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

This module is deliberately dumb. It owns the window and the pipe and nothing
else: every label is computed by the meter and every button calls back into it.
The page's behaviour lives in web/menu.js.
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
# Awareness is per-process: without it Windows bitmap-stretches this window by
# the system scale and it comes out blurry.
if sys.platform == "win32":
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass

# One CSS pixel is one real pixel whatever the Windows display scale says; the
# user's own size preference is a CSS zoom on the page (setZoom in menu.js), so
# it can change without restarting the browser environment.
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                      "--force-device-scale-factor=1")

import webview  # noqa: E402  (must follow the environment set-up above)

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


def _log(msg):
    """The meter collects our stderr into its own log."""
    print(f"[window] {msg}", file=sys.stderr, flush=True)


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

    def call(self, method, params=None):
        return self.pipe.call(method, params)

    def notify(self, method, params=None):
        """Fire and forget: a toggle whose only answer is the next state push."""
        self.pipe.send({"t": "call", "id": 0, "m": method, "p": params or {}})

    def typing(self, on):
        """Kept for the page's search boxes; nothing depends on it now."""


class AppWindow:
    def __init__(self, pipe, geom):
        self.pipe = pipe
        self.api = Api(pipe)
        self.hwnd = 0
        self._closing = False
        self._want = geom
        self._last_geom = None
        self.window = webview.create_window(
            "Farever+",
            html=_document(),
            width=int(geom.get("w") or DEFAULT_W),
            height=int(geom.get("h") or DEFAULT_H),
            x=geom.get("x"), y=geom.get("y"),
            min_size=(MIN_W, MIN_H),
            resizable=True,
            background_color="#15161C",
            js_api=self.api,
        )
        self.window.events.moved += self._on_geom
        self.window.events.resized += self._on_geom
        self.window.events.closing += self._on_closing
        pipe.on_message = self._on_message

    def attach(self):
        """Runs once the native window exists: find its handle, restore the
        saved size exactly, and tell the meter we are ready."""
        for _ in range(400):                       # ~10s, then give up quietly
            if getattr(self.window, "native", None) is not None:
                break
            time.sleep(0.025)
        self.hwnd = _own_hwnd(self.window)
        if self.hwnd and self._want.get("w") and self._want.get("h"):
            # SetWindowPos in physical pixels: pywebview's own geometry is in
            # logical pixels and would re-apply the display scale.
            flags = 0x0004 | 0x0010                 # NOZORDER | NOACTIVATE
            x, y = self._want.get("x"), self._want.get("y")
            if x is None or y is None:
                x = y = 0
                flags |= 0x0002                     # NOMOVE
            ctypes.windll.user32.SetWindowPos(
                self.hwnd, 0, int(x), int(y),
                max(int(self._want["w"]), MIN_W),
                max(int(self._want["h"]), MIN_H), flags)
        self.pipe.send({"t": "ready"})

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
        """Closing the window is quitting Farever+: the meter unloads its
        hook and stops. The window closes right away either way."""
        if not self._closing:
            self._closing = True
            self._on_geom()
            self.pipe.send({"t": "closed"})

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
        elif t == "quit":
            self._closing = True
            try:
                self.window.destroy()
            finally:
                os._exit(0)

    def _send_collection_images(self):
        """The collection's and the bestiary's pictures, handed over once the
        page is up, in batches: inlined, they took the page past WebView2's
        2 MB limit on an HTML string, and the window came up blank."""
        self._images_sent = True
        for ns, folder in (("coll", "collection_img"),
                           ("best", "bestiary_img")):
            imgs = list(_analysis_images(folder).items())
            for i in range(0, len(imgs), 40):
                chunk = json.dumps(dict(imgs[i:i + 40]))
                try:
                    self.window.evaluate_js(
                        f"window.addImages('{ns}', {json.dumps(chunk)})")
                except Exception as e:
                    _log(f"{ns} images push failed: {e!r}")
                    break

    def _push(self, data):
        """Hand one state object to the page — as a JSON string argument,
        never interpolated: it carries player names straight off the wire."""
        if not getattr(self, "_images_sent", False):
            self._send_collection_images()
        try:
            self.window.evaluate_js(
                f"window.applyState({json.dumps(json.dumps(data))})")
        except Exception as e:
            _log(f"state push failed: {e!r}")


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


def _document():
    """One self-contained HTML document, assembled from the three files."""
    def read(name):
        try:
            return (WEB_DIR / name).read_text(encoding="utf-8")
        except OSError as e:
            _log(f"missing web asset {name}: {e}")
            return ""

    html = (read("menu.html")
            .replace("/*CSS*/", read("menu.css"))
            .replace("/*JS*/", read("menu.js"))
            .replace("/*ICONS*/",
                     "window.__ICONS__ = " + json.dumps(_class_icons()) + ";"
                     "window.__PORTRAITS__ = " + json.dumps(_boss_portraits())
                     + ";"))
    # WebView2 shows nothing at all for an HTML string over 2 MB.
    if len(html.encode("utf-8")) > 1_800_000:
        _log(f"page is {len(html.encode('utf-8')) // 1024} KB — close to "
             "WebView2's 2 MB limit")
    return html


def _analysis_images(name):
    """{"Mount_Wolf_01": data URI, ...}: the .webp pictures extracted from the
    game into analysis_out/<name>/ (collection, bestiary)."""
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


def _boss_portraits():
    """{"Cleodora": data URI, ...}: the dungeon bosses' portraits, extracted
    from the game into analysis_out/boss_portraits/ — inlined once here
    rather than sent with every state push."""
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


def _class_icons():
    """{"warrior": data URI, ...} for the class icons that exist — inlined in
    the page, so nothing is ever loaded from anywhere."""
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
    # start() returns when the window is gone for good.
    pipe.send({"t": "closed"})


if __name__ == "__main__":
    main()
