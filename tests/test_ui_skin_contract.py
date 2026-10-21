"""Pack-owned UI chrome selector, isolation, cache, and discovery contract."""

from __future__ import annotations

from dataclasses import replace
import os
import json
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from world_gal_game.core.ui_skin_spec import (
    UISkinSpec,
    parse_ui_skin_spec,
)
from world_gal_game.dev.capability_manifest import (
    build_manifest,
    manifest_json,
    schema_document,
)
from world_gal_game.plugins import restore, snapshot, widget
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.system_chrome import (
    ChromeRequest,
    SystemChromeHost,
    UI_CHROME_PROTOCOL,
    build_system_chrome,
)
from world_gal_game.ui.theme import default_theme
from world_gal_game.validator import validate_pack


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


@pytest.fixture(autouse=True)
def _clean_registries():
    before = snapshot()
    yield
    restore(before)


def _services():
    return {
        "assets": None,
        "fonts": FontRegistry(("Arial",)),
        "theme": default_theme(),
    }


class _MutableVisualPolicy:
    def __init__(self, profile: str = "standard") -> None:
        self.profile = profile


def _write_meta(root: Path, data: object) -> None:
    path = root / "content" / "meta.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _skin_issues(root: Path):
    return [issue for issue in validate_pack(root) if issue.path.startswith("ui_skin")]


def test_missing_block_preserves_legacy_none() -> None:
    assert parse_ui_skin_spec({"title": "Legacy"}) is None


def test_selector_preserves_renderer_and_opaque_options() -> None:
    spec = parse_ui_skin_spec(
        {
            "ui_skin": {
                "renderer": "pack_system_chrome",
                "options": {
                    "line_weight": 2,
                    "screens": {"menu": {"label": "INDEX"}},
                },
            },
        }
    )

    assert spec == UISkinSpec(
        renderer="pack_system_chrome",
        options={
            "line_weight": 2,
            "screens": {"menu": {"label": "INDEX"}},
        },
    )


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"renderer": ""},
        {"renderer": "UpperCase"},
        {"renderer": " renderer"},
        {"renderer": 42},
        {"renderer": "valid_renderer", "options": []},
        {"renderer": "valid_renderer", "unknown": True},
    ],
)
def test_present_malformed_selector_is_rejected(raw: object) -> None:
    with pytest.raises(ValidationError):
        parse_ui_skin_spec({"ui_skin": raw})


def test_host_caches_finite_requests_and_returns_exact_surface() -> None:
    calls: list[ChromeRequest] = []

    @widget(
        "recording_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class RecordingChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            calls.append(request)
            surface = pygame.Surface(request.size, pygame.SRCALPHA)
            surface.fill((11, 22, 33, 255))
            return surface

    host = SystemChromeHost(
        UISkinSpec(renderer="recording_chrome"),
        **_services(),
    )
    request = ChromeRequest(
        part="button",
        screen="menu",
        variant="action",
        size=(180, 52),
        emphasis=2,
    )

    for _ in range(120):
        rendered = host.render(request)
        assert rendered is not None
        assert rendered.get_size() == (180, 52)
    assert len(calls) == 1
    assert host.cache_size == 1


def test_host_invalidates_composed_surfaces_after_font_reconfigure() -> None:
    calls = 0
    fonts = FontRegistry(())

    @widget(
        "font_revision_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class FontRevisionChrome:
        def __init__(self, *, fonts, **_):
            self.fonts = fonts

        def render(self, request):
            nonlocal calls
            calls += 1
            surface = pygame.Surface(request.size, pygame.SRCALPHA)
            surface.blit(
                self.fonts.render("Aa", 14, (255, 255, 255)),
                (0, 0),
            )
            return surface

    host = SystemChromeHost(
        UISkinSpec(renderer="font_revision_chrome"),
        assets=None,
        fonts=fonts,
        theme=default_theme(),
    )
    request = ChromeRequest(
        part="panel",
        screen="menu",
        variant="main",
        size=(80, 32),
    )

    first = host.render(request)
    fonts.reconfigure((), bundled=None)
    assert host.render(request) is first
    assert calls == 1

    fonts.reconfigure(("DejaVu Sans",), bundled=None)
    second = host.render(request)

    assert second is not None
    assert second is not first
    assert calls == 2
    assert host.cache_size == 1


def test_host_invalidates_composed_surfaces_after_explicit_asset_clear() -> None:
    calls = 0
    assets = AssetManager()

    @widget(
        "asset_revision_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class AssetRevisionChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            nonlocal calls
            calls += 1
            return pygame.Surface(request.size, pygame.SRCALPHA)

    host = SystemChromeHost(
        UISkinSpec(renderer="asset_revision_chrome"),
        assets=assets,
        fonts=FontRegistry(()),
        theme=default_theme(),
    )
    request = ChromeRequest(
        part="panel",
        screen="menu",
        variant="main",
        size=(80, 32),
    )

    first = host.render(request)
    assert host.render(request) is first
    assert calls == 1

    assets.clear_image_cache()
    second = host.render(request)

    assert second is not None
    assert second is not first
    assert calls == 2
    assert host.cache_size == 1


def test_standard_profile_preserves_legacy_request_and_surface_bytes() -> None:
    calls: list[ChromeRequest] = []

    @widget(
        "legacy_profile_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class LegacyProfileChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            calls.append(request)
            surface = pygame.Surface(request.size, pygame.SRCALPHA)
            surface.fill((11, 22, 33, 255))
            return surface

    host = SystemChromeHost(
        UISkinSpec(renderer="legacy_profile_chrome"),
        **_services(),
    )
    implicit = ChromeRequest(
        part="button",
        screen="menu",
        variant="action",
        size=(3, 2),
    )
    explicit = ChromeRequest(
        part="button",
        screen="menu",
        variant="action",
        size=(3, 2),
        visual_profile="standard",
    )

    first = host.render(implicit)
    second = host.render(explicit)

    assert implicit == explicit
    assert first is second
    assert first is not None
    assert pygame.image.tobytes(first, "RGBA") == bytes((11, 22, 33, 255)) * 6
    assert calls == [explicit]


def test_live_visual_profile_segregates_cache_and_switches_back() -> None:
    calls: list[str] = []

    @widget(
        "profile_aware_chrome",
        plugin_id="test_skin",
        signature={
            "protocol": UI_CHROME_PROTOCOL,
            "visual_profiles": ["standard", "high_contrast"],
        },
    )
    class ProfileAwareChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            calls.append(request.visual_profile)
            surface = pygame.Surface(request.size, pygame.SRCALPHA)
            if request.visual_profile == "standard":
                surface.fill((20, 30, 40, 255))
            else:
                surface.fill((240, 250, 255, 255))
            return surface

    visual = _MutableVisualPolicy()
    host = build_system_chrome(
        UISkinSpec(renderer="profile_aware_chrome"),
        visual=visual,
        **_services(),
    )
    assert host is not None
    request = ChromeRequest(
        part="panel",
        screen="settings",
        variant="system",
        size=(20, 10),
    )

    standard = host.render(request)
    visual.profile = "high_contrast"
    high_contrast = host.render(request)
    visual.profile = "standard"
    standard_again = host.render(request)

    assert standard is not None
    assert high_contrast is not None
    assert standard.get_at((0, 0)) == (20, 30, 40, 255)
    assert high_contrast.get_at((0, 0)) == (240, 250, 255, 255)
    assert standard_again is standard
    assert calls == ["standard", "high_contrast"]
    assert host.cache_size == 2


def test_unsupported_high_contrast_falls_back_without_disabling_renderer() -> None:
    calls: list[str] = []

    @widget(
        "standard_only_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class StandardOnlyChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            calls.append(request.visual_profile)
            return pygame.Surface(request.size, pygame.SRCALPHA)

    visual = _MutableVisualPolicy()
    host = SystemChromeHost(
        UISkinSpec(renderer="standard_only_chrome"),
        visual=visual,
        **_services(),
    )
    request = ChromeRequest(
        part="panel",
        screen="settings",
        variant="system",
        size=(20, 10),
    )

    standard = host.render(request)
    visual.profile = "high_contrast"
    assert host.render(request) is None
    assert host.active is True
    assert host.cache_size == 1
    visual.profile = "standard"
    assert host.render(request) is standard
    assert calls == ["standard"]


def test_supported_high_contrast_request_reaches_renderer_without_policy() -> None:
    calls: list[ChromeRequest] = []

    @widget(
        "explicit_profile_chrome",
        plugin_id="test_skin",
        signature={
            "protocol": UI_CHROME_PROTOCOL,
            "visual_profiles": ["standard", "high_contrast"],
        },
    )
    class ExplicitProfileChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            calls.append(request)
            return pygame.Surface(request.size, pygame.SRCALPHA)

    host = SystemChromeHost(
        UISkinSpec(renderer="explicit_profile_chrome"),
        **_services(),
    )
    request = ChromeRequest(
        part="focus",
        screen="menu",
        variant="keyboard",
        size=(80, 4),
        visual_profile="high_contrast",
    )

    assert host.render(request) is not None
    assert calls == [request]


def test_paint_size_correction_preserves_visual_profile() -> None:
    calls: list[ChromeRequest] = []

    @widget(
        "profile_paint_chrome",
        plugin_id="test_skin",
        signature={
            "protocol": UI_CHROME_PROTOCOL,
            "visual_profiles": ["standard", "high_contrast"],
        },
    )
    class ProfilePaintChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            calls.append(request)
            return pygame.Surface(request.size, pygame.SRCALPHA)

    host = SystemChromeHost(
        UISkinSpec(renderer="profile_paint_chrome"),
        **_services(),
    )
    target = pygame.Surface((40, 30), pygame.SRCALPHA)
    rect = pygame.Rect(3, 4, 20, 12)
    request = ChromeRequest(
        part="focus",
        screen="menu",
        variant="keyboard",
        size=(1, 1),
        visual_profile="high_contrast",
    )

    assert host.paint(target, rect, request) is True
    assert calls[0].size == rect.size
    assert calls[0].visual_profile == "high_contrast"


@pytest.mark.parametrize("profile", ["contrast", "", None, 1])
def test_chrome_request_rejects_invalid_visual_profile(profile: object) -> None:
    with pytest.raises(ValueError, match="visual_profile"):
        ChromeRequest(
            part="panel",
            screen="settings",
            variant="system",
            size=(20, 10),
            visual_profile=profile,  # type: ignore[arg-type]
        )


def test_high_contrast_renderer_exception_remains_fault_isolated() -> None:
    calls: list[str] = []

    @widget(
        "failing_profile_chrome",
        plugin_id="test_skin",
        signature={
            "protocol": UI_CHROME_PROTOCOL,
            "visual_profiles": ["standard", "high_contrast"],
        },
    )
    class FailingProfileChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            calls.append(request.visual_profile)
            if request.visual_profile == "high_contrast":
                raise RuntimeError("high contrast paint failed")
            return pygame.Surface(request.size, pygame.SRCALPHA)

    host = SystemChromeHost(
        UISkinSpec(renderer="failing_profile_chrome"),
        **_services(),
    )
    standard = ChromeRequest(
        part="panel",
        screen="settings",
        variant="system",
        size=(20, 10),
    )
    high_contrast = ChromeRequest(
        part="panel",
        screen="settings",
        variant="system",
        size=(20, 10),
        visual_profile="high_contrast",
    )

    assert host.render(standard) is not None
    assert host.cache_size == 1
    assert host.render(high_contrast) is None
    assert host.active is False
    assert host.cache_size == 0
    assert host.render(standard) is None
    assert calls == ["standard", "high_contrast"]


def test_cache_is_bounded_across_resizes() -> None:
    @widget(
        "bounded_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class BoundedChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            return pygame.Surface(request.size, pygame.SRCALPHA)

    host = SystemChromeHost(
        UISkinSpec(renderer="bounded_chrome"),
        max_cache_entries=8,
        **_services(),
    )
    for width in range(100, 140):
        host.render(
            ChromeRequest(
                part="panel",
                screen="settings",
                variant="system",
                size=(width, 80),
            )
        )
    assert host.cache_size == 8


def test_cache_byte_budget_uses_surface_pitch_and_lru_eviction() -> None:
    calls: list[tuple[int, int]] = []

    @widget(
        "byte_bounded_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class ByteBoundedChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            calls.append(request.size)
            return pygame.Surface(request.size, pygame.SRCALPHA)

    probe = pygame.Surface((10, 10), pygame.SRCALPHA)
    surface_bytes = probe.get_pitch() * probe.get_height()
    host = SystemChromeHost(
        UISkinSpec(renderer="byte_bounded_chrome"),
        max_cache_bytes=surface_bytes * 2,
        **_services(),
    )

    def request(width: int) -> ChromeRequest:
        return ChromeRequest(
            part="panel",
            screen="settings",
            variant="system",
            size=(width, 10),
        )

    first = request(10)
    second = request(9)
    third = request(8)
    host.render(first)
    host.render(second)
    host.render(first)  # Refresh first so second is the LRU entry.
    host.render(third)

    expected_bytes = (
        pygame.Surface(first.size, pygame.SRCALPHA).get_pitch() * first.size[1]
        + pygame.Surface(third.size, pygame.SRCALPHA).get_pitch() * third.size[1]
    )
    assert calls == [first.size, second.size, third.size]
    assert host.cache_size == 2
    assert host.cache_bytes == expected_bytes

    host.render(first)
    assert calls == [first.size, second.size, third.size]
    host.render(second)
    assert calls == [first.size, second.size, third.size, second.size]
    assert host.cache_bytes <= surface_bytes * 2


def test_default_budget_holds_complete_4k_choice_working_set() -> None:
    calls: list[ChromeRequest] = []

    @widget(
        "four_k_choice_chrome",
        plugin_id="test_skin",
        signature={
            "protocol": UI_CHROME_PROTOCOL,
            "visual_profiles": ["standard", "high_contrast"],
        },
    )
    class FourKChoiceChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            calls.append(request)
            return pygame.Surface(request.size, pygame.SRCALPHA)

    host = SystemChromeHost(
        UISkinSpec(renderer="four_k_choice_chrome"),
        **_services(),
    )
    requests = [
        ChromeRequest(
            part="backdrop",
            screen="choice",
            variant="overlay",
            size=(3840, 2160),
            density=3.0,
        ),
        ChromeRequest(
            part="panel",
            screen="choice",
            variant="route_cards",
            size=(3600, 1950),
            density=3.0,
        ),
        ChromeRequest(
            part="button",
            screen="choice",
            variant="option_card_tab:primary",
            size=(1136, 132),
            density=3.0,
            selected=True,
        ),
        ChromeRequest(
            part="button",
            screen="choice",
            variant="option_card_tab:primary",
            size=(1136, 132),
            density=3.0,
        ),
        ChromeRequest(
            part="card",
            screen="choice",
            variant="option_card_page",
            size=(3456, 1267),
            density=3.0,
            selected=True,
        ),
        ChromeRequest(
            part="button",
            screen="choice",
            variant="option_card_action:primary",
            size=(3456, 132),
            density=3.0,
        ),
    ]

    expected_bytes = sum(
        pygame.Surface(request.size, pygame.SRCALPHA).get_pitch()
        * request.size[1]
        for request in requests
    )
    assert expected_bytes > 64 * 1024 * 1024
    assert expected_bytes < 128 * 1024 * 1024

    for _ in range(3):
        for request in requests:
            assert host.render(request) is not None

    assert calls == requests
    assert host.cache_size == len(requests)
    assert host.cache_bytes == expected_bytes

    contrast_requests = [
        replace(request, visual_profile="high_contrast")
        for request in requests
    ]
    for request in contrast_requests:
        assert host.render(request) is not None
    contrast_calls = len(calls)
    for request in contrast_requests:
        assert host.render(request) is not None
    assert len(calls) == contrast_calls
    assert host.cache_bytes <= 128 * 1024 * 1024

    # Switching back may rebuild standard entries evicted by the contrast
    # frame, but the returned 4K working set must likewise become fully warm.
    for request in requests:
        assert host.render(request) is not None
    restored_calls = len(calls)
    for request in requests:
        assert host.render(request) is not None
    assert len(calls) == restored_calls
    assert host.cache_bytes <= 128 * 1024 * 1024


def test_surface_larger_than_byte_budget_is_rendered_but_not_cached() -> None:
    calls = 0

    @widget(
        "oversized_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class OversizedChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            nonlocal calls
            calls += 1
            return pygame.Surface(request.size, pygame.SRCALPHA)

    probe = pygame.Surface((20, 10), pygame.SRCALPHA)
    surface_bytes = probe.get_pitch() * probe.get_height()
    host = SystemChromeHost(
        UISkinSpec(renderer="oversized_chrome"),
        max_cache_bytes=surface_bytes - 1,
        **_services(),
    )
    request = ChromeRequest(
        part="backdrop",
        screen="menu",
        variant="system",
        size=probe.get_size(),
    )

    first = host.render(request)
    second = host.render(request)

    assert first is not None
    assert second is not None
    assert host.active is True
    assert calls == 2
    assert host.cache_size == 0
    assert host.cache_bytes == 0


def test_clear_cache_resets_entry_and_byte_accounting() -> None:
    @widget(
        "clearable_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class ClearableChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            return pygame.Surface(request.size, pygame.SRCALPHA)

    host = SystemChromeHost(
        UISkinSpec(renderer="clearable_chrome"),
        **_services(),
    )
    host.render(
        ChromeRequest(
            part="card",
            screen="gallery",
            variant="unlocked",
            size=(24, 16),
        )
    )
    assert host.cache_size == 1
    assert host.cache_bytes > 0

    host.clear_cache()

    assert host.cache_size == 0
    assert host.cache_bytes == 0


def test_clear_cache_can_release_one_screen_without_flushing_others() -> None:
    @widget(
        "screen_clearable_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class ScreenClearableChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            return pygame.Surface(request.size, pygame.SRCALPHA)

    host = SystemChromeHost(
        UISkinSpec(renderer="screen_clearable_chrome"),
        **_services(),
    )
    choice = ChromeRequest(
        part="backdrop",
        screen="choice",
        variant="overlay",
        size=(32, 18),
    )
    dialogue = ChromeRequest(
        part="panel",
        screen="dialogue",
        variant="main",
        size=(24, 16),
    )
    host.render(choice)
    retained = host.render(dialogue)
    retained_bytes = pygame.Surface(
        dialogue.size,
        pygame.SRCALPHA,
    ).get_pitch() * dialogue.size[1]

    host.clear_cache(screen="choice")

    assert host.cache_size == 1
    assert host.cache_bytes == retained_bytes
    assert host.render(dialogue) is retained


def test_render_failure_disables_instance_and_falls_back_once() -> None:
    calls = 0

    @widget(
        "failing_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class FailingChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            nonlocal calls
            calls += 1
            raise RuntimeError("paint failed")

    host = SystemChromeHost(
        UISkinSpec(renderer="failing_chrome"),
        **_services(),
    )
    request = ChromeRequest(
        part="panel",
        screen="save",
        variant="system",
        size=(200, 120),
    )

    assert host.render(request) is None
    assert host.active is False
    assert host.render(request) is None
    assert calls == 1


@pytest.mark.parametrize("bad_return", ["wrong type", (20, 10)])
def test_invalid_render_result_disables_instance(bad_return) -> None:
    @widget(
        "invalid_result_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class InvalidResultChrome:
        def __init__(self, **_):
            pass

        def render(self, request):
            if isinstance(bad_return, tuple):
                return pygame.Surface(bad_return, pygame.SRCALPHA)
            return bad_return

    host = SystemChromeHost(
        UISkinSpec(renderer="invalid_result_chrome"),
        **_services(),
    )
    assert (
        host.render(
            ChromeRequest(
                part="card",
                screen="gallery",
                variant="locked",
                size=(30, 30),
            )
        )
        is None
    )
    assert host.active is False


def test_constructor_failure_isolated_to_stock_fallback() -> None:
    @widget(
        "constructor_failure_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class ConstructorFailureChrome:
        def __init__(self, **_):
            raise RuntimeError("init failed")

    host = SystemChromeHost(
        UISkinSpec(renderer="constructor_failure_chrome"),
        **_services(),
    )
    assert host.active is False


def test_validator_reports_unknown_renderer(tmp_path: Path) -> None:
    _write_meta(
        tmp_path,
        {
            "pack_format_version": "0.1",
            "ui_skin": {"renderer": "missing_renderer"},
        },
    )

    issue = next(
        issue for issue in _skin_issues(tmp_path) if issue.path == "ui_skin.renderer"
    )
    assert issue.severity == "error"
    assert "沒有由任何" in issue.message


def test_validator_reports_wrong_renderer_protocol(tmp_path: Path) -> None:
    @widget(
        "wrong_protocol_chrome",
        plugin_id="test_skin",
        signature={"protocol": "something_else/v9"},
    )
    class WrongProtocolChrome:
        pass

    _write_meta(
        tmp_path,
        {
            "pack_format_version": "0.1",
            "ui_skin": {"renderer": "wrong_protocol_chrome"},
        },
    )

    issue = next(
        issue for issue in _skin_issues(tmp_path) if issue.path == "ui_skin.renderer"
    )
    assert issue.severity == "error"
    assert UI_CHROME_PROTOCOL in issue.message


def test_validator_uses_plugin_owned_options_model(tmp_path: Path) -> None:
    class Options(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        line_weight: int

    @widget(
        "strict_options_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class StrictOptionsChrome:
        OptionsModel = Options

    _write_meta(
        tmp_path,
        {
            "pack_format_version": "0.1",
            "ui_skin": {
                "renderer": "strict_options_chrome",
                "options": {"line_weight": "2", "mystery": True},
            },
        },
    )

    paths = {
        issue.path for issue in _skin_issues(tmp_path) if issue.severity == "error"
    }
    assert {
        "ui_skin.options.line_weight",
        "ui_skin.options.mystery",
    } <= paths


def test_validator_isolates_plugin_options_validator_exception(
    tmp_path: Path,
) -> None:
    class ExplodingOptions(BaseModel):
        value: int = 1

        @model_validator(mode="after")
        def explode(self):
            raise RuntimeError("plugin validator exploded")

    @widget(
        "exploding_options_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class ExplodingOptionsChrome:
        OptionsModel = ExplodingOptions

    _write_meta(
        tmp_path,
        {
            "pack_format_version": "0.1",
            "ui_skin": {
                "renderer": "exploding_options_chrome",
                "options": {},
            },
        },
    )

    issues = _skin_issues(tmp_path)
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].path == "ui_skin.options"
    assert "RuntimeError" in issues[0].message
    assert "plugin validator exploded" in issues[0].message


def test_validator_isolates_options_model_reflection_exception(
    tmp_path: Path,
) -> None:
    class ExplodingMeta(type):
        def __getattribute__(cls, name):
            if name == "OptionsModel":
                raise RuntimeError("reflection exploded")
            return super().__getattribute__(name)

    @widget(
        "unreadable_options_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class UnreadableOptionsChrome(metaclass=ExplodingMeta):
        pass

    _write_meta(
        tmp_path,
        {
            "pack_format_version": "0.1",
            "ui_skin": {
                "renderer": "unreadable_options_chrome",
                "options": {},
            },
        },
    )

    issues = _skin_issues(tmp_path)
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].path == "ui_skin.options"
    assert "reflection exploded" in issues[0].message


def test_capability_bundle_exposes_selector_and_widget_protocol() -> None:
    class DiscoverableOptions(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        line_weight: int = 2

    @widget(
        "discoverable_chrome",
        plugin_id="test_skin",
        signature={
            "protocol": UI_CHROME_PROTOCOL,
            "options_model": "Plugin-owned",
        },
    )
    class DiscoverableChrome:
        OptionsModel = DiscoverableOptions

    manifest = build_manifest()
    metadata_schema = manifest["metadata_schema"]["ui_skin"]
    widget_row = next(
        row for row in manifest["widgets"] if row["name"] == "discoverable_chrome"
    )

    assert metadata_schema["additionalProperties"] is False
    assert metadata_schema["required"] == ["renderer"]
    assert widget_row["signature"]["protocol"] == UI_CHROME_PROTOCOL
    assert widget_row["options_schema"]["additionalProperties"] is False
    assert (
        widget_row["options_schema"]["properties"]["line_weight"]["type"] == "integer"
    )
    assert "UISkinSpec" in manifest["content_schema"]
    assert (
        schema_document()["models"]["UISkinSpec"]["properties"]
        == metadata_schema["properties"]
    )


def test_manifest_marks_missing_or_broken_widget_options_schema_unavailable() -> None:
    @widget(
        "no_options_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class NoOptionsChrome:
        pass

    class BrokenSchemaOptions(BaseModel):
        @classmethod
        def model_json_schema(cls, *args, **kwargs):
            raise RuntimeError("schema hook exploded")

    @widget(
        "broken_schema_chrome",
        plugin_id="test_skin",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class BrokenSchemaChrome:
        OptionsModel = BrokenSchemaOptions

    manifest = build_manifest()
    rows = {row["name"]: row for row in manifest["widgets"]}

    assert rows["no_options_chrome"]["options_schema"] == {
        "unavailable": "not_declared",
    }
    assert rows["broken_schema_chrome"]["options_schema"] == {
        "unavailable": "reflection_error",
    }
    # Discovery remains serializable even when plugin schema hooks fail.
    assert json.loads(manifest_json())["widgets"]
