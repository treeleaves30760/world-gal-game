"""The pack's media, as names a writer can use.

A directive tag such as ``<bgm:雨天>`` or ``<bg:圖書館>`` names an asset the way
a person would; the engine needs a path. :class:`AssetIndex` collects what the
pack already uses — backgrounds (from locations and scenes), music, event CGs
and sound effects — gives each a short label, and resolves a written name back
to its path: exact label, then a location name, then a close match.

The index only contains assets the pack's own content references, so a writer
can never be offered (or resolve to) a file the pack does not ship.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

KINDS = ("bg", "bgm", "cg", "sfx")


@dataclass
class Asset:
    kind: str
    path: str
    label: str
    aliases: list[str] = field(default_factory=list)
    note: str = ""

    def names(self) -> list[str]:
        return [self.label, *self.aliases]


def _stem(path: str) -> str:
    return PurePosixPath(str(path).replace("\\", "/")).stem


class AssetIndex:
    """Label <-> path lookup for one pack's backgrounds, music, CGs and SFX."""

    def __init__(self) -> None:
        self._assets: dict[str, dict[str, Asset]] = {kind: {} for kind in KINDS}

    # ------------------------------------------------------------------

    def add(self, kind: str, path: str | None, *, label: str = "",
            alias: str = "", note: str = "") -> None:
        if not path or not isinstance(path, str) or kind not in self._assets:
            return
        if path.strip().casefold() in ("none", "silence", "-"):
            return
        bucket = self._assets[kind]
        asset = bucket.get(path)
        if asset is None:
            asset = Asset(kind=kind, path=path, label=label or _stem(path))
            bucket[path] = asset
        elif label and asset.label == _stem(path):
            asset.aliases.append(asset.label)
            asset.label = label
        for name in (alias, _stem(path)):
            if name and name != asset.label and name not in asset.aliases:
                asset.aliases.append(name)
        if note and not asset.note:
            asset.note = note

    @classmethod
    def from_state(cls, state: Any) -> "AssetIndex":
        """Collect every asset the loaded pack's content refers to."""
        index = cls()
        game_map = getattr(state, "map", None)
        for location in getattr(game_map, "locations", {}).values():
            index.add("bg", location.background, label=location.name,
                      alias=location.id)
            for phase, path in (location.backgrounds or {}).items():
                index.add("bg", path, alias=f"{location.name}（{phase}）")
        story = getattr(state, "story", None)
        for scene in getattr(story, "scenes", {}).values():
            index.add("bg", scene.background)
            index.add("bgm", scene.bgm)
            index.add("cg", scene.cg)
            for line in scene.lines:
                index.add("bgm", line.bgm)
                index.add("cg", line.cg)
                index.add("sfx", line.sfx)
                for effect in line.effects:
                    if effect.kind == "set_background":
                        index.add("bg", effect.target)
                    elif effect.kind == "show_cg":
                        index.add("cg", effect.target)
        meta = getattr(state, "meta", {}) or {}
        music = meta.get("__music_room_manifest__")
        for entry in getattr(music, "entries", []) or []:
            index.add("bgm", getattr(entry, "asset", None),
                      label=getattr(entry, "title", "") or "",
                      note=getattr(entry, "usage", "") or "")
        gallery = meta.get("__cg_gallery_manifest__")
        for entry in getattr(gallery, "entries", []) or []:
            index.add("cg", getattr(entry, "asset", None),
                      label=getattr(entry, "title", "") or "",
                      note=getattr(entry, "description", "") or "")
        return index

    # ------------------------------------------------------------------

    def all(self, kind: str) -> list[Asset]:
        return sorted(self._assets.get(kind, {}).values(), key=lambda a: a.label)

    def labels(self, kind: str) -> list[str]:
        return [asset.label for asset in self.all(kind)]

    def label_for(self, kind: str, path: str | None) -> str:
        asset = self._assets.get(kind, {}).get(path or "")
        return asset.label if asset else ""

    def resolve(self, kind: str, name: str) -> Asset | None:
        """Find the asset a written name refers to (``None`` if nothing fits)."""
        name = (name or "").strip()
        if not name or kind not in self._assets:
            return None
        assets = self.all(kind)
        folded = name.casefold()
        for asset in assets:
            if asset.path == name or any(n.casefold() == folded for n in asset.names()):
                return asset
        for asset in assets:
            if any(folded in n.casefold() or n.casefold() in folded
                   for n in asset.names() if len(n) >= 2):
                return asset
        pool = {n.casefold(): asset for asset in assets for n in asset.names()}
        close = difflib.get_close_matches(folded, list(pool), n=1, cutoff=0.72)
        return pool[close[0]] if close else None

    def describe(self, kind: str, limit: int = 40) -> str:
        """One label per line (with its note), for a directive block."""
        lines = []
        for asset in self.all(kind)[:limit]:
            lines.append(f"- {asset.label}" + (f"：{asset.note}" if asset.note else ""))
        return "\n".join(lines)

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {kind: [{"label": a.label, "path": a.path, "aliases": a.aliases}
                       for a in self.all(kind)] for kind in KINDS}
