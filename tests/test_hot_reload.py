"""Tests for HotReloader — verifies that player progress survives a YAML reload."""
from __future__ import annotations

import textwrap
from pathlib import Path

from world_gal_game.dev.hot_reload import HotReloader, _snapshot_progress, _restore_progress
from world_gal_game.core.game_state import GameState


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
    (content / "music_room.yaml").write_text(
        textwrap.dedent("""\
            entries:
              - asset: assets/bgm/hot_reload.ogg
                title: Hot Reload Theme
        """),
        encoding="utf-8",
    )
    bgm = root / "assets" / "bgm"
    bgm.mkdir(parents=True)
    (bgm / "hot_reload.ogg").write_bytes(b"test audio identity")
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


def test_reload_preserves_only_currently_authorized_music_progress(
    tmp_path: Path,
) -> None:
    _write_minimal_pack(tmp_path)
    from world_gal_game.content_loader import load_pack

    state, npcs, _ = load_pack(tmp_path / "content")
    state.music_room.unlock("assets/bgm/./hot_reload.ogg")
    state.music_room.unlock("../forged.ogg")

    new_state, _, _ = HotReloader(tmp_path).reload(state, npcs)

    assert new_state.music_room.unlocked == {"assets/bgm/hot_reload.ogg"}


def test_snapshot_restore_round_trip() -> None:
    """_snapshot_progress + _restore_progress is a lossless round-trip."""
    state = GameState()
    state.affection.register("hero")
    state.affection.adjust("hero", 10)
    state.events.set_flag("x", True)
    state.inventory.add("sword", 3)
    state.read_log.mark_line("intro", 0)
    state.read_log.mark_scene_done("intro")
    state.cg_gallery.unlock("assets/cgs/memory.png")
    state.music_room.unlock("assets/bgm/memory.ogg")
    state.endings.unlocked["ending_current"] = "2026-01-01"
    state.endings.seen.add("ending_current")
    state.time.advance(5)

    snap = _snapshot_progress(state)

    fresh = GameState()
    fresh.affection.register("hero")
    _restore_progress(fresh, snap)

    assert fresh.affection.get("hero") == 10
    assert fresh.events.get_flag("x") is True
    # inventory: item not in fresh.items, but counts still inserted
    assert fresh.inventory.count("sword") == 3
    assert fresh.read_log.is_read("intro", 0)
    assert "intro" in fresh.read_log.scenes
    assert fresh.cg_gallery.is_unlocked("assets/cgs/memory.png")
    assert fresh.music_room.is_unlocked("assets/bgm/memory.ogg")
    assert fresh.endings.unlocked["ending_current"] == "2026-01-01"
    assert "ending_current" in fresh.endings.seen
    assert fresh.time.model_dump() == state.time.model_dump()


def test_app_hot_reload_rebinds_runtime_owners_and_catalog_baselines() -> None:
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    try:
        app = driver.app
        app.state.cg_gallery.unlock("assets/cgs/hot-reload-memory.png")
        app.state.read_log.mark_scene_done("hot_reload_retired_scene")
        old_state = app.state
        app._hot_reloader = HotReloader(
            app.config.pack_root(app.pack),
        )

        app._do_hot_reload()

        assert app.state is not old_state
        assert app.ctx.state is app.state
        assert app.dialogue.state is app.state
        assert app.ctx.dialogue is app.dialogue
        assert app.state.meta["__clear_data__"] is app.clear_data
        assert app.state.cg_gallery.is_unlocked(
            "assets/cgs/hot-reload-memory.png",
        )
        assert "hot_reload_retired_scene" in app.state.read_log.scenes
        assert set(app._authored_ending_catalog.endings) == set(
            app.state.endings.endings,
        )
        assert set(app._authored_map_catalog.locations) == set(
            app.state.map.locations,
        )
        plugin_manager = app.state.meta["__plugin_manager__"]
        assert plugin_manager._ctx.state is app.state
    finally:
        driver.quit()
