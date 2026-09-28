"""
farever_meter.py — Farever+ party damage meter (memory-reading edition).

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
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import zlib
import tkinter as tk
from ctypes import wintypes
from tkinter import font as tkfont
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
# Every encounter the meter has finished, as data. Its own folder rather than
# a corner of parses/: parses/ holds things you MADE to share (a screenshot, a
# report card), and this holds what the meter recorded whether you asked for
# it or not. Nothing in here is ever deleted by the meter — see HistoryStore.
HISTORY_DIR = _WRITABLE / "history"
LOG_FILE = DATA_HOME / "meter.log"
TARGET_PROCESS = "Farever.exe"

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
# The input pump's tick — how long the overlay can take to notice you opened
# the game's escape menu, clicked a menu button, or freed the cursor. Separate
# from REFRESH_MS because they answer different questions: 250 ms is plenty
# often to redraw damage numbers, and far too slow to feel like a keypress.
# ~30 fps costs two user32 calls and a queue drain per tick.
UI_TICK_MS = 33
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
# How long the panel's geometry has to hold still before it is written to disk.
# A drag reports every step, and each write is a file rewrite — this turns a
# whole gesture into one save, shortly after the user lets go.
PANEL_GEOM_SETTLE_SECS = 0.6
# How long to let the foreground change settle before replaying an Escape at
# the game. Long enough that SetForegroundWindow has taken effect, short enough
# that the key still feels like the one you pressed.
PANEL_ESC_REPLAY_MS = 60
MAX_PLAYER_ROWS = 8
MAX_SKILL_ROWS = 8
# The breakdown's summary sidebar. Seven is the most it can currently show
# (dmg, dps, hits, crit, heal, overheal, kills); the pool is fixed so pack
# order stays stable and nothing is created on a 250 ms tick.
MAX_STAT_ROWS = 7

# Game windows whose presence unlocks the overlay. The game already frees the
# mouse cursor for these, so grabbing it costs nothing and the overlay becomes
# draggable/clickable exactly when the player is in "UI mode" — no hotkey.
UNLOCK_ON_WINDOWS = ("ui.win.EscapeMenu",)

# Any OTHER game window (inventory, map, vendor, ...) hides the overlay while
# it's up: those screens are what the player is reading, and a meter floating
# over them is just clutter. The escape menu is excluded because that's the
# overlay's own unlock/settings moment — it has to stay visible then.
#
# The hook's window feed names every ui.win.* class it sees (each one is logged
# as "[meter] game window ..."), so if some always-on HUD class turns out to be
# reported as open, add it here and the overlay stops treating it as a menu.
MENU_IGNORE_WINDOWS = frozenset(UNLOCK_ON_WINDOWS)

# Grace period for "hide out of combat": the game's isInCombat flag drops
# between pulls, so hiding the instant it clears would make the overlay flicker
# through a trash pack.
HIDE_OOC_LINGER_SECS = 5.0

# Show/hide is a fade rather than a pop — a window blinking out of existence
# mid-fight reads as a crash. FADE_SECS is the full 0 -> OVERLAY_ALPHA travel;
# the driver ticks every FADE_STEP_MS on its own timer, not on the 250 ms
# refresh, which would be far too coarse to look like a fade.
# Vertical stack for the floating text over the top of the game window. The
# game draws the zone name across the very top, so everything starts below it —
# the keybind hint used to sit at +24 and land right on top of it.
TOP_STRIP_HINT = 96
TOP_STRIP_PARSE = 140
TOP_STRIP_RIFT = 190
# The boss kill-time toast, below all three: the hint and the parse banner
# share the strips above, and the rift panel's default sits at 190. A kill can
# coincide with any of them (a parsed boss, a world boss with a countdown up),
# so it gets its own line rather than a timeshare.
TOP_STRIP_KILL = 240
KILL_TOAST_SECS = 8.0       # how long the time stays on screen
# The reset confirmation. Short: it answers a keypress you just made, and a
# banner sitting over your own damage numbers stops being reassuring quickly.
RESET_TOAST_SECS = 2.2
RESET_TOAST_TEXT = ("Réinitialisé",
                    "En attente de nouvelles données de combat")




OVERLAY_ALPHA = 0.94
# Extra see-through on top of that, from the Transparency slider. 0 leaves the
# overlay exactly as it has always looked; the cap stops short of a UI you can
# no longer read, which is a setting people find by dragging and then can't
# find their way back from.
#
# This is WINDOW opacity, which is the only kind Windows gives a layered window
# — so it takes the whole window with it: panel, header bar and text alike.
# There is no way to fade a background out from under its own text here.
TRANSPARENCY_MAX = 80
# The windows it applies to: everything that belongs to the game view. The
# control menu and its hint are exempt because they're what you're reading
# while you drag the slider, the rift prompt because it's a question that
# has to be answered, and the rift report because it's a page of numbers you
# stopped to read — the slider is for the things that sit over the fighting.
TRANSPARENCY_EXEMPT = ("menu", "hint", "prompt", "report")
FADE_SECS = 0.45
# The control menu and its hint don't fade AT ALL. They answer to a keypress,
# and a keypress wants a frame, not an animation: even a fast fade is time
# spent watching a panel arrive that you already asked for. Zero means the
# window is mapped at full opacity immediately — see _want_visible, which
# bypasses the fade driver entirely rather than running a one-step fade (the
# driver only wakes every FADE_STEP_MS, so "instant" through it would still
# cost a tick).
MENU_FADE_SECS = 0.0
# The rift prompt and the report card keep a short fade. They are not answers
# to a keypress — one interrupts you with a question, the other is a page of
# numbers that appears when a fight ends — and something arriving unbidden
# reads better easing in than snapping into existence.
PANEL_FADE_SECS = 0.15
FADE_STEP_MS = 25

# 60s Parse Mode: a fixed-length sample, so two runs are comparable in a way
# "whatever that pull happened to be" never is. The pre-roll exists because the
# button is clicked from the escape menu — you need those seconds to close it
# and get your hands back on the keyboard.
PARSE_PREROLL_SECS = 8
PARSE_LENGTH_SECS = 60

# Overlay elements the control menu can show/hide, as (key, label). This drives
# the menu's SHOW / HIDE section: add a row here and a checkbox appears for it,
# backed by Overlay._show[key].
# A key that names a window in Overlay._element_win is mapped/unmapped wholesale;
# any other key is a content toggle handled inside the render pass.
# Each overlay window is Show / Hide / Show in ESC rather than a tick, so
# "hidden while playing but there when I open the menu" is something you can
# ask for directly. It used to be what an unticked box did, which meant there
# was no way to say "hidden, and I mean it".
ELEMENT_MODES = ("Show", "Hide", "Show in ESC")
ELEMENT_SHOW, ELEMENT_HIDE, ELEMENT_ESC = ELEMENT_MODES
# What the menu shows for each mode. The English values above are what the
# settings file stores, so they stay as they are.
ELEMENT_MODE_LABELS = {"Show": "Afficher", "Hide": "Masquer",
                       "Show in ESC": "Seulement avec Échap"}

TOGGLEABLE_ELEMENTS = (
    ("meter", "Compteur de dégâts"),
    ("detail", "Détail"),
    ("rift", "Minuteur de faille"),
)


def _initial_shown():
    """The live "is this window on screen" map, keyed by WINDOW."""
    return {k: True for k, _ in TOGGLEABLE_ELEMENTS}


def _element_show_key(key):
    """The TOGGLEABLE_ELEMENTS row a window key answers to (the same key)."""
    return key


# ---------------------------------------------------------------------------
# Second-screen mode
# ---------------------------------------------------------------------------
# With it on, these windows stop being an overlay: they get a normal title bar,
# leave "always on top", take clicks, and stay up whatever the game is doing —
# so they can sit on another monitor without ever covering the game. The
# floating bits that only make sense over the action (rift timer and prompt,
# kill / parse / reset banners, the settings hint) stay overlays.
SCREEN2_KEYS = ("meter", "detail", "report")
SCREEN2_TITLES = {"meter": "Farever+ — Compteur",
                  "detail": "Farever+ — Détail",
                  "report": "Farever+ — Rapport de faille"}
# Where a first-time second-screen window lands on the other monitor.
SCREEN2_MARGIN = 40

# Elements the out-of-combat rule doesn't touch. The rift countdown is most use
# exactly when you're standing around between pulls, so hiding it out of combat
# would hide it for its whole useful life.
OOC_EXEMPT = ("rift",)


# The canvas is redrawn at roughly twice the sweep rate. Matching them exactly
# would beat against the hook's timer and drop or double frames; drawing a bit
# faster than the data arrives keeps motion even.



# Short class tags for the meter. The game's own names come off ent.Unit.kind,
# which for a hero is its class rather than a creature id.
CLASS_ABBR = {"Warrior": "Gue", "Mage": "Mag", "Priest": "Prê", "Rogue": "Vol"}


# The meter's name and class columns, in monospace cells. They used to be one
# 17-cell field with the class in brackets after the name; a class of its own is
# both easier to scan down and immune to a long name pushing it about. The two
# still add up to 17, so the DMG column and MIN_W["meter"] are where they were.
METER_NAME_CELLS = 13
METER_CLASS_CELLS = 4


def _class_tag(kind):
    """(War) for Warrior. Anything unrecognised falls back to its first three
    letters rather than disappearing — a new class should look odd, not absent."""
    if not kind:
        return ""
    return CLASS_ABBR.get(kind) or kind[:3].title()






# Rifts open on the hour. The countdown is just the wall clock — reading the
# game's own world-event schedule turned out to report the running event rather
# than the next one, and this needs no hook at all. For the first few minutes
# past the hour the rift that just opened is the current one, so there's nothing
# to count down to yet.
RIFT_QUIET_MINS = 6

# ---- palette (Farever-style, matches original meter) ----
BG_BORDER = "#2C1A0E"
BG_BODY = "#F2E1CB"
BG_BODY_SOFT = "#E8D5B8"
BG_HEADER = "#54A4A9"
BG_HEADER_COMBAT = "#C9612A"
BG_HEADER_UNLOCKED = "#5E9C4A"   # green — the escape menu is open / draggable
BG_BAR_TRACK = "#D9C09A"
FG_HEADER = "#FFFFFF"
FG_HEADER_DIM = "#DCE9EA"   # captions in a header bar — readable on all tints
FG_TEXT = "#3D2817"
FG_VALUE = "#1F1208"
FG_DIM = "#7B5A3A"
FG_WARN = "#A32B1C"         # red that still reads on the cream body

# ---- rift palette (matches the rifts themselves: hot magenta rim, near-black
# maroon interior) — deliberately nothing like the rest of the overlay, so the
# countdown reads as belonging to the game's event rather than to the meter.
RIFT_EDGE = "#FF2E92"
RIFT_GLOW = "#8A1048"
RIFT_BODY = "#2C0A1E"
RIFT_TITLE = "#FF7BC0"
RIFT_TIME = "#FFE0F0"
RIFT_PEAK = "#FFB3D9"       # the top of the pulse, a hotter rim
# The countdown pulses once it's close, so it catches the eye without needing to
# be read. Its own timer, because the 250 ms refresh would make it a stutter.
RIFT_PULSE_SECS = 300       # start pulsing at 5 minutes left
RIFT_STYLE_SECS = 900       # ...and turn the box rift-coloured at 15
RIFT_PULSE_PERIOD = 1.4     # seconds per full pulse
RIFT_PULSE_MS = 40
RIFT_RIPPLE_MARGIN = 26     # transparent room around the panel for it
RIFT_RIPPLE_FADEOUT = 0.8   # ripple is gone by this much of the cycle

# ---- rift report leaderboard ----
# Medal colours for ranks 1-3, tuned to read on the near-black rift body —
# true silver (#C0C0C0) goes muddy there, so it leans bluer and lighter.
REPORT_MEDALS = ("#FFD24A", "#CDD6E0", "#D89B66")
REPORT_HEAL = "#8AE28A"         # the healer's colour, distinct from any medal

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
TRANSPARENT_KEY = "#010101"

# The countdown box escalates as the rift approaches: ordinary Farever colours
# while it's far off, rift colours inside 15 minutes, then pulsing inside 5.
RIFT_BOX_FAR = {"glow": BG_BORDER, "edge": BG_BORDER, "body": BG_BODY,
                "title": ACCENT, "time": FG_VALUE}
RIFT_BOX_NEAR = {"glow": RIFT_GLOW, "edge": RIFT_EDGE, "body": RIFT_BODY,
                 "title": RIFT_TITLE, "time": RIFT_TIME}

# The meter and breakdown re-skin themselves while you're inside a rift, so the
# overlay matches what's on screen around it. Same widget tree either way — only
# the colours are swapped, by _apply_theme.
# Theme choices offered in the control menu. The two Dynamic ones are the
# interesting entries: they follow the game, so the overlay matches whatever
# you're standing in.
#
# Farever and Dark differ only in the MAP PANELS — the minimap's background and
# the compass ink. The meter and breakdown are parchment either way; that's the
# app's face and there's no dark version of it. Farever paints the map to match
# them; Dark leaves it the deep navy the panels have always been.
#
# There is deliberately no "Farever Rift" or "Dark Rift". A rift looks like a
# rift, and inside one both Dynamic modes go there — which is the whole point of
# them. Pinned Rift stays available for anyone who just likes the colours.
#
# Sparkle is the odd one out: the only theme that also changes the TYPEFACE.
# It's named for the sparkling critters the tracker points you at, and it's the
# one skin that isn't trying to look like part of the game — so the soft face
# is the point of it rather than a decoration on top of the colours.
THEME_MODES = ("Farever Dynamic", "Dark Dynamic", "Sparkle Dynamic",
               "Farever", "Dark", "Sparkle", "Rift")
# What a fresh install gets, and where an unrecognised saved value lands. Not
# THEME_MODES[0]: the dark panels are what the meter has always shipped with,
# and a new option shouldn't repaint anybody's overlay on upgrade.
THEME_MODE_DEFAULT = "Dark Dynamic"
# Settings written before the split said "Dynamic", which drew dark panels — so
# it maps to Dark Dynamic, not to the entry that merely has the same first word.
# The old "Farever" and "Rift" keep their names and now mean what they say.
THEME_MODE_ALIASES = {"Dynamic": "Dark Dynamic"}
# What the menu shows for each theme; the keys above are what gets saved.
THEME_MODE_LABELS = {
    "Farever Dynamic": "Farever (dynamique)",
    "Dark Dynamic": "Sombre (dynamique)",
    "Sparkle Dynamic": "Scintillant (dynamique)",
    "Farever": "Farever", "Dark": "Sombre", "Sparkle": "Scintillant",
    "Rift": "Faille",
}

# Every font in the overlay is a *named* Tk font. That's what makes the scale
# slider possible: reconfiguring a named font resizes every widget using it and
# triggers a relayout, with no rebuild and no hunting down font tuples.
FONT_SPECS = {
    "ui_sm_b":    ("Segoe UI", 8, "bold"),
    "ui_b":       ("Segoe UI", 10, "bold"),
    "ui":         ("Segoe UI", 9),
    "ui_10":      ("Segoe UI", 10),
    "ui_tiny_i":  ("Segoe UI", 7, "italic"),
    "ui_lg_b":    ("Segoe UI", 13, "bold"),
    "ui_hint_b":  ("Segoe UI", 11, "bold"),
    "ui_parse_b": ("Segoe UI", 15, "bold"),
    # The rift report's leaderboard: an MVP name is the headline of the card
    # and reads like one; the top-three ranks sit between it and body text.
    "ui_mvp_b":   ("Segoe UI", 17, "bold"),
    "ui_rank_b":  ("Segoe UI", 12, "bold"),
    "ui_idle_i":  ("Segoe UI", 10, "italic"),
    "mono":       ("Consolas", 9),
    "mono_10":    ("Consolas", 10),
    "mono_sm":    ("Consolas", 8),
    "mono_xl_b":  ("Consolas", 18, "bold"),
    # The breakdown sidebar: a headline number wants to be bigger and heavier
    # than the table it sits beside, or the panel reads as another data column
    # rather than a summary of them.
    "mono_stat_b": ("Consolas", 10, "bold"),
}
# The face everything wears unless a theme asks for another one.
UI_FONT_DEFAULT = "Segoe UI"
# Which of those a theme is allowed to swap. Derived from FONT_SPECS rather
# than listed by hand, so a font added up there lands in the right camp on its
# own: the Segoe UI entries are labels, names and captions and can wear
# anything, while the Consolas entries are the number columns — a theme that
# made THOSE proportional would unalign every table in the overlay, which is
# the whole reason they're monospaced.
THEME_FONT_KEYS = tuple(k for k, (family, *_rest) in FONT_SPECS.items()
                        if family == UI_FONT_DEFAULT)
# ...and on which windows. The control menu is deliberately absent: it is
# Farever-styled whatever theme you're wearing (see _pick_theme), and a panel
# that kept its own colours while changing its typeface would look broken
# rather than themed.
THEME_FONT_GROUPS = ("meter", "detail")
# The floor is where the fonts stop moving: sizes are clamped at 6pt inside
# _set_group_scale, and every body font in FONT_SPECS has hit that clamp by
# ~55%. A slider that goes lower would keep moving while the window stayed
# put — the same lie the MIN_W comment below warns about.
UI_SCALE_MIN, UI_SCALE_MAX = 50, 175      # percent

# The settings panel's tabs, in the order it lists them down the left. General
# stays the landing page — it is what you open the panel for most of the time.
# History and Social sit under it because they are the pages you open to READ
# rather than to change something; the configuration pages follow.
MENU_TABS = ("Help", "General", "History", "Actions", "Windows")
# Where EVERY launch lands. Help rather than General: the settings are
# discoverable by reading them, and the one thing the panel cannot tell you by
# being looked at is what any of it is for. Held in memory only — the tab
# follows you for the session and resets when the meter next starts.
MENU_TAB_DEFAULT = "Help"
# The tab names are ids (the panel sends them back); these are what it shows.
MENU_TAB_LABELS = {"Help": "Aide", "General": "Général",
                   "History": "Historique", "Actions": "Actions",
                   "Windows": "Fenêtres"}
# The project's fundraiser, at the top of Help. Its logo is optional: drop a
# gofundme.png beside the other assets and the button wears it, otherwise it
# falls back to a wordmark in the brand green. That way the button works
# whether or not the image has been added, rather than the page shipping a
# broken picture.
SUPPORT_URL = "https://gofund.me/6922dd070"
SUPPORT_LOGO = ROOT / "assets" / "gofundme.png"
SUPPORT_BLURB = (
    "Farever+ est là pour durer. Après beaucoup de retours et de soutien, le "
    "projet en est au point où je veux le poursuivre aussi longtemps que "
    "Shiro Games le permettra. Je prévois d'ajouter des fonctionnalités et de "
    "garder l'application à jour pour les prochaines versions du jeu.\n"
    "À terme, il devrait y avoir un site de logs et un endroit pour comparer "
    "vos temps de kill de boss avec les autres joueurs, avec la possibilité "
    "d'inspecter l'équipement, les talents, et plus encore.\n"
    "(Message de Brudr, l'auteur de Farever+.)")
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

SCALE_GROUPS = (
    ("meter", "Compteur"),
    ("detail", "Détail"),
    ("menu", "Réglages"),
)
# Where each group's slider starts when nothing is saved; absent means 100%.
# The settings panel defaults to 130: it's read at arm's length mid-game with
# the escape menu up, and the tabbed layout left it room to be bigger. Applied
# through the same path as a restored slider (see __init__), so the
# fonts-at-100 assumption inside _set_group_scale holds either way — a saved
# value, including an explicit 100, always wins over this table.
SCALE_DEFAULTS = {"menu": 1.30}
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
# Per-player skill/heal rows in a dataset's detail view.
HISTORY_DETAIL_SKILLS = 12


MAP_BODY_DARK = "#121C30"       # the deep navy the panels shipped with
MAP_BODY_FAREVER = BG_BODY_SOFT  # ...and the parchment version of the same

THEME_DEFAULT = {
    "border": BG_BORDER, "body": BG_BODY, "soft": BG_BODY_SOFT,
    "header": BG_HEADER, "header_combat": BG_HEADER_COMBAT,
    "header_unlocked": BG_HEADER_UNLOCKED, "track": BG_BAR_TRACK,
    "fg_header": FG_HEADER, "fg_header_dim": FG_HEADER_DIM,
    "fg_text": FG_TEXT, "fg_value": FG_VALUE, "fg_dim": FG_DIM,
    "accent": ACCENT, "dmg": DMG_BAR, "heal": HEAL_BAR,
    "heal_self": SELF_HEAL_BAR,
    "header_off": "#4A4441",
    # The map matches the meter on this theme. The panel used to be dark on
    # every theme, on the reasoning that a map is not a damage table — still
    # true, and still why Dark exists; but "make it look like the rest of the
    # overlay" is a legitimate thing to want and it now has an entry.
    "map_body": MAP_BODY_FAREVER,
}
# The dark overlay: the same layout in the map panel's navy, meter and
# breakdown included. Built on top of Farever rather than from scratch so a key
# added to one theme can't be missing from this one — but almost every value is
# overridden, because a dark theme is not a light theme with a darker box.
#
# What deliberately does NOT change: the damage and healing bars keep their blue
# and green, and green still means "the overlay is unlocked". Those three carry
# meaning, and a theme that recoloured them would be renaming the language the
# meter is written in.
THEME_DARK = dict(
    THEME_DEFAULT,
    border="#080D18",       # near-black navy; the panel edge
    body="#141E33",         # one step up from the map, so the map reads as inset
    soft="#1C2942",         # separators and the breakdown's column rule
    header="#2E6B70",       # the teal, taken down to sit on a dark body
    header_combat="#A94F22",
    track="#22304D",        # bar troughs
    fg_header="#FFFFFF", fg_header_dim="#CFE3E5",
    fg_text="#C6D3E8", fg_value="#FFFFFF", fg_dim="#7E8CA6",
    accent="#7FD4D4",       # headings; the teal lifted to read on navy
    header_off="#2A3346",   # a header bar whose element is hidden
    map_body=MAP_BODY_DARK,
)
# Night-violet, taken off the sparkling critters themselves — the deep blue of
# the tail, the violet fur, the magenta glow around it and the gold of the
# sparkle. The fourth base, and the only one that is nobody's idea of
# camouflage: Farever matches the game's parchment UI, Dark matches its map
# panels, Rift matches the place you're standing in. This one matches a
# squirrel.
#
# Two things make it more than a recolour:
#
#   * It swaps the proportional face to Candara — rounded and soft where Segoe
#     UI is neutral. Measured before it was chosen: Candara is NARROWER than
#     Segoe at every size the overlay uses ("OVER%" is 34px against 39px, and
#     its linespace is a pixel shorter), so nothing here can push a window past
#     the pixel minimums in MIN_W. The number columns stay Consolas.
#   * The accent is GOLD rather than another pink, which is what keeps this
#     theme and Rift apart. They are the only two dark panels with warm
#     highlights, and a pale-pink accent put them 56 deltaE closer than is
#     comfortable for two entries a player is meant to choose between.
#
# This started life as a pale lilac panel and was measured down to a dark one,
# which is worth recording because the middle is NOT available: darkening a
# light panel costs contrast on every ink row at once, and it also walks the
# violet track toward the blue damage bar. A mid lilac fails both — the
# darkest light version that still cleared the floors was #F2E8FC, which is
# indistinguishable from where it started. Dark pays for itself the other way,
# with the ink going light instead.
#
# Every ink/panel pair here was measured against the same pair on the three
# shipping themes and clears the weakest of them: body text 11.6:1 (floor
# 10.8), sidebar text 9.8 (floor 9.6, the tightest row), dim 5.5 (floor 4.9).
# The bars were checked as colour rather than ratio, the way the SELF_HEAL_BAR
# comment describes: the blue damage bar sits 28.3 deltaE off this track
# (floor 26.5) and the self-heal green 61.4 off it. The body is also held 13.5
# deltaE off Rift's and 11.1 off Dark's, so the three dark panels stay
# tellable apart at a glance.
THEME_SPARKLE = dict(
    THEME_DEFAULT,
    border="#150B28",       # near-black violet; the panel edge
    body="#261747",         # deep violet — bluer than Rift's maroon, on purpose
    soft="#362356",         # separators and the breakdown's column rule
    header="#7A4FC0",       # the critter's violet
    header_combat="#B32E72",  # ...and its magenta glow, for a fight
    track="#3D2C60",        # bar troughs
    fg_header="#FFFFFF", fg_header_dim="#E8D9FA",
    fg_text="#E4D4F7", fg_value="#FFFFFF", fg_dim="#A48CC6",
    accent="#FFD86B",       # headings; the gold of the sparkle
    header_off="#3A3350",   # a header bar whose element is hidden
    map_body="#1D1138",     # dark, so the markers stay the bright thing on it
    font="Candara",
)
THEME_RIFT = {
    "border": RIFT_EDGE, "body": RIFT_BODY, "soft": "#3D0F28",
    "header": RIFT_GLOW, "header_combat": "#C41E6E",
    # Green still means "unlocked", and it reads fine on the dark body — the
    # bars keep their meanings too (blue damage, green healing) rather than
    # being recoloured into the theme and losing what they stand for.
    "header_unlocked": BG_HEADER_UNLOCKED, "track": "#4A1030",
    "fg_header": RIFT_TIME, "fg_header_dim": "#F0A8CC",
    "fg_text": "#FFC9E4", "fg_value": "#FFFFFF", "fg_dim": "#C77AA0",
    "accent": RIFT_TITLE, "dmg": DMG_BAR, "heal": HEAL_BAR,
    "heal_self": SELF_HEAL_BAR,
    "header_off": "#3B3036",
    "map_body": RIFT_BODY,
}

# Which base each mode wears, pinned or Dynamic. A table rather than the chain
# of conditionals this used to be: with three bases the chain had a default
# arm that quietly meant "Farever", and adding a fourth would have meant
# spotting that before it silently swallowed the new name.  Anything absent
# here — "Farever", "Farever Dynamic", and "Rift", which is handled on its own
# in _pick_theme — falls to THEME_DEFAULT.
THEME_BASES = {
    "Dark": THEME_DARK, "Dark Dynamic": THEME_DARK,
    "Sparkle": THEME_SPARKLE, "Sparkle Dynamic": THEME_SPARKLE,
}

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

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000

# Win11 rounds a window's corners on request, and does it for these borderless
# layered popups too. DWM owns the shape from then on, so — unlike SetWindowRgn —
# nothing needs re-applying as the meter grows and shrinks with the party.
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2                   # 3 = DWMWCP_ROUNDSMALL, a tighter radius


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


def message_box(text, title="Farever+", flags=0x40):
    """A dialog is the only way to reach a user who has no console. Used for
    the failures that stop the meter starting at all — anything softer belongs
    in the log."""
    try:
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title),
                                         flags | 0x1000)   # MB_SETFOREGROUND
    except Exception:
        pass


def _hidden_tk():
    """A throwaway root so the startup dialogs can exist before the overlay
    does. Destroyed by the caller — the overlay builds its own."""
    r = tk.Tk()
    r.withdraw()
    r.attributes("-topmost", True)
    return r


def ask_choice(title, prompt, labels):
    """Pick one of `labels`, returning its index (or 0 if the user just closes
    the dialog — the same default the console prompt uses for a bare Enter)."""
    root = _hidden_tk()
    picked = {"i": 0}
    win = tk.Toplevel(root)
    win.title(title)
    win.configure(bg=BG_BODY, padx=14, pady=12)
    win.attributes("-topmost", True)
    win.resizable(False, False)
    tk.Label(win, text=prompt, bg=BG_BODY, fg=FG_TEXT, justify="left",
             anchor="w", font=("Segoe UI", 10)).pack(fill="x", pady=(0, 10))

    def choose(i):
        picked["i"] = i
        win.destroy()

    for i, label in enumerate(labels):
        tk.Button(win, text=label, command=lambda i=i: choose(i), anchor="w",
                  bg=BG_BODY_SOFT, fg=FG_TEXT, relief="flat", bd=0,
                  padx=10, pady=6, cursor="hand2",
                  font=("Segoe UI", 9)).pack(fill="x", pady=2)
    win.protocol("WM_DELETE_WINDOW", lambda: choose(0))
    win.update_idletasks()
    win.geometry(f"+{(win.winfo_screenwidth() - win.winfo_width()) // 2}"
                 f"+{(win.winfo_screenheight() - win.winfo_height()) // 3}")
    win.grab_set()
    root.wait_window(win)
    root.destroy()
    return picked["i"]


def ask_directory(title):
    """Folder picker, for when the game install can't be found automatically.
    Returns a Path or None."""
    from tkinter import filedialog
    root = _hidden_tk()
    try:
        d = filedialog.askdirectory(title=title, parent=root)
    finally:
        root.destroy()
    return Path(d) if d else None


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


# Backend decoration on a level id, longest first so the specific prefixes win
# over the generic ones. Measured from the zone signals normal play produces:
# 'POI/Z1Levels/Z1_POI_Dungeon_ManfishRuines', 'POI/Rifts/POI_Rift_01',
# 'World/W1_Siagarta'.
_ZONE_STRIP = re.compile(
    r"^(?:Z\d+_)?POI_(?:Dungeon|Boss|Cave|Camp)_|^(?:Z\d+_)?POI_|^W\d+_|^Z\d+_")
# A trailing variant number is the game's, not the player's — 'POI_Rift_01' is
# the rift, and "Rift 01" reads like there are others you should know about.
_ZONE_TRAILING_NUM = re.compile(r"[ _]\d+$")
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _zone_label(sig, world_map=None):
    """A level signature as somewhere you can name out loud.

    The game's `World.name` and `World.branchName` ride along on every zone
    message and have read back empty on every zone this build has seen (the
    meter.log lines print them only when they are set, and none of them are),
    so the signature itself is the honest source. `world_map` is the game's own
    `_isWorldMap` flag — the one thing that distinguishes an overworld region
    from a dungeon without pattern-matching the path.
    """
    if not sig:
        return "Inconnu"
    leaf = str(sig).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    leaf = _ZONE_STRIP.sub("", leaf) or str(sig)
    leaf = _CAMEL_SPLIT.sub(" ", leaf.replace("_", " ")).strip()
    leaf = _ZONE_TRAILING_NUM.sub("", leaf).strip() or str(sig)
    # " Overworld", not "Overworld: " — the region is what you would say first,
    # and the qualifier is only there to distinguish it from a dungeon of the
    # same name.
    return f"{leaf} (monde ouvert)" if world_map else leaf


def _dataset_name(targets, zone_sig, world_map=None):
    """Name a finished encounter: who took the most damage, and where.

    `targets` is {unit kind: damage}. The kind goes through the cdb's unit
    sheet exactly as a boss kill does — a kind is a backend id and routinely
    not the name on the nameplate ('Cleodora' displays as 'Queen
    Honeyzabeth'), so naming a dataset off the raw kind would file the fight
    under a name the player never saw.

    A dataset with no named target — every hit landed on something that isn't
    an ent.Unit, or the offsets file predates unitClasses — is named for its
    zone alone rather than for a guess.
    """
    where = _zone_label(zone_sig, world_map)
    if not targets:
        return where
    kind = max(targets.items(), key=lambda kv: kv[1])[0]
    who = _unit_names().get(kind) or _pretty_id(kind)
    return f"{who} — {where}" if who else where


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
        under = sorted((k, v) for k, v in self._audit.items() if v[0] > 1.02)
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


class HistoryStore:
    """Every finished encounter, kept as data until you delete it yourself.

    The meter's primary combat store lives exactly as long as the encounter
    does — a reset, a zone change or a boss pull throws it away and there has
    never been anywhere for it to go. This is that somewhere: one JSON file
    per finished encounter, named for what you were fighting and where.

    Three deliberate non-features:

    * Nothing here ever deletes anything. No age limit, no count limit, no
      "tidy up" pass. A meter that prunes a folder is a meter that can prune
      the WRONG folder — one bad path and it is deleting somebody's
      documents — and the cost of not pruning is disk space the player can
      see and manage in Explorer.
    * Writes go through a worker thread. save() is called from the damage
      hook's thread by way of PartySession's archive hook, and that thread is
      inside the game's own call stack; it must never wait on a disk.
    * The session id is generated once per meter launch and written into
      every file, so which launch produced a dataset is a fact in the data
      rather than a guess from timestamps. It carries the pid because two
      meters can be started inside the same second (a relaunch). The browser
      does not filter on it — it lists every session — but the id is what any
      later grouping would have to be built on, and it costs one string per
      file to keep.
    """

    VERSION = 1

    def __init__(self, directory=HISTORY_DIR):
        self.dir = Path(directory)
        self.session = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        self._q: queue.Queue = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()
        # path -> (mtime, size, summary). Listing re-reads only what changed,
        # so opening the tab on a session with hundreds of datasets costs one
        # directory scan rather than hundreds of file reads.
        self._summaries: dict[str, tuple] = {}
        self._failed = False        # a write has failed; say so once, not per file

    # ---- writing ----
    def _ensure_writer(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="history-writer")
            self._thread.start()

    def _run(self):
        while True:
            entry = self._q.get()
            if entry is None:
                return
            try:
                self._write(entry)
            except Exception as e:
                if not self._failed:
                    self._failed = True
                    print(f"[meter] combat history is not being saved: {e}",
                          file=sys.stderr)

    def _write(self, entry):
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(entry["at"]))
        base = f"{entry['kind']}-{stamp}-{_slug(entry['name'])}"
        path = self.dir / f"{base}.json"
        # Two encounters can finish inside the same second — a rift's report
        # and the reset that follows it, most obviously.
        n = 2
        while path.exists():
            path = self.dir / f"{base}-{n}.json"
            n += 1
        path.write_text(json.dumps(entry), encoding="utf-8")
        # The FILENAME, not the dataset name: names carry an em dash (and
        # whatever the cdb calls a boss), and a source run's stderr is the
        # Windows console, which is cp1252. Encoding that raises — inside the
        # worker's try, after the file is already on disk — and the meter would
        # report a failure for a dataset it had just saved. `path.name` is
        # ASCII by construction, and the slug still says what it is.
        print(f"[meter] history saved: {path.name}", file=sys.stderr)

    def save(self, kind, name, data, **extra):
        """Queue one dataset. Returns immediately; the write happens on the
        worker thread."""
        entry = dict(extra)
        entry.update({"v": self.VERSION, "kind": kind, "name": name,
                      "session": self.session, "meter": VERSION,
                      "at": data.get("at") or time.time(), "data": data})
        self._ensure_writer()
        self._q.put(entry)

    # ---- reading ----
    @staticmethod
    def _summarise(path, entry):
        """The one line the browser needs, without keeping the dataset in
        memory. Totals are recomputed from a rift's phases so both kinds of
        dataset answer the same questions."""
        data = entry.get("data") or {}
        kind = entry.get("kind") or "combat"
        if kind == "rift":
            phases = data.get("phases") or []
            duration = sum(float(ph.get("duration") or 0.0) for ph in phases)
            total = sum(float(ph.get("total") or 0.0) for ph in phases)
            heal = sum(float(ph.get("heal") or 0.0) for ph in phases)
            players = len({p.get("name") for ph in phases
                           for p in (ph.get("players") or [])})
        else:
            duration = float(data.get("duration") or 0.0)
            total = float(data.get("total") or 0.0)
            heal = float(data.get("heal") or 0.0)
            players = len(data.get("players") or [])
        return {"path": str(path), "kind": kind,
                "name": entry.get("name") or "Combat",
                "at": float(entry.get("at") or 0.0),
                "session": entry.get("session") or "",
                "zone": (entry.get("zone") or {}).get("label") or "",
                "duration": duration, "total": total, "heal": heal,
                "players": players}

    def entries(self):
        """Every dataset on disk, newest first — all of them, every session.

        There is deliberately no filter here. The session id is still written
        into every file (it is the one thing that reliably groups a night's
        datasets, since timestamps alone can't tell one launch from the next),
        but hiding earlier sessions only ever meant a browser that looked
        empty for a feature that had been recording for weeks.

        A file that won't parse is skipped rather than raised: hand-edited,
        half-written by a meter that was killed mid-save, or from a future
        format — none of those should cost you the list."""
        out = []
        try:
            paths = sorted(self.dir.glob("*.json"))
        except OSError:
            return out
        fresh = {}
        for p in paths:
            key = str(p)
            try:
                st = p.stat()
                sig = (st.st_mtime, st.st_size)
            except OSError:
                continue
            cached = self._summaries.get(key)
            if cached is not None and cached[:2] == sig:
                summary = cached[2]
            else:
                try:
                    entry = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue
                summary = self._summarise(p, entry)
            fresh[key] = (sig[0], sig[1], summary)
            out.append(summary)
        self._summaries = fresh
        out.sort(key=lambda s: -s["at"])
        return out

    @staticmethod
    def load(path):
        """One dataset in full, or None. Same tolerance as entries()."""
        try:
            entry = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[meter] couldn't read {path}: {e}", file=sys.stderr)
            return None
        return entry if isinstance(entry, dict) else None


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






    def zone_sig(self):
        with self._lock:
            return self._zone_sig

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


    def set_window(self, name: str, is_open: bool):
        if not name:
            return
        with self._lock:
            if is_open:
                self._open.add(name)
                # Stamped so the overlay can report how long IT took to react
                # to the game opening its menu. The hook side is an interceptor
                # on the window itself, so this is the moment the game did it;
                # anything after is ours.
                if name in UNLOCK_ON_WINDOWS:
                    self._unlock_at = time.monotonic()
            else:
                self._open.discard(name)

    def take_unlock_stamp(self):
        """The pending open-stamp, consumed. None if already reported."""
        with self._lock:
            t, self._unlock_at = self._unlock_at, None
            return t

    def clear(self):
        with self._lock:
            self._open.clear()
            # A loading screen tears the HUD down with it, so a bar that was up
            # on the way out must not leave the compass hidden in the new zone.
            self._boss_bar = 0

    def any_open(self, names) -> bool:
        with self._lock:
            return any(n in self._open for n in names)

    def any_open_except(self, names) -> bool:
        """True while any game window *other* than `names` is open — i.e. the
        player is looking at one of the game's own screens."""
        with self._lock:
            return bool(self._open - set(names))


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
# The other half, and NOT optional. Every size in FONT_SPECS is in POINTS, and
# Tk turns points into pixels using the DPI it is told the screen has. While
# unaware it is told 96 and uses 96/72. Once aware it is told the real 288 at
# 300%, and would re-inflate every font by exactly the factor we just removed —
# the same bug arriving by a different route. Pinning the conversion to the
# 96-DPI value is what makes "aware" mean "identical at 100%".
TK_POINT_SCALE = 96 / 72
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


# ---------------------------------------------------------------------------
# Windows click-through + hotkeys (adapted from the original meter)
# ---------------------------------------------------------------------------
def _set_rounded_corners(hwnd):
    """Ask DWM to round this window's corners. Silently does nothing anywhere
    but Windows 11 (older builds don't know the attribute and just fail)."""
    if sys.platform != "win32":
        return
    try:
        pref = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(DWMWA_WINDOW_CORNER_PREFERENCE),
            ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


def _set_clickthrough(hwnd, enabled, activatable=False):
    """Set one window's click-through, and whether it may hold keyboard focus.

    `activatable` drops WS_EX_NOACTIVATE. Only the control menu asks for it,
    and only because it carries the Social tab's search boxes: a window that
    never activates never receives a keystroke, so a tk.Entry on one is inert
    no matter what is bound to it — the same wall _begin_bind_capture works
    around by polling the keyboard instead of binding to it."""
    if sys.platform != "win32":
        return
    u = ctypes.windll.user32
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        get, setl = u.GetWindowLongPtrW, u.SetWindowLongPtrW
    else:
        get, setl = u.GetWindowLongW, u.SetWindowLongW
    ex = get(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED
    if activatable:
        ex &= ~WS_EX_NOACTIVATE
    else:
        ex |= WS_EX_NOACTIVATE
    if enabled:
        ex |= WS_EX_TRANSPARENT
    else:
        ex &= ~WS_EX_TRANSPARENT
    setl(hwnd, GWL_EXSTYLE, ex)


def _main_hwnd_of_pid(pid):
    """The process's largest visible top-level window, or None.

    Same enumeration _window_rect_of_pid does, kept separate because the caller
    wants the handle rather than the rectangle."""
    if sys.platform != "win32":
        return None
    u = ctypes.windll.user32
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                           ctypes.POINTER(wintypes.DWORD)]
    best = {"area": 0, "hwnd": None}
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
                best["area"], best["hwnd"] = area, hwnd
        return True

    try:
        u.EnumWindows(WNDENUMPROC(visit), 0)
    except Exception:
        return None
    return best["hwnd"]


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


def reset_hint_text():
    return f"Réinitialiser Farever+ : {bind_label()}"

# ---------------------------------------------------------------------------
# Version / update check
# ---------------------------------------------------------------------------
# Bump this on every release, and tag the repo with the same string — it's the
# left-hand side of the comparison below, so a release that forgets it tells
# everyone they're out of date forever.
VERSION = "4.0.1"

REPO = "brudrbear/FareverMeter"
REPO_URL = f"https://github.com/{REPO}"


QUIT_LABEL = "Arrêter le compteur"

# The top line of the control menu. It used to shout about Ctrl+C, from when
# the only way to run the meter was a console you could close out from under
# it. The shipped build has no console and two proper exits, so the line just
# says where they are — and doubles as the slot the update notice takes over.
SHUTDOWN_HINT = ("Arrête le compteur avec le bouton en bas, ou depuis "
                 "l'icône Farever+ près de l'horloge.")


def start_hotkeys(callbacks: dict, target_pid):
    """Run the keyboard hook that owns Shift+\\, on its own thread with its own
    message pump."""
    if sys.platform != "win32":
        return

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
            if vk != RESET_BIND.get("vk") or fg_pid() != target_pid:
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
            if vk != RESET_BIND.get("vk") or fg_pid() != target_pid:
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


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", ctypes.c_void_p), ("ptScreenPos", POINT)]


CURSOR_SHOWING = 0x1


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

    def __init__(self, on_quit, tip="Farever+"):
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
        u.AppendMenuW(m, MF_STRING, TRAY_SETTINGS, "Ouvrir les réglages")
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
            self.hwnd = u.CreateWindowExW(0, "FareverMeterTray", "Farever+",
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
        self._balloon("Farever+ est lancé",
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
# Overlay
# ---------------------------------------------------------------------------
class Overlay:
    def __init__(self, session: PartySession, target_pid, ui_state=None,
                 world=None, configure=None):
        self.session = session
        self.target_pid = target_pid
        self.ui_state = ui_state if ui_state is not None else GameUIState()
        self.world = world if world is not None else WorldSnapshot()
        # Pushes settings to the running hook (currently just the sweep rate).
        # A no-op when there's no hook, so the overlay stays testable on its own.
        self._configure = configure or (lambda **kw: None)
        self.focus_player = None       # drilled-in player name (None => local)
        self.mode = "party"            # "party" (group only) or "all"
        # The overlay is click-through unless the game has freed the cursor for
        # its escape menu. There is no manual lock any more — the game's own UI
        # state is the single source of truth.
        self._menu_unlock = False
        # A search box on the control menu has keyboard focus, so the game is
        # not seeing keystrokes right now. Everything that would otherwise
        # shove focus back at the game defers to this — see _refocus_game.
        self._typing = False
        # The settings panel, in its own process. Not started here: it is
        # spawned the first time the game's escape menu opens, so a player who
        # never opens it never pays for a second process or a WebView2.
        self.menubridge = MenuBridge(self)
        self._panel_visible = False
        self._panel_reassert = 0
        self._panel_errs = set()    # panel failures already reported
        # The icon sheet is sent once per panel process, on its boot — half a
        # megabyte has no business in a state push. Reset when a panel dies so
        # a restarted one gets it again.
        self._icon_sheet_sent = False
        # Position AND size, unlike every other window here — the panel is
        # resizable, which is the whole point of it, so remembering where it
        # was without remembering how big it was would be half a feature.
        # Filled in below, once the saved positions have been read.
        self._panel_geom = {}
        self._help_open = None      # which help article is open, if any
        # Deliberately NOT saved to disk. Every launch opens on Help, and the
        # tab then follows you for the rest of the session — so pressing Escape
        # again resumes where you left off, without a page you picked once
        # weeks ago being what greets you tomorrow.
        self._menu_tab = MENU_TAB_DEFAULT
        self._history_query_text = ""
        self._history_note_text = ""
        self._hide_ooc = False         # "hide out of combat" setting
        # Second-screen mode — see SCREEN2_KEYS.
        self._screen2 = False
        # The settings panel opened from the tray icon rather than from the
        # game's escape menu: shown until it is closed, whatever the game does.
        self._panel_forced = False
        self._s2_save_job = None       # debounced save after a native move
        self._best_times = self._load_best_times()   # fastest boss kills, secs by kind
        # _show is what the player asked for, _shown is what's actually mapped
        # (they differ while out-of-combat hiding is in effect).
        self._show = {k: ELEMENT_SHOW for k, _ in TOGGLEABLE_ELEMENTS}
        # Healing is columns inside the meter, not a window of its own, so it
        # stays a plain on/off rather than gaining a mode it can't honour.
        self._show_heal = True
        self._sort_heal = False        # rows ordered by healing, not damage
        # The previous encounter, kept on screen across a reset until the next
        # one starts landing hits — see _hold_last.
        self._held_rows = []
        self._held_duration = 0.0
        # Widget path -> the canvas the wheel should scroll while the cursor
        # is anywhere inside it. Filled in by scroll_list — see _on_wheel.
        self._scroll_areas: dict[str, tk.Canvas] = {}
        self._shown = _initial_shown()
        # healing, as last pushed to the widgets — see _apply_heal_columns
        self._cols_shown = (True,)
        self._combat_seen_at = 0.0     # last moment a tracked player was fighting
        self._header_bg = BG_HEADER    # last tint pushed to the header bars
        self._theme = THEME_DEFAULT    # what's painted right now
        self._theme_mode = THEME_MODE_DEFAULT   # what the player asked for
        # The face currently configured on the themed font sets. Tracked rather
        # than read back off a font object because reconfiguring a family
        # relayouts four windows, and the theme is re-picked every tick.
        self._theme_font = UI_FONT_DEFAULT
        self._families = None          # installed fonts, filled on first ask
        self._action_q = []
        self._q_lock = threading.Lock()
        self._quit_armed = False       # the Quit button's second-click window
        self._game_exit_win = None     # the "Farever has stopped" prompt
        self._game_gone = False        # the game process died; hide everything

        self._transparency = 0              # percent, on top of OVERLAY_ALPHA
        self._auto_reset_boss = False
        # One scale per window group. Each window wants a size that suits its
        # job — the meter one that suits reading numbers, the map one that
        # suits the screen it covers — and a single slider means at least one
        # of them is always wrong.
        self._scales = {group: 1.0 for group, _label in SCALE_GROUPS}
        self._game_hwnd = None         # cached; re-resolved if it goes stale
        self._cursor_free = False      # game has released the mouse (Alt, menus)
        self._focused = True           # Farever is the window you're looking at
        # True while the countdown has nothing to count down to. The window is
        # hidden in that state unless the escape menu is open, so it isn't
        # sitting there saying "No rift upcoming" for six minutes of every hour.
        self._rift_idle = True
        # The breakdown has no player to break down — see _refresh_visibility.
        self._detail_idle = True
        self._last_epoch = session.epoch
        # Parse mode: None | "countdown" (pre-roll) | "parsing" | "done" (the
        # finished sample, frozen on screen until it's cleared).
        self._parse_state = None
        self._parse_until = 0.0
        # Rift prompt: modal, and the only overlay window on screen while it's
        # up. _rift_seen is the edge detector — one prompt per rift entry.
        self._prompt_open = False
        self._prompt_kind = "enter"
        # Standing answer to both halves of that prompt: switch to all-players
        # on the way into a rift and back to party-only on the way out, without
        # being asked. One setting rather than two, because the pair only makes
        # sense together — auto-switching in and then leaving you on all-players
        # in the open world is the state the leave prompt exists to prevent.
        self._rift_auto_view = False
        self._rift_pulse = False
        self._pulse_job = None
        self._rift_box = None      # which palette the box is wearing
        self._rift_seen = False
        # End-of-rift report: the frozen data the card is showing, and the
        # "Copied" flash timer. Seeded from the newest saved report so 'Last
        # Rift Report' works across sessions — last night's rift is still
        # there this morning.
        self._report_open = False
        self._report_data = self._load_last_rift_report()
        self._report_flash_job = None

        # ---- combat history ----
        # Off until asked for: this writes a file for every fight you finish,
        # and that is not a thing to start doing to someone's disk on their
        # behalf. The store exists either way — it is what holds the session
        # id, and the browser needs it to read the folder even while the
        # recording is off.
        self._history = HistoryStore()
        self._history_on = False
        self._history_entries = []
        self._history_note_job = None
        self._binding_now = False       # waiting for a keypress to rebind reset
        self._history_detail = None     # the dataset the detail page is showing

        # Before any widget exists: the dropdowns and Show/hide ticks are built
        # from these, so loading afterwards would leave the menu disagreeing
        # with the state it is supposed to be showing.
        self._pending_scales = None
        self._load_settings()
        # The setting decides whether the primary store has anywhere to put a
        # finished encounter. Applied here rather than checked inside the hook
        # so that "history is off" costs the damage path nothing at all — the
        # hook is simply not installed.
        self._apply_history_setting()

        pos = self._load_positions()
        # Every saved position, both modes' — _save_pos writes the current
        # mode's windows into it and keeps the other mode's untouched.
        self._pos_mem = dict(pos)
        # The panel is not a Tk window, so it is not in _place_windows' list —
        # it takes its geometry with it when it is spawned instead.
        self._panel_geom = pos.get("panel") or {}
        self.root = tk.Tk()
        # Before the fonts below are built from it. Now that the process is DPI
        # aware Tk knows the screen's real DPI, and every point size in
        # FONT_SPECS would be scaled by it — see TK_POINT_SCALE for why that
        # would undo the whole point of declaring awareness.
        self.root.tk.call("tk", "scaling", TK_POINT_SCALE)
        self._ui_scale = 1.0
        # One font set per independently-scaled window group. Tk fonts are
        # shared objects, so resizing one would resize every widget using it —
        # separate scales mean separate sets, not a cleverer setter.
        #   fonts    the meter, and the small floating bits that belong with it
        #            (rift timer, hint, parse banner, rift prompt)
        #   fonts_d  the breakdown
        #   fonts_m  the control menu
        def _font_set():
            out = {}
            for key, (family, size, *style) in FONT_SPECS.items():
                out[key] = tkfont.Font(
                    root=self.root, family=family, size=size,
                    weight="bold" if "bold" in style else "normal",
                    slant="italic" if "italic" in style else "roman")
            return out

        self.fonts = _font_set()
        self.fonts_d = _font_set()
        self.fonts_m = _font_set()
        self._font_sets = {"meter": self.fonts, "detail": self.fonts_d,
                           "menu": self.fonts_m}
        self.root.title("Farever+ Party Meter")
        self.detail = tk.Toplevel(self.root)
        self.detail.title("Farever+ Breakdown")
        self.menu = tk.Toplevel(self.root)
        self.menu.title("Farever+ Controls")
        # Withdrawn HERE, at creation, and never mapped again — the settings
        # panel is the WebView2 window now.
        #
        # The startup withdraw loop further down is not early enough on its
        # own: _place_windows runs before it and calls update_idletasks() on
        # this window to measure it, which is enough to realise it on screen.
        # The symptom was the old Tk menu appearing for a moment on load.
        #
        # Its widgets are still built below, because ~200 places in this file
        # still reference them and unpicking that is a separate job from
        # replacing the window. Nothing shows them; see _refresh_visibility,
        # which pins this window's visibility to False.
        self.menu.withdraw()
        self.hintwin = tk.Toplevel(self.root)
        self.hintwin.title("Farever+ Hint")
        self.parsewin = tk.Toplevel(self.root)
        self.parsewin.title("Farever+ Parse")
        self.promptwin = tk.Toplevel(self.root)
        self.promptwin.title("Farever+ Prompt")
        self.reportwin = tk.Toplevel(self.root)
        self.reportwin.title("Farever+ Rift Report")

        self.riftwin = tk.Toplevel(self.root)
        self.riftwin.title("Farever+ Rift Timer")
        # Confirmation that a manual reset happened. Its own window, managed
        # by hand like the other toasts rather than through the fade system:
        # it answers a keypress and has to be on screen in the same frame.
        self.resetwin = tk.Toplevel(self.root)
        self.resetwin.title("Farever+ Reset")
        self.killwin = tk.Toplevel(self.root)
        self.killwin.title("Farever+ Kill Time")
        for win in (self.root, self.detail, self.menu, self.hintwin,
                    self.parsewin, self.promptwin, self.reportwin,
                    self.riftwin, self.killwin, self.resetwin):
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=TRANSPARENT_KEY)
            try:
                win.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
            except tk.TclError:
                pass
            # Rounding has to be (re-)applied on every map, not once here: Tk
            # rebuilds a toplevel's Windows wrapper as it applies the wm
            # attributes above, and the DWM setting dies with the old hwnd.
            win.bind("<Map>", self._on_win_map, add="+")
        for win in (self.root, self.detail, self.menu, self.riftwin):
            win.attributes("-alpha", OVERLAY_ALPHA)
        # Keys must match TOGGLEABLE_ELEMENTS.
        self._element_win = {"meter": self.root, "detail": self.detail,
                             "rift": self.riftwin}
        # Every window that fades: the two toggleable ones, plus the control
        # menu and its hint, which follow the game's escape menu.
        self._fade_win = dict(self._element_win, menu=self.menu,
                              hint=self.hintwin, prompt=self.promptwin,
                              report=self.reportwin)
        self._shown["menu"] = self._shown["hint"] = False
        self._shown["prompt"] = False
        self._shown["report"] = False

        self._shown["rift"] = False     # nothing to show until a timer arrives
        # Live opacity of each faded window, driven by _step_fade. The menu pair
        # starts at zero: they're withdrawn until the escape menu opens.
        # Seeded from the saved slider, not from the constant: a restart should
        # come up wearing the transparency you left it on, without a visible
        # settle from full opacity.
        self._alpha = {k: self._alpha_for(k) for k in self._fade_win}
        self._alpha["menu"] = self._alpha["hint"] = 0.0
        self._alpha["prompt"] = self._alpha["rift"] = 0.0
        self._alpha["report"] = 0.0
        for key, win in self._fade_win.items():
            if self._alpha[key]:
                win.attributes("-alpha", self._alpha[key])
        self._fade_secs = {k: FADE_SECS for k in self._fade_win}
        self._fade_secs["menu"] = self._fade_secs["hint"] = MENU_FADE_SECS
        self._fade_secs["prompt"] = self._fade_secs["report"] = PANEL_FADE_SECS

        self._fade_job = None          # pending `after` id for the fade driver

        self._build_meter()
        self._build_detail()
        self._build_hint()
        self._build_parse()
        self._build_reset_toast()
        self._build_kill_toast()
        self._build_prompt()
        self._build_report()

        self._build_rift()
        self.root.update_idletasks()
        self._place_windows(pos)
        # Restored scales can only be applied now: they resize the fonts every
        # window has already been packed against. The sliders are set from them
        # too, or the menu would read 100% while the window is at 125.
        # Defaults underneath, saved values on top — a group nobody has ever
        # touched starts at its SCALE_DEFAULTS entry, and a saved 100 stays a
        # saved 100 rather than being "upgraded" to the default.
        restored = dict(SCALE_DEFAULTS)
        restored.update(self._pending_scales or {})
        for group, factor in restored.items():
            if group in self._scales and abs(factor - 1.0) > 0.001:
                self._set_group_scale(group, factor)
        self._pending_scales = None
        # The control menu and its hint only exist while the game's escape menu
        # is up; _sync_game_ui maps them in. The parse banner is mapped by parse
        # mode itself, and deliberately answers to nothing else — a countdown
        # you can't see is worse than useless.
        # Not a faded window — parse mode maps and unmaps it itself.
        self.parsewin.withdraw()
        # ...nor the reset confirmation, which maps itself when you reset.
        self.resetwin.withdraw()
        # Nor this one: the kill-time toast maps itself when a boss dies.
        self.killwin.withdraw()
        # DERIVED from _shown rather than hand-listed, because the hand-listed
        # version had exactly one failure mode and it happened: add a faded
        # window, forget to add it here, and it starts MAPPED. A Toplevel left
        # at alpha 0 is not hidden — it is still topmost and still clickable,
        # so it paints whatever its widgets hold and eats clicks meant for the
        # game. The update offer shipped that way for one build: it sat on
        # screen as a bare header and a "Later" button, and clicking that
        # button appeared to do nothing, because _want_visible saw the target
        # it was already set to and returned without withdrawing anything.
        # This loop cannot be forgotten.
        for key, win in self._fade_win.items():
            if not self._shown[key]:
                win.withdraw()
        # A window that moves by its own title bar never goes through
        # _bind_drag, so its new place is saved from here instead.
        for win in (self.root, self.detail, self.reportwin):
            win.bind("<Configure>",
                     lambda e, w=win: self._on_s2_configure(e, w), add="+")
            # The title bar's ✕ must not end the program (closing the Tk root
            # would): it just puts the window away in the taskbar.
            win.protocol("WM_DELETE_WINDOW", lambda w=win: self._on_s2_close(w))
        if self._screen2:
            self._apply_window_mode()
            self._place_mode_windows()
        self.root.after(60, self._apply_clickthrough)
        self._install_hotkeys()

    # ---- persistence ----
    def _load_positions(self):
        """{"meter": (x, y), "detail": (x, y), "menu": (x, y)} — accepts the
        pre-split single-window cache ({"x", "y"}) as the meter position."""
        try:
            d = json.loads(POSITION_CACHE.read_text())
        except Exception:
            return {}
        # Every position written before the meter declared DPI awareness is in
        # the VIRTUALISED space Windows hands an unaware process — the desktop
        # divided by the scale factor. We now read and write physical pixels,
        # so those coordinates have to be multiplied back up or a 300% user's
        # whole layout lands in the top-left third of their screen. At 100%,
        # which is nearly everyone, the factor is 1.0 and nothing moves.
        scale = 1.0 if d.get("space") == "physical" else display_scale()

        def at(x, y):
            return (int(round(int(x) * scale)), int(round(int(y) * scale)))

        if "x" in d:
            try:
                return {"meter": at(d["x"], d["y"])}
            except Exception:
                return {}
        out = {}
        for key in ("meter", "detail", "menu", "rift",
                    "s2_meter", "s2_detail", "s2_report"):
            try:
                out[key] = at(d[key]["x"], d[key]["y"])
            except Exception:
                pass
        # The settings panel keeps a size as well as a position, and is not a
        # Tk window, so it travels as a dict rather than an (x, y) pair.
        try:
            p = d["panel"]
            x, y = at(p["x"], p["y"])
            out["panel"] = {"x": x, "y": y,
                            "w": int(round(int(p["w"]) * scale)),
                            "h": int(round(int(p["h"]) * scale))}
        except Exception:
            pass
        return out

    def _pos_visible(self, x, y):
        """True if (x, y) sits within the visible virtual desktop (all monitors),
        so a position saved on a different monitor layout can't hide the window."""
        try:
            if sys.platform == "win32":
                gm = ctypes.windll.user32.GetSystemMetrics
                vx, vy = gm(76), gm(77)          # SM_X/YVIRTUALSCREEN
                vw, vh = gm(78), gm(79)          # SM_CX/CYVIRTUALSCREEN
                return (vx - 10 <= x <= vx + vw - 60 and
                        vy - 10 <= y <= vy + vh - 40)
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            return -10 <= x <= sw - 60 and -10 <= y <= sh - 40
        except Exception:
            return True

    def _place_windows(self, pos):
        m = pos.get("meter")
        if m and self._pos_visible(*m):
            self.root.geometry(f"+{m[0]}+{m[1]}")
        else:
            self._default_meter_pos()
        self.root.update_idletasks()
        d = pos.get("detail")
        if d and self._pos_visible(*d):
            self.detail.geometry(f"+{d[0]}+{d[1]}")
        else:
            self._default_detail_pos()
        rw = pos.get("rift")
        if rw and self._pos_visible(*rw):
            self.riftwin.geometry(f"+{rw[0]}+{rw[1]}")
        else:
            self._default_rift_pos()
        mn = pos.get("menu")
        if mn and self._pos_visible(*mn):
            self.menu.geometry(f"+{mn[0]}+{mn[1]}")
        else:
            self._default_menu_pos()

    def _game_rect(self):
        """Screen rect of the game's own window, so 'centre screen' means the
        monitor the player is actually looking at rather than the primary one.
        Falls back to the primary screen when the window can't be found."""
        rect = _window_rect_of_pid(self.target_pid)
        if rect:
            return rect
        return (0, 0, self.root.winfo_screenwidth(),
                self.root.winfo_screenheight())

    def _default_meter_pos(self):
        sw = self.root.winfo_screenwidth()
        w = max(self.root.winfo_reqwidth(), self.root.winfo_width(), 380)
        self.root.geometry(f"+{sw - w - 12}+12")

    def _default_rift_pos(self):
        """Bottom of the top-centre stack, under the parse banner. Draggable
        from there like everything else, and remembered."""
        self.riftwin.update_idletasks()
        l, t, r, _b = self._game_rect()
        w = max(self.riftwin.winfo_reqwidth(), self.riftwin.winfo_width(), 120)
        self.riftwin.geometry(f"+{l + ((r - l) - w) // 2}+{t + TOP_STRIP_RIFT}")



    def _default_detail_pos(self):
        # Just below the meter, left-aligned with it. The meter is usually
        # EMPTY when this runs (startup / reset positions), so reserve room for
        # it to grow a full party of rows without covering the breakdown.
        self.root.update_idletasks()
        x = self.root.winfo_x()
        h = max(self.root.winfo_reqheight(), self.root.winfo_height(), 240)
        self.detail.geometry(f"+{x}+{self.root.winfo_y() + h + 10}")

    def _default_menu_pos(self):
        self.menu.update_idletasks()
        l, t, r, b = self._game_rect()
        w = max(self.menu.winfo_reqwidth(), self.menu.winfo_width(), 200)
        h = max(self.menu.winfo_reqheight(), self.menu.winfo_height(), 120)
        self.menu.geometry(f"+{l + ((r - l) - w) // 2}+{t + ((b - t) - h) // 2}")

    def _place_hint(self):
        """Top-middle of the game window. Re-run each time it's shown: it has no
        saved position, and the game window may have moved or resized."""
        self.hintwin.update_idletasks()
        l, t, r, _b = self._game_rect()
        w = max(self.hintwin.winfo_reqwidth(), self.hintwin.winfo_width(), 10)
        self.hintwin.geometry(f"+{l + ((r - l) - w) // 2}+{t + TOP_STRIP_HINT}")

    def _load_settings(self):
        """Read the saved settings, before any widget is built from them.

        Every value is validated against what the build actually offers rather
        than trusted: a file written by a newer version, or hand-edited, should
        cost you that one setting and not the meter."""
        try:
            data = json.loads(SETTINGS_CACHE.read_text())
        except Exception:
            return                      # absent or unreadable => defaults
        if not isinstance(data, dict):
            return
        # Aliased before the check, so a value written by an older build lands
        # on the entry that draws what that build drew rather than falling
        # through to the default and quietly changing someone's overlay.
        theme = THEME_MODE_ALIASES.get(data.get("theme"), data.get("theme"))
        if theme in THEME_MODES:
            self._theme_mode = theme
        t = data.get("transparency")
        if isinstance(t, int) and 0 <= t <= TRANSPARENCY_MAX:
            self._transparency = t
        # Validated key by key: a hand-edited or newer file shouldn't be able
        # to leave the meter with a binding the hook can't match.
        bind = data.get("reset_bind")
        if isinstance(bind, dict) and isinstance(bind.get("vk"), int):
            vk = bind["vk"]
            if 0 < vk <= 0xFF and vk not in VK_UNBINDABLE:
                RESET_BIND.update(
                    {"vk": vk} | {m: bool(bind.get(m))
                                  for m in ("shift", "ctrl", "alt")})
        if data.get("mode") in ("party", "all"):
            self.mode = data["mode"]
        if isinstance(data.get("hide_ooc"), bool):
            self._hide_ooc = data["hide_ooc"]
        if isinstance(data.get("screen2"), bool):
            self._screen2 = data["screen2"]
        if isinstance(data.get("auto_reset_boss"), bool):
            self._auto_reset_boss = data["auto_reset_boss"]
        if isinstance(data.get("rift_auto_view"), bool):
            self._rift_auto_view = data["rift_auto_view"]
        if isinstance(data.get("history_on"), bool):
            self._history_on = data["history_on"]
        # Scales can only be applied once the fonts exist, so they're parked
        # here and used after the windows are built.
        saved = data.get("scales")
        if not isinstance(saved, dict):
            # Written by the build with one global slider. The global value
            # becomes the meter's, which is the window it mostly stood for.
            saved = {"meter": data.get("ui_scale")}
        pending = {}
        for group, _label in SCALE_GROUPS:
            lo, hi = UI_SCALE_MIN, UI_SCALE_MAX
            try:
                v = float(saved.get(group))
            except (TypeError, ValueError):
                continue
            if lo <= v * 100 <= hi:
                pending[group] = v
        self._pending_scales = pending
        if isinstance(data.get("show_heal"), bool):
            self._show_heal = data["show_heal"]
        # Heal sort can't outlive the column it sorts by, so a saved True is
        # only honoured while the healing columns are on (the toggles keep
        # that invariant; this covers a hand-edited file).
        if isinstance(data.get("sort_heal"), bool):
            self._sort_heal = data["sort_heal"] and self._show_heal
        show = data.get("show")
        if isinstance(show, dict):
            for key, _label in TOGGLEABLE_ELEMENTS:
                v = show.get(key)
                if v in ELEMENT_MODES:
                    self._show[key] = v
                elif isinstance(v, bool):
                    # Written by a build that had ticks rather than modes. An
                    # unticked box then meant "hidden while playing, back when
                    # the menu opens", which is exactly Show in ESC — so the
                    # setting survives the upgrade instead of silently changing
                    # behaviour.
                    self._show[key] = ELEMENT_SHOW if v else ELEMENT_ESC
            self._shown = _initial_shown()

    def _save_settings(self):
        """Write the settings out. Called on every change rather than at exit —
        the meter is normally stopped from the tray or displaced by a newer
        instance, and neither is a good moment to be doing first-time IO."""
        try:
            SETTINGS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_CACHE.write_text(json.dumps({
                "theme": self._theme_mode,
                "transparency": self._transparency,
                "reset_bind": dict(RESET_BIND),
                "mode": self.mode,
                "hide_ooc": self._hide_ooc,
                "screen2": bool(self._screen2),
                "auto_reset_boss": bool(self._auto_reset_boss),
                "rift_auto_view": bool(self._rift_auto_view),
                "scales": {g: round(self._scales[g], 3)
                           for g, _label in SCALE_GROUPS},
                "show": {k: self._show.get(k, ELEMENT_SHOW)
                         for k, _label in TOGGLEABLE_ELEMENTS},
                "show_heal": bool(self._show_heal),
                "sort_heal": bool(self._sort_heal),
                "history_on": bool(self._history_on),
            }, indent=2))
        except OSError as e:
            print(f"[meter] couldn't save settings: {e}", file=sys.stderr)

    @staticmethod
    def _win_xy(win):
        """A window's position as its geometry string states it — the same
        reference point geometry("+x+y") sets, title bar or not."""
        m = re.match(r"\d+x\d+[+-](-?\d+)[+-](-?\d+)", win.geometry())
        if m:
            return int(m.group(1)), int(m.group(2))
        return win.winfo_x(), win.winfo_y()

    def _save_pos(self):
        self._save_settings()
        mem = self._pos_mem
        # The meter, breakdown and report each keep one position per mode, so
        # switching back and forth puts them back where they were in each.
        prefix = "s2_" if self._screen2 else ""
        mem[prefix + "meter"] = self._win_xy(self.root)
        mem[prefix + "detail"] = self._win_xy(self.detail)
        if self._screen2 and self._report_open:
            mem["s2_report"] = self._win_xy(self.reportwin)
        mem["menu"] = (self.menu.winfo_x(), self.menu.winfo_y())
        mem["rift"] = (self.riftwin.winfo_x(), self.riftwin.winfo_y())
        out = {
            # Stamps which coordinate space these are in, so the next load
            # knows whether they need migrating — see _load_positions.
            "space": "physical",
            # Whatever the panel last told us it was — see MenuBridge.geom.
            # Falls back to the geometry we started it with, so closing the
            # meter without ever having moved the panel doesn't wipe it.
            "panel": (self.menubridge.geom or self._panel_geom or {}),
        }
        for key, xy in mem.items():
            if key != "panel" and isinstance(xy, (tuple, list)):
                out[key] = {"x": int(xy[0]), "y": int(xy[1])}
        try:
            POSITION_CACHE.write_text(json.dumps(out))
        except OSError:
            pass

    def _build_meter(self):
        self.m_border = border = tk.Frame(self.root, bg=BG_BORDER, padx=2, pady=2)
        border.pack(fill="both", expand=True)

        self.header = tk.Frame(border, bg=BG_HEADER)
        self.header.pack(fill="x")
        self.title_lbl = tk.Label(self.header, text="Farever+ — Compteur",
                                  bg=BG_HEADER, fg=FG_HEADER,
                                  font=self.fonts["ui_b"], anchor="w",
                                  padx=8, pady=4)
        self.title_lbl.pack(side="left")
        self.timer_lbl = tk.Label(self.header, text="", bg=BG_HEADER, fg=FG_HEADER,
                                  font=self.fonts["mono"], padx=8)
        self.timer_lbl.pack(side="right")
        # Flips the row order between the two columns. The label is the
        # STATE, not the destination — "▼ Damage" while damage-sorted, like
        # a sorted column header — after the destination reading shipped
        # first and read as a lie about what the rows already showed.
        # Outside the drag binding below on purpose (the header drags, the
        # button clicks), and only on screen while the healing columns are:
        # sorting by a column that isn't drawn would order the rows by
        # invisible numbers.
        self.sort_btn = tk.Button(self.header, text=self._sort_btn_text(),
                                  command=self._enqueue(self._toggle_sort),
                                  bg=BG_HEADER, fg=FG_HEADER,
                                  activebackground=BG_HEADER,
                                  activeforeground=FG_HEADER,
                                  font=self.fonts["ui_sm_b"],
                                  relief="flat", bd=0, padx=6, pady=0,
                                  cursor="hand2", highlightthickness=0)
        if self._show_heal:
            self.sort_btn.pack(side="right")
        self._bind_drag(self.root, (self.header, self.title_lbl),
                        unlocked=self._mouse_available)

        self.m_body = body = tk.Frame(border, bg=BG_BODY, padx=8, pady=6)
        body.pack(fill="both", expand=True)

        self.overview_title = tk.Label(body, text="GROUPE", bg=BG_BODY,
                                       fg=ACCENT, font=self.fonts["ui_sm_b"],
                                       anchor="w")
        self.overview_title.pack(fill="x")
        # Same font/size as the rows so the monospace columns line up exactly.
        self.cols_lbl = tk.Label(
            body, text=self._meter_cols_text(),
            bg=BG_BODY, fg=FG_DIM, font=self.fonts["mono_10"], anchor="w")
        self.cols_lbl.pack(fill="x", pady=(2, 0))
        self.rows_box = rows_box = tk.Frame(body, bg=BG_BODY)
        rows_box.pack(fill="x", pady=(1, 2))
        # pack stops managing a container the moment its last slave is forgotten
        # — it keeps whatever size it last asked for. Without this 1 px keeper
        # the meter would stay as tall as the biggest party it ever showed once
        # a reset empties the rows. Packed to the bottom so row order is
        # untouched.
        self.rows_keeper = tk.Frame(rows_box, bg=BG_BODY, height=1, width=1)
        self.rows_keeper.pack(side="bottom")
        self.player_rows = [PlayerRow(rows_box, self._on_row_click, self.fonts)
                            for _ in range(MAX_PLAYER_ROWS)]

        self.root.minsize(MIN_W["meter"], 0)

    def _meter_cols_text(self):
        head = (f"  #  {'NOM':<{METER_NAME_CELLS}}{'CL.':<{METER_CLASS_CELLS}}"
                f"{'DÉGÂTS':>9} {'DPS':>6} {'%':>4}")
        # OVER% rides with the healing columns because it is a share OF them:
        # on its own, next to a damage table, it would be a percentage of a
        # number that isn't on screen.
        return head + (f"{'SOINS':>9}{'EXCÈS':>6}" if self._show_heal else "")

    def _build_detail(self):
        self.d_border = border = tk.Frame(self.detail, bg=BG_BORDER, padx=2, pady=2)
        border.pack(fill="both", expand=True)

        self.d_header = tk.Frame(border, bg=BG_HEADER)
        self.d_header.pack(fill="x")
        self.d_title = tk.Label(self.d_header, text="Détail",
                                bg=BG_HEADER, fg=FG_HEADER,
                                font=self.fonts_d["ui_b"], anchor="w",
                                padx=8, pady=4)
        self.d_title.pack(side="left")
        # Sits in the header rather than the body so it reads as a caption on
        # the window instead of another data row. It tints with the header.
        self.d_tip = tk.Label(self.d_header,
                              text="Clique sur un joueur du compteur pour voir son détail",
                              bg=BG_HEADER, fg=FG_HEADER_DIM,
                              font=self.fonts_d["ui_tiny_i"], anchor="e", padx=8)
        self.d_tip.pack(side="right")
        self._bind_drag(self.detail, (self.d_header, self.d_title, self.d_tip))

        self.d_body = body = tk.Frame(border, bg=BG_BODY, padx=8, pady=6)
        body.pack(fill="both", expand=True)

        # The body is a sidebar and then the tables. The run's headline numbers
        # used to be one "·"-separated mono line stretched across the top,
        # which made them read as a row of the table rather than a summary of
        # it — and at a glance "347 hits" and "20% crit" were indistinguishable
        # from each other because nothing but a separator told them apart.
        # As a column they get a caption each and can be scanned vertically.
        #
        # It sits on `soft` rather than a colour of its own: every theme
        # already defines soft as one subtle step off body (it is the column
        # rule and the separators), so the sidebar reads as an inset panel in
        # the parchment, dark and rift skins alike without inventing a fourth
        # value that each new theme would have to remember to set.
        self.d_split = split = tk.Frame(body, bg=BG_BODY)
        split.pack(fill="both", expand=True)

        self.d_side = tk.Frame(split, bg=BG_BODY_SOFT, padx=9, pady=5)
        self.d_side.pack(side="left", fill="y")
        # A fixed pool shown as a prefix, exactly like SkillColumn's rows: the
        # visible stats change with what the player is doing (no healer has an
        # overheal line, most people have no kills), and creating widgets on a
        # refresh tick is what makes a panel flicker.
        self.side_rows = []
        for _ in range(MAX_STAT_ROWS):
            rf = tk.Frame(self.d_side, bg=BG_BODY_SOFT)
            # pady=0 on both: Tk pads a label by a pixel top and bottom by
            # default, which across seven two-line stats is most of a stat row
            # of pure air — and the sidebar is what sets the window's height.
            cap = tk.Label(rf, text="", bg=BG_BODY_SOFT, fg=FG_DIM,
                           font=self.fonts_d["ui_sm_b"], anchor="w", pady=0)
            cap.pack(fill="x")
            val = tk.Label(rf, text="", bg=BG_BODY_SOFT, fg=FG_VALUE,
                           font=self.fonts_d["mono_stat_b"], anchor="w", pady=0)
            val.pack(fill="x")
            self.side_rows.append((rf, cap, val))
        self._side_shown = 0
        # Idle text lives in the sidebar too, so the panel is never a bare
        # coloured rectangle with nothing in it.
        self.side_idle = tk.Label(self.d_side, text="en attente\nd'un combat…",
                                  bg=BG_BODY_SOFT, fg=FG_DIM,
                                  font=self.fonts_d["ui"], anchor="w",
                                  justify="left")

        self.side_sep = tk.Frame(split, bg=BG_BODY_SOFT, width=1)
        self.side_sep.pack(side="left", fill="y", padx=(0, 8))

        self.d_right = right = tk.Frame(split, bg=BG_BODY)
        right.pack(side="left", fill="both", expand=True)

        self.d_cols = cols = tk.Frame(right, bg=BG_BODY)
        cols.pack(fill="x", pady=(0, 2))
        self.dmg_col = SkillColumn(cols, "DÉGÂTS", DMG_BAR, self.fonts_d)
        self.dmg_col.f.pack(side="left", anchor="n")
        # Kept as attributes so the healing toggle can unpack them; re-packing
        # in this order puts them back to the right of the damage column.
        self.col_sep = tk.Frame(cols, bg=BG_BODY_SOFT, width=1)
        self.col_sep.pack(side="left", fill="y", padx=6)
        self.heal_col = SkillColumn(cols, "SOINS", HEAL_BAR, self.fonts_d)
        self.heal_col.f.pack(side="left", anchor="n")

        # Stays with the tables rather than the sidebar: it is a breakdown OF
        # the damage column, and under it is where it reads as one.
        self.elem_lbl = tk.Label(right, text="", bg=BG_BODY, fg=FG_DIM,
                                 font=self.fonts_d["mono_sm"], anchor="w", justify="left")
        self.elem_lbl.pack(fill="x", pady=(3, 0))
        self.detail.minsize(MIN_W["detail"], 0)

    def _set_side_stats(self, pairs):
        """Show `pairs` [(caption, value)] in the sidebar, hiding the rest.

        A prefix of the fixed pool, so pack order never changes and no widget
        is created on a refresh tick."""
        if pairs:
            self.side_idle.pack_forget()
        n = min(len(pairs), len(self.side_rows))
        for i, (rf, cap, val) in enumerate(self.side_rows):
            if i < n:
                cap.config(text=pairs[i][0])
                val.config(text=pairs[i][1])
                if i >= self._side_shown:
                    # pady on the row rather than between rows: the first stat
                    # sits tight to the top of the panel and every later one
                    # gets the same gap above it.
                    rf.pack(fill="x", pady=(0 if i == 0 else 3, 0))
            elif i < self._side_shown:
                rf.pack_forget()
        self._side_shown = n
        if not pairs:
            self.side_idle.pack(fill="x")

    # ---- the settings panel, as data -----------------------------------
    # The panel is a WebView2 window in another process (MenuBridge, and
    # menu_host.py) and it is a renderer, not a designer: everything below
    # decides what it draws. Keeping the layout here rather than in the HTML is
    # what keeps adding a setting a one-file change, exactly as it was when the
    # panel was Tk widgets — and it means the labels and the state that feeds
    # them cannot drift apart, because they are computed together.

    @staticmethod
    def _tick(on, label):
        """The panel's checkbox convention, unchanged from the Tk menu: a
        standing setting reads as its STATE, not as the action that would
        change it."""
        return ("☑  " if on else "☐  ") + label

    def _menu_spec(self):
        """Everything the panel needs to draw itself, right now.

        Rebuilt whole on each refresh tick and compared against the last one by
        MenuBridge.push, so an unchanged panel costs one dict comparison rather
        than a pipe write and a re-render.
        """
        return {
            "version": VERSION,
            "zoom": int(round(self._scales.get("menu", 1.0) * 100)),
            "shard": self.ui_state.server() or "",
            # Two clicks to stop: a misclick that ends the meter mid-fight
            # takes the encounter with it, which is worth one extra click to
            # rule out. The arming is state on this side and disarms itself
            # after four seconds — see _quit_clicked.
            "quit": ("Clique encore pour arrêter" if self._quit_armed
                     else QUIT_LABEL),
            "quitArmed": bool(self._quit_armed),
            "banner": self._menu_banner(),
            "tab": self._menu_tab,
            "tabs": [{"v": t, "t": MENU_TAB_LABELS.get(t, t)}
                     for t in MENU_TABS],
            "page": self._menu_page(self._menu_tab),
        }

    def _menu_banner(self):
        """The panel's top line: how to stop the meter."""
        return {"t": SHUTDOWN_HINT, "update": False}

    # -- Help -------------------------------------------------------------
    @staticmethod
    def _help_articles():
        """Every article on disk, parsed once and cached.

        Cached on the function rather than the instance because the files are
        read-only assets — re-reading them on every refresh tick would be four
        file opens a second for prose that cannot change while the meter runs.
        """
        cached = getattr(Overlay._help_articles, "_cache", None)
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
        Overlay._help_articles._cache = out
        return out

    @staticmethod
    def _support_logo_uri():
        """The fundraiser logo as a data URI, or None if it was never added.

        Cached on the function: it is read on every Help index build, and the
        file cannot change while the meter runs.
        """
        cached = getattr(Overlay._support_logo_uri, "_cache", "unset")
        if cached != "unset":
            return cached
        uri = None
        try:
            if SUPPORT_LOGO.is_file():
                import base64
                uri = ("data:image/png;base64,"
                       + base64.b64encode(
                           SUPPORT_LOGO.read_bytes()).decode("ascii"))
        except OSError as e:
            print(f"[meter] couldn't read the fundraiser logo: {e}",
                  file=sys.stderr)
        Overlay._support_logo_uri._cache = uri
        return uri

    def _support_block(self):
        """The fundraiser, at the top of the Help index.

        Top of Help rather than tucked into Actions: it is the one thing on the
        panel that is asking rather than telling, and burying an ask reads as
        more of an ask than putting it where it can be seen and scrolled past.
        """
        # One node rather than a logo plus two paragraphs, so the whole thing
        # is a single card the renderer can centre and tint as a unit — three
        # loose nodes could only ever be styled one at a time.
        return [
            {"k": "support", "id": "open_support", "t": "gofundme",
             "img": self._support_logo_uri(), "url": SUPPORT_URL,
             # Opening a link changes nothing on the panel, and the browser
             # does not come forward when one is already running — so without
             # this the click reads as having done nothing at all.
             "toast": "Ouvert dans ton navigateur",
             "paras": SUPPORT_BLURB.split("\n")},
            {"k": "gap"},
        ]

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
                        + art["blocks"]
                        + [{"k": "gap"},
                           {"k": "note", "t": "Le README complet de Brudr sur "
                                              "GitHub (en anglais) va plus loin."},
                           {"k": "button", "id": "open_repo",
                            "t": "Farever+ sur GitHub"}])
        # ...or the index, which opens with the fundraiser.
        seen, out = set(), self._support_block()
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

    def _menu_page(self, tab):
        """One tab's controls. A builder that raises costs its own page, not
        the panel and not the overlay — see _sync_panel for what a page builder
        raising used to take down with it."""
        try:
            return self._menu_page_inner(tab)
        except Exception as e:
            print(f"[meter] the {tab} page failed to build: {e!r}",
                  file=sys.stderr)
            return [{"k": "section", "t": MENU_TAB_LABELS.get(tab, tab)},
                    {"k": "note", "warn": True,
                     "t": f"Cette page n'a pas pu être construite : {e}. Le "
                          f"reste du panneau fonctionne, et le détail est "
                          f"dans le journal."}]

    def _menu_page_inner(self, tab):
        builder = {
            "General": self._page_general,
            "Windows": self._page_windows,
            "Actions": self._page_actions,
            "History": self._page_history,
            "Help": self._page_help,
        }.get(tab)
        return builder() if builder else []

    # -- General ----------------------------------------------------------
    def _page_general(self):
        all_players = self.mode == "all"
        parsing = self._parse_state is not None
        return [
            {"k": "section", "t": "Compteur"},
            {"k": "button", "id": "toggle_mode", "on": all_players,
             "t": ("Afficher le groupe seulement" if all_players
                   else "Afficher tous les joueurs")
                  + "   (réinitialise les données)"},
            {"k": "button", "id": "toggle_rift_auto_view",
             "t": self._tick(self._rift_auto_view,
                             "« Tous les joueurs » automatique en faille")},
            {"k": "note", "t": "Appuie sur le bouton ci-dessus à ta place à "
                               "l'entrée et à la sortie d'une faille — tous "
                               "les joueurs en entrant, le groupe seul en "
                               "sortant — au lieu de te demander. Chaque "
                               "bascule réinitialise le combat, comme "
                               "ci-dessus."},
            {"k": "button", "id": "toggle_auto_reset",
             "t": self._tick(self._auto_reset_boss,
                             "Réinitialiser au pull d'un boss")},
            {"k": "field", "t": "Réinitialiser",
             "c": {"k": "label", "t": ("appuie sur une touche…"
                                       if self._binding_now
                                       else bind_label())}},
            {"k": "button", "id": "begin_bind", "t": "Changer cette touche"},

            {"k": "section", "t": "Apparence"},
            {"k": "field", "t": "Thème",
             "c": {"k": "select", "id": "set_theme", "v": self._theme_mode,
                   "o": [{"v": m, "t": THEME_MODE_LABELS.get(m, m)}
                         for m in THEME_MODES]}},
            {"k": "field", "t": "Transparence",
             "c": {"k": "slider", "id": "set_transparency",
                   "v": self._transparency, "min": 0, "max": TRANSPARENCY_MAX,
                   "step": 5, "unit": "%"}},
            {"k": "field", "t": "Taille du panneau",
             "c": {"k": "slider", "id": "set_menu_zoom",
                   "v": int(round(self._scales.get("menu", 1.0) * 100)),
                   "min": 50, "max": 200, "step": 5, "unit": "%"}},
            {"k": "note", "t": "Ce panneau seulement, indépendamment de la "
                               "mise à l'échelle de Windows — que le compteur "
                               "ignore : un bureau à 300 % ne rend plus "
                               "l'overlay trois fois plus grand."},
            {"k": "gap"},
            {"k": "button", "id": "toggle_parse", "on": parsing,
             "t": (f"Arrêter le parse de {PARSE_LENGTH_SECS} s" if parsing
                   else f"Mode parse {PARSE_LENGTH_SECS} s")},
        ]

    # -- Windows ----------------------------------------------------------
    def _page_windows(self):
        """One row per window: what shows it, and how big it is. A window is
        one thing, so it gets one row — the pre-tabs menu listed the same five
        windows twice, a screen apart, under SCALING and SHOW / HIDE."""
        out = [{"k": "section", "t": "Chaque fenêtre : visibilité · taille"}]
        for key, label in TOGGLEABLE_ELEMENTS:
            out.append({"k": "field", "t": label,
                        "c": {"k": "select", "id": f"show:{key}",
                              "v": self._show.get(key, ELEMENT_SHOW),
                              "o": [{"v": m, "t": ELEMENT_MODE_LABELS[m]}
                                    for m in ELEMENT_MODES]}})
        for group, label in SCALE_GROUPS:
            if group == "menu":
                continue        # it has its own slider on General
            lo, hi = UI_SCALE_MIN, UI_SCALE_MAX
            out.append({"k": "field", "t": f"Taille : {label.lower()}",
                        "c": {"k": "slider", "id": f"scale:{group}",
                              "v": int(round(self._scales[group] * 100)),
                              "min": lo, "max": hi, "step": 5, "unit": "%"}})
        out += [
            {"k": "note", "t": "Le minuteur de faille utilise les polices du "
                               "compteur : il suit donc sa taille."},
            {"k": "section", "t": "2nd écran"},
            {"k": "button", "id": "toggle_screen2",
             "t": self._tick(self._screen2, "Mode 2nd écran")},
            {"k": "note", "t": "Le compteur, le détail et le rapport de faille "
                               "deviennent des fenêtres normales, avec une "
                               "barre de titre : déplace-les sur un autre "
                               "écran, ils ne passent plus jamais par-dessus "
                               "le jeu et restent affichés même quand tu "
                               "utilises une autre application. Le minuteur "
                               "de faille et les notifications restent sur "
                               "l'écran du jeu. Les réglages s'ouvrent aussi "
                               "depuis l'icône Farever+ près de l'horloge."},
            {"k": "section", "t": "Contenu"},
            {"k": "button", "id": "toggle_heal",
             "t": self._tick(self._show_heal, "Colonnes de soins")},
            {"k": "button", "id": "toggle_hide_ooc",
             "t": self._tick(self._hide_ooc, "Masquer hors combat")},
        ]
        return out


    # -- Actions ----------------------------------------------------------
    def _page_actions(self):
        have_report = self._report_data is not None
        return [
            {"k": "section", "t": "Parses"},
            # Greyed rather than hidden before the first rift: a button that
            # appears out of nowhere mid-session is one nobody knew to look for.
            {"k": "button", "id": "reopen_report",
             "tone": None if have_report else "disabled",
             "t": ("Dernier rapport de faille" if have_report
                   else "Dernier rapport de faille   (aucune faille encore)")},
            {"k": "button", "id": "open_parses",
             "t": "Dossier des parses et rapports"},
            {"k": "section", "t": "Réinitialisation"},
            # Exactly what the hotkey fires, labelled with the keybind — the
            # hotkey is the one that is useful mid-fight, when this panel is
            # not an option.
            {"k": "button", "id": "reset_data",
             "t": f"Réinitialiser le combat   ({bind_label()})"},
            {"k": "button", "id": "reset_pos",
             "t": "Réinitialiser la position des fenêtres"},
            {"k": "section", "t": "Projet"},
            {"k": "button", "id": "open_repo",
             "t": "Farever+ sur GitHub (projet d'origine)"},
        ]

    # -- the three list-backed tabs ---------------------------------------
    def _page_history(self):
        """The whole tab is one opt-in and what it unlocks. Off, the page is
        the switch and the paragraph explaining it — a folder path and an empty
        browser for a feature that is not recording anything reads as broken
        rather than unused."""
        out = [
            {"k": "section", "t": "Historique des combats"},
            {"k": "button", "id": "toggle_history",
             "t": self._tick(self._history_on,
                             "Garder un historique des combats terminés")},
            {"k": "note", "t": "Le compteur ne garde qu'un combat à la fois — "
                               "une réinitialisation, un changement de zone "
                               "ou le pull d'un boss l'efface. Avec cette "
                               "option, chaque combat terminé est d'abord "
                               "enregistré sur le disque, nommé d'après ce "
                               "qui a pris le plus de dégâts et l'endroit."},
        ]
        if not self._history_on:
            return out
        # One dataset opened: its breakdown as text, and the way back. Text
        # rather than a rebuilt table — the point of the page is the per-skill
        # detail the card has no room for, and it is the same text the Copy
        # button puts on the clipboard, so the two cannot disagree.
        if self._history_detail is not None:
            entry = self._history_detail
            body = ""
            try:
                body = self._history_text(entry)
            except Exception as e:
                body = f"Impossible de lire ce combat : {e!r}"
            return [
                {"k": "button", "id": "close_dataset",
                 "t": "‹  Retour à la liste"},
                {"k": "section", "t": entry.get("name") or "Combat"},
                {"k": "button", "id": "copy_history",
                 "t": "Copier dans le presse-papiers"},
                {"k": "code", "t": body},
                {"k": "note", "t": self._history_note_text or ""},
            ]
        rows = []
        for e in (self._history_entries or [])[:200]:
            # Summaries are plain dicts off HistoryStore.entries().
            name = e.get("name") or "Combat"
            # A summary's `zone` is a plain label string; the LOADED entry's is
            # a dict with a "label" in it (which is what _history_text reads).
            # Accepting both, because assuming the dict shape here is what took
            # the History tab — and with it the refresh loop — down.
            z = e.get("zone")
            where = (z.get("label") if isinstance(z, dict) else z) or ""
            when = date_fr(time.localtime(e.get("at") or 0))
            q = (self._history_query_text or "").strip().lower()
            if q and q not in f"{name} {where}".lower():
                continue
            btns = [{"id": "open_dataset", "t": "Ouvrir",
                     "p": {"path": e.get("path", "")}}]
            # Only rifts have a report to re-open.
            if e.get("kind") == "rift":
                btns.insert(0, {"id": "open_report", "t": "Rapport",
                                "p": {"path": e.get("path", "")}})
            rows.append({"t": name,
                         "meta": " · ".join(x for x in (where, when) if x),
                         "btns": btns})
        out += [
            {"k": "section", "t": "Emplacement"},
            {"k": "button", "id": "open_history_folder",
             "t": str(self._history.dir)},
            {"k": "note", "t": "Le compteur ne supprime jamais rien dans ce "
                               "dossier. Fais le ménage toi-même quand tu "
                               "veux récupérer de la place."},
            {"k": "section", "t": "Combats enregistrés"},
            {"k": "search", "id": "history_query",
             "v": (self._history_query_text or ""),
             "count": f"{len(rows)} affiché(s)"},
            {"k": "button", "id": "reload_history", "t": "Actualiser"},
            {"k": "list", "id": "history", "h": 300, "rows": rows,
             "empty": "Aucun combat terminé enregistré pour l'instant."},
            {"k": "note", "t": self._history_note_text or ""},
        ]
        return out




    # ---- what the panel is allowed to ask for --------------------------
    def _menu_actions(self):
        """id -> callable, for everything the panel can press.

        Built fresh rather than cached: several entries close over the tray
        currently being edited, and a table built once at start-up would keep
        pointing at tray 0 forever.

        Callables taking one argument receive the panel's parameter dict; the
        rest are existing meter methods that already take none. MenuBridge
        works out which by inspection rather than making every entry a lambda.
        """
        acts = {
            # -- General
            "toggle_mode": self._toggle_mode,
            "toggle_rift_auto_view": self._toggle_rift_auto_view,
            "toggle_auto_reset": self._toggle_auto_reset_boss,
            "begin_bind": self._begin_bind_capture,
            "set_theme": lambda p: self._set_theme_mode(p.get("value")),
            "set_transparency":
                lambda p: self._set_transparency(p.get("value", 0)),
            "set_menu_zoom":
                lambda p: self._set_menu_zoom(p.get("value", 100)),
            "toggle_parse": self._toggle_parse,
            # -- Windows
            "toggle_heal": self._toggle_heal,
            "toggle_hide_ooc": self._toggle_hide_ooc,
            "toggle_screen2": self._toggle_screen2,
            # -- Actions
            "reopen_report": self._reopen_report,
            "open_parses": self._open_parses,
            "reset_data": self._manual_reset,
            "reset_pos": self._reset_pos,
            "open_repo": self._open_repo,
            "quit": self._quit_clicked,
            # -- History
            "toggle_history": self._toggle_history,
            "open_history_folder": self._open_history_folder,
            "reload_history": self._reload_history,
            "open_dataset": lambda p: self._open_history_entry(
                {"path": p.get("path", "")}),
            "open_report": lambda p: self._open_history_report(
                {"path": p.get("path", "")}),
            "close_dataset": lambda: setattr(self, "_history_detail", None),
            "copy_history": self._copy_history,
            "history_query": lambda p: self._set_panel_query(
                "_history_query_text", p.get("value", "")),
            # -- the panel's own chrome
            "set_tab": lambda p: self._set_menu_tab(p.get("value")),
            "help_open": lambda p: setattr(self, "_help_open", p.get("id")),
            "help_close": lambda: setattr(self, "_help_open", None),
            "open_support": lambda: self._open_url(SUPPORT_URL),
            "banner_clicked": lambda: None,
            "escape": self._panel_escape,
            "boot": self._panel_booted,
            "rendered": lambda p: None,     # telemetry; nothing to do with it
        }
        # The generated ids: one per window and size slider. Built in a
        # loop for the same reason the Tk rows were — a filter added to the
        # tuple gets its control and its handler together, or neither.
        for key, _label in TOGGLEABLE_ELEMENTS:
            acts[f"show:{key}"] = (
                lambda p, k=key: self._on_element_pick(k, p.get("value")))
        for group, _label in SCALE_GROUPS:
            acts[f"scale:{group}"] = (
                lambda p, g=group: self._set_group_scale(
                    g, int(p.get("value", 100)) / 100))
        return acts



    def _set_panel_query(self, attr, text):
        """A search box changed. Stored on the overlay rather than in a Tk
        StringVar so the spec builder can read it back on the next tick."""
        setattr(self, attr, text or "")

    def _set_menu_zoom(self, pct):
        """The panel's own size. It is a CSS zoom inside the window rather
        than a font rebuild, so unlike the other groups nothing here has to
        touch a widget — it just has to be saved and pushed."""
        self._scales["menu"] = max(50, min(200, int(pct))) / 100
        self._save_settings()

    def _sync_panel(self, visible):
        """Show, hide and feed the settings panel — and never, ever raise.

        This is called from the middle of _refresh_visibility, which runs on
        the input pump. An exception escaping here does not merely lose a push:
        it abandons the rest of the visibility pass and then kills the pump,
        because input_loop only reschedules itself if the tick returned. The
        overlay freezes in whatever state it was in.

        That is not hypothetical — it shipped. A page builder raising made
        every tab except the two whose builders happened to work look dead
        (the panel kept showing the old page, since no push ever went out) and
        left the buff trays revealed for good, because the reveal expires in
        the part of _refresh_visibility that no longer ran.

        So the whole thing is wrapped, and the error is reported once per kind
        rather than 30 times a second.
        """
        try:
            self._sync_panel_inner(visible)
        except Exception as e:
            key = f"{type(e).__name__}:{e}"
            if key not in self._panel_errs:
                self._panel_errs.add(key)
                import traceback
                print(f"[meter] settings panel sync failed ({key}) — the "
                      f"overlay is unaffected:", file=sys.stderr)
                traceback.print_exc()

    def _sync_panel_inner(self, visible):
        if visible and self.menubridge.proc is None:
            # First open of the session — or a restart, if the panel died. A
            # fresh process starts hidden whatever we last thought, so the
            # edge test below has to be reset or the show would be skipped.
            self._panel_visible = False
            self._icon_sheet_sent = False       # a new process, a new page
            # Hand it the geometry we saved, so it comes back the size and
            # place it was left.
            self.menubridge.start(self.menubridge.geom or self._panel_geom)
        if not self.menubridge.alive():
            return
        # Persist the panel's position and size once a move or resize has
        # settled. _save_pos is otherwise only reached from the end of a TK
        # window drag, and the panel is not a Tk window — so without this its
        # geometry was reported, held in memory, and never written.
        if (self.menubridge.geom_at
                and time.monotonic() - self.menubridge.geom_at
                > PANEL_GEOM_SETTLE_SECS):
            self.menubridge.geom_at = 0.0
            self._panel_geom = dict(self.menubridge.geom)
            self._save_pos()
        if visible != self._panel_visible:
            self._panel_visible = visible
            self._panel_reassert = 0
            (self.menubridge.show if visible else self.menubridge.hide)()
        if visible:
            # Belt and braces on top of the edge test above. Topmost is not a
            # standing property — anything else claiming it takes it away, and
            # over a game that happens often — so the show is re-sent about
            # once a second while the panel should be up. show() is cheap and
            # idempotent on the far side: it re-asserts topmost and only calls
            # the real show() if the window is actually hidden.
            self._panel_reassert += 1
            if self._panel_reassert >= PANEL_REASSERT_TICKS:
                self._panel_reassert = 0
                self.menubridge.show()
            # Rebuilt a few times a second, or at once if something was
            # clicked — an action calls invalidate(), and waiting up to a fifth
            # of a second to redraw a button you just pressed would feel like a
            # dead button.
            if (self.menubridge.dirty()
                    or self._panel_reassert % PANEL_PUSH_TICKS == 0):
                self.menubridge.push(self._menu_spec())

    def _panel_booted(self):
        """The panel's page finished loading. Its idea of the state is nothing
        at all, so the next push has to go even if it matches the last one."""
        self.menubridge.invalidate()


    def _panel_typing(self, on):
        """A search box in the panel gained or lost the caret. Same handshake
        the Tk entries had: while the panel holds the keyboard the game cannot
        see its own Escape."""
        self._typing = bool(on)

    def _panel_escape(self):
        """Escape pressed inside the panel — close the game's menu with it.

        The panel is a real window in another process and it holds focus while
        you are using it, so its Escape never reaches Farever and the game's
        menu stays open. Handing focus back is not enough on its own: the
        keypress that caused this was consumed by the panel, so there is
        nothing left for the game to act on.

        So the key is replayed. Focus goes to the game first, then a synthetic
        Escape is sent one tick later — the delay matters, because a keystroke
        posted before the foreground change lands would be delivered to the
        window we are trying to leave.

        keybd_event rather than PostMessage: it is synthesised at the driver
        level, so it reaches the game whichever way it reads the keyboard,
        where a posted message only works for a window that pumps for it.
        """
        self._typing = False
        if self._panel_forced:
            # Opened from the tray: Escape closes the panel itself, and the
            # game's menu — which was never opened — is left alone.
            self._panel_forced = False
            self._refresh_visibility()
            return
        self._refocus_game()
        self.root.after(PANEL_ESC_REPLAY_MS, self._replay_escape)

    def _replay_escape(self):
        """The synthetic Escape, once the game has the foreground back."""
        if sys.platform != "win32":
            return
        try:
            # Only if the game really is frontmost. Firing this blind would
            # send an Escape into whatever the user alt-tabbed to instead.
            if self._foreground_pid() != self.target_pid:
                return
            u = ctypes.windll.user32
            KEYEVENTF_KEYUP = 0x0002
            u.keybd_event(0x1B, 0, 0, 0)                 # VK_ESCAPE down
            u.keybd_event(0x1B, 0, KEYEVENTF_KEYUP, 0)   # ...and up
        except Exception as e:
            print(f"[meter] couldn't replay Escape to the game: {e}",
                  file=sys.stderr)

    def _panel_closed(self):
        """The panel took itself off screen — alt-F4 reaching it, most likely.

        Clearing _panel_visible is the point. Without it the overlay still
        believes the panel is up, so the next time the escape menu opens the
        edge test in _sync_panel sees no change and sends no show — and the
        panel simply never comes back. Reported as "it isn't always appearing
        when I press Esc".
        """
        self._panel_visible = False
        self._panel_forced = False
        self.menubridge.invalidate()



    def _set_menu_tab(self, name):
        """Raise one settings page and paint its tab as the active one. The
        frames all live in the same grid cell, so this is a lift, not a
        re-layout."""
        # Validated against MENU_TABS, not against the retired Tk frames —
        # Help has no frame and was silently refused while this checked those.
        if name not in MENU_TABS:
            return
        # Leaving a page with a search box means you're done typing.
        if name != "History":
            self._stop_typing()
        # Leaving Help closes whatever article was open, so coming back lands
        # on the index. An article is somewhere you went to read one thing —
        # returning to the tab later and finding yourself mid-page, with no
        # memory of having opened it, reads as the panel having lost its place
        # rather than kept it.
        if name != "Help":
            self._help_open = None
        self._menu_tab = name
        # Arriving at a tab is what re-reads its data, below. Everything the
        # panel draws is built from that data on the next push, so there is no
        # widget to raise and nothing to repaint here.
        # History: the folder is re-read on arrival rather than
        # polled, so the list is current whenever you are looking at it and
        # costs nothing whenever you aren't.
        if name == "History" and self._history_on:
            self._reload_history()


    def _build_hint(self):
        """The one remaining keybind, as free-floating text over the game — no
        panel, no border, just a drop-shadowed line so it stays readable on
        whatever is behind it."""
        self.hint_canvas = tk.Canvas(self.hintwin, bg=TRANSPARENT_KEY,
                                     highlightthickness=0, bd=0)
        self.hint_canvas.pack()
        self._draw_hint()

    def _draw_hint(self):
        """Re-measured rather than sized once, so the scale slider moves it."""
        f, c = self.fonts["ui_hint_b"], self.hint_canvas
        pad, off = 6, 2
        text = reset_hint_text()
        w = f.measure(text) + pad * 2 + off
        h = f.metrics("linespace") + pad * 2 + off
        c.config(width=w, height=h)
        c.delete("all")
        c.create_text(pad + off, pad + off, text=text, font=f,
                      fill=BG_BORDER, anchor="nw")
        c.create_text(pad, pad, text=text, font=f,
                      fill=BG_BODY, anchor="nw")

























    def _build_rift(self):
        """The next-rift countdown. Styled after the rifts themselves rather
        than the meter — hot magenta rim over a near-black maroon interior — so
        it reads as belonging to the game's event, not to the damage meter.

        The panel sits on a canvas with RIFT_RIPPLE_MARGIN of transparent space
        around it, which is where the pulse's expanding square is drawn. Canvas
        items render behind embedded windows, so the panel covers the middle of
        the ripple and only the part outside it shows."""
        self.rift_canvas = tk.Canvas(self.riftwin, bg=TRANSPARENT_KEY,
                                     highlightthickness=0, bd=0)
        self.rift_canvas.pack()
        m = RIFT_RIPPLE_MARGIN
        self.rift_glow = glow = tk.Frame(self.rift_canvas, bg=RIFT_GLOW,
                                         padx=1, pady=1)
        self.rift_edge = edge = tk.Frame(glow, bg=RIFT_EDGE, padx=2, pady=2)
        edge.pack(fill="both", expand=True)
        self.rift_body = body = tk.Frame(edge, bg=RIFT_BODY,
                                         padx=14, pady=8)
        body.pack(fill="both", expand=True)

        self.rift_title = tk.Label(body, text="PROCHAINE FAILLE", bg=RIFT_BODY,
                                   fg=RIFT_TITLE, font=self.fonts["ui_sm_b"],
                                   anchor="w")
        self.rift_title.pack(fill="x")
        self.rift_lbl = tk.Label(body, text="Aucune faille à venir", bg=RIFT_BODY,
                                 fg=RIFT_TIME, font=self.fonts["ui_idle_i"],
                                 anchor="w")
        self.rift_lbl.pack(fill="x")
        self.rift_canvas.create_window(m, m, anchor="nw", window=glow)
        self._rift_panel = (0, 0)          # last panel size the canvas was cut to
        self._sync_rift_canvas()
        self._bind_drag(self.riftwin, (self.rift_canvas, glow, edge, body,
                                       self.rift_title, self.rift_lbl))

    def _sync_rift_canvas(self):
        """Keep the canvas exactly panel + margin. The panel changes width with
        the text ("No rift upcoming" is far wider than a countdown) and with the
        scale slider, so this is checked rather than set once."""
        self.rift_glow.update_idletasks()
        w = self.rift_glow.winfo_reqwidth()
        h = self.rift_glow.winfo_reqheight()
        if (w, h) == self._rift_panel:
            return
        self._rift_panel = (w, h)
        m = RIFT_RIPPLE_MARGIN
        self.rift_canvas.config(width=w + m * 2, height=h + m * 2)

    def _tick_rift_timer(self):
        """Count down to the top of the hour, which is when rifts open. For the
        first RIFT_QUIET_MINS past it, the rift that just opened is the current
        one — counting 59 minutes to the *next* one then would be misleading."""
        now = time.localtime()
        into_hour = now.tm_min * 60 + now.tm_sec
        if into_hour < RIFT_QUIET_MINS * 60:
            self.rift_title.config(text="MINUTEUR DE FAILLE")
            self.rift_lbl.config(text="Aucune faille à venir",
                                 font=self.fonts["ui_idle_i"])
            self._set_rift_box(RIFT_BOX_FAR)
            self._set_pulsing(False)
            self._rift_idle = True
            return
        self._rift_idle = False
        left = 3600 - into_hour
        mins, secs = divmod(left, 60)
        self.rift_title.config(text="PROCHAINE FAILLE")
        self.rift_lbl.config(text=f"{mins:02d}:{secs:02d}",
                             font=self.fonts["mono_xl_b"])
        # Three stages: ordinary while it's far off, rift-coloured inside 15
        # minutes, pulsing inside 5. Each one is a bigger nudge than the last.
        self._set_rift_box(RIFT_BOX_NEAR if left <= RIFT_STYLE_SECS
                           else RIFT_BOX_FAR)
        self._set_pulsing(left <= RIFT_PULSE_SECS)

    def _set_rift_box(self, style):
        """Repaint the countdown box. Guarded on the current style: this runs
        every tick and reconfiguring five widgets each time is pure waste."""
        if style is self._rift_box:
            return
        self._rift_box = style
        self.rift_glow.config(bg=style["glow"])
        self.rift_edge.config(bg=style["edge"])
        self.rift_body.config(bg=style["body"])
        self.rift_title.config(bg=style["body"], fg=style["title"])
        self.rift_lbl.config(bg=style["body"], fg=style["time"])

    def _set_pulsing(self, on):
        self._rift_pulse = on
        if on and self._pulse_job is None:
            self._pulse_job = self.root.after(RIFT_PULSE_MS, self._step_pulse)

    def _step_pulse(self):
        """Ramp the rim colour up and back on a cosine, so it breathes rather
        than blinks. Stops itself once the countdown is far enough out again, or
        the window isn't on screen to pulse."""
        self._pulse_job = None
        if not self._rift_pulse or not self._shown["rift"]:
            style = self._rift_box or RIFT_BOX_NEAR
            self.rift_glow.config(bg=style["glow"])
            self.rift_edge.config(bg=style["edge"])
            self.rift_title.config(fg=style["title"])
            self.rift_canvas.delete("ripple")
            return
        phase = (time.monotonic() % RIFT_PULSE_PERIOD) / RIFT_PULSE_PERIOD
        k = (1 - math.cos(phase * 2 * math.pi)) / 2
        self.rift_edge.config(bg=_lerp_hex(RIFT_EDGE, RIFT_PEAK, k))
        self.rift_glow.config(bg=_lerp_hex(RIFT_GLOW, RIFT_EDGE, k))
        self.rift_title.config(fg=_lerp_hex(RIFT_TITLE, "#FFFFFF", k))
        self._draw_ripple(phase)
        self._pulse_job = self.root.after(RIFT_PULSE_MS, self._step_pulse)

    def _draw_ripple(self, phase):
        """One square, expanding out of the panel's edge and thinning as it
        goes. Tk canvas has no alpha, so the fade is done with `outlinestipple`
        — progressively sparser dither patterns let more of the game through,
        which over a transparent-key canvas reads as fading out."""
        self._sync_rift_canvas()
        c, m = self.rift_canvas, RIFT_RIPPLE_MARGIN
        c.delete("ripple")
        pw, ph = self._rift_panel
        if not pw:
            return
        # It has to be gone *before* it reaches the canvas edge. Run it to the
        # boundary and Tk clips the outline into a hard rectangle sitting on the
        # window's rim, which reads as a permanent border the ripple flies out
        # to rather than something dissipating.
        if phase > RIFT_RIPPLE_FADEOUT:
            return
        travel = phase / RIFT_RIPPLE_FADEOUT      # 0..1 over the visible part
        out = (m - 3) * travel
        x0, y0 = m - out, m - out
        x1, y1 = m + pw + out, m + ph + out
        stipple = ("", "gray75", "gray50", "gray25", "gray12")[
            min(4, int(travel * 5))]
        c.create_rectangle(x0, y0, x1, y1, outline=RIFT_PEAK, width=2,
                           outlinestipple=stipple, tags="ripple")

    def _build_prompt(self):
        """The rift prompts. Styled like the rifts rather than the meter — they
        only ever appear because of one, and the colour is what tells you at a
        glance which of the two questions you're being asked isn't a meter
        setting."""
        glow = tk.Frame(self.promptwin, bg=RIFT_GLOW, padx=1, pady=1)
        glow.pack(fill="both", expand=True)
        border = tk.Frame(glow, bg=RIFT_EDGE, padx=2, pady=2)
        border.pack(fill="both", expand=True)
        header = tk.Frame(border, bg=RIFT_GLOW)
        header.pack(fill="x")
        tk.Label(header, text="RIFT", bg=RIFT_GLOW, fg=RIFT_TIME,
                 font=self.fonts["ui_b"], anchor="w",
                 padx=12, pady=6).pack(side="left")

        body = tk.Frame(border, bg=RIFT_BODY, padx=18, pady=16)
        body.pack(fill="both", expand=True)
        self.prompt_title = tk.Label(body, text="", bg=RIFT_BODY, fg=RIFT_TIME,
                                     font=self.fonts["ui_lg_b"], anchor="w")
        self.prompt_title.pack(fill="x")
        self.prompt_question = tk.Label(body, text="", bg=RIFT_BODY,
                                        fg=RIFT_TITLE, font=self.fonts["ui_10"],
                                        anchor="w", pady=8)
        self.prompt_question.pack(fill="x")

        # "Do this every time" IS the General tab's setting, offered where the
        # question is being asked. Ticking it here and turning it on there are
        # the same act, so the two can't disagree — see _answer_rift for why it
        # only commits on Yes.
        self._prompt_every_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            body, text="Toujours faire ça", variable=self._prompt_every_var,
            bg=RIFT_BODY, fg=RIFT_TITLE, activebackground=RIFT_BODY,
            activeforeground=RIFT_TIME, selectcolor=RIFT_GLOW,
            font=self.fonts["ui_10"], anchor="w",
            highlightthickness=0, bd=0, cursor="hand2").pack(fill="x")

        btns = tk.Frame(body, bg=RIFT_BODY)
        btns.pack(fill="x", pady=(10, 0))

        def answer_button(text, on, yes):
            b = tk.Button(btns, text=text, command=on,
                          font=self.fonts["ui_b"],
                          bg=RIFT_EDGE if yes else RIFT_BODY,
                          fg="#2C0A1E" if yes else RIFT_TITLE,
                          activebackground=RIFT_TITLE if yes else RIFT_GLOW,
                          activeforeground="#2C0A1E" if yes else RIFT_TIME,
                          relief="flat", bd=0, padx=30, pady=8,
                          highlightthickness=1, cursor="hand2",
                          highlightbackground=RIFT_EDGE)
            b.pack(side="left", expand=True, fill="x", padx=4)
            return b

        answer_button("Oui", self._enqueue(lambda: self._answer_rift(True)), True)
        answer_button("Non", self._enqueue(lambda: self._answer_rift(False)), False)
        self.promptwin.minsize(MIN_W["prompt"], 0)





    def _open_rift_prompt(self, kind):
        self._prompt_kind = kind
        # Opens unticked every time. The prompt only exists while the setting
        # is off, so a ticked box would be claiming a state that isn't real.
        self._prompt_every_var.set(False)
        if kind == "enter":
            self.prompt_title.config(text="Tu es entré dans une faille")
            self.prompt_question.config(text="Afficher tous les joueurs ?")
        else:
            self.prompt_title.config(text="Tu as quitté la faille")
            self.prompt_question.config(
                text="Revenir à l'affichage du groupe seulement ?")
        self._prompt_open = True
        self.promptwin.update_idletasks()
        l, t, r, b = self._game_rect()
        w = max(self.promptwin.winfo_reqwidth(), 300)
        h = max(self.promptwin.winfo_reqheight(), 120)
        self.promptwin.geometry(f"+{l + ((r - l) - w) // 2}+{t + ((b - t) - h) // 2}")
        self._apply_clickthrough()      # the prompt has to be clickable
        self._refresh_visibility()
        print(f"[meter] {'entered' if kind == 'enter' else 'left'} a rift — "
              "asking about the player view.", file=sys.stderr)

    def _close_rift_prompt(self):
        self._prompt_open = False
        self._refresh_visibility()

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

    def _answer_rift(self, yes):
        """Yes switches the view; No leaves it alone.

        "Do this every time" only commits on Yes, because it is shorthand for
        "that answer, standing" — and No is the answer that says the meter
        shouldn't be touching the view. Ticking it and then pressing No is a
        contradiction, so No wins and the box is discarded."""
        if yes:
            self._apply_rift_view(self._prompt_kind)
            if self._prompt_every_var.get() and not self._rift_auto_view:
                self._rift_auto_view = True
                self._save_settings()
                print("[meter] rift view switching is now automatic.",
                      file=sys.stderr)
        self._close_rift_prompt()
        print(f"[meter] rift prompt answered: {'yes' if yes else 'no'}",
              file=sys.stderr)

    def _toggle_rift_auto_view(self):
        """The General tab's half of the same setting. Turning it ON mid-rift
        doesn't retroactively switch the view — it's a rule for the next
        crossing, and silently binning the encounter you're in the middle of
        is not what pressing a settings toggle should do."""
        self._rift_auto_view = not self._rift_auto_view
        self._save_settings()
        # An open prompt is now answering a question that has a standing
        # answer. Honour it rather than leaving a stale box on screen.
        if self._rift_auto_view and self._prompt_open:
            self._apply_rift_view(self._prompt_kind)
            self._close_rift_prompt()

    def _tick_rift(self):
        """One prompt per rift entry, and it goes away by itself if the rift
        does — an unanswered box shouldn't outlive what it was asking about.

        With the standing answer on there is no box at all: the crossing is
        acted on directly, in both directions."""
        in_rift = self.ui_state.in_rift()
        if in_rift == self._rift_seen:
            return
        self._rift_seen = in_rift
        # Leaving is a question in its own right rather than a dismissal: the
        # all-players view you switched on for the rift is the wrong one to be
        # left holding once you're back outside.
        kind = "enter" if in_rift else "leave"
        if self._rift_auto_view:
            switched = self._apply_rift_view(kind)
            print(f"[meter] {'entered' if in_rift else 'left'} a rift — "
                  + ("switched the player view automatically." if switched
                     else "the player view was already right."),
                  file=sys.stderr)
            return
        self._open_rift_prompt(kind)

    # ---- end-of-rift report ----
    def _build_report(self):
        """The end-of-rift report card. Rift-styled like the prompts — it only
        ever exists because of one — and clickable whenever it's up, for the
        same reason the prompt is: close and copy are the whole point.

        The chrome is built once; the numbers are torn down and rebuilt by
        _render_report, which only runs when the card opens or a tab is
        clicked — never on the refresh tick."""
        glow = tk.Frame(self.reportwin, bg=RIFT_GLOW, padx=1, pady=1)
        glow.pack(fill="both", expand=True)
        border = tk.Frame(glow, bg=RIFT_EDGE, padx=2, pady=2)
        border.pack(fill="both", expand=True)
        header = tk.Frame(border, bg=RIFT_GLOW)
        header.pack(fill="x")
        # Stamped with the kill time on open — the card can be brought back
        # from the menu long after the rift, and an unstamped one reads as
        # current.
        self._report_title = tk.Label(header, text="RAPPORT DE FAILLE",
                                      bg=RIFT_GLOW, fg=RIFT_TIME,
                                      font=self.fonts["ui_b"], anchor="w",
                                      padx=12, pady=6)
        title = self._report_title
        title.pack(side="left")
        tk.Button(header, text="✕", command=self._enqueue(self._close_report),
                  font=self.fonts["ui_b"], bg=RIFT_GLOW, fg=RIFT_TITLE,
                  activebackground=RIFT_EDGE, activeforeground="#2C0A1E",
                  relief="flat", bd=0, padx=10, cursor="hand2",
                  highlightthickness=0).pack(side="right", fill="y")
        # The WHOLE card is the drag handle, not just the header. Every other
        # window earns its header-only handle by being interactive inside;
        # this one is a forced popup whose body is all labels, so anywhere
        # you can grab should move it. Binding the Toplevel reaches every
        # descendant through bindtags — including the body rows
        # _render_report rebuilds later, which per-widget binding would miss —
        # and _bind_drag's button guard keeps Copy/Close/✕ clickable.
        self._bind_drag(self.reportwin, (self.reportwin,),
                        unlocked=self._mouse_available)

        body = tk.Frame(border, bg=RIFT_BODY, padx=16, pady=12)
        body.pack(fill="both", expand=True)

        # Both phases at once, side by side — the card exists to compare the
        # AoE clear against the boss burn, and a comparison you have to click
        # between isn't one.
        self._report_body = tk.Frame(body, bg=RIFT_BODY)
        self._report_body.pack(fill="both", expand=True)

        footer = tk.Frame(body, bg=RIFT_BODY)
        footer.pack(fill="x", pady=(10, 0))
        tk.Button(footer, text="Copier", command=self._enqueue(self._copy_report),
                  font=self.fonts["ui_b"], bg=RIFT_EDGE, fg="#2C0A1E",
                  activebackground=RIFT_TITLE, activeforeground="#2C0A1E",
                  relief="flat", bd=0, padx=24, pady=5, cursor="hand2",
                  highlightthickness=1,
                  highlightbackground=RIFT_EDGE).pack(side="left")
        # Close lives down here as well as the header ✕: on a forced popup
        # the footer is where the hand already is after reading, and the ✕ is
        # a small target parked over the game.
        tk.Button(footer, text="Fermer",
                  command=self._enqueue(self._close_report),
                  font=self.fonts["ui_b"], bg=RIFT_EDGE, fg="#2C0A1E",
                  activebackground=RIFT_TITLE, activeforeground="#2C0A1E",
                  relief="flat", bd=0, padx=24, pady=5, cursor="hand2",
                  highlightthickness=1,
                  highlightbackground=RIFT_EDGE).pack(side="left", padx=(8, 0))
        # The copy feedback. Empty text rather than pack_forget when idle, so
        # the footer never changes height under the cursor.
        self._report_flash = tk.Label(footer, text="", bg=RIFT_BODY,
                                      fg=RIFT_TITLE, font=self.fonts["ui"],
                                      anchor="w", padx=10)
        self._report_flash.pack(side="left", fill="x", expand=True)
        # Same wording as the minimap's tip: the card takes clicks whenever
        # it's up, but there's only a cursor to click with once the game lets
        # go of it, which isn't something you'd guess.
        tk.Label(body, text="Appuie sur Alt gauche ou Échap pour libérer la souris",
                 bg=RIFT_BODY, fg=RIFT_GLOW, font=self.fonts["ui_tiny_i"],
                 anchor="w").pack(fill="x", pady=(6, 0))

    @staticmethod
    def _mmss(secs):
        m, s = divmod(int(max(0, secs)), 60)
        return f"{m}:{s:02d}"

    @staticmethod
    def _elide_name(name, width=14):
        return name if len(name) <= width else name[:width - 1] + "…"

    def _render_report(self):
        """Rebuild the card: both phase columns, leaderboard-weighted.

        Rows are frames with the name packed left and the numbers packed
        right, not one mono string — the ranks wear different font sizes, and
        mono-space alignment dies the moment two sizes share a column."""
        data = self._report_data
        if not data:
            return
        for w in self._report_body.winfo_children():
            w.destroy()
        cols = tk.Frame(self._report_body, bg=RIFT_BODY)
        cols.pack(fill="both", expand=True)
        # Uniform grid columns, so the two phases stay the same width however
        # long the names run — a comparison wants its columns comparable.
        cols.grid_columnconfigure(0, weight=1, uniform="phase")
        cols.grid_columnconfigure(2, weight=1, uniform="phase")
        cols.grid_rowconfigure(0, weight=1)
        for i, ph in enumerate(data["phases"]):
            if i:
                tk.Frame(cols, bg=RIFT_GLOW, width=1).grid(
                    row=0, column=1, sticky="ns", padx=12)
            col = tk.Frame(cols, bg=RIFT_BODY)
            col.grid(row=0, column=i * 2, sticky="nsew")
            self._render_phase_column(col, ph)

    def _render_phase_column(self, col, ph):
        def line(text, font="ui_10", fg=RIFT_TIME, pady=0):
            tk.Label(col, text=text, bg=RIFT_BODY, fg=fg,
                     font=self.fonts[font], anchor="w",
                     pady=pady).pack(fill="x")

        def heading(text):
            row = tk.Frame(col, bg=RIFT_BODY)
            row.pack(fill="x", pady=(10, 3))
            tk.Label(row, text=text, bg=RIFT_BODY, fg=RIFT_TITLE,
                     font=self.fonts["ui_sm_b"], anchor="w").pack(side="left")
            tk.Frame(row, bg=RIFT_GLOW, height=1).pack(
                side="left", fill="x", expand=True, padx=(8, 0))

        def rank_row(i, p, amount, pct, medal=True):
            """One leaderboard entry. Ranks 1-3 wear medal colours and the
            bigger font; 4-5 are body text — the tiering IS the design.

            Three numbers, in three weights, because they answer three
            different questions and only the first one is the headline: the
            RATE is what the card is for, the total is what it came from, and
            the share is how it compares. Packed right-to-left, so they end up
            rate, total, share.

            The class acronym is a label of its own in the dim colour, not part
            of the name string: it must not be eaten by the name's elision, and
            it is not part of who anyone is."""
            row = tk.Frame(col, bg=RIFT_BODY)
            row.pack(fill="x", pady=1)
            top3 = medal and i <= 3
            rank_fg = REPORT_MEDALS[i - 1] if top3 else RIFT_TITLE
            name_font = self.fonts["ui_rank_b" if top3 else "ui_10"]
            tk.Label(row, text=str(i), bg=RIFT_BODY, fg=rank_fg,
                     font=name_font, width=2, anchor="w").pack(side="left")
            tk.Label(row, text=self._elide_name(p.get("name") or "?"),
                     bg=RIFT_BODY, fg=RIFT_TIME if top3 else RIFT_TITLE,
                     font=name_font, anchor="w").pack(side="left")
            if p.get("cls"):
                tk.Label(row, text=p["cls"], bg=RIFT_BODY, fg=RIFT_TITLE,
                         font=self.fonts["ui_sm_b"], anchor="w").pack(
                    side="left", padx=(4, 0))
            tk.Label(row, text=f"{pct:4.0f}%", bg=RIFT_BODY, fg=RIFT_TITLE,
                     font=self.fonts["mono_sm"], anchor="e",
                     width=5).pack(side="right")
            tk.Label(row, text=f"{_n(amount)}", bg=RIFT_BODY,
                     fg=RIFT_TITLE, font=self.fonts["mono_sm"],
                     anchor="e", width=10).pack(side="right", padx=(0, 4))
            rate = _rate(amount, ph["duration"])
            tk.Label(row, text="—" if rate is None else f"{_n(rate)}",
                     bg=RIFT_BODY, fg=RIFT_TIME, font=self.fonts["mono_10"],
                     anchor="e", width=8).pack(side="right", padx=(0, 6))

        # The phase title is the column's headline. Under it the RATES, which
        # are what the report is now built around, and under those the totals
        # they were computed from — the same primary/subtext pairing every
        # block on this card uses.
        line(phase_label(ph["label"]).upper(), font="ui_b", fg=RIFT_PEAK)
        dps = _rate_text(ph["total"], ph["duration"], "DPS")
        hps = _rate_text(ph["heal"], ph["duration"], "HPS")
        line(f"{self._mmss(ph['duration'])}   ·   {dps or '— DPS'}"
             f"   ·   {hps or '— HPS'}", fg=RIFT_TIME)
        line(f"{_n(ph['total'])} dégâts   ·   {_n(ph['heal'])} soins"
             + _overheal_note(ph), font="ui_sm_b", fg=RIFT_TITLE)

        players = ph["players"]
        if not players:
            line("rien n'a été enregistré pour cette phase", font="ui_idle_i",
                 fg=RIFT_TITLE, pady=12)
            return

        # The MVP block: the phase's top damage, at headline size — this is
        # the line the card exists for. The top healer rides under it in their
        # own colour; sorted by damage already, so [0] is the damage MVP.
        heading("MVP")
        mvp = players[0]
        mvp_row = tk.Frame(col, bg=RIFT_BODY)
        mvp_row.pack(fill="x")
        tk.Label(mvp_row, text=f"★ {self._elide_name(mvp['name'])}",
                 bg=RIFT_BODY, fg=REPORT_MEDALS[0],
                 font=self.fonts["ui_mvp_b"], anchor="w").pack(side="left")
        if mvp.get("cls"):
            tk.Label(mvp_row, text=mvp["cls"], bg=RIFT_BODY, fg=RIFT_TITLE,
                     font=self.fonts["ui_sm_b"], anchor="s").pack(
                side="left", padx=(5, 0), pady=(0, 4))
        mvp_dps = _rate_text(mvp["total"], ph["duration"], "DPS")
        line(mvp_dps or f"{_n(mvp['total'])} dégâts", fg=RIFT_TIME)
        if mvp_dps:
            line(f"{_n(mvp['total'])} dégâts", font="ui_sm_b",
                 fg=RIFT_TITLE)
        healer = max(players, key=lambda p: p["heal"])
        if healer["heal"] > 0.5:
            hps_txt = _rate_text(healer["heal"], ph["duration"], "HPS")
            heal_total = f"{_n(healer['heal'])} soins"
            who = self._elide_name(healer["name"])
            if healer.get("cls"):
                who += f" ({healer['cls']})"
            tk.Label(col, text=f"✚ {who}   {hps_txt or heal_total}",
                     bg=RIFT_BODY, fg=REPORT_HEAL,
                     font=self.fonts["ui_rank_b"], anchor="w").pack(
                fill="x", pady=(2, 0))
            if hps_txt:
                line(heal_total + _overheal_note(healer, "   {:.0f}% en excès"),
                     font="ui_sm_b", fg=RIFT_TITLE)

        def rank_header(rate_label):
            """Names the three columns once, so the rows don't have to repeat
            a unit five times to be readable. Widths and packing order match
            rank_row exactly — they are read as one table."""
            row = tk.Frame(col, bg=RIFT_BODY)
            row.pack(fill="x")
            tk.Label(row, text="PART", bg=RIFT_BODY, fg=RIFT_TITLE,
                     font=self.fonts["mono_sm"], anchor="e",
                     width=5).pack(side="right")
            tk.Label(row, text="TOTAL", bg=RIFT_BODY, fg=RIFT_TITLE,
                     font=self.fonts["mono_sm"], anchor="e",
                     width=10).pack(side="right", padx=(0, 4))
            tk.Label(row, text=rate_label, bg=RIFT_BODY, fg=RIFT_TITLE,
                     font=self.fonts["mono_sm"], anchor="e",
                     width=10).pack(side="right", padx=(0, 6))

        heading("DÉGÂTS — TOP 5")
        rank_header("DPS")
        for i, p in enumerate(players[:5], 1):
            pct = p["total"] / ph["total"] * 100 if ph["total"] else 0.0
            rank_row(i, p, p["total"], pct)

        healers = sorted((p for p in players if p["heal"] > 0.5),
                         key=lambda p: -p["heal"])
        heading("SOINS — TOP 5")
        if not healers:
            line("aucun soin enregistré", font="ui_idle_i", fg=RIFT_TITLE)
        else:
            rank_header("HPS")
        for i, p in enumerate(healers[:5], 1):
            pct = p["heal"] / ph["heal"] * 100 if ph["heal"] else 0.0
            rank_row(i, p, p["heal"], pct)

        # Every type in its own colour — the bar and the name wear it, the
        # percentage stays quiet. Unknown affinities get a stable hash tint
        # from element_color, so a new patch element shows up coloured.
        heading("DÉGÂTS PAR TYPE")
        top = ph["elements"][0][1] if ph["elements"] else 0.0
        for el, amt in ph["elements"][:8]:
            pct = amt / ph["total"] * 100 if ph["total"] else 0.0
            # The table's colours are tuned mid-tone; lifted toward white here
            # because they have to read on the card's near-black body.
            color = _lerp_hex(element_color(el), "#FFFFFF", 0.30)
            row = tk.Frame(col, bg=RIFT_BODY)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=element_label(el), bg=RIFT_BODY,
                     fg=color, font=self.fonts["ui_sm_b"], width=9,
                     anchor="w").pack(side="left")
            bar = "▰" * max(1, round(amt / top * 12)) if top else ""
            tk.Label(row, text=bar, bg=RIFT_BODY, fg=color,
                     font=self.fonts["mono_sm"], anchor="w").pack(side="left")
            tk.Label(row, text=f"{_pct1(pct):>6}", bg=RIFT_BODY, fg=RIFT_TIME,
                     font=self.fonts["mono_sm"], anchor="e").pack(side="right")

    def show_rift_report(self, report):
        """A rift's boss died — freeze the card over the game. Called from the
        hook thread; everything real happens on the Tk one.

        The text version goes to disk the moment the report exists, before the
        card is even up — a card closed by reflex (it happened on day one)
        must not be the only copy of a run that can't be re-fought."""
        def open_():
            self._report_data = report
            self._save_rift_report(report)
            self._archive_rift_report(report)
            self._open_report_card()
        self._enqueue(open_)()

    def _open_report_card(self):
        """Open (or re-open) the card over whatever _report_data holds."""
        self._report_flash.config(text="")
        # A report can now outlive its session, so a bare clock isn't enough:
        # "21:03" on yesterday's run reads as tonight's. The date appears
        # exactly when it stops being obvious.
        lt = time.localtime(self._report_data["at"])
        when = (time.strftime("%H:%M", lt)
                if time.strftime("%Y%m%d", lt) == time.strftime("%Y%m%d")
                else date_fr(lt))
        self._report_title.config(text="RAPPORT DE FAILLE — " + when)
        self._render_report()
        self._report_open = True
        self.reportwin.update_idletasks()
        w = max(self.reportwin.winfo_reqwidth(), 300)
        h = max(self.reportwin.winfo_reqheight(), 200)
        saved = self._pos_mem.get("s2_report") if self._screen2 else None
        if saved and self._pos_visible(*saved):
            self.reportwin.geometry(f"+{saved[0]}+{saved[1]}")
        else:
            if self._screen2:
                # Centred on the meter's monitor, not over the game.
                mx, my = self._win_xy(self.root)
                l, t, r, b = (_monitor_containing(mx + 10, my + 10)
                              or self._game_rect())
            else:
                l, t, r, b = self._game_rect()
            self.reportwin.geometry(
                f"+{l + ((r - l) - w) // 2}+{t + ((b - t) - h) // 2}")
        self._apply_clickthrough()   # the card has to be clickable
        self._refresh_visibility()

    def _reopen_report(self):
        """The menu's 'Last rift report' — the card back, exactly as it was.
        The data is already frozen plain data, so there's nothing to rebuild;
        a no-op until the first rift of the session produces one."""
        if self._report_data is not None:
            self._open_report_card()

    # ---- combat history ----
    def _apply_history_setting(self):
        """Install or remove the primary store's archive hook.

        The filter goes on at the same time and stays on: it reads
        `self.mode` when it runs, so switching party/all needs no re-wiring
        (and switching resets the encounter anyway, so no dataset can ever
        straddle both)."""
        self.session.archive_hook = (self._archive_encounter
                                     if self._history_on else None)
        self.session.archive_filter = self._apply_mode

    def _toggle_history(self):
        self._history_on = not self._history_on
        self._apply_history_setting()
        self._save_settings()
        self._refresh_menu()
        if self._history_on:
            self._reload_history()

    def _archive_encounter(self, frozen):
        """PartySession handing over a finished encounter.

        Called on whichever thread performed the reset — the hook's thread on
        a zone change or a boss pull, the Tk thread on a hotkey — so it does
        no Tk work and no disk work: it stamps on the context only the overlay
        knows (where you were, which view you were on) and queues the write.
        """
        sig, world_map = self.ui_state.zone()
        name = _dataset_name(frozen.get("targets") or {}, sig, world_map)
        self._history.save(
            "combat", name, frozen,
            zone={"sig": sig, "label": _zone_label(sig, world_map),
                  "world_map": world_map},
            # Which rows the dataset holds, not just which view was up: the
            # mode is applied as a filter in _freeze, so a party dataset
            # contains your group and nobody else. Recorded because the
            # numbers can't be read honestly without it — "100%" in a party
            # dataset means 100% of the party.
            mode=self.mode)

    def _archive_rift_report(self, report):
        """A finished rift, into the same folder as everything else.

        The report keeps going to parses/ as .json/.txt/.png — that is what
        'Last Rift Report' reads back and what people paste into chat, and
        this must not disturb it. What this adds is the per-skill and
        per-element detail the card has no room for, so a rift in the history
        list opens as a report AND as a breakdown."""
        if not self._history_on:
            return
        sig, world_map = self.ui_state.zone()
        # Named off the BOSS phase's targets when it has any: a rift's trash
        # phase is a hundred small things and its boss phase is the one that
        # gives the run its name. Falls back to everything seen.
        phases = report.get("phases") or []
        targets = {}
        for ph in reversed(phases):        # boss phase last, so it wins
            targets = ph.get("targets") or targets
            if targets:
                break
        name = _dataset_name(targets, sig, world_map)
        self._history.save(
            "rift", name, report,
            zone={"sig": sig, "label": _zone_label(sig, world_map),
                  "world_map": world_map})

    def _open_history_folder(self):
        """Open the history folder in Explorer. Created on demand so the path
        is a real place to click even before the first dataset lands."""
        try:
            self._history.dir.mkdir(parents=True, exist_ok=True)
            os.startfile(self._history.dir)
        except Exception as e:
            print(f"[meter] couldn't open {self._history.dir}: {e}",
                  file=sys.stderr)



    def _history_note(self, text, transient=False):
        """The line under the browser. Same two jobs as Social's: explain an
        empty list, or confirm a copy that is otherwise invisible."""
        if self._history_note_job is not None:
            try:
                self.root.after_cancel(self._history_note_job)
            except Exception:
                pass
            self._history_note_job = None
        # A string, not a label — the panel reading it is another process.
        self._history_note_text = text
        if transient:
            def restore():
                self._history_note_job = None
                self._history_note(self._history_idle_note())
            self._history_note_job = self.root.after(2500, restore)

    def _history_idle_note(self):
        if not self._history_entries:
            return ("Rien d'enregistré pour l'instant. Un combat apparaît "
                    f"ici une fois qu'il a duré {HISTORY_MIN_SECS:.0f} s et "
                    f"compté {HISTORY_MIN_EVENTS} coups ou soins.")
        return ""

    def _reload_history(self):
        """Re-read the folder. The browser does not poll: a dataset lands when
        an encounter ends, and re-reading a folder four times a second to
        catch that would be the most expensive thing the menu does."""
        self._history_entries = self._history.entries()


    def _open_history_entry(self, summary):
        """Open one dataset's breakdown — the page the card has no room for."""
        entry = self._history.load(summary["path"])
        if entry is None:
            self._history_note("Impossible de lire ce combat.",
                               transient=True)
            return
        self._history_detail = entry

    def _open_history_report(self, summary):
        """A saved rift, back on screen AS a rift report.

        This replaces what 'Last Rift Report' will show, deliberately: the
        card you are looking at is the last report you opened, and having the
        button re-open a different one would be the surprise."""
        entry = self._history.load(summary["path"])
        data = (entry or {}).get("data") or {}
        if not isinstance(data.get("phases"), list):
            self._history_note("Ce combat n'est pas un rapport de faille.",
                               transient=True)
            return
        self._report_data = data
        self._open_report_card()

    @staticmethod
    def _merge_history_skills(table, names, limit=HISTORY_DETAIL_SKILLS):
        """A saved per-skill table, merged by display name.

        The same rule as the live breakdown's _merge_named — all weapons'
        base "Attack" is one row — but reading the names the DATASET was
        saved with rather than the running session's. A dataset opened from
        another session must not be renamed by whatever this session happens
        to have learned."""
        merged: dict[str, list] = defaultdict(lambda: [0, 0.0, 0])
        for sid, vals in (table or {}).items():
            label = (names or {}).get(sid) or _pretty_id(sid)
            m = merged[label]
            m[0] += vals[0]; m[1] += vals[1]; m[2] += vals[2]
        out = sorted(((label, v[1], v[0], v[2]) for label, v in merged.items()),
                     key=lambda t: -t[1])
        return out[:limit]


    def _history_text(self, entry):
        """The opened dataset as chat-pasteable lines."""
        data = entry.get("data") or {}
        names = data.get("skill_names") or {}
        out = [f"Farever+ — {entry.get('name') or 'Combat'}"]
        where = (entry.get("zone") or {}).get("label")
        if where:
            out.append(f"({where}, "
                       + time.strftime("%Y-%m-%d %H:%M",
                                       time.localtime(entry.get("at") or 0))
                       + ")")

        def block(label, duration, players, total, heal):
            out.append(f"== {phase_label(label)} — {self._mmss(duration)}, "
                       f"{_n(total)} dégâts, {_n(heal)} soins ==")
            for p in players[:10]:
                dmg = float(p.get("total") or 0.0)
                pct = dmg / total * 100 if total else 0.0
                rate = _rate_text(dmg, duration, "dps")
                out.append(f"  {p.get('name') or '?'}: "
                           + (f"{rate} " if rate else "")
                           + f"({_n(dmg)}, {_pct1(pct)})")
                for lbl, tot, n, _c in self._merge_history_skills(
                        p.get("skills"), names, 5):
                    out.append(f"     {lbl} : {_n(tot)} ({n} coups)")

        if isinstance(data.get("phases"), list):
            for ph in data["phases"]:
                block(ph.get("label") or "Phase",
                      float(ph.get("duration") or 0.0),
                      ph.get("players") or [],
                      float(ph.get("total") or 0.0),
                      float(ph.get("heal") or 0.0))
        else:
            block("Combat", float(data.get("duration") or 0.0),
                  data.get("players") or [],
                  float(data.get("total") or 0.0),
                  float(data.get("heal") or 0.0))
        return "\n".join(out)

    def _copy_history(self):
        if self._history_detail is None:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(
                self._history_text(self._history_detail))
        except tk.TclError as e:
            print(f"[meter] couldn't copy the dataset: {e}", file=sys.stderr)
            return
        self._history_note("Copié dans le presse-papiers.", transient=True)

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

    def _close_report(self):
        self._report_open = False
        self._refresh_visibility()

    def _copy_report(self):
        """Copy the report as an IMAGE — the leaderboard pastes into chat
        looking like the leaderboard, and one picture carries both phases.
        Rendered from the numbers (render_rift_report_image), never
        screenshotted, so the game behind the card can't bleed in. Falls back
        to the plaintext copy if Pillow or the clipboard declines — a Copy
        button that sometimes copies nothing is worse than one that
        occasionally copies text."""
        if not self._report_data:
            return
        flash = "Copié dans le presse-papiers"
        try:
            copy_image_to_clipboard(
                render_rift_report_image(self._report_data))
        except Exception as e:
            print(f"[meter] image copy failed ({e}) — copying text instead.",
                  file=sys.stderr)
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(
                    self._report_text(self._report_data))
            except tk.TclError as e2:
                print(f"[meter] couldn't copy the report: {e2}",
                      file=sys.stderr)
                return
            flash = "Copié en texte"
        self._report_flash.config(text=flash)
        if self._report_flash_job is not None:
            try:
                self.root.after_cancel(self._report_flash_job)
            except tk.TclError:
                pass

        def clear():
            self._report_flash_job = None
            try:
                self._report_flash.config(text="")
            except tk.TclError:
                pass
        self._report_flash_job = self.root.after(1600, clear)

    def _report_text(self, data):
        """The plaintext version — chat-pasteable lines, no box drawing."""
        out = ["Farever+ — Rapport de faille"]
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
            for i, p in enumerate(players[:5], 1):
                pct = p["total"] / ph["total"] * 100 if ph["total"] else 0.0
                rate = _rate_text(p["total"], dur, "dps")
                out.append(f"  dégâts {i}. {_report_name(p)} "
                           + (f"{rate} " if rate else "")
                           + f"({_n(p['total'])}, {_pct1(pct)})")
            healers = sorted((p for p in players if p["heal"] > 0.5),
                             key=lambda p: -p["heal"])
            for i, p in enumerate(healers[:5], 1):
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

    def _build_parse(self):
        """The parse banner — the same drop-shadowed floating text as the
        keybind hint, but its content changes every second, so the canvas and
        the window are re-measured on each update instead of sized once."""
        self._parse_font = self.fonts["ui_parse_b"]
        self._parse_canvas = tk.Canvas(self.parsewin, bg=TRANSPARENT_KEY,
                                       highlightthickness=0, bd=0)
        self._parse_canvas.pack()
        self._parse_text = None        # last text drawn, to skip redundant work

    def _set_parse_banner(self, text, fill=BG_BODY):
        """Draw `text` centred over the top of the game window. Cheap to call
        every tick: unchanged text redraws nothing (and re-measuring costs a
        game-window lookup, which is why that matters)."""
        if text == self._parse_text:
            return
        self._parse_text = text
        f, c = self._parse_font, self._parse_canvas
        pad, off = 8, 2
        w = f.measure(text) + pad * 2 + off
        h = f.metrics("linespace") + pad * 2 + off
        c.config(width=w, height=h)
        c.delete("all")
        c.create_text(pad + off, pad + off, text=text, font=f, fill=BG_BORDER,
                      anchor="nw")
        c.create_text(pad, pad, text=text, font=f, fill=fill, anchor="nw")
        self.parsewin.update_idletasks()
        l, t, r, _b = self._game_rect()
        # Below the keybind hint, which shares this strip whenever the escape
        # menu is open — and it is, for the first few seconds of a parse.
        self.parsewin.geometry(f"+{l + ((r - l) - w) // 2}+{t + TOP_STRIP_PARSE}")

    def _hide_parse_banner(self):
        self._parse_text = None
        self.parsewin.withdraw()

    def _build_reset_toast(self):
        """The "that worked" panel for a manual reset.

        A reset is silent by nature: the meter empties, which looks exactly
        like a meter that was already empty, and mid-fight it looks like a
        meter that has stopped working. This is the only feedback that the
        keypress did anything.

        A solid panel rather than the drop-shadowed floating text the kill and
        parse toasts use — those are announcements about the fight and belong
        over the game, while this is about the METER and sits on it.
        """
        border = tk.Frame(self.resetwin, bg=BG_BORDER, padx=2, pady=2)
        border.pack(fill="both", expand=True)
        body = tk.Frame(border, bg=BG_HEADER_UNLOCKED, padx=14, pady=8)
        body.pack(fill="both", expand=True)
        self.reset_title = tk.Label(
            body, text=RESET_TOAST_TEXT[0], bg=BG_HEADER_UNLOCKED,
            fg=FG_HEADER, font=self.fonts["ui_hint_b"])
        self.reset_title.pack()
        self.reset_sub = tk.Label(
            body, text=RESET_TOAST_TEXT[1], bg=BG_HEADER_UNLOCKED,
            fg=FG_HEADER_DIM, font=self.fonts["ui_sm_b"])
        self.reset_sub.pack()
        self._reset_toast_job = None

    def _manual_reset(self):
        """A reset the PLAYER asked for — the hotkey, or the panel's button.

        Separate from session.reset() because the automatic resets (a zone
        change, a boss pull, switching player view) must stay silent: they
        happen while you are reading the meter for other reasons, and a banner
        over it every time you walked through a door would be noise.
        """
        self.session.reset()
        self._show_reset_toast()

    def _show_reset_toast(self):
        """Centre it over the meter for RESET_TOAST_SECS."""
        try:
            self.resetwin.update_idletasks()
            w = self.resetwin.winfo_reqwidth()
            h = self.resetwin.winfo_reqheight()
            # Over the METER, not the game — it is confirming something about
            # that window. Falls back to the meter's requested size, because a
            # window that is currently hidden reports a width of 1.
            mw = max(self.root.winfo_width(), self.root.winfo_reqwidth())
            mh = max(self.root.winfo_height(), self.root.winfo_reqheight())
            x = self.root.winfo_x() + (mw - w) // 2
            y = self.root.winfo_y() + (mh - h) // 2
            self.resetwin.geometry(f"+{x}+{y}")
            self.resetwin.deiconify()
            self.resetwin.attributes("-topmost", True)
        except tk.TclError:
            return
        # Resetting again inside the window restarts the clock rather than
        # letting the first press's timer take the second one's toast away.
        if self._reset_toast_job is not None:
            try:
                self.root.after_cancel(self._reset_toast_job)
            except Exception:
                pass
        self._reset_toast_job = self.root.after(
            int(RESET_TOAST_SECS * 1000), self._hide_reset_toast)

    def _hide_reset_toast(self):
        self._reset_toast_job = None
        try:
            self.resetwin.withdraw()
        except tk.TclError:
            pass

    def _build_kill_toast(self):
        """The boss kill time — the same drop-shadowed floating text as the
        parse banner, on its own window because the two can be up at once
        (a parsed boss dying is the ordinary way a parse ends well)."""
        self._kill_font = self.fonts["ui_parse_b"]
        self._kill_canvas = tk.Canvas(self.killwin, bg=TRANSPARENT_KEY,
                                      highlightthickness=0, bd=0)
        self._kill_canvas.pack()
        self._kill_toast_job = None    # pending hide timer

    def _show_kill_toast(self, text, best):
        """Put the time on screen for KILL_TOAST_SECS. Gold when it set a
        record — the one moment the colour means something — body-coloured
        like the parse banner otherwise.

        `text` may carry newlines; the box sizes to the widest line and the
        lines are centred against each other, which is what keeps a short
        tally line from hanging off the left of a long headline."""
        f, c = self._kill_font, self._kill_canvas
        pad, off = 8, 2
        lines = str(text).split("\n")
        line_h = f.metrics("linespace")
        widest = max(f.measure(ln) for ln in lines)
        w = widest + pad * 2 + off
        h = line_h * len(lines) + pad * 2 + off
        c.config(width=w, height=h)
        c.delete("all")
        mid = pad + widest / 2.0
        for i, ln in enumerate(lines):
            ly = pad + i * line_h
            c.create_text(mid + off, ly + off, text=ln, font=f, fill=BG_BORDER,
                          anchor="n")
            c.create_text(mid, ly, text=ln, font=f,
                          fill=REPORT_MEDALS[0] if best else BG_BODY,
                          anchor="n")
        self.killwin.update_idletasks()
        l, t, r, _b = self._game_rect()
        self.killwin.geometry(f"+{l + ((r - l) - w) // 2}+{t + TOP_STRIP_KILL}")
        self.killwin.deiconify()
        self.killwin.attributes("-topmost", True)
        # A second kill inside the window restarts the clock rather than
        # letting the first one's timer take the new text down early.
        if self._kill_toast_job is not None:
            try:
                self.root.after_cancel(self._kill_toast_job)
            except Exception:
                pass
        self._kill_toast_job = self.root.after(int(KILL_TOAST_SECS * 1000),
                                               self._hide_kill_toast)

    def _hide_kill_toast(self):
        self._kill_toast_job = None
        self.killwin.withdraw()







    def _toggle_parse(self):
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
            "title": f"Farever+ — Parse de {PARSE_LENGTH_SECS} s",
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

    # ---- drag / lock ----
    def _is_locked(self):
        """Click-through unless the game is showing a cursor-freeing window
        (its escape menu). The game's UI state is the only input."""
        return not self._menu_unlock

    def _bind_drag(self, win, widgets, unlocked=None, on_move=None):
        """Drag any of `widgets` to move `win` (while unlocked).

        `unlocked` overrides what counts as unlocked, for windows that don't
        follow the overlay-wide rule — the minimap answers to the cursor.

        `on_move` is called after each step for windows whose position is also
        written somewhere else. The buff trays are: their draw pass re-places
        them from a stored anchor every frame, so without telling that store
        where the window has got to, the next frame drags it straight back —
        which is exactly what a tray did, snapping home the moment the cursor
        stopped moving.
        """
        state = {}
        base_free = unlocked or (lambda: not self._is_locked())

        def free():
            return self._screen2_win(win) or base_free()

        def start(e):
            # A whole-window handle (the rift report binds its Toplevel, which
            # catches every descendant through bindtags) must still leave its
            # buttons as buttons: a press that starts a drag would swallow the
            # click it was.
            if isinstance(e.widget, tk.Button):
                return
            if not free():
                return
            state["dx"] = e.x_root - win.winfo_x()
            state["dy"] = e.y_root - win.winfo_y()
            state["on"] = True

        def move(e):
            if not free() or not state.get("on"):
                return
            win.geometry(f"+{e.x_root - state['dx']}+{e.y_root - state['dy']}")
            if on_move is not None:
                on_move()

        def end(e):
            if state.pop("on", None):
                self._save_pos()
                if not self._screen2_win(win):
                    self._refocus_game()

        for w in widgets:
            w.bind("<Button-1>", start)
            w.bind("<B1-Motion>", move)
            w.bind("<ButtonRelease-1>", end)

    def _foreground_pid(self):
        if sys.platform != "win32":
            return 0
        try:
            u = ctypes.windll.user32
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(u.GetForegroundWindow(),
                                       ctypes.byref(pid))
            return pid.value
        except Exception:
            return 0

    def _game_has_focus(self):
        """Is Farever the window you're actually looking at?

        Our own windows count as the game having focus. They're WS_EX_NOACTIVATE
        so they shouldn't take it, but Tk's dropdown menus are its own windows
        and do — without this, opening the Theme dropdown would hide the very
        menu you opened it from.

        The settings panel counts too, and it is the reason this is not just a
        pid comparison. It runs in a process of its own and it DOES take focus
        (it has search boxes), so leaving it out made the overlay hide itself
        the instant the panel appeared — which handed focus back to the game,
        which showed the panel again. That is a loop, and it looked like one:
        both menus flashing alternately for a few seconds until a click landed
        on the panel and left the overlay hidden for good."""
        if sys.platform != "win32" or not self.target_pid:
            return True         # can't tell => don't start hiding things
        fg = self._foreground_pid()
        if fg == 0 or fg in (self.target_pid, os.getpid()):
            return True
        panel = self.menubridge.pid()
        return panel is not None and fg == panel

    def _cursor_is_free(self):
        """Has the game let go of the mouse?

        Farever frees the cursor on Alt, and the overlay should be usable when
        it does. Detected from the OS cursor being visible rather than from the
        key: measured, GetAsyncKeyState never sees that Alt — the game takes it
        — and it behaves as a toggle rather than a hold, so watching the key
        would have been wrong twice over. Reading the cursor also covers every
        other way the game hands the mouse back, including its own menus.

        Gated on the game being frontmost, or alt-tabbing away would leave the
        minimap eating clicks meant for whatever you switched to."""
        if sys.platform != "win32" or not self.target_pid:
            return False
        try:
            u = ctypes.windll.user32
            ci = CURSORINFO()
            ci.cbSize = ctypes.sizeof(CURSORINFO)
            if not u.GetCursorInfo(ctypes.byref(ci)):
                return False
            if not (ci.flags & CURSOR_SHOWING):
                return False
            return self._foreground_pid() == self.target_pid
        except Exception:
            return False

    def _mouse_available(self):
        """There's a pointer to use: the escape menu is open, or the game has
        released the mouse (Alt).

        Deliberately narrower than the overlay-wide unlock — freeing the cursor
        lets you point at things, it doesn't summon the control menu over the
        game. Only the windows you'd want to click answer to it: the meter, for
        picking a player, and the minimap."""
        return self._menu_unlock or self._cursor_free

    def _refocus_game(self):
        """Hand keyboard focus back to Farever.

        The overlay windows carry WS_EX_NOACTIVATE so clicking them shouldn't
        steal focus — but Tk's dropdown menus are its own windows and don't,
        and once one of those has been opened the game stops seeing keystrokes.
        The symptom is Esc not closing the game's menu until you click the game
        first. Rather than leaving that to the player, every interaction with
        the control menu ends by giving the game the foreground back.

        Except while a search box is being typed into — that is the one time
        the overlay is meant to hold the keyboard, and handing it back mid-word
        would eat the rest of what you were typing. Clicking a button on the
        menu therefore does NOT end typing (Tk doesn't move focus on a button
        click), which is what makes "type a name, hit the sort toggle, keep
        typing" work."""
        if self._typing:
            return
        if sys.platform != "win32" or not self.target_pid:
            return
        hwnd = self._game_hwnd
        if not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
            hwnd = self._game_hwnd = _main_hwnd_of_pid(self.target_pid)
        if not hwnd:
            return
        try:
            u = ctypes.windll.user32
            if u.GetForegroundWindow() != hwnd:
                u.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _set_typing(self, on):
        self._typing = bool(on)

    def _stop_typing(self):
        """Give the keyboard back to the game.

        Esc or Return in a search box, and every route by which the control
        menu leaves the screen. That second half is not optional: a _typing
        that outlived the window it belonged to would silently disable
        _refocus_game for the rest of the run. Cheap and idempotent, because
        _refresh_visibility calls it on every tick the menu is down."""
        if not self._typing:
            return
        self._typing = False
        try:
            self.menu.focus_set()   # off the Entry, before the game takes over
        except tk.TclError:
            pass
        self._refocus_game()

    def _on_row_click(self, name):
        # Same rule as the window's click-through, or the row would be
        # clickable-looking and inert while the mouse is free.
        if (self._screen2 or self._mouse_available()) and name:
            self.focus_player = name

    def _set_win_clickthrough(self, win, enabled, activatable=False):
        if sys.platform != "win32":
            return
        hwnd = win.winfo_id()
        parent = ctypes.windll.user32.GetParent(hwnd)
        _set_clickthrough(parent or hwnd, enabled, activatable)

    def _round_win_corners(self, win):
        """Round one window, on the wrapper hwnd click-through also targets.
        DWM keeps the shape as the window resizes, so this only needs redoing
        when the hwnd itself is replaced — see _on_win_map."""
        if sys.platform != "win32":
            return
        hwnd = win.winfo_id()
        parent = ctypes.windll.user32.GetParent(hwnd)
        _set_rounded_corners(parent or hwnd)

    def _on_win_map(self, event):
        # <Map> fires for the initial show, for every deiconify, and after Tk
        # swaps a toplevel's wrapper — i.e. exactly when the rounding needs
        # reasserting. Child widgets bubble their own <Map> here, so only act
        # on the toplevel's.
        win = event.widget
        if not isinstance(win, (tk.Tk, tk.Toplevel)):
            return
        # Not the rift timer: its window is mostly transparent canvas around a
        # much smaller panel, so DWM's rounding has nothing to round — it just
        # leaves a faint border floating out where the ripple ends. The panel
        # draws its own edges.
        if win is self.riftwin:
            return
        self._round_win_corners(win)


    def _apply_clickthrough(self):
        locked = self._is_locked()
        for win in (self.detail, self.riftwin):
            self._set_win_clickthrough(win, locked)
        # The meter answers to the cursor rather than to the escape menu, so it
        # can be pointed at whenever the game has released the mouse, to click
        # a player's row.
        pointable = not self._mouse_available()
        self._set_win_clickthrough(self.root, pointable)
        # The control menu is always interactive (it is only ever shown while
        # the cursor is free); the floating hint and parse banner are text over
        # the game and must never take a click.
        #
        # It is also the one window allowed to ACTIVATE, because it is the one
        # window with a text field on it. Harmless here where it would not be
        # elsewhere: the menu is only ever on screen while the game's escape
        # menu holds the cursor, and _game_has_focus already counts our own
        # process as the game having focus, so taking the foreground does not
        # trip the alt-tab hiding rule. See _stop_typing for how it's handed
        # back.
        self._set_win_clickthrough(self.menu, False, activatable=True)
        self._set_win_clickthrough(self.hintwin, True)
        self._set_win_clickthrough(self.parsewin, True)
        self._set_win_clickthrough(self.killwin, True)
        # The prompt must take clicks whenever it's up, regardless of lock
        # state — it's the one overlay window that has to be answered.
        self._set_win_clickthrough(self.promptwin, False)
        # The rift report too: close and copy are its whole interface, and it
        # only ever appears the moment the fight (and the danger) is over.
        self._set_win_clickthrough(self.reportwin, False)
        # Second-screen windows are ordinary windows: clickable, and allowed
        # to take focus like any other application's.
        if self._screen2:
            for win in (self.root, self.detail, self.reportwin):
                self._set_win_clickthrough(win, False, activatable=True)


    def _sync_game_ui(self):
        """Follow the game's UI: while a cursor-freeing window (escape menu) is
        open the overlay becomes interactive and the control menu appears."""
        want = self.ui_state.any_open(UNLOCK_ON_WINDOWS)
        if want == self._menu_unlock:
            return
        self._menu_unlock = want
        self._apply_clickthrough()
        # Bring the panel's contents up to date BEFORE it is shown. These used
        # to ride the 250 ms loop, which was invisible while the menu also
        # appeared on that loop — now that it opens promptly, a stale label
        # would be on screen for a moment and then change under the eye.
        if want:
            self._refresh_menu()
        self._refresh_visibility()   # owns every window's target, menu included
        if want and not self._prompt_open:
            self._place_hint()       # after the map: it measures the window
        # Logged because it's the one state change with no keypress behind it —
        # if someone reports "the meter won't take my clicks", this line says
        # whether the game-menu signal is arriving at all.
        # The reaction time is on the line too. It is the overlay's own share
        # only — the stamp is taken when the hook's interceptor sees the game
        # open the window — so it says whether a menu that feels slow is us or
        # the game, which is otherwise pure guesswork.
        lag = ""
        if want:
            t = self.ui_state.take_unlock_stamp()
            if t is not None:
                lag = f" (reacted in {(time.monotonic() - t) * 1000:.0f} ms)"
        print(f"[meter] game menu {'open' if want else 'closed'} — overlay "
              f"{'unlocked' if not self._is_locked() else 'locked'}{lag}",
              file=sys.stderr)

    # ---- actions ----
    def _enqueue(self, fn):
        """Wrap `fn` so it runs on the Tk thread at the next refresh. Hotkey and
        mouse-hook callbacks arrive on their own threads, and Tk is not
        thread-safe; menu buttons use it too so every action takes one path."""
        def handler():
            with self._q_lock:
                self._action_q.append(fn)
        return handler







    def _open_url(self, url):
        """Open a link in the player's browser.

        Deliberately does nothing about focus, after an attempt that made
        things worse. The panel is topmost, so a browser can open behind it —
        the obvious fix was to hide the panel and hold it down while the
        browser came forward. It did not come forward: `webbrowser.open` hands
        the URL to an ALREADY RUNNING browser as a new tab, and an existing
        window is not raised by that, so AllowSetForegroundWindow (which only
        licenses a process being started) had nothing to license. The result
        was the panel vanishing, the game keeping focus, and nothing visibly
        happening — worse than the browser merely being behind something.

        Raising a window that belongs to a browser we did not launch is not
        something this can do reliably, so it does not pretend to. The tab
        opens; alt-tab reaches it.
        """
        if sys.platform == "win32":
            # Harmless and correct for the case where the browser IS being
            # started: it lets that new process take the foreground. Does
            # nothing when a browser is already running, which is why it is
            # not a fix on its own.
            try:
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
            except Exception:
                pass
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception as e:
            print(f"[meter] couldn't open {url}: {e}", file=sys.stderr)

    def _open_repo(self):
        self._open_url(REPO_URL)









    def on_game_exit(self, reason=""):
        """The game process went away. Safe from any thread — the frida
        detached signal arrives on frida's own.

        This used to assume the overlay would have vanished by itself, on the
        grounds that a dead game can't hold the foreground. It doesn't: the
        focus rule treats our own windows as the game's, so raising the prompt
        below put the overlay straight back on screen, over the prompt. The
        overlay is now stood down explicitly, before the prompt exists.
        """
        self._enqueue(lambda: self._on_game_exit(reason))()

    def _on_game_exit(self, reason=""):
        self._game_gone = True
        # The hook died with the game, so the close events for whatever was
        # open are never coming — ui_state would keep reporting the escape
        # menu as open, and with it the control menu as unlocked, forever.
        self.ui_state.clear()
        self._menu_unlock = False
        # A parse whose data source just died is not a sample of anything, and
        # its banner maps itself directly rather than through the fade system —
        # so it would climb back over the prompt on the next tick. Ending it is
        # both the honest answer and the one that gets it off the screen.
        if self._parse_state is not None:
            self._stop_parse()
        self._refresh_visibility()      # stand everything down FIRST
        self._show_game_exit_prompt(reason)

    def _show_game_exit_prompt(self, reason=""):
        if self._game_exit_win is not None:
            return
        win = tk.Toplevel(self.root)
        win.title("Farever+")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        body = tk.Frame(win, bg=BG_BODY, padx=16, pady=14)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Farever s'est arrêté.",
                 bg=BG_BODY, fg=FG_VALUE,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(body,
                 text="Le jeu est fermé : le compteur n'a plus rien à "
                      "lire.\nVeux-tu quitter le compteur ?",
                 bg=BG_BODY, fg=FG_TEXT, justify="left",
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 12))
        row = tk.Frame(body, bg=BG_BODY)
        row.pack(fill="x")

        def close(quit_now):
            self._game_exit_win = None
            try:
                win.destroy()
            except tk.TclError:
                pass
            if quit_now:
                self._quit()

        # No second-click arming here, unlike the Quit button: with the game
        # gone there is no encounter left for a misclick to destroy.
        tk.Button(row, text="Quitter le compteur", command=lambda: close(True),
                  bg=FG_WARN, fg=FG_HEADER, activebackground=FG_WARN,
                  activeforeground=FG_HEADER, relief="flat", bd=0,
                  padx=12, pady=6, cursor="hand2",
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Button(row, text="Le laisser tourner", command=lambda: close(False),
                  bg=BG_BODY_SOFT, fg=FG_TEXT, activebackground=BG_BAR_TRACK,
                  activeforeground=FG_VALUE, relief="flat", bd=0,
                  padx=12, pady=6, cursor="hand2",
                  font=("Segoe UI", 9)).pack(side="left", padx=(8, 0))
        win.protocol("WM_DELETE_WINDOW", lambda: close(False))
        win.update_idletasks()
        win.geometry(f"+{(win.winfo_screenwidth() - win.winfo_width()) // 2}"
                     f"+{(win.winfo_screenheight() - win.winfo_height()) // 3}")
        self._game_exit_win = win



    def request_quit(self):
        """Stop the meter, safely callable from any thread — the tray icon runs
        on its own. Routed through the action queue so the quit itself happens
        on the Tk thread like every other action."""
        self._enqueue(self._quit)()

    def _quit(self):
        """quit(), not destroy(): returning from the mainloop hands control back
        to main()'s finally, which is what unloads the hook and detaches. That
        ordering is the entire point of having a Quit button at all."""
        print("[meter] stop requested — shutting down.", file=sys.stderr)
        # Take the UI off the screen NOW, before the slow part.
        #
        # quit() only breaks the mainloop; unloading the hook and detaching
        # happens after that and is not quick — and on this game it is the part
        # that must not be rushed. Without this the whole overlay sat frozen
        # over the game for the duration, which reads as a hang rather than as
        # a shutdown.
        #
        # The settings panel is hidden here too, so everything goes at once.
        # Its process is ended later, in the caller's cleanup; hiding is
        # instant and that is what the user actually sees.
        self.menubridge.hide()
        extra = ("parsewin", "killwin", "badgewin",
                 "promptwin", "reportwin", "riftwin")
        wins = list(self._fade_win.values())
        wins += [w for w in (getattr(self, n, None) for n in extra) if w]
        for win in wins:
            try:
                win.withdraw()
            except Exception:
                pass         # already gone, or never built
        try:
            # Force the unmaps out to the screen before we stop pumping — a
            # withdraw that never gets drawn is a window still on screen.
            self.root.update_idletasks()
        except Exception:
            pass
        self.root.quit()

    def _quit_clicked(self):
        """Two clicks to quit. The button sits in the same menu as the display
        toggles, and a misclick that ends the meter mid-fight — taking the
        encounter with it — is worth one extra click to rule out."""
        if self._quit_armed:
            self._quit()
            return
        # State only. The label follows from it in _menu_spec — the settings
        # panel is not a widget this process owns, so there is nothing here to
        # configure and the next push carries the change.
        self._quit_armed = True
        self.root.after(4000, self._disarm_quit)

    def _disarm_quit(self):
        self._quit_armed = False

    def _install_hotkeys(self):
        start_hotkeys({HK_RESET: self._enqueue(self._manual_reset)},
                      self.target_pid)

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

    def _on_element_pick(self, key, value):
        self._enqueue(lambda: self._set_element_mode(key, value))()

    def _set_element_mode(self, key, value):
        """Show / Hide / Show in ESC for one overlay window. The control menu
        is never in TOGGLEABLE_ELEMENTS — it's how you get the others back."""
        if value not in ELEMENT_MODES:
            return
        self._show[key] = value
        self._save_settings()
        self._refresh_visibility()

    def _sort_btn_text(self):
        return "▼ Soins" if self._sort_heal else "▼ Dégâts"

    def _toggle_sort(self):
        self._sort_heal = not self._sort_heal
        self.sort_btn.config(text=self._sort_btn_text())
        self._save_settings()

    def _toggle_heal(self):
        self._show_heal = not self._show_heal
        # The sort toggle lives and dies with the healing columns: turning
        # them off while heal-sorted snaps the order back to damage, or the
        # rows would sit in an order nothing on screen explains.
        if self._show_heal:
            self.sort_btn.pack(side="right")
        else:
            if self._sort_heal:
                self._sort_heal = False
                self.sort_btn.config(text=self._sort_btn_text())
            self.sort_btn.pack_forget()
        self._save_settings()


    def _set_group_scale(self, group, factor):
        """Resize one window group's fonts, which resizes the windows that pack
        to them. The two canvas-drawn banners measure their text at draw time,
        so they're re-drawn rather than left at the old size."""
        if abs(factor - self._scales.get(group, 1.0)) < 0.001:
            return
        self._scales[group] = factor
        for key, (_family, size, *_style) in FONT_SPECS.items():
            self._font_sets[group][key].configure(
                size=max(6, round(size * factor)))
        if group == "meter":
            # Kept in step because a pile of pixel constants (minimum widths,
            # the warning wrap) are still expressed against it.
            self._ui_scale = factor
        self._parse_text = None          # force the parse banner to re-measure
        self._save_settings()
        self._draw_hint()
        # Pixel floors and wrap widths don't come along for free — and each
        # belongs to ITS OWN group's scale, not to whichever slider happened to
        # move. Applying `factor` to all four made every window jump whenever
        # any one of them was resized. Recomputed from scratch rather than
        # patched for the group that changed, so they can't drift apart.
        for win, key, group_of in ((self.root, "meter", "meter"),
                                   (self.detail, "detail", "detail"),
                                   (self.menu, "menu", "menu"),
                                   # The rift prompt is drawn with the meter's
                                   # fonts, so it scales with the meter.
                                   (self.promptwin, "prompt", "meter")):
            win.minsize(int(MIN_W[key] * self._scales[group_of]), 0)
        self.root.update_idletasks()
        print(f"[meter] {group} scale {factor:.2f}x", file=sys.stderr)



    def _toggle_hide_ooc(self):
        self._hide_ooc = not self._hide_ooc
        self._save_settings()
        self._refresh_visibility()

    def auto_reset_boss(self) -> bool:
        """Read by the hook thread, so it stays a plain attribute read."""
        return bool(self._auto_reset_boss)



    def on_boss_giveup(self):
        """The fight ended without a kill — the boss reset, or the group
        wiped — and the meter was cleared for the next attempt.

        No cue. The victory sound is a reward and the pull sound is a
        starting gun; there is nothing to announce about a fight that just
        stopped, and the player already knows. What they need is to see that
        the meter did something, which the toast says — the same line the kill
        time uses, in its plain colour rather than the record gold."""
        self._enqueue(
            lambda: self._show_kill_toast("COMBAT ABANDONNÉ — COMPTEUR VIDÉ",
                                          best=False))()

    def on_boss_timed_kill(self, kinds, secs):
        """The LAST boss bar went down killed — the fight is formally over and
        its clock has a reading. Called from the hook thread alongside the
        victory cue; the record and the toast belong to the Tk one.

        Not opt-in, deliberately: a record you had to switch on beforehand is
        a record you don't have when you finally want it."""
        self._enqueue(lambda: self._record_boss_kill(tuple(kinds), secs))()

    def _record_boss_kill(self, kinds, secs):
        """Compare the kill against the stored best and say so on screen.

        The key is the PULL's boss kinds, sorted and joined — stable for a
        council pulled together (whichever member dies last), and for the
        Nightqueen it is her alone, because her copies never fire a second
        pull edge. The killed bar's kind would be neither."""
        if not kinds:
            # A bar with no kind can't key a record, but the time is still
            # worth saying — it just can't be compared to anything.
            self._show_kill_toast(f"BOSS VAINCU  {self._mmss(secs)}", best=False)
            return
        # The record keys on the internal kind — stable across localization
        # and any rename the cdb ships — but the toast speaks the game's
        # language: the kind is routinely not the name on the bar (the first
        # live kill said "CLEODORA" for a boss the game calls Honeyzabeth).
        key = "+".join(kinds)
        name = " + ".join(_boss_label(k) for k in kinds).upper()
        prev = self._best_times.get(key)
        if prev is None:
            self._best_times[key] = secs
            self._save_best_times()
            text = f"{name} VAINCU  {self._mmss(secs)} — PREMIER KILL ENREGISTRÉ"
            best = True
        elif secs < prev:
            self._best_times[key] = secs
            self._save_best_times()
            text = (f"{name} VAINCU  {self._mmss(secs)} — NOUVEAU RECORD "
                    f"(avant : {self._mmss(prev)})")
            best = True
        else:
            text = f"{name} VAINCU  {self._mmss(secs)} — RECORD {self._mmss(prev)}"
            best = False
        print(f"[meter] boss kill timed: {key} {secs:.1f}s"
              + (f" (best {self._best_times[key]:.1f}s)"), file=sys.stderr)
        self._show_kill_toast(text, best)


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


    def _toggle_auto_reset_boss(self):
        self._auto_reset_boss = not self._auto_reset_boss
        self._save_settings()


    def _refresh_visibility(self):
        """Fade each element in/out from its own show/hide setting plus the two
        global rules: "hide out of combat", and hiding behind the game's own
        screens.

        Out-of-combat hiding keeps things up for a few seconds after the
        fighting stops (HIDE_OOC_LINGER_SECS) — the game's isInCombat flag drops
        between pulls, and without the grace period the overlay would flicker
        away and back through a trash pack.

        The escape menu overrides all of it — including a window you ticked off
        yourself. Being able to see what a checkbox does while you're clicking
        it matters more than honouring the setting for those few seconds, and it
        means the control menu is never the only thing on screen.

        What the escape menu does NOT override is another game window on top of
        it. Options, feedback and the two confirmations are all reached through
        it, and while one of those is up the game has taken the screen back."""
        # Once the game is gone, every window here is a readout of something
        # that no longer exists, and the exit prompt is the only thing left
        # with anything to say. Hide the lot — ahead of every other rule,
        # because two of them actively argue for showing things:
        #
        #   * _game_has_focus() counts OUR OWN process as the game having
        #     focus (Tk's dropdowns are separate windows and would otherwise
        #     hide the menu you opened them from). The exit prompt is one of
        #     our windows, so raising it made the overlay think the game was
        #     back in the foreground and un-hid everything.
        #   * ui_state never learns the game's windows closed — the hook died
        #     with the process, so no close events arrive and the escape menu
        #     stays "open" forever, holding the control menu unlocked.
        #
        # Both were reported as the menu rendering over the exit prompt.
        if self._game_gone:
            self._stop_typing()
            changed = False
            for key in self._fade_win:
                changed |= self._want_visible(key, False)
            if changed:
                self._start_fade()
            # Not a faded window, so it has to be told separately.
            try:
                self.parsewin.withdraw()
            except tk.TclError:
                pass
            return
        ooc_hidden = (self._hide_ooc and not self._menu_unlock and
                      (time.time() - self._combat_seen_at) >= HIDE_OOC_LINGER_SECS)
        # Any game window that isn't the escape menu (inventory, map, ...) owns
        # the screen while it's up — see MENU_IGNORE_WINDOWS. Unlike the OOC
        # rule this is unconditional: it isn't a setting the player can untick.
        #
        # It applies even while the escape menu is open, which is the whole
        # point: options, the feedback form and the "back to menu"/"exit game"
        # confirmations are all opened FROM the escape menu and sit on top of
        # it. Treating the escape menu as a blanket exemption left the overlay
        # sitting over every one of them.
        menu_hidden = self.ui_state.any_open_except(MENU_IGNORE_WINDOWS)
        # The rift prompt is modal: while it's up nothing else is on screen, not
        # even the control menu. That's deliberate — it leaves Esc free to hand
        # the cursor back so the question can actually be clicked.
        # Alt-tab away and the whole overlay goes with you. It's drawn on top
        # of everything, so leaving it up means a damage meter floating over
        # your browser — and worse, one you can't click past while the cursor
        # is free. The tray icon stays, which is how you'd stop the meter from
        # out here anyway.
        blanket = menu_hidden or self._prompt_open or not self._focused
        changed = False
        for key in self._element_win:
            show_key = _element_show_key(key)
            # A second-screen window is not over the game, so none of the
            # rules that keep the game's screen clear apply to it.
            s2 = self._screen2_key(key)
            hidden = not s2 and (
                blanket or (ooc_hidden and show_key not in OOC_EXEMPT))
            mode = self._show.get(show_key, ELEMENT_SHOW)
            if mode == ELEMENT_HIDE:
                base = False           # hidden, and stays hidden in the menu
            elif mode == ELEMENT_ESC:
                base = s2 or self._menu_unlock
            else:
                base = True
            want = base and not hidden
            # No countdown while you're inside a rift: you're in the thing it
            # was counting down to.
            if key == "rift" and self.ui_state.in_rift():
                want = False
            # ...nor while there's nothing to count. "No rift upcoming" is true
            # for six minutes of every hour and is not worth a panel; the
            # escape menu still brings it back, like every other hidden thing,
            # so the Show/hide tick can be seen to do something.
            if key == "rift" and self._rift_idle and not self._menu_unlock:
                want = False
            # Same rule for the breakdown: with nobody to inspect it is a box
            # whose only content is the fact that it has none. It comes back
            # the moment anything is recorded, and the escape menu still shows
            # it regardless, so the Show/hide tick can be seen to do something.
            if (key == "detail" and self._detail_idle and not self._menu_unlock
                    and not s2):
                want = False
            changed |= self._want_visible(key, want)
        # ...and the control menu goes with everything else when you alt-tab.
        # The game's escape menu stays open behind you, so _menu_unlock stays
        # true, and without this the one window that ignores every other hiding
        # rule sat over your browser — the exact thing the focus check exists to
        # prevent, on the largest window the overlay has.
        # ...and it goes when the game opens something over the escape menu,
        # for the same reason as everything else: "hide all UI" has to include
        # the meter's own settings panel, or the one window left on screen is
        # the one you were trying to get out of the way.
        menu_visible = (self._menu_unlock and not self._prompt_open
                        and self._focused and not menu_hidden)
        # Opened from the tray icon: up until it is closed, game or no game.
        panel_visible = menu_visible or self._panel_forced
        # Whatever route the menu leaves by — Esc, alt-tab, the game opening
        # something over it — the keyboard leaves with it if a search box was
        # holding it.
        if not panel_visible:
            self._stop_typing()
        # The WebView2 settings panel follows exactly the same rule, and is
        # started the first time it is wanted rather than at launch — a player
        # who never opens the menu never pays for a second process.
        self._sync_panel(panel_visible)
        # The Tk control menu is retired: the WebView2 panel above is the
        # settings UI now. Its widgets are still built — 200-odd places across
        # this file still reference them, and unpicking that is a separate job
        # from replacing the window — but the window itself is never mapped
        # again, so there is no second menu floating over the game. Pinned
        # False rather than deleted so the fade system, the saved positions and
        # the scale groups all keep working unchanged.
        changed |= self._want_visible("menu", False)
        changed |= self._want_visible("hint", menu_visible)
        changed |= self._want_visible("prompt", self._prompt_open)

        # The report follows the blanket rules (alt-tab, the game's own
        # screens, the modal prompt) but not the out-of-combat one — the boss
        # just died, so out-of-combat is precisely when it exists. It stays up
        # until its ✕ is clicked; a card that vanished on its own before you
        # could read the numbers would be worse than no card.
        changed |= self._want_visible(
            "report", self._report_open and (self._screen2 or not blanket))
        if changed:
            self._start_fade()

    def _pick_theme(self):
        """The mode names two things: which base to wear, and whether a rift
        overrides it.

        Pinned Rift means rift, always. It used to fall back to Farever while
        the escape menu was open, on the reasoning that the control menu is
        Farever-styled and the meter sits next to it — but somebody who pins
        Rift has asked for rift colours, and watching the overlay change theme
        every time they open the menu is a worse trade than a colour clash with
        a panel that isn't themed at all.

        The Dynamic modes still yield to the escape menu, and that's different:
        there the rift colours are something the game put you in rather than
        something you chose, so seeing the overlay's own palette while you're
        reading its settings is the more useful of the two."""
        base = THEME_BASES.get(self._theme_mode, THEME_DEFAULT)
        if self._theme_mode == "Rift":
            return THEME_RIFT
        if self._menu_unlock:
            return base
        if (self._theme_mode.endswith("Dynamic")
                and self.ui_state.in_rift()):
            return THEME_RIFT
        return base

    def _set_theme_mode(self, mode):
        self._theme_mode = mode
        self._save_settings()

    def _apply_theme_font(self, family):
        """Put the themed windows in `family`, if they aren't already.

        Guarded on the current face because this is reached from the draw pass:
        _pick_theme runs every tick, and reconfiguring a named font relayouts
        every window packed to it. The guard is what keeps that to the handful
        of ticks where the theme actually changed.

        A missing family is not an error to Tk — it silently substitutes, and
        the overlay would come up in whatever it picked with no way to tell
        that from a deliberate choice. So it's checked, and an absent face
        falls back to the one every other theme uses.
        """
        if family != UI_FONT_DEFAULT and family not in self._font_families():
            print(f"[meter] font {family!r} not installed; using "
                  f"{UI_FONT_DEFAULT}", file=sys.stderr)
            family = UI_FONT_DEFAULT
        if family == self._theme_font:
            return
        self._theme_font = family
        for group in THEME_FONT_GROUPS:
            for key in THEME_FONT_KEYS:
                self._font_sets[group][key].configure(family=family)
        # The same two the scale slider has to poke, and for the same reason:
        # both are drawn on a canvas and measure their own text, so neither
        # notices that the text just changed width.
        self._parse_text = None
        self._draw_hint()

    def _font_families(self):
        """Installed font families, folded and cached. The Tk call walks the
        system font list, which is not something to do on a draw pass."""
        if self._families is None:
            self._families = set(tkfont.families(root=self.root))
        return self._families

    def _apply_theme(self, t):
        """Repaint the meter and breakdown into `t`. Same widget tree either
        way — nothing is rebuilt, so this is safe to call mid-combat."""
        self._theme = t
        self._apply_theme_font(t.get("font") or UI_FONT_DEFAULT)
        for w in (self.m_border, self.d_border):
            w.config(bg=t["border"])
        for w in (self.m_body, self.rows_box, self.rows_keeper, self.d_body,
                  self.d_cols):
            w.config(bg=t["body"])
        self.overview_title.config(bg=t["body"], fg=t["accent"])
        self.cols_lbl.config(bg=t["body"], fg=t["fg_dim"])
        self.elem_lbl.config(bg=t["body"], fg=t["fg_dim"])
        self.col_sep.config(bg=t["soft"])
        # The summary sidebar. Its panel is `soft` — one subtle step off body
        # in every theme — and its text has to be lifted off THAT rather than
        # off the body, or the captions go muddy on the darker skins.
        for w in (self.d_split, self.d_right):
            w.config(bg=t["body"])
        self.side_sep.config(bg=t["soft"])
        self.d_side.config(bg=t["soft"])
        self.side_idle.config(bg=t["soft"], fg=t["fg_dim"])
        for rf, cap, val in self.side_rows:
            rf.config(bg=t["soft"])
            cap.config(bg=t["soft"], fg=t["fg_dim"])
            val.config(bg=t["soft"], fg=t["fg_value"])
        for row in self.player_rows:
            row.theme = t
            row.set_theme(t)
        for col in (self.dmg_col, self.heal_col):
            col.set_theme(t)
        # Force the header tint to be re-pushed: its guard compares against the
        # last colour applied, which belongs to the theme we just left.
        self._header_bg = None

    def _want_visible(self, key, visible):
        """Point one faded window at a target state, returning whether that
        changed anything. Mapping happens here, at whatever opacity the fade
        last left it (0 for a fully hidden window, mid-fade for one caught on
        the way out); unmapping happens in _step_fade once it reaches zero."""
        if visible == self._shown[key]:
            return False
        self._shown[key] = visible
        win = self._fade_win[key]
        # Zero fade means this window is driven by a keypress and has to be on
        # screen in the same frame. Handled here rather than as a one-step fade
        # because the driver only wakes every FADE_STEP_MS — going through it
        # would put a tick of nothing between the key and the panel, which is
        # the delay this exists to avoid.
        if self._fade_secs[key] <= 0:
            self._alpha[key] = self._alpha_for(key) if visible else 0.0
            win.attributes("-alpha", self._alpha[key])
            if visible:
                win.deiconify()
                if not self._screen2_key(key):
                    win.attributes("-topmost", True)
            else:
                win.withdraw()
            return True
        if visible:
            win.attributes("-alpha", self._alpha[key])
            win.deiconify()
            if not self._screen2_key(key):
                win.attributes("-topmost", True)   # re-assert over the game
        return True

    def _start_fade(self):
        if self._fade_job is None:
            self._fade_job = self.root.after(FADE_STEP_MS, self._step_fade)

    # ---- rebinding the reset key ----
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
            except tk.TclError:
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



    def _alpha_for(self, key):
        """What this window's opacity should settle at when it's on screen.

        The slider is a percentage taken OFF the overlay's normal opacity, so 0
        is the look the meter has always had rather than a subtly different
        one. The exempt windows ignore it entirely."""
        if self._screen2_key(key):
            return 1.0          # a normal window: fully opaque
        if key in TRANSPARENCY_EXEMPT or not self._transparency:
            return OVERLAY_ALPHA
        return OVERLAY_ALPHA * (1.0 - min(TRANSPARENCY_MAX,
                                          self._transparency) / 100.0)

    def _set_transparency(self, percent):
        """Apply the slider. Windows already on screen are set outright rather
        than faded there: a fade is for something arriving or leaving, and this
        is neither — you're dragging a slider and watching the result."""
        percent = max(0, min(TRANSPARENCY_MAX, int(percent)))
        if percent == self._transparency:
            return
        self._transparency = percent
        for key, win in self._fade_win.items():
            if not self._shown.get(key):
                continue
            self._alpha[key] = self._alpha_for(key)
            try:
                win.attributes("-alpha", self._alpha[key])
            except tk.TclError:
                pass
        self._save_settings()
        print(f"[meter] transparency {percent}%", file=sys.stderr)


    def _step_fade(self):
        """Walk every faded window one step towards its target opacity, and
        unmap it once it reaches zero. Reversing mid-fade needs no special
        handling: the target flips and the next step walks back from here."""
        self._fade_job = None
        fading = False
        for key, win in self._fade_win.items():
            target = self._alpha_for(key) if self._shown[key] else 0.0
            a = self._alpha[key]
            if a == target:
                continue
            secs = self._fade_secs[key]
            if secs <= 0:
                # A no-fade window is normally settled by _want_visible and
                # never reaches here. If anything else moves its target, snap —
                # never divide by zero working out a step size.
                a = target
            else:
                step = OVERLAY_ALPHA * FADE_STEP_MS / (secs * 1000)
                a = (min(target, a + step) if target > a
                     else max(target, a - step))
            self._alpha[key] = a
            win.attributes("-alpha", a)
            if a <= 0.0:
                win.withdraw()
            else:
                fading = True
        if fading:
            self._fade_job = self.root.after(FADE_STEP_MS, self._step_fade)

    def _reset_pos(self):
        """Back to the default places — for the current mode only, so resetting
        the second-screen layout doesn't also throw away the overlay one."""
        if self._screen2:
            for key in ("s2_meter", "s2_detail", "s2_report"):
                self._pos_mem.pop(key, None)
            self._place_mode_windows()
        else:
            for key in ("meter", "detail", "menu", "rift"):
                self._pos_mem.pop(key, None)
            self._default_meter_pos()
            self._default_detail_pos()
            self._default_menu_pos()
            self._default_rift_pos()
        self._save_pos()

    # ---- second-screen mode ----
    def _screen2_key(self, key):
        return self._screen2 and key in SCREEN2_KEYS

    def _screen2_win(self, win):
        return self._screen2 and win in (self.root, self.detail, self.reportwin)

    def _toggle_screen2(self):
        self._save_pos()                # remember this mode's layout first
        self._screen2 = not self._screen2
        self._save_settings()
        self._apply_window_mode()
        self._place_mode_windows()
        self._save_pos()
        self._refresh_visibility()
        self._refresh_menu()
        print(f"[meter] second-screen mode {'on' if self._screen2 else 'off'}",
              file=sys.stderr)

    def _apply_window_mode(self):
        """Turn the meter, breakdown and report into normal windows (second
        screen) or back into overlay windows.

        The window is withdrawn around the change: Tk only applies
        overrideredirect when the window is next mapped, and it rebuilds the
        Windows wrapper while doing so — which is why click-through is
        re-applied at the end, on the new wrapper."""
        normal = self._screen2
        for key, win in (("meter", self.root), ("detail", self.detail),
                         ("report", self.reportwin)):
            win.withdraw()
            win.overrideredirect(not normal)
            try:
                win.wm_attributes("-transparentcolor",
                                  "" if normal else TRANSPARENT_KEY)
            except tk.TclError:
                pass
            win.attributes("-topmost", not normal)
            if normal:
                win.title(SCREEN2_TITLES[key])
                # Sized by their content (and the size sliders), as before.
                win.resizable(False, False)
            self._alpha[key] = (self._alpha_for(key) if self._shown.get(key)
                                else 0.0)
            win.attributes("-alpha", self._alpha[key])
            if self._shown.get(key):
                win.deiconify()
        self.root.update_idletasks()
        self._apply_clickthrough()

    def _place_mode_windows(self):
        """Put the meter and breakdown where they were last left in the current
        mode, or in the mode's default place."""
        mem = self._pos_mem
        if not self._screen2:
            m, d = mem.get("meter"), mem.get("detail")
            if m and self._pos_visible(*m):
                self.root.geometry(f"+{m[0]}+{m[1]}")
            else:
                self._default_meter_pos()
            self.root.update_idletasks()
            if d and self._pos_visible(*d):
                self.detail.geometry(f"+{d[0]}+{d[1]}")
            else:
                self._default_detail_pos()
            return
        m, d = mem.get("s2_meter"), mem.get("s2_detail")
        if m and self._pos_visible(*m):
            self.root.geometry(f"+{m[0]}+{m[1]}")
        else:
            # First time: on a monitor the game isn't on, if there is one.
            l, t, r, b = self._screen2_default_area()
            self.root.geometry(f"+{l + SCREEN2_MARGIN}+{t + SCREEN2_MARGIN}")
        self.root.update_idletasks()
        if d and self._pos_visible(*d):
            self.detail.geometry(f"+{d[0]}+{d[1]}")
        else:
            x, y = self._win_xy(self.root)
            h = max(self.root.winfo_reqheight(), self.root.winfo_height(), 240)
            self.detail.geometry(f"+{x}+{y + h + 50}")

    def _screen2_default_area(self):
        """The work area of a monitor the game is not on, or the game's own
        monitor when there is only one."""
        l, t, r, b = self._game_rect()
        cx, cy = (l + r) // 2, (t + b) // 2
        areas = _monitor_work_areas()
        for a in areas:
            if not (a[0] <= cx < a[2] and a[1] <= cy < a[3]):
                return a
        return _monitor_containing(cx, cy) or (l, t, r, b)

    def _on_s2_configure(self, event, win):
        """A second-screen window moved (by its title bar) — save once the
        move has settled rather than on every pixel of it."""
        if event.widget is not win or not self._screen2:
            return
        if self._s2_save_job is not None:
            try:
                self.root.after_cancel(self._s2_save_job)
            except tk.TclError:
                pass
        self._s2_save_job = self.root.after(600, self._s2_save)

    def _s2_save(self):
        self._s2_save_job = None
        self._save_pos()

    def _on_s2_close(self, win):
        """The title bar's ✕. The report really closes; the meter and the
        breakdown only go to the taskbar — closing the meter's window must not
        stop the program (the tray icon and the settings panel do that)."""
        if win is self.reportwin:
            self._close_report()
            return
        try:
            win.iconify()
        except tk.TclError:
            pass

    def open_settings_from_tray(self):
        """Show the settings panel without the game's escape menu — the way in
        when the meter lives on another screen."""
        self._panel_forced = True
        self.menubridge.invalidate()
        self._refresh_visibility()

    def _toggle_mode(self):
        self.mode = "all" if self.mode == "party" else "party"
        self._save_settings()
        self.focus_player = None
        self.session.reset()

    # ---- render ----
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

    def _apply_heal_columns(self):
        """Show/hide every optional piece of meter chrome in one go: the
        healing columns (the meter's HEAL header, and the breakdown's HEALING
        list with its divider). Guarded on the last applied state — re-packing
        widgets on every 250 ms tick would flicker."""
        state = (self._show_heal,)
        if state == self._cols_shown:
            return
        heal_changed = self._cols_shown[0] != self._show_heal
        self._cols_shown = state
        self.cols_lbl.config(text=self._meter_cols_text())
        if not heal_changed:
            return
        if self._show_heal:
            self.col_sep.pack(side="left", fill="y", padx=6)
            self.heal_col.f.pack(side="left", anchor="n")
        else:
            self.col_sep.pack_forget()
            self.heal_col.f.pack_forget()
















    def _refresh_menu(self):
        """Retired, and deliberately not deleted.

        This used to relabel every button on the Tk control menu from the
        state behind it. The settings panel does that job now, and does it by
        being handed a fresh spec (_menu_spec) rather than by reconfiguring
        widgets — so the work here is not merely unnecessary, it is the
        expensive kind: measured on this project, a no-op Tk config() costs
        full price, and this ran on every tick.

        The ten call sites are left alone on purpose. Each one marks a place
        that changes something the panel displays, so they become the hint that
        the panel should be re-pushed — which invalidate() does for free.
        """
        self.menubridge.invalidate()


    def _pump_input(self):
        """Everything that answers to the player rather than to the fight.

        Split out of _refresh and run on its own fast timer: the aggregation
        loop ticks every 250 ms because that is often enough to redraw damage
        numbers, but it was also what decided when the control menu appeared.
        Pressing Escape therefore cost up to a full tick before the fade even
        started, which is a quarter second of nothing happening — long enough
        to feel broken rather than smooth.

        Deliberately only the cheap checks: two user32 calls, a queue drain and
        a comparison, none of which touch the session aggregation the main loop
        owns. Same division the minimap's own loop already uses.
        """
        self._drain()
        # Cheap, and only acted on when it changes, so the click-through style
        # isn't rewritten on every one of these ticks.
        free = self._cursor_is_free()
        if free != self._cursor_free:
            self._cursor_free = free
            self._apply_clickthrough()
        focused = self._game_has_focus()
        if focused != self._focused:
            self._focused = focused
            self._refresh_visibility()
        self._sync_game_ui()

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

    def _refresh(self):

        # Before the epoch check below: starting a parse resets the session
        # itself, and syncs _last_epoch so that isn't mistaken for the player
        # resetting back out of parse mode.
        self._tick_rift()
        self._tick_rift_timer()
        self._tick_parse()
        # Ahead of _refresh_menu, so a reset that drops parse mode is reflected
        # in the button on the same tick rather than the next one.
        if self.session.epoch != self._last_epoch:
            self._last_epoch = self.session.epoch
            self.focus_player = None    # snap the breakdown back to my hero
            # A reset from anywhere else — the hotkey, the menu button, a zone
            # change — drops parse mode too: the sample it was building is gone.
            if self._parse_state is not None:
                self._parse_state = None
                self._hide_parse_banner()
        # _sync_game_ui now rides the fast input pump instead — see
        # _pump_input. It stays idempotent, so nothing here depends on which
        # loop got to it first.
        #
        # Only while the panel is actually on screen. Measured 2026-08-08:
        # _refresh_menu costs ~8ms, and it was running four times a second for
        # the whole session — relabelling several dozen widgets nobody could
        # see, in the same interpreter as the 30ms tray/minimap loop and the
        # 33ms input pump. Nothing is lost by skipping it: _sync_game_ui calls
        # it explicitly before the menu is shown, precisely so the panel is
        # never stale on the way up, and every control that changes state
        # calls it too.
        if self._shown.get("menu"):
            self._refresh_menu()
        # The Social pages are deliberately NOT refreshed here: they load on
        # the menu opening, the tab being raised, and the Refresh button, and
        # hold still in between — see _reload_social for what polling cost.
        _, _, rows = self.session.snapshot()
        rows = self._apply_mode(rows)
        self._apply_heal_columns()
        if self._sort_heal:
            rows.sort(key=lambda p: -p.heal_total)
        # The capture clock only advances while at least one *displayed*
        # (mode-filtered) player is in combat, per the game's isInCombat state.
        active = any(self.session.combat_of(p.name) for p in rows)
        self.session.set_active(active, time.time())
        duration, in_combat = self.session.current()
        if in_combat:
            self._combat_seen_at = time.time()
        # Everything above this line reads the LIVE encounter — the capture
        # clock and the combat state must never be driven by held rows, or a
        # meter showing yesterday's fight would keep its clock running.
        rows, duration, holding = self._hold_last(rows, duration)
        self._refresh_visibility()

        # The header BAR carries the state; the header TEXT never changes, so a
        # screenshot always says what the overlay is. Green = unlocked (the
        # game's escape menu is open), orange = in combat, teal = idle. Unlocked
        # wins over combat: it's the transient one, and combat is already
        # obvious from the running timer.
        theme = self._pick_theme()
        if theme is not self._theme:
            self._apply_theme(theme)
        unlocked = not self._is_locked()
        live_bg = (theme["header_unlocked"] if unlocked
                   else theme["header_combat"] if in_combat else theme["header"])
        # A window you've ticked off still shows while the escape menu is open,
        # which makes "off" hard to see. Greying its header is the tell — it's
        # the only part of the window that carries state anyway.
        want = tuple(theme["header_off"] if not self._show[k] else live_bg
                     for k in ("meter", "detail"))
        if want != self._header_bg:
            meter_bg, detail_bg = want
            for w in (self.header, self.title_lbl, self.timer_lbl):
                w.config(bg=meter_bg)
            self.sort_btn.config(bg=meter_bg, activebackground=meter_bg)
            for w in (self.d_header, self.d_title, self.d_tip):
                w.config(bg=detail_bg)
            for w in (self.title_lbl, self.timer_lbl, self.d_title):
                w.config(fg=theme["fg_header"])
            self.sort_btn.config(fg=theme["fg_header"],
                                 activeforeground=theme["fg_header"])
            self.d_tip.config(fg=theme["fg_header_dim"])
            self._header_bg = want

        mins, secs = divmod(int(duration), 60)
        party_total = sum(p.total for p in rows)
        self.timer_lbl.config(
            text=(f"{mins}:{secs:02d}   {int(party_total)}" if duration > 0 else ""))

        self.overview_title.config(
            text=("GROUPE" if self.mode == "party" else "TOUS LES JOUEURS")
            + f"   ({len(rows)})" + ("   · DERNIER" if holding else ""))

        focus = self._resolve_focus(rows)
        # Bars scale against the biggest number of their own kind on screen.
        # Neither leader is positional: the sort toggle means rows[0] can be
        # the top healer, and the top of either column can be anyone.
        top_dmg = max((p.total for p in rows), default=0.0) or 1.0
        top_heal = max((p.heal_total for p in rows), default=0.0) or 1.0
        for i, row in enumerate(self.player_rows):
            if i < len(rows):
                p = rows[i]
                dps = p.total / duration if duration > 0 else 0.0
                pct = (p.total / party_total * 100) if party_total else 0.0
                row.show(i + 1, p, dps, pct, focused=(focus == p.name),
                         dmg_frac=p.total / top_dmg,
                         heal_frac=p.heal_total / top_heal,
                         heal_self_frac=p.heal_self / top_heal,
                         show_heal=self._show_heal,
                         cls_tag=_class_tag(self.world.class_of(p.name)))
            else:
                row.hide()

        # ---- breakdown window ----
        fp = next((p for p in rows if p.name == focus), None)
        if fp is None:
            self.d_title.config(text="Détail")
            self._detail_idle = True
            self._set_side_stats([])
            self.dmg_col.show([], 0)
            self.heal_col.show([], 0)
            self.elem_lbl.config(text="")
        else:
            self._detail_idle = False
            self.d_title.config(text=f"Détail — {fp.name}")
            fdps = fp.total / duration if duration > 0 else 0.0
            crit_pct = (fp.crits / fp.hits * 100) if fp.hits else 0.0
            # Thousands separators, which the old single line could not
            # afford the width for: the sidebar is the one place a five-figure
            # damage number has room to be readable at a glance.
            stats = [("DÉGÂTS", _n(fp.total)), ("DPS", _n(fdps)),
                     ("COUPS", _n(fp.hits)), ("CRITIQUES", f"{crit_pct:.0f}%")]
            if self._show_heal:
                stats.append(("SOINS", _n(fp.heal_total)))
                if fp.heal_total > 0.5:
                    stats.append(("SOIN EN EXCÈS", f"{fp.overheal_pct:.0f}%"))
            if fp.kills:
                stats.append(("KILLS", _n(fp.kills)))
            self._set_side_stats(stats)
            self.dmg_col.show(self._merge_named(fp.skills), fp.total)
            if self._show_heal:
                self.heal_col.show(self._merge_named(fp.heals), fp.heal_total)
            el = sorted(fp.elements.items(), key=lambda kv: -kv[1][1])
            self.elem_lbl.config(
                text="  ".join(f"{element_label(k)}:{int(v[1])}"
                               for k, v in el[:6]))

    def _resolve_focus(self, rows):
        if self.focus_player and any(p.name == self.focus_player for p in rows):
            return self.focus_player
        me = next((p.name for p in rows if p.is_me), None)
        return me or (rows[0].name if rows else None)

    def run(self):
        # The input pump. Its own timer, because what makes
        # the overlay feel responsive and what makes the numbers correct run at
        # completely different speeds — and the slower of the two was setting
        # the pace for both.
        def input_loop():
            try:
                self._pump_input()
            except tk.TclError:
                return              # window went away; stop rescheduling
            self.root.after(UI_TICK_MS, input_loop)
        input_loop()

        def loop():
            # Checked before the refresh and without rescheduling, so a stand-
            # down request costs at most one tick. quit() (not destroy()) leaves
            # main()'s finally to unload the hook and detach.
            if quit_requested():
                print("[meter] a newer instance asked us to exit — shutting "
                      "down.", file=sys.stderr)
                self.root.quit()
                return
            self._refresh()
            self.root.after(REFRESH_MS, loop)
        loop()
        self.root.mainloop()


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
    return _unit_names().get(kind) or _pretty_id(kind)


def _summon_label(kind):
    """A summon's real display name ('Summon_Imp' -> 'Nightling Terror',
    'Rabbit_EarlyAccess_Spark' -> 'Sparktail'), falling back to the prettified
    kind for anything the unit sheet doesn't carry.

    Same sheet and the same reason as _boss_label: a unit's kind is a backend
    id, not what the game puts on its nameplate. Stripping the `Summon_`/
    `Totem_` prefix off the kind instead looks like it works — `Summon_Imp`
    reduces to a plausible "Imp" — but it is a guess that happens to read well,
    and it degenerates to a raw id on every summon not named that way."""
    return _unit_names().get(kind) or _pretty_id(kind)


def _lerp_hex(a, b, t):
    """Blend two #rrggbb colours, t in 0..1."""
    av = tuple(int(a[i:i + 2], 16) for i in (1, 3, 5))
    bv = tuple(int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02X%02X%02X" % tuple(
        int(round(x + (y - x) * t)) for x, y in zip(av, bv))


def _clamp01(v):
    return min(1.0, max(0.0, v))


class PlayerRow:
    """One meter line (rank, name, damage, dps, %, healing) over a stacked
    bar pair: damage (blue, top) and healing (green, bottom)."""

    def __init__(self, parent, on_click, fonts, theme=None):
        self.fonts = fonts
        self.theme = theme or THEME_DEFAULT
        self.f = tk.Frame(parent, bg=BG_BODY)
        # Two labels, not one line of text. The left one holds the rank, name
        # and class; the right one holds nothing but ASCII digits. They live in
        # a grid whose first column has a fixed pixel width, which is what
        # actually pins the numbers: a monospace font is only monospace for the
        # glyphs it has, and a name in a fallback face (CJK measures 1.71 cells
        # per glyph in Consolas) drags everything after it out of true. Padding
        # with spaces gets close and can't get exact — two rows will round
        # opposite ways — so the width is enforced by the layout instead.
        self.top = tk.Frame(self.f, bg=BG_BODY)
        self.top.pack(fill="x")
        self.line = tk.Label(self.top, text="", bg=BG_BODY, fg=FG_TEXT,
                             font=self.fonts["mono_10"], anchor="w")
        self.cls = tk.Label(self.top, text="", bg=BG_BODY, fg=FG_TEXT,
                            font=self.fonts["mono_10"], anchor="w")
        self.nums = tk.Label(self.top, text="", bg=BG_BODY, fg=FG_TEXT,
                             font=self.fonts["mono_10"], anchor="w")
        self.line.grid(row=0, column=0, sticky="w")
        self.cls.grid(row=0, column=1, sticky="w")
        self.nums.grid(row=0, column=2, sticky="w")
        self.dmg_track = tk.Frame(self.f, bg=BG_BAR_TRACK, height=5)
        self.dmg_track.pack(fill="x")
        self.dmg_bar = tk.Frame(self.dmg_track, bg=DMG_BAR, height=5)
        self.dmg_bar.place(relwidth=0.0, relheight=1.0)
        self.heal_track = tk.Frame(self.f, bg=BG_BAR_TRACK, height=5)
        self.heal_track.pack(fill="x", pady=(1, 2))
        self.heal_bar = tk.Frame(self.heal_track, bg=HEAL_BAR, height=5)
        self.heal_bar.place(relwidth=0.0, relheight=1.0)
        # Self-healing, green-teal, always from the left edge — so the split
        # sits at the same place on every row and the column can be read
        # down. Placed
        # after (and therefore over) the green bar, which draws the full
        # amount: only one of the two widths has to be exactly right, and no
        # rounding gap can open between them.
        self.heal_self_bar = tk.Frame(self.heal_track, bg=SELF_HEAL_BAR,
                                      height=5)
        self.heal_self_bar.place(relwidth=0.0, relheight=1.0)
        self._packed = False
        self._heal_packed = True
        self._name = None
        self._is_me = False
        self._hover = False
        # Every visible piece of the row — bars and tracks included — clicks,
        # carries the hand cursor, and lifts the row on hover: the row IS the
        # click target (it focuses the breakdown), and a target that only
        # answers on its text is a target most of the cursor misses. Enter and
        # Leave both land before Tk repaints, so crossing between the row's
        # own widgets never flickers the lift.
        for w in (self.f, self.top, self.line, self.cls, self.nums,
                  self.dmg_track, self.dmg_bar,
                  self.heal_track, self.heal_bar, self.heal_self_bar):
            w.bind("<Button-1>", lambda e: on_click(self._name))
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))
            w.config(cursor="hand2")

    def _set_hover(self, on):
        if on == self._hover:
            return
        self._hover = on
        bg = self.theme["soft"] if on else self.theme["body"]
        for w in (self.f, self.top, self.line, self.cls, self.nums):
            w.config(bg=bg)

    def set_theme(self, t):
        self.theme = t
        bg = t["soft"] if self._hover else t["body"]
        self.f.config(bg=bg)
        self.top.config(bg=bg)
        ink = t["fg_value"] if self._is_me else t["fg_text"]
        for w in (self.line, self.cls, self.nums):
            w.config(bg=bg, fg=ink)
        self.dmg_track.config(bg=t["track"])
        self.dmg_bar.config(bg=t["dmg"])
        self.heal_track.config(bg=t["track"])
        self.heal_bar.config(bg=t["heal"])
        self.heal_self_bar.config(bg=t["heal_self"])

    def _trim(self, text, cells):
        """`text` cut down until it fits `cells` monospace cells.

        Measured against the font rather than counted, because a character
        count is only a width for the glyphs the mono face actually carries.
        Nothing is padded — the grid column does that, exactly, which spaces
        cannot."""
        f = self.fonts["mono_10"]
        target = (f.measure(" ") or 1) * cells
        text = text or ""
        while text and f.measure(text) > target:
            text = text[:-1]
        return text

    def show(self, rank, p, dps, pct, focused, dmg_frac, heal_frac,
             heal_self_frac=0.0, show_heal=True, cls_tag=""):
        if not self._packed:
            self.f.pack(fill="x", pady=1)
            self._packed = True
        self._name = p.name
        tag = "▸ " if focused else "  "
        me = "*" if p.is_me else " "
        # Name and class are separate columns. The class is what tells you
        # whether the number next to it is good — a Priest at the bottom of a
        # damage meter is doing their job — and it reads far better down a
        # column of its own than trailing each name in brackets.
        #
        # Each column is a label of its own in a grid whose widths are set in
        # pixels, so nothing is padded and nothing can push its neighbour along.
        # The name is only ever TRIMMED to fit; grid does the rest. minsize is a
        # floor, not a ceiling — which is why the trim has to be measured, or a
        # wide name would simply widen its column and undo the whole exercise.
        cell = self.fonts["mono_10"].measure(" ") or 1
        self.top.grid_columnconfigure(0, minsize=cell * (5 + METER_NAME_CELLS))
        self.top.grid_columnconfigure(1, minsize=cell * METER_CLASS_CELLS)
        line = f"{tag}{rank}.{me}{self._trim(p.name, METER_NAME_CELLS)}"
        nums = f"{int(p.total):>9} {dps:>6.0f} {pct:>3.0f}%"
        if show_heal:
            nums += f"{int(p.heal_total):>9}"
            # A row that never healed has no overheal share to report, and a
            # column of "0%" down every damage dealer is noise rather than
            # information — so those cells stay blank.
            nums += (f"{p.overheal_pct:>5.0f}%" if p.heal_total > 0.5
                     else " " * 6)
        self._is_me = p.is_me
        ink = (self.theme["fg_value"] if p.is_me else self.theme["fg_text"])
        self.line.config(text=line, fg=ink)
        self.cls.config(text=cls_tag, fg=ink)
        self.nums.config(text=nums, fg=ink)
        self.dmg_bar.place_configure(relwidth=_clamp01(dmg_frac))
        if show_heal != self._heal_packed:
            self._heal_packed = show_heal
            if show_heal:
                self.heal_track.pack(fill="x", pady=(1, 2))
            else:
                self.heal_track.pack_forget()
            # The green bar carried the row's bottom margin; hand it to the
            # damage bar so rows don't run together without it.
            self.dmg_track.pack_configure(pady=(0, 0 if show_heal else 2))
        if show_heal:
            self.heal_bar.place_configure(relwidth=_clamp01(heal_frac))
            self.heal_self_bar.place_configure(
                relwidth=_clamp01(min(heal_self_frac, heal_frac)))

    def hide(self):
        if self._packed:
            self.f.pack_forget()
            self._packed = False
            # A row that vanishes mid-hover gets no Leave event; without this
            # it would come back lifted for whoever fills the slot next.
            self._set_hover(False)


class SkillColumn:
    """A titled skill list with a bar under each row (used for both damage and
    healing; bars scale to the column's biggest entry). Rows are shown as a
    prefix of a fixed pool, so pack order is stable."""

    def __init__(self, parent, title, bar_color, fonts):
        self.fonts = fonts
        self.f = tk.Frame(parent, bg=BG_BODY)
        self.bar_key = "heal" if bar_color == HEAL_BAR else "dmg"
        self.title_lbl = tk.Label(self.f, text=title, bg=BG_BODY, fg=ACCENT,
                                  font=self.fonts["ui_sm_b"], anchor="w")
        self.title_lbl.pack(fill="x")
        self.rows = []
        for _ in range(MAX_SKILL_ROWS):
            rf = tk.Frame(self.f, bg=BG_BODY)
            lbl = tk.Label(rf, text="", bg=BG_BODY, fg=FG_TEXT,
                           font=self.fonts["mono"], anchor="w")
            lbl.pack(fill="x")
            track = tk.Frame(rf, bg=BG_BAR_TRACK, height=4)
            track.pack(fill="x", pady=(0, 1))
            bar = tk.Frame(track, bg=bar_color, height=4)
            bar.place(relwidth=0.0, relheight=1.0)
            # The self-healed segment, always drawn from the left edge so the
            # split lines up down the column. Placed AFTER the main bar so it
            # sits on top of it, which means only one width has to be right:
            # the main bar draws the whole amount and this covers the self
            # share of it, and no rounding gap can open between the two.
            self_bar = tk.Frame(track, bg=SELF_HEAL_BAR, height=4)
            self_bar.place(relwidth=0.0, relheight=1.0)
            self.rows.append((rf, lbl, bar, track, self_bar))
        self._shown = 0

    def set_theme(self, t):
        self.f.config(bg=t["body"])
        self.title_lbl.config(bg=t["body"], fg=t["accent"])
        for rf, lbl, bar, track, self_bar in self.rows:
            rf.config(bg=t["body"])
            lbl.config(bg=t["body"], fg=t["fg_text"])
            track.config(bg=t["track"])
            bar.config(bg=t[self.bar_key])
            self_bar.config(bg=t["heal_self"])

    def show(self, entries, denom):
        """entries: [(label, total, hits, crits, self_total)] sorted desc;
        denom is the player's overall total for the % column."""
        n = min(len(entries), len(self.rows))
        if n > self._shown:
            for i in range(self._shown, n):
                self.rows[i][0].pack(fill="x")
        elif n < self._shown:
            for i in range(n, self._shown):
                self.rows[i][0].pack_forget()
        self._shown = n
        top = (entries[0][1] if entries else 0.0) or 1.0
        for i in range(n):
            label, tot, hits, _crits, slf = entries[i]
            pct = (tot / denom * 100) if denom else 0.0
            _rf, lbl, bar, _track, self_bar = self.rows[i]
            lbl.config(text=f"{label[:16]:<16}{int(tot):>8} {pct:>3.0f}% {hits:>3}×")
            frac = _clamp01(tot / top)
            bar.place_configure(relwidth=frac)
            # The self share of THIS row, in the same scale as the row's bar —
            # so a skill cast only on yourself reads as a bar that is entirely
            # parchment, however small the skill is next to the column's biggest.
            self_bar.place_configure(
                relwidth=frac * _clamp01(slf / tot) if tot > 0 else 0.0)


# ---------------------------------------------------------------------------
# Frida host
# ---------------------------------------------------------------------------
def build_script_source():
    data = json.loads((ANALYSIS / "resolver_data.json").read_text(encoding="utf-8"))
    off = (ANALYSIS / "meter_offsets.json").read_text(encoding="utf-8")
    js = (FRIDA_DIR / "meter_hook.js").read_text(encoding="utf-8")
    return (f"const DATA = {json.dumps(data)};\nconst OFF = {off};\n" + js)


DATA_STAMP = ANALYSIS / ".data_stamp.json"

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
REQUIRED_OFFSET_KEYS = ("Activity", "ArrayObj", "BossInfo", "BossesInfo",
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
    if not (ANALYSIS / "heal_specs.json").exists():
        print("[meter] heal_specs.json absent — regenerating so healing can "
              "be counted on full-health targets.", file=sys.stderr)
        return False
    return True


def regenerate_data(hlboot=None, force=False):
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
    for t in tools:
        print(f"[meter] regenerating {t.name} for this build ...", file=sys.stderr)
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


def find_game_process(device):
    """Locate the running Farever process by enumeration (not by name-attach).
    Waits for the game to launch if it isn't up yet; if several instances are
    running, asks which one to meter."""
    def matches():
        return [p for p in device.enumerate_processes()
                if p.name.lower() == TARGET_PROCESS.lower()]

    procs = matches()
    if not procs:
        print(f"[*] {TARGET_PROCESS} isn't running — waiting for it to start "
              "(launch the game) ...", file=sys.stderr)
        while not procs:
            # This can be a long wait, and with no console it's an invisible
            # one — so it has to be abandonable from the tray icon rather than
            # only by Ctrl+C.
            if STOP.wait(timeout=1.5):
                print("[meter] stopped while waiting for the game.",
                      file=sys.stderr)
                return None
            procs = matches()
        print(f"[*] {TARGET_PROCESS} is up.", file=sys.stderr)
    if len(procs) == 1:
        return procs[0]
    infos = [(p, _exe_path_of_pid(p.pid)) for p in procs]
    print(f"[*] {len(infos)} {TARGET_PROCESS} processes found:", file=sys.stderr)
    for i, (p, path) in enumerate(infos, 1):
        print(f"      {i}. pid {p.pid:>6}  {path or '(path unavailable)'}",
              file=sys.stderr)
    if not HAS_CONSOLE:
        # No stdin to answer on, so the question becomes a dialog. Rare enough
        # that it doesn't need to be pretty — but it does need to be asked,
        # since guessing wrong means metering the wrong client.
        i = ask_choice(
            "Farever+",
            f"{len(infos)} instances de Farever sont lancées.\n"
            "À laquelle le compteur doit-il se connecter ?",
            [f"pid {p.pid} — {path or '(chemin indisponible)'}"
             for p, path in infos])
        return infos[i][0]
    while True:
        try:
            ans = input(f"    Which one is your game? [1-{len(infos)}] "
                        "(Enter = 1): ").strip()
        except (EOFError, RuntimeError):
            return infos[0][0]
        if not ans:
            return infos[0][0]
        if ans.isdigit() and 1 <= int(ans) <= len(infos):
            return infos[int(ans) - 1][0]


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


# The rift report as an image — same reasoning as the parse image: drawn from
# the numbers rather than screenshotted from the card, so it's pixel-clean at
# any window opacity and works with the card closed. Same two-column layout,
# same tiering, same palette, so a paste reads as the card it came from.
RIFT_IMG_COL_W = 350
# The leaderboard's right-hand gutters, measured back from the column edge:
# the share sits in the first, the total in the second, and the rate — the
# headline — takes whatever is left before the name. Named because the rows
# and their column heading both align to them and must not drift apart.
RIFT_IMG_PCT_W = 40
RIFT_IMG_TOT_W = 72
RIFT_IMG_PAD = 20
RIFT_IMG_GAP = 26


def render_rift_report_image(data, path=None):
    """Draw an end-of-rift report dict as a PIL image. Returns the image;
    also writes a PNG when `path` is given."""
    from PIL import Image, ImageDraw

    ui_mvp = _parse_font(PARSE_FONT_UI, 22)
    ui = _parse_font(PARSE_FONT_UI, 15)
    ui_rank = _parse_font(PARSE_FONT_UI, 14)
    ui_small = _parse_font(PARSE_FONT_UI, 11)
    mono = _parse_font(PARSE_FONT_MONO, 13)
    mono_small = _parse_font(PARSE_FONT_MONO, 11)

    W = RIFT_IMG_PAD * 2 + RIFT_IMG_COL_W * 2 + RIFT_IMG_GAP
    img = Image.new("RGB", (W, 1600), RIFT_BODY)
    d = ImageDraw.Draw(img)

    d.rectangle((0, 0, W - 1, 39), fill=RIFT_GLOW)
    d.text((RIFT_IMG_PAD, 10), "RAPPORT DE FAILLE", font=ui, fill=RIFT_TIME)
    stamp = time.strftime("%d/%m/%Y %H:%M", time.localtime(data["at"]))
    d.text((W - RIFT_IMG_PAD - d.textlength(stamp, font=mono_small), 14),
           stamp, font=mono_small, fill=RIFT_PEAK)

    def heading(cx, y, text):
        d.text((cx, y), text, font=ui_small, fill=RIFT_TITLE)
        tw = d.textlength(text, font=ui_small)
        d.line((cx + tw + 8, y + 7, cx + RIFT_IMG_COL_W, y + 7), fill=RIFT_GLOW)
        return y + 22

    # The card's ★ and ✚ are DRAWN here rather than typed: PIL does no font
    # fallback, so glyphs Segoe UI Bold doesn't carry come out as tofu boxes —
    # measured on the first render. Shapes can't be missing from a font.
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

    def column(cx, ph):
        y = 52
        d.text((cx, y), phase_label(ph["label"]).upper(), font=ui, fill=RIFT_PEAK)
        y += 24
        # Rates first, totals under them — the same primary/subtext pairing
        # the on-screen card uses, because this image IS that card to anyone
        # it gets pasted to.
        dps = _rate_text(ph["total"], ph["duration"], "DPS")
        hps = _rate_text(ph["heal"], ph["duration"], "HPS")
        d.text((cx, y), f"{Overlay._mmss(ph['duration'])}  ·  "
               f"{dps or '— DPS'}  ·  {hps or '— HPS'}",
               font=ui, fill=RIFT_TIME)
        y += 20
        d.text((cx, y), f"{_n(ph['total'])} dégâts  ·  "
               f"{_n(ph['heal'])} soins" + _overheal_note(ph),
               font=ui_small, fill=RIFT_TITLE)
        y += 22
        players = ph["players"]
        if not players:
            d.text((cx, y), "rien n'a été enregistré pour cette phase",
                   font=ui_small, fill=RIFT_TITLE)
            return y + 20

        y = heading(cx, y, "MVP")
        mvp = players[0]
        star(cx + 11, y + 14, 11, REPORT_MEDALS[0])
        d.text((cx + 28, y), Overlay._elide_name(mvp["name"]),
               font=ui_mvp, fill=REPORT_MEDALS[0])
        if mvp.get("cls"):
            d.text((cx + 32 + d.textlength(Overlay._elide_name(mvp["name"]),
                                           font=ui_mvp), y + 12),
                   mvp["cls"], font=ui_small, fill=RIFT_TITLE)
        y += 30
        mvp_dps = _rate_text(mvp["total"], ph["duration"], "DPS")
        mvp_total = f"{_n(mvp['total'])} dégâts"
        d.text((cx + 28, y), mvp_dps or mvp_total, font=ui, fill=RIFT_TIME)
        y += 19
        if mvp_dps:
            d.text((cx + 28, y), mvp_total, font=ui_small, fill=RIFT_TITLE)
            y += 17
        healer = max(players, key=lambda p: p["heal"])
        if healer["heal"] > 0.5:
            hps_txt = _rate_text(healer["heal"], ph["duration"], "HPS")
            heal_total = f"{_n(healer['heal'])} soins"
            who = Overlay._elide_name(healer["name"])
            if healer.get("cls"):
                who += f" ({healer['cls']})"
            plus(cx + 8, y + 9, 7, REPORT_HEAL)
            d.text((cx + 22, y), f"{who}   {hps_txt or heal_total}",
                   font=ui_rank, fill=REPORT_HEAL)
            y += 20
            if hps_txt:
                d.text((cx + 22, y),
                       heal_total + _overheal_note(healer, "   {:.0f}% en excès"),
                       font=ui_small, fill=RIFT_TITLE)
                y += 17

        def rank_rows(y, entries, total, key):
            for i, p in enumerate(entries[:5], 1):
                top3 = i <= 3
                fg = REPORT_MEDALS[i - 1] if top3 else RIFT_TITLE
                nfont = ui_rank if top3 else ui_small
                d.text((cx, y), str(i), font=nfont, fill=fg)
                nm = Overlay._elide_name(p["name"])
                d.text((cx + 18, y), nm,
                       font=nfont, fill=RIFT_TIME if top3 else RIFT_TITLE)
                # Drawn separately, in the dim colour, so a long name's
                # elision can never eat the acronym.
                if p.get("cls"):
                    # Sat on the name's baseline, not its top: the acronym is
                    # 11px against a 14px (or 11px) name, and hanging it from
                    # the same y reads as a superscript.
                    d.text((cx + 22 + d.textlength(nm, font=nfont),
                            y + (5 if top3 else 2)),
                           p["cls"], font=mono_small, fill=RIFT_TITLE)
                # Three numbers in two weights: the RATE is the headline, the
                # total it came from and the share it represents are subtext.
                # Right-aligned to fixed gutters so the columns line up down
                # the card however wide the numbers run.
                amt = f"{_n(p[key])}"
                pct = f"{p[key] / total * 100 if total else 0.0:.0f}%"
                rate = _rate(p[key], ph["duration"])
                rate_s = "—" if rate is None else f"{_n(rate)}"
                d.text((cx + RIFT_IMG_COL_W
                        - d.textlength(pct, font=mono_small), y + 3),
                       pct, font=mono_small, fill=RIFT_TITLE)
                d.text((cx + RIFT_IMG_COL_W - RIFT_IMG_PCT_W
                        - d.textlength(amt, font=mono_small), y + 3),
                       amt, font=mono_small, fill=RIFT_TITLE)
                d.text((cx + RIFT_IMG_COL_W - RIFT_IMG_PCT_W - RIFT_IMG_TOT_W
                        - d.textlength(rate_s, font=mono), y + 2),
                       rate_s, font=mono, fill=RIFT_TIME)
                y += 22 if top3 else 19
            return y

        def rank_header(y, rate_label):
            """Names the three columns once. Same gutters as rank_rows, so the
            heading sits directly over the numbers it names."""
            for text, gutter in ((("PART"), 0),
                                 (("TOTAL"), RIFT_IMG_PCT_W),
                                 ((rate_label), RIFT_IMG_PCT_W + RIFT_IMG_TOT_W)):
                d.text((cx + RIFT_IMG_COL_W - gutter
                        - d.textlength(text, font=mono_small), y),
                       text, font=mono_small, fill=RIFT_TITLE)
            return y + 15

        y = heading(cx, y + 6, "DÉGÂTS — TOP 5")
        y = rank_header(y, "DPS")
        y = rank_rows(y, players, ph["total"], "total")
        healers = sorted((p for p in players if p["heal"] > 0.5),
                         key=lambda p: -p["heal"])
        y = heading(cx, y + 4, "SOINS — TOP 5")
        if healers:
            y = rank_header(y, "HPS")
            y = rank_rows(y, healers, ph["heal"], "heal")
        else:
            d.text((cx, y), "aucun soin enregistré", font=ui_small,
                   fill=RIFT_TITLE)
            y += 19

        y = heading(cx, y + 4, "DÉGÂTS PAR TYPE")
        top = ph["elements"][0][1] if ph["elements"] else 0.0
        for el, amt in ph["elements"][:8]:
            colour = _lerp_hex(element_color(el), "#FFFFFF", 0.30)
            d.text((cx, y), element_label(el),
                   font=ui_small, fill=colour)
            frac = amt / top if top else 0.0
            bar_x = cx + 78
            bar_w = RIFT_IMG_COL_W - 78 - 44
            d.rectangle((bar_x, y + 4, bar_x + int(bar_w * frac), y + 11),
                        fill=colour)
            pct = amt / ph["total"] * 100 if ph["total"] else 0.0
            d.text((cx + RIFT_IMG_COL_W
                    - d.textlength(_pct1(pct), font=mono_small), y + 2),
                   _pct1(pct), font=mono_small, fill=RIFT_TIME)
            y += 18
        return y

    bottoms = [column(RIFT_IMG_PAD, data["phases"][0]),
               column(RIFT_IMG_PAD + RIFT_IMG_COL_W + RIFT_IMG_GAP,
                      data["phases"][1])]
    mid = RIFT_IMG_PAD + RIFT_IMG_COL_W + RIFT_IMG_GAP // 2
    h = max(bottoms) + RIFT_IMG_PAD
    d.line((mid, 52, mid, h - RIFT_IMG_PAD), fill=RIFT_GLOW)
    img = img.crop((0, 0, W, h))
    ImageDraw.Draw(img).rectangle((0, 0, W - 1, img.height - 1),
                                  outline=RIFT_EDGE, width=2)
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
    if not HAS_CONSOLE:
        # Asked at most once per install in practice: the file normally sits
        # next to the running exe, and that's checked first.
        p = ask_directory("Farever+ — où Farever est-il installé ? "
                          "(le dossier qui contient hlboot.dat)")
        if p is None:
            return None
        cand = p if p.is_file() else p / "hlboot.dat"
        if cand.is_file():
            return cand
        message_box(f"Pas de hlboot.dat dans :\n{p}\n\nLe compteur démarre "
                    "avec les données fournies, ce qui convient sauf si "
                    "Farever a été mis à jour depuis.",
                    "Farever+", 0x30)      # MB_ICONWARNING
        return None
    while True:
        try:
            d = input("    Where is Farever installed? (folder containing "
                      "hlboot.dat, Enter to skip): ").strip().strip('"')
        except (EOFError, RuntimeError):
            return None
        if not d:
            return None
        p = Path(d)
        cand = p if p.is_file() else p / "hlboot.dat"
        if cand.is_file():
            return cand
        print(f"    [!] no hlboot.dat at {p}", file=sys.stderr)


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


def _run(tray, session, ui_state, world, rift_rec, heal_sizer):
    device = frida.get_local_device()
    proc = find_game_process(device)
    if proc is None:
        return                      # stopped from the tray before we attached
    pid = proc.pid

    # Match the data files to the build that is ACTUALLY RUNNING before
    # hooking: hlboot.dat is taken from the attached process's own install
    # directory, so a version/install mismatch — the usual cause of a slow or
    # failed table search — is impossible. Skipped when the file is unchanged.
    hlboot = locate_hlboot(pid)
    if hlboot is None:
        print("[meter] using the shipped data files as-is (couldn't locate "
              "hlboot.dat to verify them).", file=sys.stderr)
    else:
        print(f"[*] game data: {hlboot}", file=sys.stderr)
        regenerate_data(hlboot)   # best-effort; falls back to existing files

    print(f"[*] attaching to {TARGET_PROCESS} (pid {pid}) ...", file=sys.stderr)
    try:
        fsession = device.attach(pid)
    except frida.ProcessNotFoundError:
        sys.exit(f"[!] {TARGET_PROCESS} (pid {pid}) s'est fermé avant la "
                 "connexion. Relance le jeu, puis le compteur.")
    except frida.PermissionDeniedError:
        sys.exit("[!] connexion refusée — si Farever tourne en "
                 "administrateur, lance aussi le compteur en administrateur.")

    def on_detached(*args):
        """The frida session died — in practice, the game closed or crashed.
        Fires on frida's own thread. On a normal quit this fires too (we're
        the ones detaching), but by then the finally below has already cleared
        _OVERLAY, which is what keeps the prompt out of that path."""
        reason = str(args[0]) if args else ""
        print(f"[meter] game session detached ({reason or 'unknown'}).",
              file=sys.stderr)
        ov = _OVERLAY["ref"]
        if ov is not None:
            ov.on_game_exit(reason)
    fsession.on("detached", on_detached)

    ready = {"ok": None}
    ready_evt = threading.Event()
    liveness = {"t": time.monotonic(), "printed": 0.0}
    hero_id = {"name": None}           # last local hero, to keep the log quiet
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
        elif k == "heal":
            # The hook reports what LANDED (0 for a heal on a full-health
            # target); this fills in how big the heal itself was, before both
            # aggregators see it, so the two can never disagree.
            heal_sizer.stamp(p)
            session.record_heal(p)
            rift_rec.record("heal", p)
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
        elif k == "window":
            name, is_open = p.get("name"), bool(p.get("open"))
            ui_state.set_window(name, is_open)
            # Logged because every class the game opens now hides the overlay
            # (MENU_IGNORE_WINDOWS aside) — if the meter goes missing and stays
            # missing, these lines name the window that's holding it down.
            print(f"[meter] game window {name} {'open' if is_open else 'closed'}",
                  file=sys.stderr)
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
                hero_id["name"] = name
                print("[meter] local hero "
                      + ("identified." if first else "changed."), file=sys.stderr)
        elif k == "shard":
            # Every player on the layer, with their class — which is where the
            # meter's class tags come from. Sent only when the roster changed.
            world.set_shard(p.get("list") or [])
        elif k == "log":
            print("[hook]", p.get("msg"), file=sys.stderr)
        elif k == "progress":
            now = time.monotonic()
            if now - liveness["printed"] > 5.0:     # throttle the status line
                liveness["printed"] = now
                print(f"[meter] hook scanning memory ... "
                      f"({p.get('done')}/{p.get('total')} regions)",
                      file=sys.stderr)
        elif k == "ready":
            ready["ok"] = p.get("ok")
            print(f"[meter] hook ready ok={p.get('ok')}", file=sys.stderr)
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
            if STOP.is_set():
                return False        # asked to quit mid-scan
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
    for attempt in range(1, 4):
        if STOP.is_set():
            break
        ready["ok"] = None
        ready_evt.clear()
        liveness["t"] = time.monotonic()
        try:
            script = load_hook()
        except Exception as e:
            print(f"[meter] load attempt {attempt} failed: {e}", file=sys.stderr)
            script = None
        if script is not None and wait_ready() and ready["ok"]:
            break
        print(f"[meter] hook didn't come up (attempt {attempt}/3); "
              "cleaning up and retrying ...", file=sys.stderr)
        if script is not None:
            try:
                script.unload()
            except Exception:
                pass
            script = None
        if ready["ok"] is False:            # search concluded, table not found
            regenerate_data(hlboot, force=True)   # => refresh data and retry
        time.sleep(1.0)

    if STOP.is_set():
        # Stopped from the tray during startup. Same teardown the overlay's
        # finally does — the hook may be half-loaded, and leaving it attached is
        # what destabilises the game.
        print("[meter] stopped during startup.", file=sys.stderr)
        try:
            if script is not None:
                script.unload()
            fsession.detach()
        except Exception:
            pass
        release_instance_lock()
        return

    if script is None or ready["ok"] is not True:
        print("[meter] could not initialise the hook after 3 attempts.\n"
              "        Fully close Farever and reopen it, then relaunch the meter.\n"
              "        If it keeps happening, send the full log above to whoever\n"
              "        gave you the meter — the [hook] lines say where it stopped.\n"
              "        (Avoid repeatedly relaunching against a stuck session — "
              "that can crash the game.)", file=sys.stderr)
        # The overlay still comes up, so without this the windowed build would
        # put two empty windows on screen and never say why they stay empty.
        if not HAS_CONSOLE:
            message_box(
                "Le compteur n'a pas pu se connecter à Farever : il "
                "n'affichera aucun chiffre.\n\nFerme complètement Farever, "
                "relance-le, puis relance le compteur.\n\nLe détail est "
                f"dans :\n{LOG_FILE}",
                "Farever+ — connexion impossible", 0x30)   # MB_ICONWARNING

    print("[*] overlay starting. Open the game's escape menu for the control "
          "menu (and to drag the windows / click a row to inspect). Only "
          "hotkey: Shift+\\ resets the encounter.", file=sys.stderr)

    def configure_hook(**kw):
        """Push a setting to the running agent. Wrapped so the overlay doesn't
        have to know about frida, and so a dead script is a logged failure
        rather than an exception in a menu callback."""
        if script is None:
            return
        script.post(dict(kw, type="config"))

    overlay = Overlay(session, pid, ui_state, world,
                      configure=configure_hook)
    # From here the overlay owns shutdown: it's the only thing that can return
    # from the mainloop and let the finally below unload the hook and detach.
    _OVERLAY["ref"] = overlay
    if STOP.is_set():
        # Asked to stop during the hook's setup, which the overlay didn't exist
        # to hear. Honour it rather than putting windows on screen.
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
        try:
            script.unload()
            fsession.detach()
        except Exception:
            pass
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
            message_box(e.code, "Farever+ — démarrage impossible", 0x10)
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        if not HAS_CONSOLE:
            message_box(
                "Le compteur a rencontré une erreur inattendue et s'est "
                "arrêté.\n\n"
                f"Le détail est dans :\n{LOG_FILE}",
                "Farever+ — erreur", 0x10)
        raise


if __name__ == "__main__":
    _cli()
