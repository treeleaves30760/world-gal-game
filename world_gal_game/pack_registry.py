"""Discover game packs available on disk.

A "game pack" is any directory containing ``content/meta.yaml``. The
registry checks several locations:

1. ``<engine_root>/games/``  — packs that ship with the engine
2. ``<engine_root>/..``      — sibling directories (so a game can live
                                next to the engine, not inside it)
3. ``~/.world-gal-game/packs/`` — user-wide pack cache

Any directory passed via ``EngineConfig.extra_pack_dirs`` is appended.
Each call rescans from disk; nothing here is cached.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from .config import EngineConfig, resource_root


@dataclass
class PackInfo:
    name: str               # directory name (URL-safe slug)
    root: Path              # absolute path
    title: str              # display title from meta.yaml
    subtitle: str = ""
    start_location: str | None = None
    intro_scene: str | None = None
    has_assets: bool = True
    has_scenes: bool = True
    source: str = ""        # human-readable hint: "engine" / "sibling" / etc.


def _scan_pack(root: Path, source: str = "") -> PackInfo | None:
    meta_path = root / "content" / "meta.yaml"
    if not meta_path.exists():
        return None
    try:
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    return PackInfo(
        name=root.name,
        root=root.resolve(),
        title=str(meta.get("title", root.name)),
        subtitle=str(meta.get("subtitle", "")),
        start_location=meta.get("start_location"),
        intro_scene=meta.get("intro_scene"),
        has_assets=(root / "assets").exists(),
        has_scenes=any((root / "content" / "scenes").glob("*.y*ml"))
                    if (root / "content" / "scenes").exists() else False,
        source=source,
    )


def _candidate_search_roots(config: EngineConfig | None) -> list[tuple[Path, str]]:
    """List (directory, source-label) pairs to scan for packs."""
    config = config or EngineConfig()
    roots: list[tuple[Path, str]] = []
    roots.append((resource_root() / config.game_pack_dir, "engine"))
    for entry in config.extra_pack_dirs:
        e = Path(entry).expanduser()
        if not e.is_absolute():
            e = (resource_root() / e).resolve()
        # Label sibling vs user-cache for clarity.
        label = "user-cache" if "~" in str(entry) or ".world-gal-game" in str(e) \
            else "sibling"
        roots.append((e, label))
    return roots


def discover_packs(games_dir: Path | None = None,
                   config: EngineConfig | None = None) -> list[PackInfo]:
    """Return every valid pack found across the engine + sibling roots.

    Compatibility: callers that pass a single ``games_dir`` still work —
    we'll scan that directory plus the engine's standard extra roots.
    """
    seen: set[Path] = set()
    out: list[PackInfo] = []

    explicit_roots: list[tuple[Path, str]] = []
    if games_dir is not None:
        explicit_roots.append((Path(games_dir), "explicit"))
    explicit_roots.extend(_candidate_search_roots(config))

    for root, source in explicit_roots:
        if not root.exists():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            child_abs = child.resolve()
            if child_abs in seen:
                continue
            info = _scan_pack(child, source=source)
            if info is not None:
                seen.add(child_abs)
                out.append(info)
    return out


def render_table(packs: Iterable[PackInfo]) -> str:
    """Human-readable pack table (for --list-packs)."""
    packs = list(packs)
    if not packs:
        return ("(no game packs found — try "
                "`python tools/scaffold_pack.py --pack my_game`)")
    name_w = max(len(p.name) for p in packs)
    title_w = max(len(p.title) for p in packs)
    src_w = max(len(p.source) for p in packs) if any(p.source for p in packs) else 0
    header = f"{'name':<{name_w}}  {'title':<{title_w}}"
    if src_w:
        header += f"  {'source':<{src_w}}"
    header += "  subtitle"
    lines = [header, "-" * len(header)]
    for p in packs:
        row = f"{p.name:<{name_w}}  {p.title:<{title_w}}"
        if src_w:
            row += f"  {p.source:<{src_w}}"
        row += f"  {p.subtitle}"
        lines.append(row)
    return "\n".join(lines)
