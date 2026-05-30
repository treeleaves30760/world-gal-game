"""Self-contained agent onboarding bundle — ``wgg agent-guide`` / ``wgg docs``.

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
`world_gal_game.headless.HeadlessSession` — `start_scene`, `next_line`,
`choose`, `move_to`, `advance_time`, `set_flag`, `adjust_affection`, `inspect`,
plus agent state control: `apply` (any effect), `check` / `assert` (conditions),
`snapshot` / `restore` (branch exploration), `affordances` (action space),
`run_script` (rich batch — per-op `diff` + a full execution `transcript`).

For thousands of small ops, prefer the **warm NDJSON session** (faster than
spawning a process or an RPC hop): load the pack once, stream one JSON op per
line. See the session protocol below.

## 2. Inspect engine capabilities (machine-readable)
`wgg capabilities --pack <pack> [--schema]` — every effect / condition / hook /
widget / scene / brain / dialogue-op / portrait-backend kind, with a real
JSON-Schema bundle for offline arg validation. Includes loaded plugins.

## 3. Reason about a pack before editing (world model)
- `wgg brief <pack> [--format text]` — the **lowest-token** orientation: compact
  scene adjacency + ending reachability + key:type variables + routes + gaps
  (~7x smaller than `context`). `wgg card <pack> --symbol <id>` zooms to one
  symbol (scene edges + guard logic, or a flag's writers/readers/gated-endings).
- `wgg variables <pack> [--check]` — declared typed narrative state.
- `wgg chapters <pack> [--check]` — declared chapter/act/route structure.
- `wgg inspect-pack <pack> [--dataflow|--references <sym>]` — structure,
  reachability, dead-ends, graph; flag/scene/item/resource writers+readers.
  Conditioned edges carry a structured `guard_logic` ({all, none}).
- `wgg context <pack>` — **one JSON** aggregating variables + reachability +
  scene graph + dataflow digest + coverage totals + structural gaps.
- `wgg impact <pack> --symbol <id>` — change pre-flight: what reads a symbol,
  which endings/scenes are gated on it, and a planner baseline of which
  at-risk endings are reachable today.
- `wgg plan --pack <pack> --goal '<json>'` — goal-directed BFS to a replayable
  op path (`{"flag":"ending_lover"}` / `{"scene_played":"meet_heroine"}`).
- `wgg coverage <pack> --script <s>` — scene/line/choice/ending coverage.
- `wgg contract <pack>` — check narrative invariants (contracts.yaml): named
  reachable / unreachable / holds / path_reaches expectations, one call.

## 4. Edit packs structurally
`wgg edit <pack> add-scene|add-choice|update-line|add-npc|add-location|add-item|
remove-*` — comment-preserving YAML round-trip, `--dry-run` prints a diff.
Or edit *inside the warm session* with `edit.*` ops: an edit autocommits and
returns its YAML `diff` **plus an `impact` delta** (new dead-ends / unreachable
endings / undeclared flags) in one response — understand → edit → verify in one
warm process. `begin`/`commit`/`rollback` group edits; a batch with
`"atomic": true` is all-or-nothing; `reload` makes edits playable. See the
session protocol.

## 5. Extend the engine (write a plugin, don't edit core)
A plugin registers handlers via nine decorators: `@effect`, `@condition`,
`@hook`, `@inspect_field`, `@widget`, `@scene`, `@brain`, `@dialogue_op`,
`@portrait_backend`. Scan roots: `world_gal_game/plugins_user/`,
`~/.world-gal-game/plugins/`, `<pack>/plugins/`.

## Self-verify
`wgg self-check <pack>` (schema → refs → dead_ends → smoke → visual),
`wgg smoke <pack>`, `wgg visual-check <pack>`.

## Invocation
The console script is `world-gal-game`; in a source checkout use
`uv run python -m world_gal_game.cli <subcommand>`. Pack-path args take a name
(`demo_pack`) or a path; `--pack`-style flags accept the same.

## Determinism & safety
- All `--pack` ops accept `--seed` where state evolves; same seed → same run.
- A failing plugin handler never crashes the engine (`isolate()` degrades it).
- `snapshot`/`restore` is the one branch-exploration mechanism (it also powers
  player rollback).

Run `wgg docs export <dir>` to drop this guide + the capability JSON-Schema +
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
        "start": "wgg session --pack <pack> [--seed N]",
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
            "move", "start_scene", "next", "choose", "chat", "advance_time",
            "set_flag", "adjust_affection", "inspect", "apply", "check",
            "assert", "affordances", "snapshot", "restore",
        ],
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
            "cli": "wgg context <pack> --format json",
            "note": "variables + reachability + scene graph + dataflow digest + "
                    "coverage totals + structural gaps in one JSON blob.",
        },
        {
            "goal": "Orient on the fewest possible tokens",
            "cli": "wgg brief <pack> --format text",
            "note": "Terse outline: compact scene adjacency + ending reachability "
                    "+ key:type variables + gaps. ~7x smaller than `context`. "
                    "Use `wgg card <pack> --symbol <id>` for one symbol's detail.",
        },
        {
            "goal": "Find a replayable path that reaches an ending",
            "cli": "wgg plan --pack <pack> --goal '{\"flag\":\"ending_lover\"}'",
            "note": "Returns {found, path:[ops], depth, nodes_explored}; replay "
                    "`path` with run_script / the session server.",
        },
        {
            "goal": "Pre-flight a change to a flag before editing",
            "cli": "wgg impact <pack> --symbol quest_started",
            "note": "Lists readers, at-risk endings/scenes, and a planner "
                    "baseline of which at-risk endings are reachable today.",
        },
        {
            "goal": "Validate effect/condition args offline",
            "cli": "wgg capabilities --pack <pack> --schema",
            "note": "Per-kind JSON Schemas + content models; validate authored "
                    "YAML without running the engine.",
        },
        {
            "goal": "Drive thousands of ops without per-call load tax",
            "cli": "wgg session --pack <pack>",
            "note": "Warm NDJSON: one JSON op per line. snapshot/restore to "
                    "branch; __inspect__/__affordances__ for state.",
        },
        {
            "goal": "Add a scene and confirm the pack still passes",
            "cli": "wgg edit <pack> add-scene --payload '{...}' --dry-run  &&  "
                   "wgg self-check <pack>",
            "note": "Dry-run prints a diff first; self-check runs schema → refs "
                    "→ dead-ends → smoke.",
        },
        {
            "goal": "Edit a pack and see what broke, in the warm session",
            "cli": 'echo \'{"op":"edit.add_choice","scene_id":"prologue",'
                   '"choice":{"id":"c","text":"…","next_scene":"lake_night"}}\' '
                   "| wgg session --pack <pack>",
            "note": "Autocommits and returns the YAML `diff` + an `impact` delta "
                    "(new dead-ends / unreachable endings / undeclared flags) in "
                    "one response. Wrap several in begin..commit; add atomic:true "
                    "to a batch for all-or-nothing. No separate self-check pass.",
        },
        {
            "goal": "Measure how much of a pack a test exercised",
            "cli": "wgg coverage <pack> --script scripts/test_lover_route.json",
            "note": "scene/line/choice/ending buckets with the unseen ids.",
        },
        {
            "goal": "Guard narrative invariants after an edit",
            "cli": "wgg contract <pack>",
            "note": "Checks contracts.yaml: named reachable / unreachable / "
                    "holds / path_reaches expectations in one call (exit non-zero "
                    "on failure). Behavioural gate to pair with structural impact.",
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
