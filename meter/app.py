"""The application: state, actions, and the pages it pushes to the window."""
from __future__ import annotations

import ctypes
import json
import math
import os
import re
import sys
import threading
import unicodedata
import time
from collections import defaultdict, deque
from pathlib import Path
import i18n
import gamedata
import report
from contributors import CONTRIBUTORS
from riftspot import RIFT_ACT, rift_zone
import themes
import steamacct
from i18n import tr
from buildtab import BuildTab

from common import (
    ACCOUNTS_FILE, ACCOUNT_TABS, ACH_FILE, ANALYSIS, APP_TABS, APP_TABS_APP_FIRST, APP_TAB_DEFAULT,
    SETTINGS_TOPICS, THIRD_PARTY_FILE, month_name,
    APP_TAB_LABELS,
    BEST_TIMES_CACHE, CODEX_FILE, COLLECTION_FILE, DATA_HOME, DUNGEONS_DIR,
    ELEMENTS_FILE, EVENTS_MAX, FAREVER_STEAM_APPID, HELP_DIR, HELP_GROUPS,
    ITEM_CODEX_FILE, LOG_FILE, MAX_PLAYER_ROWS, MAX_SKILL_ROWS,
    PARSE_LENGTH_SECS, PARSE_PREROLL_SECS, POSITION_CACHE, REFRESH_MS,
    RIFTS_DIR, RIFT_PORTAL_SECS, RIFT_STYLE_SECS, SETTINGS_CACHE, VERSION,
    _class_tag, _mmss, _n, _pct1, _pretty_id, class_key, date_fr, date_long,
    element_color, element_label, message_box, pct_sp)
from winsys import (
    HK_RESET, REBIND_TO, RESET_BIND, VK_CONTROL, VK_MENU, VK_MOUSE, VK_SHIFT,
    VK_UNBINDABLE, WM_REBIND, _monitor_containing, bind_label, client_origin,
    copy_image_to_clipboard, copy_text_to_clipboard, foreground_pid,
    game_window, quit_requested, start_hotkeys)
import goals as G
from me import MeStore
from gamedata import (
    MODEL_FORMAT, REGENERATING, _boss_label, char_look, _element_done, _fr_names, bestiary_catalogue,
    _codex_thresholds, _table, achievements_catalogue,
    _spark_units, _unit_label,
    build_data, collection_catalogue, dungeon_catalogue,
    item_rarity,
    dungeon_name, item_icon, item_label, item_type,
    ensure_ui_frame, item_model_json, locate_hlboot, regenerate_data, world_map,
    DATA_GENERATION, DATA_CONSENT, GENERATED_GROUPS, GENERATED_PICTURES,
    GENERATED_PICTURE_COST,
    game_folder_hlboot, generated_pictures,
    needs_first_data)
from combat import (
    DUNGEON_DIFFICULTIES, GameUIState, PartySession, WorldSnapshot)
from bosssheet import _icon_ok, boss_sheet_view
from journal import (
    JOURNAL_PAGE, RIFT_JOURNALS_KEPT, journal_view, load_journal,
    prune_journals, remove_journal, save_journal)
from views import (
    _ach_crest, _ach_text, _family_label,
    collection_sheet,
    encyclopedia_item, encyclopedia_view,
    CLASS_LABELS, LUCK_LABELS, RIFT_STAT_ICONS, RIFT_STAT_LABELS, _pct, _profile_luck, _profile_stats, achievements_view,
    bestiary_view, character_view, collection_view, droptable_view,
    hunt_detail_view, map_view, rift_rewards_view, soulwell_name,
    MINIMAP_LAYERS, minimap_points)
from reports import (
    _overheal_note, _rate_text, _report_name, render_rift_report_image,
    report_view)
from updater import RELEASES_URL, REPO_URL, Updater, clean_downloads
from bridge import MenuBridge, _Scheduler, _parse_help
from gamelink import GameLink


# The Help tab's links. A link without its url shows without a button.
# the page where players can support the project (the header's button)
TIPEEE_URL = "https://fr.tipeee.com/farever-book"

HELP_LINKS = (
    {"id": "discord", "t": "Discord", "url": None,
     "meta": "Une question, un bug : contacte @Aarnow sur Discord."},
    {"id": "github", "t": "GitHub", "url": REPO_URL,
     "meta": "Le code de Farever Book, ses versions et leurs nouveautés."},
    {"id": "tipeee", "t": "Tipeee", "url": TIPEEE_URL,
     "meta": "Soutenir le développement de Farever Book, si tu le souhaites."},
)


# Runs kept per dungeon: the 11th removes the oldest (records stay).
DUNGEON_RUNS_KEPT = 10


# The overlays: the grid their distance to the edge snaps to, and how long
# after the hook last saw our hero they stay up (it reports every 3 s).
OVERLAY_GRID = 8
OVERLAY_HERO_SECS = 8.0
# The minimap: its zoom's steps (map pixels a world unit; the tiles' own
# is 512 / 576), the one at first, its sizes (CSS px), the open world's
# level (the only one its tiles draw), a position older than this hides it
MINIMAP_ZOOMS = (0.2, 0.3, 0.45, 0.65, 0.9, 1.25, 1.7)
MINIMAP_ZOOM_DEFAULT = 3
MINIMAP_SIZES = (140, 320, 20)
MINIMAP_WORLD = "World/W1_Siagarta"
MINIMAP_POS_SECS = 5.0
MINIMAP_LAYERS_DEFAULT = ("chest", "orb", "obelisk", "respawn", "dungeon", "rift",
                          "pet", "foe")
# an example icon before each layer, in the overlays' settings
MINIMAP_LAYER_ICONS = {"chest": "mk:chest", "orb": "mk:orb", "obelisk": "mk:obelisk",
                       "respawn": "mk:respawn", "ore": "item:CopperOre",
                       "plant": "item:LavendulaPetal", "pet": "coll:Frog_Green",
                       "dungeon": "mk:dungeon", "rift": "mk:rift", "merchant": "mk:merchant",
                       "foe": "foe:Skunk_Z1W"}


# How often one's own character is re-read while playing (kept on disk).
SELF_PROFILE_SECS = 300.0


def _dungeon_backdrop(kind, boss, region=""):
    """The picture behind a dungeon's card: its loading screen, else an
    unclaimed one named after its boss (Munster_Chuck), else its region's
    codex illustration (Z2: loading_screen4); "" when there is none."""
    cat = bestiary_catalogue()
    got = (cat.get("loading") or {}).get(kind)
    if got:
        return got
    key = re.sub(r"[^a-z]", "", str(boss or "").lower())
    try:
        stems = [p.stem for p in (ANALYSIS / "dungeon_bg").glob("*.webp")]
    except OSError:
        stems = []
    named = next((st for st in stems
                  if key and re.sub(r"[^a-z]", "", st.lower()) == key), "")
    if named:
        return named
    art = (cat.get("regionArt") or {}).get(str(region or "").split("_")[0])
    return art if art in stems else ""


# the overlays' looks: the game's own frames and colours, for now the only one
OVERLAY_THEMES = {"farever": "Farever", "minimal": "Minimaliste"}


# an overlay's greatest size (%), when not OV_SCALE_MAX
OV_SCALE_MAX = 110
OV_SCALE_MAX_OF = {"minimap": 240}


def _ov_style(v, oid=None):
    """An overlay's settings, kept in bounds: opacity 30-100 %, size
    70-110 % (the minimap's to 160), its list's greatest height (px, 0:
    none) 30-900."""
    def num(k, lo, hi):
        try:
            x = v.get(k)
            return max(lo, min(hi, int(100 if x is None or x == "" else x)))
        except (TypeError, ValueError):
            return 100
    try:
        maxh = int(v.get("maxh") or 0)
    except (TypeError, ValueError):
        maxh = 0
    return {"opacity": num("opacity", 30, 100),
            "scale": num("scale", 70, OV_SCALE_MAX_OF.get(oid, OV_SCALE_MAX)),
            # its background's own opacity (its frame, its panels), 0-100 %
            "bg": num("bg", 0, 100),
            "maxh": max(30, min(900, maxh)) if maxh else 0}


# the saved settings' revision: 1, the boss pull reset turned on for all
SETTINGS_REV = 1


def _youtube_id(url):
    """A YouTube video's id in a link (watch?v=, youtu.be/, shorts/, embed/,
    live/), or None."""
    m = re.search(r"(?:youtube(?:-nocookie)?\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)"
                  r"|youtu\.be/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])", str(url or ""))
    return m.group(1) if m else None


class App:
    """The whole meter, minus the game connection: the aggregation loop, the
    saved data, and the window (a WebView2 app in its own process, see
    menu_host.py). Works with the game closed: saved data stays readable.

    Threading: the game link, the hotkey hook, the tray and the window's pipe
    run on their own threads and reach this object only through _enqueue();
    everything else runs on the main thread, in run()."""

    def __init__(self, session: PartySession, ui_state=None, world=None,
                 link=None, target_pid=None):
        self.session = session
        self.ui_state = ui_state if ui_state is not None else GameUIState()
        self.world = world if world is not None else WorldSnapshot()
        self.link = link
        # The running game's pid, once connected. Given directly only by tests
        # that run without a GameLink.
        self.target_pid = target_pid
        self.root = _Scheduler()            # after()/after_cancel()/quit()
        self.menubridge = MenuBridge(self)
        self.buildtab = BuildTab(self.menubridge.invalidate, self._toast_msg)
        # the overlays over the game: the group meter and the goals
        self.goals = G.Goals()
        # new versions, from the releases repository (updater.py)
        clean_downloads()
        self.updater = Updater(self.menubridge.invalidate)
        # one's own last known state, shown with the game closed
        self.me = MeStore()
        stock, _at = self.me.got("stock")
        if isinstance(stock, dict):
            self.goals.on_stock(stock.get("items"), stock.get("banks"))
        self._me_name = None                # the hero in game now
        self._me_auto = None                # (name, since): own profile asked
        self._me_auto_next = 0.0
        self._ov_tab = "dmg"                # the meter overlay's tab
        self._ov_pos = {}                   # overlay -> its anchor (see _ov_moved)
        self._ov_on = {"meter": True, "goals": False, "luck": False,
                       "bonus": True,       # shown, per overlay
                       "meterheal": True,   # (the heals' meter, split)
                       "currencies": False, "minimap": False, "quest": False}
        # the quests overlay: its kinds shown, its lines at most
        self._quest_kinds = {"drop": True, "codex": True, "ach": True}
        self._quest_max = 12
        # the minimap: the hero where the hook last saw it (x, y, facing,
        # level, when), its shape, turned with the hero or north up, its
        # zoom's step, its size, its layers, the points found left out
        self._mm_pos = None
        # a meter overlay's parts the player hid ("title", "time"), per overlay
        self._meter_hide = {"meter": set(), "meterheal": set()}
        # the units around (the hook's): wild companions and monsters
        # alive, [[kind, x, y]], the level, when
        self._mm_units = ([], [], "", 0.0)
        self._mm_shape = "round"
        self._mm_rotate = False
        self._mm_zoom = MINIMAP_ZOOM_DEFAULT
        self._mm_size = 200
        self._mm_layers = set(MINIMAP_LAYERS_DEFAULT)
        self._mm_hide_found = True
        # only the enemies a quest wants (the Quêtes overlay's), its button
        self._mm_quest_only = False
        self._quest_units = set()
        self._quest_seen = {}               # monster seen alive -> when (the quests')
        self._quest_pins = set()            # the quests of no place the player follows
        # the currencies overlay: those the player hid, how many a line
        self._cur_hidden = set()
        self._cur_per_row = 4
        # the meter split in two overlays, damage ("meter") and heals
        # ("meterheal"), each its own place and settings; else one, tabbed
        self._ov_split = False
        # the loot luck overlay's lines the player hid (their counter ids)
        self._luck_hidden = set()
        self._bonus_dungeon = None          # the hero's bonus heroic dungeon
        # locked (the mouse goes through): in the game's focus mode, or always
        self._ov_lock_always = False
        # hidden while the game's cursor is free (one of its windows open)
        self._ov_hide_free = False
        # overlay -> {opacity, scale} (%): the minimap at 130 % at first
        self._ov_style = {"minimap": {"opacity": 100, "scale": 130, "maxh": 0}}
        self._ov_theme = "farever"          # the overlays' look (OVERLAY_THEMES)
        self._hero_at = 0.0                 # last time the hook saw our hero
        self._action_q = []
        self._q_lock = threading.Lock()
        self._stopping = False
        self._ui_seen = False               # the window has been up once

        # ---- what the player chose (saved) ----
        self.mode = "party"                 # "party" (group only) or "all"
        self._show_heal = True
        self._parse_journal = False         # a parse keeps its journal
        self._auto_reset_boss = True        # on unless the player turns it off
        # the meter afresh on the first hit on a training dummy (gamelink
        # _dummy_hit): on unless the player turns it off
        self._auto_reset_dummy = True
        # Collection and Succès: the content out of reach in the game as it
        # is (no way to get it in its data) left out
        self._hide_na = False
        # the game's own bars with their values written in (its developer
        # preference, set by the hook): off by default, a prototype
        self._bar_values = False
        # the text in the bars ("none", "pct", "value", "max": value / max),
        # the monsters' and the bosses'; the bosses' bars: their colours
        # ("": the game's), thickness, level
        self._foe_text = "none"
        self._boss_text = "none"
        self._boss_color = ""
        self._boss_lag = ""
        self._boss_bg = ""
        self._boss_extra = 0
        self._boss_level_show = True
        self._boss_level_scale = 100
        self._boss_level_dx = 0
        self._boss_level_dy = 0
        # the allies' (players') bars: thicker, wider (px), their level
        # (shown, %), their name in the bar
        self._ally_extra = 0
        self._ally_width = 0
        self._ally_level_show = True
        self._ally_level_scale = 100
        # their text (as the monsters'), their life's colours and gradient end,
        # the life being lost, the background ("": the game's)
        self._ally_text = "none"
        self._ally_color = ""
        self._ally_color_end = ""
        self._ally_lag = ""
        self._ally_bg = ""
        self._me_color = ""
        self._me_color_end = ""
        self._me_lag = ""
        self._me_bg = ""
        # every life's gradient end ("": the game's, in its own ratio)
        self._bar_colors_end = {"mine": "", "aggro": "", "neutral": ""}
        self._boss_color_end = ""
        self._ally_level_dx = 0
        self._ally_level_dy = 0
        # their class's icon ("none", "replace": in the level's square,
        # "beside": apart), its size (%), its place (px)
        self._ally_class = "none"
        self._ally_class_scale = 100
        self._ally_class_dx = 0
        self._ally_class_dy = 0
        # the class icons' grounds: a class -> "#RRGGBB" ("": the game's)
        self._class_colors = {"Warrior": "", "Mage": "", "Rogue": "", "Priest": ""}
        # the monsters' bars wider (px)
        self._foe_width = 0
        # the text in the bars: its size (%), its outline ("": none), its width
        self._foe_text_scale = 100
        self._boss_text_scale = 100
        # the Farever Book button made in the game's menu (Échap) by the hook
        self._esc_button = True
        # the skills' bar: its frame a plain dark rectangle (opacity %, corners px)
        self._skillbar_min = False
        self._skillbar_alpha = 55
        self._skillbar_radius = 10
        # the hero's own health bar: moved, sized (px, 0 the game's), its
        # text; the effects on the hero moved with it, then their own offset
        self._me_bar = False
        self._me_w = 0
        self._me_h = 0
        self._me_dx = 0
        self._me_dy = 0
        self._fx_dx = 0
        self._fx_dy = 0
        self._me_text = "none"
        # the quick setups imported (name -> their values), the last share's note
        self._quick_custom = {}
        self._quick_note = ""
        self._quick_imp = None              # the last import's answer: {seq, ok, msg}
        # the targeting options (the hook's, on the monsters' nameplates):
        # the target indicator's size (%), the health bars' frame (px more),
        # the bars lighter on a monster after the hero, darker otherwise
        self._target_scale = 100
        self._frame_extra = 0
        # the health bars' colours ("#RRGGBB", "": the game's): a monster
        # targeting the hero, an aggressive one, a neutral one; the target
        # indicator's halo ("": none)
        self._bar_colors = {"mine": "", "aggro": "", "neutral": ""}
        # the life being lost (same kinds), the bars' background ("": the game's)
        self._lag_colors = {"mine": "", "aggro": "", "neutral": ""}
        self._bar_bg = ""
        # the level beside the bars: its size (%), shown, its digits' colour,
        # its picture's tint ("": the game's), its offset (px)
        self._level_scale = 100
        self._level_show = True
        self._level_text = ""
        self._level_tint = ""
        self._level_dx = 0
        self._level_dy = 0
        # the blur of the indicator's halo and of the targeted bar's outline (px)
        self._glow_blur = 5
        self._line_blur = 0
        self._target_glow = ""
        # the outline of the targeted monster's bar ("": none), its width
        self._target_line = ""
        self._target_line_w = 2
        # the bonus heroic dungeon in the loot luck overlay (one overlay),
        # else in its own
        self._luck_merge = False
        self._rift_keep = 0                 # rift reports kept (0: all)
        self._rift_sel = set()              # rift reports ticked for deletion
        self._rift_confirm = False          # "delete" pressed once
        self._zoom = 130                    # the window's own size, percent
        self._view3d = True                 # previews in 3D (else the picture)
        self._coll_sel = None               # (category, id): the Collection's pick
        self._theme = themes.DEFAULT        # the colour theme
        self._lang = i18n.DEFAULT           # the interface's language

        # ---- what the window is showing (not saved) ----
        self._menu_tab = APP_TAB_DEFAULT
        self.focus_player = None            # player picked in the meter
        self._help_open = None
        self._rift_view = None              # the rift report being read
        self._launching_until = 0           # Play was clicked: until then
        self._setup = None                  # the welcome screen's state
        self._settings_topic = "meter"      # Réglages: the subject shown
        self._self_prof = None              # own luck counters (hook, 5 s)
        self._stats_logged = set()          # heroes whose HeroStats were logged
        # a rift's gates: the game's running count when it began, and the
        # report waiting for the count after it (see _rift_gates_seen)
        self._rift_gates0 = None
        self._rift_gates_for = None
        self._dungeon_kind = None           # the dungeon whose runs are listed
        self._dungeon_tab = "info"          # its page's tab: info, runs, help
        self._run_tab = "report"            # a run's page: report, journal
        self._journal = (None, [])          # (run file, its journal's lines)
        # the journal's filters: a player, the kinds shown, lines shown
        self._journal_f = {"who": "",
                           "kinds": {"hit", "taken", "heal", "status"},
                           "limit": JOURNAL_PAGE}
        self._encyclo_sel = None            # the Encyclopedia's item shown
        self._encyclo_reset = 0             # bumped: the page on its first category
        self._encyclo_jump = 0              # bumped: the page on the item opened
        self._encyclo_list = (None, None)   # (data, its list), computed once
        self._encyclo_sheets = {}           # item -> its sheet, for that data
        self._icons_sent = False
        self._dungeon_videos = {}           # dungeon -> a YouTube video's id
        self._hunt_sel = None               # the monster whose page is open
        self._dungeon_view = None           # the dungeon run being read
        self._collection_owned = None       # .meter_collection.json, loaded
        self._codex_data = None             # .meter_codex.json, loaded
        self._elements_logged = False
        self._item_codex_logged = False
        self._hero_ids = {}                 # character -> its id in the game's database
        self._me_hkey = None                # the hero in game now, its key
        self._ach_logged = False
        self._ach_data = None
        self._item_codex_cache = (None, {})
        self._roster = []                   # players around, from the hook
        self._party = []                    # the local player's group (names)
        self._icons_ok = {}                 # skill id -> has its own picture
        self._roster_at = 0.0
        self._profiles = None               # profiles analysed this session
        self._char_sel = None               # the profile being read
        self._me_build = None               # (name, since): own build asked
        self._char_wait = None              # (name, since) of an analysis
        self._elements_data = None          # .meter_elements.json, loaded
        self._dungeon_cache = {}            # file name -> (mtime, data)
        self._binding_now = False
        self._toast = {"t": "", "n": 0}
        self._rift_map = False              # the rift spots' map window
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
        if isinstance(data.get("show_heal"), bool):
            self._show_heal = data["show_heal"]
        if isinstance(data.get("parse_journal"), bool):
            self._parse_journal = data["parse_journal"]
        # settings saved before revision 1 had the boss pull reset off by
        # default: turned on once for everyone, then the player's choice
        if isinstance(data.get("auto_reset_boss"), bool) and                 int(data.get("settings_rev") or 0) >= SETTINGS_REV:
            self._auto_reset_boss = data["auto_reset_boss"]
        if isinstance(data.get("auto_reset_dummy"), bool):
            self._auto_reset_dummy = data["auto_reset_dummy"]
        if isinstance(data.get("hide_unavailable"), bool):
            self._hide_na = data["hide_unavailable"]
        if isinstance(data.get("bar_values"), bool):
            self._bar_values = data["bar_values"]
        # (the values were shown on or off before the choice of text)
        old = "max" if self._bar_values else "none"
        for key, attr in (("foe_text", "_foe_text"), ("boss_text", "_boss_text")):
            v = data.get(key, old)
            setattr(self, attr, v if v in self.BAR_TEXTS else "none")
        for key, attr in (("boss_color", "_boss_color"), ("boss_lag", "_boss_lag"),
                          ("boss_bg", "_boss_bg")):
            if self._hex_ok(data.get(key)):
                setattr(self, attr, data[key])
        if isinstance(data.get("boss_extra"), int) and 0 <= data["boss_extra"] <= 16:
            self._boss_extra = data["boss_extra"]
        if isinstance(data.get("boss_level_show"), bool):
            self._boss_level_show = data["boss_level_show"]
        if isinstance(data.get("boss_level_scale"), int) and 50 <= data["boss_level_scale"] <= 250:
            self._boss_level_scale = data["boss_level_scale"]
        if data.get("ally_text") in self.BAR_TEXTS:
            self._ally_text = data["ally_text"]
        for key in ("ally_color", "ally_color_end", "ally_lag", "ally_bg", "boss_color_end",
                    "me_color", "me_color_end", "me_lag", "me_bg"):
            if self._hex_ok(data.get(key)):
                setattr(self, "_" + key, data[key])
        be = data.get("bar_colors_end") if isinstance(data.get("bar_colors_end"), dict) else {}
        for k in self._bar_colors_end:
            if self._hex_ok(be.get(k)):
                self._bar_colors_end[k] = be[k]
        for key in ("ally_level_show",):
            if isinstance(data.get(key), bool):
                setattr(self, "_" + key, data[key])
        cc = data.get("class_colors") if isinstance(data.get("class_colors"), dict) else {}
        for k in self._class_colors:
            if self._hex_ok(cc.get(k)):
                self._class_colors[k] = cc[k]
        # (the icon in the level's square, a choice of before, now beside it)
        ac = "beside" if data.get("ally_class") == "replace" else data.get("ally_class")
        if ac in self.ALLY_CLASS:
            self._ally_class = ac
        for key, lo, hi in (("ally_extra", 0, 16), ("ally_width", -60, 200),
                            ("ally_level_scale", 20, 250), ("ally_level_dx", -120, 120),
                            ("ally_level_dy", -120, 120), ("ally_class_scale", 20, 300),
                            ("ally_class_dx", -300, 300), ("ally_class_dy", -300, 300),
                            ("boss_level_dx", -80, 80), ("boss_level_dy", -80, 80),
                            ("foe_width", -60, 200), ("foe_text_scale", 50, 250),
                            ("boss_text_scale", 50, 250)):
            if isinstance(data.get(key), int) and lo <= data[key] <= hi:
                setattr(self, "_" + key, data[key])
        qc = data.get("quick_custom") if isinstance(data.get("quick_custom"), dict) else {}
        for name, vals in qc.items():
            ok = self._quick_clean(vals)
            if ok is not None and isinstance(name, str) and name and name not in self.QUICK_PRESETS:
                self._quick_custom[name[:40]] = ok
        if isinstance(data.get("me_bar"), bool):
            self._me_bar = data["me_bar"]
        for key, lo, hi in (("me_w", 0, 900), ("me_h", 0, 60), ("me_dx", -600, 600),
                            ("me_dy", -400, 400), ("fx_dx", -400, 400), ("fx_dy", -400, 400)):
            if isinstance(data.get(key), int) and lo <= data[key] <= hi:
                setattr(self, "_" + key, data[key])
        if data.get("me_text") in self.BAR_TEXTS:
            self._me_text = data["me_text"]
        if isinstance(data.get("skillbar_min"), bool):
            self._skillbar_min = data["skillbar_min"]
        if isinstance(data.get("skillbar_alpha"), int) and 0 <= data["skillbar_alpha"] <= 100:
            self._skillbar_alpha = data["skillbar_alpha"]
        if isinstance(data.get("skillbar_radius"), int) and 0 <= data["skillbar_radius"] <= 30:
            self._skillbar_radius = data["skillbar_radius"]
        if isinstance(data.get("esc_button"), bool):
            self._esc_button = data["esc_button"]
        if isinstance(data.get("target_scale"), int) and 50 <= data["target_scale"] <= 300:
            self._target_scale = data["target_scale"]
        if isinstance(data.get("frame_extra"), int) and 0 <= data["frame_extra"] <= 16:
            self._frame_extra = data["frame_extra"]
        bc = data.get("bar_colors") if isinstance(data.get("bar_colors"), dict) else {}
        if isinstance(data.get("level_scale"), int) and 50 <= data["level_scale"] <= 250:
            self._level_scale = data["level_scale"]
        if isinstance(data.get("level_show"), bool):
            self._level_show = data["level_show"]
        for key, attr, lo, hi in (("level_dx", "_level_dx", -60, 60), ("level_dy", "_level_dy", -60, 60),
                                  ("glow_blur", "_glow_blur", 1, 16), ("line_blur", "_line_blur", 0, 16)):
            if isinstance(data.get(key), int) and lo <= data[key] <= hi:
                setattr(self, attr, data[key])
        for key, attr in (("level_text", "_level_text"), ("level_tint", "_level_tint")):
            if self._hex_ok(data.get(key)):
                setattr(self, attr, data[key])
        lc = data.get("lag_colors") if isinstance(data.get("lag_colors"), dict) else {}
        for k in self._bar_colors:
            if self._hex_ok(bc.get(k)):
                self._bar_colors[k] = bc[k]
            if self._hex_ok(lc.get(k)):
                self._lag_colors[k] = lc[k]
        for key, attr in (("target_glow", "_target_glow"), ("target_line", "_target_line"),
                          ("bar_bg", "_bar_bg")):
            if self._hex_ok(data.get(key)):
                setattr(self, attr, data[key])
        if isinstance(data.get("target_line_w"), int) and 1 <= data["target_line_w"] <= 6:
            self._target_line_w = data["target_line_w"]
        mh = data.get("meter_hide") if isinstance(data.get("meter_hide"), dict) else {}
        for oid in self._meter_hide:
            if isinstance(mh.get(oid), list):
                self._meter_hide[oid] = {x for x in mh[oid] if x in ("title", "time")}
        mm = data.get("minimap") if isinstance(data.get("minimap"), dict) else {}
        if mm.get("shape") in ("round", "square"):
            self._mm_shape = mm["shape"]
        if isinstance(mm.get("rotate"), bool):
            self._mm_rotate = mm["rotate"]
        if isinstance(mm.get("zoom"), int) and 0 <= mm["zoom"] < len(MINIMAP_ZOOMS):
            self._mm_zoom = mm["zoom"]
        if isinstance(mm.get("size"), int) and MINIMAP_SIZES[0] <= mm["size"] <= MINIMAP_SIZES[1]:
            self._mm_size = mm["size"]
        if isinstance(mm.get("layers"), list):
            self._mm_layers = {str(x) for x in mm["layers"] if x in MINIMAP_LAYERS}
        if isinstance(mm.get("hideFound"), bool):
            self._mm_hide_found = mm["hideFound"]
        if isinstance(mm.get("questOnly"), bool):
            self._mm_quest_only = mm["questOnly"]
        if isinstance(data.get("luck_merge"), bool):
            self._luck_merge = data["luck_merge"]
        if isinstance(data.get("rift_keep"), int) and data["rift_keep"] >= 0:
            self._rift_keep = data["rift_keep"]
        bind = data.get("reset_bind")
        if isinstance(bind, dict) and isinstance(bind.get("vk"), int):
            vk = bind["vk"]
            if 0 < vk <= 0xFF and vk not in VK_UNBINDABLE:
                RESET_BIND.update(
                    {"vk": vk} | {m: bool(bind.get(m))
                                  for m in ("shift", "ctrl", "alt")})
        if data.get("theme") in themes.THEMES:
            self._theme = data["theme"]
        if data.get("lang") in i18n.LANGS:
            self._lang = data["lang"]
        i18n.set_lang(self._lang)
        if isinstance(data.get("view3d"), bool):
            self._view3d = data["view3d"]
        z = data.get("zoom")
        if isinstance(z, int) and 50 <= z <= 200:
            self._zoom = z
        pos = data.get("overlay_pos")
        if isinstance(pos, dict):
            for k, v in pos.items():
                if isinstance(v, dict) and v.get("ax") in ("l", "r") \
                        and v.get("ay") in ("t", "b"):
                    self._ov_pos[k] = {"ax": v["ax"], "ay": v["ay"],
                                       "dx": int(v.get("dx") or 0),
                                       "dy": int(v.get("dy") or 0)}
        qk = data.get("quest_kinds") if isinstance(data.get("quest_kinds"), dict) else {}
        for k in self._quest_kinds:
            if isinstance(qk.get(k), bool):
                self._quest_kinds[k] = qk[k]
        if isinstance(data.get("quest_max"), int) and 3 <= data["quest_max"] <= 40:
            self._quest_max = data["quest_max"]
        if isinstance(data.get("quest_pins"), list):
            self._quest_pins = {str(x) for x in data["quest_pins"]}
        on = data.get("overlay_on")
        if isinstance(on, dict):
            for k in self._ov_on:
                if isinstance(on.get(k), bool):
                    self._ov_on[k] = on[k]
        if data.get("overlay_tab") in ("dmg", "heal"):
            self._ov_tab = data["overlay_tab"]
        if isinstance(data.get("overlay_split"), bool):
            self._ov_split = data["overlay_split"]
        if isinstance(data.get("luck_hidden"), list):
            self._luck_hidden = {str(x) for x in data["luck_hidden"]}
        if isinstance(data.get("cur_hidden"), list):
            self._cur_hidden = {str(x) for x in data["cur_hidden"]}
        if isinstance(data.get("cur_per_row"), int) and 1 <= data["cur_per_row"] <= 6:
            self._cur_per_row = data["cur_per_row"]
        if isinstance(data.get("overlay_lock_always"), bool):
            self._ov_lock_always = data["overlay_lock_always"]
        if isinstance(data.get("overlay_hide_free"), bool):
            self._ov_hide_free = data["overlay_hide_free"]
        if data.get("overlay_theme") in OVERLAY_THEMES:
            self._ov_theme = data["overlay_theme"]
        vids = data.get("dungeon_videos")
        if isinstance(vids, dict):
            self._dungeon_videos = {
                k: v for k, v in vids.items() if isinstance(k, str)
                and _youtube_id(f"https://youtu.be/{v}") == v}
        style = data.get("overlay_style")
        if isinstance(style, dict):
            for k, v in style.items():
                if k in self._ov_on and isinstance(v, dict):
                    self._ov_style[k] = _ov_style(v, k)

    def _save_settings(self):
        try:
            SETTINGS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_CACHE.write_text(json.dumps({
                "mode": self.mode,
                "show_heal": bool(self._show_heal),
                "parse_journal": bool(self._parse_journal),
                "auto_reset_boss": bool(self._auto_reset_boss),
                "auto_reset_dummy": bool(self._auto_reset_dummy),
                "hide_unavailable": bool(self._hide_na),
                "bar_values": self._foe_text != "none",
                "foe_text": self._foe_text, "boss_text": self._boss_text,
                "boss_color": self._boss_color, "boss_lag": self._boss_lag, "boss_bg": self._boss_bg,
                "boss_extra": int(self._boss_extra), "boss_level_show": bool(self._boss_level_show),
                "boss_level_scale": int(self._boss_level_scale),
                "boss_level_dx": int(self._boss_level_dx), "boss_level_dy": int(self._boss_level_dy),
                "foe_width": int(self._foe_width),
                **{k: getattr(self, "_" + k) for k in (
                    "foe_text_scale", "boss_text_scale", "ally_extra", "ally_width",
                    "ally_level_show", "ally_level_scale", "ally_level_dx", "ally_text",
                    "ally_color", "ally_color_end", "ally_lag", "ally_bg", "boss_color_end",
                    "me_color", "me_color_end", "me_lag", "me_bg",
                    "ally_level_dy", "ally_class", "ally_class_scale", "ally_class_dx",
                    "class_colors",
                    "ally_class_dy")},
                "esc_button": bool(self._esc_button),
                "skillbar_min": bool(self._skillbar_min), "skillbar_alpha": int(self._skillbar_alpha),
                "skillbar_radius": int(self._skillbar_radius),
                "me_bar": bool(self._me_bar), "me_w": int(self._me_w), "me_h": int(self._me_h),
                "me_dx": int(self._me_dx), "me_dy": int(self._me_dy),
                "fx_dx": int(self._fx_dx), "fx_dy": int(self._fx_dy), "me_text": self._me_text,
                "quick_custom": {n: {a[1:]: v for a, v in vals.items()}
                                 for n, vals in self._quick_custom.items()},
                "target_scale": int(self._target_scale),
                "frame_extra": int(self._frame_extra),
                "bar_colors": dict(self._bar_colors),
                "lag_colors": dict(self._lag_colors),
                "bar_bg": self._bar_bg,
                "level_scale": int(self._level_scale),
                "level_show": bool(self._level_show),
                "level_text": self._level_text, "level_tint": self._level_tint,
                "level_dx": int(self._level_dx), "level_dy": int(self._level_dy),
                "glow_blur": int(self._glow_blur), "line_blur": int(self._line_blur),
                "target_glow": self._target_glow,
                "target_line": self._target_line,
                "target_line_w": int(self._target_line_w),
                "meter_hide": {k: sorted(v) for k, v in self._meter_hide.items()},
                "minimap": {"shape": self._mm_shape, "rotate": bool(self._mm_rotate),
                            "zoom": int(self._mm_zoom), "size": int(self._mm_size),
                            "layers": sorted(self._mm_layers),
                            "hideFound": bool(self._mm_hide_found),
                            "questOnly": bool(self._mm_quest_only)},
                "luck_merge": bool(self._luck_merge),
                "settings_rev": SETTINGS_REV,
                "rift_keep": int(self._rift_keep),
                "reset_bind": dict(RESET_BIND),
                "zoom": int(self._zoom),
                "view3d": bool(self._view3d),
                "theme": self._theme,
                "lang": self._lang,
                "overlay_pos": self._ov_pos,
                "overlay_on": self._ov_on,
                "overlay_tab": self._ov_tab,
                "overlay_split": bool(self._ov_split),
                "luck_hidden": sorted(self._luck_hidden),
                "cur_hidden": sorted(self._cur_hidden),
                "cur_per_row": int(self._cur_per_row),
                "quest_kinds": dict(self._quest_kinds), "quest_max": int(self._quest_max),
                "quest_pins": sorted(self._quest_pins),
                "overlay_lock_always": bool(self._ov_lock_always),
                "overlay_hide_free": bool(self._ov_hide_free),
                "overlay_style": self._ov_style,
                "overlay_theme": self._ov_theme,
                "dungeon_videos": self._dungeon_videos,
            }, indent=2))
        except OSError as e:
            print(f"[meter] couldn't save settings: {e}", file=sys.stderr)

    def _load_window_geom(self):
        """Where the window was left, in physical pixels, or {}."""
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

    # ------------------------------------------------------------ the events
    # What happened, newest first: the events window (title band button).
    def _event(self, text, tone="", btn=None):
        self._events.appendleft({"at": time.time(), "t": text, "tone": tone,
                                 "btn": btn})
        self.menubridge.invalidate()

    def _show_kill_toast(self, text, best):
        self._event(" ".join(str(text).split()).capitalize(),
                    "best" if best else "")

    def _show_reset_toast(self):
        self._event(tr("Combat réinitialisé."))

    def _set_parse_banner(self, text):
        """The parse's state, shown on the live page's cards."""
        self._parse_text = text

    def _hide_parse_banner(self):
        self._parse_text = ""


    def _open_report_card(self):
        """Show _report_data — as a page of the window now, not a card over
        the game."""
        self._rift_view = self._report_data
        self._run_tab = "report"
        self._journal_f.update(who="", limit=JOURNAL_PAGE)
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
                self._event(tr("Connecté à Farever."), "ok")
        self.menubridge.invalidate()

    def show_rift_report(self, report):
        """A rift's boss died. Called from the hook thread."""
        def done():
            self._report_data = report
            saved = self._save_rift_report(report)
            # its gates closed: the game's running count, read again now
            # that it is over, against the one read when it began
            if saved and isinstance(self._rift_gates0, (int, float)):
                self._rift_gates_for = saved
                self._me_auto_next = 0.0
            else:
                self._rift_gates0 = None
            best = (report.get("phases") or [{}])[-1].get("players") or []
            unseen = report.get("unconfirmed")
            if best:
                text = (tr("Faille terminée — MVP {name} (victoire non vue : "
                           "rapport à la fin de la faille).",
                           name=best[0]['name']) if unseen else
                        tr("Faille terminée — MVP {name}.",
                           name=best[0]['name']))
            else:
                text = (tr("Faille terminée (victoire non vue : rapport à la "
                           "fin de la faille).") if unseen else
                        tr("Faille terminée."))
            self._event(text, "rift",
                        {"id": "open_last_rift", "t": tr("Voir le rapport")})
        self._enqueue(done)()

    def on_rift_dropped(self, why):
        """A rift recording given up: said, so a missing report is never
        silent. Called from the hook thread."""
        self._enqueue(lambda: self._event(
            tr("Faille non enregistrée : {why}.", why=why), "warn"))()

    def on_boss_giveup(self):
        self._enqueue(lambda: self._event(
            tr("Combat abandonné — compteur vidé."), ""))()

    def open_settings_from_tray(self):
        """The tray's "Afficher Farever Book": bring the window to the front."""
        self.menubridge.send({"t": "show"})

    def _tick_rift(self):
        """Follow rift crossings: the journal's line, the gates count read
        anew on entering (the group mode counts the whole rift, _apply_mode)."""
        in_rift = self.ui_state.in_rift()
        if in_rift == self._rift_seen:
            return
        self._rift_seen = in_rift
        self._event(tr("Entrée dans une faille.") if in_rift
                    else tr("Sortie de la faille."), "rift")
        if in_rift:
            # the game's count of gates closed, read now: the rift's own
            # gates are what it has grown by when the boss dies
            self._rift_gates0 = "wait"
            self._rift_gates_for = None
            self._me_auto_next = 0.0

    # ------------------------------------------------------------------ actions
    def _toggle_heal(self):
        self._show_heal = not self._show_heal
        self._save_settings()

    def _toggle_parse_journal(self):
        """A parse journals its hits, heals and hits taken, or not (from
        the next parse on)."""
        self._parse_journal = not self._parse_journal
        self._save_settings()

    def _focus(self, name):
        self.focus_player = name or None

    def _set_view3d(self, on):
        """Previews in 3D or as pictures, everywhere (a model costs memory
        and work the picture does not)."""
        self._view3d = bool(on)
        self._save_settings()

    def _set_zoom(self, pct):
        self._zoom = max(50, min(200, int(pct)))
        self._save_settings()


    def _open_rift_file(self, name):
        data = self._read_rift_file(name)
        if data is None:
            self._toast_msg(tr("Ce rapport de faille est illisible."))
            return
        data["file"] = Path(str(name)).name
        self._rift_view = data
        self._run_tab = "report"
        self._journal_f.update(who="", limit=JOURNAL_PAGE)

    def _shown_report(self):
        if self._menu_tab == "Dungeons" and self._dungeon_view is not None:
            d = self._dungeon_view
            return dict(d, title=d.get("name") or tr("Donjon"),
                        sub=self._dungeon_sub(d))
        return self._rift_view

    def _copy_rift_image(self):
        data = self._shown_report()
        if not data:
            return
        try:
            copy_image_to_clipboard(render_rift_report_image(data))
            self._toast_msg(tr("Image copiée dans le presse-papiers."))
        except Exception as e:
            print(f"[meter] image copy failed ({e}) — copying text instead.",
                  file=sys.stderr)
            if copy_text_to_clipboard(self._report_text(data)):
                self._toast_msg(tr("Copié en texte."))

    # ---- problem report (Aide › Un problème ?) ----------------------------
    def _report_ctx(self):
        """What the app knows that helps understand an issue."""
        ctx = {"lang": self._lang, "theme": self._theme,
               "game_dir": gamedata._game_dir(), "state": "—"}
        if self.link is not None:
            state, detail, _pid = self.link.status()
            ctx.update(state=state, detail=detail,
                       error=self.link.last_error,
                       steps=self.link.steps_view())
        return ctx

    def _make_report(self):
        """The report on the Desktop, then the Explorer on it. Built off the
        loop: the antivirus query takes a second."""
        ctx, names = self._report_ctx(), self._profile_names()
        self._toast_msg(tr("Création du rapport…"))

        def work():
            try:
                path = report.write(ctx, names)
                report.show_in_folder(path)
                msg = tr("Rapport créé : {name}", name=path.name)
            except Exception as e:
                print(f"[meter] report failed: {e!r}", file=sys.stderr)
                msg = tr("Le rapport n'a pas pu être créé : {e}", e=e)
            self._enqueue(lambda: self._toast_msg(msg))()
        threading.Thread(target=work, daemon=True, name="report").start()

    def _copy_error(self):
        """The connection error, to paste in a message."""
        ctx = self._report_ctx()
        text = (f"Farever Book {VERSION}, {report.windows_version()}\n"
                f"{ctx.get('detail') or ''}\n{ctx.get('error') or ''}").strip()
        if copy_text_to_clipboard(report.mask(text, self._profile_names())):
            self._toast_msg(tr("Erreur copiée dans le presse-papiers."))

    def _open_log_folder(self):
        try:
            DATA_HOME.mkdir(parents=True, exist_ok=True)
            os.startfile(DATA_HOME)
        except Exception as e:
            print(f"[meter] couldn't open {DATA_HOME}: {e}", file=sys.stderr)

    # ---- the first launch: the welcome screen ----------------------------
    def _setup_begin(self):
        """At startup: the data is there (consent goes without saying), or
        the welcome screen asks — with the game's folder found, or asked."""
        if not needs_first_data():
            DATA_CONSENT.set()
            self._setup = None
            return
        hb = locate_hlboot(None)
        self._setup = ({"stage": "ask", "path": str(hb.parent)} if hb
                       else {"stage": "locate"})

    def _setup_folder(self, path):
        """The folder the player picked: Farever's, or said so."""
        if not self._setup or self._setup.get("stage") == "run":
            return
        hb = game_folder_hlboot(path)
        if hb is None:
            self._setup = dict(self._setup, err=tr(
                "Farever n'est pas dans ce dossier : choisissez celui qui "
                "contient Farever.exe et hlboot.dat (souvent "
                "Steam\\steamapps\\common\\Farever)."))
        else:
            self._setup = {"stage": "ask", "path": str(hb.parent),
                           "mode": self._setup.get("mode")}
            if self._setup["mode"] == "repair":
                self._setup_start()

    def _setup_start(self):
        """The player agreed: read the game's data, step by step."""
        if not self._setup or self._setup.get("stage") not in ("ask",
                                                                  "error"):
            return
        DATA_CONSENT.set()
        path = self._setup.get("path")
        mode = self._setup.get("mode")
        hb = Path(path) / "hlboot.dat"
        written = set()
        running = threading.Event()
        running.set()

        def lines():
            """Each group: done, running (the first not done) or waiting,
            with the pictures counted in its folders so far."""
            out, pct, current = [], 0.0, True
            total_w = sum(g[3] for g in GENERATED_GROUPS)
            for label, outs, folders, weight in GENERATED_GROUPS:
                done = all(o in written for o in outs)
                n = sum(generated_pictures(f) for f in folders)
                if done:
                    state = "done"
                    pct += weight
                elif current:
                    state, current = "run", False
                    cost = {f: GENERATED_PICTURE_COST.get(f, 1)
                            for f in folders}
                    want = sum(GENERATED_PICTURES.get(f, 0) * cost[f]
                               for f in folders)
                    got = sum(generated_pictures(f) * cost[f]
                              for f in folders)
                    part = (min(got / want, .95) if want else
                            sum(o in written for o in outs) / len(outs))
                    pct += weight * part
                else:
                    state = "wait"
                out.append({"t": tr(label), "s": state,
                            "n": n if folders and state != "wait" else None})
            return out, min(99, int(100 * pct / total_w))

        def refresh():
            st = self._setup
            if st and st.get("stage") == "run":
                rows, pct = lines()
                self._setup = dict(st, rows=rows, pct=pct)
                self.menubridge.invalidate()

        def progress(tool, line):
            m = re.match(r"\[written\]\s+(.+?)(?:\s+\(|$)", line.strip())
            if m:
                written.add(re.split(r"[\\/]", m.group(1))[-1])
                refresh()

        def poll():
            # the pictures arrive between two outputs: counted twice a second
            while running.is_set():
                refresh()
                time.sleep(0.4)

        def work():
            ok = False
            try:
                ok = regenerate_data(hb, force=True, on_progress=progress)
            except Exception as e:
                print(f"[meter] game data read failed: {e!r}",
                      file=sys.stderr)
            running.clear()
            # a repair also rebuilds the hook from the new data
            if ok and mode == "repair" and self.link is not None:
                self.link.reconnect()
            self._setup = ({"stage": "done", "mode": mode} if ok else
                           {"stage": "error", "path": path, "mode": mode})
            self.menubridge.invalidate()
        rows, _pct = lines()
        self._setup = {"stage": "run", "path": path, "rows": rows, "pct": 0,
                       "mode": mode}
        threading.Thread(target=work, daemon=True, name="first-data").start()
        threading.Thread(target=poll, daemon=True, name="first-data-count").start()

    def _setup_finish(self):
        """Into the app: after the first launch, its first tab; after a
        repair (done or given up), back to the Help tab."""
        st = self._setup or {}
        if st.get("mode") == "repair" and st.get("stage") != "run":
            self._setup = None
            self._menu_tab = "Help"
        elif st.get("stage") == "done":
            self._setup = None
            self._menu_tab = APP_TAB_DEFAULT

    def _setup_spec(self):
        """The welcome screen, in place of the tabs."""
        st = dict(self._setup)
        st["needs"] = [tr("les images de la collection, du bestiaire et de "
                          "la carte"),
                       tr("les icônes des sorts et des objets"),
                       tr("les modèles 3D des personnages et des montures"),
                       tr("les données des builds, des donjons et des boss"),
                       tr("les textes du jeu en anglais et en français")]
        # the languages, to pick one before anything else
        return {"k": "welcome", "id": "welcome", "lang": self._lang,
                "langs": [{"id": k, "t": t} for k, t in i18n.LANGS.items()],
                **st}

    def _repair(self):
        """Réparer (Aide): what a game patch needs, by hand — the game's data
        read again from scratch, on the first launch's progress screen, then
        the hook rebuilt from it (the game link reconnects). No restart."""
        if self._setup:
            return
        hb = locate_hlboot(self.link.status()[2] if self.link is not None
                           else None)
        if hb is None:
            # nowhere to read from: the screen asks for the game's folder
            self._setup = {"stage": "locate", "mode": "repair"}
            return
        self._setup = {"stage": "ask", "mode": "repair",
                       "path": str(Path(hb).parent)}
        self._setup_start()

    def _set_tab(self, name):
        """A tab clicked, the one shown or another: it opens on its own
        first page (the list of dungeons, not one dungeon's), whatever was
        open in it."""
        if name not in APP_TABS:
            return
        self._help_open = None
        if name == "Build":
            self.buildtab.close()       # the list of builds
        elif name == "Dungeons":
            self._dungeon_kind = None
            self._dungeon_view = None
            self._dungeon_tab = "info"
        elif name == "Rifts":
            self._rift_view = None
            self._rift_sel = set()
            self._rift_confirm = False
        elif name == "Hunt":
            self._hunt_sel = None
        elif name == "Character":
            self._char_sel = None
        elif name == "Encyclopedia":
            self._encyclo_sel = None
            self._encyclo_reset += 1    # the page back on its first category
        self._menu_tab = name

    def _menu_actions(self):
        acts = {
            "set_tab": lambda p: self._set_tab(p.get("value")),
            "link_retry": self._link_clicked,
            "launch_game": self._launch_game,
            "boot": self.menubridge.invalidate,
            # live
            "toggle_mode": self._toggle_mode,
            "reset_data": self._manual_reset,
            "toggle_parse": self._toggle_parse,
            "toggle_parse_journal": self._toggle_parse_journal,
            "focus_player": lambda p: self._focus(p.get("name")),
            "open_last_rift": self._open_report_card,
            "clear_events": self._events.clear,
            # rifts
            "open_rift": lambda p: self._open_rift_file(p.get("file", "")),
            "close_rift": lambda: setattr(self, "_rift_view", None),
            "copy_rift_image": self._copy_rift_image,
            "open_parses": self._open_parses,
            "rift_tick": lambda p: self._rift_tick(p.get("file")),
            "rift_tick_all": self._rift_tick_all,
            "rift_delete": self._rift_delete,
            "rift_delete_cancel": self._rift_delete_cancel,
            "set_rift_keep": lambda p: self._set_rift_keep(p.get("value")),
            # dungeons
            # the Encyclopedia: the item shown, its icons (sent once asked)
            "encyclo_open": lambda p: self._encyclo_open(
                str(p.get("id") or ""), bool(p.get("go"))),
            "encyclo_icons": self._send_item_icons,
            "npc_portrait": lambda p: self._save_npc_portrait(
                p.get("key"), p.get("data")),
            "open_dungeon_kind": lambda p: self._open_dungeon_kind(
                p.get("kind")),
            "dungeon_tab": lambda p: setattr(
                self, "_dungeon_tab", p.get("id") if p.get("id") in
                ("info", "runs", "help") else "info"),
            "dungeon_video": lambda p: self._set_dungeon_video(
                p.get("kind"), p.get("url")),
            "open_dungeon_video": lambda p: self._open_dungeon_video(
                p.get("kind")),
            "close_dungeon_kind": lambda: setattr(self, "_dungeon_kind", None),
            "open_dungeon_run": lambda p: self._open_dungeon_run(
                p.get("file", "")),
            "close_dungeon_run": lambda: setattr(self, "_dungeon_view", None),
            "dungeon_run_tab": lambda p: setattr(
                self, "_run_tab", "journal" if p.get("id") == "journal"
                else "report"),
            "journal_filter": self._journal_filter,
            # collection
            "coll_model": lambda p: self._coll_model(p.get("id")),
            # hunting log
            "hunt_open": lambda p: setattr(self, "_hunt_sel", p.get("id")),
            "hunt_close": lambda: setattr(self, "_hunt_sel", None),
            # character
            "char_analyze": lambda p: self._analyze(p.get("name")),
            "char_open": lambda p: setattr(self, "_char_sel", p.get("name")),
            "char_forget": lambda p: self._forget_profile(p.get("name")),
            "char_to_build": lambda p: self._profile_to_build(p.get("name")),
            "build_from_me": self._build_from_me,
            # overlays
            "ov_tab": lambda p: self._ov_set_tab(p.get("tab")),
            "ov_moved": self._ov_moved,
            "ov_reset": self._ov_reset,
            "ov_lock": self._ov_lock_toggle,
            "ov_hide_free": self._ov_hide_toggle,
            "ov_style": self._ov_set_style,
            "ov_style_set": self._ov_style_set,
            "ov_show": self._ov_show,
            "ov_theme": self._ov_set_theme,
            # the meter: one overlay with two tabs, or one for damage and
            # one for heals (value: True, False, or None to switch)
            "ov_split": lambda p: self._ov_set_split(p.get("value")),
            # the loot luck overlay: one of its lines shown or hidden
            "luck_row": self._luck_row,
            # the currencies overlay: one shown or hidden, how many a line
            "cur_show": self._cur_show,
            "cur_per_row": self._cur_set_per_row,
            "quest_set": self._quest_set,
            # the minimap: its zoom (d: a step in or out), its settings
            "mm_zoom": self._mm_set_zoom,
            # a meter's title or clock shown or not
            "meter_part": self._meter_part,
            "mm_set": self._mm_set,
            "esc_open": self._esc_open,
            "goal_catalog": lambda p: G.catalog(p.get("cat"), p.get("sub"),
                                                self._learnt()),
            "goal_add": self._goal_add,
            "goal_del": lambda p: self.goals.remove(p.get("id")),
            # settings
            "toggle_heal": self._toggle_heal,
            "toggle_auto_reset": self._toggle_auto_reset_boss,
            "toggle_auto_reset_dummy": self._toggle_auto_reset_dummy,
            "hide_unavailable": self._set_hide_na,
            "bar_values": self._set_bar_values,
            "esc_button": self._set_esc_button,
            "target_option": self._set_target_option,
            "quick_preset": self._set_quick_preset,
            "quick_export": self._quick_export,
            "quick_import": self._quick_import,
            "quick_delete": self._quick_delete,
            # the bonus dungeon in the loot luck overlay, or in its own
            # (value: True, False, or None to switch)
            "luck_merge": lambda p: self._set_luck_merge(p.get("value")),
            "begin_bind": self._begin_bind_capture,
            "set_zoom": lambda p: self._set_zoom(p.get("value", 130)),
            "set_view3d": lambda p: self._set_view3d(p.get("on")),
            "coll_open": lambda p: setattr(
                self, "_coll_sel", (str(p.get("cat") or ""),
                                    str(p.get("id") or ""))),
            "set_theme": lambda p: self._set_theme(p.get("id")),
            "set_lang": lambda p: self._set_lang(p.get("id")),
            "open_log": self._open_log_folder,
            # updates
            "update_check": lambda: self.updater.check(manual=True),
            "update_install": lambda: self.updater.install(self.request_quit),
            "update_later": self.updater.later,
            "update_close": self.updater.close,
            "update_offer": self.updater.offer,
            "rift_map": lambda: setattr(self, "_rift_map", True),
            "rift_map_close": lambda: setattr(self, "_rift_map", False),
            "update_page": self._open_releases,
            "open_licences": self._open_licences,
            "open_link": lambda p: self._open_link(p.get("id")),
            "open_account": self._open_account,
            "acct_sync": lambda p: self._account_sync(p.get("sid")),
            "acct_hero": lambda p: self._account_count(
                p.get("sid"), p.get("key"), bool(p.get("value"))),
            "acct_forget": lambda p: self._account_forget(p.get("key")),
            "settings_topic": lambda p: setattr(
                self, "_settings_topic",
                p.get("id") if p.get("id") in SETTINGS_TOPICS
                else self._settings_topic),
            # help
            "help_open": lambda p: setattr(self, "_help_open", p.get("id")),
            "help_close": lambda: setattr(self, "_help_open", None),
            "repair_data": self._repair,
            "make_report": self._make_report,
            "copy_error": self._copy_error,
            # the first launch's welcome screen
            "setup_folder": lambda p: self._setup_folder(p.get("path")),
            "setup_start": self._setup_start,
            "setup_finish": self._setup_finish,
        }
        acts.update(self.buildtab.actions())
        return acts


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
        try:
            ensure_ui_frame()               # the overlays' frame, the game's
        except Exception as e:
            print(f"[meter] overlay frame: {e!r}", file=sys.stderr)
        self.menubridge.start(self._win_geom, self._theme, self._lang)
        if self.menubridge.proc is None:
            message_box(tr("La fenêtre de Farever Book n'a pas pu s'ouvrir "
                           "(WebView2 ou pywebview manquant ?).\n\nLe détail "
                           "est dans :\n{log}", log=LOG_FILE),
                        tr("Farever Book — erreur"), 0x10)
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
                try:
                    self.menubridge.push_overlay(self._overlay_spec())
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
        self.me.flush(force=True)

    def _refresh(self):
        self._auto_self_profile()
        self.updater.check()                # at launch, then hourly
        self.me.flush()
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
        active = any(self.session.combat_of(p.name) for p in rows)
        self.session.set_active(active, time.time())
        duration, in_combat = self.session.current()
        rows, duration, holding = self._hold_last(rows, duration)
        self._live = (rows, duration, holding, in_combat)

    # ------------------------------------------------------------------ spec
    def _spec(self):
        return {
            "version": VERSION,
            "dataGen": DATA_GENERATION[0],
            # a new version's offer (not over the welcome screen)
            "update": None if self._setup else self.updater.state,
            # a newer version: the header's button, until it is installed
            "updateAvail": None if self._setup else self.updater.available(),
            # the header's "Soutenir" button, under Réglages and Aide
            "support": bool(TIPEEE_URL) and not self._setup,
            "zoom": int(self._zoom),
            # previews: the 3D model, or the picture (lighter)
            "view3d": bool(self._view3d),
            "theme": self._theme,
            "lang": self._lang,
            "shard": self.ui_state.server() or "",
            "link": self._link_spec(),
            "account": self._account_view(),
            "linksteps": (self.link.steps_view() if self.link is not None
                          else []),
            "rift": self._rift_clock(),
            "riftmap": self._rift_map_spec() if self._rift_map else None,
            "toast": self._toast,
            # the first launch: the welcome screen alone, no tabs
            "tab": "Welcome" if self._setup else self._menu_tab,
            # The app's own tabs, after the game's, behind a divider.
            "tabs": [] if self._setup else self._tabs_spec(),
            "page": ([self._setup_spec()] if self._setup
                     else self._page(self._menu_tab)),
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
                   "Encyclopedia": self._page_encyclopedia,
                   "Hunt": self._page_hunt,
                   "Achievements": self._page_achievements,
                   "Map": self._page_map,
                   "Character": self._page_character,
                   "Settings": self._page_settings,
                   "Build": self._page_build,
                   "Help": self._page_help}.get(tab)
        try:
            return builder() if builder else []
        except Exception as e:
            import traceback
            traceback.print_exc()
            return [{"k": "section", "t": tr(APP_TAB_LABELS.get(tab, tab))},
                    {"k": "note", "warn": True,
                     "t": tr("Cette page n'a pas pu être construite : {e}. "
                             "Le détail est dans le journal.", e=e)}]

    # ---- live
    def _ranking(self, rows, duration, heal, focus=None,
                 limit=MAX_PLAYER_ROWS * 3):
        """One ranking as the rift reports draw it (report.js rankTable):
        damage or heals, best first, a bar against the best."""
        val = (lambda p: p.heal_total) if heal else (lambda p: p.total)
        got = sorted((p for p in rows if val(p) > 0), key=lambda p: -val(p))
        total = sum(val(p) for p in got) or 1.0
        top = val(got[0]) if got else 1.0
        return [{"rank": i, "name": p.name, "me": bool(p.is_me),
                 "cls": _class_tag(self.world.class_of(p.name)),
                 "ck": class_key(self.world.class_of(p.name)),
                 "rate": _n(val(p) / duration) if duration > 0 else "—",
                 "total": _n(val(p)),
                 "pct": f"{val(p) / total * 100:.0f}%",
                 "f": round(val(p) / top, 4),
                 "on": p.name == focus}
                for i, p in enumerate(got[:limit], 1)]

    def _player_tip(self, p, duration, heal):
        """A player's details for the meter overlay's tip: its damage, or
        its heals (the tab shown), and its skills that did them."""
        if p is None:
            return None
        kind = self.world.class_of(p.name)
        if heal:
            total = p.heal_total
            stats = [[tr("Soins"), _n(total)],
                     ["HPS", _n(total / duration) if duration > 0 else "—"]]
            if total > 0.5:
                # on self and on the others (the hook tells a self-heal)
                mine = p.heal_self / total * 100
                stats.append([tr("Sur soi"), f"{mine:.0f}%"])
                stats.append([tr("Sur les autres"), f"{100 - mine:.0f}%"])
                stats.append([tr("Soin en excès"), f"{p.overheal_pct:.0f}%"])
            table = p.heals
        else:
            total = p.total
            crit = (p.crits / p.hits * 100) if p.hits else 0.0
            stats = [[tr("Dégâts"), _n(total)],
                     ["DPS", _n(total / duration) if duration > 0 else "—"],
                     [tr("Coups"), _n(p.hits)],
                     [tr("Critiques"), f"{crit:.0f}%"]]
            if p.kills:
                stats.append([tr("Kills"), _n(p.kills)])
            table = p.skills
        entries = self._merge_named(table)[:6]
        top = max((e[1] for e in entries), default=0.0) or 1.0
        # a row's icon: the first of its skills (merged by name) that has
        # its own picture; a summon's by its skill
        icons = {}
        for sid in table:
            label = self.session.skill_names.get(sid) or _pretty_id(sid)
            if label not in icons:
                base = sid.split(":")[-1]
                ok = self._icons_ok.get(base)
                if ok is None:                  # a file read: once each
                    ok = self._icons_ok[base] = _icon_ok(base)
                if ok:
                    icons[label] = base
        return {"name": p.name, "ck": class_key(kind), "cls": _class_tag(kind),
                "heal": bool(heal), "stats": stats,
                "skills": [{"t": label, "v": _n(amount),
                            "pct": f"{(amount / total * 100) if total else 0:.0f}%",
                            "n": hits, "f": round(amount / top, 4),
                            "ic": icons.get(label, "")}
                           for label, amount, hits, _c, _s in entries]}

    def _page_live(self):
        rows, duration, holding, in_combat = self._live
        online = self.game_connected()
        if not online:
            # the game is off: what the tab does once it runs
            return [{"k": "liveintro", "id": "liveintro",
                     "luck": [tr(t) for _k, t in LUCK_LABELS]}]
        parsing = self._parse_state is not None
        tools = [
            {"id": "toggle_mode", "on": self.mode == "all",
             "t": tr("Tous les joueurs") if self.mode == "all"
             else tr("Groupe")},
            {"id": "reset_data",
             "t": tr("Réinitialiser  ({key})", key=bind_label())},
            {"id": "toggle_parse", "on": parsing,
             "tone": None if parsing or online else "disabled",
             "t": (tr("Arrêter le parse") if parsing
                   else tr("Parse {n} s", n=PARSE_LENGTH_SECS))},
            {"id": "toggle_parse_journal", "on": self._parse_journal,
             "t": self._tick(self._parse_journal, tr("Journal du parse"))},
        ]
        party_total = sum(p.total for p in rows)
        heal_total = sum(p.heal_total for p in rows)
        cards = [
            {"title": tr("Combat"),
             "value": _mmss(duration) if duration > 0 else "—",
             "sub": (tr("en cours") if in_combat else
                     tr("dernier combat") if holding else
                     tr("en attente") if online else tr("jeu fermé")),
             "tone": "hot" if in_combat else ""},
            {"title": (tr("Dégâts du groupe") if self.mode == "party"
                       else tr("Dégâts du total")),
             "value": _n(party_total) if party_total else "—",
             "sub": (f"{_n(party_total / duration)} DPS"
                     if duration > 0 and party_total else "")},
            {"title": tr("Soins"),
             "value": _n(heal_total) if heal_total else "—",
             "sub": (f"{_n(heal_total / duration)} HPS"
                     if duration > 0 and heal_total else "")},
        ]
        if parsing:
            cards.append({"title": "Parse", "value": self._parse_text or "…",
                          "sub": "", "tone": "hot"})
        out = [{"k": "toolbar", "id": "live_tools", "btns": tools},
               {"k": "cards", "id": "live_cards", "items": cards}]
        focus = self._resolve_focus(rows)
        title = (tr("GROUPE") if self.mode == "party"
                 else tr("TOUS LES JOUEURS"))
        if holding:
            title = tr("{title} · DERNIER COMBAT", title=title)
        # the rift reports' rankings: damage, then heals when shown
        out.append({"k": "meter", "id": "meter", "title": title,
                    "dmg": self._ranking(rows, duration, False, focus),
                    "heal": (self._ranking(rows, duration, True, focus)
                             if self._show_heal else None),
                    "empty": (tr("En attente d'un combat…") if online else
                              tr("Lance Farever : le compteur se remplit dès "
                                 "le premier combat."))})
        out.append(self._detail_node(rows, duration, focus))
        me = self._self_prof if online else None
        if me is None:
            # the game closed: the last counters read, if any
            counters, at = self.me.got("counters")
            me = dict(counters, at=at) if isinstance(counters, dict) else None
        out.append({"k": "luck", "id": "live_luck",
                    "rows": _profile_luck(me) if me else None,
                    "empty": (tr("Lecture des compteurs…") if online else
                              tr("Lance Farever pour voir ta chance de "
                                 "butin."))})
        stats = _profile_stats(me) if me else None
        if stats:
            out.append({"k": "statcards", "id": "live_stats",
                        "items": stats})
        # the parse's journal, while it runs and once it is over
        if parsing and self.session.journal_on:
            rows = self.session.journal()
            out.append({"k": "section", "t": tr("Journal du parse")})
            if self._parse_state != "done":
                # drawn once the parse is over: not a page of lines a tick
                out.append({"k": "note", "t": tr(
                    "{n} lignes enregistrées. Le journal s'affiche à la fin "
                    "du parse.", n=len(rows))})
            else:
                f = self._journal_f
                out.append(journal_view(rows, f["who"], f["kinds"],
                                        f["limit"], node_id="parse_journal",
                                        cls=self._journal_classes(rows)))
        return out

    def _detail_node(self, rows, duration, focus):
        fp = next((p for p in rows if p.name == focus), None)
        if fp is None:
            return {"k": "detail", "id": "detail", "name": "",
                    "empty": tr("Clique sur un joueur pour voir son détail.")}
        fdps = fp.total / duration if duration > 0 else 0.0
        # as a rift report's player card (reports.py): its facts, its
        # damage and heal sources, its damage by type
        facts = [[tr("Dégâts"), _n(fp.total)],
                 ["DPS", _n(fdps) if fdps else "—"],
                 [tr("Coups"), _n(fp.hits)],
                 [tr("Critiques"), f"{fp.crits / fp.hits * 100:.0f}{pct_sp()}"
                  if fp.hits else "—"],
                 ["Kills", _n(fp.kills)]]
        if self._show_heal:
            facts.append([tr("Soins"), _n(fp.heal_total)])
            if fp.heal_total > 0.5:
                facts.append([tr("Soin en excès"),
                              f"{fp.overheal_pct:.0f}{pct_sp()}"])

        def sources(table, total, heal):
            """By display name (every weapon's "Attaque" one row), the
            skills behind it for its icon."""
            merged, ids, els = {}, {}, {}
            for sid, vals in table.items():
                label = self.session.skill_names.get(sid) or _pretty_id(sid)
                m = merged.setdefault(label, [0, 0.0, 0])
                m[0] += vals[0]; m[1] += vals[1]; m[2] += vals[2]
                ids.setdefault(label, []).append(str(sid).split(":")[-1])
                for e, a in (fp.skill_el.get(sid) or {}).items() if not heal else ():
                    w = els.setdefault(label, {})
                    w[e] = w.get(e, 0.0) + a
            out = []
            for label, (hits, amt, crits) in sorted(merged.items(),
                                                    key=lambda kv: -kv[1][1]):
                row = {"n": label, "t": _n(amt), "ids": ids[label],
                       "f": round(amt / total, 4) if total else 0,
                       "pct": _pct1(amt / total * 100 if total else 0),
                       "hits": _n(hits)}
                if not heal:
                    el_ = max(els[label], key=els[label].get) if els.get(label) else None
                    row.update({"c": element_color(el_) if el_ else "",
                                "crit": f"{crits / hits * 100:.0f}{pct_sp()}"
                                        if hits else "—",
                                "avg": _n(amt / hits) if hits else "—"})
                out.append(row)
            return out[:MAX_SKILL_ROWS]

        el = sorted(fp.elements.items(), key=lambda kv: -kv[1][1])
        return {"k": "detail", "id": "detail", "name": fp.name,
                "cls": _class_tag(self.world.class_of(fp.name)),
                "ck": class_key(self.world.class_of(fp.name)),
                "facts": facts,
                "skills": sources(fp.skills, fp.total, False),
                "heals": sources(fp.heals, fp.heal_total, True)
                if self._show_heal else [],
                "elements": [{"t": element_label(k),
                              "pct": _pct1(v[1] / fp.total * 100
                                           if fp.total else 0),
                              "f": round(v[1] / fp.total, 4) if fp.total else 0,
                              "c": element_color(k)} for k, v in el[:8]]}

    def _rift_clock(self):
        """The rift countdown shown in the sidebar. Rifts open on the hour
        and the portal stays open RIFT_PORTAL_SECS: while it is, this counts
        down to it closing; the rest of the hour, to the next one opening."""
        now = time.localtime()
        into = now.tm_min * 60 + now.tm_sec
        if self.ui_state.in_rift():
            return {"title": tr("Faille"), "value": tr("En cours"), "sub": "",
                    "tone": "rift"}
        # where it opens (the open one, or the next): the same for everyone
        hour = int(time.time()) // 3600 * 3600
        zone = rift_zone(hour if into < RIFT_PORTAL_SECS else hour + 3600)
        where = _fr_names("zone").get(zone, zone)
        if into < RIFT_PORTAL_SECS:
            left = RIFT_PORTAL_SECS - into
            return {"title": tr("Portail ouvert"),
                    "value": f"{left // 60}:{left % 60:02d}",
                    "sub": tr("avant sa fermeture"), "tone": "open",
                    "where": where}
        left = 3600 - into
        return {"title": tr("Prochaine faille"),
                "value": f"{left // 60:02d}:{left % 60:02d}",
                "sub": tr("à {hour}", hour=time.strftime(
                    "%H:00", time.localtime(time.time() + left))),
                "tone": "rift" if left <= RIFT_STYLE_SECS else "",
                "where": where}

    def _rift_map_spec(self):
        """The rift clock's window: the world map with the rift's spots,
        the next one (or the open one) picked out, and the hours after."""
        into = time.localtime().tm_min * 60 + time.localtime().tm_sec
        hour = int(time.time()) // 3600 * 3600
        first = hour if into < RIFT_PORTAL_SECS else hour + 3600
        names = _fr_names("zone")
        nxt = rift_zone(first)
        doors = (bestiary_catalogue().get("entrances") or {}).get(RIFT_ACT) or ()
        return {"meta": world_map().get("meta") or {},
                "title": (tr("Faille ouverte") if first == hour
                          else tr("Faille de {hour}", hour=time.strftime(
                              "%H:00", time.localtime(first)))),
                "where": names.get(nxt, nxt),
                "spots": [{"x": x, "y": y, "t": names.get(z, z), "on": z == nxt}
                          for x, y, z in doors],
                "next": [{"h": time.strftime("%H:00", time.localtime(t)),
                          "t": names.get(rift_zone(t), rift_zone(t))}
                         for t in (first + i * 3600 for i in range(1, 7))]}

    # ---- rifts
    def _rift_files(self):
        try:
            return sorted(RIFTS_DIR.glob("rift-*.json"), reverse=True)
        except OSError:
            return []

    def _rift_tick(self, name):
        name = Path(str(name or "")).name
        if not name.startswith("rift-"):
            return
        self._rift_sel ^= {name}
        self._rift_confirm = False

    def _rift_tick_all(self):
        names = {p.name for p in self._rift_files()}
        self._rift_sel = set() if names and names <= self._rift_sel else names
        self._rift_confirm = False

    def _rift_delete_cancel(self):
        self._rift_confirm = False

    def _rift_delete(self):
        """Delete the ticked rift reports — on the second press: the first
        only arms the button."""
        names = self._rift_sel & {p.name for p in self._rift_files()}
        if not names:
            self._rift_sel = set()
            return
        if not self._rift_confirm:
            self._rift_confirm = True
            return
        gone = self._remove_rift_reports(names)
        self._rift_sel, self._rift_confirm = set(), False
        self._toast_msg(tr("{n} failles supprimées.", n=gone) if gone > 1
                        else tr("{n} faille supprimée.", n=gone))

    @staticmethod
    def _remove_rift_reports(names):
        """Remove rift reports (rift-*.json) with their .txt and .png.
        -> how many reports went."""
        gone = 0
        for name in names:
            name = Path(str(name)).name
            if not (name.startswith("rift-") and name.endswith(".json")):
                continue                    # never anything else
            stem = name[:-len(".json")]
            remove_journal(RIFTS_DIR, name)
            for ext in (".json", ".txt", ".png"):
                try:
                    (RIFTS_DIR / (stem + ext)).unlink()
                    gone += ext == ".json"
                except FileNotFoundError:
                    pass
                except OSError as e:
                    print(f"[meter] couldn't delete {stem}{ext}: {e}",
                          file=sys.stderr)
        return gone

    def _set_rift_keep(self, value):
        try:
            self._rift_keep = max(0, int(value))
        except (TypeError, ValueError):
            return
        self._save_settings()

    def _prune_rifts(self):
        """Beyond the limit set in the Failles tab, the oldest rift reports
        go (a new one just came in)."""
        if self._rift_keep <= 0:
            return
        extra = self._rift_files()[self._rift_keep:]   # newest first
        if extra:
            gone = self._remove_rift_reports(p.name for p in extra)
            print(f"[meter] {gone} old rift report(s) removed (limit "
                  f"{self._rift_keep})", file=sys.stderr)

    def _read_rift_file(self, name):
        name = Path(str(name)).name              # never a path from the page
        try:
            data = json.loads((RIFTS_DIR / name).read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("phases"), list):
            return None
        return data

    def _rift_summary(self, path):
        """One saved rift's card — its day, hour, length, players and gates
        closed (recorded since 1.12) — cached by modification time."""
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
        at = time.localtime(data.get("at") or 0)
        gates = data.get("gates")
        out = {"day": f"{at.tm_mday} {month_name(at.tm_mon, full=True)} {at.tm_year}",
               "time": time.strftime("%H:%M", at),
               "dur": _mmss(dur), "players": len(players),
               "gates": int(gates) if isinstance(gates, (int, float)) else None}
        self._rift_cache[path.name] = (mtime, out)
        return out

    def _page_rifts(self):
        if self._rift_view is not None:
            d = self._rift_view
            out = [{"k": "toolbar", "id": "rift_tools", "btns": [
                       {"id": "close_rift", "t": tr("‹  Toutes les failles")},
                       {"id": "copy_rift_image", "t": tr("Copier l'image")}]}]
            # its boss's journal: rifts recorded since it exists
            return out + self._journal_page(d, RIFTS_DIR, "rift_journal",
                                            self._report_node(d), "Rifts")
        # the rifts done, a card each, under the day they were done
        groups = []
        files = self._rift_files()
        names = {p.name for p in files}
        self._rift_sel &= names
        for path in files[:300]:
            got = self._rift_summary(path)
            if got is None:
                continue
            if not groups or groups[-1]["t"] != got["day"]:
                groups.append({"t": got["day"], "cards": []})
            groups[-1]["cards"].append(
                {"file": path.name, "time": got["time"], "dur": got["dur"],
                 "players": got["players"], "gates": got["gates"],
                 "on": path.name in self._rift_sel})
        n = len(self._rift_sel)
        all_on = bool(names) and names <= self._rift_sel
        bottom = [{"id": "open_parses",
                   "t": tr("Ouvrir le dossier des rapports")},
                  {"id": "rift_tick_all",
                   "t": tr("Tout désélectionner") if all_on
                   else tr("Tout sélectionner"),
                   "tone": None if names else "disabled"}]
        if self._rift_confirm and n:
            bottom += [{"id": "rift_delete", "tone": "warn armed",
                        "t": tr("Confirmer la suppression ({n})", n=n)},
                       {"id": "rift_delete_cancel", "t": tr("Annuler")}]
        else:
            bottom.append({"id": "rift_delete",
                           "tone": "warn" if n else "disabled",
                           "t": tr("Supprimer la sélection ({n})", n=n) if n
                           else tr("Supprimer la sélection")})
        keep = [0, 10, 20, 30, 50, 100]
        if self._rift_keep not in keep:
            keep = sorted(keep + [self._rift_keep])
        data = self._achievements()
        entry = (data.get("heroes") or {}).get(data.get("last")) or {}
        return [
            *self._rift_stat_cards(),
            *rift_rewards_view(entry.get("counters") or {},
                               entry.get("luckUntil") or {}),
            {"k": "section", "t": tr("Failles réalisées")},
            {"k": "note", "t": tr("Chaque faille terminée (boss vaincu) est "
                                  "enregistrée ici avec son classement "
                                  "complet. Clique sur une faille pour la "
                                  "relire.")},
            {"k": "riftcards", "id": "rifts", "groups": groups,
             "empty": tr("Aucune faille enregistrée pour l'instant.")},
            {"k": "toolbar", "id": "rift_bottom", "btns": bottom},
            {"k": "field", "t": tr("Failles conservées"),
             "c": {"k": "select", "id": "set_rift_keep",
                   "v": str(self._rift_keep),
                   "o": [{"v": str(v), "t": tr("Les {n} dernières", n=v) if v
                          else tr("Toutes")} for v in keep]}},
        ]

    def _rift_stat_cards(self):
        """The rift counters at the top of the tab: the live ones (read every
        minute in game), else the last saved with the achievements."""
        counters, who = None, None
        if self._self_prof and isinstance(self._self_prof.get("counters"),
                                          dict):
            counters = self._self_prof["counters"]
        else:
            data = self._achievements()
            who = data.get("last")
            entry = (data.get("heroes") or {}).get(who) or {}
            if isinstance(entry.get("counters"), dict):
                counters = entry["counters"]
        if not counters:
            return []
        items = [{"title": tr(label), "value": _n(counters[k]), "sub": "",
                  "icon": RIFT_STAT_ICONS.get(k)}
                 for k, label in RIFT_STAT_LABELS
                 if isinstance(counters.get(k), (int, float))]
        return [{"k": "cards", "id": "rift_stats", "items": items}] \
            if items else []

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
            txt = tr("{name} ({diff}) — {result} en {time}",
                     name=report['name'], diff=tr(diff).lower(),
                     result=tr(report['result']),
                     time=_mmss(report['duration']))
            tone = "ok"
            if report["result"] == "victoire":
                if best is None:
                    txt = tr("{run} — premier temps enregistré", run=txt)
                elif report["duration"] < best:
                    txt = tr("{run} — nouveau record (avant : {best})",
                             run=txt, best=_mmss(best))
                    tone = "best"
                else:
                    txt = tr("{run} — record : {best}", run=txt,
                             best=_mmss(best))
            else:
                tone = ""
            self._event(txt, tone, {"id": "open_dungeon_run", "t": tr("Voir"),
                                    "p": {"file": name}} if name else None)
        self._enqueue(done)()

    def on_collection(self, p):
        """The account's collection, read in game. Saved, so the Collection
        tab shows it with the game closed. Hook thread."""
        owned = {k: sorted(set(p.get(k) or ()))
                 for k in ("mounts", "gliders", "pets", "gears")}
        # the hero's jobs and the recipes they learnt (per character: the
        # one played); kept from the last read when this one has none
        jobs = p.get("jobs")

        def done():
            if isinstance(jobs, dict):
                owned["jobs"] = {
                    str(k): {"lvl": int((v or {}).get("lvl") or 0),
                             "learnt": sorted(set((v or {}).get("learnt")
                                                  or ()))}
                    for k, v in jobs.items()}
            elif (self._collection_owned or {}).get("jobs") is not None:
                owned["jobs"] = self._collection_owned["jobs"]
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

    # ---- the overlays
    def on_stock(self, p):
        """What the hero owns changed (bag, equipment, bank). Hook thread."""
        def done():
            self.me.put(self._me_key(), "stock",
                        {"items": p.get("items") or {},
                         "banks": p.get("banks")})
            for g in self.goals.on_stock(p.get("items"), p.get("banks"),
                                         p.get("gear")):
                self._goal_reached(g)
        self._enqueue(done)()

    def on_pickup(self, p):
        def done():
            # a weapon's copy says its rarity; anything else is its row's
            rar = p.get("rarity") or item_rarity(p.get("item"))
            for g in self.goals.on_pickup(rar, p.get("count")):
                self._goal_reached(g)
        self._enqueue(done)()

    def _goal_reached(self, g):
        self._event(tr("Objectif atteint : {n} × {what}.", n=g['n'],
                       what=self.goals.label(g)), "ok")

    def _learnt(self):
        """The mounts and gliders in the account's collection (the goals
        count one learnt)."""
        c = self._collection()
        return set(c.get("mounts") or ()) | set(c.get("gliders") or ())

    def _goal_add(self, p):
        self.goals.add(p.get("kind"), p.get("ref"), p.get("n"),
                       p.get("rar"), p.get("istat"))

    def on_hero_seen(self, name, hid=None):
        """The hook saw our hero (every 3 s in the world), with its id in
        the game's database when the client is sent it. Hook thread."""
        self._hero_at = time.time()

        def done():
            if hid:
                self._hero_ids[name] = hid
            elif name not in self._hero_ids:
                # the app started in the world, past the select screen:
                # the known character of that name (and class), not a
                # double kept by its name alone
                guess = self._guess_hero_id(name)
                if guess:
                    self._hero_ids[name] = guess
            key = self._hero_key(name)
            self.me.set_hero(key, name)
            self._tag_hero(name)
            if name != self._me_name or key != self._me_hkey:
                self._me_name = name
                self._me_hkey = key
                self._me_auto_next = 0.0    # a new character: read it now
        self._enqueue(done)()

    def _me_key(self):
        """The key of the hero in game now (its name and id), or None."""
        return self._hero_key(self._me_name) if self._me_name else None

    def _auto_self_profile(self):
        """Read one's own character like Inspecter does, on arrival and then
        every SELF_PROFILE_SECS, so its last state is kept for the game
        closed. Never over an analysis the player asked for."""
        name = self._me_name
        now = time.time()
        if (not name or not self.game_connected() or now < self._me_auto_next
                or self._char_wait or self._me_build):
            return
        script = self.link.script if self.link is not None else None
        if script is None:
            return
        self._me_auto_next = now + SELF_PROFILE_SECS
        try:
            script.post({"type": "analyze", "name": name})
            self._me_auto = (name, now)
        except Exception:
            self._me_auto_next = now + 60

    def _ov_moved(self, p):
        """An overlay dropped: anchored to the nearer side of the game's
        window, left or right, top or bottom, at its distance from that
        edge — so a smaller window keeps it where it was against its side.
        The distance snaps to a grid of OVERLAY_GRID pixels."""
        oid = p.get("id")
        g = p.get("game")
        r = p.get("rect")
        if oid not in self._ov_on or not g or not r:
            return
        gx, gy, gw, gh = (int(v) for v in g)
        x, y, w, h = (int(v) for v in r)
        snap = lambda v: max(0, int(round(v / OVERLAY_GRID)) * OVERLAY_GRID)
        left, right = x - gx, gx + gw - (x + w)
        top, bottom = y - gy, gy + gh - (y + h)
        self._ov_pos[oid] = {
            "ax": "l" if left <= right else "r",
            "dx": snap(min(left, right)),
            "ay": "t" if top <= bottom else "b",
            "dy": snap(min(top, bottom))}
        self._save_settings()

    def _ov_hide_toggle(self):
        self._ov_hide_free = not self._ov_hide_free
        self._save_settings()

    def _esc_open(self):
        """The button over the game's menu: the app in front, on its
        overlays' settings."""
        self._set_tab("Settings")
        self._settings_topic = "overlay"
        self.menubridge.send({"t": "show"})

    def _ov_show(self, p):
        """Réglages › Overlay: one overlay shown or hidden."""
        oid = p.get("id")
        if oid in self._ov_on:
            self._ov_on[oid] = bool(p.get("value"))
            self._save_settings()

    def _ov_style_set(self, p):
        """Réglages › Overlay: one of an overlay's settings (its gear's)."""
        oid, key = p.get("id"), p.get("key")
        if oid not in self._ov_on or key not in ("opacity", "scale", "bg"):
            return
        st = dict(self._ov_style.get(oid) or _ov_style({}, oid))
        st[key] = p.get("value")
        self._ov_style[oid] = _ov_style(st, oid)
        self._save_settings()

    def _ov_set_theme(self, p):
        if p.get("value") in OVERLAY_THEMES:
            self._ov_theme = p["value"]
            self._save_settings()

    def _ov_set_style(self, p):
        """An overlay's opacity and size, from its settings (the gear): shown
        at once while a slider moves, saved once it is let go."""
        oid = p.get("id")
        if oid not in self._ov_on:
            return
        # what it sends over what it has (its background's opacity kept)
        st = dict(self._ov_style.get(oid) or _ov_style({}, oid))
        st.update({k: p[k] for k in ("opacity", "scale", "maxh", "bg") if k in p})
        self._ov_style[oid] = _ov_style(st, oid)
        if p.get("save", True):
            self._save_settings()

    def _ov_lock_toggle(self):
        self._ov_lock_always = not self._ov_lock_always
        self._save_settings()

    def _ov_reset(self):
        self._ov_pos = {}
        self._save_settings()
        self._toast_msg(tr("Overlays remis à leur place par défaut."))

    def _ov_set_tab(self, tab):
        if tab in ("dmg", "heal"):
            self._ov_tab = tab
            self._save_settings()

    def _currencies(self):
        """The game's currencies (item type Currency, the experience apart)
        with what the hero holds (its stock, read with the bag): [{id, t,
        n, q, hide}]."""
        types = gamedata._table("item_types.json")
        stock = (self.me.got("stock")[0] or {}).get("items") or {}
        out = []
        for cid in sorted(i for i, t in types.items()
                          if t == "Currency" and i not in G.NOT_GOALS):
            n = int(stock.get(cid) or 0)
            out.append({"id": cid, "t": item_label(cid), "n": n,
                        "q": f"{n:,}".replace(",", "\u202f"),
                        "hide": cid in self._cur_hidden})
        return out

    def on_pos(self, p):
        """Where the hero stands and faces (hook, on a change): for the
        minimap. Hook thread."""
        try:
            self._mm_pos = (float(p["x"]), float(p["y"]), float(p.get("r") or 0),
                            str(p.get("lv") or ""), time.time())
        except (KeyError, TypeError, ValueError):
            pass

    def _overlay_config(self, spec):
        """The in-game "Configuration overlays" window: a tab an overlay,
        each its rows as the game's options draw them (a section, a range,
        a choice, a toggle, toggles three a line), each row the app's own
        action to call with the value."""
        def sec(t):
            return {"k": "sec", "t": t}

        def sub(t):
            # a part of a section (its own heading, folded with it)
            return {"k": "sub", "t": t}

        def rng(t, lo, hi, v, act, p, step=5, style=None):
            return {"k": "range", "t": t, "lo": lo, "hi": hi, "v": int(v), "step": step,
                    "act": act, "p": p, "style": style}

        def tog(t, on, act, p, ic=None, img=None):
            return {"k": "toggle", "t": t, "on": bool(on), "act": act, "p": p,
                    "ic": ic, "img": img}

        def cho(t, cur, opts, act, p):
            return {"k": "choice", "t": t, "v": cur, "opts": opts, "act": act, "p": p}

        names = ({"meter": tr("Meter — Dégâts"), "meterheal": tr("Meter — Soins")}
                 if self._ov_split else {"meter": tr("Meter")})
        # (the bonus dungeon's name shortened: the menu's width)
        names.update({"luck": tr("Chance de butin"), "bonus": tr("Donjon bonus"),
                      "goals": tr("Objectifs"), "currencies": tr("Monnaies"),
                      "minimap": tr("Minimap"), "quest": tr("Quêtes")})
        if self._luck_merge:
            names.pop("bonus")
        def swatch(t, cur, none, key, start="#D93A2B", alpha=False):
            # a colour: its swatch and code, the palette (or the code typed)
            # on a click; "" is none (the game's colour, no halo); alpha: its
            # opacity chosen too ("#RRGGBBAA")
            return {"k": "color", "t": t, "v": cur, "none": none, "start": cur or start,
                    "act": "target_option", "p": {"key": key}, "alpha": alpha}

        game_col, no_col = tr("Couleur du jeu"), tr("Aucun")

        # the game's own options first (set in the game: its bars' values),
        # then the overlays': what holds for them all (their theme), each one
        preset = self._quick_preset_now()
        tabs = [{"id": "combat", "t": tr("Combat"), "group": "game", "tag": tr("Expérimental"), "rows": [
                    sec(tr("Configuration rapide")),
                    cho(tr("Configuration"), preset,
                        [[k, k] for k in self.QUICK_PRESETS] + [[k, k] for k in self._quick_custom]
                        + ([[preset, tr("Personnalisée")]] if preset == "custom" else []),
                        "quick_preset", {}),
                    {"k": "btns", "t": self._quick_note, "imp": self._quick_imp, "items":
                        [{"t": tr("Exporter le code"), "act": "quick_export"},
                         {"t": tr("Importer un code"), "act": "@import"}]
                        + ([{"t": tr("Supprimer"), "act": "quick_delete", "p": {"name": preset}}]
                           if preset in self._quick_custom else [])},
                    sec(tr("Ciblage")),
                    sub(tr("Indicateur au-dessus du monstre")),
                    rng(tr("Taille de l'indicateur"), 50, 300, self._target_scale,
                        "target_option", {"key": "scale"}, step=10),
                    swatch(tr("Halo autour de l'indicateur"), self._target_glow,
                           no_col, "glow", start="#FFD54F", alpha=True),
                    rng(tr("Flou du halo"), 1, 16, self._glow_blur,
                        "target_option", {"key": "glow_blur"}, step=1),
                    sub(tr("Cadre de la barre du monstre ciblé")),
                    swatch(tr("Couleur du cadre"), self._target_line,
                           no_col, "line", start="#7FD4FF", alpha=True),
                    rng(tr("Épaisseur du cadre"), 1, 6, self._target_line_w,
                        "target_option", {"key": "line_w"}, step=1),
                    rng(tr("Flou du cadre"), 0, 16, self._line_blur,
                        "target_option", {"key": "line_blur"}, step=1),
                    sec(tr("Monstres environnants")),
                    sub(tr("Barre")),
                    rng(tr("Épaisseur"), 0, 16, self._frame_extra,
                        "target_option", {"key": "frame"}, step=1),
                    rng(tr("Largeur"), -60, 200, self._foe_width,
                        "target_option", {"key": "foe_width"}, step=5),
                    swatch(tr("Fond"), self._bar_bg, game_col, "bg", start="#3F3935"),
                    sub(tr("Texte")),
                    cho(tr("Contenu"), self._foe_text,
                        [[k, tr(n)] for k, n in self.BAR_TEXTS.items()],
                        "target_option", {"key": "foe_text"}),
                    rng(tr("Taille du texte"), 50, 250, self._foe_text_scale,
                        "target_option", {"key": "foe_text_scale"}, step=10),
                    sub(tr("Couleur de la vie (dégradé : début, fin)")),
                    swatch(tr("Monstre qui te cible"), self._bar_colors["mine"],
                           game_col, "mine"),
                    swatch(tr("Monstre qui te cible (fin)"), self._bar_colors_end["mine"],
                           game_col, "end_mine"),
                    swatch(tr("Monstre agressif"), self._bar_colors["aggro"],
                           game_col, "aggro"),
                    swatch(tr("Monstre agressif (fin)"), self._bar_colors_end["aggro"],
                           game_col, "end_aggro"),
                    swatch(tr("Monstre neutre (n'attaque pas en premier)"),
                           self._bar_colors["neutral"], game_col, "neutral"),
                    swatch(tr("Monstre neutre (fin)"), self._bar_colors_end["neutral"],
                           game_col, "end_neutral"),
                    sub(tr("Couleur de la vie en cours de perte")),
                    swatch(tr("Monstre qui te cible"), self._lag_colors["mine"],
                           game_col, "lag_mine", start="#FFFFFF"),
                    swatch(tr("Monstre agressif"), self._lag_colors["aggro"],
                           game_col, "lag_aggro", start="#FFFFFF"),
                    swatch(tr("Monstre neutre (n'attaque pas en premier)"),
                           self._lag_colors["neutral"], game_col, "lag_neutral",
                           start="#FFFFFF"),
                    sub(tr("Niveau")),
                    tog(tr("Afficher le niveau à gauche de la barre"), self._level_show,
                        "target_option", {"key": "level_show"}),
                    rng(tr("Taille du niveau"), 50, 250, self._level_scale,
                        "target_option", {"key": "level"}, step=1),
                    swatch(tr("Couleur des chiffres"), self._level_text, game_col,
                           "level_text", start="#FFFFFF"),
                    swatch(tr("Teinte du fond et du cadre"), self._level_tint, game_col,
                           "level_tint", start="#FFFFFF"),
                    rng(tr("Décalage horizontal"), -60, 60, self._level_dx,
                        "target_option", {"key": "level_dx"}, step=1),
                    rng(tr("Décalage vertical"), -60, 60, self._level_dy,
                        "target_option", {"key": "level_dy"}, step=1),
                    sec(tr("Boss")),
                    sub(tr("Barre")),
                    rng(tr("Épaisseur"), 0, 16, self._boss_extra,
                        "target_option", {"key": "boss_extra"}, step=1),
                    swatch(tr("Fond"), self._boss_bg, game_col, "boss_bg", start="#3F3935"),
                    swatch(tr("Couleur de la vie"), self._boss_color, game_col, "boss_color"),
                    swatch(tr("Couleur de la vie (fin du dégradé)"), self._boss_color_end,
                           game_col, "boss_color_end"),
                    swatch(tr("Couleur de la vie en cours de perte"), self._boss_lag, game_col,
                           "boss_lag", start="#FFFFFF"),
                    sub(tr("Texte")),
                    cho(tr("Contenu"), self._boss_text,
                        [[k, tr(n)] for k, n in self.BAR_TEXTS.items()],
                        "target_option", {"key": "boss_text"}),
                    rng(tr("Taille du texte"), 50, 250, self._boss_text_scale,
                        "target_option", {"key": "boss_text_scale"}, step=10),
                    sub(tr("Niveau")),
                    tog(tr("Afficher le niveau à gauche de la barre"), self._boss_level_show,
                        "target_option", {"key": "boss_level_show"}),
                    rng(tr("Taille du niveau"), 50, 250, self._boss_level_scale,
                        "target_option", {"key": "boss_level_scale"}, step=1),
                    rng(tr("Décalage horizontal"), -80, 80, self._boss_level_dx,
                        "target_option", {"key": "boss_level_dx"}, step=1),
                    rng(tr("Décalage vertical"), -80, 80, self._boss_level_dy,
                        "target_option", {"key": "boss_level_dy"}, step=1),
                    sec(tr("Alliés")),
                    sub(tr("Barre")),
                    rng(tr("Épaisseur"), 0, 16, self._ally_extra,
                        "target_option", {"key": "ally_extra"}, step=1),
                    rng(tr("Largeur"), -60, 200, self._ally_width,
                        "target_option", {"key": "ally_width"}, step=5),
                    swatch(tr("Fond"), self._ally_bg, game_col, "ally_bg", start="#3F3935"),
                    swatch(tr("Couleur de la vie"), self._ally_color, game_col, "ally_color",
                           start="#5DBB4C"),
                    swatch(tr("Couleur de la vie (fin du dégradé)"), self._ally_color_end,
                           game_col, "ally_color_end", start="#2E7D32"),
                    swatch(tr("Couleur de la vie en cours de perte"), self._ally_lag, game_col,
                           "ally_lag", start="#FFFFFF"),
                    sub(tr("Texte")),
                    cho(tr("Contenu"), self._ally_text,
                        [[k, tr(n)] for k, n in self.BAR_TEXTS.items()],
                        "target_option", {"key": "ally_text"}),
                    sub(tr("Niveau")),
                    tog(tr("Afficher le niveau à gauche de la barre"), self._ally_level_show,
                        "target_option", {"key": "ally_level_show"}),
                    rng(tr("Taille du niveau"), 20, 250, self._ally_level_scale,
                        "target_option", {"key": "ally_level_scale"}, step=1),
                    rng(tr("Décalage horizontal"), -120, 120, self._ally_level_dx,
                        "target_option", {"key": "ally_level_dx"}, step=1),
                    rng(tr("Décalage vertical"), -120, 120, self._ally_level_dy,
                        "target_option", {"key": "ally_level_dy"}, step=1),
                    sub(tr("Icône de classe")),
                    tog(tr("Afficher l'icône de classe"),
                        self._ally_class == "beside", "target_option", {"key": "ally_class_on"}),
                    *[swatch(tr("Fond : {cls}").replace("{cls}", tr(lbl)), self._class_colors[k],
                             tr("Couleur du jeu"), "class_" + k, start=dflt)
                      for k, lbl, dflt in (("Warrior", "Guerrier", "#BB5649"), ("Mage", "Mage", "#388E9F"),
                                           ("Rogue", "Voleur", "#5F5C6E"), ("Priest", "Prêtre", "#B2945E"))],
                    rng(tr("Taille de l'icône"), 20, 300, self._ally_class_scale,
                        "target_option", {"key": "ally_class_scale"}, step=1),
                    rng(tr("Décalage horizontal"), -300, 300, self._ally_class_dx,
                        "target_option", {"key": "ally_class_dx"}, step=1),
                    rng(tr("Décalage vertical"), -300, 300, self._ally_class_dy,
                        "target_option", {"key": "ally_class_dy"}, step=1)]},
                {"id": "interface", "t": tr("Interface"), "group": "game", "tag": tr("Expérimental"), "rows": [
                    sec(tr("Menu du jeu")),
                    tog(tr("Bouton Farever Book dans le menu Échap"),
                        self._esc_button, "esc_button", {}),
                    sec(tr("Ma barre de vie")),
                    tog(tr("Placer et dimensionner ma barre de vie"), self._me_bar,
                        "target_option", {"key": "me_bar"}),
                    rng(tr("Largeur (0 : celle du jeu)"), 0, 900, self._me_w,
                        "target_option", {"key": "me_w"}, step=2),
                    rng(tr("Hauteur (0 : celle du jeu)"), 0, 60, self._me_h,
                        "target_option", {"key": "me_h"}, step=1),
                    rng(tr("Décalage horizontal"), -600, 600, self._me_dx,
                        "target_option", {"key": "me_dx"}, step=1),
                    rng(tr("Décalage vertical"), -400, 400, self._me_dy,
                        "target_option", {"key": "me_dy"}, step=1),
                    swatch(tr("Fond"), self._me_bg, game_col, "me_bg", start="#3F3935"),
                    swatch(tr("Couleur de la vie"), self._me_color, game_col, "me_color",
                           start="#5DBB4C"),
                    swatch(tr("Couleur de la vie (fin du dégradé)"), self._me_color_end,
                           game_col, "me_color_end", start="#2E7D32"),
                    swatch(tr("Couleur de la vie en cours de perte"), self._me_lag, game_col,
                           "me_lag", start="#FFFFFF"),
                    cho(tr("Contenu"), self._me_text,
                        [[k, tr(n)] for k, n in self.BAR_TEXTS.items()],
                        "target_option", {"key": "me_text"}),
                    sub(tr("Effets actifs (suivent la barre)")),
                    rng(tr("Décalage horizontal"), -400, 400, self._fx_dx,
                        "target_option", {"key": "fx_dx"}, step=1),
                    rng(tr("Décalage vertical"), -400, 400, self._fx_dy,
                        "target_option", {"key": "fx_dy"}, step=1),
                    sec(tr("Barre de sorts")),
                    tog(tr("Fond minimaliste (rectangle sombre)"), self._skillbar_min,
                        "target_option", {"key": "skillbar_min"}),
                    rng(tr("Opacité du fond"), 0, 100, self._skillbar_alpha,
                        "target_option", {"key": "skillbar_alpha"}, step=5),
                    rng(tr("Arrondi des coins"), 0, 30, self._skillbar_radius,
                        "target_option", {"key": "skillbar_radius"}, step=1)]},
                {"id": "general", "t": tr("Général"), "group": "overlays", "rows": [
                    sec(tr("Thème")),
                    {"k": "cards", "act": "ov_theme", "p": {},
                     "items": [{"v": t_id, "t": tr(t), "on": t_id == self._ov_theme}
                               for t_id, t in OVERLAY_THEMES.items()]}]}]
        for oid, title in names.items():
            st = self._ov_style.get(oid) or _ov_style({}, oid)
            rows = [tog(tr("Afficher l'overlay"), self._ov_on.get(oid), "ov_show", {"id": oid}),
                    sec(tr("Affichage")),
                    rng(tr("Taille"), 70, OV_SCALE_MAX_OF.get(oid, OV_SCALE_MAX), st["scale"],
                        "ov_style_set", {"id": oid, "key": "scale"}, style="scale"),
                    rng(tr("Opacité"), 30, 100, st["opacity"],
                        "ov_style_set", {"id": oid, "key": "opacity"}, style="opacity"),
                    rng(tr("Opacité du fond"), 0, 100, st.get("bg", 100),
                        "ov_style_set", {"id": oid, "key": "bg"}, style="bg")]
            if oid in ("meter", "meterheal"):
                rows += [tog(tr("Afficher le titre"), "title" not in self._meter_hide[oid],
                             "meter_part", {"id": oid, "key": "title"}),
                         tog(tr("Afficher la durée"), "time" not in self._meter_hide[oid],
                             "meter_part", {"id": oid, "key": "time"}),
                         tog(tr("Deux overlays : dégâts et soins"), self._ov_split,
                             "ov_split", {})]
            elif oid == "luck":
                rows.append(tog(tr("Donjon héroïque bonus"), self._luck_merge, "luck_merge", {}))
                lines = (spec.get("luck") or {}).get("rows") or []
                if lines:
                    rows += [sec(tr("Lignes")),
                             {"k": "grid", "items": [
                                 tog(r.get("t") or "", not r.get("hide"), "luck_row",
                                     {"id": r.get("id")}, img=r.get("img"))
                                 for r in lines if r.get("id")]}]
            elif oid == "quest":
                rows += [sec(tr("Quêtes affichées")),
                         tog(tr("Montures et planeurs à faire tomber"), self._quest_kinds["drop"],
                             "quest_set", {"key": "drop"}),
                         tog(tr("Codex : prochain palier"), self._quest_kinds["codex"],
                             "quest_set", {"key": "codex"}),
                         tog(tr("Succès du personnage"), self._quest_kinds["ach"],
                             "quest_set", {"key": "ach"}),
                         rng(tr("Lignes visibles (le reste à la molette)"), 3, 40, self._quest_max,
                             "quest_set", {"key": "max"}, step=1)]
                free = self._quest_offplace()
                if free:
                    rows += [sec(tr("Quêtes sans zone (à suivre)")),
                             {"k": "grid", "items": [
                                 tog(q["t"], q["id"] in self._quest_pins, "quest_set",
                                     {"key": "pin", "id": q["id"]}, ic=q.get("ic"))
                                 for q in free]}]
            elif oid == "currencies":
                rows.append(rng(tr("Monnaies par ligne"), 1, 6, self._cur_per_row,
                                "cur_per_row", {}, step=1))
                cur = self._currencies()
                if cur:
                    rows += [sec(tr("Monnaies")),
                             {"k": "grid", "items": [
                                 tog(c["t"], not c["hide"], "cur_show", {"id": c["id"]},
                                     ic="item:" + c["id"]) for c in cur]}]
            elif oid == "minimap":
                rows += [cho(tr("Forme"), self._mm_shape,
                             [["round", tr("Ronde")], ["square", tr("Carrée")]],
                             "mm_set", {"key": "shape"}),
                         cho(tr("Orientation"), "turn" if self._mm_rotate else "north",
                             [["north", tr("Nord en haut")],
                              ["turn", tr("Pivoter avec le personnage")]],
                             "mm_set", {"key": "rotate"}),
                         sec(tr("Icônes")),
                         tog(tr("Masquer les éléments trouvés"), self._mm_hide_found,
                             "mm_set", {"key": "hideFound"}),
                         {"k": "grid", "items": [
                             tog(tr(t), lid in self._mm_layers, "mm_set",
                                 {"key": "layer", "id": lid}, ic=MINIMAP_LAYER_ICONS.get(lid))
                             for lid, (t, _cs) in MINIMAP_LAYERS.items()]}]
            tabs.append({"id": oid, "t": title, "group": "overlays", "rows": rows})
        return {"tabs": tabs, "groups": {"game": tr("Jeu"), "overlays": tr("Overlays")}}

    def on_units(self, p):
        """The units the client holds around the hero (hook, each second on
        a change): the wild companions, the monsters alive. For the
        minimap. Hook thread."""
        def rows(k):
            return [c[:4] for c in p.get(k) or () if isinstance(c, list) and len(c) >= 3]
        self._mm_units = (rows("pets"), rows("foes"), str(p.get("lv") or ""), time.time())

    # ---- the quests: what the hero can do here (the Quêtes overlay) ----
    # Only what the game's data and the hero's progress tell: the monsters
    # around (the hook's, the minimap's), the dungeon the hero is in (its
    # activity, its difficulty), the codex of this character, the
    # collection, this character's achievements.
    QUEST_FOES_SECS = 10
    # the spawn points that tell the sub-zones around the hero (world units)
    QUEST_ZONE_REACH = 300
    # a monster seen alive, no spawn point of its own: kept that long
    QUEST_SEEN_SECS = 90
    # an achievement's banner by its category (the game's: its colour, its
    # icon in ui_banner_<i>: trophy, collection, swords, tools, star, armour)
    # (the star for all: an achievement's own mark; its category's colour)
    ACH_BANNERS = {"Combat": "ban:C8494B:4", "Exploration": "ban:7B3FA0:4",
                   "WorldEvents": "ban:7B3FA0:4", "Collection": "ban:C55A9C:4",
                   "Jobs": "ban:C9902F:4", "Character": "ban:4F7FD6:4",
                   "Reputation": "ban:5E9E3E:4"}

    def _quest_spec(self):
        kinds_on = self._quest_kinds
        self._quest_units = useful = set()
        empty = {"rows": [], "n": 0, "visible": int(self._quest_max), "where": ""}
        if not any(kinds_on.values()):
            return empty
        best = bestiary_catalogue()
        units = best.get("units") or {}
        # The monsters of the place: those the game spawns in the sub-zones
        # around the hero (its spawn points, each in its sub-zone: every
        # monster of those, killed or not yet back), the nearest first; then
        # those seen alive around lately (no spawn point known: events,
        # summons), kept QUEST_SEEN_SECS after they went.
        now = time.time()
        pets, foes, lv, at = self._mm_units
        pos = self._mm_pos
        near = []
        if pos and pos[3] == MINIMAP_WORLD and now - pos[4] < MINIMAP_POS_SECS                 and not self.ui_state.in_dungeon():
            hx, hy = pos[0], pos[1]
            spawns = best.get("spawns") or {}
            r2 = self.QUEST_ZONE_REACH ** 2
            zones = {z for pts in spawns.values() for sx, sy, z in pts
                     if (sx - hx) ** 2 + (sy - hy) ** 2 <= r2}
            dist = {}
            for u, pts in spawns.items():
                d = [(sx - hx) ** 2 + (sy - hy) ** 2 for sx, sy, z in pts if z in zones]
                if d:
                    dist[u] = min(d)
            near += sorted(dist, key=dist.get)
        # the invoked monsters (their ids end with _FS, the game's "Invoqué
        # ..."): of no use, never a quest
        near = [u for u in near if not self._invoked(u)]
        seen = self._quest_seen
        if now - at < self.QUEST_FOES_SECS:
            for kind, *_r in foes:
                seen[kind] = now
        for kind in sorted(seen, key=lambda k: -seen[k]):
            if now - seen[kind] > self.QUEST_SEEN_SECS:
                del seen[kind]
            elif kind not in near and not self._invoked(kind):
                near.append(kind)
        # the dungeon the hero is in: its monsters too (the game's: where
        # each one is met)
        run = self.ui_state.dungeon_run()
        if run and run.get("kind"):
            for u, w in (best.get("where") or {}).items():
                if run["kind"] in (w.get("acts") or ()) and u not in near                         and not self._invoked(u):
                    near.append(u)
        cx = self._codex()
        ranks = ((cx.get("heroes") or {}).get(cx.get("last")) or {}).get("ranks") or {}
        kills = lambda u: int((ranks.get(u) or [0])[0] or 0)
        ach = self._achievements()
        done = set(((ach.get("heroes") or {}).get(ach.get("last")) or {}).get("done") or ())
        owned = self._collection()
        mine = set(owned.get("mounts") or ()) | set(owned.get("gliders") or ())
        rows = []

        def head(t, img="", tint=""):
            rows.append({"sec": t, "img": img, "tint": tint})

        def abc(got):
            # in alphabetical order (accents aside)
            return sorted(got, key=lambda r: unicodedata.normalize(
                "NFKD", str(r.get("t") or "")).encode("ascii", "ignore").decode().casefold())

        # the quests of no place the player follows (Réglages: checked)
        pinned = []
        for q in self._quest_offplace():
            if q["id"] in self._quest_pins:
                pinned.append({"t": q["t"], "s": q.get("s") or "", "r": q.get("r") or "",
                               "img": q.get("img") or "", "name": "p:" + q["id"],
                               "wrap": q.get("wrap", False)})
        if pinned:
            head(tr("Suivies"), "ban:C9902F:0")
            rows += abc(pinned)

        # mounts and gliders those monsters drop, not owned
        if kinds_on["drop"]:
            col = collection_catalogue()
            got = []
            # (a quest followed is in its own section already)
            seen = {i[2:] for i in self._quest_pins if i.startswith("d:")}
            for cat in ("mounts", "gliders"):
                for e in col.get(cat) or ():
                    if e["id"] in mine:
                        continue
                    for s in e.get("src") or ():
                        k, p = s.get("k"), s.get("chance")
                        if not p or k not in ("unit", "family"):
                            continue
                        who = next((u for u in near if (k == "unit" and u == s["id"]) or (
                            k == "family" and (units.get(u) or {}).get("family") == s["id"])), None)
                        if who and e["id"] not in seen:
                            seen.add(e["id"])
                            useful.add(who)
                            got.append({"t": item_label(e["id"]), "img": "item:" + e["id"],
                                        "s": _unit_label(who), "r": _pct(p), "name": "q:" + e["id"],
                                        "tip": tr("{item} : {chance} par {unit} tué").replace(
                                            "{item}", item_label(e["id"])).replace(
                                            "{chance}", _pct(p)).replace("{unit}", _unit_label(who))})
            # the dungeon's own loot (its difficulty's)
            if run and run.get("kind"):
                ids = {e["id"] for c in ("mounts", "gliders") for e in col.get(c) or ()}
                for d in dungeon_catalogue():
                    if d.get("kind") != run["kind"]:
                        continue
                    for l in d.get("loot") or ():
                        it = l.get("item")
                        if it in ids and it not in mine and it not in seen and (
                                not l.get("diff") or l.get("diff") == run.get("difficulty")):
                            seen.add(it)
                            got.append({"t": item_label(it), "img": "item:" + it,
                                        "s": dungeon_name(run["kind"]),
                                        "r": _pct(l.get("chance")), "name": "q:" + it})
            if got:
                head(tr("Montures et planeurs"), "ban:9B4FD0:1")
                rows += abc(got)
        # the codex: the next rank of each monster there
        if kinds_on["codex"]:
            # the game's own rank (the codex's: 3 is mastered), the kills
            # its next one needs (the unit's codex tier: elite, big, foe)
            thr = _codex_thresholds()
            cu = _table("codex_units.json")
            nocodex = set(cu.get("noCodex") or ())
            elite, big = set(cu.get("elite") or ()), set(cu.get("big") or ())
            got = []
            for u in near:
                if u in nocodex or u not in units:
                    continue
                n, rank = ((ranks.get(u) or [0, 0]) + [0, 0])[:2]
                n, rank = int(n or 0), int(rank or 0)
                th = thr.get("elite" if u in elite else "big" if u in big else "foe") or []
                if rank >= len(th):
                    continue
                need = th[rank]
                if need <= n:
                    continue                # the game's rank behind: not told
                useful.add(u)
                got.append({"t": _unit_label(u), "img": "best:" + u,
                            "s": tr("Codex : palier {n}").replace("{n}", str(rank + 1)),
                            "r": f"{n} / {need}", "name": "c:" + u})
            if got:
                head(tr("Codex"), "ban:C8494B:2")
                rows += abc(got)
        # this character's achievements: a monster there to kill once, the
        # dungeon it is in to finish (in its difficulty)
        if kinds_on["ach"]:
            cat = achievements_catalogue()
            by_id = {a["id"]: a for a in cat.get("achievements") or ()}
            got = []
            for a in cat.get("achievements") or ():
                if a["id"] in done:
                    continue
                for o in a.get("obj") or ():
                    ref = o.get("ref")
                    for t in o.get("t") or ():
                        if not isinstance(t, list) or len(t) < 2:
                            continue
                        if ref == "FoeKilledOnce" and t[0] == 0 and t[1] in near and kills(t[1]) == 0:
                            name, _d = _ach_text(a, by_id, o.get("v"))
                            useful.add(t[1])
                            got.append({"t": name, "img": self.ACH_BANNERS.get(_ach_crest(a["id"]), "ban:C9902F:4"),
                                        "wrap": True,
                                        "s": tr("Tuer : {unit}").replace("{unit}", _unit_label(t[1])),
                                        "r": "0 / 1", "name": "a:" + a["id"] + t[1]})
                        elif (ref in ("ActivityCompleted", "DungeonWithoutSparkCube") and t[0] == 1
                              and run and t[1] == run.get("kind")):
                            want = t[2][0] if len(t) > 2 and isinstance(t[2], list) and t[2] else None
                            if want is not None and want != run.get("difficulty"):
                                continue
                            name, desc = _ach_text(a, by_id, o.get("v"))
                            got.append({"t": name, "wrap": True,
                                        "img": self.ACH_BANNERS.get(_ach_crest(a["id"]), "ban:C9902F:4"),
                                        "s": desc or dungeon_name(run["kind"]),
                                        "r": "", "name": "a:" + a["id"], "tip": desc})
            if got:
                head(tr("Succès"), "ban:C9902F:4")
                rows += abc(got)
        if not rows:
            return dict(empty, where=dungeon_name(run["kind"]) if run and run.get("kind") else "")
        # every row (the overlay shows so many, the rest by its wheel)
        return {"rows": rows, "n": sum(1 for r in rows if "sec" not in r),
                "visible": int(self._quest_max),
                "where": dungeon_name(run["kind"]) if run and run.get("kind") else ""}

    def _invoked(self, u):
        """An invoked monster (the game's French name "… invoqué(e)", its id
        with _FS): of no use, never a quest."""
        if "_FS" in str(u):
            return True
        names = getattr(self, "_fr_units", None)
        if names is None:
            names = self._fr_units = {k for k, v in (
                _table("names_fr.json").get("unit") or {}).items() if "invoqu" in str(v).lower()}
        return u in names

    def _quest_offplace(self):
        """The quests of no place (their monster spawned nowhere the levels
        say, no dungeon's): a mount or glider it drops, not owned; an
        achievement to kill it once, not done. [{id, t, s, r, img, ic}]."""
        best = bestiary_catalogue()
        units = best.get("units") or {}
        placed = set(best.get("spawns") or {}) | {
            u for u, w in (best.get("where") or {}).items() if w.get("acts")}
        fam_placed = {(m or {}).get("family") for u, m in units.items() if u in placed}
        owned = self._collection()
        mine = set(owned.get("mounts") or ()) | set(owned.get("gliders") or ())
        ach = self._achievements()
        done = set(((ach.get("heroes") or {}).get(ach.get("last")) or {}).get("done") or ())
        out, seen = [], set()
        col = collection_catalogue()
        for cat in ("mounts", "gliders"):
            for e in col.get(cat) or ():
                if e["id"] in mine or e["id"] in seen:
                    continue
                for s in e.get("src") or ():
                    k, p = s.get("k"), s.get("chance")
                    if not p or (k == "unit" and s["id"] in placed) or (
                            k == "family" and s["id"] in fam_placed) or k not in ("unit", "family"):
                        continue
                    who = _unit_label(s["id"]) if k == "unit" else _family_label(s["id"])
                    seen.add(e["id"])
                    out.append({"id": "d:" + e["id"], "t": item_label(e["id"]), "s": who,
                                "r": _pct(p), "img": "item:" + e["id"], "ic": "item:" + e["id"]})
                    break
        cat_ = achievements_catalogue()
        by_id = {a["id"]: a for a in cat_.get("achievements") or ()}
        for a in cat_.get("achievements") or ():
            if a["id"] in done:
                continue
            for o in a.get("obj") or ():
                if o.get("ref") != "FoeKilledOnce":
                    continue
                for t in o.get("t") or ():
                    if isinstance(t, list) and t[0] == 0 and t[1] not in placed:
                        name, desc = _ach_text(a, by_id, o.get("v"))
                        out.append({"id": "a:" + a["id"] + ":" + t[1], "t": name,
                                    "s": tr("Tuer : {unit}").replace("{unit}", _unit_label(t[1])),
                                    "img": self.ACH_BANNERS.get(_ach_crest(a["id"]), "ban:C9902F:4"),
                                    "wrap": True})
        return out

    def _quest_set(self, p):
        """The quests overlay: a kind shown or not, its lines at most, a
        quest of no place followed or not."""
        key, v = p.get("key"), p.get("value")
        if key == "pin" and p.get("id"):
            (self._quest_pins.add if v else self._quest_pins.discard)(str(p["id"]))
            self._save_settings()
            return
        if key in self._quest_kinds:
            self._quest_kinds[key] = bool(v)
        elif key == "max":
            try:
                self._quest_max = max(3, min(40, int(v)))
            except (TypeError, ValueError):
                return
        else:
            return
        self._save_settings()

    def _mm_pet_info(self, kind):
        """A companion's hover: its name, owned or not (the Collection),
        a Spark one (its glow)."""
        return {"t": _unit_label(kind),
                "own": kind in set(self._collection().get("pets") or ()),
                "spark": kind in _spark_units()}

    def _minimap_spec(self):
        """The minimap's state: the hero (in the open world, seen lately,
        not in a dungeon), the zoom, the shape, the points of the layers
        shown around it; None to hide it."""
        pos = self._mm_pos
        if (pos is None or time.time() - pos[4] > MINIMAP_POS_SECS
                or pos[3] != MINIMAP_WORLD or self.ui_state.in_dungeon()):
            return None
        x, y, r = pos[:3]
        ppu = MINIMAP_ZOOMS[self._mm_zoom]
        reach = self._mm_size / 2 * 1.45 / ppu         # world units, centre to corner
        # the hero's progress: the points found, left out or greyed
        els = self._elements()
        states = ((els.get("heroes") or {}).get(els.get("last")) or {}).get("states")
        pts = []
        for px, py, lay, key, eid in minimap_points():
            if lay not in self._mm_layers or abs(px - x) > reach or abs(py - y) > reach:
                continue
            found = bool(eid) and states is not None and _element_done(states, eid)
            if found and self._mm_hide_found:
                continue
            info = self._mm_pet_info(key[5:]) if lay == "pet" else None
            pts.append([px, py, key, found, info])
        # the units there now (the hook's): wild companions, monsters
        pets, foes, lv, at = self._mm_units
        if lv == MINIMAP_WORLD and time.time() - at < 10:
            if "foe" in self._mm_layers or self._mm_quest_only:
                for kind, cx, cy, *lv in foes:
                    useful = kind in self._quest_units
                    if self._mm_quest_only and not useful:
                        continue
                    if abs(cx - x) <= reach and abs(cy - y) <= reach:
                        pts.append([cx, cy, f"foe:{kind}", False,
                                    {"foe": True, "t": _unit_label(kind),
                                     "lvl": lv[0] if lv else 0, "quest": useful}])
            if "pet" in self._mm_layers:
                for kind, cx, cy, *_l in pets:
                    if abs(cx - x) <= reach and abs(cy - y) <= reach:
                        pts.append([cx, cy, f"coll:{kind}", False, self._mm_pet_info(kind)])
        meta = world_map().get("meta") or {}
        return {"x": round(x, 1), "y": round(y, 1), "r": round(r, 3), "ppu": ppu,
                "tile": meta.get("units_per_tile") or 576.0,
                "size": int(self._mm_size), "shape": self._mm_shape,
                "rotate": bool(self._mm_rotate), "pts": pts,
                # its gear's panel: what it shows
                "hideFound": bool(self._mm_hide_found),
                "questOnly": bool(self._mm_quest_only),
                "layers": [[lid, t, lid in self._mm_layers]
                           for lid, (t, _cs) in MINIMAP_LAYERS.items()]}

    def _meter_part(self, p):
        """A meter overlay's title or clock, shown (value) or not."""
        oid, key = p.get("id"), p.get("key")
        if oid not in self._meter_hide or key not in ("title", "time"):
            return
        (self._meter_hide[oid].discard if p.get("value") else self._meter_hide[oid].add)(key)
        self._save_settings()

    def _mm_set_zoom(self, p):
        """The minimap's zoom, a step in or out (its buttons, the wheel)."""
        try:
            d = int(p.get("d") or 0)
        except (TypeError, ValueError):
            return
        z = max(0, min(len(MINIMAP_ZOOMS) - 1, self._mm_zoom + (1 if d > 0 else -1 if d < 0 else 0)))
        if z != self._mm_zoom:
            self._mm_zoom = z
            self._save_settings()

    def _mm_set(self, p):
        """Réglages › Overlay › Minimap: one of its settings (key, value)."""
        key, v = p.get("key"), p.get("value")
        if key == "shape" and v in ("round", "square"):
            self._mm_shape = v
        elif key == "rotate":
            self._mm_rotate = v == "turn" if isinstance(v, str) else bool(v)
        elif key == "hideFound":
            self._mm_hide_found = bool(v)
        elif key == "questOnly":
            self._mm_quest_only = bool(v)
        elif key == "size":
            try:
                lo, hi, step = MINIMAP_SIZES
                self._mm_size = max(lo, min(hi, int(v) // step * step))
            except (TypeError, ValueError):
                return
        elif key == "layer" and p.get("id") in MINIMAP_LAYERS:
            (self._mm_layers.add if v else self._mm_layers.discard)(p["id"])
        else:
            return
        self._save_settings()

    def _cur_show(self, p):
        """Réglages › Overlay › Monnaies: one shown or hidden."""
        cid = str(p.get("id") or "")
        if not cid:
            return
        if p.get("value"):
            self._cur_hidden.discard(cid)
        else:
            self._cur_hidden.add(cid)
        self._save_settings()

    def _cur_set_per_row(self, p):
        try:
            self._cur_per_row = max(1, min(6, int(p.get("value"))))
        except (TypeError, ValueError):
            return
        self._save_settings()

    def _luck_row(self, p):
        """The loot luck overlay's gear: one of its lines shown or not."""
        cid = str(p.get("id") or "")
        if not cid:
            return
        if p.get("value"):
            self._luck_hidden.discard(cid)
        else:
            self._luck_hidden.add(cid)
        self._save_settings()

    def _ov_set_split(self, value):
        """The meter as two overlays (damage, heals) or one with two tabs
        (value None: the other way). Split, the heals' one shows."""
        split = (not self._ov_split) if value is None else bool(value)
        if split == self._ov_split:
            return
        self._ov_split = split
        if split:
            self._ov_on["meterheal"] = True
        self._save_settings()

    def _meter_spec(self, heal, split):
        """A meter overlay's state: damage or heals ranked (the live tab's
        very rows: its mode, its resets, its last fight), each player's
        details for the tip under the mouse, the group always there (those
        yet to hit or heal at nothing). split: "dmg" or "heal" when the
        meter is two overlays (no tabs then), else None."""
        rows, duration, holding, in_combat = self._live
        # every player (a rift's dozen too): the overlay scrolls past its height
        ranked = self._ranking(rows, duration, heal, limit=None)
        by_name = {p.name: p for p in rows}
        for r in ranked:
            r["tip"] = self._player_tip(by_name.get(r["name"]), duration, heal)
        shown = {r["name"] for r in ranked}
        for name in self._party:
            if name in shown:
                continue
            kind = self.world.class_of(name)
            ranked.append({"rank": len(ranked) + 1, "name": name,
                           "me": name == self._me_name,
                           "cls": _class_tag(kind), "ck": class_key(kind),
                           "rate": "0", "total": "0", "pct": "0%", "f": 0,
                           "zero": True,
                           "tip": self._player_tip(by_name.get(name),
                                                   duration, heal)})
        return {"tab": "heal" if heal else "dmg", "split": split,
                "held": bool(holding),
                "time": _mmss(duration) if duration > 0 else "",
                "fight": bool(in_combat),
                "rows": ranked}

    def _overlay_spec(self):
        """What the overlays show, and whether: only over the game, while it
        (or one of our windows) has the focus."""
        win = game_window(self.target_pid) if self.game_connected() else None
        fg = foreground_pid()
        ours = {self.target_pid, self.menubridge.pid()}
        # on a character in the world: the menus and loading screens never
        # see the hero, the world sees it every 3 s
        in_world = time.time() - self._hero_at < OVERLAY_HERO_SECS
        show = bool(win and not win[2] and fg and fg in ours and in_world
                    and any(self._ov_on.values()))
        on = dict(self._ov_on)
        # the heals' meter only while the meter is split
        on["meterheal"] = bool(self._ov_split and on.get("meterheal"))
        # the bonus dungeon in the loot luck overlay: its own one hidden
        on["bonus"] = bool(on.get("bonus") and not self._luck_merge)
        spec = {"show": show, "game": list(win[1]) if win else None,
                # the game's drawing area's corner: its own coordinates' 0, 0
                "client": client_origin(win[0]) if win else None,
                # the game itself in front (not one of our windows)
                "gameFront": bool(win and fg == self.target_pid),
                "pos": {k: dict(v) for k, v in self._ov_pos.items()},
                "on": on,
                "lock": "always" if self._ov_lock_always else "auto",
                "hideFree": bool(self._ov_hide_free)}
        if not show:
            return spec
        # one meter with its two tabs, or (split) damage here, heals in
        # the heals' own overlay
        if self._ov_split:
            spec["meter"] = self._meter_spec(False, "dmg")
            spec["meterheal"] = self._meter_spec(True, "heal")
        else:
            spec["meter"] = self._meter_spec(self._ov_tab == "heal", None)
            spec["meterheal"] = None
        # the parts the player hid: its title, its clock
        for oid in ("meter", "meterheal"):
            if spec.get(oid):
                spec[oid]["noTitle"] = "title" in self._meter_hide[oid]
                spec[oid]["noTime"] = "time" in self._meter_hide[oid]
        # copies: the bridge pushes only a spec unlike the last one, and
        # these dicts change in place
        spec["style"] = {k: dict(v) for k, v in self._ov_style.items()}
        self.goals.learnt = self._learnt()
        # the next heroic dungeon with a bonus, outside the dungeons
        kind = self._bonus_dungeon
        boss = next((d.get("boss") for d in dungeon_catalogue()
                     if d.get("kind") == kind), None) if kind else None
        spec["bonus"] = ({"kind": kind, "t": dungeon_name(kind), "boss": boss}
                         if kind and not self.ui_state.in_dungeon() else None)
        spec["goals"] = self.goals.view()
        # the loot luck counters, as the live tab shows them
        rows = _profile_luck(self._self_prof) if self._self_prof else None
        spec["luck"] = {"rows": [dict(r, hide=r.get("id") in self._luck_hidden)
                                 for r in rows] if rows is not None else None,
                        "well": soulwell_name(),
                        # the bonus dungeon in it (its gear's switch)
                        "merged": bool(self._luck_merge),
                        "bonus": spec["bonus"] if self._luck_merge else None}
        # the currencies the player shows, so many a line
        spec["currencies"] = {"items": [c for c in self._currencies() if not c["hide"]],
                              "perRow": int(self._cur_per_row)}
        try:
            spec["quest"] = self._quest_spec()
        except Exception as e:  # noqa: BLE001 - the other overlays first
            spec["quest"] = None
            if not getattr(self, "_quest_err_logged", False):
                self._quest_err_logged = True
                print(f"[meter] quests not made: {e!r}", file=sys.stderr)
        spec["minimap"] = self._minimap_spec()
        # the overlays' settings window (any overlay's gear opens it)
        spec["config"] = self._overlay_config(spec)
        # the overlays' looks (OVERLAY_THEMES): colours only
        spec["theme"] = self._ov_theme
        return spec

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
        sync = (tr("Succès du compte et progression de {hero}, lus en jeu le "
                   "{date}.", hero=hero, date=date_fr(time.localtime(at)))
                if at else
                tr("Pas encore lus : lance le jeu avec Farever Book ouvert."))
        return [{"k": "achievements", "id": "achievements", "sync": sync,
                 **achievements_view(data.get("account") or {},
                                     entry.get("counters") or {},
                                     self._collection(), states or {},
                                     self._hide_na)}]

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
            key = self._hero_key(hero)
            data.setdefault("heroes", {})[key] = {"items": items,
                                                  "at": time.time(),
                                                  "name": hero}
            data["last"] = key
            self._tag_hero(hero)
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
                print(f"[meter] item codex: {hero}, {len(items)} items; "
                      "by type: "
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
            key = self._hero_key(hero)
            first = key not in data.setdefault("heroes", {})
            data["heroes"][key] = {"ranks": ranks, "at": time.time(),
                                   "name": hero}
            data["last"] = key
            self._tag_hero(hero)
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

    # ---- the Steam account
    def _steam_account(self):
        """The Steam account signed in now ({id, name}) or None, read again
        every 15 s (Steam's files, steamacct)."""
        now = time.time()
        if now - getattr(self, "_steam_at", 0.0) > 15:
            self._steam_at = now
            try:
                self._steam_now = steamacct.active_account()
            except Exception as e:      # never the app's fault
                print(f"[meter] couldn't read the Steam account: {e}",
                      file=sys.stderr)
                self._steam_now = None
        return getattr(self, "_steam_now", None)

    def _accounts(self):
        """{"chars": {key: {name, id, sid, seen}}, "names": {steamid: name},
        "last": steamid, "synced": steamid, "excluded": {steamid: [key]}}
        (ACCOUNTS_FILE). A character's key: "name#id" (its id in the game's
        database), or its name alone when the game didn't send its id."""
        if getattr(self, "_accounts_data", None) is None:
            try:
                data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            # the first shape: {name: steamid}, no ids
            for name, sid in (data.pop("heroes", None) or {}).items():
                data.setdefault("chars", {}).setdefault(
                    name, {"name": name, "id": None, "sid": sid, "seen": 0})
            self._accounts_data = data
        return self._accounts_data

    def _guess_hero_id(self, name):
        """The database id of our character `name` when the game didn't
        send it (only read on the character select screen): the only one of
        that name known on the Steam account signed in, else the one of
        the class it plays now. None when it can't be told."""
        acct = self._steam_account()
        if not acct:
            return None
        cands = {k: c for k, c in (self._accounts().get("chars") or {}).items()
                 if c.get("name") == name and c.get("id")
                 and c.get("sid") == acct["id"]}
        if len(cands) == 1:
            return next(iter(cands.values()))["id"]
        cls = class_key(self.world.class_of(name))
        if not cls or not cands:
            return None
        profs = self.me.profiles()
        same = [c for k, c in cands.items()
                if str((profs.get(k) or {}).get("k") or "").lower() == cls]
        return same[0]["id"] if len(same) == 1 else None

    def _hero_key(self, name):
        """A character's key: its name and its id in the game's database
        (two characters may share a name), its name alone without one."""
        hid = getattr(self, "_hero_ids", {}).get(name)
        return f"{name}#{hid}" if hid else name

    def _tag_hero(self, hero):
        """Our character seen in game (only ever the one playing): tied to
        the Steam account signed in. One known by its name alone takes its
        id when the game sends it (its codex follows)."""
        acct = self._steam_account()
        if not acct or not hero or hero == "?":
            return
        data = self._accounts()
        sid = acct["id"]
        before = json.dumps(data, sort_keys=True)
        chars = data.setdefault("chars", {})
        key = self._hero_key(hero)
        if key != hero and hero in chars and chars[hero].get("sid") == sid:
            chars.pop(hero)
            self._rekey_codex(hero, key)
        old = chars.get(key) or {}
        chars[key] = {"name": hero, "id": self._hero_ids.get(hero),
                      "sid": sid, "seen": old.get("seen") or time.time()}
        if acct.get("name"):
            data.setdefault("names", {})[sid] = acct["name"]
        data["last"] = sid
        if json.dumps(data, sort_keys=True) != before:
            self._save_accounts()

    def _rekey_codex(self, old, new):
        """A character's codex entries moved to its new key."""
        for path in (CODEX_FILE, ITEM_CODEX_FILE):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            heroes = data.get("heroes") or {}
            if old in heroes and new not in heroes:
                heroes[new] = dict(heroes.pop(old), name=old.split("#")[0])
                if data.get("last") == old:
                    data["last"] = new
                try:
                    path.write_text(json.dumps(data), encoding="utf-8")
                except OSError:
                    pass
        self._codex_data = None

    def _account_id(self):
        """The account synced with the app: the one playing while the game
        runs, else the one picked in Réglages › Compte, else the one signed
        in to Steam, else the last one seen."""
        acct = self._steam_account()
        if acct and self.game_connected():
            return acct["id"]
        data = self._accounts()
        known = set(data.get("names") or {}) | {
            c.get("sid") for c in (data.get("chars") or {}).values()}
        if data.get("synced") in known:
            return data["synced"]
        return acct["id"] if acct else data.get("last")

    def _account_name(self):
        sid = self._account_id()
        acct = self._steam_account()
        if acct and acct["id"] == sid and acct.get("name"):
            return acct["name"]
        return (self._accounts().get("names") or {}).get(sid)

    def _account_heroes(self, heroes):
        """The characters among `heroes` (codex keys) of the account synced:
        those seen playing under it, less the ones left out of its progress
        (Réglages › Compte). [] when no account is known."""
        sid = self._account_id()
        if not sid:
            return []
        chars = self._accounts().get("chars") or {}
        out = set((self._accounts().get("excluded") or {}).get(sid) or ())
        return [h for h in heroes if h not in out
                and (chars.get(h) or {}).get("sid") == sid]

    def _tabs_spec(self):
        """The tabs in their band's order: the account's together (in
        ACCOUNT_TABS's order, "grp" naming their menu) where the first of
        them stands, the others as they come."""
        out, done = [], False
        for t in APP_TABS:
            if t in ACCOUNT_TABS:
                if not done:
                    done = True
                    out += [{"v": a, "t": tr(APP_TAB_LABELS[a]),
                             "grp": "account", "grpT": tr("Progression")}
                            for a in ACCOUNT_TABS]
                continue
            out.append({"v": t, "t": tr(APP_TAB_LABELS[t]),
                        "sep": t == APP_TABS_APP_FIRST})
        return out

    def _account_view(self):
        """The header's line under Play: the account synced, signed in to
        Steam ("on") or not ("sync"), else none."""
        sid = self._account_id()
        if not sid:
            return {"state": "none"}
        acct = self._steam_account()
        return {"state": "on" if acct and acct["id"] == sid else "sync",
                "name": self._account_name() or ""}

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
        heroes = data.get("heroes") or {}
        mine = self._account_heroes(heroes)
        if mine:
            # the account's characters: their kills added up, each
            # monster's rank the best one of them reached (the game ranks
            # each character's codex by rules of its own)
            ranks = {}
            for h in mine:
                for uid, (k, r) in (heroes[h].get("ranks") or {}).items():
                    kk, rr = ranks.get(uid, (0, 0))
                    ranks[uid] = [kk + int(k or 0), max(rr, int(r or 0))]
            entry = {"ranks": ranks,
                     "at": max(heroes[h].get("at") or 0 for h in mine)}
        else:
            entry = heroes.get(data.get("last")) or {}
        at = entry.get("at")
        if mine and at:
            sync = tr("Kills de tous tes personnages du compte {acct} ({heroes}), "
                      "additionnés, lus en jeu le {date}. Le rang d'un monstre "
                      "est le meilleur qu'un de tes personnages a atteint. Le "
                      "compte est celui du jeu (son Codex) : il inclut tout ce "
                      "que tu as tué avant Farever Book, et se met à jour tout "
                      "seul quand le jeu est ouvert.",
                      acct=self._account_name() or "",
                      heroes=", ".join(sorted(
                          heroes[h].get("name") or h for h in mine)),
                      date=date_fr(time.localtime(at)))
        elif at:
            sync = tr("Kills de {hero}, lus en jeu le {date}. Le compte est "
                      "celui du jeu (son Codex) : il inclut tout ce que tu as "
                      "tué avant Farever Book, et se met à jour tout seul quand "
                      "le jeu est ouvert.",
                      hero=(heroes.get(data.get("last")) or {}).get("name")
                      or data.get("last"),
                      date=date_fr(time.localtime(at)))
        else:
            sync = tr("Pas encore lu : lance le jeu avec Farever Book ouvert, "
                      "tes kills se rempliront tout seuls.")
        if self._hunt_sel:
            return [{"k": "toolbar", "id": "hunt_tools", "btns": [
                        {"id": "hunt_close",
                         "t": tr("‹  Tableau de chasse")}]},
                    {"k": "huntmon", "id": "huntmon",
                     **hunt_detail_view(self._hunt_sel,
                                        entry.get("ranks") or {})}]
        return [{"k": "hunt", "id": "hunt", "sync": sync,
                 **bestiary_view(entry.get("ranks") or {},
                                 self._collection())}]

    # ---- character
    def on_cursor(self, p):
        """The game's cursor (hook, each second and on a change): a game
        window open, the FreeCursor key's toggle. Straight to the overlays,
        which hide for a window but stay for the key. Hook thread."""
        self.menubridge.send({"t": "cursor", "win": bool(p.get("win")),
                              "alt": bool(p.get("alt")),
                              "load": bool(p.get("load")),
                              "menu": p.get("menu") or None})

    def on_character(self, p):
        """The players around (roster) or one player's profile, from the
        hook. Hook thread."""
        def done():
            if p.get("kind") == "selfprofile":
                self._self_prof = dict(p.get("profile") or {}, at=time.time())
                if "bonus" in (p.get("profile") or {}):
                    bonus = p["profile"]["bonus"] or None
                    if bonus != self._bonus_dungeon:
                        print(f"[meter] bonus heroic dungeon: {bonus}",
                              file=sys.stderr)
                    self._bonus_dungeon = bonus
                self._rift_gates_seen((p.get("profile") or {}).get("counters"))
                st = (p.get("profile") or {}).get("stats")
                if st and self._me_key() not in self._stats_logged:
                    self._stats_logged.add(self._me_key())
                    print(f"[meter] hero stats: played {st.get('total')} s, "
                          f"first connection {st.get('first')}, this one "
                          f"{st.get('conn')}", file=sys.stderr)
                self.me.put(self._me_key(), "counters",
                            p.get("profile") or {})
                return
            if p.get("kind") == "roster":
                self._roster = p.get("players") or []
                self._party = [n for n in p.get("party") or ()
                               if isinstance(n, str) and n]
                self._roster_at = time.time()
                return
            self._char_wait = None
            who = p.get("n") or (p.get("profile") or {}).get("n")
            mine = bool(self._me_build) and self._me_build[0] == who
            auto = bool(self._me_auto) and self._me_auto[0] == who
            if auto:
                self._me_auto = None
                if p.get("missing") or not p.get("profile"):
                    self._me_auto_next = time.time() + 60
                    return
            if p.get("missing"):
                if mine:
                    self._me_build = None
                    self._toast_msg(tr("Ton personnage n'a pas pu être lu."))
                    return
                self._toast_msg(tr("{name} n'est plus à proximité.",
                                   name=p.get('n')))
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
            key = name
            if name == self._me_name:
                # one's own character: kept on disk, for the game closed,
                # by its key (two characters may share a name)
                key = self._me_key()
                self.me.put(key, "profile", p.get("profile") or {})
                if auto:
                    return              # a quiet refresh, nothing to open
            else:
                # others: kept for this session only, never written to disk
                self._profiles_data()[name] = prof
            if mine:
                # asked from the Build tab: straight into a build, named
                # after the character
                self._me_build = None
                self._set_tab("Build")
                self.buildtab.import_profile(prof, name)
                return
            self._char_sel = key
        self._enqueue(done)()

    def _profiles_data(self):
        """The profiles analysed this session (in memory only)."""
        if self._profiles is None:
            self._profiles = {}
        return self._profiles

    def _analyze(self, name):
        script = self.link.script if self.link is not None else None
        if script is None:
            self._toast_msg(tr("Le jeu n'est pas connecté."))
            return
        try:
            script.post({"type": "analyze", "name": name})
            self._char_wait = (name, time.time())
        except Exception as e:
            self._toast_msg(tr("Analyse impossible : {e}", e=e))

    def _forget_profile(self, name):
        self._profiles_data().pop(name, None)
        if self._char_sel == name:
            self._char_sel = None

    def _me(self):
        """The local hero in the players around: {n, k, lvl}, or None."""
        if not self.game_connected() or time.time() - self._roster_at > 30:
            return None
        return next((r for r in self._roster if r.get("me")), None)

    def _build_from_me(self):
        """Build's "Créer depuis mon personnage": the local hero analysed
        like any player of Inspecter, then made a build (on_character)."""
        me = self._me()
        if me is None:
            # the game closed: the character as last read
            prof = self.me.profiles().get(self.me.last)
            if prof is None:
                self._toast_msg(tr("Ton personnage n'a pas encore été lu : "
                                   "lance le jeu une fois avec Farever Book "
                                   "ouvert."))
                return
            self._set_tab("Build")
            self.buildtab.import_profile(prof, self.me.name())
            return
        self._me_build = (me["n"], time.time())
        self._analyze(me["n"])

    def _page_build(self):
        nodes = self.buildtab.page()
        me = self._me()
        wait = self._me_build
        if wait and time.time() - wait[1] > 15:
            self._me_build = wait = None
        saved = self.me.profiles().get(self.me.last)
        for n in nodes:
            if n.get("k") == "build":
                n["me"] = ({"n": me["n"], "lvl": me.get("lvl"),
                            "wait": bool(wait)} if me else
                           {"n": self.me.name(), "lvl": saved.get("lvl"),
                            "wait": False,
                            "when": date_fr(time.localtime(saved["at"]))}
                           if saved else None)
        return nodes

    def _profile_to_build(self, name):
        """Inspecter's "Créer un build": the analysed player as a build,
        opened in the Build tab."""
        prof = self._shown_profiles().get(name)
        if prof:
            self._set_tab("Build")
            self.buildtab.import_profile(prof)

    def _shown_profiles(self):
        """The profiles Inspecter lists, by key: one's own characters as
        last read (their name and id: two may share a name), the players
        analysed this session (their name)."""
        return {**self._profiles_data(), **self.me.profiles()}

    def _profile_names(self):
        """The names of the characters Inspecter lists (masked in reports)."""
        return list(dict.fromkeys(p.get("n") or k for k, p
                                  in self._shown_profiles().items()))

    def _page_character(self):
        live = self.game_connected() and time.time() - self._roster_at < 30
        roster = self._roster if live else []
        wait = self._char_wait
        if wait and time.time() - wait[1] > 15:
            self._char_wait = wait = None
        profs = self._shown_profiles()
        # uid: the open profile — opening one starts the page at the top
        return [{"k": "character", "id": "character",
                 "uid": self._char_sel or "",
                 **character_view(roster, profs, self._char_sel,
                                  wait[0] if wait else None, live)}]

    def _page_map(self):
        data = self._elements()
        hero = data.get("last")
        entry = (data.get("heroes") or {}).get(hero) or {}
        at = entry.get("at")
        sync = (tr("Progression de {hero}, lue en jeu le {date}.", hero=hero,
                   date=date_fr(time.localtime(at))) if at else
                tr("Progression pas encore lue : lance le jeu avec Farever "
                   "Book ouvert."))
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

    def _coll_model(self, item_id):
        """A collectible's 3D model, built (or cached) off the app's loop and
        sent on its own channel: ~0.5 MB, kept out of the state push."""
        def work():
            d = item_model_json(item_id)
            self.menubridge.send({"t": "model", "id": item_id, "d": d})
        threading.Thread(target=work, daemon=True).start()

    def _page_encyclopedia(self):
        """Every item of the game: its list (computed once per data), and
        the sheet of the one picked."""
        cat = collection_catalogue()
        if self._encyclo_list[0] is not cat:
            self._encyclo_list = (cat, encyclopedia_view())
            self._encyclo_sheets = {}
        view = self._encyclo_list[1]
        if not view["items"]:
            return [{"k": "section", "t": tr("Encyclopédie")},
                    {"k": "note", "t": tr("Données absentes : relance Farever "
                                          "Book avec le jeu installé pour "
                                          "les générer.")}]
        sel = self._encyclo_sel
        if not sel or not any(it["id"] == sel for it in view["items"]):
            sel = view["items"][0]["id"]
        if sel not in self._encyclo_sheets:
            self._encyclo_sheets[sel] = encyclopedia_item(sel)
        return [{"k": "encyclo", "id": "encyclo", **view,
                 "reset": self._encyclo_reset, "jump": self._encyclo_jump,
                 "sel": self._encyclo_sheets[sel]}]

    def _encyclo_open(self, iid, go=False):
        """An item shown in the Encyclopedia. `go` (an item named in a
        sheet, from any tab): the tab opens on it, its category with it —
        if the Encyclopedia lists it."""
        if not iid:
            return
        if go:
            cat = collection_catalogue()
            if self._encyclo_list[0] is not cat:
                self._encyclo_list = (cat, encyclopedia_view())
                self._encyclo_sheets = {}
            if not any(it["id"] == iid for it in self._encyclo_list[1]["items"]):
                return                  # not in it (a recipe, a currency)
            self._encyclo_jump += 1
            self._menu_tab = "Encyclopedia"
        self._encyclo_sel = iid

    def _save_npc_portrait(self, key, data):
        """A character's face, photographed by the window off its model
        (one in the hero's body has no portrait in the game): kept with the
        portraits, the Encyclopedia's list then showing it."""
        import base64
        # a character's face (npc_…), a station's photo (stn_…), one's own
        # character's (chr_…)
        if not isinstance(key, str) or not re.fullmatch(
                r"(npc|stn|chr)_[A-Za-z0-9_]+", key):
            return
        head = "data:image/png;base64,"
        if not isinstance(data, str) or not data.startswith(head) \
                or len(data) > 400_000:
            return
        try:
            raw = base64.b64decode(data[len(head):], validate=True)
        except ValueError:
            return
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            return
        try:
            folder = ANALYSIS / "boss_portraits"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{key}.png").write_bytes(raw)
            # the faces taken off an older model: gone
            kind = key.split("_", 1)[0]
            for old in folder.glob(f"{kind}_*.png"):
                if not old.stem.startswith(f"{kind}_m{MODEL_FORMAT}_"):
                    old.unlink(missing_ok=True)
        except OSError as e:
            print(f"[meter] couldn't save {key}: {e}", file=sys.stderr)
            return
        # the list built again: its picture is the face now
        self._encyclo_list = (None, None)
        self._encyclo_sheets = {}

    def _send_item_icons(self):
        """The items' icons, to the window (asked by the Encyclopedia)."""
        if not self._icons_sent:
            self._icons_sent = True
            self.menubridge.send({"t": "icons"})

    def _page_collection(self):
        owned = self._collection()
        at = owned.get("at")
        sync = (tr("Lue en jeu le {date}. Elle se met à jour toute seule "
                   "quand le jeu est ouvert.",
                   date=date_fr(time.localtime(at)))
                if at else tr("Pas encore lue : lance le jeu avec Farever "
                              "Book ouvert, ta collection se remplira toute "
                              "seule."))
        codex = self._item_codex()
        heroes = codex.get("heroes") or {}
        mine = self._account_heroes(heroes)
        if mine:
            # the account's characters: their counts added up, the rank
            # the game's steps give the total (its own rule: 1, 5, 25, 50),
            # never below one of theirs
            steps = gamedata._codex_thresholds().get("item") or []
            items = {}
            for h in mine:
                for iid, (n, r) in (heroes[h].get("items") or {}).items():
                    nn, rr = items.get(iid, (0, 0))
                    items[iid] = [nn + int(n or 0), max(rr, int(r or 0))]
            for iid, (n, r) in items.items():
                items[iid] = [n, max(r, sum(1 for t in steps if n >= t))]
            entry = {"items": items}
        else:
            entry = heroes.get(codex.get("last")) or {}
        # the item picked: the Encyclopedia's sheet (computed per pick)
        sel = None
        if self._coll_sel and self._coll_sel[1]:
            key = (self._coll_sel, id(owned), id(collection_catalogue()))
            if getattr(self, "_coll_sheet", (None,))[0] != key:
                self._coll_sheet = (key, collection_sheet(*self._coll_sel,
                                                          owned))
            sel = self._coll_sheet[1]
        return [{"k": "collection", "id": "collection",
                 "sync": sync, "sel": sel,
                 **collection_view(owned, entry.get("items") or {},
                                   self._hide_na)}]

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
        # the boss fight's journal in a file of its own: the runs' list
        # reads every run, never their journals
        journal = report.pop("journal", None)
        if journal:
            report["journal"] = len(journal)
        try:
            DUNGEONS_DIR.mkdir(parents=True, exist_ok=True)
            if journal:
                # with its players' classes: the shard forgets them
                save_journal(DUNGEONS_DIR, name, journal,
                             self._journal_classes(journal))
            (DUNGEONS_DIR / name).write_text(json.dumps(report),
                                             encoding="utf-8")
            print(f"[meter] dungeon run saved to {DUNGEONS_DIR / name}",
                  file=sys.stderr)
            self._prune_dungeon_runs(report.get("kind"), keep_name=name)
            return name
        except OSError as e:
            print(f"[meter] couldn't save the dungeon run: {e}",
                  file=sys.stderr)
            return None

    def _prune_dungeon_runs(self, kind, keep_name=None):
        """At most DUNGEON_RUNS_KEPT runs per dungeon, oldest out first; a
        run holding a difficulty's record stays, as does the one just saved."""
        mine = [(n, d) for n, d in self._dungeon_runs()
                if d.get("kind") == kind]          # newest first
        if len(mine) <= DUNGEON_RUNS_KEPT:
            return
        records = set()
        for diff in {d.get("difficulty") for _n, d in mine}:
            best = self._dungeon_best(kind, diff)
            hit = next((n for n, d in mine if d.get("difficulty") == diff
                        and d.get("result") == "victoire" and best is not None
                        and abs(d["duration"] - best) < 0.05), None)
            if hit:
                records.add(hit)
        extra = len(mine) - DUNGEON_RUNS_KEPT
        for n, _d in reversed(mine):                 # oldest first
            if extra <= 0:
                break
            if n in records or n == keep_name:
                continue
            try:
                (DUNGEONS_DIR / n).unlink()
                remove_journal(DUNGEONS_DIR, n)
                self._dungeon_cache.pop(n, None)
                extra -= 1
                print(f"[meter] old dungeon run removed: {n}", file=sys.stderr)
            except OSError as e:
                print(f"[meter] couldn't remove {n}: {e}", file=sys.stderr)

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

    def _open_dungeon_kind(self, kind):
        self._dungeon_kind = kind
        self._dungeon_tab = "info"

    def _set_dungeon_video(self, kind, url):
        """A video of the fight, pasted by the player: a YouTube link, kept
        as its id (nothing else is ever opened)."""
        if not isinstance(kind, str) or not kind:
            return
        vid = _youtube_id(url)
        if vid:
            self._dungeon_videos[kind] = vid
        elif not str(url or "").strip():
            self._dungeon_videos.pop(kind, None)
        else:
            self._toast_msg(tr("Ce lien n'est pas une vidéo YouTube."))
            return
        self._save_settings()

    def _open_dungeon_video(self, kind):
        vid = self._dungeon_videos.get(kind)
        if vid:
            import webbrowser
            webbrowser.open(f"https://www.youtube.com/watch?v={vid}")

    def _dungeon_video_node(self, kind):
        vid = self._dungeon_videos.get(kind) or ""
        return {"k": "dvideo", "id": "dungeon_video", "kind": kind,
                "vid": vid,
                "url": f"https://www.youtube.com/watch?v={vid}" if vid else ""}

    def _open_dungeon_run(self, name):
        name = Path(str(name)).name
        data = next((d for n, d in self._dungeon_runs() if n == name), None)
        if data is None:
            self._toast_msg(tr("Ce run de donjon est illisible."))
            return
        self._dungeon_kind = data.get("kind")
        self._dungeon_view = data
        self._run_tab = "report"
        self._journal_f.update(who="", limit=JOURNAL_PAGE)
        self._menu_tab = "Dungeons"

    def _journal_filter(self, p):
        """The journal's player, kinds shown, or one more page of lines."""
        f = self._journal_f
        if "who" in p:
            f["who"] = str(p.get("who") or "")
            f["limit"] = JOURNAL_PAGE
        if p.get("kind") in ("hit", "taken", "heal", "status"):
            f["kinds"] ^= {p["kind"]}
            f["limit"] = JOURNAL_PAGE
        if p.get("more"):
            f["limit"] += JOURNAL_PAGE

    def _journal_rows(self, folder, name):
        """A report's journal (its own file: its lines and its players'
        classes), read once while it is shown. -> (lines, classes)"""
        key = (str(folder), name)
        if self._journal[0] != key:
            self._journal = (key, load_journal(folder, name))
        return self._journal[1]

    def _journal_page(self, d, folder, node_id, report, icon):
        """A report with a journal: a header as a build's (its title, date
        and facts, the tabs under them: its summary, its boss's journal),
        then the tab shown. The report alone when it has no journal."""
        if not d.get("journal") or not d.get("file"):
            return [report]
        tab = self._run_tab
        head = {"k": "rhead", "id": node_id + "_head", "ic": icon,
                "t": report.get("title") or "", "when": report.get("when") or "",
                "sub": report.get("sub") or "",
                "act": "dungeon_run_tab", "on": tab, "items": [
                    {"id": "report", "t": tr("Résumé")},
                    {"id": "journal", "t": tr("Journal du boss")}]}
        if tab != "journal":
            # its title is the header's now
            return [head, dict(report, notitle=True)]
        f = self._journal_f
        rows, cls = self._journal_rows(folder, d["file"])
        return [head, journal_view(rows, f["who"], f["kinds"], f["limit"],
                                   node_id=node_id, cls=cls)]

    def _journal_classes(self, rows):
        """Each player of a journal's class key, as the shard knows it."""
        names = {r.get(k) for r in rows for k in ("p", "to") if r.get(k)}
        return {n: class_key(self.world.class_of(n)) for n in names
                if class_key(self.world.class_of(n))}

    def _dungeon_sub(self, d):
        diff = tr(DUNGEON_DIFFICULTIES.get(d.get("difficulty"),
                                           "difficulté ?"))
        deaths = int(d.get("deaths") or 0)
        bits = [diff, tr("{result} en {time}",
                         result=tr(str(d.get("result") or "?")).capitalize(),
                         time=_mmss(d.get('duration') or 0)),
                tr("{n} morts", n=deaths) if deaths > 1
                else tr("{n} mort", n=deaths)]
        if d.get("wipes"):
            bits.append(tr("{n} wipes", n=d['wipes']) if d['wipes'] > 1
                        else tr("{n} wipe", n=d['wipes']))
        return " · ".join(bits)

    @staticmethod
    def _dungeon_backdrop_node(kind):
        """The dungeon's loading screen, full size, behind its whole page."""
        dg = next((x for x in dungeon_catalogue() if x["kind"] == kind), {})
        bg = _dungeon_backdrop(kind, dg.get("boss"), dg.get("region"))
        return {"k": "backdrop", "id": "page_bg", "bg": f"{bg}_hd" if bg else ""}

    def _page_dungeons(self):
        runs = self._dungeon_runs()
        if self._dungeon_view is not None:
            d = self._dungeon_view
            node = report_view(dict(d, title=dungeon_name(d.get("kind"))))
            node["sub"] = self._dungeon_sub(d)
            out = [{"k": "toolbar", "id": "dungeon_tools", "btns": [
                       {"id": "close_dungeon_run",
                        "t": tr("‹  Runs de {dungeon}",
                                dungeon=dungeon_name(d.get("kind")))},
                       {"id": "copy_rift_image", "t": tr("Copier l'image")}]},
                   self._dungeon_backdrop_node(d.get("kind"))]
            # the boss fight's journal: runs recorded since it exists
            return out + self._journal_page(d, DUNGEONS_DIR,
                                            "dungeon_journal", node,
                                            "Dungeons")
        if self._dungeon_kind is not None:
            kind = self._dungeon_kind
            mine = [(n, d) for n, d in runs if d.get("kind") == kind]
            name = dungeon_name(kind)
            cards = []
            for diff, label in DUNGEON_DIFFICULTIES.items():
                best = self._dungeon_best(kind, diff)
                won = [d for _n, d in mine if d.get("difficulty") == diff
                       and d.get("result") == "victoire"]
                cards.append({"title": tr(label), "art": f"dungeon_diff_{diff}",
                              "value": _mmss(best) if best else "—",
                              "sub": tr("{n} victoires", n=len(won))
                                     if len(won) > 1
                                     else tr("{n} victoire", n=len(won))})
            # the runs, a card each under their day, as the rifts are
            days = []
            for n, d in mine:
                best = self._dungeon_best(kind, d.get("difficulty"))
                star = (d.get("result") == "victoire" and best is not None
                        and abs(d["duration"] - best) < 0.05)
                players = d["phases"][-1].get("players") or []
                at = time.localtime(d.get("at") or 0)
                day = f"{at.tm_mday} {month_name(at.tm_mon, full=True)} {at.tm_year}"
                if not days or days[-1]["t"] != day:
                    days.append({"t": day, "cards": []})
                deaths = int(d.get("deaths") or 0)
                days[-1]["cards"].append({
                    "file": n, "open": "open_dungeon_run",
                    "time": time.strftime("%H:%M", at),
                    "diff": {"d": d.get("difficulty"),
                             "t": tr(DUNGEON_DIFFICULTIES.get(
                                 d.get("difficulty"), "difficulté ?"))},
                    "result": str(d.get("result") or "?"),
                    "star": star,
                    "facts": [[tr("durée"), _mmss(d.get("duration") or 0)],
                              [tr("mort") if deaths == 1 else tr("morts"),
                               str(deaths)],
                              [tr("joueur") if len(players) == 1
                               else tr("joueurs"), str(len(players))]],
                    "group": ", ".join(p.get("name", "?") for p in players[:6])})
            # what the runs brought back, per difficulty
            got = {k: {} for k in DUNGEON_DIFFICULTIES}
            for _name, d in mine:
                g = got.setdefault(d.get("difficulty"), {})
                for it in d.get("loot") or ():
                    g[it.get("item")] = (g.get(it.get("item"), 0)
                                         + int(it.get("count") or 1))
            dg = next((x for x in dungeon_catalogue()
                       if x["kind"] == kind), None)
            # three tabs: what it is (records, boss, where, loot), the runs,
            # help for the fight
            tab = self._dungeon_tab
            out = [{"k": "toolbar", "id": "dungeon_kind_tools", "btns": [
                       {"id": "close_dungeon_kind",
                        "t": tr("‹  Tous les donjons")}]},
                   self._dungeon_backdrop_node(kind)]
            # a header as a build's: the boss's portrait, the dungeon, its
            # boss, level and place, the tabs under them
            boss = (dg or {}).get("boss") or ""
            zones = _fr_names("zone")
            ways = (bestiary_catalogue().get("entrances") or {}).get(kind) or []
            zone = next((z for _x, _y, z in ways if z), "")
            zone = zones.get(zone) or "" if zone else ""
            facts = [tr("Boss : {name}", name=_boss_label(boss)) if boss else "",
                     tr("Niveau {n}", n=dg["level"])
                     if (dg or {}).get("level") else "",
                     zone if zone != name else "",
                     zones.get((dg or {}).get("region") or "") or ""]
            out.append({"k": "rhead", "id": "dungeon_tabs", "t": name,
                        "portrait": boss,
                        "when": " · ".join(f for f in facts if f),
                        "act": "dungeon_tab", "on": tab, "items": [
                            {"id": "info", "t": tr("Informations")},
                            {"id": "runs", "t": tr("Historique")
                             + (f" ({len(mine)})" if mine else "")},
                            {"id": "help", "t": tr("Aide")}]})
            if tab == "runs":
                out += [{"k": "riftcards", "id": "dungeon_runs",
                         "groups": days,
                         "empty": tr("Aucun run pour ce donjon.")}]
                return out
            if tab == "help":
                sheet = boss_sheet_view(dg.get("boss")) if dg else None
                if sheet:
                    out += [{"k": "section", "t": tr("Fiche du boss")}, sheet]
                # the fight's video (_dungeon_video_node): hidden for now
                return out
            # where it is: its way in, in the open world (the Hunt tab's);
            # who and what it is are the header's
            doors = [{"x": x, "y": y} for x, y, _z in ways]
            out += [{"k": "dungeonhead", "id": "dungeon_head", "t": name,
                     "maponly": True,
                     "meta": world_map().get("meta") if doors else None,
                     "doors": doors},
                    {"k": "section", "t": tr("Records")},
                    {"k": "cards", "id": "dungeon_records", "items": cards}]
            if dg and dg.get("loot"):
                # the pieces' tooltips at the loot's level: the dungeon's in
                # Normal, the top one in Veteran and Heroic
                # (st.Activity.getLevel)
                top = build_data().get("maxLevel") or 25
                out += [{"k": "section", "t": tr("Butin possible")},
                        {"k": "note", "t": tr(
                         "D'après les données et le code du jeu. Le coffre "
                         "de fin donne une des armes du boss (tirée au "
                         "hasard) et des fragments d'Étincelle selon ton "
                         "niveau. Chaque joueur reçoit aussi une pièce "
                         "d'armure de la faction, garantie : en Normal et "
                         "Vétéran une rare, tirée à parts égales parmi "
                         "celles que sa classe peut porter (les 2 dernières "
                         "reçues sont écartées), en Héroïque une épique, "
                         "parmi celles du boss pour sa classe. À 3 joueurs "
                         "une pièce de plus est donnée au hasard, 2 à 4 "
                         "joueurs. Chaque pièce a 10 % de chances d'être "
                         "prismatique (15 % avec l'offrande du Puits des "
                         "âmes). La mort du boss peut en plus donner un objet "
                         "rare et, en Héroïque, donne toujours le patron "
                         "d'imprégnation du donjon. « Obtenu » compte ce que "
                         "tes runs ont rapporté dans cette difficulté.")},
                        {"k": "droptable", "id": "dungeon_drops",
                         "tables": [{"d": k, "t": tr(label),
                                     "rows": droptable_view(
                                         dg, got[k], k,
                                         dg.get("level") if k == 0 else top)}
                                    for k, label
                                    in DUNGEON_DIFFICULTIES.items()]}]
            return out
        by_kind = {}
        for _name, d in runs:
            by_kind.setdefault(d.get("kind"), []).append(d)
        catalogue = {dg["kind"]: dg for dg in dungeon_catalogue()}

        def row(kind, boss):
            ds = by_kind.get(kind) or []
            # what we have done there: runs, victories, best time per
            # difficulty
            won = sum(1 for d in ds if d.get("result") == "victoire")
            recs = [{"d": diff, "t": tr(label), "v": _mmss(best)}
                    for diff, label in DUNGEON_DIFFICULTIES.items()
                    for best in [self._dungeon_best(kind, diff)] if best]
            dg = catalogue.get(kind) or {}
            loot = dg.get("loot") or ()
            # the loot worth coming for, a line per difficulty: the boss's
            # weapons (the chest), the faction armour (rare up to Vétéran,
            # epic in Héroïque), the infusion pattern (Héroïque), the glider
            # or mount (any difficulty)
            rk = lambda e: (e.get("rarity") or "").lower()
            weapons = [{"img": item_icon(e["item"]), "rk": rk(e),
                        "tip": tr("{item} — {pct} (coffre de fin)",
                                  item=item_label(e['item']),
                                  pct=_pct(e['chance']))}
                       for e in loot if e.get("src") == "coffre"
                       and e.get("type") != "UpgradeComponent"]
            rides = [{"img": item_icon(e["item"]), "rk": rk(e),
                      "tip": f"{item_label(e['item'])} — {_pct(e['chance'])}"}
                     for e in loot if e.get("src") == "boss"
                     and e.get("type") in ("GearGlider", "Mount")]

            def armour(src, tip):
                # tip: a translated template, its {n} the pieces
                a = [e for e in loot if e.get("src") == src]
                return [{"img": item_icon(a[0]["item"]), "n": len(a),
                         "rk": rk(a[0]),
                         "tip": tip.format(n=len(a))}] if a else []
            infusion = [{"img": item_icon(e["item"]), "rk": rk(e),
                         "tip": tr("{item} — garanti",
                                   item=item_label(e['item']))}
                        for e in loot if e.get("type") == "InfusionPattern"]
            rare = tr("Armure de faction rare : {n} pièces, une garantie par "
                      "joueur parmi celles de sa classe")
            epic = tr("Armure de faction épique : {n} pièces, une garantie "
                      "par joueur parmi celles de sa classe")
            tiers = [
                {"d": 0, "t": tr("Normal"), "icons": weapons
                 + armour("faction", rare) + rides},
                {"d": 1, "t": tr("Vétéran"), "sub": tr("niveau max"),
                 "icons": weapons + armour("faction", rare) + rides},
                {"d": 2, "t": tr("Héroïque"), "icons": weapons
                 + armour("heroic", epic) + infusion + rides}]
            tiers = [t for t in tiers if t["icons"]]
            return {"kind": kind, "t": dungeon_name(kind),
                    "boss": _boss_label(boss) if boss else "",
                    "portrait": boss or "",
                    # the dungeon's own loading screen, behind its card
                    "bg": _dungeon_backdrop(kind, boss, region_of.get(kind)),
                    "runs": len(ds), "wins": won, "recs": recs,
                    "tiers": tiers}

        # Every dungeon of the game, by region, in the game's own order;
        # runs of a dungeon the list doesn't know (a newer game) at the end.
        out = [{"k": "section", "t": tr("Donjons")},
               {"k": "note", "t": tr("Chaque donjon est enregistré "
                                     "automatiquement de l'entrée à la "
                                     "sortie : difficulté, temps (celui du "
                                     "jeu), morts, groupe, classement complet "
                                     "en deux phases — exploration et boss — "
                                     "et butin. Les échecs et abandons sont "
                                     "gardés aussi.")}]
        region_of = {dg["kind"]: dg.get("region") or ""
                     for dg in dungeon_catalogue()}
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
                or (tr("Autres donjons") if regions.keys() - {""} else "")
            if title:
                out.append({"k": "sub", "t": title})
            out.append({"k": "dcards", "id": f"dungeons_{i}",
                        "cards": [row(dg["kind"], dg.get("boss"))
                                  for dg in dgs]})
        if not regions:
            out.append({"k": "list", "id": "dungeons",
                        "rows": [],
                        "empty": tr("Aucun donjon enregistré pour "
                                    "l'instant.")})
        return out

    # ---- settings
    def _page_settings(self):
        """A menu of subjects on the left, the chosen one's settings."""
        topic = (self._settings_topic if self._settings_topic
                 in SETTINGS_TOPICS else "meter")
        nav = {"k": "setnav", "id": "setnav", "on": topic,
               "items": [{"id": k, "t": tr(t),
                          "tag": "alpha" if k == "overlay" else None}
                         for k, t in SETTINGS_TOPICS.items()]}
        return [nav] + {"account": self._settings_account,
                        "meter": self._settings_meter,
                        "overlay": self._settings_overlay,
                        "display": self._settings_display,
                        "config": self._settings_config}[topic]()

    def _settings_account(self):
        """Réglages › Compte: each Steam account seen on this PC, its
        characters (portrait, class, level), the one synced with the app
        (the one playing, while the game runs) and the characters counted."""
        data = self._accounts()
        names = data.get("names") or {}
        acct = self._steam_account()
        sync = self._account_id()
        ingame = bool(acct) and self.game_connected()
        chars_all = data.get("chars") or {}
        sids = list(dict.fromkeys(
            ([acct["id"]] if acct else []) + list(names)
            + [c.get("sid") for c in chars_all.values() if c.get("sid")]))
        profiles = self.me.profiles()
        out = [{"k": "section", "t": tr("Compte Steam")}]
        if not sids:
            out.append({"k": "note", "t": tr(
                "Aucun compte Steam n’est synchronisé avec l’application. "
                "Ouvre Steam, puis lance le jeu avec Farever Book ouvert : "
                "ton compte et tes personnages apparaîtront ici.")})
            return out
        out.append({"k": "note", "t": tr(
            "Le compte synchronisé est celui dont l’application additionne "
            "la progression (le Codex de la Chasse). En jeu, c’est forcément "
            "celui qui joue.") if not ingame else tr(
            "Tu es en jeu : le compte synchronisé est celui qui joue.")})
        accounts = []
        for sid in sids:
            mine = [k for k, c in chars_all.items() if c.get("sid") == sid]
            excluded = set((data.get("excluded") or {}).get(sid) or ())
            chars = []
            for k in sorted(mine, key=lambda k: (chars_all[k].get("name")
                                                 or k).lower()):
                h = chars_all[k].get("name") or k
                prof = profiles.get(k) or {}
                cls = str(prof.get("k") or "").lower()
                key = char_look(prof)
                port = f"chr_m{MODEL_FORMAT}_{key}" if key else None
                chars.append({
                    "key": k, "n": h, "cls": cls,
                    # its id in the game's database: none for one seen
                    # before it was read (a double, once its id is known)
                    "hid": chars_all[k].get("id"),
                    "clsT": tr(CLASS_LABELS[cls]) if cls in CLASS_LABELS else "",
                    "lvl": prof.get("lvl"),
                    **self._char_time(k),
                    "on": k not in excluded,
                    # its portrait: kept with the others, else taken by the
                    # window off its model
                    "port": port if port and (ANALYSIS / "boss_portraits"
                                              / f"{port}.png").exists()
                    else None,
                    "snap": ["char:" + key, port] if port else None})
            accounts.append({
                "sid": sid,
                "name": names.get(sid) or (acct or {}).get("name") or sid,
                "steam": bool(acct) and acct["id"] == sid,
                "sync": sid == sync, "locked": ingame,
                "chars": chars})
        out.append({"k": "accounts", "id": "accounts", "items": accounts})
        return out

    def _char_time(self, key):
        """A character's creation date and time played (its HeroStats, read
        in game: our own characters only), as the account card shows them:
        {"since": "3 septembre 2026", "played": "74 h"}, each when known."""
        st = ((self.me.entry(key).get("counters") or {}).get("stats")) or {}
        out = {}
        now = time.time()
        total, first, conn = (st.get(k) for k in ("total", "first", "conn"))
        # epochs in seconds, from the game's start (2025) to now
        plausible = lambda t: isinstance(t, (int, float)) and 1.7e9 < t <= now + 86400
        if plausible(first):
            out["since"] = date_long(time.localtime(first))
        if isinstance(total, (int, float)) and total > 0:
            # the game's own sum (getTotalPlayed): the saved time plus this
            # connection's, when it is readable
            if plausible(conn) and conn <= now:
                total += now - conn
            h = int(total // 3600)
            out["played"] = (tr("{n} h", n=h) if h
                             else tr("{n} min", n=int(total // 60)))
        return out

    def _account_sync(self, sid):
        """The account picked as the synced one (not while the game runs:
        the one playing is)."""
        data = self._accounts()
        if not isinstance(sid, str) or self.game_connected():
            return
        data["synced"] = sid
        self._save_accounts()

    def _account_count(self, sid, hero, on):
        """A character counted, or not, in its account's progress."""
        data = self._accounts()
        if not isinstance(sid, str) or not isinstance(hero, str):
            return
        ex = set((data.setdefault("excluded", {})).get(sid) or ())
        (ex.discard if on else ex.add)(hero)
        data["excluded"][sid] = sorted(ex)
        self._codex_data = None
        self._save_accounts()

    def _account_forget(self, key):
        """A character removed from the app (deleted in game): out of its
        account, its codex entries gone. Seen playing again, it comes back."""
        data = self._accounts()
        if not isinstance(key, str) or key not in (data.get("chars") or {}):
            return
        sid = data["chars"].pop(key).get("sid")
        ex = (data.get("excluded") or {}).get(sid)
        if ex and key in ex:
            ex.remove(key)
        for path in (CODEX_FILE, ITEM_CODEX_FILE):
            try:
                cx = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if key in (cx.get("heroes") or {}):
                cx["heroes"].pop(key)
                if cx.get("last") == key:
                    cx["last"] = next(iter(cx["heroes"]), None)
                try:
                    path.write_text(json.dumps(cx), encoding="utf-8")
                except OSError:
                    pass
        self._codex_data = None
        self._save_accounts()

    def _save_accounts(self):
        try:
            ACCOUNTS_FILE.write_text(json.dumps(self._accounts()),
                                     encoding="utf-8")
        except OSError as e:
            print(f"[meter] couldn't save the accounts: {e}", file=sys.stderr)

    def _open_account(self):
        """The header's account name clicked: Réglages › Compte."""
        self._settings_topic = "account"
        self._set_tab("Settings")
        self._menu_tab = "Settings"

    def _settings_meter(self):
        return [
            {"k": "section", "t": tr("Compteur")},
            {"k": "button", "id": "toggle_heal",
             "t": self._tick(self._show_heal, tr("Colonnes de soins"))},
            {"k": "button", "id": "toggle_auto_reset",
             "t": self._tick(self._auto_reset_boss,
                             tr("Réinitialiser au pull d'un boss"))},
            {"k": "button", "id": "toggle_auto_reset_dummy",
             "t": self._tick(self._auto_reset_dummy,
                             tr("Réinitialiser au premier coup sur un mannequin "
                                "(après 10 s sans le frapper)"))},
            {"k": "section", "t": tr("Raccourci clavier")},
            {"k": "field", "t": tr("Réinitialiser le combat"),
             "c": {"k": "label", "t": self._bind_prompt()}},
            {"k": "button", "id": "begin_bind",
             "t": tr("Changer cette touche")},
            {"k": "note", "t": tr("Le raccourci ne fonctionne que lorsque "
                                  "Farever est au premier plan, et n'affiche "
                                  "rien dans le jeu. Il faut Ctrl, Maj ou "
                                  "Alt, sauf pour les touches F1 à F24 et "
                                  "les boutons de souris. Échap annule.")},
        ]

    def _settings_overlay(self):
        # the meter: one overlay (two tabs), or split, its damage and its
        # heals each an overlay with its own settings
        names = ({"meter": tr("Meter — Dégâts"), "meterheal": tr("Meter — Soins")}
                 if self._ov_split else {"meter": tr("Meter")})
        names.update({"goals": tr("Objectifs"),
                      "luck": tr("Chance de butin"),
                      "bonus": tr("Donjon héroïque bonus"),
                      "currencies": tr("Monnaies"),
                      "minimap": tr("Minimap"),
                      "quest": tr("Quêtes")})
        if self._luck_merge:
            # the bonus dungeon is in the loot luck overlay
            names.pop("bonus")
        out = [{"k": "section", "t": tr("Afficher / masquer")}]
        # each overlay, a switch at the end of its line
        for oid, t in names.items():
            out.append({"k": "field", "t": t,
                        "c": {"k": "toggle", "id": "ov_show",
                              "on": bool(self._ov_on.get(oid)), "p": {"id": oid}}})
        out.append({"k": "button", "id": "ov_split",
                    "t": self._tick(self._ov_split,
                                    tr("Meter : deux overlays, un pour les "
                                       "dégâts et un pour les soins"))})
        out.append({"k": "button", "id": "luck_merge",
                    "t": self._tick(self._luck_merge,
                                    tr("Chance de butin : y afficher le donjon "
                                       "héroïque bonus (un seul overlay)"))})
        # each one's own settings, as its gear has them
        for oid, t in names.items():
            st = self._ov_style.get(oid) or _ov_style({})
            out += [{"k": "section", "t": t},
                    {"k": "field", "t": tr("Opacité"),
                     "c": {"k": "slider", "id": "ov_style_set", "min": 30,
                           "max": 100, "step": 5, "v": st["opacity"],
                           "unit": " %", "p": {"id": oid, "key": "opacity"}}},
                    {"k": "field", "t": tr("Taille"),
                     "c": {"k": "slider", "id": "ov_style_set", "min": 70,
                           "max": OV_SCALE_MAX_OF.get(oid, OV_SCALE_MAX), "step": 5,
                           "v": st["scale"],
                           "unit": " %", "p": {"id": oid, "key": "scale"}}}]
            if oid == "minimap":
                lo, hi, step = MINIMAP_SIZES
                out += [{"k": "field", "t": tr("Diamètre"),
                         "c": {"k": "slider", "id": "mm_set", "min": lo, "max": hi,
                               "step": step, "v": int(self._mm_size), "unit": " px",
                               "p": {"key": "size"}}},
                        {"k": "field", "t": tr("Forme"),
                         "c": {"k": "select", "id": "mm_set", "v": self._mm_shape,
                               "p": {"key": "shape"},
                               "o": [{"v": "round", "t": tr("Ronde")},
                                     {"v": "square", "t": tr("Carrée")}]}},
                        {"k": "field", "t": tr("Pivoter avec le personnage"),
                         "c": {"k": "toggle", "id": "mm_set", "on": bool(self._mm_rotate),
                               "p": {"key": "rotate"}}},
                        {"k": "field", "t": tr("Masquer les éléments trouvés"),
                         "c": {"k": "toggle", "id": "mm_set", "on": bool(self._mm_hide_found),
                               "p": {"key": "hideFound"}}},
                        {"k": "note", "t": tr("Zoom : les boutons + et − de la "
                                              "minimap, ou la molette sur elle.")}]
                for lid, (t, _cs) in MINIMAP_LAYERS.items():
                    out.append({"k": "field", "t": tr(t),
                                "c": {"k": "toggle", "id": "mm_set",
                                      "on": lid in self._mm_layers,
                                      "p": {"key": "layer", "id": lid}}})
            if oid == "currencies":
                out.append({"k": "field", "t": tr("Monnaies par ligne"),
                            "c": {"k": "slider", "id": "cur_per_row", "min": 1,
                                  "max": 6, "step": 1, "v": int(self._cur_per_row),
                                  "unit": ""}})
                for c in self._currencies():
                    out.append({"k": "field", "t": c["t"],
                                "c": {"k": "toggle", "id": "cur_show",
                                      "on": not c["hide"], "p": {"id": c["id"]}}})
        out += [
            {"k": "section", "t": tr("Thème")},
            {"k": "field", "t": tr("Thème des overlays"),
             "c": {"k": "select", "id": "ov_theme", "v": self._ov_theme,
                   "o": [{"v": k, "t": tr(t)} for k, t in OVERLAY_THEMES.items()]}},
            {"k": "section", "t": tr("Comportement")},
            {"k": "button", "id": "ov_lock",
             "t": self._tick(self._ov_lock_always,
                             tr("Toujours verrouillés (la souris les "
                                "traverse)"))},
            {"k": "button", "id": "ov_hide_free",
             "t": self._tick(self._ov_hide_free,
                             tr("Masquer quand une interface du jeu est "
                                "ouverte"))},
            {"k": "button", "id": "ov_reset",
             "t": tr("Remettre les overlays à leur place par défaut")},
        ]
        return out

    def _settings_display(self):
        return [
            {"k": "section", "t": tr("Fenêtre")},
            {"k": "field", "t": tr("Taille de l'interface"),
             "c": {"k": "slider", "id": "set_zoom", "v": int(self._zoom),
                   "min": 50, "max": 200, "step": 5, "unit": "%"}},
            {"k": "section", "t": tr("Thème")},
            {"k": "themes", "id": "themes", "on": self._theme,
             "items": [{"id": tid, "t": tr(name), "c": themes.preview(tid)}
                       for tid, (name, *_r) in themes.THEMES.items()]},
            {"k": "section", "t": tr("Langue")},
            {"k": "langs", "id": "langs", "on": self._lang,
             "items": [{"id": lid, "t": name}
                       for lid, name in i18n.LANGS.items()]},
        ]

    def _set_lang(self, lang):
        if lang in i18n.LANGS and lang != self._lang:
            self._lang = lang
            i18n.set_lang(lang)
            self._save_settings()
            self.menubridge.invalidate()

    def _set_theme(self, theme):
        if theme in themes.THEMES and theme != self._theme:
            self._theme = theme
            self._save_settings()
            self.menubridge.invalidate()

    def _open_licences(self):
        try:
            os.startfile(THIRD_PARTY_FILE)
        except (OSError, TypeError) as e:
            print(f"[meter] couldn't open the licences: {e}", file=sys.stderr)

    def _open_releases(self):
        import webbrowser
        webbrowser.open(RELEASES_URL)

    @staticmethod
    def _open_link(link_id):
        """One of the Help tab's links, by its id: never a URL from the page."""
        url = next((ln["url"] for ln in HELP_LINKS
                    if ln["id"] == link_id and ln["url"]), None)
        if url:
            import webbrowser
            webbrowser.open(url)

    def _settings_config(self):
        return [
            {"k": "section", "t": tr("Version")},
            {"k": "note", "t": tr("Farever Book {version}. Les nouvelles "
                                  "versions sont proposées d'elles-mêmes au "
                                  "lancement, puis toutes les heures.",
                                  version=VERSION)},
            {"k": "button", "id": "update_check",
             "t": tr("Rechercher une mise à jour")},
            *([{"k": "button", "id": "open_licences",
                "t": tr("Licences des composants")}]
              if THIRD_PARTY_FILE and THIRD_PARTY_FILE.is_file() else []),
            {"k": "section", "t": tr("Fichiers")},
            {"k": "button", "id": "open_parses",
             "t": tr("Dossier des rapports de faille")},
            {"k": "button", "id": "open_log", "t": tr("Dossier du journal")},
        ]

    # ---- boss records, rift reports ----
    @staticmethod
    def _load_best_times():
        """The record book; a missing file is empty, a corrupt entry drops."""
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

        Keyed on the PULL's boss kinds, sorted and joined: stable for a
        council (whichever member dies last) and for the Nightqueen, whose
        copies never fire a second pull edge. The killed bar's kind isn't."""
        if not kinds:
            # no kind, no record: the time alone
            self._show_kill_toast(tr("Boss vaincu en {time}",
                                     time=self._mmss(secs)), best=False)
            return
        # Keyed on the internal kind (stable across localization), shown by
        # the game's name: the two often differ (kind CLEODORA = Honeyzabeth).
        key = "+".join(kinds)
        name = " + ".join(_boss_label(k) for k in kinds)
        prev = self._best_times.get(key)
        if prev is None:
            self._best_times[key] = secs
            self._save_best_times()
            text = tr("{name} vaincu en {time} — premier kill enregistré",
                      name=name, time=self._mmss(secs))
            best = True
        elif secs < prev:
            self._best_times[key] = secs
            self._save_best_times()
            text = tr("{name} vaincu en {time} — nouveau record "
                      "(avant : {prev})", name=name, time=self._mmss(secs),
                      prev=self._mmss(prev))
            best = True
        else:
            text = tr("{name} vaincu en {time} — record : {prev}", name=name,
                      time=self._mmss(secs), prev=self._mmss(prev))
            best = False
        print(f"[meter] boss kill timed: {key} {secs:.1f}s"
              + (f" (best {self._best_times[key]:.1f}s)"), file=sys.stderr)
        self._show_kill_toast(text, best)

    def on_boss_timed_kill(self, kinds, secs):
        """The LAST boss bar went down killed. Hook thread: the record is
        done on the app's loop. Deliberately not opt-in."""
        self._enqueue(lambda: self._record_boss_kill(tuple(kinds), secs))()

    def auto_reset_boss(self) -> bool:
        """Read by the hook thread, so it stays a plain attribute read."""
        return bool(self._auto_reset_boss)

    def _toggle_auto_reset_boss(self):
        self._auto_reset_boss = not self._auto_reset_boss
        self._save_settings()

    def auto_reset_dummy(self) -> bool:
        """Read by the hook thread, so it stays a plain attribute read."""
        return bool(self._auto_reset_dummy)

    def _set_luck_merge(self, value):
        """The bonus heroic dungeon in the loot luck overlay (one overlay)
        or in its own. In it, the loot luck overlay shows."""
        merge = (not self._luck_merge) if value is None else bool(value)
        if merge == self._luck_merge:
            return
        self._luck_merge = merge
        if merge:
            self._ov_on["luck"] = True
        self._save_settings()

    def on_open_panel(self, p):
        """The Farever Book button of the game's menu: our panel in the game
        (drawn by the window's host). Hook thread."""
        self.menubridge.send({"t": "config_open"})

    def on_hook_ready(self):
        """The hook up (each attach): what it is to do for the app."""
        self._enqueue(self._post_hook_config)()

    def _post_hook_config(self):
        script = self.link.script if self.link is not None else None
        if script is None:
            return
        try:
            script.post({"type": "config", "barValues": self._foe_text != "none",
                         "foeText": self._foe_text, "bossText": self._boss_text,
                         "bossColor": self._rgb(self._boss_color),
                         "bossLag": self._rgb(self._boss_lag),
                         "bossBg": self._rgb(self._boss_bg),
                         "bossExtra": int(self._boss_extra),
                         "bossLevelShow": bool(self._boss_level_show),
                         "bossLevelScale": int(self._boss_level_scale),
                         "bossLevelDx": int(self._boss_level_dx),
                         "bossLevelDy": int(self._boss_level_dy),
                         "foeWidth": int(self._foe_width),
                         "foeTextScale": int(self._foe_text_scale),
                         "allyExtra": int(self._ally_extra), "allyWidth": int(self._ally_width),
                         "allyLevelShow": bool(self._ally_level_show),
                         "allyLevelScale": int(self._ally_level_scale),
                         "allyText": self._ally_text,
                         "allyColor": self._rgb(self._ally_color),
                         "allyColorEnd": self._rgb(self._ally_color_end),
                         "allyLag": self._rgb(self._ally_lag),
                         "allyBg": self._rgb(self._ally_bg),
                         "meColor": self._rgb(self._me_color),
                         "meColorEnd": self._rgb(self._me_color_end),
                         "meLag": self._rgb(self._me_lag),
                         "meBg": self._rgb(self._me_bg),
                         "bossColorEnd": self._rgb(self._boss_color_end),
                         "barColorsEnd": {k: self._rgb(v) for k, v in self._bar_colors_end.items()},
                         "allyLevelDx": int(self._ally_level_dx),
                         "allyLevelDy": int(self._ally_level_dy),
                         "allyClass": self._ally_class,
                         "allyClassScale": int(self._ally_class_scale),
                         "allyClassDx": int(self._ally_class_dx),
                         "allyClassDy": int(self._ally_class_dy),
                         "skillBarMin": bool(self._skillbar_min),
                         "meBar": bool(self._me_bar), "meW": int(self._me_w), "meH": int(self._me_h),
                         "meDx": int(self._me_dx), "meDy": int(self._me_dy),
                         "fxDx": int(self._fx_dx), "fxDy": int(self._fx_dy), "meText": self._me_text,
                         "skillBarAlpha": int(self._skillbar_alpha),
                         "skillBarRadius": int(self._skillbar_radius),
                         "classTint": {k: self._rgb(v) for k, v in self._class_colors.items() if v},
                         "bossTextScale": int(self._boss_text_scale),
                         "targetGlowA": self._alpha(self._target_glow),
                         "targetLineA": self._alpha(self._target_line),
                         "escButton": bool(self._esc_button),
                         "targetScale": int(self._target_scale),
                         "frameExtra": int(self._frame_extra),
                         "barColors": {k: self._rgb(v) for k, v in self._bar_colors.items()},
                         "lagColors": {k: self._rgb(v) for k, v in self._lag_colors.items()},
                         "barBg": self._rgb(self._bar_bg),
                         "levelScale": int(self._level_scale),
                         "levelShow": bool(self._level_show),
                         "levelText": self._rgb(self._level_text),
                         "levelTint": self._rgb(self._level_tint),
                         "levelDx": int(self._level_dx), "levelDy": int(self._level_dy),
                         "glowBlur": int(self._glow_blur), "lineBlur": int(self._line_blur),
                         "targetGlow": self._rgb(self._target_glow),
                         "targetLine": self._rgb(self._target_line),
                         "targetLineWidth": int(self._target_line_w)})
        except Exception as e:
            print(f"[meter] hook config not sent: {e!r}", file=sys.stderr)

    @staticmethod
    def _hex_ok(v):
        """A colour as kept: "" (the game's, or none), "#RRGGBB" or
        "#RRGGBBAA" (with its opacity)."""
        return isinstance(v, str) and (
            v == "" or re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", v) is not None)

    @staticmethod
    def _alpha(v):
        """A colour's opacity (%): its AA, 100 without."""
        return round(int(v[7:9], 16) / 255 * 100) if isinstance(v, str) and len(v) == 9 else 100

    @staticmethod
    def _rgb(v):
        """"#RRGGBB[AA]" as an int for the hook (its colour, the opacity apart), "" as None."""
        try:
            return int(v[1:7], 16) if v else None
        except (TypeError, ValueError):
            return None

    # The allies' level and class icon as they look right side by side
    # (the level small, the icon level with it), found in the game
    ALLY_ICON_BASE = {"_ally_level_scale": 35, "_ally_level_dx": 0, "_ally_level_dy": 7,
                      "_ally_class_scale": 105, "_ally_class_dx": 0, "_ally_class_dy": 0}

    # Ready-made settings of the in-game options: the game as it is
    # (Default), the creator's own (Creator).
    QUICK_PRESETS = {
        "Default": {"_foe_text": "none", "_boss_text": "none", "_frame_extra": 0,
                    "_bar_colors": {"mine": "", "aggro": "", "neutral": ""},
                    "_lag_colors": {"mine": "", "aggro": "", "neutral": ""}, "_bar_bg": "",
                    "_target_scale": 100, "_target_glow": "", "_glow_blur": 5,
                    "_target_line": "", "_target_line_w": 2, "_line_blur": 0,
                    "_level_show": True, "_level_scale": 100, "_level_text": "", "_level_tint": "",
                    "_level_dx": 0, "_level_dy": 0},
        "Creator": {"_foe_text": "max", "_boss_text": "max", "_frame_extra": 3,
                    "_bar_colors": {"mine": "#EF7E71", "aggro": "#833030", "neutral": ""},
                    "_lag_colors": {"mine": "#82453E", "aggro": "", "neutral": ""},
                    "_bar_bg": "#000000",
                    "_target_scale": 120, "_target_glow": "#FF0002", "_glow_blur": 3,
                    "_target_line": "#FF0002", "_target_line_w": 2, "_line_blur": 4,
                    "_level_show": False, "_level_scale": 80, "_level_text": "", "_level_tint": "",
                    "_level_dx": 2, "_level_dy": 3,
                    # the allies: the level and the class's icon each side of the bar
                    **{"_ally_level_show": True, "_ally_class": "beside"},
                    **dict(ALLY_ICON_BASE)},
    }

    # an ally's class's icon, its choices
    ALLY_CLASS = {"none": "Aucune", "beside": "À côté du niveau"}
    # the text in the bars, its choices
    BAR_TEXTS = {"none": "Rien", "pct": "Pourcentage", "value": "Valeur",
                 "max": "Valeur / max", "max_pct": "Valeur / max (pourcentage)"}
    # the bosses' bars as the game has them (in every quick setup)
    BOSS_DEFAULTS = {"_boss_color": "", "_boss_lag": "", "_boss_bg": "", "_boss_extra": 0,
                     "_boss_level_show": True, "_boss_level_scale": 100,
                     "_boss_level_dx": 0, "_boss_level_dy": 0, "_foe_width": 0,
                     "_foe_text_scale": 100, "_boss_text_scale": 100,
                     "_ally_extra": 0, "_ally_width": 0, "_ally_level_show": True,
                     "_ally_level_scale": 100, "_ally_text": "none", "_ally_color": "",
                     "_ally_color_end": "", "_ally_lag": "", "_ally_bg": "", "_boss_color_end": "",
                     "_bar_colors_end": {"mine": "", "aggro": "", "neutral": ""},
                     "_ally_level_dx": 0, "_ally_level_dy": 0, "_ally_class": "none",
                     "_ally_class_scale": 100, "_ally_class_dx": 0, "_ally_class_dy": 0}

    def _quick_all(self):
        """Every quick setup by name (ready-made, then imported), whole."""
        out = {n: dict(self.BOSS_DEFAULTS, **v) for n, v in self.QUICK_PRESETS.items()}
        out.update(self._quick_custom)
        return out

    def _quick_preset_now(self):
        """The quick setup the in-game options are now, or "custom"."""
        for name, vals in self._quick_all().items():
            if all(getattr(self, a) == v for a, v in vals.items()):
                return name
        return "custom"

    # A setup's values as a share code may carry them: each known setting's
    # kind (anything else, or out of its bounds, refused).
    QUICK_KINDS = {
        "_foe_text": "text", "_boss_text": "text",
        "_bar_colors": "colors", "_lag_colors": "colors",
        "_level_show": "bool", "_boss_level_show": "bool",
        "_ally_level_show": "bool", "_ally_text": "text", "_bar_colors_end": "colors",
        "_ally_extra": (0, 16), "_ally_width": (-60, 200), "_ally_level_scale": (20, 250),
        "_ally_level_dx": (-120, 120), "_ally_level_dy": (-120, 120), "_ally_class": "class",
        "_ally_class_scale": (20, 300), "_ally_class_dx": (-300, 300),
        "_ally_class_dy": (-300, 300),
        "_frame_extra": (0, 16), "_target_scale": (50, 300), "_glow_blur": (1, 16),
        "_target_line_w": (1, 6), "_line_blur": (0, 16), "_level_scale": (50, 250),
        "_level_dx": (-60, 60), "_level_dy": (-60, 60), "_boss_extra": (0, 16),
        "_boss_level_scale": (50, 250), "_boss_level_dx": (-80, 80), "_boss_level_dy": (-80, 80),
        "_foe_width": (-60, 200), "_foe_text_scale": (50, 250), "_boss_text_scale": (50, 250),
    }

    def _quick_clean(self, vals):
        """A setup's values (keys without their "_"), checked: whole (the
        Default's for what is missing), or None if any is wrong."""
        if not isinstance(vals, dict):
            return None
        out = dict(self.BOSS_DEFAULTS, **self.QUICK_PRESETS["Default"])
        for k, v in vals.items():
            a = "_" + str(k)
            if a not in out:
                continue                    # a setting of another version: left
            kind = self.QUICK_KINDS.get(a, "hex")
            if kind == "text":
                if v not in self.BAR_TEXTS:
                    return None
            elif kind == "class":
                if v not in self.ALLY_CLASS:
                    return None
            elif kind == "colors":
                if not isinstance(v, dict) or set(v) != {"mine", "aggro", "neutral"} \
                        or not all(self._hex_ok(c) for c in v.values()):
                    return None
                v = {c: x.upper() for c, x in v.items()}
            elif kind == "bool":
                if not isinstance(v, bool):
                    return None
            elif kind == "hex":
                if not self._hex_ok(v):
                    return None
                v = v.upper()
            else:
                if not isinstance(v, int) or isinstance(v, bool) or not kind[0] <= v <= kind[1]:
                    return None
            out[a] = v
        return out

    def _quick_code(self, name):
        """The in-game options as a share code: FB1: then their JSON,
        compressed, in URL-safe base64."""
        import base64
        import zlib
        vals = {a[1:]: getattr(self, a) for a in self._quick_all()["Default"]}
        raw = json.dumps({"n": name, "v": vals}, separators=(",", ":")).encode("utf-8")
        return "FB1:" + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")

    def _quick_export(self, p):

        now = self._quick_preset_now()
        name = now if now != "custom" else tr("Ma configuration")
        code = self._quick_code(name)
        self._quick_note = (tr("Code copié ({n} caractères)").replace("{n}", str(len(code)))
                            if copy_text_to_clipboard(code)
                            else tr("Copie impossible : presse-papiers occupé"))

    def _quick_import(self, p):
        """A share code given in the panel's import window, under the name
        given there: checked, added to the list, applied. The answer (taken
        or why not) goes back to the window."""
        import base64
        import zlib

        seq = p.get("seq")
        name = " ".join(str(p.get("name") or "").split())[:40]
        text = "".join(str(p.get("code") or "").split())
        vals = None
        if text.startswith("FB1:") and len(text) < 8000:
            try:
                body = text[4:]
                raw = zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
                vals = self._quick_clean(json.loads(raw.decode("utf-8")).get("v"))
            except Exception:  # noqa: BLE001 - any bad code is refused alike
                vals = None
        if not name:
            msg = tr("Donne un nom à cette configuration")
        elif name in self.QUICK_PRESETS or name in self._quick_custom:
            msg = tr("Ce nom est déjà dans la liste")
        elif vals is None:
            msg = tr("Ce code n'est pas valide")
        else:
            msg = None
        if msg:
            self._quick_imp = {"seq": seq, "ok": False, "msg": msg}
            return
        self._quick_custom[name] = vals
        self._quick_imp = {"seq": seq, "ok": True}
        self._quick_note = tr("Importée : {x}").replace("{x}", name)
        self._set_quick_preset({"value": name})

    def _quick_delete(self, p):
        name = p.get("name")
        if name in self._quick_custom:
            del self._quick_custom[name]
            self._quick_note = tr("Supprimée : {x}").replace("{x}", name)
            self._save_settings()
            self._post_hook_config()

    def _set_quick_preset(self, p):
        vals = self._quick_all().get(p.get("value"))
        if not vals:
            return
        for a, v in vals.items():
            setattr(self, a, dict(v) if isinstance(v, dict) else v)
        self._save_settings()
        self._post_hook_config()

    def _set_target_option(self, p):
        """In the game: the target indicator's size and halo, the bars'
        frame, their colours (key, value)."""
        key, v = p.get("key"), p.get("value")
        try:
            if key == "scale":
                self._target_scale = max(50, min(300, int(v)))
            elif key == "level":
                self._level_scale = max(50, min(250, int(v)))
            elif key in ("foe_text", "boss_text") and v in self.BAR_TEXTS:
                setattr(self, "_" + key, v)
            elif key in ("boss_color", "boss_lag", "boss_bg") and self._hex_ok(v):
                setattr(self, "_" + key, v.upper())
            elif key == "boss_extra":
                self._boss_extra = max(0, min(16, int(v)))
            elif key == "boss_level_show":
                self._boss_level_show = bool(v)
            elif key in ("boss_level_dx", "boss_level_dy"):
                setattr(self, "_" + key, max(-80, min(80, int(v))))
            elif key in ("foe_text_scale", "boss_text_scale"):
                setattr(self, "_" + key, max(50, min(250, int(v))))
            elif key == "ally_text" and v in self.BAR_TEXTS:
                self._ally_text = v
            elif key in ("ally_color", "ally_color_end", "ally_lag", "ally_bg",
                         "boss_color_end", "me_color", "me_color_end", "me_lag",
                         "me_bg") and self._hex_ok(v):
                setattr(self, "_" + key, v.upper())
            elif str(key).startswith("end_") and key[4:] in self._bar_colors_end and self._hex_ok(v):
                self._bar_colors_end[key[4:]] = v.upper()
            elif key == "ally_level_show":
                setattr(self, "_" + key, bool(v))
            elif key == "ally_extra":
                self._ally_extra = max(0, min(16, int(v)))
            elif key == "ally_width":
                self._ally_width = max(-60, min(200, int(v)))
            elif key == "ally_level_scale":
                self._ally_level_scale = max(20, min(250, int(v)))
            elif key in ("ally_level_dx", "ally_level_dy"):
                setattr(self, "_" + key, max(-120, min(120, int(v))))
            elif str(key).startswith("class_") and key[6:] in self._class_colors and self._hex_ok(v):
                self._class_colors[key[6:]] = v.upper()
            elif key == "me_bar":
                self._me_bar = bool(v)
            elif key in ("me_w", "me_h", "me_dx", "me_dy", "fx_dx", "fx_dy"):
                lo, hi = {"me_w": (0, 900), "me_h": (0, 60), "me_dx": (-600, 600),
                          "me_dy": (-400, 400), "fx_dx": (-400, 400), "fx_dy": (-400, 400)}[key]
                setattr(self, "_" + key, max(lo, min(hi, int(v))))
            elif key == "me_text" and v in self.BAR_TEXTS:
                self._me_text = v
            elif key == "skillbar_min":
                self._skillbar_min = bool(v)
            elif key == "skillbar_alpha":
                self._skillbar_alpha = max(0, min(100, int(v)))
            elif key == "skillbar_radius":
                self._skillbar_radius = max(0, min(30, int(v)))
            elif key == "ally_class_on":
                self._ally_class = "beside" if v else "none"
                # turned on with the level and icon untouched: the setting
                # that looks right (found in the game, 2026-10-14)
                untouched = all(getattr(self, a) == d for a, d in
                                (("_ally_level_scale", 100), ("_ally_level_dx", 0),
                                 ("_ally_level_dy", 0), ("_ally_class_scale", 100),
                                 ("_ally_class_dx", 0), ("_ally_class_dy", 0)))
                if v and untouched:
                    for a, d in self.ALLY_ICON_BASE.items():
                        setattr(self, a, d)
            elif key == "ally_class" and v in self.ALLY_CLASS:
                self._ally_class = v
            elif key == "ally_class_scale":
                self._ally_class_scale = max(20, min(300, int(v)))
            elif key in ("ally_class_dx", "ally_class_dy"):
                setattr(self, "_" + key, max(-300, min(300, int(v))))
            elif key == "foe_width":
                self._foe_width = max(-60, min(200, int(v)))
            elif key == "boss_level_scale":
                self._boss_level_scale = max(50, min(250, int(v)))
            elif key == "level_show":
                self._level_show = bool(v)
            elif key in ("level_dx", "level_dy"):
                setattr(self, "_" + key, max(-60, min(60, int(v))))
            elif key == "glow_blur":
                self._glow_blur = max(1, min(16, int(v)))
            elif key == "line_blur":
                self._line_blur = max(0, min(16, int(v)))
            elif key in ("level_text", "level_tint") and self._hex_ok(v):
                setattr(self, "_" + key, v.upper())
            elif key == "frame":
                self._frame_extra = max(0, min(16, int(v)))
            elif key in self._bar_colors and self._hex_ok(v):
                self._bar_colors[key] = v.upper()
            elif str(key).startswith("lag_") and key[4:] in self._lag_colors and self._hex_ok(v):
                self._lag_colors[key[4:]] = v.upper()
            elif key in ("glow", "line", "bg") and self._hex_ok(v):
                setattr(self, {"glow": "_target_glow", "line": "_target_line",
                               "bg": "_bar_bg"}[key], v.upper())
            elif key == "line_w":
                self._target_line_w = max(1, min(6, int(v)))
            else:
                return
        except (TypeError, ValueError):
            return
        self._save_settings()
        self._post_hook_config()

    def _set_esc_button(self, p):
        """In the game: the Farever Book button in its menu, or not (the
        next time the menu opens)."""
        self._esc_button = bool(p.get("value"))
        self._save_settings()
        self._post_hook_config()

    def _set_bar_values(self, p):
        """In the game: the values written in its bars, or not."""
        self._bar_values = bool(p.get("value"))
        self._save_settings()
        self._post_hook_config()

    def _set_hide_na(self, p):
        """Collection and Succès: the unavailable content shown or not."""
        self._hide_na = bool(p.get("value"))
        self._save_settings()

    def _toggle_auto_reset_dummy(self):
        self._auto_reset_dummy = not self._auto_reset_dummy
        self._save_settings()


    def _save_rift_report(self, report):
        """The report into failles/: .json (full metrics, read back by
        _load_last_rift_report), .txt (chat-pasteable), .png (image). Never
        fatal; each format fails alone (no Pillow costs only the picture)."""
        base = f"rift-{time.strftime('%Y%m%d-%H%M%S')}"
        saved = None
        # its boss's journal in a file of its own (gzip), the last
        # RIFT_JOURNALS_KEPT kept
        journal = report.pop("journal", None)
        if journal:
            report["journal"] = len(journal)
            report["file"] = f"{base}.json"
            if save_journal(RIFTS_DIR, f"{base}.json", journal,
                            self._journal_classes(journal)):
                prune_journals(RIFTS_DIR, "rift-", RIFT_JOURNALS_KEPT)
        try:
            RIFTS_DIR.mkdir(parents=True, exist_ok=True)
            (RIFTS_DIR / f"{base}.json").write_text(json.dumps(report),
                                                     encoding="utf-8")
            saved = f"{base}.json"
            (RIFTS_DIR / f"{base}.txt").write_text(self._report_text(report),
                                                    encoding="utf-8")
            print(f"[meter] rift report saved to {RIFTS_DIR / base}.json/.txt",
                  file=sys.stderr)
        except Exception as e:
            print(f"[meter] couldn't save the rift report: {e}",
                  file=sys.stderr)
        try:
            render_rift_report_image(report, RIFTS_DIR / f"{base}.png")
        except Exception as e:
            print(f"[meter] couldn't render the rift report image: {e}",
                  file=sys.stderr)
        return saved

    def _rift_gates_seen(self, counters):
        """A fresh read of one's counters: the gates count when a rift began,
        or, its report saved, the gates it closed — written into the report
        (rift cards show it). A rift with no read at its start gets none."""
        c = (counters or {}).get("Rift_NbGatesClosed")
        if not isinstance(c, (int, float)):
            return
        if self._rift_gates0 == "wait":
            if self._rift_gates_for is None:
                self._rift_gates0 = c
            else:
                self._rift_gates0 = self._rift_gates_for = None
            return
        name = self._rift_gates_for
        if name is None or not isinstance(self._rift_gates0, (int, float)):
            return
        gates = int(c - self._rift_gates0)
        self._rift_gates0 = self._rift_gates_for = None
        if gates < 0:
            return
        try:
            path = RIFTS_DIR / name
            data = json.loads(path.read_text(encoding="utf-8"))
            data["gates"] = gates
            path.write_text(json.dumps(data), encoding="utf-8")
        except (OSError, ValueError) as e:
            print(f"[meter] couldn't note the rift's gates: {e}",
                  file=sys.stderr)
        self._prune_rifts()

    @staticmethod
    def _load_last_rift_report():
        """The newest saved rift report, or None. Timestamped names sort, so
        newest is last. Validated for shape: a bad file costs the button."""
        try:
            files = sorted(RIFTS_DIR.glob("rift-*.json"))
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
        out = ["Farever Book — " + (data.get("title")
                                    or tr("Rapport de faille"))
               + (f" ({data['sub']})" if data.get("sub") else "")]
        for ph in data["phases"]:
            dur = ph["duration"]
            # rate first, as on the card and the image
            dps = _rate_text(ph["total"], dur, "DPS")
            hps = _rate_text(ph["heal"], dur, "HPS")
            out.append(f"== {tr(ph['label'])} — {self._mmss(dur)}, "
                       f"{dps or '— DPS'}, {hps or '— HPS'} ("
                       + tr("{dmg} dégâts, {heal} soins",
                            dmg=_n(ph['total']), heal=_n(ph['heal']))
                       + _overheal_note(ph, tr(", {:.0f}% de soin en excès"))
                       + ") ==")
            players = ph["players"]
            if not players:
                out.append("  " + tr("(rien d'enregistré)"))
                continue
            for i, p in enumerate(players, 1):
                pct = p["total"] / ph["total"] * 100 if ph["total"] else 0.0
                rate = _rate_text(p["total"], dur, "dps")
                out.append("  " + tr("dégâts {i}. {name}", i=i,
                                     name=_report_name(p)) + " "
                           + (f"{rate} " if rate else "")
                           + f"({_n(p['total'])}, {_pct1(pct)})")
            healers = sorted((p for p in players if p["heal"] > 0.5),
                             key=lambda p: -p["heal"])
            for i, p in enumerate(healers, 1):
                pct = p["heal"] / ph["heal"] * 100 if ph["heal"] else 0.0
                rate = _rate_text(p["heal"], dur, "hps")
                out.append("  " + tr("soins {i}. {name}", i=i,
                                     name=_report_name(p)) + " "
                           + (f"{rate} " if rate else "")
                           + f"({_n(p['heal'])}, {_pct1(pct)}"
                           + _overheal_note(p, tr(", {:.0f}% en excès"))
                           + ")")
            if ph["elements"]:
                out.append("  " + tr("types :") + " " + " · ".join(
                    f"{element_label(el)} "
                    f"{_pct1(amt / ph['total'] * 100 if ph['total'] else 0.0)}"
                    for el, amt in ph["elements"][:8]))
        return "\n".join(out)

    @staticmethod
    def _mmss(secs):
        m, s = divmod(int(max(0, secs)), 60)
        return f"{m}:{s:02d}"

    def _toggle_parse(self):
        if self._parse_state is None and not self.game_connected():
            return                  # nothing to measure without the game
        if self._parse_state is None:
            self._parse_state = "countdown"
            self._parse_until = time.time() + PARSE_PREROLL_SECS
            self._set_parse_banner(tr("PARSE DANS {n}", n=PARSE_PREROLL_SECS))
        else:
            self._stop_parse()

    def _begin_parse(self, now):
        """Pre-roll over: clear the meter and start the fixed-length sample."""
        self.session.reset()
        self.session.journal_on = self._parse_journal
        self._journal_f.update(who="", limit=JOURNAL_PAGE)
        self.session.set_capture_window(PARSE_LENGTH_SECS)
        # our own reset: not the player leaving parse mode
        self._last_epoch = self.session.epoch
        self.focus_player = None
        self._parse_state = "parsing"
        self._parse_until = now + PARSE_LENGTH_SECS
        self._set_parse_banner(tr("PARSE  {n} s", n=PARSE_LENGTH_SECS))

    def _finish_parse(self):
        """The session's capture window has already stopped data and clock;
        only the UI state changes. Nothing is saved."""
        self._parse_state = "done"
        self._set_parse_banner(tr("PARSE TERMINÉ  {n} s",
                                  n=PARSE_LENGTH_SECS))

    def _open_parses(self):
        """Open the rift reports' folder in Explorer, created if needed."""
        try:
            RIFTS_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(RIFTS_DIR)
        except Exception as e:
            print(f"[meter] couldn't open {RIFTS_DIR}: {e}", file=sys.stderr)

    def _stop_parse(self):
        """Back to live metering, clearing the sample (resuming into it would
        mix live hits into the parse's numbers)."""
        self._parse_state = None
        self._hide_parse_banner()
        self.session.reset()
        self._last_epoch = self.session.epoch
        self.focus_player = None

    def _tick_parse(self):
        """Drive the countdown from the refresh loop. The exact cutoff is the
        session's, so a late tick can't lengthen the sample."""
        if self._parse_state is None or self._parse_state == "done":
            return
        now = time.time()
        left = self._parse_until - now
        if self._parse_state == "countdown":
            if left <= 0:
                self._begin_parse(now)
            else:
                self._set_parse_banner(tr("PARSE DANS {n}", n=math.ceil(left)))
        elif self._parse_state == "parsing":
            if left <= 0:
                self._finish_parse()
            else:
                self._set_parse_banner(tr("PARSE  {n} s", n=math.ceil(left)))

    def _begin_bind_capture(self):
        """Listen for the next keypress and make it the reset bind.

        Polled with GetAsyncKeyState, not taken from the window: the game has
        the keyboard, and these are the same virtual-key codes the hook
        matches against."""
        if self._binding_now:
            self._end_bind_capture()
            return
        self._binding_now = True
        self._bind_refused = None
        self._bind_poll_job = None
        # the prompt is drawn by _bind_prompt
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
        # middle and side buttons only; 0x03 is Break, not a button
        for vk in list(VK_MOUSE) + list(range(0x08, 0xFF)):
            if vk in VK_UNBINDABLE or not down(vk):
                continue
            # A modifier is required except for F-keys and mouse buttons: the
            # hook swallows what it fires on, so a bare letter is lost in game.
            if (not (shift or ctrl or alt) and not (0x70 <= vk <= 0x87)
                    and vk not in VK_MOUSE):
                # keep listening, and say why nothing happened
                refused = bind_label({"vk": vk})
                if refused != self._bind_refused:
                    self._bind_refused = refused
                    self.menubridge.invalidate()
                break
            self._set_reset_bind({"vk": vk, "shift": shift, "ctrl": ctrl,
                                  "alt": alt})
            self._end_bind_capture()
            return
        self._bind_poll_job = self.root.after(40, self._poll_bind_capture)

    def _bind_prompt(self):
        if not self._binding_now:
            return bind_label()
        if getattr(self, "_bind_refused", None):
            return tr("{key} seul ne marche pas, ajoute Ctrl, Maj ou Alt",
                      key=self._bind_refused)
        return tr("appuie sur une touche…")

    def _end_bind_capture(self):
        self._binding_now = False
        self._bind_refused = None
        if getattr(self, "_bind_poll_job", None):
            try:
                self.root.after_cancel(self._bind_poll_job)
            except Exception:
                pass
            self._bind_poll_job = None
        # nothing to unbind: the capture is polled
        self.menubridge.invalidate()

    def _set_reset_bind(self, bind):
        RESET_BIND.update(bind)
        self._save_settings()
        # only the RegisterHotKey fallback needs telling; the low-level hook
        # reads RESET_BIND on every keypress
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
            # a rift is an instance of its own: everyone in it fights with
            # the player, its group as much as the party
            if self.ui_state.in_rift():
                return rows
            party = [p for p in rows if p.in_party]
            # no party known yet (solo, group not read): me alone
            return party if party else [p for p in rows if p.is_me]
        return rows

    def _merge_named(self, table):
        """Merge a skill-id table by display name (all weapons' "Attack" share
        one row): [(label, total, hits, crits, self)] by total desc.

        Damage rows are [hits, total, crits]; healing rows add the self-healed
        share, which damage reports as 0."""
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
        """Keep the previous encounter on screen until the next one starts:
        a reset (pull, wipe, zone change) empties the store just as the
        numbers become final. The last non-empty rows and duration are
        re-shown, headed LAST, until the next hit.

        Returns (rows, duration, holding).
        """
        if rows:
            self._held_rows = rows
            self._held_duration = duration
            return rows, duration, False
        if not self._held_rows:
            return rows, duration, False
        # A set held from all-players mode has strangers in it: in party mode
        # it is dropped whole, since its totals and percentages count them.
        if self.mode == "party" and not self.ui_state.in_rift() and any(
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

    @staticmethod
    def _help_articles():
        """Every article on disk in the interface's language (help/<lang>/,
        French in help/), parsed once per language and cached on the function
        (read-only assets, otherwise re-read on every refresh tick)."""
        lang = i18n.lang()
        cache = App._help_articles.__dict__.setdefault("_cache", {})
        if lang in cache:
            return cache[lang]
        out = []
        folder = HELP_DIR / lang if lang != i18n.SOURCE else HELP_DIR
        try:
            files = sorted(folder.glob("*.md")) or sorted(HELP_DIR.glob("*.md"))
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
        cache[lang] = out
        return out


    def _page_help(self):
        arts = self._help_articles()
        if not arts:
            return [{"k": "section", "t": tr("Aide")},
                    {"k": "note", "warn": True,
                     "t": tr("Les articles d'aide sont absents de cette "
                             "version.")}]
        # One article open: its text, and the way back.
        if self._help_open:
            art = next((a for a in arts if a["id"] == self._help_open), None)
            if art:
                return ([{"k": "button", "id": "help_close",
                          "t": tr("‹  Tous les sujets d'aide")},
                         {"k": "section", "t": art["title"]}]
                        + art["blocks"])
        # ...or the index: the repair, how it works, the links, the credits
        seen, out = set(), (self._repair_nodes() + self._about_nodes()
                            + self._links_nodes())
        for heading, ids in HELP_GROUPS:
            rows = [a for a in arts if a["id"] in ids]
            if not rows:
                continue
            seen.update(a["id"] for a in rows)
            out.append({"k": "section", "t": tr(heading)})
            out.append({"k": "list", "id": f"help:{heading}", "rows": [
                {"t": a["title"], "meta": a["blurb"],
                 "btns": [{"id": "help_open", "t": tr("Lire"),
                           "p": {"id": a["id"]}}]}
                for a in rows]})
        rest = [a for a in arts if a["id"] not in seen] if HELP_GROUPS \
            else []
        if rest:
            out.append({"k": "section", "t": tr("Autres")})
            out.append({"k": "list", "id": "help:more", "rows": [
                {"t": a["title"], "meta": a["blurb"],
                 "btns": [{"id": "help_open", "t": tr("Lire"),
                           "p": {"id": a["id"]}}]}
                for a in rest]})
        return out + self._credits_nodes()

    @staticmethod
    def _about_nodes():
        return [{"k": "section", "t": tr("Fonctionnement")},
                {"k": "prose",
                 "t": tr("Farever Book fonctionne uniquement en lecture : "
                         "rien n'est jamais écrit ni modifié dans le jeu, et "
                         "rien n'y est affiché.")},
                {"k": "prose",
                 "t": tr("L'application se contente de lire le contenu du "
                         "jeu et ce que tu fais pendant tes sessions "
                         "(combats, butin, progression), pour te le révéler "
                         "et le mettre en forme ici.")}]

    @staticmethod
    def _links_nodes():
        return [{"k": "section", "t": tr("Liens utiles")},
                {"k": "linkcards", "id": "help:links", "items": [
                    {"id": ln["id"], "t": ln["t"], "meta": tr(ln["meta"]),
                     "open": bool(ln["url"])} for ln in HELP_LINKS]}]

    @staticmethod
    def _credits_nodes():
        return [{"k": "section", "t": tr("Crédits")},
                {"k": "prose",
                 "t": tr("Farever Book est un projet de fan, gratuit, autour "
                         "du jeu Farever. Il n'est ni affilié à Shiro Games, "
                         "ni approuvé par le studio. Farever et ses contenus "
                         "appartiennent à Shiro Games.")},
                {"k": "prose",
                 "t": tr("Ce projet existe en partie grâce à Brudr, auteur "
                         "du mod Farever+, qui a généreusement partagé son "
                         "code. Merci à lui !")}] + App._contributor_nodes()

    @staticmethod
    def _contributor_nodes():
        """Who supports the project on Tipeee (contributors.py): hidden
        while nobody does."""
        if not CONTRIBUTORS:
            return []
        return [{"k": "section", "t": tr("Contributeurs")},
                {"k": "prose",
                 "t": tr("Merci à celles et ceux qui soutiennent Farever Book "
                         "sur Tipeee :") + " " + ", ".join(CONTRIBUTORS)}]


    def _repair_nodes(self):
        return [{"k": "section", "t": tr("Un problème ?")},
                {"k": "note",
                 "t": tr("Farever Book relit les données du jeu tout seul "
                         "quand Farever change. Si une page reste vide, que "
                         "des images manquent ou que la connexion au jeu "
                         "échoue, Réparer réanalyse le jeu depuis zéro puis "
                         "se reconnecte, sans relancer l'application. Une "
                         "trentaine de secondes.")},
                {"k": "button", "id": "repair_data", "t": tr("Réparer"),
                 "tone": "go"},
                {"k": "note",
                 "t": tr("La connexion au jeu échoue toujours ? C'est "
                         "souvent l'antivirus : dans Sécurité Windows › "
                         "Protection contre les virus et menaces › Historique "
                         "de protection, autorise Farever Book s'il y "
                         "apparaît. Smart App Control (Sécurité Windows › "
                         "Contrôle des applications) bloque aussi la "
                         "lecture du jeu quand il est activé. Si Farever est "
                         "lancé en administrateur, lance Farever Book en "
                         "administrateur lui aussi.")},
                {"k": "note",
                 "t": tr("Rien n'y fait ? Crée un rapport et envoie-le sur "
                         "le Discord : il rassemble ce qui aide à trouver la "
                         "cause (version de Windows, antivirus, erreur, "
                         "journal), sans ton nom Windows ni les pseudos des "
                         "joueurs.")},
                {"k": "button", "id": "make_report",
                 "t": tr("Créer un rapport"), "tone": "go soft"}]

    @staticmethod
    def _tick(on, label):
        """A setting's button label, showing its state."""
        return ("☑  " if on else "☐  ") + label


    def _enqueue(self, fn):
        """Wrap `fn` so it runs on the app's loop at the next refresh; the
        way in for every other thread."""
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
        if self._parse_state is not None:
            self._stop_parse()
        self.menubridge.invalidate()

    def _link_spec(self):
        """The title band's game state: "play" (launch button), "launching",
        "connecting", "ingame" or "failed"."""
        if self.link is None:
            return {"state": "ingame", "t": tr("En jeu")}
        state, detail, _pid = self.link.status()
        if state == GameLink.CONNECTED:
            self._launching_until = 0
            return {"state": "ingame", "t": tr("En jeu")}
        if REGENERATING.is_set() and not self._setup:
            return {"state": "connecting", "t": tr("Mise à jour…"),
                    "tip": tr("lecture des données du jeu (images, icônes, "
                              "modèles), une trentaine de secondes")}
        if state == GameLink.CONNECTING:
            if REGENERATING.is_set():
                return {"state": "connecting", "t": tr("Mise à jour…"),
                        "tip": tr("relecture des données du jeu")}
            return {"state": "connecting", "t": tr("Connexion…"),
                    "tip": tr("au jeu en cours")}
        if state == GameLink.FAILED:
            return {"state": "failed",
                    "tip": tr("Connexion à Farever impossible : {detail}",
                              detail=detail)}
        if time.time() < self._launching_until:
            return {"state": "launching", "t": tr("Lancement…"),
                    "tip": tr("Farever démarre")}
        return {"state": "play", "tip": tr("Lancer Farever (via Steam)")}

    def _launch_game(self):
        """Launch Farever through Steam (it handles the login and updates),
        then look for it straight away."""
        try:
            os.startfile(f"steam://rungameid/{FAREVER_STEAM_APPID}")
        except OSError as e:
            self._toast_msg(tr("Impossible de lancer Farever : {e}", e=e))
            return
        self._launching_until = time.time() + 120
        if self.link is not None:
            self.link.retry()
        self.menubridge.invalidate()

    def _link_clicked(self):
        if self.link is not None and not self.game_connected():
            self.link.retry()

    def _manual_reset(self):
        """A reset the PLAYER asked for (hotkey or button): unlike the
        automatic ones (zone, pull, view switch), it shows a toast."""
        self.session.reset()
        self._show_reset_toast()

