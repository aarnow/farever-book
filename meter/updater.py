"""New versions: asked of the releases repository on GitHub, offered in the
window, downloaded, then installed by the installer itself.

A release is a GitHub Release of RELEASES_REPO tagged with the version
(1.15.0 or v1.15.0) carrying FareverFrance-<version>-Setup.exe. The check
runs in the background (at launch, then hourly) and never fails loudly:
offline, rate-limited (60 requests an hour without an account) or no
release yet costs the offer, not the app.

Installing is the installer's job, run the way a person would run it:
opened visibly (ShellExecute) once the player said yes, this app quitting
so nothing is in its way, its last page starting the new version. No
hidden script waiting for us to exit and no silent install: Farever+'s
updater did that and Windows Defender quarantined it as defense evasion."""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

from common import DATA_HOME, FROZEN, VERSION

RELEASES_REPO = "aarnow/farever-france-releases"
RELEASES_URL = f"https://github.com/{RELEASES_REPO}/releases"
API_LATEST = f"https://api.github.com/repos/{RELEASES_REPO}/releases/latest"
TIMEOUT = 8.0
RECHECK_SECS = 3600.0
UPDATE_DIR = DATA_HOME / "updates"


def version_tuple(s):
    """"v1.15.0" -> (1, 15, 0), or None."""
    parts = str(s or "").strip().lstrip("vV").split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _request(url):
    return urllib.request.Request(url, headers={
        "User-Agent": f"FareverFrance/{VERSION}",
        "Accept": "application/vnd.github+json"})


class Updater:
    """What the window shows: nothing, an offer, a download's progress, an
    error. `state` is read by the app's spec; `changed` is called after
    each change (the window redraws)."""

    def __init__(self, changed):
        self._changed = changed
        self.state = None           # {"stage": ..., "v": ..., ...}
        self._release = None        # the newer release found
        self._dismissed = None      # a version the player put off
        self._checked = 0.0
        self._lock = threading.Lock()

    # ---- the check ------------------------------------------------------
    def enabled(self):
        """Only the installed app updates itself: from source, git does.
        FAREVER_UPDATE_TEST lets a source run try it."""
        return FROZEN or bool(os.environ.get("FAREVER_UPDATE_TEST"))

    def check(self, manual=False):
        """Ask GitHub in the background. `manual`: the player asked (the
        answer is shown even when there is nothing new)."""
        if not self.enabled() and not manual:
            return
        now = time.monotonic()
        with self._lock:
            if not manual and now - self._checked < RECHECK_SECS:
                return
            self._checked = now
        if manual:
            self._set({"stage": "checking"})
        threading.Thread(target=self._work, args=(manual,), daemon=True,
                         name="update-check").start()

    def _work(self, manual):
        try:
            with urllib.request.urlopen(_request(API_LATEST),
                                        timeout=TIMEOUT) as r:
                rel = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            # 404: no release published yet, nothing newer than us
            print(f"[update] no release to compare with ({e.code}).",
                  file=sys.stderr)
            if manual:
                self._set({"stage": "uptodate"} if e.code == 404 else
                          {"stage": "error",
                           "t": "GitHub ne répond pas pour le moment. "
                                "Réessayez plus tard."})
            return
        except Exception as e:
            print(f"[update] check skipped: {e}", file=sys.stderr)
            if manual:
                self._set({"stage": "error",
                           "t": "Impossible de joindre GitHub pour le "
                                "moment. Vérifiez votre connexion."})
            return
        latest = version_tuple(rel.get("tag_name"))
        mine = version_tuple(VERSION)
        asset = next((a for a in rel.get("assets") or ()
                      if str(a.get("name", "")).startswith("FareverFrance-")
                      and str(a.get("name", "")).endswith("-Setup.exe")
                      and a.get("browser_download_url")), None)
        if latest and mine and latest > mine and not asset:
            print(f"[update] {rel.get('tag_name')} has no "
                  "FareverFrance-<version>-Setup.exe: not offered.",
                  file=sys.stderr)
        if not latest or not mine or latest <= mine or not asset:
            if latest and mine and latest <= mine:
                print(f"[update] up to date (running {VERSION}).",
                      file=sys.stderr)
            if manual:
                self._set({"stage": "uptodate"})
            return
        v = ".".join(map(str, latest))
        self._release = {"v": v, "url": asset["browser_download_url"],
                         "size": int(asset.get("size") or 0),
                         "name": asset["name"],
                         "notes": (rel.get("body") or "").strip(),
                         "page": rel.get("html_url") or RELEASES_URL}
        print(f"[update] {v} is available (running {VERSION}).",
              file=sys.stderr)
        if manual or self._dismissed != v:
            self._offer()

    def _offer(self):
        r = self._release
        self._set({"stage": "offer", "v": r["v"], "mine": VERSION,
                   "notes": r["notes"][:2000],
                   "mb": round(r["size"] / 1048576, 1) if r["size"] else None})

    # ---- the player's answers ---------------------------------------------
    def later(self):
        """Put off: not offered again before the next launch."""
        if self._release:
            self._dismissed = self._release["v"]
        self._set(None)

    def close(self):
        self._set(None)

    def install(self, quit_app):
        """Download the installer (its progress shown), open it, quit."""
        r = self._release
        if not r or (self.state or {}).get("stage") == "download":
            return
        self._set({"stage": "download", "v": r["v"], "pct": 0})

        def work():
            try:
                UPDATE_DIR.mkdir(parents=True, exist_ok=True)
                dest = UPDATE_DIR / r["name"]
                part = dest.with_suffix(".part")
                with urllib.request.urlopen(_request(r["url"]),
                                            timeout=30) as resp, \
                        open(part, "wb") as f:
                    total = int(resp.headers.get("Content-Length")
                                or r["size"] or 0)
                    got, last = 0, -1
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        pct = int(100 * got / total) if total else 0
                        if pct != last:
                            last = pct
                            self._set({"stage": "download", "v": r["v"],
                                       "pct": min(pct, 100)})
                if total and got < total:
                    raise OSError(f"téléchargement incomplet ({got}/{total})")
                part.replace(dest)
            except Exception as e:
                print(f"[update] download failed: {e}", file=sys.stderr)
                self._set({"stage": "error", "v": r["v"],
                           "t": "Le téléchargement n'a pas abouti. "
                                "Réessayez plus tard, ou téléchargez "
                                "l'installeur depuis la page des versions.",
                           "page": r["page"]})
                return
            self._set({"stage": "installing", "v": r["v"]})
            try:
                # a double-click's launch: visible, the player's own
                os.startfile(str(dest))  # noqa: S606 - the update agreed to
            except OSError as e:
                print(f"[update] couldn't open the installer: {e}",
                      file=sys.stderr)
                self._set({"stage": "error", "v": r["v"],
                           "t": "L'installeur n'a pas pu être ouvert. Il est "
                                f"dans {UPDATE_DIR}.", "page": r["page"]})
                return
            # out of its way: the installer replaces our files, and its
            # last page starts the new version
            time.sleep(1.5)
            quit_app()
        threading.Thread(target=work, daemon=True, name="update-download").start()

    def _set(self, state):
        self.state = state
        try:
            self._changed()
        except Exception:
            pass


def clean_downloads():
    """Installers of past updates: removed at launch, once used."""
    try:
        for p in UPDATE_DIR.glob("FareverFrance-*-Setup.*"):
            p.unlink(missing_ok=True)
    except OSError:
        pass
