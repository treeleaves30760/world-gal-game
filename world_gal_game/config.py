"""Global engine configuration.

Holds screen size, FPS target, default colors, and path resolution that
works both in development (running from source) and inside a PyInstaller
bundle (where data lives next to the executable under sys._MEIPASS).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)

# User-tunable settings that round-trip through settings.json. Only these
# fields are serialized; pack/path/dev fields are intentionally excluded so
# the on-disk file stays a pure user-preferences document.
_PERSISTED_SETTING_FIELDS: tuple[str, ...] = (
    "bgm_volume",
    "sfx_volume",
    "voice_volume",
    "text_speed",
    "auto_play_delay",
    "touch_mode",
    "auto_play_speed",
    "auto_play_wait_voice",
    "skip_unread_only",
    "nvl_mode",
    "rollback_enabled",
    "show_status_hud",
    "show_affection_feedback",
    "dim_inactive_speakers",
    "auto_emote_on_emotion",
    "reduce_motion",
    "text_scale",
    "typewriter_sound",
    "ui_sound_enabled",
    "per_character_voice_volume",
    "autosave_enabled",
    "autosave_slot_count",
    "quicksave_slot",
    "seen_intro",
)


# ---------- path resolution (dev + pyinstaller) -----------------------------


def resource_root() -> Path:
    """Return the directory where bundled resources live.

    - In development: the project root (the directory containing
      'engine/', 'games/', 'main.py').
    - In a PyInstaller one-file bundle: sys._MEIPASS.
    - In a PyInstaller one-dir bundle: directory of the executable.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def writable_root(app_name: str = "WorldGalGame") -> Path:
    """A writable location for save files / user-local data.

    - On the web (Emscripten/pygbag) saves must live under the IDBFS mount
      that gets persisted to IndexedDB — ``/data/<app_name>``.
    - Inside a frozen exe we can't necessarily write next to the binary, so
      fall back to the per-platform user data directory.
    - In development, write next to the project.

    ``app_name`` lets a shipping title pin its folder (e.g. so Steam
    Auto-Cloud's configured path matches the runtime path exactly).
    """
    if sys.platform == "emscripten":
        # pygbag mounts /data as IDBFS; flushed to IndexedDB on save.
        base = Path("/data") / app_name
    elif getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support" / app_name
        elif sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", Path.home())) / app_name
        else:
            base = Path.home() / ".local" / "share" / app_name
    else:
        base = Path(__file__).resolve().parent.parent
    base.mkdir(parents=True, exist_ok=True)
    return base


def _sanitize_pack_id(pack_id: str | None) -> str:
    """Reduce a pack id to a single safe directory component for save namespacing.

    A pack id is usually a plain slug (``demo_pack``), but it may arrive as a
    path-like ``default_pack`` (e.g. ``../Tsing-Hua-Strange-Tales``). We keep
    only the final component and strip anything that isn't filesystem-friendly,
    so the save namespace can never escape the saves/ root or collide via
    separators. An empty / all-stripped id yields ``""`` (flat layout).
    """
    if not pack_id:
        return ""
    # Take the last path component, normalizing both separators.
    name = str(pack_id).replace("\\", "/").rstrip("/").split("/")[-1]
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name)
    safe = safe.strip("._")
    return safe


def resolve_asset(path: str | Path | None) -> Path | None:
    """Resolve an asset path (relative -> absolute against resource_root)."""
    if path is None:
        return None
    p = Path(path)
    if p.is_absolute():
        return p
    return (resource_root() / p).resolve()


# ---------- runtime config --------------------------------------------------


# Minimum touch-target edge length, in logical canvas pixels, recommended for
# tappable widgets when ``EngineConfig.touch_mode`` is on. ~44px is the floor
# both Apple's HIG and Google's Material guidelines cite for finger targets;
# at the engine's 1280x720 logical canvas that is comfortably tappable on a
# phone-sized viewport. Widgets MUST only consult this when ``touch_mode`` is
# True so desktop (mouse) layouts stay byte-identical. Hit-target enlargement
# itself is deferred to a follow-up; this constant documents the intended
# minimum so the flag and the number live together.
MIN_TOUCH_TARGET_PX: int = 44


@dataclass
class EngineConfig:
    # Engine framework default — every pack should override this in
    # meta.yaml. If a pack forgets, the player will at least see "World
    # Gal-Game" and not a game-specific title leaking through.
    title: str = "World Gal-Game"
    subtitle: str = ""
    # Folder name under the OS user-data dir (and the web IDBFS mount). A
    # shipping title overrides this so saves land in a stable, brandable
    # location that Steam Auto-Cloud can be pointed at.
    app_data_name: str = "WorldGalGame"
    # Logical canvas the whole UI is drawn at, then letterbox-scaled to the
    # window. 1920x1080 (16:9) renders crisp on HiDPI / large displays;
    # layouts are screen-size-relative so they adapt. On a laptop smaller
    # than this the window opens letterboxed/shrunk by the OS. Override
    # per-run with --width/--height or per-pack in meta.yaml.
    screen_size: tuple[int, int] = (1920, 1080)
    fps: int = 60
    vsync: bool = True
    fullscreen: bool = False

    # default font search list (will be probed in order; first found wins)
    font_candidates: tuple[str, ...] = (
        "PingFang TC",
        "Heiti TC",
        "Microsoft JhengHei",
        "Noto Sans CJK TC",
        "Noto Sans TC",
        "Hiragino Sans GB",
        "Source Han Sans TC",
        "Arial Unicode MS",
    )
    bundled_font: str | None = None    # optional path to a bundled .ttf/.otf
    font_size_dialogue: int = 24
    font_size_speaker: int = 28
    font_size_header: int = 40
    font_size_menu: int = 26
    font_size_small: int = 18

    # paths (relative to resource_root); content/asset roots per game pack
    game_pack_dir: Path = field(default_factory=lambda: Path("games"))
    default_pack: str = "demo_pack"
    save_subdir: str = "saves"
    # Identity of the pack currently being driven (``meta.id`` or the pack
    # directory name). Set by the app once the pack is loaded; used to give each
    # pack its own save namespace so foreign saves never collide or get rejected
    # (see ``save_dir``). Empty = unnamespaced (legacy flat ``saves/`` layout).
    pack_id: str = ""
    # Extra directories to scan when resolving a pack name. The engine
    # always also checks the in-repo games/ directory; this list lets a
    # user pull packs from sibling directories or from a personal pack
    # cache. Each entry is interpreted relative to resource_root() if not
    # absolute.
    extra_pack_dirs: tuple[str | Path, ...] = (
        "..",                         # sibling directories
        "~/.world-gal-game/packs",    # user-wide pack cache
    )

    # audio volumes (0.0 - 1.0)
    bgm_volume: float = 0.6
    sfx_volume: float = 1.0
    voice_volume: float = 1.0

    # Dim non-speaking portrait slots so the active speaker stands out (the
    # commercial-VN convention). When False, every on-screen portrait draws at
    # full brightness — byte-identical to the historical render path.
    dim_inactive_speakers: bool = True

    # Auto-react: when a character's on-screen expression changes, play a small
    # one-shot emote so portraits respond to emotion without the author writing
    # an emote on every line. Defaults OFF: like 白色相簿2, sprites swap poses /
    # expressions with no procedural geometric motion. Authored emotes still
    # play; players who want the auto "acting" feel can enable 立繪情緒反應.
    auto_emote_on_emotion: bool = False

    # Accessibility: when True, drop motion-sickness / photosensitivity triggers
    # (camera pans/zooms, screen shake, flashes, blur). Atmospheric tints, CG
    # swaps and the gentle scene crossfade are kept. Honoured in
    # DialogueScene._spawn_visual_fx.
    reduce_motion: bool = False

    # Accessibility: dialogue text-size multiplier. Scales the dialogue body +
    # speaker font AND the text box height together (so larger text still fits),
    # honoured in DialogueScene.enter / DialogueBox. 1.0 = standard.
    text_scale: float = 1.0

    # Presentation (pack-driven, set from meta.yaml): when True, the dialogue
    # scene paints a subtle time-of-day base tint on any scene that doesn't set
    # its own screen_tint — giving un-directed scenes (dailies) a lighting
    # signature for the time of day. Directed scenes (with an explicit tint)
    # are untouched, so the two never double up. Honoured in DialogueScene.draw.
    ambient_time_tint: bool = False

    # text speed (chars/sec); 0 = instant
    text_speed: float = 45.0

    # Optional typewriter "blip" sound (a pack asset path, set from meta), played
    # softly while dialogue text reveals. Not a user preference (it's a pack
    # asset), so it is NOT persisted; the toggle below is.
    text_blip: str | None = None
    # User toggle for the typewriter blip (divisive — some players dislike it).
    typewriter_sound: bool = True

    # Optional UI click sound (a pack asset path from meta; NOT persisted) and
    # its persisted user toggle.
    ui_sound: str | None = None
    ui_sound_enabled: bool = True

    # seconds between auto-advances when auto-play mode is on
    auto_play_delay: float = 2.5

    # Auto/skip playback tuning (consumed by the dialogue scene).
    # ``auto_play_speed`` scales ``auto_play_delay`` (higher = faster).
    auto_play_speed: float = 1.0
    # When auto-play is on, wait for the current voice clip to finish
    # before advancing to the next line.
    auto_play_wait_voice: bool = True
    # Skip only advances through already-read lines when True; when False
    # skip races through unread lines too.
    skip_unread_only: bool = True

    # NVL (full-screen accumulating text) presentation mode toggle.
    nvl_mode: bool = False

    # Player-facing rollback (rewind the game state to a previous line within
    # the current scene). Built on the same snapshot/restore machinery the
    # headless agent layer uses for branch exploration. Bound to Backspace.
    rollback_enabled: bool = True

    # Persistent chapter/date HUD: a small, unobtrusive corner indicator shown
    # during dialogue with the current chapter title (from current_chapter ->
    # ChapterManifest) and the in-game date/time (TimeSystem.label). On by
    # default and deliberately subtle; players who want a clean frame turn it
    # off. Honoured in DialogueScene.draw.
    show_status_hud: bool = True

    # Per-choice affection feedback: when True, an affection-affecting choice
    # surfaces a subtle "好感度 +N" toast for the character at the moment it
    # resolves, so the stat that silently decides the route is legible. The
    # named-threshold relationship toast ("「在意你」") always fires regardless;
    # this controls only the lighter per-change "+N" beat. On by default and
    # deliberately brief; players who find a per-choice number too gamey for a
    # narrative VN can turn it off. Honoured in GameState.apply_all.
    show_affection_feedback: bool = True

    # Per-character voice volume overrides keyed by speaker id; speakers
    # absent here fall back to ``voice_volume``. UI lives in the settings
    # scene.
    per_character_voice_volume: dict[str, float] = field(default_factory=dict)

    # Whether the first-run control onboarding has been shown (persisted so a
    # returning player never sees it again).
    seen_intro: bool = False

    # Quicksave / autosave behaviour.
    autosave_enabled: bool = True
    autosave_slot_count: int = 3
    quicksave_slot: str = "quicksave"

    # Touch-friendly UI mode. Off by default so desktop (mouse) input and
    # widget sizing are unchanged. Intended to be auto-enabled on web/mobile
    # in a follow-up; when on, tappable widgets should honour
    # ``MIN_TOUCH_TARGET_PX`` for their hit areas. Any such enlargement MUST
    # be gated behind this flag — with ``touch_mode`` False the engine renders
    # and hit-tests exactly as before.
    touch_mode: bool = False

    # dev-mode toggles — populated by from_env(); all off by default
    dev_mode: bool = False
    debug_overlay_enabled: bool = False
    hot_reload_enabled: bool = False

    # Determinism seed. When set, the engine seeds the per-state RNG
    # (``GameState.rng()``) so plugins/brains that opt into it replay
    # identically: same seed + same script -> same state. Runtime metadata,
    # NOT a user preference, so it is deliberately absent from
    # ``_PERSISTED_SETTING_FIELDS``. ``None`` means "fresh entropy".
    seed: int | None = None

    @classmethod
    def from_env(cls, **overrides) -> "EngineConfig":
        """Construct an EngineConfig, auto-enabling dev tools when WGG_DEV=1."""
        dev = bool(os.environ.get("WGG_DEV"))
        return cls(
            dev_mode=dev,
            debug_overlay_enabled=dev,
            hot_reload_enabled=dev,
            **overrides,
        )

    def save_dir(self) -> Path:
        """Per-pack save directory: ``<writable_root>/saves/<pack_id>``.

        Each pack gets its own namespace so a save from one pack never collides
        with — or is rejected as incompatible by — another. When ``pack_id`` is
        unset (legacy / embedding), it degrades to the historical flat
        ``saves/`` layout, so old saves stay readable where they already live.
        """
        d = writable_root(self.app_data_name) / self.save_subdir
        ns = _sanitize_pack_id(self.pack_id)
        if ns:
            d = d / ns
        d.mkdir(parents=True, exist_ok=True)
        return d

    def settings_path(self) -> Path:
        """Location of the user-preferences JSON document."""
        return writable_root(self.app_data_name) / "settings.json"

    def save_to_disk(self) -> None:
        """Persist only the user-tunable settings to ``settings.json``.

        Pack/path/dev fields are deliberately excluded; the file is a pure
        preferences document so it stays portable across packs.
        """
        data = {name: getattr(self, name) for name in _PERSISTED_SETTING_FIELDS}
        path = self.settings_path()
        path.write_text(json.dumps(data, indent=2, sort_keys=True),
                        encoding="utf-8")

    def load_from_disk(self) -> None:
        """Overwrite known settings fields from ``settings.json`` if present.

        Robust by contract: a missing file is a no-op, a corrupt/unparseable
        file is logged and ignored (defaults are kept), and unknown keys are
        skipped. This never raises.
        """
        path = self.settings_path()
        try:
            if not path.exists():
                return
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _log.warning("Ignoring unreadable settings file %s: %s", path, exc)
            return
        if not isinstance(raw, dict):
            _log.warning("Ignoring settings file %s: expected an object", path)
            return
        for name in _PERSISTED_SETTING_FIELDS:
            if name in raw:
                setattr(self, name, raw[name])

    def pack_root(self, pack: str | None = None) -> Path:
        """Resolve a pack identifier to its on-disk root directory.

        ``pack`` may be:

        - ``None``  → use ``default_pack``.
        - an absolute path → used directly (must have ``content/meta.yaml``).
        - a relative path with a separator → resolved against the current
          working directory (works for ``./``-style invocations).
        - a bare name → searched in:
            1. ``<resource_root>/games/<name>/``
            2. each entry of ``extra_pack_dirs`` joined with the name,
               and a sibling whose directory name title-cased / kebab-cased
               (so ``my_game`` also matches ``My-Game/``).

        Raises ``FileNotFoundError`` if no candidate has a
        ``content/meta.yaml``.
        """
        pack = pack or self.default_pack
        return _resolve_pack(pack, self)

    def pack_content(self, pack: str | None = None) -> Path:
        return self.pack_root(pack) / "content"

    def pack_assets(self, pack: str | None = None) -> Path:
        return self.pack_root(pack) / "assets"


# ---------- pack resolution -------------------------------------------------


def _is_valid_pack_root(p: Path) -> bool:
    return (p / "content" / "meta.yaml").exists()


def _name_variants(name: str) -> list[str]:
    """Generate equivalent forms a directory might be named under."""
    out = {name}
    out.add(name.replace("_", "-"))
    out.add(name.replace("-", "_"))
    # Title-cased variants: "my_demo_pack" -> "My-Demo-Pack"
    for sep_in in ("_", "-"):
        parts = [p.capitalize() for p in name.split(sep_in)]
        for sep_out in ("-", "_"):
            out.add(sep_out.join(parts))
    return list(out)


def _resolve_pack(pack: str, config: "EngineConfig") -> Path:
    p = Path(pack).expanduser()

    # 1) Absolute path.
    if p.is_absolute():
        if _is_valid_pack_root(p):
            return p
        raise FileNotFoundError(f"Pack path missing content/meta.yaml: {p}")

    # 2) Relative path containing a separator → CWD-relative.
    if any(sep in pack for sep in ("/", "\\")) or pack.startswith("."):
        cand = (Path.cwd() / p).resolve()
        if _is_valid_pack_root(cand):
            return cand
        # Fall through to name-based lookup if not found.

    # 3) Bare name: search known locations.
    search_roots: list[Path] = [resource_root() / config.game_pack_dir]
    for entry in config.extra_pack_dirs:
        e = Path(entry).expanduser()
        if not e.is_absolute():
            e = (resource_root() / e).resolve()
        search_roots.append(e)

    for root in search_roots:
        if not root.exists():
            continue
        for variant in _name_variants(pack):
            cand = root / variant
            if _is_valid_pack_root(cand):
                return cand.resolve()

    raise FileNotFoundError(
        f"Pack not found: {pack!r}. Tried name variants "
        f"{_name_variants(pack)} in: {[str(r) for r in search_roots]}"
    )
