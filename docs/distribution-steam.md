# Shipping on Steam (desktop)

Steam is a **desktop integration**, not a separate build target: you build a
normal desktop binary (`--target current`, see
[distribution.md](distribution.md)) and the engine *optionally* talks to a
running Steam client for achievements and (via Steam's own config) cloud
saves.

Everything here degrades gracefully. With no Steam present — itch.io
downloads, dev machines, CI, the web — the game runs **byte-identically**.
There is no PyPI dependency: the bridge is a small `ctypes` wrapper over the
official `steam_api` redistributable, which you supply from your Steam depot.

---

## Components

| Piece | Role |
|-------|------|
| `world_gal_game/integrations/steam_bridge.py` | `ctypes` wrapper. `SteamBridge.try_init(app_id, mapping)` returns `None` on any failure. Methods: `unlock`, `run_callbacks`, `shutdown`. |
| `world_gal_game/integrations/steam_plugin.py` | A `@hook(EFFECT_AFTER_APPLY)` handler that diffs `state.achievements.unlocked` onto the bridge. Inert without a bridge. |
| `world_gal_game/app.py` | Constructs the bridge only when enabled, pumps `run_callbacks()` each frame, pre-seeds already-unlocked achievements, and shuts down on exit. |

---

## Enabling it

Steam is **off by default.** Turn it on per-pack in `meta.yaml`:

```yaml
steam:
  enabled: true
  app_id: 480           # your depot app id (480 = Spacewar, for testing)
  # Optional: engine achievement id -> Steam API name. Defaults to 1:1.
  achievements:
    ach_heroine_1_lover: LOVER_ENDING
    ach_first_meeting:   FIRST_MEETING
```

Or per-run via the `WGG_STEAM` env var (its value is used as the app id):

```bash
WGG_STEAM=480 ./WorldGalGameDemo
```

If neither is set, no bridge is constructed and the integration is fully
dormant.

---

## The `steam_api` library

The native redistributable ships in the Steamworks SDK and **is never
vendored into this repo** (it's in `.gitignore` via the `*.dll`/`*.so`/
`*.dylib` rules). Place the right file next to the binary (or the CWD), or
point at it with `WGG_STEAM_LIB`:

| OS | File |
|----|------|
| Windows | `steam_api64.dll` |
| Linux | `libsteam_api.so` |
| macOS | `libsteam_api.dylib` |

```bash
WGG_STEAM_LIB=/path/to/libsteam_api.so WGG_STEAM=480 ./WorldGalGameDemo
```

`SteamBridge.try_init` returns `None` (→ no Steam) when the library can't be
loaded, when `SteamAPI_Init` returns false (Steam not running), or when
`steam_appid.txt` is absent during development.

> The exact ctypes signatures (the `ISteamUserStats` interface-version
> string, in particular) are SDK-version sensitive and best-effort. They all
> live in `steam_bridge.py`; if a Steamworks SDK bump changes the ABI, that
> is the single file to revise.

---

## Local development with `steam_appid.txt`

During development, drop a `steam_appid.txt` containing just your app id next
to where you launch from so `SteamAPI_Init` succeeds without a published
build:

```bash
echo 480 > steam_appid.txt    # 480 = Spacewar
WGG_STEAM=480 uv run python main.py
```

`steam_appid.txt` is in `.gitignore` — never commit it.

End-to-end smoke without a real app: launch against Spacewar (app id 480)
with the Steam client running; the Steam overlay (Shift+Tab) appearing
confirms `SteamAPI_Init` worked.

---

## Achievements

`Achievement.id` maps 1:1 to the Steam API name by default; override per-id
with the `steam.achievements` table above. The flow is automatic:

1. An effect unlocks an achievement (engine side, as always).
2. The `EFFECT_AFTER_APPLY` hook diffs `unlocked` against what's already been
   pushed and calls `SetAchievement` for the new ones (repeats are no-ops).
3. `run_callbacks()` (once per frame) flushes `StoreStats` when dirty.
4. At startup, already-unlocked achievements (e.g. from a loaded save) are
   pre-seeded to Steam.

---

## Cloud saves: use Steam Auto-Cloud (zero code)

Do **not** wire the Steam Remote Storage API — it would duplicate the
engine's clean on-disk save system. Instead use **Steam Auto-Cloud**,
configured entirely on the Steamworks partner site:

1. In the partner site, enable Auto-Cloud and add a root + path pattern that
   matches the engine's save directory.
2. The save path is pinned by `EngineConfig.app_data_name` (the per-platform
   user-data folder, `writable_root(app_data_name)/saves`). Set
   `app_data_name` to a stable, brandable value for your title so the
   partner-site path and the runtime path match exactly.

| OS | Save root (with `app_data_name = "MyGame"`) |
|----|---------------------------------------------|
| Windows | `%APPDATA%\MyGame\saves\` |
| macOS | `~/Library/Application Support/MyGame/saves/` |
| Linux | `~/.local/share/MyGame/saves/` |

No code change is needed for cloud saves — only the partner-site path config
plus a stable `app_data_name`.

---

## CI / SteamPipe

`.github/workflows/release.yml` builds the desktop binaries on a native
matrix. A commented-out `steam` job stub shows where a SteamPipe upload
(e.g. `game-ci/steam-deploy`) would attach — uncomment and supply the
`STEAM_USERNAME` / `STEAM_CONFIG_VDF` secrets and your app/depot ids.
