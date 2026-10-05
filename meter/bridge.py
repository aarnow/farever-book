"""The window's process and the pipe to it (menu_host.py)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from common import (ANALYSIS, CREATE_NO_WINDOW, FROZEN, LOG_FILE, MENU_FLAG,
                    message_box)
from i18n import tr


# ---------------------------------------------------------------------------
# The window process
# ---------------------------------------------------------------------------
class MenuBridge:
    """The meter's half of the link to the WebView2 window.

    The window runs in its own process (see menu_host.py) and speaks
    line-JSON over stdin/stdout. This class starts it, pushes state to it,
    turns its messages into actions on the app's loop and notices when it
    dies. Every failure ends in "no window", never an exception reaching the
    refresh loop.
    """

    def __init__(self, app):
        self.app = app
        self.proc = None
        self.ready = False
        self.geom = {}                  # last reported x/y/w/h
        self.geom_at = 0.0              # when it last changed; see _handle
        self._last_push = None          # the spec we last sent, to skip repeats
        self._lock = threading.Lock()
        self._failed = False            # give up after one failure to start

    # -- lifecycle --------------------------------------------------------
    def start(self, geom=None, theme=None, lang=None):
        """Spawn the window process, hidden, at `geom` in `theme` and
        `lang`. Called once."""
        if self.proc is not None or self._failed:
            return
        self.geom = dict(geom or {})
        arg = json.dumps(dict(self.geom, theme=theme, lang=lang))
        cmd = ([sys.executable, MENU_FLAG, arg] if FROZEN
               else [sys.executable, str(Path(__file__).resolve().parent
                                         / "menu_host.py"), arg])
        # Its log goes to our stderr; frozen there may be none, and inheriting
        # the absent handle fails its first write with Errno 22.
        try:
            err = sys.stderr if sys.stderr and sys.stderr.fileno() >= 0 else None
        except (OSError, ValueError, AttributeError):
            err = None
        if FROZEN and err is None:
            err = subprocess.DEVNULL
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                # where the window finds the boss portraits
                env=dict(os.environ, FAREVER_ANALYSIS=str(ANALYSIS)),
                stderr=err,
                text=True, encoding="utf-8", bufsize=1,
                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except OSError as e:
            print(f"[meter] settings panel wouldn't start: {e}",
                  file=sys.stderr)
            self._failed = True
            return
        threading.Thread(target=self._read, daemon=True).start()
        print("[meter] settings panel started", file=sys.stderr)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def pid(self):
        """The window's process id, or None (the focus test counts it as
        'us')."""
        return self.proc.pid if self.alive() else None

    def stop(self):
        if not self.alive():
            return
        self.send({"t": "quit"})
        try:
            self.proc.wait(timeout=2)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None
        self.ready = False

    # -- sending ----------------------------------------------------------
    def send(self, obj):
        if not self.alive():
            return
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        with self._lock:
            try:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass                    # it died; _read notices

    def show(self):
        self.send({"t": "show"})

    def push(self, spec):
        """Send the window its state, if it has changed (called every tick)."""
        if not self.ready:
            return
        if spec == self._last_push:
            return
        self._last_push = spec
        self.send({"t": "state", "d": spec})

    def push_overlay(self, spec):
        """The overlays' state, when it changed (their own channel)."""
        if not self.ready or spec == getattr(self, "_last_ov", None):
            return
        self._last_ov = spec
        self.send({"t": "ov", "d": spec})

    def invalidate(self):
        """Force the next push through even if it matches (window just ready,
        or after an action so a click redraws at once)."""
        self._last_push = None

    def dirty(self):
        """True if a push is owed: the app skips building the spec on the
        ticks in between."""
        return self._last_push is None

    # -- receiving --------------------------------------------------------
    def _read(self):
        """Reader thread for the window's lifetime; actions go to the app's
        loop."""
        proc = self.proc
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                self._handle(msg)
        except (OSError, ValueError):
            pass
        # stdout closed: the window has gone
        print("[meter] settings panel closed", file=sys.stderr)
        if not self.ready and self.proc is proc:
            # died before showing: say so rather than sit silent in the tray
            self._failed = True
            code = proc.poll()
            print(f"[meter] the window never opened (exit code {code})",
                  file=sys.stderr)
            message_box(
                tr("La fenêtre de Farever Book n'a pas pu s'ouvrir.\n\n"
                   "Cause la plus courante : Microsoft Edge WebView2 n'est "
                   "pas installé "
                   "(https://developer.microsoft.com/microsoft-edge/webview2/)"
                   ".\n\nLe détail est dans :\n{log}", log=LOG_FILE),
                tr("Farever Book — fenêtre impossible à ouvrir"), 0x10)
        self.ready = False
        if self.proc is proc:
            self.proc = None

    def _handle(self, msg):
        t = msg.get("t")
        if t == "ready":
            self.ready = True
            self.invalidate()
        elif t == "geom":
            # no lock: a torn read of four ints is harmless
            got = {k: msg.get(k) for k in ("x", "y", "w", "h")}
            if got != self.geom:
                self.geom = got
                # stamped, not saved: this fires on every drag step; the app
                # saves once the gesture settles
                self.geom_at = time.monotonic()
        elif t == "call":
            self._dispatch(msg)
        elif t == "closed":
            self.app._enqueue(self.app._panel_closed)()

    def _dispatch(self, msg):
        method, params = msg.get("m"), msg.get("p") or {}
        cid = msg.get("id") or 0
        fn = self.app._menu_actions().get(method)
        if fn is None:
            print(f"[meter] panel asked for unknown action {method!r}",
                  file=sys.stderr)
            if cid:
                self.send({"t": "ret", "id": cid, "r": None})
            return

        def run():
            result = None
            try:
                result = fn(params) if _wants_params(fn) else fn()
            except Exception as e:
                print(f"[meter] panel action {method!r} failed: {e!r}",
                      file=sys.stderr)
            # any action may change what the window shows
            self.invalidate()
            if cid:
                self.send({"t": "ret", "id": cid, "r": result})

        self.app._enqueue(run)()


def _parse_help(text):
    """Turn one help article into (title, blurb, spec blocks).

    A deliberately small markdown subset:

        # Title          the article's name (first one wins)
        > blurb          the one-liner on the index
        ## Heading       a section rule
        * item           a bullet list
        ```              a fenced code block
        anything else    a paragraph

    Inline **bold** and `code` are left as markers for the renderer, which
    builds elements (never innerHTML).
    """
    title, blurb, blocks = "", "", []
    para, bullets, code, in_code = [], [], [], False

    def flush():
        if para:
            blocks.append({"k": "prose", "t": " ".join(para)})
            para.clear()
        if bullets:
            blocks.append({"k": "bullets", "items": list(bullets)})
            bullets.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                blocks.append({"k": "code", "t": "\n".join(code)})
                code.clear()
            else:
                flush()
            in_code = not in_code
            continue
        if in_code:
            code.append(raw)
            continue
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("# ") and not title:
            title = s[2:].strip()
        elif s.startswith("> ") and not blurb:
            blurb = s[2:].strip()
        elif s.startswith("## "):
            flush()
            blocks.append({"k": "section", "t": s[3:].strip()})
        elif s.startswith("* "):
            if para:
                flush()
            bullets.append(s[2:].strip())
        elif bullets and raw.startswith("  "):
            bullets[-1] += " " + s          # a wrapped bullet
        else:
            if bullets:
                flush()
            para.append(s)
    if in_code and code:
        blocks.append({"k": "code", "t": "\n".join(code)})
    flush()
    return title, blurb, blocks


def _wants_params(fn):
    """True if `fn` takes the window's parameter dict (the action table mixes
    no-argument methods and adapters that take it)."""
    try:
        import inspect
        sig = inspect.signature(fn)
        return len(sig.parameters) >= 1
    except (TypeError, ValueError):
        return False


class _Scheduler:
    """Delayed calls (after / after_cancel), run by the app's loop
    (App.run)."""

    def __init__(self):
        self._jobs = {}
        self._next = 1
        self._lock = threading.Lock()

    def after(self, ms, fn):
        with self._lock:
            jid = self._next
            self._next += 1
            self._jobs[jid] = (time.monotonic() + ms / 1000.0, fn)
        return jid

    def after_cancel(self, jid):
        with self._lock:
            self._jobs.pop(jid, None)

    def run_due(self, now):
        with self._lock:
            due = [(j, fn) for j, (t, fn) in self._jobs.items() if t <= now]
            for j, _fn in due:
                self._jobs.pop(j, None)
        for _j, fn in sorted(due):
            try:
                fn()
            except Exception as e:
                print(f"[meter] timer failed: {e!r}", file=sys.stderr)

    def quit(self):
        pass

