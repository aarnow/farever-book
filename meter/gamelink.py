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
    _boss_label, _zone_label, build_script_source, locate_hlboot,
    regenerate_data)
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
            # the heartbeat also closes a rift whose kill never came in
            report = rift_rec.tick()
            if report is not None:
                print("[meter] rift over without a kill seen — reporting it "
                      "anyway", file=sys.stderr)
                _stamp_report_classes(report, world)
                o = _OVERLAY["ref"]
                if o is not None:
                    o.show_rift_report(report)
        elif k == "rift":
            state = bool(p.get("state"))
            ui_state.set_rift(state)
            what = rift_rec.set_rift(state)
            print(f"[meter] rift: {state}"
                  + (f" (recording: {what})" if what else ""), file=sys.stderr)
            o = _OVERLAY["ref"]
            if what == "abandoned" and o is not None:
                o.on_rift_dropped("la faille s’est terminée avant le boss")
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
            if rift_rec.on_zone():
                print("[meter] rift recording dropped (zone change)",
                      file=sys.stderr)
                o = _OVERLAY["ref"]
                if o is not None:
                    o.on_rift_dropped("changement de zone avant la victoire")
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
            # in the world on a character (not the menus, not a loading
            # screen): the overlays show only then
            ov = _OVERLAY["ref"]
            if ov is not None and name:
                ov.on_hero_seen()
            if name and name != hero_id["name"]:
                first = hero_id["name"] is None
                link.step("hero", "ok", name)
                hero_id["name"] = name
                print("[meter] local hero "
                      + ("identified." if first else "changed."), file=sys.stderr)
        elif k == "pickup":
            dungeon.pickup(p)
            ov = _OVERLAY["ref"]
            if ov is not None:
                ov.on_pickup(p)
        elif k == "stock":
            ov = _OVERLAY["ref"]
            if ov is not None:
                ov.on_stock(p)
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

