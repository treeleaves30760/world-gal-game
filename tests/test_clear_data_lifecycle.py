"""Cross-playthrough completion and shutdown persistence regressions."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402


def _clear_data_path(root: Path) -> Path:
    return root / "saves" / "demo_pack" / "clear_data.json"


def _make_app(root: Path):
    from world_gal_game.app import GalGameApp
    from world_gal_game.config import EngineConfig

    pack = Path("games/demo_pack").resolve()
    app = GalGameApp(
        config=EngineConfig(
            default_pack=str(pack),
            screen_size=(1280, 720),
            writable_root_override=root,
            seen_intro=True,
            seed=0,
        ),
        pack=str(pack),
        headless=True,
    )
    app.manager.commit_pending()
    return app


def test_initial_launch_applies_clear_data_to_live_galleries(tmp_path):
    from world_gal_game.core.clear_data import ClearData

    path = _clear_data_path(tmp_path)
    ClearData(
        scenes_seen={"prologue"},
        line_identity_version=None,
        endings_seen={"ending_lover", "ending_removed_from_pack"},
        cgs_seen={"assets/cgs/lover_lakeside.png"},
    ).save(path)

    app = _make_app(tmp_path)
    try:
        assert app.state.read_log.scenes == {"prologue"}
        assert app.state.read_log.is_read("prologue", 0)
        assert app.state.cg_gallery.is_unlocked(
            "assets/cgs/lover_lakeside.png",
        )
        assert app.state.endings.is_unlocked("ending_lover")
        assert "ending_lover" in {
            ending.id for ending in app.state.endings.visible_to_player()
        }
        assert "ending_removed_from_pack" not in app.state.endings.unlocked
        assert app.state.endings.newly_unlocked() == []
    finally:
        pygame.quit()


def test_legacy_clear_data_drops_blank_cg_keys_before_live_union():
    from world_gal_game.core.clear_data import ClearData
    from world_gal_game.core.game_state import GameState

    clear_data = ClearData(
        cgs_seen={"", "   ", " assets/cgs/prior-run.png "},
    )
    state = GameState()

    clear_data.apply_to_state(state)

    assert clear_data.cgs_seen == {"assets/cgs/prior-run.png"}
    assert state.cg_gallery.unlocked == {"assets/cgs/prior-run.png"}


def test_clear_data_seeds_stable_read_keys_from_current_authored_lines():
    from world_gal_game.core.clear_data import ClearData
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.story_graph import Line, Scene

    state = GameState()
    state.story.add_scene(Scene(
        id="stable_scene",
        lines=[
            Line(text="first", loc_id="first"),
            Line(text="legacy fallback"),
        ],
    ))

    ClearData(
        scenes_seen={"stable_scene"},
        line_identity_version=None,
    ).apply_to_state(state)

    assert state.read_log.lines == {
        "stable_scene:@first",
        "stable_scene:1",
    }
    assert state.read_log.is_read("stable_scene", 99, loc_id="first")
    assert state.read_log.is_read("stable_scene", 1)


def test_clear_data_records_and_roundtrips_exact_partial_line_identities(
    tmp_path,
):
    from world_gal_game.core.clear_data import ClearData
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.story_graph import Line, Scene

    state = GameState()
    state.story.add_scene(Scene(
        id="partial_scene",
        lines=[
            Line(text="seen stable line", loc_id="alpha"),
            Line(text="unseen conditional branch", loc_id="unseen"),
            Line(text="seen legacy line"),
        ],
    ))
    state.read_log.mark_line("partial_scene", 0, loc_id="alpha")
    state.read_log.mark_line("partial_scene", 2)
    state.read_log.mark_scene_done("partial_scene")

    clear_data = ClearData()
    assert clear_data.record_from_state(state) is True
    assert clear_data.line_identity_version == 1
    assert clear_data.lines_seen == {
        "partial_scene:@alpha",
        "partial_scene:2",
    }
    assert "partial_scene:@unseen" not in clear_data.lines_seen

    path = tmp_path / "clear_data.json"
    clear_data.save(path)
    restored = ClearData.load(path)

    assert restored.line_identity_version == 1
    assert restored.lines_seen == clear_data.lines_seen
    seeded = GameState()
    restored.apply_to_state(seeded)
    assert seeded.read_log.is_read("partial_scene", 99, loc_id="alpha")
    assert seeded.read_log.is_read("partial_scene", 2)
    assert not seeded.read_log.is_read(
        "partial_scene",
        1,
        loc_id="unseen",
    )


def test_legacy_scene_progress_freezes_once_before_insert_and_reorder(
    tmp_path,
):
    import json

    from world_gal_game.core.clear_data import ClearData
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.story_graph import Line, Scene

    path = tmp_path / "clear_data.json"
    path.write_text(
        json.dumps({"scenes_seen": ["changed_scene"]}),
        encoding="utf-8",
    )
    legacy = ClearData.load(path)
    assert legacy.line_identity_version is None
    assert legacy.lines_seen == set()

    first_release = GameState()
    first_release.story.add_scene(Scene(
        id="changed_scene",
        lines=[
            Line(text="alpha", loc_id="alpha"),
            Line(text="beta", loc_id="beta"),
            Line(text="legacy positional"),
        ],
    ))
    legacy.apply_to_state(first_release)

    assert legacy.line_identity_version == 1
    assert legacy.lines_seen == {
        "changed_scene:@alpha",
        "changed_scene:@beta",
        "changed_scene:2",
    }
    legacy.save(path)

    # A later release reorders both identified lines and inserts new copy. The
    # persisted v1 marker prevents scenes_seen from expanding to that new id.
    restored = ClearData.load(path)
    next_release = GameState()
    next_release.story.add_scene(Scene(
        id="changed_scene",
        lines=[
            Line(text="beta moved", loc_id="beta"),
            Line(text="new content", loc_id="inserted"),
            Line(text="legacy positional"),
            Line(text="alpha moved", loc_id="alpha"),
        ],
    ))
    restored.apply_to_state(next_release)

    assert restored.lines_seen == legacy.lines_seen
    assert next_release.read_log.is_read(
        "changed_scene",
        0,
        loc_id="beta",
    )
    assert next_release.read_log.is_read(
        "changed_scene",
        3,
        loc_id="alpha",
    )
    assert next_release.read_log.is_read("changed_scene", 2)
    assert not next_release.read_log.is_read(
        "changed_scene",
        1,
        loc_id="inserted",
    )
    assert "changed_scene:@inserted" not in restored.lines_seen


def test_legacy_clear_data_without_music_field_loads_and_sorts_roundtrip(
    tmp_path,
):
    import json

    from world_gal_game.core.clear_data import ClearData

    path = tmp_path / "clear_data.json"
    path.write_text(
        json.dumps({"scenes_seen": ["legacy"]}),
        encoding="utf-8",
    )
    clear_data = ClearData.load(path)
    assert clear_data.music_seen == set()

    clear_data.music_seen = {
        "assets/bgm/z.ogg",
        "assets/bgm/a.ogg",
    }
    clear_data.save(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["music_seen"] == [
        "assets/bgm/a.ogg",
        "assets/bgm/z.ogg",
    ]


def test_title_menu_quit_persists_clear_data_immediately(tmp_path):
    from world_gal_game.core.clear_data import ClearData
    from world_gal_game.scenes.title import TitleScene

    app = _make_app(tmp_path)
    try:
        app.state.read_log.mark_scene_done("menu_quit_scene")
        title = app.manager.current
        assert isinstance(title, TitleScene)

        title.on_quit()

        assert app._running is False
        persisted = ClearData.load(app._clear_data_path)
        assert "menu_quit_scene" in persisted.scenes_seen
    finally:
        pygame.quit()


def test_os_quit_event_persists_clear_data(tmp_path):
    from world_gal_game.core.clear_data import ClearData

    app = _make_app(tmp_path)
    try:
        app.state.read_log.mark_scene_done("os_quit_scene")
        pygame.event.post(pygame.event.Event(pygame.QUIT))

        app._step(0.0)

        assert app._running is False
        persisted = ClearData.load(app._clear_data_path)
        assert "os_quit_scene" in persisted.scenes_seen
    finally:
        pygame.quit()


def test_sync_loop_exception_still_persists_and_shuts_down(
    tmp_path,
    monkeypatch,
):
    from world_gal_game.core.clear_data import ClearData

    app = _make_app(tmp_path)
    app.state.read_log.mark_scene_done("sync_finalizer_scene")
    calls: list[str] = []
    real_pygame_quit = pygame.quit

    def explode(_dt: float) -> None:
        raise RuntimeError("frame failed")

    monkeypatch.setattr(app, "_step", explode)
    monkeypatch.setattr(
        app,
        "_shutdown_steam",
        lambda: calls.append("steam"),
    )
    monkeypatch.setattr(pygame, "quit", lambda: calls.append("pygame"))

    try:
        with pytest.raises(RuntimeError, match="frame failed"):
            app.run()
        persisted = ClearData.load(app._clear_data_path)
        assert "sync_finalizer_scene" in persisted.scenes_seen
        assert calls == ["steam", "pygame"]
    finally:
        real_pygame_quit()


def test_async_loop_exception_still_persists_and_shuts_down(
    tmp_path,
    monkeypatch,
):
    from world_gal_game.core.clear_data import ClearData

    app = _make_app(tmp_path)
    app.state.read_log.mark_scene_done("async_finalizer_scene")
    calls: list[str] = []
    original_record = app._record_clear_data
    real_pygame_quit = pygame.quit

    def record() -> None:
        calls.append("record")
        original_record()

    def explode(_dt: float) -> None:
        raise RuntimeError("frame failed")

    monkeypatch.setattr(app, "_record_clear_data", record)
    monkeypatch.setattr(app, "_step", explode)
    monkeypatch.setattr(
        app,
        "_shutdown_steam",
        lambda: calls.append("steam"),
    )
    monkeypatch.setattr(pygame, "quit", lambda: calls.append("pygame"))

    try:
        with pytest.raises(RuntimeError, match="frame failed"):
            asyncio.run(app.run_async())
        persisted = ClearData.load(app._clear_data_path)
        assert "async_finalizer_scene" in persisted.scenes_seen
        assert calls == ["record", "steam", "pygame"]
    finally:
        real_pygame_quit()
