# CLAUDE — World Gal-Game Engine

Onboarding notes for AI collaborators (Claude Code, Codex, etc.).
Strategy and roadmap: [ROADMAP.md](ROADMAP.md). Engine internals:
[docs/architecture.md](docs/architecture.md).

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

A **distribution and platform push is currently in the working tree** (web via
pygbag/WASM, Steam, Android/mobile + PWA, rich text, voice, touch and responsive
rendering, pack migration). Separately, three Phase 2 plugin-system loose ends
remain open. See [ROADMAP.md](ROADMAP.md) for the full picture; the highest-value
next PRs:

1. **Align the manifest schema with the registry** — let `plugin.yaml` declare
   `@widget` / `@scene` / `@brain` / `@dialogue_op`, which today only register
   in Python.
2. **Generate the effect / condition references dynamically** from
   `build_manifest()` (currently hand-written and drifts).
3. **Wire `wgg edit` to CapabilityManifest hints** — a misspelled kind returns
   "did you mean ...".

---

## Interfaces you (the AI) can use

### Play / inspect a running game

- `world_gal_game.headless.HeadlessSession` — high-level ops (`start_scene`,
  `next_line`, `choose`, `move_to`, `advance_time`, `set_flag`,
  `adjust_affection`, `inspect`, `run_script`). Pure Python, no pygame display.
  See `docs/headless.md`.
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
  `wgg capabilities --pack <pack>`.

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
  entry module (default `plugin.py`). The entry registers handlers via eight
  decorators: `@effect`, `@condition`, `@hook`, `@inspect_field`, `@widget`,
  `@scene`, `@brain`, `@dialogue_op`.
- 16 lifecycle `HookEvent`s: `pack.{before_load,after_load}`,
  `game.state_ready`, `effect.{before_apply,after_apply}`,
  `save.{before_serialize,after_load}`, `scene.{push,pop,replace}`,
  `dialogue.{before_line,after_line,choice_made}`, `player.move`,
  `time.advance`, `app.frame`.
- Three scan roots: `world_gal_game/plugins_user/` (bundled),
  `~/.world-gal-game/plugins/` (per-user), `<pack>/plugins/` (pack-local).
- Example: `games/demo_pack/plugins/step_counter/` (demonstrates effect +
  condition + hook + inspect_field).
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
# Unit tests (614 cases)
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
| Pack structure analysis | `world_gal_game/dev/pack_inspector.py` |
| Pack structure editing | `world_gal_game/dev/pack_editor.py` |
| Engine capability list | `world_gal_game/dev/capability_manifest.py` |
| Five-stage self-check | `world_gal_game/dev/self_check.py` |
| Example plugin | `games/demo_pack/plugins/step_counter/` |
| End-to-end smoke (demo_pack routes) | `games/demo_pack/scripts/test_*_route.json` |
