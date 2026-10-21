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
- **C. Third-party plugins extend the engine** — ten categories add effects,
  conditions, hooks, inspect fields, widgets, scenes, brains, dialogue ops,
  portrait backends, and ambient backends without touching core.

---

## Current focus

A **distribution and platform push is in the working tree** (web via
pygbag/WASM, Steam, Android/mobile + PWA, rich text, voice, touch and responsive
rendering, pack migration). The **AI-Coding-Native contract has landed** — read
[docs/ai-native-contract.md](docs/ai-native-contract.md):

- Typed effect/condition arg models with real **JSON-Schema export**
  (`world-gal-game capabilities --schema`); `world-gal-game validate` does warning-level arg checks.
- `run_script` gained `apply` / `check` / `assert` / `affordances` /
  `snapshot` / `restore` ops, per-op state **diff**, and an execution
  **trace** (`transcript`).
- **Determinism**: `EngineConfig.seed` → `GameState.rng()`.
- The three former plugin loose ends are **closed**: `plugin.yaml` declares all
  ten extension categories (manager reconciles & warns); references generate
  from `build_manifest()` (`tools/gen_references.py`); `world-gal-game edit` / `world-gal-game
  validate` return "did you mean" hints.
- **Animated portraits (Phase 5A) have landed**: the ninth extension category,
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
- CLI: `world-gal-game --headless --inspect`, `world-gal-game --headless --script <json>`,
  `world-gal-game --screenshot out.png`, `world-gal-game debug <script.json>`.

### Inspect pack structure / engine capabilities

- `world_gal_game.dev.pack_inspector.PackInspector` — developer view (`summary`,
  `scenes`, `locations`, `npcs`, `items`, `reachability`, `dead_ends`, `graph`
  as mermaid/dot).
- `world_gal_game.dev.capability_manifest` — machine-readable list of engine
  capabilities: `build_manifest()`, `manifest_json()`, `summary_table()`, plus
  `all_*_kinds()` / `find_effect()` / `find_condition()` helpers. Includes
  loaded plugins.
- CLI: `world-gal-game inspect-pack <pack>`, `world-gal-game inspect-pack <pack> --capabilities`,
  `world-gal-game capabilities --pack <pack>`, `world-gal-game capabilities --pack <pack> --schema`
  (JSON-Schema bundle: per-kind arg schemas + content models, for offline
  validation by any agent).

### Reason about a pack before editing (world model)

Beyond the contract's verbs, a static/searchable world model — see
[docs/ai-native-world-model.md](docs/ai-native-world-model.md):

- `world_gal_game.core.variable_spec` — `VariableManifest` (typed declared
  state: key/type/default/description/category). Optional
  `<pack>/content/variables.yaml`; surfaced in `inspect()` + `world-gal-game variables`.
- `world_gal_game.dev.dataflow.DataflowAnalyzer` — `flag/scene/item/resource →
  {writers, readers}` impact analysis + conditioned scene→scene edges.
- `world_gal_game.dev.planner.Planner` — goal-directed BFS (`find_path(goal)`)
  over next/choose/move/start_scene via snapshot/restore.
- `world_gal_game.dev.coverage.CoverageTracker` — scene/line/choice/ending
  coverage of a run vs. pack totals.
- `world_gal_game.dev.session_server` — warm NDJSON session (load once, stream
  ops over stdin/stdout); the language-agnostic fast path, not MCP.
- CLI: `world-gal-game variables <pack> [--check]`, `world-gal-game chapters <pack> [--check]`,
  `world-gal-game inspect-pack <pack> --dataflow` (edges now carry structured
  `guard_logic`), `world-gal-game inspect-pack <pack> --references <sym>`,
  `world-gal-game session --pack <pack>`, `world-gal-game plan --pack <pack> --goal '<json>'`
  (state-key includes inventory/affection/resources/time), `world-gal-game coverage <pack>
  --script <s>`, `world-gal-game contract <pack>` (narrative-invariant gate).

### Aggregate agent endpoints (lowest-token orientation)

These endpoints load the pack once and answer the common pre-edit questions in a
single JSON object — `world_gal_game.dev.agent_endpoints`:

- `world-gal-game brief <pack> [--format text]` — the **lowest-token** orientation: compact
  scene adjacency + ending reachability + `key:type` variables + routes + gaps
  (~7x smaller than `context`) (`pack_brief`). `world-gal-game card <pack> --symbol <id>`
  zooms to one symbol — a scene's edges + `guard_logic`, or a flag's
  writers/readers/gated-endings (`symbol_card`).
- `world-gal-game context <pack>` — variables + reachability + scene graph + dataflow
  digest + coverage totals + structural gaps in one blob (`build_context`).
- `world-gal-game impact <pack> --symbol <id>` — change pre-flight: writers/readers,
  endings/scenes gated on the symbol, conditioned edges referencing it, and a
  planner baseline of which at-risk endings are reachable today
  (`analyze_impact`).

### Self-contained onboarding bundle (works pip-installed)

`world_gal_game.dev.agent_bundle` generates the agent-facing artifacts from
code (not from `docs/`, which is not packaged in the wheel):

- `world-gal-game agent-guide` — the agent-neutral quickstart (`agent_guide_text`).
- `world-gal-game docs export <dir|->` — guide + capability JSON-Schema + NDJSON
  session-protocol schema + recipes catalogue (`export_bundle`). The NDJSON
  protocol is fully specified in [docs/session-protocol.md](docs/session-protocol.md).

### Edit packs structurally

- `world_gal_game.dev.pack_editor.PackEditor` — structured CRUD (`add_scene`,
  `add_npc`, `add_location`, `add_item`, `add_choice`, `update_*`, `remove_*`).
  Comment-preserving via ruamel.yaml round-trip. Supports `dry_run` + `diff()`.
  Failures raise `PackEditError` carrying `field` / `path` / `expected` / `got` /
  `hint`.
- CLI: `world-gal-game edit <pack> add-scene --payload '{"id":"..."}'`,
  `world-gal-game edit <pack> add-choice --scene-id ... --payload ...`, etc.

### Extend the engine (write a plugin, don't edit core)

- A plugin is a directory (`plugins/<id>/`) with a `plugin.yaml` plus a Python
  entry module (default `plugin.py`). The entry registers handlers via ten
  decorators: `@effect`, `@condition`, `@hook`, `@inspect_field`, `@widget`,
  `@scene`, `@brain`, `@dialogue_op`, `@portrait_backend`, `@ambient_backend`.
- `HookEvent` defines 18 constants; 17 are currently emitted:
  `pack.{before_load,after_load}`,
  `game.state_ready`, `effect.{before_apply,after_apply}`,
  `save.{before_serialize,after_load}`, `scene.{push,pop,replace}`,
  `dialogue.{before_line,after_line,presentation_ready,choice_made}`,
  `player.move`, `time.advance`, and `chapter.change`. The eighteenth,
  `app.frame`, is reserved and is not fired automatically.
- Three scan roots: `world_gal_game/plugins_user/` (bundled),
  `~/.world-gal-game/plugins/` (per-user), `<pack>/plugins/` (pack-local).
- Example: `games/demo_pack/plugins/step_counter/` (demonstrates effect +
  condition + hook + inspect_field); `world_gal_game/plugins_user/animated_portraits/`
  (bundled, demonstrates `@portrait_backend`).
- See `docs/plugins.md`, `docs/ai-developer-guide.md`.

### Self-verify / end-to-end dev loop

- `world_gal_game.dev.self_check.SelfCheck` — seven stages chained:
  schema → refs → dead_ends → softlock → reachability → smoke → optional visual;
  emits a JSON-friendly `SelfCheckReport`.
- `world_gal_game.dev.smoke_runner.SmokeRunner` — runs every
  `<pack>/scripts/test_*.json`; assertion scripts require every assert to pass,
  while legacy scripts without assertions use the `ending_*` heuristic.
- `world_gal_game.dev.visual_check.VisualCheck` — md5 + pixel diff against
  `<pack>/visual_baselines/`; `--strict-baselines` makes a missing baseline fail
  instead of silently creating one. Generated candidates default to the
  isolated `<pack>/visual_candidates/visual_check/` namespace; explicit
  candidate directories retain exact-path semantics.
- `world_gal_game.dev.production_check` — fail-closed source-release preflight:
  exact seven-stage self-check, 17 semantic visual views at 720p/1080p (34
  explicitly approved baselines), 100% union coverage, a non-empty narrative
  contract, calibrated numeric runtime limits, and clean tracked Git inputs.
  `production-verify` rechecks the expiring artifact against current sources;
  it is not packaged-target, device, accessibility, legal, or reviewer-identity
  proof.
- `world_gal_game.dev.asset_studio` — `placeholder_image`, `resize`, `convert`,
  `stock_placeholder_pack`.
- CLI: `world-gal-game self-check <pack>`, `world-gal-game smoke <pack>`,
  `world-gal-game visual-check <pack>`, `world-gal-game production-check <pack>`,
  `world-gal-game production-verify <report> --pack <pack>`.

---

## Quick dev loop

```bash
# Unit tests (1,200+ cases)
uv run pytest tests/

# End-to-end playthrough (demo_pack's lover / friend / alone routes)
uv run python main.py --headless --pack demo_pack \
    --script games/demo_pack/scripts/test_lover_route.json

# Seven-stage self-check (visual off by default)
uv run world-gal-game self-check demo_pack

# Fail-closed evidence (report must be outside both Git worktrees)
uv run world-gal-game production-check /path/to/pack \
  --report /tmp/release-evidence/production.json
uv run world-gal-game production-verify \
  /tmp/release-evidence/production.json --pack /path/to/pack

# Pack state, player view
uv run python main.py --headless --inspect --pack demo_pack

# Pack structure, developer view
uv run world-gal-game inspect-pack games/demo_pack
uv run world-gal-game inspect-pack games/demo_pack --format mermaid

# Engine capabilities (including loaded plugins)
uv run world-gal-game capabilities --pack demo_pack

# Structured pack edit (dry-run)
uv run world-gal-game edit games/demo_pack add-scene \
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
- **Only the bundled `demo_pack` is a public example.** External packs may be
  private or closed-source; never name them, their characters, places or
  evidence in engine code, tests, docs or commit messages. Use generic fixtures.
- **`docs/` is the single source of documentation.** The website lives in the
  separate `World-Gal-Game-Web` repo and syncs `docs/*.md` at build time — edit
  docs here, not there.
- **pydantic v2 + YAML packs.** Define structured data as a pydantic model
  first, then serialize to YAML.
- **Read [docs/architecture.md](docs/architecture.md) before writing code** — it
  has the module layering, data flow, and templates for adding an effect /
  widget / scene.
- **Engine changes affect every pack.** Before changing core schema, confirm
  demo_pack still passes smoke (`uv run world-gal-game smoke demo_pack`). `meta.yaml`
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
| Effect list | `docs/effects-reference.md` (or `world-gal-game capabilities`) |
| Condition list | `docs/conditions-reference.md` (or `world-gal-game capabilities`) |
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
| Seven-stage self-check | `world_gal_game/dev/self_check.py` |
| Fail-closed production source preflight | `world_gal_game/dev/production_check.py` |
| Example plugin | `games/demo_pack/plugins/step_counter/` |
| End-to-end smoke (demo_pack routes) | `games/demo_pack/scripts/test_*_route.json` |
