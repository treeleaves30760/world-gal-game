# ROADMAP — World Gal-Game Engine

Where the engine is going next. Read alongside [CLAUDE.md](CLAUDE.md) (AI
onboarding) and [docs/architecture.md](docs/architecture.md) (engine internals).

> Last updated: 2026-08-02. Phases 1-2.5, 5A-5C, 6, and 7 are implemented:
> plugin extensibility, the AI-Coding-Native contract and world model, animated
> portraits, image-sequence / desktop-plugin movie playback, and the warm
> structural-edit loop. The 2026-07-26 reliability pass added exact dialogue
> save/resume, keyboard/gamepad choice navigation, and responsive title layout;
> the 2026-07-28 product-UI pass added fresh-run isolation, truthful save
> discovery, responsive system screens, full-input focus, honest legacy
> reachability exceptions, and a 24-frame dual-resolution visual-review matrix.
> The 2026-07-30 device/visual pass added persistent analogue and touch routing,
> three truthful spatial-map candidate states, and proportional 4K title-brand
> geometry and chrome. The expanded map now discloses one ordinary destination
> at a time, while the travel sheet and choice panel derive their geometry from
> live font metrics and canvas density. The 2026-07-31 ADV-reading pass added
> measured 720p/1080p/4K geometry, lossless row pagination for ordinary long
> lines, keyboard/controller quick-menu focus, and page-aware
> Manual/Auto/Skip/rollback/save-resume contracts. The same-day central
> reduced-motion pass added one live engine policy across scenes, widgets,
> transitions, rich text, portraits, ambient backends and movies. The
> semantic-input pass then added persisted Controls rebinding, conflict swaps,
> dynamic binding prompts, capture isolation, and complete automated
> keyboard/controller action paths; the map hot-path measurement recorded
> MapScene 20.75 ms, destination detail 16.56 ms, and 0 full clones.
> The 2026-08-01 persistence pass added versioned presentation resume,
> current-pack story/map/NPC/media authority, transactional restore, exact PCM
> voice continuation, replay provenance, hard-cut load audio and decoder/plugin
> failure containment. The runtime-localization v1 pass then added versioned
> source/target catalogs, digest/parity validation, presentation-only story and
> UI lookup, per-pack preferences, title-safe switching, locale font sources,
> and the same boundary in headless/warm sessions. The 2026-08-02 Settings P1
> follow-up added a four-page accessibility-first information architecture,
> per-page focus/scroll continuity, responsive audio controls, live persisted
> fullscreen, strict preference normalization, and Menu return continuity.
> The first-hour-flow follow-up then removed terminal-whitespace ghost pages,
> added exact multi-phase future-event CTAs with per-phase interruption,
> introduced opt-in guided-story map handoff after idle dialogue closures, and
> compacted the six-action dialogue rail without shrinking its 44 px targets.
> **Implemented engine capability is not the same as commercial-quality
> proof.** Cross-platform release, performance, accessibility, localization,
> real-asset audio/video, and visual-regression gates remain the next phase; see
> [the commercial-quality benchmark](docs/commercial-quality-benchmark.md).

---

## Status at a glance

The engine is built around three pillars (see CLAUDE.md for the framing):

- **Pillar A — humans author packs:** broad engine capability is implemented.
  YAML pack model, scaffold tool, asset studio, and subsystem docs exist;
  production validation with a commercial-scale pack is still pending.
- **Pillar B — AI develops packs:** the current contract is implemented except
  for an official autonomous spec-to-pack example.
  AI can play, inspect, edit, extend, and self-verify via `HeadlessSession`,
  `GameDriver`, `PackInspector`, `PackEditor`, `CapabilityManifest`, `SelfCheck`,
  `SmokeRunner`, `VisualCheck`, and `asset_studio`. The **world-model** layer
  (Phase 6) adds a typed `VariableManifest`, `DataflowAnalyzer` (writers/readers +
  conditioned edges), a warm NDJSON `SessionServer`, a goal-directed `Planner`,
  and a `CoverageTracker` — so an agent can reason about a pack before editing it,
  not just step through it.
- **Pillar C — third-party plugins extend the engine:** the extension surface is
  implemented. All ten extension points work in code *and* are declarable in
  `plugin.yaml`; cross-pack distribution and trust/release policy remain open.
  `PluginManager` reconciles declared-vs-registered (see
  [Extension points](#extension-points-pillar-c)).

Test inventory: 2,427 tests passed, with 3 environment-conditional skips and
16 expected dummy-SDL renderer warnings, in 106.89 seconds on the 2026-08-01
working tree. CI remains the source of truth for a given commit.

---

## Phases

### Phase 1 — Core extensibility — Done

- Plugin system MVP: `world_gal_game/plugins/{registry,manager,manifest,context,errors,builtin_effects,builtin_conditions}.py`, demo plugin, `docs/plugins.md`.
- `core/game_state.py`'s `apply` / `evaluate` converted from a 39-branch if-elif ladder to pure registry dispatch (with hook firing).
- `core/story_graph.py`: `Effect.kind` / `Condition.kind` moved from `Literal[...]` to `str` + `field_validator`.
- CapabilityManifest, PackEditor (dry-run + diff), PackInspector (incl. dead-end detection, wired into SelfCheck).
- CLI: `world-gal-game edit` / `validate` / `check` / `inspect-pack` / `capabilities`.
- `docs/ai-developer-guide.md`; `pack_format_version` added to `meta.yaml`.

### Phase 2 — More plugin points + AI dev loop — Done

Done:

- Four new extension points wired into the registry: `@widget`, `@scene`,
  `@brain`, `@dialogue_op` (alongside the Phase 1 four).
- `HookEvent` defines 18 constants (more than the 13 originally planned).
  Seventeen are currently emitted; `app.frame` remains reserved. The emitted
  `dialogue.presentation_ready` boundary defers autosave until narrative state
  and UI presentation are both complete.
- AI end-to-end dev loop: `SelfCheck` (schema → refs → dead-ends → softlock →
  reachability → smoke → optional visual), `SmokeRunner`, `VisualCheck` (md5 +
  pixel diff), `asset_studio`.

Wrap-up — done 2026-05-24:

- **Manifest schema alignment** — `plugin.yaml` declares all ten categories;
  `PluginManager` reconciles declared-vs-registered and warns on mismatch.
- **Auto-generated** `effects-reference.md` / `conditions-reference.md` via
  `tools/gen_references.py` (`--check` drift guard).
- **`world-gal-game edit` / `world-gal-game validate` capability hints** — "did you mean ..." on a
  misspelled kind.

### Phase 2.5 — AI-Coding-Native contract — Done (2026-05-24)

See [docs/ai-native-contract.md](docs/ai-native-contract.md).

- **Typed arg models** for all builtin effects/conditions
  (`plugins/{effect_args,condition_args}.py`) → real **JSON-Schema export**
  (`world-gal-game capabilities --schema`) and warning-level validator arg checks.
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
  `world-gal-game variables <pack>`; `world-gal-game validate` does a pure-YAML used-vs-declared
  cross-check ("did you mean" on a typo'd flag, advisory on an unused one).
- **Dataflow / conditioned graph** — `dev/dataflow.py` (`DataflowAnalyzer`):
  per-symbol `writers`/`readers` (flags/scenes/items/resources, incl. reads in
  endings/achievements/clues/quests) and scene→scene `edges` carrying their
  guard conditions. `world-gal-game inspect-pack --dataflow` / `--references <sym>`.
- **Warm NDJSON session** — `dev/session_server.py` (`SessionServer` /
  `run_session`); `world-gal-game session` loads the pack once and streams the `run_script`
  op vocabulary over stdin/stdout (control ops `__ping__`/`__inspect__`/
  `__affordances__`/`__reset__`/`__quit__`). The language-agnostic fast path
  with no per-call process spawn — the "faster than MCP" answer.
- **Goal planner** — `dev/planner.py` (`Planner.find_path`): BFS over the
  next/choose/move/start_scene action space using snapshot/restore to reach a
  goal predicate; `world-gal-game plan --goal '<json>'`.
- **Coverage** — `dev/coverage.py` (`CoverageTracker`): scene/line/choice/ending
  coverage of a run vs. the pack totals; `world-gal-game coverage <pack> --script`.
- **Player rollback** — `core/history.py` (`StateHistory`): a bounded
  (snapshot, presentation) stack on the *same* `dev/diff` machinery the agent
  uses for branch exploration. `DialogueScene` records each display and rewinds
  on **Backspace** (`EngineConfig.rollback_enabled`, default on) without
  re-running the engine. One mechanism, two audiences.
- Also fixed: `inspect()` raised `AttributeError` when an NPC was present
  (`present_npcs` yields id strings) — surfaced by the planner's state sweep.

### Phase 3 — Distribution, platforms, presentation, autonomy — In progress

The engine-side capabilities below are present. Phase 3 remains in progress
because the production gates and real-target release evidence are not complete:

- **Distribution:** web (pygbag/WASM, `build_web.py` + `platform_web.py`),
  Steam (`integrations/steam_bridge.py` + `steam_plugin.py`: achievements,
  cloud saves), Android/mobile + PWA (`build.py`, `test_build_android.py`,
  `test_web_pwa.py`). Docs: `distribution-{web,steam,mobile}.md`.
- **Presentation:** rich text (`dialogue/richtext.py` — BBCode-style parser with
  color/size/waits/speed/ruby/per-glyph effects), `ui/easing.py`, portrait
  animation, responsive/letterbox rendering, touch input, per-line voice
  channel, and `play_movie` routed through the web-safe image-sequence player
  or the optional desktop video plugin.
- **VN table-stakes (presentation + extras):** CG gallery / music room / scene
  replay / endings + completion overlays (reached from the in-game menu and the
  title "鑑賞模式"), Auto/Skip polish + on-screen indicators, NVL mode,
  builtin camera/screen FX (`camera_pan`/`camera_zoom`/`screen_shake`/
  `screen_flash`/`screen_tint`), per-character voice volume, quicksave (F6) /
  quickload (F9), bundled autosave plugin, save thumbnails + scrollable save UX,
  and settings persistence (`settings.json`). Media auto-unlocks when a line
  displays a `cg` / plays a `bgm`; `show_cg` follows the same unlock rule.
  Optional `content/cg_gallery.yaml` supplies a strict authored roster,
  title/subtitle/description, chapter/character context, thumbnails, ordering,
  hidden-spoiler semantics, and save-compatible path fallbacks. Docs:
  `docs/presentation-and-extras.md`.
- **Versioning:** `core/pack_migration.py` — load-time, plugin-extensible pack
  save migration (`@save_migration`), plus `PackEditor.scaffold_save_migration`.

Reliability pass — done 2026-07-26:

- **Exact dialogue save/resume:** manual and quick saves reconstruct the
  displayed line without replaying effects, including choice prompts, portrait
  presentation, NVL transcript, and the exact remaining time of an active
  inline wait. Autosave uses
  `dialogue.presentation_ready` to wait until narrative state and UI
  presentation form one complete transaction before capturing the same
  envelope. Legacy saves without `_resume` continue into exploration. Covered by
  `tests/test_dialogue_save_resume.py` and `tests/test_save_manager.py`.
- **Keyboard/gamepad choices:** `ChoiceMenu` supports `InputState.nav`,
  confirm, disabled-row skipping, initial focus, and wrap while retaining the
  mouse path. Covered by `tests/test_choice_menu_navigation.py`.
- **Responsive title layout:** the full and extras menus fit 1280×720 and
  1920×1080 without clipping the panel or branding. Covered by
  `tests/test_title_scene_layout.py`.
- **Readable persistent dialogue chrome:** the status HUD and six-action
  quick menu plus progressive “More” layer scale their hit rectangles through
  4K, meet the 720p/1080p text-height floor, and expose keyboard/controller
  focus. Covered by `tests/test_dialogue_accessibility_layout.py` and
  `tests/test_dialogue_quick_menu.py`.
- **Strict visual baseline gate:** `visual-check --strict-baselines` and
  `self-check --strict-visual-baselines` fail when a reviewed baseline is
  missing; baseline creation remains an explicit development/update action.
- **Consistent pack resolution:** developer subcommands use the runtime
  resolver for bare names, relative paths, sibling names and absolute paths;
  `VisualCheck` preserves the resolved absolute path instead of re-resolving a
  basename, so an external pack cannot be confused with a same-named pack.
- The complete engine suite passed 1,251 tests on 2026-07-26. This is feature
  evidence, not a substitute for the production gates below.

Product-UI reliability pass — done 2026-07-28:

- **Fresh New Game:** returning to the title and choosing New Game reloads the
  pack into a fresh `GameState`, dialogue engine, scene context, plugin context,
  autosave bridge, and Steam bridge. Runtime route/time/inventory/affection/map
  and plugin state cannot leak into the next run; only the title-entered player
  name and explicit cross-run clear/read data survive. Clear data is unioned
  back into read-log, CG, and currently registered ending trackers on launch,
  New Game, quickload, Continue, and formal load; ordinary menu/OS/sync/async
  shutdown paths all persist it.
- **Truthful save discovery:** `SaveManager` excludes `clear_data.json` and
  unrelated JSON from Continue/load while retaining legacy v0 save discovery
  and migration. Loading an occupied slot and overwriting a save require a
  cancel-first confirmation; cancel is side-effect free. JSON and thumbnail
  staging now preserve the previous pair if the final JSON commit fails, and
  thumbnail-less overwrites cannot retain stale art.
- **Product-scale system screens:** the in-game menu is a three-category hub
  instead of a 20-item wall. Save/load, settings, and CG gallery use
  content-driven 360p/450p/720p/1080p layouts, visible keyboard/gamepad focus,
  scrolling, and 44 px-or-larger targets.
- **One activation path:** pointer, keyboard, and gamepad actions share
  `Button.activate()`, including disabled guards, click feedback, callbacks,
  and an immediate visible focus ring.
- **Unbounded reusable scrolling:** `ScrollArea` no longer allocates a fixed
  8000 px surface and a second viewport every frame. Its buffer grows to the
  measured content height, is reused across frames, clamps immediately when
  content shrinks, and renders rows beyond 9000 px without a blank tail.
- **Virtualized dialogue history:** the legal 500-line backlog keeps only row
  geometry and renders the viewport plus one overscan row. It has no
  full-content surface, supports pointer/touch/keyboard/controller navigation,
  restores the latest reading position, and replays voiced rows at the
  canonical per-character volume. A local dummy-SDL benchmark reduced the
  former 101/221/856 MiB owned surfaces to 0 MiB at 720p/1080p/4K; first draw
  measured 6.93/9.07/25.48 ms instead of 567/623/1060 ms.
- **Historical replay is evidence-backed:** the first completed viewing stores
  a bounded evaluated presentation trace in cross-playthrough clear data.
  Replay redraws that exact conditional/interpolated path and selected choice
  without evaluating conditions, invoking an LLM, dispatching effects, or
  following authored transitions. Legacy read bits, incomplete traces, edited
  payloads, and stale authored provenance fail closed. The virtualized library
  supports pointer, touch, keyboard, remapped bindings, and controller input;
  completion, semantic Back, overlay removal, and exceptions restore BGM,
  ambient, voice, and replay-owned one-shot SFX without stopping pre-existing
  live SFX or writing any active-run state. Because the archive is persistent,
  the same library is available from both the title Extras menu and the
  in-game menu; its bounded title viewport keeps 44 px rows through 360p.
- **Honest legacy reachability exceptions:** packs may declare
  `verification.expected_unreachable` cases for compatibility-only scenes and
  endings. Raw inspector results remain visible; malformed, duplicate, unknown,
  newly reachable, or wrong-kind exemptions fail instead of silently hiding a
  regression.
- **Broader visual contract:** default `VisualCheck` captures sixteen semantic
  views at 1280×720 and 1920×1080: title, a real fresh-New-Game onboarding
  stack, dialogue, choice, exploration,
  story-priority spatial map, all-reachable spatial map, travel confirmation,
  pause menu, save, load, settings, CG gallery, populated dialogue history, and
  a non-empty evidence-backed scene replay library, and flowchart. Strict mode
  still fails until all 32 candidates have been reviewed and promoted; candidate
  generation alone is not a passing gate.
- **Persistent device routing:** one application-lifetime `InputRouter` now
  retains left-stick deadzone/hysteresis/repeat and cross-frame touch contacts.
  Touch activates on release, distinguishes tap/drag/swipe, cancels on
  multi-touch, suppresses emulated ghost mouse clicks, and feeds finger deltas
  to reusable `ScrollArea` and `Slider` controls. Deterministic tests do not
  replace controller/WebView/iOS real-device QA.
- **High-density title branding:** branded rails, raster wordmarks, padding and
  controls scale with the full canvas density instead of stopping at the 1080p
  factor while typography keeps growing. Structural strokes now scale as
  1/2/3 physical pixels at 720p/1080p/4K. Regressions prevent both the
  half-sized rail/logo mismatch and one-pixel 4K chrome.
- **Measured decision and travel UI:** the choice panel derives prompt,
  button, reason, gap and total-panel geometry from live font metrics. Expanding
  a guided map hands focus to one ordinary destination and its bounded detail
  strip; travel confirmation uses a compact density-aware itinerary card and
  conservative cancel-first focus.
- **Bounded decoded-asset retention:** image/scaled/placeholder surfaces share
  a 256 MiB byte LRU; short sounds use a 64 MiB decoded-PCM LRU; BGM stays
  outside that cache and retains at most the two crossfade slots. Immediate
  stop, pack switches and stale voice/ambient refs release their ownership;
  app inspect, GameDriver and the dev overlay expose cache/live-audio telemetry.
- The integrated engine suite passed 1,691 tests with 10 expected dummy-renderer
  warnings in 173.95 seconds on 2026-07-31 after the cache-budget, CG catalog,
  save/reload lifecycle, responsive gallery and visual-check refinements.

ADV-reading reliability pass — done 2026-07-31:

- **Measured reading viewport:** ADV box, body, footer, nameplate, quick rail,
  status HUD and NVL spacing share live font metrics and full canvas density.
  Standard canvases reserve three body rows; short 450p-class canvases reserve
  two. Geometry is covered at 720p, 1080p, 4K, 1.0×/1.4× text and 800×450.
- **Page-aware long lines:** ordinary long authored lines are partitioned by
  laid-out rows without changing the story cursor, voice, effects, read log,
  backlog or rollback checkpoints. Manual, Auto and Skip each respect visual
  pages; choices still require a separate player input.
- **Resolution-independent resume:** saves carry additive page ordinal, raw
  reveal cursor and authored-text cursor. Same-layout loads preserve the exact
  side of an implicit wrap; changed resolution/text size reflows to the same
  authored character without revealing extra text. Explicit blank lines and
  inline waits have round-trip regressions. Inline-wait progress is stored in
  authored plain-text space, so active waits retain only their remaining time
  across ADV/NVL, page boundaries, and reflow; legacy v1 envelopes without the
  additive timeline remain loadable with their historical full-wait fallback.
- **Inspectable and reproducible:** `DialogueScene.describe()` exposes
  `adv_page`; the real pack opening matrix now contains 24 source-bound
  review candidates across 720p/1080p/4K. Candidates remain unapproved until
  human visual review.
- The integrated engine suite passed 1,803 tests with 10 expected
  dummy-renderer warnings in 146.01 seconds. Pathological single rows taller
  than the viewport, BGM/ambient fade-position proof and real-player reading
  comfort remain open gates; exact voice PCM continuation landed in the later
  persistence pass below.

ADV terminal-newline follow-up — done 2026-08-01:

- YAML literal blocks keep their authored terminal newline for plain/reveal
  cursor and save compatibility, while pagination excludes only trailing empty
  layout rows and Unicode whitespace. An exactly three-line presentation
  therefore has one visual page and one Enter advances to the next authored
  line; internal blank rows remain visible and lossless.
- Product regressions cover LF/CRLF three-line boundaries, Unicode terminal
  whitespace, empty and repeated-LF inputs, internal blank rows, resolution /
  text-scale pagination, multipage reflow/restore, terminal inline waits, and
  a real `GameDriver` Enter transition. The complete engine suite passed
  2,416 tests, with 3 environment-conditional skips and 16 expected
  dummy-renderer warnings, in 107.82 seconds.

Choice text-scale and explicit-confirm follow-up — done 2026-08-01:

- Standard 1.0× route cards retain their existing layout pixel-for-pixel.
  When larger live text no longer fits three full cards, `ChoiceMenu` switches
  to measured tabs plus one scrollable preview instead of clipping or silently
  shrinking authored copy. CJK and unbroken tokens wrap losslessly, and a live
  scale change preserves focus by stable choice id.
- Tabs are preview controls only, including disabled choices. Selection now
  requires the fixed, real 44 px action button or an explicit keyboard/gamepad
  confirm on an enabled focused choice; disabled reasons are rendered
  separately and never replace the authored action. Pointer capture prevents a
  touch drag ending over the action button from committing a route.
- High contrast uses engine semantic focus, borders and text rather than an
  arbitrary pack accent. Focus, locked-preview, CTA, mouse, keyboard, gamepad,
  tap and drag behavior are covered together with 1.0×/1.4× and short-canvas
  geometry. The integrated engine suite passed 2,539 tests with 3
  environment-conditional skips and 16 expected dummy-renderer warnings.
  Human review with disabled players remains an external product gate.

Persistence, replay and media reliability pass — done 2026-08-01:

- **Versioned and transactional presentation restore:** `_resume.version: 1`
  remains additive, while voice and visual timelines have independent inner
  versions; pack migrations receive state and `_resume` together. Detached
  candidate validation happens before live state/stack replacement, and direct
  live-scene restore commits a fresh widget state only after an audio rollback
  snapshot and full reconstruction succeed.
- **Current-pack authority:** load deep-copies the current StoryGraph and map
  catalog, keeps only valid cursor/played/current/visited progress, and uses the
  current NPC registry for speaker/portrait backends. Every resume asset is
  proven against that story/map/NPC authority before decoder or plugin access;
  save-embedded content cannot widen it.
- **Exact audio and replay provenance:** playing voice v2 resumes only from a
  frame-aligned decoded-PCM identity match; ambiguous legacy/replaced/unsupported
  checkpoints fail silent. Replay FX, effective BGM/ambient carry, SFX/voice,
  CG/portrait/card and NPC speaker identity are checked against current authored
  provenance while historical non-NPC display copy remains intact.
- **Persistent media and load cut:** CG/Music Room progress is intersected with
  a current-pack `MediaAuthority`, and clear data applies the same CG filter on
  record and restore. Load hard-cuts outgoing voice and changed/silent BGM or
  ambient, including untracked fade tails, before a replacement attempt.
  Image failures degrade to placeholders, audio failures to silence, and
  isolated plugin/backend exceptions remain local.
- These are engine reliability contracts, not production proof. Packaged
  cross-version journeys, real codecs/devices, long-run memory/audio behavior
  and external QA remain release gates.
- The final integrated engine regression for this pass was 2,416 passed and 3
  environment-conditional skips, with no failures.

Central reduced-motion reliability pass — done 2026-07-31:

- **One live authority:** `SceneContext.motion` dynamically reads the persisted
  setting and is shared by SceneManager, dialogue/map/system widgets and Toast;
  an overlay toggle affects already-running presentation on the next frame.
- **Semantic degradation:** required feedback snaps, opacity hand-offs are
  capped at 120 ms, spatial/flash/continuous decoration stops, and authored
  content such as typewriter progress, voice and movies remains intact.
- **Safe final state:** camera directives snap to their final framing,
  reset/clear directives still run, active shake/flash/blur is removed, every
  non-cut transition becomes a short dissolve, and active weather/portrait
  backends freeze under engine control.
- **No content trap:** looping or authored-unskippable movies remain escapable
  in reduced mode through the normal completion callback.
- The integrated engine suite passed 1,892 tests with 16 expected
  dummy-renderer warnings in 151.68 seconds after the final semantic-input,
  runtime/plugin propagation, and save-screen integration fixes.
- Remaining product gates are OS-preference discovery, temporal visual
  evidence on supported devices, and accessibility review with real users;
  they are not missing central runtime propagation.

Semantic-input and map hot-path reliability pass — done 2026-07-31:

- **One semantic action contract:** stable action ids and metadata now drive
  pressed/released/held input while preserving raw physical key edges, held
  state, legacy confirm/cancel/navigation aliases, and synthetic events.
  Keyboard profiles update live; controller confirm/back/D-pad/analogue
  semantics remain fixed and therefore cannot be stranded by a keyboard
  rebind. Cross-frame holds, releases, focus loss, and horizontal/vertical
  controller navigation have deterministic coverage.
- **Player-owned Controls profile:** normalized bindings persist in
  `settings.json`. The Controls screen supports context-aware conflict swap,
  protects reserved F1/F5/F11 and required actions, and resets through empty
  overrides. Dynamic full and primary/concise labels let onboarding and narrow
  hints reflect the active profile without overflowing.
- **Capture does not leak:** rebind capture consumes the physical key frame
  before scene or global semantic dispatch, so assigning F6/F9/F12 cannot
  quicksave, quickload, take a screenshot, activate a row, or advance dialogue.
- **Measured map hot path:** the focused local benchmark recorded
  **MapScene 20.75 ms**, destination **detail 16.56 ms**, and **0 full
  clones**. Lightweight route probes avoid cloning; isolated preview state is
  reserved for conditions that actually need it. These values are local path
  measurements, not target-device frame-time, memory, cold-start, or soak
  certification.
- **External release gates remain open:** physical controllers, regional and
  non-US keyboard layouts, focus restoration across device changes,
  accessibility sessions with disabled players, and human-approved
  interaction/visual baselines must still pass on supported targets.

Visual Accessibility Policy v1 — engine capability done 2026-08-01:

- **One strict live authority:** persisted `visual_accessibility_mode` accepts
  only `standard | high_contrast`, updates the next frame, and falls back to
  canonical standard for malformed saved values.
- **Standard fidelity and semantic high contrast:** standard mode preserves the
  authored theme path. High contrast applies engine-owned semantic UI colors
  and opaque reading/control planes without filtering authored CG or portraits.
- **Profile-aware system chrome:** the visual profile is part of cache identity;
  pack renderers declare supported profiles, while an unsupported or failing
  high-contrast renderer safely falls back to stock chrome without poisoning
  its standard cache entry.
- **Stock widget and dialogue coverage:** `Panel`, `Button`, Settings, ADV and
  NVL support live switching. Focused, selected and disabled controls use
  boundaries, inset shape or hatching in addition to color; authored inline
  text colors are suppressed only in high contrast and return in standard.
  High-contrast screen tint and flash composite into the world layer before
  dialogue/system chrome, so authored effects cannot recolor the certified
  text and control planes.
- **Automated acceptance evidence:** the 16-combination 640×360–3840×2160,
  text-scale 1.0/1.4 and reduced-motion on/off matrix covers Settings and ADV
  containment, reading planes, glyphs, focus and selection. The complete engine
  suite passes **2,523 tests with 3 environment-conditional skips**.
- **External gates remain open:** a dedicated color-vision-deficiency mode,
  supported-device review with disabled players, and external art/UX approval
  are still required. This engine milestone is not commercial-quality proof.

Settings information architecture P1 — engine capability done 2026-08-02:

- **Four fixed pages:** Reading, Audio, Accessibility, and Controls/Display are
  persistent tabs outside the scrolling body. Wide layouts use one four-column
  row; narrow portrait layouts use a measured 2 × 2 grid without abbreviating
  translated labels or shrinking interaction targets below 44 px.
- **Accessibility-first entry:** Accessibility is the default page and initial
  focus order makes high contrast, reduced motion, and text scale reachable in
  exactly one, two, and three semantic operations respectively. Each page keeps
  focus by stable control id and its own bounded scroll across page switches and
  live rebuilds.
- **Responsive, honest content:** wide audio controls share one measured row;
  narrow layouts stack the slider beneath its label/value/steppers while keeping
  touch ownership. Per-character volume rows exist only for NPCs with an
  authored voice cue or a pre-existing explicit override.
- **Live integration and navigation:** fullscreen changes apply to the desktop
  window and persist the new boolean; headless sessions persist without creating
  a display. Opening Settings from the in-game Menu keeps that exact `MenuScene`
  underneath, so Back restores its System category and Settings focus.
- **Untrusted preference boundary:** persisted booleans are exact booleans;
  Settings-owned numerics must be finite, numeric (not strings or booleans), and
  in supported ranges; per-character volume keys/values and mapping size are
  bounded. Load rejects malformed fields without truth coercion, and save
  canonicalizes direct embedding mutations before serialization.
- Automated layout, semantic-input, configuration-boundary, and App-integration
  regressions cover these contracts. Human-approved visual baselines,
  real-device fullscreen/touch/controller checks, and disabled-player review
  remain open release gates.

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

The one place that tracks declared-vs-implemented status. All ten register via
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
| `@ambient_backend` | `AMBIENT_BACKEND_REGISTRY` | `ambient_backends` | done |

All ten register via decorator and can be *declared* in `plugin.yaml`'s
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

### 1. Commercial-quality benchmark and production gates

Use [docs/commercial-quality-benchmark.md](docs/commercial-quality-benchmark.md)
as the gap baseline. The next phase is not another list of engine APIs; it is
repeatable evidence that a shipping pack works under player and release
conditions:

- `Production Check v1` now provides a strict, fail-closed source preflight:
  exact seven-stage self-check, the approved visual matrix, deterministic union
  coverage, a non-empty narrative contract and calibrated runtime evidence are
  bound to clean, fully tracked, byte-stable engine/pack Git subjects in one
  expiring, self-digested artifact. `production-verify` rechecks that artifact
  against current inputs. Repository-local top-level `.venv/` tooling state is
  excluded from the source snapshot, while symlinks in included product paths
  remain a hard failure. This closes the engine capability, not the pack proof:
  no commercial-scale pack has supplied all passing evidence yet.
- Review and promote the canonical 32-candidate (16 semantic views ×
  1280×720 / 1920×1080) matrix. Automated semantic keyboard/controller
  traversal and Controls capture are implemented; the release gate must now
  exercise physical controllers, regional keyboard layouts, touch,
  disabled-player workflows,
  exact save/resume, locale/font coverage, required production assets, and
  audio/video target compatibility.
- Reusable decoded-image and short-sound caches are now byte-bounded
  (256/64 MiB defaults), BGM retention is limited to the two crossfade slots,
  and cache/live-audio telemetry is available. `Local Runtime Reliability Gate
  v1` is now an engine capability: its fresh-process app-ready/first-frame probe
  and deterministic semantic-view soak always attempt a policy-bound JSON
  artifact with platform, revision, RSS, frame-time, cache, protocol and failure
  evidence. A future pack release workflow must run Production Check v1
  independently from default `self-check` and always upload the report; it is
  intentionally not wired as a permanently red gate before the pack provides
  approved baselines and numeric budgets. Still establish owner-approved
  end-to-end RSS and frame-time budgets for dialogue, transitions, weather,
  movies, renderer caches and non-1:1 scaling on representative target devices;
  provisional dummy-SDL source-runtime numbers are calibration evidence, not
  packaged-binary or production-platform claims.
- Build and launch-test real desktop/web targets; promote Android, Steam,
  signing, native video, and other integrations from best-effort to blocking
  only when their target-specific jobs exist.

Until those gates pass on a real-asset, commercial-scale pack, status wording
must remain **engine capability implemented**, not **commercial-production
ready**.

### 1A. Destination schedule map

[docs/destination-schedule-map.md](docs/destination-schedule-map.md) is now an
implemented engine capability: the spatial map, story-first destination layer,
preview/confirmation sheet and atomic graph travel all quote the same
`TravelPlan`. Commercial release evidence and external UX/art approval remain
product gates.

- **P0 correctness — done:** one immutable `TravelPlan`, deterministic time-dependent
  pathfinding, projected-arrival NPC/event evaluation, stale-plan validation,
  and a shared `TravelService` for GUI/headless. Cancel and failed commit must
  be side-effect free; confirm must charge once and fire the advertised hook.
- **P1 presentation — engine capability done:** strict pack-authored
  `travel.spatial_map` schema, stable illustrated canvas anchors,
  campus/interior/story-only hierarchy, directional off-canvas thumbnail
  cards, character/event markers, route detail/confirmation, list fallback,
  44 px targets, keyboard/gamepad/pointer focus, responsive 720p/1080p and
  reduced motion. Guided story mode contains only the current position and
  authored story offers; the explicit ordinary layer reveals off-campus cards
  without leaving an empty perimeter rail while those cards are hidden.
  Future offers expose one exact day/phase/count CTA, internally retain the
  existing per-phase wait authority, and stop at blocked or ambient-dialogue
  boundaries. Finite guided sequences may opt into `auto_reopen`, which hands
  an otherwise idle dialogue closure back to the map only after explicit
  scene-map rules, same-location hooks, ambient dialogue, and modals decline
  ownership.
  Missing art degrades safely; global and edge-card
  attributions render in the map UI, while validator checks paths,
  decodability, attribution, references, bounds and parent cycles. A focused
  local measurement recorded MapScene 20.75 ms and detail 16.56 ms with zero
  full clones; representative target-device budgets remain separate.
- **P2 pack proof — playable slice done:** after `prologue_settle` writes
  `intro_done`, the target pack automatically opens the real campus map with
  only the current dorm and one zero-cost main-gate story offer. Orientation
  then reuses that map for the pre-gate three-stop newcomer tour, limiting
  movement to authored zero-cost direct exits. The primer opens the formal
  graph, presents equal heroine story markers, consumes the quoted trip cost
  only after confirmation, and starts the promised arrival scene.
  The target pack uses the same map again after every unfinished guided
  closure and replaces repeated one-phase empty waits with a single explicit
  wait-to-event action; its GUI route proof checks exact time, unchanged
  location, one CTA per gap, and the separate final travel cost.
  Old saves preserve progress while rebinding the current authored map
  catalog. GUI/headless focused contracts cover cancel, stale state, graph
  gates, arrival time and hook parity.
- **Remaining product gate:** approve visual baselines; run real-device touch,
  physical-controller, regional-keyboard and fullscreen matrices; include
  disabled players in accessibility sessions; and obtain external UX/art
  review. These are commercial-shipping gates, not missing movement or input
  semantics.

This remains engine-generic. Character identity, story-stage progression, map
art, event previews, and route eligibility belong to the pack.

### 2. GalGame maturity follow-through

Mostly plugin-able (`@scene` / `@widget` / `@dialogue_op` /
`@portrait_backend` / `@ambient_backend`). See `docs/galgame-maturity.md`.

- **5A — animated 立繪 — done.** `@portrait_backend` (9th extension category) is
  the seam in `core/portrait_spec.py` (`backend` / `backend_args`); the dialogue
  scene delegates the resting draw, transitions stay surface-based, `"static"`
  is unchanged. Bundled `animated_portraits` plugin ships web-safe `breath`
  (procedural idle) + `sprite` (sheet frames) + `layered` (blink + lip-sync +
  breathing on stacked PNGs — the flagship cross-platform rig; lip-sync driven
  by a `talking` signal the scene feeds via `update(dt, **ctx)`).
  **Remaining:** native Live2D / Spine rigs as a desktop-only plugin (no pygame
  binding; out of core).
- **5B — video / movie playback — engine capability done.** `play_movie`,
  `MoviePlayerScene`, and the built-in image-sequence player provide a
  pygame/web-safe path; the optional `desktop_video` plugin provides real video
  through pyvidplayer2/FFmpeg. **Proof still pending:** real encoded assets,
  audio/loop synchronization, browser support, dependency packaging, and
  target-specific performance/release gates.
- **5C — runtime localization v1 — engine capability done.**
  `tools/i18n_extract.py` now emits/checks bounded versioned source and target
  catalogs under `content/i18n`, including source SHA-256 binding,
  placeholder/markup/dialogue-op parity, `complete` versus preview
  `story_scenes`, and optional stable `Line.loc_id` enforcement. One
  session-scoped authority resolves meta/UI/time/affection and authored story
  presentation without changing IDs, conditions, effects, transitions, state,
  or assets. Per-pack preferences, title-only GUI switching, locale font
  sources, headless/NDJSON locale inspection and switching, and web catalog
  staging are wired. **Production follow-through remains:** key every raw
  system string, true per-glyph fallback, text-expansion/reflow regressions,
  complete game translations, and human linguistic/cultural review.

### 3. Plugin distribution design

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
- **Commercial-quality proof is incomplete** — authoring-mode visual baselines
  remain optional, web/mobile/native-video jobs are not yet release-blocking,
  and no commercial-scale real-asset pack has passed Production Check v1.
- **Input capability is not external input certification** — semantic
  keyboard/controller paths, rebinding, conflict swap and capture isolation
  are implemented, but physical controllers, regional keyboard layouts,
  disabled-player sessions and human baseline approval remain release gates.
- **Runtime localization v1 is implemented, production localization is not** —
  story/meta and keyed UI/time/affection copy apply at runtime, language-specific
  font sources work, and preferences are isolated per pack. Not every legacy
  raw system string is keyed yet; legacy history rows and LLM-generated lines
  cannot always be re-localized; true per-glyph fallback, text-expansion/reflow
  coverage, complete pack translations, and human linguistic/cultural QA remain
  open. GUI switching intentionally stays at the title boundary in v1.
- **No `@field` decorator** for public plugin state — plugins use
  `state.meta["__plugin:<id>__"]` for private state and `@inspect_field` for the
  read path; writing public fields into `GameState` is not yet a first-class API.
- **Cross-pack plugin distribution** (Pillar C) — undecided; see Next PR #3.

---

## Risks and tradeoffs

| Risk | Approach |
|---|---|
| Core minimalism vs. plugin power | Core holds only genre-agnostic primitives; new-theme effects always go through plugins. Settled in Phase 1. |
| Pack-owned system UI chrome | `ui_skin` selects an `@widget` declaring `ui_chrome/v1`; `SystemChromeHost` caches and isolates paint-only requests while core retains text, focus, geometry, and input. |
| AI interface vs. human interface | Not two stacks. `PackEditor` / `CapabilityManifest` are thin wrappers over one schema; the CLI is another layer on top. Settled in Phase 1. |
| Backward compatibility | `pack_format_version` in `meta.yaml` (`"0.1"`); `core/pack_migration.py` provides the load-time migration path. |
| Plugin trust | No sandbox (single-player). `plugin.yaml` must declare extension points and side effects; `PluginManager.print_summary()` prints a load summary. Revisit as Phase 2 breadth grows. |
| Performance | Reusable image/short-sound caches have byte budgets, BGM has bounded retention, and the focused map path measured 20.75 ms / 16.56 ms with 0 full clones. Hooks and presentation effects still run on hot paths; representative 720p/1080p frame-time, total-RSS and real-device gates are still required before a production-ready claim. |
| MCP server | Still not building one. Claude Code / Codex import `HeadlessSession` / `PackEditor` / `PackInspector` / `CapabilityManifest` directly. |

---

## Changelog

| Date | Milestone | Status |
|---|---|---|
| 2026-05-18 | Blueprint set (CLAUDE.md + ROADMAP.md) | done |
| 2026-05-18 | Phase 1: plugin system MVP (`@effect`/`@condition`/`@hook`/`@inspect_field`) | done |
| 2026-05-18 | Phase 1: CapabilityManifest + `world-gal-game capabilities` | done |
| 2026-05-18 | Phase 1: PackEditor (dry-run + diff) + PackInspector + `world-gal-game inspect-pack` | done |
| 2026-05-18 | Phase 1: dead-end detection in SelfCheck; `pack_format_version` added | done |
| 2026-05-19 | Phase 2: four new extension points (`@widget`/`@scene`/`@brain`/`@dialogue_op`, registry side) | done |
| 2026-05-19 | Phase 2: initial 16 lifecycle hooks (18 names defined; 17 currently emitted) | done |
| 2026-05-19 | Phase 2: AI dev loop (SelfCheck + SmokeRunner + VisualCheck + asset_studio) | done |
| 2026-05-19 | Clue / Journal system (`core/clue.py`, `scenes/clues_scene.py`, `ui/widgets/clue_log.py`, `content/clues.yaml`) | done |
| 2026-05-22 | Phase 3: distribution + platform + presentation (web/Steam/mobile, rich text, voice, touch, responsive, pack migration) | in progress (working tree) |
| 2026-05-23 | Phase 3 presentation/extras table-stakes: CG gallery, music room, scene replay, endings + completion, Auto/Skip polish, NVL mode, camera/screen FX, per-character voice, quicksave/autosave + save-UX | done |
| 2026-05-24 | Phase 2 wrap-up: manifest schema alignment (all 8 categories) + reconciliation, dynamic references (`tools/gen_references.py`), `world-gal-game edit`/`validate` did-you-mean | done |
| 2026-05-24 | Phase 2.5: AI-Coding-Native contract — typed arg JSON-Schema (`--schema`), `run_script` trace/diff + apply/check/assert/affordances/snapshot/restore, determinism seed | done |
| 2026-05-24 | Phase 5C: i18n translation extraction (`tools/i18n_extract.py`) — pack scenes → string table + `--check` coverage | done |
| 2026-05-24 | Phase 5A: animated portraits — `@portrait_backend` (9th extension category), `PortraitSpec.backend`/`backend_args`, dialogue-scene seam, bundled `animated_portraits` (breath + sprite + layered blink/lip-sync/breathing rig) | done |
| 2026-05-25 | Phase 6: AI-native world model — variable manifest, dataflow/conditioned graph, warm NDJSON session, goal planner, coverage (`world-gal-game variables`/`inspect-pack --dataflow`/`session`/`plan`/`coverage`) + player rollback (`StateHistory`, Backspace) on the shared snapshot machinery | done |
| 2026-05-30 | Phase 7 (P0): warm structural-edit loop — `edit.*` ops in `HeadlessSession`/`SessionServer` (autocommit), `begin`/`commit`/`rollback` transactions, `"atomic": true` batches, `reload`, a uniform `changed` envelope, and a post-edit `impact` delta (`dev/world_model.py`: new dead-ends / unreachable endings / undeclared flags) — understand → edit → verify in one warm process | done |
| 2026-05-30 | Phase 7 (P1): planner state-key now includes inventory/affection/resources/time (correct reachability); dataflow edges gained structured `guard_logic` ({all,none}); token-frugal `world-gal-game brief` / `world-gal-game card` (~7x smaller than `context`); narrative contracts (`dev/contract.py`, `world-gal-game contract`: reachable/unreachable/holds/path_reaches) | done |
| 2026-05-30 | Phase 7 (P2): `PackEditor` add_quest/add_clue/add_achievement/add_resource (+ warm `edit.*`); first-class chapter/act/route overlay (`core/chapter_spec.py`, optional `content/chapters.yaml`, `world-gal-game chapters [--check]`, surfaced in inspect/brief) | done |
| 2026-07-26 | Phase 5B engine capability: `play_movie`, `MoviePlayerScene`, web-safe image sequences, optional desktop video plugin | done; production proof remains |
| 2026-07-26 | Player reliability: exact dialogue resume; post-presentation autosave transaction hook; keyboard/gamepad choices; responsive 720p/1080p title layout | done; focused tests green |
| 2026-07-26 | Commercial-quality benchmark and strict production/release gates | next phase |
| 2026-07-28 | Product-UI reliability: fresh New Game state; save discovery/confirmation; categorized menu; responsive save/settings/CG; unified focus/activation; explicit expected-unreachable compatibility contract; 24-candidate dual-resolution visual matrix | done; reviewed baselines remain open |
| 2026-07-30 | Device/visual proof: persistent analogue/touch router; release-tap/drag/multi-touch semantics; story/all/confirmation spatial-map candidate captures; proportional 4K title branding | done; real-device and reviewed-baseline gates remain open |
| 2026-07-30 | Product CG catalog: optional strict `content/cg_gallery.yaml`; authored captions/order/context/thumbnail/hidden semantics; effect-only CG unlock parity; spoiler-safe headless inspection; visible completion totals and representative visual candidates | done; human art/copy review and reviewed baselines remain open |
| 2026-07-31 | Measured ADV reading viewport: 720p/1080p/4K density, row pagination, page-aware Manual/Auto/Skip/rollback, reflow-safe resume, quick-menu full-input focus, 24 source-bound opening candidates | done; pathological oversized-row validation, reviewed baselines and external UX remain open |
| 2026-07-31 | Central reduced-motion runtime: shared live policy, 120 ms opacity fallback, spatial/flash/continuous freeze, backend enforcement, movie escape and regression matrix | done; OS-default discovery, temporal device evidence and real-user accessibility review remain open |
| 2026-07-31 | Semantic input and map hot path: persisted Controls rebinding, context conflict swap, dynamic/concise prompts, capture no-through, complete automated keyboard/controller actions; MapScene 20.75 ms, detail 16.56 ms, 0 full clones | done; physical controllers, regional keyboards, disabled-player sessions and human-approved baselines remain open |
| 2026-07-31 | Headless player-equivalence boundary: structured `examine`／`gift`, dialogue-locked movement/map/time, post-dialogue ambient hooks, per-phase time interruption and natural New Game-to-ending replay | done; developer controls remain explicitly excluded from player-reachability evidence |
| 2026-08-01 | Semantic sound-caption vertical slice: strict line model, settings, measured 360p–4K ADV/NVL safe areas, history/save/rollback/replay/headless preservation and hostile resume/replay provenance; Backlog voice ownership restoration; first target Pack six-cue plus five silence/two room-tone boundary proof | engine capability done; full-pack cue coverage, screen-reader integration, external audio production and disabled-player review remain open |
| 2026-08-01 | Local Runtime Reliability Gate v1: fresh-process app-ready/first-frame, current+peak/growth RSS, deterministic semantic-view soak, strict frame/cache policy and always-uploaded evidence | engine capability done; target-device numeric budgets and packaged launch proof remain open |
| 2026-08-01 | Active inline-wait exact resume: authored-position consumed set + remaining clock, ADV/NVL/page-boundary/reflow restore, legacy fallback and hostile-envelope validation | engine capability done; 2,238 passed / 2 environment-skipped in the 2,240-test full suite; BGM/ambient fade-position proof and the full cross-version journey matrix remain open |
| 2026-08-01 | Route-card readability and input proof: larger standard-card secondary type, 4K/density geometry, real touch release, and source-bound semantic keyboard focus | engine capability done; target pack authored copy/contrast and six changed review candidates pass, while target-player approval remains open |
| 2026-08-01 | Fail-closed Production Check v1: exact self-check/approved 32-image visual/100% union coverage/non-empty contract/calibrated runtime gates, clean tracked source digests and saved-artifact verification | engine capability done; its initial checkpoint suites were 2,427 Engine passes / 3 environment-conditional skips and a clean target-pack suite; Demo still lacks a production policy, and the target pack still has 0/32 approved visual baselines, so no production-ready claim |
| 2026-08-01 | Save/load/replay/media reliability: versioned presentation restore, current StoryGraph/map/NPC/asset rebinding, atomic candidate/direct restore, exact PCM voice continuation, replay FX/audio/speaker provenance, CG/Music Room/clear-data authority, hard-cut outgoing audio and decoder/plugin containment | engine capability done; packaged cross-version, real-codec/device, soak and external review gates remain open, so no production-ready claim |
| 2026-08-02 | Guided-map decision hierarchy, player-facing Scene Replay metadata and recoverable Save deletion: story-only mode hides ordinary/off-campus cards without reserving ghost rails; replay copy rejects internal ids/paths; Save rows expose a deliberate Delete sub-action with Cancel-first confirmation and JSON commit-point recovery | engine capability done; map/replay/save focused matrix 134 passed and full engine regression 2,838 passed / 3 skipped; physical-device UX and human-approved visual baselines remain open |
| 2026-08-02 | First-hour flow continuity: terminal-whitespace pages no longer create blank ADV pages; exact multi-phase future CTA retains per-phase interruption; finite guided progress can auto-return idle closures to MapScene; six-action quick rail is compact with semantic labels/tooltips and 44 px targets | engine capability done; combined focused engine matrix 177 passed and target-pack complete GUI route proof passed; full regression, physical-device UX and human-approved visual baselines remain open |
| 2026-08-01 | Visual Accessibility Policy v1: strict persisted live standard/high-contrast authority; profile-aware chrome cache and safe fallback; stock Panel/Button/Settings and ADV/NVL coverage; inline-color suppression; tint/flash isolation; 16-combination resolution/text/reduced-motion matrix | engine capability done; complete suite is 2,523 passed / 3 environment-conditional skipped, while color-vision-deficiency support, supported-device disabled-player sessions and external art/UX approval remain open, so no commercial-quality claim |
| 2026-08-01 | Runtime localization v1: versioned digest-bound source/target catalogs, complete/preview scene scope, presentation-only story/UI lookup, stable line ids, per-pack preferences, title-safe GUI switch, locale font sources, headless/NDJSON boundary and web staging | engine capability done; raw UI-key completion, legacy/LLM relocalization, per-glyph fallback, expansion tests, complete translations and human linguistic QA remain open |
| 2026-08-02 | Settings information architecture P1: four fixed responsive pages, accessibility-first 1/2/3-operation entry, stable per-page focus/scroll, stacked narrow audio rows, voiced-character filtering, live/persisted fullscreen, strict preference normalization and Menu-context restoration | engine capability done; human-approved visual baselines, real-device input/fullscreen and disabled-player review remain open |
| — | Phase 5A native rigs: Live2D / Spine as a desktop-only plugin | open (Next PR #2) |
| — | Phase 3: LLM NPC, autonomous spec-to-pack, cross-pack plugins | deferred |
