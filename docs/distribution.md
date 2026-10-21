# Distributing a Game Pack

This guide explains how to package a World Gal-Game pack into a
standalone binary that players can run without installing Python.

---

## What "distribute" means

The build command uses PyInstaller to collect:

- the World Gal-Game engine (Python code + pygame-ce)
- the Python runtime and all transitive dependencies
- the entire contents of your pack directory (YAML + assets)

The result is a single folder that a player can copy anywhere and run
directly.  No Python installation is required on their machine.

---

## Prerequisites

Install the `[build]` extras into your development environment:

```bash
uv pip install -e ".[build]"
```

This installs PyInstaller alongside the engine.  Confirm with:

```bash
pyinstaller --version
```

---

## Simplest possible build

```bash
world-gal-game build /path/to/my-pack
```

Or, if you prefer the direct Python wrapper at the repo root:

```bash
python build.py /path/to/my-pack
```

The engine derives the app name from the `title` field in your pack's
`content/meta.yaml`.  A pack with `title: "小鎮的午後"` produces an app
name of `_` (ASCII-safe characters are kept; non-ASCII are replaced by
`_`).  Override it explicitly with `--name`:

```bash
world-gal-game build /path/to/my-pack --name SmallTownAfternoon
```

---

## Output location

The finished build lands in `dist/<app_name>/` relative to your current
working directory.  Change the destination with `--output`:

```bash
world-gal-game build /path/to/my-pack --output /tmp/releases
# result: /tmp/releases/<app_name>/
```

On macOS the app bundle is `dist/<app_name>/<app_name>.app`.
On Windows the executable is `dist/<app_name>/<app_name>.exe`.
On Linux it is `dist/<app_name>/<app_name>`.

---

## Cross-platform limitations

PyInstaller **cannot cross-compile**.  You must build on the target OS:

| Want to produce | Must build on |
|-----------------|---------------|
| `.exe`          | Windows       |
| `.app`          | macOS         |
| Linux binary    | Linux         |

There is therefore **no per-OS `--target` value to pass**: always use
`--target current` (the default) and run the build on a native runner for
each OS. Passing `--target windows` (or `macos`, `linux`) when you are on a
different OS only prints a warning and falls back to a native build anyway —
it does not produce a foreign binary. Use a CI matrix (see below) to build
all three OSes from one repo.

## Target overview

| `--target` | Builder | Output |
|------------|---------|--------|
| `current` (default) | PyInstaller | native desktop binary for the host OS |
| `web` | pygbag (WASM) | browser bundle — see [distribution-web.md](distribution-web.md) |
| `android-apk` | pygbag (APK) | Android WebView APK wrapping the web bundle — see [distribution-mobile.md](distribution-mobile.md) |

Steam is a desktop *integration* layered on top of a `current` build, not a
separate target — see [distribution-steam.md](distribution-steam.md).

Mobile (Android APK + PWA, iOS PWA) all builds on the web target — see
[distribution-mobile.md](distribution-mobile.md). Note that iOS is PWA-only
(no App Store) and native buildozer SDL2 is out of scope.

---

## Adding an icon

```bash
# Windows
world-gal-game build /path/to/my-pack --icon icon.ico

# macOS
world-gal-game build /path/to/my-pack --icon icon.icns
```

PyInstaller accepts `.ico` on Windows and `.icns` on macOS.  Passing the
wrong format for the host OS is silently ignored by PyInstaller.

---

## Single-file mode

Produce a single executable instead of a folder:

```bash
world-gal-game build /path/to/my-pack --onefile
```

Single-file builds are convenient for distribution but start more slowly
because the executable unpacks itself to a temp directory on every launch.
For games with large asset trees the folder mode is preferred.

---

## Assets and YAML paths

All paths in your pack's YAML files should be **relative to the pack
root** (e.g. `assets/backgrounds/title.png`).  The build system copies
the entire pack directory into the binary, preserving this layout, so the
engine resolves them identically at runtime.

Do not use absolute paths or paths that walk above the pack root
(`../../something`).

## Asset and memory budgets

Release size and runtime memory are different budgets. A compressed PNG or OGG
may be small on disk but much larger after decoding, so both must be measured
on a representative full pack.

- Author delivery assets at their largest real presentation size. Do not ship
  1536 px inventory icons if the UI only presents them near 128–160 px.
- Run a duplicate-content audit before every release. Redirect authored
  references to one canonical asset before removing byte-identical copies;
  filename similarity alone is not sufficient evidence.
- Treat lossless WebP conversion as a disk-size choice, not a decoded-memory
  fix. Validate target loaders and visual baselines before changing formats.
- The engine's reusable image LRU defaults to 256 MiB; the short-sound LRU
  defaults to 64 MiB. BGM is isolated to the two crossfade slots so a long
  walkthrough cannot retain every visited track.
- `AssetManager.cache_stats()` exposes cache and live-audio estimates for soak
  tests. `runtime-check` consumes that telemetry and records fresh-process
  startup, current/peak/growth RSS and deterministic title/new-game frame
  samples. Packaged artifact size and representative real-scene/transition
  evidence must still be recorded separately on each supported target.

Engine integrators can tune the process-level caps with
`EngineConfig.max_image_cache_mb` and `EngineConfig.max_sound_cache_mb`. They
are deliberately not player-facing persisted settings. Setting either cap to
zero disables retention for that cache; it does not prevent an asset from
being loaded and presented once.

### Local Runtime Reliability Gate v1

Run the independent release gate from a clean process environment before
building a distributable:

```bash
uv sync --extra runtime
world-gal-game runtime-check /path/to/my-pack \
    --policy /path/to/my-pack/content/runtime_reliability.yaml \
    --report artifacts/runtime-reliability/my-pack.json
```

`runtime-check` is intentionally not a `self-check` stage. `self-check` remains
the fast, deterministic authoring loop; this gate starts multiple application
processes and measures the current host. The command returns non-zero when a
blocking check fails and still attempts an atomic JSON report for pass, fail,
timeout, malformed-policy, and controller-error outcomes. CI should therefore
upload the report with `if: always()`.

The policy path is explicit above. If `--policy` is omitted, the CLI looks for
`content/runtime_reliability.yaml` under the selected pack. Policy schema v1 is
strict: missing required fields and unknown fields are errors. The canonical
portable example is
[`games/demo_pack/content/runtime_reliability.yaml`](../games/demo_pack/content/runtime_reliability.yaml).
It defines:

| Section | Contract |
|---|---|
| `profile` | screen size, seed, fixed delta, warm-up/measured frames, ordered semantic views, fresh-process run counts, RSS sampling interval and per-process timeout |
| invariant `limits` | finite telemetry, RSS availability, cross-process semantic determinism, configured cache bounds, plugin errors, pack UI chrome and live BGM slots |
| numeric `limits` | app-ready/first-frame p95, current/peak/growth RSS, frame p95/p99 and count over a frame budget; each may be `null` for observation-only calibration |
| `calibrated_fingerprint` | exact runtime fields required whenever any numeric limit is set; every field must match before that limit is enforced |

The JSON report identifies schema/status and claim scope; engine and pack git
revisions; controller environment; the full policy and its SHA-256; per-run
status, milestone/protocol output and bounded stdout/stderr evidence;
fresh-process app-ready and first-frame samples; current/peak/growth RSS;
per-frame percentiles; cache, retained-audio, BGM, plugin and UI-chrome data;
semantic checkpoint determinism; individual checks/failures; and a canonical
content digest. Keep the whole report, not only the console summary.

The v1 controller forces dummy SDL video and audio so it is reproducible on a
headless source checkout. Every process also receives a unique writable root
and an empty redirected per-user plugin root; engine-bundled and selected-pack
plugins still load, while unrelated local plugins cannot contaminate evidence.
Those numbers are calibration evidence only. They do
not measure time-to-visible, GPU/VRAM or compositor work, real audio, packaged
binary extraction/launch, or player hardware. A shipping pack still needs
owner-approved numeric budgets captured with a matching fingerprint on every
representative target device, plus launch evidence from the packaged artifact.
The separately installed `runtime` extra supplies `psutil`. After App shutdown,
the real worker emits `cleanup_ready` and stays alive; the controller takes a
recursive descendant snapshot, cleans it, writes an acknowledgement, and only
then allows the worker to emit `cleanup_complete` and exit. This closes the
ordinary polling race even for a direct child that opened a new session. A
POSIX process group or Linux `/proc` alone cannot provide that evidence, so
without `psutil`, `runtime-check` fails closed. This is lifecycle evidence for
the cooperative probe, not a security sandbox against malicious native code.

---

### Fail-closed Production Check v1

`production-check` is the aggregate source-release preflight. Unlike the fast
authoring commands, it has no skip, baseline-bootstrap, warning-acceptance,
dirty-tree or stale-evidence override. Run it from clean engine and pack Git
worktrees after the pack has committed its policy, scenarios, reviewed
baselines, approval manifest, narrative contract, scripts and required assets:

```bash
world-gal-game production-check /path/to/my-pack \
    --policy /path/to/my-pack/content/production.yaml \
    --report /tmp/release-evidence/my-pack-production.json

world-gal-game production-verify \
    /tmp/release-evidence/my-pack-production.json \
    --pack /path/to/my-pack
```

The report path must be outside both the selected pack and engine Git
worktrees. The command attempts one atomic JSON report for ordinary gate and policy
failures, returns non-zero on failure, and never creates or promotes a visual
baseline. `production-verify` rechecks the saved artifact's strict JSON shape,
self-digest, semantics, freshness, source revisions and current policy,
scenario, approval and baseline bindings; recomputing only the outer digest
cannot turn a semantically incomplete report green.

The authoring command `visual-check` writes its generated PNGs under
`<pack>/visual_candidates/visual_check/`; it does not reuse the root of
`visual_candidates`, where a pack may keep curated, source-bound review
candidates with the same generic names. Explicit `VisualCheck(candidates_dir=)`
callers keep exact-path semantics. `production-check` supplies an isolated
temporary candidate directory and deletes it after comparison, so it never
modifies either candidate area in the pack.

The strict `content/production.yaml` schema is:

```yaml
schema_version: 1
name: my-pack-source-release
claim_scope: automated-source-release-preflight
max_evidence_age_seconds: 86400
visual:
  scenarios_path: content/production_visual_scenarios.json
  approvals_path: visual_baselines/approved.yaml
runtime:
  policy_path: content/runtime_reliability.yaml
  require_numeric_limits: true
coverage:
  script_glob: scripts/test_*.json
  seed: 42
  minimum_percent:
    scenes: 100.0
    lines: 100.0
    choices: 100.0
    endings: 100.0
contract:
  path: contracts.yaml
  seed: 42
required_asset_roots:
  - assets
```

The production visual scenario file is a strict JSON list. It must contain the
17 canonical semantic views at both 1280×720 and 1920×1080: title, a real fresh
New Game onboarding stack, dialogue, choice, exploration, story-priority map,
all-reachable map, travel confirmation, menu, populated history, save, load,
settings, CG gallery, Music Room, evidence-backed scene replay and flowchart.
This is an exact 34-scenario minimum; extra scenarios may supplement it. Each committed
scenario may also pin a declared runtime language with a path-safe BCP-47
`locale` value, allowing every shipping locale to receive independently
reviewed layout baselines. A development-only `text_expansion_factor` from
1.0 through 2.0 applies deterministic pseudo expansion after canonical
localization, without changing state, saves, catalog coverage or preferences.
Each committed
`visual_baselines/<scenario-name>.png` must have an `approved` entry in
`visual_baselines/approved.yaml` that binds the exact scenario SHA-256,
baseline SHA-256, scenario-set digest, engine-source digest and pack-runtime
digest, with a reviewer, non-empty note and timezone-aware approval time.
`review_candidate` and `rejected` are never passing statuses. The manifest is
auditable review metadata, not cryptographic proof of the reviewer's identity.

A passing report requires exactly these five aggregate gates:

1. `self_check`: all seven stages run and pass; nothing is skipped, schema and
   reference warnings are empty, every non-exempt ending is verified, and at
   least one smoke script runs.
2. `visual_approval`: the 34 required candidates reproduce the exact approved
   PNG bytes and dimensions in an isolated temporary candidate directory.
3. `coverage`: the deterministic union of every `scripts/test_*.json` reaches
   100% of scenes, lines, choices and endings.
4. `contract`: the pinned narrative-contract document is non-empty and every
   expectation passes with the declared seed.
5. `runtime_check`: every invariant is enabled, all nine numeric limits are
   non-null, the complete calibrated fingerprint matches, all eight numeric
   checks pass, and the fresh-process run/log evidence is complete.

The controller also requires clean Git subjects, rejects untracked production
inputs, records canonical engine/pack content manifests, and fails if either
input tree changes during the run. A repository-local top-level `.venv/` is
excluded as regenerable tooling state, including its expected interpreter and
console-script symlinks. This exception does not apply to included product
paths: any symlink there still fails the source snapshot. The aggregate artifact
is self-digested and expires according to `max_evidence_age_seconds`.

This claim is deliberately only `automated-source-release-preflight`. It does
not cover packaged-binary launch, target GPU/VRAM or device performance, real
audio and physical controllers, accessibility sessions, localization/font
review, legal and asset-license clearance, store submission, or independent
reviewer identity. Those remain separate release evidence. The bundled demo
does not yet provide a production policy or human-approved 32-image baseline
set, and its runtime policy intentionally has observation-only (`null`) numeric
limits. It is a calibration example and must not pass this production gate.

---

## Save-file location

Player saves are **not** stored inside the binary.  The engine writes
them to the user's application-data directory:

| OS      | Path |
|---------|------|
| macOS   | `~/Library/Application Support/<app_name>/` |
| Windows | `%APPDATA%\<app_name>\` |
| Linux   | `~/.local/share/<app_name>/` |

Pack developers do not need to configure anything; the engine handles
this automatically.

---

## macOS code signing

Pass your Developer ID to sign the output after building:

```bash
world-gal-game build /path/to/my-pack \
    --sign-identity "Developer ID Application: My Company (XXXXXXXXXX)"
```

This runs `codesign --deep --force --sign` on the produced bundle.
Notarisation (required for distribution outside the Mac App Store) is a
separate step and must be done with `xcrun notarytool` — outside the
scope of this command.

---

## Windows code signing

Windows code signing uses Microsoft's `signtool.exe`, which is not
bundled with PyInstaller or this engine.  After building, sign with:

```bat
signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 ^
    /f MyCert.pfx /p MyPassword dist\MyGame\MyGame.exe
```

Refer to Microsoft's documentation for certificate acquisition and
`signtool` usage.

---

## Troubleshooting

**Missing fonts at runtime**

Add font files to `assets/fonts/` inside your pack directory and
reference them by pack-relative path in your YAML.  PyInstaller will
include them automatically.

**Missing images or audio**

Verify that every asset referenced in YAML lives inside the pack
directory.  Files outside the pack root are not bundled.

**The .exe closes immediately on Windows**

Run the binary from a command prompt (`cmd.exe`) so you can see the
error output.  Common causes: a missing DLL (usually resolved by
installing the Visual C++ Redistributable) or a YAML parse error.

**`pyinstaller` is not found**

Ensure you activated the correct virtual environment and ran
`uv pip install -e ".[build]"`.

---

## GitHub Actions CI example

The matrix below builds a binary for each platform on every tagged push.
Note that each runner builds with the **default** target (`current`) — there
is no per-OS `target` matrix value, because PyInstaller builds for whatever
OS it runs on. (The repo ships a complete, ready-to-use version of this at
`.github/workflows/release.yml`, including a web job and a release-attach
job.)

```yaml
name: Release

on:
  push:
    tags: ["v*"]

jobs:
  build:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]

    runs-on: ${{ matrix.os }}

    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v5

      - name: Install dependencies
        run: uv sync --extra build

      - name: Build pack (native target only — no cross-compile)
        run: uv run python build.py games/my-pack --name MyGame --target current --output dist

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: MyGame-${{ runner.os }}
          path: dist/MyGame/
```

Adjust `games/my-pack` and `MyGame` to match your pack directory and
desired app name.  The three artifacts can then be downloaded from the
Actions run and attached to a GitHub Release.

For the **web** build add a separate job that runs
`uv sync --extra web` then `python build.py games/my-pack --target web`;
see [distribution-web.md](distribution-web.md).
