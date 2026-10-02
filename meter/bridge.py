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


# ---------------------------------------------------------------------------
# The settings panel, at arm's length
# ---------------------------------------------------------------------------
class MenuBridge:
    """The meter's half of the link to the WebView2 settings panel.

    The panel runs in its own process (menu_host.py explains why) and speaks
    line-JSON over its stdin and stdout. This class owns that process: starting
    it, pushing state at it, translating what comes back into actions on the Tk
    thread, and noticing when it dies.

    It is deliberately forgiving. Nothing here may take the meter down: the
    overlay, the hook and the damage numbers all work perfectly well with no
    settings panel at all, so every failure path ends in "no panel" rather than
    an exception reaching the refresh loop.
    """

    def __init__(self, overlay):
        self.overlay = overlay
        self.proc = None
        self.ready = False
        self.geom = {}                  # last reported x/y/w/h
        self.geom_at = 0.0              # when it last changed; see _handle
        self._last_push = None          # the spec we last sent, to skip repeats
        self._lock = threading.Lock()
        self._failed = False            # give up after one failure to start

    # -- lifecycle --------------------------------------------------------
    def start(self, geom=None):
        """Spawn the panel, hidden. Called once, lazily — a player who never
        opens the menu never pays for a second process or a WebView2."""
        if self.proc is not None or self._failed:
            return
        self.geom = dict(geom or {})
        cmd = ([sys.executable, MENU_FLAG, json.dumps(self.geom)] if FROZEN
               else [sys.executable, str(Path(__file__).resolve().parent
                                         / "menu_host.py"),
                     json.dumps(self.geom)])
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                # Where the window finds the boss portraits it inlines.
                env=dict(os.environ, FAREVER_ANALYSIS=str(ANALYSIS)),
                stderr=None,            # its log lines join ours
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
        """The panel's process id, or None. Used by the overlay's focus test —
        the panel takes focus like any other window, and the meter has to know
        that is still 'us'."""
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
                # The panel died. Leave the corpse for _read to notice.
                pass

    def show(self):
        self.send({"t": "show"})

    def hide(self):
        self.send({"t": "hide"})

    def push(self, spec):
        """Send the panel its state, if it has changed.

        The refresh loop calls this on every tick the panel is open, and almost
        every tick produces exactly what the last one did — comparing here is
        far cheaper than serialising it down a pipe and re-rendering it.
        """
        if not self.ready:
            return
        if spec == self._last_push:
            return
        self._last_push = spec
        self.send({"t": "state", "d": spec})

    def invalidate(self):
        """Force the next push through even if it matches. Used when the panel
        has just appeared and its idea of the state is nothing at all, and by
        every action the panel triggers, so a click redraws immediately rather
        than on the next throttled rebuild."""
        self._last_push = None

    def dirty(self):
        """True if a push is owed. Lets the overlay skip building the spec at
        all on the ticks in between — see PANEL_PUSH_TICKS."""
        return self._last_push is None

    # -- receiving --------------------------------------------------------
    def _read(self):
        """One thread, for the panel's lifetime. Everything it decides to do
        is handed to the Tk thread through the overlay's action queue — this
        thread must never touch a widget."""
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
        # stdout closed: the panel has gone.
        print("[meter] settings panel closed", file=sys.stderr)
        if not self.ready and self.proc is proc:
            # It died before ever showing: the window is all there is to
            # see of the app, so say so rather than sit silent in the tray.
            self._failed = True
            code = proc.poll()
            print(f"[meter] the window never opened (exit code {code})",
                  file=sys.stderr)
            message_box(
                "La fenêtre de Farever France n'a pas pu s'ouvrir.\n\n"
                "Causes les plus courantes :\n"
                "• le zip n'a pas été débloqué (clic droit sur le zip > "
                "Propriétés > cocher « Débloquer », puis décompresser à "
                "nouveau) ;\n"
                "• Microsoft Edge WebView2 n'est pas installé "
                "(https://developer.microsoft.com/microsoft-edge/webview2/).\n\n"
                f"Le détail est dans :\n{LOG_FILE}",
                "Farever France — fenêtre impossible à ouvrir", 0x10)
        self.ready = False
        if self.proc is proc:
            self.proc = None

    def _handle(self, msg):
        t = msg.get("t")
        if t == "ready":
            self.ready = True
            self.invalidate()
        elif t == "geom":
            # Straight onto the object; the save path reads it on the Tk
            # thread and a torn read of four ints is not a real hazard here.
            got = {k: msg.get(k) for k in ("x", "y", "w", "h")}
            if got != self.geom:
                self.geom = got
                # Stamped rather than saved here. This arrives on the reader
                # thread, and it arrives for every step of a drag — writing the
                # file each time would be sixty writes a second. The overlay
                # notices the stamp and saves once the gesture has settled.
                self.geom_at = time.monotonic()
        elif t == "typing":
            self.overlay._enqueue(
                lambda on=bool(msg.get("on")): self.overlay._panel_typing(on))()
        elif t == "call":
            self._dispatch(msg)
        elif t == "closed":
            self.overlay._enqueue(self.overlay._panel_closed)()

    def _dispatch(self, msg):
        method, params = msg.get("m"), msg.get("p") or {}
        cid = msg.get("id") or 0
        fn = self.overlay._menu_actions().get(method)
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
            # Anything the panel asked for may have changed what it should be
            # showing, so the next tick rebuilds rather than waiting for the
            # throttle. One place, so no action can forget.
            self.invalidate()
            if cid:
                self.send({"t": "ret", "id": cid, "r": result})

        # Onto the Tk thread, like every hotkey and every old menu button.
        self.overlay._enqueue(run)()


def _parse_help(text):
    """Turn one help article into (title, blurb, spec blocks).

    A deliberately small markdown subset — enough for the prose we actually
    write and nothing more, because a full parser here would be a dependency
    and a surface for the panel to render something unexpected:

        # Title          the article's name (first one wins)
        > blurb          the one-liner on the index
        ## Heading       a section rule
        * item           a bullet list
        ```              a fenced code block
        anything else    a paragraph

    Inline **bold** and `code` survive as markers and are handled by the
    renderer, which builds them as elements rather than as HTML — nothing here
    ever becomes innerHTML.
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
    """True if `fn` takes the panel's parameter dict.

    The action table mixes two kinds of callable: existing meter methods that
    already take nothing (self._toggle_sounds) and small adapters written for
    the panel that need the value the user picked. Rather than wrap the former
    in dozens of no-argument lambdas, ask.
    """
    try:
        import inspect
        sig = inspect.signature(fn)
        return len(sig.parameters) >= 1
    except (TypeError, ValueError):
        return False


class _Scheduler:
    """after()/after_cancel()/quit() for code written against Tk's root: the
    engine has no Tk, and runs these from its own loop (App.run)."""

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

