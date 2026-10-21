"""Pack-authored title branding, interaction, and rendering contracts."""
from __future__ import annotations

import os

import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    yield
    pygame.quit()


class _Assets:
    def __init__(
        self,
        *,
        logo: pygame.Surface | Exception | None = None,
    ):
        self.background = pygame.Surface((1280, 720), pygame.SRCALPHA)
        self.background.fill((80, 100, 140, 255))
        self.background.set_alpha(173)
        self.logo = logo

    def stop_ambient(self, *, fade_ms=0):
        return None

    def play_music(self, path, *, volume=0.6):
        return None

    def scaled(self, path, size, *, fit="cover"):
        return self.background

    def has_image(self, path):
        return self.logo is not None

    def image(self, path, *, fallback_size=(640, 240)):
        if isinstance(self.logo, Exception):
            raise self.logo
        return self.logo


def _make_scene(
    size=(1280, 720),
    *,
    presentation=None,
    assets=None,
    callbacks=None,
):
    from world_gal_game.config import EngineConfig
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.localization import Localization
    from world_gal_game.scenes.base import SceneContext
    from world_gal_game.scenes.title import TitleScene
    from world_gal_game.ui.fonts import FontRegistry
    from world_gal_game.ui.theme import default_theme

    config = EngineConfig(
        title="物語集",
        subtitle="A campus mystery",
        screen_size=size,
    )
    fonts = FontRegistry(candidates=(), scale=max(1.0, size[1] / 720.0))
    state = GameState()
    state.player.name = "原姓名"
    ctx = SceneContext(
        config=config,
        state=state,
        npcs=None,  # type: ignore[arg-type]
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=assets or _Assets(),  # type: ignore[arg-type]
        fonts=fonts,
        theme=default_theme(),
        localization=Localization(),
        screen_size=size,
    )
    callbacks = callbacks if callbacks is not None else []

    def callback():
        callbacks.append("new")

    def noop():
        return None

    scene = TitleScene(ctx)
    scene.enter(
        bg="assets/ui/title.png",
        title=config.title,
        subtitle=config.subtitle,
        version="2.0",
        presentation=presentation,
        on_continue=noop,
        on_new_game=callback,
        on_load=noop,
        on_quit=noop,
        on_cg_gallery=noop,
        on_music_room=noop,
        on_endings=noop,
        on_flowchart=noop,
        on_credits=noop,
        on_settings=noop,
    )
    return scene, callbacks


def _assert_brand_layout_safe(scene, size):
    canvas = pygame.Rect((0, 0), size)
    assert canvas.contains(scene._panel_rect)
    assert canvas.contains(scene._logo_rect)
    assert canvas.contains(scene._byline_rect)
    assert scene._version_rect is not None
    assert canvas.contains(scene._version_rect)
    assert scene.menu is not None
    assert scene._panel_rect.contains(scene.menu.rect)
    for index in range(len(scene.menu.items)):
        row = pygame.Rect(
            scene.menu.rect.x,
            scene.menu.rect.y + index * scene.menu.row_h,
            scene.menu.rect.width,
            scene.menu.row_h,
        )
        assert scene._panel_rect.contains(row)
    surface = pygame.Surface(size, pygame.SRCALPHA)
    scene.draw(surface)


@pytest.mark.parametrize(
    "size",
    [
        (800, 450),
        (960, 540),
        (1280, 720),
        (1920, 1080),
        (3840, 2160),
    ],
)
@pytest.mark.parametrize(
    "layout",
    ["centered", "left_rail", "right_rail"],
)
def test_brand_layouts_stay_safe_at_supported_resolutions(size, layout):
    from world_gal_game.core.title_screen_spec import TitleScreenSpec

    spec = TitleScreenSpec(
        layout=layout,
        panel_style="rail" if layout != "centered" else "glass",
        name_entry="on_new_game",
        wordmark_style="editorial",
        tagline="在校園裡，選擇下一段故事",
    )
    scene, _ = _make_scene(size, presentation=spec)

    assert scene.name_input is None
    _assert_brand_layout_safe(scene, size)
    if layout != "centered":
        assert scene.menu.row_h >= 44
        assert not scene._panel_rect.colliderect(scene._byline_rect)
        assert not scene._panel_rect.colliderect(scene._version_rect)
    else:
        assert scene._logo_rect.bottom < scene._panel_rect.top
        assert scene._panel_rect.bottom < scene._byline_rect.top

    scene._start_new()
    assert scene.name_input is not None
    assert scene._panel_rect.contains(scene._prompt_heading_rect)
    assert scene._panel_rect.contains(scene._prompt_rect)
    assert scene._panel_rect.contains(scene.name_input.rect)
    _assert_brand_layout_safe(scene, size)


def test_on_new_game_opens_registration_and_cancel_is_non_destructive():
    from world_gal_game.core.title_screen_spec import TitleScreenSpec
    from world_gal_game.ui.input import InputState

    scene, callbacks = _make_scene(
        presentation=TitleScreenSpec(
            layout="left_rail",
            panel_style="rail",
            name_entry="on_new_game",
        ),
    )
    assert scene.ctx.state.player.name == "原姓名"
    assert scene.describe()["mode"] == "main"
    assert scene.name_input is None

    scene._start_new()
    assert scene.describe()["mode"] == "name_prompt"
    assert scene.describe()["name_prompt_title"] == "新生指南 · 入學登記"
    assert scene.name_input is not None
    assert scene.name_input.focused is True
    scene.name_input.text = "未提交姓名"
    scene.update(0.016, InputState(cancel=True))

    assert scene.describe()["mode"] == "main"
    assert scene.name_input is None
    assert scene.ctx.state.player.name == "原姓名"
    assert callbacks == []


def test_name_prompt_does_not_treat_printable_back_binding_as_cancel():
    from world_gal_game.core.title_screen_spec import TitleScreenSpec
    from world_gal_game.ui.input import InputState
    from world_gal_game.ui.input_bindings import InputAction

    scene, callbacks = _make_scene(
        presentation=TitleScreenSpec(name_entry="on_new_game"),
    )
    scene._start_new()
    printable = pygame.event.Event(
        pygame.KEYDOWN,
        key=pygame.K_x,
        unicode="x",
    )

    scene.update(
        0.016,
        InputState(
            events=[printable],
            actions_pressed={InputAction.BACK},
        ),
    )

    assert scene.describe()["mode"] == "name_prompt"
    assert scene.ctx.state.player.name == "原姓名"
    assert callbacks == []


@pytest.mark.parametrize("layout", ["left_rail", "right_rail"])
@pytest.mark.parametrize(
    "size",
    [
        (800, 450),
        (960, 540),
        (1280, 720),
        (1920, 1080),
        (3840, 2160),
    ],
)
def test_inline_name_rail_keeps_six_touch_rows_when_they_fit(size, layout):
    from world_gal_game.core.title_screen_spec import TitleScreenSpec

    scene, _ = _make_scene(
        size,
        presentation=TitleScreenSpec(
            layout=layout,
            panel_style="rail",
            name_entry="inline",
        ),
    )
    assert scene.name_input is not None
    assert scene.menu is not None
    assert len(scene.menu.items) == 6
    assert scene.menu.row_h >= 44
    _assert_brand_layout_safe(scene, size)


def test_registration_confirm_commits_name_and_callback_exactly_once():
    from world_gal_game.core.title_screen_spec import TitleScreenSpec
    from world_gal_game.ui.input import InputState

    scene, callbacks = _make_scene(
        presentation=TitleScreenSpec(name_entry="on_new_game"),
    )
    scene._start_new()
    assert scene.name_input is not None
    scene.name_input.text = "  新主角  "
    scene.update(0.016, InputState(confirm=True))
    scene._confirm_new_game()

    assert scene.ctx.state.player.name == "新主角"
    assert callbacks == ["new"]


def test_extras_cancel_returns_to_main_for_keyboard_or_gamepad_semantics():
    from world_gal_game.core.title_screen_spec import TitleScreenSpec
    from world_gal_game.ui.input import InputState

    scene, _ = _make_scene(
        presentation=TitleScreenSpec(name_entry="on_new_game"),
    )
    scene._open_extras()
    assert scene.describe()["mode"] == "extras"
    scene.update(0.016, InputState(cancel=True))
    assert scene.describe()["mode"] == "main"

    scene._open_extras()
    escape = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)
    scene.update(0.016, InputState(events=[escape]))
    assert scene.describe()["mode"] == "main"


@pytest.mark.parametrize("branded", [False, True])
def test_background_opacity_never_mutates_asset_manager_cache(branded):
    from world_gal_game.core.title_screen_spec import TitleScreenSpec

    assets = _Assets()
    scene, _ = _make_scene(
        presentation=(
            TitleScreenSpec(background_opacity=0.2)
            if branded else None
        ),
        assets=assets,
    )
    original_alpha = assets.background.get_alpha()
    scene.draw(pygame.Surface((1280, 720), pygame.SRCALPHA))
    assert original_alpha == 173
    assert assets.background.get_alpha() == original_alpha


@pytest.mark.parametrize(
    ("logo", "source"),
    [
        (None, "text"),
        (pygame.error("broken"), "text"),
        (pygame.Surface((480, 160), pygame.SRCALPHA), "image"),
    ],
)
def test_optional_logo_uses_valid_image_and_falls_back_to_text(logo, source):
    from world_gal_game.core.title_screen_spec import TitleScreenSpec

    scene, _ = _make_scene(
        presentation=TitleScreenSpec(
            logo_image="assets/ui/logo.png",
            name_entry="on_new_game",
        ),
        assets=_Assets(logo=logo),
    )
    assert scene.describe()["wordmark_source"] == source
    _assert_brand_layout_safe(scene, (1280, 720))


def test_4k_image_wordmark_and_rail_keep_their_visual_scale():
    """Raster branding must scale with the same density as typography."""
    from world_gal_game.core.title_screen_spec import TitleScreenSpec

    size = (3840, 2160)
    scene, _ = _make_scene(
        size,
        presentation=TitleScreenSpec(
            layout="left_rail",
            panel_style="rail",
            logo_image="assets/ui/logo.png",
            tagline="在校園裡，選擇下一段故事",
            name_entry="on_new_game",
        ),
        assets=_Assets(
            logo=pygame.Surface((2400, 585), pygame.SRCALPHA),
        ),
    )

    assert scene._logo_image_rect is not None
    assert scene._logo_image_rect.width >= round(size[0] * 0.20)
    assert scene._panel_rect.width >= round(size[0] * 0.30)
    _assert_brand_layout_safe(scene, size)


@pytest.mark.parametrize(
    ("size", "border_width", "accent_width"),
    [
        ((1280, 720), 1, 3),
        ((1920, 1080), 2, 5),
        ((3840, 2160), 3, 9),
    ],
)
def test_brand_chrome_strokes_scale_with_canvas_density(
    size,
    border_width,
    accent_width,
):
    """Rail structure must not collapse to one physical pixel at 4K."""
    from world_gal_game.core.title_screen_spec import TitleScreenSpec

    scene, _ = _make_scene(
        size,
        presentation=TitleScreenSpec(
            layout="left_rail",
            panel_style="rail",
            name_entry="on_new_game",
        ),
    )

    assert scene._chrome_stroke() == border_width
    assert scene._chrome_stroke(3) == accent_width


def test_footer_copy_and_describe_expose_authoring_state():
    from world_gal_game.core.title_screen_spec import TitleScreenSpec

    scene, _ = _make_scene(
        presentation=TitleScreenSpec(
            name_entry="on_new_game",
            show_engine_credit=False,
            footer_text="© 2026 Example Studio",
        ),
    )
    description = scene.describe()
    assert scene._footer_display_text == "© 2026 Example Studio"
    assert description["mode"] == "main"
    assert description["menu"] == description["menu_labels"]
    assert description["selected"] == description["menu"][0]
    assert description["name_prompt"] is False
