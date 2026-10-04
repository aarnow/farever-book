"""The connection to Farever: GameLink keeps it alive in the background,
_game_session runs one attach of the hook."""
from __future__ import annotations

import sys
import threading
import time
import frida

from common import (
    BOSS_PULL_BACKLAG_SECS, NULLIFIED_BLOCKERS, STOP, TARGET_PROCESS, _OVERLAY,
    _mmss)
from winsys import _process_age, _window_rect_of_pid
from gamedata import (
    DATA_CONSENT, _boss_label, _zone_label, build_script_source,
    locate_hlboot, regenerate_data)
from combat import DungeonTracker, _stamp_report_classes


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
    """One connection to one running Farever (see GameSession)."""
    return GameSession(link, device, proc, session, ui_state, world,
                       rift_rec, heal_sizer).run()


def _overlay():
    """The app's window, once it exists."""
    return _OVERLAY["ref"]


class GameSession:
    """One connection to one running Farever: the data files checked against
    the running build, the attach, the hook brought up, then its messages
    fed to the meter until the game closes or the meter stops. Runs on
    GameLink's thread, telling the window each step through GameLink."""

    TARGET_LOG_MAX = 40         # distinct hit targets named in the log
    REPORT_EVERY = 30.0         # seconds between the diagnostics' tallies
    ATTEMPTS = 3                # hook bring-ups before giving up
    BOOTING_GRACE = 120.0       # a game still booting is retried meanwhile

    # Messages the window takes as they come: kind -> its handler there.
    TO_OVERLAY = {"stock": "on_stock",
                  "collection": "on_collection", "codex": "on_codex",
                  "itemcodex": "on_item_codex",
                  "achievements": "on_achievements",
                  "roster": "on_character", "profile": "on_character",
                  "selfprofile": "on_character", "elements": "on_elements"}

    def __init__(self, link, device, proc, session, ui_state, world,
                 rift_rec, heal_sizer):
        self.link, self.device, self.pid = link, device, proc.pid
        self.session, self.ui_state, self.world = session, ui_state, world
        self.rift_rec, self.heal_sizer = rift_rec, heal_sizer
        self.dungeon = DungeonTracker(world)
        self.hlboot = None
        self.fsession = None
        self.script = None
        # set when frida's session dies: the game closed or crashed
        self.detached = threading.Event()
        self.ready = {"ok": None, "early": False}
        self.ready_evt = threading.Event()
        self.last_message = time.monotonic()     # any traffic is life
        self.progress_printed = 0.0
        self.hero_name = None       # the last local hero (a quiet log)
        self.zone_seen = False      # the first zone report came in
        # A boss fight under way: started on the pull edge, ended by a
        # kill of the last boss bar, a loading screen, or the bars staying
        # down (bossgone). Its clock is read on the kill; `kinds` keys
        # the record.
        self.boss_fight_on = False
        self.boss_t0 = None
        self.boss_kinds = ()
        # diagnostics, from ordinary play (see their handlers)
        self.nullified = {}
        self.nullified_at = 0.0
        self.heal_log_at = 0.0
        self.pet_seen = set()
        self.target_seen = set()
        self.handlers = {
            "hit": self._on_hit, "heal": self._on_heal,
            "combat": self._on_combat, "rift": self._on_rift,
            "bossbar": self._on_bossbar, "bossgone": self._on_bossgone,
            "zone": self._on_zone, "server": self._on_server,
            "hero": self._on_hero, "pickup": self._on_pickup,
            "dungeon": lambda p: self.dungeon.update(p.get("d") or {}),
            "shard": lambda p: self.world.set_shard(p.get("list") or []),
            "log": self._on_log, "progress": self._on_progress,
            "ready": self._on_ready,
        }

    # ---- the session ----------------------------------------------------
    def run(self):
        """True once connected then disconnected; False (or None, stopped
        during the hook's start) when it never connected."""
        if not self._sync_data():
            return False
        if not self._attach():
            return False
        started = self._start_hook()
        if STOP.is_set() or self.detached.is_set():
            # Stopped from the tray during startup, or the game closed: a
            # half-loaded hook left attached destabilises the game.
            print("[meter] connection abandoned during startup.",
                  file=sys.stderr)
            if self.script is not None and not self.detached.is_set():
                _unload_hook(self.script)
            self._detach()
            return None
        if not started:
            print("[meter] could not initialise the hook after 3 attempts.\n"
                  "        Fully close Farever and reopen it; the meter "
                  "reconnects on its own.\n"
                  "        (Avoid repeatedly relaunching against a stuck "
                  "session — that can crash the game.)", file=sys.stderr)
            self._detach()
            self.link.step("scan", "fail", "échec après 3 tentatives")
            self.link.set_state(GameLink.FAILED,
                                "le compteur n'a pas pu se brancher sur le "
                                "jeu — ferme complètement Farever et "
                                "relance-le")
            return False
        self._connected()
        return True

    def _sync_data(self):
        """The data files matched to the build actually running (its own
        hlboot.dat), once the player has agreed to it (the welcome
        screen). Skipped when the file is unchanged."""
        link = self.link
        link.step("data", "run", "comparaison avec la version installée")
        while not DATA_CONSENT.wait(0.5):
            if STOP.is_set():
                return False
        self.hlboot = locate_hlboot(self.pid)
        if self.hlboot is None:
            print("[meter] using the shipped data files as-is (couldn't "
                  "locate hlboot.dat to verify them).", file=sys.stderr)
        else:
            print(f"[*] game data: {self.hlboot}", file=sys.stderr)
            regenerate_data(self.hlboot, on_step=lambda t: link.step(
                "data", detail=f"mise à jour du jeu détectée : relecture "
                               f"({t})"))
        link.step("data", "ok", "à jour")
        return True

    def _attach(self):
        link, pid = self.link, self.pid
        print(f"[*] attaching to {TARGET_PROCESS} (pid {pid}) ...",
              file=sys.stderr)
        link.step("attach", "run", "recherche du jeu en cours d'utilisation")
        try:
            self.fsession = self.device.attach(pid)
        except frida.ProcessNotFoundError:
            print(f"[meter] {TARGET_PROCESS} (pid {pid}) closed before "
                  "attach.", file=sys.stderr)
            return False
        except frida.PermissionDeniedError:
            link.set_state(GameLink.FAILED,
                           "connexion refusée — si Farever tourne en "
                           "administrateur, lance aussi le compteur en "
                           "administrateur")
            return False
        except Exception as e:
            print(f"[meter] attach to pid {pid} failed: {e}", file=sys.stderr)
            # A game shutting down is still listed for a few seconds and
            # refuses new threads (STATUS_PROCESS_IS_TERMINATING): "closing"
            # only if it really is gone shortly after.
            for _ in range(8):
                if not link.game_alive(self.device, pid):
                    print(f"[meter] {TARGET_PROCESS} (pid {pid}) closed — "
                          "back to waiting for it.", file=sys.stderr)
                    return False
                if STOP.wait(0.5):
                    return False
            link.set_state(GameLink.FAILED, f"connexion impossible : {e}")
            return False

        def on_detached(*args):
            reason = str(args[0]) if args else ""
            print(f"[meter] game session detached ({reason or 'unknown'}).",
                  file=sys.stderr)
            self.detached.set()
        self.fsession.on("detached", on_detached)
        link.step("attach", "ok", "")
        return True

    def _start_hook(self):
        """The hook brought up with bounded retries: a dead start is
        unloaded cleanly and retried, never left half-attached. A game
        still booting is waited for without using up an attempt."""
        link = self.link
        attempt = 0
        booting_until = time.monotonic() + self.BOOTING_GRACE
        while attempt < self.ATTEMPTS:
            attempt += 1
            if STOP.is_set() or self.detached.is_set():
                break
            self.ready.update(ok=None, early=False)
            self.ready_evt.clear()
            link.step("scan", "run", "recherche de la table des fonctions"
                      + (f" — tentative {attempt}/3" if attempt > 1 else ""))
            link.step("hook", "wait", "")
            self.last_message = time.monotonic()
            try:
                self.script = self.fsession.create_script(
                    build_script_source())
                self.script.on("message", self.on_message)
                self.script.load()      # the hook sets up asynchronously
            except Exception as e:
                print(f"[meter] load attempt {attempt} failed: {e}",
                      file=sys.stderr)
                self.script = None
            if self.script is not None and self._wait_ready() \
                    and self.ready["ok"]:
                return True
            if self.ready["early"] and time.monotonic() < booting_until:
                _unload_hook(self.script)
                self.script = None
                attempt -= 1
                link.step("scan", detail="le jeu n'a pas fini de charger — "
                                         "nouvel essai dans 4 s")
                if STOP.wait(4.0):
                    break
                continue
            print(f"[meter] hook didn't come up (attempt {attempt}/3); "
                  "cleaning up and retrying ...", file=sys.stderr)
            if self.script is not None:
                _unload_hook(self.script)
                self.script = None
            if self.ready["ok"] is False and not self.ready["early"]:
                # the function table was not found: the data, read again
                link.step("scan", detail="introuvable — relecture des "
                                         "données du jeu puis nouvel essai")
                regenerate_data(self.hlboot, force=True)
            time.sleep(1.0)
        return False

    def _wait_ready(self, max_total=240.0, idle_grace=30.0):
        """The hook's ready message. Its memory scan can take minutes on a
        slow machine, and unloading it restarts it from zero: waited for as
        long as it keeps talking, up to a cap."""
        start = time.monotonic()
        while True:
            if self.ready_evt.wait(timeout=0.5):
                return True
            if STOP.is_set() or self.detached.is_set():
                return False
            now = time.monotonic()
            if now - start > max_total:
                print("[meter] hook scan exceeded the time cap.",
                      file=sys.stderr)
                return False
            if now - self.last_message > idle_grace:
                print("[meter] hook went silent — treating it as dead.",
                      file=sys.stderr)
                return False

    def _connected(self):
        """Connected: the hook feeds on_message until the game closes, the
        meter stops or a reconnect is asked; then everything the hook was
        saying is forgotten."""
        link = self.link
        link.script = self.script
        link.step("hook", "ok", "")
        if self.hero_name is None:
            link.step("hero", "run", "en attente de ton personnage en jeu")
        if not self.zone_seen:
            link.step("zone", "run", "en attente")
        link.set_state(GameLink.CONNECTED, pid=self.pid)
        print("[*] connected — everything shows in the Farever France "
              "window; the reset hotkey is set in Réglages.", file=sys.stderr)
        try:
            while (not STOP.is_set() and not self.detached.wait(0.5)
                   and not link._reconnect.is_set()):
                pass
        finally:
            link.script = None
            if not self.detached.is_set():
                # we are the ones leaving: unload and detach properly
                _unload_hook(self.script)
                self._detach()
            ov = _overlay()
            if ov is not None:
                ov.on_game_disconnected()
            self.dungeon.disconnect()           # kept as abandoned
            self.ui_state.set_rift(False)
            self.rift_rec.set_rift(False)
            self.session.set_combat({})

    def _detach(self):
        try:
            self.fsession.detach()
        except Exception:
            pass

    # ---- the hook's messages ---------------------------------------------
    def on_message(self, message, data):
        self.last_message = time.monotonic()
        if message["type"] == "error":
            print("[JS]", message.get("description"), file=sys.stderr)
            return
        p = message.get("payload") or {}
        k = p.get("kind")
        handler = self.handlers.get(k)
        if handler is not None:
            handler(p)
        elif k in self.TO_OVERLAY:
            ov = _overlay()
            if ov is not None:
                getattr(ov, self.TO_OVERLAY[k])(p)

    def _on_hit(self, p):
        dropped = "blocker" in p and self._tally_nullified(p)
        if p.get("pet"):
            sig = (p["pet"], p.get("player") or "?")
            if sig not in self.pet_seen:
                # summon damage merges into the owner's row: the only line
                # that says it is attributed, and to whom
                self.pet_seen.add(sig)
                print(f"[meter] summon damage: pet={sig[0]!r} "
                      f"credited to {sig[1]!r}", file=sys.stderr)
        tgt = p.get("target")
        if tgt and tgt not in self.target_seen:
            # what combat history names a fight after, measurable in the log
            n = len(self.target_seen)
            if n < self.TARGET_LOG_MAX:
                print(f"[meter] hit target: {tgt!r} -> "
                      f"{_boss_label(tgt)!r}", file=sys.stderr)
            elif n == self.TARGET_LOG_MAX:
                print(f"[meter] ({self.TARGET_LOG_MAX} distinct hit targets "
                      "named; no longer listing them)", file=sys.stderr)
            self.target_seen.add(tgt)
        if not dropped:
            # a hit the target never took is not damage: dropped before it
            # can start or extend a fight
            self.session.record(p)
            self.rift_rec.record("hit", p)
            self.dungeon.record("hit", p)

    def _tally_nullified(self, p):
        """A mitigated hit (immunity phase, block): dropped when its blocker
        is a known nullifier. Every shape is tallied and the tallies logged
        every 30 s, to learn which field really marks a nullified hit."""
        who = p.get("blocker") or ""
        dropped = who in NULLIFIED_BLOCKERS
        sig = (who, p.get("effect"), (p.get("block") or 0) > 0,
               (p.get("amount") or 0) > 0, dropped)
        self.nullified[sig] = self.nullified.get(sig, 0) + 1
        now = time.monotonic()
        if now - self.nullified_at > self.REPORT_EVERY:
            self.nullified_at = now
            for s, n in sorted(self.nullified.items(), key=lambda kv: -kv[1]):
                print(f"[meter] mitigated-hit x{n}: blocker={s[0]!r} "
                      f"effect={s[1]} block>0={s[2]} amount>0={s[3]} "
                      f"{'DROPPED' if s[4] else 'counted'}", file=sys.stderr)
            self.nullified.clear()
        return dropped

    def _on_heal(self, p):
        # the hook reports what landed: its full size is filled in first,
        # so every aggregator sees the same heal
        self.heal_sizer.stamp(p)
        self.session.record_heal(p)
        self.rift_rec.record("heal", p)
        self.dungeon.record("heal", p)
        now = time.monotonic()
        if now - self.heal_log_at > self.REPORT_EVERY:
            self.heal_log_at = now
            line = self.heal_sizer.drain_report()
            if line:
                print(line, file=sys.stderr)

    def _on_combat(self, p):
        self.session.set_combat(p.get("state") or {})
        # the heartbeat also closes a rift whose kill never came in
        report = self.rift_rec.tick()
        if report is not None:
            print("[meter] rift over without a kill seen — reporting it "
                  "anyway", file=sys.stderr)
            self._show_rift_report(report)

    def _show_rift_report(self, report):
        # the classes are frozen into the report: it is reopened days later
        _stamp_report_classes(report, self.world)
        ov = _overlay()
        if ov is not None:
            ov.show_rift_report(report)

    def _on_rift(self, p):
        state = bool(p.get("state"))
        self.ui_state.set_rift(state)
        what = self.rift_rec.set_rift(state)
        print(f"[meter] rift: {state}"
              + (f" (recording: {what})" if what else ""), file=sys.stderr)
        ov = _overlay()
        if what == "abandoned" and ov is not None:
            ov.on_rift_dropped("la faille s’est terminée avant le boss")

    def _on_bossbar(self, p):
        """The game's boss / elite bar went up or down. A fight starts on
        the edge into a boss bar and ends only on a kill of the LAST boss
        bar, a loading screen or the bars staying down: "no bar right now"
        is not an end (the Nightqueen drops hers and raises copies')."""
        self.ui_state.set_boss_bar(p.get("n") or 0)
        boss_up = bool(p.get("boss"))
        if boss_up and not self.boss_fight_on:
            self._boss_pulled(p)
        for b in (p.get("down") or []):
            # `killed`: decided by the hook from the last health seen; a bar
            # dropped because the boss reset is not a kill
            if b.get("boss") and b.get("killed"):
                print(f"[meter] boss killed: {b.get('kind')}",
                      file=sys.stderr)
                # `boss` counts the bars left after this one: a copy dying
                # with the boss still up does not end the fight
                if not boss_up:
                    self._boss_won(b)

    def _boss_pulled(self, p):
        self.boss_fight_on = True
        kinds = [b.get("kind") for b in (p.get("up") or []) if b.get("boss")]
        self.boss_t0 = time.monotonic()
        self.boss_kinds = tuple(sorted(k for k in kinds if k))
        print(f"[meter] boss fight started: "
              f"{', '.join(k for k in kinds if k) or '?'}", file=sys.stderr)
        self.rift_rec.on_boss_pull()        # the rift report's phase edge
        ov = _overlay()
        if ov is not None and ov.auto_reset_boss():
            # the bar is polled, so the opening burst has already landed:
            # the last seconds are kept
            kept = self.session.reset_keeping_recent()
            print(f"[meter] meter reset for the pull "
                  f"(kept {kept} event{'' if kept == 1 else 's'} from "
                  f"the last {BOSS_PULL_BACKLAG_SECS:.0f}s)", file=sys.stderr)

    def _boss_won(self, bar):
        """The last boss bar went down killed: the fight's time, and the
        rift's report when one was recording."""
        self.boss_fight_on = False
        print("[meter] last boss bar down — pull reset re-armed",
              file=sys.stderr)
        t0, self.boss_t0 = self.boss_t0, None
        ov = _overlay()
        if t0 is not None and ov is not None:
            kinds = self.boss_kinds or (
                (bar.get("kind"),) if bar.get("kind") else ())
            ov.on_boss_timed_kill(kinds, time.monotonic() - t0)
        report = self.rift_rec.on_boss_kill()
        if report is not None:
            _stamp_report_classes(report, self.world)
            if ov is not None:
                print("[meter] rift complete — showing the end-of-rift "
                      "report", file=sys.stderr)
                ov.show_rift_report(report)

    def _end_boss_fight(self):
        """A fight that ended without a kill: no time recorded."""
        self.boss_fight_on = False
        self.boss_t0 = None

    def _on_bossgone(self, p):
        """Every boss bar down for ~5 s with a fight still on: the boss
        reset (de-aggro, or a wipe without a loading screen)."""
        if not self.boss_fight_on:
            return
        self._end_boss_fight()
        print("[meter] boss fight ended without a kill (no boss bar "
              f"for {p.get('polls', 0)} polls) — pull reset re-armed",
              file=sys.stderr)
        ov = _overlay()
        if ov is not None and ov.auto_reset_boss():
            self.session.reset()
            ov.on_boss_giveup()

    def _on_zone(self, p):
        """A loading screen (or, first, where we already are)."""
        extra = ", ".join(f"{k}={p.get(k)!r}"
                          for k in ("name", "branch", "world_map")
                          if p.get(k) is not None)
        if p.get("initial"):
            self.zone_seen = True
            self.link.step("zone", "ok", _zone_label(
                str(p.get("sig") or "").split("/")[-1]))
            self.ui_state.set_zone(p.get("sig"), p.get("world_map"))
            print(f"[meter] zone identified ({p.get('sig')!r}"
                  + (f"; {extra}" if extra else "") + ")", file=sys.stderr)
            return
        self.ui_state.clear()           # the UI is rebuilt across it
        # reset first: the fight being dropped is filed under the zone left
        self.session.reset()
        self.ui_state.set_zone(p.get("sig"), p.get("world_map"))
        if self.rift_rec.on_zone():     # a wipe or a walk-out, not a run
            print("[meter] rift recording dropped (zone change)",
                  file=sys.stderr)
            ov = _overlay()
            if ov is not None:
                ov.on_rift_dropped("changement de zone avant la victoire")
        self._end_boss_fight()
        print(f"[meter] zone change ({p.get('sig')!r}"
              + (f"; {extra}" if extra else "") + ") — meter reset",
              file=sys.stderr)

    def _on_server(self, p):
        """The shard (it changes on its own, on a relog): nothing resets."""
        self.ui_state.set_server(p.get("name"))
        if p.get("initial"):
            self.link.step("zone", detail=" · ".join(
                x for x in (self.link.steps_detail("zone"),
                            f"serveur {p.get('name')}") if x))
        print(f"[meter] shard "
              f"{'identified' if p.get('initial') else 'change'}"
              f" ({p.get('name')!r})", file=sys.stderr)

    def _on_hero(self, p):
        """The local hero, re-reported every 3 s with the group: only a new
        one is logged, and never by name (the log is often on screen)."""
        name = p.get("name")
        self.world.set_hero(name, p.get("party"))
        ov = _overlay()
        if ov is not None and name:
            ov.on_hero_seen(name, p.get("uid"), p.get("acct"))
        if name and name != self.hero_name:
            first = self.hero_name is None
            self.link.step("hero", "ok", name)
            self.hero_name = name
            print("[meter] local hero "
                  + ("identified." if first else "changed."), file=sys.stderr)

    def _on_pickup(self, p):
        self.dungeon.pickup(p)
        ov = _overlay()
        if ov is not None:
            ov.on_pickup(p)

    def _on_log(self, p):
        msg = str(p.get("msg") or "")
        print("[hook]", p.get("msg"), file=sys.stderr)
        if "functions_ptrs via" in msg:
            self.link.step("scan", "ok", "fonctions trouvées")
            self.link.step("hook", "run", "mise en place des modules")
        elif "memory scan" in msg:
            self.link.step("scan", detail="balayage de la mémoire du jeu")

    def _on_progress(self, p):
        if p.get("total"):
            self.link.step("scan", detail=f"balayage de la mémoire : "
                           f"{p.get('done')} / {p.get('total')} régions")
        now = time.monotonic()
        if now - self.progress_printed > 5.0:
            self.progress_printed = now
            print(f"[meter] hook scanning memory ... "
                  f"({p.get('done')}/{p.get('total')} regions)",
                  file=sys.stderr)

    def _on_ready(self, p):
        if p.get("ok"):
            self.link.step("scan", "ok", "fonctions trouvées")
            self.link.step("hook", "ok", "")
        self.ready["ok"] = p.get("ok")
        self.ready["early"] = bool(p.get("early"))
        print(f"[meter] hook ready ok={p.get('ok')}"
              + (" (game still booting)" if self.ready["early"] else ""),
              file=sys.stderr)
        self.ready_evt.set()


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

