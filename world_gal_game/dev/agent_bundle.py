"""Self-contained agent onboarding bundle — ``world-gal-game agent-guide`` /
``world-gal-game docs``.

When the engine is ``pip install``-ed, the repo's ``docs/`` tree and
``AGENTS.md`` do **not** ship inside the wheel (they live at the repo root, not
under the ``world_gal_game`` package). An agent that imports the installed
package therefore can't ``Read`` any of the onboarding material — it only has
the importable code. This module closes that gap: it *generates* the essential
agent-facing artifacts from code (plus the live capability manifest), so the
same bundle is available whether the engine is a source checkout or a wheel.

Two entry points back the CLI:

- :func:`agent_guide_text` — a compact, agent-neutral quickstart (the
  importable twin of ``AGENTS.md``): the interfaces, the fast NDJSON path, and
  the verbs an agent uses to play / inspect / reason / edit / verify.
- :func:`build_bundle` — a dict of ``filename -> content`` covering the guide,
  the capability manifest + its JSON-Schema bundle, the NDJSON session-protocol
  schema, and a recipes catalogue. :func:`export_bundle` writes it to a
  directory or emits it as one JSON object on stdout.

Everything here is derived from code or the capability manifest, never from
reading ``docs/`` — so it is correct for an installed wheel.
"""
from __future__ import annotations

import json
from pathlib import Path


# ----------------------------------------------------------------------
# Agent guide (importable twin of AGENTS.md)


def agent_guide_text() -> str:
    """Return the agent-neutral onboarding guide as Markdown."""
    return _AGENT_GUIDE.strip() + "\n"


_AGENT_GUIDE = """
# World Gal-Game — Agent Guide

This is an **engine**, not a game. A game is a *pack* (a directory with a
`content/` tree). The engine carries zero game-specific logic; everything a
theme needs is added through plugins, not core edits.

You (an AI developer) are a first-class user. Five interface tiers, all
headless and deterministic (`EngineConfig(seed=...)` pins `GameState.rng()`):

## 1. Play / inspect a running game
`world_gal_game.headless.HeadlessSession` — player-equivalent `move_to`,
`examine`, `gift`, `next_line`, `choose`, `advance_time`; developer controls
`start_scene`, `apply`, `set_flag`, `adjust_affection`; plus `inspect`,
`check` / `assert` (conditions), `snapshot` / `restore` (branch exploration),
`affordances` (action space), and `run_script` (rich batch — per-op `diff` + a
full execution `transcript`).

`move_to`, `examine`, and `gift` reject while dialogue is open. `examine`
revalidates a current-location `trigger: examine` hook; `gift` revalidates an
owned, unlocked item and an NPC present now. Examine/gift disappear, map travel
clears, and exits become unavailable in structured affordances at that boundary.
`advance_time(phases=N)` advances phase by phase, stops at the first eligible
`enter` / `auto` hook, and reports the actual `phases_advanced`. `start_scene`
and raw `apply` bypass those player constraints, so use them for focused
developer tests, not natural-reachability evidence.

For thousands of small ops, prefer the **warm NDJSON session** (faster than
spawning a process or an RPC hop): load the pack once, stream one JSON op per
line. See the session protocol below.

## 2. Inspect engine capabilities (machine-readable)
`world-gal-game capabilities --pack <pack> [--schema]` — every effect /
condition / hook / inspect-field / widget / scene / brain / dialogue-op /
portrait-backend / ambient-backend kind, with a real JSON-Schema bundle for
offline arg validation. Includes loaded plugins.

## 3. Reason about a pack before editing (world model)
- `world-gal-game brief <pack> [--format text]` — the **lowest-token** orientation: compact
  scene adjacency + ending reachability + key:type variables + routes + gaps
  (~7x smaller than `context`). `world-gal-game card <pack> --symbol <id>` zooms to one
  symbol (scene edges + guard logic, or a flag's writers/readers/gated-endings).
- `world-gal-game variables <pack> [--check]` — declared typed narrative state.
- `world-gal-game chapters <pack> [--check]` — declared chapter/act/route structure.
- `world-gal-game inspect-pack <pack> [--dataflow|--references <sym>]` — structure,
  reachability, dead-ends, graph; flag/scene/item/resource writers+readers.
  Conditioned edges carry a structured `guard_logic` ({all, none}).
- `world-gal-game context <pack>` — **one JSON** aggregating variables + reachability +
  scene graph + dataflow digest + coverage totals + structural gaps.
- `world-gal-game impact <pack> --symbol <id>` — change pre-flight: what reads a symbol,
  which endings/scenes are gated on it, and a planner baseline of which
  at-risk endings are reachable today.
- `world-gal-game plan --pack <pack> --goal '<json>'` — goal-directed BFS to a replayable
  op path (`{"flag":"ending_lover"}` / `{"scene_played":"meet_heroine"}`).
- `world-gal-game coverage <pack> --script <s>` — scene/line/choice/ending coverage.
- `world-gal-game contract <pack>` — check narrative invariants (contracts.yaml): named
  reachable / unreachable / holds / path_reaches expectations, one call.

## 4. Edit packs structurally
`world-gal-game edit <pack> add-scene|add-choice|update-line|add-npc|add-location|add-item|
remove-*` — comment-preserving YAML round-trip, `--dry-run` prints a diff.
Or edit *inside the warm session* with `edit.*` ops: an edit autocommits and
returns its YAML `diff` **plus an `impact` delta** (new dead-ends / unreachable
endings / undeclared flags) in one response — understand → edit → verify in one
warm process. `begin`/`commit`/`rollback` group edits; a batch with
`"atomic": true` is all-or-nothing; `reload` makes edits playable. See the
session protocol.

## 5. Extend the engine (write a plugin, don't edit core)
A plugin registers handlers via ten decorators: `@effect`, `@condition`,
`@hook`, `@inspect_field`, `@widget`, `@scene`, `@brain`, `@dialogue_op`,
`@portrait_backend`, `@ambient_backend`. Scan roots: `world_gal_game/plugins_user/`,
`~/.world-gal-game/plugins/`, `<pack>/plugins/`.
`HookEvent` defines 18 names; 17 are currently emitted. `app.frame` is reserved
and is not fired automatically.

## Music Room pack contract
Packs may author optional `content/music_room.yaml` in wrapper (`entries:`),
top-level list, or compact asset-keyed mapping form. Read the exact union schema
from `capabilities --schema` at
`content_files["content/music_room.yaml"]`. Each entry has canonical `asset`,
required `title`, optional `artist`, `usage`, integer `order`, boolean `hidden`,
and optional stable `loc_id`. Localized copy uses
`music_room.<loc_id>.{title,artist,usage}`; without `loc_id`, the engine derives
a deterministic asset-digest segment.

An absent file preserves legacy first-level `assets/bgm/` discovery and
filename labels. An authored file controls the locked roster: `hidden` locked
tracks are omitted and excluded from the visible total; authorized unlocked
tracks missing from the catalog append as legacy fallbacks. Save/ClearData store
only heard canonical asset identities, never metadata. Load, quickload, new game,
and cross-playthrough ClearData merge intersect those identities with the
current pack's media authority, so retired, absolute, traversal, or hand-edited
paths cannot reach the decoder.

The Music Room consumes semantic navigation/page/focus/confirm/back actions,
exposes visible focus and 44 px minimum targets, and restores its captured BGM,
ambient, and voice snapshot on every exit path. The default visual gate captures
the view at 720p and 1080p; source production evidence requires both approved
baselines but does not replace real-audio, device, controller, touch, or human
accessibility review.

## Self-verify
`world-gal-game self-check <pack>` (schema → refs → dead_ends → softlock →
reachability → smoke → optional visual), `world-gal-game smoke <pack>`,
`world-gal-game visual-check <pack>`.
For authoring, `visual-check --strict-baselines` and
`self-check --strict-visual-baselines` fail instead of approving a missing
baseline. For a source release, use `world-gal-game production-check <pack>`:
it has no skip or baseline-promotion path and requires all seven self-check
stages, 17 semantic views at 720p/1080p (34 approved PNGs), 100% union coverage,
a non-empty narrative contract, calibrated runtime limits, and clean tracked
Git inputs. `world-gal-game production-verify <report> --pack <pack>` rechecks
the expiring artifact against current sources. This is source preflight, not
packaged-target, device, accessibility, legal, or human-identity proof.

## Invocation
The console script is `world-gal-game`; in a source checkout use
`uv run python -m world_gal_game.cli <subcommand>`. Developer pack arguments
use the runtime name/path resolver. `VisualCheck` preserves the resolved
absolute pack path, so packs outside configured search roots and same-named
packs in different directories are captured without basename ambiguity.

## Determinism & safety
- All `--pack` ops accept `--seed` where state evolves; same seed → same run.
- A failing plugin handler never crashes the engine (`isolate()` degrades it).
- `snapshot`/`restore` is the one branch-exploration mechanism (it also powers
  player rollback).

Run `world-gal-game docs export <dir>` to drop this guide + the capability JSON-Schema +
the session-protocol schema + a recipes catalogue into a directory (works even
when pip-installed).
""".strip()


# ----------------------------------------------------------------------
# NDJSON session protocol — machine-readable spec


def session_protocol_schema() -> dict:
    """Return the warm NDJSON session protocol as a machine-readable dict.

    Mirrors :mod:`world_gal_game.dev.session_server`. Authoritative for the
    line framing, the three message shapes, batch atomicity, and the error
    envelope. Kept in code so it ships in the wheel and stays in lockstep with
    the server (the prose copy lives in ``docs/session-protocol.md``).
    """
    return {
        "transport": "NDJSON over stdin/stdout (one JSON value per line)",
        "start": "world-gal-game session --pack <pack> [--seed N]",
        "framing": {
            "request": "one JSON object per non-blank line; blank lines ignored",
            "response": "one JSON object per request line, with a monotonic "
                        "integer `seq` (one per non-blank input line) and an "
                        "`ok` boolean",
            "seq": "starts at 1, increments per non-blank line; blank lines do "
                   "not advance it",
        },
        "message_shapes": [
            {
                "shape": "control",
                "match": "object whose `op` is a __dunder__ verb",
                "ops": {
                    "__ping__": "{ok, seq, pong:true} — liveness",
                    "__inspect__": "{ok, seq, snapshot:<inspect()>} — full state",
                    "__affordances__": "{ok, seq, affordances:<affordances()>}",
                    "__reset__": "rebuild a fresh session ({ok,seq,reset:true}); "
                                 "error if no opener configured",
                    "__quit__": "{ok, seq, bye:true} then the server stops",
                    "__begin__": "open a structural-edit transaction ({ok,seq,tx:'begin'})",
                    "__commit__": "write staged edits; {ok,seq,tx:'commit',diff,impact}",
                    "__rollback__": "discard staged edits ({ok,seq,tx:'rollback',discarded})",
                    "__reload__": "rebuild the in-memory pack from disk so runtime "
                                  "ops see committed edits ({ok,seq,reloaded:true,scenes})",
                },
            },
            {
                "shape": "batch",
                "match": "object with an `ops` array",
                "request": {"ops": [{"op": "...", "...": "..."}],
                            "atomic": "optional bool — all-or-nothing across "
                                      "state + edits (see atomicity)"},
                "response": {"ok": "all ops ok", "seq": "int",
                             "results": "one result per op (each with its own "
                                        "`ok`, a `changed` bool, and a `diff` "
                                        "when state changed)",
                             "transcript": "ordered execution trace of the batch"},
            },
            {
                "shape": "single",
                "match": "any other object (treated as a one-op batch)",
                "request": {"op": "move", "location": "cafe"},
                "response": {"ok": "bool", "seq": "int", "result": "the op result",
                             "transcript": "ordered execution trace"},
            },
        ],
        "ops": [
            "move", "examine", "gift", "start_scene", "next", "choose",
            "chat", "advance_time", "set_flag", "adjust_affection", "inspect",
            "apply", "check", "assert", "affordances", "snapshot", "restore",
            "locale",
        ],
        "runtime_op_semantics": {
            "player_equivalent": {
                "move": {
                    "args": {
                        "location": "location id",
                        "distant": "optional bool; default false",
                    },
                    "constraints": "no open dialogue; destination must be "
                                   "reachable through the currently unlocked "
                                   "local or graph travel contract",
                },
                "examine": {
                    "args": {"scene": "scene id"},
                    "constraints": "no open dialogue; the scene must be an "
                                   "available trigger:examine hook at the "
                                   "current location and satisfy time, flags, "
                                   "once, and scene-existence checks",
                },
                "gift": {
                    "args": {
                        "npc": "npc id",
                        "item": "item id",
                        "count": "optional; must equal 1",
                    },
                    "constraints": "no open dialogue; NPC must be present at "
                                   "the current location/day/time; item must be "
                                   "known, owned, and unlocked",
                },
                "advance_time": {
                    "args": {"phases": "optional positive int; default 1"},
                    "constraints": "no open dialogue",
                    "semantics": "advance one phase at a time, rechecking "
                                 "action policy; stop and open the first "
                                 "eligible enter/auto hook",
                    "result": "phases_advanced is the actual amount and may "
                              "be less than requested",
                },
            },
            "developer_control": {
                "start_scene": "opens a scene without player hook/location/"
                               "time availability checks",
                "apply": "applies any registered effect directly",
                "set_flag": "writes narrative state directly",
                "adjust_affection": "writes relationship state directly",
                "locale": "changes presentation language only at a boundary "
                          "with no active dialogue; accepts an authored locale id",
            },
            "affordances": {
                "examine": "structured rows {op,scene,title}",
                "gift": "structured rows {op,npc,npc_name,item,item_name,"
                        "count:1,owned}",
                "dialogue_boundary": "examine and gift arrays are empty while "
                                     "dialogue is open; map is unavailable, "
                                     "exits are disabled, and travel rows clear",
            },
        },
        "edit_ops": {
            "verbs": [
                "edit.add_scene", "edit.update_scene", "edit.remove_scene",
                "edit.add_choice", "edit.update_line", "edit.add_npc",
                "edit.update_npc", "edit.remove_npc", "edit.add_location",
                "edit.update_location", "edit.remove_location", "edit.add_item",
                "edit.add_quest", "edit.add_clue", "edit.add_achievement",
                "edit.add_resource",
            ],
            "transaction": ["begin", "commit", "rollback", "reload"],
            "autocommit": "outside begin..commit, an edit.* op stages + writes + "
                          "returns its YAML `diff` and an `impact` delta in one "
                          "response; inside a transaction it only stages "
                          "({staged:true}) and `commit` reports one aggregate impact",
            "impact": "world_model.world_delta: {clean:bool, regressions:[{kind,"
                      "items}], improvements:[…], scenes_added, newly_unreachable_"
                      "endings, new_dead_ends, new_undeclared_flags, counts_delta}. "
                      "regression kinds: unreachable_endings, unreachable_scenes, "
                      "dead_ends, undeclared_flags, orphan_scenes",
            "errors": "a bad payload returns {ok:false, error:{op,field,expected,"
                      "got,hint}} and writes nothing",
            "reload": "edits never auto-reload the live session; send `reload` "
                      "(or __reload__) before playing/planning the edited pack",
        },
        "atomicity": {
            "model": "best-effort sequential, NON-atomic by default",
            "on_op_error": "the failing op's result is {ok:false, error:<msg>}; "
                           "the batch CONTINUES with the next op — there is no "
                           "automatic rollback of earlier ops",
            "atomic_batch": "add `\"atomic\": true` to a batch for all-or-nothing "
                            "across BOTH state and pack edits: a runtime snapshot "
                            "+ edit transaction are taken up front, then committed "
                            "on success ({atomic:'committed', impact}) or discarded "
                            "on any failure ({atomic:'rolled_back'})",
            "manual_rollback": "for runtime-only batches, take a `snapshot` before "
                               "and `restore` it if any result has ok:false",
        },
        "errors": {
            "envelope": {"ok": False, "seq": "int", "error": "<message>"},
            "covers": "JSON parse errors, unrecognized message shapes, unknown "
                      "control ops, and engine exceptions — the server never "
                      "raises out of `handle`",
        },
        "determinism": "open with --seed N to pin GameState.rng(); the same "
                       "seed + same op stream reproduces the run",
    }


# ----------------------------------------------------------------------
# Recipes catalogue


def recipes() -> list[dict]:
    """Return a catalogue of common agent task recipes (op sequences / CLI)."""
    return [
        {
            "goal": "Orient on an unfamiliar pack with one call",
            "cli": "world-gal-game context <pack> --format json",
            "note": "variables + reachability + scene graph + dataflow digest + "
                    "coverage totals + structural gaps in one JSON blob.",
        },
        {
            "goal": "Orient on the fewest possible tokens",
            "cli": "world-gal-game brief <pack> --format text",
            "note": "Terse outline: compact scene adjacency + ending reachability "
                    "+ key:type variables + gaps. ~7x smaller than `context`. "
                    "Use `world-gal-game card <pack> --symbol <id>` for one symbol's detail.",
        },
        {
            "goal": "Find a replayable path that reaches an ending",
            "cli": "world-gal-game plan --pack <pack> --goal '{\"flag\":\"ending_lover\"}'",
            "note": "Returns {found, path:[ops], depth, nodes_explored}; replay "
                    "`path` with run_script / the session server.",
        },
        {
            "goal": "Pre-flight a change to a flag before editing",
            "cli": "world-gal-game impact <pack> --symbol quest_started",
            "note": "Lists readers, at-risk endings/scenes, and a planner "
                    "baseline of which at-risk endings are reachable today.",
        },
        {
            "goal": "Validate effect/condition args offline",
            "cli": "world-gal-game capabilities --pack <pack> --schema",
            "note": "Per-kind JSON Schemas + content models; validate authored "
                    "YAML without running the engine.",
        },
        {
            "goal": "Drive thousands of ops without per-call load tax",
            "cli": "world-gal-game session --pack <pack>",
            "note": "Warm NDJSON: one JSON op per line. snapshot/restore to "
                    "branch; __inspect__/__affordances__ for state.",
        },
        {
            "goal": "Add a scene and confirm the pack still passes",
            "cli": "world-gal-game edit <pack> add-scene --payload '{...}' --dry-run  &&  "
                   "world-gal-game self-check <pack>",
            "note": "Dry-run prints a diff first; self-check runs schema → refs "
                    "→ dead-ends → softlock → reachability → smoke, with visual "
                    "available as an opt-in strict release gate.",
        },
        {
            "goal": "Edit a pack and see what broke, in the warm session",
            "cli": 'echo \'{"op":"edit.add_choice","scene_id":"prologue",'
                   '"choice":{"id":"c","text":"…","next_scene":"lake_night"}}\' '
                   "| world-gal-game session --pack <pack>",
            "note": "Autocommits and returns the YAML `diff` + an `impact` delta "
                    "(new dead-ends / unreachable endings / undeclared flags) in "
                    "one response. Wrap several in begin..commit; add atomic:true "
                    "to a batch for all-or-nothing. No separate self-check pass.",
        },
        {
            "goal": "Measure how much of a pack a test exercised",
            "cli": "world-gal-game coverage <pack> --script scripts/test_lover_route.json",
            "note": "scene/line/choice/ending buckets with the unseen ids.",
        },
        {
            "goal": "Guard narrative invariants after an edit",
            "cli": "world-gal-game contract <pack>",
            "note": "Checks contracts.yaml: named reachable / unreachable / "
                    "holds / path_reaches expectations in one call (exit non-zero "
                    "on failure). Behavioural gate to pair with structural impact.",
        },
        {
            "goal": "Run and independently re-verify source-release evidence",
            "cli": "world-gal-game production-check <pack> --report "
                   "/tmp/world-gal-game-evidence/production.json  &&  "
                   "world-gal-game production-verify "
                   "/tmp/world-gal-game-evidence/production.json --pack <pack>",
            "note": "Fail-closed exact self-check, approved 34-image visual, "
                    "100% union coverage, non-empty contract and calibrated "
                    "runtime gates bound to clean tracked source digests. "
                    "Source preflight only; it is not target-release proof.",
        },
    ]


# ----------------------------------------------------------------------
# Bundle assembly


def build_bundle(*, manager=None) -> dict[str, str]:
    """Assemble the onboarding bundle as ``filename -> content`` strings.

    Self-contained: every artifact is generated from code or the live
    capability manifest, so the bundle is identical for a source checkout and
    an installed wheel. ``manager`` is an optional loaded
    :class:`~world_gal_game.plugins.manager.PluginManager` so a pack's plugins
    show up in the capability manifest.
    """
    from .capability_manifest import manifest_json, schema_json

    return {
        "agent-guide.md": agent_guide_text(),
        "capabilities.json": manifest_json(manager=manager),
        "capabilities.schema.json": schema_json(),
        "session-protocol.json": json.dumps(
            session_protocol_schema(), ensure_ascii=False, indent=2),
        "recipes.json": json.dumps(recipes(), ensure_ascii=False, indent=2),
    }


def export_bundle(dest: str, *, manager=None) -> list[str]:
    """Write the bundle to ``dest`` (a directory), or to stdout if ``dest`` is ``-``.

    Returns the list of written paths (or ``["<stdout>"]`` for the stdout form).
    For ``-`` the whole bundle is emitted as one JSON object
    (``{filename: content}``) so it can be piped into another tool.
    """
    bundle = build_bundle(manager=manager)
    if dest == "-":
        print(json.dumps(bundle, ensure_ascii=False, indent=2))
        return ["<stdout>"]

    out_dir = Path(dest).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, content in bundle.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return written
