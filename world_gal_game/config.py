"""Global engine configuration.

Holds screen size, FPS target, default colors, and path resolution that
works both in development (running from source) and inside a PyInstaller
bundle (where data lives next to the executable under sys._MEIPASS).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


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


def writable_root() -> Path:
    """A writable location for save files / user-local data.

    Inside a frozen exe we can't necessarily write next to the binary, so
    fall back to the user's home directory.
    """
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support" / "WorldGalGame"
        elif sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", Path.home())) / "WorldGalGame"
        else:
            base = Path.home() / ".local" / "share" / "WorldGalGame"
    else:
        base = Path(__file__).resolve().parent.parent
    base.mkdir(parents=True, exist_ok=True)
    return base


def resolve_asset(path: str | Path | None) -> Path | None:
    """Resolve an asset path (relative -> absolute against resource_root)."""
    if path is None:
        return None
    p = Path(path)
    if p.is_absolute():
        return p
    return (resource_root() / p).resolve()


# ---------- runtime config --------------------------------------------------


@dataclass
class EngineConfig:
    # Engine framework default — every pack should override this in
    # meta.yaml. If a pack forgets, the player will at least see "World
    # Gal-Game" and not a game-specific title leaking through.
    title: str = "World Gal-Game"
    subtitle: str = ""
    screen_size: tuple[int, int] = (1280, 720)
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
    default_pack: str = "tsinghua_strange_tales"
    save_subdir: str = "saves"
    # Extra directories to scan when resolving a pack name. The engine
    # always also checks the in-repo games/ directory; this list lets a
    # user pull packs from sibling directories or from a personal pack
    # cache. Each entry is interpreted relative to resource_root() if not
    # absolute.
    extra_pack_dirs: tuple[str | Path, ...] = (
        "..",                         # sibling directories
        "~/.world-gal-game/packs",    # user-wide pack cache
    )

    # text speed (chars/sec); 0 = instant
    text_speed: float = 45.0

    # dev-mode toggles — populated by from_env(); all off by default
    dev_mode: bool = False
    debug_overlay_enabled: bool = False
    hot_reload_enabled: bool = False

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
        d = writable_root() / self.save_subdir
        d.mkdir(parents=True, exist_ok=True)
        return d

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
    # Title-cased variants: "tsinghua_strange_tales" -> "Tsinghua-Strange-Tales"
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
