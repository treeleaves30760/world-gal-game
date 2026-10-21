from __future__ import annotations

import ast
from pathlib import Path

import world_gal_game

from world_gal_game.core.localization import DEFAULT_UI


def test_every_literal_runtime_translation_key_has_canonical_source_copy() -> None:
    """Freeze keyed UI coverage without pretending raw literals are localized."""

    root = Path(world_gal_game.__file__).parent
    calls: dict[str, list[str | None]] = {}
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "t"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            key = node.args[0].value
            source: str | None = None
            if (
                len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                source = node.args[1].value
            for keyword in node.keywords:
                if (
                    keyword.arg == "default"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    source = keyword.value.value
            calls.setdefault(key, []).append(source)

    missing = sorted(set(calls) - set(DEFAULT_UI))
    assert not missing, f"literal runtime t() keys missing from DEFAULT_UI: {missing}"

    # When call sites provide one or more literal fallback spellings, the
    # canonical source must equal at least one of them. A few shared keys are
    # intentionally rendered with short/long variants on different screens.
    mismatched: dict[str, tuple[str, set[str]]] = {}
    for key, observed in calls.items():
        literal_sources = {value for value in observed if value is not None}
        if literal_sources and DEFAULT_UI[key] not in literal_sources:
            mismatched[key] = (DEFAULT_UI[key], literal_sources)
    assert not mismatched
