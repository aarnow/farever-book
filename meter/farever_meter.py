"""
farever_meter.py — Farever France party damage meter (memory-reading edition).

Attaches to Farever via Frida, injects meter_hook.js (which hooks the game's
own ent.Unit.onInflictDamage and ent.Unit.playHitHealFX and streams every
player's damage and healing with real spell IDs), and renders two overlay
windows:

  * the METER: every player sorted by damage done, with damage/DPS/%, healing
    done and the share of it that was overheal, and
  * the BREAKDOWN: the inspected player's per-skill damage and per-skill
    healing side by side, plus per-element totals.

Everything is driven from the game itself rather than from hotkeys. The hook
watches the game's own window manager (ui.BaseUI.displayWindow/removeWindow), so
opening the game's escape menu — the moment the game frees the mouse cursor —
unlocks both windows for dragging, lets a click on a player row point the
breakdown at them, and pops up a small CONTROL MENU (centred on the game window,
draggable, position remembered) holding what used to be hotkeys. Closing the
escape menu puts everything back to click-through.

The breakdown snaps back to *your* hero on encounter reset, zone change, and
party/all mode switches.

Run:  python meter/farever_meter.py   (with Farever running)

Shipped as a windowed executable (see packaging/), which has no console — so it
logs to %LOCALAPPDATA%\\FareverMeter\\meter.log, asks its startup questions as
dialogs, and puts a tray icon in the notification area whose menu holds the
clean shutdown. Run from source it keeps the console and all three still work.

Stopping it matters: both the tray icon's "Stop the meter" and the control
menu's Quit button return from the Tk mainloop so the Frida hook is unloaded and
detached on the way out. Force-killing the process skips that, and a
half-attached agent is what destabilises the game across relaunches.

The only surviving hotkey (fires while Farever has focus):
  Shift+\\   reset the current encounter
"""
from __future__ import annotations

import colorsys
import ctypes
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from ctypes import wintypes
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

import frida

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
PARSES_DIR = _WRITABLE / "parses"   # finished-parse images land here (gitignored)
DUNGEONS_DIR = _WRITABLE / "donjons"    # one JSON per dungeon run
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
# The input pump runs at ~30Hz, which is the right pace for "does the overlay
# feel responsive" and far too fast for a settings panel. Two throttles on top
# of it, both counted in pump ticks:
#
#   PANEL_PUSH_TICKS     how often the panel's state is rebuilt when nothing
#                        has been clicked. Building it walks the roster and the
#                        whole status list, so doing it 30 times a second to
#                        discover nothing changed is exactly the waste the Tk
#                        panel was retired for. A click bypasses this — see
#                        MenuBridge.dirty.
#   PANEL_REASSERT_TICKS how often the show is re-sent so the window keeps its
#                        topmost standing, which it loses to anything else that
#                        claims it.
PANEL_PUSH_TICKS = 6            # ~5 times a second
PANEL_REASSERT_TICKS = 30       # ~once a second
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

# ---- palette (Farever-style, matches original meter) ----
BG_BORDER = "#2C1A0E"
BG_BODY = "#F2E1CB"
BG_HEADER = "#54A4A9"
BG_HEADER_UNLOCKED = "#5E9C4A"   # green — the escape menu is open / draggable
BG_BAR_TRACK = "#D9C09A"
FG_HEADER = "#FFFFFF"
FG_TEXT = "#3D2817"
FG_VALUE = "#1F1208"
FG_DIM = "#7B5A3A"

RIFT_STYLE_SECS = 900       # ...and turn the box rift-coloured at 15


# Big mono while counting; smaller and quieter for the idle placeholder, which
# is only ever on screen so the window can be dragged into place.

ACCENT = "#3D7C7C"
DMG_BAR = "#5279B5"       # blue — damage bars
HEAL_BAR = "#5E9C4A"      # green — healing bars
# Healing done to yourself, drawn as the LEFT segment of every healing bar so
# the split reads at a glance down a column. A vivid green-teal — still the
# healing family, because healing yourself is still healing, but saturated
# where HEAL_BAR is muted. The shade has been round the houses: teal first
# (read as a shield), then off-white (too stark), then a washed-out green
# (#D8E9D0 — separated well from the green, but nearly landed on the Farever
# theme's tan track), and now this.
#
# The separation this one trades on is CHROMA AND HUE, not lightness. Against
# HEAL_BAR the greyscale ratio is only 1.35:1, which reads as a failure and
# isn't — measured as colour the gap is deltaE2000 12.3, five times the
# just-noticeable step, and it is a vivid-vs-muted jump rather than a
# light-vs-dark one. A contrast ratio cannot see that, which is why the colour
# is checked in Lab and on screen rather than by ratio.
#
# What it fixes: the old shade sat at deltaE 18.3 from the Farever track
# (#D9C09A) and 14.7 from that theme's body — close enough that a short self
# segment on a light theme could read as empty track. This one is at 31.3 and
# 32.9, and clears every theme's track and body by a wide margin. The binding
# constraint has moved back to the HEAL_BAR boundary, which is the one that
# only has to hold across a hard edge at five pixels tall.
SELF_HEAL_BAR = "#08BD71"


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

WS_EX_NOACTIVATE = 0x08000000



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
    previous run is kept as meter.log.1, because "it worked yesterday" is
    usually asked after today's run has already overwritten the evidence."""
    if HAS_CONSOLE:
        return
    try:
        DATA_HOME.mkdir(parents=True, exist_ok=True)
        prev = LOG_FILE.with_suffix(".log.1")
        if LOG_FILE.exists():
            try:
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


_SLUG_STRIP = re.compile(r"[^A-Za-z0-9]+")


def _slug(text, limit=48):
    """A filename-safe stub of a dataset name. Only used to make a file
    recognisable in Explorer — the real name is inside the JSON, so this is
    allowed to be lossy."""
    s = _SLUG_STRIP.sub("-", str(text or "")).strip("-")
    return (s[:limit].rstrip("-") or "encounter").lower()


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _skill_ident(ev):
    """(breakdown key, display name) for one hit or heal event.

    A summon's hit carries `pet` — its raw Unit.kind. The damage already
    merged into the owner's row upstream, so the breakdown is the only place
    left that can show a pet was responsible for part of it.

    The KEY is prefixed with the raw kind (stable, and the same on a
    non-English client), which also keeps a pet's ability separate from an
    identically named one of the player's own. The NAME gets the sheet's
    display name — "Nightling Terror: Attack".

    Module-level because the rift recorder now keeps its own per-skill tables
    for the history dataset, and two copies of this rule is two places for a
    pet's damage to end up filed under the player's own ability.
    """
    sid = ev.get("skill", "?")
    nm = ev.get("name")
    pet = ev.get("pet")
    if pet:
        sid = f"{pet}:{sid}"
        label = _summon_label(pet)
        nm = f"{label}: {nm}" if nm else label
    return sid, nm


def _stamp_report_classes(report, world):
    """Freeze each player's class acronym into a finished rift report.

    Done once, at the kill, because the report is saved to disk and re-opened
    later — by then the world sweep has long forgotten a stranger who was in
    the rift, and the card would show a row of blanks. A player the sweep never
    saw gets "" and simply has no acronym."""
    for ph in report.get("phases", ()):
        for p in ph.get("players", ()):
            p["cls"] = _class_tag(world.class_of(p.get("name")))


def _report_name(p):
    """`Brudr (War)` for the report — the acronym only when one is known."""
    name = p.get("name") or "?"
    return f"{name} ({p['cls']})" if p.get("cls") else name


def _overheal_note(d, fmt=" ({:.0f}% en excès)"):
    """The overheal clause for a report dict, or "" when there is none to give.

    Rift reports are reloaded from JSON on disk, and a file written before
    healing meant RAW healing carries `heal` but no `heal_landed`. Treating
    that absence as zero would stamp every archived report 100% overheal, so
    an old report simply says nothing about overhealing — which is the truth
    about what it recorded."""
    landed = d.get("heal_landed")
    heal = d.get("heal") or 0.0
    if landed is None or heal <= 0.5:
        return ""
    return fmt.format(_overheal_pct(heal, landed))


# A window shorter than this has no rate worth showing. The boss-phase
# rewind can leave a phase a few milliseconds long, and dividing by that
# produces a seven-figure DPS that is not a number about anything.
RATE_MIN_SECS = 0.5


def _rate(amount, duration):
    """`amount` per second, or None when the window is too short to divide by.

    None rather than 0.0, because "no rate" and "a rate of zero" are different
    facts and the card renders them differently — one shows the total instead,
    the other shows a real zero."""
    if not duration or duration < RATE_MIN_SECS:
        return None
    return (amount or 0.0) / duration


def _rate_text(amount, duration, unit):
    """"12 345 DPS", or None when there is no rate to state."""
    r = _rate(amount, duration)
    return None if r is None else f"{_n(r)} {unit}"


def _overheal_pct(total, landed):
    """Share of `total` healing that restored no health, as a percentage.

    Clamped at 0 because the two figures come from different observations —
    a health rise can be attributed to a heal whose estimated size is smaller
    than the rise itself (a regen tick landing inside a heal's match window,
    say), and "-3% overheal" is not a thing to show anyone."""
    if not total or total <= 0.0:
        return 0.0
    return max(0.0, (total - landed) / total * 100.0)


class HealSizeEstimator:
    """How big was that heal? The client is never told, so this estimates it.

    Measured 2026-08-03 (`frida/run_heal.py`, 40 heal events across 6 healers
    and 4 skills): of the fifteen heal entry points in the build, ONLY
    `ent.Unit.playHitHealFX` runs client-side, and its `HitData.amount` reads
    0.000. `receiveHeal`, `computeHeal`, `evalHeal`, the four `*HealEval`
    callbacks, `applyHeal`, `rpcDisplayHeal(__impl)` and
    `ui.hud.EffectsFeed.displayHeal` never fire on a client at all. The only
    heal quantity observable here is the RISE in the target's replicated
    health — which is zero when the target is already full.

    A heal's size is therefore estimated as the HIGH-WATER MARK of what that
    player's casts of that skill have been seen to restore. A cast on a target
    missing more health than the heal restores lands in full, so the largest
    observation converges on the true per-cast value from below; every smaller
    one is a cast that was capped by the target's missing health, and every
    zero is a cast that was capped completely.

    Deliberately the maximum, not a mean or a quantile. Capping biases
    observations DOWN and there is no way to tell a capped observation from an
    uncapped one — `ent.UnitAttributes.maxHealth` reads 0 for heroes (measured
    in the same session), so "how hurt was the target" isn't available either.
    Averaging would report a healer as weaker the healthier their party was,
    which is the exact bug this replaces. The known cost is crits: once a skill
    has been seen to crit it is credited its crit value on every cast, so a
    crit-heavy healing build reads somewhat high.

    The window bounds that across a session — levels, gear and talent changes
    all move a skill's real value, and a lifetime maximum would pin the
    estimate to the best it ever was.

    Called only from the hook's message thread (the same thread that feeds
    PartySession), so it needs no lock of its own.
    """

    WINDOW = 64          # observations kept per (player, skill)
    UNDER_MIN_HP = 3     # a smaller shortfall than this is not reported

    def __init__(self, specs=None):
        self._obs: dict[tuple, deque] = defaultdict(
            lambda: deque(maxlen=self.WINDOW))
        # skill id -> {step index: [effect spec, ...]} out of the game's own
        # data.cdb (analysis_out/heal_specs.json). This is what makes a heal
        # on a full-health target countable at all, so its absence is worth
        # saying out loud rather than quietly falling back.
        self._specs = specs or {}
        self._computed = 0      # heals sized from the game's own numbers
        self._guessed = 0       # ...and heals that fell back to observation
        self._unsized = 0       # ...and heals nothing could size
        # skill -> (landed/computed, landed, computed) for the worst case seen
        self._audit: dict[str, tuple] = {}

    def size_from_spec(self, ev):
        """The heal's real size, computed the way the game computes it.

        `dyn` heals carry their amount in BaseSkill.dynVal1-3, which the server
        replicates; `scale` heals are a ratio on one of the caster's
        attributes. Both arrive on the event from the hook. Returns None when
        this skill isn't in the table, or when the inputs it needs are missing
        — a summon's attributes, say, or a step index that didn't match."""
        steps = self._specs.get(ev.get("skill"))
        if not steps:
            return None
        specs = steps.get(str(ev.get("step")))
        if specs is None:
            # One heal step is the common case (39 of 44 skills). When there is
            # exactly one, a step index that didn't line up doesn't matter.
            if len(steps) != 1:
                return None
            specs = next(iter(steps.values()))
        dyn, atb = ev.get("dyn") or [], ev.get("atb") or {}
        total = 0.0
        for spec in specs:
            amount = 0.0
            # A spec carrying both is a dyn with a floor: the dyn is the real
            # value and the base is what it falls back to.
            n = spec.get("dyn")
            if n and len(dyn) >= n and dyn[n - 1]:
                amount = float(dyn[n - 1])
            elif spec.get("scale"):
                for ratio, name in spec["scale"]:
                    if name not in atb:
                        return None      # MaxHealth/FoePower — not readable
                    amount += float(ratio) * float(atb[name])
            if not amount:
                amount = float(spec.get("base") or 0.0)
            total += amount
        return total if total > 0 else None

    def stamp(self, ev: dict) -> None:
        """Fold one heal event in and fill in its raw size.

        `landed` (what the health bar actually moved) arrives from the hook;
        `amount` leaves as the estimated size of the heal itself. Every
        downstream consumer already reads `amount`, so healing totals become
        raw healing without any of them knowing about the estimate."""
        landed = float(ev.get("landed", ev.get("amount", 0.0)) or 0.0)
        ev["landed"] = landed
        if not ev.get("est"):
            ev["amount"] = landed          # regen: observed AS the rise
            return
        obs = self._obs[(ev.get("player") or "?", ev.get("skill") or "?")]
        if landed > 0:
            obs.append(landed)
        # The game's own numbers first — they are right on the very first cast
        # and don't care whether the target had room for the heal. Observation
        # is only the fallback for the skills the table can't size.
        spec = self.size_from_spec(ev)
        if spec is not None:
            self._computed += 1
            ev["sized"] = "spec"
        else:
            spec = max(obs) if obs else 0.0
            if spec > 0:
                self._guessed += 1
                ev["sized"] = "seen"
            else:
                self._unsized += 1
                ev["sized"] = "none"
        # A size can never make a measured heal smaller than it actually was.
        ev["amount"] = max(landed, spec)
        # Ground truth, collected from ordinary play rather than a probe: a
        # heal that LANDED in full is a direct measurement of what that heal
        # was worth, so a computed size below it means the formula is wrong.
        # (Above it is expected and means nothing — the target was topped off.)
        if ev.get("sized") == "spec" and landed > 0:
            key = ev.get("skill") or "?"
            worst = self._audit.get(key)
            ratio = landed / spec if spec > 0 else 0.0
            if worst is None or ratio > worst[0]:
                self._audit[key] = (ratio, landed, spec)

    def drain_report(self):
        """A one-line summary for the log, or None when nothing has healed.

        The `under` entries are the ones worth reading: a skill whose computed
        size came out BELOW what it was measured to restore is a formula that
        needs fixing, not a rounding artifact."""
        computed, guessed, unsized = (self._computed, self._guessed,
                                      self._unsized)
        if not (computed or guessed or unsized):
            return None
        # Both a ratio and a few HP: on an 8 HP heal, 2% is rounding.
        under = sorted((k, v) for k, v in self._audit.items()
                       if v[0] > 1.02 and v[1] - v[2] >= self.UNDER_MIN_HP)
        self._computed = self._guessed = self._unsized = 0
        self._audit = {}
        line = (f"[meter] heal sizing: {computed} computed, "
                f"{guessed} from observation, {unsized} unsized")
        if under:
            line += "   UNDER-COMPUTED: " + ", ".join(
                f"{k} landed={v[1]:.0f} vs computed={v[2]:.0f}"
                for k, v in under[:4])
        return line


@dataclass
class PlayerAgg:
    name: str
    is_me: bool = False
    in_party: bool = False
    total: float = 0.0
    hits: int = 0
    crits: int = 0
    kills: int = 0
    heal_total: float = 0.0     # raw healing (see HealSizeEstimator)
    heal_landed: float = 0.0    # ...of which actually restored health
    heal_self: float = 0.0      # ...and of which the healer was the target
    heal_hits: int = 0
    # skill -> [hits, total, crits]  (damage)
    skills: dict[str, list] = field(default_factory=lambda: defaultdict(lambda: [0, 0.0, 0]))
    # skill -> [hits, total, crits, self_total]  (healing). The fourth column
    # is what makes a healing bar splittable: how much of that skill's healing
    # the caster put on themselves.
    heals: dict[str, list] = field(default_factory=lambda: defaultdict(lambda: [0, 0.0, 0, 0.0]))
    # element -> [hits, total]
    elements: dict[str, list] = field(default_factory=lambda: defaultdict(lambda: [0, 0.0]))
    # unit kind -> damage THIS player dealt to it. Per player rather than per
    # session because a history dataset can be filtered down to your party,
    # and a session-wide tally would then claim the whole shard's damage on a
    # boss above rows that only add up to your group's share.
    targets: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    first_time: float = 0.0
    last_time: float = 0.0

    def record(self, skill, element, amount, crit, kill, now, target=None):
        if self.first_time == 0.0:
            self.first_time = now
        self.last_time = now
        self.total += amount
        self.hits += 1
        if crit:
            self.crits += 1
        if kill:
            self.kills += 1
        s = self.skills[skill]
        s[0] += 1; s[1] += amount; s[2] += crit
        e = self.elements[element]
        e[0] += 1; e[1] += amount
        # Absent on a hit whose target wasn't a unit the hook could name; the
        # dataset then falls back to its zone name, which is still true.
        if target:
            self.targets[target] += amount

    def record_heal(self, skill, amount, crit, landed=0.0, is_self=False):
        self.heal_total += amount
        self.heal_landed += landed
        self.heal_hits += 1
        if is_self:
            self.heal_self += amount
        s = self.heals[skill]
        s[0] += 1; s[1] += amount; s[2] += crit
        s[3] += amount if is_self else 0.0

    @property
    def overheal_pct(self):
        """Share of this player's healing that restored no health.

        Zero — not "unknown" — when they have not healed at all: a row with no
        healing has nothing to have wasted."""
        return _overheal_pct(self.heal_total, self.heal_landed)


class PartySession:
    """Tracks the current encounter across all players.

    Encounter boundaries are hit-driven (a hit after > timeout of no damage
    starts a fresh encounter). The *duration* clock, however, only advances
    while at least one captured player is in combat — set_active() is driven by
    the caller with the game's isInCombat state, so DPS averages over active
    combat time rather than wall-clock.
    """

    def __init__(self, combat_timeout=COMBAT_TIMEOUT_SECS):
        self.lock = threading.Lock()
        self.timeout = combat_timeout
        self.players: dict[str, PlayerAgg] = {}
        self.skill_names: dict[str, str] = {}   # skill id -> display name
        self.combat: dict[str, int] = {}        # name -> isInCombat (from hook)
        self.enc_start = 0.0
        self.last_hit = 0.0
        self.active_accum = 0.0                  # accumulated in-combat seconds
        self.active_since = None                 # ts when current active run began
        self.in_combat = False
        self.epoch = 0          # bumped on explicit/zone reset (UI watches it)
        self.capture_until = None   # parse mode's hard cutoff (None = no limit)
        self.capture_start = None   # ...and when that window opened
        # (timestamp, "hit"|"heal", event) for the last few seconds, so a
        # boss-pull reset can rewind instead of wiping. Only what was actually
        # recorded goes in here — anything the capture window rejected was never
        # part of the encounter and must not reappear.
        self._recent: deque = deque(maxlen=RECENT_EVENT_MAX)
        # Called with the finished encounter, as plain data, every time one is
        # thrown away. Set by the overlay when combat history is on; None means
        # a reset is exactly what it always was. Never called under the lock —
        # the receiver writes a file, and holding the data path open across
        # disk IO would stall the damage hook.
        self.archive_hook = None
        # Which players belong in an archived dataset — the meter's party/all
        # mode, supplied by the overlay. Applied inside _freeze, BEFORE any
        # total is summed, so a party dataset's totals, event count, target
        # tally and minimum-size floor all describe the same set of people.
        # None keeps everyone (which is what a rift report does deliberately).
        self.archive_filter = None

    def set_capture_window(self, seconds):
        """Parse mode: take data for exactly `seconds` from now, then stop.
        Enforced here on the data path rather than by the UI tick, so the sample
        is the length asked for however the 250 ms refresh happens to land.
        None clears the limit."""
        with self.lock:
            now = time.time()
            self.capture_start = None if seconds is None else now
            self.capture_until = None if seconds is None else now + seconds

    def _capturing(self, now):
        return self.capture_until is None or now <= self.capture_until

    def _effective_duration(self, now):
        """Seconds to divide by for DPS.

        Normally that's in-combat time, so a pull's DPS isn't diluted by the
        walk to it. Inside a parse window it's wall-clock elapsed instead: the
        window *is* the measurement, so downtime has to count against you or two
        runs aren't comparable — and the game's isInCombat flag drops between
        pulls, which would otherwise inflate a 60 s parse by however much of it
        the flag happened to miss."""
        if self.capture_start is not None:
            return max(0.001, min(now, self.capture_until) - self.capture_start)
        return max(0.001, self._duration(now)) if self.enc_start else 0.0

    def _reset(self, now):
        self.players.clear()
        self.enc_start = 0.0
        self.active_accum = 0.0
        self.active_since = None

    def _player_for(self, ev):
        name = ev.get("player") or "?"
        p = self.players.get(name)
        if p is None:
            p = PlayerAgg(name=name, is_me=bool(ev.get("is_me")))
            self.players[name] = p
        if ev.get("is_me"):
            p.is_me = True
        if ev.get("in_party"):
            p.in_party = True
        return p

    def _skill_of(self, ev):
        sid, nm = _skill_ident(ev)
        if nm and sid not in self.skill_names:
            self.skill_names[sid] = nm
        return sid

    def record(self, ev: dict):
        with self.lock:
            now = time.time()
            if not self._capturing(now):
                return
            # The lull-reset is suppressed inside a parse window: a quiet
            # stretch mid-parse is part of the sample, not the start of a new
            # encounter, and wiping 40 seconds in would ruin the run.
            if (self.capture_until is None and self.last_hit
                    and (now - self.last_hit) > self.timeout):
                self._reset(now)          # new encounter after a long lull
            if self.enc_start == 0.0:
                self.enc_start = now
            self.last_hit = now
            self._recent.append((now, "hit", ev))
            self._apply_hit(self._player_for(ev), ev, now)

    def _apply_hit(self, p, ev, ts):
        """Record one hit. Shared with the boss-pull rewind, so a replayed hit
        lands exactly where the live one did."""
        p.record(self._skill_of(ev), ev.get("element", "?"),
                 float(ev.get("amount", 0.0)), int(ev.get("crit", 0)),
                 int(ev.get("kill", 0)), ts, ev.get("target"))

    def record_heal(self, ev: dict):
        # Heals are recorded but never drive encounter boundaries: an
        # out-of-combat potion/regen must not roll the meter into a fresh
        # encounter (damage does that), so last_hit stays untouched.
        with self.lock:
            now = time.time()
            if not self._capturing(now):
                return
            self._recent.append((now, "heal", ev))
            p = self._player_for(ev)
            p.record_heal(self._skill_of(ev),
                          float(ev.get("amount", 0.0)), int(ev.get("crit", 0)),
                          float(ev.get("landed", 0.0)),
                          bool(ev.get("self")))

    def set_combat(self, state: dict):
        with self.lock:
            self.combat = state

    def set_active(self, active: bool, now: float):
        """Advance/pause the duration clock based on whether a captured player
        is currently in combat. Called each UI tick with a mode-aware value."""
        with self.lock:
            # Past a parse window's cutoff the clock stops even mid-fight, so
            # the duration the meter shows is the sample's, not the pull's.
            if not self._capturing(now):
                active = False
            if active and self.active_since is None:
                self.active_since = now
            elif not active and self.active_since is not None:
                self.active_accum += now - self.active_since
                self.active_since = None
            self.in_combat = active

    def _freeze(self, now):
        """The live encounter as plain, JSON-safe data — or None when there
        isn't enough of one to be worth keeping.

        Called with the lock held and returning nothing but built-ins, so the
        caller can hand it to a disk writer on another thread without the
        aggregates moving underneath it.
        """
        if not self.players:
            return None
        # Filter FIRST. Every number below is summed over `rows`, so a party
        # dataset is internally consistent — including the floor, which then
        # asks "did YOUR GROUP fight for long enough", not "did anything
        # happen on this shard".
        rows = sorted(self.players.values(), key=lambda p: -p.total)
        if self.archive_filter is not None:
            try:
                rows = list(self.archive_filter(rows))
            except Exception as e:
                print(f"[meter] archive filter failed ({e}); keeping every "
                      "player", file=sys.stderr)
        if not rows:
            return None
        duration = self._effective_duration(now)
        events = sum(p.hits + p.heal_hits for p in rows)
        if duration < HISTORY_MIN_SECS or events < HISTORY_MIN_EVENTS:
            return None
        targets: dict[str, float] = defaultdict(float)
        for p in rows:
            for kind, amt in p.targets.items():
                targets[kind] += amt
        return {
            "at": now,
            "start": self.enc_start or now,
            "duration": duration,
            "events": events,
            "total": sum(p.total for p in rows),
            "heal": sum(p.heal_total for p in rows),
            "heal_landed": sum(p.heal_landed for p in rows),
            "targets": {k: v for k, v in targets.items() if v > 0.5},
            # Only the names this encounter actually used: skill_names is
            # never cleared (a display name is a fact about the game, not
            # about the fight), and shipping the whole session's table with
            # every dataset would grow every file for nothing.
            "skill_names": {
                sid: nm for sid, nm in self.skill_names.items()
                if any(sid in p.skills or sid in p.heals for p in rows)},
            "players": [{
                "name": p.name,
                "is_me": bool(p.is_me),
                "in_party": bool(p.in_party),
                "total": p.total, "hits": p.hits, "crits": p.crits,
                "kills": p.kills,
                "heal": p.heal_total, "heal_landed": p.heal_landed,
                "heal_self": p.heal_self, "heal_hits": p.heal_hits,
                "first": p.first_time, "last": p.last_time,
                "skills": {k: list(v) for k, v in p.skills.items()},
                "heals": {k: list(v) for k, v in p.heals.items()},
                "elements": {k: list(v) for k, v in p.elements.items()},
                # Per player as well as summed above: it answers who was on
                # the boss and who was on the adds, and it makes the file
                # self-checking — the dataset's `targets` must be the sum of
                # these, whatever filter produced it.
                "targets": dict(p.targets),
            } for p in rows],
        }

    def _emit_archive(self, frozen):
        """Hand a finished encounter to the archive, outside the lock. A
        broken hook costs the dataset, never the reset that was asked for."""
        if frozen is None or self.archive_hook is None:
            return
        try:
            self.archive_hook(frozen)
        except Exception as e:
            print(f"[meter] couldn't archive the encounter: {e}",
                  file=sys.stderr)

    def reset(self):
        frozen = None
        with self.lock:
            now = time.time()
            frozen = self._freeze(now)
            self._reset(now)
            self.last_hit = 0.0
            self.in_combat = False
            # A reset always returns to live capture — and so to in-combat DPS.
            self.capture_until = self.capture_start = None
            self._recent.clear()
            self.epoch += 1
        self._emit_archive(frozen)

    def reset_keeping_recent(self, backlag=BOSS_PULL_BACKLAG_SECS):
        """Reset the encounter but carry the last `backlag` seconds forward.

        For the boss-pull reset. The healthbar the pull is detected from lags
        the pull itself, so a plain reset lands *after* the opening burst and
        deletes it — the single most interesting part of the parse. Replaying
        the buffered events with their ORIGINAL timestamps keeps the numbers,
        the per-player first/last times and the encounter start honest, rather
        than restamping everything to the moment the bar appeared and reporting
        a burst that took four seconds as instantaneous.

        Returns how many events were carried over, for the log."""
        frozen = None
        with self.lock:
            now = time.time()
            cutoff = now - backlag
            keep = [e for e in self._recent if e[0] >= cutoff]
            # The trash phase, archived before the pull takes the meter. The
            # carried events are counted in BOTH this dataset and the next
            # one: they are already in these aggregates and there is no exact
            # way to subtract them back out of a PlayerAgg. `carried` says so
            # in the file rather than leaving a few seconds of overlap for
            # someone to discover by adding two datasets up.
            frozen = self._freeze(now)
            if frozen is not None:
                frozen["carried"] = len(keep)
                frozen["carried_secs"] = backlag
            self._reset(now)
            self.last_hit = 0.0
            self.in_combat = False
            self.capture_until = self.capture_start = None
            self._recent.clear()
            self.epoch += 1
            for ts, kind, ev in keep:
                self._recent.append((ts, kind, ev))
                p = self._player_for(ev)
                if kind == "hit":
                    if self.enc_start == 0.0:
                        self.enc_start = ts
                    self.last_hit = ts
                    self._apply_hit(p, ev, ts)
                else:
                    p.record_heal(self._skill_of(ev),
                                  float(ev.get("amount", 0.0)),
                                  int(ev.get("crit", 0)),
                                  float(ev.get("landed", 0.0)),
                                  bool(ev.get("self")))
            # Damage was landing, so the player was in combat for the whole
            # replayed stretch. Without this the duration clock would only start
            # at the next UI tick and those seconds would be missing from the
            # divisor — inflating the DPS of the very burst we just rescued.
            if self.enc_start:
                self.active_since = self.enc_start
                self.in_combat = True
        self._emit_archive(frozen)
        return len(keep)

    def _duration(self, now):
        d = self.active_accum
        if self.active_since is not None:
            d += now - self.active_since
        return d

    def combat_of(self, name):
        return bool(self.combat.get(name))

    def current(self):
        """(duration, in_combat) — cheap, reflects the latest clock state."""
        with self.lock:
            return self._effective_duration(time.time()), self.in_combat

    def snapshot(self):
        """Return (duration, in_combat, [PlayerAgg sorted by total desc])."""
        with self.lock:
            duration = self._effective_duration(time.time())
            rows = sorted(self.players.values(), key=lambda p: -p.total)
            import copy
            return duration, self.in_combat, [copy.copy(p) for p in rows]


class RiftRecorder:
    """Captures one rift run for the end-of-rift report.

    Fed the same hit/heal stream as PartySession but never reset by the
    player — its boundaries are the rift's own. Entering the rift starts
    phase 1 (the trash), the boss-pull edge starts phase 2 (the boss), and
    the kill that ends the fight freezes both into a report. Two phases and
    not a running meter, because that's the question the report answers:
    who carries the AoE clear and who carries the single-target, which are
    different players on purpose.

    A run that doesn't end in a kill — walking out, a wipe's loading screen —
    produces nothing. Half a rift isn't a rift report.

    Aggregates everything the hook sends rather than the meter's party/all
    mode: the mode can change mid-rift (the rift prompt exists to change it),
    and a report whose phase 1 and phase 2 counted different sets of players
    would be comparing nothing with nothing."""

    PHASE_LABELS = ("Phase de faille", "Phase du boss")

    def __init__(self):
        self.lock = threading.Lock()
        self.active = False
        self.phase = 0
        self._phases = [self._new_phase(), self._new_phase()]
        # (timestamp, phase, "hit"|"heal", event) — kept so the boss-pull
        # edge can move the opening burst across the phase boundary, same
        # trick (and same measured bar lag) as reset_keeping_recent().
        self._recent: deque = deque(maxlen=RECENT_EVENT_MAX)
        # skill key -> display name, for the per-skill tables below. Kept
        # across the whole rift rather than per phase: a name is a fact about
        # the game, and the boss phase should not have to re-learn one the
        # trash phase already saw.
        self.skill_names: dict[str, str] = {}

    @staticmethod
    def _new_phase():
        # `skills`/`heals` are per player (below); `elements` and `targets`
        # are per phase, because "what did this phase consist of" is a
        # question about the phase and not about any one player.
        return {"players": {}, "elements": defaultdict(float),
                "targets": defaultdict(float), "start": 0.0, "end": 0.0}

    @staticmethod
    def _player_of(ph, name):
        p = ph["players"].get(name)
        if p is None:
            # The per-skill tables carry the same [hits, total, crits] (plus
            # the healing self-share) shape PlayerAgg uses, so a rift dataset
            # and an ordinary encounter dataset render through one code path.
            p = {"name": name, "total": 0.0, "hits": 0, "crits": 0,
                 "kills": 0, "heal": 0.0, "heal_landed": 0.0,
                 "heal_hits": 0,
                 "skills": defaultdict(lambda: [0, 0.0, 0]),
                 "heals": defaultdict(lambda: [0, 0.0, 0, 0.0]),
                 "elements": defaultdict(lambda: [0, 0.0])}
            ph["players"][name] = p
        return p

    def _apply(self, ph, kind, ev, sign):
        """Add (or, for the phase-boundary rewind, subtract) one event. Every
        stat is a plain sum, which is what makes the rewind exact — including
        the per-skill tables, which is why they are sums and not counters."""
        p = self._player_of(ph, ev.get("player") or "?")
        amount = sign * float(ev.get("amount", 0.0))
        sid, nm = _skill_ident(ev)
        if nm and sid not in self.skill_names:
            self.skill_names[sid] = nm
        if kind == "hit":
            p["total"] += amount
            p["hits"] += sign
            p["crits"] += sign * int(ev.get("crit", 0))
            p["kills"] += sign * int(ev.get("kill", 0))
            el = ev.get("element") or "?"
            ph["elements"][el] += amount
            s = p["skills"][sid]
            s[0] += sign; s[1] += amount; s[2] += sign * int(ev.get("crit", 0))
            e = p["elements"][el]
            e[0] += sign; e[1] += amount
            tk = ev.get("target")
            if tk:
                ph["targets"][tk] += amount
        else:
            p["heal"] += amount
            p["heal_landed"] += sign * float(ev.get("landed", 0.0))
            p["heal_hits"] += sign
            h = p["heals"][sid]
            h[0] += sign; h[1] += amount; h[2] += sign * int(ev.get("crit", 0))
            h[3] += amount if ev.get("self") else 0.0

    def set_rift(self, state: bool):
        with self.lock:
            if state:
                self.active = True
                self.phase = 0
                self._phases = [self._new_phase(), self._new_phase()]
                self._phases[0]["start"] = time.time()
                self._recent.clear()
            else:
                # Leaving normally happens after the kill, when the report has
                # already been taken; leaving mid-run abandons the recording.
                self.active = False

    def on_zone(self):
        """A loading screen means the player left the instance — a wipe or a
        walk-out. Whatever was building is not a finished rift."""
        with self.lock:
            self.active = False

    def record(self, kind, ev: dict):
        """kind is "hit" or "heal". Hits arrive already filtered of nullified
        damage — the caller drops those before the meter sees them too."""
        with self.lock:
            if not self.active:
                return
            now = time.time()
            self._recent.append((now, self.phase, kind, ev))
            self._apply(self._phases[self.phase], kind, ev, 1)

    def on_boss_pull(self, backlag=BOSS_PULL_BACKLAG_SECS):
        """The healthbar the pull is detected from lags the pull itself
        (fetchBosses is a 2/s timer), so the opening burst on the boss has
        already been recorded as trash. Move the last few seconds across the
        boundary — measured damage on the boss, miscounted only in which
        column it landed."""
        with self.lock:
            if not self.active or self.phase != 0:
                return
            self.phase = 1
            now = time.time()
            boundary = now
            cutoff = now - backlag
            for ts, ph, kind, ev in self._recent:
                if ph == 0 and ts >= cutoff:
                    self._apply(self._phases[0], kind, ev, -1)
                    self._apply(self._phases[1], kind, ev, 1)
                    boundary = min(boundary, ts)
            # The boundary is where the earliest moved event landed, not where
            # the bar rose — the durations should agree with the totals.
            self._phases[0]["end"] = boundary
            self._phases[1]["start"] = boundary

    def on_boss_kill(self):
        """The kill that ended the fight. Returns the finished report as plain
        data (safe to hand to the Tk thread), or None if nothing was recording.
        One report per rift: taking it stops the recording, so the walk to the
        exit portal can't dribble into the boss column."""
        with self.lock:
            if not self.active:
                return None
            self.active = False
            now = time.time()
            self._phases[self.phase]["end"] = now
            phases = []
            used_skills = set()
            for label, ph in zip(self.PHASE_LABELS, self._phases):
                players = sorted((dict(p) for p in ph["players"].values()),
                                 key=lambda p: -p["total"])
                # The rewind leaves float dust (and a player who only acted in
                # the moved window ends up all-zero) — drop empty rows rather
                # than showing "0" lines.
                players = [p for p in players
                           if p["total"] > 0.5 or p["heal"] > 0.5]
                # The rewind subtracts, so a skill moved wholesale to the next
                # phase is left behind as a zero row. Same dust, same rule.
                for p in players:
                    for key in ("skills", "heals", "elements"):
                        p[key] = {k: list(v) for k, v in p[key].items()
                                  if abs(v[1]) > 0.5}
                    used_skills.update(p["skills"])
                    used_skills.update(p["heals"])
                total = sum(p["total"] for p in players)
                heal = sum(p["heal"] for p in players)
                heal_landed = sum(p["heal_landed"] for p in players)
                elements = sorted(((el, amt) for el, amt
                                   in ph["elements"].items() if amt > 0.5),
                                  key=lambda kv: -kv[1])
                start, end = ph["start"] or now, ph["end"] or now
                phases.append({"label": label,
                               "duration": max(0.0, end - start),
                               "players": players, "total": total,
                               "heal": heal, "heal_landed": heal_landed,
                               "elements": elements,
                               "targets": {k: v for k, v
                                           in ph["targets"].items()
                                           if v > 0.5}})
            # `skill_names` outlives one rift, so only the names this report
            # can actually use are written into it — see PartySession._freeze.
            return {"at": now, "phases": phases,
                    "skill_names": {sid: nm for sid, nm
                                    in self.skill_names.items()
                                    if sid in used_skills}}


class DungeonRecorder(RiftRecorder):
    """The rift recorder's two-phase capture, for a dungeon: the exploration,
    then the boss. Its boundaries come from the game's dungeon state rather
    than from the boss bar — see DungeonTracker."""

    PHASE_LABELS = ("Exploration", "Phase du boss")


# The dungeon difficulty as the instance lobby stores it (measured: the value
# followed the Normal/Difficile toggle in the lobby).
DUNGEON_DIFFICULTIES = {0: "Normal", 1: "Difficile", 2: "Héroïque"}
# How long a difficulty seen in a lobby is trusted for the run that follows.
DUNGEON_LOBBY_TTL = 30 * 60
# A run left this soon without reaching the boss is not worth keeping.
DUNGEON_MIN_SECS = 30


def dungeon_name(kind):
    """The dungeon's French name, as the game shows it
    ("R1_POI_CleodorasNest" -> "Tronc-ruche d'Élizabeille"); the prettified
    id when the game's translation doesn't have it."""
    fr = _fr_names("activity").get(str(kind or ""))
    if fr:
        return fr
    s = re.sub(r"^R\d+_POI_(Dungeon_)?", "", str(kind or ""))
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s.replace("_", " "))
    return " ".join(s.split()) or "Donjon"


class DungeonTracker:
    """Follows one dungeon run from the hook's `dungeon` messages.

    Measured (a Manfish Ruins run, 2026-09-28): the active activity is an
    st.activity.Dungeon; its state lives on the player's DungeonContext and
    goes Explo -> BossStart -> BossPhase -> BossWin (then back to Explo); the
    context's `end` is the instance clock at the kill, which is the run's
    time (the clock starts at the instance's creation); `start` stays -1 on a
    client. The difficulty exists only on the instance lobby, which vanishes
    at launch — so the last one seen is remembered for the run that follows.

    Runs on the hook's thread; a finished run is handed to the app."""

    def __init__(self, world):
        self.world = world
        self.rec = DungeonRecorder()
        self.lobbies = {}           # activity id -> (difficulty, seen at)
        self.run = None

    def record(self, kind, ev):
        self.rec.record(kind, ev)

    def update(self, d):
        now = time.time()
        for lb in d.get("lobbies") or ():
            if lb.get("a") is not None:
                self.lobbies[lb["a"]] = (lb.get("d"), now)
        in_dungeon = d.get("type") == "st.activity.Dungeon"
        kind = d.get("kind")
        if self.run and (not in_dungeon or kind != self.run["kind"]):
            self._leave()
        if not in_dungeon:
            return
        if self.run is None:
            diff, seen = self.lobbies.get(kind, (None, 0))
            if now - seen > DUNGEON_LOBBY_TTL:
                diff = None
            self.run = {"kind": kind, "boss": d.get("bossId") or "",
                        "difficulty": diff, "state": None, "wipes": 0,
                        "deaths": 0, "clock": 0.0, "done": False,
                        "boss_seen": False, "loot": [], "file": None}
            self.rec.set_rift(True)
            print(f"[dungeon] entered {kind} (difficulty "
                  f"{DUNGEON_DIFFICULTIES.get(diff, '?')})", file=sys.stderr)
        run = self.run
        if isinstance(d.get("now"), (int, float)):
            run["clock"] = float(d["now"])
        ctx = next((c for c in d.get("playerCtx") or ()
                    if c.get("type") == "st.activity.DungeonContext"), None)
        if not ctx:
            return
        if isinstance(ctx.get("deaths"), int):
            run["deaths"] = ctx["deaths"]
        state = ctx.get("state")
        if state == run["state"]:
            return
        print(f"[dungeon] state {run['state']} -> {state} at "
              f"{run['clock']:.1f}s", file=sys.stderr)
        run["state"] = state
        if state in ("BossStart", "BossPhase") and not run["boss_seen"]:
            run["boss_seen"] = True
            self.rec.on_boss_pull(backlag=1.0)
        elif state == "BossLoose":
            run["wipes"] += 1
        elif state == "BossWin" and not run["done"]:
            end = ctx.get("end")
            self._finish("victoire",
                         end if isinstance(end, (int, float)) and end > 0
                         else run["clock"])

    def pickup(self, p):
        """An item that entered the local player's bags (the hook's inventory
        sweep). Kept on the run, tagged with the phase it came in: after the
        boss's death it is the reward chest's."""
        run = self.run
        if run is None or not p.get("item"):
            return
        phase = ("coffre" if run["done"] else
                 "boss" if run["boss_seen"] else "exploration")
        run["loot"].append({"item": p["item"], "rarity": p.get("rarity"),
                            "level": p.get("level"),
                            "count": int(p.get("count") or 1),
                            "phase": phase})
        print(f"[dungeon] loot ({phase}): {p.get('count')}x {p['item']} "
              f"{p.get('rarity') or ''}", file=sys.stderr)
        # A finished run is already saved: rewrite it with the chest's loot.
        ov = _OVERLAY["ref"]
        if run["done"] and run["file"] and ov is not None:
            ov.on_dungeon_loot(run["file"], list(run["loot"]))

    def _leave(self):
        run, self.run = self.run, None
        if run["done"]:
            return
        if run["clock"] < DUNGEON_MIN_SECS and not run["boss_seen"]:
            self.rec.on_zone()
            return
        self.run = run                  # _finish reads it
        self._finish("échec" if run["wipes"] or run["boss_seen"] else "abandon",
                     run["clock"])
        self.run = None

    def disconnect(self):
        if self.run is not None:
            self._leave()

    def _finish(self, result, duration):
        run = self.run
        run["done"] = True
        report = self.rec.on_boss_kill()
        if report is None:
            return
        _stamp_report_classes(report, self.world)
        run["file"] = f"run-{time.strftime('%Y%m%d-%H%M%S')}.json"
        report.update({
            "file": run["file"], "loot": list(run["loot"]),
            "type": "dungeon", "kind": run["kind"],
            "name": dungeon_name(run["kind"]), "boss": run["boss"],
            "difficulty": run["difficulty"], "result": result,
            "duration": float(duration), "deaths": run["deaths"],
            "wipes": run["wipes"]})
        print(f"[dungeon] {run['kind']} {result} in {duration:.1f}s "
              f"({run['deaths']} deaths, {run['wipes']} wipes)", file=sys.stderr)
        ov = _OVERLAY["ref"]
        if ov is not None:
            ov.on_dungeon_run(report)


# The constant every SteamID64 is built on: the individual-account block.
# SteamID64 = STEAM64_BASE + account_id.
STEAM64_BASE = 76561197960265728


class WorldSnapshot:
    """Who you are, who is in your group, and which class every player is.

    Fed by two hook messages: `hero` (the local hero plus the group roster the
    meter's party filter reads) and `shard` (every player the client holds
    state for, each with its class). The meter's own rows come from damage
    events, which carry no class, so this is where the class tag comes from."""

    def __init__(self):
        self._lock = threading.Lock()
        self.party = frozenset()
        self.local = None
        # name -> class ("Warrior", "Mage", ...). Kept rather than replaced
        # wholesale: a player who leaves the layer shouldn't lose their tag on
        # the meter while their damage is still on it.
        self.classes = {}

    def set_hero(self, name, party):
        with self._lock:
            if name:
                self.local = name
            self.party = frozenset(party or ())

    def set_shard(self, rows):
        with self._lock:
            for r in rows or ():
                if r.get("n") and r.get("k"):
                    self.classes[r["n"]] = r["k"]

    def who(self):
        with self._lock:
            return self.local, self.party

    def class_of(self, name):
        with self._lock:
            return self.classes.get(name)


class GameUIState:
    """Which of the game's own UI windows are open, streamed by the hook.

    The hook watches ui.BaseUI.displayWindow/removeWindow — the game's window
    manager — and reports each window class as it opens and closes. That lets
    the overlay react to the game's UI (escape menu open => unlock) instead of
    making the player remember a key."""

    def __init__(self):
        self._lock = threading.Lock()
        self._open: set[str] = set()
        self._rift = False
        self._unlock_at = None         # when the game's escape menu opened
        self._boss_bar = 0
        self._zone_sig = None
        # The game's own `World._isWorldMap`. None until a zone message has
        # carried it — which is not the same as False, and a history dataset
        # would otherwise call an unknown zone a dungeon.
        self._zone_world_map = None
        # Which shard the character is on — st.GameLayer.serverName, sent by
        # the hook once at attach and then on every change. None until it
        # arrives, which is a real state worth distinguishing from "no shard":
        # the settings panel says "..." rather than claiming to know.
        self._server = None

    def set_zone(self, sig, world_map=None):
        """layer.world.level from the hook — the loaded level's name, sent
        once at attach and then on every change. (Its predecessor,
        Main.getMapId(), turned out to return the machine hostname.)

        `world_map` is the same message's `_isWorldMap`: 1 in an overworld
        region, 0 in a dungeon or a rift. It is what lets a history dataset
        say "Siagarta Overworld" without pattern-matching the path."""
        with self._lock:
            self._zone_sig = sig or None
            self._zone_world_map = (None if world_map is None
                                    else bool(world_map))

    def set_server(self, name):
        """st.GameLayer.serverName from the hook — which shard you are on.

        Measured 2026-08-05: reads "Sfojuxa3386_6601_na", and a relog to
        character select and back moved it to "Snitura2642_6306_na" while the
        zone stayed "World/W1_Siagarta" throughout. So it names the shard, not
        the zone, and the trailing "_na" is the server region.

        Stored raw. Prettifying it would risk two genuinely different shards
        rendering the same, and the whole use of this string is comparing it
        with somebody else's."""
        with self._lock:
            self._server = name or None

    def server(self):
        """The shard name, or None if the hook hasn't reported one yet."""
        with self._lock:
            return self._server






    def zone(self):
        """(sig, world_map) together — read as a pair because a history
        dataset names itself from both, and reading them one lock at a time
        could straddle a loading screen."""
        with self._lock:
            return self._zone_sig, self._zone_world_map

    def set_rift(self, state: bool):
        with self._lock:
            self._rift = bool(state)

    def in_rift(self) -> bool:
        with self._lock:
            return self._rift

    def set_boss_bar(self, count: int):
        """How many of the game's own boss/elite healthbars are on screen.

        The hook reads ui.hud.BossesInfo, so this counts bars the player can
        actually see — it goes to zero when they walk away and the boss resets,
        not just when something dies."""
        with self._lock:
            self._boss_bar = max(0, int(count))


    def clear(self):
        with self._lock:
            self._open.clear()
            # A loading screen tears the HUD down with it, so a bar that was up
            # on the way out must not leave the compass hidden in the new zone.
            self._boss_bar = 0




# ---------------------------------------------------------------------------
# Display scaling
# ---------------------------------------------------------------------------
# Windows scales an application that never says otherwise. This one never did:
# there is no dpiAware entry in the shipped manifest and no awareness call
# anywhere, so at a 300% system scale the desktop composer bitmap-stretched
# every overlay window to three times its size and blurred it on the way. The
# size sliders could not fight that, because the stretch happens after Tk has
# finished drawing.
#
# Declaring per-monitor-v2 turns the stretching off. It also puts every
# coordinate this process handles into one space — ours AND the game's, since
# _window_rect_of_pid asks Windows for the game's rect and an unaware process
# is handed a virtualised answer.
DPI_PER_MONITOR_V2 = -4
# Windows' own reference DPI. A scale factor is whatever the monitor reports
# divided by this.
USER_DEFAULT_SCREEN_DPI = 96


def declare_dpi_awareness():
    """Opt out of Windows' bitmap stretching. Returns what was achieved.

    Must run before this process owns its first window — the tray icon's, Tk's,
    or a message box's — because awareness is latched at that moment and cannot
    be changed afterwards. Each fallback is a older-Windows entry point for the
    same idea, tried newest first.
    """
    if sys.platform != "win32":
        return "not windows"
    u = ctypes.windll.user32
    try:                                  # Windows 10 1703 and later
        u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if u.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(DPI_PER_MONITOR_V2)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:                                  # Windows 8.1 .. 10 1607
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:                                  # Vista .. 8.0
        return "system" if u.SetProcessDPIAware() else "unaware"
    except (AttributeError, OSError):
        return "unaware"


def display_scale():
    """The primary monitor's scale factor: 1.0 at 100%, 3.0 at 300%.

    Only meaningful once awareness is declared — an unaware process is told 96
    whatever the user chose, which is the whole point of being unaware. Used to
    migrate window positions saved by a build that had not declared it.
    """
    if sys.platform != "win32":
        return 1.0
    try:
        dc = ctypes.windll.user32.GetDC(0)
        try:
            # LOGPIXELSX = 88
            dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)
        finally:
            ctypes.windll.user32.ReleaseDC(0, dc)
        return (dpi / USER_DEFAULT_SCREEN_DPI) if dpi else 1.0
    except Exception:
        return 1.0


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _monitor_work_areas():
    """Every monitor's work area (the screen minus the taskbar), as
    (left, top, right, bottom) in physical pixels. Empty off Windows."""
    if sys.platform != "win32":
        return []
    out = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def visit(hmon, _hdc, _rect, _data):
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            out.append((r.left, r.top, r.right, r.bottom))
        return True

    try:
        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(visit), 0)
    except Exception:
        return []
    return out


def _monitor_containing(x, y):
    """The work area of the monitor holding (x, y), or None."""
    for r in _monitor_work_areas():
        if r[0] <= x < r[2] and r[1] <= y < r[3]:
            return r
    return None


def _window_rect_of_pid(pid):
    """(left, top, right, bottom) of a process's largest visible top-level
    window, or None. Used to centre the control menu on the game rather than on
    whichever monitor Windows calls primary."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                           ctypes.POINTER(wintypes.DWORD)]
    best = {"area": 0, "rect": None}
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                     wintypes.LPARAM)

    def visit(hwnd, _lparam):
        wpid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value != pid or not u.IsWindowVisible(hwnd):
            return True
        r = wintypes.RECT()
        if u.GetWindowRect(hwnd, ctypes.byref(r)):
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > best["area"]:
                best["area"] = area
                best["rect"] = (r.left, r.top, r.right, r.bottom)
        return True

    try:
        u.EnumWindows(WNDENUMPROC(visit), 0)
    except Exception:
        return None
    return best["rect"] if best["area"] > 0 else None


def _process_age(pid):
    """Seconds since the process started, or None if Windows won't say."""
    if sys.platform != "win32":
        return None
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wintypes.HANDLE
    h = k.OpenProcess(0x1000, False, pid)      # QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        made, x1, x2, x3 = (wintypes.FILETIME() for _ in range(4))
        if not k.GetProcessTimes(h, ctypes.byref(made), ctypes.byref(x1),
                                 ctypes.byref(x2), ctypes.byref(x3)):
            return None
        now = wintypes.FILETIME()
        k.GetSystemTimeAsFileTime(ctypes.byref(now))
        ft = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime
        return (ft(now) - ft(made)) / 1e7
    finally:
        k.CloseHandle(h)


VK_OEM_5 = 0xDC                      # the \ key
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
WH_KEYBOARD_LL, WH_MOUSE_LL, HC_ACTION = 13, 14, 0
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_MBUTTONDOWN, WM_XBUTTONDOWN = 0x0207, 0x020B
WM_HOTKEY = 0x0312
WM_REBIND = 0x0400 + 1               # WM_APP+1, posted to the hotkey thread
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000
# Thread id of the RegisterHotKey fallback's pump, or 0 while the low-level
# hook is doing the work (which needs no re-registration at all).
REBIND_TO = [0]

# Shift+\ (reset the encounter) is the only key the meter still owns — every
# other control moved onto the in-game control menu. Plain \ and / are left
# alone now so the game keeps them.
HK_RESET = 1

# The reset keybind, rebindable from the control menu. A dict rather than a
# constant because the hook thread reads it on every keypress: rebinding is
# then a matter of writing new values here, with no hook to tear down and
# reinstall. Mutated in place for the same reason — the thread closed over this
# object, not over the name.
RESET_BIND = {"vk": VK_OEM_5, "shift": True, "ctrl": False, "alt": False}
# Virtual-key codes whose names aren't derivable. Everything else falls back to
# its character (A-Z, 0-9 are their own VK) or a bare hex code, so an unusual
# keyboard shows something rather than nothing.
VK_NAMES = {
    0x08: "Retour arrière", 0x09: "Tab", 0x0D: "Entrée", 0x13: "Pause",
    0x14: "Verr. Maj", 0x1B: "Échap", 0x20: "Espace", 0x21: "Page préc.",
    0x22: "Page suiv.", 0x23: "Fin", 0x24: "Début", 0x25: "Gauche",
    0x26: "Haut", 0x27: "Droite", 0x28: "Bas", 0x2D: "Inser", 0x2E: "Suppr",
    0x6A: "Pavé *", 0x6B: "Pavé +", 0x6D: "Pavé -", 0x6E: "Pavé .",
    0x6F: "Pavé /",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}
VK_NAMES.update({0x60 + i: f"Pavé {i}" for i in range(10)})
VK_NAMES.update({0x70 + i: f"F{i + 1}" for i in range(24)})
# Mouse buttons are bindable too, but only these three. Left and right belong
# to the game and always will; the hook SWALLOWS whatever it fires on, and
# taking left-click away from somebody mid-fight is not a setting, it's a
# hostage situation. Middle and the two side buttons are fair game.
VK_MOUSE = {0x04: "Clic milieu", 0x05: "Souris 4", 0x06: "Souris 5"}
VK_NAMES.update(VK_MOUSE)
# Modifiers can't be the key itself, and Escape is how you back out of the
# capture — binding it would leave no way to cancel.
VK_UNBINDABLE = frozenset({0x10, 0x11, 0x12, 0x1B, 0x5B, 0x5C,
                           0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5})


def _vk_name(vk):
    if vk in VK_NAMES:
        return VK_NAMES[vk]
    if 0x30 <= vk <= 0x5A:          # 0-9 and A-Z share their ASCII codes
        return chr(vk)
    return f"VK {vk:#04x}"


def bind_label(bind=None):
    """"Shift + \\" — what the menu button and the floating hint both show, so
    they can't drift apart."""
    b = bind or RESET_BIND
    parts = [n for n, k in (("Ctrl", "ctrl"), ("Alt", "alt"), ("Maj", "shift"))
             if b.get(k)]
    parts.append(_vk_name(b.get("vk", VK_OEM_5)))
    return " + ".join(parts)


# ---------------------------------------------------------------------------
# Version / update check
# ---------------------------------------------------------------------------
# Bump this on every release, and tag the repo with the same string — it's the
# left-hand side of the comparison below, so a release that forgets it tells
# everyone they're out of date forever.
VERSION = "1.0.0"



def start_hotkeys(callbacks: dict, target_pid):
    """Run the keyboard hook that owns Shift+\\, on its own thread with its own
    message pump. `target_pid` may be a callable: the game can start, close
    and start again while the meter runs."""
    if sys.platform != "win32":
        return

    def _game_pid():
        return target_pid() if callable(target_pid) else target_pid

    def pump():
        from ctypes import wintypes
        u = ctypes.windll.user32
        LRESULT = ctypes.c_ssize_t
        HHOOK = ctypes.c_void_p

        class KBD(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]

        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        u.SetWindowsHookExW.restype = HHOOK
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
        u.CallNextHookEx.restype = LRESULT
        u.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetAsyncKeyState.restype = ctypes.c_short

        keys_down = set()

        def pressed(vk):
            return bool(u.GetAsyncKeyState(vk) & 0x8000)

        def fg_pid():
            h = u.GetForegroundWindow()
            if not h:
                return 0
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            return pid.value

        def proc(nCode, wParam, lParam):
            if nCode != HC_ACTION:
                return u.CallNextHookEx(None, nCode, wParam, lParam)
            kbd = ctypes.cast(lParam, ctypes.POINTER(KBD))[0]
            vk = kbd.vkCode
            if wParam in (WM_KEYUP, WM_SYSKEYUP):
                keys_down.discard(vk)
                return u.CallNextHookEx(None, nCode, wParam, lParam)
            if wParam not in (WM_KEYDOWN, WM_SYSKEYDOWN) or vk in keys_down:
                return u.CallNextHookEx(None, nCode, wParam, lParam)
            keys_down.add(vk)
            # The bound key only, and only while Farever has focus. Everything
            # else falls through untouched so the game keeps its own bindings —
            # which matters more than usual here, because the branch below
            # SWALLOWS the keypress.
            #
            # RESET_BIND is read fresh every time rather than captured: that's
            # what makes rebinding take effect immediately instead of at the
            # next launch.
            if vk != RESET_BIND.get("vk") or fg_pid() != _game_pid():
                return u.CallNextHookEx(None, nCode, wParam, lParam)
            # Every modifier has to match exactly — a binding of Shift+\ must
            # not fire on Ctrl+Shift+\, which is somebody else's shortcut.
            if (pressed(VK_SHIFT) != bool(RESET_BIND.get("shift"))
                    or pressed(VK_CONTROL) != bool(RESET_BIND.get("ctrl"))
                    or pressed(VK_MENU) != bool(RESET_BIND.get("alt"))):
                return u.CallNextHookEx(None, nCode, wParam, lParam)
            cb = callbacks.get(HK_RESET)
            if cb:
                try:
                    cb()
                except Exception as e:
                    print("[hotkey]", e, file=sys.stderr)
            return 1

        class MSLL(ctypes.Structure):
            _fields_ = [("pt_x", wintypes.LONG), ("pt_y", wintypes.LONG),
                        ("mouseData", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]

        def mouse_proc(nCode, wParam, lParam):
            # A separate hook because WH_KEYBOARD_LL cannot see mouse buttons
            # at all — nor can RegisterHotKey, which is why a mouse binding
            # only works on this path.
            # First line, and it matters: a low-level mouse hook is called for
            # every WM_MOUSEMOVE too, which on a 1000Hz mouse is a thousand
            # trips into Python a second, each one in front of the input it's
            # inspecting. Everything that isn't a button press leaves here.
            if wParam not in (WM_MBUTTONDOWN, WM_XBUTTONDOWN):
                return u.CallNextHookEx(None, nCode, wParam, lParam)
            if nCode != HC_ACTION:
                return u.CallNextHookEx(None, nCode, wParam, lParam)
            vk = 0
            if wParam == WM_MBUTTONDOWN:
                vk = 0x04
            elif wParam == WM_XBUTTONDOWN:
                # Which side button is in the HIGH word of mouseData: 1 or 2.
                ms = ctypes.cast(lParam, ctypes.POINTER(MSLL))[0]
                vk = 0x04 + ((ms.mouseData >> 16) & 0xFFFF)     # -> 0x05, 0x06
            if vk != RESET_BIND.get("vk") or fg_pid() != _game_pid():
                return u.CallNextHookEx(None, nCode, wParam, lParam)
            if (pressed(VK_SHIFT) != bool(RESET_BIND.get("shift"))
                    or pressed(VK_CONTROL) != bool(RESET_BIND.get("ctrl"))
                    or pressed(VK_MENU) != bool(RESET_BIND.get("alt"))):
                return u.CallNextHookEx(None, nCode, wParam, lParam)
            cb = callbacks.get(HK_RESET)
            if cb:
                try:
                    cb()
                except Exception as e:
                    print("[hotkey]", e, file=sys.stderr)
            return 1

        cproc = HOOKPROC(proc)
        cmproc = HOOKPROC(mouse_proc)
        hMod = ctypes.windll.kernel32.GetModuleHandleW(None)
        hook = u.SetWindowsHookExW(WH_KEYBOARD_LL, cproc, hMod, 0)
        from ctypes import wintypes
        if hook:
            # Installed unconditionally rather than only when a mouse button is
            # bound: the binding can change at any moment from the menu, and a
            # hook that has to be installed from this thread can't be added
            # later without waking it up.
            if not u.SetWindowsHookExW(WH_MOUSE_LL, cmproc, hMod, 0):
                print("[meter] mouse hook failed; mouse buttons can't be bound.",
                      file=sys.stderr)
            print("[meter] focus-conditional hotkeys active.", file=sys.stderr)
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
            return
        # ---- Fallback: global RegisterHotKey (fires regardless of focus) ----
        print("[meter] LL hook failed; using global RegisterHotKey fallback.",
              file=sys.stderr)

        def register():
            u.UnregisterHotKey(None, HK_RESET)
            mods = MOD_NOREPEAT
            if RESET_BIND.get("shift"):
                mods |= MOD_SHIFT
            if RESET_BIND.get("ctrl"):
                mods |= MOD_CONTROL
            if RESET_BIND.get("alt"):
                mods |= MOD_ALT
            if not u.RegisterHotKey(None, HK_RESET, mods,
                                    RESET_BIND.get("vk", VK_OEM_5)):
                print(f"[meter] {bind_label()} unavailable (another app owns "
                      "it) — the encounter still resets itself on a zone "
                      "change or after a lull, but the manual reset won't "
                      "fire.", file=sys.stderr)

        register()
        # RegisterHotKey belongs to the thread that called it, so a rebind
        # can't just re-register from the Tk thread. The overlay posts
        # WM_REBIND here instead and this thread does it — see _rebind_reset.
        REBIND_TO[0] = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_REBIND:
                register()
                continue
            if msg.message == WM_HOTKEY:
                cb = callbacks.get(msg.wParam)
                if cb:
                    try:
                        cb()
                    except Exception as e:
                        print("[hotkey]", e, file=sys.stderr)
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))

    threading.Thread(target=pump, daemon=True, name="hotkeys").start()


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------
# With no console there is no Ctrl+C, and the overlay windows are borderless and
# click-through — so without this there would be no way to stop the meter except
# Task Manager, which is exactly the force-kill that leaves a half-attached
# agent in the game. The icon exists to make the clean exit reachable.
#
# Hand-rolled on ctypes rather than pystray: the file already talks to user32
# directly for click-through, hotkeys and window enumeration, and a tray icon is
# one window and one message pump. It also keeps `pip install frida` as the only
# thing a from-source run needs.
ICON_FILE = ROOT / "assets" / "farevermeter.ico"




WM_TRAY = 0x0400 + 1                      # WM_APP + 1
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO = 0x01
WM_DESTROY, WM_CLOSE, WM_COMMAND = 0x0002, 0x0010, 0x0111
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
SM_CXSMICON, SM_CYSMICON = 49, 50

TRAY_QUIT, TRAY_LOG, TRAY_PARSES = 1001, 1002, 1003
TRAY_SETTINGS = 1004

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    """The full (Vista+) layout. cbSize is set to sizeof(), which is what tells
    the shell which version it's being handed."""
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
                ("hBalloonIcon", wintypes.HICON)]


class TrayIcon:
    """A notification-area icon whose menu holds the clean shutdown.

    Owns a hidden window on its own thread: tray callbacks are window messages,
    and they're delivered to the thread that created the window, so it needs a
    pump of its own rather than sharing Tk's."""

    def __init__(self, on_quit, tip="Farever France"):
        self.on_quit = on_quit
        self.tip = tip
        self.hwnd = None
        self._ready = threading.Event()
        self._thread = None

    # ---- lifecycle ----
    def start(self):
        if sys.platform != "win32":
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="tray")
        self._thread.start()
        # Bounded: a tray that fails to come up must not stop the meter from
        # starting — the in-game menu's Quit button is the other way out.
        self._ready.wait(timeout=5.0)

    def stop(self):
        """Called from the Tk thread on the way out. PostMessage rather than a
        direct call because the window belongs to the tray thread."""
        if self.hwnd:
            try:
                ctypes.windll.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass

    # ---- internals ----
    def _load_icon(self):
        u = ctypes.windll.user32
        if ICON_FILE.is_file():
            h = u.LoadImageW(None, str(ICON_FILE), IMAGE_ICON,
                             u.GetSystemMetrics(SM_CXSMICON),
                             u.GetSystemMetrics(SM_CYSMICON), LR_LOADFROMFILE)
            if h:
                return h
            print(f"[tray] couldn't load {ICON_FILE} — using the stock icon.",
                  file=sys.stderr)
        return u.LoadIconW(None, ctypes.c_wchar_p(32512))   # IDI_APPLICATION

    def _notify(self, action, data):
        return bool(ctypes.windll.shell32.Shell_NotifyIconW(action,
                                                            ctypes.byref(data)))

    def _base_data(self):
        d = NOTIFYICONDATAW()
        d.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        d.hWnd = self.hwnd
        d.uID = 1
        return d

    def _add(self):
        d = self._base_data()
        d.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        d.uCallbackMessage = WM_TRAY
        d.hIcon = self.hicon
        d.szTip = self.tip
        self._notify(NIM_ADD, d)

    def _balloon(self, title, text):
        """Windows 11 files a brand-new tray icon into the overflow flyout by
        default, so a first-run user would never find it. The toast is what
        tells them it's there — and how to get it back."""
        d = self._base_data()
        d.uFlags = NIF_INFO
        d.szInfoTitle = title
        d.szInfo = text
        d.dwInfoFlags = NIIF_INFO
        self._notify(NIM_MODIFY, d)

    def _menu(self):
        u = ctypes.windll.user32
        m = u.CreatePopupMenu()
        u.AppendMenuW(m, MF_STRING, TRAY_SETTINGS, "Afficher Farever France")
        u.AppendMenuW(m, MF_STRING, TRAY_PARSES, "Ouvrir le dossier des parses")
        u.AppendMenuW(m, MF_STRING, TRAY_LOG, "Ouvrir le dossier du journal")
        u.AppendMenuW(m, MF_SEPARATOR, 0, None)
        u.AppendMenuW(m, MF_STRING, TRAY_QUIT, "Arrêter le compteur")
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        # Required by TrackPopupMenu, or the menu refuses to close when the user
        # clicks away from it.
        u.SetForegroundWindow(self.hwnd)
        cmd = u.TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                               pt.x, pt.y, 0, self.hwnd, None)
        u.PostMessageW(self.hwnd, 0x0000, 0, 0)     # WM_NULL, same reason
        u.DestroyMenu(m)
        return cmd

    def _on_command(self, cmd):
        if cmd == TRAY_QUIT:
            self.on_quit()
        elif cmd == TRAY_SETTINGS:
            # The tray runs on its own thread; the overlay's Tk work has to be
            # queued onto the Tk one.
            ov = _OVERLAY["ref"]
            if ov is not None:
                ov._enqueue(ov.open_settings_from_tray)()
        elif cmd == TRAY_LOG:
            try:
                DATA_HOME.mkdir(parents=True, exist_ok=True)
                os.startfile(DATA_HOME)
            except Exception as e:
                print(f"[tray] couldn't open {DATA_HOME}: {e}", file=sys.stderr)
        elif cmd == TRAY_PARSES:
            try:
                PARSES_DIR.mkdir(parents=True, exist_ok=True)
                os.startfile(PARSES_DIR)
            except Exception as e:
                print(f"[tray] couldn't open {PARSES_DIR}: {e}", file=sys.stderr)

    @staticmethod
    def _prototypes():
        """Declare every call this class makes.

        Not optional on 64-bit: ctypes defaults an unprototyped argument to C
        int, so any handle or pointer — a module handle, an lParam carrying a
        struct — overflows on the way through. Declared here in one place
        rather than at each call site, because the failure mode is a call that
        looks correct and raises at runtime on some machines and not others."""
        u, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        k32.GetModuleHandleW.restype = ctypes.c_void_p
        k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM]
        u.RegisterClassW.restype = wintypes.ATOM
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
        u.LoadImageW.restype = ctypes.c_void_p
        u.LoadImageW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                 wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                 wintypes.UINT]
        u.LoadIconW.restype = ctypes.c_void_p
        u.LoadIconW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        u.CreatePopupMenu.restype = ctypes.c_void_p
        u.AppendMenuW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                  ctypes.c_size_t, wintypes.LPCWSTR]
        u.TrackPopupMenu.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     wintypes.HWND, ctypes.c_void_p]
        u.DestroyMenu.argtypes = [ctypes.c_void_p]
        shell = ctypes.windll.shell32
        shell.Shell_NotifyIconW.restype = wintypes.BOOL
        shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                            ctypes.POINTER(NOTIFYICONDATAW)]

    def _run(self):
        u = ctypes.windll.user32
        self._prototypes()
        # Explorer drops every tray icon when it restarts and broadcasts this to
        # ask for them back. Without it an explorer crash silently costs the
        # user their only way to stop the meter.
        taskbar_created = u.RegisterWindowMessageW("TaskbarCreated")

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAY:
                if lparam in (WM_RBUTTONUP, WM_LBUTTONUP):
                    self._on_command(self._menu())
                return 0
            if msg == WM_COMMAND:
                # The popup is read with TPM_RETURNCMD, so a click comes back
                # from _menu() rather than through here. This is the standard
                # route into the same commands for anything that drives the icon
                # by message — and it's how the shutdown path gets tested
                # without a human clicking the menu.
                self._on_command(wparam & 0xFFFF)
                return 0
            if msg == taskbar_created:
                self._add()
                return 0
            if msg == WM_CLOSE:
                d = self._base_data()
                self._notify(NIM_DELETE, d)
                u.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                u.PostQuitMessage(0)
                return 0
            return u.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)      # kept alive: Windows holds a raw
        cls = WNDCLASSW()                     # pointer to it for the window's life
        cls.lpfnWndProc = self._wndproc
        cls.lpszClassName = "FareverMeterTray"
        cls.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        try:
            if not u.RegisterClassW(ctypes.byref(cls)):
                raise OSError(ctypes.get_last_error())
            u.CreateWindowExW.restype = wintypes.HWND
            self.hwnd = u.CreateWindowExW(0, "FareverMeterTray", "Farever France tray",
                                          0, 0, 0, 0, 0, None, None,
                                          cls.hInstance, None)
            if not self.hwnd:
                raise OSError("CreateWindowExW failed")
            self.hicon = self._load_icon()
            self._add()
        except Exception as e:
            print(f"[tray] icon unavailable ({e}) — use the control menu's Quit "
                  "button to stop the meter.", file=sys.stderr)
            self._ready.set()
            return
        print("[meter] tray icon active.", file=sys.stderr)
        self._balloon("Farever France est lancé",
                      "Clic droit sur cette icône pour l'arrêter. Si elle est "
                      "masquée, clique sur la flèche ^ près de l'horloge et "
                      "fais-la glisser dans la barre des tâches.")
        self._ready.set()
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))


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


# ---------------------------------------------------------------------------
# The application window
# ---------------------------------------------------------------------------
# One window, meant for a second screen. Tab ids are what the window sends
# back; the labels are what it shows.
APP_TABS = ("Live", "Rifts", "Dungeons", "Collection", "Hunt", "Map",
            "Achievements", "Character", "Settings", "Help")
APP_TABS_APP_FIRST = "Settings"     # the first tab about the app, not the game
APP_TAB_LABELS = {"Live": "En direct", "Rifts": "Failles",
                  "Dungeons": "Donjons", "Collection": "Collection",
                  "Hunt": "Chasse", "Map": "Carte",
                  "Achievements": "Succès",
                  "Character": "Inspecter",
                  "Settings": "Réglages",
                  "Help": "Aide"}
APP_TAB_DEFAULT = "Live"
# Class tags written into reports before the interface was translated.
OLD_CLASS_TAGS = {"War": "Gue", "Pst": "Prê", "Rog": "Vol"}
EVENTS_MAX = 40             # lines kept in the live page's event feed


def _mmss(secs):
    m, s = divmod(int(round(max(0, secs))), 60)
    return f"{m}:{s:02d}"


def _elide_name(name, width=14):
    return name if len(name) <= width else name[:width - 1] + "…"


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


class _NoWindow:
    """Stands in for a Tk window the carried-over code still pokes."""

    def __getattr__(self, _name):
        return lambda *a, **k: None


def copy_text_to_clipboard(text):
    """Put text on the Windows clipboard. True on success."""
    if sys.platform != "win32":
        return False
    data = (str(text) + "\0").encode("utf-16-le")
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = (ctypes.c_void_p,)
    k32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
    k32.GlobalFree.argtypes = (ctypes.c_void_p,)
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.SetClipboardData.argtypes = (wintypes.UINT, ctypes.c_void_p)
    h = k32.GlobalAlloc(0x0002, len(data))          # GMEM_MOVEABLE
    if not h:
        return False
    ctypes.memmove(k32.GlobalLock(h), data, len(data))
    k32.GlobalUnlock(h)
    for _ in range(5):
        if u32.OpenClipboard(0):
            break
        time.sleep(0.05)
    else:
        k32.GlobalFree(h)
        return False
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(13, ctypes.c_void_p(h)):   # CF_UNICODETEXT
            k32.GlobalFree(h)
            return False
        return True
    finally:
        u32.CloseClipboard()


class App:
    """The whole meter, minus the game connection: the aggregation loop, the
    saved data, and the one window (a WebView2 app in its own process — see
    menu_host.py) that shows all of it.

    Nothing here draws over the game. The window is an ordinary application
    window, meant for a second screen, and it works with the game closed:
    live modules say they need the game, everything saved stays readable.

    Threading: the game link, the hotkey hook, the tray and the window's pipe
    all run on their own threads and reach this object only through
    _enqueue(); everything else runs on the main thread, in run()."""

    def __init__(self, session: PartySession, ui_state=None, world=None,
                 link=None, target_pid=None):
        self.session = session
        self.ui_state = ui_state if ui_state is not None else GameUIState()
        self.world = world if world is not None else WorldSnapshot()
        self.link = link
        # The running game's pid, once connected. Given directly only by tests
        # that run without a GameLink.
        self.target_pid = target_pid
        self._game_hwnd = None
        self.root = _Scheduler()            # after()/after_cancel()/quit()
        self.parsewin = _NoWindow()         # parse mode's old banner window
        self.menubridge = MenuBridge(self)
        self._action_q = []
        self._q_lock = threading.Lock()
        self._stopping = False
        self._ui_seen = False               # the window has been up once

        # ---- what the player chose (saved) ----
        self.mode = "party"                 # "party" (group only) or "all"
        self._show_heal = True
        self._sort_heal = False
        self._auto_reset_boss = False
        self._rift_auto_view = False
        self._zoom = 100                    # the window's own size, percent

        # ---- what the window is showing (not saved) ----
        self._menu_tab = APP_TAB_DEFAULT
        self.focus_player = None            # player picked in the meter
        self._help_open = None
        self._rift_view = None              # the rift report being read
        self._launching_until = 0           # Play was clicked: until then
        self._repairing = False             # Réparer is running
        self._self_prof = None              # own luck counters (hook, 1 min)
        self._repair_note = None            # (ok, text) once it has run
        self._rift_rewards = False          # the rift rewards page is open
        self._dungeon_kind = None           # the dungeon whose runs are listed
        self._dungeon_view = None           # the dungeon run being read
        self._collection_owned = None       # .meter_collection.json, loaded
        self._codex_data = None             # .meter_codex.json, loaded
        self._elements_logged = False
        self._item_codex_logged = False
        self._ach_logged = False
        self._ach_data = None
        self._item_codex_cache = (None, {})
        self._roster = []                   # players around, from the hook
        self._roster_at = 0.0
        self._profiles = None               # profiles analysed this session
        self._char_sel = None               # the profile being read
        self._char_wait = None              # (name, since) of an analysis
        self._elements_data = None          # .meter_elements.json, loaded
        self._dungeon_cache = {}            # file name -> (mtime, data)
        self._binding_now = False
        self._menu_unlock = False           # no game menu to follow any more
        self._toast = {"t": "", "n": 0}
        self._events = deque(maxlen=EVENTS_MAX)

        # ---- live state ----
        self._held_rows = []
        self._held_duration = 0.0
        self._live = ([], 0.0, False, False)    # rows, duration, holding, fight
        self._last_epoch = session.epoch
        self._parse_state = None
        self._parse_until = 0.0
        self._parse_text = ""
        self._rift_seen = False
        self._best_times = self._load_best_times()
        self._report_data = self._load_last_rift_report()
        self._rift_cache = {}               # rift file name -> (mtime, summary)

        self._load_settings()
        self._win_geom = self._load_window_geom()
        self._install_hotkeys()

    # ------------------------------------------------------------------ settings
    def _load_settings(self):
        """Read the saved settings. Tolerant on purpose: a value this build
        does not offer costs that one setting, never the program."""
        try:
            data = json.loads(SETTINGS_CACHE.read_text())
        except Exception:
            return
        if not isinstance(data, dict):
            return
        if data.get("mode") in ("party", "all"):
            self.mode = data["mode"]
        for key, attr in (("show_heal", "_show_heal"), ("sort_heal", "_sort_heal"),
                          ("auto_reset_boss", "_auto_reset_boss"),
                          ("rift_auto_view", "_rift_auto_view")):
            if isinstance(data.get(key), bool):
                setattr(self, attr, data[key])
        self._sort_heal = self._sort_heal and self._show_heal
        bind = data.get("reset_bind")
        if isinstance(bind, dict) and isinstance(bind.get("vk"), int):
            vk = bind["vk"]
            if 0 < vk <= 0xFF and vk not in VK_UNBINDABLE:
                RESET_BIND.update(
                    {"vk": vk} | {m: bool(bind.get(m))
                                  for m in ("shift", "ctrl", "alt")})
        z = data.get("zoom")
        if not isinstance(z, int):
            # The old settings panel's size slider.
            try:
                z = int(round(float((data.get("scales") or {}).get("menu")) * 100))
            except (TypeError, ValueError):
                z = None
        if isinstance(z, int) and 50 <= z <= 200:
            self._zoom = z

    def _save_settings(self):
        try:
            SETTINGS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_CACHE.write_text(json.dumps({
                "mode": self.mode,
                "show_heal": bool(self._show_heal),
                "sort_heal": bool(self._sort_heal),
                "auto_reset_boss": bool(self._auto_reset_boss),
                "rift_auto_view": bool(self._rift_auto_view),
                "reset_bind": dict(RESET_BIND),
                "zoom": int(self._zoom),
            }, indent=2))
        except OSError as e:
            print(f"[meter] couldn't save settings: {e}", file=sys.stderr)

    def _load_window_geom(self):
        """Where the window was left, in physical pixels — or {} for the
        default size, centred by Windows."""
        try:
            d = json.loads(POSITION_CACHE.read_text())
        except Exception:
            return {}
        g = d.get("app") or {}
        try:
            out = {k: int(g[k]) for k in ("x", "y", "w", "h")}
        except (KeyError, TypeError, ValueError):
            return {}
        # A layout from another monitor setup must not open off-screen.
        if not _monitor_containing(out["x"] + 40, out["y"] + 20):
            out.pop("x"), out.pop("y")
        return out

    def _save_window_geom(self):
        g = self.menubridge.geom or {}
        if not all(isinstance(g.get(k), int) for k in ("x", "y", "w", "h")):
            return
        # A minimised window reports itself far off-screen; not a place.
        if g["x"] <= -30000 or g["y"] <= -30000:
            return
        try:
            POSITION_CACHE.write_text(json.dumps({"space": "physical",
                                                  "app": g}))
        except OSError:
            pass

    # ------------------------------------------------------------------ shims
    # The methods carried over from the old overlay announce things with these
    # names. With no overlay they become entries in the live page's event feed.
    def _event(self, text, tone="", btn=None):
        self._events.appendleft({"at": time.time(), "t": text, "tone": tone,
                                 "btn": btn})
        self.menubridge.invalidate()

    def _show_kill_toast(self, text, best):
        self._event(" ".join(str(text).split()).capitalize(),
                    "best" if best else "")

    def _show_reset_toast(self):
        self._event("Combat réinitialisé.")

    def _set_parse_banner(self, text, fill=None):
        self._parse_text = text

    def _hide_parse_banner(self):
        self._parse_text = ""

    def _draw_hint(self):
        pass

    def _refocus_game(self):
        pass

    def _refresh_visibility(self):
        self.menubridge.invalidate()


    def _open_report_card(self):
        """Show _report_data — as a page of the window now, not a card over
        the game."""
        self._rift_view = self._report_data
        self._menu_tab = "Rifts"
        self.menubridge.invalidate()

    def _toast_msg(self, text):
        self._toast = {"t": text, "n": self._toast["n"] + 1}
        self.menubridge.invalidate()

    # ------------------------------------------------------------------ game
    def _on_link_changed(self):
        if self.link is not None:
            state, _detail, pid = self.link.status()
            if state == GameLink.CONNECTED and pid:
                self.target_pid = pid
                self._game_hwnd = None
                self._event("Connecté à Farever.", "ok")
        self.menubridge.invalidate()

    def show_rift_report(self, report):
        """A rift's boss died. Called from the hook thread."""
        def done():
            self._report_data = report
            self._save_rift_report(report)
            best = (report.get("phases") or [{}])[-1].get("players") or []
            who = f" — MVP {best[0]['name']}" if best else ""
            self._event(f"Faille terminée{who}.", "rift",
                        {"id": "open_last_rift", "t": "Voir le rapport"})
        self._enqueue(done)()

    def on_boss_giveup(self):
        self._enqueue(lambda: self._event(
            "Combat abandonné — compteur vidé.", ""))()

    def open_settings_from_tray(self):
        """The tray's "Afficher Farever France": bring the window to the front."""
        self.menubridge.send({"t": "show"})

    def _tick_rift(self):
        """Follow rift crossings for the standing "all players in rifts"
        setting. There is no question to answer any more: without the setting
        the view is simply left alone."""
        in_rift = self.ui_state.in_rift()
        if in_rift == self._rift_seen:
            return
        self._rift_seen = in_rift
        self._event("Entrée dans une faille." if in_rift
                    else "Sortie de la faille.", "rift")
        if self._rift_auto_view:
            self._apply_rift_view("enter" if in_rift else "leave")

    def _toggle_rift_auto_view(self):
        self._rift_auto_view = not self._rift_auto_view
        self._save_settings()

    # ------------------------------------------------------------------ actions
    def _toggle_heal(self):
        self._show_heal = not self._show_heal
        if not self._show_heal:
            self._sort_heal = False
        self._save_settings()

    def _toggle_sort(self):
        if self._show_heal:
            self._sort_heal = not self._sort_heal
            self._save_settings()

    def _focus(self, name):
        self.focus_player = name or None

    def _set_zoom(self, pct):
        self._zoom = max(50, min(200, int(pct)))
        self._save_settings()


    def _open_rift_file(self, name):
        data = self._read_rift_file(name)
        if data is None:
            self._toast_msg("Ce rapport de faille est illisible.")
            return
        self._rift_view = data

    def _shown_report(self):
        if self._menu_tab == "Dungeons" and self._dungeon_view is not None:
            d = self._dungeon_view
            return dict(d, title=d.get("name") or "Donjon",
                        sub=self._dungeon_sub(d))
        return self._rift_view

    def _copy_rift_image(self):
        data = self._shown_report()
        if not data:
            return
        try:
            copy_image_to_clipboard(render_rift_report_image(data))
            self._toast_msg("Image copiée dans le presse-papiers.")
        except Exception as e:
            print(f"[meter] image copy failed ({e}) — copying text instead.",
                  file=sys.stderr)
            if copy_text_to_clipboard(self._report_text(data)):
                self._toast_msg("Copié en texte.")

    def _copy_rift_text(self):
        data = self._shown_report()
        if data and copy_text_to_clipboard(self._report_text(data)):
            self._toast_msg("Texte copié dans le presse-papiers.")

    def _open_log_folder(self):
        try:
            DATA_HOME.mkdir(parents=True, exist_ok=True)
            os.startfile(DATA_HOME)
        except Exception as e:
            print(f"[meter] couldn't open {DATA_HOME}: {e}", file=sys.stderr)

    def _repair(self):
        """Réparer (Aide): what a game patch needs, by hand — read the game's
        data again from scratch, forget everything loaded from the old files,
        and reconnect with a hook built from the new ones. No restart: the
        hook's source and every table are re-read when they are next used."""
        if self._repairing:
            return
        self._repairing = True
        self._repair_note = None
        self.menubridge.invalidate()

        def work():
            ok = False
            try:
                pid = self.link.status()[2] if self.link is not None else None
                hlboot = locate_hlboot(pid) if pid else None
                print("[meter] repair: regenerating the game data ...",
                      file=sys.stderr)
                ok = regenerate_data(hlboot, force=True)
                forget_loaded_data()
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[meter] repair failed: {e}", file=sys.stderr)

            def done():
                self._repairing = False
                self._repair_note = (
                    (True, "Données du jeu relues. Reconnexion au jeu en "
                           "cours si Farever est ouvert.")
                    if ok else
                    (False, "La relecture des données a échoué : le détail "
                            "est dans le journal (Réglages › Ouvrir le "
                            "dossier du journal)."))
                if ok and self.link is not None:
                    self.link.reconnect()
                self.menubridge.invalidate()
            self._enqueue(done)()
        threading.Thread(target=work, daemon=True, name="repair").start()

    def _set_tab(self, name):
        if name not in APP_TABS:
            return
        if name != "Help":
            self._help_open = None
        self._menu_tab = name

    def _menu_actions(self):
        acts = {
            "set_tab": lambda p: self._set_tab(p.get("value")),
            "link_retry": self._link_clicked,
            "launch_game": self._launch_game,
            "boot": self.menubridge.invalidate,
            "rendered": lambda p: None,
            "escape": lambda: None,
            # live
            "toggle_mode": self._toggle_mode,
            "toggle_sort": self._toggle_sort,
            "reset_data": self._manual_reset,
            "toggle_parse": self._toggle_parse,
            "focus_player": lambda p: self._focus(p.get("name")),
            "open_last_rift": self._open_report_card,
            "clear_events": self._events.clear,
            # rifts
            "open_rift": lambda p: self._open_rift_file(p.get("file", "")),
            "close_rift": lambda: setattr(self, "_rift_view", None),
            "open_rift_rewards": lambda: setattr(self, "_rift_rewards", True),
            "close_rift_rewards": lambda: setattr(self, "_rift_rewards",
                                                  False),
            "copy_rift_image": self._copy_rift_image,
            "copy_rift_text": self._copy_rift_text,
            "open_parses": self._open_parses,
            # dungeons
            "open_dungeon_kind": lambda p: setattr(
                self, "_dungeon_kind", p.get("kind")),
            "close_dungeon_kind": lambda: setattr(self, "_dungeon_kind", None),
            "open_dungeon_run": lambda p: self._open_dungeon_run(
                p.get("file", "")),
            "close_dungeon_run": lambda: setattr(self, "_dungeon_view", None),
            # character
            "char_analyze": lambda p: self._analyze(p.get("name")),
            "char_open": lambda p: setattr(self, "_char_sel", p.get("name")),
            "char_close": lambda: setattr(self, "_char_sel", None),
            "char_forget": lambda p: self._forget_profile(p.get("name")),
            # settings
            "toggle_heal": self._toggle_heal,
            "toggle_rift_auto_view": self._toggle_rift_auto_view,
            "toggle_auto_reset": self._toggle_auto_reset_boss,
            "begin_bind": self._begin_bind_capture,
            "set_zoom": lambda p: self._set_zoom(p.get("value", 100)),
            "open_log": self._open_log_folder,
            # help
            "help_open": lambda p: setattr(self, "_help_open", p.get("id")),
            "help_close": lambda: setattr(self, "_help_open", None),
            "repair_data": self._repair,
        }
        return acts


    def _panel_typing(self, on):
        pass

    def _panel_closed(self):
        """The window was closed: that is quitting the program."""
        self._quit()

    def request_quit(self):
        self._enqueue(self._quit)()

    def _quit(self):
        print("[meter] stop requested — shutting down.", file=sys.stderr)
        self._stopping = True

    # ------------------------------------------------------------------ loop
    def run(self):
        """The main loop: the window's actions, the timers, and the refresh
        that turns the session into what the window shows."""
        self.menubridge.start(self._win_geom)
        if self.menubridge.proc is None:
            message_box("La fenêtre de Farever France n'a pas pu s'ouvrir "
                        "(WebView2 ou pywebview manquant ?).\n\nLe détail est "
                        f"dans :\n{LOG_FILE}", "Farever France — erreur", 0x10)
            return
        last = 0.0
        while not self._stopping:
            now = time.monotonic()
            self._drain()
            self.root.run_due(now)
            if quit_requested():
                print("[meter] a newer instance asked us to exit — shutting "
                      "down.", file=sys.stderr)
                break
            alive = self.menubridge.alive()
            self._ui_seen |= alive
            if self._ui_seen and not alive:
                break                   # the window is gone: so are we
            if now - last >= REFRESH_MS / 1000:
                last = now
                self._refresh()
                if (self.menubridge.geom_at
                        and now - self.menubridge.geom_at > 0.6):
                    self.menubridge.geom_at = 0.0
                    self._save_window_geom()
                try:
                    self.menubridge.push(self._spec())
                except Exception:
                    import traceback
                    traceback.print_exc()
            elif self.menubridge.dirty():
                try:
                    self.menubridge.push(self._spec())
                except Exception:
                    pass
            time.sleep(0.03)
        self._save_window_geom()

    def _refresh(self):
        self._tick_rift()
        self._tick_parse()
        if self.session.epoch != self._last_epoch:
            self._last_epoch = self.session.epoch
            self.focus_player = None
            if self._parse_state is not None:
                self._parse_state = None
                self._hide_parse_banner()
        _, _, rows = self.session.snapshot()
        rows = self._apply_mode(rows)
        if self._sort_heal:
            rows.sort(key=lambda p: -p.heal_total)
        active = any(self.session.combat_of(p.name) for p in rows)
        self.session.set_active(active, time.time())
        duration, in_combat = self.session.current()
        rows, duration, holding = self._hold_last(rows, duration)
        self._live = (rows, duration, holding, in_combat)

    # ------------------------------------------------------------------ spec
    def _spec(self):
        return {
            "version": VERSION,
            "zoom": int(self._zoom),
            "shard": self.ui_state.server() or "",
            "link": self._link_spec(),
            "linksteps": (self.link.steps_view() if self.link is not None
                          else []),
            "rift": self._rift_clock(),
            "toast": self._toast,
            "tab": self._menu_tab,
            # The app's own tabs, after the game's, behind a divider.
            "tabs": [{"v": t, "t": APP_TAB_LABELS[t],
                      "sep": t == APP_TABS_APP_FIRST} for t in APP_TABS],
            "page": self._page(self._menu_tab),
            # the events window (title band button), always up to date
            "events": [{"when": time.strftime("%H:%M",
                                              time.localtime(e["at"])),
                        "t": e["t"], "tone": e["tone"], "btn": e["btn"]}
                       for e in self._events],
        }

    def _page(self, tab):
        builder = {"Live": self._page_live, "Rifts": self._page_rifts,
                   "Dungeons": self._page_dungeons,
                   "Collection": self._page_collection,
                   "Hunt": self._page_hunt,
                   "Achievements": self._page_achievements,
                   "Map": self._page_map,
                   "Character": self._page_character,
                   "Settings": self._page_settings,
                   "Help": self._page_help}.get(tab)
        try:
            return builder() if builder else []
        except Exception as e:
            import traceback
            traceback.print_exc()
            return [{"k": "section", "t": APP_TAB_LABELS.get(tab, tab)},
                    {"k": "note", "warn": True,
                     "t": f"Cette page n'a pas pu être construite : {e}. Le "
                          "détail est dans le journal."}]

    # ---- live
    def _page_live(self):
        rows, duration, holding, in_combat = self._live
        online = self.game_connected()
        parsing = self._parse_state is not None
        tools = [
            {"id": "toggle_mode", "on": self.mode == "all",
             "t": "Tous les joueurs" if self.mode == "all" else "Groupe"},
            {"id": "reset_data", "t": f"Réinitialiser  ({bind_label()})"},
            {"id": "toggle_parse", "on": parsing,
             "tone": None if parsing or online else "disabled",
             "t": ("Arrêter le parse" if parsing
                   else f"Parse {PARSE_LENGTH_SECS} s")},
        ]
        if self._show_heal:
            tools.insert(1, {"id": "toggle_sort", "on": self._sort_heal,
                             "t": "Tri : soins" if self._sort_heal
                             else "Tri : dégâts"})
        party_total = sum(p.total for p in rows)
        heal_total = sum(p.heal_total for p in rows)
        cards = [
            {"title": "Combat",
             "value": _mmss(duration) if duration > 0 else "—",
             "sub": ("en cours" if in_combat else
                     "dernier combat" if holding else
                     "en attente" if online else "jeu fermé"),
             "tone": "hot" if in_combat else ""},
            {"title": "Dégâts du " + ("groupe" if self.mode == "party"
                                      else "total"),
             "value": _n(party_total) if party_total else "—",
             "sub": (f"{_n(party_total / duration)} DPS"
                     if duration > 0 and party_total else "")},
            {"title": "Soins", "value": _n(heal_total) if heal_total else "—",
             "sub": (f"{_n(heal_total / duration)} HPS"
                     if duration > 0 and heal_total else "")},
        ]
        if parsing:
            cards.append({"title": "Parse", "value": self._parse_text or "…",
                          "sub": "", "tone": "hot"})
        out = [{"k": "toolbar", "id": "live_tools", "btns": tools},
               {"k": "cards", "id": "live_cards", "items": cards}]
        focus = self._resolve_focus(rows)
        top_dmg = max((p.total for p in rows), default=0.0) or 1.0
        top_heal = max((p.heal_total for p in rows), default=0.0) or 1.0
        meter_rows = []
        for i, p in enumerate(rows[:MAX_PLAYER_ROWS * 3], 1):
            meter_rows.append({
                "rank": i, "name": p.name, "me": bool(p.is_me),
                "cls": _class_tag(self.world.class_of(p.name)),
                "ck": class_key(self.world.class_of(p.name)),
                "dmg": _n(p.total),
                "dps": _n(p.total / duration) if duration > 0 else "—",
                "pct": f"{(p.total / party_total * 100) if party_total else 0:.0f}%",
                "heal": _n(p.heal_total),
                "over": (f"{p.overheal_pct:.0f}%" if p.heal_total > 0.5
                         else ""),
                "df": round(p.total / top_dmg, 4),
                "hf": round(p.heal_total / top_heal, 4),
                "hsf": round(p.heal_self / top_heal, 4),
                "focus": p.name == focus})
        title = ("GROUPE" if self.mode == "party" else "TOUS LES JOUEURS")
        if holding:
            title += " · DERNIER COMBAT"
        out.append({"k": "meter", "id": "meter", "title": title,
                    "heal": bool(self._show_heal), "rows": meter_rows,
                    "empty": ("En attente d'un combat…" if online else
                              "Lance Farever : le compteur se remplit dès le "
                              "premier combat.")})
        out.append(self._detail_node(rows, duration, focus))
        me = self._self_prof if online else None
        out.append({"k": "luck", "id": "live_luck",
                    "rows": _profile_luck(me) if me else None,
                    "empty": ("Lecture des compteurs…" if online else
                              "Lance Farever pour voir ta chance de butin.")})
        stats = _profile_stats(me) if me else None
        if stats:
            out.append({"k": "statcards", "id": "live_stats",
                        "items": stats})
        return out

    def _detail_node(self, rows, duration, focus):
        fp = next((p for p in rows if p.name == focus), None)
        if fp is None:
            return {"k": "detail", "id": "detail", "name": "",
                    "empty": "Clique sur un joueur pour voir son détail."}
        fdps = fp.total / duration if duration > 0 else 0.0
        crit = (fp.crits / fp.hits * 100) if fp.hits else 0.0
        stats = [["Dégâts", _n(fp.total)], ["DPS", _n(fdps)],
                 ["Coups", _n(fp.hits)], ["Critiques", f"{crit:.0f}%"]]
        if self._show_heal:
            stats.append(["Soins", _n(fp.heal_total)])
            if fp.heal_total > 0.5:
                stats.append(["Soin en excès", f"{fp.overheal_pct:.0f}%"])
        if fp.kills:
            stats.append(["Kills", _n(fp.kills)])

        def skills(table, total):
            out = []
            entries = self._merge_named(table)
            top = max((e[1] for e in entries), default=0.0) or 1.0
            for label, amount, hits, _crits, slf in entries:
                out.append({"t": label, "v": _n(amount),
                            "pct": f"{(amount / total * 100) if total else 0:.0f}%",
                            "n": hits, "f": round(amount / top, 4),
                            "sf": round(slf / top, 4) if amount > 0 else 0})
            return out

        el = sorted(fp.elements.items(), key=lambda kv: -kv[1][1])
        return {"k": "detail", "id": "detail", "name": fp.name,
                "cls": _class_tag(self.world.class_of(fp.name)),
                "ck": class_key(self.world.class_of(fp.name)),
                "stats": stats,
                "dmg": skills(fp.skills, fp.total),
                "heal": skills(fp.heals, fp.heal_total) if self._show_heal
                else None,
                "elements": [{"t": element_label(k),
                              "pct": _pct1(v[1] / fp.total * 100
                                           if fp.total else 0),
                              "c": element_color(k)} for k, v in el[:8]]}

    def _rift_clock(self):
        """The rift countdown shown in the sidebar. Rifts open on the hour
        and the portal stays open RIFT_PORTAL_SECS: while it is, this counts
        down to it closing; the rest of the hour, to the next one opening."""
        now = time.localtime()
        into = now.tm_min * 60 + now.tm_sec
        if self.ui_state.in_rift():
            return {"title": "Faille", "value": "En cours", "sub": "",
                    "tone": "rift"}
        if into < RIFT_PORTAL_SECS:
            left = RIFT_PORTAL_SECS - into
            return {"title": "Portail ouvert",
                    "value": f"{left // 60}:{left % 60:02d}",
                    "sub": "avant sa fermeture", "tone": "open"}
        left = 3600 - into
        return {"title": "Prochaine faille",
                "value": f"{left // 60:02d}:{left % 60:02d}",
                "sub": "à " + time.strftime(
                    "%H:00", time.localtime(time.time() + left)),
                "tone": "rift" if left <= RIFT_STYLE_SECS else ""}

    # ---- rifts
    def _rift_files(self):
        try:
            return sorted(PARSES_DIR.glob("rift-*.json"), reverse=True)
        except OSError:
            return []

    def _read_rift_file(self, name):
        name = Path(str(name)).name              # never a path from the page
        try:
            data = json.loads((PARSES_DIR / name).read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("phases"), list):
            return None
        return data

    def _rift_summary(self, path):
        """(title, meta) for one saved rift, cached by modification time."""
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        hit = self._rift_cache.get(path.name)
        if hit and hit[0] == mtime:
            return hit[1]
        data = self._read_rift_file(path.name)
        if data is None:
            return None
        phases = data["phases"]
        dur = sum(float(ph.get("duration") or 0) for ph in phases)
        boss = phases[-1] if phases else {}
        players = boss.get("players") or []
        mvp = ""
        if players:
            rate = _rate(players[0].get("total", 0), boss.get("duration", 0))
            mvp = (f"MVP {players[0].get('name', '?')}"
                   + (f" ({_n(rate)} DPS)" if rate else ""))
        title = date_fr(time.localtime(data.get("at") or 0))
        meta = " · ".join(x for x in (f"durée {_mmss(dur)}",
                                      f"{len(players)} joueurs", mvp) if x)
        out = (title, meta)
        self._rift_cache[path.name] = (mtime, out)
        return out

    def _page_rifts(self):
        if self._rift_rewards:
            data = self._achievements()
            entry = (data.get("heroes") or {}).get(data.get("last")) or {}
            return [{"k": "toolbar", "id": "rift_reward_tools", "btns": [
                        {"id": "close_rift_rewards",
                         "t": "‹  Toutes les failles"}]},
                    *rift_rewards_view(entry.get("counters") or {},
                                       entry.get("luckUntil") or {})]
        if self._rift_view is not None:
            return [{"k": "toolbar", "id": "rift_tools", "btns": [
                        {"id": "close_rift", "t": "‹  Toutes les failles"},
                        {"id": "copy_rift_image", "t": "Copier l'image"},
                        {"id": "copy_rift_text", "t": "Copier le texte"}]},
                    self._report_node(self._rift_view)]
        rows = []
        for path in self._rift_files()[:300]:
            got = self._rift_summary(path)
            if got is None:
                continue
            rows.append({"t": got[0], "meta": got[1],
                         "btns": [{"id": "open_rift", "t": "Voir",
                                   "p": {"file": path.name}}]})
        return [
            {"k": "toolbar", "id": "rift_list_tools", "btns": [
                {"id": "open_rift_rewards",
                 "t": "Récompenses des failles et chances"}]},
            {"k": "section", "t": "Failles réalisées"},
            {"k": "note", "t": "Chaque faille terminée (boss vaincu) est "
                               "enregistrée ici avec son classement complet. "
                               "Clique sur « Voir » pour la relire."},
            {"k": "list", "id": "rifts", "grow": True, "rows": rows,
             "empty": "Aucune faille enregistrée pour l'instant."},
            {"k": "button", "id": "open_parses",
             "t": "Ouvrir le dossier des rapports"},
        ]

    def _report_node(self, data):
        return report_view(data)

    # ---- dungeons
    def on_dungeon_run(self, report):
        """A dungeon run ended (won, failed or abandoned). Hook thread."""
        def done():
            name = self._save_dungeon_run(report)
            best = self._dungeon_best(report["kind"], report.get("difficulty"),
                                      exclude=name)
            diff = DUNGEON_DIFFICULTIES.get(report.get("difficulty"), "?")
            txt = (f"{report['name']} ({diff.lower()}) — {report['result']}"
                   f" en {_mmss(report['duration'])}")
            tone = "ok"
            if report["result"] == "victoire":
                if best is None:
                    txt += " — premier temps enregistré"
                elif report["duration"] < best:
                    txt += f" — nouveau record (avant : {_mmss(best)})"
                    tone = "best"
                else:
                    txt += f" — record : {_mmss(best)}"
            else:
                tone = ""
            self._event(txt, tone, {"id": "open_dungeon_run", "t": "Voir",
                                    "p": {"file": name}} if name else None)
        self._enqueue(done)()

    def on_collection(self, p):
        """The account's collection, read in game. Saved, so the Collection
        tab shows it with the game closed. Hook thread."""
        if p.get("types"):
            print(f"[meter] collection element types: {p['types']}",
                  file=sys.stderr)
        owned = {k: sorted(set(p.get(k) or ()))
                 for k in ("mounts", "gliders", "pets", "gears")}

        def done():
            owned["at"] = time.time()
            self._collection_owned = owned
            try:
                COLLECTION_FILE.write_text(json.dumps(owned),
                                           encoding="utf-8")
            except OSError as e:
                print(f"[meter] couldn't save the collection: {e}",
                      file=sys.stderr)
            print(f"[meter] collection: {len(owned['mounts'])} mounts, "
                  f"{len(owned['gliders'])} gliders, {len(owned['pets'])} "
                  f"companions, {len(owned['gears'])} gear appearances "
                  f"(e.g. {owned['gears'][:3]})", file=sys.stderr)
        self._enqueue(done)()

    def on_achievements(self, p):
        """The achievements read in game: the account's completion times and
        this character's completed ids and counters. Saved, so the Succès
        tab works with the game closed. Hook thread."""
        hero = p.get("hero") or "?"

        def done():
            data = self._achievements()
            data["account"] = p.get("account") or {}
            now, wall = p.get("now"), time.time()
            until = {}
            for k, start, dur, stop in p.get("luck") or ():
                end = stop if stop and stop > 0 else (
                    start + dur if dur and dur > 0 else None)
                if end is not None and isinstance(now, (int, float)):
                    until[k] = wall + (end - now)
            data.setdefault("heroes", {})[hero] = {
                "done": p.get("done") or [],
                "counters": p.get("counters") or {}, "at": wall,
                "luckUntil": until}
            data["last"] = hero
            try:
                ACH_FILE.write_text(json.dumps(data), encoding="utf-8")
            except OSError as e:
                print(f"[meter] couldn't save the achievements: {e}",
                      file=sys.stderr)
            if not self._ach_logged:
                self._ach_logged = True
                print(f"[meter] achievements: {hero}, "
                      f"{len(data['account'])} completed on the account, "
                      f"{len(p.get('done') or [])} by this character",
                      file=sys.stderr)
        self._enqueue(done)()

    def _achievements(self):
        if self._ach_data is None:
            try:
                self._ach_data = json.loads(
                    ACH_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._ach_data = {}
        return self._ach_data

    def _page_achievements(self):
        data = self._achievements()
        hero = data.get("last")
        entry = (data.get("heroes") or {}).get(hero) or {}
        at = entry.get("at")
        els = self._elements()
        states = ((els.get("heroes") or {}).get(hero) or {}).get("states")
        sync = (f"Succès du compte et progression de {hero}, lus en jeu le "
                f"{date_fr(time.localtime(at))}." if at else
                "Pas encore lus : lance le jeu avec Farever France ouvert.")
        return [{"k": "achievements", "id": "achievements", "sync": sync,
                 **achievements_view(data.get("account") or {},
                                     entry.get("counters") or {},
                                     self._collection(), states or {})}]

    def on_item_codex(self, p):
        """A character's item codex (item -> [count, rank]), read in game.
        Saved per character. Hook thread. The first read of a session logs
        what it holds, by item type — the catalogue is built on that."""
        hero = p.get("hero") or "?"
        items = {k: v for k, v in (p.get("items") or {}).items()
                 if isinstance(v, list) and len(v) == 2}

        def done():
            try:
                data = json.loads(ITEM_CODEX_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            data.setdefault("heroes", {})[hero] = {"items": items,
                                                   "at": time.time()}
            data["last"] = hero
            try:
                ITEM_CODEX_FILE.write_text(json.dumps(data), encoding="utf-8")
            except OSError as e:
                print(f"[meter] couldn't save the item codex: {e}",
                      file=sys.stderr)
            if not self._item_codex_logged:
                self._item_codex_logged = True
                by_type = defaultdict(int)
                for iid in items:
                    by_type[item_type(iid) or "?"] += 1
                print(f"[meter] item codex: {hero}, {len(items)} items "
                      f"(other value type: {p.get('other')}); by type: "
                      f"{dict(sorted(by_type.items(), key=lambda kv: -kv[1]))}",
                      file=sys.stderr)
        self._enqueue(done)()

    def on_codex(self, p):
        """A character's kill counts per monster (the game's codex), read in
        game. Saved per character. Hook thread."""
        hero = p.get("hero") or "?"
        ranks = {k: v for k, v in (p.get("ranks") or {}).items()
                 if isinstance(v, list) and len(v) == 2}

        def done():
            data = self._codex()
            first = hero not in data.setdefault("heroes", {})
            data["heroes"][hero] = {"ranks": ranks, "at": time.time()}
            data["last"] = hero
            try:
                CODEX_FILE.write_text(json.dumps(data), encoding="utf-8")
            except OSError as e:
                print(f"[meter] couldn't save the kill counts: {e}",
                      file=sys.stderr)
            if first:
                print(f"[meter] kill counts: {hero}, {len(ranks)} monsters, "
                      f"{sum(v[0] for v in ranks.values())} kills",
                      file=sys.stderr)
        self._enqueue(done)()

    def on_elements(self, p):
        """A character's completed world elements (element id -> when),
        read in game: the map's completion. Saved per character. Hook
        thread."""
        hero = p.get("hero") or "?"
        states = p.get("states") or {}

        def done():
            try:
                data = json.loads(ELEMENTS_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            first = not self._elements_logged
            self._elements_data = data
            data.setdefault("heroes", {})[hero] = {"states": states,
                                                   "at": time.time()}
            data["last"] = hero
            try:
                ELEMENTS_FILE.write_text(json.dumps(data), encoding="utf-8")
            except OSError as e:
                print(f"[meter] couldn't save the map progress: {e}",
                      file=sys.stderr)
            if first:
                self._elements_logged = True
                pts = world_map().get("points") or []
                done_ = sum(1 for q in pts if _element_done(states,
                                                            q.get("id")))
                print(f"[meter] map progress: {hero}, {done_} / {len(pts)} "
                      "points", file=sys.stderr)
        self._enqueue(done)()

    def _elements(self):
        if self._elements_data is None:
            try:
                self._elements_data = json.loads(
                    ELEMENTS_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._elements_data = {}
        return self._elements_data

    def _codex(self):
        if self._codex_data is None:
            try:
                self._codex_data = json.loads(
                    CODEX_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._codex_data = {}
        return self._codex_data

    def _page_hunt(self):
        data = self._codex()
        hero = data.get("last")
        entry = (data.get("heroes") or {}).get(hero) or {}
        at = entry.get("at")
        sync = (f"Kills de {hero}, lus en jeu le "
                f"{date_fr(time.localtime(at))}. Le compte est celui du jeu "
                "(son Codex) : il inclut tout ce que tu as tué avant "
                "Farever France, et se met à jour tout seul quand le jeu est "
                "ouvert." if at else
                "Pas encore lu : lance le jeu avec Farever France ouvert, tes "
                "kills se rempliront tout seuls.")
        return [{"k": "hunt", "id": "hunt", "sync": sync,
                 **bestiary_view(entry.get("ranks") or {},
                                 self._collection())}]

    # ---- character
    def on_character(self, p):
        """The players around (roster) or one player's profile, from the
        hook. Hook thread."""
        def done():
            if p.get("kind") == "selfprofile":
                self._self_prof = dict(p.get("profile") or {}, at=time.time())
                return
            if p.get("kind") == "roster":
                self._roster = p.get("players") or []
                self._roster_at = time.time()
                return
            wait, self._char_wait = self._char_wait, None
            if p.get("missing"):
                self._toast_msg(f"{p.get('n')} n'est plus à proximité.")
                return
            prof = dict(p.get("profile") or {}, at=time.time())
            name = prof.get("n")
            if not name:
                return
            print(f"[meter] profile {name}: equipment "
                  f"{[(i, s[0]) for i, s in enumerate(prof.get('equip') or []) if s]}"
                  f"; arsenals {prof.get('arsenals')}; weapon skills "
                  f"{prof.get('weaponSkills')}; secondary "
                  f"{prof.get('secondary')}; statuses "
                  f"{prof.get('statuses')}", file=sys.stderr)
            # kept for this session only: profiles are never written to disk
            self._profiles_data()[name] = prof
            self._char_sel = name
        self._enqueue(done)()

    def _profiles_data(self):
        """The profiles analysed this session (in memory only)."""
        if self._profiles is None:
            self._profiles = {}
        return self._profiles

    def _analyze(self, name):
        script = self.link.script if self.link is not None else None
        if script is None:
            self._toast_msg("Le jeu n'est pas connecté.")
            return
        try:
            script.post({"type": "analyze", "name": name})
            self._char_wait = (name, time.time())
        except Exception as e:
            self._toast_msg(f"Analyse impossible : {e}")

    def _forget_profile(self, name):
        self._profiles_data().pop(name, None)
        if self._char_sel == name:
            self._char_sel = None

    def _page_character(self):
        profs = self._profiles_data()
        live = self.game_connected() and time.time() - self._roster_at < 30
        roster = self._roster if live else []
        wait = self._char_wait
        if wait and time.time() - wait[1] > 15:
            self._char_wait = wait = None
        return [{"k": "character", "id": "character",
                 **character_view(roster, profs, self._char_sel,
                                  wait[0] if wait else None, live)}]

    def _page_map(self):
        data = self._elements()
        hero = data.get("last")
        entry = (data.get("heroes") or {}).get(hero) or {}
        at = entry.get("at")
        sync = (f"Progression de {hero}, lue en jeu le "
                f"{date_fr(time.localtime(at))}." if at else
                "Progression pas encore lue : lance le jeu avec Farever France "
                "ouvert.")
        return [{"k": "map", "id": "map", "sync": sync,
                 **map_view(entry.get("states") if at else None)}]

    def _collection(self):
        if self._collection_owned is None:
            try:
                self._collection_owned = json.loads(
                    COLLECTION_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._collection_owned = {}
        return self._collection_owned

    def _page_collection(self):
        owned = self._collection()
        at = owned.get("at")
        sync = (f"Lue en jeu le {date_fr(time.localtime(at))}. Elle se met "
                "à jour toute seule quand le jeu est ouvert."
                if at else "Pas encore lue : lance le jeu avec Farever France "
                           "ouvert, ta collection se remplira toute seule.")
        codex = self._item_codex()
        entry = (codex.get("heroes") or {}).get(codex.get("last")) or {}
        return [{"k": "collection", "id": "collection",
                 "sync": sync, **collection_view(owned,
                                                 entry.get("items") or {})}]

    def _item_codex(self):
        try:
            path = ITEM_CODEX_FILE
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        if self._item_codex_cache[0] != mtime:
            try:
                self._item_codex_cache = (mtime, json.loads(
                    path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                return {}
        return self._item_codex_cache[1]

    def on_dungeon_loot(self, name, loot):
        """Loot that arrived after the run was saved (the reward chest):
        rewrite the saved run with it. Hook thread."""
        def done():
            path = DUNGEONS_DIR / Path(name).name
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["loot"] = loot
                path.write_text(json.dumps(data), encoding="utf-8")
            except (OSError, ValueError) as e:
                print(f"[meter] couldn't add the loot to {name}: {e}",
                      file=sys.stderr)
                return
            view = self._dungeon_view
            if view is not None and view.get("file") == name:
                self._dungeon_view = data
        self._enqueue(done)()

    def _save_dungeon_run(self, report):
        name = (report.get("file")
                or f"run-{time.strftime('%Y%m%d-%H%M%S')}.json")
        try:
            DUNGEONS_DIR.mkdir(parents=True, exist_ok=True)
            (DUNGEONS_DIR / name).write_text(json.dumps(report),
                                             encoding="utf-8")
            print(f"[meter] dungeon run saved to {DUNGEONS_DIR / name}",
                  file=sys.stderr)
            return name
        except OSError as e:
            print(f"[meter] couldn't save the dungeon run: {e}",
                  file=sys.stderr)
            return None

    def _dungeon_runs(self):
        """Every saved run, newest first, as (file name, data). Cached by
        modification time — the folder is re-read on every page push."""
        try:
            files = sorted(DUNGEONS_DIR.glob("run-*.json"), reverse=True)
        except OSError:
            return []
        out = []
        for path in files:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            hit = self._dungeon_cache.get(path.name)
            if not hit or hit[0] != mtime:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(data.get("phases"), list):
                    continue
                hit = (mtime, data)
                self._dungeon_cache[path.name] = hit
            out.append((path.name, hit[1]))
        return out

    def _dungeon_best(self, kind, difficulty, exclude=None):
        times = [d["duration"] for n, d in self._dungeon_runs()
                 if n != exclude and d.get("kind") == kind
                 and d.get("difficulty") == difficulty
                 and d.get("result") == "victoire"]
        return min(times) if times else None

    def _open_dungeon_run(self, name):
        name = Path(str(name)).name
        data = next((d for n, d in self._dungeon_runs() if n == name), None)
        if data is None:
            self._toast_msg("Ce run de donjon est illisible.")
            return
        self._dungeon_kind = data.get("kind")
        self._dungeon_view = data
        self._menu_tab = "Dungeons"

    def _dungeon_sub(self, d):
        diff = DUNGEON_DIFFICULTIES.get(d.get("difficulty"), "difficulté ?")
        deaths = int(d.get("deaths") or 0)
        bits = [diff, str(d.get("result") or "?").capitalize()
                + f" en {_mmss(d.get('duration') or 0)}",
                f"{deaths} mort{'s' if deaths > 1 else ''}"]
        if d.get("wipes"):
            bits.append(f"{d['wipes']} wipe{'s' if d['wipes'] > 1 else ''}")
        return " · ".join(bits)

    def _page_dungeons(self):
        runs = self._dungeon_runs()
        if self._dungeon_view is not None:
            d = self._dungeon_view
            node = report_view(dict(d, title=dungeon_name(d.get("kind"))))
            node["sub"] = self._dungeon_sub(d)
            return [{"k": "toolbar", "id": "dungeon_tools", "btns": [
                        {"id": "close_dungeon_run",
                         "t": "‹  Runs de " + dungeon_name(d.get("kind"))},
                        {"id": "copy_rift_image", "t": "Copier l'image"},
                        {"id": "copy_rift_text", "t": "Copier le texte"}]},
                    node]
        if self._dungeon_kind is not None:
            kind = self._dungeon_kind
            mine = [(n, d) for n, d in runs if d.get("kind") == kind]
            name = dungeon_name(kind)
            cards = []
            for diff, label in DUNGEON_DIFFICULTIES.items():
                best = self._dungeon_best(kind, diff)
                won = [d for _n, d in mine if d.get("difficulty") == diff
                       and d.get("result") == "victoire"]
                cards.append({"title": f"Record — {label}",
                              "value": _mmss(best) if best else "—",
                              "sub": f"{len(won)} victoire"
                                     f"{'s' if len(won) > 1 else ''}",
                              "tone": "rift" if best else ""})
            rows = []
            for n, d in mine:
                best = self._dungeon_best(kind, d.get("difficulty"))
                star = (d.get("result") == "victoire" and best is not None
                        and abs(d["duration"] - best) < 0.05)
                group = ", ".join(p.get("name", "?") for p in
                                  (d["phases"][-1].get("players") or [])[:6])
                rows.append({"t": date_fr(time.localtime(d.get("at") or 0))
                                  + ("  ★" if star else ""),
                             "meta": self._dungeon_sub(d)
                                     + (f" · {group}" if group else ""),
                             "btns": [{"id": "open_dungeon_run", "t": "Voir",
                                       "p": {"file": n}}]})
            got = {}
            for _n, d in mine:
                for it in d.get("loot") or ():
                    got[it.get("item")] = (got.get(it.get("item"), 0)
                                           + int(it.get("count") or 1))
            dg = next((x for x in dungeon_catalogue()
                       if x["kind"] == kind), None)
            out = [{"k": "toolbar", "id": "dungeon_kind_tools", "btns": [
                       {"id": "close_dungeon_kind",
                        "t": "‹  Tous les donjons"}]},
                   {"k": "section", "t": name},
                   {"k": "cards", "id": "dungeon_records", "items": cards},
                   {"k": "gap"},
                   {"k": "list", "id": "dungeon_runs",
                    "rows": rows, "empty": "Aucun run pour ce donjon."}]
            if dg and dg.get("loot"):
                out += [{"k": "section", "t": "Butin possible"},
                        {"k": "note", "t":
                         "D'après les données et le code du jeu. Le coffre "
                         "de fin donne une des armes du boss (tirée au "
                         "hasard) et des fragments d'Étincelle selon ton "
                         "niveau. Chaque joueur reçoit aussi une pièce "
                         "d'armure de la faction, garantie : en Normal et "
                         "Difficile une rare, tirée à parts égales parmi "
                         "celles que sa classe peut porter (les 2 dernières "
                         "reçues sont écartées) ; en Héroïque une épique, "
                         "parmi celles du boss pour sa classe. À 3 joueurs "
                         "une pièce de plus est donnée au hasard, 2 à 4 "
                         "joueurs. Chaque pièce a 10 % de chances d'être "
                         "prismatique (15 % avec l'offrande du Puits des "
                         "âmes). La mort du boss peut en plus donner un objet "
                         "rare et, en Héroïque, donne toujours le patron "
                         "d'imprégnation du donjon. « Obtenu » compte ce que "
                         "tes runs ont rapporté."},
                        {"k": "droptable", "id": "dungeon_drops",
                         "rows": droptable_view(dg, got)}]
            return out
        by_kind = {}
        for _n, d in runs:
            by_kind.setdefault(d.get("kind"), []).append(d)
        catalogue = {dg["kind"]: dg for dg in dungeon_catalogue()}

        def row(kind, boss):
            ds = by_kind.get(kind) or []
            # Second line the boss, third the runs and records.
            stats = []
            if ds:
                won = sum(1 for d in ds if d.get("result") == "victoire")
                stats.append(f"{len(ds)} run{'s' if len(ds) > 1 else ''} · "
                             f"{won} victoire{'s' if won > 1 else ''}")
                recs = [f"{label} {_mmss(best)}"
                        for diff, label in DUNGEON_DIFFICULTIES.items()
                        for best in [self._dungeon_best(kind, diff)] if best]
                if recs:
                    stats.append("records : " + ", ".join(recs))
            else:
                stats.append("pas encore fait")
            dg = catalogue.get(kind) or {}
            icons = [{"img": item_icon(e["item"]),
                      "tip": f"{item_label(e['item'])} — {_pct(e['chance'])}"}
                     for e in dg.get("loot") or ()
                     if e.get("src") in ("coffre", "boss")
                     and e.get("chance") is not None and e["chance"] < 1]
            # the boss's infusion pattern (guaranteed, Heroic only)
            icons += [{"img": item_icon(e["item"]),
                       "tip": f"{item_label(e['item'])} — garanti"
                              + (f" en {DUNGEON_DIFFICULTIES.get(e['diff'])}"
                                 if e.get("diff") else "")}
                      for e in dg.get("loot") or ()
                      if e.get("type") == "InfusionPattern"]
            for src, label in (("faction", "rare (Normal, Difficile)"),
                               ("heroic", "épique (Héroïque)")):
                armour = [e for e in dg.get("loot") or ()
                          if e.get("src") == src]
                if armour:
                    icons.append({"img": item_icon(armour[0]["item"]),
                                  "n": len(armour),
                                  "tip": f"Armure de faction {label} : "
                                         f"{len(armour)} pièces, une garantie "
                                         "par joueur parmi celles de sa "
                                         "classe"})
            return {"t": dungeon_name(kind),
                    "icons": icons,
                    "meta": f"Boss : {_boss_label(boss)}" if boss else "",
                    "meta2": " · ".join(stats),
                    "portrait": boss or "",
                    "btns": [{"id": "open_dungeon_kind", "t": "Voir",
                              "p": {"kind": kind}}]}

        # Every dungeon of the game, by region, in the game's own order;
        # runs of a dungeon the list doesn't know (a newer game) at the end.
        out = [{"k": "section", "t": "Donjons"},
               {"k": "note", "t": "Chaque donjon est enregistré "
                                  "automatiquement de l'entrée à la sortie : "
                                  "difficulté, temps (celui du jeu), morts, "
                                  "groupe, classement complet en deux "
                                  "phases — exploration et boss — et butin. "
                                  "Les échecs et abandons sont gardés "
                                  "aussi."}]
        regions, known = {}, set()
        for dg in dungeon_catalogue():
            regions.setdefault(dg.get("region") or "", []).append(dg)
            known.add(dg["kind"])
        others = [{"kind": k, "boss": (ds[0].get("boss") or "")}
                  for k, ds in by_kind.items() if k not in known]
        if others:
            regions.setdefault("", []).extend(others)
        for i, (region, dgs) in enumerate(regions.items()):
            title = (_fr_names("zone").get(region) if region else None) \
                or ("Autres donjons" if regions.keys() - {""} else "")
            if title:
                out.append({"k": "sub", "t": title})
            out.append({"k": "list", "id": f"dungeons_{i}",
                        "rows": [row(dg["kind"], dg.get("boss"))
                                 for dg in dgs]})
        if not regions:
            out.append({"k": "list", "id": "dungeons", "grow": True,
                        "rows": [],
                        "empty": "Aucun donjon enregistré pour l'instant."})
        return out

    # ---- settings
    def _page_settings(self):
        return [
            {"k": "section", "t": "Compteur"},
            {"k": "button", "id": "toggle_heal",
             "t": self._tick(self._show_heal, "Colonnes de soins")},
            {"k": "button", "id": "toggle_auto_reset",
             "t": self._tick(self._auto_reset_boss,
                             "Réinitialiser au pull d'un boss")},
            {"k": "button", "id": "toggle_rift_auto_view",
             "t": self._tick(self._rift_auto_view,
                             "Tous les joueurs automatiquement en faille")},
            {"k": "note", "t": "Passe le compteur sur « Tous les joueurs » en "
                               "entrant dans une faille, et revient au groupe "
                               "en sortant. Chaque bascule réinitialise le "
                               "combat."},
            {"k": "section", "t": "Raccourci clavier"},
            {"k": "field", "t": "Réinitialiser le combat",
             "c": {"k": "label", "t": ("appuie sur une touche…"
                                       if self._binding_now
                                       else bind_label())}},
            {"k": "button", "id": "begin_bind", "t": "Changer cette touche"},
            {"k": "note", "t": "Le raccourci ne fonctionne que lorsque Farever "
                               "est au premier plan, et n'affiche rien dans le "
                               "jeu. Il faut Ctrl, Maj ou Alt, sauf pour les "
                               "touches F1 à F24 et les boutons de souris. "
                               "Échap annule."},
            {"k": "section", "t": "Fenêtre"},
            {"k": "field", "t": "Taille de l'interface",
             "c": {"k": "slider", "id": "set_zoom", "v": int(self._zoom),
                   "min": 50, "max": 200, "step": 5, "unit": "%"}},
            {"k": "section", "t": "Fichiers"},
            {"k": "button", "id": "open_parses",
             "t": "Dossier des parses et rapports"},
            {"k": "button", "id": "open_log", "t": "Dossier du journal"},
        ]

    # ---- carried over from the old overlay, unchanged but for the shims above ----
    @staticmethod
    def _load_best_times():
        """The record book, tolerantly: a missing file is an empty one, and a
        hand-edited or corrupt entry drops rather than crashing the launch."""
        try:
            d = json.loads(BEST_TIMES_CACHE.read_text())
            return {str(k): float(v) for k, v in d.items()
                    if isinstance(v, (int, float)) and v > 0}
        except Exception:
            return {}

    def _save_best_times(self):
        try:
            BEST_TIMES_CACHE.parent.mkdir(parents=True, exist_ok=True)
            BEST_TIMES_CACHE.write_text(json.dumps(self._best_times, indent=1))
        except OSError as e:
            print(f"[meter] couldn't save best times: {e}", file=sys.stderr)

    def _record_boss_kill(self, kinds, secs):
        """Compare the kill against the stored best and say so on screen.

        The key is the PULL's boss kinds, sorted and joined — stable for a
        council pulled together (whichever member dies last), and for the
        Nightqueen it is her alone, because her copies never fire a second
        pull edge. The killed bar's kind would be neither."""
        if not kinds:
            # A bar with no kind can't key a record, but the time is still
            # worth saying — it just can't be compared to anything.
            self._show_kill_toast(f"Boss vaincu en {self._mmss(secs)}", best=False)
            return
        # The record keys on the internal kind — stable across localization
        # and any rename the cdb ships — but the toast speaks the game's
        # language: the kind is routinely not the name on the bar (the first
        # live kill said "CLEODORA" for a boss the game calls Honeyzabeth).
        key = "+".join(kinds)
        name = " + ".join(_boss_label(k) for k in kinds)
        prev = self._best_times.get(key)
        if prev is None:
            self._best_times[key] = secs
            self._save_best_times()
            text = f"{name} vaincu en {self._mmss(secs)} — premier kill enregistré"
            best = True
        elif secs < prev:
            self._best_times[key] = secs
            self._save_best_times()
            text = (f"{name} vaincu en {self._mmss(secs)} — nouveau record "
                    f"(avant : {self._mmss(prev)})")
            best = True
        else:
            text = f"{name} vaincu en {self._mmss(secs)} — record : {self._mmss(prev)}"
            best = False
        print(f"[meter] boss kill timed: {key} {secs:.1f}s"
              + (f" (best {self._best_times[key]:.1f}s)"), file=sys.stderr)
        self._show_kill_toast(text, best)

    def on_boss_timed_kill(self, kinds, secs):
        """The LAST boss bar went down killed — the fight is formally over and
        its clock has a reading. Called from the hook thread alongside the
        victory cue; the record and the toast belong to the Tk one.

        Not opt-in, deliberately: a record you had to switch on beforehand is
        a record you don't have when you finally want it."""
        self._enqueue(lambda: self._record_boss_kill(tuple(kinds), secs))()

    def auto_reset_boss(self) -> bool:
        """Read by the hook thread, so it stays a plain attribute read."""
        return bool(self._auto_reset_boss)

    def _toggle_auto_reset_boss(self):
        self._auto_reset_boss = not self._auto_reset_boss
        self._save_settings()



    def _save_rift_report(self, report):
        """The report into parses/, three ways: .json is the full metrics —
        the file _load_last_rift_report reads back, which is what lets 'Last
        Rift Report' survive a meter restart; .txt is the chat-pasteable
        lines; .png is the shareable image. Same folder, same lifecycle as
        the parse screenshots. Never fatal, and each format fails alone: no
        Pillow costs the picture, not the data."""
        base = f"rift-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            PARSES_DIR.mkdir(parents=True, exist_ok=True)
            (PARSES_DIR / f"{base}.json").write_text(json.dumps(report),
                                                     encoding="utf-8")
            (PARSES_DIR / f"{base}.txt").write_text(self._report_text(report),
                                                    encoding="utf-8")
            print(f"[meter] rift report saved to {PARSES_DIR / base}.json/.txt",
                  file=sys.stderr)
        except Exception as e:
            print(f"[meter] couldn't save the rift report: {e}",
                  file=sys.stderr)
        try:
            render_rift_report_image(report, PARSES_DIR / f"{base}.png")
        except Exception as e:
            print(f"[meter] couldn't render the rift report image: {e}",
                  file=sys.stderr)

    @staticmethod
    def _load_last_rift_report():
        """The newest saved rift report, or None — how a fresh session still
        has a 'Last Rift Report'. Timestamped filenames sort lexicographically,
        so newest is just last. Validated for shape, not trusted: a truncated
        or hand-edited file costs the button, never the meter."""
        try:
            files = sorted(PARSES_DIR.glob("rift-*.json"))
            if not files:
                return None
            data = json.loads(files[-1].read_text(encoding="utf-8"))
            phases = data.get("phases")
            if (isinstance(data.get("at"), (int, float))
                    and isinstance(phases, list) and len(phases) == 2
                    and all(isinstance(ph, dict)
                            and isinstance(ph.get("players"), list)
                            and isinstance(ph.get("elements"), list)
                            for ph in phases)):
                return data
            print(f"[meter] ignoring malformed rift report {files[-1].name}",
                  file=sys.stderr)
        except Exception as e:
            print(f"[meter] couldn't load the last rift report: {e}",
                  file=sys.stderr)
        return None

    def _report_text(self, data):
        """The plaintext version — chat-pasteable lines, no box drawing."""
        out = ["Farever France — " + (data.get("title") or "Rapport de faille")
               + (f" ({data['sub']})" if data.get("sub") else "")]
        for ph in data["phases"]:
            dur = ph["duration"]
            # Rate first here too. The card, the image and this line are three
            # renderings of one report, and a paste that ranked people by a
            # different number than the picture would be its own bug report.
            dps = _rate_text(ph["total"], dur, "DPS")
            hps = _rate_text(ph["heal"], dur, "HPS")
            out.append(f"== {phase_label(ph['label'])} — {self._mmss(dur)}, "
                       f"{dps or '— DPS'}, {hps or '— HPS'} "
                       f"({_n(ph['total'])} dégâts, {_n(ph['heal'])} soins"
                       + _overheal_note(ph, ", {:.0f}% de soin en excès")
                       + ") ==")
            players = ph["players"]
            if not players:
                out.append("  (rien d'enregistré)")
                continue
            for i, p in enumerate(players, 1):
                pct = p["total"] / ph["total"] * 100 if ph["total"] else 0.0
                rate = _rate_text(p["total"], dur, "dps")
                out.append(f"  dégâts {i}. {_report_name(p)} "
                           + (f"{rate} " if rate else "")
                           + f"({_n(p['total'])}, {_pct1(pct)})")
            healers = sorted((p for p in players if p["heal"] > 0.5),
                             key=lambda p: -p["heal"])
            for i, p in enumerate(healers, 1):
                pct = p["heal"] / ph["heal"] * 100 if ph["heal"] else 0.0
                rate = _rate_text(p["heal"], dur, "hps")
                out.append(f"  soins {i}. {_report_name(p)} "
                           + (f"{rate} " if rate else "")
                           + f"({_n(p['heal'])}, {_pct1(pct)}"
                           + _overheal_note(p, ", {:.0f}% en excès") + ")")
            if ph["elements"]:
                out.append("  types : " + " · ".join(
                    f"{element_label(el)} "
                    f"{_pct1(amt / ph['total'] * 100 if ph['total'] else 0.0)}"
                    for el, amt in ph["elements"][:8]))
        return "\n".join(out)

    @staticmethod
    def _mmss(secs):
        m, s = divmod(int(max(0, secs)), 60)
        return f"{m}:{s:02d}"

    @staticmethod
    def _elide_name(name, width=14):
        return name if len(name) <= width else name[:width - 1] + "…"

    def _toggle_parse(self):
        if self._parse_state is None and not self.game_connected():
            return                  # nothing to measure without the game
        if self._parse_state is None:
            self._parse_state = "countdown"
            self._parse_until = time.time() + PARSE_PREROLL_SECS
            self.parsewin.deiconify()
            self.parsewin.attributes("-topmost", True)
            self._set_parse_banner(f"PARSE DANS {PARSE_PREROLL_SECS}")
        else:
            self._stop_parse()

    def _begin_parse(self, now):
        """Pre-roll over: clear the meter and start the fixed-length sample."""
        self.session.reset()
        self.session.set_capture_window(PARSE_LENGTH_SECS)
        # Our own reset, so don't let the epoch watcher read it as the player
        # resetting out of parse mode.
        self._last_epoch = self.session.epoch
        self.focus_player = None
        self._parse_state = "parsing"
        self._parse_until = now + PARSE_LENGTH_SECS
        self._set_parse_banner(f"PARSE  {PARSE_LENGTH_SECS} s")

    def _finish_parse(self):
        """Nothing to switch off: the session's capture window has already
        elapsed, which stops both new data and the duration clock. This just
        moves the UI into its 'sample is sitting there to be read' state, and
        writes the result out before anything can clear it."""
        self._parse_state = "done"
        self._set_parse_banner(f"PARSE TERMINÉ  {PARSE_LENGTH_SECS} s",
                               fill=BG_HEADER_UNLOCKED)
        self._save_parse_image()

    def _parse_snapshot(self):
        """Everything the image needs, as plain data — same rows, same focus and
        the same merged skill tables the overlay is displaying."""
        duration, _ = self.session.current()
        rows = self._apply_mode(self.session.snapshot()[2])
        party_total = sum(p.total for p in rows)
        focus_name = self._resolve_focus(rows)
        fp = next((p for p in rows if p.name == focus_name), None)

        focus = None
        if fp is not None:
            fdps = fp.total / duration if duration > 0 else 0.0
            crit_pct = (fp.crits / fp.hits * 100) if fp.hits else 0.0
            stats = [f"{_n(fp.total)} dégâts", f"{fdps:.0f} DPS",
                     f"{fp.hits} coups", f"{crit_pct:.0f} % critiques",
                     f"{_n(fp.heal_total)} soins"]
            if fp.heal_total > 0.5:
                stats.append(f"{fp.overheal_pct:.0f} % de soin en excès")
            if fp.kills:
                stats.append(f"{fp.kills} kills")
            el = sorted(fp.elements.items(), key=lambda kv: -kv[1][1])
            focus = {
                "name": fp.name,
                "total": fp.total,
                "heal": fp.heal_total,
                "stats": " · ".join(stats),
                "skills": self._merge_named(fp.skills),
                "heals": self._merge_named(fp.heals),
                "elements": "  ".join(f"{element_label(k)}:{int(v[1])}"
                                      for k, v in el[:6]),
            }
        return {
            "title": f"Farever France — Parse de {PARSE_LENGTH_SECS} s",
            "when": time.strftime("%d/%m/%Y %H:%M"),
            "duration": duration,
            "mode": "GROUPE" if self.mode == "party" else "TOUS LES JOUEURS",
            "rows": [{
                "name": p.name, "total": p.total, "heal": p.heal_total,
                "heal_self": p.heal_self, "overheal": p.overheal_pct,
                "dps": p.total / duration if duration > 0 else 0.0,
                "pct": (p.total / party_total * 100) if party_total else 0.0,
                "is_me": p.is_me,
            } for p in rows],
            "focus": focus,
        }

    def _open_parses(self):
        """Open the parse folder in Explorer. Created on demand, so the button
        does something sensible before the first parse has ever been saved
        rather than failing on a folder that doesn't exist yet."""
        try:
            PARSES_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(PARSES_DIR)
        except Exception as e:
            print(f"[meter] couldn't open {PARSES_DIR}: {e}", file=sys.stderr)

    def _save_parse_image(self):
        """Write the finished parse to parses/. Never fatal: a missing Pillow or
        an unwritable folder costs you the picture, not the parse that's sitting
        on screen."""
        try:
            name = f"parse-{time.strftime('%Y%m%d-%H%M%S')}.png"
            out = render_parse_image(self._parse_snapshot(), PARSES_DIR / name)
            print(f"[meter] parse saved to {out}", file=sys.stderr)
        except ImportError:
            print("[meter] parse image skipped — Pillow isn't installed "
                  "(pip install pillow).", file=sys.stderr)
        except Exception as e:
            print(f"[meter] couldn't save the parse image: {e}", file=sys.stderr)

    def _stop_parse(self):
        """Back to live metering — which clears the sample. Resuming capture
        into a finished parse would quietly append live hits to the numbers you
        were reading, so leaving them would be worse than dropping them."""
        self._parse_state = None
        self._hide_parse_banner()
        self.session.reset()
        self._last_epoch = self.session.epoch
        self.focus_player = None

    def _tick_parse(self):
        """Drive the countdown from the refresh loop. Only the phase changes
        matter for correctness — the exact 60 s cutoff is enforced inside the
        session, not here, so a late tick can't lengthen the sample."""
        if self._parse_state is None or self._parse_state == "done":
            return
        now = time.time()
        left = self._parse_until - now
        if self._parse_state == "countdown":
            if left <= 0:
                self._begin_parse(now)
            else:
                self._set_parse_banner(f"PARSE DANS {math.ceil(left)}")
        elif self._parse_state == "parsing":
            if left <= 0:
                self._finish_parse()
            else:
                self._set_parse_banner(f"PARSE  {math.ceil(left)} s")

    def _begin_bind_capture(self):
        """Listen for the next keypress and make it the reset bind.

        POLLED, not bound. The obvious version — focus the button and take a
        Tk <KeyPress> — silently never fires unless something has deliberately
        claimed the keyboard first: the overlay windows are overrideredirect,
        and every one of them except the control menu carries WS_EX_NOACTIVATE
        precisely so that clicking it can't steal focus from the game, which
        also means it never receives a keystroke. The menu is the exception
        only while a Social search box has focus (see _stop_typing) — and
        _set_menu_tab ends that on the way to this tab, so by the time you are
        looking at this button the keyboard belongs to Farever again.

        GetAsyncKeyState doesn't care who has focus, needs no hook, and reads
        the same virtual-key codes the hook will later match against — so what
        you press here is exactly what will fire in play."""
        if self._binding_now:
            self._end_bind_capture()
            return
        self._binding_now = True
        self._bind_poll_job = None
        # The prompt is drawn from _binding_now by _menu_spec, not written to a
        # button here — the panel lives in another process.
        self._poll_bind_capture()

    def _poll_bind_capture(self):
        """Watch the keyboard until something bindable is held down."""
        if not self._binding_now or sys.platform != "win32":
            return
        u = ctypes.windll.user32

        def down(vk):
            return bool(u.GetAsyncKeyState(vk) & 0x8000)

        if down(0x1B):                      # Esc — back out, bind unchanged
            self._end_bind_capture()
            return
        shift, ctrl, alt = down(VK_SHIFT), down(VK_CONTROL), down(VK_MENU)
        # Middle and the side buttons are offered; left and right never are,
        # and 0x03 is Break rather than a button at all.
        for vk in list(VK_MOUSE) + list(range(0x08, 0xFF)):
            if vk in VK_UNBINDABLE or not down(vk):
                continue
            # A modifier is required for anything that would otherwise be a
            # plain keystroke — the hook swallows what it fires on, so a bare
            # letter costs you that key in game. F-keys and the bindable mouse
            # buttons are exempt: nothing in Farever wants them by default, and
            # binding Mouse 4 on its own is the normal thing to do.
            if (not (shift or ctrl or alt) and not (0x70 <= vk <= 0x87)
                    and vk not in VK_MOUSE):
                break                       # keep listening; they'll try again
            self._set_reset_bind({"vk": vk, "shift": shift, "ctrl": ctrl,
                                  "alt": alt})
            self._end_bind_capture()
            return
        self._bind_poll_job = self.root.after(40, self._poll_bind_capture)

    def _end_bind_capture(self):
        self._binding_now = False
        if getattr(self, "_bind_poll_job", None):
            try:
                self.root.after_cancel(self._bind_poll_job)
            except Exception:
                pass
            self._bind_poll_job = None
        # Nothing to unbind: the capture is polled through GetAsyncKeyState
        # (see _begin_bind_capture) and never went through a Tk widget. The new
        # label reaches the panel with the next state push.
        self.menubridge.invalidate()

    def _set_reset_bind(self, bind):
        RESET_BIND.update(bind)
        self._save_settings()
        self._draw_hint()          # the floating hint carries the same label
        # Only the RegisterHotKey fallback needs telling; the low-level hook
        # reads RESET_BIND on every keypress and has already picked it up.
        if RESET_BIND.get("vk") in VK_MOUSE and REBIND_TO[0]:
            print("[meter] mouse buttons need the low-level hook, which isn't "
                  "installed — this binding won't fire.", file=sys.stderr)
        if REBIND_TO[0] and sys.platform == "win32":
            try:
                ctypes.windll.user32.PostThreadMessageW(
                    REBIND_TO[0], WM_REBIND, 0, 0)
            except Exception as e:
                print(f"[meter] couldn't re-register the hotkey: {e}",
                      file=sys.stderr)
        print(f"[meter] reset bind is now {bind_label()}", file=sys.stderr)

    def _toggle_mode(self):
        self.mode = "all" if self.mode == "party" else "party"
        self._save_settings()
        self.focus_player = None
        self.session.reset()

    def _apply_mode(self, rows):
        if self.mode == "party":
            party = [p for p in rows if p.in_party]
            # If we haven't identified any party members yet, fall back to me so
            # the meter isn't blank (e.g. solo, or group not read yet).
            return party if party else [p for p in rows if p.is_me]
        return rows

    def _merge_named(self, table):
        """Merge a skill-id table by display name (e.g. all weapons' base
        "Attack" share one row) and return [(label, total, hits, crits, self)]
        sorted by total desc.

        Damage rows carry [hits, total, crits] and healing rows carry a fourth
        column (the self-healed share). Both go through here, so the fourth is
        read optionally and damage simply reports 0 — a damage bar has nothing
        to split."""
        merged: dict[str, list] = defaultdict(lambda: [0, 0.0, 0, 0.0])
        for sid, vals in table.items():
            label = self.session.skill_names.get(sid) or _pretty_id(sid)
            m = merged[label]
            m[0] += vals[0]; m[1] += vals[1]; m[2] += vals[2]
            m[3] += vals[3] if len(vals) > 3 else 0.0
        out = [(label, v[1], v[0], v[2], v[3]) for label, v in merged.items()]
        out.sort(key=lambda t: -t[1])
        return out[:MAX_SKILL_ROWS]

    def _hold_last(self, rows, duration):
        """Keep the previous encounter on screen until the next one starts.

        A reset empties the store instantly, and the meter used to go blank
        with it — which is the one moment you most want to read it. A boss
        pull wipes the meter to measure the pull; a wipe or a zone change
        wipes it because the fight is over. In every case the numbers you were
        looking at vanish at the exact instant they became final.

        So the last non-empty set of rows is held and re-shown while the live
        one has nothing in it. Nothing is faked: the rows, the totals and the
        duration are all the previous encounter's own, frozen together, and
        the header says LAST so it can't be mistaken for a live fight. The
        first hit of the next encounter replaces them.

        Returns (rows, duration, holding).
        """
        if rows:
            # Held as a snapshot, not a reference: PlayerAgg copies come out
            # of session.snapshot() already detached, but the LIST is ours to
            # keep, and the sort above has already ordered it the way it was
            # displayed.
            self._held_rows = rows
            self._held_duration = duration
            return rows, duration, False
        if not self._held_rows:
            return rows, duration, False
        # Party-only means party-only, including for the encounter being held
        # over. A set recorded while all-players was on carries people who are
        # not in the group, and re-showing it after the player has switched
        # back would quietly contradict the setting they just chose — the
        # meter would be listing strangers under a header that says PARTY.
        #
        # Dropped wholesale rather than filtered down to the party members in
        # it: the totals, the percentages and the duration were all computed
        # against the full set, so a filtered view would be a set of numbers
        # that never existed. Better to show nothing than to show arithmetic
        # about a fight that did not happen.
        if self.mode == "party" and any(
                not p.in_party and not p.is_me for p in self._held_rows):
            self._held_rows = []
            self._held_duration = 0.0
            return rows, duration, False
        return self._held_rows, self._held_duration, True

    def _resolve_focus(self, rows):
        if self.focus_player and any(p.name == self.focus_player for p in rows):
            return self.focus_player
        me = next((p.name for p in rows if p.is_me), None)
        return me or (rows[0].name if rows else None)

    def _apply_rift_view(self, kind):
        """Switch the view the way the rift wants it — to all-players on the
        way in, back to party-only on the way out.

        That resets the encounter, the same as the mode button: the two views
        can't share one encounter without the percentages lying. Saved too, so
        a restart comes back on the view you're actually looking at."""
        want = "all" if kind == "enter" else "party"
        if self.mode == want:
            return False
        self.mode = want
        self.focus_player = None
        self.session.reset()
        self._save_settings()
        return True

    @staticmethod
    def _help_articles():
        """Every article on disk, parsed once and cached.

        Cached on the function rather than the instance because the files are
        read-only assets — re-reading them on every refresh tick would be four
        file opens a second for prose that cannot change while the meter runs.
        """
        cached = getattr(App._help_articles, "_cache", None)
        if cached is not None:
            return cached
        out = []
        try:
            files = sorted(HELP_DIR.glob("*.md"))
        except OSError:
            files = []
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            title, blurb, blocks = _parse_help(text)
            out.append({"id": f.stem, "title": title or f.stem,
                        "blurb": blurb, "blocks": blocks})
        App._help_articles._cache = out
        return out



    def _page_help(self):
        arts = self._help_articles()
        if not arts:
            return [{"k": "section", "t": "Aide"},
                    {"k": "note", "warn": True,
                     "t": "Les articles d'aide sont absents de cette version."}]
        # One article open: its text, and the way back.
        if self._help_open:
            art = next((a for a in arts if a["id"] == self._help_open), None)
            if art:
                return ([{"k": "button", "id": "help_close",
                          "t": "‹  Tous les sujets d'aide"},
                         {"k": "section", "t": art["title"]}]
                        + art["blocks"])
        # ...or the index, the repair first.
        seen, out = set(), self._repair_nodes()
        for heading, ids in HELP_GROUPS:
            rows = [a for a in arts if a["id"] in ids]
            if not rows:
                continue
            seen.update(a["id"] for a in rows)
            out.append({"k": "section", "t": heading})
            out.append({"k": "list", "id": f"help:{heading}", "rows": [
                {"t": a["title"], "meta": a["blurb"],
                 "btns": [{"id": "help_open", "t": "Lire",
                           "p": {"id": a["id"]}}]}
                for a in rows]})
        rest = [a for a in arts if a["id"] not in seen]
        if rest:
            out.append({"k": "section", "t": "Autres"})
            out.append({"k": "list", "id": "help:more", "rows": [
                {"t": a["title"], "meta": a["blurb"],
                 "btns": [{"id": "help_open", "t": "Lire",
                           "p": {"id": a["id"]}}]}
                for a in rest]})
        return out


    def _repair_nodes(self):
        out = [{"k": "section", "t": "Après une mise à jour du jeu"},
               {"k": "note",
                "t": "Farever France relit les données du jeu tout seul quand "
                     "Farever change. Si une page reste vide ou que la "
                     "connexion échoue après une mise à jour, Réparer refait "
                     "cette lecture depuis zéro puis se reconnecte au jeu, "
                     "sans relancer l'application. Compte quelques secondes "
                     "(plusieurs minutes la toute première fois)."},
               {"k": "button", "id": "repair_data",
                "t": "Réparation en cours…" if self._repairing
                     else "Réparer",
                "tone": "disabled" if self._repairing else None}]
        if self._repair_note and not self._repairing:
            ok, text = self._repair_note
            out.append({"k": "note", "warn": not ok, "t": text})
        return out

    @staticmethod
    def _tick(on, label):
        """The panel's checkbox convention, unchanged from the Tk menu: a
        standing setting reads as its STATE, not as the action that would
        change it."""
        return ("☑  " if on else "☐  ") + label




    def _enqueue(self, fn):
        """Wrap `fn` so it runs on the Tk thread at the next refresh. Hotkey and
        mouse-hook callbacks arrive on their own threads, and Tk is not
        thread-safe; menu buttons use it too so every action takes one path."""
        def handler():
            with self._q_lock:
                self._action_q.append(fn)
        return handler

    def _drain(self):
        with self._q_lock:
            q, self._action_q = self._action_q, []
        for fn in q:
            try:
                fn()
            except Exception as e:
                print("[action]", e, file=sys.stderr)
        # Only while the game's menu is open — that's the only time the overlay
        # is interactive, and so the only time it can have taken focus.
        if q and self._menu_unlock:
            self._refocus_game()


    def _install_hotkeys(self):
        start_hotkeys({HK_RESET: self._enqueue(self._manual_reset)},
                      lambda: self.target_pid)

    def game_connected(self):
        if self.link is None:
            return bool(self.target_pid)
        return self.link.status()[0] == GameLink.CONNECTED

    def on_link_changed(self):
        """GameLink's state moved. Safe from any thread."""
        self._enqueue(self._on_link_changed)()

    def on_link_steps(self):
        """A connection step moved (not the state: no event, no reset) —
        just show it. Safe from any thread."""
        self._enqueue(self.menubridge.invalidate)()

    def on_game_disconnected(self):
        """The game closed (or the meter is leaving it). Safe from any
        thread."""
        self._enqueue(self._on_game_disconnected)()

    def _on_game_disconnected(self):
        self.target_pid = None
        self._game_hwnd = None
        # The hook died with the game, so the close events for whatever was
        # open are never coming — ui_state would keep reporting the escape
        # menu as open, and with it the control menu as unlocked, forever.
        self.ui_state.clear()
        self._menu_unlock = False
        # A parse whose data source just died is not a sample of anything.
        if self._parse_state is not None:
            self._stop_parse()
        self._refresh_visibility()

    def _link_spec(self):
        """The title band's game state: "play" (Farever is not running: a
        button that launches it), "launching" (just asked Steam), then
        "connecting", "ingame" or "failed"."""
        if self.link is None:
            return {"state": "ingame", "t": "En jeu"}
        state, detail, _pid = self.link.status()
        if state == GameLink.CONNECTED:
            self._launching_until = 0
            return {"state": "ingame", "t": "En jeu"}
        if state == GameLink.CONNECTING or self._repairing:
            if REGENERATING.is_set():
                return {"state": "connecting", "t": "Mise à jour…",
                        "tip": "relecture des données du jeu"}
            return {"state": "connecting", "t": "Connexion…",
                    "tip": "au jeu en cours"}
        if state == GameLink.FAILED:
            return {"state": "failed",
                    "tip": f"Connexion à Farever impossible : {detail}"}
        if time.time() < self._launching_until:
            return {"state": "launching", "t": "Lancement…",
                    "tip": "Farever démarre"}
        return {"state": "play", "tip": "Lancer Farever (via Steam)"}

    def _launch_game(self):
        """Launch Farever through Steam (it handles the login and updates),
        then look for it straight away."""
        try:
            os.startfile(f"steam://rungameid/{FAREVER_STEAM_APPID}")
        except OSError as e:
            self._toast_msg(f"Impossible de lancer Farever : {e}")
            return
        self._launching_until = time.time() + 120
        if self.link is not None:
            self.link.retry()
        self.menubridge.invalidate()

    def _link_clicked(self):
        if self.link is not None and not self.game_connected():
            self.link.retry()

    def _manual_reset(self):
        """A reset the PLAYER asked for — the hotkey, or the panel's button.

        Separate from session.reset() because the automatic resets (a zone
        change, a boss pull, switching player view) must stay silent: they
        happen while you are reading the meter for other reasons, and a banner
        over it every time you walked through a door would be noise.
        """
        self.session.reset()
        self._show_reset_toast()


_COLLECTION = None


def collection_catalogue():
    """{"mounts": [...], "gliders": [...], "pets": [...]}: every collectible
    with how it is obtained, from analysis_out/collection.json (built from the
    game's data and levels by hltools/collection_data.py). {} when absent."""
    global _COLLECTION
    if _COLLECTION is None:
        try:
            _COLLECTION = json.loads(
                (ANALYSIS / "collection.json").read_text(encoding="utf-8"))
        except Exception:
            _COLLECTION = {}
    return _COLLECTION


COLLECTION_CATS = (("mounts", "Montures", "monture"),
                   ("gliders", "Planeurs", "planeur"),
                   ("pets", "Compagnons", "compagnon"),
                   ("gears", "Équipements", "équipement"),
                   ("items", "Objets", "objet"))
# the armour appearances: slots in the game's order, and the aptitudes
GEAR_SLOTS = (("Head", "Tête"), ("Shoulders", "Épaules"), ("Chest", "Torse"),
              ("Hands", "Mains"), ("Waist", "Taille"), ("Legs", "Jambes"),
              ("Feet", "Pieds"), ("Back", "Dos"))
# a piece's aptitudes -> the classes that wear it (unit.props.aptitudes:
# Warrior Fighter, Rogue Assassin, Mage Wizard, Priest Cleric); none = all
APTITUDE_CLASSES = {"Fighter": "warrior", "Assassin": "rogue",
                    "Wizard": "mage", "Cleric": "priest"}
GEAR_CLASSES = ("warrior", "mage", "rogue", "priest")
FACTION_ACTS = (("WorldElite", "élite", "élites"),
                ("FightStone", "pierre de combat", "pierres de combat"),
                ("ChestOrb", "orbe à coffre", "orbes à coffre"),
                ("WorldCamp", "camp", "camps"),
                ("TimerCollectRun", "course", "courses"),
                ("Ascension", "ascension", "ascensions"))
# the item codex's rank steps (codex_units.json "item": 1, 5, 25, 50)
ITEM_RANKS = 4


_CODEX_ITEMS = None


def codex_items_catalogue():
    """[{id, rarity, type, src, uses}]: the items the game's item codex
    counts (crafting components, ores, cloth, leather), from
    analysis_out/codex_items.json (hltools/codex_items.py)."""
    global _CODEX_ITEMS
    if _CODEX_ITEMS is None:
        try:
            _CODEX_ITEMS = json.loads(
                (ANALYSIS / "codex_items.json").read_text(encoding="utf-8"))
        except Exception:
            _CODEX_ITEMS = []
    return _CODEX_ITEMS
CLASS_LABELS = {"warrior": "Guerrier", "mage": "Mage", "rogue": "Voleur",
                "priest": "Prêtre"}
CHEST_LABELS = {"VaultChest": "Coffre-fort", "WorldChest": "Coffre",
                "BossChest": "Coffre du boss"}


# The game's generic [terms] (skill kinds), named in no sheet.
# (singular, plural): the texts write "[WeaponSkill]s".
FR_TERMS = {"Skill": ("compétence", "compétences"),
            "WeaponSkill": ("compétence d'arme", "compétences d'arme"),
            "ClassSkill": ("compétence de classe", "compétences de classe"),
            "ComboAttack": ("attaque combo", "attaques combo")}


def _fr_ref(text):
    """The game's [Id] references in a French text, replaced by names."""
    def one(m):
        rid, plural = m.group(1), m.group(2)
        if rid in FR_TERMS:
            return FR_TERMS[rid][1 if plural else 0]
        for sheet in ("zone", "unit", "activity", "item", "itemType",
                      "unitType", "skill", "attribute", "faction"):
            name = _fr_names(sheet).get(rid)
            if name:
                return name + plural
        return _pretty_id(rid) + plural
    return re.sub(r"\[([A-Za-z0-9_]+)\](s?)", one, text or "")


def _zone_label(z):
    return _fr_names("zone").get(z) or _pretty_id(z or "")


def _unit_label(u):
    return (_fr_names("unit").get(u) or _unit_names().get(u)
            or _pretty_id(u or ""))


def _source_text(s, bosses):
    """One way to obtain a collectible, in French."""
    k, ch = s.get("k"), s.get("chance")
    pct = ("" if ch is None else " — garanti" if ch >= 1
           else f" — {_pct(ch)}")
    if k == "family":
        name = _fr_names("unitType").get(s.get("id")) or _pretty_id(s["id"])
        return f"Butin des ennemis : {name}{pct}"
    if k == "unit":
        dg = bosses.get(s.get("id"))
        where = f" ({dungeon_name(dg)})" if dg else ""
        return f"Butin de {_unit_label(s.get('id'))}{where}{pct}"
    if k == "chest":
        kind = CHEST_LABELS.get(s.get("id"), "Coffre")
        zone = f" — {_zone_label(s['zone'])}" if s.get("zone") else ""
        return f"{kind}{zone}{pct}"
    if k == "shop":
        npc = s.get("npc")
        who = (_fr_names("unit").get(npc) if npc else None) or "un marchand"
        zone = f" — {_zone_label(s['zone'])}" if s.get("zone") else ""
        cost = ", ".join(f"{c['n']} × {item_label(c['item'])}"
                         if c.get("n") else item_label(c["item"])
                         for c in s.get("cost") or () if c.get("item"))
        return f"Vendu par {who}{zone}" + (f" (prix : {cost})" if cost else "")
    if k == "ach":
        chain = s.get("chain") or [s.get("id")]
        name = next((_fr_names("ach").get(a) for a in chain
                     if _fr_names("ach").get(a)), None)
        desc = next((_fr_desc("ach").get(a) for a in chain
                     if _fr_desc("ach").get(a)), "")
        v = s.get("v")
        desc = _fr_ref(desc.replace("::targetValue::", str(v)
                                    if v is not None else "…"))
        name = _fr_ref(name) if name else desc or _pretty_id(s.get("id"))
        if name and v and len(chain) > 1:
            name = f"{name} ({v})"
        return f"Succès « {name} »" + (f" : {desc}" if desc and desc != name
                                        else "")
    if k == "starter":
        what = "Équipement" if s.get("gear") else "Planeur"
        return f"{what} de départ du {CLASS_LABELS.get(s.get('cls'), '?')}"
    if k == "world":
        lvl = s.get("lvl") or 1
        return (f"Butin aléatoire du monde (ennemis, coffres, activités) "
                f"de niveau {max(1, lvl - 1)} à {lvl + 2}")
    if k == "faction":
        f = s.get("f")
        info = (collection_catalogue().get("factions") or {}).get(f) or {}
        name = faction_label(f)
        dgs = [_fr_names("activity").get(a) or _pretty_id(a)
               for a in info.get("dungeons") or ()]
        acts = [f"{n} {one if n == 1 else many}"
                for key, one, many in FACTION_ACTS
                for n in [(info.get("acts") or {}).get(key)] if n]
        n = info.get("chests") or 0
        lines = [f"Butin de la faction {name} : 20 % par activité"
                 + (", 5 % par coffre" if n else "")]
        if dgs:
            lines.append("Donjons : " + ", ".join(dgs))
        if acts:
            lines.append("Activités du monde : " + ", ".join(acts))
        if n:
            lines.append(f"Coffres de la faction : {n}")
        return "\n".join(lines)
    if k == "gather":
        name = _fr_names("gatherable").get(s.get("id")) or _pretty_id(
            s.get("id"))
        return f"Récolte : {name}{pct}"
    if k == "craft":
        job = _fr_names("job").get(s.get("job")) or _pretty_id(s.get("job"))
        inputs = " + ".join(f"{n} × {item_label(i)}"
                            for i, n in s.get("input") or ())
        made = f" (×{s['n']})" if (s.get("n") or 1) > 1 else ""
        return (f"Fabrication{made} : {job} niv. {s.get('lvl') or 1}"
                + (f" — {inputs}" if inputs else ""))
    if k == "scrap":
        what = "objet rare" if s.get("id") == "Scrap_Rare" else "objet"
        return f"Recyclage d'un {what} à la station d'Étincelle{pct}"
    if k == "combine":
        return "Combinaison : " + " + ".join(
            f"{n} × {item_label(i)}" for i, n in s.get("from") or ())
    if k == "salvage":
        lo, hi = (s.get("lvl") or [1, 25])[:2]
        rar = s.get("rarity")
        what = (f"d'un équipement {rarity_label(rar).lower()}" if rar
                else "d'un équipement")
        return f"Démontage {what} de niveau {lo} à {hi}"
    if k == "spawn":
        zones = ", ".join(_zone_label(z) for z in s.get("zones") or ())
        where = "en faille" if s.get("rift") else (zones or "dans le monde")
        rate = ("" if ch is None or ch >= 1
                else f" · {_pct(ch)} des apparitions")
        return f"Se capture : {where}{rate}"
    return k or "?"


_SPARK = None


def _spark_units():
    global _SPARK
    if _SPARK is None:
        try:
            _SPARK = set(json.loads((ANALYSIS / "unit_traits.json")
                                    .read_text(encoding="utf-8"))["spark"])
        except Exception:
            _SPARK = set()
    return _SPARK


def collection_view(owned, item_codex=None):
    """The Collection page's data: categories with counts, every item with
    its name, rarity, whether it is owned, description and sources — and
    for the "items" category (the game's item codex) each one's count and
    rank."""
    cat = dict(collection_catalogue())
    item_codex = item_codex or {}
    cat["items"] = codex_items_catalogue()
    owned = dict(owned, items=[k for k, v in item_codex.items()
                               if v and v[0] > 0])
    bosses = {d.get("boss"): d.get("kind") for d in dungeon_catalogue()}
    cats, items = [], []
    for key, label, one in COLLECTION_CATS:
        mine = set(owned.get(key) or ())
        rows = cat.get(key) or []
        got = sum(1 for e in rows if e["id"] in mine)
        cats.append({"v": key, "t": label, "one": one, "n": len(rows),
                     "got": got})
        for e in rows:
            iid = e["id"]
            pet = key == "pets"
            spark = pet and iid in _spark_units()
            rar = e.get("rarity") or ""
            srcs = []
            spawns = {}
            for s in e.get("src") or ():
                if s.get("k") == "spawn":
                    # one line per rate, with every zone it spawns in
                    key2 = (bool(s.get("rift")), s.get("chance"))
                    if key2 not in spawns:
                        spawns[key2] = dict(s, zones=[])
                        srcs.append(spawns[key2])
                    spawns[key2]["zones"] += [z for z in s.get("zones") or ()
                                              if z not in spawns[key2]["zones"]]
                else:
                    srcs.append(s)
            count, rank = (item_codex.get(iid) or [0, 0])[:2] \
                if key == "items" else (None, None)
            items.append({
                "id": iid, "c": key, "own": iid in mine,
                "count": count, "rank": rank,
                "rmax": ITEM_RANKS if key == "items" else None,
                "uses": e.get("uses") if key == "items" else None,
                "name": _unit_label(iid) if pet else item_label(iid),
                "rk": "legendary" if spark else rar.lower(),
                "rar": "Étincelle" if spark else
                       (rarity_label(rar) if rar else ""),
                "desc": _fr_ref(_fr_desc("item").get(iid)) if not pet else "",
                "sl": e.get("slot"),
                "slot": dict(GEAR_SLOTS).get(e.get("slot")),
                "cls": [APTITUDE_CLASSES[a] for a in e.get("apt") or ()
                        if a in APTITUDE_CLASSES],
                "apt": (", ".join(CLASS_LABELS[APTITUDE_CLASSES[a]]
                                  for a in e.get("apt") or ()
                                  if a in APTITUDE_CLASSES)
                        or "toutes") if key == "gears" else "",
                "lvl": e.get("lvl"),
                "src": [line for s in srcs
                        for line in _source_text(s, bosses).split("\n")]})
    return {"cats": cats, "items": items,
            "slots": [{"v": v, "t": t} for v, t in GEAR_SLOTS],
            "classes": [{"v": c, "t": CLASS_LABELS[c]} for c in GEAR_CLASSES]}


_BESTIARY = None


def bestiary_catalogue():
    """{"placed": [{id, family, tier, zones, regions, lvl}] — every monster
    the levels place —, "units": {id: {family, tier, region}} — every monster
    of the game —, "families": [...]}, from analysis_out/bestiary.json
    (hltools/bestiary_data.py)."""
    global _BESTIARY
    if _BESTIARY is None:
        try:
            _BESTIARY = json.loads(
                (ANALYSIS / "bestiary.json").read_text(encoding="utf-8"))
        except Exception:
            _BESTIARY = {}
        if isinstance(_BESTIARY, list):         # before the family view
            _BESTIARY = {"placed": _BESTIARY}
    return _BESTIARY


_CODEX_SETS = None


def _codex_thresholds():
    """tier -> the kill counts of the codex's three ranks (the game's own
    numbers, analysis_out/codex_units.json)."""
    global _CODEX_SETS
    if _CODEX_SETS is None:
        try:
            _CODEX_SETS = json.loads((ANALYSIS / "codex_units.json")
                                     .read_text(encoding="utf-8"))
        except Exception:
            _CODEX_SETS = {}
    return _CODEX_SETS.get("thresholds") or {}


HUNT_TIERS = {"elite": "Élite / boss", "big": "Grand", "foe": ""}
HUNT_REGIONS = ("Z1_Region", "Z2_Region", "Z3_Region", "rift")


def _family_label(fam):
    if fam == "Demon_Rift":             # "Démons" too in the game's text
        return "Démons des failles"
    if fam == "Human":                  # the game has no French name for it
        return "Humains"
    return (_fr_names("unitType").get(fam) or _pretty_id(fam)) if fam else ""


def _family_drops(owned):
    """family -> the collectibles every monster of that family can drop
    (its unitType loot table), each {id, name, chance, own}."""
    out = defaultdict(list)
    for cat in ("mounts", "gliders"):
        mine = set(owned.get(cat) or ())
        for e in collection_catalogue().get(cat) or ():
            for s in e.get("src") or ():
                if s.get("k") == "family" and s.get("chance"):
                    out[s["id"]].append({"id": e["id"],
                                         "name": item_label(e["id"]),
                                         "chance": s["chance"],
                                         "own": e["id"] in mine})
    return out


def bestiary_view(ranks, owned=None):
    """The hunting log's data: regions, every monster with its name, family,
    kills and codex rank, and every family with its total and the
    collectibles its loot table can give. Monsters the codex holds but the
    levels don't place (rift waves, invasions, summons) are listed too."""
    thresholds = _codex_thresholds()
    cat = bestiary_catalogue()
    every = cat.get("units") or {}
    rows, known = [], set()
    for e in cat.get("placed") or ():
        known.add(e["id"])
        rows.append(e)
    for uid in ranks:
        if uid in known or uid.startswith("TODO") or uid == "Dummy":
            continue
        meta = every.get(uid) or {}
        rows.append({"id": uid, "family": meta.get("family") or "",
                     "tier": meta.get("tier") or "foe",
                     "regions": [meta["region"]] if meta.get("region")
                     else [], "zones": []})
    regions = []
    for r in HUNT_REGIONS + ("",):
        n = sum(1 for e in rows if (e.get("regions") or [""])[0] == r)
        if n:
            regions.append({"v": r or "other",
                            "t": "Failles et invasions" if r == "rift"
                            else _fr_names("zone").get(r) or "Autres"
                            if r else "Autres", "n": n})
    items = []
    for e in rows:
        kills, rank = (ranks.get(e["id"]) or [0, 0])[:2]
        steps = thresholds.get(e.get("tier") or "foe") or []
        nxt = next((t for t in steps if t > kills), None)
        fam = e.get("family") or ""
        reg = (e.get("regions") or [""])[0]
        items.append({
            "id": e["id"], "name": _unit_label(e["id"]),
            "fam": _family_label(fam), "famId": fam,
            "reg": reg if reg in HUNT_REGIONS else "other",
            "zones": ", ".join(_zone_label(z) for z in e.get("zones") or ()
                               [:4]),
            "tier": HUNT_TIERS.get(e.get("tier"), ""),
            "kills": int(kills), "rank": int(rank),
            "max": len(steps) or 3,
            "next": nxt})
    fams = {}
    for it in items:
        f = it["famId"]
        if not f:
            continue
        a = fams.setdefault(f, {"id": f, "name": it["fam"], "kills": 0,
                                "species": 0, "hunted": 0,
                                "top": (-1, "")})
        a["top"] = max(a["top"], (it["kills"], it["id"]))
        a["kills"] += it["kills"]
        a["species"] += 1
        a["hunted"] += 1 if it["kills"] else 0
    for f, a in fams.items():
        # the family's own picture, else its most hunted species'
        a["img"] = (f"family_{f}"
                    if (ANALYSIS / "bestiary_img" / f"family_{f}.webp")
                    .exists() else a["top"][1])
        del a["top"]
    return {"regions": regions, "items": items,
            "families": sorted(fams.values(), key=lambda a: -a["kills"]),
            "farm": _farm_view(items, fams, owned or {}),
            "total": sum(i["kills"] for i in items)}


def _farm_view(items, fams, owned):
    """The mounts and gliders a monster can drop, each with every monster
    (a whole family, or one unit — a dungeon boss, an elite demon) that can
    drop it, the kills behind each, and the chances: per kill, and of having
    seen it drop by now (1 - (1-p)^kills, each kill an independent roll)."""
    by_id = {it["id"]: it for it in items}
    out = []
    for cat, label in (("mounts", "Monture"), ("gliders", "Planeur")):
        mine = set(owned.get(cat) or ())
        for e in collection_catalogue().get(cat) or ():
            sources, miss = [], 1.0
            for s in e.get("src") or ():
                p = s.get("chance")
                if s.get("k") not in ("family", "unit") or not p:
                    continue
                if s["k"] == "family":
                    fam = fams.get(s["id"]) or {}
                    kills = fam.get("kills", 0)
                    species = sorted((it for it in items
                                      if it["famId"] == s["id"]
                                      and it["kills"]),
                                     key=lambda it: -it["kills"])
                    sources.append({
                        "kind": "family", "fid": s["id"],
                        "img": fam.get("img") or "",
                        "name": _family_label(s["id"]),
                        "sub": f"toute la famille · {fam.get('species', 0)} "
                               "espèces",
                        "species": [{"name": it["name"], "k": it["kills"]}
                                    for it in species[:8]],
                        "kills": kills, "p": p})
                else:
                    it = by_id.get(s["id"]) or {}
                    kills = it.get("kills", 0)
                    dg = next((d.get("kind") for d in dungeon_catalogue()
                               if d.get("boss") == s["id"]), None)
                    sources.append({
                        "kind": "unit", "img": s["id"],
                        "name": _unit_label(s["id"]),
                        "sub": (f"boss de {dungeon_name(dg)}" if dg else
                                it.get("fam") or "monstre"),
                        "kills": kills, "p": p})
                miss *= (1 - p) ** kills
            if not sources:
                continue
            # One set of numbers per item: the kills of every source summed,
            # the chance per kill (a range when the sources differ), and the
            # chance of having seen it drop by now over all of them.
            total = sum(s["kills"] for s in sources)
            ps = sorted({s["p"] for s in sources})
            pct = (_pct(ps[0]) if len(ps) == 1
                   else f"{_pct(ps[0])} à {_pct(ps[-1])}")
            odds = (f"1 chance sur {_n(round(1 / ps[0]))}" if len(ps) == 1
                    else "selon le monstre")
            # every monster that can drop it: a family's species, or the
            # unit itself — portraits only, the name on hover
            mobs = []
            for s in sources:
                if s["kind"] == "family":
                    mobs += [{"img": it["id"], "name": it["name"],
                              "k": it["kills"]}
                             for it in items if it["famId"] == s["fid"]]
                else:
                    mobs.append({"img": s["img"], "name": s["name"],
                                 "k": s["kills"]})
            mobs.sort(key=lambda x: -x["k"])
            out.append({"id": e["id"], "name": item_label(e["id"]),
                        "cat": label, "rk": (e.get("rarity") or "").lower(),
                        "own": e["id"] in mine, "kills": total,
                        "pct": pct, "odds": odds,
                        "had": _pct(1 - miss) if total else "",
                        "mobs": mobs})
    # what is still to farm first, the most advanced of those first
    out.sort(key=lambda m: (m["own"], -m["kills"]))
    return out


_ITEM_TYPES = None


def item_type(kind):
    global _ITEM_TYPES
    if _ITEM_TYPES is None:
        try:
            _ITEM_TYPES = json.loads(
                (ANALYSIS / "item_types.json").read_text(encoding="utf-8"))
        except Exception:
            _ITEM_TYPES = {}
    return _ITEM_TYPES.get(kind) or ""


# Equipment that is gear (shown on the profile), by item type; the rest of
# the equipment container is tools, bags, the mount and glider, consumables.
ARMOUR_SLOTS = ("Head", "Shoulders", "Chest", "Back", "Hands", "Waist",
                "Legs", "Feet")
NOT_GEAR = {"GearPickaxe", "GearSickle", "Bag", "Misc", "Mount",
            "GearGlider", "Consumable", "Usable", "HealthPotion", "Quest",
            "Currency"}
CLASS_FR = {"Warrior": "Guerrier", "Mage": "Mage", "Priest": "Prêtre",
            "Rogue": "Voleur"}


_AUGMENTS = None

# augment type -> the style of its line on the profile
AUGMENT_KIND = {"AugmentDemon": "gift", "AugmentDemonSigil": "sigil",
                "AugmentJeweller": "gem"}


def _augments_data():
    global _AUGMENTS
    if _AUGMENTS is None:
        try:
            _AUGMENTS = json.loads(
                (ANALYSIS / "augments.json").read_text(encoding="utf-8"))
        except Exception:
            _AUGMENTS = {}
    return _AUGMENTS


def _scaled(val, factor):
    """A stat on a piece worn in a weakening slot, as the game's tooltip
    shows it ($HText.makeAfxDescTextsComparisons): ceil(val * factor)."""
    return val if factor == 1 else math.ceil(val * factor)


def slot_factor(slot):
    """The share of its stats a piece keeps in an equipment slot (EQUIP_SLOTS
    name): 0.4 for the arsenal's weapon, 1 elsewhere."""
    return ((gear_stats_data().get("slotFactors") or {})
            .get(f"Slot_{slot}") or 1)


def _augment_view(aid, factor=1):
    """One augment set into a gear: its name and what it does — the attribute
    bonuses and maluses, or the skill it grants (a formula's enchantment, a
    sigil's talent). Corrupted gifts all share one name, so the effect is
    what tells them apart."""
    global _AUGMENTS
    if _AUGMENTS is None:
        try:
            _AUGMENTS = json.loads(
                (ANALYSIS / "augments.json").read_text(encoding="utf-8"))
        except Exception:
            _AUGMENTS = {}
    a = _AUGMENTS.get(aid) or {}
    t = a.get("t") or ""
    fx = []
    for atb, val in a.get("a") or ():
        name = _fr_names("attribute").get(atb) or _pretty_id(atb)
        val = _scaled(val, factor)
        sign = "+" if val > 0 else "\u2212"
        fx.append(f"{name} {sign}{abs(val):g}")
    name = item_label(aid)
    # a formula is already named after its enchantment ("Formule magique :
    # Dévot" gives "Dévot"): no need to say it twice
    fx += [sk for sk in (_skill_label(s) for s in a.get("s") or ())
           if sk not in name]
    return {"k": AUGMENT_KIND.get(t, "enchant" if "Enchant" in t else "aug"),
            "name": name, "fx": " · ".join(fx)}


def _skill_label(sid):
    return _fr_names("skill").get(sid) or _pretty_id(sid)


_TALENTS = None


def talent_data():
    """{"trees": {class: {root, talents: [{s, tier, branch, max}]}},
    "runes": {rune: skill}} from analysis_out/talents.json
    (hltools/skills_data.py)."""
    global _TALENTS
    if _TALENTS is None:
        try:
            _TALENTS = json.loads(
                (ANALYSIS / "talents.json").read_text(encoding="utf-8"))
        except Exception:
            _TALENTS = {}
    return _TALENTS


def _talent_tree(cls, ranks, granted=()):
    """A class's talent tree laid out as the game draws it: the root, then
    per tier (1-4) the three branches, each talent with its points. A talent
    the hero has without having put points in it is one its gear gives."""
    tree = (talent_data().get("trees") or {}).get(cls)
    if not tree:
        return None
    granted = set(granted)
    def cell(t):
        pts = int(ranks.get(t["s"]) or 0)
        gift = not pts and t["s"] in granted
        return {"id": t["s"], "name": _skill_label(t["s"])
                + (" (offert par l'équipement)" if gift else ""),
                "pts": t["max"] if gift else pts, "max": t["max"],
                "gift": gift}
    root = next((t for t in tree["talents"] if t["tier"] == 0), None)
    tiers = []
    for tier in (1, 2, 3, 4):
        tiers.append([[cell(t) for t in tree["talents"]
                       if t["tier"] == tier and t["branch"] == b]
                      for b in ("Left", "Center", "Right")])
    spent = sum(int(v or 0) for v in ranks.values())
    # the tree's own talents only; the root's point counts like any other
    return {"root": cell(root) if root else None, "tiers": tiers,
            "cost": ["", "1", "3", "7"], "spent": spent}


def _spell_bar(prof):
    """The action bar as the game shows it: the four weapon skill slots
    (1-4), the next prayer, then the four class skills (A E R G)."""
    if not prof.get("weaponSkills") and not prof.get("slots"):
        return []
    def sk(sid, key):
        return {"id": sid, "name": _skill_label(sid) if sid else "",
                "key": key, "empty": not sid}
    weap = list(prof.get("weaponSkills") or [])[:4]
    weap += [None] * (4 - len(weap))
    out = [sk(s, str(i + 1)) for i, s in enumerate(weap)]
    prayers = prof.get("prayers") or []
    if prayers:
        out.append(dict(sk(prayers[0], "Prière"), sep=True,
                        seq=[_skill_label(p) for p in prayers]))
    for s, key in zip(list(prof.get("slots") or [])[:4],
                      ("A", "E", "R", "G")):
        out.append(dict(sk(s, key), sep=key == "A" and not prayers))
    return out


def _runes_view(runes):
    """The runes, each under the skill it sits on (rune ids carry it)."""
    owner = talent_data().get("runes") or {}
    by_skill = {}
    for r in runes or ():
        sid = owner.get(r) or r.rsplit("_M", 1)[0]
        by_skill.setdefault(sid, []).append({"id": r, "name": _skill_label(r)})
    return [{"id": sid, "name": _skill_label(sid), "runes": rs}
            for sid, rs in by_skill.items()]


# The equipment container's cells, in order: data.cdb's Slot_* lines
# (itemType, after the item types). Weapon2 is the arsenal's weapon.
EQUIP_SLOTS = ("Weapon1", "Weapon2", "OffhandWeapon", "Head", "Neck",
               "Shoulders", "Chest", "Back", "Hands", "Waist", "Legs", "Feet",
               "FingerLeft", "Trinket", "FingerRight")
# what can sit in each, by item type (weapons: anything else that is gear)
SLOT_TYPES = {"Head": {"Head"}, "Neck": {"GearNeck"},
              "Shoulders": {"Shoulders"}, "Chest": {"Chest"},
              "Back": {"Back"}, "Hands": {"Hands"}, "Waist": {"Waist"},
              "Legs": {"Legs"}, "Feet": {"Feet"},
              "FingerLeft": {"GearFinger"}, "FingerRight": {"GearFinger"},
              "Trinket": {"GearTrinket"}}
# the character sheet as the game lays it out: two columns around the hero
SHEET_LEFT = (("Head", "Tête"), ("Neck", "Cou"), ("Shoulders", "Épaules"),
              ("Chest", "Torse"), ("Back", "Dos"), ("FingerLeft", "Anneau"))
SHEET_RIGHT = (("Hands", "Mains"), ("Waist", "Taille"), ("Legs", "Jambes"),
               ("Feet", "Pieds"), ("Trinket", "Babiole"),
               ("FingerRight", "Anneau"))
SLOT_ICON = {"FingerLeft": "Finger", "FingerRight": "Finger"}
_SLOT_WARNED = set()


def _equip_by_slot(entries):
    """[(cell index, gear entry)] -> {slot: entry}. The cell says the slot;
    an item that can't sit there (the order changed in a patch) is placed by
    its type instead, and said once in the log."""
    out, loose = {}, []
    for i, g in entries:
        slot = EQUIP_SLOTS[i] if i < len(EQUIP_SLOTS) else None
        ok = slot is not None and (
            g["t"] in SLOT_TYPES[slot] if slot in SLOT_TYPES
            else not any(g["t"] in v for v in SLOT_TYPES.values()))
        if ok and slot not in out:
            out[slot] = g
        else:
            loose.append(g)
    for g in loose:
        key = (g["t"], g["id"])
        if key not in _SLOT_WARNED:
            _SLOT_WARNED.add(key)
            print(f"[meter] equipment: {g['id']} ({g['t']}) not in its "
                  "expected cell — placed by type", file=sys.stderr)
        for slot in EQUIP_SLOTS:
            fits = (g["t"] in SLOT_TYPES[slot] if slot in SLOT_TYPES
                    else not any(g["t"] in v for v in SLOT_TYPES.values()))
            if fits and slot not in out:
                out[slot] = g
                break
    return out


def _weapon_skills(prof, kind, t):
    """The skills chosen for a weapon (Specialization.arsenals, keyed by the
    weapon's item kind or its type)."""
    ars = prof.get("arsenals") or {}
    got = ars.get(kind) or ars.get(t) or []
    return [{"id": s, "name": _skill_label(s)} for s in got if s]


def _sheet(prof, entries):
    """The character sheet: the gear around the hero, the weapons with
    their skills."""
    by = _equip_by_slot(entries)
    def cell(slot, label):
        g = by.get(slot)
        return {"slot": slot, "label": label,
                "icon": SLOT_ICON.get(slot, slot), "g": g}
    def weapon(slot, label):
        g = by.get(slot)
        return {"label": label, "g": g,
                "skills": _weapon_skills(prof, g["id"], g["t"]) if g else []}
    return {"left": [cell(*s) for s in SHEET_LEFT],
            "right": [cell(*s) for s in SHEET_RIGHT],
            "weapons": [weapon("Weapon1", "Main principale"),
                        weapon("OffhandWeapon", "Main secondaire")],
            "arsenal": weapon("Weapon2", "Arme de rechange")}


def character_view(roster, profiles, sel, waiting, live):
    """The Character tab: the players around (to analyse), the profiles
    already built, and the open one."""
    near = []
    for r in sorted(roster, key=lambda r: (not r.get("me"),
                                           -(r.get("lvl") or 0),
                                           r.get("n") or "")):
        near.append({"n": r.get("n"), "lvl": r.get("lvl"),
                     "cls": CLASS_FR.get(r.get("k"), r.get("k") or ""),
                     "ck": class_key(r.get("k")), "me": bool(r.get("me")),
                     "saved": r.get("n") in profiles,
                     "busy": r.get("n") == waiting})
    saved = [{"n": n, "lvl": p.get("lvl"),
              "cls": CLASS_FR.get(p.get("k"), p.get("k") or ""),
              "ck": class_key(p.get("k")),
              "when": date_fr(time.localtime(p.get("at") or 0))}
             for n, p in sorted(profiles.items(),
                                key=lambda kv: -(kv[1].get("at") or 0))]
    view = {"near": near, "count": len(near), "saved": saved, "live": live,
            "open": None}
    prof = profiles.get(sel) if sel else None
    if prof:
        gear, other, cells = [], [], []
        for idx, slot in enumerate(prof.get("equip") or ()):
            if not slot:
                continue
            kind, rar, lvl, upg, gslots, effects, infu, istat, iflags = (
                list(slot) + [None] * 9)[:9]
            prism = _item_flag(iflags, "Prismatic")
            rar = rar or item_rarity(kind) or ""
            t = item_type(kind)
            cell = EQUIP_SLOTS[idx] if idx < len(EQUIP_SLOTS) else None
            fac = slot_factor(cell) if cell else 1
            extras = [_augment_view(g, fac) for g in gslots or ()
                      if g and not str(g).startswith("[")]
            for e in effects or ():
                if e and not str(e).startswith("["):
                    extras.append({"k": "enchant", "name": "Enchantement",
                                   "fx": _skill_label(e)})
            entry = {"id": kind, "name": item_label(kind),
                     "img": item_icon(kind), "rk": rar.lower(),
                     "rar": rarity_label(rar) if rar else "",
                     "type": item_type_label(t) if t else "",
                     "lvl": lvl if isinstance(lvl, int) and lvl > 0 else None,
                     "up": upg if isinstance(upg, int) and upg > 0 else 0,
                     "extras": extras,
                     "prism": prism,
                     "inf": _gear_infusion(kind, infu, istat, prism),
                     "t": t}
            st = gear_stats(kind, rar, lvl, upg, gslots, iflags)
            inf = entry["inf"]
            if st and inf and istat:
                bonus = infusion_bonus(kind, rar, st[0], istat)
                if bonus:
                    inf["val"] = _scaled(bonus, fac)
            if st:
                entry["il"] = st[0]
                entry["stats"] = [{"k": k, "t": n, "v": _scaled(v, fac)}
                                  for k, n, v in st[1]]
            entry["augs"] = [[(atb, _scaled(val, fac)) for atb, val in
                              (_augments_data().get(g) or {}).get("a") or ()]
                             for g in gslots or ()
                             if g and not str(g).startswith("[")]
            if fac != 1:
                entry["eff"] = round(fac * 100)
            (other if t in NOT_GEAR else gear).append(entry)
            if t not in NOT_GEAR:
                cells.append((idx, entry))
        view["open"] = {
            "n": prof.get("n"), "lvl": prof.get("lvl"),
            "cls": CLASS_FR.get(prof.get("k"), prof.get("k") or ""),
            "ck": class_key(prof.get("k")), "me": bool(prof.get("me")),
            "when": date_fr(time.localtime(prof.get("at") or 0)),
            "gear": gear, "other": other, "sheet": _sheet(prof, cells),
            "atbs": _hero_sheet(prof, gear),
            "tree": _talent_tree(
                prof.get("k"),
                prof["talents"] if isinstance(prof.get("talents"), dict)
                else {t: 1 for t in prof.get("talents") or ()},
                prof.get("skills") or ()),
            "ranked": isinstance(prof.get("talents"), dict),
            "slots": [{"id": t, "name": _skill_label(t)}
                      for t in prof.get("slots") or ()],
            "runes": _runes_view(prof.get("masteries")),
            "bar": _spell_bar(prof),
            "passives": [{"id": t, "name": _skill_label(t)}
                         for t in dict.fromkeys(prof.get("skills") or ())
                         if t and (t.endswith("_Passive")
                                   or t.endswith("_P"))],
            "raw": {k: prof.get(k) for k in ("arsenals", "prayers",
                                              "secondary")},
            "infusions": _infusion_sets(gear)}
    return view


# The loot luck counters (analysis_out/luck.json), in display order.
LUCK_LABELS = (("Luck_Mount", "Monture"), ("Luck_Glider", "Planeur"),
               ("Luck_LegendaryWeapon", "Arme légendaire"),
               ("Luck_RareMaterial", "Matériau rare"),
               ("Luck_PrismaticGear", "Équipement prismatique"))
# Progress.counters shown as statistics (the rest are internal flags).
STAT_LABELS = (("Rift_NbCompleted", "Failles terminées"),
               ("Rift_NbGatesClosed", "Portails de faille fermés"),
               ("Rift_NbGatesClosed_InOneRift",
                "Record de portails fermés en une faille"),
               ("Gold_TotalEarned", "Or gagné"),
               ("Gold_TotalEarned_FromActivity", "Or gagné en activités"),
               ("Scrap_NbItemsScrapped", "Objets recyclés"),
               ("CraftPoint_TotalEarned", "Points d'artisanat gagnés"),
               ("CraftPoint_TotalSpent", "Points d'artisanat dépensés"),
               ("Jobs_NbLearnt", "Métiers appris"))
_LUCK = None


def luck_data():
    global _LUCK
    if _LUCK is None:
        try:
            _LUCK = json.loads((ANALYSIS / "luck.json").read_text(
                encoding="utf-8"))
        except Exception:
            _LUCK = {}
    return _LUCK


def _pct2(v):
    return f"{v * 100:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " %"


def _profile_luck(prof):
    """Each loot luck counter: the count the game keeps, the bonus it gives
    (base + count * increment, capped), how many more steps to the cap, and
    whether the Soulwell status that carries it is on (time left). None when
    the counters are not readable (another player: not replicated)."""
    counters = prof.get("counters")
    if not isinstance(counters, dict):
        return None
    now = prof.get("now")
    active = {}
    for k, start, dur, stop in prof.get("luckStatuses") or ():
        end = stop if stop and stop > 0 else (
            start + dur if start is not None and dur and dur > 0 else None)
        left = end - now if end is not None and isinstance(
            now, (int, float)) else None
        active[k] = left if left is None or left > 0 else 0
    out = []
    for cid, label in LUCK_LABELS:
        p = luck_data().get(cid)
        if not p:
            continue
        n = counters.get(cid) or 0
        n = n if isinstance(n, (int, float)) else 0
        base, inc, cap = p.get("base") or 0, p.get("increment") or 0,             p.get("max") or 0
        bonus = min(cap, base + n * inc) if cap else base + n * inc
        steps = (max(0, math.ceil(round((cap - base) / inc, 6)) - int(n))
                 if inc else 0)
        st = p.get("status")
        out.append({"t": label, "n": int(n), "bonus": _pct2(bonus),
                    "cap": _pct2(cap), "full": bonus >= cap,
                    "grows": bool(inc), "inc": _pct2(inc),
                    "steps": steps,
                    "on": st in active,
                    "left": (round(active[st] / 60)
                             if st in active and active[st] is not None
                             else None)})
    return out


def _profile_stats(prof):
    counters = prof.get("counters")
    if not isinstance(counters, dict):
        return None
    return [{"t": label, "v": counters[k]} for k, label in STAT_LABELS
            if isinstance(counters.get(k), (int, float))]


_ACHIEVEMENTS = None


def achievements_catalogue():
    """{"categories": [{id, parent}], "achievements": [{id, cat, parent,
    points, obj, reward, copyDesc, consts}]} from analysis_out/
    achievements.json (hltools/achievements_data.py)."""
    global _ACHIEVEMENTS
    if _ACHIEVEMENTS is None:
        try:
            _ACHIEVEMENTS = json.loads((ANALYSIS / "achievements.json")
                                       .read_text(encoding="utf-8"))
        except Exception:
            _ACHIEVEMENTS = {}
    return _ACHIEVEMENTS


# Collect objectives by item type ([9, type]) / unit type ([4, type]): the
# collection list that counts them.
COLLECT_LISTS = {"Mount": "mounts", "GearGlider": "gliders", "Gear": "gears",
                 "Critter": "pets"}
# ElementCompleted's element kinds -> the map's point category
ELEMENT_CATS = {"LEVEL_Obelisk": "obelisk", "LEVEL_WorldChest": "chest",
                "RedOrb_World": "orb"}


def _ach_progress(a, done, counters, owned, states):
    """[have, need] for an achievement's first objective the app can
    measure, else None."""
    owned_sets = {k: set(owned.get(k) or ()) for k in COLLECT_LISTS.values()}
    for o in a.get("obj") or ():
        ref, v, ts = o.get("ref"), o.get("v"), o.get("t") or []
        if ref == "CounterValue" and ts and isinstance(v, (int, float)):
            name = ts[0][1] if isinstance(ts[0], list) else None
            have = counters.get(name)
            if isinstance(have, (int, float)):
                return [have, v]
        elif ref == "AchievementCompleted" and ts:
            ids = [t[1] for t in ts if isinstance(t, list)
                       and isinstance(t[1], str)]
            return [sum(1 for i in ids if i in done), len(ids)]
        elif ref == "Collect" and ts:
            t0 = ts[0]
            if isinstance(t0, list) and t0[0] in (4, 9):
                lst = COLLECT_LISTS.get(t0[1])
                if lst and isinstance(v, (int, float)):
                    return [len(owned_sets[lst]), v]
            else:
                ids = [t[1] for t in ts if isinstance(t, list)
                       and isinstance(t[1], str)]
                mine = set().union(*owned_sets.values())
                return [sum(1 for i in ids if i in mine), len(ids)]
        elif ref == "ElementCompleted" and ts:
            t0 = ts[0]
            if isinstance(t0, list) and t0[0] == 13:
                kind = t0[1][1] if isinstance(t0[1], list) else None
                region = t0[2][1] if len(t0) > 2 and isinstance(
                    t0[2], list) else None
                cat = ELEMENT_CATS.get(kind)
                pts = [p["id"] for p in world_map().get("points") or ()
                       if p.get("c") == cat and p.get("region") == region]
                if pts:
                    return [sum(1 for i in pts if _element_done(states, i)),
                            len(pts)]
            else:
                ids = [t[1] for t in ts if isinstance(t, list)
                       and isinstance(t[1], str)]
                return [sum(1 for i in ids if _element_done(states, i)),
                        len(ids)]
    return None


def _ach_target_label(t):
    """An objective target ([sheet ref, id]) in French: an activity (a
    dungeon), a job, a faction, an item, a unit."""
    kind, rid = t[0], t[1]
    sheets = {1: ("activity",), 7: ("job",), 5: ("faction", "unitType"),
              3: ("item",), 4: ("unitType",), 12: ("unit",)}.get(
        kind, ("activity", "job", "item", "unit", "unitType", "zone"))
    for sh in sheets:
        nm = _fr_names(sh).get(rid)
        if nm:
            return nm
    return _pretty_id(rid)


def _ach_text(a, by_id, tier_v):
    """An achievement's French name and description: a tier has neither of
    its own, they are its first ancestor's, with the tier's target value."""
    chain, cur = [], a
    while cur and cur["id"] not in [c["id"] for c in chain]:
        chain.append(cur)
        cur = by_id.get(cur.get("parent"))
    name = next((_fr_names("ach").get(c["id"]) for c in chain
                 if _fr_names("ach").get(c["id"])), None)
    desc = next((_fr_desc("ach").get(c["id"]) for c in chain
                 if _fr_desc("ach").get(c["id"])), "")
    if not desc and a.get("copyDesc"):
        desc = _fr_desc("ach").get(a["copyDesc"]) or ""
    v = tier_v
    tgt = next((t for o in a.get("obj") or () for t in o.get("t") or ()
                if isinstance(t, list) and len(t) > 1
                and isinstance(t[1], str)), None)
    tname = _ach_target_label(tgt) if tgt else "…"
    desc = desc.replace("::target::", tname)
    if name:
        name = name.replace("::target::", tname)
    num = (lambda x: f"{x:,.0f}".replace(",", "\u202f")
           if isinstance(x, (int, float)) else "…")
    desc = desc.replace("::targetValue::", num(v))
    for k, cv in (a.get("consts") or {}).items():
        desc = desc.replace(f"::{k}::", num(cv) if isinstance(
            cv, (int, float)) else str(cv))
    if name and "::targetValue::" in name:
        name = name.replace("::targetValue::", num(v))
    elif not _fr_names("ach").get(a["id"]) and name and isinstance(
            v, (int, float)) and len(chain) > 1:
        name = f"{name} ({num(v)})"
    return _fr_ref(name or _pretty_id(a["id"])), _fr_ref(desc)


def achievements_view(account, counters, owned, states):
    """The Succès tab: categories with points and counts, and every
    achievement chain (an achievement and its tiers) with its current tier,
    progress where the app can measure it, reward and completion date."""
    cat = achievements_catalogue()
    achs = cat.get("achievements") or []
    by_id = {a["id"]: a for a in achs}
    done = set(account)
    children = {}
    for a in achs:
        if a.get("parent") in by_id:
            children.setdefault(a["parent"], []).append(a)
    cats = {c["id"]: c for c in cat.get("categories") or ()}

    def top(cid):
        seen = set()
        while cid in cats and cats[cid].get("parent") and cid not in seen:
            seen.add(cid)
            cid = cats[cid]["parent"]
        return cid

    items, totals = [], {}
    for root in achs:
        if root.get("parent") in by_id:
            continue
        tiers, cur = [], root
        while cur and len(tiers) < 20:
            tiers.append(cur)
            nxt = children.get(cur["id"]) or []
            cur = nxt[0] if nxt else None
        cur = next((t for t in tiers if t["id"] not in done), None)
        show = cur or tiers[-1]
        v = next((o.get("v") for o in show.get("obj") or ()
                  if isinstance(o.get("v"), (int, float))), None)
        name, desc = _ach_text(show, by_id, v)
        prog = None if cur is None else _ach_progress(
            cur, done, counters, owned, states)
        last = max((account.get(t["id"]) or 0 for t in tiers), default=0)
        c = show.get("cat") or ""
        tc = top(c) or c
        rewards = [{"id": r, "name": item_label(r), "img": item_icon(r)}
                   for r in (show.get("reward") or ())]
        pts_done = sum(t.get("points") or 0 for t in tiers
                       if t["id"] in done)
        pts_all = sum(t.get("points") or 0 for t in tiers)
        tt = totals.setdefault(tc, {"n": 0, "got": 0, "pts": 0,
                                    "ptsAll": 0})
        tt["n"] += len(tiers)
        tt["got"] += sum(1 for t in tiers if t["id"] in done)
        tt["pts"] += pts_done
        tt["ptsAll"] += pts_all
        items.append({
            "id": root["id"], "c": tc, "sub": c if c != tc else "",
            "name": name, "desc": desc,
            "done": cur is None,
            "tiers": [{"ok": t["id"] in done, "p": t.get("points") or 0}
                      for t in tiers],
            "have": min(prog[0], prog[1]) if prog else None,
            "need": prog[1] if prog else None,
            "pct": (min(1.0, prog[0] / prog[1]) if prog and prog[1]
                    else None),
            "pts": show.get("points") or 0,
            "rewards": rewards,
            "when": date_fr(time.localtime(last / 1000)) if cur is None
            and last else ""})
    order = [c["id"] for c in cat.get("categories") or ()
             if not c.get("parent")]
    out_cats = [{"v": c, "t": _fr_ref(_fr_names("ach").get(c) or c),
                 "img": "achcat_" + c, **totals[c]}
                for c in order if c in totals]
    subs = {c["id"]: _fr_ref(_fr_names("ach").get(c["id"]) or c["id"])
            for c in cat.get("categories") or () if c.get("parent")}
    for it in items:
        it["subT"] = subs.get(it["sub"], "")
    return {"cats": out_cats, "items": items,
            "pts": sum(c["pts"] for c in out_cats),
            "ptsAll": sum(c["ptsAll"] for c in out_cats),
            "got": sum(c["got"] for c in out_cats),
            "n": sum(c["n"] for c in out_cats)}


_RIFT_REWARDS = None


def rift_rewards_data():
    """analysis_out/rift_rewards.json (emit_offsets.extract_rift_rewards)."""
    global _RIFT_REWARDS
    if _RIFT_REWARDS is None:
        try:
            _RIFT_REWARDS = json.loads((ANALYSIS / "rift_rewards.json")
                                       .read_text(encoding="utf-8"))
        except Exception:
            _RIFT_REWARDS = {}
    return _RIFT_REWARDS


RARITY_ORDER_IDS = ("Common", "Uncommon", "Rare", "Epic", "Legendary")


def weapon_rarity_odds(level, luck_bonus=0.0):
    """A dropped weapon's rarity, at least Rare, as ent.Hero.makeLootItem
    draws it: the rarity sheet's generationChance at the player's level;
    the Legendary share first (plus the luck bonus, when the offering is
    on), then Epic / Rare by weight. {rarity: probability}."""
    rr = rift_rewards_data().get("rarities") or {}
    w = {}
    for rid in RARITY_ORDER_IDS[2:]:
        for g in rr.get(rid) or ():
            if g.get("minLevel", 0) <= level <= g.get("maxLevel", 10 ** 6):
                w[rid] = g.get("chance") or 0
    total = sum(w.values())
    if not total:
        return {}
    leg = min(1.0, w.get("Legendary", 0) / total
              + (luck_bonus if w.get("Legendary") else 0))
    rest = total - w.get("Legendary", 0)
    out = {"Legendary": leg}
    for rid in ("Epic", "Rare"):
        out[rid] = (1 - leg) * (w.get(rid, 0) / rest if rest else 0)
    return out


def _luck_bonus(counter_id, counters):
    p = luck_data().get(counter_id) or {}
    n = counters.get(counter_id) or 0
    n = n if isinstance(n, (int, float)) else 0
    return min(p.get("max") or 0, (p.get("base") or 0)
               + n * (p.get("increment") or 0)), int(n)


def rift_rewards_view(counters, luck_until):
    """The rift rewards page: what each gate tier unlocks, the chests'
    contents with their chances, and the weapon's rarity odds at the
    player's level, with and without the Soulwell offering."""
    d = rift_rewards_data()
    if not d:
        return [{"k": "note", "t": "Données des failles absentes : relance "
                                   "Farever France avec le jeu ouvert pour les "
                                   "générer."}]
    level = counters.get("HeroLevel") if isinstance(
        counters.get("HeroLevel"), (int, float)) else 25
    now = time.time()
    on = {k for k, t in (luck_until or {}).items() if t > now}
    left = {k: max(0, round((t - now) / 60)) for k, t in
            (luck_until or {}).items() if t > now}

    def names(ls):
        return ", ".join(item_label(ln["item"]) for ln in ls if ln.get("item"))

    # what each tier does, in the code's order (tier 3 adds Rift_Tier4,
    # tier 5 adds Rift_Tier6 — the data's own comment says Tier5)
    tier_txt = {0: "Ouvre le coffre du boss : sans ça, aucune de ses "
                   "récompenses (armes, montures…)",
                3: f"Ajoute au coffre du boss : {names(d.get('tier4') or [])} "
                   "(une des deux, garantie)",
                5: f"Ajoute au coffre du boss : {names(d.get('tier6') or [])} "
                   "(garanti)"}
    rows = [{"t": f"{int(t['gates'])} portails fermés",
             "meta": tier_txt.get(i, "Un coffre bonus de plus")}
            for i, t in enumerate(d.get("tiers") or ())]

    leg_bonus, leg_n = _luck_bonus("Luck_LegendaryWeapon", counters)
    base = weapon_rarity_odds(level)
    lucky = weapon_rarity_odds(level, leg_bonus)
    leg_on = "Luck_LegendaryWeapon_Status" in on
    cards = [
        {"title": "Arme légendaire", "value": _pct(base.get("Legendary", 0)),
         "sub": f"par arme, sans offrande (niv. {int(level)})"},
        {"title": "Avec l'offrande", "value": _pct(lucky.get("Legendary", 0)),
         "sub": (f"active · {left.get('Luck_LegendaryWeapon_Status', 0)} min"
                 if leg_on else "si tu en fais une")
                + f" · compteur {leg_n}",
         "tone": "rift" if leg_on else ""},
        {"title": "Arme épique",
         "value": _pct((lucky if leg_on else base).get("Epic", 0)),
         "sub": "sinon rare"}]

    def luck_note(item):
        """A mount's / glider's own luck counter, when its offering is on."""
        t = item_type(item)
        cid = {"Mount": "Luck_Mount", "GearGlider": "Luck_Glider"}.get(t)
        if not cid:
            return 0.0
        status = (luck_data().get(cid) or {}).get("status")
        return _luck_bonus(cid, counters)[0] if status in on else 0.0

    def row(item, src, chance=None, qty="", note=""):
        rar = item_rarity(item) or ""
        return {"img": item_icon(item), "name": item_label(item),
                "rk": rar.lower(),
                "type": item_type_label(item_type(item))
                if item_type(item) else "",
                "apt": [], "src": src,
                "chance": (_pct(chance) if chance is not None and chance < 1
                           else "garanti") + note,
                "qty": qty, "got": 0}

    def chest_rows(ls, src):
        out = []
        for ln in ls:
            qty = (f"{ln['itemMin']}" if ln.get("itemMin") else "")
            if ln.get("lootTable") == "Soulstone":
                out.append({"img": item_icon("Soulstone_Z1_1"),
                            "name": "Une pierre d'âme", "rk": "rare",
                            "type": "Pierre d'âme", "apt": [], "src": src,
                            "chance": "garanti",
                            "qty": f"1 parmi {len(d.get('soulstone') or [])}",
                            "got": 0})
            elif ln.get("item"):
                p = ln.get("proba") or 0
                bonus = luck_note(ln["item"]) if p < 1 else 0
                out.append(row(ln["item"], src, min(1.0, p + bonus), qty,
                               " (offrande)" if bonus else ""))
        return out

    boss_rows = []
    for b in d.get("bosses") or ():
        who = _unit_label(b["id"])
        ws = [w for w in b.get("weapons") or () if w.get("item")]
        for w in ws:
            boss_rows.append(row(w["item"], f"Coffre du boss · {who}",
                                 1 / len(ws) if ws else None))
        boss_rows += chest_rows(b.get("extra") or [],
                                f"Coffre du boss · {who}")
    boss_rows += chest_rows(d.get("bossChest") or [], "Coffre du boss")
    t4 = [ln for ln in d.get("tier4") or () if ln.get("item")]
    boss_rows += [row(ln["item"], "Coffre du boss · 10 portails",
                      1 / len(t4)) for ln in t4]
    boss_rows += [row(ln["item"], "Coffre du boss · 15 portails")
                  for ln in d.get("tier6") or () if ln.get("item")]
    return [
        {"k": "section", "t": "Récompenses des failles"},
        {"k": "note", "t": "D'après le code et les données du jeu. Chaque "
                           "joueur reçoit sa propre part de chaque coffre. Le "
                           "coffre du boss s'ouvre une fois 3 portails "
                           "fermés ; chaque palier suivant ajoute un coffre "
                           "bonus ou une récompense garantie."},
        {"k": "list", "id": "rift_tiers", "rows": rows},
        {"k": "section", "t": "Rareté de l'arme du boss"},
        {"k": "note", "t": "Chaque joueur reçoit une des deux armes du boss "
                           "de la faille. Sa rareté est tirée à ton niveau, "
                           "rare au minimum : la légendaire d'abord, puis "
                           "épique ou rare. Pendant l'offrande d'arme "
                           "légendaire du Puits des âmes, ton compteur "
                           "s'ajoute à la chance : +1 %, puis +0,5 % par "
                           "arme non légendaire (jusqu'à +25 %), et il "
                           "revient à 0 quand une légendaire tombe."},
        {"k": "cards", "id": "rift_weapon_odds", "items": cards},
        {"k": "section", "t": "Coffre du boss"},
        {"k": "droptable", "id": "rift_boss_chest", "rows": boss_rows},
        {"k": "section", "t": "Coffre bonus (5, 9 et 14 portails)"},
        {"k": "droptable", "id": "rift_bonus_chest",
         "rows": chest_rows(d.get("bonusChest") or [], "Coffre bonus")},
    ]


_INFUSIONS = None


def infusion_data():
    """{"infusions": {skill: {f, role, name, pattern, t2, t4, t6}},
    "item_faction": {item: faction}} from analysis_out/infusions.json
    (hltools/infusions_data.py)."""
    global _INFUSIONS
    if _INFUSIONS is None:
        try:
            _INFUSIONS = json.loads(
                (ANALYSIS / "infusions.json").read_text(encoding="utf-8"))
        except Exception:
            _INFUSIONS = {}
    return _INFUSIONS


def _infusion_id(raw):
    """The infusion skill a gear's `infusion` field names (the skill, or
    the pattern that teaches it)."""
    infs = infusion_data().get("infusions") or {}
    if not raw:
        return None
    if raw in infs:
        return raw
    for sid, e in infs.items():
        if e.get("pattern") == raw or sid == "Infusion_" + raw:
            return sid
    return raw


_OFFSETS = None


def _offsets():
    """analysis_out/meter_offsets.json, read once."""
    global _OFFSETS
    if _OFFSETS is None:
        try:
            _OFFSETS = json.loads(
                (ANALYSIS / "meter_offsets.json").read_text(encoding="utf-8"))
        except Exception:
            _OFFSETS = {}
    return _OFFSETS


def faction_label(f):
    """A faction's French name (the faction sheet: Apix, Nepsides, Béliers
    écarlates...), else its monster family's."""
    return (_fr_names("faction").get(f) or _fr_names("unitType").get(f)
            or _pretty_id(f or ""))


def _item_flag(bits, name):
    """Whether an item copy carries one st.ItemFlag (bit index from
    meter_offsets.json's ItemFlag, read off the bytecode)."""
    idx = (_offsets().get("ItemFlag") or {}).get(name)
    return isinstance(bits, int) and idx is not None and bool(
        (bits >> idx) & 1)


# ---- gear stats: the game's own computation (hltools/gear_stats_data.py) --
_GEAR_STATS = None
STAT_GROUP_KEYS = ("primary", "vitality", "armor", "ratings")
# shown first, in the character sheet's order; the ratings after
GEAR_STAT_ORDER = ("Armor", "Vitality", "Strength", "Dexterity", "Faith",
                   "Intellect")


def gear_stats_data():
    global _GEAR_STATS
    if _GEAR_STATS is None:
        try:
            _GEAR_STATS = json.loads((ANALYSIS / "gear_stats.json").read_text(
                encoding="utf-8"))
        except Exception:
            _GEAR_STATS = {}
    return _GEAR_STATS


def _hx_round(v):
    """Haxe's Math.round: half up, also for negatives."""
    return math.floor(v + 0.5)


def _atb_level_scaling(d, index, level, start, end, reduction=-1.0):
    """$HAttributes.getAtbLevelScaling."""
    c = d["consts"]
    if index in (23, 24):                       # Armor, MagicArmor
        if index == 24 or reduction < 0:
            return 0.0
        a, b = c["resist"][0], c["resist"][1]
        return (-a * reduction - b * level * reduction) / (reduction - 1)
    if start < 1e-10 or end == 0:
        return 0.0
    step = (end / start) ** (1.0 / (c["earlyMax"] - 1))
    return start * step ** (level - 1)


def _item_ilevel(d, it, rarity, glevel):
    """$HItem.getILevel: the definition's iLevel, else its required level
    * 10 + the rarity's bonus. The copy's level (Gear.level) and rarity
    (Weapon.rarity) are what count: measured 2026-10-01 on Amon Arès
    (Mace_Benediction, Rare level 20 in the data; the copy Legendary level
    25, 5 upgrades, a corrupted gift set) — iLevel 250 + 70 + 50 + 10 = 380
    gives the tooltip's Vitalité 75, Force 29, Foi 29, Ferveur 110."""
    # A fixed iLevel holds for a copy at the definition's level only: scaled
    # loot is defined at level 1 / iLevel 1 and takes the level it dropped
    # at (Cape déchirâme, Back_RDemon_Cle, a level 25 copy: iLevel 260 gives
    # the tooltip's Armure 50, Vitalité 6, Foi 6, Perforation magique 16).
    if it.get("il") is not None and (not glevel or glevel == it.get("lvl")):
        return int(it["il"])
    lvl = glevel or it.get("lvl") or 1
    return int(lvl) * 10 + int((d["rarities"].get(rarity) or {}).get("il")
                               or 0)


def _compute_atb_scaling(d, it, rarity, ilevel, group, armor_red,
                         divide=True):
    """$HItem.computeAtbScaling(def, iLevel, group, divide, armorRed)."""
    c = d["consts"]
    level = ilevel * 0.1
    first = group[0]
    ratio = _atb_level_scaling(d, 0, level, c["bounds"][0], c["bounds"][1])
    key = first["src"] or first["end"]
    start = sum(x["s"] for x in group) / len(group)
    end = sum(x["e"] for x in group) / len(group)
    index = (d["attributes"].get(key) or {}).get("i", -1)
    v = _atb_level_scaling(d, index, level, start, end, armor_red)
    if not first["gearOnly"]:
        v *= ratio
    if divide:
        v /= max(1, len(it["apt"]))
    if _hx_round(v) <= 0:
        return 0.0
    gname = STAT_GROUP_KEYS[first["g"]] if first["g"] < 4 else None
    t = d["types"].get(it.get("type")) or {}
    mul = (t.get("ratio") or {}).get(gname) or 0.0
    over = (t.get("rarities") or {}).get(rarity)
    if over is not None and gname in ("primary", "vitality") \
            and over.get(gname) is not None:
        mul = over[gname]
    return v * mul


def _generate_affixes(d, it, rarity, ilevel):
    """$HItem.generateItemAffixes -> [(attribute, value)]."""
    apts = [d["aptitudes"][a] for a in it["apt"] if a in d["aptitudes"]]
    if not apts:
        return []
    rar_i = (d["rarities"].get(rarity) or {}).get("i", 0)
    n = len(it["apt"])
    lines = []
    for a in apts:                      # $HItem.getItemExpectedScalings
        for x in a["scalings"]:
            if rarity == "Uncommon":
                if x["g"] == 1 and n > 1:
                    continue
                if x["g"] == 0 and n == 1:
                    continue
            mr = x.get("minRarity")
            if mr and rar_i < (d["rarities"].get(mr) or {}).get("i", 0):
                continue
            if x.get("factions") and it.get("fac") not in x["factions"]:
                continue
            lines.append(x)
    armor_red = sum(a["armorReduction"] for a in apts) / n
    groups = {}
    for x in lines:
        groups.setdefault(x["end"], []).append(x)
    out = []
    for end_atb, group in groups.items():
        v = _compute_atb_scaling(d, it, rarity, ilevel, group, armor_red)
        acc = {}
        for x in group:
            k = x["src"] or x["end"]
            acc[k] = acc.get(k, 0.0) + v
        for k, val in acc.items():
            if val == 0:
                continue
            if k != end_atb:
                sc = (d["attributes"].get(end_atb) or {}).get("scale", {})
                if not sc.get(k):
                    continue            # the game logs an error and skips
                val /= sc[k]
            out.append((k, _hx_round(val)))
    return out


def infusion_bonus(kind, rarity, ilevel, stat):
    """st.item.Gear.getInfusionBonusAffix: the infusion's bonus stat on a
    piece — the InfusionBonus aptitude's line for that stat, scaled like
    the piece's own (not divided by its aptitudes), times
    Item_InfusionBonusRatio, rounded. 0 when unknown."""
    d = gear_stats_data()
    it = (d.get("items") or {}).get(kind)
    apt = (d.get("aptitudes") or {}).get("InfusionBonus") or {}
    group = [x for x in apt.get("scalings") or () if x["end"] == stat]
    if not it or not group or not ilevel:
        return 0
    v = _compute_atb_scaling(d, it, rarity, ilevel, group, -1.0,
                             divide=False)
    return _hx_round((d["consts"].get("infusionBonus") or 0) * v)


def gear_stats(kind, rarity, glevel, upgrade, slots, flags):
    """A gear copy's attributes as the game computes them (st.Item.
    getItemAffixes): (iLevel, [(attribute id, French name, value)]), or None
    when the piece has no stats in the game's data."""
    d = gear_stats_data()
    it = (d.get("items") or {}).get(kind)
    if not it:
        return None
    rarity = rarity or it.get("rar")
    glevel = glevel if isinstance(glevel, int) and glevel > 0 else None
    base = _item_ilevel(d, it, rarity, glevel)
    il = base
    if _item_flag(flags, "Flawless"):
        il += _hx_round(d["consts"]["flawlessIL"])
    if isinstance(upgrade, int) and upgrade > 0:
        il += _hx_round(upgrade * d["consts"]["upgradeIL"])
    for aug in slots or ():
        a = (d["items"].get(aug) or {}) if isinstance(aug, str) else {}
        il += int(a.get("il") or 0)
    if il == base and it.get("fixed"):
        affixes = [(x["atb"], x["v"]) for x in it["fixed"] if x["atb"]]
    else:
        affixes = _generate_affixes(d, it, rarity, il)
    total = {}
    for k, v in affixes:
        total[k] = total.get(k, 0) + v
    names = _fr_names("attribute")
    order = {k: i for i, k in enumerate(GEAR_STAT_ORDER)}
    rows = sorted(((k, names.get(k) or _pretty_id(k), v)
                   for k, v in total.items() if v),
                  key=lambda r: (order.get(r[0], 99), r[1]))
    return il, rows


# The character sheet's attributes, as the game derives them (ent.Unit.
# getAtbScaling): the class base at the hero's level, plus the gear, then
# each derived attribute from its sources.
ATB_PERCENT, ATB_MOVESPEED = 4, 256           # attribute flags
HERO_PRIMARY = ("Vitality", "Strength", "Dexterity", "Faith", "Intellect")
# the game's "Plus de stats" list, in its order (BlockMitigation left out:
# it comes from the shield, which the data here doesn't say)
HERO_SECONDARY = ("CritChance", "CritDamage", "ArmorPenetration",
                  "SpellPenetration", "Fervor", "DodgeChance",
                  "MagicMastery", "PhysicalMastery", "Armor", "MaxHealth",
                  "HealthRegen")


def hero_attributes(cls, level, gear_totals, effects=None):
    """{attribute: value} for a hero of class `cls` at `level` wearing gear
    worth `gear_totals` ({attribute: flat value}), under `effects`
    ({attribute: [flat, share, factor]}: statuses, passives, talents —
    (value + flat) * (1 + share) * factor). None without the data."""
    effects = effects or {}
    d = gear_stats_data()
    base = (d.get("heroes") or {}).get(cls)
    atbs = d.get("attributes") or {}
    if not base or not level:
        return None
    memo = {}

    def own(k):
        b = base.get(k)
        if isinstance(b, list):
            v = _atb_level_scaling(d, (atbs.get(k) or {}).get("i", -1),
                                   level, b[0], b[1])
        else:
            v = b or 0
        return v + (gear_totals.get(k) or 0) + (effects.get(k) or (0,))[0]

    def kfac(k):
        f = (atbs.get(k) or {}).get("flags") or 0
        m = 0.01 if f & ATB_PERCENT else 1.0
        return m                        # MoveSpeed scaling: not on the sheet

    def total(k, depth=0):
        if k in memo:
            return memo[k]
        v = own(k)
        if depth < 6:
            for src, scale, op in (atbs.get(k) or {}).get("ops") or ():
                sv = total(src, depth + 1)
                kind = op[0] if op else 0
                if kind == 0:
                    v += scale * sv
                elif kind == 1:
                    v += (1 - 1 / (1 + sv * kfac(src) * scale)) / kfac(k)
                elif kind == 2 and len(op) >= 4:
                    ref = _atb_level_scaling(d, (atbs.get(src) or {}).get(
                        "i", -1), level, op[1], op[2])
                    if ref:
                        v += sv / ref * op[3]
        e = effects.get(k)
        if e:
            v = v * (1 + e[1]) * e[2]
        memo[k] = v
        return v

    return {k: total(k) for k in HERO_PRIMARY + HERO_SECONDARY}


def _hero_sheet(prof, gear):
    """The sheet's attributes: the primaries, then the derived ones, each
    {t, v} ready to show; armour with the damage it stops at this level."""
    totals = {}
    for g in gear:
        for x in g.get("stats") or ():
            totals[x["k"]] = totals.get(x["k"], 0) + x["v"]
        for a in g.get("augs") or ():
            for atb, val in a:
                totals[atb] = totals.get(atb, 0) + val
    for g in gear:
        inf = g.get("inf")
        if inf and inf.get("on") and inf.get("val") and inf.get("statId"):
            k = inf["statId"]
            totals[k] = totals.get(k, 0) + inf["val"]
    lvl = prof.get("lvl") if isinstance(prof.get("lvl"), int) else None
    effects, counted = _hero_effects(prof, gear)
    vals = hero_attributes(prof.get("k"), lvl, totals, effects)
    if vals is None:
        return None
    d = gear_stats_data()
    names = _fr_names("attribute")
    atbs = d.get("attributes") or {}

    def row(k):
        v = vals[k]
        pct = bool((atbs.get(k) or {}).get("flags", 0) & ATB_PERCENT)
        if pct:
            txt = f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " %"
        elif k == "HealthRegen":
            txt = f"{v:.1f}".replace(".", ",")
        else:
            txt = _n(round(v))
        out = {"k": k, "t": names.get(k) or _pretty_id(k), "v": txt}
        if k == "Armor" and lvl:
            a, b = d["consts"]["resist"][0], d["consts"]["resist"][1]
            red = v / (v + a + b * lvl) if v > 0 else 0
            out["sub"] = f"\u2212{red * 100:.2f}".replace(".", ",") + " %"
        return out
    return {"primary": [row(k) for k in HERO_PRIMARY],
            "secondary": [row(k) for k in HERO_SECONDARY],
            "effects": counted}


def _hero_effects(prof, gear):
    """The attribute effects on the hero: its active statuses, its passives
    and talents, its infusion passives (rank: one per two pieces).
    -> ({attribute: [flat, share, factor]}, [names of what counted])."""
    aff = gear_stats_data().get("skillAffixes") or {}
    masteries = set(prof.get("masteries") or ())
    talents = prof.get("talents") if isinstance(prof.get("talents"),
                                                dict) else {}
    ranks = {}
    for sid in prof.get("statuses") or ():
        ranks.setdefault(sid, 1)
    for sid in prof.get("skills") or ():
        if sid in aff:
            ranks.setdefault(sid, int(talents.get(sid) or 1))
    for sid, r in talents.items():
        if sid in aff:
            ranks[sid] = max(ranks.get(sid, 0), int(r or 1))
    pieces = {}
    for g in gear:
        inf = g.get("inf")
        if inf:
            pieces[inf["id"]] = pieces.get(inf["id"], 0) + 1
    for sid, n in pieces.items():
        if sid in aff and n >= 2:
            ranks[sid] = max(ranks.get(sid, 0), n // 2)
    out, counted = {}, []
    for sid, rank in ranks.items():
        used = False
        for ref, atb, val, conds in aff.get(sid) or ():
            if not isinstance(val, (int, float)):
                continue
            if set(conds) - {"mastery", "minRank"}:
                continue                # a condition the sheet can't judge
            if conds.get("mastery") and conds["mastery"] not in masteries:
                continue
            if conds.get("minRank") and rank < conds["minRank"]:
                continue
            e = out.setdefault(atb, [0.0, 0.0, 1.0])
            if ref == "TAttribute_Flat":
                e[0] += val
            elif ref == "TAttribute_ARatio":
                e[1] += val
            elif ref in ("TAttribute_MRatio", "TAttribute_MRatioMin"):
                e[2] *= val
            else:
                continue
            used = True
        if used:
            counted.append(_skill_label(sid))
    return out, sorted(set(counted))


def _gear_infusion(kind, raw, stat, prism=False):
    """One gear piece's infusion: name, bonus stat, and whether the bonus
    applies (the piece's faction must be the infusion's — or the piece is
    prismatic: constant Item_PrismaticChance, "infusion bonus active
    regardless of faction")."""
    sid = _infusion_id(raw)
    if not sid:
        return None
    e = (infusion_data().get("infusions") or {}).get(sid) or {}
    fac = e.get("f")
    mine = (infusion_data().get("item_faction") or {}).get(kind)
    return {"id": sid, "name": e.get("name") or _pretty_id(sid),
            "fac": faction_label(fac),
            "bonus": (_fr_names("attribute").get(stat) or _pretty_id(stat))
            if stat else "",
            "statId": stat or None,
            "on": prism or (bool(fac) and mine == fac)}


def _infusion_sets(gear):
    """The infusions worn, by number of pieces, with their 2 / 4 / 6
    tiers and which are reached."""
    infs = infusion_data().get("infusions") or {}
    counts = {}
    for g in gear:
        inf = g.get("inf")
        if inf:
            counts[inf["id"]] = counts.get(inf["id"], 0) + 1
    out = []
    for sid, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        e = infs.get(sid) or {}
        four = []
        for atb, val, ref in e.get("t4") or ():
            name = _fr_names("attribute").get(atb) or _pretty_id(atb)
            v = val * 100 if ref == "TAttribute_ARatio" else val
            four.append(f"{name} {'+' if v >= 0 else '−'}"
                        f"{abs(v):g} %".replace(".", ","))
        out.append({
            "name": e.get("name") or _pretty_id(sid),
            "fac": faction_label(e.get("f")),
            "role": e.get("role") or "", "n": n,
            "tiers": [{"n": k, "on": n >= k, "txt": _fr_ref(t)}
                      for k, t in ((2, e.get("t2")), (4, " · ".join(four)),
                                   (6, e.get("t6"))) if t]})
    return out


_WORLD_MAP = None


def world_map():
    """{"meta": tiles and transform, "points": [{c, id, x, y, zone,
    region}]} from analysis_out/map.json (hltools/map_data.py)."""
    global _WORLD_MAP
    if _WORLD_MAP is None:
        try:
            _WORLD_MAP = json.loads(
                (ANALYSIS / "map.json").read_text(encoding="utf-8"))
        except Exception:
            _WORLD_MAP = {}
    return _WORLD_MAP


# category -> (label, group). The groups are the map panel's sections.
MAP_CATS = {"chest": ("Coffre du monde", "Coffres"),
            "vault": ("Coffre de chambre forte", "Coffres"),
            "recipe": ("Coffre de recette", "Coffres"),
            "orb": ("Orbe rouge", "Orbes"),
            "obelisk": ("Obélisque", "Utilitaires"),
            "respawn": ("Point de réapparition", "Utilitaires")}


# Regions whose points are in the game's files but not yet playable (Bel-Etir
# is still in development, 2026-09-28): left off the map and its totals.
MAP_UNRELEASED_REGIONS = {"Bel_Etir_Region"}


def _element_done(states, eid):
    """Whether a world element has been completed (chest opened, orb picked
    up, obelisk discovered): Progress.elements has it, with a time. A value
    kept from the first measuring build is a [byte, time] pair."""
    v = (states or {}).get(eid)
    if isinstance(v, list):
        v = v[-1] if v else None
    return isinstance(v, (int, float)) and v > 0


def map_view(states=None):
    """The Map tab's data: the tile grid, every point with its French zone
    and region (and whether it is done, when the progress has been read),
    and the categories and regions with their counts."""
    wm = world_map()
    pts = []
    for p in wm.get("points") or ():
        if p.get("c") not in MAP_CATS \
                or p.get("region") in MAP_UNRELEASED_REGIONS:
            continue
        num = re.search(r"(\d+)$", str(p.get("id") or ""))
        pts.append({"c": p["c"], "x": p["x"], "y": p["y"],
                    "f": 1 if _element_done(states, p.get("id")) else 0,
                    "n": int(num.group(1)) if num else 0,
                    "z": _zone_label(p["zone"]) if p.get("zone") else "",
                    "r": p.get("region") or "other"})
    cats = [{"v": k, "t": t, "g": g, "n": sum(1 for p in pts if p["c"] == k)}
            for k, (t, g) in MAP_CATS.items()]
    regions = []
    seen = sorted({p["r"] for p in pts} - {"other"},
                  key=lambda r: (r not in HUNT_REGIONS, r))
    for r in seen + ["other"]:
        n = sum(1 for p in pts if p["r"] == r)
        if n:
            regions.append({"v": r, "n": n,
                            "t": _fr_names("zone").get(r) or _pretty_id(r)
                            if r != "other" else "Autres"})
    return {"meta": wm.get("meta") or {}, "points": pts, "cats": cats,
            "regions": regions, "known": states is not None}


_UNIT_NAMES = None


def _unit_names():
    """kind -> display name, from analysis_out/unit_names.json — the game's
    own data.cdb rows, extracted by emit_offsets.py on the same self-heal
    cycle as the offsets. Loaded once; {} when the file is absent.

    Names the boss kill toast and every combat history dataset: a unit's kind
    is routinely NOT the name the game shows (measured: 'Cleodora' displays as
    'Queen Honeyzabeth', 'Phrixes' as 'High Inquisitor Chakram' — the kind
    often names the LAIR, not the boss)."""
    global _UNIT_NAMES
    if _UNIT_NAMES is None:
        try:
            _UNIT_NAMES = json.loads(
                (ANALYSIS / "unit_names.json").read_text(encoding="utf-8"))
        except Exception:
            _UNIT_NAMES = {}
    return _UNIT_NAMES


_FR_NAMES = None


def _fr_names(sheet):
    """id -> French display name for one of the game's sheets (activity,
    item, rarity, unit), from analysis_out/names_fr.json — the game's own
    translation, extracted by emit_offsets.py. {} when absent."""
    global _FR_NAMES
    if _FR_NAMES is None:
        try:
            _FR_NAMES = json.loads(
                (ANALYSIS / "names_fr.json").read_text(encoding="utf-8"))
        except Exception:
            _FR_NAMES = {}
    return _FR_NAMES.get(sheet) or {}


_ITEM_RARITY = None


def item_rarity(kind):
    """An item's base rarity from its sheet row (analysis_out/
    item_rarity.json); a weapon's own copy rarity overrides it."""
    global _ITEM_RARITY
    if _ITEM_RARITY is None:
        try:
            _ITEM_RARITY = json.loads(
                (ANALYSIS / "item_rarity.json").read_text(encoding="utf-8"))
        except Exception:
            _ITEM_RARITY = {}
    return _ITEM_RARITY.get(kind)


_ITEM_ICONS = {}


def item_icon(kind):
    """An item's icon as a data URI (analysis_out/item_icons/<id>.png,
    extracted from the game by emit_offsets.py), or "" when there is none.
    Inlined because the window loads nothing from anywhere."""
    kind = str(kind or "")
    if kind not in _ITEM_ICONS:
        uri = ""
        if re.fullmatch(r"[A-Za-z0-9_]+", kind):
            try:
                import base64
                data = (ANALYSIS / "item_icons" / f"{kind}.png").read_bytes()
                uri = "data:image/png;base64," + base64.b64encode(data).decode()
            except OSError:
                pass
        _ITEM_ICONS[kind] = uri
    return _ITEM_ICONS[kind]


_DUNGEONS = None


def dungeon_catalogue():
    """Every dungeon in the game, [{kind, boss, region}], from
    analysis_out/dungeons.json (the game's achievements). [] when absent."""
    global _DUNGEONS
    if _DUNGEONS is None:
        try:
            _DUNGEONS = json.loads(
                (ANALYSIS / "dungeons.json").read_text(encoding="utf-8"))
        except Exception:
            _DUNGEONS = []
    return _DUNGEONS


def item_type_label(t):
    return _fr_names("itemType").get(t) or _pretty_id(t)


# The loot a dungeon's list row shows as icons: everything but the shards.
LOOT_ICON_SOURCES = ("coffre", "boss", "faction")


def _pct(chance):
    if chance is None:
        return "?"
    v = chance * 100
    return f"{v:.0f} %" if v >= 1 and abs(v - round(v)) < 0.05 \
        else f"{v:.2g} %".replace(".", ",")


def droptable_view(dg, got):
    """A dungeon's possible loot as table rows, rarest first. `got`: item id
    -> how many the saved runs of this dungeon brought back."""
    src_label = {"coffre": "Coffre de fin", "boss": "Mort du boss",
                 "faction": "Armure (Normal, Difficile)",
                 "heroic": "Armure (Héroïque)"}
    pools = dg.get("pools") or {}
    rows = []
    for e in dg.get("loot") or ():
        qty = ""
        if e.get("qty"):
            qty = " · ".join(
                f"{lo}–{hi}" + (f" (niv. {a}–{b})" if a and b else "")
                for lo, hi, a, b in e["qty"])
        chance = e.get("chance")
        per_class = None
        if e.get("src") in pools:
            # one piece per player, evenly among his class's: 1 / pool
            ps = sorted({1 / pools[e["src"]][c]
                         for c in (e.get("apt") or pools[e["src"]])
                         if pools[e["src"]].get(c)})
            if ps:
                chance = ps[-1]
                per_class = ps
        rows.append({
            "img": item_icon(e["item"]), "name": item_label(e["item"]),
            "rk": (e.get("rarity") or "").lower(),
            "type": item_type_label(e.get("type")) if e.get("type") else "",
            "apt": e.get("apt") or [],
            "src": src_label.get(e.get("src"), e.get("src") or ""),
            # per run; a range when the piece fits classes with different
            # pools ("8,3–9,1 %")
            "chance": ((_pct(per_class[0]).replace(" %", "") + "–"
                        + _pct(per_class[-1])
                        if per_class and len(per_class) > 1 else
                        _pct(chance) if chance is None or chance < 1
                        else "garanti")
                       + (f" en {DUNGEON_DIFFICULTIES.get(e['diff'], '?')}"
                          if e.get("diff") and not per_class else "")),
            "qty": qty, "got": got.get(e["item"], 0),
            # rarest first; the faction armour (chance unknown) after the
            # chest's pick, the guaranteed shards last
            "_k": (chance if chance is not None else 0.75,
                   -RARITY_ORDER.get(e.get("rarity"), -1))})
    rows.sort(key=lambda r: r.pop("_k"))
    return rows


def _fr_desc(sheet):
    """id -> French description for a sheet (ach, item), from
    names_fr.json's "_desc"."""
    _fr_names(sheet)
    return (_FR_NAMES or {}).get("_desc", {}).get(sheet) or {}


def item_label(kind):
    """An item's French name, else its prettified id. An infusion pattern's
    name is a template ("Infusion: ::ref_skill::"): it is named after its
    infusion."""
    name = _fr_names("item").get(kind)
    if (not name or "::" in name) and str(kind).startswith(
            "InfusionPattern_"):
        inf = next((e for e in (infusion_data().get("infusions") or {})
                    .values() if e.get("pattern") == kind), None)
        if inf:
            return f"Patron d'imprégnation : {inf.get('name')}"
    return name or _pretty_id(kind)


RARITY_FR = {"Common": "Ordinaire", "Uncommon": "Peu ordinaire",
             "Rare": "Rare", "Epic": "Épique", "Legendary": "Légendaire"}


def rarity_label(r):
    return _fr_names("rarity").get(r) or RARITY_FR.get(r) or (r or "")


_HEAL_SPECS = None


def _heal_specs():
    """skill id -> {step: [heal effect spec]}, from analysis_out/heal_specs.json.

    The game's own cdb, extracted on the same self-heal cycle as the offsets.
    Without it every heal on a full-health target is unsizeable and healing
    collapses back to "health actually restored" — so its absence is logged
    rather than swallowed."""
    global _HEAL_SPECS
    if _HEAL_SPECS is None:
        try:
            _HEAL_SPECS = json.loads(
                (ANALYSIS / "heal_specs.json").read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[meter] heal_specs.json unavailable ({e}) — healing falls "
                  "back to what each skill has been seen to restore",
                  file=sys.stderr)
            _HEAL_SPECS = {}
    return _HEAL_SPECS


def _boss_label(kind):
    """The boss's real display name, falling back to the prettified kind for
    anything the unit sheet doesn't carry."""
    return (_fr_names("unit").get(kind) or _unit_names().get(kind)
            or _pretty_id(kind))


def _summon_label(kind):
    """A summon's real display name ('Summon_Imp' -> 'Nightling Terror',
    'Rabbit_EarlyAccess_Spark' -> 'Sparktail'), falling back to the prettified
    kind for anything the unit sheet doesn't carry.

    Same sheet and the same reason as _boss_label: a unit's kind is a backend
    id, not what the game puts on its nameplate. Stripping the `Summon_`/
    `Totem_` prefix off the kind instead looks like it works — `Summon_Imp`
    reduces to a plausible "Imp" — but it is a guess that happens to read well,
    and it degenerates to a raw id on every summon not named that way."""
    return (_fr_names("unit").get(kind) or _unit_names().get(kind)
            or _pretty_id(kind))


# ---------------------------------------------------------------------------
# Frida host
# ---------------------------------------------------------------------------
def build_script_source():
    data = json.loads((ANALYSIS / "resolver_data.json").read_text(encoding="utf-8"))
    off = (ANALYSIS / "meter_offsets.json").read_text(encoding="utf-8")
    js = (FRIDA_DIR / "meter_hook.js").read_text(encoding="utf-8")
    return (f"const DATA = {json.dumps(data)};\nconst OFF = {off};\n" + js)


DATA_STAMP = ANALYSIS / ".data_stamp.json"
# Set while regenerate_data runs: the title band says the game's data is
# being re-read, rather than a bare "Connexion…" for a minute.
REGENERATING = threading.Event()

# Top-level keys the current hook needs out of the two generated files. Data
# generated by older tools predates some of these, and the hlboot.dat stamp
# alone can't tell (the *game* hasn't changed, our tools have) — so a file
# missing any of them forces a regenerate.
#
# BOTH files have to be checked, and that is not a detail. 2.3 shipped with
# only the resolver list here, and every upgrade from 2.2 came up with a dead
# minimap: %LOCALAPPDATA% still held offsets generated before the minimap
# existed, missing Entity, ArrayObj, Element and the rest. The game hadn't
# changed, so the stamp matched; the resolver keys listed here were all
# present, so the currency check passed; and sweepWorld's first line is
# `if (!OFF.Entity || !OFF.ArrayObj) return`, which fails silently forever.
# Add to these lists whenever the hook starts reading something new.
REQUIRED_RESOLVER_KEYS = ("anchors", "boss_fns", "boss_targets", "cam_targets",
                          "count_targets", "funcs", "ui_targets",
                          # The codex (3.6). Without the hooks no popup ever
                          # fires, and without the map natives the kind->rank
                          # mirror is never read — so the "only missing from
                          # codex" filter shows everything, forever, with
                          # nothing anywhere saying why.
                          "codex_targets", "map_natives",
                          # Critter captures (3.7.3). Without these the collected
                          # list still refreshes on its 20s timer, so the cost
                          # is only a laggy filter — but the regenerate is the
                          # same either way.
                          "pet_targets")
# The minimap's half of the offsets. The combat half (DamageResult, HitData,
# ...) is deliberately not listed wholesale: it has been there since the first
# release, so it can't be what an upgrade is missing, and a list that mentions
# everything is a list nobody maintains.
#
# "Key.subkey" entries check INSIDE a group. DamageResult has existed forever,
# so its presence proves nothing — but 2.3.3 started reading `blocker` out of
# it to drop damage against an invulnerable target, and a 2.3.2 file has the
# group without that field. The hook degrades quietly when it's absent (no
# gate, immune damage counted again), which is precisely the silent-upgrade
# failure the rest of this comment is about.
REQUIRED_OFFSET_KEYS = ("DungeonCtx", "InstanceLobby", "Dungeon",
                        "Activity.globalCtx", "Group.instanceLobbies",
                        "Activity.contexts", "Player.activityCtx",
                        "Activity", "ArrayObj", "BossInfo", "BossesInfo",
                        "Camera", "DamageResult.blocker", "DamageResult.effect",
                        "Element", "Entity", "Foe", "GameLayer", "Hero",
                        "Interactible", "State", "String", "Unit", "Unit.attr",
                        # Healing on a full-health target. Without these the
                        # hook sends no dynVal/attributes and every overheal is
                        # sized 0 — the exact bug the feature exists to fix.
                        "BaseSkill.dynVal1", "HitData.step", "SkillStep",
                        "UnitAttributes.faith",
                        # The legendary pickup cue. Hero has existed forever,
                        # so its presence proves nothing — the subkeys are what
                        # a pre-3.1 file is missing, and sweepInventory() bails
                        # silently without them.
                        "Hero.loadout", "Hero.weaponInHand", "Inventory",
                        "Item", "Item.uid", "Loadout", "Weapon", "Weapon.rarity",
                        # The zone signal / map backdrop identity. GameLayer
                        # has existed forever; these subkeys are what a
                        # pre-3.0.4 file is missing — without them the hook
                        # falls back to no zone identity at all (getMapId is a
                        # hostname) and the backdrop never
                        # draws.
                        "GameLayer.world", "World.level",
                        # The party roster's array walk (Group.players is an
                        # hxbit proxy, not a plain array). Arrived with the
                        # retired mount feature's collection walk and stayed
                        # when that went: the roster reads through the same
                        # two hops.
                        "ArrayProxyData", "ArrayDyn",
                        # Ore/herb nodes. A pre-3.2.1 file lacks the group and
                        # sweepArray quietly draws no nodes at all.
                        "Gatherable",
                        # Summon and pet damage. A pre-3.3.4 file has no foe
                        # class list, and without it the hook cannot safely
                        # read summonOwner off a dealer — so it doesn't, and
                        # every pet's damage silently vanishes from the parse
                        # exactly as it did before the feature existed.
                        "foeClasses",
                        # The Social tab's shard roster. Player, Hero and
                        # GameLayer have all existed for releases, so their
                        # presence proves nothing — these five subkeys are what
                        # a pre-3.2.2 file lacks. readShard() returns an empty
                        # list on its first line without them, so the tab would
                        # sit there looking like an empty shard rather than
                        # like a stale data directory: exactly the silent
                        # upgrade failure this list exists to prevent.
                        "Player.uid", "Player.hero", "GameLayer.players",
                        "Hero.kind", "Hero.level",
                        # Naming a hit's target, which is what names a combat
                        # history dataset. A pre-3.5 file has no unit class
                        # list, and without it the hook refuses to read
                        # `Unit.kind` off a DamageResult.target (typed
                        # ent.GameObject, so the field may not be there at
                        # all) — every dataset would fall back to its zone
                        # name alone.
                        "DamageResult.target", "unitClasses",
                        # The codex (3.6). st.Player has existed forever, so
                        # its presence proves nothing — `progress` is the hop a
                        # pre-3.6 file lacks, and unitsProgressMap() returns
                        # null on its first line without it. The rest are new
                        # groups. Absent, the popups never fire and the map
                        # filter has no ranks to filter on.
                        "Player.progress", "Progress", "MapData", "StringMap",
                        "CodexProxy",
                        # Which shard you are on (3.7.1). GameLayer is listed
                        # above and has existed forever, so its presence proves
                        # nothing — `serverName` is the subkey a pre-3.7.1 file
                        # lacks. checkRift() guards on it being non-null and so
                        # simply never sends the shard, leaving the settings
                        # footer reading "…" for good with nothing anywhere
                        # saying why. Same silent-upgrade shape as the roster
                        # subkeys above.
                        "GameLayer.serverName",
                        # Collected critters (3.7.3). Player has existed forever;
                        # `accountProgress` and the two new groups are what a
                        # pre-3.7.3 file lacks — readPets() returns null without
                        # them and the "only uncollected" filter silently shows
                        # every critter forever.
                        "Player.accountProgress", "AccountProgress",
                        "Collection",
                        # The buff tracker (3.8). Unit is listed above and has
                        # existed forever, so `statuses` is the subkey a pre-3.8
                        # file lacks; the Status group is entirely new.
                        # sweepStatuses() bails on its first line without them
                        # and every tray sits empty, which reads as "I picked
                        # the wrong buffs" rather than as a stale data
                        # directory. GameLayer.time and TimeState are what turn
                        # an expiry into a countdown — without them a buff would
                        # show as up forever and never sweep.
                        "Unit.statuses", "Status", "Status.refreshDuration",
                        "GameLayer.time", "TimeState")


def _data_is_current():
    """True if both generated files carry everything the hook reads.

    Missing keys are named rather than just counted: this runs before the
    overlay exists, so the log is the only place anyone can see why a
    multi-second regenerate just happened.

    A key may be "Group.field", which checks for the field inside the group —
    a group that has existed for releases can still be missing a field this
    build depends on."""
    def present(d, key):
        group, _, field = key.partition(".")
        got = d.get(group)
        if not got:
            return False
        # `is not None` rather than truthiness: an offset of 0 is a real
        # offset, and `not 0` would condemn a perfectly good file.
        return True if not field else (isinstance(got, dict)
                                       and got.get(field) is not None)

    for name, required in (("resolver_data.json", REQUIRED_RESOLVER_KEYS),
                           ("meter_offsets.json", REQUIRED_OFFSET_KEYS)):
        try:
            d = json.loads((ANALYSIS / name).read_text())
        except Exception:
            return False
        missing = [k for k in required if not present(d, k)]
        if missing:
            print(f"[meter] {name} predates this build — missing "
                  f"{', '.join(missing)}; regenerating.", file=sys.stderr)
            return False
    # unit_names.json arrived with the boss kill timer (3.4). The generators
    # only re-run when this returns False, so an upgrade over an older
    # analysis_out has to fail here once or bosses and history datasets show
    # backend ids until the next game patch. Existence only — its content is
    # cosmetic and self-describing.
    if not (ANALYSIS / "unit_names.json").exists():
        print("[meter] unit_names.json absent — regenerating for the boss "
              "names.", file=sys.stderr)
        return False
    # heal_specs.json arrived when healing started counting overheal — without
    # it a heal that restores nothing cannot be sized, which is the whole
    # feature. Same upgrade trap as the two above.
    try:
        best = json.loads((ANALYSIS / "bestiary.json").read_text(
            encoding="utf-8"))
        if isinstance(best, list):
            print("[meter] bestiary.json predates the family view — "
                  "regenerating.", file=sys.stderr)
            return False
    except (OSError, ValueError):
        pass
    if not (ANALYSIS / "codex_items.json").exists():
        print("[meter] codex_items.json absent — regenerating for the item "
              "collection.", file=sys.stderr)
        return False
    if not (ANALYSIS / "augments.json").exists():
        print("[meter] augments.json absent — regenerating for the gear "
              "augments.", file=sys.stderr)
        return False
    if not (ANALYSIS / "talents.json").exists():
        print("[meter] talents.json absent — regenerating for the talent "
              "trees.", file=sys.stderr)
        return False
    if not (ANALYSIS / "item_types.json").exists():
        print("[meter] item_types.json absent — regenerating for the "
              "Character tab.", file=sys.stderr)
        return False
    if not (ANALYSIS / "map_tiles" / "icon_chest.webp").exists():
        print("[meter] map icons absent — regenerating for the Map tab.",
              file=sys.stderr)
        return False
    if not (ANALYSIS / "map.json").exists():
        print("[meter] map.json absent — regenerating for the Map tab.",
              file=sys.stderr)
        return False
    if not (ANALYSIS / "bestiary.json").exists():
        print("[meter] bestiary.json absent — regenerating for the hunting "
              "log.", file=sys.stderr)
        return False
    try:
        has_gears = "gears" in json.loads(
            (ANALYSIS / "collection.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        has_gears = False
    if not has_gears:
        print("[meter] collection.json absent — regenerating for the "
              "Collection tab.", file=sys.stderr)
        return False
    if not (ANALYSIS / "boss_portraits").is_dir():
        print("[meter] boss_portraits absent — regenerating for the dungeon "
              "list.", file=sys.stderr)
        return False
    try:
        dgs = json.loads((ANALYSIS / "dungeons.json").read_text(
            encoding="utf-8"))
        if dgs and "loot" not in dgs[0]:
            print("[meter] dungeons.json predates the loot tables — "
                  "regenerating.", file=sys.stderr)
            return False
    except (OSError, ValueError):
        pass
    if not (ANALYSIS / "dungeons.json").exists():
        print("[meter] dungeons.json absent — regenerating for the dungeon "
              "list.", file=sys.stderr)
        return False
    if not (ANALYSIS / "item_icons").is_dir():
        print("[meter] item_icons absent — regenerating for the loot "
              "icons.", file=sys.stderr)
        return False
    if not (ANALYSIS / "item_rarity.json").exists():
        print("[meter] item_rarity.json absent — regenerating for the loot "
              "rarities.", file=sys.stderr)
        return False
    if not (ANALYSIS / "names_fr.json").exists():
        print("[meter] names_fr.json absent — regenerating for the French "
              "names (dungeons, items, bosses).", file=sys.stderr)
        return False
    if not (ANALYSIS / "achievements.json").exists():
        print("[meter] achievements.json absent — regenerating for the "
              "Succès tab.", file=sys.stderr)
        return False
    if not (ANALYSIS / "rift_rewards.json").exists():
        print("[meter] rift_rewards.json absent — regenerating for the rift "
              "rewards.", file=sys.stderr)
        return False
    if not (ANALYSIS / "luck.json").exists():
        print("[meter] luck.json absent — regenerating for the luck "
              "counters.", file=sys.stderr)
        return False
    if not (ANALYSIS / "infusions.json").exists():
        print("[meter] infusions.json absent — regenerating for the gear "
              "infusions.", file=sys.stderr)
        return False
    if not (ANALYSIS / "heal_specs.json").exists():
        print("[meter] heal_specs.json absent — regenerating so healing can "
              "be counted on full-health targets.", file=sys.stderr)
        return False
    return True


def forget_loaded_data():
    """Drop every table loaded from analysis_out/, so the next use reads the
    files a regenerate just wrote (Réparer)."""
    g = globals()
    for name in ("_GEAR_STATS", "_COLLECTION", "_CODEX_ITEMS", "_SPARK", "_BESTIARY", "_CODEX_SETS", "_ITEM_TYPES", "_AUGMENTS", "_TALENTS", "_LUCK", "_ACHIEVEMENTS", "_RIFT_REWARDS", "_INFUSIONS", "_OFFSETS", "_WORLD_MAP", "_UNIT_NAMES", "_FR_NAMES", "_ITEM_RARITY", "_DUNGEONS", "_HEAL_SPECS",):
        g[name] = None


def regenerate_data(hlboot=None, force=False, on_step=None):
    """Re-run the target/offset generators against the given hlboot.dat (or the
    tools' own auto-detect when None). Self-heals the shipped JSONs after a
    Farever patch. Skips the multi-second reparse when the same hlboot.dat is
    unchanged since the last successful run. Returns True on success."""
    import subprocess
    tools = [ROOT / "hltools" / "build_targets.py",
             ROOT / "hltools" / "emit_offsets.py"]
    missing = [t.name for t in tools if not t.exists()]
    if missing:
        print(f"[meter] can't self-heal — missing {', '.join(missing)} "
              "(copy the whole farevermeter-plus folder).", file=sys.stderr)
        return False
    stamp = None
    if hlboot is not None:
        st = Path(hlboot).stat()
        stamp = {"src": str(hlboot), "mtime": st.st_mtime, "size": st.st_size}
        if not force:
            try:
                # Say WHY when the skip doesn't happen. Regenerating costs two
                # subprocess parses of a 14 MB bytecode file, right as the game
                # is loading, and without this the log shows the cost with no
                # reason attached — which is exactly the state that made a
                # stale stamp take an hour to spot. `_data_is_current` prints
                # its own reason, so only the stamp arm needs one here.
                on_disk = json.loads(DATA_STAMP.read_text())
                if on_disk != stamp:
                    print(f"[meter] hlboot.dat has changed since the last "
                          f"regenerate (stamp {on_disk.get('size')} bytes, "
                          f"now {stamp['size']}); regenerating.",
                          file=sys.stderr)
                elif (not (ANALYSIS / "resolver_data.json").is_file()
                        or not (ANALYSIS / "meter_offsets.json").is_file()):
                    print("[meter] a generated file is missing; regenerating.",
                          file=sys.stderr)
                elif _data_is_current():
                    print("[meter] data already matches this build "
                          "(hlboot.dat unchanged).", file=sys.stderr)
                    return True
            except FileNotFoundError:
                print("[meter] no data stamp yet; regenerating.",
                      file=sys.stderr)
            except Exception as e:
                print(f"[meter] couldn't read the data stamp ({e}); "
                      "regenerating.", file=sys.stderr)
    # The tools write beside their own location, which frozen is the bundle's
    # temp directory — the output would be thrown away with it on exit. Point
    # them at the writable copy instead. Harmless from source, where the two
    # paths are already the same.
    env = dict(os.environ, FAREVER_ANALYSIS_OUT=str(ANALYSIS))
    REGENERATING.set()
    try:
        return _run_generators(tools, hlboot, env, stamp, on_step)
    finally:
        REGENERATING.clear()


def _run_generators(tools, hlboot, env, stamp, on_step=None):
    labels = {"build_targets.py": "cibles du code",
              "emit_offsets.py": "structures, images et tables"}
    for t in tools:
        print(f"[meter] regenerating {t.name} for this build ...", file=sys.stderr)
        if on_step:
            on_step(labels.get(t.name, t.name))
        # Frozen there is no python.exe to hand a script to, and sys.executable
        # is this program — so it re-invokes itself in tool mode instead.
        cmd = ([sys.executable, TOOL_FLAG, t.name] if FROZEN
               else [sys.executable, str(t)])
        if hlboot is not None:
            cmd.append(str(hlboot))
        # Without CREATE_NO_WINDOW a console flashes up for each tool on every
        # launch of the windowed build — twice, right as the game is loading.
        r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            print(f"[meter] {t.name} failed:\n{r.stdout}\n{r.stderr}",
                  file=sys.stderr)
            return False
    if stamp is not None:
        # Written AND read back. A stamp that silently fails to land costs a
        # full regenerate on every single launch — the data stays correct, so
        # nothing looks wrong except several seconds of startup, and the old
        # `except OSError: pass` made that invisible. Whatever goes wrong here,
        # the log now says so once per launch instead of never.
        try:
            DATA_STAMP.write_text(json.dumps(stamp), encoding="utf-8")
            back = json.loads(DATA_STAMP.read_text())
            if back != stamp:
                print("[meter] the data stamp did not take — every launch will "
                      f"regenerate. Wrote {stamp['size']} bytes, read back "
                      f"{back.get('size')}. Check {DATA_STAMP}.",
                      file=sys.stderr)
        except Exception as e:
            print(f"[meter] couldn't write the data stamp ({e}) — data is "
                  "correct, but every launch will regenerate it. "
                  f"Check {DATA_STAMP}.", file=sys.stderr)
    print("[meter] data regenerated for current build.", file=sys.stderr)
    return True


def _exe_path_of_pid(pid):
    """Full image path of a running process (None if unavailable)."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(4096)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        k32.CloseHandle(h)
    return None


# ---------------------------------------------------------------------------
# Parse image
# ---------------------------------------------------------------------------
# Drawn from the numbers rather than screenshotted from the overlay: the live
# windows are layered and transparent, the breakdown only ever shows one player,
# and a capture would be at the mercy of whatever the game had drawn behind
# them. Same palette and the same monospace column layout, so it still reads as
# the meter.
PARSE_IMG_W = 620
PARSE_FONT_UI = "segoeuib.ttf"      # Segoe UI Bold, to match the headers
PARSE_FONT_MONO = "consola.ttf"     # Consolas, to match the columns


def _parse_font(name, size):
    """Load a Windows font by filename, falling back to PIL's built-in bitmap
    font so a missing/odd font install degrades the image instead of losing it."""
    from PIL import ImageFont
    for path in (Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name,
                 Path(name)):
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_parse_image(data, path):
    """Draw a finished parse to `path` as a PNG. `data` is the plain dict built
    by Overlay._parse_snapshot — no Tk or session access from in here."""
    from PIL import Image, ImageDraw

    ui = _parse_font(PARSE_FONT_UI, 15)
    ui_small = _parse_font(PARSE_FONT_UI, 11)
    mono = _parse_font(PARSE_FONT_MONO, 14)
    mono_small = _parse_font(PARSE_FONT_MONO, 12)

    pad, bar_h, line_h, row_gap = 12, 6, 19, 6
    x0, x1 = 12, PARSE_IMG_W - 13
    rows, focus = data["rows"], data["focus"]

    # Drawn onto a canvas that's certainly tall enough and cropped to the ink at
    # the end — cheaper to get right than keeping a height formula in step with
    # the layout below, and it can't clip a long skill list.
    img = Image.new("RGB", (PARSE_IMG_W, 2000), BG_BODY)
    d = ImageDraw.Draw(img)

    d.rectangle((0, 0, PARSE_IMG_W - 1, 35), fill=BG_HEADER)
    d.text((x0, 9), data["title"], font=ui, fill=FG_HEADER)
    stamp = f"{data['when']}   {data['duration']:.0f}s"
    d.text((x1 - d.textlength(stamp, font=mono_small), 13), stamp,
           font=mono_small, fill=FG_HEADER)
    y = 36 + pad

    def bar(x, y, w, frac, colour, thick, self_frac=0.0):
        """One bar, optionally split: `self_frac` of the SAME scale as `frac`
        is drawn from the left edge in the self-heal parchment, over the top of
        full-length bar — same trick as the live overlay, so the two can't
        disagree about where the join is."""
        d.rectangle((x, y, x + w, y + thick - 1), fill=BG_BAR_TRACK)
        filled = int(w * min(1.0, max(0.0, frac)))
        if filled > 0:
            d.rectangle((x, y, x + filled, y + thick - 1), fill=colour)
        selfw = int(w * min(min(1.0, max(0.0, self_frac)),
                            min(1.0, max(0.0, frac))))
        if selfw > 0:
            d.rectangle((x, y, x + selfw, y + thick - 1), fill=SELF_HEAL_BAR)

    d.text((x0, y), f"{data['mode']}   ({len(rows)})", font=ui_small, fill=ACCENT)
    y += 18
    d.text((x0, y),
           f"  #  {'NOM':<12}{'DÉGÂTS':>9} {'DPS':>6} {'%':>4}"
           f"{'SOINS':>9}{'EXCÈS':>6}",
           font=mono, fill=FG_DIM)
    y += line_h

    top_dmg = max((r["total"] for r in rows), default=0.0) or 1.0
    top_heal = max((r["heal"] for r in rows), default=0.0) or 1.0
    for i, r in enumerate(rows, 1):
        me = "*" if r["is_me"] else " "
        over = (f"{r.get('overheal', 0.0):>5.0f}%" if r["heal"] > 0.5
                else " " * 6)
        d.text((x0, y), f"  {i}.{me}{r['name'][:12]:<12}{int(r['total']):>9} "
                        f"{r['dps']:>6.0f} {r['pct']:>3.0f}%"
                        f"{int(r['heal']):>9}{over}",
               font=mono, fill=FG_VALUE if r["is_me"] else FG_TEXT)
        y += line_h
        bar(x0, y, x1 - x0, r["total"] / top_dmg, DMG_BAR, bar_h)
        y += bar_h
        bar(x0, y, x1 - x0, r["heal"] / top_heal, HEAL_BAR, bar_h,
            self_frac=r.get("heal_self", 0.0) / top_heal)
        y += bar_h + row_gap

    if focus:
        y += 6
        d.line((x0, y, x1, y), fill=BG_BAR_TRACK)
        y += 10
        d.text((x0, y), f"DÉTAIL — {focus['name']}", font=ui, fill=FG_TEXT)
        y += 24
        d.text((x0, y), focus["stats"], font=mono_small, fill=FG_DIM)
        y += line_h + 4

        # Damage left, healing right — each column's bars scale to that column's
        # own biggest entry, exactly like the live breakdown.
        colw = (x1 - x0 - 18) // 2
        columns = ((x0, "DÉGÂTS", focus["skills"], DMG_BAR, focus["total"]),
                   (x0 + colw + 18, "SOINS", focus["heals"], HEAL_BAR,
                    focus["heal"]))
        for cx, title, _entries, _colour, _denom in columns:
            d.text((cx, y), title, font=ui_small, fill=ACCENT)
        y += 16
        col_bottom = y
        for cx, _title, entries, colour, denom in columns:
            # Same denominator and same line format as SkillColumn.show: the %
            # is of the player's overall total, not of the listed rows.
            scale = max((e[1] for e in entries), default=0.0) or 1.0
            cy = y
            for label, amount, hits, _crits, slf in entries:
                pct = (amount / denom * 100) if denom else 0.0
                d.text((cx, cy),
                       f"{label[:16]:<16}{int(amount):>8} {pct:>3.0f}% {hits:>3}×",
                       font=mono_small, fill=FG_TEXT)
                cy += line_h
                frac = amount / scale
                bar(cx, cy, colw, frac, colour, 4,
                    self_frac=frac * (slf / amount) if amount > 0 else 0.0)
                cy += 4 + 3
            col_bottom = max(col_bottom, cy)
        y = col_bottom

        if focus["elements"]:
            y += 4
            d.text((x0, y), focus["elements"], font=mono_small, fill=FG_DIM)
            y += line_h

    img = img.crop((0, 0, PARSE_IMG_W, y + pad))
    # Border last, so it frames the cropped height rather than the scratch one.
    ImageDraw.Draw(img).rectangle((0, 0, PARSE_IMG_W - 1, img.height - 1),
                                 outline=BG_BORDER, width=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def report_view(data):
    """A saved rift report, as display-ready data for the page."""
    def cls(p):
        # Reports saved before the translation carry English tags.
        c = p.get("cls") or ""
        return OLD_CLASS_TAGS.get(c, c)

    def rank(players, key, dur, total):
        out = []
        for i, p in enumerate(players, 1):
            amt = float(p.get(key) or 0)
            rate = _rate(amt, dur)
            out.append({"rank": i, "name": p.get("name") or "?",
                        "cls": cls(p), "ck": class_key(p.get("cls")),
                        "rate": _n(rate) if rate else "—",
                        "total": _n(amt),
                        "pct": f"{(amt / total * 100) if total else 0:.0f}%"})
        return out

    phases = []
    for ph in data.get("phases") or []:
        dur = float(ph.get("duration") or 0)
        total, heal = float(ph.get("total") or 0), float(ph.get("heal") or 0)
        players = ph.get("players") or []
        healers = sorted((p for p in players if (p.get("heal") or 0) > 0.5),
                         key=lambda p: -p["heal"])
        mvp = players[0] if players else None
        phases.append({
            "label": phase_label(ph.get("label") or "Phase"),
            "dur": _mmss(dur),
            "dps": _rate_text(total, dur, "DPS") or "— DPS",
            "hps": _rate_text(heal, dur, "HPS") or "— HPS",
            "totals": f"{_n(total)} dégâts · {_n(heal)} soins"
                      + _overheal_note(ph),
            "mvp": ({"name": mvp.get("name") or "?",
                     "cls": cls(mvp), "ck": class_key(mvp.get("cls")),
                     "v": _rate_text(mvp.get("total", 0), dur, "DPS")
                     or f"{_n(mvp.get('total', 0))} dégâts"}
                    if mvp else None),
            "healer": ({"name": healers[0].get("name") or "?",
                        "cls": cls(healers[0]),
                        "ck": class_key(healers[0].get("cls")),
                        "v": _rate_text(healers[0]["heal"], dur, "HPS")
                        or f"{_n(healers[0]['heal'])} soins"}
                       if healers else None),
            "dmg": rank(players, "total", dur, total),
            # Everyone in the phase: those who healed nothing at the
            # bottom, greyed, so the table always lists the whole group.
            "heal": rank(healers, "heal", dur, heal) + [
                dict(r, rank=len(healers) + i, zero=True)
                for i, r in enumerate(rank(
                    [p for p in players
                     if (p.get("heal") or 0) <= 0.5], "heal", dur, heal),
                    1)],
            "types": [{"t": element_label(el),
                       "pct": _pct1(amt / total * 100 if total else 0),
                       "f": round(amt / (ph["elements"][0][1] or 1), 4),
                       "c": element_color(el)}
                      for el, amt in (ph.get("elements") or [])[:8]],
        })
    when = date_fr(time.localtime(data.get("at") or 0))
    out = {"k": "report", "id": "report",
           "detail": _report_players(data),
           "title": data.get("title") or "Rapport de faille",
           "sub": data.get("sub") or "",
           "when": when, "phases": phases}
    if isinstance(data.get("loot"), list):
        out["loot"] = loot_view(data["loot"])
    return out


RARITY_ORDER = {"Common": 0, "Uncommon": 1, "Rare": 2, "Epic": 3,
                "Legendary": 4}

def _report_players(data):
    """Each player of a report, phase by phase: what their damage and
    healing were made of — per skill (share, hits, crit rate, average hit)
    and per element. Everything a saved report already holds."""
    names = data.get("skill_names") or {}

    def label(sid):
        return names.get(sid) or _pretty_id(str(sid).split(":")[-1])

    out = {}
    for ph in data.get("phases") or []:
        dur = float(ph.get("duration") or 0)
        for p in ph.get("players") or []:
            who = p.get("name") or "?"
            total = float(p.get("total") or 0)
            heal = float(p.get("heal") or 0)
            # One row per NAME: the game names every step of a basic-attack
            # combo "Attaque", and three "Attaque" rows read as a bug.
            merged = {}
            for sid, v in (p.get("skills") or {}).items():
                hits, amt, crits = (list(v) + [0, 0, 0])[:3]
                m = merged.setdefault(label(sid), [0, 0.0, 0])
                m[0] += hits
                m[1] += amt
                m[2] += crits
            skills = []
            for name, (hits, amt, crits) in merged.items():
                skills.append({"n": name, "t": _n(amt),
                               "f": round(amt / total, 4) if total else 0,
                               "pct": _pct1(amt / total * 100 if total else 0),
                               "hits": _n(hits),
                               "crit": f"{crits / hits * 100:.0f} %"
                                       if hits else "—",
                               "avg": _n(amt / hits) if hits else "—",
                               "_a": amt})
            skills.sort(key=lambda s: -s.pop("_a"))
            hmerged = {}
            for sid, v in (p.get("heals") or {}).items():
                hits, amt = (list(v) + [0, 0])[:2]
                m = hmerged.setdefault(label(sid), [0, 0.0])
                m[0] += hits
                m[1] += amt
            heals = []
            for name, (hits, amt) in hmerged.items():
                heals.append({"n": name, "t": _n(amt),
                              "f": round(amt / heal, 4) if heal else 0,
                              "pct": _pct1(amt / heal * 100 if heal else 0),
                              "hits": _n(hits), "_a": amt})
            heals.sort(key=lambda s: -s.pop("_a"))
            els = sorted((p.get("elements") or {}).items(),
                         key=lambda kv: -(kv[1][1] if isinstance(kv[1], list)
                                          else kv[1]))
            elements = []
            for el_, v in els:
                amt = v[1] if isinstance(v, list) else v
                elements.append({"t": element_label(el_),
                                 "pct": _pct1(amt / total * 100 if total
                                              else 0),
                                 "f": round(amt / total, 4) if total else 0,
                                 "c": element_color(el_)})
            hits = int(p.get("hits") or 0)
            entry = out.setdefault(who, {"name": who,
                                         "ck": class_key(p.get("cls")),
                                         "cls": OLD_CLASS_TAGS.get(
                                             p.get("cls") or "",
                                             p.get("cls") or ""),
                                         "phases": []})
            entry["phases"].append({
                "label": phase_label(ph.get("label") or "Phase"),
                "facts": [
                    ["Dégâts", _n(total)],
                    ["DPS", _n(_rate(total, dur)) if _rate(total, dur)
                     else "—"],
                    ["Coups", _n(hits)],
                    ["Critiques", f"{int(p.get('crits') or 0) / hits * 100:.0f} %"
                     if hits else "—"],
                    ["Kills", _n(int(p.get("kills") or 0))],
                    ["Soins", _n(heal)]],
                "skills": skills, "heals": heals, "elements": elements})
    return out


LOOT_PHASES = (("coffre", "Coffre de fin"), ("boss", "Phase du boss"),
               ("exploration", "Exploration"))


def loot_view(loot):
    """A dungeon run's loot, grouped by phase (the chest first), identical
    items merged."""
    groups = []
    for key, label in LOOT_PHASES:
        merged = {}
        for it in loot:
            if it.get("phase") != key:
                continue
            k = (it.get("item"),
                 it.get("rarity") or item_rarity(it.get("item")),
                 it.get("level"))
            merged[k] = merged.get(k, 0) + int(it.get("count") or 1)
        if merged:
            groups.append({"t": label, "items": [
                {"name": item_label(item), "qty": qty,
                 "img": item_icon(item),
                 "rarity": rarity_label(rar) if rar else "",
                 "rk": (rar or "").lower(),
                 "level": f"niv. {lvl}" if isinstance(lvl, int) and lvl > 0
                 else ""}
                for (item, rar, lvl), qty in sorted(
                    merged.items(),
                    key=lambda kv: -RARITY_ORDER.get(kv[0][1], -1))]})
    return groups


# The rift report as an image. Drawn from the same display data as the Failles
# page (report_view), in the same palette and layout — cards per phase, framed
# tables, every player — so a pasted image looks like the window it came from.
# Drawn rather than screenshotted: pixel-clean, and it works with the window
# closed (the .png is written the moment a rift ends).
IMG_BG, IMG_PANEL, IMG_PANEL2 = "#15161C", "#1D1F28", "#242733"
IMG_LINE, IMG_TEXT, IMG_DIM, IMG_FAINT = "#2E3240", "#E7E4DC", "#9C988F", "#6E6B65"
IMG_ACCENT, IMG_HEAL, IMG_RIFT, IMG_PHASE = "#E2B65B", "#57C08A", "#D65DB1", "#F3C9E5"
IMG_COL_W = 560                 # one phase card
IMG_PAD = 24


def render_rift_report_image(data, path=None):
    """Draw a rift report as a PIL image; also writes a PNG when `path` is
    given."""
    from PIL import Image, ImageDraw

    view = report_view(data)
    reg = lambda size: _parse_font("segoeui.ttf", size)     # noqa: E731
    bold = lambda size: _parse_font("segoeuib.ttf", size)   # noqa: E731
    f_title, f_phase, f_mvp = bold(22), bold(17), bold(18)
    f_fact_v, f_body_b, f_body = bold(19), bold(14), reg(14)
    f_small, f_small_b, f_tiny = reg(12), bold(12), reg(11)

    W = IMG_PAD * 3 + IMG_COL_W * 2
    img = Image.new("RGBA", (W, 4000), IMG_BG)
    icons = {}

    def icon(key, size, faded=False):
        """A class icon at `size` px, or None if there is none."""
        if not key:
            return None
        k = (key, size, faded)
        if k not in icons:
            try:
                im = Image.open(CLASS_ICON_DIR / f"{key}.png").convert("RGBA")
                im = im.resize((size, size), Image.LANCZOS)
                if faded:
                    a = im.getchannel("A").point(lambda v: v * 45 // 100)
                    im.putalpha(a)
                icons[k] = im
            except OSError:
                icons[k] = None
        return icons[k]
    d = ImageDraw.Draw(img)

    def text(x, y, s, font, fill, anchor="la"):
        d.text((x, y), str(s), font=font, fill=fill, anchor=anchor)

    def fit(s, font, width):
        """Elide `s` to fit `width` pixels."""
        s = str(s)
        if d.textlength(s, font=font) <= width:
            return s
        while s and d.textlength(s + "…", font=font) > width:
            s = s[:-1]
        return s + "…"

    def star(x, y, r, fill):
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rad = r if i % 2 == 0 else r * 0.42
            pts.append((x + rad * math.cos(a), y + rad * math.sin(a)))
        d.polygon(pts, fill=fill)

    def plus(x, y, r, fill):
        t = max(2, int(r * 0.55))
        d.rectangle((x - t // 2, y - r, x + t // 2, y + r), fill=fill)
        d.rectangle((x - r, y - t // 2, x + r, y + t // 2), fill=fill)

    def table(x, y, w, rows, rate_label):
        """A framed ranking: header band, a rule between rows. Returns the
        y below it."""
        row_h, head_h = 26, 24
        cols = (("", 26, "la"), ("JOUEUR", None, "la"), (rate_label, 70, "ra"),
                ("TOTAL", 86, "ra"), ("PART", 50, "ra"))
        fixed = sum(c[1] for c in cols if c[1])
        name_w = w - fixed - 20
        h = head_h + row_h * len(rows)
        d.rounded_rectangle((x, y, x + w, y + h), 6, fill=IMG_BG,
                            outline=IMG_LINE)
        d.rounded_rectangle((x + 1, y + 1, x + w - 1, y + head_h), 5,
                            fill=IMG_PANEL2)

        def cells(yy, values, fonts, fills):
            cx = x + 10
            for (label, cw, anchor), v, f, fl in zip(cols, values, fonts, fills):
                cw = cw or name_w
                if anchor == "ra":
                    text(cx + cw, yy, v, f, fl, "ra")
                else:
                    text(cx, yy, v, f, fl)
                cx += cw

        cells(y + 6, [c[0] for c in cols], [f_tiny] * 5, [IMG_DIM] * 5)
        yy = y + head_h
        for i, r in enumerate(rows):
            if i % 2:
                d.rectangle((x + 1, yy, x + w - 1, yy + row_h),
                            fill="#191B22")
            d.line((x + 1, yy, x + w - 1, yy), fill=IMG_LINE)
            zero, top = r.get("zero"), r["rank"] <= 3 and not r.get("zero")
            ink = IMG_FAINT if zero else IMG_TEXT
            nfont = f_body_b if top else f_body
            name = fit(r["name"], nfont, name_w - 50)
            cells(yy + 5, [r["rank"], name, r["rate"], r["total"], r["pct"]],
                  [f_body, nfont, f_body, f_body, f_body],
                  [IMG_FAINT if zero else IMG_DIM, ink, ink, ink, ink])
            nx = int(x + 10 + 26 + d.textlength(name, font=nfont) + 6)
            ic = icon(r.get("ck"), 16, zero)
            if ic is not None:
                img.alpha_composite(ic, (nx, yy + 5))
            elif r.get("cls"):
                text(nx, yy + 7, r["cls"], f_small, IMG_FAINT if zero else IMG_DIM)
            yy += row_h
        # The frame last, so the row fills cannot paint over its edge.
        d.rounded_rectangle((x, y, x + w, y + h), 6, outline=IMG_LINE)
        return y + h

    def phase_card(x, y, ph):
        w = IMG_COL_W
        pad = 18
        ix, iw = x + pad, w - pad * 2
        cy = y + pad
        text(ix, cy, ph["label"].upper(), f_phase, IMG_PHASE)
        cy += 32
        fx = ix
        for label, value in (("DURÉE", ph["dur"]),
                             ("DPS", ph["dps"].replace(" DPS", "")),
                             ("HPS", ph["hps"].replace(" HPS", ""))):
            fw = max(110, int(d.textlength(value, font=f_fact_v)) + 26)
            d.rounded_rectangle((fx, cy, fx + fw, cy + 52), 6,
                                fill=IMG_PANEL2, outline=IMG_LINE)
            text(fx + 12, cy + 7, label, f_tiny, IMG_DIM)
            text(fx + 12, cy + 22, value, f_fact_v, IMG_TEXT)
            fx += fw + 8
        cy += 62
        text(ix, cy, ph["totals"], f_small, IMG_DIM)
        cy += 26
        d.line((ix, cy, ix + iw, cy), fill=IMG_LINE)
        cy += 14
        if ph.get("mvp"):
            boxes = [("MVP DÉGÂTS", ph["mvp"], IMG_ACCENT, star)]
            if ph.get("healer"):
                boxes.append(("MVP SOINS", ph["healer"], IMG_HEAL, plus))
            bw = (iw - 10 * (len(boxes) - 1)) // len(boxes)
            for i, (label, who, colour, mark) in enumerate(boxes):
                bx = ix + i * (bw + 10)
                d.rounded_rectangle((bx, cy, bx + bw, cy + 78), 6,
                                    fill=IMG_PANEL2, outline=IMG_LINE)
                d.rectangle((bx, cy + 1, bx + 3, cy + 77), fill=colour)
                text(bx + 14, cy + 8, label, f_tiny, IMG_DIM)
                mark(bx + 24, cy + 38, 9 if mark is star else 7, colour)
                name = fit(who["name"], f_mvp, bw - 90)
                text(bx + 40, cy + 26, name, f_mvp, colour)
                ix2 = int(bx + 46 + d.textlength(name, font=f_mvp))
                ic = icon(who.get("ck"), 20)
                if ic is not None:
                    img.alpha_composite(ic, (ix2, cy + 30))
                elif who.get("cls"):
                    text(ix2, cy + 32, who["cls"], f_small, IMG_DIM)
                text(bx + 14, cy + 54, who["v"], f_body, IMG_TEXT)
            cy += 92
        else:
            text(ix, cy, "rien n'a été enregistré pour cette phase", f_body,
                 IMG_FAINT)
            cy += 30

        def heading(s):
            nonlocal cy
            cy += 8
            text(ix, cy, s.upper(), f_small_b, IMG_RIFT)
            cy += 22

        if ph.get("dmg"):
            n = len(ph["dmg"])
            heading(f"Dégâts — {n} joueur{'s' if n > 1 else ''}")
            cy = table(ix, cy, iw, ph["dmg"], "DPS") + 10
        heals = ph.get("heal") or []
        n = len(heals)
        heading("Soins" + (f" — {n} joueur{'s' if n > 1 else ''}" if n else ""))
        if heals:
            cy = table(ix, cy, iw, heals, "HPS") + 10
        else:
            text(ix, cy, "aucun soin enregistré", f_body, IMG_FAINT)
            cy += 28
        if ph.get("types"):
            heading("Dégâts par type")
            th = 14 + 24 * len(ph["types"])
            d.rounded_rectangle((ix, cy, ix + iw, cy + th), 6, fill=IMG_BG,
                                outline=IMG_LINE)
            ty = cy + 9
            for t in ph["types"]:
                text(ix + 12, ty, t["t"], f_small, t["c"])
                bx0, bx1 = ix + 110, ix + iw - 80
                bw = max(4, int((bx1 - bx0) * max(0.02, t["f"])))
                d.rounded_rectangle((bx0, ty + 4, bx0 + bw, ty + 11), 3,
                                    fill=t["c"])
                text(ix + iw - 12, ty, t["pct"], f_small, IMG_TEXT, "ra")
                ty += 24
            cy += th
        return cy + pad

    # Measure both cards on a scratch pass, so they can share one height.
    y0 = IMG_PAD + 64
    bottoms = []
    for i, ph in enumerate(view["phases"][:2]):
        bottoms.append(phase_card(IMG_PAD + i * (IMG_COL_W + IMG_PAD), y0, ph))
    card_bottom = max(bottoms or [y0 + 40])

    # Draw for real: background panel, title, then the cards over it.
    d.rectangle((0, 0, W, 4000), fill=IMG_BG)
    H = card_bottom + IMG_PAD
    d.rounded_rectangle((8, 8, W - 8, H - 8), 10, fill=IMG_PANEL,
                        outline=IMG_LINE)
    text(IMG_PAD, IMG_PAD, view["title"], f_title, IMG_RIFT)
    after = view["when"] + (f"   ·   {view['sub']}" if view.get("sub") else "")
    text(IMG_PAD + d.textlength(view["title"], font=f_title) + 14,
         IMG_PAD + 6, after, f_body, IMG_DIM)
    d.line((IMG_PAD, IMG_PAD + 44, W - IMG_PAD, IMG_PAD + 44), fill=IMG_LINE)
    for i, ph in enumerate(view["phases"][:2]):
        x = IMG_PAD + i * (IMG_COL_W + IMG_PAD)
        d.rounded_rectangle((x, y0, x + IMG_COL_W, card_bottom), 10,
                            fill=IMG_BG, outline=IMG_LINE)
        phase_card(x, y0, ph)
    img = img.crop((0, 0, W, H)).convert("RGB")
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
    return img


def copy_image_to_clipboard(img):
    """Put a PIL image on the Windows clipboard as CF_DIB — the format every
    paste target understands. A BMP file is a 14-byte header ahead of a DIB,
    so the conversion is a save and a slice, no encoder gymnastics."""
    import io
    if sys.platform != "win32":
        raise OSError("image clipboard is Windows-only")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    dib = buf.getvalue()[14:]

    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = (ctypes.c_void_p,)
    k32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
    k32.GlobalFree.argtypes = (ctypes.c_void_p,)
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.SetClipboardData.argtypes = (wintypes.UINT, ctypes.c_void_p)

    GMEM_MOVEABLE = 0x0002
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
    if not h:
        raise OSError("GlobalAlloc failed")
    p = k32.GlobalLock(h)
    ctypes.memmove(p, dib, len(dib))
    k32.GlobalUnlock(h)
    # The clipboard is one shared lock; whoever synced it last (clipboard
    # managers love to) can hold it for a beat. Brief retries beat failing.
    for attempt in range(5):
        if u32.OpenClipboard(0):
            break
        time.sleep(0.05)
    else:
        k32.GlobalFree(h)
        raise OSError("clipboard is held by another window")
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(8, ctypes.c_void_p(h)):    # CF_DIB
            k32.GlobalFree(h)
            raise OSError("SetClipboardData failed")
        # Ownership of `h` passed to the system on success — no free here.
    finally:
        u32.CloseClipboard()


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259


def _open_process(pid, access):
    if sys.platform != "win32" or pid <= 0:
        return None
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    return k32.OpenProcess(access, 0, pid) or None


def _close_handle(h):
    k32 = ctypes.windll.kernel32
    k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    k32.CloseHandle(h)


def _process_alive(pid):
    """True while `pid` is still running. Note this can't be os.kill(pid, 0):
    on Windows os.kill TERMINATES the target instead of probing it."""
    h = _open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not h:
        return False
    try:
        k32 = ctypes.windll.kernel32
        k32.GetExitCodeProcess.argtypes = (ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_ulong))
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        return bool(ok) and code.value == STILL_ACTIVE
    finally:
        _close_handle(h)


def _process_image(pid):
    """Full path of a pid's executable, or "". Guards the force-kill path: a
    stale lock file can name a pid Windows has since recycled onto something
    else entirely, and that must not be what gets terminated."""
    h = _open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not h:
        return ""
    try:
        k32 = ctypes.windll.kernel32
        k32.QueryFullProcessImageNameW.argtypes = (
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong))
        size = ctypes.c_ulong(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return buf.value if ok else ""
    finally:
        _close_handle(h)


def _quit_flag(pid):
    return LOCK_DIR / f"quit-{pid}"


def quit_requested():
    """Has a newly-started instance asked us to stand down? Polled by the
    overlay's own loop, so the answer is acted on within one refresh tick."""
    try:
        return _quit_flag(os.getpid()).exists()
    except OSError:
        return False


def watch_for_quit_request():
    """Poll the stand-down flag on a background thread, for the stretches where
    nothing else is polling it.

    The overlay checks the flag on its own refresh tick, but the overlay doesn't
    exist yet while we're waiting for Farever to launch or for the hook's memory
    scan to finish — and those are precisely the stretches a newly-started meter
    has to displace us through. Without this, an instance that hasn't reached
    the overlay ignores the request entirely and gets force-killed twelve
    seconds later, which is the outcome the whole polite handover exists to
    avoid. It matters more now that the meter is built to be started *before*
    the game, and so spends real time waiting."""
    def work():
        while not STOP.is_set():
            if quit_requested():
                print("[meter] a newer instance asked us to exit — standing "
                      "down.", file=sys.stderr)
                request_stop()
                return
            time.sleep(0.25)

    threading.Thread(target=work, daemon=True, name="quit-watch").start()


def _stop_instance(pid):
    """Ask pid to exit, and wait. The request is a file the running overlay
    polls — it returns from its mainloop and takes main()'s normal shutdown
    path, unloading the hook and detaching. Force-killing is the fallback only,
    because that's what leaves a half-attached agent in the game."""
    flag = _quit_flag(pid)
    try:
        flag.write_text("quit")
    except OSError:
        return
    deadline = time.monotonic() + QUIT_WAIT_SECS
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            print(f"[meter] pid {pid} shut down cleanly.", file=sys.stderr)
            break
        time.sleep(0.25)
    else:
        print(f"[meter] pid {pid} didn't respond within {QUIT_WAIT_SECS:.0f}s — "
              "forcing it. If the hook then fails to attach, fully close "
              "Farever and reopen it.", file=sys.stderr)
        h = _open_process(pid, PROCESS_TERMINATE)
        if h:
            ctypes.windll.kernel32.TerminateProcess.argtypes = (ctypes.c_void_p,
                                                                ctypes.c_uint)
            ctypes.windll.kernel32.TerminateProcess(h, 1)
            _close_handle(h)
    try:
        flag.unlink()
    except OSError:
        pass


def _acquire_claim_mutex():
    """Take the system-wide lock covering the read-decide-write in
    claim_single_instance(), returning a handle to release afterwards.

    Without it that sequence races itself. Stopping the previous instance takes
    up to QUIT_WAIT_SECS, and the winner's own pid isn't written to the lock
    file until after that — so a meter started inside the gap reads the same
    stale pid, shuts down the same already-dying instance, and declares itself
    the survivor too. Two live meters, each certain it's the only one. Starting
    the game from Steam while a shortcut launch is still settling is enough to
    hit it."""
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    try:
        h = k32.CreateMutexW(None, False, CLAIM_MUTEX)
        if not h:
            return None
        # Long enough to outlast a full QUIT_WAIT_SECS handover ahead of us.
        # A timeout isn't fatal — carrying on unserialised is exactly the old
        # behaviour, so the worst case is no worse than before.
        k32.WaitForSingleObject(h, CLAIM_WAIT_MS)
        return h
    except Exception as e:
        print(f"[meter] claim lock unavailable ({e}) — continuing.",
              file=sys.stderr)
        return None


def _release_claim_mutex(h):
    if not h:
        return
    k32 = ctypes.windll.kernel32
    try:
        k32.ReleaseMutex(h)
        k32.CloseHandle(h)
    except Exception:
        pass


def claim_single_instance():
    """Become the only meter running, then record ourselves in the lock file.

    Two overlays on screen at once is confusing enough; two hooks in the game is
    worse. The usual way into it is launching a *second copy* of this script
    from a different folder while the first is still up — which is why the lock
    lives in LOCK_DIR rather than beside the script."""
    try:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return                       # no lock dir => skip the mechanism entirely

    claim = _acquire_claim_mutex()
    try:
        _claim_locked()
    finally:
        _release_claim_mutex(claim)


def _claim_locked():
    """The claim itself. Only ever runs one-at-a-time system-wide."""
    try:
        other = json.loads(LOCK_FILE.read_text())
    except Exception:
        other = {}
    pid = int(other.get("pid") or 0)
    if pid and pid != os.getpid() and _process_alive(pid):
        image = _process_image(pid)
        # A meter is either a python interpreter running the script or the
        # installed executable, and BOTH have to be recognised from either side
        # — the case this exists for is one build being started while the other
        # is already up, and each has to see the other as a meter to displace.
        name = Path(image).name.lower()
        if "python" in name or name in METER_IMAGE_NAMES:
            print(f"[meter] another meter is already running (pid {pid}, "
                  f"{other.get('script') or 'unknown script'}) — asking it to "
                  "exit ...", file=sys.stderr)
            _stop_instance(pid)
        else:
            print(f"[meter] ignoring a stale lock: pid {pid} is "
                  f"{image or 'something unidentifiable'}, not a meter.",
                  file=sys.stderr)

    try:
        LOCK_FILE.write_text(json.dumps({
            "pid": os.getpid(),
            "script": str(Path(__file__).resolve()),
            "started": time.time(),
        }))
    except OSError:
        pass


def release_instance_lock():
    for p in (LOCK_FILE, _quit_flag(os.getpid())):
        try:
            p.unlink()
        except OSError:
            pass


def locate_hlboot(pid):
    """Find the hlboot.dat matching the *running* game. Priority: explicit
    FAREVER_HLBOOT override, then the file next to the process's own exe (which
    makes a multi-install mismatch impossible), then the drive auto-detect, and
    finally just asking. Returns a Path or None (= use shipped data as-is)."""
    env = os.environ.get("FAREVER_HLBOOT")
    if env:
        if Path(env).is_file():
            return Path(env)
        print(f"[meter] FAREVER_HLBOOT points to a missing file: {env}",
              file=sys.stderr)
    exe = _exe_path_of_pid(pid)
    if exe:
        cand = Path(exe).parent / "hlboot.dat"
        if cand.is_file():
            return cand
        print(f"[meter] no hlboot.dat next to {exe} — searching drives.",
              file=sys.stderr)
    sys.path.insert(0, str(ROOT / "hltools"))
    try:
        from gamepath import find_hlboot
        return Path(find_hlboot(argv_index=99))
    except (SystemExit, Exception):
        pass
    # Nothing is asked any more: this runs on the game link's thread, with no
    # window of its own to ask from. The shipped data is used as-is, which is
    # fine unless the game has patched since.
    print("[meter] hlboot.dat not found — using the shipped data files. Set "
          "FAREVER_HLBOOT to its full path if Farever is installed somewhere "
          "unusual.", file=sys.stderr)
    return None


def main():
    # First, before anything that can put a window on screen — the tray icon,
    # Tk, or an update message box. Windows latches DPI awareness at the first
    # window and ignores every later attempt to change it.
    print(f"[meter] dpi awareness: {declare_dpi_awareness()} "
          f"(display at {display_scale():.2f}x)", file=sys.stderr)
    seed_analysis()
    claim_single_instance()
    # Only after claiming: before it, the flag on disk may still be the one
    # aimed at the instance we just displaced.
    watch_for_quit_request()
    session = PartySession()
    ui_state = GameUIState()
    world = WorldSnapshot()
    rift_rec = RiftRecorder()
    # Outlives every encounter on purpose: a skill's heal size is a property of
    # the build, not of the pull, and resetting it each fight would throw away
    # exactly the observations that make the first heals of the next one
    # countable.
    heal_sizer = HealSizeEstimator(_heal_specs())

    # Up before anything that can block. Attaching waits for the game to launch
    # and the hook's memory scan can run for minutes on a slow machine — with no
    # console, an icon that only appeared afterwards would leave the user
    # staring at nothing, with Task Manager as their only way to change their
    # mind. Its quit callback works throughout, overlay or not.
    tray = TrayIcon(request_stop)
    tray.start()
    try:
        return _run(tray, session, ui_state, world, rift_rec, heal_sizer)
    finally:
        tray.stop()
        # Here as well as on the paths inside _run, which miss the early
        # returns — stopping while still waiting for the game would otherwise
        # leave our pid sitting in the lock file. Unlinking twice is harmless.
        release_instance_lock()


# Frida 17.19.0 crashes whatever process it leaves: attach then detach, no
# script at all, and the target dies with 0xC0000005 (measured 2026-09-28 on
# Windows 11 build 26200, against 16.7.19 / 17.2.17 / 17.10.1 / 17.18.0 that
# all leave it running). With the game, that is closing Farever France closing
# Farever. The meter refuses to attach with it.
FRIDA_CRASHING_VERSIONS = {"17.19.0"}
FRIDA_GOOD_VERSION = "17.18.0"

# How long the game's threads get to leave our hooks' trampolines, between
# the hooks coming off and the agent being unloaded. The hooked functions are
# short (a health write, a damage event); a second is ample.
HOOK_DRAIN_SECS = 1.0


def _unload_hook(script):
    """Take the hook out of a running game without crashing it: hooks off
    and timers stopped first (the script's shutdown()), a pause for the
    game's threads to leave them, then the unload. Unloading straight away
    crashed the game (see the note at the top of meter_hook.js)."""
    try:
        exports = getattr(script, "exports_sync", None) or script.exports
        exports.shutdown()
        time.sleep(HOOK_DRAIN_SECS)
    except Exception as e:
        print(f"[meter] hook shutdown call failed ({e}); unloading anyway",
              file=sys.stderr)
    try:
        script.unload()
    except Exception:
        pass


def _game_session(link, device, proc, session, ui_state, world, rift_rec,
                  heal_sizer):
    """One connection to one running Farever: check the data files, attach,
    bring the hook up, then feed its messages to the meter until the game
    closes or the meter stops. Runs on GameLink's thread; the overlay already
    exists and is told about each step through GameLink's state."""
    pid = proc.pid

    # Match the data files to the build that is ACTUALLY RUNNING before
    # hooking: hlboot.dat is taken from the attached process's own install
    # directory, so a version/install mismatch — the usual cause of a slow or
    # failed table search — is impossible. Skipped when the file is unchanged.
    link.step("data", "run", "comparaison avec la version installée")
    hlboot = locate_hlboot(pid)
    if hlboot is None:
        print("[meter] using the shipped data files as-is (couldn't locate "
              "hlboot.dat to verify them).", file=sys.stderr)
    else:
        print(f"[*] game data: {hlboot}", file=sys.stderr)
        # best-effort; falls back to existing files
        regenerate_data(hlboot, on_step=lambda t: link.step(
            "data", detail=f"mise à jour du jeu détectée : relecture ({t})"))
    link.step("data", "ok", "à jour")

    print(f"[*] attaching to {TARGET_PROCESS} (pid {pid}) ...", file=sys.stderr)
    link.step("attach", "run", "recherche du jeu en cours d'utilisation")
    try:
        fsession = device.attach(pid)
    except frida.ProcessNotFoundError:
        print(f"[meter] {TARGET_PROCESS} (pid {pid}) closed before attach.",
              file=sys.stderr)
        return False                # back to waiting for the game
    except frida.PermissionDeniedError:
        link.set_state(GameLink.FAILED,
                       "connexion refusée — si Farever tourne en "
                       "administrateur, lance aussi le compteur en "
                       "administrateur")
        return False
    except Exception as e:
        print(f"[meter] attach to pid {pid} failed: {e}", file=sys.stderr)
        # A game that is shutting down is still listed for a few seconds, and
        # Windows refuses to start a thread in it (STATUS_PROCESS_IS_TERMINATING,
        # 0xc000010a). Only called "closing" if it really is gone shortly after:
        # a live game refusing the attach is a failure, and must stay one.
        for _ in range(8):
            if not link.game_alive(device, pid):
                print(f"[meter] {TARGET_PROCESS} (pid {pid}) closed — back to "
                      "waiting for it.", file=sys.stderr)
                return False
            if STOP.wait(0.5):
                return False
        link.set_state(GameLink.FAILED, f"connexion impossible : {e}")
        return False

    # Set when the frida session dies — in practice, the game closed or
    # crashed. Fires on frida's own thread; the loop at the end waits on it.
    detached = threading.Event()

    def on_detached(*args):
        reason = str(args[0]) if args else ""
        print(f"[meter] game session detached ({reason or 'unknown'}).",
              file=sys.stderr)
        detached.set()
    fsession.on("detached", on_detached)
    link.step("attach", "ok", "")

    ready = {"ok": None, "early": False}
    ready_evt = threading.Event()
    liveness = {"t": time.monotonic(), "printed": 0.0}
    hero_id = {"name": None}           # last local hero, to keep the log quiet
    zone_seen = [False]                # the first zone report came in
    nullified: dict = {}               # mitigated-hit shapes seen, see below
    nullified_at = [0.0]               # last time they were reported
    boss_fight_on = [False]            # a boss fight is under way, see below
    # The fight clock: armed on the pull edge, read when the last bar goes
    # down killed. It inherits the pull edge's known cost — walk away and
    # re-pull without a loading screen and the clock keeps the first pull's
    # start — because a fight that never formally ended was never re-timed
    # either. `kinds` is what keys the record; see _record_boss_kill.
    boss_clock = {"t0": None, "kinds": ()}
    # (pet, owner) pairs already reported. Summon damage merges silently into
    # the owner's row, which means a regression here is invisible — the number
    # is just quietly ~13% low, exactly as it was before 3.3.4. This is the
    # only place that says out loud that pet attribution is working, and on
    # which summons. See "Summon and pet damage".
    pet_seen: set = set()
    # Unit kinds seen as the TARGET of a hit. Combat history names a dataset
    # after whichever of these took the most damage, and if the read were
    # silently returning nothing the only symptom would be datasets named for
    # their zone alone — which looks like a design choice, not a break. These
    # lines are what make that measurable from ordinary play. Capped so a busy
    # world zone reports its bestiary once and then goes quiet.
    target_seen: set = set()
    TARGET_LOG_MAX = 40

    heal_log_at = [0.0]
    dungeon = DungeonTracker(world)

    def on_message(message, data):
        liveness["t"] = time.monotonic()   # any agent traffic counts as alive
        if message["type"] == "error":
            print("[JS]", message.get("description"), file=sys.stderr)
            return
        p = message.get("payload") or {}
        k = p.get("kind")
        if k == "hit":
            # Nullified-hit diagnostic. The hook only attaches these when one of
            # them is set, so an ordinary hit never reaches this branch. Damage
            # against a boss in an immunity phase is currently counted in full;
            # this is here to establish WHICH field marks it before the meter
            # starts discarding hits, because gating on the wrong one would
            # silently drop real damage instead of fake damage.
            # Tallied and reported once every 30s, not per hit: a boss with a
            # long immune phase would otherwise write thousands of lines.
            dropped = False
            if "blocker" in p:
                who = p.get("blocker") or ""
                dropped = who in NULLIFIED_BLOCKERS
                sig = (who, p.get("effect"), (p.get("block") or 0) > 0,
                       (p.get("amount") or 0) > 0, dropped)
                nullified[sig] = nullified.get(sig, 0) + 1
                now_m = time.monotonic()
                if now_m - nullified_at[0] > 30.0:
                    nullified_at[0] = now_m
                    for s, n in sorted(nullified.items(), key=lambda kv: -kv[1]):
                        print(f"[meter] mitigated-hit x{n}: blocker={s[0]!r} "
                              f"effect={s[1]} block>0={s[2]} amount>0={s[3]} "
                              f"{'DROPPED' if s[4] else 'counted'}",
                              file=sys.stderr)
                    nullified.clear()
            # The target never took this, so it is not damage. Dropped before
            # record() rather than subtracted after, so it can't start an
            # encounter or extend one either — whaling on an immune boss is not
            # combat as far as the parse is concerned.
            if p.get("pet"):
                sig = (p["pet"], p.get("player") or "?")
                if sig not in pet_seen:
                    pet_seen.add(sig)
                    print(f"[meter] summon damage: pet={sig[0]!r} "
                          f"credited to {sig[1]!r}", file=sys.stderr)
            tgt = p.get("target")
            if tgt and tgt not in target_seen:
                if len(target_seen) < TARGET_LOG_MAX:
                    print(f"[meter] hit target: {tgt!r} -> "
                          f"{_boss_label(tgt)!r}", file=sys.stderr)
                elif len(target_seen) == TARGET_LOG_MAX:
                    print(f"[meter] ({TARGET_LOG_MAX} distinct hit targets "
                          "named; no longer listing them)", file=sys.stderr)
                target_seen.add(tgt)
            if not dropped:
                session.record(p)
                rift_rec.record("hit", p)
                dungeon.record("hit", p)
        elif k == "heal":
            # The hook reports what LANDED (0 for a heal on a full-health
            # target); this fills in how big the heal itself was, before both
            # aggregators see it, so the two can never disagree.
            heal_sizer.stamp(p)
            session.record_heal(p)
            rift_rec.record("heal", p)
            dungeon.record("heal", p)
            # The formula is checked against ordinary play, not asserted: this
            # says how many heals the cdb table could size and names any skill
            # whose computed size came out below what it measurably restored.
            now_h = time.monotonic()
            if now_h - heal_log_at[0] > 30.0:
                heal_log_at[0] = now_h
                line = heal_sizer.drain_report()
                if line:
                    print(line, file=sys.stderr)
        elif k == "combat":
            session.set_combat(p.get("state") or {})
        elif k == "rift":
            state = bool(p.get("state"))
            ui_state.set_rift(state)
            rift_rec.set_rift(state)
            print(f"[meter] rift: {state}", file=sys.stderr)
        elif k == "bossbar":
            # The game's own boss/elite healthbar went up or down. `n` drives
            # the compass auto-hide; the up/down lists drive the boss-only
            # rules, which is why the hook classifies each unit — an elite
            # raises the same bar and must NOT reset the meter or play a cue.
            ui_state.set_boss_bar(p.get("n") or 0)
            ov = _OVERLAY["ref"]
            # The reset fires on the edge INTO a boss fight, once, and the
            # fight only ends the two ways it ends for the player: the boss
            # died (the same signal the victory cue rides on, below), or a
            # loading screen took them out of the instance (the zone handler).
            #
            # What decidedly does NOT end it is "no boss bar is up right now".
            # That was the previous rule and the Nightqueen breaks it: she
            # replaces herself with copies, and between the old bar dropping
            # and the new ones rising there is at least one poll seeing zero
            # boss bars. That read as the fight ending, so the copies' bars
            # read as a fresh pull and wiped the meter repeatedly through a
            # single fight.
            #
            # The cost of the stricter rule: walking away from a boss and
            # re-pulling it, without dying and without a loading screen, will
            # not reset again — the fight never formally ended. A wipe usually
            # brings a loading screen with it, which does re-arm.
            now_boss = bool(p.get("boss"))
            if now_boss and not boss_fight_on[0]:
                boss_fight_on[0] = True
                kinds = [b.get("kind") for b in (p.get("up") or [])
                         if b.get("boss")]
                boss_clock["t0"] = time.monotonic()
                boss_clock["kinds"] = tuple(sorted(k for k in kinds if k))
                print(f"[meter] boss fight started: "
                      f"{', '.join(k for k in kinds if k) or '?'}",
                      file=sys.stderr)
                # The rift report's phase boundary — the same edge, whether or
                # not the auto-reset setting below is on. A no-op outside a
                # rift, or on a second pull edge in one.
                rift_rec.on_boss_pull()
                if ov is not None and ov.auto_reset_boss():
                    # Not a plain reset: the bar is refreshed on a timer, so
                    # this arrives after the opening burst has already landed.
                    # Carry the last few seconds forward or the reset eats it.
                    kept = session.reset_keeping_recent()
                    print(f"[meter] meter reset for the pull "
                          f"(kept {kept} event{'' if kept == 1 else 's'} from "
                          f"the last {BOSS_PULL_BACKLAG_SECS:.0f}s)",
                          file=sys.stderr)
            for b in (p.get("down") or []):
                # `killed` is decided in the hook from the last health seen
                # while the bar was up — a bar that drops because the player
                # walked away and the boss reset is not a kill, and measured
                # it is the common case.
                if b.get("boss") and b.get("killed"):
                    print(f"[meter] boss killed: {b.get('kind')}",
                          file=sys.stderr)
                    # A kill ends the fight and re-arms the reset — but ONLY
                    # if it took the last boss bar with it. The hook keys bars
                    # by unit pointer and caches the boss/elite classification
                    # by KIND, so every copy the Nightqueen spawns is its own
                    # bar carrying boss=true, and a copy dying reports exactly
                    # the same {boss, killed} as she does. Re-arming on that
                    # would unlatch mid-fight and let the surviving copies'
                    # bars read as a fresh pull on the very next poll — the
                    # reported bug, straight back through a different door.
                    # `boss` is computed over the bar set as it stands AFTER
                    # this removal, so "no boss left" is exactly what it says.
                    if not now_boss:
                        boss_fight_on[0] = False
                        print("[meter] last boss bar down — pull reset "
                              "re-armed", file=sys.stderr)
                        # ...and the fight clock has its reading. Keyed by the
                        # pull's kinds; the killed bar's kind is the fallback
                        # for a pull whose bars arrived nameless.
                        t0 = boss_clock["t0"]
                        boss_clock["t0"] = None
                        if t0 is not None and ov is not None:
                            fkinds = boss_clock["kinds"] or (
                                (b.get("kind"),) if b.get("kind") else ())
                            ov.on_boss_timed_kill(fkinds,
                                                  time.monotonic() - t0)
                        # The fight is formally over — if a rift was recording,
                        # this is its ending, and the report goes up on the
                        # same signal the victory cue rides. Guarded by the
                        # recorder itself: None outside a rift.
                        report = rift_rec.on_boss_kill()
                        # Classes are stamped in HERE rather than looked up
                        # when the card draws: a report outlives its session
                        # (it is saved to disk and re-opened days later, when
                        # the world sweep no longer knows who these people
                        # were), so the acronym has to be frozen with the rest
                        # of the numbers.
                        if report is not None:
                            _stamp_report_classes(report, world)
                        if report is not None and ov is not None:
                            print("[meter] rift complete — showing the "
                                  "end-of-rift report", file=sys.stderr)
                            ov.show_rift_report(report)
        elif k == "bossgone":
            # Every boss bar has been down for ~5 seconds. The hook reports
            # the observation and this decides what it meant: a kill has
            # already cleared boss_fight_on, so a fight STILL latched here is
            # one that ended without one — the boss reset and healed up, or
            # the group wiped without a loading screen to notice it by.
            #
            # (A wipe usually does bring a loading screen, and the zone
            # handler catches that one. This is the case it can't see: nobody
            # died, the boss just de-aggroed and went home.)
            if boss_fight_on[0]:
                boss_fight_on[0] = False
                boss_clock["t0"] = None    # an abandoned fight is not a time
                print("[meter] boss fight ended without a kill (no boss bar "
                      f"for {p.get('polls', 0)} polls) — pull reset re-armed",
                      file=sys.stderr)
                # Same setting, same reasoning as the pull reset: if you want
                # the next attempt measured on its own, you want the failed
                # one cleared off the meter too. Nothing is lost by it — the
                # numbers stay on screen until the re-pull lands its first
                # hit (see _hold_last), and combat history has already
                # archived the attempt if it is on.
                if ov is not None and ov.auto_reset_boss():
                    session.reset()
                    ov.on_boss_giveup()
        elif k == "zone":
            # The first report after attach says where we already are — it
            # keys the map background but is not a loading screen, so nothing
            # resets on it.
            # The sidecar fields ride along so their real meaning accumulates
            # from normal play — `name`, `branchName` and `_isWorldMap` were
            # emitted unmeasured, and this line is how they get measured.
            extra = ", ".join(f"{k}={p.get(k)!r}"
                              for k in ("name", "branch", "world_map")
                              if p.get(k) is not None)
            if p.get("initial"):
                zone_seen[0] = True
                link.step("zone", "ok", _zone_label(
                    str(p.get("sig") or "").split("/")[-1]))
                ui_state.set_zone(p.get("sig"), p.get("world_map"))
                print(f"[meter] zone identified ({p.get('sig')!r}"
                      + (f"; {extra}" if extra else "") + ")",
                      file=sys.stderr)
                return
            ui_state.clear()      # the UI is rebuilt across a loading screen
            # Reset BEFORE the new zone is recorded. The encounter being
            # thrown away happened in the zone we are LEAVING, and it is
            # archived under the zone the ui_state currently names — set the
            # new one first and every dataset would be filed under wherever
            # the loading screen dropped you.
            session.reset()
            ui_state.set_zone(p.get("sig"), p.get("world_map"))
            # A loading screen mid-rift is a wipe or a walk-out, not a finished
            # run — the recording is abandoned, not reported.
            rift_rec.on_zone()
            # The other way a boss fight ends. Nothing else re-arms the pull
            # reset now that a dropped bar doesn't, so leaving an instance
            # mid-fight has to — otherwise a fight abandoned rather than won
            # would suppress the reset for the rest of the session.
            boss_fight_on[0] = False
            boss_clock["t0"] = None    # an abandoned fight is not a time
            print(f"[meter] zone change ({p.get('sig')!r}"
                  + (f"; {extra}" if extra else "") + ") — meter reset",
                  file=sys.stderr)
        elif k == "server":

            # Which shard. Sent on its own rather than folded into `zone`
            # because the two move independently — a relog can land you on a
            # different shard in the same zone, which is exactly how this was
            # measured (Sfojuxa3386_6601_na -> Snitura2642_6306_na, zone
            # unchanged). Nothing resets on it: a shard change without a
            # loading screen is not a thing, and when there IS one the zone
            # handler has already done the resetting.
            ui_state.set_server(p.get("name"))
            if p.get("initial"):
                link.step("zone", detail=" · ".join(
                    x for x in (link.steps_detail("zone"),
                                f"serveur {p.get('name')}") if x))
            print(f"[meter] shard {'identified' if p.get('initial') else 'change'}"
                  f" ({p.get('name')!r})", file=sys.stderr)
        elif k == "hero":
            # The hook re-reports the local hero every 3s so it survives a
            # respawn or zone change, so only the first identification and a
            # genuine change are worth a line. The name itself is tracked but
            # never printed — the console is often on screen next to the game,
            # and the overlay's own `*` row already says which player is you.
            name = p.get("name")
            # The roster rides along on this message; the minimap needs it to
            # ring group members, including ones who haven't fought yet.
            world.set_hero(name, p.get("party"))
            if name and name != hero_id["name"]:
                first = hero_id["name"] is None
                link.step("hero", "ok", name)
                hero_id["name"] = name
                print("[meter] local hero "
                      + ("identified." if first else "changed."), file=sys.stderr)
        elif k == "pickup":
            dungeon.pickup(p)
        elif k == "collection":
            ov = _OVERLAY["ref"]
            if ov is not None:
                ov.on_collection(p)
        elif k == "codex":
            ov = _OVERLAY["ref"]
            if ov is not None:
                ov.on_codex(p)
        elif k == "itemcodex":
            ov = _OVERLAY["ref"]
            if ov is not None:
                ov.on_item_codex(p)
        elif k == "achievements":
            ov = _OVERLAY["ref"]
            if ov is not None:
                ov.on_achievements(p)
        elif k in ("roster", "profile", "selfprofile"):
            ov = _OVERLAY["ref"]
            if ov is not None:
                ov.on_character(p)
        elif k == "elements":
            ov = _OVERLAY["ref"]
            if ov is not None:
                ov.on_elements(p)
        elif k == "dungeon":
            # The running activity and, in a dungeon, its state — see
            # DungeonTracker for what each field was measured to mean.
            dungeon.update(p.get("d") or {})
        elif k == "shard":
            # Every player on the layer, with their class — which is where the
            # meter's class tags come from. Sent only when the roster changed.
            world.set_shard(p.get("list") or [])
        elif k == "log":
            print("[hook]", p.get("msg"), file=sys.stderr)
            msg = str(p.get("msg") or "")
            if "functions_ptrs via" in msg:
                link.step("scan", "ok", "fonctions trouvées")
                link.step("hook", "run", "mise en place des modules")
            elif "memory scan" in msg:
                link.step("scan", detail="balayage de la mémoire du jeu")
        elif k == "progress":
            if p.get("total"):
                link.step("scan", detail=f"balayage de la mémoire : "
                          f"{p.get('done')} / {p.get('total')} régions")
            now = time.monotonic()
            if now - liveness["printed"] > 5.0:     # throttle the status line
                liveness["printed"] = now
                print(f"[meter] hook scanning memory ... "
                      f"({p.get('done')}/{p.get('total')} regions)",
                      file=sys.stderr)
        elif k == "ready":
            if p.get("ok"):
                link.step("scan", "ok", "fonctions trouvées")
                link.step("hook", "ok", "")
            ready["ok"] = p.get("ok")
            ready["early"] = bool(p.get("early"))
            print(f"[meter] hook ready ok={p.get('ok')}"
                  + (" (game still booting)" if ready["early"] else ""),
                  file=sys.stderr)
            ready_evt.set()

    def load_hook():
        sc = fsession.create_script(build_script_source())
        sc.on("message", on_message)
        sc.load()   # returns promptly; the hook sets up asynchronously
        return sc

    def wait_ready(max_total=240.0, idle_grace=30.0):
        """Wait for the hook's ready message. The scan can legitimately take
        minutes on a slow/loaded machine, and unloading a live scan restarts it
        from zero — so as long as the agent keeps talking (progress heartbeats),
        keep waiting. Give up only when it goes silent or hits the hard cap."""
        start = time.monotonic()
        while True:
            if ready_evt.wait(timeout=0.5):
                return True
            if STOP.is_set() or detached.is_set():
                return False        # asked to quit, or the game went, mid-scan
            now = time.monotonic()
            if now - start > max_total:
                print("[meter] hook scan exceeded the time cap.", file=sys.stderr)
                return False
            if now - liveness["t"] > idle_grace:
                print("[meter] hook went silent — treating it as dead.",
                      file=sys.stderr)
                return False

    # Bring the hook up with clean, bounded retries. The scan runs async in the
    # agent, so a genuinely dead init times out here — we unload cleanly and
    # retry rather than leaving a half-attached agent (which is what destabilises
    # the game when people force-kill and relaunch repeatedly).
    script = None
    attempt = 0
    booting_until = time.monotonic() + 120.0
    while attempt < 3:
        attempt += 1
        if STOP.is_set() or detached.is_set():
            break
        ready["ok"] = None
        ready["early"] = False
        ready_evt.clear()
        link.step("scan", "run", "recherche de la table des fonctions"
                  + (f" — tentative {attempt}/3" if attempt > 1 else ""))
        link.step("hook", "wait", "")
        liveness["t"] = time.monotonic()
        try:
            script = load_hook()
        except Exception as e:
            print(f"[meter] load attempt {attempt} failed: {e}", file=sys.stderr)
            script = None
        if script is not None and wait_ready() and ready["ok"]:
            break
        if ready["early"] and time.monotonic() < booting_until:
            # Not a failure: the game hasn't finished booting. Unload, give
            # it a few seconds, and try again without using up an attempt.
            _unload_hook(script)
            script = None
            attempt -= 1
            link.step("scan", detail="le jeu n'a pas fini de charger — "
                                     "nouvel essai dans 4 s")
            if STOP.wait(4.0):
                break
            continue
        print(f"[meter] hook didn't come up (attempt {attempt}/3); "
              "cleaning up and retrying ...", file=sys.stderr)
        if script is not None:
            _unload_hook(script)
            script = None
        if ready["ok"] is False and not ready["early"]:   # table not found
            link.step("scan", detail="introuvable — relecture des données "
                                     "du jeu puis nouvel essai")
            regenerate_data(hlboot, force=True)   # => refresh data and retry
        time.sleep(1.0)

    if STOP.is_set() or detached.is_set():
        # Stopped from the tray during startup, or the game closed under us.
        # The hook may be half-loaded, and leaving it attached is what
        # destabilises the game.
        print("[meter] connection abandoned during startup.", file=sys.stderr)
        if script is not None and not detached.is_set():
            _unload_hook(script)
        try:
            fsession.detach()
        except Exception:
            pass
        return

    if script is None or ready["ok"] is not True:
        print("[meter] could not initialise the hook after 3 attempts.\n"
              "        Fully close Farever and reopen it; the meter reconnects "
              "on its own.\n"
              "        (Avoid repeatedly relaunching against a stuck session — "
              "that can crash the game.)", file=sys.stderr)
        try:
            fsession.detach()
        except Exception:
            pass
        link.step("scan", "fail", "échec après 3 tentatives")
        link.set_state(GameLink.FAILED,
                       "le compteur n'a pas pu se brancher sur le jeu — ferme "
                       "complètement Farever et relance-le")
        return False

    # Connected. From here the hook feeds on_message until the game closes.
    link.script = script
    link.step("hook", "ok", "")
    if hero_id["name"] is None:
        link.step("hero", "run", "en attente de ton personnage en jeu")
    if not zone_seen[0]:
        link.step("zone", "run", "en attente")
    link.set_state(GameLink.CONNECTED, pid=pid)
    print("[*] connected — everything shows in the Farever France window; the "
          "reset hotkey is set in Réglages.", file=sys.stderr)
    try:
        while (not STOP.is_set() and not detached.wait(0.5)
               and not link._reconnect.is_set()):
            pass
    finally:
        link.script = None
        if not detached.is_set():
            # We are the ones leaving (the meter is stopping): unload the hook
            # and detach properly — never leave a half-attached agent behind.
            _unload_hook(script)
            try:
                fsession.detach()
            except Exception:
                pass
        # The overlay forgets what the hook was telling it (open game windows,
        # rift, combat state) — none of it is true any more.
        ov = _OVERLAY["ref"]
        if ov is not None:
            ov.on_game_disconnected()
        # A dungeon run cut short by the game closing is kept as abandoned.
        dungeon.disconnect()
        # A rift that was running when the game closed is over.
        ui_state.set_rift(False)
        rift_rec.set_rift(False)
        session.set_combat({})
    return True                     # we were connected, and now we are not


class GameLink:
    """The meter's connection to Farever, kept alive in the background.

    The interface no longer waits for the game: it opens straight away, and
    this thread watches for Farever to start, connects to it, and goes back to
    watching once it closes — so the meter can stay open across game sessions,
    and everything it saved is readable without the game.

    State is read by the overlay (the status light) and changed only here."""

    CLOSED, CONNECTING, CONNECTED, FAILED = (
        "closed", "connecting", "connected", "failed")
    # The connection, step by step, for the window the game state opens:
    # what is being done right now, and since when.
    STEPS = (("boot", "Démarrage du jeu"),
             ("data", "Vérification des données du jeu"),
             ("attach", "Connexion à Farever"),
             ("scan", "Recherche des fonctions du jeu"),
             ("hook", "Installation des modules"),
             ("hero", "Identification du personnage"),
             ("zone", "Zone et serveur"))
    POLL_SECS = 2.0          # how often a closed game is looked for

    def __init__(self, session, ui_state, world, rift_rec, heal_sizer):
        self._args = (session, ui_state, world, rift_rec, heal_sizer)
        self._lock = threading.Lock()
        self._state, self._detail, self._pid = self.CLOSED, "", None
        self._retry = threading.Event()
        self._reconnect = threading.Event()
        self._steps = {}
        self._thread = None
        self.script = None
        # The game we were last connected to. Once it closes it stays in the
        # process list for a few seconds while it shuts down; it must not be
        # mistaken for the game starting again.
        self._gone_pid = None

    # -- read by the overlay ---------------------------------------------
    def status(self):
        with self._lock:
            return self._state, self._detail, self._pid

    def retry(self):
        """Look for the game / reconnect now, rather than at the next poll."""
        self._retry.set()

    def steps_reset(self):
        with self._lock:
            self._steps = {}

    def step(self, key, state=None, detail=None):
        """One step's state ("run", "ok", "fail") and/or what it is doing.
        A step that starts running is timed from then."""
        now = time.time()
        with self._lock:
            st = self._steps.setdefault(key, {"state": "wait", "detail": "",
                                              "t0": None, "t1": None})
            if state and state != st["state"]:
                if state == "run":
                    st["t0"], st["t1"] = now, None
                elif st["t0"] is None:
                    st["t0"] = st["t1"] = now
                else:
                    st["t1"] = now
                st["state"] = state
            if detail is not None:
                st["detail"] = detail
        ov = _OVERLAY["ref"]
        if ov is not None:
            ov.on_link_steps()

    def steps_detail(self, key):
        with self._lock:
            return (self._steps.get(key) or {}).get("detail") or ""

    def steps_view(self):
        now = time.time()
        with self._lock:
            out = []
            for key, label in self.STEPS:
                st = self._steps.get(key) or {}
                t0, t1 = st.get("t0"), st.get("t1")
                secs = ((t1 or now) - t0) if t0 else None
                out.append({"t": label, "s": st.get("state") or "wait",
                            "d": st.get("detail") or "",
                            "secs": _mmss(secs) if secs is not None
                            and secs >= 1 else ""})
            return out

    def reconnect(self):
        """Unload the hook and attach again (Réparer: the data it was built
        from has changed). Waiting or failed: just look for the game now."""
        self._reconnect.set()
        self._retry.set()


    # -- lifecycle --------------------------------------------------------
    def set_state(self, state, detail="", pid=None):
        with self._lock:
            self._state, self._detail = state, detail
            self._pid = pid if state == self.CONNECTED else None
        print(f"[meter] game link: {state}"
              + (f" ({detail})" if detail else ""), file=sys.stderr)
        ov = _OVERLAY["ref"]
        if ov is not None:
            ov.on_link_changed()

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="game-link")
        self._thread.start()

    def stop(self, timeout=20.0):
        """Called after STOP is set. Waits for the thread to unload the hook
        and detach — the part that must not be cut short."""
        self._retry.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self):
        bad = getattr(frida, "__version__", "")
        if bad in FRIDA_CRASHING_VERSIONS:
            print(f"[meter] frida {bad} crashes the game when it detaches "
                  f"(measured 2026-09-28) — not attaching. Install "
                  f"{FRIDA_GOOD_VERSION}: py -m pip install "
                  f"frida=={FRIDA_GOOD_VERSION}", file=sys.stderr)
            self.set_state(self.FAILED,
                           f"Frida {bad} ferait planter le jeu à la fermeture "
                           f"de Farever France. Installe la {FRIDA_GOOD_VERSION} : "
                           f"py -m pip install frida=={FRIDA_GOOD_VERSION}")
            return
        try:
            device = frida.get_local_device()
        except Exception as e:
            self.set_state(self.FAILED, f"frida indisponible : {e}")
            return
        while not STOP.is_set():
            self.set_state(self.CLOSED)
            proc = self._wait_for_game(device)
            if proc is None:
                return
            self._reconnect.clear()
            self.steps_reset()
            self.set_state(self.CONNECTING)
            if not self._wait_booted(device, proc.pid):
                continue                    # closed while starting, or STOP
            try:
                # Only a game we were actually connected to is set aside once
                # it closes — never one whose attach merely failed.
                if (_game_session(self, device, proc, *self._args)
                        and not self._reconnect.is_set()):
                    self._gone_pid = proc.pid
                self._reconnect.clear()
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.set_state(self.FAILED, f"erreur inattendue : {e}")
            if STOP.is_set():
                return
            if self.status()[0] == self.FAILED:
                # Not straight back at the same process: hammering a stuck
                # game with attaches is what crashes it. Wait for it to close,
                # or for a click on the status light.
                self._wait_failed(device, proc.pid)

    def _game_procs(self, device):
        try:
            return [p for p in device.enumerate_processes()
                    if p.name.lower() == TARGET_PROCESS.lower()]
        except Exception:
            return []

    def game_alive(self, device, pid):
        return any(p.pid == pid for p in self._game_procs(device))

    def _wait_for_game(self, device):
        """The Farever process, once there is one; None if the meter stops."""
        while not STOP.is_set():
            procs = [p for p in self._game_procs(device)
                     if p.pid != self._gone_pid]
            if self._gone_pid is not None and not self.game_alive(
                    device, self._gone_pid):
                self._gone_pid = None       # it has finished closing
            if procs:
                if len(procs) > 1:
                    print(f"[meter] {len(procs)} copies of {TARGET_PROCESS} "
                          f"are running — using pid {procs[0].pid}.",
                          file=sys.stderr)
                return procs[0]
            self._retry.wait(self.POLL_SECS)
            self._retry.clear()
        return None

    # A game that has just started is not hooked straight away: its code and
    # tables only exist once it has finished booting, and a hook brought up
    # before that finds nothing — three times over, and the link gives up.
    # That was the "second launch never connects" bug (2026-10-01): with the
    # data already checked, the meter attached the instant Farever.exe
    # appeared. Hooked once it shows its window and has run a little while.
    BOOT_MIN_SECS = 12.0
    BOOT_MAX_SECS = 90.0

    def _wait_booted(self, device, pid):
        """True once the game looks booted; False if it closes or we stop."""
        said = False
        self.step("boot", "run", "Farever est lancé")
        while not STOP.is_set():
            age = _process_age(pid)
            if age is None or age >= self.BOOT_MAX_SECS:
                self.step("boot", "ok", "")
                return True
            if age >= self.BOOT_MIN_SECS and _window_rect_of_pid(pid):
                self.step("boot", "ok", "")
                return True
            self.step(detail=("le jeu finit de démarrer"
                              if _window_rect_of_pid(pid)
                              else "en attente de la fenêtre du jeu"),
                      key="boot")
            if not self.game_alive(device, pid):
                return False
            if not said:
                said = True
                print(f"[meter] {TARGET_PROCESS} (pid {pid}) is starting "
                      f"({age:.0f}s old) — waiting for it to finish booting.",
                      file=sys.stderr)
            STOP.wait(1.0)
        return False

    def _wait_failed(self, device, pid):
        while not STOP.is_set():
            if self._retry.wait(self.POLL_SECS):
                self._retry.clear()
                return
            if not any(p.pid == pid for p in self._game_procs(device)):
                return


def _run(tray, session, ui_state, world, rift_rec, heal_sizer):
    """The interface first, the game whenever it turns up."""
    link = GameLink(session, ui_state, world, rift_rec, heal_sizer)
    overlay = App(session, ui_state, world, link=link)
    # From here the overlay owns shutdown: it's the only thing that can return
    # from the mainloop and let the finally below unload the hook and detach.
    _OVERLAY["ref"] = overlay
    link.start()
    if STOP.is_set():
        overlay.request_quit()
    try:
        overlay.run()
    finally:
        _OVERLAY["ref"] = None
        try:
            # End the settings panel's process. It is already off the screen —
            # _quit hides it along with every Tk window before the mainloop
            # breaks — so this is just the process going away.
            overlay.menubridge.stop()
        except Exception:
            pass
        STOP.set()
        link.stop()                 # unloads the hook and detaches
        release_instance_lock()


def _cli():
    # Tool mode first: this is the frozen build standing in for python.exe to
    # run one of the bundled hltools generators, and it must not start a meter.
    if len(sys.argv) > 2 and sys.argv[1] == TOOL_FLAG:
        run_bundled_tool(sys.argv[2], sys.argv[3:])
        return
    # ...and the settings panel, for the same reason: frozen, there is no
    # python.exe to launch menu_host.py with, so the exe re-enters itself.
    # Before setup_logging(), because the panel logs through the meter's
    # stderr, which is the pipe its parent is already reading.
    if len(sys.argv) > 1 and sys.argv[1] == MENU_FLAG:
        sys.argv = sys.argv[1:]         # menu_host reads its geometry as [1]
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import menu_host
        menu_host.main()
        return
    setup_logging()
    try:
        main()
    except KeyboardInterrupt:
        # Ctrl+C is a documented way to stop a from-source run, so it shouldn't
        # look like a crash: main()'s finally has already unloaded the hook and
        # detached by the time this runs. Printing (and exiting 0) also lets a
        # launcher tell a normal stop from a real failure.
        print("[meter] stopped.", file=sys.stderr)
    except SystemExit as e:
        # sys.exit() carries the startup failures — messages written for a
        # console that the windowed build doesn't have. Put them on screen
        # instead of exiting silently, which would look like nothing happened.
        if not HAS_CONSOLE and e.code not in (0, None):
            print(f"[meter] {e.code}", file=sys.stderr)
            message_box(e.code, "Farever France — démarrage impossible", 0x10)
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        if not HAS_CONSOLE:
            message_box(
                "Le compteur a rencontré une erreur inattendue et s'est "
                "arrêté.\n\n"
                f"Le détail est dans :\n{LOG_FILE}",
                "Farever France — erreur", 0x10)
        raise


if __name__ == "__main__":
    _cli()
