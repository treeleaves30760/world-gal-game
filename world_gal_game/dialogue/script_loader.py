"""Load scenes from YAML files.

Scenes can be authored as YAML; each YAML file may contain a single scene
(dict) or a list of scenes. The loader is forgiving about missing fields.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import yaml

from ..core.story_graph import Scene, Line, Choice, Effect, Condition


def _to_effects(items: list[Any] | None) -> list[Effect]:
    if not items:
        return []
    out: list[Effect] = []
    for i in items:
        if isinstance(i, str):
            # Shorthand: "kind:target=value"
            kind, _, rest = i.partition(":")
            target, _, value = rest.partition("=")
            out.append(Effect(kind=kind.strip(), target=target.strip(),
                              value=value.strip() or None))
        else:
            out.append(Effect(**i))
    return out


def _to_conditions(items: list[Any] | None) -> list[Condition]:
    if not items:
        return []
    out: list[Condition] = []
    for i in items:
        if isinstance(i, str):
            kind, _, rest = i.partition(":")
            target, _, value = rest.partition("=")
            cond = Condition(kind=kind.strip(), target=target.strip())
            if value:
                v = value.strip()
                try:
                    cond.value = int(v)
                except ValueError:
                    cond.value = v
            out.append(cond)
        else:
            out.append(Condition(**i))
    return out


def _to_lines(items: list[dict] | None) -> list[Line]:
    if not items:
        return []
    out: list[Line] = []
    for it in items:
        d = dict(it)
        d["effects"] = _to_effects(d.pop("effects", None))
        d["requires"] = _to_conditions(d.pop("requires", None))
        out.append(Line(**d))
    return out


def _to_choices(items: list[dict] | None) -> list[Choice]:
    if not items:
        return []
    out: list[Choice] = []
    for it in items:
        d = dict(it)
        d["effects"] = _to_effects(d.pop("effects", None))
        d["requires"] = _to_conditions(d.pop("requires", None))
        d["forbids"] = _to_conditions(d.pop("forbids", None))
        out.append(Choice(**d))
    return out


def _to_scene(d: dict[str, Any]) -> Scene:
    d = dict(d)
    d["lines"] = _to_lines(d.pop("lines", None))
    d["choices"] = _to_choices(d.pop("choices", None))
    d["on_end"] = _to_effects(d.pop("on_end", None))
    d["requires"] = _to_conditions(d.pop("requires", None))
    return Scene(**d)


def load_scenes_from_yaml(path: Path) -> list[Scene]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return []
    if isinstance(data, dict):
        # Either a single scene or a "scenes:" block.
        if "scenes" in data and isinstance(data["scenes"], list):
            return [_to_scene(s) for s in data["scenes"]]
        return [_to_scene(data)]
    if isinstance(data, list):
        return [_to_scene(s) for s in data]
    raise ValueError(f"unknown YAML structure in {path}")


def load_scenes_dir(directory: Path) -> list[Scene]:
    out: list[Scene] = []
    for p in sorted(Path(directory).glob("**/*.y*ml")):
        out.extend(load_scenes_from_yaml(p))
    return out
