# ROADMAP — World Gal-Game Engine

Where the engine is going next. Read alongside [CLAUDE.md](CLAUDE.md) (AI
onboarding) and [docs/architecture.md](docs/architecture.md) (engine internals).

> Last updated: 2026-05-25. Phases 1-2 done, including the **AI-Coding-Native
> contract** (typed arg JSON-Schema, `run_script` trace/diff/snapshot/ops,
> determinism — see [docs/ai-native-contract.md](docs/ai-native-contract.md)).
> **Phase 6 — AI-native world model + player rollback — done** (variable
> manifest, dataflow / conditioned graph, warm NDJSON session, goal planner,
> coverage, and snapshot-powered rollback — see
> [docs/ai-native-world-model.md](docs/ai-native-world-model.md)). A Phase 3
> distribution + platform push remains. Phase 5 GalGame-maturity: **animated
> portraits (5A) + i18n extraction (5C) done**; video playback (5B) and native
> Live2D/Spine rigs remain.

---

## Status at a glance

The engine is built around three pillars (see CLAUDE.md for the framing):

- **Pillar A — humans author packs:** mature. YAML pack model, scaffold tool,
  asset studio, full subsystem docs.
- **Pillar B — AI develops packs:** complete except autonomous spec-to-pack.
  AI can play, inspect, edit, extend, and self-verify via `HeadlessSession`,
  `GameDriver`, `PackInspector`, `PackEditor`, `CapabilityManifest`, `SelfCheck`,
  `SmokeRunner`, `VisualCheck`, and `asset_studio`. The **world-model** layer
  (Phase 6) adds a typed `VariableManifest`, `DataflowAnalyzer` (writers/readers +
  conditioned edges), a warm NDJSON `SessionServer`, a goal-directed `Planner`,
  and a `CoverageTracker` — so an agent can reason about a pack before editing it,
  not just step through it.
- **Pillar C — third-party plugins extend the engine:** complete. All nine
  extension points work in code *and* are declarable in `plugin.yaml`;
  `PluginManager` reconciles declared-vs-registered (see
  [Extension points](#extension-points-pillar-c)).

Test suite: 888 cases, all green.

---

## Phases

### Phase 1 — Core extensibility — Done

- Plugin system MVP: `world_gal_game/plugins/{registry,manager,manifest,context,errors,builtin_effects,builtin_conditions}.py`, demo plugin, `docs/plugins.md`.
- `core/game_state.py`'s `apply` / `evaluate` converted from a 39-branch if-elif ladder to pure registry dispatch (with hook firing).
- `core/story_graph.py`: `Effect.kind` / `Condition.kind` moved from `Literal[...]` to `str` + `field_validator`.
- CapabilityManifest, PackEditor (dry-run + diff), PackInspector (incl. dead-end detection, wired into SelfCheck).
- CLI: `wgg edit` / `validate` / `check` / `inspect-pack` / `capabilities`.
- `docs/ai-developer-guide.md`; `pack_format_version` added to `meta.yaml`.

### Phase 2 — More plugin points + AI dev loop — Mostly done

Done:

- Four new extension points wired into the registry: `@widget`, `@scene`,
  `@brain`, `@dialogue_op` (alongside the Phase 1 four).
- 16 lifecycle `HookEvent`s (more than the 13 originally planned).
- AI end-to-end dev loop: `SelfCheck` (schema → refs → dead-ends → smoke →
  visual), `SmokeRunner`, `VisualCheck` (md5 + pixel diff), `asset_studio`.

Wrap-up — done 2026-05-24:

- **Manifest schema alignment** — `plugin.yaml` declares all nine categories;
  `PluginManager` reconciles declared-vs-registered and warns on mismatch.
- **Auto-generated** `effects-reference.md` / `conditions-reference.md` via
  `tools/gen_references.py` (`--check` drift guard).
- **`wgg edit` / `wgg validate` capability hints** — "did you mean ..." on a
  misspelled kind.

### Phase 2.5 — AI-Coding-Native contract — Done (2026-05-24)

See [docs/ai-native-contract.md](docs/ai-native-contract.md).

- **Typed arg models** for all 45 builtin effects/conditions
  (`plugins/{effect_args,condition_args}.py`) → real **JSON-Schema export**
  (`wgg capabilities --schema`) and warning-level validator arg checks.
- **`run_script`** gained `apply` / `check` / `assert` / `affordances` /
  `snapshot` / `restore` ops, per-op state **diff**, and an execution **trace**
  (`dev/trace.py` + `dev/diff.py`; the dormant `transcript` is now populated).
- **Determinism**: `EngineConfig.seed` → `GameState.rng()`; a test guards
  against uncontrolled global `random`.
- pygame-ce stdout banner suppressed so all CLI/headless JSON is clean.

### Phase 6 — AI-native world model + player rollback — Done (2026-05-25)

Extends the Phase 2.5 contract from *verbs* (apply / snapshot / diff / trace) to
a *world model* (reason before acting). See
[docs/ai-native-world-model.md](docs/ai-native-world-model.md).

- **Variable manifest** — `core/variable_spec.py` (`VariableSpec` /
  `VariableManifest`); optional `content/variables.yaml` declares typed
  narrative state (key/type/default/description/category). Loaded onto
  `state.meta["__variables__"]`, surfaced in `inspect()` (`variables` view) and
  `wgg variables <pack>`; `wgg validate` does a pure-YAML used-vs-declared
  cross-check ("did you mean" on a typo'd flag, advisory on an unused one).
- **Dataflow / conditioned graph** — `dev/dataflow.py` (`DataflowAnalyzer`):
  per-symbol `writers`/`readers` (flags/scenes/items/resources, incl. reads in
  endings/achievements/clues/quests) and scene→scene `edges` carrying their
  guard conditions. `wgg inspect-pack --dataflow` / `--references <sym>`.
- **Warm NDJSON session** — `dev/session_server.py` (`SessionServer` /
  `run_session`); `wgg session` loads the pack once and streams the `run_script`
  op vocabulary over stdin/stdout (control ops `__ping__`/`__inspect__`/
  `__affordances__`/`__reset__`/`__quit__`). The language-agnostic fast path
  with no per-call process spawn — the "faster than MCP" answer.
- **Goal planner** — `dev/planner.py` (`Planner.find_path`): BFS over the
  next/choose/move/start_scene action space using snapshot/restore to reach a
  goal predicate; `wgg plan --goal '<json>'`.
- **Coverage** — `dev/coverage.py` (`CoverageTracker`): scene/line/choice/ending
  coverage of a run vs. the pack totals; `wgg coverage <pack> --script`.
- **Player rollback** — `core/history.py` (`StateHistory`): a bounded
  (snapshot, presentation) stack on the *same* `dev/diff` machinery the agent
  uses for branch exploration. `DialogueScene` records each display and rewinds
  on **Backspace** (`EngineConfig.rollback_enabled`, default on) without
  re-running the engine. One mechanism, two audiences.
- Also fixed: `inspect()` raised `AttributeError` when an NPC was present
  (`present_npcs` yields id strings) — surfaced by the planner's state sweep.

### Phase 3 — Distribution, platforms, presentation, autonomy — In progress

A large body of work is in the working tree (uncommitted as of 2026-05-22) and
not yet folded into the phase plan below. Landing now:

- **Distribution:** web (pygbag/WASM, `build_web.py` + `platform_web.py`),
  Steam (`integrations/steam_bridge.py` + `steam_plugin.py`: achievements,
  cloud saves), Android/mobile + PWA (`build.py`, `test_build_android.py`,
  `test_web_pwa.py`). Docs: `distribution-{web,steam,mobile}.md`.
- **Presentation:** rich text (`dialogue/richtext.py` — BBCode-style parser with
  color/size/waits/speed/ruby/per-glyph effects), `ui/easing.py`, portrait
  animation, responsive/letterbox rendering, touch input, per-line voice channel.
- **VN table-stakes (presentation + extras):** CG gallery / music room / scene
  replay / endings + completion overlays (reached from the in-game menu and the
  title "鑑賞模式"), Auto/Skip polish + on-screen indicators, NVL mode,
  builtin camera/screen FX (`camera_pan`/`camera_zoom`/`screen_shake`/
  `screen_flash`/`screen_tint`), per-character voice volume, quicksave (F6) /
  quickload (F9), bundled autosave plugin, save thumbnails + scrollable save UX,
  and settings persistence (`settings.json`). Media auto-unlocks when a line
  displays a `cg` / plays a `bgm`. Docs: `docs/presentation-and-extras.md`.
- **Versioning:** `core/pack_migration.py` — load-time, plugin-extensible pack
  save migration (`@save_migration`), plus `PackEditor.scaffold_save_migration`.

Still deferred:

- **LLM-driven NPC dialogue** — wire an LLM-backed brain to `@brain` and let
  `DialogueEngine` drive dynamic lines. (The `Brain` interface, registry, a
  deterministic `EchoBrain` placeholder in `npc/llm_brain.py`, and a
  `HeadlessSession.chat()` op all exist; no live LLM is wired up yet.)
- **Autonomous spec-to-pack** — the end-to-end "idea → pack → smoke → screenshot
  review → iterate" loop has the pieces but no official example.
- **Cross-pack plugin sharing / distribution** — packaging mechanism undecided.

---

## Extension points (Pillar C)

The one place that tracks declared-vs-implemented status. All nine register via
decorator, resolve through a registry singleton, and can be declared in
`plugin.yaml`'s `extends`:

| Decorator | Registry | `extends` field | Status |
|---|---|---|---|
| `@effect` | `EFFECT_REGISTRY` | `effects` | done |
| `@condition` | `CONDITION_REGISTRY` | `conditions` | done |
| `@hook` | `HOOK_REGISTRY` | `hooks` | done |
| `@inspect_field` | `INSPECT_FIELD_REGISTRY` | `inspect_fields` | done |
| `@widget` | `WIDGET_REGISTRY` | `widgets` | done |
| `@scene` | `SCENE_REGISTRY` | `scenes` | done |
| `@brain` | `BRAIN_REGISTRY` | `brains` | done |
| `@dialogue_op` | `DIALOGUE_OP_REGISTRY` | `dialogue_ops` | done |
| `@portrait_backend` | `PORTRAIT_BACKEND_REGISTRY` | `portrait_backends` | done |

All nine register via decorator and can be *declared* in `plugin.yaml`'s
`extends`. `PluginManager` reconciles declared-vs-registered per plugin and
records advisory warnings on mismatch (`PluginRecord.warnings`), so the manifest
is a checkable description of a plugin's surface.

Other plugin-system facts: 3 scan roots (bundled / per-user / pack-local),
`isolate()` wraps every handler call (log + safe default, never an engine
crash), private plugin state lives under `state.meta["__plugin:<id>__"]` and is
filtered out by `SaveManager`, and `depends` + topological load ordering exist
(cycle/version-range detection is still minimal).

---

## Next PRs

In priority order.

PRs #1-3 (manifest alignment, dynamic references, edit hints) **shipped
2026-05-24** — see the Phase 2 wrap-up + Phase 2.5 above. Remaining:

### 1. GalGame maturity (Phase 5) — presentation / production table-stakes

Mostly plugin-able (`@scene` / `@widget` / `@dialogue_op` / `@portrait_backend`);
video may need a thin runtime hook. See `docs/galgame-maturity.md`.

- **5A — animated 立繪 — done.** `@portrait_backend` (9th extension category) is
  the seam in `core/portrait_spec.py` (`backend` / `backend_args`); the dialogue
  scene delegates the resting draw, transitions stay surface-based, `"static"`
  is unchanged. Bundled `animated_portraits` plugin ships web-safe `breath`
  (procedural idle) + `sprite` (sheet frames) + `layered` (blink + lip-sync +
  breathing on stacked PNGs — the flagship cross-platform rig; lip-sync driven
  by a `talking` signal the scene feeds via `update(dt, **ctx)`).
  **Remaining:** native Live2D / Spine rigs as a desktop-only plugin (no pygame
  binding; out of core).
- **5B — video / movie playback** (OP/ED/過場) — `@dialogue_op` or `@scene`;
  evaluate the web/pygbag decode path. Open.
- **5C — i18n translation extraction — done** (`tools/i18n_extract.py`):
  pack scenes YAML → translatable string table + `--check` coverage; runtime
  apply-translation is the follow-up.

### 2. Plugin distribution design

Sharing one plugin across packs currently means copying. Start with a
`docs/distribution-plugins.md` proposal weighing PyPI namespace vs git submodule
vs `pyproject.toml`-pinned, and seek review.

---

## Known gaps

- ~~**`PackEditor` lacks `add_clue` / `add_quest` / `add_achievement`**~~ —
  **closed 2026-05-30** (Phase 7 P2): `add_quest` / `add_clue` /
  `add_achievement` / `add_resource` added and wired into the warm-session
  `edit.*` ops. `add_item` still has no update/remove twin.
- **No autonomous spec-to-pack example** (Pillar B) — the loop exists piecewise.
- **No `@field` decorator** for public plugin state — plugins use
  `state.meta["__plugin:<id>__"]` for private state and `@inspect_field` for the
  read path; writing public fields into `GameState` is not yet a first-class API.
- **Cross-pack plugin distribution** (Pillar C) — undecided; see Next PR #4.

---

## Risks and tradeoffs

| Risk | Approach |
|---|---|
| Core minimalism vs. plugin power | Core holds only genre-agnostic primitives; new-theme effects always go through plugins. Settled in Phase 1. |
| AI interface vs. human interface | Not two stacks. `PackEditor` / `CapabilityManifest` are thin wrappers over one schema; the CLI is another layer on top. Settled in Phase 1. |
| Backward compatibility | `pack_format_version` in `meta.yaml` (`"0.1"`); `core/pack_migration.py` provides the load-time migration path. |
| Plugin trust | No sandbox (single-player). `plugin.yaml` must declare extension points and side effects; `PluginManager.print_summary()` prints a load summary. Revisit as Phase 2 breadth grows. |
| Performance | Hooks fire on hot paths (`effect.before/after_apply`, `app.frame`, `dialogue.before/after_line`). Demo_pack smoke is still millisecond-scale; re-profile when an LLM brain lands. |
| MCP server | Still not building one. Claude Code / Codex import `HeadlessSession` / `PackEditor` / `PackInspector` / `CapabilityManifest` directly. |

---

## Changelog

| Date | Milestone | Status |
|---|---|---|
| 2026-05-18 | Blueprint set (CLAUDE.md + ROADMAP.md) | done |
| 2026-05-18 | Phase 1: plugin system MVP (`@effect`/`@condition`/`@hook`/`@inspect_field`) | done |
| 2026-05-18 | Phase 1: CapabilityManifest + `wgg capabilities` | done |
| 2026-05-18 | Phase 1: PackEditor (dry-run + diff) + PackInspector + `wgg inspect-pack` | done |
| 2026-05-18 | Phase 1: dead-end detection in SelfCheck; `pack_format_version` added | done |
| 2026-05-19 | Phase 2: four new extension points (`@widget`/`@scene`/`@brain`/`@dialogue_op`, registry side) | done |
| 2026-05-19 | Phase 2: 16 lifecycle hooks | done |
| 2026-05-19 | Phase 2: AI dev loop (SelfCheck + SmokeRunner + VisualCheck + asset_studio) | done |
| 2026-05-19 | Clue / Journal system (`core/clue.py`, `scenes/clues_scene.py`, `ui/widgets/clue_log.py`, `content/clues.yaml`) | done |
| 2026-05-22 | Phase 3: distribution + platform + presentation (web/Steam/mobile, rich text, voice, touch, responsive, pack migration) | in progress (working tree) |
| 2026-05-23 | Phase 3 presentation/extras table-stakes: CG gallery, music room, scene replay, endings + completion, Auto/Skip polish, NVL mode, camera/screen FX, per-character voice, quicksave/autosave + save-UX | done |
| 2026-05-24 | Phase 2 wrap-up: manifest schema alignment (all 8 categories) + reconciliation, dynamic references (`tools/gen_references.py`), `wgg edit`/`validate` did-you-mean | done |
| 2026-05-24 | Phase 2.5: AI-Coding-Native contract — typed arg JSON-Schema (`--schema`), `run_script` trace/diff + apply/check/assert/affordances/snapshot/restore, determinism seed | done |
| 2026-05-24 | Phase 5C: i18n translation extraction (`tools/i18n_extract.py`) — pack scenes → string table + `--check` coverage | done |
| 2026-05-24 | Phase 5A: animated portraits — `@portrait_backend` (9th extension category), `PortraitSpec.backend`/`backend_args`, dialogue-scene seam, bundled `animated_portraits` (breath + sprite + layered blink/lip-sync/breathing rig) | done |
| 2026-05-25 | Phase 6: AI-native world model — variable manifest, dataflow/conditioned graph, warm NDJSON session, goal planner, coverage (`wgg variables`/`inspect-pack --dataflow`/`session`/`plan`/`coverage`) + player rollback (`StateHistory`, Backspace) on the shared snapshot machinery | done |
| 2026-05-30 | Phase 7 (P0): warm structural-edit loop — `edit.*` ops in `HeadlessSession`/`SessionServer` (autocommit), `begin`/`commit`/`rollback` transactions, `"atomic": true` batches, `reload`, a uniform `changed` envelope, and a post-edit `impact` delta (`dev/world_model.py`: new dead-ends / unreachable endings / undeclared flags) — understand → edit → verify in one warm process | done |
| 2026-05-30 | Phase 7 (P1): planner state-key now includes inventory/affection/resources/time (correct reachability); dataflow edges gained structured `guard_logic` ({all,none}); token-frugal `wgg brief` / `wgg card` (~7x smaller than `context`); narrative contracts (`dev/contract.py`, `wgg contract`: reachable/unreachable/holds/path_reaches) | done |
| 2026-05-30 | Phase 7 (P2): `PackEditor` add_quest/add_clue/add_achievement/add_resource (+ warm `edit.*`); first-class chapter/act/route overlay (`core/chapter_spec.py`, optional `content/chapters.yaml`, `wgg chapters [--check]`, surfaced in inspect/brief) | done |
| — | Phase 5B: video / movie playback (OP/ED/過場) | open (Next PR #1) |
| — | Phase 5A native rigs: Live2D / Spine as a desktop-only plugin | open (Next PR #1) |
| — | Phase 3: LLM NPC, autonomous spec-to-pack, cross-pack plugins | deferred |
