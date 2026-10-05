"""Farever Book — a second-screen companion for Farever.

Reads the game through Frida (frida/meter_hook.js: read-only memory reads and
a few function hooks) and shows it in one window meant for another screen:
the damage/heal meter, rifts, dungeons, collection, hunting log, map,
achievements and the character sheet. Nothing is ever drawn in the game.

Run:  python meter/farever_book.py

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
%LOCALAPPDATA%\\FareverBook\\meter.log and puts a tray icon in the notification
area. Quitting through the window or the tray unloads the hook and detaches
cleanly; force-killing the process skips that.
"""
from __future__ import annotations

import sys
from pathlib import Path

from common import (
    HAS_CONSOLE, LOG_FILE, MENU_FLAG, STOP, TOOL_FLAG, _APP,
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
    # before any window: Windows latches DPI awareness at the first one
    print(f"[meter] dpi awareness: {declare_dpi_awareness()} "
          f"(display at {display_scale():.2f}x)", file=sys.stderr)
    seed_analysis()
    claim_single_instance()
    # after claiming: until then the flag may target the displaced instance
    watch_for_quit_request()
    session = PartySession()
    ui_state = GameUIState()
    world = WorldSnapshot()
    rift_rec = RiftRecorder()
    # outlives encounters: heal size depends on the build, not the pull
    heal_sizer = HealSizeEstimator(_heal_specs())

    # Up before anything that can block (waiting for the game, the hook's
    # memory scan): the way out must exist from the start.
    tray = TrayIcon(request_stop)
    tray.start()
    try:
        return _run(tray, session, ui_state, world, rift_rec, heal_sizer)
    finally:
        tray.stop()
        # also covers early returns; unlinking twice is harmless
        release_instance_lock()


def _run(tray, session, ui_state, world, rift_rec, heal_sizer):
    """The interface first, the game whenever it turns up."""
    link = GameLink(session, ui_state, world, rift_rec, heal_sizer)
    app = App(session, ui_state, world, link=link)
    # from here the app owns shutdown
    _APP["ref"] = app
    app._setup_begin()              # before the link: it waits for consent
    link.start()
    if STOP.is_set():
        app.request_quit()
    try:
        app.run()
    finally:
        _APP["ref"] = None
        try:
            app.menubridge.stop()
        except Exception:
            pass
        STOP.set()
        link.stop()                 # unloads the hook and detaches
        release_instance_lock()


def _cli():
    # The frozen exe re-invoked as an hltools runner or as the window process
    # (no python.exe). Before setup_logging(): the window logs to the stderr
    # its parent gave it.
    if len(sys.argv) > 2 and sys.argv[1] == TOOL_FLAG:
        run_bundled_tool(sys.argv[2], sys.argv[3:])
        return
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
        # Ctrl+C from source is a normal stop; main() has already detached
        print("[meter] stopped.", file=sys.stderr)
    except SystemExit as e:
        # startup failures: without a console, show them in a dialog
        if not HAS_CONSOLE and e.code not in (0, None):
            print(f"[meter] {e.code}", file=sys.stderr)
            message_box(e.code, "Farever Book — démarrage impossible", 0x10)
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        if not HAS_CONSOLE:
            message_box(
                "Le compteur a rencontré une erreur inattendue et s'est "
                "arrêté.\n\n"
                f"Le détail est dans :\n{LOG_FILE}",
                "Farever Book — erreur", 0x10)
        raise


if __name__ == "__main__":
    _cli()

