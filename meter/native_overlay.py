"""Overlays drawn by us, in plain Windows windows (no WebView2).

Why: on some Windows 11 26H2 (build 26300), the overlays made of a WebView2
page showed a black rectangle around their panel. Their see-through parts
came only from cutting the window (SetWindowRgn), and WebView2 draws in child
windows of its own through DirectComposition, which that build no longer
clips to the cut. FareverMeter (brudrbear), whose overlays never had it,
draws in plain windows of its own. So do these: each overlay is drawn into
an image (Pillow) and handed to Windows with its alpha, pixel by pixel
(UpdateLayeredWindow): its corners and its frame's edges see-through and
smooth, nothing for the compositor to clip.

They look as the WebView2 ones did: the same frame, title, colours, fonts
and measures (web/overlay.css and web/js/overlay.js, measured on the page as
Edge lays it out). One thread owns their windows and draws them; the rest of
menu_host talks to them as to its other overlays (Overlay's methods).
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import queue
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent


def _log(msg):
    try:
        print(f"[window] {msg}", file=sys.stderr, flush=True)
    except (OSError, ValueError, AttributeError):
        pass


def _analysis():
    return Path(os.environ.get("FAREVER_ANALYSIS") or HERE.parent / "analysis_out")


def _icon_dir():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "res" / "assets" / "classes"
    return HERE.parent / "assets" / "classes"


# ---------------------------------------------------------------------------
# Win32
# ---------------------------------------------------------------------------
_U32 = ctypes.WinDLL("user32", use_last_error=True)
_G32 = ctypes.WinDLL("gdi32", use_last_error=True)
_K32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("hwndTrack", wintypes.HWND), ("dwHoverTime", wintypes.DWORD)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", wintypes.POINT),
                ("lPrivate", wintypes.DWORD)]


_U32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                wintypes.LPARAM]
_U32.DefWindowProcW.restype = LRESULT
_U32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                 wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                 wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
_U32.CreateWindowExW.restype = wintypes.HWND
_U32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
_U32.RegisterClassExW.restype = wintypes.ATOM
_U32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
_U32.UpdateLayeredWindow.restype = wintypes.BOOL
_U32.GetDC.argtypes = [wintypes.HWND]
_U32.GetDC.restype = wintypes.HDC
_U32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_U32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
_U32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_U32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_U32.GetWindowLongW.restype = ctypes.c_long
_U32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_U32.SetWindowLongW.restype = ctypes.c_long
_U32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT,
                             wintypes.UINT]
_U32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
_U32.SetCursor.argtypes = [wintypes.HANDLE]
_U32.SetCursor.restype = wintypes.HANDLE
_U32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
_U32.LoadCursorW.restype = wintypes.HANDLE
_U32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
_U32.CreateIconIndirect.restype = wintypes.HICON
_U32.SetCapture.argtypes = [wintypes.HWND]
_U32.GetCapture.restype = wintypes.HWND
_U32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_U32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_U32.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]
_U32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
try:
    _U32.GetDpiForWindow.argtypes = [wintypes.HWND]
    _U32.GetDpiForWindow.restype = wintypes.UINT
except AttributeError:
    pass
_G32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_G32.CreateCompatibleDC.restype = wintypes.HDC
_G32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                  ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                  wintypes.DWORD]
_G32.CreateDIBSection.restype = wintypes.HBITMAP
_G32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_G32.SelectObject.restype = wintypes.HGDIOBJ
_G32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_G32.DeleteDC.argtypes = [wintypes.HDC]
_G32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT,
                              wintypes.UINT, ctypes.c_void_p]
_G32.CreateBitmap.restype = wintypes.HBITMAP
_K32.GetModuleHandleW.restype = wintypes.HMODULE
_K32.GetCurrentThreadId.restype = wintypes.DWORD

GWL_EXSTYLE = -20
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
HWND_TOPMOST = wintypes.HWND(-1)
ULW_ALPHA = 2
AC_SRC_ALPHA = 1
WM_DESTROY = 0x0002
WM_SETCURSOR = 0x0020
WM_MOUSEACTIVATE = 0x0021
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEWHEEL = 0x020A
WM_CAPTURECHANGED = 0x0215
WM_MOUSELEAVE = 0x02A3
WM_DPICHANGED = 0x02E0
WM_APP_RUN = 0x8000 + 17
MA_NOACTIVATE = 3
HTCLIENT = 1
TME_LEAVE = 2
IDC_SIZENS = 32645
IDC_ARROW = 32512
IDC_HAND = 32649


def _signed16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class _UiThread:
    """The thread that owns the native overlays' windows: their messages
    (the mouse), their drawing. Others hand it work with run()."""

    _one = None
    CLASS = "FareverBookNativeOverlay"

    @classmethod
    def get(cls):
        if cls._one is None:
            cls._one = cls()
        return cls._one

    def __init__(self):
        self.jobs = queue.Queue()
        self.windows = {}               # hwnd -> its overlay
        self.ready = threading.Event()
        self.tid = 0
        self._proc = WNDPROC(self._wndproc)     # kept alive
        threading.Thread(target=self._loop, daemon=True,
                         name="native-overlays").start()
        self.ready.wait(5)

    def run(self, fn, wait=False):
        """fn() on this thread; with wait, its result (None after 5 s)."""
        if threading.get_ident() == getattr(self, "ident", None):
            return fn()
        box, ev = [None], threading.Event()

        def job():
            try:
                box[0] = fn()
            except Exception as e:
                _log(f"native overlay: {e!r}")
            finally:
                ev.set()
        self.jobs.put(job)
        _U32.PostThreadMessageW(self.tid, WM_APP_RUN, 0, 0)
        if wait:
            ev.wait(5)
            return box[0]
        return None

    def _loop(self):
        self.ident = threading.get_ident()
        self.tid = _K32.GetCurrentThreadId()
        hinst = _K32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hCursor = _U32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
        wc.lpszClassName = self.CLASS
        if not _U32.RegisterClassExW(ctypes.byref(wc)):
            _log(f"native overlay: no window class ({ctypes.get_last_error()})")
        self.hinst = hinst
        msg = MSG()
        # the thread's queue exists once it asked for a message: posts work
        _U32.PeekMessageW = getattr(_U32, "PeekMessageW")
        _U32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self.ready.set()
        while _U32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP_RUN and not msg.hwnd:
                self._drain()
                continue
            _U32.TranslateMessage(ctypes.byref(msg))
            _U32.DispatchMessageW(ctypes.byref(msg))

    def _drain(self):
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                return
            job()

    def create(self, owner, title):
        hwnd = _U32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST,
            self.CLASS, title, WS_POPUP, -4000, -4000, 10, 10, None, None,
            self.hinst, None)
        if hwnd:
            self.windows[hwnd] = owner
        else:
            _log(f"native overlay: no window ({ctypes.get_last_error()})")
        return hwnd or 0

    def _wndproc(self, hwnd, msg, wp, lp):
        o = self.windows.get(hwnd)
        try:
            if o is not None:
                if msg == WM_MOUSEACTIVATE:
                    return MA_NOACTIVATE
                if msg == WM_SETCURSOR and (lp & 0xFFFF) == HTCLIENT:
                    _U32.SetCursor(o.cursor())
                    return 1
                if msg == WM_MOUSEMOVE:
                    if not o._tracking:
                        tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT),
                                              TME_LEAVE, hwnd, 0)
                        _U32.TrackMouseEvent(ctypes.byref(tme))
                        o._tracking = True
                    o.on_mouse("move", _signed16(lp), _signed16(lp >> 16))
                    return 0
                if msg == WM_MOUSELEAVE:
                    o._tracking = False
                    o.on_mouse("leave", 0, 0)
                    return 0
                if msg == WM_LBUTTONDOWN:
                    _U32.SetCapture(hwnd)
                    o.on_mouse("down", _signed16(lp), _signed16(lp >> 16))
                    return 0
                if msg == WM_LBUTTONUP:
                    o.on_mouse("up", _signed16(lp), _signed16(lp >> 16))
                    if _U32.GetCapture() == hwnd:
                        _U32.ReleaseCapture()
                    return 0
                if msg == WM_CAPTURECHANGED:
                    o.on_mouse("cancel", 0, 0)
                    return 0
                if msg == WM_MOUSEWHEEL:
                    pt = wintypes.POINT(_signed16(lp), _signed16(lp >> 16))
                    _U32.ScreenToClient(hwnd, ctypes.byref(pt))
                    o.on_wheel(_signed16(wp >> 16), pt.x, pt.y)
                    return 0
                if msg == WM_DPICHANGED:
                    o.redraw()
                    return 0
        except Exception as e:
            _log(f"native overlay {getattr(o, 'id', '?')}: {e!r}")
        return _U32.DefWindowProcW(hwnd, msg, wp, lp)


def _present(hwnd, img, pos, alpha):
    """img (RGBA) as the window's pixels, its alpha kept (premultiplied, as
    Windows wants it), at pos (screen, None: where it is), the whole window
    at `alpha` (0-255)."""
    w, h = img.size
    r, g, b, a = img.split()
    bgra = Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a),
                                ImageChops.multiply(r, a), a)).tobytes()
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0,
                           0, 0, 0, 0, 0)
    screen = _U32.GetDC(None)
    mem = _G32.CreateCompatibleDC(screen)
    bits = ctypes.c_void_p()
    hbm = _G32.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits),
                                None, 0)
    ok = False
    if hbm and bits:
        ctypes.memmove(bits, bgra, len(bgra))
        old = _G32.SelectObject(mem, hbm)
        blend = BLENDFUNCTION(0, 0, max(0, min(255, int(alpha))), AC_SRC_ALPHA)
        ok = _U32.UpdateLayeredWindow(
            hwnd, screen, ctypes.byref(wintypes.POINT(*pos)) if pos else None,
            ctypes.byref(wintypes.SIZE(w, h)), mem,
            ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
        _G32.SelectObject(mem, old)
        _G32.DeleteObject(hbm)
    _G32.DeleteDC(mem)
    _U32.ReleaseDC(None, screen)
    return bool(ok)


def _cursor_from_png(path, scale=1.0, hot=(0, 0)):
    """A mouse cursor from a picture (the game's own, its hotspot the
    top-left as the page's CSS has it), or None."""
    try:
        im = Image.open(path).convert("RGBA")
    except OSError:
        return None
    if abs(scale - 1) > 0.01:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                       Image.LANCZOS)
    w, h = im.size
    r, g, b, a = im.split()
    data = Image.merge("RGBA", (b, g, r, a)).tobytes()
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0,
                           0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    color = _G32.CreateDIBSection(None, ctypes.byref(bmi), 0, ctypes.byref(bits),
                                  None, 0)
    if not color:
        return None
    ctypes.memmove(bits, data, len(data))
    mask = _G32.CreateBitmap(w, h, 1, 1, None)
    ii = ICONINFO(False, hot[0], hot[1], mask, color)
    cur = _U32.CreateIconIndirect(ctypes.byref(ii))
    _G32.DeleteObject(mask)
    _G32.DeleteObject(color)
    return cur


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
FONT_FILES = {400: "segoeui.ttf", 600: "seguisb.ttf", 700: "segoeuib.ttf"}
# Segoe UI's vertical metrics (its hhea and OS/2 win values, units per em):
# where Chromium puts a line's baseline in its line box
SEGOE_ASC, SEGOE_DESC, SEGOE_EM = 2210, 514, 2048
SS = 4                              # shapes drawn this many times larger
TEXT_GAMMA = [round(255 * (v / 255) ** 0.72) for v in range(256)]


def _rgba(c, a=1.0):
    """'#RRGGBB' or (r, g, b) and an alpha 0..1 -> (r, g, b, a 0..255)."""
    if isinstance(c, str):
        c = c.lstrip("#")
        c = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    return (c[0], c[1], c[2], max(0, min(255, round(a * 255))))


def _mix(c, other, t):
    """CSS color-mix(in srgb, c t, other): t of c."""
    c, other = _rgba(c)[:3], _rgba(other)[:3]
    return tuple(round(c[i] * t + other[i] * (1 - t)) for i in range(3))


_FONTS = {}


SYMBOL_CHARS = set("✓✦")            # not in Segoe UI: Segoe UI Symbol's


def _font(weight, px, symbol=False):
    key = (weight, round(px * 4) / 4, symbol)
    if key not in _FONTS:
        try:
            _FONTS[key] = ImageFont.truetype(
                str(FONT_DIR / ("seguisym.ttf" if symbol else FONT_FILES[weight])), key[1])
        except OSError:
            _FONTS[key] = ImageFont.load_default()
    return _FONTS[key]


class Paint:
    """An image of a w x h CSS pixels panel drawn at k device pixels each:
    its shapes smoothed (drawn SS times larger, then reduced), its texts as
    Chromium lays them (Segoe UI, baseline in the line box)."""

    def __init__(self, w, h, k):
        self.k = k
        self.img = Image.new("RGBA", (math.ceil(w * k), math.ceil(h * k)), (0, 0, 0, 0))

    def box(self, x, y, w, h):
        """A CSS box on the device pixels (its edges snapped, as Chromium)."""
        k = self.k
        x0, y0 = round(x * k), round(y * k)
        return x0, y0, max(0, round((x + w) * k) - x0), max(0, round((y + h) * k) - y0)

    def _mask(self, w, h, r, inset=0.0):
        """A rounded rectangle's coverage, w x h device pixels, corners r
        (device), drawn inset by `inset` device pixels."""
        m = Image.new("L", (w * SS, h * SS), 0)
        d = ImageDraw.Draw(m)
        i = inset * SS
        if w * SS - 2 * i > 0 and h * SS - 2 * i > 0:
            d.rounded_rectangle((i, i, w * SS - 1 - i, h * SS - 1 - i),
                                max(0, r * SS - i), fill=255)
        return m.resize((w, h), Image.BOX)

    def _put(self, layer, x0, y0):
        self.img.alpha_composite(layer, (x0, y0))

    def fill(self, x, y, w, h, color, alpha=1.0, r=0.0):
        x0, y0, pw, ph = self.box(x, y, w, h)
        if pw <= 0 or ph <= 0:
            return
        c = _rgba(color, alpha)
        layer = Image.new("RGBA", (pw, ph), c[:3] + (0,))
        if r > 0:
            m = self._mask(pw, ph, r * self.k)
            layer.putalpha(m.point(lambda v: v * c[3] // 255))
        else:
            layer.putalpha(c[3])
        self._put(layer, x0, y0)

    def ring(self, x, y, w, h, width, color, alpha=1.0, r=0.0):
        """An inset box-shadow 0 0 0 width: a ring inside the box's edge."""
        x0, y0, pw, ph = self.box(x, y, w, h)
        if pw <= 0 or ph <= 0:
            return
        k = self.k
        outer = self._mask(pw, ph, r * k)
        inner = self._mask(pw, ph, max(0.0, r - width) * k, width * k)
        m = ImageChops.subtract(outer, inner)
        c = _rgba(color, alpha)
        layer = Image.new("RGBA", (pw, ph), c[:3] + (0,))
        layer.putalpha(m.point(lambda v: v * c[3] // 255))
        self._put(layer, x0, y0)

    def border(self, x, y, w, h, color, alpha=1.0, r=0.0):
        """A 1 px border (a ring of 1 CSS pixel)."""
        self.ring(x, y, w, h, 1, color, alpha, r)

    def hline(self, x, y, w, color, alpha=1.0):
        self.fill(x, y, w, 1, color, alpha)

    def image(self, im, x, y, w, h, r=0.0, opacity=1.0, fit="fill"):
        """A picture in a w x h box: stretched (fill), whole inside it
        (contain) or covering it (cover), its corners r."""
        x0, y0, pw, ph = self.box(x, y, w, h)
        if pw <= 0 or ph <= 0:
            return
        im = im.convert("RGBA")
        if fit in ("contain", "cover") and im.width and im.height:
            s = (min if fit == "contain" else max)(pw / im.width, ph / im.height)
            iw, ih = max(1, round(im.width * s)), max(1, round(im.height * s))
            scaled = im.resize((iw, ih), Image.LANCZOS)
            im = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
            if fit == "contain":
                im.alpha_composite(scaled, ((pw - iw) // 2, (ph - ih) // 2))
            else:
                im = scaled.crop(((iw - pw) // 2, (ih - ph) // 2,
                                  (iw - pw) // 2 + pw, (ih - ph) // 2 + ph))
        else:
            im = im.resize((pw, ph), Image.LANCZOS)
        if r > 0:
            im.putalpha(ImageChops.multiply(im.getchannel("A"),
                                            self._mask(pw, ph, r * self.k)))
        if opacity < 1:
            im.putalpha(im.getchannel("A").point(lambda v: round(v * opacity)))
        self._put(im, x0, y0)

    def mask_image(self, im, x, y, w, h, color, alpha=1.0):
        """A picture's shape in one colour (CSS -webkit-mask)."""
        x0, y0, pw, ph = self.box(x, y, w, h)
        if pw <= 0 or ph <= 0:
            return
        a = im.convert("RGBA").resize((pw, ph), Image.LANCZOS).getchannel("A")
        c = _rgba(color, alpha)
        layer = Image.new("RGBA", (pw, ph), c[:3] + (0,))
        layer.putalpha(a.point(lambda v: v * c[3] // 255))
        self._put(layer, x0, y0)

    def dashed(self, x, y, w, h, width, color, alpha=1.0, r=0.0):
        """A dashed border (Chromium: dashes three times its width, gaps
        alike, the corners left whole)."""
        x0, y0, pw, ph = self.box(x, y, w, h)
        if pw <= 0 or ph <= 0:
            return
        k = self.k
        bw = max(1, round(width * k))
        outer = self._mask(pw, ph, r * k)
        inner = self._mask(pw, ph, max(0.0, r * k - bw), bw)
        ring = ImageChops.subtract(outer, inner)
        keep = Image.new("L", (pw, ph), 0)
        d = ImageDraw.Draw(keep)
        dash = 3 * bw
        corner = max(round(r * k), bw)
        for x_ in range(corner, pw - corner, 2 * dash):
            d.rectangle((x_, 0, min(pw - corner, x_ + dash) - 1, ph), fill=255)
        for y_ in range(corner, ph - corner, 2 * dash):
            d.rectangle((0, y_, pw, min(ph - corner, y_ + dash) - 1), fill=255)
        d.rectangle((0, 0, corner, corner), fill=255)
        d.rectangle((pw - corner, 0, pw, corner), fill=255)
        d.rectangle((0, ph - corner, corner, ph), fill=255)
        d.rectangle((pw - corner, ph - corner, pw, ph), fill=255)
        m = ImageChops.multiply(ring, keep)
        c = _rgba(color, alpha)
        layer = Image.new("RGBA", (pw, ph), c[:3] + (0,))
        layer.putalpha(m.point(lambda v: v * c[3] // 255))
        self._put(layer, x0, y0)

    # -- text ---------------------------------------------------------------
    @staticmethod
    def _runs(s):
        """s cut where Segoe UI lacks a glyph (✓, ✦: Segoe UI Symbol, as
        Chromium falls back): [(text, symbol)]."""
        out = []
        for ch in s:
            sym = ch in SYMBOL_CHARS
            if out and out[-1][1] == sym:
                out[-1] = (out[-1][0] + ch, sym)
            else:
                out.append((ch, sym))
        return out

    def width(self, s, px, weight=400, spacing=0.0):
        k = self.k
        total = 0.0
        for run, sym in self._runs(s):
            f = _font(weight, px * k, sym)
            total += f.getlength(run) / k
        return total + spacing * len(s)

    def wrap(self, s, px, weight, maxw):
        """s cut into lines no wider than maxw, at its spaces."""
        lines, cur = [], ""
        for word in s.split():
            t = (cur + " " + word) if cur else word
            if cur and self.width(t, px, weight) > maxw:
                lines.append(cur)
                cur = word
            else:
                cur = t
        if cur:
            lines.append(cur)
        return lines or [""]

    def text(self, s, x, top, lh, px, weight=400, color="#000000", alpha=1.0,
             align="left", w=None, spacing=0.0, shadow=None, clip=None, stroke=None):
        """s on a line box at `top` lh high, from x (or right/centre in a box
        w wide), clipped with an ellipsis to clip wide. shadow: (dy, blur,
        alpha) of black, or a list of them, as the CSS text-shadow 0 dy blur;
        stroke: (width, colour) outside the letters (canvas strokeText)."""
        k = self.k
        f = _font(weight, px * k)
        if clip is not None and self.width(s, px, weight, spacing) > clip:
            while s and self.width(s + "…", px, weight, spacing) > clip:
                s = s[:-1]
            s = s.rstrip() + "…"
        tw = self.width(s, px, weight, spacing)
        if align == "right" and w is not None:
            x = x + w - tw
        elif align == "center" and w is not None:
            x = x + (w - tw) / 2
        # Chromium: the font's ascent and descent rounded to whole pixels,
        # the baseline on the pixel above
        asc = round(px * k * SEGOE_ASC / SEGOE_EM)
        desc = round(px * k * SEGOE_DESC / SEGOE_EM)
        base = math.floor(top * k + (lh * k - (asc + desc)) / 2 + asc) / k
        shadows = ([shadow] if shadow and not isinstance(shadow, list) else shadow) or []
        pad = 4 + max([s_[1] * 2 for s_ in shadows] + [stroke[0] * 2 if stroke else 0])
        mw = math.ceil(tw * k) + 2 * math.ceil(pad * k) + 2
        mh = math.ceil(lh * k + 2 * pad * k) + 4
        ox, oy = math.floor(x * k) - math.ceil(pad * k), math.floor((top - pad) * k)
        bx = x * k - ox
        by = base * k - oy

        def draw(width=0):
            m = Image.new("L", (mw, mh), 0)
            d = ImageDraw.Draw(m)
            sw = round(width * k)
            if spacing:
                # each letter where the whole line puts it (its kerning
                # kept), the spacing added after each
                for i, ch in enumerate(s):
                    fi = _font(weight, px * k, ch in SYMBOL_CHARS)
                    d.text((bx + self.width(s[:i], px, weight, spacing) * k, by), ch,
                           font=fi, fill=255, anchor="ls", stroke_width=sw, stroke_fill=255)
            else:
                cx = bx
                for run, sym in self._runs(s):
                    fr = _font(weight, px * k, sym)
                    d.text((cx, by), run, font=fr, fill=255, anchor="ls",
                           stroke_width=sw, stroke_fill=255)
                    cx += fr.getlength(run)
            # DirectWrite's darker anti-aliasing (its contrast and gamma),
            # which FreeType's coverage alone draws thinner
            return m.point(TEXT_GAMMA)
        mask = draw()
        for dy, blur, sa in shadows:
            sm = mask.filter(ImageFilter.GaussianBlur(blur / 2 * k)) if blur else mask
            sl = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
            sl.putalpha(sm.point(lambda v: round(v * sa * alpha)))
            self._put(sl, ox, oy + round(dy * k))
        if stroke:
            sc = _rgba(stroke[1], alpha)
            sl = Image.new("RGBA", (mw, mh), sc[:3] + (0,))
            sl.putalpha(draw(stroke[0]).point(lambda v: v * sc[3] // 255))
            self._put(sl, ox, oy)
        c = _rgba(color, alpha)
        layer = Image.new("RGBA", (mw, mh), c[:3] + (0,))
        layer.putalpha(mask.point(lambda v: v * c[3] // 255))
        self._put(layer, ox, oy)
        return tw


# -- the game's pieces (gamedata.ensure_ui_frame) ----------------------------
_PIECES = {}


def _piece(name):
    """A picture of analysis_out (the game's frame, cursors...), or None."""
    if name not in _PIECES:
        try:
            _PIECES[name] = Image.open(_analysis() / name).convert("RGBA")
        except OSError:
            _PIECES[name] = None
    return _PIECES[name]


def _class_icon(ck):
    key = "class:" + str(ck)
    if key not in _PIECES:
        try:
            _PIECES[key] = Image.open(_icon_dir() / f"{ck}.png").convert("RGBA")
        except (OSError, ValueError):
            _PIECES[key] = None
    return _PIECES[key]


FRAME_SLICE, FRAME_CSS = 32, 32


def _frame(p, w, h):
    """The game's window frame (overlay.css html.framed #ov: border-image
    32 fill / 32px stretch) over the whole w x h box."""
    tex = _piece("ui_frame.png")
    if tex is None:
        p.fill(0, 0, w, h, "#B89786", r=5)
        p.border(0, 0, w, h, "#6A4E44", r=5)
        return False
    s, c = FRAME_SLICE, FRAME_CSS
    tw, th = tex.size
    xs = [(0, s, 0, c), (s, tw - s, c, w - c), (tw - s, tw, w - c, w)]
    ys = [(0, s, 0, c), (s, th - s, c, h - c), (th - s, th, h - c, h)]
    k = p.k
    for (sx0, sx1, dx0, dx1) in xs:
        for (sy0, sy1, dy0, dy1) in ys:
            x0, y0 = round(dx0 * k), round(dy0 * k)
            pw, ph = round(dx1 * k) - x0, round(dy1 * k) - y0
            if pw <= 0 or ph <= 0:
                continue
            part = tex.crop((sx0, sy0, sx1, sy1)).resize((pw, ph), Image.BILINEAR)
            p._put(part, x0, y0)
    return True


_TITLE = {}


def _title_image(text, dpi_k):
    """A window's title as the game writes it (its bitmap font, Platypi Bold,
    inked #6A4E44, overlay.js titleEl), at the screen's scale; None when
    the font or one of the letters is missing."""
    key = (text, round(dpi_k * 100))
    if key in _TITLE:
        return _TITLE[key]
    out = None
    try:
        meta = json.loads((_analysis() / "ui_font_title.json").read_text(encoding="utf-8"))
        atlas = Image.open(_analysis() / "ui_font_title.png").convert("RGBA")
        glyphs = {int(cp): g for cp, g in meta["glyphs"].items()}
        if all(ord(ch) in glyphs or ch == " " for ch in text):
            space = glyphs[32][6] if 32 in glyphs else round(meta["size"] / 4)
            w = sum(glyphs[ord(ch)][6] if ord(ch) in glyphs else space for ch in text)
            cw = max(2, w + 2 + ((w + 2) % 2))
            ch_ = meta["lineHeight"] + (meta["lineHeight"] % 2)
            cv = Image.new("RGBA", (cw, ch_), (0, 0, 0, 0))
            x = 0
            for ch in text:
                g = glyphs.get(ord(ch))
                if not g:
                    x += space
                    continue
                gx, gy, gw, gh, ox, oy, adv = g
                if gw and gh:
                    cv.alpha_composite(atlas.crop((gx, gy, gx + gw, gy + gh)), (x + ox, oy))
                x += adv
            ink = Image.new("RGBA", cv.size, _rgba("#6A4E44"))
            ink.putalpha(cv.getchannel("A"))
            out = (ink, cw, ch_)
    except (OSError, ValueError, KeyError):
        out = None
    _TITLE[key] = out
    return out


def _svg_path_points(d, steps=10):
    """A path's first outline (M, L, H, V, C and their lower-case forms, Z)
    as points: enough for the overlays' small icons."""
    import re
    toks = re.findall(r"[A-Za-z]|-?\d*\.?\d+(?:e-?\d+)?", d)
    pts, i, cmd = [], 0, None
    x = y = 0.0
    sx = sy = 0.0

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                return pts
            continue
        if cmd in "Mm":
            nx, ny = num(), num()
            x, y = (x + nx, y + ny) if cmd == "m" and pts else (nx, ny)
            sx, sy = x, y
            pts.append((x, y))
            cmd = "l" if cmd == "m" else "L"
        elif cmd in "Ll":
            nx, ny = num(), num()
            x, y = (x + nx, y + ny) if cmd == "l" else (nx, ny)
            pts.append((x, y))
        elif cmd in "Hh":
            nx = num()
            x = x + nx if cmd == "h" else nx
            pts.append((x, y))
        elif cmd in "Vv":
            ny = num()
            y = y + ny if cmd == "v" else ny
            pts.append((x, y))
        elif cmd in "Cc":
            c = [num() for _ in range(6)]
            if cmd == "c":
                c = [c[0] + x, c[1] + y, c[2] + x, c[3] + y, c[4] + x, c[5] + y]
            x0, y0 = x, y
            for s in range(1, steps + 1):
                t_ = s / steps
                mt = 1 - t_
                pts.append((mt ** 3 * x0 + 3 * mt * mt * t_ * c[0] + 3 * mt * t_ * t_ * c[2] + t_ ** 3 * c[4],
                            mt ** 3 * y0 + 3 * mt * mt * t_ * c[1] + 3 * mt * t_ * t_ * c[3] + t_ ** 3 * c[5]))
            x, y = c[4], c[5]
        else:
            i += 1
    return pts


GEAR_PATH = ("M9.4 1l.3 1.8c.4.1.8.3 1.1.5l1.5-1 1.4 1.4-1 1.5c.2.3.4.7.5 1.1L15 6.6v2l-1.8.3"
             "c-.1.4-.3.8-.5 1.1l1 1.5-1.4 1.4-1.5-1c-.3.2-.7.4-1.1.5L9.4 15h-2l-.3-1.8"
             "c-.4-.1-.8-.3-1.1-.5l-1.5 1-1.4-1.4 1-1.5c-.2-.3-.4-.7-.5-1.1L1.8 9V7l1.8-.3"
             "c.1-.4.3-.8.5-1.1l-1-1.5 1.4-1.4 1.5 1c.3-.2.7-.4 1.1-.5L7.4 1h2z")


def _icon(p, kind, x, y, w, h, color, alpha=1.0):
    """The overlays' small drawn icons (overlay.js svg): "gear" (16 x 16
    box), "fit" (12 x 14 box), in w x h CSS pixels at x, y."""
    x0, y0, pw, ph = p.box(x, y, w, h)
    if pw <= 0 or ph <= 0:
        return
    vb = (16, 16) if kind == "gear" else (12, 14)
    S = 8
    m = Image.new("L", (pw * S, ph * S), 0)
    d = ImageDraw.Draw(m)
    sx, sy = pw * S / vb[0], ph * S / vb[1]
    if kind == "gear":
        d.polygon([(px * sx, py * sy) for px, py in _svg_path_points(GEAR_PATH)], fill=255)
        cx, cy, r = 8.4 * sx, 8.0 * sy, 2.4 * sx
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=0)
    else:
        d.polygon([(6 * sx, 1 * sy), (2.5 * sx, 4.5 * sy), (9.5 * sx, 4.5 * sy)], fill=255)
        d.polygon([(6 * sx, 13 * sy), (2.5 * sx, 9.5 * sy), (9.5 * sx, 9.5 * sy)], fill=255)
        d.rectangle((2 * sx, 6.3 * sy, 10 * sx, 7.7 * sy), fill=255)
    m = m.resize((pw, ph), Image.BOX)
    c = _rgba(color, alpha)
    layer = Image.new("RGBA", (pw, ph), c[:3] + (0,))
    layer.putalpha(m.point(lambda v: v * c[3] // 255))
    p._put(layer, x0, y0)


# ---------------------------------------------------------------------------
# The panels: the meter, the loot luck, the goals
# ---------------------------------------------------------------------------
# overlay.css's colours
OV_BG = "#B89786"
OV_WELL = "#3A2E27"
OV_TEXT = "#2E1E18"
OV_DIM = "#5E4238"
OV_LINE = (94, 66, 56)
OV_ACCENT = "#E9A23B"
OV_OK = "#7CBF3C"
CLASS_COLORS = {"warrior": "#B8513C", "mage": "#3F78D6", "priest": "#C9A93E",
                "rogue": "#3E9E66"}
CLASS_DEFAULT = "#7A7698"
RARITY_COLORS = {"uncommon": "#7BD88F", "rare": "#5B8DEF", "epic": "#B57BEF",
                 "legendary": "#F29A4A"}
ROW_H, ROW_GAP = 24, 2
# Chromium's line heights, in its layout units (1/64 px, rounded down):
# 1.3 times 13, 12, 11.5, 11, 10 and 9 px
LH13, LH12, LH115, LH11, LH9 = 1081 / 64, 998 / 64, 956 / 64, 915 / 64, 748 / 64
LH125 = 1040 / 64
TAB_H = LH12 + 6                     # a tab: its line, 3 px above and below
SET_H = LH12 + 4 + 20                # a setting: its label, the slider
CHIP_H = LH11 + 4 + 2
GRIP_MIN, GRIP_MAX = 30, 900
SCROLL_STEP = 40                    # a wheel notch, CSS pixels
TEXT_SH = (1, 2, 0.75)              # text-shadow: 0 1px 2px rgba(0,0,0,.75)


def _border_px(css, k):
    """A border's width as Chromium draws it: whole device pixels, at
    least one."""
    return max(1, math.floor(css * k)) / k


def _head(p, W, title, dpi_k, ui, hits, extra=None):
    """A panel's header (overlay.css .head, html.framed): the title in the
    game's font, centred; on the right `extra` (a dim text) then the gear.
    Its whole width drags the window."""
    k = p.k
    t = _title_image(title, dpi_k)
    if t:
        ink, cw, chh = t
        x0 = round((18 + (W - 36) / 2) * k - cw * dpi_k / 2)
        y0 = round((12 + 16) * k - chh * dpi_k / 2)
        p._put(ink.resize((round(cw * dpi_k), round(chh * dpi_k)), Image.BILINEAR)
               if abs(dpi_k - 1) > 0.01 else ink, x0, y0)
    else:
        p.text(title, 18, 12, 32, 15, 700, "#6A4E44", align="center", w=W - 36)
    gx, gy = W - 49, 16.5
    hover = ui.get("hover")
    on = bool(ui.get("settings"))
    if on:
        p.fill(gx, gy, 23, 23, OV_LINE, 0.18, r=6)
    elif hover == "gear":
        p.fill(gx, gy, 23, 23, OV_LINE, 0.12, r=6)
    _icon(p, "gear", gx + 3, gy + 3, 17, 17, OV_TEXT if on else OV_DIM,
          1.0 if on or hover == "gear" else 0.85)
    if extra:
        p.text(extra, 18, 12 + (32 - LH12) / 2, LH12, 12, 400, OV_DIM,
               align="right", w=gx - 6 - 18)
    hits.append((gx, gy, 23, 23, "gear", None))
    hits.append((18, 12, W - 36, 32, "drag", None))


def _settings(p, W, style, ui, hits, tr):
    """The gear's panel: opacity and size, each a slider, and back to the
    defaults (overlay.js settingsPanel). -> its bottom."""
    st = ui.get("style") or style
    y = 44.0
    h = 1 + 10 + SET_H + 10 + SET_H + 10 + CHIP_H + 12 + 1
    p.fill(8, y, W - 16, h, OV_BG)
    p.hline(8, y, W - 16, OV_LINE, 0.35)
    p.hline(8, y + h - 1, W - 16, OV_LINE, 0.35)
    sy = y + 1 + 10
    for key, label, lo, hi in (("opacity", tr("Opacité"), 30, 100),
                               ("scale", tr("Taille"), 90, 110)):
        v = int(st.get(key) or 100)
        p.text(label, 20, sy, LH12, 12, 400, "#FFF6E6")
        p.text(f"{v} %", 20, sy, LH12, 12, 700, "#FFF6E6", align="right", w=W - 40)
        tx, ty, tw = 22, sy + LH12 + 4, W - 44
        frac = (v - lo) / (hi - lo)
        # Chromium's range, accent-coloured: its track, the thumb
        mid = ty + 8
        p.fill(tx + 1, mid - 3, tw - 2, 6, "#3B3B3B", r=3)
        p.border(tx + 1, mid - 3, tw - 2, 6, "#767676", 0.6, r=3)
        p.fill(tx + 1, mid - 3, max(6, (tw - 2) * frac), 6, OV_ACCENT, r=3)
        cx = tx + 8 + (tw - 16) * frac
        p.fill(cx - 7, mid - 7, 14, 14, OV_ACCENT, r=7)
        hits.append((tx, ty, tw, 16, "slider", (key, lo, hi, tx + 8, tw - 16)))
        sy += SET_H + 10
    label = tr("Par défaut")
    cw = p.width(label, 11) + 18 + 2
    hov = ui.get("hover") == "reset"
    p.border(20, sy, cw, CHIP_H, (237, 216, 208) if not hov else "#6A4E44",
             0.4 if not hov else 1.0, r=12)
    p.text(label, 20, sy + 3, LH11, 11, 400, OV_TEXT if hov else "#EDD8D0",
           align="center", w=cw)
    hits.append((20, sy, cw, CHIP_H, "reset", None))
    return y + h


def _settings_height():
    return 44.0 + 1 + 10 + SET_H + 10 + SET_H + 10 + CHIP_H + 12 + 1


class MeterView:
    """The meter overlay (overlay.js renderMeter): the page's layout
    (overlay.css html.framed, measured in Edge), in CSS pixels."""

    MIN_W, MAX_W = 300, 380
    title = "Meter"

    def __init__(self, tr):
        self.tr = tr

    def layout_width(self, p, m):
        """#ov: inline-block, 300 to 380 wide, as wide as its longest name."""
        need = 0.0
        for r in m.get("rows") or []:
            wt = 700 if r.get("rank", 9) <= 3 and not r.get("zero") else 400
            need = max(need, p.width(f"{r.get('rank')}. {r.get('name')}", 12, wt))
        return max(self.MIN_W, min(self.MAX_W, math.ceil(218 + need)))

    def draw(self, k, dpi_k, m, style, ui):
        """m: the meter's state; style: {opacity, scale, maxh}; ui: the
        overlay's own (settings, hover, grip, scroll, tab).
        -> (Paint, hits [(x, y, w, h, kind, arg)], W, H)"""
        tr = self.tr
        m = m or {"rows": []}
        hits = []
        rows = m.get("rows") or []
        heal = (ui.get("tab") or m.get("tab")) == "heal"
        hover = ui.get("hover")
        if ui.get("settings"):
            W, H = self.MIN_W, _settings_height() + 22
            p = Paint(W, H, k)
            _frame(p, W, H)
            _head(p, W, tr(self.title), dpi_k, ui, hits)
            _settings(p, W, style, ui, hits, tr)
            return p, hits, W, H
        W = self.layout_width(Paint(1, 1, k), m)
        # the list: its rows' height, at most the player's (max-height),
        # or exactly the one being set with the grip
        gripping = ui.get("grip_h") is not None
        maxh = ui["grip_h"] if gripping else (style.get("maxh") or 0)
        sized = bool(style.get("maxh"))     # the fit button shown
        mline_h = 18 if sized else LH115
        tabs_y = 49 + mline_h + 5
        bar_bottom = tabs_y + TAB_H + 2
        content_h = 26 * len(rows) if rows else LH12 + 16
        list_h = maxh if gripping else (min(content_h, maxh) if maxh else content_h)
        body_h = 4 + 18 + list_h + 6 + 1
        H = bar_bottom + body_h + 22
        p = Paint(W, H, k)
        _frame(p, W, H)
        _head(p, W, tr(self.title), dpi_k, ui, hits)
        # -- the duration line and the tabs ---------------------------------
        p.fill(8, 44, W - 16, bar_bottom - 44, OV_BG)
        p.hline(8, 44, W - 16, OV_LINE, 0.35)
        hot = bool(m.get("fight"))
        clock = tr("Durée : {t}").replace("{t}", m.get("time") or "0:00")
        if hot:
            clock += " " + tr("(en cours)")
        elif m.get("held"):
            clock += " " + tr("(terminé)")
        clock_top = 49 + (18 - LH115) / 2 if sized else 49
        p.text(clock, 18, clock_top, LH115, 11.5, 700 if hot else 400,
               "#8A3F0E" if hot else "#4A3229")
        if sized:
            fx = W - 36
            p.fill(fx, 49, 20, 18, (255, 246, 230), 0.4 if hover == "fit" else 0.2, r=4)
            p.border(fx, 49, 20, 18, (74, 50, 41), 0.45, r=4)
            _icon(p, "fit", fx + 4.5, 49 + 2.5, 11, 13, OV_TEXT if hover == "fit" else "#4A3229")
            hits.append((fx, 49, 20, 18, "fit", None))
        tw = W - 32
        p.fill(16, tabs_y, tw, TAB_H + 2, (255, 246, 230), 0.18, r=4)
        half = (tw - 2) / 2
        # the shown tab's background (.mtabs::before), its outer corners
        # rounded by the tabs' clip, its inner side straight
        wx = 17 + (half if heal else 0)
        p.fill(wx, tabs_y + 1, half, TAB_H, OV_WELL, r=3)
        p.fill(wx if heal else wx + half - 3, tabs_y + 1, 3, TAB_H, OV_WELL)
        p.border(16, tabs_y, tw, TAB_H + 2, (58, 46, 39), 0.45, r=4)
        for i, (key, label) in enumerate((("dmg", tr("Dégâts")), ("heal", tr("Soins")))):
            sel = (key == "heal") == heal
            col = "#FFFFFF" if sel else (OV_TEXT if hover == "tab:" + key else "#4A3229")
            p.text(label, 17 + i * half, tabs_y + 1 + 3, LH12, 12, 600,
                   col, align="center", w=half)
            hits.append((17 + i * half, tabs_y + 1, half, TAB_H, "tab", key))
        # -- the list --------------------------------------------------------
        body_y = bar_bottom
        p.fill(8, body_y, W - 16, body_h, OV_BG)
        p.hline(8, body_y + body_h - 1, W - 16, OV_LINE, 0.35)
        th_y = body_y + 4
        list_y = th_y + 18
        scroll_w = 6 if (maxh and content_h > list_h) else 0
        rw = W - 28 - scroll_w
        cols = self._cols(W - 218 - scroll_w)
        # the titles' line
        heads = [tr("Joueur"), "HPS" if heal else "DPS", tr("Total"), tr("Part")]
        hl_top = th_y + (18 - LH9) / 2
        p.text(heads[0].upper(), cols[0][0], hl_top, LH9, 9, 700, "#FFFFFF", spacing=0.5)
        for (cx, cw_), label in zip(cols[1:], heads[1:]):
            p.text(label.upper(), cx, hl_top, LH9, 9, 700, OV_DIM, align="right",
                   w=cw_ - 0.5, spacing=0.5)
        if not rows:
            p.text(tr("En attente d’un combat…"), 20, list_y + 8, LH12, 12, 400, OV_DIM)
        else:
            scroll = max(0.0, min(ui.get("scroll", 0.0), content_h - list_h)) if maxh else 0.0
            ui["scroll"] = scroll
            # drawn where they stand on the panel (its pixels rounded as
            # there), then only the list's height of them kept
            lp = Paint(W, H, k)
            for i, r in enumerate(rows):
                ry = 2 + i * 26 - scroll
                if ry + ROW_H < 0 or ry > list_h:
                    continue
                self._row(lp, r, 14, list_y + ry, rw, cols)
                vis_y = max(0.0, ry)
                vis_h = min(list_h, ry + ROW_H) - vis_y
                if vis_h > 0:
                    hits.append((14, list_y + vis_y, rw, vis_h, "row", r.get("name")))
            _x0, y0, _pw, ph_ = p.box(0, list_y, W, list_h)
            p._put(lp.img.crop((0, y0, p.img.width, y0 + max(1, ph_))), 0, y0)
            if scroll_w:
                # its scrollbar (.mrows::-webkit-scrollbar)
                sx = 14 + rw
                p.fill(sx, list_y, 6, list_h, (58, 46, 39), 0.25, r=3)
                th = max(12.0, list_h * list_h / content_h)
                ty = list_y + (list_h - th) * (scroll / max(1e-6, content_h - list_h))
                p.fill(sx, ty, 6, th, OV_WELL, r=3)
                hits.append((14, list_y, W - 28, list_h, "list", None))
        if gripping:
            # the height being set: the list's edge outlined (.mrows.sizing)
            p.border(14, list_y, W - 28, list_h, (74, 50, 41), 0.6)
        hits.append((32, H - 24, W - 64, 16, "grip", list_h))
        return p, hits, W, H

    @staticmethod
    def _cols(name_w):
        """The rows' columns (x, w): the name, DPS, total, share."""
        x = 48.0
        out = [(x, name_w)]
        x += name_w + 6
        for w in (44, 54, 34):
            out.append((x, w))
            x += w + 6
        return out

    def _row(self, p, r, x, y, w, cols):
        """A player's line (overlay.css .rkr): its class's colour from 80 %
        to 55 % as far as its share, over the well, a frame of a lighter
        shade inside its edge, its icon, its name and numbers."""
        cc = CLASS_COLORS.get(r.get("ck"), CLASS_DEFAULT)
        f = max(0.0, min(1.0, float(r.get("f") or 0)))
        x0, y0, pw, ph = p.box(x, y, w, ROW_H)
        if pw > 0 and ph > 0:
            k = p.k
            layer = Image.new("RGBA", (pw, ph), _rgba(OV_WELL))
            fw = round(w * f * k)
            if fw > 0:
                c = _rgba(cc)[:3]
                grad = Image.new("RGBA", (fw, 1))
                grad.putdata([c + (round(255 * (0.8 - 0.25 * (i / max(1, fw - 1)))),)
                              for i in range(fw)])
                layer.alpha_composite(grad.resize((fw, ph)), (0, 0))
            layer.putalpha(ImageChops.multiply(layer.getchannel("A"), p._mask(pw, ph, 3 * k)))
            p._put(layer, x0, y0)
            p.ring(x, y, w, ROW_H, 1.5, _mix(cc, "#FFFFFF", 0.7), 1.0, r=3)
        zero = bool(r.get("zero"))
        op = 0.6 if zero else 1.0
        # the icon: 20 x 20 at 3 px into its column, a dark rounded square
        icon = _class_icon(r.get("ck"))
        if icon is not None:
            ix, iy = x + 2 + 3, y + 2
            p.fill(ix, iy, 20, 20, (0, 0, 0), 0.55 * op, r=5)
            p.ring(ix, iy, 20, 20, 1, (255, 255, 255), 0.08 * op, r=5)
            p.image(icon, ix + 2, iy + 2, 16, 16, r=3, opacity=op)
        top = y + (ROW_H - LH12) / 2
        bold = r.get("rank", 9) <= 3 and not zero
        nx, nw = cols[0]
        p.text(f"{r.get('rank')}. {r.get('name')}", x + nx - 14, top, LH12, 12,
               700 if bold else 400, "#FFF6E6", op, shadow=TEXT_SH, clip=nw)
        for (cx, cw_), v in zip(cols[1:], (r.get("rate"), r.get("total"), r.get("pct"))):
            p.text(str(v if v is not None else ""), x + cx - 14, top, LH12, 12, 400,
                   "#FFF6E6", op, align="right", w=cw_, shadow=TEXT_SH)


def _lock_icon(p, x, y, w, h, color, alpha=1.0):
    """The padlock (overlay.js lockIcon, viewBox 12 x 14): its shackle, its
    body."""
    x0, y0, pw, ph = p.box(x, y, w, h)
    if pw <= 0 or ph <= 0:
        return
    S = 8
    m = Image.new("L", (pw * S, ph * S), 0)
    d = ImageDraw.Draw(m)
    sx, sy = pw * S / 12, ph * S / 14
    lw = max(1, round(1.6 * sx))
    d.arc((3 * sx, 1 * sy, 9 * sx, 7 * sy), 180, 360, fill=255, width=lw)
    d.line((3 * sx, 4 * sy, 3 * sx, 6.5 * sy), fill=255, width=lw)
    d.line((9 * sx, 4 * sy, 9 * sx, 6.5 * sy), fill=255, width=lw)
    d.rounded_rectangle((1 * sx, 6 * sy, 11 * sx, 13.5 * sy), 1.5 * sx, fill=255)
    m = m.resize((pw, ph), Image.BOX)
    c = _rgba(color, alpha)
    layer = Image.new("RGBA", (pw, ph), c[:3] + (0,))
    layer.putalpha(m.point(lambda v: v * c[3] // 255))
    p._put(layer, x0, y0)


class LuckView:
    """The loot luck counters (overlay.js renderLuck)."""

    title = "Chance de butin"
    W = 300

    def __init__(self, tr):
        self.tr = tr

    def draw(self, k, dpi_k, data, style, ui):
        tr = self.tr
        W = self.W
        hits = []
        rows = (data or {}).get("rows")
        if ui.get("settings"):
            H = _settings_height() + 22
            p = Paint(W, H, k)
            _frame(p, W, H)
            _head(p, W, tr(self.title), dpi_k, ui, hits)
            _settings(p, W, style, ui, hits, tr)
            return p, hits, W, H
        content = 26 * len(rows) if rows else LH12 + 16
        body_h = 1 + 4 + content + 6 + 1
        H = 44 + body_h + 22
        p = Paint(W, H, k)
        _frame(p, W, H)
        _head(p, W, tr(self.title), dpi_k, ui, hits)
        p.fill(8, 44, W - 16, body_h, OV_BG)
        p.hline(8, 44, W - 16, OV_LINE, 0.35)
        p.hline(8, 44 + body_h - 1, W - 16, OV_LINE, 0.35)
        if not rows:
            p.text(tr("Lecture des compteurs…"), 20, 49 + 8, LH12, 12, 400, OV_DIM)
            return p, hits, W, H
        sh = [(1, 2, 0.9), (0, 3, 0.6)]
        for i, r in enumerate(rows):
            y = 49 + i * 26
            on, full = bool(r.get("on")), bool(r.get("full"))
            img = _data_image(r.get("img"))
            if img is not None:
                if not on:
                    img = _grayscale(img, 0.7)
                p.image(img, 18, y + 2, 22, 22, fit="contain",
                        opacity=1.0 if on else 0.7)
            bx, by, bw = 46, y + 3, W - 64
            p.fill(bx, by, bw, 20, OV_WELL, r=3)
            f = max(0.0, min(1.0, float(r.get("f") or 0)))
            if f > 0:
                col, a = (("#A9671A", 1) if full else ("#4C8A24", 1)) if on \
                    else ((176, 146, 112), 0.3)
                _clipped_fill(p, bx, by, bw * f, 20, col, a, bw, 3)
            p.ring(bx, by, bw, 20, 1, (0, 0, 0), 0.35, r=3)
            top = by + (20 - LH115) / 2
            v = "+" + str(r.get("bonus"))
            vw = p.width(v, 11.5, 700)
            right = bx + bw - 7
            lock_w = 0 if on else 9 + 4
            p.text(str(r.get("t") or ""), bx + 7, top, LH115, 11.5, 600, "#FFFFFF",
                   shadow=sh, clip=bw - 14 - 8 - vw - lock_w)
            p.text(v, right - vw, top, LH115, 11.5, 700, "#FFFFFF", shadow=sh)
            if not on:
                _lock_icon(p, right - vw - 4 - 9, by + (20 - 11) / 2, 9, 11, "#FFFFFF")
        if not any(r.get("on") for r in rows):
            self._veil(p, W, body_h, (data or {}).get("well"))
        return p, hits, W, H

    def _veil(self, p, W, body_h, well):
        """No offering at all: a veil over the counters, what unfreezes them
        (.lveil, .lvbox)."""
        tr = self.tr
        vy, vh = 45, body_h - 2
        p.fill(8, vy, W - 16, vh, OV_WELL, 0.45, r=3)
        bw = W - 16 - 28
        msg = tr("Fais une offrande au {well} pour les activer.").replace(
            "{well}", well or tr("Puits des âmes"))
        # (its box's width, a little more: Chromium's letters are narrower)
        lines = p.wrap(msg, 11.5, 400, bw - 28 - 2 + 3)
        bh = 8 + LH125 + 3 + LH115 * len(lines) + 8 + 2
        bx, by = 22, vy + (vh - bh) / 2
        _shadow_box(p, bx, by + 2, bw, bh, 10, 0.5, 5)
        p.fill(bx, by, bw, bh, (38, 28, 23), 0.94, r=5)
        p.border(bx, by, bw, bh, OV_ACCENT, r=5)
        t = tr("Bonus gelés").upper()
        tw = p.width(t, 12.5, 700, 0.75) + 11 + 6
        tx = bx + (bw - tw) / 2
        ty = by + 1 + 8
        _lock_icon(p, tx, ty + (LH125 - 13) / 2, 11, 13, OV_ACCENT)
        p.text(t, tx + 17, ty, LH125, 12.5, 700, "#FFFFFF", spacing=0.75)
        ly = ty + LH125 + 3
        for line in lines:
            p.text(line, bx, ly, LH115, 11.5, 400, "#EDD8D0", align="center", w=bw)
            ly += LH115


class GoalsView:
    """The goals (overlay.js renderGoals): each, its picture, its progress,
    its removal; the last line adds one."""

    title = "Objectifs"
    W = 300

    def __init__(self, tr):
        self.tr = tr

    def draw(self, k, dpi_k, g, style, ui):
        tr = self.tr
        W = self.W
        hits = []
        g = g or {"rows": []}
        rows = g.get("rows") or []
        done = sum(1 for r in rows if r.get("done"))
        extra = f"{done} / {len(rows)}" if rows else ""
        if ui.get("settings"):
            H = _settings_height() + 22
            p = Paint(W, H, k)
            _frame(p, W, H)
            _head(p, W, tr(self.title), dpi_k, ui, hits, extra)
            _settings(p, W, style, ui, hits, tr)
            return p, hits, W, H
        row_h = 4 + LH13 + 2 + LH11 + 4
        content = sum(row_h + (1 if i else 0) for i in range(len(rows)))
        add_h = LH12 + 14 + 2 * _border_px(1.5, k)
        note = bool(rows and any(not r.get("rk") for r in rows) and g.get("banks") == 0)
        body_h = 1 + 4 + content + 6 + add_h + 2 + (4 + LH11 if note else 0) + 6 + 1
        H = 44 + body_h + 22
        p = Paint(W, H, k)
        _frame(p, W, H)
        _head(p, W, tr(self.title), dpi_k, ui, hits, extra)
        p.fill(8, 44, W - 16, body_h, OV_BG)
        p.hline(8, 44, W - 16, OV_LINE, 0.35)
        p.hline(8, 44 + body_h - 1, W - 16, OV_LINE, 0.35)
        hover = ui.get("hover")
        y = 49.0
        for i, r in enumerate(rows):
            h = row_h + (1 if i else 0)
            if i % 2:
                p.fill(8, y, W - 16, h, (255, 246, 230), 0.1)
            if i:
                p.hline(8, y, W - 16, OV_LINE, 0.35)
            cy = y + (1 if i else 0)
            hits.append((8, y, W - 16, h, "goal", r.get("id")))
            self._goal(p, r, W, cy, hover)
            if hover in (("goal", r.get("id")), ("del", r.get("id"))):
                xx, xy = W - 36, cy + (row_h - 22) / 2
                cross = _piece("ui_close_x.png")
                col = OV_DIM if hover == ("del", r.get("id")) else "#886B56"
                if cross is not None:
                    p.mask_image(cross, xx, xy, 22, 22, col)
                else:
                    p.text("×", xx, xy, 22, 16, 400, col, align="center", w=22)
                hits.insert(0, (xx, xy, 22, 22, "del", r.get("id")))
            y += h
        # the last line: add one (the chooser in the middle of the game)
        ay = y + 6
        hov = hover == "add"
        p.fill(14, ay, W - 28, add_h, (255, 246, 230), 0.36 if hov else 0.22, r=6)
        p.dashed(14, ay, W - 28, add_h, _border_px(1.5, k),
                 "#4A3229" if hov else (74, 50, 41), 1.0 if hov else 0.55, r=6)
        label = tr("Ajouter un objectif")
        lw = p.width(label, 12, 600)
        pw_ = p.width("+", 15, 600)
        tx = 14 + (W - 28 - (pw_ + 6 + lw)) / 2
        ty = ay + _border_px(1.5, k) + 7
        p.text("+", tx, ty + (LH12 - 15) / 2, 15, 15, 600, "#4A3229")
        p.text(label, tx + pw_ + 6, ty, LH12, 12, 600, "#4A3229")
        hits.insert(0, (14, ay, W - 28, add_h, "add", None))
        if note:
            p.text(tr("Banque pas encore lue : ouvre-la une fois en jeu."), 20,
                   ay + add_h + 2 + 4, LH11, 11, 400, OV_DIM, clip=W - 40)
        return p, hits, W, H

    def _goal(self, p, r, W, y, hover):
        row_h = 4 + LH13 + 2 + LH11 + 4
        # its picture: the item's, framed in its rarity's colour
        px_, py_ = 14, y + (row_h - 28) / 2
        p.fill(px_, py_, 28, 28, OV_WELL, r=5)
        rk = r.get("rk")
        img = _data_image(r.get("img"))
        inset = 0
        if rk in RARITY_COLORS:
            p.ring(px_, py_, 28, 28, 2, RARITY_COLORS[rk], r=5)
            inset = 2
        if img is not None:
            p.image(img, px_ + inset, py_ + inset, 28 - 2 * inset, 28 - 2 * inset,
                    fit="contain", r=max(0, 5 - inset))
        else:
            p.text("✦" if rk else "?", px_, py_ + (28 - LH13) / 2, LH13, 13, 400,
                   OV_TEXT, align="center", w=28)
        gx, gw = 50, W - 94
        p.text(str(r.get("t") or ""), gx, y + 4, LH13, 13, 600, OV_TEXT, clip=gw)
        done = bool(r.get("done"))
        have, n = r.get("have") or 0, r.get("n") or 1
        count = (f"✓ {have} / {n}" if done else f"{have} / {n}")
        cw = p.width(count, 11, 700 if done else 400)
        gy = y + 4 + LH13 + 2
        p.text(count, gx + gw - cw, gy, LH11, 11, 700 if done else 400,
               "#2F5E10" if done else OV_DIM)
        bw = gw - cw - 6
        by = gy + (LH11 - 6) / 2
        p.fill(gx, by, bw, 6, OV_WELL, r=3)
        f = min(1.0, have / n) if n else 0
        if f > 0:
            _clipped_fill(p, gx, by, bw * f, 6, OV_OK if done else OV_ACCENT, 1, bw, 3)


# -- small drawing helpers used by the panels --------------------------------
_DATA_IMGS = {}


def _data_image(uri):
    """A data URI's picture (the app's items, skills...), or None; kept."""
    if not uri or not isinstance(uri, str) or not uri.startswith("data:"):
        return None
    key = hash(uri)
    if key not in _DATA_IMGS:
        import base64
        import io
        try:
            raw = base64.b64decode(uri.split(",", 1)[1])
            _DATA_IMGS[key] = Image.open(io.BytesIO(raw)).convert("RGBA")
        except Exception:
            _DATA_IMGS[key] = None
        if len(_DATA_IMGS) > 400:
            _DATA_IMGS.pop(next(iter(_DATA_IMGS)))
    return _DATA_IMGS[key]


def _grayscale(img, amount):
    """CSS filter: grayscale(amount)."""
    g = img.convert("LA").convert("RGBA")
    out = Image.blend(img, g, amount)
    out.putalpha(img.getchannel("A"))
    return out


def _clipped_fill(p, x, y, w, h, color, alpha, full_w, r):
    """A bar's fill (overflow: hidden in a box `full_w` wide, corners r):
    its left end rounded as the box, its right end straight."""
    x0, y0, pw, ph = p.box(x, y, w, h)
    _bx, _by, fw, _fh = p.box(x, y, full_w, h)
    if pw <= 0 or ph <= 0:
        return
    mask = p._mask(fw, ph, r * p.k).crop((0, 0, pw, ph))
    c = _rgba(color, alpha)
    layer = Image.new("RGBA", (pw, ph), c[:3] + (0,))
    layer.putalpha(mask.point(lambda v: v * c[3] // 255))
    p._put(layer, x0, y0)


def _shadow_box(p, x, y, w, h, blur, alpha, r):
    """box-shadow: 0 0 blur rgba(0,0,0,alpha) of a box (its offset in y)."""
    k = p.k
    pad = blur
    x0, y0, pw, ph = p.box(x - pad, y - pad, w + 2 * pad, h + 2 * pad)
    if pw <= 0 or ph <= 0:
        return
    m = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        (pad * k, pad * k, pw - pad * k, ph - pad * k), r * k, fill=round(255 * alpha))
    m = m.filter(ImageFilter.GaussianBlur(blur / 2 * k))
    layer = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    layer.putalpha(m)
    p._put(layer, x0, y0)


# ---------------------------------------------------------------------------
# The tip, the bonus dungeon, the menu's button
# ---------------------------------------------------------------------------
class TipView:
    """A player's details under the mouse (overlay.js renderTip)."""

    def __init__(self, tr):
        self.tr = tr

    def draw(self, k, t):
        tr = self.tr
        heal = bool(t.get("heal"))
        kind = (tr("Soins") if heal else tr("Dégâts")).upper()
        probe = Paint(1, 1, k)
        kw = probe.width(kind, 10, 700, 0.6) + 12
        name = str(t.get("name") or "")
        need = 20 + 6 + probe.width(name, 13, 700) + 6 + kw + 24
        stats = t.get("stats") or []
        skills = t.get("skills") or []
        W = max(230, min(300, math.ceil(need)))
        iw = W - 24
        stats_h = 0.0
        if stats:
            nrows = (len(stats) + 1) // 2
            stats_h = nrows * LH12 + (nrows - 1) * 3
        sk_h = (len(skills) * 22 + (len(skills) - 1) * 2) if skills else LH12 + 16
        H = 2 + 8 + 27 + 6 + stats_h + 7 + sk_h + 9 + 2
        p = Paint(W, H, k)
        p.fill(0, 0, W, H, (38, 28, 23), 0.96, r=5)
        p.ring(0, 0, W, H, 2, "#6A4E44", r=5)
        x, y = 12, 10
        cc = CLASS_COLORS.get(t.get("ck"), CLASS_DEFAULT)
        icon = _class_icon(t.get("ck"))
        if icon is not None:
            p.fill(x, y, 20, 20, (0, 0, 0), 0.55, r=5)
            p.image(icon, x + 2, y + 2, 16, 16, r=3)
            p.ring(x, y, 20, 20, 1, cc, r=5)
        p.text(name, x + 26, y + (20 - LH13) / 2, LH13, 13, 700, "#FFF6E6",
               clip=iw - 26 - 6 - kw)
        kx = x + iw - kw
        p.fill(kx, y + 2.5, kw, 15, "#3E9E66" if heal else "#E9765B", r=3)
        p.text(kind, kx, y + 3.5, 13, 10, 700, "#FFFFFF", align="center", w=kw, spacing=0.6)
        p.hline(x, y + 26, iw, (237, 216, 208), 0.2)
        y += 27 + 6
        cw = (iw - 12) / 2
        for i, (label, v) in enumerate(stats):
            sx = x + (i % 2) * (cw + 12)
            sy = y + (i // 2) * (LH12 + 3)
            p.text(str(label), sx, sy, LH12, 12, 400, "#CDB8AC")
            p.text(str(v), sx, sy, LH12, 12, 700, "#FFFFFF", align="right", w=cw)
        y += stats_h + 7
        if not skills:
            p.text(tr("Aucun soin pour l’instant.") if heal else tr("Aucun dégât pour l’instant."),
                   x + 6, y + 8, LH12, 12, 400, OV_DIM)
            return p, W, H
        for s in skills:
            f = max(0.0, min(1.0, float(s.get("f") or 0)))
            col, a = ((76, 138, 36), 0.45) if heal else ((233, 118, 91), 0.35)
            p.fill(x, y, iw, 22, (255, 255, 255), 0.05, r=3)
            if f > 0:
                _clipped_fill(p, x, y, iw * f, 22, col, a, iw, 3)
            p.fill(x + 2, y + 2, 18, 18, (0, 0, 0), 0.45, r=3)
            img = s.get("img_pil")
            if img is not None:
                p.image(img, x + 2, y + 2, 18, 18, r=3, fit="cover")
            top = y + (22 - LH12) / 2
            pct_x = x + iw - 5 - 34
            vw = p.width(str(s.get("v") or ""), 12)
            p.text(str(s.get("pct") or ""), pct_x, top, LH12, 12, 400, "#CDB8AC",
                   align="right", w=34)
            p.text(str(s.get("v") or ""), pct_x - 6 - vw, top, LH12, 12, 400, "#FFF6E6")
            p.text(str(s.get("t") or ""), x + 26, top, LH12, 12, 400, "#FFF6E6",
                   clip=pct_x - 6 - vw - 6 - (x + 26))
            y += 24
        return p, W, H


BONUS_INK = "#6B5A52"


def _outlined(p, im, x, y, w, h, r, ink):
    """A picture with a dark outline r pixels wide around it (overlay.js
    outlinedInk: its silhouette drawn 16 times around, then it)."""
    k = p.k
    x0, y0, pw, ph = p.box(x, y, w, h)
    if pw <= 0 or ph <= 0:
        return
    pad = math.ceil(r * k) + 1
    src = im.convert("RGBA").resize((pw, ph), Image.LANCZOS)
    sil = Image.new("RGBA", (pw, ph), _rgba(ink))
    sil.putalpha(src.getchannel("A"))
    layer = Image.new("RGBA", (pw + 2 * pad, ph + 2 * pad), (0, 0, 0, 0))
    for a in range(16):
        t = a / 16 * math.pi * 2
        dx, dy = math.cos(t) * r * k, math.sin(t) * r * k
        sh = sil.transform(sil.size, Image.AFFINE, (1, 0, -(dx % 1), 0, 1, -(dy % 1)),
                           Image.BILINEAR)
        layer.alpha_composite(sh, (pad + math.floor(dx), pad + math.floor(dy)))
    layer.alpha_composite(src, (pad, pad))
    p._put(layer, x0 - pad, y0 - pad)


class BonusView:
    """The bonus heroic dungeon (overlay.js renderBonus): the boss's
    portrait, round, the dungeon's name outlined, the heroic mark with the
    game's green plus. Only these show: the rest of the window is clear."""

    def draw(self, k, b, art):
        portrait, heroic, plus = art
        font_px = 17
        pad, face, mark, gap, line, ring = 4, 34, 34, 8, 1, 0.5
        text = str(b.get("t") or "")
        probe = Paint(1, 1, k)
        tw = math.ceil(probe.width(text, font_px, 700))
        W = pad * 2 + face + gap + tw + gap + mark + line
        H = pad * 2 + face
        p = Paint(W, H, k)
        if portrait is not None:
            p.fill(pad, pad, face, face, BONUS_INK, r=face / 2)
            p.image(portrait, pad + ring, pad + ring, face - 2 * ring, face - 2 * ring,
                    r=face / 2 - ring)
        tx = pad + face + gap
        p.text(text, tx, 0, H + 2, font_px, 700, "#FFFFFF", stroke=(line, BONUS_INK))
        mx, my = tx + tw + gap, (H - mark) / 2
        if heroic is not None:
            mh = mark * heroic.height / max(1, heroic.width)
            _outlined(p, heroic, mx, my, mark, mh, ring, BONUS_INK)
        if plus is not None:
            ps = 16
            g = plus.convert("RGBA").resize((64, 64), Image.LANCZOS)
            grad = Image.new("RGBA", (1, 64))
            top, bot = _rgba("#9BE04E"), _rgba("#4E9F27")
            grad.putdata([tuple(round(top[c] + (bot[c] - top[c]) * i / 63) for c in range(3)) + (255,)
                          for i in range(64)])
            grad = grad.resize((64, 64))
            grad.putalpha(g.getchannel("A"))
            _outlined(p, grad, mx + mark - ps + 1, my + mark - ps - 5, ps, ps, 1, "#2A3A1C")
        return p, W, H


# ---------------------------------------------------------------------------
# The windows
# ---------------------------------------------------------------------------
class _NativeWindow:
    """A window of ours drawn from an image: made on the UI thread, placed,
    shown and hidden from any."""

    id = "?"

    def _make(self):
        self.ui = _UiThread.get()
        self.hwnd = self.ui.run(lambda: self.ui.create(self, f"Farever Book — {self.id}"),
                                wait=True) or 0
        if not self.hwnd:
            _log(f"overlay {self.id}: no native window")
        return self.hwnd

    def _dpi_k(self):
        try:
            return (_U32.GetDpiForWindow(self.hwnd) or 96) / 96
        except Exception:
            return 1.0

    def _present(self, at=None):
        if self._img is not None:
            _present(self.hwnd, self._img, at or self._at, round(255 * self.opacity / 100))

    def _set_ex(self, transparent):
        ex = _U32.GetWindowLongW(self.hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        want = ex | WS_EX_LAYERED
        want = want | WS_EX_TRANSPARENT if transparent else want & ~WS_EX_TRANSPARENT
        if want != ex:
            _U32.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                                ctypes.c_long(want - (1 << 32) if want & 0x80000000 else want))

    def _topmost(self):
        _U32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                          SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def redraw(self):
        if self.hwnd:
            self.ui.run(self._redraw)

    # the mouse: nothing by default
    _tracking = False
    _cursor_kind = "arrow"

    def cursor(self):
        """The game's own cursors (its arrow, its hand on what clicks), the
        system's for the grip."""
        kind = self._cursor_kind
        if kind == "resize":
            return _U32.LoadCursorW(None, ctypes.c_void_p(IDC_SIZENS))
        name = "button" if kind == "hand" else "default"
        key = (name, round(self._dpi_k() * 100))
        cache = _NativeWindow.__dict__.get("_cursors")
        if cache is None:
            cache = {}
            _NativeWindow._cursors = cache
        if key not in cache:
            cache[key] = _cursor_from_png(_analysis() / f"ui_cursor_{name}.png", self._dpi_k())
        return cache[key] or _U32.LoadCursorW(
            None, ctypes.c_void_p(IDC_HAND if kind == "hand" else IDC_ARROW))

    def on_mouse(self, ev, x, y):
        pass

    def on_wheel(self, delta, x, y):
        pass


class NativeOverlay(_NativeWindow):
    """An overlay over the game in a window of our own, with the methods
    menu_host calls on its overlays (Overlay's): update, set_locked,
    set_free_hidden, keep_styles, set_theme, set_lang, attach. Subclasses
    draw it (view) and add what their places do (_click)."""

    VIEW = None
    KEYED = False                       # shown only with something to say

    def __init__(self, oid, pipe, lang, catalog, tip_ref=None, picker_ref=None):
        self.id = oid
        self.pipe = pipe
        self.window = None                  # no page: menu_host skips it
        self.hwnd = 0
        self.size = (300, 248)
        self.shown = False
        self.dragging = False
        self.typing = None
        self.game = None
        self.client = None
        self.front = False
        self.pos = None
        self.lock_mode = "auto"
        self.locked = False
        self.hide_free = False
        self.free_hidden = False
        self.wanted = False
        self.opacity = 100
        self.scale = 100
        self._tip = tip_ref                 # menu_host.TIP
        self._picker = picker_ref           # menu_host.PICKER
        self._catalog = catalog
        self._dict = catalog(lang) if catalog else {}
        self._data = None
        self._style = {"opacity": 100, "scale": 100, "maxh": 0}
        self._ui = {"settings": False, "hover": None, "scroll": 0.0}
        self._hits = []
        self._k = 1.0
        self._at = None                     # where the window is (screen)
        self._img = None
        self._press = None                  # what the button went down on
        self._last_send = 0.0
        self._tip_row = None

    # -- menu_host's side ---------------------------------------------------
    def tr(self, s):
        return self._dict.get(s) or s

    def attach(self):
        if self._make():
            _log(f"overlay {self.id}: drawn natively (no WebView2)")
            self.redraw()

    def set_lang(self, lang):
        if self._catalog:
            self._dict = self._catalog(lang)
        self.redraw()

    def set_theme(self, theme):
        """The overlays keep the game's colours."""

    def keep_styles(self):
        """Our own window: nothing rewrites its styles (WinForms did the
        WebView2 ones'); only the click-through follows the lock."""
        if self.hwnd:
            self._set_ex(self.locked)

    def set_locked(self, on):
        on = bool(on) and not self.dragging and self._press is None
        if not self.hwnd or on == self.locked:
            return
        self.locked = on
        self.keep_styles()

    def set_free_hidden(self, on):
        on = bool(on) and not self.dragging
        if not self.hwnd or on == self.free_hidden:
            return
        self.free_hidden = on
        _log(f"overlay {self.id}: {'hidden (a game window or a loading screen)' if on else 'back'}"
             f" (shown={self.shown}, wanted={self.wanted})")
        if on and self.shown:
            self._hide()
        elif not on and self.wanted and not self.shown:
            self._show()

    def update(self, d):
        if not self.hwnd:
            return
        data = d.get(self.id)
        style = (d.get("style") or {}).get(self.id) or {}
        self.game = d.get("game")
        self.client = d.get("client")
        self.front = bool(d.get("gameFront"))
        self.pos = (d.get("pos") or {}).get(self.id)
        self.lock_mode = d.get("lock") or "auto"
        self.hide_free = bool(d.get("hideFree"))
        on = (d.get("on") or {}).get(self.id, True)
        changed = False
        if d.get("show") and data != self._data:
            self._data = data
            self._pushed(data)
            changed = True
        # the settings open or the grip held: the style being chosen is ours
        if style and style != self._style and not self._ui.get("settings") \
                and self._ui.get("grip_h") is None:
            self._style = dict(style)
            changed = True
        op = max(30, min(100, int(self._style.get("opacity") or 100)))
        sc = max(90, min(110, int(self._style.get("scale") or 100)))
        if op != self.opacity or sc != self.scale:
            self.opacity, self.scale = op, sc
            changed = True
        self.wanted = bool(d.get("show") and on and self.game
                           and (self._data is not None or not self.KEYED))
        if changed:
            self.redraw()
            # the row under the mouse: its tip kept fresh
            if self._tip_row is not None:
                self._show_tip(self._tip_row)
        if self.wanted and not self.free_hidden:
            if not self.dragging:
                self.ui.run(self._place)
            if not self.shown:
                self._show()
        elif self.shown and self.typing is None:
            self._hide()

    def _pushed(self, data):
        """A new state from the app (the meter's tab caught up...)."""

    def _show(self):
        self.shown = True

        def go():
            self._place()
            _U32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self.ui.run(go)

    def _hide(self):
        self.shown = False
        self.ui.run(lambda: _U32.ShowWindow(self.hwnd, SW_HIDE))
        self._hide_tip()

    # -- drawing (the UI thread) ----------------------------------------------
    def _redraw(self):
        dpi_k = self._dpi_k()
        k = dpi_k * self.scale / 100
        self._k = k
        ui = dict(self._ui)
        p, hits, _w, _h = self.VIEW(self.tr).draw(k, dpi_k, self._data, self._style, ui)
        self._ui["scroll"] = ui.get("scroll", 0.0)
        self._hits = hits
        self._img = p.img
        if p.img.size != self.size:
            self.size = p.img.size
            if self.game and not self.dragging:
                self._place(present=False)
        self._present()

    def _default_pos(self, gw, gh, w, h):
        return {"ax": "r", "dx": 24, "ay": "t", "dy": gh // 3}

    def _place(self, present=True):
        """Anchored to a side of the game's window (the meter's place, or
        the overlay's default), inside it."""
        if not self.game or not self.hwnd:
            return
        gx, gy, gw, gh = self.game
        w, h = self.size
        a = self.pos or self._default_pos(gw, gh, w, h)
        x = gx + a["dx"] if a["ax"] == "l" else gx + gw - w - a["dx"]
        y = gy + a["dy"] if a["ay"] == "t" else gy + gh - h - a["dy"]
        x = max(gx, min(x, gx + gw - w))
        y = max(gy, min(y, gy + gh - min(h, 60)))
        at = (int(x), int(y))
        if at != self._at:
            self._at = at
            if present:
                self._present()
        self._topmost()

    # -- the mouse (the UI thread) --------------------------------------------
    HAND = ("gear", "tab", "fit", "slider", "reset", "add", "del")

    def _hit(self, x, y):
        cx, cy = x / self._k, y / self._k
        for hx, hy, hw, hh, kind, arg in self._hits:
            if hx <= cx < hx + hw and hy <= cy < hy + hh:
                return kind, arg, (hx, hy, hw, hh)
        return None, None, None

    def on_mouse(self, ev, x, y):
        if ev == "move":
            if self._press is not None:
                self._drag_move(x, y)
                return
            kind, arg, _r = self._hit(x, y)
            self._cursor_kind = ("resize" if kind == "grip" else
                                 "hand" if kind in self.HAND else "arrow")
            hover = ((kind, arg) if kind in ("row", "goal", "del") else
                     "tab:" + arg if kind == "tab" else kind)
            if hover != self._ui.get("hover"):
                self._ui["hover"] = hover
                self._redraw()
            row = arg if kind == "row" else None
            if row != self._tip_row:
                self._tip_row = row
                if row is None:
                    self._hide_tip()
                else:
                    self._show_tip(row)
            return
        if ev == "leave":
            if self._press is None and self._ui.get("hover") is not None:
                self._ui["hover"] = None
                self._redraw()
            if self._tip_row is not None:
                self._tip_row = None
                self._hide_tip()
            return
        if ev == "down":
            kind, arg, rect = self._hit(x, y)
            self._press = {"kind": kind, "arg": arg, "rect": rect, "x": x, "y": y,
                           "screen": _screen_pt()}
            if kind == "drag":
                self.dragging = True
                self._press["at"] = self._at or (0, 0)
            elif kind == "grip":
                self._press["h"] = arg
            elif kind == "slider":
                self._slide(arg, x, final=False)
            return
        if ev in ("up", "cancel"):
            press, self._press = self._press, None
            if not press:
                return
            kind, arg = press["kind"], press["arg"]
            if kind == "drag":
                self.dragging = False
                if ev == "up" and self.game and self._at:
                    w, h = self.size
                    self.pipe.send({"t": "call", "id": 0, "m": "ov_moved",
                                    "p": {"id": self.id,
                                          "rect": [self._at[0], self._at[1], w, h],
                                          "game": list(self.game)}})
                return
            if kind == "grip":
                v = self._ui.pop("grip_h", None)
                if ev == "up" and v is not None:
                    self._style["maxh"] = v
                    self._send_style(save=True)
                self._redraw()
                return
            if kind == "slider":
                self._slide(arg, x, final=True)
                return
            if ev != "up":
                return
            k2, a2, _r = self._hit(x, y)
            if (k2, a2) != (kind, arg):
                return                      # let go elsewhere: no click
            self._click(kind, arg)

    def _drag_move(self, x, y):
        pr = self._press
        if pr["kind"] == "drag":
            sx, sy = _screen_pt()
            self._at = (pr["at"][0] + sx - pr["screen"][0], pr["at"][1] + sy - pr["screen"][1])
            self._present()
        elif pr["kind"] == "grip":
            _sx, sy = _screen_pt()
            v = round(pr["h"] + (sy - pr["screen"][1]) / self._k)
            v = max(max(GRIP_MIN, ROW_H + 4), min(GRIP_MAX, v))
            if v != self._ui.get("grip_h"):
                self._ui["grip_h"] = v
                self._redraw()
        elif pr["kind"] == "slider":
            self._slide(pr["arg"], x, final=False)

    def _slide(self, arg, x, final):
        key, lo, hi, x0, w = arg
        v = lo + (x / self._k - x0) / max(1, w) * (hi - lo)
        v = int(round(max(lo, min(hi, v)) / 5) * 5)
        st = self._ui.setdefault("style", dict(self._style))
        if st.get(key) != v or final:
            st[key] = v
            self._style = dict(st)
            if key == "opacity":
                self.opacity = max(30, min(100, v))
            else:
                self.scale = max(90, min(110, v))
            self._redraw()
            now = time.monotonic()
            if final or now - self._last_send > 0.06:
                self._last_send = now
                self._send_style(save=final)

    def _send_style(self, save):
        st = self._style
        self.pipe.send({"t": "call", "id": 0, "m": "ov_style",
                        "p": {"id": self.id, "opacity": st.get("opacity", 100),
                              "scale": st.get("scale", 100), "maxh": st.get("maxh", 0),
                              "save": save}})

    def _notify(self, method, params=None):
        self.pipe.send({"t": "call", "id": 0, "m": method, "p": params or {}})

    def _click(self, kind, arg):
        if kind == "gear":
            self._ui["settings"] = not self._ui.get("settings")
            if self._ui["settings"]:
                self._ui["style"] = dict(self._style)
            else:
                self._ui.pop("style", None)
            self._hide_tip()
            self._redraw()
        elif kind == "reset":
            self._style.update(opacity=100, scale=100)
            self._ui["style"] = dict(self._style)
            self.opacity, self.scale = 100, 100
            self._send_style(save=True)
            self._redraw()

    # -- the tip (menu_host.TIP): talked to off this thread ---------------------
    def _show_tip(self, name):
        tip = self._tip[0] if self._tip else None
        rows = (self._data or {}).get("rows") or []
        row = next((r for r in rows if r.get("name") == name), None)
        if tip is None or row is None or not row.get("tip"):
            return
        _off_thread(lambda: tip.show(self, row["tip"]), f"overlay {self.id}: tip")

    def _hide_tip(self):
        tip = self._tip[0] if self._tip else None
        if tip is not None and getattr(tip, "owner", None) is self:
            _off_thread(tip.hide, f"overlay {self.id}: tip")


def _off_thread(fn, what):
    def go():
        try:
            fn()
        except Exception as e:
            _log(f"{what} failed: {e!r}")
    threading.Thread(target=go, daemon=True).start()


def _screen_pt():
    pt = wintypes.POINT()
    _U32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


class NativeMeter(NativeOverlay):
    VIEW = MeterView

    def _pushed(self, data):
        if data and self._ui.get("tab") == (data.get("tab") or "dmg"):
            self._ui.pop("tab", None)       # the push caught up

    def _click(self, kind, arg):
        if kind == "tab":
            if (self._ui.get("tab") or (self._data or {}).get("tab") or "dmg") != arg:
                self._ui["tab"] = arg            # at once, the push follows
                self._redraw()
                self._notify("ov_tab", {"tab": arg})
        elif kind == "fit":
            self._style["maxh"] = 0
            self._send_style(save=True)
            self._redraw()
        else:
            super()._click(kind, arg)

    def on_wheel(self, delta, x, y):
        kind, _a, _r = self._hit(x, y)
        if kind not in ("list", "row"):
            return
        self._ui["scroll"] = max(0.0, self._ui.get("scroll", 0.0) - delta / 120 * SCROLL_STEP)
        self._redraw()


class NativeLuck(NativeOverlay):
    VIEW = LuckView

    def _default_pos(self, gw, gh, w, h):
        return {"ax": "l", "dx": 24, "ay": "b", "dy": gh // 5}       # low left


class NativeGoals(NativeOverlay):
    VIEW = GoalsView

    def _default_pos(self, gw, gh, w, h):
        return {"ax": "l", "dx": 24, "ay": "t", "dy": gh // 3}

    def _click(self, kind, arg):
        if kind == "del":
            self._notify("goal_del", {"id": arg})
        elif kind == "add":
            picker = self._picker[0] if self._picker else None
            if picker is not None:
                _off_thread(lambda: picker.open(self), "goal chooser")
        else:
            super()._click(kind, arg)


class NativeBonus(NativeOverlay):
    """The bonus heroic dungeon: a text alone over the game, held to move."""

    KEYED = True

    def __init__(self, oid, pipe, lang, catalog, art):
        super().__init__(oid, pipe, lang, catalog)
        self._art_fn = art                  # () -> (portraits, heroic uri, plus path)
        self._art = None

    def _default_pos(self, gw, gh, w, h):
        return {"ax": "l", "dx": max(0, (gw - w) // 2), "ay": "t", "dy": 70}

    def _art_for(self, boss):
        if self._art is None:
            try:
                self._art = self._art_fn()
            except Exception as e:
                _log(f"overlay {self.id}: art unavailable: {e!r}")
                self._art = ({}, None, None)
        portraits, heroic, plus = self._art
        try:
            plus_img = Image.open(plus).convert("RGBA") if plus else None
        except OSError:
            plus_img = None
        return (_data_image(portraits.get(boss)), _data_image(heroic), plus_img)

    def _redraw(self):
        if not self._data:
            return
        dpi_k = self._dpi_k()
        k = dpi_k * self.scale / 100
        self._k = k
        p, W, H = BonusView().draw(k, self._data, self._art_for(self._data.get("boss")))
        self._hits = [(0, 0, W, H, "drag", None)]
        self._img = p.img
        if p.img.size != self.size:
            self.size = p.img.size
            if self.game and not self.dragging:
                self._place(present=False)
        self._present()


class NativeTip(_NativeWindow):
    """A player's details, under the mouse (the meter's rows): never
    focused, the clicks going through it; it follows the mouse
    (MenuHost._lock_loop) inside the game's window."""

    GAP = 18

    def __init__(self, pipe, lang, catalog, skill_dir):
        self.id = "tip"
        self.pipe = pipe
        self.window = None
        self.hwnd = 0
        self.size = (230, 140)
        self.shown = False
        self.owner = None
        self.game = None
        self.at = None
        self.opacity = 100
        self._catalog = catalog
        self._dict = catalog(lang) if catalog else {}
        self._skill_dir = skill_dir
        self._img = None
        self._at = None
        self._data = None

    def tr(self, s):
        return self._dict.get(s) or s

    def attach(self):
        if self._make():
            self.ui.run(lambda: self._set_ex(True))

    def set_lang(self, lang):
        if self._catalog:
            self._dict = self._catalog(lang)

    def set_theme(self, theme):
        pass

    def update(self, d):
        pass

    def _skill(self, ic):
        ic = str(ic or "")
        if not ic or not ic.replace("_", "").isalnum():
            return None
        key = "skill:" + ic
        if key not in _PIECES:
            try:
                _PIECES[key] = Image.open(self._skill_dir() / f"{ic}.webp").convert("RGBA")
            except (OSError, ValueError):
                _PIECES[key] = None
        return _PIECES[key]

    def show(self, owner, data):
        self.owner = owner
        self.game = owner.game
        self._data = dict(data, skills=[dict(s, img_pil=self._skill(s.get("ic")))
                                        for s in data.get("skills") or ()])
        k = getattr(owner, "_k", 1.0)

        def go():
            p, _w, _h = TipView(self.tr).draw(k, self._data)
            self._img = p.img
            self.size = p.img.size
            self.at = None
            self.follow()
            if not self.shown:
                _U32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
                self.shown = True
        if self.hwnd:
            self.ui.run(go)

    def hide(self):
        if self.shown and self.hwnd:
            self.ui.run(lambda: _U32.ShowWindow(self.hwnd, SW_HIDE))
        self.shown = False
        self.owner = None

    def follow(self):
        """Beside the mouse, flipped to its other side near the game's
        window's edges."""
        if not self.hwnd or self._img is None:
            return
        px, py = _screen_pt()
        w, h = self.size
        x, y = px + self.GAP, py + self.GAP
        if self.game:
            gx, gy, gw, gh = self.game
            if x + w > gx + gw:
                x = px - self.GAP - w
            if y + h > gy + gh:
                y = py - self.GAP - h
        if (x, y, w, h) == self.at:
            return
        self.at = (x, y, w, h)
        self._at = (int(x), int(y))
        self.ui.run(lambda: (self._present(), self._topmost()))


class NativeEsc(_NativeWindow):
    """A button over the game's own menu (Échap): the app brought forward on
    its overlays' settings; centred above the menu the hook reads."""

    GAP = 10

    def __init__(self, pipe, lang, catalog):
        self.id = "esc"
        self.pipe = pipe
        self.window = None
        self.hwnd = 0
        self.size = (210, 38)
        self.shown = False
        self.at = None
        self.opacity = 100
        self._catalog = catalog
        self._dict = catalog(lang) if catalog else {}
        self._img = None
        self._at = None
        self._hover = False
        self._k = 1.0

    def tr(self, s):
        return self._dict.get(s) or s

    def attach(self):
        if self._make():
            self.redraw()

    def set_lang(self, lang):
        if self._catalog:
            self._dict = self._catalog(lang)
        self.redraw()

    def set_theme(self, theme):
        pass

    def update(self, d):
        pass

    def _redraw(self):
        k = self._dpi_k()
        self._k = k
        label = self.tr("Farever Book - Overlay")
        probe = Paint(1, 1, k)
        W = probe.width(label, 15, 600) + 56
        H = LH15 + 18
        p = Paint(W, H, k)
        p.fill(0, 0, W, H, "#8A5F46" if self._hover else "#B1978D", r=6)
        p.text(label, 28, 9, LH15, 15, 600, "#FFFFFF")
        self._img = p.img
        self.size = p.img.size
        self._present()

    def follow(self, origin, menu):
        """Over the menu (the game's pixels from its drawing area's corner,
        `origin` on the screen), hidden without one."""
        if not self.hwnd:
            return
        if not origin or not menu or len(menu) != 4 or menu[2] <= 0:
            if self.shown:
                self.ui.run(lambda: _U32.ShowWindow(self.hwnd, SW_HIDE))
                self.shown = False
            return
        gx, gy = origin[0], origin[1]
        mx, my, mw, _mh = menu
        w, h = self.size
        x = int(gx + mx + (mw - w) / 2)
        y = int(gy + my - h - self.GAP)
        if (x, y, w, h) != self.at:
            self.at = (x, y, w, h)
            self._at = (x, y)
            self.ui.run(lambda: (self._present(), self._topmost()))
        if not self.shown:
            _log(f"menu button: game menu at {menu}, origin {origin}, button at {self.at}")
            self.ui.run(lambda: _U32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE))
            self.shown = True

    def on_mouse(self, ev, x, y):
        self._cursor_kind = "hand"
        if ev == "move" and not self._hover:
            self._hover = True
            self._redraw()
        elif ev == "leave" and self._hover:
            self._hover = False
            self._redraw()
        elif ev == "up":
            self.pipe.send({"t": "call", "id": 0, "m": "esc_open", "p": {}})


LH15 = 1248 / 64                    # 1.3 times 15 px
