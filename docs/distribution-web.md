# Distributing to the Web (pygbag / WASM)

World Gal-Game can run entirely in the browser via
[pygbag](https://pypi.org/project/pygbag/), which compiles CPython +
pygame-ce to WebAssembly. No server, no plugin, no install — players open a
URL.

This is an **optional** capability. pygbag lives in the `[web]` extra and is
never imported by the running engine; it is only shelled out to by the build
step.

---

## How it fits together

| Piece | Role |
|-------|------|
| `world_gal_game/web_main.py` | Async entry. `asyncio.run(main())` builds the app and `await`s `app.run_async()` (yields to the browser each frame). A template — the build rewrites its `_PACK` constant. |
| `world_gal_game/app.py` | `run_async()` + a fixed 1280×720 logical canvas scaled onto a resizable window, so it works under both desktop and the browser. |
| `world_gal_game/platform_web.py` | `is_web()` + `flush_storage()`. The **single** place that knows pygbag's IDBFS→IndexedDB sync API. |
| `world_gal_game/build_web.py` | Stages the engine + pack into a build tree and runs `python -m pygbag --build`. |

Saves are written under `/data/<app_data_name>` (an IDBFS mount). After each
`save()`/`delete()` the engine calls `flush_storage()` to push that mount
down to the browser's IndexedDB, so saves survive a hard reload. Off-web
`flush_storage()` is an immediate no-op.

---

## Prerequisites

```bash
uv sync --extra web        # installs pygbag (pinned >= 0.9.2)
# or: uv pip install -e ".[web]"
```

Confirm pygbag is available:

```bash
uv run python -m pygbag --version
```

---

## Building

```bash
# Produce a one-shot WASM bundle under build/web/.
uv run python build.py games/demo_pack --target web

# Or via the console script:
wgg build games/demo_pack --target web
```

The builder stages a self-contained tree (engine + the pack's `content/` and
`assets/`, mirroring the source layout so the engine's path math resolves the
pack) and writes a templated `main.py` at its root, then invokes pygbag.

---

## Local verification (manual)

The fastest loop is to run pygbag directly against the in-repo web entry:

```bash
uv run python -m pygbag world_gal_game/web_main.py
# then open http://localhost:8000
```

Or use the build's serve mode (stages the pack, then serves):

```bash
wgg build games/demo_pack --target web --serve
# then open http://localhost:8000
```

Verification checklist in the browser:

1. **Title CJK renders** (not tofu boxes). If you see boxes, the pack is
   missing a `bundled_font` — run the web gate (below).
2. Click **New Game**; tap/click advances dialogue.
3. **Save → hard-reload the tab → the save is still there.** This proves the
   IDBFS→IndexedDB flush works.
4. Audio plays after the first click (browsers block autoplay until a user
   gesture — the title click satisfies this).
5. The browser console shows no thread/blocking errors.

---

## Pre-flight: the web validator gate

The browser has no system fonts and patchy `.mp3`/`.wav` support. An opt-in
gate flags both before you ship:

```python
from pathlib import Path
from world_gal_game.validator import validate_for_web

for issue in validate_for_web(Path("games/demo_pack")):
    print(issue.severity, issue.message)
```

- **No `bundled_font` in `meta.yaml` → ERROR** (CJK would render as tofu).
  Ship a (subset) CJK font and reference it: `bundled_font: assets/fonts/YourFont.ttf`.
- **Any `.mp3` / `.wav` asset → WARNING.** Convert audio to OGG/Vorbis for
  reliable, compact playback.

The normal `validate_pack` does **not** apply these rules, so a desktop pack
isn't penalised; only the web build calls `validate_for_web`.

---

## Known web limitations

- **No threads.** LLM-backed NPC brains (the `anthropic` path) don't run in
  the browser; the engine's default `EchoBrain` is used. This is automatic.
- **Audio is gated behind a first user gesture.** The title screen click
  unlocks it.
- **First load is large.** CPython-WASM + pygame-ce + a CJK font + assets can
  reach tens of MB. Mitigate with a subset font and compressed assets.
- The exact pygbag FS-sync call is **version-sensitive**. We pin a lower
  bound in the `[web]` extra and keep all of that knowledge in
  `platform_web.flush_storage()` — if a future pygbag renames the sync API,
  fix it there and nowhere else.
