"""The application: state, actions, and the pages it pushes to the window."""
from __future__ import annotations

import ctypes
import json
import math
import os
import re
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from buildtab import BuildTab

from common import (
    ACH_FILE, ANALYSIS, APP_TABS, APP_TABS_APP_FIRST, APP_TAB_DEFAULT,
    APP_TAB_LABELS,
    BEST_TIMES_CACHE, CODEX_FILE, COLLECTION_FILE, DATA_HOME, DUNGEONS_DIR,
    ELEMENTS_FILE, EVENTS_MAX, FAREVER_STEAM_APPID, HELP_DIR, HELP_GROUPS,
    ITEM_CODEX_FILE, LOG_FILE, MAX_PLAYER_ROWS, MAX_SKILL_ROWS,
    PARSE_LENGTH_SECS, PARSE_PREROLL_SECS, POSITION_CACHE, REFRESH_MS,
    RIFTS_DIR, RIFT_PORTAL_SECS, RIFT_STYLE_SECS, SETTINGS_CACHE, VERSION,
    _class_tag, _mmss, _n, _pct1, _pretty_id, class_key, date_fr,
    element_color, element_label, message_box, phase_label)
from winsys import (
    HK_RESET, REBIND_TO, RESET_BIND, VK_CONTROL, VK_MENU, VK_MOUSE, VK_SHIFT,
    VK_UNBINDABLE, WM_REBIND, _monitor_containing, bind_label,
    copy_image_to_clipboard, copy_text_to_clipboard, foreground_pid,
    game_window, quit_requested, start_hotkeys)
import goals as G
from me import MeStore
from gamedata import (
    REGENERATING, _boss_label, _element_done, _fr_names, bestiary_catalogue,
    dungeon_catalogue,
    item_rarity,
    dungeon_name, forget_loaded_data, item_icon, item_label, item_type,
    item_model_json, locate_hlboot, regenerate_data, world_map)
from combat import (
    DUNGEON_DIFFICULTIES, GameUIState, PartySession, WorldSnapshot,
    _overheal_note, _rate_text, _report_name)
from views import (
    RIFT_STAT_ICONS, RIFT_STAT_LABELS, _pct, _profile_luck, _profile_stats, achievements_view,
    bestiary_view, character_view, collection_view, droptable_view,
    hunt_detail_view, map_view, rift_rewards_view)
from reports import render_rift_report_image, report_view
from bridge import MenuBridge, _Scheduler, _parse_help
from gamelink import GameLink



# Runs kept per dungeon: the 11th removes the oldest (records stay).
DUNGEON_RUNS_KEPT = 10


# The overlays: the grid their distance to the edge snaps to, and how long
# after the hook last saw our hero they stay up (it reports every 3 s).
OVERLAY_GRID = 8
OVERLAY_HERO_SECS = 8.0


# How often one's own character is re-read while playing (kept on disk).
SELF_PROFILE_SECS = 300.0

MONTHS_FR = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre")


def _dungeon_backdrop(kind, boss, region=""):
    """The picture behind a dungeon's card: the loading screen the game shows
    for it, else the one named after its boss that no instance claims (the
    cheese station's Munster_Chuck), else its region's illustration in the
    game's codex (the Gorgon's Hollow, in Z2: loading_screen4); "" when
    there is none."""
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
        self.root = _Scheduler()            # after()/after_cancel()/quit()
        self.menubridge = MenuBridge(self)
        self.buildtab = BuildTab(self.menubridge.invalidate, self._toast_msg)
        # the overlays over the game: the group meter and the goals
        self.goals = G.Goals()
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
        self._ov_on = {"meter": True, "goals": True}   # shown, per overlay
        self._hero_at = 0.0                 # last time the hook saw our hero
        self._action_q = []
        self._q_lock = threading.Lock()
        self._stopping = False
        self._ui_seen = False               # the window has been up once

        # ---- what the player chose (saved) ----
        self.mode = "party"                 # "party" (group only) or "all"
        self._show_heal = True
        self._sort_heal = False
        self._auto_reset_boss = False
        self._rift_keep = 0                 # rift reports kept (0: all)
        self._rift_sel = set()              # rift reports ticked for deletion
        self._rift_confirm = False          # "delete" pressed once
        self._rift_auto_view = False
        self._zoom = 130                    # the window's own size, percent

        # ---- what the window is showing (not saved) ----
        self._menu_tab = APP_TAB_DEFAULT
        self.focus_player = None            # player picked in the meter
        self._help_open = None
        self._rift_view = None              # the rift report being read
        self._launching_until = 0           # Play was clicked: until then
        self._repairing = False             # Réparer is running
        self._self_prof = None              # own luck counters (hook, 1 min)
        self._repair_note = None            # (ok, text) once it has run
        # a rift's gates: the game's running count when it began, and the
        # report waiting for the count after it (see _rift_gates_seen)
        self._rift_gates0 = None
        self._rift_gates_for = None
        self._dungeon_kind = None           # the dungeon whose runs are listed
        self._hunt_sel = None               # the monster whose page is open
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
        self._me_build = None               # (name, since): own build asked
        self._char_wait = None              # (name, since) of an analysis
        self._elements_data = None          # .meter_elements.json, loaded
        self._dungeon_cache = {}            # file name -> (mtime, data)
        self._binding_now = False
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
        if isinstance(data.get("rift_keep"), int) and data["rift_keep"] >= 0:
            self._rift_keep = data["rift_keep"]
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
        pos = data.get("overlay_pos")
        if isinstance(pos, dict):
            for k, v in pos.items():
                if isinstance(v, dict) and v.get("ax") in ("l", "r") \
                        and v.get("ay") in ("t", "b"):
                    self._ov_pos[k] = {"ax": v["ax"], "ay": v["ay"],
                                       "dx": int(v.get("dx") or 0),
                                       "dy": int(v.get("dy") or 0)}
                elif isinstance(v, list) and len(v) == 2:   # the first form
                    self._ov_pos[k] = {"ax": "l", "ay": "t",
                                       "dx": int(v[0]), "dy": int(v[1])}
        on = data.get("overlay_on")
        if isinstance(on, dict):
            for k in self._ov_on:
                if isinstance(on.get(k), bool):
                    self._ov_on[k] = on[k]
        if data.get("overlay_tab") in ("dmg", "heal"):
            self._ov_tab = data["overlay_tab"]

    def _save_settings(self):
        try:
            SETTINGS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_CACHE.write_text(json.dumps({
                "mode": self.mode,
                "show_heal": bool(self._show_heal),
                "sort_heal": bool(self._sort_heal),
                "auto_reset_boss": bool(self._auto_reset_boss),
                "rift_auto_view": bool(self._rift_auto_view),
                "rift_keep": int(self._rift_keep),
                "reset_bind": dict(RESET_BIND),
                "zoom": int(self._zoom),
                "overlay_pos": self._ov_pos,
                "overlay_on": self._ov_on,
                "overlay_tab": self._ov_tab,
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
        self._event("Combat réinitialisé.")

    def _set_parse_banner(self, text):
        """The parse's state, shown on the live page's cards."""
        self._parse_text = text

    def _hide_parse_banner(self):
        self._parse_text = ""


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
                self._event("Connecté à Farever.", "ok")
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
            who = f" — MVP {best[0]['name']}" if best else ""
            seen = (" (victoire non vue : rapport à la fin de la faille)"
                    if report.get("unconfirmed") else "")
            self._event(f"Faille terminée{who}{seen}.", "rift",
                        {"id": "open_last_rift", "t": "Voir le rapport"})
        self._enqueue(done)()

    def on_rift_dropped(self, why):
        """A rift recording given up: said, so a missing report is never
        silent. Called from the hook thread."""
        self._enqueue(lambda: self._event(
            f"Faille non enregistrée : {why}.", "warn"))()

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
        if in_rift:
            # the game's count of gates closed, read now: the rift's own
            # gates are what it has grown by when the boss dies
            self._rift_gates0 = "wait"
            self._rift_gates_for = None
            self._me_auto_next = 0.0
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
        if name == "Build" and self._menu_tab != "Build":
            self.buildtab.close()       # the tab opens on the list of builds
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
            "copy_rift_image": self._copy_rift_image,
            "open_parses": self._open_parses,
            "rift_tick": lambda p: self._rift_tick(p.get("file")),
            "rift_tick_all": self._rift_tick_all,
            "rift_delete": self._rift_delete,
            "rift_delete_cancel": self._rift_delete_cancel,
            "set_rift_keep": lambda p: self._set_rift_keep(p.get("value")),
            # dungeons
            "open_dungeon_kind": lambda p: setattr(
                self, "_dungeon_kind", p.get("kind")),
            "close_dungeon_kind": lambda: setattr(self, "_dungeon_kind", None),
            "open_dungeon_run": lambda p: self._open_dungeon_run(
                p.get("file", "")),
            "close_dungeon_run": lambda: setattr(self, "_dungeon_view", None),
            # collection
            "coll_model": lambda p: self._coll_model(p.get("id")),
            # hunting log
            "hunt_open": lambda p: setattr(self, "_hunt_sel", p.get("id")),
            "hunt_close": lambda: setattr(self, "_hunt_sel", None),
            # character
            "char_analyze": lambda p: self._analyze(p.get("name")),
            "char_open": lambda p: setattr(self, "_char_sel", p.get("name")),
            "char_close": lambda: setattr(self, "_char_sel", None),
            "char_forget": lambda p: self._forget_profile(p.get("name")),
            "char_to_build": lambda p: self._profile_to_build(p.get("name")),
            "build_from_me": self._build_from_me,
            # overlays
            "ov_tab": lambda p: self._ov_set_tab(p.get("tab")),
            "ov_moved": self._ov_moved,
            "ov_toggle_meter": lambda: self._ov_toggle("meter"),
            "ov_toggle_goals": lambda: self._ov_toggle("goals"),
            "ov_reset": self._ov_reset,
            "goal_search": lambda p: G.search(p.get("q")),
            "goal_add": self._goal_add,
            "goal_del": lambda p: self.goals.remove(p.get("id")),
            "goal_n": lambda p: self.goals.set_n(p.get("id"), p.get("n")),
            "goal_clear_done": self.goals.clear_done,
            # settings
            "toggle_heal": self._toggle_heal,
            "toggle_rift_auto_view": self._toggle_rift_auto_view,
            "toggle_auto_reset": self._toggle_auto_reset_boss,
            "begin_bind": self._begin_bind_capture,
            "set_zoom": lambda p: self._set_zoom(p.get("value", 130)),
            "open_log": self._open_log_folder,
            # help
            "help_open": lambda p: setattr(self, "_help_open", p.get("id")),
            "help_close": lambda: setattr(self, "_help_open", None),
            "repair_data": self._repair,
        }
        acts.update(self.buildtab.actions())
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
                   "Build": self._page_build,
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
        if me is None:
            # the game closed: the last counters read, if any
            counters, at = self.me.got("counters")
            me = dict(counters, at=at) if isinstance(counters, dict) else None
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
        self._toast_msg(f"{gone} faille{'s' if gone > 1 else ''} "
                        f"supprimée{'s' if gone > 1 else ''}.")

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
        out = {"day": f"{at.tm_mday} {MONTHS_FR[at.tm_mon - 1]} {at.tm_year}",
               "time": time.strftime("%H:%M", at),
               "dur": _mmss(dur), "players": len(players),
               "gates": int(gates) if isinstance(gates, (int, float)) else None}
        self._rift_cache[path.name] = (mtime, out)
        return out

    def _page_rifts(self):
        if self._rift_view is not None:
            return [{"k": "toolbar", "id": "rift_tools", "btns": [
                        {"id": "close_rift", "t": "‹  Toutes les failles"},
                        {"id": "copy_rift_image", "t": "Copier l'image"}]},
                    self._report_node(self._rift_view)]
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
        bottom = [{"id": "open_parses", "t": "Ouvrir le dossier des rapports"},
                  {"id": "rift_tick_all",
                   "t": "Tout désélectionner" if all_on else "Tout sélectionner",
                   "tone": None if names else "disabled"}]
        if self._rift_confirm and n:
            bottom += [{"id": "rift_delete", "tone": "warn armed",
                        "t": f"Confirmer la suppression ({n})"},
                       {"id": "rift_delete_cancel", "t": "Annuler"}]
        else:
            bottom.append({"id": "rift_delete",
                           "tone": "warn" if n else "disabled",
                           "t": f"Supprimer la sélection ({n})" if n
                           else "Supprimer la sélection"})
        keep = [0, 10, 20, 30, 50, 100]
        if self._rift_keep not in keep:
            keep = sorted(keep + [self._rift_keep])
        data = self._achievements()
        entry = (data.get("heroes") or {}).get(data.get("last")) or {}
        return [
            *self._rift_stat_cards(),
            *rift_rewards_view(entry.get("counters") or {},
                               entry.get("luckUntil") or {}),
            {"k": "section", "t": "Failles réalisées"},
            {"k": "note", "t": "Chaque faille terminée (boss vaincu) est "
                               "enregistrée ici avec son classement complet. "
                               "Clique sur une faille pour la relire."},
            {"k": "riftcards", "id": "rifts", "groups": groups,
             "empty": "Aucune faille enregistrée pour l'instant."},
            {"k": "toolbar", "id": "rift_bottom", "btns": bottom},
            {"k": "field", "t": "Failles conservées",
             "c": {"k": "select", "id": "set_rift_keep",
                   "v": str(self._rift_keep),
                   "o": [{"v": str(v), "t": f"Les {v} dernières" if v
                          else "Toutes"} for v in keep]}},
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
        items = [{"title": label, "value": _n(counters[k]), "sub": "",
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

    # ---- the overlays
    def on_stock(self, p):
        """What the hero owns changed (bag, equipment, bank). Hook thread."""
        def done():
            self.me.put(self._me_name, "stock",
                        {"items": p.get("items") or {},
                         "banks": p.get("banks")})
            for g in self.goals.on_stock(p.get("items"), p.get("banks")):
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
        self._event(f"Objectif atteint : {g['n']} × {self.goals.label(g)}.",
                    "ok")

    def _goal_add(self, p):
        self.goals.add(p.get("kind"), p.get("ref"), p.get("n"))

    def on_hero_seen(self, name, uid=None, acct=None):
        """The hook saw our hero (every 3 s in the world). Hook thread."""
        self._hero_at = time.time()

        def done():
            self.me.set_account(uid, acct)
            self.me.set_hero(name)
            if name != self._me_name:
                self._me_name = name
                self._me_auto_next = 0.0    # a new character: read it now
        self._enqueue(done)()

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

    def _ov_toggle(self, oid):
        if oid in self._ov_on:
            self._ov_on[oid] = not self._ov_on[oid]
            self._save_settings()

    def _ov_reset(self):
        self._ov_pos = {}
        self._save_settings()
        self._toast_msg("Overlays remis à leur place par défaut.")

    def _ov_set_tab(self, tab):
        if tab in ("dmg", "heal"):
            self._ov_tab = tab
            self._save_settings()

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
        spec = {"show": show, "game": list(win[1]) if win else None,
                "pos": self._ov_pos, "on": dict(self._ov_on)}
        if not show:
            return spec
        rows, duration, _holding, in_combat = self._live
        group = [p for p in rows if p.in_party] or [p for p in rows
                                                    if p.is_me]
        heal = self._ov_tab == "heal"
        group.sort(key=lambda p: -(p.heal_total if heal else p.total))
        top = max(((p.heal_total if heal else p.total) for p in group),
                  default=0.0) or 1.0
        total = sum((p.heal_total if heal else p.total) for p in group)
        spec["meter"] = {
            "tab": self._ov_tab,
            "time": _mmss(duration) if duration > 0 else "",
            "fight": bool(in_combat),
            "total": _n(total) if total else "",
            "rows": [{"n": p.name, "me": bool(p.is_me),
                      "ck": class_key(self.world.class_of(p.name)),
                      "v": _n(p.heal_total if heal else p.total),
                      "ps": (_n((p.heal_total if heal else p.total) / duration)
                             if duration > 0 else ""),
                      "f": round((p.heal_total if heal else p.total) / top, 4)}
                     for p in group[:8]
                     if (p.heal_total if heal else p.total) > 0]}
        spec["goals"] = self.goals.view()
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
        if self._hunt_sel:
            return [{"k": "toolbar", "id": "hunt_tools", "btns": [
                        {"id": "hunt_close", "t": "‹  Tableau de chasse"}]},
                    {"k": "huntmon", "id": "huntmon",
                     **hunt_detail_view(self._hunt_sel,
                                        entry.get("ranks") or {})}]
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
                self._rift_gates_seen((p.get("profile") or {}).get("counters"))
                self.me.put(self._me_name, "counters",
                            p.get("profile") or {})
                return
            if p.get("kind") == "roster":
                self._roster = p.get("players") or []
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
                    self._toast_msg("Ton personnage n'a pas pu être lu.")
                    return
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
            if name == self._me_name:
                # one's own character: kept on disk, for the game closed
                self.me.put(name, "profile", p.get("profile") or {})
                if auto:
                    return              # a quiet refresh, nothing to open
            # others: kept for this session only, never written to disk
            self._profiles_data()[name] = prof
            if mine:
                # asked from the Build tab: straight into a build, named
                # after the character
                self._me_build = None
                self._set_tab("Build")
                self.buildtab.import_profile(prof, name)
                return
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
                self._toast_msg("Ton personnage n'a pas encore été lu : lance "
                                "le jeu une fois avec Farever France ouvert.")
                return
            self._set_tab("Build")
            self.buildtab.import_profile(prof, self.me.last)
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
                           {"n": self.me.last, "lvl": saved.get("lvl"),
                            "wait": False,
                            "when": date_fr(time.localtime(saved["at"]))}
                           if saved else None)
        return nodes

    def _profile_to_build(self, name):
        """Inspecter's "Créer un build": the analysed player as a build,
        opened in the Build tab."""
        prof = self._profiles_data().get(name)
        if prof:
            self._set_tab("Build")
            self.buildtab.import_profile(prof)

    def _page_character(self):
        profs = self._profiles_data()
        live = self.game_connected() and time.time() - self._roster_at < 30
        roster = self._roster if live else []
        wait = self._char_wait
        if wait and time.time() - wait[1] > 15:
            self._char_wait = wait = None
        # one's own characters as last read, under any analysed this session
        profs = {**self.me.profiles(), **profs}
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

    def _coll_model(self, item_id):
        """The viewer asked for a collectible's 3D model: built (or read from
        the cache) off the Tk thread, and sent on its own channel — half a
        megabyte that has no business riding along in every state push."""
        def work():
            d = item_model_json(item_id)
            self.menubridge.send({"t": "model", "id": item_id, "d": d})
        threading.Thread(target=work, daemon=True).start()

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
            self._prune_dungeon_runs(report.get("kind"), keep_name=name)
            return name
        except OSError as e:
            print(f"[meter] couldn't save the dungeon run: {e}",
                  file=sys.stderr)
            return None

    def _prune_dungeon_runs(self, kind, keep_name=None):
        """At most DUNGEON_RUNS_KEPT runs per dungeon: past that the oldest
        go. A run holding a difficulty's record stays, or the record would
        go with it; the run just saved stays too."""
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
                        {"id": "copy_rift_image", "t": "Copier l'image"}]},
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
                cards.append({"title": label, "art": f"dungeon_diff_{diff}",
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
            # what the runs brought back, per difficulty
            got = {k: {} for k in DUNGEON_DIFFICULTIES}
            for _name, d in mine:
                g = got.setdefault(d.get("difficulty"), {})
                for it in d.get("loot") or ():
                    g[it.get("item")] = (g.get(it.get("item"), 0)
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
                         "Vétéran une rare, tirée à parts égales parmi "
                         "celles que sa classe peut porter (les 2 dernières "
                         "reçues sont écartées) ; en Héroïque une épique, "
                         "parmi celles du boss pour sa classe. À 3 joueurs "
                         "une pièce de plus est donnée au hasard, 2 à 4 "
                         "joueurs. Chaque pièce a 10 % de chances d'être "
                         "prismatique (15 % avec l'offrande du Puits des "
                         "âmes). La mort du boss peut en plus donner un objet "
                         "rare et, en Héroïque, donne toujours le patron "
                         "d'imprégnation du donjon. « Obtenu » compte ce que "
                         "tes runs ont rapporté dans cette difficulté."},
                        {"k": "droptable", "id": "dungeon_drops",
                         "tables": [{"d": k, "t": label,
                                     "rows": droptable_view(dg, got[k], k)}
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
            recs = [{"d": diff, "t": label, "v": _mmss(best)}
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
                        "tip": f"{item_label(e['item'])} — {_pct(e['chance'])}"
                               " (coffre de fin)"}
                       for e in loot if e.get("src") == "coffre"
                       and e.get("type") != "UpgradeComponent"]
            rides = [{"img": item_icon(e["item"]), "rk": rk(e),
                      "tip": f"{item_label(e['item'])} — {_pct(e['chance'])}"}
                     for e in loot if e.get("src") == "boss"
                     and e.get("type") in ("GearGlider", "Mount")]

            def armour(src, what):
                a = [e for e in loot if e.get("src") == src]
                return [{"img": item_icon(a[0]["item"]), "n": len(a),
                         "rk": rk(a[0]),
                         "tip": f"Armure de faction {what} : {len(a)} pièces, "
                                "une garantie par joueur parmi celles de sa "
                                "classe"}] if a else []
            infusion = [{"img": item_icon(e["item"]), "rk": rk(e),
                         "tip": f"{item_label(e['item'])} — garanti"}
                        for e in loot if e.get("type") == "InfusionPattern"]
            tiers = [
                {"d": 0, "t": "Normal", "icons": weapons
                 + armour("faction", "rare") + rides},
                {"d": 1, "t": "Vétéran", "sub": "niveau max",
                 "icons": weapons + armour("faction", "rare") + rides},
                {"d": 2, "t": "Héroïque", "icons": weapons
                 + armour("heroic", "épique") + infusion + rides}]
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
        out = [{"k": "section", "t": "Donjons"},
               {"k": "note", "t": "Chaque donjon est enregistré "
                                  "automatiquement de l'entrée à la sortie : "
                                  "difficulté, temps (celui du jeu), morts, "
                                  "groupe, classement complet en deux "
                                  "phases — exploration et boss — et butin. "
                                  "Les échecs et abandons sont gardés "
                                  "aussi."}]
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
                or ("Autres donjons" if regions.keys() - {""} else "")
            if title:
                out.append({"k": "sub", "t": title})
            out.append({"k": "dcards", "id": f"dungeons_{i}",
                        "cards": [row(dg["kind"], dg.get("boss"))
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
            {"k": "section", "t": "Overlay en jeu"},
            {"k": "button", "id": "ov_toggle_meter",
             "t": self._tick(self._ov_on["meter"], "Compteur du groupe")},
            {"k": "button", "id": "ov_toggle_goals",
             "t": self._tick(self._ov_on["goals"], "Objectifs")},
            {"k": "button", "id": "ov_reset",
             "t": "Remettre les overlays à leur place par défaut"},
            {"k": "note", "t": "Les overlays s'affichent par-dessus le jeu, "
                               "seulement sur un personnage (pas dans les "
                               "menus) et quand Farever est au premier plan. "
                               "Déplace-les en tirant leur en-tête : chacun "
                               "s'accroche au bord le plus proche de l'écran "
                               f"(grille de {OVERLAY_GRID} px) et garde cette "
                               "distance si la taille du jeu change."},
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
             "t": "Dossier des rapports de faille"},
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
        """The report into failles/, three ways: .json is the full metrics —
        the file _load_last_rift_report reads back, which is what lets 'Last
        Rift Report' survive a meter restart; .txt is the chat-pasteable
        lines; .png is the shareable image. Same folder, same lifecycle as
        the parse screenshots. Never fatal, and each format fails alone: no
        Pillow costs the picture, not the data."""
        base = f"rift-{time.strftime('%Y%m%d-%H%M%S')}"
        saved = None
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
        """The newest saved rift report, or None — how a fresh session still
        has a 'Last Rift Report'. Timestamped filenames sort lexicographically,
        so newest is just last. Validated for shape, not trusted: a truncated
        or hand-edited file costs the button, never the meter."""
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
        moves the UI into its 'sample is sitting there to be read' state
        (nothing is saved: the result is read on screen)."""
        self._parse_state = "done"
        self._set_parse_banner(f"PARSE TERMINÉ  {PARSE_LENGTH_SECS} s")

    def _open_parses(self):
        """Open the rift reports' folder in Explorer. Created on demand, so
        the button does something sensible before the first rift has been
        saved rather than failing on a folder that doesn't exist yet."""
        try:
            RIFTS_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(RIFTS_DIR)
        except Exception as e:
            print(f"[meter] couldn't open {RIFTS_DIR}: {e}", file=sys.stderr)

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
        # The hook died with the game: what it said about the game's UI is
        # stale.
        self.ui_state.clear()
        # A parse whose data source just died is not a sample of anything.
        if self._parse_state is not None:
            self._stop_parse()
        self.menubridge.invalidate()

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

