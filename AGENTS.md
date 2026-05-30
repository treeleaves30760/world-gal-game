# AGENTS.md — World Gal-Game Engine

Agent-neutral onboarding for any AI coding tool (Claude Code, Codex, Cursor,
Aider, …). This is the vendor-neutral twin of [CLAUDE.md](CLAUDE.md); the two
stay in sync. Strategy: [ROADMAP.md](ROADMAP.md). Engine internals:
[docs/architecture.md](docs/architecture.md). Machine map: [llms.txt](llms.txt).

If you are running inside a `pip install`-ed engine (no repo checkout, so this
file and `docs/` are not on disk), regenerate the essentials from code:

```
world-gal-game agent-guide                 # this guide, from code
world-gal-game docs export <dir>           # guide + capability schema + protocol + recipes
world-gal-game capabilities --schema       # JSON-Schema bundle
```

---

## What this is

**This is an engine, not a game.** A game is a *pack*, shipped independently and
loaded from `games/<pack>/`, a sibling `../<pack>/`,
`~/.world-gal-game/packs/<pack>/`, or any `--pack <path>`. The engine
(`world_gal_game/`) carries **zero game-specific logic** — any change under
`games/` is not an engine change. New themes go through plugins, never core
edits.

## Three pillars

- **A. Humans author games** — YAML pack model, scaffold tool, references.
- **B. AI develops games** — play, inspect, reason, edit, extend, verify.
- **C. Third-party plugins extend the engine** — ten extension categories, no
  core edits.

---

## Invocation

The console script is `world-gal-game`. In a source checkout you may also use
`uv run python -m world_gal_game.cli <subcommand>` (the `wgg` shorthand used in
docs is not a registered script). Subcommands take a pack **name** (`demo_pack`)
or a **path**; where state evolves, pass `--seed N` for determinism
(`EngineConfig.seed` pins `GameState.rng()`).

```bash
uv run pytest tests/                                    # unit tests
uv run python -m world_gal_game.cli self-check demo_pack
uv run python -m world_gal_game.cli context games/demo_pack   # one-call orientation
```

---

## Interfaces you (the AI) can use

### 1. Play / inspect a running game
`world_gal_game.headless.HeadlessSession` — `start_scene`, `next_line`,
`choose`, `move_to`, `advance_time`, `set_flag`, `adjust_affection`, `inspect`,
plus agent state control: `apply` (any effect), `check` / `assert`,
`snapshot` / `restore` (branch exploration), `affordances` (action space),
`run_script` (rich batch → per-op `diff` + execution `transcript`). Pure
Python, no display. See [docs/headless.md](docs/headless.md),
[docs/ai-native-contract.md](docs/ai-native-contract.md).

For thousands of small ops, use the **warm NDJSON session** — load the pack
once, stream one JSON op per line over stdio. Faster than spawning a process or
an RPC hop. See [docs/session-protocol.md](docs/session-protocol.md).

CLI: `--headless --inspect`, `--headless --script <json>`, `--screenshot out.png`,
`debug <script.json>`.

### 2. Inspect engine capabilities (machine-readable)
`world_gal_game.dev.capability_manifest` — `build_manifest()`, `manifest_json()`,
`schema_json()`, `summary_table()`. CLI: `capabilities --pack <pack> [--schema]`
(JSON-Schema bundle for offline arg validation; includes loaded plugins).

### 3. Reason about a pack before editing (world model)
- `variables <pack> [--check]` — typed declared narrative state.
- `inspect-pack <pack> [--dataflow | --references <sym>]` — structure,
  reachability, dead-ends, graph; flag/scene/item/resource writers+readers.
- **`context <pack>`** — one JSON aggregating variables + reachability + scene
  graph + dataflow digest + coverage totals + structural gaps. The
  lowest-token way to orient before an edit.
- **`impact <pack> --symbol <id>`** — change pre-flight: readers, endings/scenes
  gated on the symbol, and a planner baseline of which at-risk endings are
  reachable today.
- `plan --pack <pack> --goal '<json>'` — goal-directed BFS to a replayable op
  path.
- `coverage <pack> --script <s>` — scene/line/choice/ending coverage.

See [docs/ai-native-world-model.md](docs/ai-native-world-model.md).

### 4. Edit packs structurally
`world_gal_game.dev.pack_editor.PackEditor` — `add_scene`, `add_choice`,
`update_line`, `add_npc`/`add_location`/`add_item`, `remove_*`.
Comment-preserving (ruamel.yaml), `--dry-run` + `diff()`. CLI: `edit <pack> …`.

### 5. Extend the engine (write a plugin, don't edit core)
A plugin (`plugins/<id>/` + `plugin.yaml` + entry module) registers handlers via
ten decorators: `@effect`, `@condition`, `@hook`, `@inspect_field`, `@widget`,
`@scene`, `@brain`, `@dialogue_op`, `@portrait_backend`, `@ambient_backend`.
16 lifecycle
`HookEvent`s. Scan roots: `world_gal_game/plugins_user/`,
`~/.world-gal-game/plugins/`, `<pack>/plugins/`. See
[docs/plugins.md](docs/plugins.md),
[docs/ai-developer-guide.md](docs/ai-developer-guide.md).

### Self-verify
`world_gal_game.dev.self_check.SelfCheck` (schema → refs → dead_ends → smoke →
visual). CLI: `self-check <pack>`, `smoke <pack>`, `visual-check <pack>`.

---

## Core principles

- **No game-specific logic in core.** Add effects/conditions via
  `@effect` / `@condition`, not core edits — `GameState.apply` / `evaluate` is
  pure registry dispatch.
- **No decorative emoji** — not in UI, docs, commit messages, or output.
  Functional symbols (arrows, `→`, `✓` in tables) are fine.
- **pydantic v2 + YAML packs.** Model first, then serialize.
- **Engine changes affect every pack.** Before changing core schema, confirm
  `smoke demo_pack` still passes; schema changes need a `pack_format_version`
  bump + migration (`core/pack_migration.py`).
- **A failing plugin handler must not crash the engine** — every handler call is
  wrapped in `isolate()`; your handler should also try/except on its own.

---

## Key files

| Goal | Location |
|---|---|
| Engine internals | `docs/architecture.md` |
| AI-Coding-Native contract | `docs/ai-native-contract.md` |
| AI-native world model | `docs/ai-native-world-model.md` |
| NDJSON session protocol | `docs/session-protocol.md` |
| AI developer guide | `docs/ai-developer-guide.md` |
| Pack format | `docs/pack-format.md` |
| Effect / condition lists | `docs/{effects,conditions}-reference.md` (or `capabilities`) |
| GameState central dispatch | `world_gal_game/core/game_state.py` |
| Pack loading entry point | `world_gal_game/content_loader.py` |
| Capability manifest | `world_gal_game/dev/capability_manifest.py` |
| Dataflow / cross-reference | `world_gal_game/dev/dataflow.py` |
| Warm NDJSON session | `world_gal_game/dev/session_server.py` |
| Goal-directed planner | `world_gal_game/dev/planner.py` |
| Coverage tracker | `world_gal_game/dev/coverage.py` |
| Aggregate endpoints (context / impact) | `world_gal_game/dev/agent_endpoints.py` |
| Onboarding bundle (agent-guide / docs export) | `world_gal_game/dev/agent_bundle.py` |
| Pack structure analysis / editing | `world_gal_game/dev/{pack_inspector,pack_editor}.py` |
| Five-stage self-check | `world_gal_game/dev/self_check.py` |
| Example plugin | `games/demo_pack/plugins/step_counter/` |
