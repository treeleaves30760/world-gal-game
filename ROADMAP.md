# ROADMAP — World Gal-Game Engine

Where the engine is going next. Read alongside [CLAUDE.md](CLAUDE.md) (AI
onboarding) and [docs/architecture.md](docs/architecture.md) (engine internals).

> Last updated: 2026-05-23. Phase 1 done, Phase 2 mostly done. A Phase 3
> distribution + platform push (incl. VN presentation/extras table-stakes) is
> currently in the working tree (uncommitted).

---

## Status at a glance

The engine is built around three pillars (see CLAUDE.md for the framing):

- **Pillar A — humans author packs:** mature. YAML pack model, scaffold tool,
  asset studio, full subsystem docs.
- **Pillar B — AI develops packs:** complete except autonomous spec-to-pack.
  AI can play, inspect, edit, extend, and self-verify via `HeadlessSession`,
  `GameDriver`, `PackInspector`, `PackEditor`, `CapabilityManifest`, `SelfCheck`,
  `SmokeRunner`, `VisualCheck`, and `asset_studio`.
- **Pillar C — third-party plugins extend the engine:** all eight extension
  points work in code, but the manifest schema only declares four of them (the
  one real gap — see [Extension points](#extension-points-pillar-c)).

Test suite: 766 cases, all green.

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

Remaining (the wrap-up — see [Next PRs](#next-prs)):

- **Manifest schema alignment** for the four Phase 2 extension points.
- **Auto-generated** `effects-reference.md` / `conditions-reference.md`.
- **`wgg edit` capability hints** on unknown kinds.

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

The one place that tracks declared-vs-implemented status. All eight register via
decorator and resolve through a registry singleton; only the first four can be
declared in `plugin.yaml`'s `extends`:

| Decorator | Registry | `extends` field | Status |
|---|---|---|---|
| `@effect` | `EFFECT_REGISTRY` | `effects` | done |
| `@condition` | `CONDITION_REGISTRY` | `conditions` | done |
| `@hook` | `HOOK_REGISTRY` | `hooks` | done |
| `@inspect_field` | `INSPECT_FIELD_REGISTRY` | `inspect_fields` | done |
| `@widget` | `WIDGET_REGISTRY` | — | registry only, manifest lags |
| `@scene` | `SCENE_REGISTRY` | — | registry only, manifest lags |
| `@brain` | `BRAIN_REGISTRY` | — | registry only, manifest lags |
| `@dialogue_op` | `DIALOGUE_OP_REGISTRY` | — | registry only, manifest lags |

Plugins can register all eight, but a `plugin.yaml` can only *declare* the first
four — so for the last four the "declared side-effects" guarantee is broken, and
`PluginManager.activate()` does no declared-vs-registered consistency check.
Closing this is the top next PR.

Other plugin-system facts: 3 scan roots (bundled / per-user / pack-local),
`isolate()` wraps every handler call (log + safe default, never an engine
crash), private plugin state lives under `state.meta["__plugin:<id>__"]` and is
filtered out by `SaveManager`, and `depends` + topological load ordering exist
(cycle/version-range detection is still minimal).

---

## Next PRs

In priority order.

### 1. Align the manifest schema with the registry (high leverage, low effort)

`@widget` / `@scene` / `@brain` / `@dialogue_op` work, but `plugin.yaml` can't
declare them, breaking side-effect transparency.

- `plugins/manifest.py` — add `widgets` / `scenes` / `brains` / `dialogue_ops`
  to `Extends` (reuse the `ExtensionDeclaration` shape).
- `plugins/manager.py` — `activate()` compares declared vs registered and warns
  on mismatch (without blocking load).
- `docs/plugins.md` — add `@widget` / `@scene` / `@brain` / `@dialogue_op`
  examples, plus a small `<pack>/plugins/widget_example/` demo.
- Test the declared-vs-implemented mismatch warning path.

### 2. Auto-generate the effect / condition references (medium effort, high long-term value)

The two reference docs are hand-maintained and drift from the registry.

- `tools/gen_reference.py` (new) — run `build_manifest()`, emit markdown.
- `docs/effects-reference.md` / `conditions-reference.md` — mark as generated.
- Add the generation step to the dev loop.

### 3. Wire `wgg edit` to CapabilityManifest hints (medium effort, high UX value)

Today a misspelled kind yields a bare "unknown kind." Add "did you mean ...".

- `dev/pack_editor.py` — on a kind validation failure, run
  `difflib.get_close_matches` against `EFFECT_REGISTRY.list_kinds()`.
- Test the hint path.

### 4. Plugin distribution design (Phase 3 starter)

Sharing one plugin across packs currently means copying. Out of scope for the
PRs above — start with a `docs/distribution-plugins.md` proposal weighing PyPI
namespace vs git submodule vs `pyproject.toml`-pinned, and seek review.

---

## Known gaps

- **References hand-maintained** (Pillar A) — fixed by Next PR #2.
- **`PackEditor` lacks `add_clue` / `add_quest` / `add_achievement`** (Pillar B)
  — it covers scene / choice / npc / location / item / save-migration only;
  other pack-level collections have no dedicated mutator yet.
- **No autonomous spec-to-pack example** (Pillar B) — the loop exists piecewise.
- **Manifest schema lags registry** (Pillar C) — fixed by Next PR #1.
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
| — | Phase 2 wrap-up: manifest schema alignment / dynamic references / `wgg edit` hints | open (Next PRs #1–3) |
| — | Phase 3: LLM NPC, autonomous spec-to-pack, cross-pack plugins | deferred |
