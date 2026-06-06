"""Chapter / act / route manifest — first-class narrative structure.

A ``Scene.route`` is only a *tag*; nothing in the pack says "these scenes form
chapter 2 of the lover route, which can reach ``ending_lover``". For a small
demo that is fine, but a multi-route, multi-chapter VN wants that structure to
be first-class so authors (and agents) can reason about *acts and routes*, not
just individual scenes — "what scenes are in this chapter", "which chapters
belong to the lover route", "what's the act ordering".

This module adds that as an **optional declarative overlay**:
``content/chapters.yaml``, loaded exactly like ``content/variables.yaml`` (parked
on the private ``state.meta["__chapters__"]`` bridge that the save system
strips). It is pure static metadata — it does **not** touch the runtime dispatch
or change how scenes play, so it is fully backward compatible: a pack with no
``chapters.yaml`` simply has no chapter structure.

Shape (a bare list or ``{chapters: [...]}``)::

    chapters:
      - id: ch1_arrival
        title: "第一章 · 到站"
        subtitle: "搬家當天"     # optional; shown under the title on the eyecatch
        route: common
        act: act1
        order: 10
        entry_scene: prologue
        scenes: [prologue, town_square, meet_heroine]
      - id: ch2_lover
        title: "第二章 · 湖畔"
        route: lover
        act: act2
        order: 20
        scenes: [lover_event]
        endings: [ending_lover]
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ChapterSpec(BaseModel):
    """One declared chapter: a named, ordered grouping of scenes on a route."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = ""
    subtitle: str = ""        # optional human subtitle shown under the title-card
    route: str = ""           # route/path tag this chapter belongs to
    act: str = ""             # optional higher-level act grouping
    order: int = 0            # sort key for sequence (ties broken by id)
    entry_scene: str = ""     # optional first scene of the chapter
    scenes: list[str] = Field(default_factory=list)   # member scene ids
    endings: list[str] = Field(default_factory=list)  # endings this route reaches
    description: str = ""


class ChapterManifest(BaseModel):
    """The declared chapter/act/route structure of a pack (optional)."""

    model_config = ConfigDict(extra="forbid")

    chapters: list[ChapterSpec] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Construction

    @classmethod
    def from_items(cls, items: Any) -> "ChapterManifest":
        """Build from a list of dicts or a ``{id: body}`` mapping."""
        out: list[ChapterSpec] = []
        seen: set[str] = set()
        if isinstance(items, dict):
            rows = [{**(body or {}), "id": cid} for cid, body in items.items()]
        elif isinstance(items, list):
            rows = list(items)
        else:
            raise ValueError(
                f"expected a list or mapping of chapters, got {type(items).__name__}")
        for index, raw in enumerate(rows):
            if not isinstance(raw, dict) or "id" not in raw:
                raise ValueError(f"chapter at index {index} is missing required 'id'")
            if raw["id"] in seen:
                raise ValueError(f"duplicate chapter id: {raw['id']!r}")
            seen.add(raw["id"])
            out.append(ChapterSpec(**raw))
        return cls(chapters=out)

    @classmethod
    def load(cls, path: Path) -> "ChapterManifest":
        """Load from a YAML file, or an empty manifest if absent/empty."""
        path = Path(path)
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            return cls()
        items = raw.get("chapters", []) if isinstance(raw, dict) else raw
        return cls.from_items(items)

    # ------------------------------------------------------------------
    # Queries

    def ids(self) -> list[str]:
        return [c.id for c in self.chapters]

    def ordered(self) -> list[ChapterSpec]:
        """Chapters sorted by ``order`` then ``id`` — the narrative sequence."""
        return sorted(self.chapters, key=lambda c: (c.order, c.id))

    def by_route(self) -> dict[str, list[ChapterSpec]]:
        """Route tag -> its chapters, each route's list in narrative order."""
        routes: dict[str, list[ChapterSpec]] = {}
        for c in self.ordered():
            routes.setdefault(c.route, []).append(c)
        return routes

    def scene_to_chapter(self) -> dict[str, str]:
        """Scene id -> the (first, by order) chapter id that lists it."""
        mapping: dict[str, str] = {}
        for c in self.ordered():
            for sid in c.scenes:
                mapping.setdefault(sid, c.id)
        return mapping

    def referenced_scenes(self) -> set[str]:
        out: set[str] = set()
        for c in self.chapters:
            out.update(c.scenes)
            if c.entry_scene:
                out.add(c.entry_scene)
        return out

    def referenced_endings(self) -> set[str]:
        out: set[str] = set()
        for c in self.chapters:
            out.update(c.endings)
        return out
