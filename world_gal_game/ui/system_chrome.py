"""Fault-isolated bridge from system UI to a pack-owned chrome widget.

The renderer protocol is intentionally paint-only.  A plugin receives a
finite :class:`ChromeRequest` and may return an exact-size alpha surface.
Geometry, text, focus semantics, hit-testing, and input ordering remain owned
by the engine scenes and widgets.

Renderer class contract (``ui_chrome/v1``)::

    class MyChrome:
        def __init__(self, *, options, assets, fonts, theme): ...
        def render(self, request: ChromeRequest) -> pygame.Surface | None: ...

The class is registered through ``@widget(...,
signature={"protocol": "ui_chrome/v1"})``.  Constructor/render failures,
invalid return types, and wrong-sized surfaces disable only that host and
fall back to the stock UI for the rest of the session.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import logging
from typing import Literal, TYPE_CHECKING

import pygame

from ..core.ui_skin_spec import UISkinSpec
from ..plugins import WIDGET_REGISTRY
from ..plugins.errors import isolate

if TYPE_CHECKING:
    from .assets import AssetManager
    from .fonts import FontRegistry
    from .theme import Theme
    from .visual import VisualPolicy


_log = logging.getLogger("world_gal_game.ui.system_chrome")

ChromePart = Literal[
    "backdrop",
    "panel",
    "divider",
    "button",
    "card",
    "footer",
    "focus",
]
VisualProfile = Literal["standard", "high_contrast"]

UI_CHROME_PROTOCOL = "ui_chrome/v1"
_MAX_CACHE_ENTRIES = 128
# A complete 4K comparison-choice frame retains roughly 80 MiB of plugin
# chrome (backdrop, panel, tabs, page, and CTA).  The old 64 MiB ceiling
# evicted the backdrop while painting the page, then evicted the page while
# repainting the backdrop on the next frame: a bounded cache that nevertheless
# guaranteed continuous procedural rerendering.  128 MiB holds that finite
# working set while remaining well below the separate image-asset budget.
_MAX_CACHE_BYTES = 128 * 1024 * 1024
_VISUAL_PROFILES = frozenset({"standard", "high_contrast"})


@dataclass(frozen=True, slots=True)
class ChromeRequest:
    """One finite, cache-safe request sent to a UI chrome renderer."""

    part: ChromePart
    screen: str
    variant: str
    size: tuple[int, int]
    density: float = 1.0
    enabled: bool = True
    hovered: bool = False
    focused: bool = False
    selected: bool = False
    emphasis: int = 0
    visual_profile: VisualProfile = "standard"

    def __post_init__(self) -> None:
        width, height = self.size
        if width <= 0 or height <= 0:
            raise ValueError("chrome request size must be positive")
        if self.density <= 0:
            raise ValueError("chrome request density must be positive")
        if not 0 <= self.emphasis <= 3:
            raise ValueError("chrome request emphasis must be between 0 and 3")
        if (
            not isinstance(self.visual_profile, str)
            or self.visual_profile not in _VISUAL_PROFILES
        ):
            raise ValueError(
                "chrome request visual_profile must be "
                "'standard' or 'high_contrast'",
            )


class SystemChromeHost:
    """Instantiate, cache, validate, and isolate one plugin chrome renderer."""

    def __init__(
        self,
        spec: UISkinSpec,
        *,
        assets: "AssetManager | None",
        fonts: "FontRegistry",
        theme: "Theme",
        visual: "VisualPolicy | None" = None,
        max_cache_entries: int = _MAX_CACHE_ENTRIES,
        max_cache_bytes: int = _MAX_CACHE_BYTES,
    ) -> None:
        self.spec = spec
        self._visual = visual
        self._fonts = fonts
        self._font_revision = getattr(fonts, "revision", None)
        self._assets = assets
        self._asset_revision = getattr(assets, "image_cache_revision", None)
        self._renderer = None
        self._plugin_id = "unknown"
        self._disabled = False
        self._visual_profiles: frozenset[str] = frozenset({"standard"})
        self._cache: OrderedDict[
            ChromeRequest,
            pygame.Surface | None,
        ] = OrderedDict()
        self._cache_entry_bytes: dict[ChromeRequest, int] = {}
        self._cache_bytes = 0
        self._max_cache_entries = max(8, int(max_cache_entries))
        self._max_cache_bytes = max(0, int(max_cache_bytes))

        entry = WIDGET_REGISTRY.get(spec.renderer)
        if entry is None:
            self._disable(
                f"unknown ui_skin renderer {spec.renderer!r}",
            )
            return
        self._plugin_id = entry.plugin_id
        protocol = (entry.signature or {}).get("protocol")
        if protocol != UI_CHROME_PROTOCOL:
            self._disable(
                f"renderer {spec.renderer!r} declares protocol "
                f"{protocol!r}, expected {UI_CHROME_PROTOCOL!r}",
            )
            return
        declared_profiles = (entry.signature or {}).get("visual_profiles")
        if isinstance(declared_profiles, (list, tuple, set, frozenset)):
            supported_profiles = frozenset(
                profile
                for profile in declared_profiles
                if isinstance(profile, str) and profile in _VISUAL_PROFILES
            )
            if "standard" in supported_profiles:
                self._visual_profiles = supported_profiles

        failures = []
        renderer = None
        with isolate(
            self._plugin_id,
            f"ui_skin:{spec.renderer}:init",
            capture=failures,
        ):
            renderer = entry.cls(
                options=dict(spec.options),
                assets=assets,
                fonts=fonts,
                theme=theme,
            )
            if not callable(getattr(renderer, "render", None)):
                raise TypeError(
                    "ui chrome renderer must define render(request)",
                )
        if failures or renderer is None:
            self._disabled = True
            return
        self._renderer = renderer

    @property
    def active(self) -> bool:
        return self._renderer is not None and not self._disabled

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    @property
    def cache_bytes(self) -> int:
        """Estimated bytes retained by cached pygame surfaces."""
        return self._cache_bytes

    def clear_cache(self, *, screen: str | None = None) -> None:
        """Release cached chrome, optionally for one owning screen only.

        A large transient presentation such as the route comparison can free
        its 4K surfaces when it is genuinely left without discarding reusable
        dialogue/menu chrome owned by the same host.  A parameterless call
        retains the historical full-reset behaviour used by hot reload and
        renderer fault isolation.
        """
        if screen is None:
            self._cache.clear()
            self._cache_entry_bytes.clear()
            self._cache_bytes = 0
            return
        for request in tuple(self._cache):
            if request.screen != screen:
                continue
            self._cache.pop(request, None)
            self._cache_bytes -= self._cache_entry_bytes.pop(request, 0)

    def render(self, request: ChromeRequest) -> pygame.Surface | None:
        """Return an exact-size cached surface, or ``None`` for stock fallback."""
        font_revision = getattr(self._fonts, "revision", None)
        asset_revision = getattr(
            self._assets,
            "image_cache_revision",
            None,
        )
        if (
            font_revision != self._font_revision
            or asset_revision != self._asset_revision
        ):
            # Plugin renderers retain the shared registry object and therefore
            # see font/asset invalidation immediately. Their already-composed
            # chrome surfaces must not survive either source change.
            self.clear_cache()
            self._font_revision = font_revision
            self._asset_revision = asset_revision
        request = self._request_for_live_profile(request)
        if not self.active:
            return None
        if request.visual_profile not in self._visual_profiles:
            return None
        cached = self._cache.get(request)
        if request in self._cache:
            self._cache.move_to_end(request)
            return cached

        failures = []
        rendered = None
        with isolate(
            self._plugin_id,
            f"ui_skin:{self.spec.renderer}:render:{request.part}",
            capture=failures,
        ):
            rendered = self._renderer.render(request)
            if rendered is not None:
                if not isinstance(rendered, pygame.Surface):
                    raise TypeError(
                        "ui chrome renderer must return pygame.Surface or None",
                    )
                if rendered.get_size() != request.size:
                    raise ValueError(
                        "ui chrome renderer returned "
                        f"{rendered.get_size()}, expected {request.size}",
                    )
        if failures:
            self._disabled = True
            self.clear_cache()
            return None

        rendered_bytes = 0
        if rendered is not None:
            rendered_bytes = rendered.get_pitch() * rendered.get_height()
            if rendered_bytes > self._max_cache_bytes:
                return rendered

        self._cache[request] = rendered
        self._cache_entry_bytes[request] = rendered_bytes
        self._cache_bytes += rendered_bytes
        self._cache.move_to_end(request)
        while (
            len(self._cache) > self._max_cache_entries
            or self._cache_bytes > self._max_cache_bytes
        ):
            evicted_request, _ = self._cache.popitem(last=False)
            self._cache_bytes -= self._cache_entry_bytes.pop(
                evicted_request,
            )
        return rendered

    def paint(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        request: ChromeRequest,
    ) -> bool:
        """Blit a rendered part into ``rect`` and report whether it handled it."""
        if request.size != rect.size:
            request = replace(request, size=rect.size)
        rendered = self.render(request)
        if rendered is None:
            return False
        surface.blit(rendered, rect.topleft)
        return True

    def _request_for_live_profile(self, request: ChromeRequest) -> ChromeRequest:
        """Apply the current live profile without mutating caller-owned input."""
        if self._visual is None:
            return request
        try:
            profile = self._visual.profile
        except Exception:
            profile = "standard"
        if not isinstance(profile, str) or profile not in _VISUAL_PROFILES:
            profile = "standard"
        if request.visual_profile == profile:
            return request
        return replace(request, visual_profile=profile)

    def _disable(self, message: str) -> None:
        if not self._disabled:
            _log.warning("%s; using stock system UI", message)
        self._disabled = True
        self._renderer = None
        self.clear_cache()


def build_system_chrome(
    spec: UISkinSpec | None,
    *,
    assets: "AssetManager | None",
    fonts: "FontRegistry",
    theme: "Theme",
    visual: "VisualPolicy | None" = None,
) -> SystemChromeHost | None:
    """Build the selected host; an absent selector has zero runtime overhead."""
    if spec is None:
        return None
    return SystemChromeHost(
        spec,
        assets=assets,
        fonts=fonts,
        theme=theme,
        visual=visual,
    )


__all__ = [
    "ChromePart",
    "ChromeRequest",
    "SystemChromeHost",
    "UI_CHROME_PROTOCOL",
    "VisualProfile",
    "build_system_chrome",
]
