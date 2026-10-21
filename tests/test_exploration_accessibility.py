"""Responsive target and keyboard contracts for ExplorationScene."""

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
from world_gal_game.core.map_system import (
    Exit,
    Location,
    NPCPresence,
    Region,
    SceneHook,
)
from world_gal_game.core.story_graph import Line, Scene
from world_gal_game.npc.npc_base import NPC, NPCRegistry
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.exploration import ExplorationScene
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.theme import default_theme


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    yield
    pygame.quit()


def _key(key: int, *, mod: int = 0) -> InputState:
    event = pygame.event.Event(
        pygame.KEYDOWN,
        key=key,
        mod=mod,
        unicode="",
    )
    return InputState(
        events=[event],
        confirm=key
        in (
            pygame.K_RETURN,
            pygame.K_KP_ENTER,
            pygame.K_SPACE,
            pygame.K_z,
        ),
    )


def _make_scene(
    size: tuple[int, int],
    tmp_path,
    *,
    localization: Localization | None = None,
    input_bindings: dict[str, list[str]] | None = None,
):
    state = GameState()
    state.story.add_scene(
        Scene(
            id="inspect_notice",
            title="查看公告欄",
            lines=[Line(text="測試")],
        )
    )
    state.map.add_location(
        Location(
            id="start",
            name="校門口",
            description="新生在這裡確認今天的行程與下一個目的地。",
            exits=[Exit(target="library")],
            npcs=[NPCPresence(npc_id="heroine")],
            scene_hooks=[
                SceneHook(
                    scene_id="inspect_notice",
                    trigger="examine",
                )
            ],
        )
    )
    state.map.add_location(Location(id="library", name="圖書館"))
    state.map.move_to("start")

    npcs = NPCRegistry()
    npcs.add(
        NPC(
            id="heroine",
            name="名字很長但仍應安全截斷的女主角",
            role="新生指引協助者",
            is_heroine=True,
        )
    )

    config = EngineConfig(
        screen_size=size,
        input_bindings=input_bindings or {},
    )
    fonts = FontRegistry(
        candidates=config.font_candidates,
        scale=max(1.0, size[1] / 720),
    )
    localization = localization or Localization()
    state.affection.bind_localization(localization)
    ctx = SceneContext(
        config=config,
        state=state,
        npcs=npcs,
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=AssetManager(pack_root=tmp_path),
        fonts=fonts,
        theme=default_theme(),
        localization=localization,
        screen_size=size,
        meta={
            "travel": {
                "scope": "graph",
                "requires_flags": ["map_ready"],
                "guided_story": {},
            }
        },
    )
    calls: dict[str, list] = {
        "map": [],
        "clues": [],
        "menu": [],
        "move": [],
        "npc": [],
        "save": [],
        "scene": [],
        "wait": [],
    }
    locked_reason = "我知道現在不應該略過；先完成新生指引。"
    scene = ExplorationScene(ctx)
    scene.enter(
        on_map=lambda: calls["map"].append(True),
        on_clues=lambda: calls["clues"].append(True),
        on_menu=lambda: calls["menu"].append(True),
        on_move_to=calls["move"].append,
        on_open_npc=calls["npc"].append,
        on_save=lambda: calls["save"].append(True),
        on_start_scene=calls["scene"].append,
        on_advance_time=lambda: calls["wait"].append(True),
        get_advance_time_availability=lambda: (False, locked_reason),
    )
    return scene, calls, locked_reason


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080)])
def test_exploration_targets_are_safe_and_only_travel_is_primary(
    size: tuple[int, int],
    tmp_path,
) -> None:
    scene, _calls, _reason = _make_scene(size, tmp_path)
    scene.ctx.state.clues.register(
        Clue(id="unread_system_hint", title="新線索", category="系統")
    )
    scene.ctx.state.clues.refresh(scene.ctx.state)
    scene._rebuild_widgets()
    surface = pygame.Surface(size, pygame.SRCALPHA)
    scene.update(0.016, _key(pygame.K_DOWN))
    scene.draw(surface)

    buttons = [
        *scene._top_buttons,
        *scene._buttons,
        scene._travel_btn,
    ]
    assert all(button is not None for button in buttons)
    concrete_buttons = [button for button in buttons if button is not None]
    interactive_rects = [
        *(button.rect for button in concrete_buttons),
        *(rect for rect, _npc_id in scene._npc_cards),
    ]
    assert interactive_rects
    assert all(rect.width >= 44 and rect.height >= 44 for rect in interactive_rects)
    assert all(surface.get_rect().contains(rect) for rect in interactive_rects)
    assert all(
        scene._info_rect.contains(rect)
        for rect in [
            *(button.rect for button in scene._buttons),
            scene._travel_btn.rect,
            *(rect for rect, _npc_id in scene._npc_cards),
        ]
    )

    primary = [button for button in concrete_buttons if button.style == "primary"]
    assert primary == [scene._travel_btn]
    assert scene._travel_btn.label == "行程地圖（1 個地點）"
    assert all(
        button.style == "ghost"
        for button in concrete_buttons
        if button is not scene._travel_btn
    )
    assert scene.describe()["focus"]["id"] == "travel"

    ring = scene._travel_btn.rect.inflate(6, 6)
    ring.clamp_ip(surface.get_rect().inflate(-2, -2))
    assert (
        surface.get_at((ring.centerx, ring.top))[:3]
        == (scene.ctx.theme.accent_warm[:3])
    )

    screenshot = tmp_path / f"exploration-focus-{size[1]}p.png"
    pygame.image.save(surface, screenshot)
    assert screenshot.stat().st_size > 0


def test_exploration_renders_region_name_without_leaking_machine_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene, _calls, _reason = _make_scene((1280, 720), tmp_path)
    scene.ctx.state.map.add_region(Region(id="campus", name="校園"))
    current = scene.ctx.state.map.current
    assert current is not None
    current.region = "campus"

    rendered: list[str] = []
    original_render = scene.ctx.fonts.render

    def record_render(text: str, *args, **kwargs):
        rendered.append(text)
        return original_render(text, *args, **kwargs)

    monkeypatch.setattr(scene.ctx.fonts, "render", record_render)
    scene.draw(pygame.Surface((1280, 720), pygame.SRCALPHA))

    assert "校園" in rendered
    assert "campus" not in rendered

    rendered.clear()
    current.region = "unresolved_internal_region"
    scene.draw(pygame.Surface((1280, 720), pygame.SRCALPHA))
    assert "unresolved_internal_region" not in rendered


def test_tab_arrow_and_enter_activate_actions_and_npc(tmp_path) -> None:
    scene, calls, _reason = _make_scene((1280, 720), tmp_path)

    scene.update(0.016, _key(pygame.K_TAB))
    assert scene.describe()["focus"]["id"] == "top:clues"
    scene.update(0.016, InputState())
    assert scene.describe()["focus"]["id"] == "top:clues"
    assert scene.describe()["focus"]["visible"] is True
    scene.update(
        0.016,
        _key(pygame.K_TAB, mod=pygame.KMOD_SHIFT),
    )
    assert scene.describe()["focus"]["id"] == "npc:heroine"
    scene.update(0.016, _key(pygame.K_TAB))
    assert scene.describe()["focus"]["id"] == "top:clues"
    scene.update(0.016, _key(pygame.K_RETURN))
    assert calls["clues"] == [True]

    scene._focus_id = None
    scene._focus_visible = False
    scene.update(0.016, _key(pygame.K_DOWN))
    assert scene.describe()["focus"]["id"] == "travel"
    scene.update(0.016, _key(pygame.K_RETURN))
    assert calls["map"] == [True]

    scene.update(0.016, _key(pygame.K_UP))
    assert scene.describe()["focus"]["id"] == "action:wait"
    scene.update(0.016, _key(pygame.K_RIGHT))
    assert scene.describe()["focus"]["id"] == "action:scene:inspect_notice"
    scene.update(0.016, _key(pygame.K_RETURN))
    assert calls["scene"] == ["inspect_notice"]

    scene._focus_id = "travel"
    scene._focus_visible = True
    scene.update(0.016, _key(pygame.K_DOWN))
    assert scene.describe()["focus"]["id"] == "npc:heroine"
    scene.update(0.016, _key(pygame.K_RETURN))
    assert calls["npc"] == ["heroine"]

    scene._focus_id = "travel"
    scene._focus_visible = True
    save_input = _key(pygame.K_s)
    save_input.nav = 1
    scene.update(0.016, save_input)
    assert calls["save"] == [True]
    assert scene.describe()["focus"]["id"] == "travel"


def test_locked_wait_reason_is_visible_on_focus_and_confirmed_on_enter(
    tmp_path,
    monkeypatch,
) -> None:
    scene, calls, reason = _make_scene((1280, 720), tmp_path)
    scene.update(0.016, _key(pygame.K_DOWN))
    scene.update(0.016, _key(pygame.K_UP))

    focus = scene.describe()["focus"]
    assert focus == {
        "id": "action:wait",
        "label": "等下個時段",
        "visible": True,
        "enabled": False,
        "reason": reason,
    }
    assert scene._focused_action_hint() is not None
    assert scene._focused_action_hint()[1] == reason

    rendered_text: list[str] = []
    original_render = scene.ctx.fonts.render

    def _record_render(text, *args, **kwargs):
        rendered_text.append(text)
        return original_render(text, *args, **kwargs)

    monkeypatch.setattr(scene.ctx.fonts, "render", _record_render)
    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    scene.draw(surface)
    assert reason in rendered_text
    screenshot = tmp_path / "exploration-locked-focus.png"
    pygame.image.save(surface, screenshot)
    assert screenshot.stat().st_size > 0

    scene.update(0.016, _key(pygame.K_RETURN))
    assert calls["wait"] == []
    assert scene.ctx.state.meta["__pending_toasts__"] == [
        (
            "notice",
            "等下個時段",
            reason,
        )
    ]
    rendered_text.clear()
    scene.draw(surface)
    assert reason in rendered_text


def test_semantic_focus_survives_rebuild_and_clears_with_its_action(
    tmp_path,
) -> None:
    scene, _calls, _reason = _make_scene((1280, 720), tmp_path)
    scene._focus_id = "action:scene:inspect_notice"
    scene._focus_visible = True

    scene.ctx.state.events.set_flag("unrelated_revision", True)
    scene.update(0.016, InputState())
    assert scene.describe()["focus"]["id"] == "action:scene:inspect_notice"

    scene.ctx.state.story.played.add("inspect_notice")
    scene.update(0.016, InputState())
    assert scene.describe()["focus"] is None
    assert scene._focus_visible is False


def test_exploration_ui_copy_localizes_with_live_shortcuts(
    tmp_path,
    monkeypatch,
) -> None:
    localization = Localization(
        affection_levels=[{"min": -999, "label": "Stranger"}],
    )
    localization.ui.update({
        "menu_short": "Menu",
        "clues": "Clues",
        "menu_item_with_binding": "{label} [{binding}]",
        "menu_item_with_binding_unread": (
            "{label} [{binding}] · {count} new"
        ),
        "go_to_location": "Go to {location}",
        "new_discovery": "New discovery",
        "complete_current_objective_first": (
            "Complete your current objective first."
        ),
        "actions_heading": "Actions",
        "travel_heading": "Travel",
        "current_objective": "Current objective: {objective}",
        "affection_summary": "Bond {level} · {value}",
        "show_ui_hint": "Click / Press {binding} to show the UI",
        "travel_map": "Journey Map",
    })
    scene, calls, _reason = _make_scene(
        (1280, 720),
        tmp_path,
        localization=localization,
        input_bindings={
            "open_menu": ["g"],
            "open_clues": ["c"],
            "hide_ui": ["u"],
        },
    )
    scene.ctx.state.clues.register(Clue(
        id="next_step",
        title="Read the notice",
        category="main",
    ))
    scene.ctx.state.clues.refresh(scene.ctx.state)
    library = scene.ctx.state.map.locations["library"]
    library.name = "Library"
    library.npcs.append(NPCPresence(npc_id="heroine"))
    scene.get_map_availability = lambda: (False, None)
    scene._rebuild_widgets()

    assert [button.label for button in scene._top_buttons] == [
        "Clues [C] · 1 new",
        "Menu [G]",
    ]
    assert "Go to Library" in [button.label for button in scene._buttons]
    assert scene._discovery_text == "New discovery"

    scene._request_map()
    assert calls["map"] == []
    assert scene.ctx.state.meta["__pending_toasts__"][-1] == (
        "notice",
        "Journey Map",
        "Complete your current objective first.",
    )

    rendered_text: list[str] = []
    original_render = scene.ctx.fonts.render

    def _record_render(text, *args, **kwargs):
        rendered_text.append(text)
        return original_render(text, *args, **kwargs)

    monkeypatch.setattr(scene.ctx.fonts, "render", _record_render)
    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    scene.draw(surface)
    scene._draw_hidden_hint(surface)

    assert "Actions" in rendered_text
    assert "Travel" in rendered_text
    assert "Current objective: Read the notice" in rendered_text
    assert "Bond Stranger · 0" in rendered_text
    assert "Click / Press U to show the UI" in rendered_text
