"""The player's own last known state, kept on disk so the app shows it with
the game closed: per character, the profile (as Inspecter reads it), the
counters (luck, statistics) and the stock (the goals' counts), plus the last
character played. Players inspected in Inspecter stay in memory only.
Saved in .meter_me.json, at most every FLUSH_SECS."""
from __future__ import annotations

import json
import sys
import time

from common import _WRITABLE

ME_FILE = _WRITABLE / ".meter_me.json"
FLUSH_SECS = 10.0


class MeStore:
    def __init__(self):
        self.data = {"last": None, "chars": {}}
        self._dirty = False
        self._written = 0.0
        try:
            got = json.loads(ME_FILE.read_text(encoding="utf-8"))
            if isinstance(got, dict) and isinstance(got.get("chars"), dict):
                self.data.update(got)
        except Exception:
            pass

    # -- writing -----------------------------------------------------------
    def set_hero(self, name):
        if name and name != self.data.get("last"):
            self.data["last"] = name
            self.data["chars"].setdefault(name, {})
            self._dirty = True

    def put(self, name, key, value):
        """One part of a character's state (profile, counters, stock), with
        the time it was read."""
        if not name:
            return
        entry = self.data["chars"].setdefault(name, {})
        if entry.get(key) == value:
            return
        entry[key] = value
        entry[key + "_at"] = time.time()
        self._dirty = True

    def flush(self, force=False):
        if not self._dirty:
            return
        if not force and time.time() - self._written < FLUSH_SECS:
            return
        try:
            ME_FILE.write_text(json.dumps(self.data), encoding="utf-8")
            self._dirty = False
            self._written = time.time()
        except OSError as e:
            print(f"[meter] couldn't save the player's state: {e}",
                  file=sys.stderr)

    # -- reading -----------------------------------------------------------
    @property
    def last(self):
        return self.data.get("last")

    def entry(self, name=None):
        return self.data["chars"].get(name or self.last) or {}

    def got(self, key, name=None):
        """(value, when) of one part of a character's state, or (None, 0)."""
        e = self.entry(name)
        return e.get(key), e.get(key + "_at") or 0

    def profiles(self):
        """{name: profile} of one's characters, each with its read time."""
        return {n: dict(e["profile"], at=e.get("profile_at") or 0)
                for n, e in self.data["chars"].items()
                if isinstance(e.get("profile"), dict)}
