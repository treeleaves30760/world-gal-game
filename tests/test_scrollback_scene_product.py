"""Product contracts for the virtualized dialogue-history overlay."""
from __future__ import annotations

import math
import os
from collections.abc import Mapping

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.game_state import GameState
from world_gal_game.core.localization import Localization
from world_gal_game.npc.npc_base import NPC, NPCRegistry
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.scrollback_scene import ScrollbackScene
from world_gal_game.ui.assets import AudioPlaybackSnapshot
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.input_bindings import InputAction
from world_gal_game.ui.system_chrome import ChromeRequest
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.presentation_text import PresentationTextPolicy
from world_gal_game.ui.widgets.scrollable import ScrollArea


class RecordingAssets:
    def __init__(self) -> None:
        self.voice_calls: list[tuple[str | None, float]] = []
        self.capture_calls = 0
        self.restore_calls: list[AudioPlaybackSnapshot] = []
        self.snapshot = AudioPlaybackSnapshot(
            music="bgm/live.ogg",
            music_volume=0.6,
            ambient="ambient/live.ogg",
            ambient_volume=0.4,
            voice="voice/live.ogg",
            voice_volume=0.8,
            voice_was_busy=True,
        )

    def capture_audio_playback(self) -> AudioPlaybackSnapshot:
        self.capture_calls += 1
        return self.snapshot

    def restore_audio_playback(self, snapshot: AudioPlaybackSnapshot) -> None:
        self.restore_calls.append(snapshot)

    def play_voice(self, path: str | None, *, volume: float = 1.0) -> None:
        self.voice_calls.append((path, volume))


class RecordingChrome:
    def __init__(self) -> None:
        self.requests: list[ChromeRequest] = []

    def paint(
        self,
        _surface: pygame.Surface,
        _rect: pygame.Rect,
        request: ChromeRequest,
    ) -> bool:
        self.requests.append(request)
        return False


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


def _history_line(
    index: int,
    *,
    speaker: str | None = "測試角色",
    speaker_id: str | None = None,
    voice: str | None = None,
    text: str | None = None,
    sound_captions: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    line: dict[str, object] = {
        "speaker": speaker,
        "text": text or f"第 {index} 句對話內容",
        "scene_id": "history_probe",
        "portrait": None,
        "voice": voice,
    }
    if speaker_id is not None:
        line["speaker_id"] = speaker_id
    if sound_captions is not None:
        line["sound_captions"] = sound_captions
    return line


def _make_scene(
    history: list[dict[str, object]],
    *,
    size: tuple[int, int] = (1280, 720),
    npcs: NPCRegistry | None = None,
    reduce_motion: bool = False,
    voice_volume: float = 0.8,
    text_scale: float = 1.0,
    show_sound_captions: bool = True,
    per_character_voice_volume: Mapping[str, float] | None = None,
    chrome: RecordingChrome | None = None,
    presentation_text: PresentationTextPolicy | None = None,
) -> tuple[ScrollbackScene, RecordingAssets, list[str]]:
    config = EngineConfig(
        screen_size=size,
        reduce_motion=reduce_motion,
        voice_volume=voice_volume,
        text_scale=text_scale,
        show_sound_captions=show_sound_captions,
        per_character_voice_volume=dict(
            per_character_voice_volume or {},
        ),
    )
    state = GameState()
    state.dialogue_history.lines = list(history)
    assets = RecordingAssets()
    ctx = SceneContext(
        config=config,
        state=state,
        npcs=npcs or NPCRegistry(),
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=assets,  # type: ignore[arg-type]
        fonts=FontRegistry(
            candidates=(),
            scale=max(1.0, size[1] / 720.0),
        ),
        theme=default_theme(),
        localization=Localization(),
        screen_size=size,
        ui_chrome=chrome,  # type: ignore[arg-type]
        presentation_text=(
            presentation_text
            if presentation_text is not None
            else PresentationTextPolicy()
        ),
    )
    closed: list[str] = []
    scene = ScrollbackScene(ctx)
    scene.enter(on_close=lambda: closed.append("closed"))
    return scene, assets, closed


def _key(key: int) -> InputState:
    return InputState(
        events=[pygame.event.Event(
            pygame.KEYDOWN,
            key=key,
            mod=0,
            unicode="",
        )],
        mouse_pos=(-1000, -1000),
    )


def _hat(x: int, y: int) -> InputState:
    return InputState(
        events=[pygame.event.Event(
            pygame.JOYHATMOTION,
            joy=0,
            instance_id=0,
            hat=0,
            value=(x, y),
        )],
        mouse_pos=(-1000, -1000),
    )


def _gamepad_button(button: int) -> InputState:
    return InputState(
        events=[pygame.event.Event(
            pygame.JOYBUTTONDOWN,
            joy=0,
            instance_id=0,
            button=button,
        )],
        mouse_pos=(-1000, -1000),
    )


def test_500_row_history_virtualizes_without_a_giant_scrollarea_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    size = (1280, 720)
    history = [_history_line(index) for index in range(500)]
    scene, _assets, _closed = _make_scene(history, size=size)

    original_ensure_surface = ScrollArea._ensure_surface

    def bounded_surface(
        scroll: ScrollArea,
        required_height: int,
    ) -> pygame.Surface:
        assert required_height <= size[1], (
            "dialogue history attempted to allocate a full-content "
            f"ScrollArea surface ({required_height}px high)"
        )
        result = original_ensure_surface(scroll, required_height)
        assert result.get_height() <= size[1]
        return result

    monkeypatch.setattr(ScrollArea, "_ensure_surface", bounded_surface)

    target = pygame.Surface(size, pygame.SRCALPHA)
    scene.draw(target)
    description = scene.describe()
    first, last = description["visible_range"]
    minimum_row_height = min(row.height for row in scene._rows)
    maximum_viewport_rows = (
        math.ceil(scene._viewport.height / minimum_row_height) + 4
    )

    assert len(scene._rows) == 500
    assert scene._content_height > size[1] * 20
    assert description["drawn_rows"] == last - first
    assert 0 < description["drawn_rows"] <= maximum_viewport_rows
    assert description["drawn_rows"] < len(scene._rows) // 10
    for value in vars(scene).values():
        if isinstance(value, pygame.Surface):
            assert value.get_width() <= size[0]
            assert value.get_height() <= size[1]
        if isinstance(value, ScrollArea) and value.surface is not None:
            assert value.surface.get_height() <= size[1]


@pytest.mark.parametrize(
    "size",
    [
        (640, 360),
        (800, 450),
        (1280, 720),
        (1920, 1080),
        (3840, 2160),
    ],
    ids=["360p", "450p", "720p", "1080p", "4k"],
)
def test_geometry_stays_contained_at_supported_resolutions(
    size: tuple[int, int],
) -> None:
    long_text = "校園裡的風景與剛才的談話仍清楚留在記憶裡。" * 8
    scene, _assets, _closed = _make_scene([
        _history_line(index, text=long_text)
        for index in range(4)
    ], size=size)
    canvas = pygame.Rect((0, 0), size)

    assert canvas.contains(scene._panel_rect)
    assert scene._panel_rect.contains(scene._header_rect)
    assert scene._panel_rect.contains(scene._viewport)
    assert scene._panel_rect.contains(scene._footer_rect)
    assert scene._header_rect.contains(scene._close_rect)
    assert scene._header_rect.contains(scene._title_rect)
    assert not scene._title_rect.colliderect(scene._close_rect)
    assert scene._header_rect.bottom <= scene._viewport.top
    assert scene._viewport.bottom <= scene._footer_rect.top
    assert scene._viewport.width > scene._panel_rect.width // 2
    assert scene._viewport.height > size[1] // 3
    assert scene._close_rect.width >= 44
    assert scene._close_rect.height >= 44
    assert all(row.height >= max(44, scene._sp(44)) for row in scene._rows)
    latest_rect = scene._row_screen_rect(scene._rows[-1])
    assert latest_rect.right <= scene._viewport.right
    assert latest_rect.bottom <= scene._viewport.bottom


def test_dialogue_text_scale_reflows_rows_without_breaking_the_viewport() -> None:
    history = [
        _history_line(
            index,
            text="放大後仍應保持完整換行與可捲動的閱讀區域。" * 5,
        )
        for index in range(12)
    ]
    regular, _assets, _closed = _make_scene(history, text_scale=1.0)
    enlarged, _assets, _closed = _make_scene(history, text_scale=1.4)

    assert enlarged._body_size > regular._body_size
    assert enlarged._rows[0].height > regular._rows[0].height
    assert enlarged._content_height > regular._content_height
    assert enlarged._panel_rect.contains(enlarged._viewport)
    assert enlarged._row_screen_rect(enlarged._rows[-1]).colliderect(
        enlarged._viewport,
    )


def test_captioned_and_legacy_history_rows_reflow_safely() -> None:
    captioned = _history_line(
        1,
        sound_captions=[
            {"kind": "ambient", "text": "雨聲沿著屋簷落下" * 18},
            {"kind": "sfx", "text": "doorclosingwithoutspaces" * 8},
        ],
    )
    scene, _assets, _closed = _make_scene([
        _history_line(0),
        captioned,
        {"speaker": None, "text": "舊版本紀錄"},
    ], size=(640, 360), text_scale=1.4)

    legacy, with_captions, old_shape = scene._rows
    assert legacy.sound_captions == ()
    assert old_shape.sound_captions == ()
    assert [cue.kind for cue in with_captions.sound_captions] == [
        "ambient",
        "sfx",
    ]
    assert with_captions.caption_lines
    assert with_captions.height > legacy.height
    font = scene.ctx.fonts.get(scene._caption_size, bold=True)
    available = scene._viewport.width - scene._row_pad_x * 2
    assert all(
        font.size(line)[0] <= available
        for lines in with_captions.caption_lines
        for line in lines
    )
    scene.draw(pygame.Surface(scene.ctx.screen_size, pygame.SRCALPHA))
    described = scene.describe()
    assert described["sound_captions_enabled"] is True
    assert described["captioned_rows"] == 1
    assert described["visible_caption_rows"] == [{
        "index": 1,
        "captions": [
            {"kind": "ambient", "text": "雨聲沿著屋簷落下" * 18},
            {"kind": "sfx", "text": "doorclosingwithoutspaces" * 8},
        ],
    }]
    assert described["visible_caption_rows_truncated"] is False
    assert described["focused_sound_captions"] == []
    scene._focus = 1
    scene._focus_close = False
    assert scene.describe()["focused_sound_captions"] == [
        {"kind": "ambient", "text": "雨聲沿著屋簷落下" * 18},
        {"kind": "sfx", "text": "doorclosingwithoutspaces" * 8},
    ]

    hidden, _assets, _closed = _make_scene(
        [captioned],
        show_sound_captions=False,
    )
    assert hidden._rows[0].sound_captions == ()
    assert hidden._rows[0].caption_lines == ()
    hidden.draw(pygame.Surface(hidden.ctx.screen_size, pygame.SRCALPHA))
    hidden_description = hidden.describe()
    assert hidden_description["sound_captions_enabled"] is False
    assert hidden_description["captioned_rows"] == 0
    assert hidden_description["visible_caption_rows"] == []
    assert hidden_description["focused_sound_captions"] == []


def test_500_captioned_rows_remain_virtualized() -> None:
    scene, _assets, _closed = _make_scene([
        _history_line(
            index,
            sound_captions=[{
                "kind": "sfx",
                "text": f"第 {index} 句的環境音提示",
            }],
        )
        for index in range(500)
    ])
    surface = pygame.Surface(scene.ctx.screen_size, pygame.SRCALPHA)

    scene.draw(surface)

    description = scene.describe()
    assert len(scene._rows) == 500
    assert all(len(row.sound_captions) == 1 for row in scene._rows)
    assert 0 < description["drawn_rows"] < 50
    assert description["visible_range"][1] == 500
    assert description["captioned_rows"] == 500
    assert 0 < len(description["visible_caption_rows"]) <= 32
    assert all(
        len(row["captions"]) == 1
        for row in description["visible_caption_rows"]
    )
    assert description["visible_caption_rows_truncated"] is False
    assert description["focused_sound_captions"] == [{
        "kind": "sfx",
        "text": "第 499 句的環境音提示",
    }]

    # Even an embedding client with an unusually tall viewport cannot make the
    # headless summary dump the complete 500-row history.
    scene._scroll_y = 0
    scene._viewport.height = scene._content_height
    scene._last_visible_range = (0, len(scene._rows))
    bounded = scene.describe()
    assert len(bounded["visible_caption_rows"]) == 32
    assert bounded["visible_caption_rows_truncated"] is True


def test_history_emits_bounded_semantic_chrome_requests() -> None:
    chrome = RecordingChrome()
    scene, _assets, _closed = _make_scene(
        [_history_line(index, voice="voice/probe.ogg") for index in range(80)],
        chrome=chrome,
    )
    scene.draw(pygame.Surface(scene.ctx.screen_size, pygame.SRCALPHA))

    requests = chrome.requests
    assert requests
    assert all(request.screen == "history" for request in requests)
    assert {request.part for request in requests} >= {
        "backdrop",
        "panel",
        "card",
        "focus",
        "footer",
    }
    card_requests = [request for request in requests if request.part == "card"]
    # One overscan row may be measured/drawn but clipped before it emits a
    # chrome request.  The renderer still receives only on-screen cards.
    assert 0 < len(card_requests) <= scene.describe()["drawn_rows"]
    assert len(card_requests) < len(scene._rows) // 2
    assert all(
        width <= scene._viewport.width and height <= scene._viewport.height
        for width, height in (request.size for request in card_requests)
    )


def test_initial_focus_and_scroll_position_land_on_latest_line() -> None:
    scene, _assets, _closed = _make_scene([
        _history_line(index)
        for index in range(80)
    ])

    assert scene._focus == 79
    assert scene._focus_close is False
    assert scene.close_btn.focused is False
    assert scene._scroll_y == scene._max_scroll()

    scene.draw(pygame.Surface(scene.ctx.screen_size, pygame.SRCALPHA))
    description = scene.describe()
    assert description["focused_index"] == 79
    assert description["visible_range"][1] == 80
    assert scene._row_screen_rect(scene._rows[79]).colliderect(
        scene._viewport,
    )


def test_initial_bottom_scroll_never_bisects_the_top_text_band() -> None:
    # Deliberately reproduce a mixed-height history where the natural
    # bottom-aligned viewport begins inside a speaker label.  The final
    # two-line entry perturbs an otherwise regular list just enough to exercise
    # the real defect without depending on a bundled font, pack-authored text,
    # or a hard-coded pixel offset.
    scene, _assets, _closed = _make_scene([
        _history_line(
            index,
            text="一\n二" if index == 9 else "一",
        )
        for index in range(10)
    ])
    natural_bottom = max(
        0,
        scene._content_height - scene._viewport.height,
    )
    natural_cuts = [
        (top, bottom)
        for row in scene._rows
        for top, bottom in scene._row_text_bands(row)
        if top < natural_bottom < bottom
    ]

    assert natural_cuts, "fixture must reproduce a bisected text band"
    assert scene._scroll_y > natural_bottom
    assert scene._scroll_y - natural_bottom <= max(
        scene._speaker_line_h,
        scene._body_line_h,
        scene._caption_line_h,
    )
    assert not any(
        top < scene._scroll_y < bottom
        for row in scene._rows
        for top, bottom in scene._row_text_bands(row)
    )
    latest_rect = scene._row_screen_rect(scene._rows[-1])
    assert latest_rect.bottom <= scene._viewport.bottom


def test_keyboard_and_gamepad_semantics_navigate_and_confirm_voice_replay(
) -> None:
    history = [
        _history_line(index, voice=f"voice/{index}.ogg")
        for index in range(3)
    ]
    scene, assets, _closed = _make_scene(history, voice_volume=0.64)

    scene.update(0.016, _key(pygame.K_UP))
    assert scene.describe()["focused_index"] == 1
    scene.update(0.016, _key(pygame.K_RETURN))
    assert assets.voice_calls == [("voice/1.ogg", pytest.approx(0.64))]

    scene.update(0.016, _hat(0, 1))
    assert scene.describe()["focused_index"] == 0
    scene.update(0.016, _gamepad_button(0))
    assert assets.voice_calls == [
        ("voice/1.ogg", pytest.approx(0.64)),
        ("voice/0.ogg", pytest.approx(0.64)),
    ]
    assert scene.describe()["last_replay"] == {
        "index": 0,
        "speaker": "測試角色",
        "speaker_id": None,
        "voice": "voice/0.ogg",
        "volume": pytest.approx(0.64),
    }


def test_voice_replay_restores_entry_audio_before_close_callback() -> None:
    history = [_history_line(0, voice="voice/history.ogg")]
    scene, assets, closed = _make_scene(history)

    assert assets.capture_calls == 1
    scene.update(0.016, _key(pygame.K_RETURN))
    assert assets.voice_calls == [
        ("voice/history.ogg", pytest.approx(0.8)),
    ]

    scene._close()

    assert assets.restore_calls == [assets.snapshot]
    assert closed == ["closed"]
    # SceneManager calls exit after the close callback queues a pop.  That
    # second teardown must not restart/stop authored audio again.
    scene.exit()
    assert assets.restore_calls == [assets.snapshot]


def test_first_replay_does_not_revive_voice_that_finished_in_scrollback(
) -> None:
    scene, assets, _closed = _make_scene([
        _history_line(0, voice="voice/history.ogg"),
    ])
    stale_entry_snapshot = assets.snapshot
    completed_checkpoint = {
        "version": 2,
        "state": "completed",
        "position_seconds": 3.0,
        "duration_seconds": 3.0,
        "pcm_sha256": "1" * 64,
        "mixer_format": [48_000, -16, 2],
        "fallback": "silence",
    }
    # Model the underlying dialogue voice naturally becoming idle while the
    # player is still reading history.  Its revision has not changed, but the
    # latest logical checkpoint is completed rather than playing.
    assets.snapshot = AudioPlaybackSnapshot(
        music=stale_entry_snapshot.music,
        music_volume=stale_entry_snapshot.music_volume,
        ambient=stale_entry_snapshot.ambient,
        ambient_volume=stale_entry_snapshot.ambient_volume,
        voice=stale_entry_snapshot.voice,
        voice_volume=stale_entry_snapshot.voice_volume,
        voice_was_busy=False,
        music_revision=stale_entry_snapshot.music_revision,
        ambient_revision=stale_entry_snapshot.ambient_revision,
        voice_revision=stale_entry_snapshot.voice_revision,
        voice_playback=completed_checkpoint,
        voice_checkpoint_captured=True,
    )

    scene.update(0.016, _key(pygame.K_RETURN))
    assert assets.capture_calls == 2
    assert assets.voice_calls == [
        ("voice/history.ogg", pytest.approx(0.8)),
    ]

    scene.exit()

    assert assets.restore_calls == [assets.snapshot]
    restored = assets.restore_calls[0]
    assert restored is not stale_entry_snapshot
    assert restored.voice_was_busy is False
    assert restored.voice_playback == completed_checkpoint


def test_failed_first_preview_refreshes_again_before_later_success() -> None:
    scene, assets, _closed = _make_scene([
        _history_line(0, voice="voice/missing.ogg"),
        _history_line(1, voice="voice/valid.ogg"),
    ])
    handles = iter([None, object()])
    assets.current_voice_handle = None

    def _play(path: str | None, *, volume: float = 1.0):
        assets.voice_calls.append((path, volume))
        handle = next(handles)
        assets.current_voice_handle = handle
        return handle

    assets.play_voice = _play

    assert scene._replay(0) is False
    assert scene._audio_preview_started is False
    assert assets.capture_calls == 2

    assert scene._replay(1) is True
    assert scene._audio_preview_started is True
    assert assets.capture_calls == 3


def test_direct_exit_restores_audio_even_without_voice_replay() -> None:
    scene, assets, closed = _make_scene([_history_line(0)])

    scene.exit()

    assert assets.restore_calls == [assets.snapshot]
    assert closed == []


def test_pointer_activation_focuses_row_and_replays_its_voice() -> None:
    scene, assets, _closed = _make_scene([
        _history_line(0, voice="voice/first.ogg"),
        _history_line(1, voice="voice/latest.ogg"),
    ])
    row_rect = scene._row_screen_rect(scene._rows[0]).clip(scene._viewport)
    assert row_rect.width > 0 and row_rect.height > 0

    scene.update(0.016, InputState(
        mouse_pos=row_rect.center,
        mouse_clicked=True,
    ))

    assert scene.describe()["focused_index"] == 0
    assert assets.voice_calls == [("voice/first.ogg", pytest.approx(0.8))]
    assert scene.describe()["last_replay"]["index"] == 0


def test_legacy_speaker_name_resolves_per_character_voice_volume() -> None:
    registry = NPCRegistry()
    registry.add(NPC(id="heroine_yuki", name="白石由紀"))
    legacy_line = _history_line(
        0,
        speaker="白石由紀",
        voice="voice/yuki_legacy.ogg",
    )
    assert "speaker_id" not in legacy_line
    scene, assets, _closed = _make_scene(
        [legacy_line],
        npcs=registry,
        voice_volume=0.9,
        per_character_voice_volume={"heroine_yuki": 0.27},
    )

    scene.update(0.016, _key(pygame.K_RETURN))

    assert assets.voice_calls == [
        ("voice/yuki_legacy.ogg", pytest.approx(0.27)),
    ]
    assert scene.describe()["last_replay"]["speaker_id"] == "heroine_yuki"
    assert scene.describe()["last_replay"]["volume"] == pytest.approx(0.27)


def test_empty_history_draws_and_places_focus_on_close() -> None:
    scene, assets, closed = _make_scene([])
    target = pygame.Surface(scene.ctx.screen_size, pygame.SRCALPHA)

    scene.draw(target)

    assert scene.describe() == {
        "scene": "ScrollbackScene",
        "lines": 0,
        "replayable": 0,
        "sound_captions_enabled": True,
        "captioned_rows": 0,
        "visible_caption_rows": [],
        "visible_caption_rows_truncated": False,
        "focused_sound_captions": [],
        "focused_index": None,
        "focus": "close",
        "visible_range": [0, 0],
        "drawn_rows": 0,
        "content_height": 0,
        "viewport_height": scene._viewport.height,
        "scroll_y": 0,
        "last_replay": None,
    }
    assert scene.close_btn.focused is True
    assert pygame.mask.from_surface(target).count() > 0
    assert assets.voice_calls == []

    for action in (InputAction.FOCUS_NEXT, InputAction.FOCUS_PREVIOUS):
        scene.update(0.016, InputState(
            actions_pressed={action},
            mouse_pos=(-1000, -1000),
        ))
        assert scene.describe()["focus"] == "close"
        assert scene.describe()["focused_index"] is None

    scene.update(0.016, _key(pygame.K_RETURN))
    assert closed == ["closed"]


def test_reduced_motion_exposes_latest_focus_on_first_draw() -> None:
    chrome = RecordingChrome()
    scene, _assets, _closed = _make_scene(
        [
            _history_line(0, voice="voice/old.ogg"),
            _history_line(1, voice="voice/latest.ogg"),
        ],
        reduce_motion=True,
        chrome=chrome,
    )

    assert scene.ctx.motion.reduced is True
    assert scene.describe()["focused_index"] == 1
    scene.draw(pygame.Surface(scene.ctx.screen_size, pygame.SRCALPHA))

    focus_requests = [
        request
        for request in chrome.requests
        if request.part == "focus"
    ]
    assert focus_requests
    assert all(request.focused for request in focus_requests)
    assert all(request.selected for request in focus_requests)
    assert all(request.emphasis == 3 for request in focus_requests)
    assert scene._row_screen_rect(scene._rows[1]).colliderect(
        scene._viewport,
    )


def test_describe_reports_the_drawn_visible_range_after_scrolling() -> None:
    scene, _assets, _closed = _make_scene([
        _history_line(index)
        for index in range(120)
    ])
    target = pygame.Surface(scene.ctx.screen_size, pygame.SRCALPHA)

    scene.draw(target)
    before = scene.describe()
    first, last = before["visible_range"]
    actually_visible = [
        row.index
        for row in scene._rows
        if scene._row_screen_rect(row).colliderect(scene._viewport)
    ]

    assert actually_visible
    assert before["drawn_rows"] == last - first
    assert set(actually_visible).issubset(range(first, last))
    assert first >= max(0, min(actually_visible) - 1)
    assert last <= min(len(scene._rows), max(actually_visible) + 2)

    scene.update(0.016, InputState(
        actions_pressed={InputAction.PAGE_UP},
        mouse_pos=(-1000, -1000),
    ))
    scene.draw(target)
    after = scene.describe()

    assert after["scroll_y"] < before["scroll_y"]
    assert after["visible_range"] != before["visible_range"]
    assert after["drawn_rows"] == (
        after["visible_range"][1] - after["visible_range"][0]
    )


def test_scrollback_expands_final_draw_copy_without_mutating_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _history_line(
        0,
        speaker="Canonical Speaker",
        speaker_id="heroine_1",
        text="Canonical backlog copy",
        sound_captions=[
            {"kind": "ambient", "text": "Pages rustle"},
        ],
    )
    policy = PresentationTextPolicy.pseudo_expand(1.35)
    scene, _assets, _closed = _make_scene(
        [source],
        size=(1920, 1080),
        presentation_text=policy,
    )
    row = scene._rows[0]

    assert row.speaker == source["speaker"]
    assert row.speaker_id == source["speaker_id"]
    assert row.sound_captions[0].text == "Pages rustle"
    assert row.text_lines == (policy.apply("Canonical backlog copy"),)
    caption_display = "［環境音］ Pages rustle"
    assert row.caption_lines == ((policy.apply(caption_display),),)
    assert scene.ctx.state.dialogue_history.lines == [source]

    rendered: list[str] = []
    original_render = scene.ctx.fonts.render

    def record_render(text: str, *args, **kwargs):
        rendered.append(text)
        return original_render(text, *args, **kwargs)

    monkeypatch.setattr(scene.ctx.fonts, "render", record_render)
    scene.draw(pygame.Surface(scene.ctx.screen_size, pygame.SRCALPHA))

    assert policy.apply("Canonical Speaker") in rendered
    assert scene.ctx.state.dialogue_history.lines == [source]
