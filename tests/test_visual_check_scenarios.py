"""Representative, deterministic visual-check scenario coverage."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


def _make_app(tmp_path: Path, name: str):
    from world_gal_game.app import GalGameApp
    from world_gal_game.config import EngineConfig

    pack = Path("games/demo_pack").resolve()
    config = EngineConfig(
        default_pack=str(pack),
        screen_size=(1280, 720),
        writable_root_override=tmp_path / name,
        seen_intro=True,
        seed=0,
    )
    app = GalGameApp(
        config=config,
        pack=str(pack),
        headless=True,
    )
    app.manager.transitions_enabled = False
    app.manager.commit_pending()
    return app


def _enable_demo_guided_map(app) -> None:
    """Give demo content a generic story/ordinary split without pack edits."""
    app.meta["travel"] = {
        "scope": "graph",
        "minimum_cost": 1,
        "guided_story": {
            "future_horizon_phases": 2,
            "max_primary": 3,
        },
    }


def test_gallery_visual_state_uses_manifest_order_without_pack_ids() -> None:
    from world_gal_game.core.cg_gallery_spec import CGGalleryManifest
    from world_gal_game.core.game_state import GameState
    from world_gal_game.dev.visual_check import VisualCheck

    state = GameState()
    state.meta["__cg_gallery_manifest__"] = CGGalleryManifest.from_items([
        {
            "asset": f"assets/cgs/memory_{index}.png",
            "title": f"Memory {index}",
            "order": index,
            "hidden": index == 4,
        }
        for index in range(5)
    ])

    VisualCheck._seed_gallery_visual_state(
        SimpleNamespace(state=state)
    )

    assert state.cg_gallery.unlocked == {
        "assets/cgs/memory_0.png",
        "assets/cgs/memory_2.png",
        "assets/cgs/memory_4.png",
    }


def test_music_room_visual_state_uses_media_authority_without_playback() -> None:
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.media_authority import MediaAuthority
    from world_gal_game.dev.visual_check import VisualCheck

    state = GameState()
    state.meta["__pack_media_authority__"] = MediaAuthority(
        cg_ids=frozenset(),
        music_ids=frozenset({
            f"assets/bgm/track_{index}.ogg" for index in range(5)
        }),
    )
    assets = SimpleNamespace(_current_music="assets/bgm/story_theme.ogg")

    VisualCheck._seed_music_room_visual_state(
        SimpleNamespace(state=state, assets=assets)
    )

    assert state.music_room.unlocked == {
        "assets/bgm/track_0.ogg",
        "assets/bgm/track_2.ogg",
        "assets/bgm/track_4.ogg",
    }
    assert assets._current_music == "assets/bgm/story_theme.ogg"


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, {"assets/bgm/track_0.ogg"}),
        (2, {"assets/bgm/track_0.ogg"}),
        (3, {"assets/bgm/track_0.ogg", "assets/bgm/track_2.ogg"}),
    ],
)
def test_music_room_visual_state_keeps_a_locked_row_when_possible(
    count: int,
    expected: set[str],
) -> None:
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.media_authority import MediaAuthority
    from world_gal_game.dev.visual_check import VisualCheck

    state = GameState()
    state.meta["__pack_media_authority__"] = MediaAuthority(
        cg_ids=frozenset(),
        music_ids=frozenset({
            f"assets/bgm/track_{index}.ogg" for index in range(count)
        }),
    )

    VisualCheck._seed_music_room_visual_state(SimpleNamespace(state=state))

    assert state.music_room.unlocked == expected


def test_default_visual_scenarios_name_the_screen_they_capture():
    from world_gal_game.dev.visual_check import VisualCheck

    scenarios = VisualCheck.default_scenarios()

    base = [
        ("title", "title"),
        ("onboarding", "onboarding"),
        ("dialogue", "dialogue"),
        ("choice", "choice"),
        ("exploration", "exploration"),
        (
            "spatial_map_story_priority",
            "spatial_map_story_priority",
        ),
        (
            "spatial_map_all_reachable",
            "spatial_map_all_reachable",
        ),
        ("travel_confirmation", "travel_confirmation"),
        ("pause_menu", "menu"),
        ("dialogue_history", "history"),
        ("save_screen", "save"),
        ("load_screen", "load"),
        ("settings", "settings"),
        ("cg_gallery", "cg_gallery"),
        ("music_room", "music_room"),
        ("scene_replay", "scene_replay"),
        ("flowchart", "flowchart"),
    ]
    assert len(scenarios) == 34
    assert [
        (scenario["name"], scenario["view"], scenario["screen_size"])
        for scenario in scenarios
    ] == [
        (f"{name}_{label}", view, size)
        for label, size in [
            ("720p", (1280, 720)),
            ("1080p", (1920, 1080)),
        ]
        for name, view in base
    ]


def test_visual_check_cli_help_lists_music_room_aliases(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from world_gal_game.cli import visual_check_main

    with pytest.raises(SystemExit) as captured:
        visual_check_main(["--help"])

    assert captured.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "music_room (aliases: music, jukebox)" in help_text


def test_cli_dev_start_map_screenshots_the_actual_map_scene(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from world_gal_game.cli import _run_screenshot_mode
    from world_gal_game.scenes.map_scene import MapScene

    app = _make_app(tmp_path, "cli-map")
    drawn: list[MapScene] = []
    observed_state: list[tuple[bool, str, str]] = []
    real_draw = MapScene.draw

    def recording_draw(self, surface):
        drawn.append(self)
        observed_state.append(
            (
                self.ctx.state.events.has_flag("cli_map_flag"),
                self.ctx.state.map.current_location_id,
                self.ctx.state.time.time_of_day.value,
            )
        )
        return real_draw(self, surface)

    monkeypatch.setattr(MapScene, "draw", recording_draw)
    monkeypatch.setattr(pygame.display, "flip", lambda: None)
    target = tmp_path / "map.png"
    args = SimpleNamespace(
        dev_flags='{"cli_map_flag": true}',
        dev_affection='{"heroine_1": 7}',
        dev_time="night",
        dev_location="town_square",
        dev_start="map",
        screenshot=target,
        autoplay=0.0,
    )

    assert _run_screenshot_mode(app, args) == 0
    assert drawn
    assert observed_state
    assert set(observed_state) == {(True, "town_square", "night")}
    assert app.state.affection.get("heroine_1") == 7
    assert target.exists()
    assert target.stat().st_size > 1000


def test_visual_views_build_real_stacks_and_post_intro_state(tmp_path: Path):
    from world_gal_game.dev.visual_check import VisualCheck
    from world_gal_game.plugins import restore, snapshot

    expected_stacks = {
        "title": ["TitleScene"],
        "onboarding": [
            "ExplorationScene",
            "DialogueScene",
            "OnboardingScene",
        ],
        "dialogue": ["ExplorationScene", "DialogueScene"],
        "choice": ["ExplorationScene", "DialogueScene"],
        "exploration": ["ExplorationScene"],
        "destination": ["ExplorationScene", "MapScene"],
        "spatial_map_story_priority": ["ExplorationScene", "MapScene"],
        "spatial_map_all_reachable": ["ExplorationScene", "MapScene"],
        "travel_confirmation": [
            "ExplorationScene",
            "MapScene",
            "DestinationPickerScene",
        ],
        "menu": ["ExplorationScene", "MenuScene"],
        "history": ["ExplorationScene", "ScrollbackScene"],
        "save": ["ExplorationScene", "SaveScene"],
        "load": ["ExplorationScene", "SaveScene"],
        "settings": ["ExplorationScene", "SettingsScene"],
        "cg_gallery": ["ExplorationScene", "CGGalleryScene"],
        "music_room": ["ExplorationScene", "MusicRoomScene"],
        "music": ["ExplorationScene", "MusicRoomScene"],
        "jukebox": ["ExplorationScene", "MusicRoomScene"],
        "scene_replay": ["ExplorationScene", "SceneReplayScene"],
        "replay": ["ExplorationScene", "SceneReplayScene"],
        "flowchart": ["ExplorationScene", "FlowchartScene"],
    }
    plugin_snapshot = snapshot()
    try:
        for view, expected in expected_stacks.items():
            app = _make_app(tmp_path, view)
            try:
                VisualCheck._prepare_view(app, view)
                app.manager.commit_pending()
                stack = [
                    type(scene).__name__ for scene in app.manager.stack()
                ]
                assert stack == expected

                current = app.manager.current
                if view == "dialogue":
                    description = current.describe()
                    assert description["scene_id"] == "prologue"
                    assert description["choice_visible"] is False
                    assert current.box.fully_revealed()
                elif view == "onboarding":
                    description = current.describe()
                    assert description["scene"] == "OnboardingScene"
                    assert current.start_btn.focused is True
                    assert app.config.seen_intro is False
                elif view == "choice":
                    description = current.describe()
                    assert description["scene_id"] == "meet_heroine"
                    assert description["choice_visible"] is True
                    assert len(current.choices.buttons) == 2
                elif view in {
                    "exploration",
                    "destination",
                    "spatial_map_story_priority",
                    "spatial_map_all_reachable",
                    "travel_confirmation",
                    "menu",
                    "history",
                    "save",
                    "load",
                    "settings",
                    "cg_gallery",
                    "music_room",
                    "music",
                    "jukebox",
                    "scene_replay",
                    "replay",
                    "flowchart",
                }:
                    # The player reaches these screens only after the intro,
                    # so the scenario must carry its effects instead of
                    # showing a raw pre-intro state under an overlay.
                    assert app.state.events.get_flag("prologue_done") is True
                    assert app.state.time.time_of_day.value == "noon"
                if view == "travel_confirmation":
                    description = current.describe()
                    assert description["detail_only"] is True
                    assert description["selected"] == "town_square"
                    assert description["travel_previews"]["town_square"][
                        "path"
                    ] == ["starting_room", "town_square"]
                    # Opening the sheet quotes a plan but never commits it.
                    assert app.state.map.current_location_id == "starting_room"
                if view in {"save", "load"}:
                    assert current.mode == view
                    assert any(
                        item.get("slot") == app.config.quicksave_slot
                        for item in current._items
                    )
                if view == "history":
                    description = current.describe()
                    assert description["lines"] > 0
                    assert description["focused_index"] == description["lines"] - 1
                if view in {"scene_replay", "replay"}:
                    description = current.describe()
                    assert description["replaying"] is False
                    assert description["replayable"]
                    assert "prologue" in description["replayable"]
                    assert app.state.read_log.replay_entry("prologue") is not None
                if view in {"music_room", "music", "jukebox"}:
                    assert current.describe()["scene"] == "MusicRoomScene"
                    # Staging the visual state must never start a preview.
                    assert current._started_playback is False
                if view == "flowchart":
                    description = current.describe()
                    assert description["scene"] == "FlowchartScene"
                    assert len(description["chapters"]) > 0
            finally:
                pygame.quit()
    finally:
        restore(plugin_snapshot)


def test_replay_view_without_intro_drives_a_complete_authored_trace(
    tmp_path: Path,
) -> None:
    from world_gal_game.dev.visual_check import VisualCheck
    from world_gal_game.plugins import restore, snapshot

    plugin_snapshot = snapshot()
    app = _make_app(tmp_path, "replay-without-intro")
    try:
        app.meta.pop("intro_scene", None)
        app.state.read_log.scenes.clear()
        app.state.read_log.replay_archive.clear()

        VisualCheck._prepare_view(app, "replay", scene_id="prologue")
        app.manager.commit_pending()

        assert [
            type(scene).__name__ for scene in app.manager.stack()
        ] == ["ExplorationScene", "SceneReplayScene"]
        assert app.state.read_log.replay_entry("prologue") is not None
        assert app.manager.current.describe()["replayable"] == ["prologue"]
        assert app.state.events.get_flag("prologue_done") is True
    finally:
        pygame.quit()
        restore(plugin_snapshot)


@pytest.mark.parametrize("view", ["save", "load"])
def test_visual_save_views_pin_temporary_slot_timestamp(
    tmp_path: Path,
    monkeypatch,
    view: str,
) -> None:
    import world_gal_game.core.save_manager as save_manager_module
    from world_gal_game.dev.visual_check import VisualCheck
    from world_gal_game.plugins import restore, snapshot

    class FutureClock:
        @classmethod
        def now(cls, tz):
            return datetime(2099, 12, 31, 23, 59, 58, tzinfo=tz)

    monkeypatch.setattr(save_manager_module, "datetime", FutureClock)
    plugin_snapshot = snapshot()
    app = _make_app(tmp_path, f"fixed-{view}-timestamp")
    try:
        VisualCheck._prepare_view(app, view)
        app.manager.commit_pending()

        expected = "2026-01-01T00:00:00+00:00"
        slot = app.config.quicksave_slot
        save_path = app.config.save_dir() / f"{slot}.json"
        payload = json.loads(save_path.read_text(encoding="utf-8"))
        assert payload["_saved_at"] == expected

        row = next(item for item in app.manager.current._items
                   if item.get("slot") == slot)
        assert row["saved_at"] == expected
    finally:
        pygame.quit()
        restore(plugin_snapshot)


def test_semantic_map_views_pin_story_all_and_confirmation_states(
    tmp_path: Path,
) -> None:
    from world_gal_game.dev.visual_check import VisualCheck
    from world_gal_game.plugins import restore, snapshot

    plugin_snapshot = snapshot()
    try:
        story_app = _make_app(tmp_path, "story-priority")
        try:
            _enable_demo_guided_map(story_app)
            VisualCheck._prepare_view(
                story_app,
                "spatial_map_story_priority",
            )
            story_app.manager.commit_pending()
            story = story_app.manager.current.describe()

            assert story["view_mode"] == "story_only"
            assert story["show_ordinary"] is False
            assert story["markers"] == {
                "starting_room": "current",
                "town_square": "story",
            }
            assert set(story["reachable"]) == {
                "town_square",
                "park",
                "shop_alley",
            }
        finally:
            pygame.quit()

        all_app = _make_app(tmp_path, "all-reachable")
        try:
            _enable_demo_guided_map(all_app)
            VisualCheck._prepare_view(
                all_app,
                "spatial_map_all_reachable",
            )
            all_app.manager.commit_pending()
            expanded = all_app.manager.current.describe()

            assert expanded["view_mode"] == "all_reachable"
            assert expanded["show_ordinary"] is True
            assert expanded["markers"] == {
                "starting_room": "current",
                "town_square": "story",
                "park": "ordinary",
                "shop_alley": "ordinary",
            }
            assert expanded["focus"]["node"] == "park"
        finally:
            pygame.quit()

        confirm_app = _make_app(tmp_path, "travel-confirmation")
        try:
            _enable_demo_guided_map(confirm_app)
            VisualCheck._prepare_view(confirm_app, "travel_confirmation")
            confirm_app.manager.commit_pending()
            confirmation = confirm_app.manager.current.describe()

            assert confirmation["detail_only"] is True
            assert confirmation["selected"] == "town_square"
            assert confirmation["travel_previews"]["town_square"] == {
                "path": ["starting_room", "town_square"],
                "available_hooks": ["meet_heroine"],
                "arrival_hooks": ["meet_heroine"],
                "arrival": {
                    "day": 1,
                    "weekday": "mon",
                    "time_of_day": "afternoon",
                },
            }
            assert confirm_app.state.map.current_location_id == "starting_room"
            assert confirm_app.state.time.time_of_day.value == "noon"
        finally:
            pygame.quit()
    finally:
        restore(plugin_snapshot)


def test_spatial_map_scenario_reaches_authored_auto_open_boundary(
    tmp_path: Path,
) -> None:
    from world_gal_game.dev.visual_check import VisualCheck
    from world_gal_game.plugins import restore, snapshot

    plugin_snapshot = snapshot()
    app = _make_app(tmp_path, "map-entry")
    try:
        app.meta["travel"] = {
            "spatial_map": {
                "auto_open_after_scenes": ["meet_heroine"],
            },
        }

        VisualCheck._complete_intro_state(app)
        assert app.state.map.current_location_id == "starting_room"
        assert app.state.story.is_played("meet_heroine") is False

        VisualCheck._complete_spatial_map_entry_state(app)

        assert app.state.map.current_location_id == "town_square"
        assert app.state.story.is_played("meet_heroine") is True
        assert app.state.events.get_flag("met_heroine_1") is True
        assert app.state.events.get_flag("quest_started") is True
    finally:
        pygame.quit()
        restore(plugin_snapshot)


def test_travel_confirmation_advances_to_first_allowed_story_departure(
    tmp_path: Path,
) -> None:
    from world_gal_game.dev.visual_check import VisualCheck
    from world_gal_game.plugins import restore, snapshot

    plugin_snapshot = snapshot()
    app = _make_app(tmp_path, "future-confirmation")
    try:
        _enable_demo_guided_map(app)
        meet_hook = app.state.map.locations["town_square"].scene_hooks[0]
        meet_hook.requires_time = ["night"]

        VisualCheck._prepare_view(app, "travel_confirmation")
        app.manager.commit_pending()
        confirmation = app.manager.current.describe()

        # Intro ends at noon. The future offer advises an evening departure,
        # then its one-phase route arrives at the night-only story.
        assert app.state.time.time_of_day.value == "evening"
        assert app.state.map.current_location_id == "starting_room"
        assert confirmation["selected"] == "town_square"
        assert confirmation["travel_previews"]["town_square"]["arrival"] == {
            "day": 1,
            "weekday": "mon",
            "time_of_day": "night",
        }
    finally:
        pygame.quit()
        restore(plugin_snapshot)


@pytest.mark.parametrize(
    "view",
    [
        "spatial_map_story_priority",
        "spatial_map_all_reachable",
        "travel_confirmation",
    ],
)
@pytest.mark.parametrize(
    ("size_label", "screen_size"),
    [
        ("720p", (1280, 720)),
        ("1080p", (1920, 1080)),
    ],
)
def test_semantic_map_candidates_are_reproducible_at_shipping_sizes(
    tmp_path: Path,
    view: str,
    size_label: str,
    screen_size: tuple[int, int],
) -> None:
    from world_gal_game.dev.visual_check import VisualCheck
    from world_gal_game.plugins import restore, snapshot

    plugin_snapshot = snapshot()
    try:
        check = VisualCheck(
            "games/demo_pack",
            baselines_dir=tmp_path / "baselines",
            candidates_dir=tmp_path / "candidates",
        )
        first = check.capture(
            name=f"{view}_{size_label}_first",
            view=view,
            screen_size=screen_size,
            settle_frames=1,
        )
        second = check.capture(
            name=f"{view}_{size_label}_second",
            view=view,
            screen_size=screen_size,
            settle_frames=1,
        )
    finally:
        restore(plugin_snapshot)

    assert pygame.image.load(first).get_size() == screen_size
    assert pygame.image.load(second).get_size() == screen_size
    assert first.stat().st_size > 1000
    assert second.stat().st_size > 1000
    assert first.read_bytes() == second.read_bytes()


def test_visual_capture_uses_fixed_frames_and_neutral_input(
    tmp_path: Path,
    monkeypatch,
):
    from world_gal_game.dev.visual_check import VisualCheck
    from world_gal_game.plugins import restore, snapshot
    from world_gal_game.scenes.base import SceneManager

    updates: list[tuple[float, tuple[int, int]]] = []
    real_update = SceneManager.update

    def recording_update(self, dt, inp):
        updates.append((dt, inp.mouse_pos))
        return real_update(self, dt, inp)

    monkeypatch.setattr(SceneManager, "update", recording_update)
    plugin_snapshot = snapshot()
    try:
        check = VisualCheck(
            "games/demo_pack",
            baselines_dir=tmp_path / "baselines",
            candidates_dir=tmp_path / "candidates",
        )
        path = check.capture(
            name="fixed",
            view="title",
            settle_frames=3,
        )
    finally:
        restore(plugin_snapshot)

    assert path.is_file()
    assert len(updates) == 3
    assert [dt for dt, _pos in updates] == pytest.approx([1 / 60] * 3)
    assert {pos for _dt, pos in updates} == {(-10_000, -10_000)}


def test_visual_capture_screen_size_is_part_of_render_contract(
    tmp_path: Path,
) -> None:
    from world_gal_game.dev.visual_check import VisualCheck

    check = VisualCheck(
        "games/demo_pack",
        baselines_dir=tmp_path / "baselines",
        candidates_dir=tmp_path / "candidates",
    )
    path = check.capture(
        name="title_1080p",
        view="title",
        screen_size=(1920, 1080),
        settle_frames=1,
    )

    image = pygame.image.load(path)
    assert image.get_size() == (1920, 1080)


def test_visual_scenario_forwards_runtime_locale_without_persisting_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from world_gal_game.dev.visual_check import VisualCheck

    check = VisualCheck(
        "games/demo_pack",
        baselines_dir=tmp_path / "baselines",
        candidates_dir=tmp_path / "candidates",
    )
    observed: dict[str, object] = {}

    def recording_capture(**kwargs):
        observed.update(kwargs)
        return tmp_path / "candidate.png"

    monkeypatch.setattr(check, "capture", recording_capture)

    result = check.capture_scenario({
        "name": "english-title",
        "view": "title",
        "locale": "en-GB",
        "text_expansion_factor": 1.35,
        "screen_size": [1280, 720],
    })

    assert result == tmp_path / "candidate.png"
    assert observed["locale"] == "en-GB"
    assert observed["text_expansion_factor"] == 1.35
    assert observed["screen_size"] == (1280, 720)


def test_visual_scenario_prestate_selects_authored_scene(tmp_path: Path):
    from world_gal_game.dev.visual_check import VisualCheck
    from world_gal_game.plugins import restore, snapshot

    plugin_snapshot = snapshot()
    app = _make_app(tmp_path, "prestate")
    try:
        app.state.events.set_flag("quest_started", True)
        VisualCheck._prepare_view(
            app,
            "choice",
            scene_id="return_to_heroine",
        )

        description = app.manager.current.describe()
        assert description["scene_id"] == "return_to_heroine"
        assert description["choice_visible"] is True
    finally:
        pygame.quit()
        restore(plugin_snapshot)


def test_pixel_diff_works_after_hermetic_capture_quits_pygame(
    tmp_path: Path,
):
    from world_gal_game.dev.visual_check import VisualCheck

    pygame.init()
    first = pygame.Surface((2, 2), pygame.SRCALPHA)
    second = first.copy()
    first.fill((10, 20, 30, 255))
    second.fill((10, 20, 30, 255))
    second.set_at((1, 1), (11, 20, 30, 255))
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    pygame.image.save(first, first_path)
    pygame.image.save(second, second_path)
    pygame.quit()

    assert pygame.get_init() is False
    assert VisualCheck._pixel_diff(first_path, second_path) == 1
    assert pygame.get_init() is False
