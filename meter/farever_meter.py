"""Farever France — a second-screen companion for Farever.

Reads the game through Frida (frida/meter_hook.js: read-only memory reads and
a few function hooks) and shows it in one window meant for another screen:
the damage/heal meter, rifts, dungeons, collection, hunting log, map,
achievements and the character sheet. Nothing is ever drawn in the game.

Run:  python meter/farever_meter.py

The modules, by responsibility:
  common     paths, constants and small helpers
  winsys     Windows plumbing (DPI, hotkey, tray, clipboard, one instance)
  gamedata   the game's data tables, and their regeneration after a patch
  gearstats  gear stats and character attributes, as the game computes them
  combat     damage/heal accounting, rift and dungeon recording
  views      page builders: game data turned into what the window shows
  reports    rift reports: their page and their image
  bridge     the window's process (menu_host.py) and the pipe to it
  gamelink   the connection to the game: attach, hook, reconnect
  app        the application: its state, its actions, its pages
  this file  startup and shutdown

Shipped as a windowed program, it has no console: it logs to
%LOCALAPPDATA%\\FareverFrance\\meter.log and puts a tray icon in the notification
area. Quitting through the window or the tray unloads the hook and detaches
cleanly; force-killing the process skips that.
"""
from __future__ import annotations

import sys
from pathlib import Path

from common import (
    HAS_CONSOLE, LOG_FILE, MENU_FLAG, STOP, TOOL_FLAG, _OVERLAY,
    message_box, request_stop, run_bundled_tool,
    seed_analysis, setup_logging)
from winsys import (
    TrayIcon, claim_single_instance, declare_dpi_awareness, display_scale,
    release_instance_lock, watch_for_quit_request)
from gamedata import _heal_specs
from combat import (
    GameUIState, HealSizeEstimator, PartySession, RiftRecorder, WorldSnapshot)
from gamelink import GameLink
from app import App


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
    """The interface first, the game whenever it turns up."""
    link = GameLink(session, ui_state, world, rift_rec, heal_sizer)
    overlay = App(session, ui_state, world, link=link)
    # From here the overlay owns shutdown: it's the only thing that can return
    # from the mainloop and let the finally below unload the hook and detach.
    _OVERLAY["ref"] = overlay
    overlay._setup_begin()          # before the link: it waits for consent
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

