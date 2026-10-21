"""Category, responsive-layout, and input contracts for MenuScene."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.clue import Clue
from world_gal_game.core.game_state import GameState
from world_gal_game.core.localization import Localization
from world_gal_game.npc.npc_base import NPCRegistry
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.menu_scene import MenuScene
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.theme import default_theme


ACTION_IDS = (
    "map",
    "affection",
    "character_profiles",
    "clues",
    "log",
    "achievements",
    "inventory",
    "quest_log",
    "flowchart",
    "relationships",
    "cg_gallery",
    "music_room",
    "endings",
    "scene_replay",
    "credits",
    "save",
    "load",
    "settings",
    "quit_to_title",
    "quit_app",
)


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


def _callback(calls: list[str], action_id: str):
    return lambda: calls.append(action_id)


def _make_scene(
    size: tuple[int, int],
    tmp_path,
    *,
    enabled_actions: tuple[str, ...] = ACTION_IDS,
    localization: Localization | None = None,
    input_bindings: dict[str, list[str]] | None = None,
) -> tuple[MenuScene, list[str]]:
    config = EngineConfig(
        screen_size=size,
        input_bindings=input_bindings or {},
    )
    fonts = FontRegistry(
        candidates=config.font_candidates,
        scale=max(1.0, size[1] / 720.0),
    )
    ctx = SceneContext(
        config=config,
        state=GameState(),
        npcs=NPCRegistry(),
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=AssetManager(pack_root=tmp_path),
        fonts=fonts,
        theme=default_theme(),
        localization=localization or Localization(),
        screen_size=size,
    )
    calls: list[str] = []
    callbacks = {
        f"on_{action_id}": _callback(calls, action_id)
        for action_id in enabled_actions
    }
    scene = MenuScene(ctx)
    scene.enter(
        on_close=_callback(calls, "close"),
        **callbacks,
    )
    return scene, calls


def _key(key: int) -> InputState:
    return InputState(
        events=[pygame.event.Event(
            pygame.KEYDOWN,
            key=key,
            mod=0,
            unicode="",
        )],
        confirm=key in (
            pygame.K_RETURN,
            pygame.K_KP_ENTER,
            pygame.K_SPACE,
            pygame.K_z,
        ),
        cancel=key in (pygame.K_ESCAPE, pygame.K_x),
        mouse_pos=(-1000, -1000),
    )


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080)])
def test_hub_shows_one_responsive_category_without_scroll(
    size: tuple[int, int],
    tmp_path,
) -> None:
    scene, _calls = _make_scene(size, tmp_path)
    density = size[1] / 720.0
    surface = pygame.Surface(size, pygame.SRCALPHA)
    scene.draw(surface)

    description = scene.describe()
    assert description["categories"] == ["records", "extras", "system"]
    assert description["active_category"] == "records"
    assert len(description["visible_items"]) == 8
    assert len(scene._buttons) == 8
    assert scene._columns == 1
    assert scene._scroll_y == 0
    assert scene._content_height <= scene._body_rect.height

    viewport = surface.get_rect()
    targets = [scene.close_btn, *scene._tab_buttons, *scene._buttons]
    minimum_target = round(44 * density)
    assert all(
        button.rect.width >= minimum_target
        and button.rect.height >= minimum_target
        for button in targets
    )
    assert all(viewport.contains(button.rect) for button in targets)
    assert all(scene._panel_rect.contains(button.rect) for button in targets)

    assert scene._panel_rect.contains(scene._title_rect)
    assert scene._title_rect.bottom <= scene._header_divider_y
    assert scene.close_btn.rect.bottom <= scene._header_divider_y
    assert all(
        tab.rect.bottom < scene._body_rect.top
        for tab in scene._tab_buttons
    )
    assert all(
        scene._body_rect.contains(button.rect)
        for button in scene._buttons
    )
    assert scene._tab_buttons[0].rect.left == scene._body_rect.left
    assert scene._tab_buttons[-1].rect.right == scene._body_rect.right

    title_line = scene.ctx.fonts.get(
        scene._title_font_size,
        bold=True,
    ).get_linesize()
    tab_line = scene.ctx.fonts.get(
        scene._tab_font_size,
        bold=True,
    ).get_linesize()
    item_line = scene.ctx.fonts.get(
        scene._item_font_size,
        bold=True,
    ).get_linesize()
    close_line = scene.ctx.fonts.get(
        scene._close_font_size,
        bold=True,
    ).get_linesize()
    assert title_line <= scene._header_h
    assert all(tab_line <= tab.rect.height for tab in scene._tab_buttons)
    assert all(item_line <= button.rect.height for button in scene._buttons)
    assert close_line <= scene.close_btn.rect.height

    ring = scene._focus_ring_rect
    assert ring is not None
    assert scene._panel_rect.contains(ring)
    assert (
        surface.get_at((ring.centerx, ring.top))[:3]
        == scene.ctx.theme.accent_warm[:3]
    )


def test_keyboard_and_gamepad_move_between_categories_and_actions(
    tmp_path,
) -> None:
    scene, calls = _make_scene((1280, 720), tmp_path)

    assert scene._focus == 0
    scene.update(0.016, _key(pygame.K_DOWN))
    assert scene._focus == 1
    scene.update(0.016, _key(pygame.K_RETURN))
    assert calls == ["affection"]

    scene.update(0.016, _key(pygame.K_RIGHT))
    assert scene.describe()["active_category"] == "extras"
    assert scene._focus == 0
    scene.update(0.016, _key(pygame.K_q))
    assert scene.describe()["active_category"] == "records"
    scene.update(0.016, _key(pygame.K_LEFT))
    assert scene.describe()["active_category"] == "system"
    scene.update(0.016, _key(pygame.K_e))
    assert scene.describe()["active_category"] == "records"

    category_input = InputState.collect([
        pygame.event.Event(pygame.JOYHATMOTION, value=(1, 0)),
    ])
    category_input.mouse_pos = (-1000, -1000)
    scene.update(0.016, category_input)
    assert scene.describe()["active_category"] == "extras"

    item_input = InputState.collect([
        pygame.event.Event(pygame.JOYHATMOTION, value=(0, -1)),
    ])
    item_input.mouse_pos = (-1000, -1000)
    scene.update(0.016, item_input)
    assert scene._focus == 1

    confirm_input = InputState.collect([
        pygame.event.Event(pygame.JOYBUTTONDOWN, button=0),
    ])
    confirm_input.mouse_pos = (-1000, -1000)
    scene.update(0.016, confirm_input)
    assert calls[-1] == "relationships"


def test_mouse_tabs_actions_and_close_remain_clickable(tmp_path) -> None:
    scene, calls = _make_scene((1280, 720), tmp_path)

    extras_tab = scene._tab_buttons[1]
    scene.update(0.016, InputState(
        mouse_pos=extras_tab.rect.center,
        mouse_clicked=True,
    ))
    assert scene.describe()["active_category"] == "extras"
    assert len(scene._buttons) == 7

    cg_button = scene._buttons[2]
    scene.update(0.016, InputState(
        mouse_pos=cg_button.rect.center,
        mouse_clicked=True,
    ))
    assert calls == ["cg_gallery"]

    scene.update(0.016, InputState(
        mouse_pos=scene.close_btn.rect.center,
        mouse_clicked=True,
    ))
    assert calls == ["cg_gallery", "close"]


def test_all_existing_callbacks_are_preserved_across_three_pages(tmp_path) -> None:
    scene, calls = _make_scene((1280, 720), tmp_path)

    for category_index in range(3):
        scene._set_category(category_index)
        for button in scene._buttons:
            assert button.enabled
            assert button.on_click is not None
            button.on_click()

    assert calls == list(ACTION_IDS)


def test_focus_skips_unavailable_callbacks(tmp_path) -> None:
    scene, calls = _make_scene(
        (1280, 720),
        tmp_path,
        enabled_actions=("affection", "inventory"),
    )

    assert scene._buttons[0].enabled is False
    assert scene._focus == 1
    scene.update(0.016, InputState(nav=1, mouse_pos=(-1000, -1000)))
    assert scene._focus == 6
    scene.update(0.016, _key(pygame.K_RETURN))
    assert calls == ["inventory"]

    scene._set_category(1)
    assert scene._focus == -1
    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    scene.draw(surface)
    assert scene._focus_ring_rect is None


def test_locked_map_stays_visible_focusable_and_explains_itself(
    tmp_path,
) -> None:
    scene, calls = _make_scene((1280, 720), tmp_path)
    reason = "先依新生指南完成集合。"
    scene.get_map_availability = lambda: (False, reason)
    scene._build()

    map_button = scene._buttons[0]
    assert map_button.label.startswith("地圖")
    assert map_button.enabled is False
    assert map_button.tooltip == reason
    assert scene._focus == 0
    assert scene.describe()["visible_item_states"][0] == {
        "label": map_button.label,
        "enabled": False,
        "reason": reason,
    }

    scene.update(0.016, InputState(
        mouse_pos=map_button.rect.center,
        mouse_clicked=False,
    ))
    assert scene._active_explanation() == reason

    scene.update(0.016, InputState(
        mouse_pos=map_button.rect.center,
        mouse_clicked=True,
    ))
    assert calls == []
    assert scene.ctx.state.meta["__pending_toasts__"][-1] == (
        "notice",
        map_button.label,
        reason,
    )


def test_localized_menu_copy_uses_live_bindings_and_sentence_template(
    tmp_path,
    monkeypatch,
) -> None:
    localization = Localization()
    localization.ui.update({
        "map": "Campus Map",
        "affection": "Bonds",
        "characters": "Cast",
        "clue_journal": "Clue Journal",
        "log": "Events",
        "achievements": "Achievements",
        "inventory": "Items",
        "quest_log": "Quest Log",
        "menu_item_with_binding": "{label} [{binding}]",
        "menu_item_with_binding_unread": (
            "{label} [{binding}] · {count} new"
        ),
        "menu_footer": (
            "{left}/{right} Pages | {up}/{down} Select | "
            "{confirm} Confirm | {back} Back"
        ),
    })
    bindings = {
        "open_map": ["g"],
        "open_affection": ["v"],
        "open_clues": ["c"],
        "open_event_log": ["e"],
        "open_achievements": ["u"],
        "open_inventory": ["o"],
        "nav_left": ["j"],
        "nav_right": ["k"],
        "nav_up": ["r"],
        "nav_down": ["f"],
        "confirm_advance": ["p"],
        "back": ["b"],
    }
    scene, _calls = _make_scene(
        (1280, 720),
        tmp_path,
        localization=localization,
        input_bindings=bindings,
    )
    scene.ctx.state.clues.register(Clue(id="fresh", title="Fresh clue"))
    scene.ctx.state.clues.refresh(scene.ctx.state)
    scene._build()

    assert scene.describe()["visible_items"] == [
        "Campus Map [G]",
        "Bonds [V]",
        "Cast",
        "Clue Journal [C] · 1 new",
        "Events [E]",
        "Achievements [U]",
        "Items [O]",
        "Quest Log",
    ]

    footer_calls: list[dict[str, str]] = []
    original_t = Localization.t

    def _record_t(self, key, default=None, **fmt):
        if key == "menu_footer":
            footer_calls.append(fmt)
        return original_t(self, key, default, **fmt)

    monkeypatch.setattr(Localization, "t", _record_t)
    scene.draw(pygame.Surface((1280, 720), pygame.SRCALPHA))
    assert footer_calls == [{
        "left": "J",
        "right": "K",
        "up": "R",
        "down": "F",
        "confirm": "P",
        "back": "B",
    }]
