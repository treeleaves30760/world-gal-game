"""Responsive layout, focus, and persistence contracts for SettingsScene."""

from __future__ import annotations

import json
import math
import os
from itertools import combinations

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.game_state import GameState
from world_gal_game.core.localization import Localization
from world_gal_game.npc.npc_base import NPC, NPCRegistry
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.settings_scene import SettingsScene
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputRouter, InputState
from world_gal_game.ui.input_bindings import InputAction
from world_gal_game.ui.theme import default_theme


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


class _SettingsLocalization:
    """Small Settings-facing locale double with real manifest-shaped options."""

    def __init__(
        self,
        messages: dict[str, str],
        *,
        options: tuple[dict[str, str], ...],
        effective: str = "en",
    ) -> None:
        self.messages = messages
        self.options = options
        self.effective = effective

    def t(self, key: str, default: str | None = None, **fmt) -> str:
        template = self.messages.get(
            key,
            default if default is not None else key,
        )
        return template.format(**fmt)


def _make_scene(
    size: tuple[int, int],
    tmp_path,
    *,
    localization: Localization | _SettingsLocalization | None = None,
    locale_change_allowed: bool = False,
) -> tuple[SettingsScene, list[str]]:
    config = EngineConfig(
        screen_size=size,
        writable_root_override=tmp_path,
    )
    npcs = NPCRegistry()
    for index, name in enumerate(("白若雪", "蘇雨晴", "白石由紀")):
        npcs.add(NPC(id=f"heroine_{index}", name=name))
    fonts = FontRegistry(
        candidates=config.font_candidates,
        scale=max(1.0, size[1] / 720.0),
    )
    ctx = SceneContext(
        config=config,
        state=GameState(),
        npcs=npcs,
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=AssetManager(pack_root=tmp_path),
        fonts=fonts,
        theme=default_theme(),
        localization=localization or Localization(),
        screen_size=size,
    )
    calls: list[str] = []
    scene = SettingsScene(ctx)
    scene.enter(
        on_close=lambda: calls.append("close"),
        on_controls=lambda: calls.append("controls"),
        on_locale_change=lambda locale: calls.append(f"locale:{locale}"),
        locale_change_allowed=locale_change_allowed,
    )
    return scene, calls


def _key(key: int) -> InputState:
    return InputState(
        events=[
            pygame.event.Event(
                pygame.KEYDOWN,
                key=key,
                mod=0,
                unicode="",
            )
        ],
        confirm=key
        in (
            pygame.K_RETURN,
            pygame.K_KP_ENTER,
            pygame.K_SPACE,
            pygame.K_z,
        ),
        cancel=key in (pygame.K_ESCAPE, pygame.K_x),
        mouse_pos=(-1000, -1000),
    )


def _semantic(action: str) -> InputState:
    """Build one input frame containing only the requested semantic action."""

    return InputState(
        actions_pressed={action},
        mouse_pos=(-1000, -1000),
    )


@pytest.mark.parametrize(
    "size",
    [(1280, 720), (1920, 1080), (800, 450), (640, 360), (375, 667)],
)
def test_density_layout_uses_live_lines_and_stays_contained(
    size: tuple[int, int],
    tmp_path,
) -> None:
    scene, _calls = _make_scene(size, tmp_path)
    density = max(1.0, size[1] / 720.0)
    viewport = pygame.Rect((0, 0), size)

    assert viewport.contains(scene._panel_rect)
    assert scene._panel_rect.contains(scene._title_rect)
    assert scene._panel_rect.contains(scene.close_btn.rect)
    assert scene._panel_rect.contains(scene._tabs_rect)
    assert scene._panel_rect.contains(scene._body_rect)
    assert scene._panel_rect.contains(scene._footer_rect)
    assert scene._title_rect.bottom <= scene._header_divider_y
    assert scene.close_btn.rect.bottom <= scene._header_divider_y
    assert scene._header_divider_y < scene._tabs_rect.top
    assert scene._tabs_rect.bottom < scene._body_rect.top
    assert scene._body_rect.bottom < scene._footer_rect.top
    assert not scene._title_rect.colliderect(scene.close_btn.rect)
    assert scene._header_h >= max(
        scene._title_line_h,
        scene.close_btn.rect.height,
    )

    minimum_target = round(44 * density)
    persistent_buttons = [
        scene.close_btn,
        *(button for _category, button in scene._tab_buttons),
    ]
    assert all(
        button.rect.width >= minimum_target and button.rect.height >= minimum_target
        for button in persistent_buttons
    )
    assert all(button.role == "tab" for _category, button in scene._tab_buttons)
    assert sum(button.selected for _category, button in scene._tab_buttons) == 1

    surface = pygame.Surface(size, pygame.SRCALPHA)
    for category in scene.CATEGORY_ORDER:
        scene._set_category(category)
        assert scene.describe()["category_id"] == category
        assert scene._content_column_rect.width <= scene._sp(760)
        assert (
            abs(scene._content_column_rect.centerx - scene._body_rect.width // 2) <= 1
        )
        assert all(
            scene._content_column_rect.contains(region)
            for _name, region in scene._content_regions
        )

        content_buttons = [button for button, _cx, _cy in scene._buttons]
        assert content_buttons
        assert all(
            button.rect.width >= minimum_target and button.rect.height >= minimum_target
            for button in content_buttons
        )
        button_line = scene.ctx.fonts.get(
            scene._button_font_size,
            bold=True,
        ).get_linesize()
        assert all(button_line <= button.rect.height for button in content_buttons)

        logical_body = pygame.Rect(
            0,
            0,
            scene._body_rect.width,
            scene._content_height,
        )
        for button, cx, cy in scene._buttons:
            logical = pygame.Rect((cx, cy), button.rect.size)
            assert logical_body.contains(logical), (category, button.label, logical)
        for slider, cx, cy, _getter in scene._sliders:
            logical = pygame.Rect((cx, cy), slider.rect.size)
            assert logical_body.contains(logical), (category, logical)
            assert logical.left >= 8
            assert logical.right + 8 <= logical_body.right
            assert slider.rect.width >= minimum_target
            assert slider.rect.height == scene._volume_slider_h

        regions = scene._content_regions
        assert regions
        for name, rect in regions:
            assert logical_body.contains(rect), (category, name, rect)
        for (previous_name, previous), (next_name, following) in zip(
            regions,
            regions[1:],
        ):
            assert previous.bottom <= following.top, (
                category,
                previous_name,
                previous,
                next_name,
                following,
            )

        scene.draw(surface)
        focused = scene._buttons[scene._focus][0]
        assert focused.focused is True
        assert scene._body_rect.contains(focused.rect)
        assert (
            surface.get_at((focused.rect.centerx, focused.rect.top + 1))[:3]
            == scene.ctx.theme.accent_warm[:3]
        )

        if size == (375, 667) and category == "audio":
            assert scene._volume_is_compact is True
            assert scene._sliders
            assert all(
                slider.rect.width >= minimum_target
                for slider, _cx, _cy, _getter in scene._sliders
            )


def test_default_accessibility_actions_take_exactly_one_two_three_operations(
    tmp_path,
) -> None:
    high_contrast, _calls = _make_scene(
        (1280, 720),
        tmp_path / "high-contrast",
    )
    assert high_contrast.describe()["focused_id"] == (
        "accessibility:visual_accessibility_mode"
    )
    high_contrast.update(0.0, _semantic(InputAction.CONFIRM_ADVANCE))
    assert high_contrast.describe()["visual_accessibility_mode"] == ("high_contrast")

    reduced_motion, _calls = _make_scene(
        (1280, 720),
        tmp_path / "reduced-motion",
    )
    reduced_motion.update(0.0, _semantic(InputAction.NAV_DOWN))
    assert reduced_motion.describe()["focused_id"] == ("accessibility:reduce_motion")
    reduced_motion.update(0.0, _semantic(InputAction.CONFIRM_ADVANCE))
    assert reduced_motion.describe()["reduce_motion"] is True

    text_scale, _calls = _make_scene(
        (1280, 720),
        tmp_path / "text-scale",
    )
    before_scale = text_scale.describe()["text_scale"]
    text_scale.update(0.0, _semantic(InputAction.NAV_DOWN))
    text_scale.update(0.0, _semantic(InputAction.NAV_DOWN))
    assert text_scale.describe()["focused_id"] == "accessibility:text_scale"
    text_scale.update(0.0, _semantic(InputAction.CONFIRM_ADVANCE))
    assert text_scale.describe()["text_scale"] != before_scale
    assert text_scale.describe()["text_scale"] == pytest.approx(1.2)


def test_tab_pointer_keyboard_and_gamepad_paths_converge(tmp_path) -> None:
    target = "controls_display"
    scenes = []

    pointer, _calls = _make_scene((1280, 720), tmp_path / "pointer")
    target_tab = dict(pointer._tab_buttons)[target]
    pointer.update(
        0.0,
        InputState(
            mouse_pos=target_tab.rect.center,
            mouse_clicked=True,
        ),
    )
    scenes.append(pointer)

    keyboard, _calls = _make_scene((1280, 720), tmp_path / "keyboard")
    keyboard.update(0.0, _key(pygame.K_RIGHT))
    scenes.append(keyboard)

    gamepad, _calls = _make_scene((1280, 720), tmp_path / "gamepad")
    shoulder = InputRouter().collect(
        [
            pygame.event.Event(
                pygame.JOYBUTTONDOWN,
                button=5,
                instance_id=3,
            ),
        ]
    )
    shoulder.mouse_pos = (-1000, -1000)
    gamepad.update(0.0, shoulder)
    scenes.append(gamepad)

    descriptions = [scene.describe() for scene in scenes]
    assert all(description["category_id"] == target for description in descriptions)
    assert all(
        description["focused_id"] == "controls:open" for description in descriptions
    )
    assert all(
        [
            category["id"]
            for category in description["categories"]
            if category["selected"]
        ]
        == [target]
        for description in descriptions
    )
    assert all(
        [category["id"] for category in description["categories"]]
        == list(SettingsScene.CATEGORY_ORDER)
        for description in descriptions
    )
    assert all(
        button.role == "tab"
        for scene in scenes
        for _category, button in scene._tab_buttons
    )


def test_semantic_focus_survives_category_roundtrip_and_rebuild(tmp_path) -> None:
    scene, _calls = _make_scene((640, 360), tmp_path)
    scene.update(0.0, _semantic(InputAction.FOCUS_NEXT))
    scene.update(0.0, _semantic(InputAction.FOCUS_NEXT))
    accessibility_focus = scene.describe()["focused_id"]
    assert accessibility_focus == "accessibility:text_scale"

    scene._set_category("reading")
    scene.update(0.0, _semantic(InputAction.FOCUS_NEXT))
    scene.update(0.0, _semantic(InputAction.FOCUS_NEXT))
    reading_focus = scene.describe()["focused_id"]
    assert reading_focus is not None

    scene._set_category("accessibility")
    assert scene.describe()["focused_id"] == accessibility_focus
    assert scene._body_rect.contains(scene._buttons[scene._focus][0].rect)

    scene._set_category("reading")
    assert scene.describe()["focused_id"] == reading_focus
    scene._rebuild()
    assert scene.describe()["focused_id"] == reading_focus
    assert scene._body_rect.contains(scene._buttons[scene._focus][0].rect)


def _latin_expand_35(text: str) -> str:
    target = math.ceil(len(text) * 1.35)
    suffix = " localization preview context" * 8
    return text + suffix[: target - len(text)]


_ENGLISH_LAYOUT_MESSAGES = {
    "on": "Enabled",
    "off": "Disabled",
    "toggle_format": "{label}: {state}",
    "playback": "Playback",
    "accessibility": "Accessibility",
    "wait_for_voice": "Wait for voice playback",
    "skip_read_only": "Skip previously read dialogue only",
    "dim_inactive_speakers": "Dim inactive character portraits",
    "auto_emote": "Automatic portrait emotion responses",
    "typewriter_sound": "Typewriter sound effects",
    "ui_sound": "Interface sound effects",
    "nvl_mode": "NVL reading mode",
    "status_hud": "Chapter and date status display",
    "affection_feedback": "Affection change notifications",
    "sound_captions": "Audio captions",
    "high_contrast": "High contrast interface",
    "reduce_motion": "Reduce motion effects",
    "sound_captions_help": (
        "Show text alternatives for meaningful ambient audio, sound effects, "
        "music, and silence."
    ),
    "high_contrast_help": (
        "Increase text, control boundary, and focus contrast without changing "
        "backgrounds or character artwork."
    ),
    "reduce_motion_help": (
        "Stop movement, flashing, pulsing, and idle animation while preserving "
        "static state cues."
    ),
    "text_size_help": ("Applies when the next dialogue presentation begins."),
    "control_settings_help": (
        "Open Control Settings for the complete binding list, conflict "
        "resolution, and reset actions."
    ),
}

_CATEGORY_LAYOUT_TARGETS = {
    "reading": (
        "playback",
        {
            "auto_play_wait_voice": "wait_for_voice",
            "skip_unread_only": "skip_read_only",
            "nvl_mode": "nvl_mode",
        },
    ),
    "audio": (
        "audio",
        {
            "typewriter_sound": "typewriter_sound",
            "ui_sound_enabled": "ui_sound",
        },
    ),
    "accessibility": (
        "accessibility",
        {
            "show_sound_captions": "sound_captions",
            "visual_accessibility_mode": "high_contrast",
            "reduce_motion": "reduce_motion",
        },
    ),
    "controls_display": (
        "display",
        {
            "dim_inactive_speakers": "dim_inactive_speakers",
            "auto_emote_on_emotion": "auto_emote",
            "show_status_hud": "status_hud",
            "show_affection_feedback": "affection_feedback",
        },
    ),
}

_CATEGORY_HELP_LAYOUT_TARGETS = {
    "accessibility": {
        "help:show_sound_captions": "sound_captions_help",
        "help:visual_accessibility_mode": "high_contrast_help",
        "help:reduce_motion": "reduce_motion_help",
        "preview:text_scale": "text_size_help",
    },
    "controls_display": {
        "controls:help": "control_settings_help",
    },
}


def _locale_with_layout_copy(
    messages: dict[str, str],
    *,
    expanded_options: bool,
) -> _SettingsLocalization:
    labels = ("Traditional Chinese", "English Preview")
    if expanded_options:
        labels = tuple(_latin_expand_35(label) for label in labels)
    return _SettingsLocalization(
        messages,
        options=(
            {"id": "zh-Hant", "label": labels[0]},
            {"id": "en", "label": labels[1]},
        ),
    )


def _logical_button_rect(
    button,
    content_x: int,
    content_y: int,
) -> pygame.Rect:
    return pygame.Rect((content_x, content_y), button.rect.size)


@pytest.mark.parametrize(
    "size",
    [(1280, 720), (1920, 1080), (3840, 2160)],
)
def test_real_english_preview_language_choice_never_overflows(
    size: tuple[int, int],
    tmp_path,
) -> None:
    localization = _locale_with_layout_copy(
        _ENGLISH_LAYOUT_MESSAGES,
        expanded_options=False,
    )
    scene, _calls = _make_scene(
        size,
        tmp_path,
        localization=localization,
        locale_change_allowed=True,
    )
    scene._set_category("reading")
    language_region = dict(scene._content_regions)["language"]
    button, cx, cy = next(
        entry for entry in scene._buttons if entry[0].label == "English Preview"
    )
    logical = _logical_button_rect(button, cx, cy)

    assert button.font_size >= 16
    assert scene._content_column_rect.contains(logical)
    assert language_region.contains(logical)
    assert button.text_overflow is False
    assert all(button.rect.contains(rect) for rect in button.text_line_rects)


@pytest.mark.parametrize(
    "size",
    [(1280, 720), (1920, 1080), (3840, 2160)],
)
def test_latin_expansion_settings_copy_is_complete_and_non_overlapping(
    size: tuple[int, int],
    tmp_path,
) -> None:
    expanded = {
        key: (
            value
            if key in {"on", "off", "toggle_format", "playback", "accessibility"}
            else _latin_expand_35(value)
        )
        for key, value in _ENGLISH_LAYOUT_MESSAGES.items()
    }
    localization = _locale_with_layout_copy(
        expanded,
        expanded_options=True,
    )
    scene, _calls = _make_scene(
        size,
        tmp_path,
        localization=localization,
        locale_change_allowed=True,
    )
    scene._set_category("reading")
    regions = dict(scene._content_regions)
    audited_regions_by_category: dict[str, list[pygame.Rect]] = {
        category: [] for category in scene.CATEGORY_ORDER
    }
    audited_buttons_by_category: dict[str, list[pygame.Rect]] = {
        category: [] for category in scene.CATEGORY_ORDER
    }
    audited_regions_by_category["reading"].append(regions["language"])

    for option, source_label in zip(
        localization.options,
        ("Traditional Chinese", "English Preview"),
        strict=True,
    ):
        label = option["label"]
        assert len(label) == math.ceil(len(source_label) * 1.35)
        button, cx, cy = next(
            entry for entry in scene._buttons if entry[0].label == label
        )
        logical = _logical_button_rect(button, cx, cy)
        assert regions["language"].contains(logical)
        assert scene._content_column_rect.contains(logical)
        assert button.font_size >= 16
        assert button.text_overflow is False
        assert all(button.rect.contains(rect) for rect in button.text_line_rects)
        audited_buttons_by_category["reading"].append(logical)

    wrapped_help_count = 0
    for category, (prefix, targets) in _CATEGORY_LAYOUT_TARGETS.items():
        scene._set_category(category)
        regions = dict(scene._content_regions)
        for attr, message_key in targets.items():
            authored = expanded[message_key]
            button, cx, cy = next(
                entry for entry in scene._buttons if entry[0].label.startswith(authored)
            )
            logical = _logical_button_rect(button, cx, cy)
            region = regions[f"{prefix}:{attr}"]
            assert len(authored) == math.ceil(
                len(_ENGLISH_LAYOUT_MESSAGES[message_key]) * 1.35,
            )
            assert region.contains(logical)
            assert scene._content_column_rect.contains(logical)
            assert button.font_size >= 16
            assert button.text_overflow is False
            assert all(button.rect.contains(rect) for rect in button.text_line_rects)
            audited_regions_by_category[category].append(region)
            audited_buttons_by_category[category].append(logical)

        for region_name, message_key in _CATEGORY_HELP_LAYOUT_TARGETS.get(
            category,
            {},
        ).items():
            text = expanded[message_key]
            label_index, label = next(
                (index, entry)
                for index, entry in enumerate(scene._labels)
                if entry[0] == text
            )
            _text, font_size, bold, _color, cx, cy = label
            lines = scene._label_lines[label_index]
            font = scene.ctx.fonts.get(font_size, bold=bold)
            line_h = font.get_linesize()
            region = regions[region_name]

            assert len(text) == math.ceil(
                len(_ENGLISH_LAYOUT_MESSAGES[message_key]) * 1.35,
            )
            assert "".join(lines) == text
            assert font_size >= 16
            assert region.height == len(lines) * line_h
            for line_index, line in enumerate(lines):
                line_rect = pygame.Rect(
                    cx,
                    cy + line_index * line_h,
                    font.size(line)[0],
                    line_h,
                )
                assert region.contains(line_rect)
                assert scene._content_column_rect.contains(line_rect)
            wrapped_help_count += len(lines) > 1
            audited_regions_by_category[category].append(region)

    assert wrapped_help_count >= 1
    assert any(
        rect.width > scene._sp(300)
        for buttons in audited_buttons_by_category.values()
        for rect in buttons
    )
    for category in scene.CATEGORY_ORDER:
        page_buttons = audited_buttons_by_category[category]
        assert all(
            not first.colliderect(second)
            for first, second in combinations(page_buttons, 2)
        )
        ordered_regions = sorted(
            audited_regions_by_category[category],
            key=lambda rect: rect.y,
        )
        assert all(
            previous.bottom <= following.top
            for previous, following in zip(
                ordered_regions,
                ordered_regions[1:],
            )
        )


def test_keyboard_and_gamepad_focus_is_immediate_and_scrolls_into_view(
    tmp_path,
) -> None:
    scene, _calls = _make_scene((640, 360), tmp_path)

    first = scene._buttons[0][0]
    assert scene._focus == 0
    assert first.focused is True
    assert scene._body_rect.contains(first.rect)

    for _ in range(len(scene._buttons) - 1):
        scene.update(0.0, _key(pygame.K_DOWN))
        focused = scene._buttons[scene._focus][0]
        assert focused.focused is True
        assert scene._body_rect.contains(focused.rect)

    assert scene._focus == len(scene._buttons) - 1
    assert scene._scroll_y > 0

    scene.update(0.0, _key(pygame.K_UP))
    gamepad_down = InputState.collect(
        [
            pygame.event.Event(pygame.JOYHATMOTION, value=(0, -1)),
        ]
    )
    gamepad_down.mouse_pos = (-1000, -1000)
    scene.update(0.0, gamepad_down)
    assert scene._focus == len(scene._buttons) - 1

    focused = scene._buttons[scene._focus][0]
    surface = pygame.Surface((640, 360), pygame.SRCALPHA)
    scene.draw(surface)
    assert scene._body_rect.contains(focused.rect)
    assert (
        surface.get_at((focused.rect.centerx, focused.rect.top + 1))[:3]
        == scene.ctx.theme.accent_warm[:3]
    )


def test_confirm_persists_toggle_and_keeps_rebuilt_focus_visible(
    tmp_path,
) -> None:
    scene, calls = _make_scene((800, 450), tmp_path)
    scene._set_category("reading")
    focus_index = next(
        index
        for index, (button, _cx, _cy) in enumerate(scene._buttons)
        if button.label.startswith("等待語音")
    )
    scene._focus = focus_index
    scene._ensure_focus_visible()
    scene._sync_focus_state()
    before = scene.ctx.config.auto_play_wait_voice
    focused_id = scene._button_ids[focus_index]

    scene.update(0.0, _key(pygame.K_RETURN))

    assert scene.ctx.config.auto_play_wait_voice is not before
    assert scene._focus == focus_index
    assert scene.describe()["focused_id"] == focused_id
    focused = scene._buttons[focus_index][0]
    assert focused.focused is True
    assert scene._body_rect.contains(focused.rect)
    settings = json.loads(scene.ctx.config.settings_path().read_text(encoding="utf-8"))
    assert settings["auto_play_wait_voice"] is (not before)

    scene.update(0.0, _key(pygame.K_ESCAPE))
    assert calls == ["close"]


def test_mouse_buttons_slider_scroll_and_close_keep_working(tmp_path) -> None:
    scene, calls = _make_scene((800, 450), tmp_path)

    scene._set_category("reading")
    fast_text = next(
        button for button, _cx, _cy in scene._buttons if button.label == "快"
    )
    scene.update(
        0.016,
        InputState(
            mouse_pos=fast_text.rect.center,
            mouse_clicked=True,
        ),
    )
    assert scene.ctx.config.text_speed == 80.0
    assert scene.ctx.config.settings_path().exists()

    scene._set_category("audio")
    bgm_slider = scene._sliders[0][0]
    target_x = bgm_slider.rect.x + bgm_slider.rect.width // 4
    scene.update(
        0.016,
        InputState(
            mouse_pos=(target_x, bgm_slider.rect.centery),
            mouse_pressed=(True, False, False),
            mouse_clicked=True,
        ),
    )
    scene.update(
        0.016,
        InputState(
            mouse_pos=(target_x, bgm_slider.rect.centery),
        ),
    )
    assert scene.ctx.config.bgm_volume == pytest.approx(0.25, abs=0.01)

    scene._set_category("controls_display")
    assert scene._max_scroll() > 0
    before_scroll = scene._scroll_y
    scene.update(
        0.016,
        InputState(
            mouse_pos=scene._body_rect.center,
            mouse_wheel=-4,
        ),
    )
    assert scene._scroll_y > before_scroll

    scene.update(
        0.016,
        InputState(
            mouse_pos=scene.close_btn.rect.center,
            mouse_clicked=True,
        ),
    )
    assert calls == ["close"]


def test_touch_drag_scrolls_without_activating_a_setting(tmp_path) -> None:
    size = (640, 360)
    scene, calls = _make_scene(size, tmp_path)
    router = InputRouter(touch_mode=True)
    start = (scene._body_rect.right - 4, scene._body_rect.centery)
    x = start[0] / size[0]
    y = start[1] / size[1]

    down = router.collect(
        [
            pygame.event.Event(
                pygame.FINGERDOWN,
                x=x,
                y=y,
                dx=0.0,
                dy=0.0,
                finger_id=1,
                touch_id=1,
            )
        ],
        window_size=size,
    )
    scene.update(0.016, down)
    motion = router.collect(
        [
            pygame.event.Event(
                pygame.FINGERMOTION,
                x=x,
                y=max(0.0, y - 0.2),
                dx=0.0,
                dy=-0.2,
                finger_id=1,
                touch_id=1,
            )
        ],
        window_size=size,
    )
    assert motion.touch_delta[1] < 0
    scene.update(0.016, motion)

    assert 0 < scene._scroll_y <= scene._max_scroll()
    assert calls == []

    release = router.collect(
        [
            pygame.event.Event(
                pygame.FINGERUP,
                x=x,
                y=max(0.0, y - 0.2),
                dx=0.0,
                dy=0.0,
                finger_id=1,
                touch_id=1,
            )
        ],
        window_size=size,
    )
    scene.update(0.016, release)
    assert release.mouse_clicked is False
    assert calls == []


def test_touch_slider_keeps_horizontal_drag_ownership(tmp_path) -> None:
    size = (1280, 720)
    scene, calls = _make_scene(size, tmp_path)
    scene._set_category("audio")
    router = InputRouter(touch_mode=True)
    slider = scene._sliders[0][0]
    start = (
        slider.rect.x + slider.rect.width // 4,
        slider.rect.centery,
    )
    end = (
        slider.rect.x + slider.rect.width * 3 // 4,
        slider.rect.centery,
    )

    down = router.collect(
        [
            pygame.event.Event(
                pygame.FINGERDOWN,
                x=start[0] / size[0],
                y=start[1] / size[1],
                dx=0.0,
                dy=0.0,
                finger_id=2,
                touch_id=1,
            )
        ],
        window_size=size,
    )
    scene.update(0.016, down)
    motion = router.collect(
        [
            pygame.event.Event(
                pygame.FINGERMOTION,
                x=end[0] / size[0],
                y=end[1] / size[1],
                dx=(end[0] - start[0]) / size[0],
                dy=0.0,
                finger_id=2,
                touch_id=1,
            )
        ],
        window_size=size,
    )
    scene.update(0.016, motion)

    assert scene._scroll_y == 0
    assert scene.ctx.config.bgm_volume == pytest.approx(0.75, abs=0.02)
    assert calls == []


def test_compact_footer_keeps_confirm_and_back_before_ellipsis(
    tmp_path,
) -> None:
    scene, _calls = _make_scene((640, 360), tmp_path)

    text = scene._footer_text()

    assert text.index("分頁") < text.index("選項")
    assert text.index("選項") < text.index("變更")
    assert text.index("變更") < text.index("返回")


def _focus_sound_caption_toggle(scene: SettingsScene):
    label = scene.ctx.t("sound_captions", "聲音字幕")
    index = next(
        index
        for index, (button, _cx, _cy) in enumerate(scene._buttons)
        if button.label.startswith(label)
    )
    scene._focus = index
    scene._ensure_focus_visible()
    scene._sync_focus_state()
    return scene._buttons[index][0]


def _focus_high_contrast_toggle(scene: SettingsScene):
    label = scene.ctx.t("high_contrast", "高對比介面")
    index = next(
        index
        for index, (button, _cx, _cy) in enumerate(scene._buttons)
        if button.label.startswith(label)
    )
    scene._focus = index
    scene._ensure_focus_visible()
    scene._sync_focus_state()
    return scene._buttons[index][0]


def test_sound_caption_settings_copy_uses_locale_defaults_and_pack_overrides(
    tmp_path,
) -> None:
    defaults = Localization()
    assert defaults.t("accessibility") == "輔助使用"
    assert defaults.t("sound_captions") == "聲音字幕"
    assert defaults.t("sound_captions_help") == (
        "以文字顯示環境音、音效、音樂與重要的寂靜"
    )
    assert defaults.t("high_contrast") == "高對比介面"
    assert defaults.t("high_contrast_help") == (
        "提高文字、控制邊界與焦點對比；不改變背景、CG 或角色立繪"
    )

    localization = Localization.from_meta(
        {
            "locale": {
                "ui": {
                    "accessibility": "Accessibility",
                    "sound_captions": "Audio Captions",
                    "sound_captions_help": (
                        "Show text alternatives for meaningful non-speech audio."
                    ),
                    "high_contrast": "High Contrast UI",
                    "high_contrast_help": (
                        "Increase control contrast without filtering artwork."
                    ),
                },
            },
        }
    )
    scene, _calls = _make_scene(
        (800, 450),
        tmp_path,
        localization=localization,
    )
    labels = [entry[0] for entry in scene._labels]

    assert any(
        button.label == "Accessibility"
        for _category, button in scene._tab_buttons
    )
    assert "Show text alternatives for meaningful non-speech audio." in labels
    assert _focus_sound_caption_toggle(scene).label.startswith(
        "Audio Captions",
    )
    assert "Increase control contrast without filtering artwork." in labels
    assert _focus_high_contrast_toggle(scene).label.startswith(
        "High Contrast UI",
    )


def test_high_contrast_toggle_is_live_strict_and_persisted(tmp_path) -> None:
    scene, _calls = _make_scene((800, 450), tmp_path)
    authored = scene.ctx.theme.base_theme

    assert scene.ctx.visual is not None
    assert scene.ctx.visual.profile == "standard"
    assert scene.ctx.theme.resolved_theme is authored

    _focus_high_contrast_toggle(scene)
    scene.update(0.0, _key(pygame.K_RETURN))

    assert scene.ctx.visual.profile == "high_contrast"
    assert scene.ctx.theme.visual_profile == "high_contrast"
    assert scene.ctx.theme.bg_panel[:3] == (0, 0, 0)
    assert scene.ctx.theme.text[:3] == (255, 255, 255)
    assert scene.ctx.theme.base_theme is authored
    assert scene.describe()["visual_accessibility_mode"] == "high_contrast"
    focused = _focus_high_contrast_toggle(scene)
    assert focused.selected is True

    stored = json.loads(
        scene.ctx.config.settings_path().read_text(encoding="utf-8"),
    )
    assert stored["visual_accessibility_mode"] == "high_contrast"

    # The same live policy switches back without replacing or mutating the
    # authored pack theme.
    scene.update(
        0.0,
        InputState(
            mouse_pos=focused.rect.center,
            mouse_clicked=True,
        ),
    )
    assert scene.ctx.visual.profile == "standard"
    assert scene.ctx.theme.resolved_theme is authored
    assert scene.ctx.theme.text == authored.text


def test_sound_caption_toggle_supports_all_inputs_and_persists(tmp_path) -> None:
    size = (800, 450)
    scene, _calls = _make_scene(size, tmp_path)

    # Keyboard / semantic confirm.
    _focus_sound_caption_toggle(scene)
    scene.update(0.0, _key(pygame.K_RETURN))
    assert scene.ctx.config.show_sound_captions is False
    assert scene.describe()["show_sound_captions"] is False

    # Mouse activation.
    button = _focus_sound_caption_toggle(scene)
    scene.update(
        0.0,
        InputState(
            mouse_pos=button.rect.center,
            mouse_clicked=True,
        ),
    )
    assert scene.ctx.config.show_sound_captions is True

    # Gamepad A uses the same focused semantic action.
    _focus_sound_caption_toggle(scene)
    gamepad = InputState.collect(
        [
            pygame.event.Event(
                pygame.JOYBUTTONDOWN,
                joy=0,
                instance_id=0,
                button=0,
            ),
        ]
    )
    gamepad.mouse_pos = (-1000, -1000)
    scene.update(0.0, gamepad)
    assert scene.ctx.config.show_sound_captions is False

    # A stationary touch release activates the same button without becoming a
    # scroll drag or producing a duplicate ghost-mouse click.
    button = _focus_sound_caption_toggle(scene)
    router = InputRouter(touch_mode=True)
    x = button.rect.centerx / size[0]
    y = button.rect.centery / size[1]
    down = router.collect(
        [
            pygame.event.Event(
                pygame.FINGERDOWN,
                x=x,
                y=y,
                dx=0.0,
                dy=0.0,
                finger_id=9,
                touch_id=1,
            ),
        ],
        window_size=size,
    )
    scene.update(0.0, down)
    up = router.collect(
        [
            pygame.event.Event(
                pygame.FINGERUP,
                x=x,
                y=y,
                dx=0.0,
                dy=0.0,
                finger_id=9,
                touch_id=1,
            ),
        ],
        window_size=size,
    )
    scene.update(0.0, up)

    assert scene.ctx.config.show_sound_captions is True
    stored = json.loads(
        scene.ctx.config.settings_path().read_text(encoding="utf-8"),
    )
    assert stored["show_sound_captions"] is True
