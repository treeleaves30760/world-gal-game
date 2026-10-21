"""Responsive grid, focus, and fullscreen contracts for CGGalleryScene."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.cg_gallery_spec import CGGalleryManifest
from world_gal_game.core.game_state import GameState
from world_gal_game.core.localization import Localization
from world_gal_game.npc.npc_base import NPCRegistry
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.cg_gallery_scene import CGGalleryScene
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.theme import default_theme


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


def _make_scene(
    size: tuple[int, int],
    tmp_path,
    *,
    unlocked_indices: tuple[int, ...] = (0, 2, 5, 16),
    manifest: CGGalleryManifest | None = None,
) -> tuple[CGGalleryScene, list[str], list[str]]:
    cg_dir = tmp_path / "assets" / "cgs"
    cg_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for index in range(17):
        name = (
            "event_scene_with_a_very_long_authored_filename_"
            f"{index:02}.png"
        )
        path = f"assets/cgs/{name}"
        paths.append(path)
        art = pygame.Surface((320, 180))
        art.fill((
            (index * 31) % 255,
            (index * 71) % 255,
            (index * 113) % 255,
        ))
        pygame.image.save(art, cg_dir / name)

    config = EngineConfig(
        screen_size=size,
        writable_root_override=tmp_path / "preferences",
    )
    state = GameState()
    for index in unlocked_indices:
        state.cg_gallery.unlock(paths[index])
    if manifest is not None:
        state.meta["__cg_gallery_manifest__"] = manifest
    fonts = FontRegistry(
        candidates=config.font_candidates,
        scale=max(1.0, size[1] / 720.0),
    )
    ctx = SceneContext(
        config=config,
        state=state,
        npcs=NPCRegistry(),
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=AssetManager(pack_root=tmp_path),
        fonts=fonts,
        theme=default_theme(),
        localization=Localization(),
        screen_size=size,
    )
    calls: list[str] = []
    scene = CGGalleryScene(ctx)
    scene.enter(on_close=lambda: calls.append("close"))
    return scene, calls, paths


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


def test_media_authority_hides_forged_unlocked_gallery_path(tmp_path) -> None:
    from world_gal_game.core.media_authority import build_media_authority

    scene, _calls, paths = _make_scene((1280, 720), tmp_path)
    forged = "/tmp/save-forged-cg.png"
    scene.ctx.state.cg_gallery.unlock(forged)
    scene.ctx.state.meta["__pack_media_authority__"] = build_media_authority(
        scene.ctx.state.story,
        additional_cg_ids=paths,
    )

    discovered = scene._discover_cgs()

    assert forged not in discovered
    assert set(paths).issubset(discovered)


def _screen_card(scene: CGGalleryScene, index: int) -> pygame.Rect:
    card = scene._card_rect(index)
    return card.move(
        scene._scroll.rect.x,
        scene._scroll.rect.y - scene._scroll.scroll_y,
    )


def test_thumbnail_mask_preserves_rounded_card_corners() -> None:
    source = pygame.Surface((80, 45), pygame.SRCALPHA)
    source.fill((230, 20, 40, 255))

    rounded = CGGalleryScene._rounded_thumbnail(
        source,
        source.get_size(),
        12,
    )

    assert rounded.get_at((0, 0)).a == 0
    assert rounded.get_at((40, 22)) == pygame.Color(230, 20, 40, 255)


@pytest.mark.parametrize(
    ("size", "expected_columns"),
    [
        ((1280, 720), 4),
        ((1920, 1080), 4),
        ((800, 450), 3),
        ((640, 360), 2),
    ],
)
def test_header_and_16_by_9_grid_scale_without_overlap(
    size: tuple[int, int],
    expected_columns: int,
    tmp_path,
) -> None:
    scene, _calls, paths = _make_scene(size, tmp_path)
    density = max(1.0, size[1] / 720.0)
    viewport = pygame.Rect((0, 0), size)
    surface = pygame.Surface(size, pygame.SRCALPHA)
    scene.draw(surface)

    assert viewport.contains(scene._panel_rect)
    assert scene._panel_rect.contains(scene._title_rect)
    assert scene._panel_rect.contains(scene._counter_rect)
    assert scene._panel_rect.contains(scene.close_btn.rect)
    assert scene._panel_rect.contains(scene._scroll.rect)
    assert scene._title_rect.bottom <= scene._header_divider_y
    assert scene._counter_rect.bottom <= scene._header_divider_y
    assert scene.close_btn.rect.bottom <= scene._header_divider_y
    assert scene._header_divider_y < scene._scroll.rect.top
    assert not scene._title_rect.colliderect(scene._counter_rect)
    assert not scene._counter_rect.colliderect(scene.close_btn.rect)
    assert not scene._title_rect.colliderect(scene.close_btn.rect)
    assert scene._counter_text == "4 / 17"

    minimum_target = round(44 * density)
    assert scene.close_btn.rect.width >= minimum_target
    assert scene.close_btn.rect.height >= minimum_target
    assert scene._cell_w >= minimum_target
    assert scene._cell_h >= minimum_target
    assert abs(scene._cell_w * 9 - scene._cell_h * 16) <= 8
    assert scene._grid_columns == expected_columns
    assert scene._caption_h >= scene._caption_line_h

    content_bounds = pygame.Rect(
        0,
        0,
        scene._scroll.rect.width,
        scene._content_height,
    )
    for index, (rect, path, _unlocked) in enumerate(scene._cells):
        assert path == paths[index]
        assert rect.size == (scene._cell_w, scene._cell_h)
        assert content_bounds.contains(scene._card_rect(index))
    for index, (rect, _path, _unlocked) in enumerate(scene._cells[:-1]):
        next_rect = scene._cells[index + 1][0]
        if next_rect.y == rect.y:
            assert rect.right + scene._gap <= next_rect.left
        else:
            assert (
                scene._card_rect(index).bottom + scene._gap
                <= scene._card_rect(index + 1).top
            )

    raw_caption = paths[0].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    caption_font = scene.ctx.fonts.get(scene._caption_font_size)
    displayed = scene._ellipsize(
        raw_caption,
        caption_font,
        scene._cell_w - scene._sp(16),
    )
    assert caption_font.size(displayed)[0] <= scene._cell_w - scene._sp(16)
    assert displayed.endswith("…")

    assert scene._focus == 0
    focused = _screen_card(scene, 0)
    assert scene._scroll.rect.contains(focused)
    assert (
        surface.get_at((focused.centerx, focused.top + 1))[:3]
        == scene.ctx.theme.accent_warm[:3]
    )


def test_keyboard_and_gamepad_focus_scroll_and_respect_locks(
    tmp_path,
) -> None:
    scene, calls, paths = _make_scene((640, 360), tmp_path)

    scene.update(0.0, _key(pygame.K_RIGHT))
    assert scene._focus == 1
    scene.update(0.0, _key(pygame.K_RETURN))
    assert scene._fullscreen is None

    scene.update(0.0, _key(pygame.K_LEFT))
    scene.update(0.0, _key(pygame.K_RETURN))
    assert scene._fullscreen == paths[0]
    scene.update(0.0, _key(pygame.K_ESCAPE))
    assert scene._fullscreen is None
    assert calls == []

    for _ in range(8):
        scene.update(0.0, _key(pygame.K_DOWN))
        assert scene._scroll.rect.contains(
            _screen_card(scene, scene._focus)
        )
    assert scene._focus == 16
    assert scene._scroll.scroll_y > 0

    scene.update(0.0, _key(pygame.K_RETURN))
    assert scene._fullscreen == paths[16]
    scene.update(0.0, _key(pygame.K_ESCAPE))

    gamepad_up = InputState.collect([
        pygame.event.Event(pygame.JOYHATMOTION, value=(0, 1)),
    ])
    gamepad_up.mouse_pos = (-1000, -1000)
    scene.update(0.0, gamepad_up)
    assert scene._focus == 14
    assert scene._scroll.rect.contains(_screen_card(scene, 14))

    gamepad_right = InputState.collect([
        pygame.event.Event(pygame.JOYHATMOTION, value=(1, 0)),
    ])
    gamepad_right.mouse_pos = (-1000, -1000)
    scene.update(0.0, gamepad_right)
    assert scene._focus == 15
    scene.update(0.0, _key(pygame.K_RETURN))
    assert scene._fullscreen is None

    surface = pygame.Surface((640, 360), pygame.SRCALPHA)
    scene.draw(surface)
    focused = _screen_card(scene, scene._focus)
    assert (
        surface.get_at((focused.centerx, focused.top + 1))[:3]
        == scene.ctx.theme.accent_warm[:3]
    )

    scene.update(0.0, _key(pygame.K_ESCAPE))
    assert calls == ["close"]


def test_mouse_keeps_unlocked_and_locked_cell_semantics(tmp_path) -> None:
    scene, calls, paths = _make_scene((1280, 720), tmp_path)
    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    scene.draw(surface)

    locked = _screen_card(scene, 1)
    locked_caption = (
        locked.centerx,
        locked.bottom - scene._caption_h // 2,
    )
    scene.update(0.016, InputState(
        mouse_pos=locked_caption,
        mouse_clicked=True,
    ))
    assert scene._focus == 1
    assert scene._fullscreen is None

    unlocked = _screen_card(scene, 2)
    unlocked_caption = (
        unlocked.centerx,
        unlocked.bottom - scene._caption_h // 2,
    )
    scene.update(0.016, InputState(
        mouse_pos=unlocked_caption,
        mouse_clicked=True,
    ))
    assert scene._focus == 2
    assert scene._fullscreen == paths[2]

    scene.update(0.016, InputState(
        mouse_pos=(640, 360),
        mouse_clicked=True,
    ))
    assert scene._fullscreen is None

    before_scroll = scene._scroll.scroll_y
    scene.update(0.016, InputState(
        mouse_pos=scene._scroll.rect.center,
        mouse_wheel=-4,
    ))
    assert scene._scroll.scroll_y > before_scroll

    scene.update(0.016, InputState(
        mouse_pos=scene.close_btn.rect.center,
        mouse_clicked=True,
    ))
    assert calls == ["close"]


@pytest.mark.parametrize("size", [(1920, 1080), (640, 360)])
def test_fullscreen_contain_view_and_hint_stay_separate_and_in_bounds(
    size: tuple[int, int],
    tmp_path,
) -> None:
    scene, calls, paths = _make_scene(size, tmp_path)
    scene.ctx.localization.ui["cg_close_hint"] = (
        "點擊畫面或按下 Escape 返回 CG 鑑賞列表；"
        "這是一段刻意很長、必須安全收納的提示文字。"
    )
    scene._focus = 0
    assert scene._open_focused() is True

    surface = pygame.Surface(size, pygame.SRCALPHA)
    scene.draw(surface)
    viewport = surface.get_rect()

    assert scene._fullscreen == paths[0]
    assert viewport.contains(scene._fullscreen_image_rect)
    assert viewport.contains(scene._fullscreen_hint_rect)
    assert (
        scene._fullscreen_image_rect.bottom
        < scene._fullscreen_hint_rect.top
    )
    assert (
        scene._fullscreen_hint_rect.height
        >= round(44 * max(1.0, size[1] / 720.0))
    )

    scene.update(0.0, _key(pygame.K_ESCAPE))
    assert scene._fullscreen is None
    assert calls == []


def test_authored_roster_order_hidden_fallback_and_spoiler_safe_describe(
    tmp_path,
) -> None:
    # Build the paths once, then use the same deterministic filenames in the
    # authored roster.  Only already-unlocked unlisted paths may survive an
    # authoritative catalog.
    probe, _calls, paths = _make_scene(
        (1280, 720),
        tmp_path,
        unlocked_indices=(),
    )
    del probe
    manifest = CGGalleryManifest.from_items([
        {
            "asset": paths[2],
            "title": "Locked title must stay private",
            "subtitle": "Locked subtitle",
            "description": "Locked description",
            "order": 10,
        },
        {
            "asset": paths[0],
            "title": "The first memory",
            "subtitle": "Library · Afternoon",
            "description": "A human-authored memory caption.",
            "thumbnail": paths[4],
            "order": 20,
            "character_ids": ["heroine"],
            "chapter_id": "chapter_one",
        },
        {
            "asset": paths[1],
            "title": "Hidden and locked",
            "order": 30,
            "hidden": True,
        },
        {
            "asset": paths[3],
            "title": "Hidden but unlocked",
            "order": 40,
            "hidden": True,
        },
    ])
    scene, _calls, _paths = _make_scene(
        (1280, 720),
        tmp_path,
        unlocked_indices=(0, 3, 16),
        manifest=manifest,
    )

    # path[16] is an old-save/plugin fallback. The other twelve unlisted
    # files on disk are intentionally absent from the authored gallery.
    assert scene._cgs == [paths[2], paths[0], paths[3], paths[16]]
    assert scene._counter_text == "3 / 4"
    assert scene._caption_h >= (
        scene._caption_line_h
        + scene._caption_subtitle_line_h
        + scene._sp(2)
    )

    described = scene.describe()
    assert described["total"] == 4
    locked = described["entries"][0]
    assert locked == {"unlocked": False}
    assert described["focused_index"] == 0
    assert described["focused"] is None
    unlocked = described["entries"][1]
    assert unlocked["title"] == "The first memory"
    assert unlocked["subtitle"] == "Library · Afternoon"
    assert unlocked["description"] == "A human-authored memory caption."
    assert unlocked["thumbnail"] == paths[4]
    assert unlocked["character_ids"] == ["heroine"]
    assert paths[1] not in {
        entry.get("asset") for entry in described["entries"]
    }


def test_explicit_empty_roster_disables_directory_scan_but_keeps_old_unlock(
    tmp_path,
) -> None:
    manifest = CGGalleryManifest.from_items([])
    scene, _calls, paths = _make_scene(
        (1280, 720),
        tmp_path,
        unlocked_indices=(5,),
        manifest=manifest,
    )

    assert scene._cgs == [paths[5]]
    assert scene._counter_text == "1 / 1"
    assert scene.describe()["entries"] == [{
        "asset": paths[5],
        "unlocked": True,
        "title": paths[5].rsplit("/", 1)[-1].rsplit(".", 1)[0],
    }]


def test_grid_uses_thumbnail_and_fullscreen_uses_original(
    tmp_path,
    monkeypatch,
) -> None:
    probe, _calls, paths = _make_scene(
        (1280, 720),
        tmp_path,
        unlocked_indices=(),
    )
    del probe
    manifest = CGGalleryManifest.from_items([{
        "asset": paths[0],
        "title": "Authored memory",
        "subtitle": "A contextual subtitle",
        "description": "A description that belongs only to the open viewer.",
        "thumbnail": paths[1],
    }])
    scene, _calls, _paths = _make_scene(
        (1280, 720),
        tmp_path,
        unlocked_indices=(0,),
        manifest=manifest,
    )
    real_scaled = scene.ctx.assets.scaled
    requested: list[str] = []

    def recording_scaled(path, size, *, fit="contain"):
        requested.append(path)
        return real_scaled(path, size, fit=fit)

    monkeypatch.setattr(scene.ctx.assets, "scaled", recording_scaled)
    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)

    scene.draw(surface)
    assert paths[1] in requested
    assert paths[0] not in requested

    scene._focus = 0
    assert scene._open_focused() is True
    requested.clear()
    scene.draw(surface)
    assert requested == [paths[0]]
    assert scene._fullscreen_info_rect is not None
    assert (
        scene._fullscreen_image_rect.bottom
        < scene._fullscreen_info_rect.top
    )
    assert scene._fullscreen_info_rect.contains(
        scene._fullscreen_hint_rect
    )


@pytest.mark.parametrize(
    ("size", "description_lines"),
    [
        ((640, 360), 0),
        ((1280, 720), 1),
        ((1920, 1080), 2),
    ],
)
def test_metadata_fullscreen_is_bounded_at_shipping_sizes(
    size: tuple[int, int],
    description_lines: int,
    tmp_path,
) -> None:
    probe, _calls, paths = _make_scene(
        size,
        tmp_path,
        unlocked_indices=(),
    )
    del probe
    long_text = (
        "這是一段沒有空格而且刻意很長的全螢幕回憶說明"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        "，必須在任何解析度都不能碰到返回提示或撐破畫面。"
    ) * 4
    manifest = CGGalleryManifest.from_items([{
        "asset": paths[0],
        "title": long_text,
        "subtitle": long_text,
        "description": long_text,
    }])
    scene, _calls, _paths = _make_scene(
        size,
        tmp_path,
        unlocked_indices=(0,),
        manifest=manifest,
    )
    scene._focus = 0
    assert scene._open_focused()
    surface = pygame.Surface(size, pygame.SRCALPHA)
    scene.draw(surface)

    viewport = surface.get_rect()
    assert viewport.contains(scene._fullscreen_image_rect)
    assert viewport.contains(scene._fullscreen_info_rect)
    assert scene._fullscreen_info_rect.contains(
        scene._fullscreen_hint_rect
    )
    text_width = (
        scene._fullscreen_info_rect.width
        - scene._sp(16) * 2
        - scene._fullscreen_hint_rect.width
        - scene._sp(14)
    )
    lines = scene._wrap_ellipsized(
        long_text,
        scene.ctx.fonts.get(scene._fullscreen_meta_font_size),
        text_width,
        description_lines,
    )
    assert len(lines) == description_lines
    assert all(
        scene.ctx.fonts.get(
            scene._fullscreen_meta_font_size
        ).size(line)[0] <= text_width
        for line in lines
    )

    # Confirm/A is symmetrical with Esc/B while the viewer is open.
    scene.update(0.0, _key(pygame.K_RETURN))
    assert scene._fullscreen is None
