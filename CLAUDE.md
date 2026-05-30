# CLAUDE — World Gal-Game Engine

Onboarding notes for AI collaborators (Claude Code, Codex, etc.).
Strategy and roadmap: [ROADMAP.md](ROADMAP.md). Engine internals:
[docs/architecture.md](docs/architecture.md). The agent-neutral twin of this
file is [AGENTS.md](AGENTS.md); the machine map is [llms.txt](llms.txt). When
running pip-installed (no repo checkout), regenerate the essentials from code:
`world-gal-game agent-guide`, `world-gal-game docs export <dir>`,
`world-gal-game capabilities --schema`.

---

## What this is

**This is an engine, not a game.** A game is a "pack," shipped independently of
the engine and loaded from any of:

- `games/<pack>/` — bundled with the engine (`demo_pack` lives here)
- `../<pack>/` — sibling repo
- `~/.world-gal-game/packs/<pack>/` — per-user
- anywhere passed via `--pack <path>`

The engine repo is `world_gal_game/`: pygame runtime, core dataclasses
(pydantic v2), scene framework, dialogue engine, UI, headless driver, save
system, plugin system. The engine carries **zero game-specific logic** — any
change under `games/` is not an engine change.

---

## Three pillars

The goal is not only for humans to build games, but for AI to act as a
first-class developer throughout:

- **A. Humans author games** — YAML pack model, scaffold tool, effect/condition
  references.
- **B. AI develops games** — play, inspect, edit, extend, verify, generate. All
  available today except autonomous spec-to-pack (deferred to Phase 3).
- **C. Third-party plugins extend the engine** — add effects, conditions, hooks,
  inspect fields, widgets, scenes, brains, and dialogue ops without touching
  core.

---

## Current focus

A **distribution and platform push is in the working tree** (web via
pygbag/WASM, Steam, Android/mobile + PWA, rich text, voice, touch and responsive
rendering, pack migration). The **AI-Coding-Native contract has landed** — read
[docs/ai-native-contract.md](docs/ai-native-contract.md):

- Typed effect/condition arg models with real **JSON-Schema export**
  (`wgg capabilities --schema`); `wgg validate` does warning-level arg checks.
- `run_script` gained `apply` / `check` / `assert` / `affordances` /
  `snapshot` / `restore` ops, per-op state **diff**, and an execution
  **trace** (`transcript`).
- **Determinism**: `EngineConfig.seed` → `GameState.rng()`.
- The three former plugin loose ends are **closed**: `plugin.yaml` declares all
  nine extension categories (manager reconciles & warns); references generate
  from `build_manifest()` (`tools/gen_references.py`); `wgg edit` / `wgg
  validate` return "did you mean" hints.
- **Animated portraits (Phase 5A) have landed**: a 9th extension category,
  `@portrait_backend`, is the seam between *which* portrait (`PortraitSpec`)
  and *how it moves* once it settles. `PortraitSpec.backend` (default
  `"static"` = the unchanged blit) routes a portrait's resting animation
  through a registered backend; the bundled `animated_portraits` plugin ships
  web-safe `breath` (procedural idle), `sprite` (sheet frames), and `layered`
  (blink + lip-sync + breathing on stacked PNGs — the flagship cross-platform
  rig) backends. Native rigs (Live2D/Spine) are a documented desktop-only
  plugin path, not core. See `docs/galgame-maturity.md`.

The **AI-native world model (Phase 6) has landed** — read
[docs/ai-native-world-model.md](docs/ai-native-world-model.md). It extends the
contract from *verbs* to *reasoning*: a typed `VariableManifest`
(`content/variables.yaml`), a `DataflowAnalyzer` (`flag/scene/item/resource →
{writers, readers}` + conditioned scene edges), a warm NDJSON `SessionServer`
(load-once, stream ops over stdio — the faster-than-MCP path), a goal-directed
`Planner`, and a `CoverageTracker`. The same `dev/diff` snapshot/restore the
agent layer uses for branch exploration now also powers **player rollback**
(`core/history.py`, Backspace in the dialogue scene) — one mechanism, two
audiences.

See [ROADMAP.md](ROADMAP.md) for the full picture.

---

## Interfaces you (the AI) can use

### Play / inspect a running game

- `world_gal_game.headless.HeadlessSession` — high-level ops (`start_scene`,
  `next_line`, `choose`, `move_to`, `advance_time`, `set_flag`,
  `adjust_affection`, `inspect`, `run_script`) **plus agent state control**:
  `apply` (any effect), `check` / `assert` (conditions / expectations),
  `snapshot` / `restore` (branch exploration), `affordances` (action space).
  `run_script` returns per-op `diff`s and collects an execution `transcript`.
  **Plus a warm structural-edit loop**: `edit.*` ops (add_scene / add_choice /
  update_line / add_npc / add_location / …) stage a comment-preserving edit and
  autocommit, returning the YAML `diff` *and* a world-model `impact` delta (new
  dead-ends / unreachable endings / undeclared flags) in one response;
  `begin` / `commit` / `rollback` group edits, a batch with `"atomic": true` is
  all-or-nothing across state + edits, and `reload` makes edits playable —
  understand → edit → verify in one warm process (`dev/world_model.py`). Pure
  Python, no pygame display. See `docs/headless.md`, `docs/session-protocol.md`,
  and `docs/ai-native-contract.md`.
- `world_gal_game.dev.driver.GameDriver` — low-level pygame events + screenshot +
  widget queries, for pixel-level UI debugging. See `docs/ai-debug.md`.
- CLI: `wgg --headless --inspect`, `wgg --headless --script <json>`,
  `wgg --screenshot out.png`, `wgg debug <script.json>`.

### Inspect pack structure / engine capabilities

- `world_gal_game.dev.pack_inspector.PackInspector` — developer view (`summary`,
  `scenes`, `locations`, `npcs`, `items`, `reachability`, `dead_ends`, `graph`
  as mermaid/dot).
- `world_gal_game.dev.capability_manifest` — machine-readable list of engine
  capabilities: `build_manifest()`, `manifest_json()`, `summary_table()`, plus
  `all_*_kinds()` / `find_effect()` / `find_condition()` helpers. Includes
  loaded plugins.
- CLI: `wgg inspect-pack <pack>`, `wgg inspect-pack <pack> --capabilities`,
  `wgg capabilities --pack <pack>`, `wgg capabilities --pack <pack> --schema`
  (JSON-Schema bundle: per-kind arg schemas + content models, for offline
  validation by any agent).

### Reason about a pack before editing (world model)

Beyond the contract's verbs, a static/searchable world model — see
[docs/ai-native-world-model.md](docs/ai-native-world-model.md):

- `world_gal_game.core.variable_spec` — `VariableManifest` (typed declared
  state: key/type/default/description/category). Optional
  `<pack>/content/variables.yaml`; surfaced in `inspect()` + `wgg variables`.
- `world_gal_game.dev.dataflow.DataflowAnalyzer` — `flag/scene/item/resource →
  {writers, readers}` impact analysis + conditioned scene→scene edges.
- `world_gal_game.dev.planner.Planner` — goal-directed BFS (`find_path(goal)`)
  over next/choose/move/start_scene via snapshot/restore.
- `world_gal_game.dev.coverage.CoverageTracker` — scene/line/choice/ending
  coverage of a run vs. pack totals.
- `world_gal_game.dev.session_server` — warm NDJSON session (load once, stream
  ops over stdin/stdout); the language-agnostic fast path, not MCP.
- CLI: `wgg variables <pack> [--check]`, `wgg chapters <pack> [--check]`,
  `wgg inspect-pack <pack> --dataflow` (edges now carry structured
  `guard_logic`), `wgg inspect-pack <pack> --references <sym>`,
  `wgg session --pack <pack>`, `wgg plan --pack <pack> --goal '<json>'`
  (state-key includes inventory/affection/resources/time), `wgg coverage <pack>
  --script <s>`, `wgg contract <pack>` (narrative-invariant gate).

### Aggregate agent endpoints (lowest-token orientation)

These endpoints load the pack once and answer the common pre-edit questions in a
single JSON object — `world_gal_game.dev.agent_endpoints`:

- `wgg brief <pack> [--format text]` — the **lowest-token** orientation: compact
  scene adjacency + ending reachability + `key:type` variables + routes + gaps
  (~7x smaller than `context`) (`pack_brief`). `wgg card <pack> --symbol <id>`
  zooms to one symbol — a scene's edges + `guard_logic`, or a flag's
  writers/readers/gated-endings (`symbol_card`).
- `wgg context <pack>` — variables + reachability + scene graph + dataflow
  digest + coverage totals + structural gaps in one blob (`build_context`).
- `wgg impact <pack> --symbol <id>` — change pre-flight: writers/readers,
  endings/scenes gated on the symbol, conditioned edges referencing it, and a
  planner baseline of which at-risk endings are reachable today
  (`analyze_impact`).

### Self-contained onboarding bundle (works pip-installed)

`world_gal_game.dev.agent_bundle` generates the agent-facing artifacts from
code (not from `docs/`, which is not packaged in the wheel):

- `wgg agent-guide` — the agent-neutral quickstart (`agent_guide_text`).
- `wgg docs export <dir|->` — guide + capability JSON-Schema + NDJSON
  session-protocol schema + recipes catalogue (`export_bundle`). The NDJSON
  protocol is fully specified in [docs/session-protocol.md](docs/session-protocol.md).

### Edit packs structurally

- `world_gal_game.dev.pack_editor.PackEditor` — structured CRUD (`add_scene`,
  `add_npc`, `add_location`, `add_item`, `add_choice`, `update_*`, `remove_*`).
  Comment-preserving via ruamel.yaml round-trip. Supports `dry_run` + `diff()`.
  Failures raise `PackEditError` carrying `field` / `path` / `expected` / `got` /
  `hint`.
- CLI: `wgg edit <pack> add-scene --payload '{"id":"..."}'`,
  `wgg edit <pack> add-choice --scene-id ... --payload ...`, etc.

### Extend the engine (write a plugin, don't edit core)

- A plugin is a directory (`plugins/<id>/`) with a `plugin.yaml` plus a Python
  entry module (default `plugin.py`). The entry registers handlers via nine
  decorators: `@effect`, `@condition`, `@hook`, `@inspect_field`, `@widget`,
  `@scene`, `@brain`, `@dialogue_op`, `@portrait_backend`.
- 16 lifecycle `HookEvent`s: `pack.{before_load,after_load}`,
  `game.state_ready`, `effect.{before_apply,after_apply}`,
  `save.{before_serialize,after_load}`, `scene.{push,pop,replace}`,
  `dialogue.{before_line,after_line,choice_made}`, `player.move`,
  `time.advance`, `app.frame`.
- Three scan roots: `world_gal_game/plugins_user/` (bundled),
  `~/.world-gal-game/plugins/` (per-user), `<pack>/plugins/` (pack-local).
- Example: `games/demo_pack/plugins/step_counter/` (demonstrates effect +
  condition + hook + inspect_field); `world_gal_game/plugins_user/animated_portraits/`
  (bundled, demonstrates `@portrait_backend`).
- See `docs/plugins.md`, `docs/ai-developer-guide.md`.

### Self-verify / end-to-end dev loop

- `world_gal_game.dev.self_check.SelfCheck` — five stages chained:
  schema → refs → dead_ends → smoke → visual; emits a JSON-friendly
  `SelfCheckReport`.
- `world_gal_game.dev.smoke_runner.SmokeRunner` — runs every
  `<pack>/scripts/test_*.json`; passes when at least one `ending_*` flag is set.
- `world_gal_game.dev.visual_check.VisualCheck` — md5 + pixel diff against
  `<pack>/visual_baselines/`.
- `world_gal_game.dev.asset_studio` — `placeholder_image`, `resize`, `convert`,
  `stock_placeholder_pack`.
- CLI: `wgg self-check <pack>`, `wgg smoke <pack>`, `wgg visual-check <pack>`.

---

## Quick dev loop

```bash
# Unit tests (888 cases)
uv run pytest tests/

# End-to-end playthrough (demo_pack's lover / friend / alone routes)
uv run python main.py --headless --pack demo_pack \
    --script games/demo_pack/scripts/test_lover_route.json

# Five-stage self-check (schema + refs + dead-ends + smoke; visual off by default)
uv run wgg self-check demo_pack

# Pack state, player view
uv run python main.py --headless --inspect --pack demo_pack

# Pack structure, developer view
uv run wgg inspect-pack games/demo_pack
uv run wgg inspect-pack games/demo_pack --format mermaid

# Engine capabilities (including loaded plugins)
uv run wgg capabilities --pack demo_pack

# Structured pack edit (dry-run)
uv run wgg edit games/demo_pack add-scene \
    --payload '{"id":"sketch_demo","title":"...","lines":[...]}' --dry-run

# Screenshot for UI verification
uv run python main.py --pack demo_pack \
    --screenshot out.png --autoplay 1.0 --dev-start explore

# Launch the game (dev)
uv run python main.py
```

---

## Core principles

- **No game-specific logic in core.** New themes (horror, romance, raising sims)
  go through plugins, not edits to `world_gal_game/core/`.
- **Add effects/conditions via `@effect` / `@condition`**, not core edits.
  `core/game_state.py`'s `apply` / `evaluate` are pure registry dispatch — no
  if-elif ladder.
- **No decorative emoji** — not in UI, docs, commit messages, or chat output.
  Functional symbols (arrows, `→`, `✓` inside tables) are fine.
- **pydantic v2 + YAML packs.** Define structured data as a pydantic model
  first, then serialize to YAML.
- **Read [docs/architecture.md](docs/architecture.md) before writing code** — it
  has the module layering, data flow, and templates for adding an effect /
  widget / scene.
- **Engine changes affect every pack.** Before changing core schema, confirm
  demo_pack still passes smoke (`uv run wgg smoke demo_pack`). `meta.yaml`
  carries `pack_format_version`; schema changes need a matching migration (see
  `core/pack_migration.py`).
- **`GameState.apply` / `evaluate` is the central dispatch.** Route every state
  change through it. Writing state fields directly is usually a bug.
- **A failing plugin handler must not crash the engine.** Every effect /
  condition / hook call is wrapped in `isolate()` (log, then degrade to a safe
  default). Don't rely on the caller to catch — your handler should try/except
  on its own.

---

## Key files

| Goal | Location |
|---|---|
| Engine internals | `docs/architecture.md` |
| AI-Coding-Native contract (schema / ops / trace / determinism) | `docs/ai-native-contract.md` |
| AI-native world model (variables / dataflow / session / planner / coverage / rollback) | `docs/ai-native-world-model.md` |
| Agent-neutral onboarding (any AI tool) + machine map | `AGENTS.md`, `llms.txt` |
| NDJSON session protocol (framing / shapes / atomicity / errors) | `docs/session-protocol.md` |
| Aggregate endpoints (context / impact) | `world_gal_game/dev/agent_endpoints.py` |
| Onboarding bundle (agent-guide / docs export) | `world_gal_game/dev/agent_bundle.py` |
| Full AI developer guide | `docs/ai-developer-guide.md` |
| AI plays the game | `docs/headless.md` |
| AI debugs the UI | `docs/ai-debug.md` |
| Pack format | `docs/pack-format.md` |
| Effect list | `docs/effects-reference.md` (or `wgg capabilities`) |
| Condition list | `docs/conditions-reference.md` (or `wgg capabilities`) |
| Write your first pack | `docs/getting-started.md`, `docs/tutorial-build-a-game.md` |
| Write a plugin | `docs/plugins.md` |
| Common patterns | `docs/cookbook.md` |
| GameState central dispatch | `world_gal_game/core/game_state.py` |
| Pack loading entry point | `world_gal_game/content_loader.py` |
| Scene framework | `world_gal_game/scenes/base.py` |
| Plugin registry / manager | `world_gal_game/plugins/{registry,manager,manifest,context}.py` |
| Builtin effects / conditions | `world_gal_game/plugins/{builtin_effects,builtin_conditions}.py` |
| Effect / condition arg models (JSON Schema) | `world_gal_game/plugins/{effect_args,condition_args}.py` |
| Execution trace / state diff (headless) | `world_gal_game/dev/{trace,diff}.py` |
| Variable manifest (typed narrative-state schema) | `world_gal_game/core/variable_spec.py` |
| Chapter/act/route manifest (optional structural overlay) | `world_gal_game/core/chapter_spec.py` |
| Token-frugal orientation (brief / per-symbol card) | `world_gal_game/dev/agent_endpoints.py` (`pack_brief`/`symbol_card`) |
| Narrative-contract checker (behavioural regression gate) | `world_gal_game/dev/contract.py` |
| Dataflow / cross-reference + conditioned graph | `world_gal_game/dev/dataflow.py` |
| Warm NDJSON control session (faster-than-MCP) | `world_gal_game/dev/session_server.py` |
| Warm structural-edit loop + post-edit impact delta | `world_gal_game/dev/world_model.py` (+ `edit.*` ops in `headless.py`) |
| Goal-directed planner | `world_gal_game/dev/planner.py` |
| Coverage tracker (scene/line/choice/ending) | `world_gal_game/dev/coverage.py` |
| Player rollback buffer (shared snapshot machinery) | `world_gal_game/core/history.py` |
| Reference doc generator | `tools/gen_references.py` |
| Pack structure analysis | `world_gal_game/dev/pack_inspector.py` |
| Pack structure editing | `world_gal_game/dev/pack_editor.py` |
| Engine capability list | `world_gal_game/dev/capability_manifest.py` |
| Five-stage self-check | `world_gal_game/dev/self_check.py` |
| Example plugin | `games/demo_pack/plugins/step_counter/` |
| End-to-end smoke (demo_pack routes) | `games/demo_pack/scripts/test_*_route.json` |
