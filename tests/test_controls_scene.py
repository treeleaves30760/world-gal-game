"""ControlsScene capture, persistence, and responsive-layout contracts."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.game_state import GameState
from world_gal_game.core.localization import Localization
from world_gal_game.npc.npc_base import NPCRegistry
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.controls_scene import ControlsScene
from world_gal_game.scenes.settings_scene import SettingsScene
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputRouter, InputState
from world_gal_game.ui.input_bindings import (
    BindingProfile,
    InputAction,
    rebind_action,
)
from world_gal_game.ui.theme import default_theme


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


def _context(
    size: tuple[int, int],
    tmp_path,
    *,
    bindings: dict[str, list[str]] | None = None,
) -> SceneContext:
    config = EngineConfig(
        screen_size=size,
        writable_root_override=tmp_path,
        input_bindings=bindings or {},
    )
    return SceneContext(
        config=config,
        state=GameState(),
        npcs=NPCRegistry(),
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=AssetManager(pack_root=tmp_path),
        fonts=FontRegistry(
            candidates=config.font_candidates,
            scale=max(1.0, size[1] / 720.0),
        ),
        theme=default_theme(),
        localization=Localization(),
        screen_size=size,
    )


def _controls(
    size: tuple[int, int],
    tmp_path,
    *,
    bindings: dict[str, list[str]] | None = None,
) -> tuple[ControlsScene, list[str]]:
    calls: list[str] = []
    scene = ControlsScene(_context(size, tmp_path, bindings=bindings))
    scene.enter(on_close=lambda: calls.append("close"))
    return scene, calls


def _keydown(*keys: int, actions: tuple[str, ...] = ()) -> InputState:
    return InputState(
        events=[
            pygame.event.Event(
                pygame.KEYDOWN,
                key=key,
                mod=0,
                unicode="",
            )
            for key in keys
        ],
        actions_pressed=set(actions),
        mouse_pos=(-1000, -1000),
    )


def _finger_event(
    event_type: int,
    x: float,
    y: float,
) -> pygame.event.Event:
    return pygame.event.Event(
        event_type,
        x=x,
        y=y,
        dx=0.0,
        dy=0.0,
        finger_id=1,
        touch_id=1,
        pressure=1.0,
    )


@pytest.mark.parametrize(
    "size",
    [(640, 360), (800, 450), (1280, 720), (1920, 1080)],
)
def test_responsive_layout_is_contained_and_has_accessible_targets(
    size: tuple[int, int],
    tmp_path,
) -> None:
    scene, _calls = _controls(size, tmp_path)
    viewport = pygame.Rect((0, 0), size)
    density = max(1.0, size[1] / 720.0)

    assert viewport.contains(scene._panel_rect)
    assert scene._panel_rect.contains(scene._title_rect)
    assert scene._panel_rect.contains(scene.close_btn.rect)
    assert not scene._title_rect.colliderect(scene.close_btn.rect)
    assert scene._header_divider_y < scene._body_rect.top
    assert scene._body_rect.bottom <= scene._status_rect.top
    assert scene._status_rect.bottom <= scene._footer_rect.top
    assert scene._footer_rect.bottom <= scene._panel_rect.bottom
    assert scene._content_column_rect.width <= scene._sp(820)

    logical_body = pygame.Rect(
        0,
        0,
        scene._body_rect.width,
        scene._content_height,
    )
    minimum_target = round(44 * density)
    all_buttons = [
        scene.close_btn,
        *(button for button, _cx, _cy, _control_id in scene._buttons),
    ]
    assert all(
        button.rect.width >= minimum_target
        and button.rect.height >= minimum_target
        for button in all_buttons
    )
    for button, cx, cy, control_id in scene._buttons:
        assert logical_body.contains(
            pygame.Rect((cx, cy), button.rect.size),
        ), control_id
    assert all(
        scene._content_column_rect.contains(region)
        for _name, region in scene._content_regions
    )
    for (previous_name, previous), (next_name, following) in zip(
        scene._content_regions,
        scene._content_regions[1:],
    ):
        assert previous.bottom <= following.top, (
            previous_name,
            previous,
            next_name,
            following,
        )

    surface = pygame.Surface(size, pygame.SRCALPHA)
    scene.draw(surface)
    focused = scene._buttons[scene._focus][0]
    assert focused.focused is True
    assert scene._body_rect.contains(focused.rect)
    assert (
        surface.get_at((focused.rect.centerx, focused.rect.top + 1))[:3]
        == scene.ctx.theme.accent_warm[:3]
    )

    font = scene.ctx.fonts.get(scene._status_font_size, bold=True)
    max_width = scene._status_rect.width - scene._sp(24)
    lines = scene._wrap_text(
        "這是一段沒有空白而且必須安全換行的繁體中文狀態訊息" * 4,
        font,
        max_width,
        max_lines=2,
    )
    assert 1 <= len(lines) <= 2
    assert all(font.size(line)[0] <= max_width for line in lines)


def test_touch_delta_scrolls_clamps_and_never_activates_a_row(
    tmp_path,
) -> None:
    size = (640, 360)
    scene, calls = _controls(size, tmp_path)
    router = InputRouter(touch_mode=True)
    first_button = scene._buttons[scene._focus][0]
    start = first_button.rect.center
    x = start[0] / size[0]
    y = start[1] / size[1]

    down = router.collect(
        [_finger_event(pygame.FINGERDOWN, x, y)],
        window_size=size,
    )
    scene.update(0.0, down)
    motion = router.collect(
        [_finger_event(pygame.FINGERMOTION, x, max(0.0, y - 0.2))],
        window_size=size,
    )
    assert motion.touch_delta[1] < 0
    scene.update(0.0, motion)

    assert 0 < scene._scroll_y <= scene._max_scroll()
    assert scene.capturing is False
    assert scene.ctx.config.input_bindings == {}
    assert calls == []

    release = router.collect(
        [_finger_event(pygame.FINGERUP, x, max(0.0, y - 0.2))],
        window_size=size,
    )
    assert release.mouse_clicked is False
    scene.update(0.0, release)
    assert scene.capturing is False
    assert calls == []

    # The scene still consumes a drag release if a host reports it as a click.
    scene.update(0.0, InputState(
        touch_started=True,
        touch_active=True,
        touch_mode=True,
        mouse_pos=scene._body_rect.center,
    ))
    scene.update(0.0, InputState(
        touch_active=True,
        touch_delta=(0, -100_000),
        touch_mode=True,
        mouse_pos=scene._body_rect.center,
    ))
    assert scene._scroll_y == scene._max_scroll()
    visible_button = next(
        button
        for button, _cx, _cy, _control_id in scene._buttons
        if scene._body_rect.contains(button.rect)
    )
    scene.update(0.0, InputState(
        touch_released=True,
        touch_mode=True,
        mouse_clicked=True,
        mouse_pos=visible_button.rect.center,
    ))
    assert scene.capturing is False

    scene.update(0.0, InputState(
        touch_started=True,
        touch_active=True,
        touch_mode=True,
        mouse_pos=scene._body_rect.center,
    ))
    scene.update(0.0, InputState(
        touch_active=True,
        touch_delta=(0, 100_000),
        touch_mode=True,
        mouse_pos=scene._body_rect.center,
    ))
    assert scene._scroll_y == 0


def test_capture_success_persists_and_preserves_focus_and_scroll(
    tmp_path,
) -> None:
    scene, calls = _controls((640, 360), tmp_path)
    action = InputAction.OPEN_MAP
    target_index = next(
        index
        for index, (_button, _cx, _cy, control_id)
        in enumerate(scene._buttons)
        if control_id == f"action:{action}"
    )
    scene._focus = target_index
    scene._ensure_focus_visible()
    scene._sync_focus_state()
    scene._start_capture(action)
    focus_before = scene._focused_control_id()
    scroll_before = scene._scroll_y
    expected = rebind_action(
        scene.ctx.config.input_bindings,
        action,
        pygame.K_F2,
        resolution="swap",
    )

    scene.update(0.0, _keydown(pygame.K_F2))

    assert expected.success is True
    assert scene.capturing is False
    assert calls == []
    assert scene.ctx.config.input_bindings == expected.overrides
    assert scene._focused_control_id() == focus_before
    assert scene._scroll_y == scroll_before
    assert scene.describe()["status"]["kind"] == "success"
    on_disk = json.loads(
        scene.ctx.config.settings_path().read_text(encoding="utf-8"),
    )
    assert on_disk["input_bindings"] == expected.overrides


@pytest.mark.parametrize(
    ("key", "global_action"),
    [
        (pygame.K_F6, InputAction.QUICKSAVE),
        (pygame.K_F9, InputAction.QUICKLOAD),
        (pygame.K_F12, InputAction.SCREENSHOT),
    ],
)
def test_capture_consumes_frames_that_also_contain_global_actions(
    key: int,
    global_action: str,
    tmp_path,
) -> None:
    scene, calls = _controls((800, 450), tmp_path)
    scene._start_capture(InputAction.OPEN_MAP)
    noisy = _keydown(key, actions=(global_action,))
    noisy.confirm = True

    assert scene.capturing is True
    assert noisy.pressed(global_action) is True
    scene.update(0.0, noisy)

    assert scene.capturing is False
    assert calls == []
    assert BindingProfile(
        scene.ctx.config.input_bindings,
    ).keys_for(InputAction.OPEN_MAP)[0] == key


def test_capture_header_click_cancels_without_closing_or_rebinding(
    tmp_path,
) -> None:
    scene, calls = _controls((800, 450), tmp_path)
    before = dict(scene.ctx.config.input_bindings)
    scene._start_capture(InputAction.OPEN_MAP)
    assert scene.close_btn.label.startswith("取消設定")

    scene.update(0.0, InputState(
        mouse_clicked=True,
        mouse_pos=scene.close_btn.rect.center,
    ))

    assert scene.capturing is False
    assert scene.ctx.config.input_bindings == before
    assert calls == []
    assert scene.describe()["status"]["kind"] == "info"
    assert "取消" in scene.describe()["status"]["text"]


def test_capture_touch_on_header_cancels_without_closing_or_rebinding(
    tmp_path,
) -> None:
    size = (800, 450)
    scene, calls = _controls(size, tmp_path)
    before = dict(scene.ctx.config.input_bindings)
    scene._start_capture(InputAction.OPEN_MAP)
    router = InputRouter(touch_mode=True)
    center = scene.close_btn.rect.center
    x = center[0] / size[0]
    y = center[1] / size[1]

    down = router.collect(
        [_finger_event(pygame.FINGERDOWN, x, y)],
        window_size=size,
    )
    scene.update(0.0, down)
    assert scene.capturing is True

    release = router.collect(
        [_finger_event(pygame.FINGERUP, x, y)],
        window_size=size,
    )
    assert release.mouse_clicked is True
    scene.update(0.0, release)

    assert scene.capturing is False
    assert scene.ctx.config.input_bindings == before
    assert calls == []


def test_capture_gamepad_back_cancels_without_a_physical_key_or_pop(
    tmp_path,
) -> None:
    scene, calls = _controls((800, 450), tmp_path)
    before = dict(scene.ctx.config.input_bindings)
    scene._start_capture(InputAction.OPEN_MAP)
    inp = InputRouter().collect([
        pygame.event.Event(
            pygame.JOYBUTTONDOWN,
            instance_id=0,
            button=1,
        ),
    ])
    assert inp.pressed(InputAction.BACK) is True
    assert inp.physical_keys_down == set()

    scene.update(0.0, inp)

    assert scene.capturing is False
    assert scene.ctx.config.input_bindings == before
    assert calls == []


def test_reserved_multiple_and_unsafe_conflicts_stay_in_capture(
    tmp_path,
) -> None:
    scene, calls = _controls((800, 450), tmp_path)
    initial = dict(scene.ctx.config.input_bindings)

    scene._start_capture(InputAction.OPEN_MAP)
    scene.update(0.0, _keydown(pygame.K_F1))
    assert scene.capturing is True
    assert scene.ctx.config.input_bindings == initial
    assert "系統保留鍵" in scene.describe()["status"]["text"]
    assert not scene.describe()["status"]["text"].startswith("設定失敗")

    scene.update(0.0, _keydown(pygame.K_q, pygame.K_p))
    assert scene.capturing is True
    assert scene.ctx.config.input_bindings == initial
    assert "一次只能按一個" in scene.describe()["status"]["text"]

    scene._start_capture(InputAction.HIDE_UI)
    scene.update(0.0, _keydown(pygame.K_a))
    assert scene.capturing is True
    assert scene.ctx.config.input_bindings == initial
    assert "同時與多項操作衝突" in scene.describe()["status"]["text"]

    cancel = _keydown(
        pygame.K_ESCAPE,
        actions=(InputAction.CONFIRM_ADVANCE,),
    )
    cancel.mouse_clicked = True
    cancel.mouse_pos = scene.close_btn.rect.center
    scene.update(0.0, cancel)
    assert scene.capturing is False
    assert calls == []
    assert scene.ctx.config.input_bindings == initial


def test_same_context_conflict_swaps_and_cross_context_reuse_is_legal(
    tmp_path,
) -> None:
    scene, _calls = _controls((800, 450), tmp_path)

    scene._start_capture(InputAction.NAV_UP)
    scene.update(0.0, _keydown(pygame.K_DOWN))
    profile = BindingProfile(scene.ctx.config.input_bindings)
    assert profile.keys_for(InputAction.NAV_UP)[0] == pygame.K_DOWN
    assert profile.keys_for(InputAction.NAV_DOWN)[0] == pygame.K_UP
    status = scene.describe()["status"]["text"]
    assert "交換" in status
    assert profile.primary_display(InputAction.NAV_UP) in status
    assert "down" not in status.lower()

    scene._start_capture(InputAction.OPEN_MAP)
    scene.update(0.0, _keydown(pygame.K_w))
    profile = BindingProfile(scene.ctx.config.input_bindings)
    assert profile.keys_for(InputAction.OPEN_MAP)[0] == pygame.K_w
    assert pygame.K_w in profile.keys_for(InputAction.NAV_UP)


def test_360p_focus_scroll_keeps_the_complete_action_row_visible(
    tmp_path,
) -> None:
    scene, _calls = _controls((640, 360), tmp_path)

    scene.update(
        0.0,
        _keydown(actions=(InputAction.NAV_UP,)),
    )
    scene.update(
        0.0,
        _keydown(actions=(InputAction.NAV_UP,)),
    )

    control_id = scene._focused_control_id()
    assert control_id is not None
    content_region = dict(scene._content_regions)[control_id]
    screen_region = content_region.move(
        scene._body_rect.x,
        scene._body_rect.y - scene._scroll_y,
    )
    assert scene._body_rect.contains(screen_region), (
        control_id,
        screen_region,
        scene._body_rect,
    )
    assert scene._body_rect.contains(scene._buttons[scene._focus][0].rect)


def test_reset_is_two_stage_and_required_rescue_bindings_remain(
    tmp_path,
) -> None:
    custom = rebind_action(
        {},
        InputAction.OPEN_MAP,
        pygame.K_F2,
    ).overrides
    scene, _calls = _controls(
        (800, 450),
        tmp_path,
        bindings=custom,
    )

    scene._request_reset()
    assert scene.describe()["reset_pending"] is True
    assert scene.ctx.config.input_bindings == custom
    assert "再次" in scene.describe()["status"]["text"]

    scene._request_reset()
    assert scene.describe()["reset_pending"] is False
    assert scene.ctx.config.input_bindings == {}
    profile = BindingProfile(scene.ctx.config.input_bindings)
    assert profile.keys_for(InputAction.CONFIRM_ADVANCE)
    assert profile.keys_for(InputAction.BACK)
    saved = json.loads(
        scene.ctx.config.settings_path().read_text(encoding="utf-8"),
    )
    assert saved["input_bindings"] == {}


def test_semantic_navigation_and_dynamic_hints_follow_rebinding(
    tmp_path,
) -> None:
    bindings = {
        InputAction.NAV_UP: ["p"],
        InputAction.NAV_DOWN: ["o"],
        InputAction.CONFIRM_ADVANCE: ["c"],
        InputAction.BACK: ["v"],
    }
    scene, _calls = _controls(
        (800, 450),
        tmp_path,
        bindings=bindings,
    )
    footer = scene.describe()["footer"]
    assert all(key in footer for key in ("P", "O", "C", "V"))
    assert "V" in (scene.close_btn.tooltip or "")

    start_focus = scene._focus
    raw_w_only = SimpleNamespace(
        pressed=lambda _action: False,
        nav=0,
        events=[
            pygame.event.Event(
                pygame.KEYDOWN,
                key=pygame.K_w,
                mod=0,
                unicode="w",
            ),
        ],
        cancel=False,
        confirm=False,
        mouse_pos=(-1000, -1000),
        mouse_wheel=0,
        mouse_clicked=False,
    )
    scene.update(0.0, raw_w_only)
    assert scene._focus == start_focus

    semantic_down = SimpleNamespace(
        pressed=lambda action: action == InputAction.NAV_DOWN,
        nav=0,
        events=[],
        cancel=False,
        confirm=False,
        mouse_pos=(-1000, -1000),
        mouse_wheel=0,
        mouse_clicked=False,
    )
    scene.update(0.0, semantic_down)
    assert scene._focus == start_focus + 1
    json.dumps(scene.describe(), ensure_ascii=False)


def test_settings_exposes_controls_entry_and_live_summary(tmp_path) -> None:
    calls: list[str] = []
    settings = SettingsScene(_context((800, 450), tmp_path))
    settings.enter(
        on_close=lambda: calls.append("close"),
        on_controls=lambda: calls.append("controls"),
    )
    settings._set_category("controls_display")
    controls_button = next(
        button
        for button, _cx, _cy in settings._buttons
        if button.label == "操作設定"
    )
    assert controls_button.enabled is True
    assert controls_button.activate() is True
    assert calls == ["controls"]
    assert not any(
        "Space / Enter / Z" in label[0]
        or "F6 / F9" in label[0]
        for label in settings._labels
    )

    settings.ctx.config.input_bindings = {
        InputAction.BACK: ["v"],
    }
    assert settings._controls_summary() == "已自訂 1 項操作"
    settings.resume()
    assert "V" in (settings.close_btn.tooltip or "")
    assert "V" in settings.describe()["footer"]
    rebuilt_controls = next(
        button
        for button, _cx, _cy in settings._buttons
        if button.label == "操作設定"
    )
    assert rebuilt_controls.activate() is True
    assert calls == ["controls", "controls"]
