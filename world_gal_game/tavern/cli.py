"""``world-gal-game tavern …`` — cards, prompt inspector, linter, stage runs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _dump(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _context(pack: str, seed: int | None = None):
    from ..config import EngineConfig
    from .stage import StageContext
    return StageContext.from_pack(pack, seed=seed), EngineConfig().pack_root(pack)


def _linter(ctx):
    from .slop import SlopLinter
    names = [npc.name for npc in ctx.npcs.all()] if ctx.npcs is not None else []
    return SlopLinter(ctx.tavern.style, never_says=ctx.tavern.never_says(),
                      names=names, player_name=ctx.tavern.player_name)


def _cmd_cards(args) -> int:
    ctx, _root = _context(args.pack)
    summary = ctx.tavern.summary()
    if args.format == "json":
        _dump(summary)
        return 0
    print(f"tavern: {summary['root']}  locale={summary['locale']}  "
          f"player={summary['player']}")
    for card in summary["cards"]:
        print(f"  {card['id']:<20} {card['name']:<10} source={card['source']:<18} "
              f"examples={card['examples']} speech={'yes' if card['speech_profile'] else 'no'} "
              f"secrets={card['secrets']} lore={card['lore_entries']}")
    for issue in summary["issues"]:
        print(f"  [issue] {issue['file']}: {issue['message']}")
    return 0


def _cmd_lint(args) -> int:
    from .slop import lines_from_scene
    ctx, _root = _context(args.pack)
    linter = _linter(ctx)
    scenes = ctx.state.story.scenes
    wanted = args.scene or sorted(scenes)
    missing = [sid for sid in wanted if sid not in scenes]
    if missing:
        print(f"[error] unknown scene(s): {', '.join(missing)}", file=sys.stderr)
        return 1
    reports = {sid: linter.lint(lines_from_scene(scenes[sid])) for sid in wanted}
    worst = max((r.score for r in reports.values()), default=0.0)
    if args.format == "json":
        _dump({"worst_score": round(worst, 1),
               "scenes": {sid: r.to_dict() for sid, r in reports.items()}})
    else:
        for sid, report in sorted(reports.items(), key=lambda kv: -kv[1].score):
            print(f"== {sid}")
            print(report.render(limit=args.limit))
    return 1 if args.max_score is not None and worst > args.max_score else 0


def _cmd_prompt(args) -> int:
    from .stage import SceneBrief, Stage
    ctx, _root = _context(args.pack)
    if args.brief:
        brief = SceneBrief.load(args.brief)
        ctx.apply_setup(brief.setup)
    else:
        brief = SceneBrief(id="inspect", cast=[args.who])
    brief = brief.model_copy(update={"order": "round_robin", "narration": "none",
                                     "opening": ""})
    stage = Stage(brief, ctx, seed=0)
    request = stage.next_request()
    while request is not None and request.speaker_id != args.who:
        stage.submit(request.id, "……")
        request = stage.next_request()
    if request is None or stage._last_build is None:
        print(f"[error] '{args.who}' never gets a turn in this brief", file=sys.stderr)
        return 1
    if args.format == "json":
        _dump(stage._last_build.to_dict())
    else:
        print(stage._last_build.render(full=args.full))
    return 0


def _save_state(path: Path, *, pack: str, stage, into_file: str | None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "pack": pack, "into_file": into_file, "stage": stage.to_dict(),
        # Words resolved so far, so the next invocation resolves them the same.
        "memo": stage.ctx.tavern.memo.data,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def _load_state(path: Path):
    from .stage import SceneBrief, Stage
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ctx, root = _context(data["pack"])
    ctx.apply_setup(SceneBrief.model_validate(data["stage"]["brief"]).setup)
    for character, words in (data.get("memo") or {}).items():
        for word, key in words.items():
            ctx.tavern.memo.record(character, word, key)
    return data, Stage.from_dict(data["stage"], ctx), root


def _request_view(stage) -> dict[str, Any]:
    request = stage.next_request()
    if request is None:
        return {"done": True}
    return {"done": False, "request": request.model_dump(exclude={"params"})}


def _report(args, stage, root: Path, into_file: str | None) -> int:
    from ..dev.pack_editor import PackEditError
    from .commit import commit_stage
    try:
        report = commit_stage(stage, root, write=args.write, into_file=into_file)
    except PackEditError as exc:
        # The performance is fine but the pack will not take it as it stands
        # (most often: a scene with this id already exists).
        if args.format == "json":
            _dump({"ok": False, "error": exc.to_dict()})
        else:
            print(f"[error] {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        _dump(report)
        return 0
    print(json.dumps(report["summary"], ensure_ascii=False))
    for row in report.get("lines", []):
        print(f"  L{row['line']} [{row['speaker'] or '-'}] {row['status']}: "
              f"{row['before']}" + (f"  ->  {row['after']}" if "after" in row else ""))
    for warning in report.get("warnings", []):
        print(f"  [warning] {warning}")
    print(report["diff"] or "(no changes)")
    print("written: " + ", ".join(report["files"]) if report["written"]
          else "dry run — pass --write to apply")
    return 0


def _run(args, stage, root: Path) -> int:
    from .backends import BackendError, make_backend, run_stage
    into_file = getattr(args, "into_file", None)
    if (args.backend or "").lower() == "relay":
        # No backend: park the stage in a state file and hand out the first
        # request. `tavern step` answers it and returns the next one.
        if not args.state:
            print("[error] --backend relay needs --state <file>", file=sys.stderr)
            return 1
        view = _request_view(stage)
        _save_state(args.state, pack=args.pack, stage=stage, into_file=into_file)
        _dump({"state": str(args.state), **view})
        return 0
    try:
        backend = make_backend(args.backend, model=args.model, stage=stage)
        run_stage(stage, backend)
    except BackendError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    if not stage.done:
        print("[error] the stage stopped waiting for a human line; "
              "use player.mode: actor or silent for a written scene", file=sys.stderr)
        return 1
    return _report(args, stage, root, into_file)


def _cmd_step(args) -> int:
    from .stage import StageError
    data, stage, root = _load_state(args.state)
    result = None
    if args.submit is not None or args.submit_file is not None:
        text = args.submit if args.submit is not None else \
            Path(args.submit_file).read_text(encoding="utf-8")
        pending = stage.next_request()
        if pending is None:
            print("[error] the stage is already finished", file=sys.stderr)
            return 1
        if args.request_id and args.request_id != pending.id:
            print(f"[error] pending request is '{pending.id}', not "
                  f"'{args.request_id}'", file=sys.stderr)
            return 1
        try:
            result = stage.submit(pending.id, text)
        except StageError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 1
    view = _request_view(stage)
    _save_state(args.state, pack=data["pack"], stage=stage,
                into_file=data.get("into_file"))
    if view["done"] and (args.write or args.result):
        return _report(args, stage, root, data.get("into_file"))
    if result is not None:
        view["last"] = result
    if view["done"]:
        view["hint"] = "finished — run `tavern step <state> --result` to preview, " \
                       "`--write` to apply"
    _dump(view)
    return 0


def _cmd_card_export(args) -> int:
    ctx, root = _context(args.pack)
    card = ctx.tavern.card(args.id)
    if card is None:
        print(f"[error] no card for '{args.id}'", file=sys.stderr)
        return 1
    image = None
    npc = ctx.npc(card.id)
    portrait = getattr(npc, "portrait", None)
    if args.out.suffix.lower() == ".png" and portrait:
        candidate = root / portrait
        if candidate.suffix.lower() == ".png" and candidate.is_file():
            image = candidate.read_bytes()
    card.save(args.out, spec=args.spec, image=image)
    print(f"exported {card.name} -> {args.out}")
    return 0


def _cmd_card_import(args) -> int:
    from .card import CharacterCard
    ctx, root = _context(args.pack)
    try:
        card = CharacterCard.load(args.file, card_id=args.as_id or "")
    except Exception as exc:
        print(f"[error] could not read card: {exc}", file=sys.stderr)
        return 1
    match = ctx.tavern.card(card.id) or ctx.tavern.card(card.name)
    if not args.as_id and match is not None and match.id:
        card.id = match.id            # bind to the pack character of that name
    target = ctx.tavern.root / "cards" / f"{card.id}.yaml"
    if target.exists() and not args.force:
        print(f"[error] {target} exists — pass --force to replace it", file=sys.stderr)
        return 1
    card.save(target)
    bound = "bound to character" if ctx.npc(card.id) is not None \
        else "no character with this id in characters.yaml"
    print(f"imported {card.name} as '{card.id}' -> {target} ({bound})")
    return 0


def _cmd_import_chat(args) -> int:
    from ..dev.pack_editor import PackEditError, PackEditor
    from .chatlog import scene_from_chat
    ctx, root = _context(args.pack)
    try:
        scene, warnings = scene_from_chat(
            args.file, scene_id=args.scene_id, tavern=ctx.tavern, npcs=ctx.npcs,
            assets=ctx.assets, title=args.title, location=args.location)
        editor = PackEditor(root, dry_run=True)
        editor.add_scene(scene, into_file=args.into_file)
    except (ValueError, PackEditError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"[warning] {warning}")
    print(editor.diff())
    if args.write:
        editor.commit()
        ctx.tavern.memo.save()
        print(f"written: {len(scene['lines'])} lines as scene '{scene['id']}'")
    else:
        print("dry run — pass --write to apply")
    return 0


def _cmd_enact(args) -> int:
    from .stage import SceneBrief, Stage
    brief = SceneBrief.load(args.brief)
    ctx, root = _context(args.pack, args.seed)
    ctx.apply_setup(brief.setup)
    return _run(args, Stage(brief, ctx, seed=args.seed), root)


def _cmd_restage(args) -> int:
    from .stage import Stage
    ctx, root = _context(args.pack, args.seed)
    scene = ctx.state.story.get(args.scene)
    if scene is None:
        print(f"[error] unknown scene '{args.scene}'", file=sys.stderr)
        return 1
    stage = Stage.restage(scene, ctx, narration=args.narration,
                          only_flagged=args.only_flagged, seed=args.seed)
    return _run(args, stage, root)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="world-gal-game tavern",
        description="Character cards, prompt inspector, de-slop linter and the "
                    "multi-agent stage.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p) -> None:
        p.add_argument("--backend", default=None,
                       help="anthropic | openai | echo | relay "
                            "(default: config / anthropic)")
        p.add_argument("--state", type=Path, default=None,
                       help="(relay) file the stage is parked in between steps")
        p.add_argument("--model", default=None)
        p.add_argument("--seed", type=int, default=None)
        p.add_argument("--write", action="store_true", help="apply instead of dry run")
        p.add_argument("--format", choices=("text", "json"), default="text")

    p = sub.add_parser("cards", help="list every character's effective card")
    p.add_argument("pack")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=_cmd_cards)

    p = sub.add_parser("lint", help="score scenes for machine-written tells")
    p.add_argument("pack")
    p.add_argument("--scene", action="append", default=[])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--max-score", type=float, default=None,
                   help="exit 1 when any scene scores above this")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=_cmd_lint)

    p = sub.add_parser("prompt", help="show the prompt one character would get")
    p.add_argument("pack")
    p.add_argument("--as", dest="who", required=True, help="character id")
    p.add_argument("--brief", type=Path, default=None)
    p.add_argument("--full", action="store_true", help="print the messages too")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=_cmd_prompt)

    p = sub.add_parser("enact", help="stage a brief into a new scene")
    p.add_argument("pack")
    p.add_argument("--brief", type=Path, required=True)
    p.add_argument("--into-file", default=None)
    common(p)
    p.set_defaults(func=_cmd_enact)

    p = sub.add_parser("restage", help="re-perform an existing scene line by line")
    p.add_argument("pack")
    p.add_argument("--scene", required=True)
    p.add_argument("--narration", action="store_true")
    p.add_argument("--only-flagged", action="store_true")
    common(p)
    p.set_defaults(func=_cmd_restage)

    p = sub.add_parser("step", help="(relay) show or answer the pending request")
    p.add_argument("state", type=Path)
    p.add_argument("--submit", default=None, help="the reply text")
    p.add_argument("--submit-file", type=Path, default=None)
    p.add_argument("--request-id", default=None,
                   help="refuse unless this is the pending request")
    p.add_argument("--result", action="store_true",
                   help="when finished, print the dry-run result")
    p.add_argument("--write", action="store_true", help="when finished, apply it")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.set_defaults(func=_cmd_step)

    p = sub.add_parser("card-export", help="write a character's card to a file")
    p.add_argument("pack")
    p.add_argument("id")
    p.add_argument("--out", type=Path, required=True,
                   help=".yaml (native), .json or .png (tavern card)")
    p.add_argument("--spec", choices=("v2", "v3"), default="v2")
    p.set_defaults(func=_cmd_card_export)

    p = sub.add_parser("card-import", help="add a tavern card (.png/.json/.yaml)")
    p.add_argument("pack")
    p.add_argument("file", type=Path)
    p.add_argument("--as", dest="as_id", default=None, help="character id to bind to")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_cmd_card_import)

    p = sub.add_parser("import-chat", help="turn a tavern chat log into a scene")
    p.add_argument("pack")
    p.add_argument("file", type=Path)
    p.add_argument("--scene-id", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--location", default=None)
    p.add_argument("--into-file", default=None)
    p.add_argument("--write", action="store_true")
    p.set_defaults(func=_cmd_import_chat)

    args = parser.parse_args(argv)
    return int(args.func(args))
