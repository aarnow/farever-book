"""Paths, constants and small helpers shared by every module."""
from __future__ import annotations

import colorsys
import ctypes
import os
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path


# ---------------------------------------------------------------------------
# Where things live
# ---------------------------------------------------------------------------
# From source, everything is in the project folder. Installed, PyInstaller
# unpacks to a temp dir that changes every launch and is deleted on exit:
# reads come from the bundle, writes go to DATA_HOME.
FROZEN = bool(getattr(sys, "frozen", False))


# res/ keeps our "frida" folder from colliding with the bundled frida package.
ROOT = (Path(sys._MEIPASS) / "res") if FROZEN else Path(__file__).resolve().parent.parent


# %LOCALAPPDATA%\FareverFrance
DATA_HOME = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "FareverFrance"


_WRITABLE = DATA_HOME if FROZEN else ROOT


FRIDA_DIR = ROOT / "frida"                  # read-only: the agent's JS


SHIPPED_ANALYSIS = ROOT / "analysis_out"    # read-only: the JSON we ship with


# Regenerated on most launches, so writable; seeded from SHIPPED_ANALYSIS.
ANALYSIS = (_WRITABLE / "analysis_out") if FROZEN else SHIPPED_ANALYSIS


POSITION_CACHE = _WRITABLE / ".meter_position.json"


# Apart from positions so resetting window positions keeps the settings.
SETTINGS_CACHE = _WRITABLE / ".meter_settings.json"


# Fastest kill of each boss; its own file so no reset erases it.
BEST_TIMES_CACHE = _WRITABLE / ".meter_besttimes.json"


# The rift reports (.json data, .txt, .png)
RIFTS_DIR = _WRITABLE / "failles"


DUNGEONS_DIR = _WRITABLE / "donjons"    # one JSON per dungeon run
BUILDS_DIR = _WRITABLE / "builds"       # one JSON per build (Build tab)
# The game's folder, when the player had to pick it (welcome screen).
GAME_PATH_FILE = _WRITABLE / ".meter_gamepath.json"
# Written at build time by packaging/third_party.py; none from source.
THIRD_PARTY_FILE = (Path(sys._MEIPASS) / "THIRD_PARTY_LICENSES.txt"
                    if FROZEN else None)


# What the account owns (mounts, gliders, companions), as last read in game.
COLLECTION_FILE = _WRITABLE / ".meter_collection.json"


# Each character's kill counts per monster (the game's codex), last read.
CODEX_FILE = _WRITABLE / ".meter_codex.json"


# Each character's item codex (item -> [count, rank]), last read.
ITEM_CODEX_FILE = _WRITABLE / ".meter_itemcodex.json"


# The achievements: the account's (id -> completion time) and each
# character's (completed ids, counters), last read.
ACH_FILE = _WRITABLE / ".meter_achievements.json"


# Each character's world elements (chests, orbs, obelisks...) -> state.
ELEMENTS_FILE = _WRITABLE / ".meter_elements.json"


LOG_FILE = DATA_HOME / "meter.log"
LOG_KEEP = 10                    # earlier runs kept, meter.log.1 to .10


TARGET_PROCESS = "Farever.exe"


# Farever's Steam app id: the Play button launches it with steam://rungameid
FAREVER_STEAM_APPID = 3672400


# Outside the project folder, so copies run from anywhere find each other.
LOCK_DIR = DATA_HOME


LOCK_FILE = LOCK_DIR / "instance.json"


# Images that count as "another meter". A literal, not sys.executable, so
# source and installed runs recognise each other.
EXE_NAME = "FareverFrance.exe"


METER_IMAGE_NAMES = frozenset({EXE_NAME.lower(), "python.exe", "pythonw.exe"})


# Read at import, before stderr is redirected. False for the windowed build
# and pythonw.exe (output then goes to the log file).
HAS_CONSOLE = sys.stderr is not None and sys.stdout is not None


# How long a running instance gets to unload its hook before it is forced.
QUIT_WAIT_SECS = 12.0


# "Local\": one meter per logon session.
CLAIM_MUTEX = "Local\\FareverFranceClaim"


CLAIM_WAIT_MS = 30000       # comfortably longer than a full QUIT_WAIT_SECS wait


COMBAT_TIMEOUT_SECS = 25.0


# DamageResult.blocker values whose hits report a full `_amount` but deal
# nothing. Measured: Ratsar's immune phase, 33 'InvulnerableHit' hits with
# amount > 0, none touching his health. Missed/DamageDodge are unmeasured, and
# wrongly dropping real hits is worse than counting fake ones.
NULLIFIED_BLOCKERS = frozenset({"InvulnerableHit"})


# A boss-pull reset replays damage newer than this into the new encounter:
# the boss healthbar (2/s timer) is detected after the opening burst lands.
# Short enough to leave out the previous trash pack.
BOSS_PULL_BACKLAG_SECS = 4.0


# Cap on the rolling event buffer behind that replay.
RECENT_EVENT_MAX = 2048


REFRESH_MS = 250


MAX_PLAYER_ROWS = 8


MAX_SKILL_ROWS = 8


# 60s Parse Mode: a fixed-length, comparable sample. The pre-roll leaves time
# to go back to the game after clicking.
PARSE_PREROLL_SECS = 8


PARSE_LENGTH_SECS = 60


# Short class tags, keyed by ent.Unit.kind (a hero's class).
CLASS_ABBR = {"Warrior": "Gue", "Mage": "Mag", "Priest": "Prê", "Rogue": "Vol"}


# The class icon for a player: by the game's class name ("Warrior") live,
# by its tag in a saved report.
CLASS_KEYS = {"Warrior": "warrior", "Mage": "mage", "Priest": "priest",
              "Rogue": "rogue", "Gue": "warrior", "Mag": "mage",
              "Prê": "priest", "Vol": "rogue"}


CLASS_ICON_DIR = ROOT / "assets" / "classes"


def class_key(kind_or_tag):
    """"warrior", "mage", "priest", "rogue" — or "" for a class this build
    has no icon for (the abbreviation is shown instead)."""
    return CLASS_KEYS.get(kind_or_tag or "", "")


def _class_tag(kind):
    """Gue for Warrior; an unknown class falls back to its first three
    letters."""
    if not kind:
        return ""
    return CLASS_ABBR.get(kind) or kind[:3].title()


# Rifts open on the hour, and the portal stays open this long.
RIFT_PORTAL_SECS = 180


RIFT_STYLE_SECS = 900       # ...and turn the box rift-coloured at 15


# The Help tab's markdown articles; the numeric prefix is their order.
HELP_DIR = (ROOT / "web" / "help") if FROZEN else (
    Path(__file__).resolve().parent / "web" / "help")


# The index's groups of articles. None for now: the help tab shows the
# repair alone.
HELP_GROUPS = ()


# DamageResult.affinity values as the game sends them (yes, Cheese), with
# their colour. Read through element_color().
ELEMENT_COLORS = {
    "Physical": "#B68A4E", "Magic": "#5279B5", "Fire": "#C9612A",
    "Spark": "#D9B43C", "Earth": "#7C5A2E", "Water": "#4B8FB5",
    "Faith": "#C8B280", "Light": "#E5C95A", "Raw": "#8A6A4A",
    "Cheese": "#D8C25E", "Chaos": "#8E4FB5", "None": "#9A8B7A",
}


_ELEMENT_FOLD = {k.lower(): v for k, v in ELEMENT_COLORS.items()}


# Display names; the English keys are what the game sends and reports store.
ELEMENT_LABELS = {
    "Physical": "Physique", "Magic": "Magie", "Fire": "Feu",
    "Spark": "Étincelle", "Earth": "Terre", "Water": "Eau", "Faith": "Foi",
    "Light": "Lumière", "Raw": "Brut", "Cheese": "Fromage", "Chaos": "Chaos",
    "Nature": "Nature", "Wind": "Vent", "Shadow": "Ombre", "Honey": "Miel",
    "Flower": "Fleur", "Lava": "Lave", "Electric": "Électrique",
    "Violence": "Violence",
    "None": "Aucun", "?": "Autre",
}


def element_label(el):
    """The French name of an affinity, or the raw value for a new one."""
    return ELEMENT_LABELS.get(el, str(el))


# Independent of the Windows locale.
_MONTHS_FR = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.",
              "août", "sept.", "oct.", "nov.", "déc.")


def date_fr(lt, with_time=True):
    """'27 sept. 21:07' for a time.struct_time."""
    out = f"{lt.tm_mday} {_MONTHS_FR[lt.tm_mon - 1]}"
    return f"{out} {lt.tm_hour:02d}:{lt.tm_min:02d}" if with_time else out


def _pct1(x):
    """A one-decimal percentage the French way: 33,3 %."""
    return f"{x:.1f}".replace(".", ",") + " %"


def _n(x):
    """A whole number with French thousands grouping: 12 345."""
    return f"{x:,.0f}".replace(",", "\u00a0")


def element_color(name):
    """A damage type's colour: the table (case-folded), else a stable pastel
    from the name's hash."""
    key = (name or "?").strip().lower()
    hit = _ELEMENT_FOLD.get(key)
    if hit:
        return hit
    h = (zlib.crc32(key.encode("utf-8")) % 360) / 360.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.45, 0.95)
    return f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"


# ---------------------------------------------------------------------------
# Running without a console
# ---------------------------------------------------------------------------
# Frozen, there is no python.exe: the exe re-invokes itself with these flags
# to run a bundled hltools script or the window process (menu_host.py).
TOOL_FLAG = "--run-hltool"


MENU_FLAG = "--menu-host"


CREATE_NO_WINDOW = 0x08000000   # no console flashing up for child processes


def run_bundled_tool(name, argv_rest):
    """Entry point for `FareverFrance.exe --run-hltool build_targets.py ...`.

    The tools do their work at top level and exit via SystemExit, so they are
    run as scripts, not imported."""
    tool = ROOT / "hltools" / name
    if not tool.is_file():
        sys.exit(f"[!] bundled tool missing: {tool}")
    import runpy
    # progress lines reach the meter live (frozen may ignore PYTHONUNBUFFERED)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    sys.argv = [str(tool), *argv_rest]
    sys.path.insert(0, str(tool.parent))
    runpy.run_path(str(tool), run_name="__main__")


def seed_analysis():
    """Make ANALYSIS exist and hold something usable.

    Installed build only: copies the shipped JSON into the empty writable
    directory, so the first launch works even if regeneration fails."""
    if not FROZEN:
        return
    try:
        ANALYSIS.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[meter] can't create {ANALYSIS}: {e}", file=sys.stderr)
        return
    import shutil
    for src in SHIPPED_ANALYSIS.glob("*.json"):
        dst = ANALYSIS / src.name
        if not dst.exists():
            try:
                shutil.copyfile(src, dst)
            except OSError as e:
                print(f"[meter] couldn't seed {dst.name}: {e}", file=sys.stderr)


def setup_logging():
    """Point stdout/stderr at a log file when there's no console behind them.
    Previous runs are kept as meter.log.1 (newest) to .10."""
    if HAS_CONSOLE:
        return
    try:
        DATA_HOME.mkdir(parents=True, exist_ok=True)
        prev = LOG_FILE.with_suffix(".log.1")
        if LOG_FILE.exists():
            try:
                for n in range(LOG_KEEP - 1, 0, -1):
                    old = LOG_FILE.with_suffix(f".log.{n}")
                    if old.exists():
                        old.replace(LOG_FILE.with_suffix(f".log.{n + 1}"))
                LOG_FILE.replace(prev)
            except OSError:
                # still open by an outgoing instance (handover): append below
                pass
        # append so a handover keeps the outgoing instance's last lines;
        # line-buffered so a crash keeps everything before it
        f = open(LOG_FILE, "a", buffering=1, encoding="utf-8", errors="replace")
    except OSError:
        return          # nowhere to log => run silently rather than not at all
    sys.stdout = sys.stderr = f
    print(f"[meter] log started {time.strftime('%Y-%m-%d %H:%M:%S')} "
          f"(frozen={FROZEN}, pid {os.getpid()})", file=sys.stderr)

    def hook(exc_type, exc, tb):
        import traceback
        print("[meter] unhandled exception:", file=sys.stderr)
        traceback.print_exception(exc_type, exc, tb, file=sys.stderr)
        f.flush()
    sys.excepthook = hook
    # worker threads too, or one dies without a trace
    def thook(args):
        hook(args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = thook


# Watched by startup waits before the app exists; afterwards the app handles
# the stop (only it can unload the hook).
STOP = threading.Event()


_APP = {"ref": None}


def request_stop():
    """Stop the meter. Safe from any thread and at any point in startup."""
    STOP.set()
    app = _APP["ref"]
    if app is not None:
        app.request_quit()


def message_box(text, title="Farever France", flags=0x40):
    """For failures that stop the meter starting; anything softer goes to the
    log."""
    try:
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title),
                                         flags | 0x1000)   # MB_SETFOREGROUND
    except Exception:
        pass


def _pretty_id(sid: str) -> str:
    """Readable fallback for skills the CDB has no display name for."""
    return sid.replace("_", " ") if sid and sid != "?" else sid


# ---------------------------------------------------------------------------
# Version / update check
# ---------------------------------------------------------------------------
# Bump on every release and tag the repo with the same string: the update
# check compares it with the latest release.
VERSION = "1.14.0"


# ---------------------------------------------------------------------------
# The application window
# ---------------------------------------------------------------------------
# Tab ids are what the window sends back; the labels are what it shows.
APP_TABS = ("Live", "Rifts", "Dungeons", "Collection", "Hunt", "Map",
            "Achievements", "Character", "Build", "Settings", "Help")


APP_TABS_APP_FIRST = "Settings"     # the first tab about the app, not the game


APP_TAB_LABELS = {"Live": "En jeu", "Rifts": "Failles",
                  "Dungeons": "Donjons", "Collection": "Collection",
                  "Hunt": "Codex", "Map": "Carte",
                  "Achievements": "Succès",
                  "Character": "Inspecter",
                  "Build": "Build",
                  "Settings": "Réglages",
                  "Help": "Aide"}


APP_TAB_DEFAULT = "Live"


# Réglages: its subjects, in the menu on its left
SETTINGS_TOPICS = {"meter": "DPS Meter", "overlay": "Overlay",
                   "display": "Affichage", "config": "Configuration"}


EVENTS_MAX = 40             # lines kept in the live page's event feed


def _mmss(secs):
    m, s = divmod(int(round(max(0, secs))), 60)
    return f"{m}:{s:02d}"


