"""New versions: asked of the releases repository on GitHub, offered in the
window, downloaded, then installed by the installer itself.

A release is a GitHub Release of RELEASES_REPO tagged with the version
(1.15.0 or v1.15.0) carrying FareverBook-<version>-Setup.exe. The check
runs in the background (at launch, then hourly) and never fails loudly
(offline, rate-limited at 60 requests/hour, no release yet).

The installer is opened visibly (ShellExecute) with /SILENT: no pages to
click through, its progress window shown, the app started again at the end;
then the app quits. Never hidden (/VERYSILENT, a script): an updater doing
that was quarantined by Windows Defender as defense evasion."""
import ctypes
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

from common import DATA_HOME, FROZEN, VERSION
import i18n
from i18n import tr

RELEASES_REPO = "aarnow/farever-book"     # the code and its releases
REPO_URL = f"https://github.com/{RELEASES_REPO}"
RELEASES_URL = f"{REPO_URL}/releases"
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


_DETAILS = re.compile(r"<details[^>]*>\s*<summary>(.*?)</summary>(.*?)</details>",
                      re.S | re.I)


def release_text(body, lang):
    """A release's notes for the offer, as plain text in the player's
    language: the <details> block titled with the language's name (the
    release page carries both), else the whole text; pictures, markup and
    the alert's tag left out."""
    name = i18n.LANGS.get(lang, "")
    text = next((b for s, b in _DETAILS.findall(body)
                 if name and name in re.sub(r"<[^>]+>", "", s)), body)
    for pat, rep in ((r"!\[[^\]]*\]\([^)]*\)", ""),        # pictures
                     (r"<[^>]+>", ""),                       # html
                     (r"\[([^\]]+)\]\([^)]*\)", r"\1"),      # links
                     (r"(?m)^>\s?\[![A-Z]+\]\s*$", ""),     # alert tag
                     (r"(?m)^>\s?", ""), (r"(?m)^#+\s*", ""),
                     (r"\*\*|`", ""), (r"\n{3,}", "\n\n")):
        text = re.sub(pat, rep, text)
    return text.strip()


def _request(url):
    return urllib.request.Request(url, headers={
        "User-Agent": f"FareverBook/{VERSION}",
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
                           "t": tr("GitHub ne répond pas pour le moment. "
                                   "Réessayez plus tard.")})
            return
        except Exception as e:
            print(f"[update] check skipped: {e}", file=sys.stderr)
            if manual:
                self._set({"stage": "error",
                           "t": tr("Impossible de joindre GitHub pour le "
                                   "moment. Vérifiez votre connexion.")})
            return
        latest = version_tuple(rel.get("tag_name"))
        mine = version_tuple(VERSION)
        asset = next((a for a in rel.get("assets") or ()
                      if str(a.get("name", "")).startswith("FareverBook-")
                      and str(a.get("name", "")).endswith("-Setup.exe")
                      and a.get("browser_download_url")), None)
        if latest and mine and latest > mine and not asset:
            print(f"[update] {rel.get('tag_name')} has no "
                  "FareverBook-<version>-Setup.exe: not offered.",
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

    def available(self):
        """The newer version found, or None: the header's "Mettre à jour"
        button stays while it is there, put off or not."""
        return self._release["v"] if self._release else None

    def offer(self):
        """The header's button: the offer again."""
        if self._release:
            self._offer()

    def _offer(self):
        r = self._release
        self._set({"stage": "offer", "v": r["v"], "mine": VERSION,
                   "notes": release_text(r["notes"], i18n.lang())[:2000],
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
                           "t": tr("Le téléchargement n'a pas abouti. "
                                   "Réessayez plus tard, ou téléchargez "
                                   "l'installeur depuis la page des "
                                   "versions."),
                           "page": r["page"]})
                return
            self._set({"stage": "installing", "v": r["v"]})
            try:
                _run_installer(dest)
            except OSError as e:
                print(f"[update] couldn't open the installer: {e}",
                      file=sys.stderr)
                self._set({"stage": "error", "v": r["v"],
                           "t": tr("L'installeur n'a pas pu être ouvert. "
                                   "Il est dans {dir}.", dir=UPDATE_DIR),
                           "page": r["page"]})
                return
            # the installer replaces our files and starts the new version
            time.sleep(1.5)
            quit_app()
        threading.Thread(target=work, daemon=True, name="update-download").start()

    def _set(self, state):
        self.state = state
        try:
            self._changed()
        except Exception:
            pass


def _run_installer(path):
    """The installer, shown (SW_SHOWNORMAL), its pages skipped: the player
    agreed to the update. ShellExecute returns more than 32 on success."""
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "open", str(path), "/SILENT /SUPPRESSMSGBOXES /NORESTART",
        None, 1)
    if rc <= 32:
        raise OSError(f"ShellExecute failed ({rc})")


def clean_downloads():
    """Installers of past updates: removed at launch, once used."""
    try:
        for p in UPDATE_DIR.glob("FareverBook-*-Setup.*"):
            p.unlink(missing_ok=True)
    except OSError:
        pass
