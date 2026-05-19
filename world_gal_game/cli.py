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
    p.add_argument("--pack", default="demo_pack",
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


def inspect_pack_main(argv: list[str]) -> int:
    """Entry point for ``wgg inspect-pack <pack>``."""
    import argparse
    import json as _json

    p = argparse.ArgumentParser(
        prog="world-gal-game inspect-pack",
        description="Print structural analysis of a game pack (scenes, "
                    "locations, NPCs, reachability, dead-ends, graph).")
    p.add_argument("pack", help="path to the pack directory")
    p.add_argument("--format", choices=["text", "json", "mermaid", "dot"],
                   default="text",
                   help="text (default), json (full dump), mermaid (graph), "
                        "dot (graphviz).")
    p.add_argument("--capabilities", action="store_true",
                   help="instead of inspecting the pack, dump the engine's "
                        "capability manifest (effect/condition/hook kinds).")
    p.add_argument("--no-reachability", action="store_true",
                   help="skip the BFS reachability section.")
    args = p.parse_args(argv)

    if args.capabilities:
        from world_gal_game.dev.capability_manifest import (
            build_manifest, manifest_json, summary_table,
        )
        if args.format == "json":
            print(manifest_json())
        else:
            print(summary_table())
        return 0

    from world_gal_game.dev.pack_inspector import PackInspector
    pack_path = Path(args.pack).resolve()
    if not pack_path.exists():
        print(f"[error] pack 目錄不存在：{pack_path}", file=sys.stderr)
        return 1

    inspector = PackInspector(pack_path)
    if args.format == "mermaid":
        print(inspector.graph(format="mermaid"))
        return 0
    if args.format == "dot":
        print(inspector.graph(format="dot"))
        return 0
    if args.format == "json":
        out = {
            "summary": inspector.summary(),
            "scenes": inspector.scenes(),
            "locations": inspector.locations(),
            "npcs": inspector.npcs(),
            "items": inspector.items(),
            "dead_ends": [
                {"kind": d.kind, "target": d.target,
                 "file": d.file, "detail": d.detail}
                for d in inspector.dead_ends()
            ],
        }
        if not args.no_reachability:
            out["reachability"] = inspector.reachability()
        print(_json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # text format
    summary = inspector.summary()
    print(f"pack: {summary['title']}  (format v{summary['pack_format_version']})")
    print(f"  root:        {summary['pack_root']}")
    print(f"  start:       location={summary['start_location']!r} "
          f"intro_scene={summary['intro_scene']!r}")
    print(f"  counts:      "
          + ", ".join(f"{k}={v}" for k, v in summary["counts"].items()))
    if not args.no_reachability:
        r = inspector.reachability()
        print(f"  reachable:   {len(r['reachable'])} / "
              f"{len(r['reachable']) + len(r['unreachable'])} scenes")
        print(f"  endings:     reachable={r['endings']['reachable']} "
              f"unreachable={r['endings']['unreachable']}")
    de = inspector.dead_ends()
    if de:
        print(f"  dead-ends:   {len(de)}")
        for d in de:
            print(f"    [{d.kind}] {d.target} -- {d.detail}")
    else:
        print("  dead-ends:   none")
    return 0


def edit_main(argv: list[str]) -> int:
    """Entry point for ``wgg edit <pack> <op> [args]``."""
    import argparse
    import json as _json

    p = argparse.ArgumentParser(
        prog="world-gal-game edit",
        description="Structured edits to a game pack (comment-preserving).")
    p.add_argument("pack", help="path to the pack directory")
    p.add_argument("op", choices=[
        "add-scene", "add-choice", "add-npc",
        "add-location", "add-item",
        "remove-scene", "remove-npc", "remove-location",
    ], help="operation")
    p.add_argument("--payload", default=None,
                   help="JSON payload describing the entity to add/update")
    p.add_argument("--payload-file", default=None,
                   help="read JSON payload from a file")
    p.add_argument("--scene-id", default=None,
                   help="parent scene id (for add-choice)")
    p.add_argument("--id", dest="target_id", default=None,
                   help="id of the target (for remove-*)")
    p.add_argument("--into-file", default=None,
                   help="target YAML file under content/ "
                        "(default: scenes/_generated.yaml etc)")
    p.add_argument("--dry-run", action="store_true",
                   help="don't write; print diff and exit")
    args = p.parse_args(argv)

    pack_path = Path(args.pack).resolve()
    if not pack_path.exists():
        print(f"[error] pack 目錄不存在：{pack_path}", file=sys.stderr)
        return 1

    payload: dict | None = None
    if args.payload_file:
        payload = _json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    elif args.payload:
        payload = _json.loads(args.payload)

    from world_gal_game.dev.pack_editor import PackEditor, PackEditError
    editor = PackEditor(pack_path, dry_run=args.dry_run)

    try:
        if args.op == "add-scene":
            if payload is None:
                print("[error] --payload or --payload-file required for add-scene",
                      file=sys.stderr)
                return 2
            editor.add_scene(payload, into_file=args.into_file)
        elif args.op == "add-choice":
            if not args.scene_id or payload is None:
                print("[error] add-choice needs --scene-id and --payload",
                      file=sys.stderr)
                return 2
            editor.add_choice(args.scene_id, payload)
        elif args.op == "add-npc":
            if payload is None:
                print("[error] --payload required", file=sys.stderr); return 2
            editor.add_npc(payload, into_file=args.into_file)
        elif args.op == "add-location":
            if payload is None:
                print("[error] --payload required", file=sys.stderr); return 2
            editor.add_location(payload, into_file=args.into_file)
        elif args.op == "add-item":
            if payload is None:
                print("[error] --payload required", file=sys.stderr); return 2
            editor.add_item(payload, into_file=args.into_file)
        elif args.op == "remove-scene":
            if not args.target_id:
                print("[error] remove-scene needs --id", file=sys.stderr); return 2
            editor.remove_scene(args.target_id)
        elif args.op == "remove-npc":
            if not args.target_id:
                print("[error] remove-npc needs --id", file=sys.stderr); return 2
            editor.remove_npc(args.target_id)
        elif args.op == "remove-location":
            if not args.target_id:
                print("[error] remove-location needs --id", file=sys.stderr); return 2
            editor.remove_location(args.target_id)
    except PackEditError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        diff = editor.diff()
        if diff:
            print(diff)
        else:
            print("(no changes)")
        print()
        print("--- summary ---")
        for c in editor.list_changes():
            print(f"  {c['op']} {c['id']} -> {c['file']}: {c['summary']}")
    else:
        for c in editor.list_changes():
            print(f"[ok] {c['op']} {c['id']} -> {c['file']}")
    return 0


def capabilities_main(argv: list[str]) -> int:
    """Entry point for ``wgg capabilities``."""
    import argparse
    p = argparse.ArgumentParser(
        prog="world-gal-game capabilities",
        description="Dump the engine's capability manifest (effect/condition/"
                    "hook kinds + loaded plugins).")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--pack", default=None,
                   help="optionally load a pack first so its plugins show up")
    args = p.parse_args(argv)

    if args.pack:
        # Loading a pack registers its pack-local plugins, then we capture
        # the manager from state.meta so the manifest includes them.
        from world_gal_game.config import EngineConfig
        from world_gal_game.headless import HeadlessSession
        sess = HeadlessSession.open(EngineConfig(), pack=args.pack)
        manager = sess.state.meta.get("__plugin_manager__")
    else:
        manager = None

    from world_gal_game.dev.capability_manifest import (
        manifest_json, summary_table,
    )
    if args.format == "json":
        print(manifest_json(manager=manager))
    else:
        print(summary_table())
    return 0


def smoke_main(argv: list[str]) -> int:
    """Entry point for ``wgg smoke <pack>`` — runs every scripts/test_*.json."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(prog="world-gal-game smoke",
                                description="Run every scripts/test_*.json in a pack.")
    p.add_argument("pack", help="path to the pack directory")
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args(argv)

    pack_path = Path(args.pack).resolve()
    if not pack_path.exists():
        print(f"[error] pack 目錄不存在：{pack_path}", file=sys.stderr)
        return 1
    from world_gal_game.dev.smoke_runner import SmokeRunner
    report = SmokeRunner(pack_path).run()
    if args.format == "json":
        print(_json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for r in report.results:
            status = "ok " if r.ok else "FAIL"
            print(f"[{status}] {r.script}  ({r.duration_s:.2f}s, "
                  f"ending={r.ending_flag})")
            for err in r.errors:
                print(f"        ! {err}")
        print()
        passed = sum(1 for r in report.results if r.ok)
        print(f"smoke: {passed}/{len(report.results)} passed")
    return 0 if report.ok else 1


def visual_check_main(argv: list[str]) -> int:
    """Entry point for ``wgg visual-check <pack>``."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(prog="world-gal-game visual-check",
                                description="Render scenarios and diff against baselines.")
    p.add_argument("pack", help="path to the pack directory")
    p.add_argument("--update-baselines", action="store_true",
                   help="overwrite baselines with the current render (use after intended UI changes)")
    p.add_argument("--scenarios", default=None,
                   help="JSON file with scenarios; falls back to the default set")
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args(argv)

    pack_path = Path(args.pack).resolve()
    if not pack_path.exists():
        print(f"[error] pack 目錄不存在：{pack_path}", file=sys.stderr)
        return 1
    from world_gal_game.dev.visual_check import VisualCheck

    vc = VisualCheck(pack_path)
    if args.scenarios:
        scenarios = _json.loads(Path(args.scenarios).read_text(encoding="utf-8"))
    else:
        scenarios = VisualCheck.default_scenarios()

    if args.update_baselines:
        # Force-promote: capture, then copy candidate → baseline.
        for sc in scenarios:
            vc.capture(
                name=sc["name"],
                dev_start=sc.get("dev_start"),
                dev_location=sc.get("dev_location"),
                dev_time=sc.get("dev_time"),
                dev_flags=sc.get("dev_flags"),
                autoplay=sc.get("autoplay", 0.6),
            )
            vc.update_baseline(name=sc["name"])
            print(f"[updated] {sc['name']}")
        return 0

    report = vc.run(scenarios)
    if args.format == "json":
        print(_json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for r in report.results:
            status = "ok " if r.ok else "FAIL"
            tag = " [new baseline]" if r.created_baseline else ""
            print(f"[{status}] {r.name}{tag}  {r.detail}")
        print()
        passed = sum(1 for r in report.results if r.ok)
        print(f"visual: {passed}/{len(report.results)} passed")
    return 0 if report.ok else 1


def self_check_main(argv: list[str]) -> int:
    """Entry point for ``wgg self-check <pack>``."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(prog="world-gal-game self-check",
                                description="Full 5-stage pack verification.")
    p.add_argument("pack", help="path to the pack directory")
    p.add_argument("--skip-smoke", action="store_true")
    p.add_argument("--include-visual", action="store_true",
                   help="also run the visual stage (off by default)")
    p.add_argument("--no-stop-on-failure", action="store_true",
                   help="run every stage even if earlier ones fail")
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args(argv)

    pack_path = Path(args.pack).resolve()
    if not pack_path.exists():
        print(f"[error] pack 目錄不存在：{pack_path}", file=sys.stderr)
        return 1

    from world_gal_game.dev.self_check import SelfCheck
    sc = SelfCheck(
        pack_path,
        stop_on_failure=not args.no_stop_on_failure,
        skip_smoke=args.skip_smoke,
        skip_visual=not args.include_visual,
    )
    report = sc.run()
    if args.format == "json":
        print(_json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for stage in report.stages:
            if stage.skipped:
                marker = "skip"
            else:
                marker = "ok " if stage.ok else "FAIL"
            print(f"[{marker}] {stage.name:<10}  {stage.summary}")
            if not stage.ok and not stage.skipped:
                for k, v in stage.details.items():
                    print(f"        {k}: {v if not isinstance(v, list) else f'{len(v)} item(s)'}")
        print()
        print("self-check: " + ("OK" if report.ok else "FAIL"))
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    # Early-exit subcommands — keep before the main argparse so they don't
    # collide with engine flags. Use the argv passed in (or sys.argv[1:]).
    _args = argv if argv is not None else sys.argv[1:]
    if _args[:1] == ["build"]:
        from world_gal_game.build import main as build_main
        return build_main(_args[1:])
    if _args[:1] == ["validate"]:
        return validate_main(_args[1:])
    if _args[:1] == ["check"]:
        # ``check`` is the friendlier alias for ``validate`` — same checks
        # (schema + refs + dead-ends), and the recommended verb for CI.
        return validate_main(_args[1:])
    if _args[:1] == ["debug"]:
        from world_gal_game.dev.driver import _cli_main as debug_main
        return debug_main(_args[1:])
    if _args[:1] == ["inspect-pack"]:
        return inspect_pack_main(_args[1:])
    if _args[:1] == ["edit"]:
        return edit_main(_args[1:])
    if _args[:1] == ["capabilities"]:
        return capabilities_main(_args[1:])
    if _args[:1] == ["smoke"]:
        return smoke_main(_args[1:])
    if _args[:1] == ["visual-check"]:
        return visual_check_main(_args[1:])
    if _args[:1] == ["self-check"]:
        return self_check_main(_args[1:])

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
