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


def _resolve_cli_pack(pack: str) -> Path | None:
    """Resolve a CLI pack name or path with the runtime's search semantics."""
    from world_gal_game.config import EngineConfig

    try:
        return EngineConfig().pack_root(pack)
    except FileNotFoundError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return None


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
    p.add_argument(
        "--locale",
        default="",
        help=(
            "request an authored locale for this run (for example en or "
            "zh-Hant-TW); unsupported values use the pack fallback"
        ),
    )

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
    """Entry point for `world-gal-game validate <pack> [--json]`."""
    import argparse
    import json as _json

    p = argparse.ArgumentParser(prog="world-gal-game validate",
                                description="Validate a game pack's YAML content.")
    p.add_argument("pack", help="pack name or path")
    p.add_argument("--json", action="store_true",
                   help="output issues as machine-readable JSON")
    args = p.parse_args(argv)

    from world_gal_game.validator import validate_pack

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
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
    """Entry point for ``world-gal-game inspect-pack <pack>``."""
    import argparse
    import json as _json

    p = argparse.ArgumentParser(
        prog="world-gal-game inspect-pack",
        description="Print structural analysis of a game pack (scenes, "
                    "locations, NPCs, reachability, dead-ends, graph).")
    p.add_argument("pack", help="pack name or path")
    p.add_argument("--format", choices=["text", "json", "mermaid", "dot"],
                   default="text",
                   help="text (default), json (full dump), mermaid (graph), "
                        "dot (graphviz).")
    p.add_argument("--capabilities", action="store_true",
                   help="instead of inspecting the pack, dump the engine's "
                        "capability manifest (effect/condition/hook kinds).")
    p.add_argument("--schema", action="store_true",
                   help="with --capabilities, emit only the JSON-Schema bundle "
                        "(per-kind arg schemas + content models).")
    p.add_argument("--no-reachability", action="store_true",
                   help="skip the BFS reachability section.")
    p.add_argument("--dataflow", action="store_true",
                   help="dump the flag/scene/item/resource writers+readers "
                        "cross-reference and conditioned scene edges instead "
                        "of the structural view (loads the pack).")
    p.add_argument("--references", default=None, metavar="SYMBOL",
                   help="show the writers+readers of a single symbol id "
                        "(flag/scene/item/resource) and exit.")
    args = p.parse_args(argv)

    if args.capabilities:
        from world_gal_game.dev.capability_manifest import (
            manifest_json, schema_json, summary_table,
        )
        if args.schema:
            print(schema_json())
        elif args.format == "json":
            print(manifest_json())
        else:
            print(summary_table())
        return 0

    from world_gal_game.dev.pack_inspector import PackInspector
    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1

    # Dataflow / cross-reference view (loads the pack for typed fidelity).
    if args.dataflow or args.references:
        from world_gal_game.dev.dataflow import DataflowAnalyzer
        analyzer = DataflowAnalyzer(pack_path)
        if args.references:
            print(_json.dumps(analyzer.references(args.references),
                              ensure_ascii=False, indent=2))
            return 0
        declared = {v["key"] for v in PackInspector(pack_path).variables()}
        report = analyzer.analyze(declared_flags=declared or None)
        if args.format == "json":
            print(_json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
            return 0
        print("flags (writers / readers):")
        for fid, usage in report.flags.items():
            print(f"  {fid}: {len(usage.writers)}w / {len(usage.readers)}r")
        if report.undeclared_flags:
            print("  used-but-undeclared: " + ", ".join(report.undeclared_flags))
        if report.unused_declared_flags:
            print("  declared-but-unused: " + ", ".join(report.unused_declared_flags))
        guarded = sum(1 for e in report.edges if e.guard)
        print(f"edges: {len(report.edges)} scene->scene ({guarded} guarded)")
        return 0

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
    print("  counts:      "
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
    """Entry point for ``world-gal-game edit <pack> <op> [args]``."""
    import argparse
    import json as _json

    p = argparse.ArgumentParser(
        prog="world-gal-game edit",
        description="Structured edits to a game pack (comment-preserving).")
    p.add_argument("pack", help="pack name or path")
    p.add_argument("op", nargs="?", choices=[
        "add-scene", "add-choice", "update-line", "add-npc",
        "add-location", "add-item",
        "remove-scene", "remove-npc", "remove-location",
    ], default=None,
       help="operation (optional when only --gen-migration is used)")
    p.add_argument("--payload", default=None,
                   help="JSON payload describing the entity to add/update")
    p.add_argument("--payload-file", default=None,
                   help="read JSON payload from a file")
    p.add_argument("--scene-id", default=None,
                   help="parent scene id (for add-choice / update-line)")
    p.add_argument("--line-index", type=int, default=None,
                   help="0-based line index (for update-line)")
    p.add_argument("--id", dest="target_id", default=None,
                   help="id of the target (for remove-*)")
    p.add_argument("--into-file", default=None,
                   help="target YAML file under content/ "
                        "(default: scenes/_generated.yaml etc)")
    p.add_argument("--gen-migration", default=None, metavar="FROM:TO",
                   help="scaffold a @save_migration stub bridging the given "
                        "pack content versions and bump meta.yaml's "
                        "pack_format_version (e.g. --gen-migration 0.1:0.2)")
    p.add_argument("--reason", default="",
                   help="(--gen-migration) human description of the change")
    p.add_argument("--dry-run", action="store_true",
                   help="don't write; print diff and exit")
    args = p.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1

    payload: dict | None = None
    if args.payload_file:
        payload = _json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    elif args.payload:
        payload = _json.loads(args.payload)

    if args.op is None and not args.gen_migration:
        print("[error] an operation or --gen-migration is required",
              file=sys.stderr)
        return 2

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
        elif args.op == "update-line":
            if not args.scene_id or args.line_index is None or payload is None:
                print("[error] update-line needs --scene-id, --line-index "
                      "and --payload", file=sys.stderr)
                return 2
            editor.update_line(args.scene_id, args.line_index, payload)
        elif args.op == "add-npc":
            if payload is None:
                print("[error] --payload required", file=sys.stderr)
                return 2
            editor.add_npc(payload, into_file=args.into_file)
        elif args.op == "add-location":
            if payload is None:
                print("[error] --payload required", file=sys.stderr)
                return 2
            editor.add_location(payload, into_file=args.into_file)
        elif args.op == "add-item":
            if payload is None:
                print("[error] --payload required", file=sys.stderr)
                return 2
            editor.add_item(payload, into_file=args.into_file)
        elif args.op == "remove-scene":
            if not args.target_id:
                print("[error] remove-scene needs --id", file=sys.stderr)
                return 2
            editor.remove_scene(args.target_id)
        elif args.op == "remove-npc":
            if not args.target_id:
                print("[error] remove-npc needs --id", file=sys.stderr)
                return 2
            editor.remove_npc(args.target_id)
        elif args.op == "remove-location":
            if not args.target_id:
                print("[error] remove-location needs --id", file=sys.stderr)
                return 2
            editor.remove_location(args.target_id)

        if args.gen_migration:
            spec = args.gen_migration
            if ":" not in spec:
                print("[error] --gen-migration expects FROM:TO "
                      "(e.g. 0.1:0.2)", file=sys.stderr)
                return 2
            from_v, to_v = spec.split(":", 1)
            editor.scaffold_save_migration(
                from_version=from_v.strip(), to_version=to_v.strip(),
                reason=args.reason, pack_id=pack_path.name,
            )
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
    """Entry point for ``world-gal-game capabilities``."""
    import argparse
    p = argparse.ArgumentParser(
        prog="world-gal-game capabilities",
        description="Dump the engine's capability manifest (effect/condition/"
                    "hook kinds + loaded plugins).")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--schema", action="store_true",
                   help="emit only the JSON-Schema bundle (a real JSON Schema "
                        "per effect/condition kind + the pack content models) "
                        "for offline pack validation by any agent")
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
        manifest_json, schema_json, summary_table,
    )
    if args.schema:
        print(schema_json())
    elif args.format == "json":
        print(manifest_json(manager=manager))
    else:
        print(summary_table())
    return 0


def variables_main(argv: list[str]) -> int:
    """Entry point for ``wgg variables <pack>``."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(
        prog="world-gal-game variables",
        description="List a pack's declared narrative-state variables "
                    "(content/variables.yaml) — the typed state schema. With "
                    "--check, cross-checks declared vs. used flags.")
    p.add_argument("pack", help="pack name or path")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--check", action="store_true",
                   help="cross-check declared vs. used: report used-but-"
                        "undeclared and declared-but-unused flags (loads the "
                        "pack).")
    args = p.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1

    from world_gal_game.dev.pack_inspector import PackInspector
    declared = PackInspector(pack_path).variables()
    out: dict = {"variables": declared}
    if args.check:
        from world_gal_game.dev.dataflow import DataflowAnalyzer
        report = DataflowAnalyzer(pack_path).analyze(
            declared_flags={v["key"] for v in declared})
        out["undeclared_flags"] = report.undeclared_flags
        out["unused_declared_flags"] = report.unused_declared_flags

    if args.format == "json":
        print(_json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if not declared:
        print("(this pack declares no variables; add content/variables.yaml)")
    else:
        print(f"declared variables ({len(declared)}):")
        for v in declared:
            cat = f" [{v['category']}]" if v["category"] else ""
            print(f"  {v['key']}: {v['type']} = {v['default']!r}{cat}"
                  + (f"  — {v['description']}" if v["description"] else ""))
    if args.check:
        undeclared = out["undeclared_flags"]
        unused = out["unused_declared_flags"]
        print()
        print("used-but-undeclared: "
              + (", ".join(undeclared) if undeclared else "none"))
        print("declared-but-unused: "
              + (", ".join(unused) if unused else "none"))
    return 0


def chapters_main(argv: list[str]) -> int:
    """Entry point for ``wgg chapters <pack>`` — declared narrative structure."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(
        prog="world-gal-game chapters",
        description="List a pack's declared chapter/act/route structure "
                    "(content/chapters.yaml). With --check, cross-checks chapter "
                    "scene references against the pack's real scenes.")
    p.add_argument("pack", help="pack name or path")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--check", action="store_true",
                   help="report unknown scene refs and scenes covered by no "
                        "chapter.")
    args = p.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1

    from world_gal_game.dev.pack_inspector import PackInspector
    inspector = PackInspector(pack_path)
    chapters = inspector.chapters()
    out: dict = {"chapters": chapters}
    if args.check:
        out["issues"] = inspector.chapter_issues()

    if args.format == "json":
        print(_json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if not chapters:
        print("(this pack declares no chapters; add content/chapters.yaml)")
    else:
        print(f"declared chapters ({len(chapters)}):")
        for c in chapters:
            route = f" route={c['route']}" if c["route"] else ""
            act = f" act={c['act']}" if c["act"] else ""
            print(f"  [{c['order']:>3}] {c['id']}{route}{act}  "
                  f"({len(c['scenes'])} scenes)"
                  + (f" — {c['title']}" if c["title"] else ""))
    if args.check:
        issues = out["issues"]
        print()
        print("unknown scene refs: "
              + (", ".join(issues["unknown_scenes"]) if issues["unknown_scenes"] else "none"))
        print("scenes in no chapter: "
              + (", ".join(issues["uncovered_scenes"]) if issues["uncovered_scenes"] else "none"))
    return 0


def session_main(argv: list[str]) -> int:
    """Entry point for ``wgg session`` — a warm NDJSON control session."""
    import argparse
    p = argparse.ArgumentParser(
        prog="world-gal-game session",
        description="Start a warm NDJSON control session: load the pack once, "
                    "then read one JSON command per line on stdin and write one "
                    "JSON response per line on stdout. The fast, language-"
                    "agnostic control plane — no per-call process spawn, no RPC "
                    "envelope. Same op vocabulary as --headless --script. "
                    "Control ops: __ping__ / __inspect__ / __affordances__ / "
                    "__reset__ / __quit__.")
    p.add_argument("--pack", default="demo_pack", help="pack name or path")
    p.add_argument("--seed", type=int, default=None,
                   help="determinism seed (GameState.rng)")
    args = p.parse_args(argv)
    from world_gal_game.dev.session_server import run_session
    run_session(pack=args.pack, seed=args.seed)
    return 0


def plan_main(argv: list[str]) -> int:
    """Entry point for ``world-gal-game plan`` — goal-directed path search."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(
        prog="world-gal-game plan",
        description="Goal-directed search: find a sequence of ops that reaches "
                    "a goal predicate, using the deterministic forward model + "
                    "snapshot/restore. Prints the op path as JSON.")
    p.add_argument("--pack", default="demo_pack", help="pack name or path")
    p.add_argument("--goal", required=True,
                   help='JSON goal predicate, e.g. \'{"flag":"quest_started"}\' '
                        'or \'{"scene_played":"meet_heroine"}\'')
    p.add_argument("--setup", default=None,
                   help="JSON list of setup ops to run before searching, e.g. "
                        '\'[{"op":"start_scene","scene":"prologue"}]\'')
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-depth", type=int, default=30)
    p.add_argument("--max-nodes", type=int, default=4000)
    p.add_argument("--no-moves", action="store_true",
                   help="don't branch on location moves")
    p.add_argument("--no-scenes", action="store_true",
                   help="don't branch on scene starts")
    args = p.parse_args(argv)

    try:
        goal = _json.loads(args.goal)
        setup = _json.loads(args.setup) if args.setup else None
    except _json.JSONDecodeError as e:
        print(f"[error] bad JSON: {e}", file=sys.stderr)
        return 2

    from world_gal_game.dev.planner import Planner
    result = Planner(args.pack, seed=args.seed).find_path(
        goal, setup=setup,
        explore_moves=not args.no_moves, explore_scenes=not args.no_scenes,
        max_depth=args.max_depth, max_nodes=args.max_nodes,
    )
    print(_json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    return 0 if result.found else 1


def coverage_main(argv: list[str]) -> int:
    """Entry point for ``wgg coverage <pack> [--script s.json]``."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(
        prog="world-gal-game coverage",
        description="Report scene/line/choice/ending coverage of a script run "
                    "against the pack's totals.")
    p.add_argument("pack", help="pack name or path")
    script_group = p.add_mutually_exclusive_group()
    script_group.add_argument("--script", default=None,
                              help="JSON script (a list of ops or {commands:[...]}) to "
                                   "run; omitted = report the totals with zero coverage.")
    script_group.add_argument(
        "--all-scripts",
        action="store_true",
        help=(
            "run and union every scripts/test_*.json observation using the "
            "authoritative absolute pack path"
        ),
    )
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1

    from world_gal_game.config import EngineConfig
    from world_gal_game.dev.coverage import CoverageTracker
    from world_gal_game.headless import HeadlessSession

    if args.all_scripts:
        from world_gal_game.dev.production_check import _run_coverage_union

        union = _run_coverage_union(
            pack_path,
            seed=args.seed if args.seed is not None else 42,
        )
        if args.format == "json":
            print(_json.dumps(union, ensure_ascii=False, indent=2))
        else:
            coverage = union["coverage"]
            for name in ("scenes", "lines", "choices", "endings"):
                bucket = coverage[name]
                line = (
                    f"  {name:8} {bucket['seen']}/{bucket['total']} "
                    f"({bucket['pct']}%)"
                )
                if bucket["missing"] and name in ("scenes", "endings"):
                    line += f"  missing: {', '.join(bucket['missing'])}"
                print(line)
        return 0 if union["ok"] else 1

    tracker = CoverageTracker(pack_path)
    sess = HeadlessSession.open(EngineConfig(seed=args.seed), pack=str(pack_path))
    if args.script:
        data = _json.loads(Path(args.script).read_text(encoding="utf-8"))
        commands = data if isinstance(data, list) else data.get("commands", [])
        sess.run_script(commands)
    report = tracker.report(sess)

    if args.format == "json":
        print(_json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
        return 0
    for name in ("scenes", "lines", "choices", "endings"):
        b = getattr(report, name)
        line = f"  {name:8} {b.seen}/{b.total} ({b.pct}%)"
        if b.missing and name in ("scenes", "endings"):
            line += f"  missing: {', '.join(b.missing)}"
        print(line)
    return 0


def agent_guide_main(argv: list[str]) -> int:
    """Entry point for ``wgg agent-guide`` — print the agent onboarding guide."""
    import argparse
    p = argparse.ArgumentParser(
        prog="world-gal-game agent-guide",
        description="Print the agent-neutral onboarding guide (the importable "
                    "twin of AGENTS.md). Works even when pip-installed — the "
                    "guide is generated from code, not read from docs/.")
    p.parse_args(argv)
    from world_gal_game.dev.agent_bundle import agent_guide_text
    print(agent_guide_text(), end="")
    return 0


def docs_main(argv: list[str]) -> int:
    """Entry point for ``wgg docs export <dir>`` — write the onboarding bundle."""
    import argparse
    p = argparse.ArgumentParser(
        prog="world-gal-game docs",
        description="Export a self-contained agent onboarding bundle (guide + "
                    "capability JSON-Schema + session-protocol schema + recipes) "
                    "to a directory or stdout. Works when pip-installed.")
    p.add_argument("action", choices=["export"], help="bundle action")
    p.add_argument("dest", nargs="?", default="-",
                   help="output directory, or '-' for one JSON object on stdout "
                        "(default: stdout)")
    p.add_argument("--pack", default=None,
                   help="load a pack first so its plugins show up in the "
                        "capability manifest")
    args = p.parse_args(argv)

    manager = None
    if args.pack:
        from world_gal_game.config import EngineConfig
        from world_gal_game.headless import HeadlessSession
        sess = HeadlessSession.open(EngineConfig(), pack=args.pack)
        manager = sess.state.meta.get("__plugin_manager__")

    from world_gal_game.dev.agent_bundle import export_bundle
    written = export_bundle(args.dest, manager=manager)
    if args.dest != "-":
        for path in written:
            print(f"[ok] wrote {path}", file=sys.stderr)
    return 0


def context_main(argv: list[str]) -> int:
    """Entry point for ``world-gal-game context <pack>`` — aggregate JSON."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(
        prog="world-gal-game context",
        description="Aggregate a pack's variables + reachability + scene graph "
                    "+ dataflow digest + coverage totals + structural gaps into "
                    "a single JSON object — the lowest-token way to orient an "
                    "agent before editing.")
    p.add_argument("pack", help="pack name or path")
    p.add_argument("--script", default=None,
                   help="JSON script (a list of ops or {commands:[...]}) to run "
                        "for real coverage; omitted = totals only.")
    p.add_argument("--full-dataflow", action="store_true",
                   help="emit the full writers/readers report instead of the "
                        "per-symbol count digest.")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--format", choices=["json", "text"], default="json")
    args = p.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1

    script = None
    if args.script:
        data = _json.loads(Path(args.script).read_text(encoding="utf-8"))
        script = data if isinstance(data, list) else data.get("commands", [])

    from world_gal_game.dev.agent_endpoints import build_context
    ctx = build_context(pack_path, script=script, seed=args.seed,
                        full_dataflow=args.full_dataflow)

    if args.format == "json":
        print(_json.dumps(ctx, ensure_ascii=False, indent=2))
        return 0

    # Compact human summary.
    s = ctx["pack"]
    print(f"pack: {s['title']}  ({', '.join(f'{k}={v}' for k, v in s['counts'].items())})")
    r = ctx["reachability"]
    print(f"reachable: {len(r['reachable'])}/"
          f"{len(r['reachable']) + len(r['unreachable'])} scenes; "
          f"endings unreachable={r['endings']['unreachable']}")
    g = ctx["gaps"]
    print(f"gaps: dead_ends={len(g['dead_ends'])} "
          f"undeclared_flags={len(g['undeclared_flags'])} "
          f"unused_declared_flags={len(g['unused_declared_flags'])}")
    return 0


def impact_main(argv: list[str]) -> int:
    """Entry point for ``wgg impact <pack> --symbol <id>`` — change pre-flight."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(
        prog="world-gal-game impact",
        description="Pre-flight a change to a symbol: what reads it, which "
                    "endings/scenes are gated on it (and may become unreachable "
                    "if its writers change), and a planner baseline of which "
                    "at-risk endings are reachable today.")
    p.add_argument("pack", help="pack name or path")
    p.add_argument("--symbol", required=True,
                   help="the flag/scene/item/resource id to analyze")
    p.add_argument("--type", dest="symbol_type", default=None,
                   choices=["flags", "scenes", "items", "resources"],
                   help="restrict to one symbol type (default: auto-detect)")
    p.add_argument("--no-probe", action="store_true",
                   help="skip the planner reachability baseline (faster)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--format", choices=["json", "text"], default="json")
    args = p.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1

    from world_gal_game.dev.agent_endpoints import analyze_impact
    result = analyze_impact(
        pack_path, args.symbol, symbol_type=args.symbol_type,
        probe_reachability=not args.no_probe, seed=args.seed)

    if args.format == "json":
        print(_json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"symbol: {result['symbol']}  (type={result['symbol_type']})")
    print(f"  writers: {len(result['writers'])}  readers: {len(result['readers'])}")
    print(f"  at-risk endings: {result['at_risk_endings'] or 'none'}")
    print(f"  at-risk scenes:  {result['at_risk_scenes'] or 'none'}")
    print(f"  edges referencing: {len(result['edges_referencing'])}")
    if result["reachable_today"] is not None:
        for eid, info in result["reachable_today"].items():
            mark = "reachable" if info.get("found") else "NOT reached"
            print(f"    {eid}: {mark} (depth={info.get('depth')})")
    return 0


def brief_main(argv: list[str]) -> int:
    """Entry point for ``wgg brief <pack>`` — the token-frugal orientation."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(
        prog="world-gal-game brief",
        description="A minimal pack digest (compact scene adjacency + ending "
                    "reachability + key:type variables + gaps) — the cheapest "
                    "read before editing. `--format text` is the tersest form.")
    p.add_argument("pack", help="pack name or path")
    p.add_argument("--format", choices=["json", "text"], default="json")
    args = p.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1

    from world_gal_game.dev.agent_endpoints import pack_brief
    if args.format == "text":
        print(pack_brief(pack_path, as_text=True))
    else:
        print(_json.dumps(pack_brief(pack_path), ensure_ascii=False, indent=2))
    return 0


def card_main(argv: list[str]) -> int:
    """Entry point for ``wgg card <pack> --symbol <id>`` — one-symbol view."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(
        prog="world-gal-game card",
        description="A focused, compact view of one symbol (scene / flag / item "
                    "/ resource / npc / location): edges + guard logic for a "
                    "scene, writers/readers + gated endings for a flag.")
    p.add_argument("pack", help="pack name or path")
    p.add_argument("--symbol", required=True, help="the symbol id to describe")
    args = p.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1

    from world_gal_game.dev.agent_endpoints import symbol_card
    print(_json.dumps(symbol_card(pack_path, args.symbol),
                      ensure_ascii=False, indent=2))
    return 0


def contract_main(argv: list[str]) -> int:
    """Run ``world-gal-game contract <pack>`` behavioural regression checks."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(
        prog="world-gal-game contract",
        description="Check a pack's narrative contract (contracts.yaml): named "
                    "reachable / unreachable / holds / path_reaches expectations "
                    "verified in one call. Exit non-zero if any fail — a "
                    "behavioural regression gate to pair with structural impact.")
    p.add_argument("pack", help="pack name or path")
    p.add_argument("--contract", default=None,
                   help="path to the contract file (default: <pack>/contracts.yaml)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--format", choices=["json", "text"], default="text")
    args = p.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1

    from world_gal_game.dev.contract import check_contract
    report = check_contract(str(pack_path), contract_path=args.contract,
                            seed=args.seed)

    if args.format == "json":
        print(_json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if report.get("no_contract"):
        print(f"contract: none found for {pack_path} (nothing to check)")
        return 0
    print(f"contract: {report.get('contract')}  "
          f"(passed {report['passed']}/{report['total']})")
    for r in report["results"]:
        mark = "ok  " if r["ok"] else "FAIL"
        extra = "" if r["ok"] else f"  {r.get('error') or r.get('detail')}"
        print(f"  [{mark}] {r['name']} ({r['kind']}){extra}")
    return 0 if report["ok"] else 1


def smoke_main(argv: list[str]) -> int:
    """Run every script through ``world-gal-game smoke <pack>``."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(prog="world-gal-game smoke",
                                description="Run every scripts/test_*.json in a pack.")
    p.add_argument("pack", help="pack name or path")
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1
    from world_gal_game.dev.smoke_runner import SmokeRunner
    report = SmokeRunner(pack_path).run()
    if args.format == "json":
        print(_json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for r in report.results:
            status = "ok " if r.ok else "FAIL"
            if r.criterion == "assert":
                detail = f"asserts={r.asserts_passed}/{r.asserts_total}"
            else:
                detail = f"ending={r.ending_flag}"
            print(f"[{status}] {r.script}  ({r.duration_s:.2f}s, {detail})")
            for err in r.errors:
                print(f"        ! {err}")
            for fa in r.failed_asserts:
                print(f"        ✗ assert #{fa['index']}: {fa['assert']} "
                      f"(actual={fa['actual']!r})")
        print()
        passed = sum(1 for r in report.results if r.ok)
        print(f"smoke: {passed}/{len(report.results)} passed")
    return 0 if report.ok else 1


def visual_check_main(argv: list[str]) -> int:
    """Entry point for ``world-gal-game visual-check <pack>``."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(
        prog="world-gal-game visual-check",
        description=(
            "Render scenarios and diff against baselines. Generated candidates "
            "default to <pack>/visual_candidates/visual_check/."
        ),
    )
    p.add_argument("pack", help="pack name or path")
    p.add_argument("--update-baselines", action="store_true",
                   help="overwrite baselines with the current render (use after intended UI changes)")
    p.add_argument(
        "--strict-baselines",
        action="store_true",
        help=(
            "fail when any baseline is missing instead of creating it "
            "(recommended for CI/release gates)"
        ),
    )
    p.add_argument(
        "--scenarios",
        default=None,
        help=(
            "JSON scenario list; view can be title, onboarding, dialogue, choice, "
            "exploration, spatial_map_story_priority, "
            "spatial_map_all_reachable, travel_confirmation, "
            "destination_list, menu, history, save, load, settings, "
            "cg_gallery, music_room (aliases: music, jukebox), "
            "scene_replay (alias: replay), or flowchart. Entries can also "
            "set scene_id/dev_flags/dev_location/dev_time; "
            "falls back to the representative default set"
        ),
    )
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1
    from world_gal_game.dev.visual_check import VisualCheck

    vc = VisualCheck(pack_path, strict_baselines=args.strict_baselines)
    if args.scenarios:
        scenarios = _json.loads(Path(args.scenarios).read_text(encoding="utf-8"))
    else:
        scenarios = VisualCheck.default_scenarios()

    if args.update_baselines:
        # Force-promote: capture, then copy candidate → baseline.
        for sc in scenarios:
            vc.capture_scenario(sc)
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
    """Entry point for ``world-gal-game self-check <pack>``."""
    import argparse
    import json as _json
    p = argparse.ArgumentParser(prog="world-gal-game self-check",
                                description="Full 7-stage pack verification.")
    p.add_argument("pack", help="pack name or path")
    p.add_argument("--skip-smoke", action="store_true")
    p.add_argument("--skip-softlock", action="store_true",
                   help="skip the soft-lock linter stage")
    p.add_argument("--skip-reachability", action="store_true",
                   help="skip the ending-reachability (strand) stage")
    p.add_argument("--reachability-deep", action="store_true",
                   help="also run the slow organic planner replay on top of the "
                        "fast static fixpoint (default is static-only: seconds, "
                        "real ok/strand verdicts). Deep mode can refute a static "
                        "strand and confirm a real start-to-finish path, but most "
                        "endings on a large pack run out their per-ending budget.")
    p.add_argument("--reachability-budget", type=float, default=30.0,
                   help="(--reachability-deep) per-ending wall-clock budget (s)")
    p.add_argument("--reachability-max-nodes", type=int, default=700,
                   help="(--reachability-deep) per-ending node cap")
    p.add_argument("--include-visual", action="store_true",
                   help="also run the visual stage (off by default)")
    p.add_argument(
        "--strict-visual-baselines",
        action="store_true",
        help=(
            "run the visual stage and fail on missing baselines; implies "
            "--include-visual and is intended for CI/release gates"
        ),
    )
    p.add_argument(
        "--visual-scenarios",
        type=Path,
        default=None,
        help="explicit JSON scenario list (pins the visual matrix)",
    )
    p.add_argument(
        "--smoke-seed",
        type=int,
        default=None,
        help="deterministic seed for every smoke script",
    )
    p.add_argument("--no-stop-on-failure", action="store_true",
                   help="run every stage even if earlier ones fail")
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1

    from world_gal_game.dev.self_check import SelfCheck
    visual_scenarios = None
    if args.visual_scenarios is not None:
        visual_scenarios = _json.loads(
            args.visual_scenarios.read_text(encoding="utf-8")
        )
    sc = SelfCheck(
        pack_path,
        stop_on_failure=not args.no_stop_on_failure,
        skip_smoke=args.skip_smoke,
        skip_visual=not (
            args.include_visual or args.strict_visual_baselines
        ),
        strict_visual_baselines=args.strict_visual_baselines,
        visual_scenarios=visual_scenarios,
        smoke_seed=args.smoke_seed,
        skip_softlock=args.skip_softlock,
        skip_reachability=args.skip_reachability,
        reachability_deep=args.reachability_deep,
        reachability_max_nodes=args.reachability_max_nodes,
        reachability_time_budget_s=args.reachability_budget,
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


def runtime_check_main(argv: list[str]) -> int:
    """Run the fresh-process runtime reliability gate.

    This command is intentionally separate from ``self-check``.  It starts
    multiple real App processes and writes host-sensitive release evidence;
    the normal authoring loop remains fast and deterministic.
    """
    import json as _json

    parser = argparse.ArgumentParser(
        prog="world-gal-game runtime-check",
        description=(
            "Run fresh-process startup/first-frame and deterministic semantic "
            "soak probes. Always attempts to write a JSON evidence report."
        ),
    )
    parser.add_argument("pack", help="pack name or path")
    parser.add_argument(
        "--policy",
        type=Path,
        default=None,
        help=(
            "strict runtime policy YAML; defaults to "
            "<pack>/content/runtime_reliability.yaml"
        ),
    )
    parser.add_argument(
        "--report",
        "--output",
        dest="report",
        type=Path,
        default=Path("artifacts/runtime-reliability/report.json"),
        help="JSON evidence artifact path",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    resolved = _resolve_cli_pack(args.pack)
    pack_root = resolved or Path(args.pack).expanduser().resolve()
    policy_path = args.policy or pack_root / "content" / "runtime_reliability.yaml"
    from world_gal_game.dev.runtime_reliability import run_runtime_check

    report = run_runtime_check(
        pack_root,
        policy_path=policy_path,
        report_path=args.report,
    )
    if args.format == "json":
        print(_json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        status = "ok" if report["ok"] else report["status"]
        print(f"runtime-check: {status}")
        print(f"report: {args.report.expanduser().resolve()}")
        startup = report.get("summary", {}).get("fresh_process_startup", {})
        frames = report.get("summary", {}).get("frame_time", {})
        memory = report.get("summary", {}).get("memory", {})
        if startup:
            print(
                "fresh-process app-ready p95: "
                f"{startup.get('app_ready_p95_ms')} ms; first-frame p95: "
                f"{startup.get('first_frame_p95_ms')} ms"
            )
        if frames:
            print(
                f"frame p95/p99/max: {frames.get('p95_ms')} / "
                f"{frames.get('p99_ms')} / {frames.get('max_ms')} ms"
            )
        if memory:
            print(
                f"RSS current/peak max: {memory.get('current_rss_max_bytes')} / "
                f"{memory.get('peak_rss_max_bytes')} bytes"
            )
        for failure in report.get("failures", []):
            print(f"[FAIL] {failure['code']}: {failure['message']}")
    return 0 if report["ok"] else 1


def production_check_main(argv: list[str]) -> int:
    """Run the fail-closed aggregate source-release preflight."""
    import json as _json

    parser = argparse.ArgumentParser(
        prog="world-gal-game production-check",
        description=(
            "Run strict self-check, approved visual, union coverage, narrative "
            "contract, and calibrated runtime gates; always attempt one JSON artifact."
        ),
    )
    parser.add_argument("pack", help="pack name or path")
    parser.add_argument(
        "--policy",
        type=Path,
        default=None,
        help="strict policy; defaults to <pack>/content/production.yaml",
    )
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="aggregate JSON evidence path (must be outside both Git worktrees)",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    resolved = _resolve_cli_pack(args.pack)
    pack_root = resolved or Path(args.pack).expanduser().resolve()
    policy_path = args.policy or pack_root / "content" / "production.yaml"
    from world_gal_game.dev.production_check import run_production_check

    report = run_production_check(
        pack_root,
        policy_path=policy_path,
        report_path=args.report,
    )
    if args.format == "json":
        print(_json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(f"production-check: {report['status']}")
        print(f"report: {args.report.expanduser().resolve()}")
        for name, gate in report.get("gates", {}).items():
            marker = "ok" if gate.get("ok") else "FAIL"
            print(f"[{marker}] {name}")
        for failure in report.get("failures", []):
            print(f"[FAIL] {failure['code']}: {failure['message']}")
    return 0 if report["ok"] else 1


def production_verify_main(argv: list[str]) -> int:
    """Verify a saved production artifact against the current source tree."""
    import json as _json

    parser = argparse.ArgumentParser(
        prog="world-gal-game production-verify",
        description="Verify artifact schema, semantics, freshness, and current inputs.",
    )
    parser.add_argument("report", type=Path, help="production-check JSON artifact")
    parser.add_argument("--pack", required=True, help="current pack name or path")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    pack_path = _resolve_cli_pack(args.pack)
    if pack_path is None:
        return 1
    from world_gal_game.dev.production_check import verify_production_report

    result = verify_production_report(
        args.report,
        pack_root=pack_path,
    )
    if args.format == "json":
        print(_json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print("production-verify: " + ("pass" if result["ok"] else "fail"))
        for failure in result.get("failures", []):
            print(f"[FAIL] {failure['code']}: {failure['message']}")
    return 0 if result["ok"] else 1


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
    if _args[:1] == ["variables"]:
        return variables_main(_args[1:])
    if _args[:1] == ["chapters"]:
        return chapters_main(_args[1:])
    if _args[:1] == ["session"]:
        return session_main(_args[1:])
    if _args[:1] == ["plan"]:
        return plan_main(_args[1:])
    if _args[:1] == ["coverage"]:
        return coverage_main(_args[1:])
    if _args[:1] == ["agent-guide"]:
        return agent_guide_main(_args[1:])
    if _args[:1] == ["docs"]:
        return docs_main(_args[1:])
    if _args[:1] == ["context"]:
        return context_main(_args[1:])
    if _args[:1] == ["impact"]:
        return impact_main(_args[1:])
    if _args[:1] == ["brief"]:
        return brief_main(_args[1:])
    if _args[:1] == ["card"]:
        return card_main(_args[1:])
    if _args[:1] == ["contract"]:
        return contract_main(_args[1:])
    if _args[:1] == ["smoke"]:
        return smoke_main(_args[1:])
    if _args[:1] == ["visual-check"]:
        return visual_check_main(_args[1:])
    if _args[:1] == ["self-check"]:
        return self_check_main(_args[1:])
    if _args[:1] == ["runtime-check"]:
        return runtime_check_main(_args[1:])
    if _args[:1] == ["production-check"]:
        return production_check_main(_args[1:])
    if _args[:1] == ["production-verify"]:
        return production_verify_main(_args[1:])

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
        locale=args.locale,
    )
    if args.text_speed is not None:
        config.text_speed = args.text_speed

    if args.headless:
        from world_gal_game.headless import run_inspect, run_script
        if args.script:
            # Propagate the script's exit code: non-zero when any `assert` op
            # failed or any op errored, so `--script s.json && echo OK` and CI
            # gates actually catch a regression (a silent exit 0 once let a
            # route-strand stay hidden).
            return run_script(config, str(args.script), pack=args.pack,
                              inspect_after=not args.no_inspect_after)
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

    def apply_dev_state() -> None:
        """Apply screenshot overrides to the current, initialized state."""
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
    ds = args.dev_start
    new_game_screens = {
        "explore",
        "map",
        "affection",
        "log",
        "save",
        "settings",
        "menu",
        "achievements",
        "inventory",
    }
    needs_new_game = bool(
        ds
        and (
            ds in new_game_screens
            or ds.startswith("scene:")
            or ds.startswith("npc:")
        )
    )
    if needs_new_game:
        app._start_new_game()
        app.manager.commit_pending()

    # New-game initialization may replace time, location, flags, and route
    # state. Apply screenshot overrides only after that initialization, but
    # before constructing the requested screen so its authored conditions see
    # the final state.
    apply_dev_state()

    if ds:
        if ds == "explore":
            pass
        elif ds.startswith("scene:"):
            app._start_dialogue(ds.split(":", 1)[1])
        elif ds == "map":
            app._open_map()
        elif ds == "affection":
            app._open_affection()
        elif ds == "log":
            app._open_event_log()
        elif ds == "save":
            app._open_save_menu()
        elif ds == "load":
            app._open_load_menu()
        elif ds == "settings":
            app._open_settings()
        elif ds == "menu":
            app._open_menu()
        elif ds == "achievements":
            app.state.achievements.check(app.state)
            app._open_achievements()
        elif ds == "inventory":
            app._open_inventory()
        elif ds.startswith("npc:"):
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
