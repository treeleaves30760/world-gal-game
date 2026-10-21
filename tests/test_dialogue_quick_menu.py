"""Progressive-disclosure and input contracts for dialogue controls."""
from __future__ import annotations

import pygame
import pytest


PRIMARY_LABELS = ["自動", "快進", "記錄", "快存", "隱藏", "更多"]
PRIMARY_GLYPHS = ["A", ">>", "LOG", "QS", "UI−", "…"]
OVERFLOW_LABELS = ["快讀", "存檔", "讀取", "設定", "完整選單"]


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver

    instance = GameDriver(pack="demo_pack")
    instance.new_game()
    yield instance
    instance.quit()


def _labels(entries):
    return [button.label for button, _active in entries]


def test_quick_menu_keeps_reading_actions_primary_and_risky_load_secondary(
    driver,
):
    scene = driver.app.manager.current
    bar = scene._quick_bar

    assert _labels(bar._entries) == PRIMARY_LABELS
    assert [button.display_label for button, _active in bar._entries] \
        == PRIMARY_GLYPHS
    assert all(button.tooltip == button.label for button, _ in bar._entries)
    assert _labels(bar._overflow_entries) == OVERFLOW_LABELS
    assert "快讀" not in _labels(bar._entries)
    assert bar.rect.width < driver.app.screen.get_width() * 0.4

    visible = [
        widget["label"] for widget in driver._widget_catalogue()
        if widget["path"].startswith("_quick_bar.")
    ]
    assert visible == PRIMARY_LABELS

    more = driver.find_widget(label="更多")
    driver.click(tuple(more["rect_center"]))
    driver.advance_frames(1)
    assert bar.overflow_open is True
    visible = [
        widget["label"] for widget in driver._widget_catalogue()
        if widget["path"].startswith("_quick_bar.")
    ]
    assert visible == PRIMARY_LABELS + OVERFLOW_LABELS


def test_more_escape_priority_and_dialogue_escape_open_full_menu(driver):
    scene = driver.app.manager.current
    bar = scene._quick_bar
    more = driver.find_widget(label="更多")
    driver.click(tuple(more["rect_center"]))
    driver.advance_frames(1)
    assert bar.overflow_open is True

    # First Escape is owned by the disclosure layer.
    driver.press_escape()
    driver.advance_frames(1)
    assert driver.app.manager.current is scene
    assert bar.overflow_open is False

    # A second Escape fulfils the onboarding/documented full-menu shortcut.
    driver.press_escape()
    driver.advance_frames(1)
    assert type(driver.app.manager.current).__name__ == "MenuScene"
    assert scene._covered_by_overlay is True

    driver.press_escape()
    driver.advance_frames(1)
    assert driver.app.manager.current is scene
    assert scene._covered_by_overlay is False


def test_documented_cancel_alias_x_opens_full_menu(driver):
    driver.press_key(pygame.K_x)
    driver.advance_frames(1)
    assert type(driver.app.manager.current).__name__ == "MenuScene"


def test_driver_catalogue_does_not_expose_hidden_choice_buttons(driver):
    scene = driver.app.manager.current
    scene.choices.set_choices([
        ("hidden", "INVISIBLE CHOICE", True),
    ])
    scene.choices.visible = False
    assert driver.find_widget(label="INVISIBLE CHOICE") is None

    scene.choices.visible = True
    assert driver.find_widget(label="INVISIBLE CHOICE") is not None


def test_click_outside_open_more_closes_without_advancing_dialogue(driver):
    scene = driver.app.manager.current
    bar = scene._quick_bar
    scene.box.force_reveal()
    before = (
        driver.app.state.story.current_scene,
        driver.app.state.story.current_line_index,
    )

    more = driver.find_widget(label="更多")
    driver.click(tuple(more["rect_center"]))
    driver.advance_frames(1)
    assert bar.overflow_open is True

    driver.click((4, driver.app.screen.get_height() // 2))
    driver.advance_frames(1)
    assert bar.overflow_open is False
    assert (
        driver.app.state.story.current_scene,
        driver.app.state.story.current_line_index,
    ) == before


def test_new_dialogue_presentation_closes_more(driver):
    scene = driver.app.manager.current
    more = driver.find_widget(label="更多")
    driver.click(tuple(more["rect_center"]))
    driver.advance_frames(1)
    assert scene._quick_bar.overflow_open is True

    scene.box.force_reveal()
    before = (
        driver.app.state.story.current_line_index,
        scene.box.page_index,
    )
    driver.press_space()
    driver.advance_frames(1)
    assert (
        driver.app.state.story.current_line_index,
        scene.box.page_index,
    ) != before
    assert scene._quick_bar.overflow_open is False


def test_quick_menu_click_owns_frame_at_auto_advance_boundary(driver):
    scene = driver.app.manager.current
    more = driver.find_widget(label="更多")
    driver.click(tuple(more["rect_center"]))
    driver.advance_frames(1)

    scene.box.force_reveal()
    scene.auto_play_enabled = True
    auto_delay = (
        driver.cfg.auto_play_delay / max(0.1, driver.cfg.auto_play_speed)
    )
    scene._auto_play_timer = auto_delay - driver.frame_dt / 2
    before = (
        driver.app.state.story.current_scene,
        driver.app.state.story.current_line_index,
    )

    settings = driver.find_widget(label="設定")
    driver.click(tuple(settings["rect_center"]))
    driver.advance_frames(1)
    assert type(driver.app.manager.current).__name__ == "SettingsScene"
    assert (
        driver.app.state.story.current_scene,
        driver.app.state.story.current_line_index,
    ) == before


def test_visible_quick_rail_padding_consumes_click_without_turning_page(
    driver,
):
    scene = driver.app.manager.current
    bar = scene._quick_bar
    scene.box.force_reveal()
    before = (
        driver.app.state.story.current_scene,
        driver.app.state.story.current_line_index,
        scene.box.page_index,
    )
    rail = bar._rail_rect(bar.rect)
    point = (bar.rect.centerx, rail.top)
    assert rail.collidepoint(point)
    assert not bar.rect.collidepoint(point)

    driver.click(point)
    driver.advance_frames(1)

    assert (
        driver.app.state.story.current_scene,
        driver.app.state.story.current_line_index,
        scene.box.page_index,
    ) == before


def test_tab_focus_and_confirm_activate_quick_action_without_advancing(driver):
    scene = driver.app.manager.current
    scene.box.force_reveal()
    before = (
        driver.app.state.story.current_scene,
        driver.app.state.story.current_line_index,
        scene.box.page_index,
    )

    driver.press_key(pygame.K_TAB)
    driver.advance_frames(1)
    assert scene._quick_bar.focused_label == "自動"

    driver.press_space()
    driver.advance_frames(1)

    assert scene.auto_play_enabled is True
    assert (
        driver.app.state.story.current_scene,
        driver.app.state.story.current_line_index,
        scene.box.page_index,
    ) == before


def test_keyboard_can_reach_and_dismiss_more_layer(driver):
    scene = driver.app.manager.current
    for _ in range(len(PRIMARY_LABELS)):
        driver.press_key(pygame.K_TAB)
        driver.advance_frames(1)
    assert scene._quick_bar.focused_label == "更多"

    driver.press_key(pygame.K_RETURN)
    driver.advance_frames(1)
    assert scene._quick_bar.overflow_open is True

    driver.press_key(pygame.K_TAB)
    driver.advance_frames(1)
    assert scene._quick_bar.focused_label == "快讀"

    driver.press_escape()
    driver.advance_frames(1)
    assert scene._quick_bar.overflow_open is False
    assert scene._quick_bar.focused_label == "更多"
    assert driver.app.manager.current is scene


@pytest.mark.parametrize("size", [(1280, 720), (800, 450)])
def test_touch_quick_menu_targets_are_at_least_44px_and_in_bounds(size):
    from world_gal_game.config import MIN_TOUCH_TARGET_PX
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack", screen_size=size)
    try:
        driver.cfg.touch_mode = True
        driver.new_game()
        bar = driver.app.manager.current._quick_bar
        buttons = [
            button for button, _active in
            (bar._entries + bar._overflow_entries)
        ]
        canvas = pygame.Rect((0, 0), size)
        assert all(button.rect.width >= MIN_TOUCH_TARGET_PX for button in buttons)
        assert all(button.rect.height >= MIN_TOUCH_TARGET_PX for button in buttons)
        assert all(canvas.contains(button.rect) for button in buttons)
    finally:
        driver.quit()


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080)])
def test_compact_primary_rail_stays_in_reading_and_face_safe_area(size):
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack", screen_size=size)
    try:
        driver.new_game()
        scene = driver.app.manager.current
        bar = scene._quick_bar
        painted = bar._rail_rect(bar.rect)
        nameplate_band = pygame.Rect(
            scene.box.rect.left,
            scene.box.rect.top - scene.box.metrics.nameplate_height // 2,
            scene.box.rect.width,
            scene.box.metrics.nameplate_height,
        )

        assert 300 <= painted.width <= 360
        assert all(
            button.rect.width >= 44 and button.rect.height >= 44
            for button, _active in bar._entries
        )
        assert not painted.colliderect(scene.box.rect)
        assert not painted.colliderect(nameplate_band)
        assert not painted.colliderect(scene.portrait.rect)
    finally:
        driver.quit()


def test_hover_focus_and_reduced_motion_never_reflow_compact_rail():
    from world_gal_game.dev.driver import GameDriver
    from world_gal_game.ui.input import InputState

    driver = GameDriver(pack="demo_pack", screen_size=(1920, 1080))
    try:
        driver.cfg.reduce_motion = True
        driver.new_game()
        bar = driver.app.manager.current._quick_bar
        first = bar._entries[0][0]
        geometry = [button.rect.copy() for button, _active in bar._entries]
        rail_geometry = bar.rect.copy()

        bar.update(
            0.0,
            InputState(
                events=[pygame.event.Event(
                    pygame.MOUSEMOTION,
                    pos=first.rect.center,
                )],
                mouse_pos=first.rect.center,
            ),
        )

        assert bar.focused_label == "自動"
        assert first._hover_t == 1.0
        assert bar.rect == rail_geometry
        assert [button.rect for button, _active in bar._entries] == geometry
    finally:
        driver.quit()


def test_gamepad_traverses_and_activates_compact_rail_without_reflow():
    from world_gal_game.dev.driver import GameDriver
    from world_gal_game.ui.input import InputState

    driver = GameDriver(pack="demo_pack")
    try:
        driver.new_game()
        scene = driver.app.manager.current
        bar = scene._quick_bar
        geometry = [button.rect.copy() for button, _active in bar._entries]

        bar.update(
            1 / 60,
            InputState(
                events=[pygame.event.Event(
                    pygame.JOYHATMOTION,
                    value=(1, 0),
                )],
                mouse_pos=(-1000, -1000),
            ),
        )
        assert bar.focused_label == "自動"

        bar.update(
            1 / 60,
            InputState(
                events=[pygame.event.Event(
                    pygame.JOYBUTTONDOWN,
                    button=0,
                )],
                mouse_pos=(-1000, -1000),
            ),
        )
        assert scene.auto_play_enabled is True
        assert [button.rect for button, _active in bar._entries] == geometry
    finally:
        driver.quit()


def test_quick_rail_rest_is_quiet_but_high_contrast_is_opaque():
    from world_gal_game.dev.driver import GameDriver

    alphas = []
    for mode in ("standard", "high_contrast"):
        driver = GameDriver(pack="demo_pack")
        try:
            driver.cfg.visual_accessibility_mode = mode
            driver.new_game()
            bar = driver.app.manager.current._quick_bar
            surface = pygame.Surface(driver.app.screen.get_size(), pygame.SRCALPHA)
            bar.draw(surface)
            first = bar._entries[0][0]
            gap_probe = (
                first.rect.right + bar._gap // 2,
                first.rect.centery,
            )
            alphas.append(surface.get_at(gap_probe).a)
        finally:
            driver.quit()

    standard_alpha, contrast_alpha = alphas
    assert 0 < standard_alpha < 255
    assert contrast_alpha == 255


def test_large_text_short_canvas_keeps_upward_overflow_in_bounds():
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack", screen_size=(800, 450))
    try:
        driver.cfg.text_scale = 1.4
        driver.new_game()
        bar = driver.app.manager.current._quick_bar
        canvas = pygame.Rect((0, 0), (800, 450))
        assert canvas.contains(bar.rect)
        assert canvas.contains(bar._overflow_rect)
    finally:
        driver.quit()


def test_long_localized_labels_are_ellipsized_inside_short_canvas():
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack", screen_size=(800, 450))
    try:
        driver.app.localization.ui.update({
            "auto": "Automatic dialogue playback",
            "skip": "Skip previously read dialogue",
            "history": "Complete dialogue history",
            "quick_save": "Create a quick save",
            "hide_ui": "Hide the entire interface",
            "more": "More dialogue actions",
            "quick_load": "Immediately load the quick save",
            "save": "Open manual save management",
            "load": "Open manual load management",
            "settings": "Accessibility and settings",
            "menu": "Open the complete system menu",
        })
        driver.new_game()
        bar = driver.app.manager.current._quick_bar
        canvas = pygame.Rect((0, 0), (800, 450))
        assert canvas.contains(bar.rect)
        assert canvas.contains(bar._overflow_rect)
        buttons = [
            button for button, _active in
            (bar._entries + bar._overflow_entries)
        ]
        assert all(canvas.contains(button.rect) for button in buttons)
        assert any(button.display_label.endswith("…") for button in buttons)
        assert all(
            button.tooltip
            for button in buttons
            if button.display_label.endswith("…")
        )

        truncated = next(
            button for button in buttons
            if button.display_label.endswith("…")
        )
        from world_gal_game.ui.input import InputState

        inp = InputState(events=[])
        inp.mouse_pos = truncated.rect.center
        bar.update(1 / 60, inp)
        assert bar._hovered_tooltip == (truncated, truncated.tooltip)

        surface = pygame.Surface((800, 450), pygame.SRCALPHA)
        bar.draw(surface)
        assert bar._tooltip_rect is not None
        assert canvas.contains(bar._tooltip_rect)
        assert surface.get_at(bar._tooltip_rect.center).a > 0
    finally:
        driver.quit()


def test_dialogue_footer_and_mode_badges_use_locale_and_live_binding():
    from world_gal_game.dev.driver import GameDriver
    from world_gal_game.ui.input_bindings import InputAction

    driver = GameDriver(pack="demo_pack")
    try:
        driver.app.localization.ui.update({
            "dialogue_continue_hint": "Press {binding} / Click to continue",
            "dialogue_next_page_hint": "Press {binding} / Click for next page",
            "auto": "Localized Auto",
            "skip": "Localized Skip",
        })
        driver.cfg.input_bindings = {
            InputAction.CONFIRM_ADVANCE: ["p"],
        }
        driver.new_game()
        scene = driver.app.manager.current

        assert scene.box.continue_hint == "Press P / Click to continue"
        assert scene.box.next_page_hint == "Press P / Click for next page"
        scene.auto_play_enabled = True
        scene._skip_active = True
        assert scene._mode_indicator_labels() == [
            "Localized Auto",
            "Localized Skip",
        ]

        # Returning from Controls refreshes the footer without reconstructing
        # the active dialogue or resetting its page/reveal state.
        driver.cfg.input_bindings = {
            InputAction.CONFIRM_ADVANCE: ["q"],
        }
        box = scene.box
        scene.resume()
        assert scene.box is box
        assert scene.box.continue_hint == "Press Q / Click to continue"
        assert scene.box.next_page_hint == "Press Q / Click for next page"
    finally:
        driver.quit()


@pytest.mark.parametrize(
    ("nvl_mode", "touch_mode", "text_scale", "size"),
    [
        (False, True, 1.4, (800, 450)),
        (True, False, 1.0, (1280, 720)),
        (True, True, 1.0, (800, 450)),
    ],
)
def test_quick_menu_rails_do_not_cover_dialogue_content(
    nvl_mode,
    touch_mode,
    text_scale,
    size,
):
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack", screen_size=size)
    try:
        driver.cfg.nvl_mode = nvl_mode
        driver.cfg.touch_mode = touch_mode
        driver.cfg.text_scale = text_scale
        driver.new_game()
        scene = driver.app.manager.current
        bar = scene._quick_bar
        rail_padding = scene.ctx.theme.pad_s
        if nvl_mode:
            overflow_rail = bar._overflow_rect.inflate(
                rail_padding, rail_padding,
            )
            assert scene.box.rect.bottom <= overflow_rail.top
        else:
            primary_rail = bar.rect.inflate(rail_padding, rail_padding)
            assert not primary_rail.colliderect(scene.box.rect)
    finally:
        driver.quit()
