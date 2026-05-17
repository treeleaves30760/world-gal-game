"""Console entry point for the World Gal-Game engine.

Exposed via the ``world-gal-game`` console script (see pyproject.toml's
``[project.scripts]``). The repo root keeps a thin ``main.py`` wrapper
that simply delegates here, so existing ``python main.py …`` invocations
continue to work in source checkouts.

Execution modes mirror the ones documented in the project README; the
authoritative help text comes from ``--help``.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="world-gal-game",
        description="World Gal-Game engine. Loads a game pack from "
                    "games/<pack>/, a sibling directory, or any path "
                    "passed to --pack.",
    )
    p.add_argument("--pack", default="tsing_hua_strange_tales",
                   help="game pack name or path")
    p.add_argument("--list-packs", action="store_true",
                   help="list discovered game packs and exit.")
    p.add_argument("--fullscreen", action="store_true")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--fps", type=int, default=60)
    p.add_argument("--text-speed", type=float, default=None,
                   help="characters per second (0 = instant)")

    # headless / inspection
    p.add_argument("--headless", action="store_true",
                   help="run without a real window/audio. Useful in CI.")
    p.add_argument("--inspect", action="store_true",
                   help="(headless) dump game state as JSON and exit.")
    p.add_argument("--script", type=Path, default=None,
                   help="(headless) JSON file with a list of actions to run.")
    p.add_argument("--no-inspect-after", action="store_true",
                   help="(--script) don't dump final state.")

    # screenshot mode (with display)
    p.add_argument("--screenshot", type=Path, default=None,
                   help="render and take a screenshot here, then exit.")
    p.add_argument("--autoplay", type=float, default=0.0,
                   help="(--screenshot) wait this many seconds (game time) "
                        "before taking the shot.")
    p.add_argument("--dev-start", default=None,
                   help="(--screenshot) skip title; start at:  "
                        "explore | scene:<id> | map | affection | log | "
                        "save | load | settings | achievements | npc:<npc_id>")
    p.add_argument("--dev-flags", default=None,
                   help="(--screenshot) JSON dict of flags to pre-set.")
    p.add_argument("--dev-location", default=None,
                   help="(--screenshot) move to this location id first.")
    p.add_argument("--dev-time", default=None,
                   help="(--screenshot) set time of day: morning|noon|"
                        "afternoon|evening|night|midnight")
    p.add_argument("--dev-affection", default=None,
                   help="(--screenshot) JSON dict of npc_id -> delta.")
    return p


def validate_main(argv: list[str]) -> int:
    """Entry point for `wgg validate <pack> [--json]`."""
    import argparse
    import json as _json

    p = argparse.ArgumentParser(prog="world-gal-game validate",
                                description="Validate a game pack's YAML content.")
    p.add_argument("pack", help="path to the pack directory")
    p.add_argument("--json", action="store_true",
                   help="output issues as machine-readable JSON")
    args = p.parse_args(argv)

    from pathlib import Path
    from world_gal_game.validator import validate_pack

    pack_path = Path(args.pack).resolve()
    if not pack_path.exists():
        print(f"[error] pack 目錄不存在：{pack_path}", file=sys.stderr)
        return 1

    issues = validate_pack(pack_path)

    if args.json:
        data = [
            {
                "severity": iss.severity,
                "file": iss.file,
                "path": iss.path,
                "message": iss.message,
                "hint": iss.hint,
            }
            for iss in issues
        ]
        print(_json.dumps(data, ensure_ascii=False, indent=2))
    else:
        if not issues:
            print("驗證通過，沒有發現問題。")
        else:
            for iss in issues:
                loc = f"{iss.file}:{iss.path}" if iss.path else iss.file
                print(f"[{iss.severity}] {loc}")
                print(f"  {iss.message}")
                if iss.hint:
                    print(f"  提示：{iss.hint}")

    errors = sum(1 for iss in issues if iss.severity == "error")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    # Early-exit subcommands — keep before the main argparse so they don't
    # collide with engine flags. Use the argv passed in (or sys.argv[1:]).
    _args = argv if argv is not None else sys.argv[1:]
    if _args[:1] == ["build"]:
        from world_gal_game.build import main as build_main
        return build_main(_args[1:])
    if _args[:1] == ["validate"]:
        return validate_main(_args[1:])
    if _args[:1] == ["debug"]:
        from world_gal_game.dev.driver import _cli_main as debug_main
        return debug_main(_args[1:])

    args = build_parser().parse_args(argv)

    from world_gal_game.config import EngineConfig
    if args.list_packs:
        from world_gal_game.pack_registry import discover_packs, render_table
        print(render_table(discover_packs()))
        return 0

    config = EngineConfig(
        screen_size=(args.width, args.height),
        fps=args.fps,
        fullscreen=args.fullscreen,
        default_pack=args.pack,
    )
    if args.text_speed is not None:
        config.text_speed = args.text_speed

    if args.headless:
        from world_gal_game.headless import run_inspect, run_script
        if args.script:
            run_script(config, str(args.script), pack=args.pack,
                       inspect_after=not args.no_inspect_after)
        else:
            run_inspect(config, pack=args.pack)
        return 0

    # GUI mode (or screenshot mode, which is GUI but exits early).
    import os
    if args.screenshot:
        # Stay windowless via SDL_VIDEODRIVER=dummy so this can run in CI /
        # on a remote box without a display server.
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    from world_gal_game.app import GalGameApp
    app = GalGameApp(config=config, pack=args.pack, headless=False)

    if args.screenshot:
        return _run_screenshot_mode(app, args)

    app.run()
    return 0


def _run_screenshot_mode(app, args) -> int:
    """Drive the App for a few frames and save the resulting Surface."""
    import json
    import pygame
    from world_gal_game.ui.input import InputState

    # Apply pre-state flags so we can screenshot mid-route states.
    if args.dev_flags:
        for k, v in json.loads(args.dev_flags).items():
            app.state.events.set_flag(k, v)
    if args.dev_affection:
        for k, v in json.loads(args.dev_affection).items():
            app.state.affection.adjust(k, int(v))
    if args.dev_time:
        from world_gal_game.core.time_system import TimeOfDay
        try:
            app.state.time.set_phase(TimeOfDay(args.dev_time))
        except ValueError:
            pass
    if args.dev_location and args.dev_location in app.state.map.locations:
        app.state.map.move_to(args.dev_location)

    # Skip the title screen if requested.
    if args.dev_start:
        ds = args.dev_start
        if ds == "explore":
            app._start_new_game()
        elif ds.startswith("scene:"):
            app._start_new_game()
            app.manager.commit_pending()
            app._start_dialogue(ds.split(":", 1)[1])
        elif ds == "map":
            app._start_new_game(); app.manager.commit_pending()
            app._open_map()
        elif ds == "affection":
            app._start_new_game(); app.manager.commit_pending()
            app._open_affection()
        elif ds == "log":
            app._start_new_game(); app.manager.commit_pending()
            app._open_event_log()
        elif ds == "save":
            app._start_new_game(); app.manager.commit_pending()
            app._open_save_menu()
        elif ds == "load":
            app._open_load_menu()
        elif ds == "settings":
            app._start_new_game(); app.manager.commit_pending()
            app._open_settings()
        elif ds == "menu":
            app._start_new_game(); app.manager.commit_pending()
            app._open_menu()
        elif ds == "achievements":
            app._start_new_game(); app.manager.commit_pending()
            app.state.achievements.check(app.state)
            app._open_achievements()
        elif ds == "inventory":
            app._start_new_game(); app.manager.commit_pending()
            app._open_inventory()
        elif ds.startswith("npc:"):
            app._start_new_game(); app.manager.commit_pending()
            app._open_npc_actions(ds.split(":", 1)[1])

    start = time.monotonic()
    target_path = Path(args.screenshot).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        dt = app.clock.tick(app.config.fps) / 1000.0
        events = pygame.event.get()
        inp = InputState.collect(events)
        app.manager.update(dt, inp)
        # Mirror App.run()'s toast handling so screenshots show them.
        app._poll_achievement_toasts()
        app.toast_stack.update(dt, inp)
        app.manager.draw(app.screen)
        app.toast_stack.draw(app.screen)
        pygame.display.flip()
        if time.monotonic() - start >= max(0.5, args.autoplay):
            break
    pygame.image.save(app.screen, str(target_path))
    print(f"[screenshot] saved -> {target_path}", file=sys.stderr)
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
