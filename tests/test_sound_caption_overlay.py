"""Responsive, non-interactive semantic sound-caption presentation."""
from __future__ import annotations

import os
import unicodedata
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.core.localization import Localization
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.system_chrome import ChromeRequest
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.widgets import SoundCaptionOverlay
from world_gal_game.ui.widgets.sound_caption import wrap_sound_caption


class RecordingChrome:
    def __init__(self) -> None:
        self.requests: list[ChromeRequest] = []

    def paint(self, _surface, _rect, request: ChromeRequest) -> bool:
        self.requests.append(request)
        return False


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


def _relative_luminance(color: tuple[int, ...]) -> float:
    def channel(value: int) -> float:
        normalized = value / 255.0
        if normalized <= 0.04045:
            return normalized / 12.92
        return ((normalized + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in color[:3])
    return red * 0.2126 + green * 0.7152 + blue * 0.0722


def _contrast_ratio(foreground: tuple, background: tuple) -> float:
    light = max(_relative_luminance(foreground), _relative_luminance(background))
    dark = min(_relative_luminance(foreground), _relative_luminance(background))
    return (light + 0.05) / (dark + 0.05)


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
@pytest.mark.parametrize("text_scale", [1.0, 1.4], ids=["standard", "large"])
def test_overlay_wraps_cjk_and_latin_inside_top_safe_area(
    size: tuple[int, int],
    text_scale: float,
) -> None:
    fonts = FontRegistry((), scale=max(1.0, size[1] / 720.0))
    overlay = SoundCaptionOverlay(
        size,
        fonts=fonts,
        theme=default_theme(),
        localization=Localization(),
        text_scale=text_scale,
    )
    overlay.set_captions([
        {"kind": "ambient", "text": "遠方的鐘聲穿過校園與樹梢逐漸靠近" * 8},
        SimpleNamespace(
            kind="sfx",
            text="averylonglatinidentifierwithoutbreaks" * 6,
        ),
        {"kind": "music", "text": "弦樂由遠而近，旋律轉入副歌" * 8},
        {"kind": "silence", "text": "所有聲音突然消失" * 8},
        {"kind": "sfx", "text": "第五則不可顯示"},
    ])
    surface = pygame.Surface(size, pygame.SRCALPHA)

    overlay.draw(surface)

    canvas = pygame.Rect((0, 0), size)
    assert canvas.contains(overlay.rect)
    assert overlay.rect.centerx == size[0] // 2
    assert overlay.rect.top >= 8
    assert overlay.rect.bottom <= int(size[1] * 0.5)
    assert len(overlay.captions) == 4
    assert overlay.enabled is False
    assert overlay.hit(overlay.rect.center) is False
    assert overlay.content_rect.width > 0
    font = fonts.get(overlay._font_size, bold=True)
    assert all(
        font.size(line)[0] <= overlay.content_rect.width
        for cue_lines in overlay.caption_lines
        for line in cue_lines
    )
    assert pygame.mask.from_surface(surface).count() > 0


def test_stock_palette_has_enhanced_text_contrast_and_chrome_semantics() -> None:
    chrome = RecordingChrome()
    overlay = SoundCaptionOverlay(
        (1280, 720),
        fonts=FontRegistry(()),
        theme=default_theme(),
        localization=Localization(ui={
            "sound_caption_sfx": "SFX",
        }),
        chrome=chrome,  # type: ignore[arg-type]
    )
    overlay.set_captions([
        {"kind": "sfx", "text": "door closes"},
        {"kind": "ambient", "text": "夜風掠過樹梢"},
    ])
    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)

    overlay.draw(surface)

    assert _contrast_ratio(
        overlay.STOCK_FOREGROUND,
        overlay.STOCK_BACKGROUND,
    ) >= 7.0
    assert overlay.captions[0].display_text == "［SFX］ door closes"
    assert overlay.captions[1].label == "環境音"
    assert chrome.requests
    request = chrome.requests[-1]
    assert request == ChromeRequest(
        part="panel",
        screen="dialogue",
        variant="sound_caption",
        size=overlay.rect.size,
        density=1.0,
        emphasis=3,
    )


def test_overlay_update_never_consumes_or_mutates_input() -> None:
    overlay = SoundCaptionOverlay(
        (1280, 720),
        fonts=FontRegistry(()),
        theme=default_theme(),
    )
    overlay.set_captions([{"kind": "music", "text": "主題旋律響起"}])
    marker = SimpleNamespace(confirm=True, advance_dialogue=True)

    overlay.update(1.0, marker)

    assert marker.confirm is True
    assert marker.advance_dialogue is True
    assert len(overlay.captions) == 1


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("A\u0301B\u0327C\u0308D\u0301E\u0327", id="combining"),
        pytest.param("שָׁלוֹםשָׁלוֹם", id="rtl-niqqud"),
        pytest.param("👩‍💻👩‍💻👩‍💻", id="emoji-zwj"),
        pytest.param("✈️✈️✈️✈️", id="variation-selector"),
    ],
)
def test_wrapping_preserves_graphemes_and_rtl_logical_order(source: str) -> None:
    font = FontRegistry(()).get(24, bold=True)
    # Narrow enough to force multiple lines even when the backend represents
    # an unsupported emoji as a replacement glyph.
    lines = wrap_sound_caption(source, font, 28)

    assert len(lines) > 1
    assert "".join(lines) == source
    for line in lines:
        assert line
        assert unicodedata.category(line[0]).startswith("M") is False
        assert line[0] != "\u200d"
        assert ord(line[0]) not in range(0xFE00, 0xFE10)
        assert line[-1] != "\u200d"


def test_untrusted_zwj_caption_fails_closed_at_normalization_boundary() -> None:
    overlay = SoundCaptionOverlay(
        (640, 360),
        fonts=FontRegistry(()),
        theme=default_theme(),
        text_scale=1.4,
    )
    overlay.set_captions([
        {"kind": "sfx", "text": "👩‍💻 開啟終端機 👩‍💻 開啟終端機"},
    ])
    overlay.layout(safe_rect=pygame.Rect(210, 0, 220, 120))

    assert overlay.captions == ()
    assert overlay.caption_lines == ()
    assert overlay.rect.size == (0, 0)
