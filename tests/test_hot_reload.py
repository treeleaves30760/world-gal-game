"""Tests for HotReloader — verifies that player progress survives a YAML reload."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from world_gal_game.dev.hot_reload import HotReloader, _snapshot_progress, _restore_progress
from world_gal_game.core.game_state import GameState
from world_gal_game.npc.npc_base import NPCRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_minimal_pack(root: Path) -> None:
    """Write the minimum files for load_pack to succeed."""
    content = root / "content"
    content.mkdir(parents=True)
    (content / "meta.yaml").write_text(
        textwrap.dedent("""\
            title: Test Pack
            start_location: campus
        """),
        encoding="utf-8",
    )
    (content / "locations.yaml").write_text(
        textwrap.dedent("""\
            locations:
              - id: campus
                name: 校園
                exits: []
        """),
        encoding="utf-8",
    )
    (content / "characters.yaml").write_text(
        textwrap.dedent("""\
            characters:
              - id: npc_a
                name: Aya
        """),
        encoding="utf-8",
    )
    scenes_dir = content / "scenes"
    scenes_dir.mkdir()
    (scenes_dir / "s00.yaml").write_text(
        textwrap.dedent("""\
            scenes:
              - id: intro
                title: Intro
                lines:
                  - {speaker: npc_a, text: "Hello."}
        """),
        encoding="utf-8",
    )


def _write_modified_pack(root: Path) -> None:
    """Modify the scene definition but keep location and character."""
    scenes_dir = root / "content" / "scenes"
    (scenes_dir / "s00.yaml").write_text(
        textwrap.dedent("""\
            scenes:
              - id: intro
                title: Intro (v2)
                lines:
                  - {speaker: npc_a, text: "Hello again."}
              - id: new_scene
                title: Brand new
                lines:
                  - {speaker: npc_a, text: "New content."}
        """),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reload_preserves_flags(tmp_path: Path) -> None:
    """Flags set before reload must still be set after reload."""
    _write_minimal_pack(tmp_path)
    from world_gal_game.content_loader import load_pack

    state, npcs, _ = load_pack(tmp_path / "content")
    state.events.set_flag("met_aya", True)
    state.events.set_flag("chapter", 3)

    reloader = HotReloader(tmp_path)
    new_state, _, _ = reloader.reload(state, npcs)

    assert new_state.events.get_flag("met_aya") is True
    assert new_state.events.get_flag("chapter") == 3


def test_reload_preserves_affection(tmp_path: Path) -> None:
    """Affection values for existing NPCs survive a reload."""
    _write_minimal_pack(tmp_path)
    from world_gal_game.content_loader import load_pack

    state, npcs, _ = load_pack(tmp_path / "content")
    state.affection.adjust("npc_a", 42)

    reloader = HotReloader(tmp_path)
    new_state, _, _ = reloader.reload(state, npcs)

    assert new_state.affection.get("npc_a") == 42


def test_reload_preserves_inventory(tmp_path: Path) -> None:
    """Items in the player's inventory survive a reload."""
    _write_minimal_pack(tmp_path)
    # Add an item definition.
    (tmp_path / "content" / "items.yaml").write_text(
        textwrap.dedent("""\
            items:
              - id: potion
                name: Potion
        """),
        encoding="utf-8",
    )
    from world_gal_game.content_loader import load_pack

    state, npcs, _ = load_pack(tmp_path / "content")
    state.inventory.add("potion", 5)

    reloader = HotReloader(tmp_path)
    new_state, _, _ = reloader.reload(state, npcs)

    assert new_state.inventory.count("potion") == 5


def test_reload_picks_up_new_scene(tmp_path: Path) -> None:
    """After modifying the YAML, the new scene should be visible."""
    _write_minimal_pack(tmp_path)
    from world_gal_game.content_loader import load_pack

    state, npcs, _ = load_pack(tmp_path / "content")
    assert "new_scene" not in state.story.scenes

    _write_modified_pack(tmp_path)
    reloader = HotReloader(tmp_path)
    new_state, _, _ = reloader.reload(state, npcs)

    assert "new_scene" in new_state.story.scenes


def test_reload_does_not_reset_played_scenes(tmp_path: Path) -> None:
    """Scenes marked as played before reload remain in the played set."""
    _write_minimal_pack(tmp_path)
    from world_gal_game.content_loader import load_pack

    state, npcs, _ = load_pack(tmp_path / "content")
    state.story.played.add("intro")

    reloader = HotReloader(tmp_path)
    new_state, _, _ = reloader.reload(state, npcs)

    assert "intro" in new_state.story.played


def test_snapshot_restore_round_trip() -> None:
    """_snapshot_progress + _restore_progress is a lossless round-trip."""
    state = GameState()
    state.affection.register("hero")
    state.affection.adjust("hero", 10)
    state.events.set_flag("x", True)
    state.inventory.add("sword", 3)

    snap = _snapshot_progress(state)

    fresh = GameState()
    fresh.affection.register("hero")
    _restore_progress(fresh, snap)

    assert fresh.affection.get("hero") == 10
    assert fresh.events.get_flag("x") is True
    # inventory: item not in fresh.items, but counts still inserted
    assert fresh.inventory.count("sword") == 3
