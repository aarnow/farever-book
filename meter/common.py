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
# Two layouts, one codebase.
#
#   From source — everything stays in the project folder, exactly as it always
#   has, so the developer workflow is untouched.
#
#   From the installed build — the code and the data part company. PyInstaller
#   unpacks the bundle into a temporary directory that is a *different path on
#   every launch* and is deleted on exit, so anything we WRITE (regenerated
#   offsets, window positions, parse images, the log) has to go somewhere
#   durable and user-writable instead. Everything we only READ — the agent JS,
#   the shipped JSON — comes out of the bundle.
FROZEN = bool(getattr(sys, "frozen", False))


# Bundled resources go under res/ rather than at the bundle root, so our own
# "frida" folder of agent JS can't collide with the frida *package* PyInstaller
# unpacks alongside it. Inside res/ the layout is the project's, unchanged.
ROOT = (Path(sys._MEIPASS) / "res") if FROZEN else Path(__file__).resolve().parent.parent


# %LOCALAPPDATA%\FareverMeter. Already the home of the single-instance lock, so
# the installed build isn't inventing a location — just keeping more there.
DATA_HOME = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "FareverMeter"


_WRITABLE = DATA_HOME if FROZEN else ROOT


FRIDA_DIR = ROOT / "frida"                  # read-only: the agent's JS


SHIPPED_ANALYSIS = ROOT / "analysis_out"    # read-only: the JSON we ship with


# Regenerated on nearly every launch, so it has to be writable — which the
# bundle isn't (usefully). Seeded from SHIPPED_ANALYSIS on first run.
ANALYSIS = (_WRITABLE / "analysis_out") if FROZEN else SHIPPED_ANALYSIS


POSITION_CACHE = _WRITABLE / ".meter_position.json"


# Settings live apart from window positions on purpose: "Reset window
# positions" clears that file, and it has no business resetting your theme and
# your Show/hide ticks along with it.
SETTINGS_CACHE = _WRITABLE / ".meter_settings.json"


# Your fastest kill of each boss. Beside the two caches above rather than
# inside either: "Reset window positions" must not erase records, and the
# settings file is rewritten on every toggled checkbox — a record only needs
# writing when it's beaten. Same home as the positions, so it survives updates.
BEST_TIMES_CACHE = _WRITABLE / ".meter_besttimes.json"


# The rift reports (.json data, .txt, .png), in a folder of their own
# (gitignored). They used to share parses/ with the parse images, which are
# no longer saved: _move_rift_reports() moves what is left there.
RIFTS_DIR = _WRITABLE / "failles"


OLD_PARSES_DIR = _WRITABLE / "parses"


def _move_rift_reports():
    """Once: the rift reports out of the old shared parses/ folder into
    failles/, the parse images (no longer saved) deleted, and parses/ removed
    when nothing else is left in it."""
    if not OLD_PARSES_DIR.is_dir():
        return
    try:
        RIFTS_DIR.mkdir(parents=True, exist_ok=True)
        moved = dropped = 0
        for f in list(OLD_PARSES_DIR.iterdir()):
            if f.name.startswith("rift-") and f.suffix in (".json", ".txt",
                                                           ".png"):
                f.replace(RIFTS_DIR / f.name)
                moved += 1
            elif f.name.startswith("parse-") and f.suffix == ".png":
                f.unlink()
                dropped += 1
        if not any(OLD_PARSES_DIR.iterdir()):
            OLD_PARSES_DIR.rmdir()
        print(f"[meter] {moved} rift report file(s) moved to {RIFTS_DIR}, "
              f"{dropped} parse image(s) deleted", file=sys.stderr)
    except OSError as e:
        print(f"[meter] couldn't tidy {OLD_PARSES_DIR}: {e}", file=sys.stderr)


DUNGEONS_DIR = _WRITABLE / "donjons"    # one JSON per dungeon run
BUILDS_DIR = _WRITABLE / "builds"       # one JSON per build (Build tab)


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


# One meter at a time. The lock deliberately lives OUTSIDE the project folder:
# copies of this script run from different directories have to find each other,
# and that's the case that actually bites — an old copy left running while a
# newer one is launched from somewhere else, with only the old overlay on screen
# to show for it. A per-project lock would miss exactly that.
LOCK_DIR = DATA_HOME


LOCK_FILE = LOCK_DIR / "instance.json"


# Process images that count as "another meter" when the lock file names them.
# The installed executable's name is here as a literal rather than read from
# sys.executable, so a source run recognises an installed one and vice versa.
EXE_NAME = "FareverMeter.exe"


METER_IMAGE_NAMES = frozenset({EXE_NAME.lower(), "python.exe", "pythonw.exe"})


# Set once at startup, before stderr is redirected: is there a console for a
# human to read? False under the installed (windowed) build and under
# pythonw.exe. Decides whether a prompt is a terminal question or a dialog, and
# whether the control menu still warns about closing the console window.
HAS_CONSOLE = sys.stderr is not None and sys.stdout is not None


# How long to let a running instance shut itself down before forcing it. It has
# to unload the hook and detach, which is the whole point of asking nicely.
QUIT_WAIT_SECS = 12.0


# Serialises the claim in claim_single_instance(). "Local\" scopes it to the
# logon session, which is the right boundary — two users on one machine each get
# their own meter, their own lock file and their own game.
CLAIM_MUTEX = "Local\\FareverMeterClaim"


CLAIM_WAIT_MS = 30000       # comfortably longer than a full QUIT_WAIT_SECS wait


COMBAT_TIMEOUT_SECS = 25.0


# Hits the game reports a full `_amount` for but the target never takes.
#
# st.skill.DamageResult.blocker carries a _Data.$GameBeatKind_Impl_ name:
# AttackBlock, DamageDodge, Backstabbed, Critical, InvulnerableHit,
# BlockWellTimed, Missed. Measured against Ratsar's immune phase — 33 hits
# reported with blocker='InvulnerableHit', amount > 0 and _block == 0, every
# one of them counted by the meter and none of them touching his health.
#
# Only InvulnerableHit is listed, because only InvulnerableHit was measured.
# Missed and DamageDodge read like they belong here too, but a blocker that
# turns out to still deal damage would mean silently DROPPING real hits, which
# is a worse and far less visible bug than counting fake ones. Everything not
# listed is still reported by the mitigated-hit log, so adding one later is a
# one-line change backed by the same evidence this one was.
NULLIFIED_BLOCKERS = frozenset({"InvulnerableHit"})


# ---------------------------------------------------------------------------
# Patch quirks
# ---------------------------------------------------------------------------
# Things this build of Farever does that the meter has to work around, kept in
# one block so they can be removed in one edit when a patch fixes them. Nothing
# else in the file should grow a special case for a single skill: it goes here,
# or it doesn't go in.
#
# --- RETIRED in 3.4.0: the BOOST column ---
# "Swarmstrike Accord" buffs every ally in range and the game credits the bonus
# damage to the BUFF'S CASTER rather than to whoever swung, so a wielder's row
# filled up with the group's damage. 3.3.3 answered that by pulling those hits
# into a BOOST column that belonged to nobody.
#
# That is gone, because the damage turned out to be attributable after all. The
# blessing is a status instantiated PER ALLY, and the hook now credits the hit
# to `DamageResult.baseSkill.owner` — the ally carrying the status, i.e. the
# player who actually swung. Measured 2026-08-04 with frida/boost_probe.js.
# The fix lives in the agent, so by the time a
# hit reaches this file it already carries the right player and nothing here
# needs to know the skill exists.
#
# (For the record, since 3.3.3's note named it wrongly: the weapon is
# Wingsabers, `DS_Z1RBee_AssWiz` — not Beefury/`Sword_Swarm` — and the id that
# carries the damage is `DS_Bladeleaf_Skill2_Status`.)


# How much damage a boss-pull reset keeps rather than wiping.
#
# The reset is driven by the game's boss healthbar, and that bar is refreshed on
# a 2/s timer — so up to half a second passes between the pull landing and the
# meter hearing about it, plus however long the engagement takes to register at
# all. A player opening on a boss dumps their whole burst into that gap, and a
# plain reset throws exactly the numbers they wanted away.
#
# So the reset rewinds instead: damage newer than this is replayed into the
# fresh encounter with its original timestamps. Long enough to cover an opening
# burst and the detection lag, short enough not to drag in the trash pack you
# finished on the way over.
BOSS_PULL_BACKLAG_SECS = 4.0


# Rolling event buffer backing that rewind. Bounded by count as well as age so a
# big party in a busy fight can't grow it without limit — at ~4s of backlag this
# is far more headroom than the window can use.
RECENT_EVENT_MAX = 2048


REFRESH_MS = 250


MAX_PLAYER_ROWS = 8


MAX_SKILL_ROWS = 8


# 60s Parse Mode: a fixed-length sample, so two runs are comparable in a way
# "whatever that pull happened to be" never is. The pre-roll exists because the
# button is clicked from the escape menu — you need those seconds to close it
# and get your hands back on the keyboard.
PARSE_PREROLL_SECS = 8


PARSE_LENGTH_SECS = 60


# The canvas is redrawn at roughly twice the sweep rate. Matching them exactly
# would beat against the hook's timer and drop or double frames; drawing a bit
# faster than the data arrives keeps motion even.


# Short class tags for the meter. The game's own names come off ent.Unit.kind,
# which for a hero is its class rather than a creature id.
CLASS_ABBR = {"Warrior": "Gue", "Mage": "Mag", "Priest": "Prê", "Rogue": "Vol"}


# The class icon for a player, keyed by what the meter knows about them: the
# game's class name ("Warrior") live, or the abbreviation saved in a rift
# report — in French or, for reports older than the translation, in English.
CLASS_KEYS = {"Warrior": "warrior", "Mage": "mage", "Priest": "priest",
              "Rogue": "rogue", "Gue": "warrior", "War": "warrior",
              "Mag": "mage", "Prê": "priest", "Pst": "priest",
              "Vol": "rogue", "Rog": "rogue"}


CLASS_ICON_DIR = ROOT / "assets" / "classes"


def class_key(kind_or_tag):
    """"warrior", "mage", "priest", "rogue" — or "" for a class this build
    has no icon for (the abbreviation is shown instead)."""
    return CLASS_KEYS.get(kind_or_tag or "", "")


def _class_tag(kind):
    """(War) for Warrior. Anything unrecognised falls back to its first three
    letters rather than disappearing — a new class should look odd, not absent."""
    if not kind:
        return ""
    return CLASS_ABBR.get(kind) or kind[:3].title()


# Rifts open on the hour, and the portal stays open this long.
RIFT_PORTAL_SECS = 180


RIFT_STYLE_SECS = 900       # ...and turn the box rift-coloured at 15


# Big mono while counting; smaller and quieter for the idle placeholder, which
# is only ever on screen so the window can be dragged into place.


# The Help tab's articles, as markdown beside the panel's other web assets.
# Files rather than string constants so they stay writable prose — and the
# numeric prefix is the running order, so inserting one is a rename rather than
# an edit to a list somewhere else.
HELP_DIR = (ROOT / "web" / "help") if FROZEN else (
    Path(__file__).resolve().parent / "web" / "help")


# Which heading each article sits under on the index. Anything not named here
# lands in the last group, so a new file appears rather than disappearing.
HELP_GROUPS = (
    ("Pour commencer", ("10-steam", "20-stopping")),
)


# Minimum widths, at 100%. They're pixel values, so the scale slider has to
# scale them too or scaling down just hits the floor and nothing moves.
# The menu is wide because its tabs run down the LEFT rather than across the
# top: the navbar eats a fixed strip, and what is left has to still be a
# comfortable page. It also has to fit the Social tab's widest row — a name, a
# class, a level and two buttons — without that row deciding the window size on
# its own.
# The meter grew by one 6-cell column (OVER%) when healing stopped meaning
# "health restored" and started meaning "healing done", and the floor had to
# grow with it or the new column would be drawn off the right edge of every
# window narrow enough to be at the old minimum.
MIN_W = {"meter": 404, "detail": 320, "menu": 620, "prompt": 320}


# ---- combat history ----
# The floor a finished encounter has to clear to be worth keeping. Both, not
# either: a single crit on a passing boar clears the event count in one hit,
# and standing in a damage aura for ten seconds clears the duration without
# anything happening. Deliberately low — this exists to keep mis-clicks and
# walk-bys out of the list, not to judge which fights were interesting.
HISTORY_MIN_SECS = 5.0


HISTORY_MIN_EVENTS = 5


# The game's affinity vocabulary as it actually arrives off
# DamageResult.affinity (yes, Cheese), each with a colour. This is the single
# element-colour table — the rift report reads it through element_color().
ELEMENT_COLORS = {
    "Physical": "#B68A4E", "Magic": "#5279B5", "Fire": "#C9612A",
    "Spark": "#D9B43C", "Earth": "#7C5A2E", "Water": "#4B8FB5",
    "Faith": "#C8B280", "Light": "#E5C95A", "Raw": "#8A6A4A",
    "Cheese": "#D8C25E", "Chaos": "#8E4FB5", "None": "#9A8B7A",
}


_ELEMENT_FOLD = {k.lower(): v for k, v in ELEMENT_COLORS.items()}


# Display names for the affinities. The English keys are what the game sends
# and what saved reports hold, so they are only translated on the way out.
ELEMENT_LABELS = {
    "Physical": "Physique", "Magic": "Magie", "Fire": "Feu",
    "Spark": "Étincelle", "Earth": "Terre", "Water": "Eau", "Faith": "Foi",
    "Light": "Lumière", "Raw": "Brut", "Cheese": "Fromage", "Chaos": "Chaos",
    "None": "Aucun", "?": "Autre",
}


def element_label(el):
    """The French name of an affinity, or the raw value for a new one."""
    return ELEMENT_LABELS.get(el, str(el))


# Rift report phases. New reports are written with the French labels; this
# also translates the English ones in reports saved before the translation.
PHASE_LABELS_FR = {"Rift phase": "Phase de faille", "Boss phase": "Phase du boss"}


def phase_label(label):
    return PHASE_LABELS_FR.get(label, label)


# Month names for dates shown on screen, so they do not depend on the
# Windows locale Python happens to start with.
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
    """The colour a damage type wears — table first (case-folded), then a
    stable pastel from the name hash, so an affinity a patch adds arrives
    tinted rather than invisible, and the same colour every session."""
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
# The installed build is a windowed executable: no console, so nothing to press
# Ctrl+C in, nowhere for a print() to land, and no stdin to answer a prompt on.
# Each of those needs a replacement rather than a removal — the diagnostics in
# particular are the entire support process ("send me the log").

# Re-invoking ourselves to run one of the hltools generators. Frozen, there is
# no python.exe to call and sys.executable is *this* program, so the exe has to
# be able to act as its own interpreter for the two bundled tool scripts.
TOOL_FLAG = "--run-hltool"


# ...and the same trick for the settings panel, which is a WebView2 window in
# its own process. See menu_host.py for why it cannot share this one.
MENU_FLAG = "--menu-host"


CREATE_NO_WINDOW = 0x08000000   # ...or every regenerate flashes a console up


def run_bundled_tool(name, argv_rest):
    """Entry point for `FareverMeter.exe --run-hltool build_targets.py ...`.

    The tools are plain top-level scripts that do their work on import and exit
    via SystemExit, so they're run as scripts rather than imported — which also
    keeps them in their own process, as they are when run from source."""
    tool = ROOT / "hltools" / name
    if not tool.is_file():
        sys.exit(f"[!] bundled tool missing: {tool}")
    import runpy
    sys.argv = [str(tool), *argv_rest]
    sys.path.insert(0, str(tool.parent))
    runpy.run_path(str(tool), run_name="__main__")


def seed_analysis():
    """Make ANALYSIS exist and hold something usable.

    Only the installed build needs this: its writable data directory starts
    empty, while the JSON it should start from is inside the read-only bundle.
    Copying rather than symlinking means the first launch after an install has
    working data even if the game is mid-patch and regeneration fails."""
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

    Without this the windowed build is silent in the one situation where output
    matters most — it failed to start and the user wants to know why. The
    previous runs are kept as meter.log.1 to .10, because "it worked yesterday" is
    usually asked after today's run has already overwritten the evidence."""
    if HAS_CONSOLE:
        return
    try:
        DATA_HOME.mkdir(parents=True, exist_ok=True)
        # the last runs, meter.log.1 the newest: a restart no longer
        # wipes what an earlier session saw
        prev = LOG_FILE.with_suffix(".log.1")
        if LOG_FILE.exists():
            try:
                for n in range(LOG_KEEP - 1, 0, -1):
                    old = LOG_FILE.with_suffix(f".log.{n}")
                    if old.exists():
                        old.replace(LOG_FILE.with_suffix(f".log.{n + 1}"))
                LOG_FILE.replace(prev)
            except OSError:
                # Windows won't rename a file another process still has open,
                # which is exactly the case where two meters overlap — during a
                # handover, or when one is displacing another. Appending below
                # rather than truncating means the outgoing instance's last
                # lines (the ones explaining the handover) survive it.
                pass
        # Append, not truncate: see above. After a successful rotation the file
        # is gone, so this creates a fresh one and the two are equivalent.
        # Line-buffered, so a crash mid-write still leaves the lines before it —
        # which is exactly the log you want to read after a crash.
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
    # Overlay work happens on the Tk thread but the hook, hotkeys and tray all
    # run on their own; a thread dying quietly would otherwise take a feature
    # with it and leave no trace.
    def thook(args):
        hook(args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = thook


# The meter can be asked to stop long before there's an overlay to stop — most
# obviously while it sits waiting for Farever to launch, which with no console
# is a stretch where the tray icon is the only sign of life and so must be the
# way out too. STOP is what the pre-overlay waits watch; once the overlay
# exists it takes over, because only it can unload the hook on the way down.
STOP = threading.Event()


_OVERLAY = {"ref": None}


def request_stop():
    """Stop the meter. Safe from any thread and at any point in startup."""
    STOP.set()
    ov = _OVERLAY["ref"]
    if ov is not None:
        ov.request_quit()


def message_box(text, title="Farever France", flags=0x40):
    """A dialog is the only way to reach a user who has no console. Used for
    the failures that stop the meter starting at all — anything softer belongs
    in the log."""
    try:
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title),
                                         flags | 0x1000)   # MB_SETFOREGROUND
    except Exception:
        pass


# Finishing the update is the INSTALLER's job, not a helper's.
#
# This used to hand off to a detached, hidden PowerShell script that polled
# until this process died, ran the installer with /SILENT /SUPPRESSMSGBOXES,
# and relaunched the replaced exe. Every one of those steps is a step malware
# takes, and Windows Defender agreed: it quarantined FareverMeter.exe as
# `Behavior:Win32/DefenseEvasion.A!ml` — a BEHAVIOURAL detection, on more than
# one machine, each time right after an update.
#
# Hidden PowerShell with -ExecutionPolicy Bypass is the single most flagged
# pattern in Windows telemetry (ATT&CK T1059.001, and "defense evasion" is
# literally what the detection was named). Waiting for a parent to exit so you
# can overwrite its binary, then silently running an installer and relaunching
# it, is the rest of the same story.
#
# None of it was ever necessary. The helper existed only because a /SILENT run
# REFUSES to proceed while the meter is running (see AskToStopMeter in
# FareverMeter.iss — a silent run has nobody to answer its prompt, so it bails
# rather than hang), so something had to wait for us to die first. Run the
# installer the way a person would — visibly — and Inno asks politely on its
# own, and its [Run] entry offers to start the meter again afterwards. That
# entry is flagged `skipifsilent`, so under the old flow it never once ran.
#
# What is left is one ShellExecute of a file the user just agreed to install.


def _pretty_id(sid: str) -> str:
    """Readable fallback for skills the CDB has no display name for."""
    return sid.replace("_", " ") if sid and sid != "?" else sid


# ---------------------------------------------------------------------------
# Version / update check
# ---------------------------------------------------------------------------
# Bump this on every release, and tag the repo with the same string — it's the
# left-hand side of the comparison below, so a release that forgets it tells
# everyone they're out of date forever.
VERSION = "1.10.1"


# ---------------------------------------------------------------------------
# The application window
# ---------------------------------------------------------------------------
# One window, meant for a second screen. Tab ids are what the window sends
# back; the labels are what it shows.
APP_TABS = ("Live", "Rifts", "Dungeons", "Collection", "Hunt", "Map",
            "Achievements", "Character", "Build", "Settings", "Help")


APP_TABS_APP_FIRST = "Settings"     # the first tab about the app, not the game


APP_TAB_LABELS = {"Live": "En direct", "Rifts": "Failles",
                  "Dungeons": "Donjons", "Collection": "Collection",
                  "Hunt": "Codex", "Map": "Carte",
                  "Achievements": "Succès",
                  "Character": "Inspecter",
                  "Build": "Build",
                  "Settings": "Réglages",
                  "Help": "Aide"}


APP_TAB_DEFAULT = "Live"


# Class tags written into reports before the interface was translated.
OLD_CLASS_TAGS = {"War": "Gue", "Pst": "Prê", "Rog": "Vol"}


EVENTS_MAX = 40             # lines kept in the live page's event feed


def _mmss(secs):
    m, s = divmod(int(round(max(0, secs))), 60)
    return f"{m}:{s:02d}"


